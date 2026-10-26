from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app

engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base.metadata.create_all(bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


def reset_db() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def setup_db() -> None:
    reset_db()


def test_post_telemetry_creates_vehicle_and_event() -> None:
    payload = {
        "vehicle_id": "VF-1001",
        "battery_percentage": 88.5,
        "status": "ready",
        "temperature": 24.7,
        "fault_code": None,
        "timestamp": "2026-10-26T12:00:00Z",
    }

    response = client.post("/telemetry", json=payload)

    assert response.status_code == 201
    data = response.json()
    assert data["vehicle_id"] == "VF-1001"
    assert data["battery_percentage"] == 88.5
    assert data["status"] == "ready"
    assert data["temperature"] == 24.7
    assert data["fault_code"] is None
    assert data["timestamp"] == "2026-10-26T12:00:00Z"


def test_get_vehicles_returns_latest_known_state() -> None:
    client.post(
        "/telemetry",
        json={
            "vehicle_id": "VF-1002",
            "battery_percentage": 50,
            "status": "charging",
            "temperature": 31.2,
            "fault_code": "BMS-01",
            "timestamp": "2026-10-26T12:10:00Z",
        },
    )

    response = client.get("/vehicles")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["vehicle_id"] == "VF-1002"
    assert payload[0]["battery_percentage"] == 50
    assert payload[0]["status"] == "charging"
    assert payload[0]["fault_code"] == "BMS-01"


def test_get_vehicle_telemetry_history_returns_recent_events() -> None:
    client.post(
        "/telemetry",
        json={
            "vehicle_id": "VF-1003",
            "battery_percentage": 80,
            "status": "idle",
            "temperature": 22.5,
            "fault_code": None,
            "timestamp": "2026-10-26T10:00:00Z",
        },
    )
    client.post(
        "/telemetry",
        json={
            "vehicle_id": "VF-1003",
            "battery_percentage": 82,
            "status": "ready",
            "temperature": 23.1,
            "fault_code": None,
            "timestamp": "2026-10-26T10:05:00Z",
        },
    )

    response = client.get("/vehicles/VF-1003/telemetry")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 2
    assert payload[0]["battery_percentage"] == 82
    assert payload[0]["status"] == "ready"
    assert payload[0]["vehicle_id"] == "VF-1003"


def test_invalid_payload_returns_422() -> None:
    response = client.post(
        "/telemetry",
        json={
            "vehicle_id": "VF-1004",
            "battery_percentage": 150,
            "status": "running",
            "temperature": 25,
        },
    )

    assert response.status_code == 422


def test_unknown_vehicle_history_returns_404() -> None:
    response = client.get("/vehicles/UNKNOWN/telemetry")

    assert response.status_code == 404
