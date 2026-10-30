from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.publisher import PublishUnavailable, get_publisher
from app.schemas import TelemetryCreate


class RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[TelemetryCreate] = []
        self.unavailable = False

    async def publish(self, event: TelemetryCreate) -> None:
        if self.unavailable:
            raise PublishUnavailable("test broker unavailable")
        self.events.append(event)


@pytest.fixture
def publisher() -> Iterator[RecordingPublisher]:
    instance = RecordingPublisher()
    app.dependency_overrides[get_publisher] = lambda: instance
    yield instance
    app.dependency_overrides.pop(get_publisher, None)


@pytest.fixture
def api_client() -> TestClient:
    return TestClient(app)
