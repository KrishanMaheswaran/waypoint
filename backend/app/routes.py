from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import TelemetryEvent, Vehicle
from app.publisher import PublishUnavailable, TelemetryPublisher, get_publisher
from app.schemas import TelemetryCreate, VehicleState, VehicleTelemetryHistory

router = APIRouter()


@router.post("/telemetry", status_code=status.HTTP_202_ACCEPTED, response_model=TelemetryCreate)
async def create_telemetry(
    payload: TelemetryCreate,
    response: Response,
    publisher: TelemetryPublisher = Depends(get_publisher),
) -> TelemetryCreate:
    try:
        await publisher.publish(payload)
    except PublishUnavailable as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="telemetry broker is unavailable; retry with the same event_id",
            headers={"Retry-After": "1"},
        ) from error

    response.headers["X-Waypoint-Delivery"] = "broker-acknowledged"
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
