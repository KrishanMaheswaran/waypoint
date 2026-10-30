from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI
from app.config import settings
from app.metrics import PrometheusMetricsMiddleware, metrics_response
from app.publisher import KafkaTelemetryPublisher
from app.routes import router


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    publisher = KafkaTelemetryPublisher()
    await publisher.start()
    application.state.telemetry_publisher = publisher
    try:
        yield
    finally:
        await publisher.stop()


app = FastAPI(title=settings.APP_NAME, lifespan=lifespan)
app.add_middleware(PrometheusMetricsMiddleware)
app.include_router(router)
app.add_api_route("/metrics", metrics_response, methods=["GET"], include_in_schema=False)


@app.get("/health")
def health_check() -> dict[str, str]:
    """Return service health status for monitoring and smoke tests."""
    return {"status": "ok"}
