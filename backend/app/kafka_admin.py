from __future__ import annotations

import asyncio
import logging
import random

from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import KafkaError, TopicAlreadyExistsError

from app.config import settings

logger = logging.getLogger(__name__)


async def ensure_topic() -> None:
    admin = AIOKafkaAdminClient(
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS.split(","),
        request_timeout_ms=5_000,
    )
    try:
        for attempt in range(5):
            try:
                await asyncio.wait_for(admin.start(), timeout=10)
                try:
                    await admin.create_topics(
                        [
                            NewTopic(
                                name=settings.KAFKA_TOPIC,
                                num_partitions=settings.KAFKA_PARTITIONS,
                                replication_factor=1,
                            )
                        ]
                    )
                except TopicAlreadyExistsError:
                    logger.info(
                        "telemetry_topic_already_exists",
                        extra={"topic": settings.KAFKA_TOPIC},
                    )
                else:
                    logger.info(
                        "telemetry_topic_created",
                        extra={
                            "topic": settings.KAFKA_TOPIC,
                            "partitions": settings.KAFKA_PARTITIONS,
                        },
                    )
                return
            except KafkaError:
                if attempt == 4:
                    raise
                await asyncio.sleep(random.uniform(0, min(0.5 * (2**attempt), 4.0)))
    finally:
        await admin.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(ensure_topic())
