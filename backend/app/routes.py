from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import TelemetryEvent, Vehicle
from app.schemas import TelemetryCreate, VehicleState, VehicleTelemetryHistory

router = APIRouter()


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


def _handle_duplicate(
    event: TelemetryEvent | None,
    payload: TelemetryCreate,
    response: Response,
) -> TelemetryCreate | None:
    if event is None:
        return None
    if not _matches_event(event, payload):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"event_id '{payload.event_id}' was already used for a different event",
        )
    response.status_code = status.HTTP_200_OK
    return payload


@router.post("/telemetry", status_code=status.HTTP_201_CREATED, response_model=TelemetryCreate)
def create_telemetry(
    payload: TelemetryCreate,
    response: Response,
    db: Session = Depends(get_db),
) -> TelemetryCreate:
    """Persist an event once and update state only when its timestamp is newer."""
    existing = db.scalar(select(TelemetryEvent).where(TelemetryEvent.event_id == payload.event_id))
    duplicate = _handle_duplicate(existing, payload, response)
    if duplicate is not None:
        return duplicate

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
        duplicate = _handle_duplicate(existing, payload, response)
        if duplicate is not None:
            return duplicate
        raise

    return payload


@router.get("/vehicles", response_model=list[VehicleState])
def list_vehicles(db: Session = Depends(get_db)) -> list[VehicleState]:
    """Return the latest known state of each vehicle."""
    vehicles = db.scalars(select(Vehicle).order_by(Vehicle.vehicle_id)).all()
    return [
        VehicleState(
            vehicle_id=vehicle.vehicle_id,
            battery_percentage=vehicle.battery_percentage,
            status=vehicle.status,
            temperature=vehicle.temperature,
            fault_code=vehicle.fault_code,
            last_updated=vehicle.last_updated,
        )
        for vehicle in vehicles
    ]


@router.get("/vehicles/{vehicle_id}/telemetry", response_model=list[VehicleTelemetryHistory])
def get_vehicle_telemetry(
    vehicle_id: str,
    limit: int = Query(default=50, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[VehicleTelemetryHistory]:
    """Return recent telemetry events for a specific vehicle."""
    vehicle = db.scalar(select(Vehicle).where(Vehicle.vehicle_id == vehicle_id))
    if vehicle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Vehicle '{vehicle_id}' not found")

    events = (
        db.scalars(
            select(TelemetryEvent)
            .where(TelemetryEvent.vehicle_id == vehicle.id)
            .order_by(TelemetryEvent.timestamp.desc())
            .limit(limit)
        )
        .all()
    )

    return [
        VehicleTelemetryHistory(
            id=event.id,
            event_id=event.event_id,
            vehicle_id=vehicle.vehicle_id,
            battery_percentage=event.battery_percentage,
            status=event.status,
            temperature=event.temperature,
            fault_code=event.fault_code,
            timestamp=event.timestamp,
        )
        for event in events
    ]
