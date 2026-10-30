from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app

test_database_url = os.getenv("WAYPOINT_TEST_DATABASE_URL")
if test_database_url:
    engine = create_engine(test_database_url, pool_pre_ping=True)
else:
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
        "event_id": "test-event-vf-1001",
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
    assert data["event_id"] == "test-event-vf-1001"
    assert data["battery_percentage"] == 88.5
    assert data["status"] == "ready"
    assert data["temperature"] == 24.7
    assert data["fault_code"] is None
    assert data["timestamp"] == "2026-10-26T12:00:00Z"


def test_get_vehicles_returns_latest_known_state() -> None:
    client.post(
        "/telemetry",
        json={
            "event_id": "test-event-vf-1002",
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
            "event_id": "test-event-vf-1003-1",
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
            "event_id": "test-event-vf-1003-2",
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
            "event_id": "test-event-invalid",
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


def test_identical_event_retry_is_successful_without_duplicate() -> None:
    payload = {
        "event_id": "retry-event-1",
        "vehicle_id": "VF-2001",
        "battery_percentage": 73.5,
        "status": "ready",
        "temperature": 26.0,
        "fault_code": None,
        "timestamp": "2026-10-26T12:00:00Z",
    }

    first = client.post("/telemetry", json=payload)
    retry = client.post("/telemetry", json=payload)

    assert first.status_code == 201
    assert retry.status_code == 200
    assert retry.json()["event_id"] == payload["event_id"]
    history = client.get("/vehicles/VF-2001/telemetry")
    assert history.status_code == 200
    assert len(history.json()) == 1


def test_reusing_event_id_with_different_payload_returns_conflict() -> None:
    payload = {
        "event_id": "conflicting-event-1",
        "vehicle_id": "VF-2002",
        "battery_percentage": 73.5,
        "status": "ready",
        "temperature": 26.0,
        "fault_code": None,
        "timestamp": "2026-10-26T12:00:00Z",
    }
    conflicting_payload = {**payload, "battery_percentage": 72.0}

    assert client.post("/telemetry", json=payload).status_code == 201
    response = client.post("/telemetry", json=conflicting_payload)

    assert response.status_code == 409
    assert len(client.get("/vehicles/VF-2002/telemetry").json()) == 1


def test_out_of_order_event_is_stored_without_overwriting_latest_state() -> None:
    newest_payload = {
        "event_id": "out-of-order-newer",
        "vehicle_id": "VF-2003",
        "battery_percentage": 80.0,
        "status": "ready",
        "temperature": 24.0,
        "fault_code": None,
        "timestamp": "2026-10-26T12:10:00Z",
    }
    older_payload = {
        **newest_payload,
        "event_id": "out-of-order-older",
        "battery_percentage": 35.0,
        "status": "charging",
        "timestamp": "2026-10-26T12:00:00Z",
    }

    assert client.post("/telemetry", json=newest_payload).status_code == 201
    assert client.post("/telemetry", json=older_payload).status_code == 201

    current_state = client.get("/vehicles").json()
    vehicle = next(item for item in current_state if item["vehicle_id"] == "VF-2003")
    assert vehicle["battery_percentage"] == newest_payload["battery_percentage"]
    assert vehicle["status"] == newest_payload["status"]
    assert vehicle["last_updated"].startswith("2026-10-26T12:10:00")

    history = client.get("/vehicles/VF-2003/telemetry").json()
    assert len(history) == 2
    assert {event["event_id"] for event in history} == {
        newest_payload["event_id"],
        older_payload["event_id"],
    }
