from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Protocol

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.consumer import persist_message
from app.main import app
from app.models import TelemetryEvent, TelemetryIngestionFailure
from app.schemas import TelemetryCreate


class EventRecorder(Protocol):
    events: list[TelemetryCreate]

test_database_url = os.getenv("WAYPOINT_TEST_DATABASE_URL")
if test_database_url:
    engine = create_engine(test_database_url, pool_pre_ping=True)
else:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base.metadata.create_all(bind=engine)


def override_get_db() -> Iterator[Session]:
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def setup_db() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def post_and_consume(
    client: TestClient,
    publisher: EventRecorder,
    payload: dict[str, object],
    offset: int,
) -> tuple[httpx.Response, str]:
    response = client.post("/telemetry", json=payload)
    if response.status_code == 202:
        published_event = publisher.events[-1]
        outcome = persist_message(
            TestingSessionLocal,
            topic="waypoint.telemetry",
            partition=0,
            offset=offset,
            value=published_event.model_dump_json().encode("utf-8"),
        )
    else:
        outcome = "not-published"
    return response, outcome


def test_post_returns_accepted_only_after_broker_publish(
    api_client: TestClient,
    publisher: EventRecorder,
) -> None:
    payload = {
        "event_id": "test-event-vf-1001",
        "vehicle_id": "VF-1001",
        "battery_percentage": 88.5,
        "status": "ready",
        "temperature": 24.7,
        "fault_code": None,
        "timestamp": "2026-10-26T12:00:00Z",
    }

    response = api_client.post("/telemetry", json=payload)

    assert response.status_code == 202
    assert response.headers["x-waypoint-delivery"] == "broker-acknowledged"
    assert response.json()["event_id"] == payload["event_id"]
    assert len(publisher.events) == 1
    with TestingSessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(TelemetryEvent)) == 0

    assert persist_message(
        TestingSessionLocal,
        topic="waypoint.telemetry",
        partition=0,
        offset=0,
        value=publisher.events[0].model_dump_json().encode("utf-8"),
    ) == "persisted"


def test_get_vehicles_returns_latest_known_state(
    api_client: TestClient,
    publisher: EventRecorder,
) -> None:
    _, outcome = post_and_consume(
        api_client,
        publisher,
        {
            "event_id": "test-event-vf-1002",
            "vehicle_id": "VF-1002",
            "battery_percentage": 50,
            "status": "charging",
            "temperature": 31.2,
            "fault_code": "BMS-01",
            "timestamp": "2026-10-26T12:10:00Z",
        },
        0,
    )
    assert outcome == "persisted"

    response = api_client.get("/vehicles")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["vehicle_id"] == "VF-1002"
    assert payload[0]["battery_percentage"] == 50
    assert payload[0]["status"] == "charging"
    assert payload[0]["fault_code"] == "BMS-01"


def test_get_vehicle_telemetry_history_returns_recent_events(
    api_client: TestClient,
    publisher: EventRecorder,
) -> None:
    for offset, event_id, battery, state, time_value in (
        (0, "test-event-vf-1003-1", 80, "idle", "2026-10-26T10:00:00Z"),
        (1, "test-event-vf-1003-2", 82, "ready", "2026-10-26T10:05:00Z"),
    ):
        response, outcome = post_and_consume(
            api_client,
            publisher,
            {
                "event_id": event_id,
                "vehicle_id": "VF-1003",
                "battery_percentage": battery,
                "status": state,
                "temperature": 22.5,
                "fault_code": None,
                "timestamp": time_value,
            },
            offset,
        )
        assert response.status_code == 202
        assert outcome == "persisted"

    response = api_client.get("/vehicles/VF-1003/telemetry")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 2
    assert payload[0]["battery_percentage"] == 82
    assert payload[0]["status"] == "ready"
    assert payload[0]["vehicle_id"] == "VF-1003"


def test_invalid_payload_returns_422_without_publishing(
    api_client: TestClient,
    publisher: EventRecorder,
) -> None:
    response = api_client.post(
        "/telemetry",
        json={
            "event_id": "test-event-invalid",
            "vehicle_id": "VF-1004",
            "battery_percentage": 150,
            "status": "running",
            "temperature": 25,
        },
    )

    assert response.status_code == 422
    assert publisher.events == []


def test_unknown_vehicle_history_returns_404(api_client: TestClient) -> None:
    response = api_client.get("/vehicles/UNKNOWN/telemetry")
    assert response.status_code == 404


def test_identical_event_retry_is_accepted_and_persisted_once(
    api_client: TestClient,
    publisher: EventRecorder,
) -> None:
    payload = {
        "event_id": "retry-event-1",
        "vehicle_id": "VF-2001",
        "battery_percentage": 73.5,
        "status": "ready",
        "temperature": 26.0,
        "fault_code": None,
        "timestamp": "2026-10-26T12:00:00Z",
    }

    first = api_client.post("/telemetry", json=payload)
    retry = api_client.post("/telemetry", json=payload)

    assert first.status_code == 202
    assert retry.status_code == 202
    assert publisher.events[0] == publisher.events[1]
    assert persist_message(
        TestingSessionLocal,
        topic="waypoint.telemetry",
        partition=0,
        offset=0,
        value=publisher.events[0].model_dump_json().encode(),
    ) == "persisted"
    assert persist_message(
        TestingSessionLocal,
        topic="waypoint.telemetry",
        partition=0,
        offset=1,
        value=publisher.events[1].model_dump_json().encode(),
    ) == "duplicate"
    with TestingSessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(TelemetryEvent)) == 1


def test_conflicting_event_id_is_audited_as_async_rejection(
    api_client: TestClient,
    publisher: EventRecorder,
) -> None:
    payload = {
        "event_id": "conflicting-event-1",
        "vehicle_id": "VF-2002",
        "battery_percentage": 73.5,
        "status": "ready",
        "temperature": 26.0,
        "fault_code": None,
        "timestamp": "2026-10-26T12:00:00Z",
    }
    conflicting_payload = {**payload, "battery_percentage": 72.0}

    original_response, original_outcome = post_and_consume(api_client, publisher, payload, 0)
    conflict_response = api_client.post("/telemetry", json=conflicting_payload)
    conflicting_event = publisher.events[-1]
    conflict_outcome = persist_message(
        TestingSessionLocal,
        topic="waypoint.telemetry",
        partition=0,
        offset=1,
        value=conflicting_event.model_dump_json().encode(),
    )

    assert original_response.status_code == 202
    assert original_outcome == "persisted"
    assert conflict_response.status_code == 202
    assert conflict_outcome == "rejected"
    with TestingSessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(TelemetryEvent)) == 1
        failure = db.scalar(select(TelemetryIngestionFailure))
        assert failure is not None
        assert failure.event_id == "conflicting-event-1"
        assert failure.reason == "event_id_conflict"


def test_out_of_order_event_is_stored_without_overwriting_latest_state(
    api_client: TestClient,
    publisher: EventRecorder,
) -> None:
    newest_payload = {
        "event_id": "out-of-order-newer",
        "vehicle_id": "VF-2003",
        "battery_percentage": 80.0,
        "status": "ready",
        "temperature": 24.0,
        "fault_code": None,
        "timestamp": "2026-10-26T12:10:00Z",
    }
    older_payload = {
        **newest_payload,
        "event_id": "out-of-order-older",
        "battery_percentage": 35.0,
        "status": "charging",
        "timestamp": "2026-10-26T12:00:00Z",
    }

    first, first_outcome = post_and_consume(api_client, publisher, newest_payload, 0)
    second, second_outcome = post_and_consume(api_client, publisher, older_payload, 1)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first_outcome == second_outcome == "persisted"
    vehicle = api_client.get("/vehicles").json()[0]
    assert vehicle["battery_percentage"] == newest_payload["battery_percentage"]
    assert vehicle["status"] == newest_payload["status"]
    assert vehicle["last_updated"].startswith("2026-10-26T12:10:00")
    history = api_client.get("/vehicles/VF-2003/telemetry").json()
    assert len(history) == 2
    assert {event["event_id"] for event in history} == {
        newest_payload["event_id"],
        older_payload["event_id"],
    }


def test_broker_unavailable_returns_503_and_allows_same_id_retry(
    api_client: TestClient,
    publisher: EventRecorder,
) -> None:
    publisher.unavailable = True
    response = api_client.post(
        "/telemetry",
        json={
            "event_id": "broker-outage-event",
            "vehicle_id": "VF-2004",
            "battery_percentage": 60,
            "status": "ready",
            "temperature": 23,
            "fault_code": None,
            "timestamp": "2026-10-26T12:00:00Z",
        },
    )

    assert response.status_code == 503
    assert response.headers["retry-after"] == "1"
    assert publisher.events == []
