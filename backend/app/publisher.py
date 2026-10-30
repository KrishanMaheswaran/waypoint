from __future__ import annotations

import asyncio
import logging
import random
from typing import Protocol

from aiokafka import AIOKafkaProducer
from aiokafka.errors import KafkaError
from fastapi import Request

from app.config import settings
from app.metrics import TELEMETRY_PUBLISH_EVENTS
from app.schemas import TelemetryCreate

logger = logging.getLogger(__name__)


class TelemetryPublisher(Protocol):
    async def publish(self, event: TelemetryCreate) -> None: ...


class PublishUnavailable(Exception):
    pass


class KafkaTelemetryPublisher:
    def __init__(self) -> None:
        self._producer = AIOKafkaProducer(
            bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS.split(","),
            enable_idempotence=True,
            request_timeout_ms=5_000,
            retry_backoff_ms=250,
        )

    async def start(self) -> None:
        for attempt in range(3):
            try:
                await asyncio.wait_for(self._producer.start(), timeout=5)
                return
            except (KafkaError, TimeoutError, OSError) as error:
                if attempt == 2:
                    raise PublishUnavailable("telemetry broker is unavailable") from error
                await asyncio.sleep(random.uniform(0, min(0.5 * (2**attempt), 2.0)))

    async def stop(self) -> None:
        await self._producer.stop()

    async def publish(self, event: TelemetryCreate) -> None:
        key = event.vehicle_id.encode("utf-8")
        value = event.model_dump_json().encode("utf-8")
        for attempt in range(3):
            try:
                await asyncio.wait_for(
                    self._producer.send_and_wait(settings.KAFKA_TOPIC, value=value, key=key),
                    timeout=5,
                )
            except (KafkaError, TimeoutError, OSError) as error:
                if attempt == 2:
                    TELEMETRY_PUBLISH_EVENTS.labels("failure").inc()
                    logger.error(
                        "telemetry_publish_failed",
                        extra={
                            "attempts": attempt + 1,
                            "topic": settings.KAFKA_TOPIC,
                            "vehicle_id": event.vehicle_id,
                            "event_id": event.event_id,
                            "error_type": type(error).__name__,
                        },
                    )
                    raise PublishUnavailable("telemetry broker is unavailable") from error

                delay = random.uniform(0, min(0.5 * (2**attempt), 2.0))
                logger.warning(
                    "telemetry_publish_retry",
                    extra={
                        "attempt": attempt + 1,
                        "topic": settings.KAFKA_TOPIC,
                        "vehicle_id": event.vehicle_id,
                        "error_type": type(error).__name__,
                    },
                )
                await asyncio.sleep(delay)
            else:
                TELEMETRY_PUBLISH_EVENTS.labels("success").inc()
                return


def get_publisher(request: Request) -> TelemetryPublisher:
    return request.app.state.telemetry_publisher
