from __future__ import annotations

import asyncio
import json
import logging
import random
import signal
from typing import Literal, Protocol

from aiokafka import AIOKafkaConsumer
from aiokafka.errors import KafkaError
from aiokafka.structs import OffsetAndMetadata, TopicPartition
from prometheus_client import Counter, Gauge, REGISTRY, start_http_server
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.database import SessionLocal
from app.persistence import (
    EventIdConflict,
    persist_ingestion_failure,
    persist_telemetry,
)
from app.schemas import TelemetryCreate

logger = logging.getLogger(__name__)

CONSUMER_EVENTS = Counter(
    "waypoint_kafka_consumer_events",
    "Kafka telemetry records committed after durable database handling.",
    ("outcome",),
)
CONSUMER_FAILURES = Counter(
    "waypoint_kafka_consumer_failures",
    "Kafka telemetry records requiring terminal rejection or retry.",
    ("reason",),
)
CONSUMER_LAG = Gauge(
    "waypoint_kafka_consumer_lag",
    "Kafka offsets behind the consumer's committed position.",
    ("topic", "partition"),
)

ProcessOutcome = Literal["persisted", "duplicate", "rejected"]


class BrokerMessage(Protocol):
    topic: str
    partition: int
    offset: int
    value: bytes | None


class OffsetCommitter(Protocol):
    async def commit(
        self,
        offsets: dict[TopicPartition, OffsetAndMetadata],
    ) -> None: ...


def _extract_event_id(value: object) -> str | None:
    if isinstance(value, dict):
        event_id = value.get("event_id")
        if isinstance(event_id, str) and 1 <= len(event_id) <= 100:
            return event_id
    return None


def persist_message(
    session_factory: sessionmaker[Session],
    *,
    topic: str,
    partition: int,
    offset: int,
    value: bytes,
) -> ProcessOutcome:
    decoded: object
    try:
        decoded = json.loads(value)
        payload = TelemetryCreate.model_validate(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, TypeError) as error:
        event_id = None
        try:
            event_id = _extract_event_id(json.loads(value))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            pass
        with session_factory() as db:
            persist_ingestion_failure(
                db,
                topic=topic,
                partition=partition,
                offset=offset,
                event_id=event_id,
                reason="invalid_event",
            )
        CONSUMER_FAILURES.labels("invalid_event").inc()
        logger.error(
            "telemetry_message_rejected",
            extra={
                "topic": topic,
                "partition": partition,
                "offset": offset,
                "event_id": event_id,
                "reason": "invalid_event",
                "error_type": type(error).__name__,
            },
        )
        return "rejected"

    with session_factory() as db:
        try:
            result = persist_telemetry(db, payload)
        except EventIdConflict:
            persist_ingestion_failure(
                db,
                topic=topic,
                partition=partition,
                offset=offset,
                event_id=payload.event_id,
                reason="event_id_conflict",
            )
            CONSUMER_FAILURES.labels("event_id_conflict").inc()
            logger.error(
                "telemetry_message_rejected",
                extra={
                    "topic": topic,
                    "partition": partition,
                    "offset": offset,
                    "event_id": payload.event_id,
                    "reason": "event_id_conflict",
                },
            )
            return "rejected"

    return "duplicate" if result.duplicate else "persisted"


async def consume_message(
    consumer: OffsetCommitter,
    message: BrokerMessage,
    session_factory: sessionmaker[Session] = SessionLocal,
) -> ProcessOutcome:
    outcome = persist_message(
        session_factory,
        topic=message.topic,
        partition=message.partition,
        offset=message.offset,
        value=message.value,
    )
    topic_partition = TopicPartition(message.topic, message.partition)
    await consumer.commit(
        {topic_partition: OffsetAndMetadata(message.offset + 1, "")}
    )
    CONSUMER_EVENTS.labels(outcome).inc()
    return outcome


async def consume_message_with_retry(
    consumer: OffsetCommitter,
    message: BrokerMessage,
    session_factory: sessionmaker[Session] = SessionLocal,
) -> ProcessOutcome:
    for attempt in range(3):
        try:
            return await consume_message(consumer, message, session_factory)
        except (SQLAlchemyError, KafkaError, TimeoutError, OSError) as error:
            CONSUMER_FAILURES.labels("retryable").inc()
            if attempt == 2:
                logger.exception(
                    "telemetry_message_processing_failed",
                    extra={
                        "topic": message.topic,
                        "partition": message.partition,
                        "offset": message.offset,
                        "attempts": attempt + 1,
                        "error_type": type(error).__name__,
                    },
                )
                raise

            delay = random.uniform(0, min(0.5 * (2**attempt), 2.0))
            logger.warning(
                "telemetry_message_processing_retry",
                extra={
                    "topic": message.topic,
                    "partition": message.partition,
                    "offset": message.offset,
                    "attempt": attempt + 1,
                    "error_type": type(error).__name__,
                },
            )
            await asyncio.sleep(delay)
    raise RuntimeError("unreachable consumer retry state")


async def update_consumer_lag(consumer: AIOKafkaConsumer) -> None:
    for topic_partition in consumer.assignment():
        try:
            committed = await consumer.committed(topic_partition)
        except (KafkaError, TimeoutError):
            continue
        highwater = consumer.highwater(topic_partition)
        if committed is not None and highwater is not None:
            CONSUMER_LAG.labels(
                topic_partition.topic,
                str(topic_partition.partition),
            ).set(max(0, highwater - committed))


async def run_consumer() -> None:
    start_http_server(settings.KAFKA_METRICS_PORT, addr="0.0.0.0", registry=REGISTRY)
    consumer = AIOKafkaConsumer(
        settings.KAFKA_TOPIC,
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS.split(","),
        group_id=settings.KAFKA_CONSUMER_GROUP,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        session_timeout_ms=10_000,
        heartbeat_interval_ms=3_000,
    )
    consumer_started = False
    loop = asyncio.get_running_loop()
    stopping = asyncio.Event()
    handled_signals = (signal.SIGINT, signal.SIGTERM)
    for handled_signal in handled_signals:
        loop.add_signal_handler(handled_signal, stopping.set)
    try:
        for attempt in range(3):
            try:
                await asyncio.wait_for(consumer.start(), timeout=10)
                consumer_started = True
                break
            except (KafkaError, TimeoutError, OSError):
                if attempt == 2:
                    raise
                await asyncio.sleep(random.uniform(0, min(0.5 * (2**attempt), 2.0)))

        logger.info(
            "telemetry_consumer_started",
            extra={
                "topic": settings.KAFKA_TOPIC,
                "consumer_group": settings.KAFKA_CONSUMER_GROUP,
            },
        )
        while not stopping.is_set():
            try:
                message = await asyncio.wait_for(consumer.getone(), timeout=1)
            except TimeoutError:
                await update_consumer_lag(consumer)
                continue
            await consume_message_with_retry(consumer, message)
            await update_consumer_lag(consumer)
    finally:
            for handled_signal in handled_signals:
                loop.remove_signal_handler(handled_signal)
            if consumer_started:
                await consumer.stop()


if __name__ == "__main__":
    asyncio.run(run_consumer())
