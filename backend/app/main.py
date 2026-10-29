from __future__ import annotations

from fastapi import FastAPI

from app.config import settings
from app.metrics import PrometheusMetricsMiddleware, metrics_response
from app.routes import router

app = FastAPI(title=settings.APP_NAME)
app.add_middleware(PrometheusMetricsMiddleware)
app.include_router(router)
app.add_api_route("/metrics", metrics_response, methods=["GET"], include_in_schema=False)


@app.get("/health")
def health_check() -> dict[str, str]:
    """Return service health status for monitoring and smoke tests."""
    return {"status": "ok"}
