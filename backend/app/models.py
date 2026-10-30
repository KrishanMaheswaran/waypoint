from datetime import datetime, timezone

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Vehicle(Base):
    __tablename__ = "vehicles"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    vehicle_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="idle")
    battery_percentage: Mapped[float] = mapped_column(nullable=False, default=0.0)
    temperature: Mapped[float] = mapped_column(nullable=False, default=0.0)
    fault_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    telemetry_events: Mapped[list["TelemetryEvent"]] = relationship(
        "TelemetryEvent",
        back_populates="vehicle",
        cascade="all, delete-orphan",
    )


class TelemetryEvent(Base):
    __tablename__ = "telemetry_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_telemetry_events_event_id"),
        {"extend_existing": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    event_id: Mapped[str] = mapped_column(String(100), nullable=False)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), nullable=False, index=True)
    vehicle: Mapped["Vehicle"] = relationship("Vehicle", back_populates="telemetry_events")
    battery_percentage: Mapped[float] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    temperature: Mapped[float] = mapped_column(nullable=False)
    fault_code: Mapped[str | None] = mapped_column(String(100), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)


class TelemetryIngestionFailure(Base):
    __tablename__ = "telemetry_ingestion_failures"
    __table_args__ = (
        UniqueConstraint(
            "topic",
            "partition",
            "offset",
            name="uq_telemetry_ingestion_failures_source_offset",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    topic: Mapped[str] = mapped_column(String(255), nullable=False)
    partition: Mapped[int] = mapped_column(nullable=False)
    offset: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reason: Mapped[str] = mapped_column(String(50), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
