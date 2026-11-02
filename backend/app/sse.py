"""Non-durable vehicle invalidations; PostgreSQL reads remain authoritative."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import suppress

import psycopg
from fastapi import APIRouter, HTTPException, Request
from prometheus_client import Counter, Gauge
from sqlalchemy.engine import make_url
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from app.config import settings

CHANNEL = "waypoint_vehicle_changes"
MAX_CLIENTS = 128
HEARTBEAT_SECONDS = 15
CLIENTS = Gauge("waypoint_sse_clients", "Active SSE clients in this API worker.")
LISTENER_UP = Gauge(
    "waypoint_sse_listener_up", "Whether the PostgreSQL listener is connected."
)
EVENTS = Counter("waypoint_sse_events", "SSE frames generated.", ["kind"])
RECONNECTS = Counter(
    "waypoint_sse_listener_reconnects", "PostgreSQL listener reconnect attempts."
)
OVERFLOWS = Counter(
    "waypoint_sse_queue_overflows", "Client queues replaced with refresh hints."
)


def frame(kind: str, payload: dict[str, str]) -> str:
    # json.dumps escapes line breaks, preventing SSE field injection.
    return f"event: {kind}\ndata: {json.dumps(payload)}\n\n"


class VehicleHub:
    def __init__(self, max_clients: int = MAX_CLIENTS) -> None:
        self.queues: set[asyncio.Queue[str]] = set()
        self.ready = False
        self.max_clients = max_clients

    def subscribe(self) -> asyncio.Queue[str]:
        if not self.ready or len(self.queues) >= self.max_clients:
            raise HTTPException(
                503,
                "Live updates temporarily unavailable",
                headers={"Retry-After": "5"},
            )
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=1)
        self.queues.add(queue)
        CLIENTS.inc()
        return queue

    def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        if queue in self.queues:
            self.queues.remove(queue)
            CLIENTS.dec()

    def broadcast(self, kind: str, payload: dict[str, str]) -> None:
        for queue in self.queues:
            message = frame(kind, payload)
            if queue.full():
                queue.get_nowait()
                OVERFLOWS.inc()
                message = frame("resync", {"reason": "queue_overflow"})
            queue.put_nowait(message)

    async def listen(self) -> None:
        dsn = (
            make_url(settings.DATABASE_URL)
            .set(drivername="postgresql")
            .render_as_string(hide_password=False)
        )
        try:
            while True:
                try:
                    async with await psycopg.AsyncConnection.connect(
                        dsn, autocommit=True, connect_timeout=5
                    ) as connection:
                        await connection.execute(f"LISTEN {CHANNEL}")
                        self.ready = True
                        LISTENER_UP.set(1)
                        self.broadcast("resync", {"reason": "listener_connected"})
                        async for notification in connection.notifies():
                            try:
                                payload = json.loads(notification.payload)
                                vehicle_id = payload["vehicle_id"]
                                if (
                                    not isinstance(vehicle_id, str)
                                    or not 1 <= len(vehicle_id) <= 100
                                ):
                                    continue
                            except (ValueError, KeyError, TypeError):
                                continue
                            self.broadcast("vehicle", {"vehicle_id": vehicle_id})
                except (psycopg.Error, OSError):
                    # Never log connection strings or exception text containing credentials.
                    RECONNECTS.inc()
                finally:
                    self.ready = False
                    LISTENER_UP.set(0)
                    self.broadcast("resync", {"reason": "listener_disconnected"})
                await asyncio.sleep(2)
        finally:
            self.ready = False
            LISTENER_UP.set(0)


async def stream(
    request: Request, hub: VehicleHub, queue: asyncio.Queue[str]
) -> AsyncIterator[str]:
    try:
        # No Last-Event-ID replay: notify is not a durable event log.
        EVENTS.labels("resync").inc()
        yield "retry: 3000\n" + frame("resync", {"reason": "connection_open"})
        while not await request.is_disconnected():
            try:
                message = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                EVENTS.labels("notification").inc()
                yield message
            except TimeoutError:
                EVENTS.labels("heartbeat").inc()
                yield ": heartbeat\n\n"
    finally:
        hub.unsubscribe(queue)


async def unsubscribe_client(hub: VehicleHub, queue: asyncio.Queue[str]) -> None:
    # Starlette runs sync background callbacks in a thread pool. Keep hub mutation
    # on its owning event loop, including when the body never starts iterating.
    hub.unsubscribe(queue)


router = APIRouter()


@router.get("/events/vehicles", response_class=StreamingResponse)
async def vehicle_events(request: Request) -> StreamingResponse:
    hub: VehicleHub = request.app.state.vehicle_hub
    queue = hub.subscribe()
    return StreamingResponse(
        stream(request, hub, queue),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
        background=BackgroundTask(unsubscribe_client, hub, queue),
    )


async def stop_listener(task: asyncio.Task[None]) -> None:
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
