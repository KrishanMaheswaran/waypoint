from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import random
import time
import uuid
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Sequence
from urllib.parse import urlsplit, urlunsplit

import httpx

STATUSES = ("idle", "charging", "ready", "maintenance")
MAX_EVENTS = 1000
MAX_VEHICLES = 1000
MAX_CONCURRENCY = 32


@dataclass(frozen=True)
class RunStatistics:
    requested_events: int
    successful_events: int
    success_status_counts: dict[str, int]
    http_error_count: int
    transport_error_count: int
    status_counts: dict[str, int]
    transport_error_counts: dict[str, int]
    duration_seconds: float
    successful_events_per_second: float
    request_latency_ms: dict[str, float | None]


def validate_base_url(base_url: str) -> str:
    parsed = urlsplit(base_url)
    if parsed.scheme != "http" or not parsed.hostname:
        raise ValueError("benchmark target must be an http URL on localhost or a loopback IP")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("credentials are not allowed in the benchmark URL")
    if parsed.query or parsed.fragment:
        raise ValueError("query strings and fragments are not allowed in the benchmark URL")

    hostname = parsed.hostname.rstrip(".").lower()
    is_loopback = hostname == "localhost"
    if not is_loopback:
        try:
            is_loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            is_loopback = False
    if not is_loopback:
        raise ValueError("refusing non-loopback target; only localhost and loopback IPs are allowed")

    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError(f"invalid benchmark URL port: {error}") from error
    netloc = f"[{hostname}]" if ":" in hostname else hostname
    if port is not None:
        netloc = f"{netloc}:{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, netloc, path, "", ""))


def generate_event(
    *,
    run_id: str,
    sequence: int,
    vehicle_count: int,
    rng: random.Random,
    timestamp: datetime | None = None,
) -> dict[str, object]:
    if sequence < 0:
        raise ValueError("sequence must be nonnegative")
    if not 1 <= vehicle_count <= MAX_VEHICLES:
        raise ValueError(f"vehicle_count must be between 1 and {MAX_VEHICLES}")
    event_time = timestamp or datetime.now(timezone.utc)
    if event_time.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return {
        "event_id": str(uuid.uuid4()),
        "vehicle_id": f"BENCH-{run_id}-{sequence % vehicle_count + 1:04d}",
        "battery_percentage": round(rng.uniform(20.0, 100.0), 2),
        "status": rng.choice(STATUSES),
        "temperature": round(rng.uniform(18.0, 38.0), 2),
        "fault_code": None,
        "timestamp": event_time.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def percentile(values: Sequence[float], percentage: float) -> float | None:
    if not values:
        return None
    if not 0 <= percentage <= 100:
        raise ValueError("percentage must be between 0 and 100")
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentage / 100
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def summarize(
    *,
    requested_events: int,
    successful_events: int,
    status_codes: Sequence[int],
    transport_errors: Sequence[str],
    latency_seconds: Sequence[float],
    duration_seconds: float,
) -> RunStatistics:
    status_counts = Counter(str(status) for status in status_codes if status < 200 or status >= 300)
    success_status_counts = Counter(str(status) for status in status_codes if 200 <= status < 300)
    transport_error_counts = Counter(transport_errors)
    duration = max(duration_seconds, 0.0)
    return RunStatistics(
        requested_events=requested_events,
        successful_events=successful_events,
        success_status_counts=dict(sorted(success_status_counts.items())),
        http_error_count=sum(status_counts.values()),
        transport_error_count=sum(transport_error_counts.values()),
        status_counts=dict(sorted(status_counts.items())),
        transport_error_counts=dict(sorted(transport_error_counts.items())),
        duration_seconds=duration,
        successful_events_per_second=successful_events / duration if duration else 0.0,
        request_latency_ms={
            "p50": percentile(latency_seconds, 50) * 1000 if latency_seconds else None,
            "p95": percentile(latency_seconds, 95) * 1000 if latency_seconds else None,
            "p99": percentile(latency_seconds, 99) * 1000 if latency_seconds else None,
        },
    )


async def send_events(
    client: httpx.AsyncClient,
    endpoint: str,
    events: Sequence[dict[str, object]],
    concurrency: int,
) -> tuple[int, list[int], list[str], list[float]]:
    semaphore = asyncio.Semaphore(concurrency)
    successful = 0
    status_codes: list[int] = []
    transport_errors: list[str] = []
    latencies: list[float] = []

    async def send(event: dict[str, object]) -> None:
        nonlocal successful
        async with semaphore:
            started = time.perf_counter()
            try:
                response = await client.post(endpoint, json=event)
            except httpx.RequestError as error:
                latencies.append(time.perf_counter() - started)
                transport_errors.append(type(error).__name__)
            else:
                latencies.append(time.perf_counter() - started)
                status_codes.append(response.status_code)
                if 200 <= response.status_code < 300:
                    successful += 1

    await asyncio.gather(*(send(event) for event in events))
    return successful, status_codes, transport_errors, latencies


async def benchmark(
    *,
    base_url: str,
    event_count: int,
    vehicle_count: int,
    concurrency: int,
    warmup_events: int,
    seed: int,
) -> dict[str, object]:
    safe_base_url = validate_base_url(base_url)
    if not 1 <= event_count <= MAX_EVENTS:
        raise ValueError(f"event_count must be between 1 and {MAX_EVENTS}")
    if not 1 <= vehicle_count <= MAX_VEHICLES:
        raise ValueError(f"vehicle_count must be between 1 and {MAX_VEHICLES}")
    if not 1 <= concurrency <= MAX_CONCURRENCY:
        raise ValueError(f"concurrency must be between 1 and {MAX_CONCURRENCY}")
    if not 0 <= warmup_events <= MAX_EVENTS:
        raise ValueError(f"warmup_events must be between 0 and {MAX_EVENTS}")

    run_id = uuid.uuid4().hex[:12]
    rng = random.Random(seed)
    endpoint = f"{safe_base_url}/telemetry"
    client_timeout = httpx.Timeout(connect=3.0, read=5.0, write=5.0, pool=3.0)
    client_limits = httpx.Limits(
        max_connections=concurrency,
        max_keepalive_connections=concurrency,
    )

    async with httpx.AsyncClient(timeout=client_timeout, limits=client_limits) as client:
        warmup = [
            generate_event(
                run_id=run_id,
                sequence=index,
                vehicle_count=vehicle_count,
                rng=rng,
            )
            for index in range(warmup_events)
        ]
        warmup_successes, warmup_statuses, warmup_transport_errors, _ = await send_events(
            client,
            endpoint,
            warmup,
            concurrency,
        )

        measured_events = [
            generate_event(
                run_id=run_id,
                sequence=index,
                vehicle_count=vehicle_count,
                rng=rng,
            )
            for index in range(event_count)
        ]
        started = time.perf_counter()
        successes, statuses, transport_errors, latencies = await send_events(
            client,
            endpoint,
            measured_events,
            concurrency,
        )
        duration = time.perf_counter() - started

    statistics = summarize(
        requested_events=event_count,
        successful_events=successes,
        status_codes=statuses,
        transport_errors=transport_errors,
        latency_seconds=latencies,
        duration_seconds=duration,
    )
    warmup_http_errors = Counter(
        str(code) for code in warmup_statuses if code < 200 or code >= 300
    )
    return {
        "configuration": {
            "base_url": safe_base_url,
            "endpoint": endpoint,
            "event_count": event_count,
            "vehicle_count": vehicle_count,
            "concurrency": concurrency,
            "warmup_events": warmup_events,
            "seed": seed,
            "run_id": run_id,
        },
        "warmup": {
            "requested_events": warmup_events,
            "successful_events": warmup_successes,
            "http_error_count": sum(warmup_http_errors.values()),
            "http_status_counts": dict(sorted(warmup_http_errors.items())),
            "transport_error_count": len(warmup_transport_errors),
            "transport_error_counts": dict(sorted(Counter(warmup_transport_errors).items())),
        },
        "results": asdict(statistics),
        "methodology": {
            "latency_percentiles": "all measured requests, including HTTP and transport failures",
            "successful_events_per_second": "successful measured responses divided by measured wall-clock duration",
            "automatic_retries": False,
        },
    }


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark local Waypoint telemetry ingestion.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--events", type=positive_int, default=100)
    parser.add_argument("--vehicles", type=positive_int, default=20)
    parser.add_argument("--concurrency", type=positive_int, default=8)
    parser.add_argument("--warmup-events", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)
    try:
        validate_base_url(args.base_url)
    except ValueError as error:
        parser.error(str(error))
    for argument, value, maximum in (
        ("events", args.events, MAX_EVENTS),
        ("vehicles", args.vehicles, MAX_VEHICLES),
        ("concurrency", args.concurrency, MAX_CONCURRENCY),
    ):
        if value > maximum:
            parser.error(f"--{argument} must not exceed {maximum}")
    if not 0 <= args.warmup_events <= MAX_EVENTS:
        parser.error(f"--warmup-events must be between 0 and {MAX_EVENTS}")
    return args


async def _main(args: argparse.Namespace) -> None:
    report = await benchmark(
        base_url=args.base_url,
        event_count=args.events,
        vehicle_count=args.vehicles,
        concurrency=args.concurrency,
        warmup_events=args.warmup_events,
        seed=args.seed,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()
