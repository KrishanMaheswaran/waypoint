from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import TelemetryEvent, TelemetryIngestionFailure, Vehicle
from app.schemas import TelemetryCreate


class EventIdConflict(Exception):
    def __init__(self, event_id: str) -> None:
        super().__init__(f"event_id '{event_id}' was already used for a different event")
        self.event_id = event_id


@dataclass(frozen=True)
class PersistenceResult:
    duplicate: bool


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _matches_event(event: TelemetryEvent, payload: TelemetryCreate) -> bool:
    return (
        event.vehicle.vehicle_id == payload.vehicle_id
        and event.battery_percentage == payload.battery_percentage
        and event.status == payload.status
        and event.temperature == payload.temperature
        and event.fault_code == payload.fault_code
        and _as_utc(event.timestamp) == _as_utc(payload.timestamp)
    )


def _get_or_create_vehicle(db: Session, payload: TelemetryCreate) -> Vehicle:
    vehicle = db.scalar(
        select(Vehicle)
        .where(Vehicle.vehicle_id == payload.vehicle_id)
        .with_for_update()
    )
    if vehicle is not None:
        return vehicle

    try:
        with db.begin_nested():
            vehicle = Vehicle(
                vehicle_id=payload.vehicle_id,
                battery_percentage=payload.battery_percentage,
                status=payload.status,
                temperature=payload.temperature,
                fault_code=payload.fault_code,
                last_updated=payload.timestamp,
            )
            db.add(vehicle)
            db.flush()
    except IntegrityError:
        vehicle = db.scalar(
            select(Vehicle)
            .where(Vehicle.vehicle_id == payload.vehicle_id)
            .with_for_update()
        )
        if vehicle is None:
            raise

    return vehicle


def persist_telemetry(db: Session, payload: TelemetryCreate) -> PersistenceResult:
    existing = db.scalar(select(TelemetryEvent).where(TelemetryEvent.event_id == payload.event_id))
    if existing is not None:
        if not _matches_event(existing, payload):
            raise EventIdConflict(payload.event_id)
        return PersistenceResult(duplicate=True)

    try:
        vehicle = _get_or_create_vehicle(db, payload)
        event = TelemetryEvent(
            event_id=payload.event_id,
            vehicle_id=vehicle.id,
            battery_percentage=payload.battery_percentage,
            status=payload.status,
            temperature=payload.temperature,
            fault_code=payload.fault_code,
            timestamp=payload.timestamp,
        )
        db.add(event)
        db.flush()

        if _as_utc(payload.timestamp) > _as_utc(vehicle.last_updated):
            vehicle.battery_percentage = payload.battery_percentage
            vehicle.status = payload.status
            vehicle.temperature = payload.temperature
            vehicle.fault_code = payload.fault_code
            vehicle.last_updated = payload.timestamp

        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.scalar(select(TelemetryEvent).where(TelemetryEvent.event_id == payload.event_id))
        if existing is None:
            raise
        if not _matches_event(existing, payload):
            raise EventIdConflict(payload.event_id) from None
        return PersistenceResult(duplicate=True)

    return PersistenceResult(duplicate=False)


def persist_ingestion_failure(
    db: Session,
    *,
    topic: str,
    partition: int,
    offset: int,
    event_id: str | None,
    reason: str,
) -> None:
    identity = (
        TelemetryIngestionFailure.topic == topic,
        TelemetryIngestionFailure.partition == partition,
        TelemetryIngestionFailure.offset == offset,
    )
    if db.scalar(select(TelemetryIngestionFailure.id).where(*identity)) is not None:
        return

    db.add(
        TelemetryIngestionFailure(
            topic=topic,
            partition=partition,
            offset=offset,
            event_id=event_id,
            reason=reason,
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        if db.scalar(select(TelemetryIngestionFailure.id).where(*identity)) is None:
            raise
