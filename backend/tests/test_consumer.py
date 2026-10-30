from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from aiokafka.structs import OffsetAndMetadata, TopicPartition

from app import consumer as consumer_module
from app.consumer import consume_message, consume_message_with_retry
from app.database import Base
from app.models import TelemetryEvent, TelemetryIngestionFailure, Vehicle

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def reset_test_database() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


@dataclass(frozen=True)
class Message:
    topic: str
    partition: int
    offset: int
    value: bytes | None


class FakeConsumer:
    def __init__(self) -> None:
        self.commits: list[dict[TopicPartition, OffsetAndMetadata]] = []
        self.commit_attempts = 0
        self.fail_commits = 0

    async def commit(self, offsets: dict[TopicPartition, OffsetAndMetadata]) -> None:
        self.commit_attempts += 1
        if self.fail_commits:
            self.fail_commits -= 1
            raise TimeoutError("commit acknowledgment lost")
        self.commits.append(offsets)


def event(
    event_id: str,
    vehicle_id: str = "WP-TEST-1",
    battery: float = 80,
    timestamp: str = "2026-10-29T12:00:00Z",
) -> bytes:
    return json.dumps(
        {
            "event_id": event_id,
            "vehicle_id": vehicle_id,
            "battery_percentage": battery,
            "status": "ready",
            "temperature": 24.0,
            "fault_code": None,
            "timestamp": timestamp,
        }
    ).encode()


def test_replayed_message_after_consumer_restart_is_idempotent() -> None:
    message = Message("waypoint.telemetry", 0, 0, event("restart-event"))
    first_consumer = FakeConsumer()
    restarted_consumer = FakeConsumer()

    first_outcome = asyncio.run(
        consume_message(first_consumer, message, TestSessionLocal)
    )
    replay_outcome = asyncio.run(
        consume_message(restarted_consumer, message, TestSessionLocal)
    )

    assert first_outcome == "persisted"
    assert replay_outcome == "duplicate"
    assert len(first_consumer.commits) == 1
    assert len(restarted_consumer.commits) == 1
    with TestSessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(TelemetryEvent)) == 1


def test_conflicting_event_id_is_audited_and_offset_can_progress() -> None:
    consumer = FakeConsumer()
    original = Message("waypoint.telemetry", 1, 0, event("conflict-event", battery=80))
    conflict = Message("waypoint.telemetry", 1, 1, event("conflict-event", battery=55))

    assert asyncio.run(
        consume_message(consumer, original, TestSessionLocal)
    ) == "persisted"
    assert asyncio.run(
        consume_message(consumer, conflict, TestSessionLocal)
    ) == "rejected"

    assert len(consumer.commits) == 2
    with TestSessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(TelemetryEvent)) == 1
        rejection = db.scalar(select(TelemetryIngestionFailure))
        assert rejection is not None
        assert rejection.event_id == "conflict-event"
        assert rejection.reason == "event_id_conflict"


def test_tombstone_message_is_audited_before_offset_commit() -> None:
    consumer = FakeConsumer()
    message = Message("waypoint.telemetry", 1, 2, None)

    outcome = asyncio.run(consume_message(consumer, message, TestSessionLocal))

    assert outcome == "rejected"
    assert len(consumer.commits) == 1
    with TestSessionLocal() as db:
        rejection = db.scalar(select(TelemetryIngestionFailure))
        assert rejection is not None
        assert rejection.reason == "invalid_event"


def test_out_of_order_records_keep_history_without_rolling_back_vehicle_state() -> None:
    consumer = FakeConsumer()
    newer = Message(
        "waypoint.telemetry",
        0,
        0,
        event("newer-event", battery=90, timestamp="2026-10-29T12:10:00Z"),
    )
    older = Message(
        "waypoint.telemetry",
        0,
        1,
        event("older-event", battery=35, timestamp="2026-10-29T12:00:00Z"),
    )

    asyncio.run(consume_message(consumer, newer, TestSessionLocal))
    asyncio.run(consume_message(consumer, older, TestSessionLocal))

    with TestSessionLocal() as db:
        vehicle = db.scalar(select(Vehicle).where(Vehicle.vehicle_id == "WP-TEST-1"))
        assert vehicle is not None
        assert vehicle.battery_percentage == 90
        assert vehicle.last_updated.isoformat().startswith("2026-10-29T12:10:00")
        assert db.scalar(select(func.count()).select_from(TelemetryEvent)) == 2


def test_database_failure_does_not_commit_consumer_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    consumer = FakeConsumer()

    def fail_persist(*args: object, **kwargs: object) -> str:
        raise OperationalError("persist", {}, RuntimeError("database unavailable"))

    monkeypatch.setattr(consumer_module, "persist_message", fail_persist)
    with pytest.raises(OperationalError):
        asyncio.run(
            consume_message(
                consumer,
                Message("waypoint.telemetry", 0, 4, event("db-failure")),
                TestSessionLocal,
            )
        )

    assert consumer.commits == []


def test_database_and_commit_retries_are_bounded_and_replay_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    consumer = FakeConsumer()
    consumer.fail_commits = 1
    sleep_calls = 0

    async def no_sleep(_: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1

    monkeypatch.setattr(consumer_module.asyncio, "sleep", no_sleep)
    message = Message("waypoint.telemetry", 2, 0, event("commit-retry-event"))

    result = asyncio.run(
        consume_message_with_retry(
            consumer,
            message,
            TestSessionLocal,
        )
    )

    assert result == "duplicate"
    assert consumer.commit_attempts == 2
    assert len(consumer.commits) == 1
    assert sleep_calls == 1
    with TestSessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(TelemetryEvent)) == 1

    attempts = 0

    def fail_persist(*args: object, **kwargs: object) -> str:
        nonlocal attempts
        attempts += 1
        raise OperationalError("persist", {}, RuntimeError("database unavailable"))

    monkeypatch.setattr(consumer_module, "persist_message", fail_persist)
    with pytest.raises(OperationalError):
        asyncio.run(
            consume_message_with_retry(
                consumer,
                Message("waypoint.telemetry", 2, 1, event("bounded-retry-event")),
                TestSessionLocal,
            )
        )
    assert attempts == 3
