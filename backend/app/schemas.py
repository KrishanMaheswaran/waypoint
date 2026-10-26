from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

VehicleStatus = Literal["idle", "charging", "ready", "maintenance"]


class TelemetryCreate(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    vehicle_id: str = Field(..., min_length=1, max_length=100)
    battery_percentage: float = Field(..., ge=0, le=100)
    status: VehicleStatus
    temperature: float = Field(..., ge=-100, le=200)
    fault_code: str | None = Field(default=None, max_length=100)
    timestamp: datetime


class TelemetryRead(TelemetryCreate):
    id: int


class VehicleState(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    vehicle_id: str
    battery_percentage: float
    status: VehicleStatus
    temperature: float
    fault_code: str | None = None
    last_updated: datetime


class VehicleTelemetryHistory(TelemetryRead):
    pass
