from __future__ import annotations

import re

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def metric_value(metrics: str, name: str, labels: str) -> float:
    match = re.search(rf"^{re.escape(name)}\{{{re.escape(labels)}\}} ([0-9.eE+-]+)$", metrics, re.MULTILINE)
    assert match is not None, f"metric sample missing: {name}{{{labels}}}"
    return float(match.group(1))


def test_metrics_endpoint_exposes_prometheus_format_and_excludes_scrapes() -> None:
    before = client.get("/metrics").text
    before_request_total = metric_value(
        before,
        "waypoint_http_requests_total",
        'method="GET",route="/health",status_code="200"',
    )

    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert "# TYPE waypoint_http_requests_total counter" in response.text
    assert "# TYPE waypoint_http_request_duration_seconds histogram" in response.text
    assert "# TYPE waypoint_telemetry_ingestion_events_total counter" in response.text
    assert 'route="/metrics"' not in response.text
    after = client.get("/metrics").text
    after_request_total = metric_value(
        after,
        "waypoint_http_requests_total",
        'method="GET",route="/health",status_code="200"',
    )
    assert after_request_total == before_request_total


def test_request_metrics_use_route_templates_and_bounded_labels() -> None:
    client.get("/vehicles/unique-vehicle-for-metrics/telemetry")
    client.get("/an-unmatched-path-with-an-identifier")

    metrics = client.get("/metrics").text

    assert (
        'method="GET",route="/vehicles/{vehicle_id}/telemetry",status_code="404"'
        in metrics
    )
    assert 'route="__unmatched__"' in metrics
    assert "unique-vehicle-for-metrics" not in metrics
    assert "unmatched-path-with-an-identifier" not in metrics


def test_telemetry_ingestion_counters_track_publish_acceptance_and_failure(
    publisher,
) -> None:
    response = client.post(
        "/telemetry",
        json={
            "event_id": "metrics-test-event",
            "vehicle_id": "METRICS-TEST-VEHICLE",
            "battery_percentage": 60,
            "status": "ready",
            "temperature": 23,
            "fault_code": None,
            "timestamp": "2026-10-29T12:00:00Z",
        },
    )
    duplicate_delivery = client.post(
        "/telemetry",
        json={
            "event_id": "metrics-test-event",
            "vehicle_id": "METRICS-TEST-VEHICLE",
            "battery_percentage": 59,
            "status": "ready",
            "temperature": 23,
            "fault_code": None,
            "timestamp": "2026-10-29T12:00:00Z",
        },
    )

    assert response.status_code == 202
    assert duplicate_delivery.status_code == 202
    publisher.unavailable = True
    unavailable = client.post(
        "/telemetry",
        json={
            "event_id": "metrics-broker-unavailable",
            "vehicle_id": "METRICS-TEST-VEHICLE-2",
            "battery_percentage": 60,
            "status": "ready",
            "temperature": 23,
            "fault_code": None,
            "timestamp": "2026-10-29T12:00:00Z",
        },
    )
    assert unavailable.status_code == 503
    metrics = client.get("/metrics").text
    assert metric_value(metrics, "waypoint_telemetry_ingestion_events_total", 'outcome="success"') >= 1
    assert metric_value(metrics, "waypoint_telemetry_ingestion_events_total", 'outcome="failure"') >= 1
    assert "# TYPE waypoint_telemetry_publish_events_total counter" in metrics
    assert "METRICS-TEST-VEHICLE" not in metrics
    assert "metrics-test-event" not in metrics
