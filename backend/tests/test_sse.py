from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from app import sse
from fastapi import HTTPException


def test_admission_and_bounded_overflow():
    hub = sse.VehicleHub(max_clients=1)
    with pytest.raises(HTTPException) as unavailable:
        hub.subscribe()
    assert unavailable.value.status_code == 503
    hub.ready = True
    queue = hub.subscribe()
    try:
        with pytest.raises(HTTPException):
            hub.subscribe()
        hub.broadcast("vehicle", {"vehicle_id": "one"})
        hub.broadcast("vehicle", {"vehicle_id": "two"})
        assert queue.qsize() == 1
        assert "event: resync" in queue.get_nowait()
    finally:
        hub.unsubscribe(queue)
        hub.unsubscribe(queue)
    assert not hub.queues


def test_stream_resync_heartbeat_delivery_and_cleanup(monkeypatch):
    async def scenario():
        hub = sse.VehicleHub()
        hub.ready = True
        queue = hub.subscribe()

        async def connected():
            return False

        request = SimpleNamespace(is_disconnected=connected)
        generator = sse.stream(request, hub, queue)
        assert "connection_open" in await anext(generator)
        assert ": heartbeat" in await anext(generator)
        hub.broadcast("vehicle", {"vehicle_id": "VF-1"})
        assert "VF-1" in await anext(generator)
        await generator.aclose()
        assert not hub.queues

    monkeypatch.setattr(sse, "HEARTBEAT_SECONDS", 0.001)
    asyncio.run(scenario())


def test_listener_reconnect_resync_and_malformed_payload(monkeypatch):
    async def scenario():
        hub = sse.VehicleHub()
        hub.ready = True
        queue = hub.subscribe()
        attempts = 0
        delivered = asyncio.Event()

        class Connection:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def execute(self, sql):
                assert sql == "LISTEN waypoint_vehicle_changes"

            async def notifies(self):
                yield SimpleNamespace(payload="[]")
                yield SimpleNamespace(payload=json.dumps({"vehicle_id": "VF-1"}))
                delivered.set()
                await asyncio.Event().wait()

        async def connect(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("isolated disconnect")
            return Connection()

        original_sleep = asyncio.sleep

        async def quick_sleep(seconds):
            await original_sleep(0)

        monkeypatch.setattr(sse.psycopg.AsyncConnection, "connect", connect)
        monkeypatch.setattr(sse.asyncio, "sleep", quick_sleep)
        task = asyncio.create_task(hub.listen())
        await asyncio.wait_for(delivered.wait(), 1)
        assert attempts == 2 and hub.ready
        # Disconnection/reconnection and subsequent bursts collapse to a refresh.
        assert "resync" in queue.get_nowait()
        await sse.stop_listener(task)
        assert not hub.ready
        hub.unsubscribe(queue)

    asyncio.run(scenario())


def test_background_cleanup_stays_on_event_loop():
    async def scenario():
        hub = sse.VehicleHub()
        hub.ready = True
        request = SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(vehicle_hub=hub))
        )
        response = await sse.vehicle_events(request)
        assert len(hub.queues) == 1
        assert response.background.is_async
        await response.background()
        assert not hub.queues

    asyncio.run(scenario())


def test_publisher_stops_even_if_listener_failed(monkeypatch):
    from unittest.mock import AsyncMock

    from app import main

    async def scenario():
        publisher = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
        monkeypatch.setattr(main, "KafkaTelemetryPublisher", lambda: publisher)

        async def failed(_):
            raise RuntimeError("listener failed")

        monkeypatch.setattr(sse.VehicleHub, "listen", failed)
        application = SimpleNamespace(state=SimpleNamespace())
        with pytest.raises(RuntimeError, match="listener failed"):
            async with main.lifespan(application):
                await asyncio.sleep(0)
        publisher.stop.assert_awaited_once()

    asyncio.run(scenario())
