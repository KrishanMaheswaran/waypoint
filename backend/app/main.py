from __future__ import annotations

from fastapi import FastAPI

from app.config import settings
from app.routes import router

app = FastAPI(title=settings.APP_NAME)
app.include_router(router)


@app.get("/health")
def health_check() -> dict[str, str]:
    """Return service health status for monitoring and smoke tests."""
    return {"status": "ok"}
