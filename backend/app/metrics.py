from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.requests import Request
from starlette.responses import Response

HTTP_REQUESTS = Counter(
    "waypoint_http_requests",
    "HTTP requests served by the Waypoint API.",
    ("method", "route", "status_code"),
)
HTTP_REQUEST_DURATION = Histogram(
    "waypoint_http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ("method", "route"),
)
TELEMETRY_INGESTION_EVENTS = Counter(
    "waypoint_telemetry_ingestion_events",
    "Telemetry API requests by HTTP outcome.",
    ("outcome",),
)
TELEMETRY_PUBLISH_EVENTS = Counter(
    "waypoint_telemetry_publish_events",
    "Telemetry events acknowledged or rejected by the Kafka producer.",
    ("outcome",),
)

_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}


def _method_label(method: str) -> str:
    return method if method in _METHODS else "OTHER"


def _route_label(scope: dict[str, Any]) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "__unmatched__"


class PrometheusMetricsMiddleware:
    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[..., Awaitable[dict[str, Any]]],
        send: Callable[..., Awaitable[None]],
    ) -> None:
        if scope["type"] != "http" or scope.get("path") == "/metrics":
            await self.app(scope, receive, send)
            return

        method = _method_label(scope.get("method", "OTHER"))
        started = time.perf_counter()
        response_status = 500

        async def record_response(message: dict[str, Any]) -> None:
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, record_response)
        finally:
            route = _route_label(scope)
            status_label = str(response_status)
            HTTP_REQUESTS.labels(method, route, status_label).inc()
            HTTP_REQUEST_DURATION.labels(method, route).observe(time.perf_counter() - started)

            if method == "POST" and route == "/telemetry":
                outcome = "success" if 200 <= response_status < 300 else "failure"
                TELEMETRY_INGESTION_EVENTS.labels(outcome).inc()


async def metrics_response(_: Request) -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
