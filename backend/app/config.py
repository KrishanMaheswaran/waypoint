from __future__ import annotations

import os
from typing import Final

from dotenv import load_dotenv

load_dotenv(dotenv_path="../.env")


class Settings:
    """Application settings loaded from environment variables."""

    APP_NAME: Final[str] = os.getenv("APP_NAME", "waypoint")
    POSTGRES_DB: Final[str] = os.getenv("POSTGRES_DB", "waypoint_db")
    POSTGRES_USER: Final[str] = os.getenv("POSTGRES_USER", "waypoint_user")
    POSTGRES_PASSWORD: Final[str] = os.getenv("POSTGRES_PASSWORD", "waypoint_password")
    POSTGRES_HOST: Final[str] = os.getenv("POSTGRES_HOST", "localhost")
    POSTGRES_PORT: Final[str] = os.getenv("POSTGRES_PORT", "5432")
    DATABASE_URL: Final[str] = os.getenv(
        "DATABASE_URL",
        f"postgresql+psycopg://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}",
    )
    KAFKA_BOOTSTRAP_SERVERS: Final[str] = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    KAFKA_TOPIC: Final[str] = os.getenv("KAFKA_TOPIC", "waypoint.telemetry")
    KAFKA_CONSUMER_GROUP: Final[str] = os.getenv(
        "KAFKA_CONSUMER_GROUP",
        "waypoint-telemetry-persistence",
    )
    KAFKA_PARTITIONS: Final[int] = int(os.getenv("KAFKA_PARTITIONS", "3"))
    KAFKA_METRICS_PORT: Final[int] = int(os.getenv("KAFKA_METRICS_PORT", "8001"))


settings = Settings()
