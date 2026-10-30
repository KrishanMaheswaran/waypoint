from __future__ import annotations

import asyncio

import pytest

from app import publisher as publisher_module
from app.publisher import KafkaTelemetryPublisher, PublishUnavailable
from app.schemas import TelemetryCreate


class FakeProducer:
    def __init__(self, fail_attempts: int = 0) -> None:
        self.fail_attempts = fail_attempts
        self.calls: list[tuple[str, bytes, bytes]] = []

    async def send_and_wait(self, topic: str, *, value: bytes, key: bytes) -> None:
        self.calls.append((topic, value, key))
        if self.fail_attempts:
            self.fail_attempts -= 1
            raise OSError("broker unavailable")


def sample_event() -> TelemetryCreate:
    return TelemetryCreate.model_validate(
        {
            "event_id": "publisher-test-event",
            "vehicle_id": "WP-PUBLISH-1",
            "battery_percentage": 80,
            "status": "ready",
            "temperature": 24,
            "fault_code": None,
            "timestamp": "2026-10-29T12:00:00Z",
        }
    )


def test_publisher_acknowledges_message_with_vehicle_partition_key() -> None:
    async def publish() -> list[tuple[str, bytes, bytes]]:
        publisher = KafkaTelemetryPublisher()
        producer = FakeProducer()
        publisher._producer = producer  # type: ignore[assignment]
        await publisher.publish(sample_event())
        return producer.calls

    calls = asyncio.run(publish())

    assert len(calls) == 1
    topic, value, key = calls[0]
    assert topic == publisher_module.settings.KAFKA_TOPIC
    assert key == b"WP-PUBLISH-1"
    assert b'"event_id":"publisher-test-event"' in value


def test_publish_failure_uses_bounded_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    producer = FakeProducer(fail_attempts=3)
    sleep_calls = 0

    async def no_sleep(_: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1

    monkeypatch.setattr(publisher_module.asyncio, "sleep", no_sleep)

    async def publish() -> None:
        publisher = KafkaTelemetryPublisher()
        publisher._producer = producer  # type: ignore[assignment]
        await publisher.publish(sample_event())

    with pytest.raises(PublishUnavailable):
        asyncio.run(publish())

    assert len(producer.calls) == 3
    assert sleep_calls == 2
