"""Owned, disposable asynchronous performance suite. No externally supplied target URLs."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import psycopg
from prometheus_client.parser import text_string_to_metric_families

from benchmarks.run_benchmark import percentile

ROOT = Path(__file__).resolve().parents[1]
SERVICES = ("db", "redpanda", "backend", "consumer", "frontend")


def command(args, **kwargs):
    return subprocess.check_output(args, text=True, timeout=180, **kwargs).strip()


@dataclass(frozen=True)
class Workload:
    name: str
    events: int
    concurrency: int
    vehicles: int = 100
    rate: float = 0

    def validate(self):
        if not re.fullmatch(r"[a-z0-9-]{1,40}", self.name):
            raise ValueError("invalid scenario name")
        if not 1 <= self.events <= 5000 or not 1 <= self.concurrency <= 32:
            raise ValueError("events must be 1..5000; concurrency 1..32")
        if not 1 <= self.vehicles <= 1000 or not 0 <= self.rate <= 500:
            raise ValueError("vehicles must be 1..1000; rate 0..500")


def quantiles(values):
    return {f"p{p}": percentile(values, p) for p in (50, 95, 99)}


def metrics(text):
    return {
        s.name + json.dumps(s.labels, sort_keys=True): s.value
        for family in text_string_to_metric_families(text)
        for s in family.samples
        if s.name.startswith("waypoint_")
    }


def total(samples, prefix):
    return sum(v for k, v in samples.items() if k.split("{")[0] == prefix)


def reconcile(expected, rows, latest):
    """Compare full event contents and latest timestamp per vehicle, not just counts."""
    by_id = {r["event_id"]: r for r in rows}
    if len(rows) != len(by_id) or set(by_id) != {e["event_id"] for e in expected}:
        raise AssertionError("event IDs/counts differ")
    wanted = {}
    for e in expected:
        r = by_id[e["event_id"]]
        for k in (
            "vehicle_id",
            "battery_percentage",
            "temperature",
            "status",
            "fault_code",
        ):
            if r[k] != e[k]:
                raise AssertionError(f"event payload differs: {k}")
        if datetime.fromisoformat(r["timestamp"]) != datetime.fromisoformat(
            e["timestamp"]
        ):
            raise AssertionError("event timestamp differs")
        v = e["vehicle_id"]
        if v not in wanted or e["timestamp"] > wanted[v]["timestamp"]:
            wanted[v] = e
    for v, e in wanted.items():
        r = latest[v]
        for k in ("battery_percentage", "temperature", "status", "fault_code"):
            if r[k] != e[k]:
                raise AssertionError(f"latest state differs: {k}")
        if datetime.fromisoformat(r["last_updated"]) != datetime.fromisoformat(
            e["timestamp"]
        ):
            raise AssertionError("latest timestamp differs")
    return {
        "events": len(rows),
        "vehicles": len(wanted),
        "payloads_and_latest_correct": True,
    }


def assert_owned(inspect, project, service):
    labels = inspect["Config"]["Labels"]
    if (
        labels.get("com.docker.compose.project") != project
        or labels.get("com.docker.compose.service") != service
    ):
        raise ValueError("refusing container outside owned benchmark project")
    if not re.fullmatch(r"waypoint-m8b-[0-9a-f]{12}", project):
        raise ValueError("invalid benchmark project")
    if any(m["Type"] == "volume" for m in inspect["Mounts"]):
        raise ValueError("benchmark must not use named/anonymous volumes")


class Stack:
    def __init__(self):
        self.run = uuid.uuid4().hex[:12]
        self.project = "waypoint-m8b-" + self.run
        self.env = {**os.environ, "M8B_RUN": self.run}
        # An empty env file prevents Compose from inheriting the repository .env.
        self.base = [
            "docker",
            "compose",
            "--env-file",
            os.devnull,
            "-p",
            self.project,
            "-f",
            str(ROOT / "benchmarks/compose.performance.yml"),
        ]
        self.ids = {}
        self.urls = {}

    def compose(self, *args):
        return command(self.base + list(args), env=self.env)

    def inspect(self, service):
        cid = self.ids.get(service) or self.compose("ps", "-q", service)
        info = json.loads(command(["docker", "inspect", cid]))[0]
        assert_owned(info, self.project, service)
        return info

    def endpoint(self, service, port):
        info = self.inspect(service)
        bindings = info["NetworkSettings"]["Ports"][f"{port}/tcp"]
        if len(bindings) != 1 or bindings[0]["HostIp"] != "127.0.0.1":
            raise ValueError("only owned loopback port mappings allowed")
        return "http://127.0.0.1:" + bindings[0]["HostPort"]

    def setup(self):
        self.compose("config", "--quiet")
        self.compose("up", "-d", "--wait", "db", "redpanda")
        self.compose("build", "backend", "consumer", "kafka-init", "frontend")
        self.compose("run", "--rm", "--no-deps", "kafka-init")
        self.compose(
            "run", "--rm", "--no-deps", "backend", "alembic", "upgrade", "head"
        )
        self.compose("up", "-d", "--wait", "backend", "consumer")
        self.compose("up", "-d", "frontend")
        for service in SERVICES:
            self.ids[service] = self.compose("ps", "-q", service)
            self.inspect(service)
        self.api = self.endpoint("frontend", 80) + "/api"
        self.backend = self.endpoint("backend", 8000)
        self.consumer = self.endpoint("consumer", 8001)
        port = self.endpoint("db", 5432).rsplit(":", 1)[1]
        self.dsn = f"postgresql://m8b:m8b_disposable_only@127.0.0.1:{port}/m8b"

    def cleanup(self):
        # Scope is immutable and freshly generated; no user-supplied Compose project.
        for service in self.ids:
            self.inspect(service)
        self.compose("down", "--remove-orphans", "--timeout", "20")
        remaining = command(
            [
                "docker",
                "ps",
                "-aq",
                "--filter",
                "label=com.docker.compose.project=" + self.project,
            ]
        )
        networks = command(
            [
                "docker",
                "network",
                "ls",
                "-q",
                "--filter",
                "label=com.docker.compose.project=" + self.project,
            ]
        )
        volumes = command(
            [
                "docker",
                "volume",
                "ls",
                "-q",
                "--filter",
                "label=com.docker.compose.project=" + self.project,
            ]
        )
        if remaining or networks or volumes:
            raise RuntimeError("benchmark resources remain")
        for service in ("backend", "consumer", "frontend", "kafka-init"):
            tag = self.project + "-" + service
            if command(["docker", "image", "ls", "-q", tag]):
                command(["docker", "image", "rm", tag])

    def sql(self, query):
        return self.compose(
            "exec", "-T", "db", "psql", "-U", "m8b", "-d", "m8b", "-Atc", query
        )

    def resources(self):
        samples = []
        output = command(
            [
                "docker",
                "stats",
                "--no-stream",
                "--format",
                "{{json .}}",
                *self.ids.values(),
            ]
        )
        for line in output.splitlines():
            x = json.loads(line)
            used, limit = x["MemUsage"].split(" / ")
            samples.append(
                {
                    "service": next(
                        k for k, v in self.ids.items() if v.startswith(x["ID"])
                    ),
                    "cpu_percent": float(x["CPUPerc"].rstrip("%")),
                    "memory_percent": float(x["MemPerc"].rstrip("%")),
                    "memory_used": used,
                    "memory_limit": limit,
                    "pids": int(x["PIDs"]),
                }
            )
        return samples


async def bounded_wait(predicate, timeout=120, interval=0.075):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await predicate():
            return
        await asyncio.sleep(interval)
    raise TimeoutError("bounded eventual observation timed out")


def local_client(**kwargs):
    # Environment proxies must never redirect benchmark traffic.
    return httpx.AsyncClient(trust_env=False, **kwargs)


class Streams:
    def __init__(self, url, count=2):
        self.url, self.count = url, count
        self.frames, self.errors, self.tasks = [], [], []
        self.ready = [asyncio.Event() for _ in range(count)]

    async def read(self, index):
        try:
            async with (
                local_client(timeout=httpx.Timeout(25, connect=5)) as client,
                client.stream("GET", self.url) as response,
            ):
                response.raise_for_status()
                assert response.headers["content-type"].startswith("text/event-stream")
                kind = ""
                async for line in response.aiter_lines():
                    if line.startswith("event: "):
                        kind = line[7:]
                    if line.startswith("data: "):
                        self.frames.append(
                            {
                                "client": index,
                                "kind": kind,
                                "payload": json.loads(line[6:]),
                                "at": time.monotonic(),
                            }
                        )
                        self.ready[index].set()
                    if line.startswith(": heartbeat"):
                        self.frames.append(
                            {
                                "client": index,
                                "kind": "heartbeat",
                                "at": time.monotonic(),
                            }
                        )
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - surface task failures at the run health gate
            self.errors.append(type(error).__name__)

    async def start(self):
        self.tasks = [asyncio.create_task(self.read(i)) for i in range(self.count)]
        await asyncio.wait_for(asyncio.gather(*(e.wait() for e in self.ready)), 10)

    async def close(self):
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)


async def send_one(client, url, event, retries=1):
    started = time.monotonic()
    attempts = []
    for attempt in range(retries + 1):
        tick = time.monotonic()
        try:
            r = await client.post(url, json=event)
            status, error = r.status_code, None
            if (
                status == 202
                and r.headers.get("x-waypoint-delivery") != "broker-acknowledged"
            ):
                raise AssertionError("missing broker acknowledgement header")
        except httpx.RequestError as exc:
            status, error = None, type(exc).__name__
        attempts.append(
            {
                "status": status,
                "error": error,
                "latency_ms": (time.monotonic() - tick) * 1000,
            }
        )
        if status == 202 or (status is not None and status < 500) or attempt == retries:
            break
        await asyncio.sleep(0.1 * (attempt + 1))
    return {
        "event_id": event["event_id"],
        "vehicle_id": event["vehicle_id"],
        "start": started,
        "end": time.monotonic(),
        "attempts": attempts,
        "ok": status == 202,
    }


async def workload(stack, spec, streams):
    spec.validate()
    token = stack.run + "-" + spec.name
    base_time = datetime.now(UTC)
    events = [
        {
            "event_id": f"{token}-{i}",
            "vehicle_id": f"BENCH-{token}-{i % spec.vehicles}",
            "battery_percentage": 20 + i % 80,
            "temperature": 20 + i % 15,
            "status": "ready",
            "fault_code": None,
            "timestamp": (base_time + timedelta(microseconds=i)).isoformat(),
        }
        for i in range(spec.events)
    ]
    expected_ids = {e["event_id"] for e in events}
    observations, requests, lag, resources = {}, [], [], []
    done = asyncio.Event()
    first_frame = len(streams.frames)
    async with await psycopg.AsyncConnection.connect(stack.dsn, autocommit=True) as db:
        await db.execute("SET default_transaction_read_only=on")
        async with local_client(
            timeout=20, limits=httpx.Limits(max_connections=spec.concurrency)
        ) as client:

            async def getmetrics(url):
                r = await client.get(url + "/metrics")
                r.raise_for_status()
                return metrics(r.text)

            before = await getmetrics(stack.backend)
            consumer_before = await getmetrics(stack.consumer)

            async def observe():
                # One serial writer commits in ID order. Observer includes warm-up only in its own scenario.
                watermark = 0
                while not done.is_set():
                    cursor = await db.execute(
                        "SELECT id,event_id FROM telemetry_events WHERE id>%s ORDER BY id",
                        (watermark,),
                    )
                    rows = await cursor.fetchall()
                    tick = time.monotonic()
                    for row_id, eid in rows:
                        watermark = max(watermark, row_id)
                        if eid in expected_ids:
                            observations.setdefault(eid, tick)
                    sample = await getmetrics(stack.consumer)
                    lag.append(
                        {
                            "at": tick,
                            "lag": total(sample, "waypoint_kafka_consumer_lag"),
                            "persisted_counter": total(
                                sample, "waypoint_kafka_consumer_events_total"
                            ),
                        }
                    )
                    if len(observations) == spec.events:
                        return
                    await asyncio.sleep(0.075)

            async def resource_loop():
                while not done.is_set():
                    resources.append(
                        {
                            "at": time.monotonic(),
                            "containers": await asyncio.to_thread(stack.resources),
                        }
                    )
                    if any(
                        x["memory_percent"] > 85 for x in resources[-1]["containers"]
                    ):
                        raise RuntimeError("memory safety threshold exceeded")
                    await asyncio.sleep(1)

            observer = asyncio.create_task(observe())
            resource_task = asyncio.create_task(resource_loop())
            started = time.monotonic()
            next_index = 0

            async def worker():
                nonlocal next_index
                while next_index < spec.events:
                    if resource_task.done():
                        resource_task.result()
                    i = next_index
                    next_index += 1
                    if spec.rate:
                        await asyncio.sleep(
                            max(0, started + i / spec.rate - time.monotonic())
                        )
                    requests.append(
                        await send_one(client, stack.api + "/telemetry", events[i])
                    )

            try:
                await asyncio.wait_for(
                    asyncio.gather(*(worker() for _ in range(spec.concurrency))), 120
                )
                ack_finished = time.monotonic()
                await asyncio.wait_for(observer, 120)
                observed_finished = max(observations.values())

                async def drained():
                    sample = await getmetrics(stack.consumer)
                    return (
                        total(sample, "waypoint_kafka_consumer_lag") == 0
                        and total(sample, "waypoint_kafka_consumer_events_total")
                        >= total(
                            consumer_before, "waypoint_kafka_consumer_events_total"
                        )
                        + spec.events
                    )

                await bounded_wait(drained, 20)
                after = await getmetrics(stack.backend)
                consumer_after = await getmetrics(stack.consumer)
            finally:
                done.set()
                observer.cancel()
                await asyncio.gather(observer, return_exceptions=True)
                await resource_task
            rows_cursor = await db.execute(
                "SELECT t.event_id,v.vehicle_id,t.battery_percentage,t.temperature,t.status,t.fault_code,t.timestamp FROM telemetry_events t JOIN vehicles v ON v.id=t.vehicle_id WHERE t.event_id = ANY(%s)",
                (list(expected_ids),),
            )
            keys = (
                "event_id",
                "vehicle_id",
                "battery_percentage",
                "temperature",
                "status",
                "fault_code",
                "timestamp",
            )
            rows = [dict(zip(keys, r)) for r in await rows_cursor.fetchall()]
            for r in rows:
                r["timestamp"] = r["timestamp"].isoformat()
            latest_response = await client.get(stack.api + "/vehicles")
            latest_response.raise_for_status()
            integrity = reconcile(
                events, rows, {v["vehicle_id"]: v for v in latest_response.json()}
            )
    attempt_list = [a for r in requests for a in r["attempts"]]
    final_frames = streams.frames[first_frame:]
    failures = total(consumer_after, "waypoint_kafka_consumer_failures_total") - total(
        consumer_before, "waypoint_kafka_consumer_failures_total"
    )
    result = {
        "configuration": asdict(spec),
        "single_run": True,
        "ack_rps": sum(r["ok"] for r in requests) / (ack_finished - started),
        "ack_duration_s": ack_finished - started,
        "ack_latency_ms": quantiles([a["latency_ms"] for a in attempt_list]),
        "request_completion_ms": quantiles(
            [(r["end"] - r["start"]) * 1000 for r in requests]
        ),
        "http_errors": sum(
            a["status"] is not None and a["status"] != 202 for a in attempt_list
        ),
        "transport_errors": sum(a["error"] is not None for a in attempt_list),
        "timeouts": sum(
            a["error"] is not None and "Timeout" in a["error"] for a in attempt_list
        ),
        "error_rate": sum(a["status"] != 202 for a in attempt_list) / len(attempt_list),
        "timeout_rate": sum(
            a["error"] is not None and "Timeout" in a["error"] for a in attempt_list
        )
        / len(attempt_list),
        "retry_attempts": len(attempt_list) - spec.events,
        "failed_events": sum(not r["ok"] for r in requests),
        "persistence_observation_ms": quantiles(
            [(observations[r["event_id"]] - r["start"]) * 1000 for r in requests]
        ),
        "persistence_observed_rps": spec.events / (observed_finished - started),
        "backlog_drain_observed_s": max(0, observed_finished - ack_finished),
        "integrity": integrity,
        "peak_sampled_lag": max(s["lag"] for s in lag),
        "final_lag": 0,
        "consumer_failures_delta": failures,
        "lag_samples": lag,
        "resource_samples": resources,
        "sse": {
            "frames": len(final_frames),
            "vehicle_frames": sum(f["kind"] == "vehicle" for f in final_frames),
            "overflows_delta": total(after, "waypoint_sse_queue_overflows_total")
            - total(before, "waypoint_sse_queue_overflows_total"),
            "listener_up": total(after, "waypoint_sse_listener_up"),
            "connected_clients": total(after, "waypoint_sse_clients"),
            "generated_frames_delta": total(after, "waypoint_sse_events_total")
            - total(before, "waypoint_sse_events_total"),
        },
        "requests": requests,
        "observations": observations,
    }
    if (
        failures
        or result["failed_events"]
        or result["http_errors"]
        or result["transport_errors"]
        or streams.errors
    ):
        raise RuntimeError(
            "health/error gate failed: "
            + json.dumps(
                {
                    k: result[k]
                    for k in (
                        "failed_events",
                        "http_errors",
                        "transport_errors",
                        "consumer_failures_delta",
                    )
                }
            )
        )
    return result


def scenario_specs(custom=None):
    specs = (
        list(custom)
        if custom is not None
        else [
            Workload("s1", 100, 4, 20),
            Workload("s2", 500, 8, 50),
            Workload("s3", 1000, 8),
            Workload("s4", 2500, 16),
            Workload("s5", 5000, 32),
            Workload("steady", 200, 8, 50, 40),
            Workload("burst", 500, 16),
            Workload("s3-repeat", 1000, 8),
        ]
    )
    if not specs:
        raise ValueError("at least one scenario is required")
    for spec in specs:
        spec.validate()
    return specs


def escalation_allowed(previous, memory_bytes):
    return (
        memory_bytes >= 6 * 1024**3
        and previous is not None
        and previous["backlog_drain_observed_s"] <= 30
        and previous["peak_sampled_lag"] <= 2500
    )


async def run_scenarios(stack, report, output, streams, custom=None):
    for spec in scenario_specs(custom):
        memory_bytes = None
        previous = report["scenarios"][-1] if report["scenarios"] else None
        if spec.events == 5000:
            memory_bytes = json.loads(
                command(["docker", "info", "--format", "{{json .}}"])
            )["MemTotal"]
            if memory_bytes < 6 * 1024**3 or (
                previous is not None and not escalation_allowed(previous, memory_bytes)
            ):
                report.setdefault("skipped", []).append(
                    {
                        "scenario": asdict(spec),
                        "reason": "hardware/backlog escalation gate",
                    }
                )
                continue
        warm = await workload(stack, Workload("warm-" + spec.name, 20, 2, 10), streams)
        report["warmups"].append(
            {"configuration": warm["configuration"], "integrity": warm["integrity"]}
        )
        print("warm-up", spec.name, warm["integrity"], flush=True)
        # A standalone large run has no escalation history: its existing bounded
        # warm-up supplies the required health/drain evidence before admission.
        if (
            spec.events == 5000
            and previous is None
            and not escalation_allowed(warm, memory_bytes)
        ):
            report.setdefault("skipped", []).append(
                {
                    "scenario": asdict(spec),
                    "reason": "warm-up preflight escalation gate",
                }
            )
            continue
        result = await workload(stack, spec, streams)
        report["scenarios"].append(result)
        output.write_text(json.dumps(report, indent=2))
        print(
            json.dumps(
                {
                    k: result[k]
                    for k in (
                        "configuration",
                        "ack_rps",
                        "ack_latency_ms",
                        "persistence_observation_ms",
                        "peak_sampled_lag",
                        "backlog_drain_observed_s",
                    )
                }
            ),
            flush=True,
        )


async def execute(stack, report, output, custom=None):
    scenario_specs(custom)  # Reject invalid workloads before opening any connection.
    streams = Streams(stack.api + "/events/vehicles")
    await streams.start()
    report["scenarios"] = []
    report["warmups"] = []
    try:
        await run_scenarios(stack, report, output, streams, custom)
        # Unique vehicle per event makes matching vehicle hints unambiguous in this probe only.
        start_frames = len(streams.frames)
        probe = await workload(stack, Workload("sse-probe", 30, 2, 30, 10), streams)
        values = []
        for r in probe["requests"]:
            hints = [
                f["at"]
                for f in streams.frames[start_frames:]
                if f["client"] == 0
                and f["kind"] == "vehicle"
                and f["payload"]["vehicle_id"] == r["vehicle_id"]
                and f["at"] >= r["start"]
            ]
            if hints:
                values.append((min(hints) - r["start"]) * 1000)
        report["sse_probe"] = {
            "workload": probe,
            "matched": len(values),
            "post_start_to_vehicle_hint_ms": quantiles(values),
            "notification_to_client_ms": None,
            "reason": "payload has vehicle_id only, no server timestamp",
        }
        await streams.close()
        reopened = Streams(stack.api + "/events/vehicles", 1)
        await reopened.start()

        async def heartbeat():
            return any(f["kind"] == "heartbeat" for f in reopened.frames)

        await bounded_wait(heartbeat, 20, 0.1)
        report["sse_reconnect"] = {
            "initial_frames": reopened.frames,
            "errors": reopened.errors,
        }
        await reopened.close()
        report["sse_errors"] = streams.errors
    finally:
        await streams.close()
    browser = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    if browser.exists():
        report["browser_recovery"] = json.loads(
            await asyncio.to_thread(
                command,
                [
                    "node",
                    str(ROOT / "benchmarks/browser_probe.mjs"),
                    stack.api.removesuffix("/api"),
                    "BENCH-" + stack.run + "-sse-probe-0",
                    str(browser),
                ],
            )
        )
    else:
        report["browser_recovery"] = {
            "measured": False,
            "reason": "Chrome executable unavailable",
        }
    report["partition_distribution"] = stack.compose(
        "exec", "-T", "redpanda", "rpk", "topic", "describe", "m8b." + stack.run, "-p"
    )
    report["postgres_statistics"] = stack.sql(
        "SELECT json_build_object('transactions',xact_commit,'rollbacks',xact_rollback,'blocks_read',blks_read,'blocks_hit',blks_hit,'rows_inserted',tup_inserted,'deadlocks',deadlocks,'temp_bytes',temp_bytes) FROM pg_stat_database WHERE datname='m8b'"
    )
    report["final_counts"] = json.loads(
        stack.sql(
            "SELECT json_build_object('vehicles',(SELECT count(*) FROM vehicles),'events',(SELECT count(*) FROM telemetry_events),'failures',(SELECT count(*) FROM telemetry_ingestion_failures))"
        )
    )
    expected_count = (
        sum(
            r["configuration"]["events"]
            for r in report["scenarios"] + report["warmups"]
        )
        + 30
    )
    assert report["final_counts"]["events"] == expected_count
    assert report["final_counts"]["failures"] == 0
    async with local_client(timeout=20) as client:
        r = await client.get(stack.backend + "/metrics")
        report["final_sse_metrics"] = metrics(r.text)
        r = await client.get(stack.consumer + "/metrics")
        before_duplicate = metrics(r.text)
        payload = json.loads(
            stack.sql(
                "SELECT json_build_object('event_id',t.event_id,'vehicle_id',v.vehicle_id,'battery_percentage',t.battery_percentage,'temperature',t.temperature,'status',t.status,'fault_code',t.fault_code,'timestamp',t.timestamp) FROM telemetry_events t JOIN vehicles v ON t.vehicle_id=v.id ORDER BY t.id LIMIT 1"
            )
        )
        r = await client.post(stack.api + "/telemetry", json=payload)
        assert r.status_code == 202

        async def duplicate_handled():
            r = await client.get(stack.consumer + "/metrics")
            return total(
                metrics(r.text), "waypoint_kafka_consumer_events_total"
            ) > total(before_duplicate, "waypoint_kafka_consumer_events_total")

        await bounded_wait(duplicate_handled, 20)
        assert int(stack.sql("SELECT count(*) FROM telemetry_events")) == expected_count
        report["duplicate_probe"] = {"http_status": 202, "row_count_unchanged": True}
        r = await client.get(stack.backend + "/metrics")
        report["final_sse_metrics"] = metrics(r.text)
        assert total(report["final_sse_metrics"], "waypoint_sse_clients") == 0
    report["log_errors"] = {}
    for service in ("backend", "consumer"):
        log = command(["docker", "logs", stack.ids[service]], stderr=subprocess.STDOUT)
        errors = [
            line for line in log.splitlines() if "ERROR" in line or "Traceback" in line
        ]
        report["log_errors"][service] = errors
        assert not errors, "benchmark service log errors"

    report["containers"] = {
        s: {
            "image_id": stack.inspect(s)["Image"],
            "limits": {
                k: stack.inspect(s)["HostConfig"][k] for k in ("Memory", "NanoCpus")
            },
            "state": stack.inspect(s)["State"],
        }
        for s in SERVICES
    }

    for service in SERVICES:
        info = stack.inspect(service)
        assert info["RestartCount"] == 0 and not info["State"]["OOMKilled"]
        assert info["State"]["Status"] == "running"
        if "Health" in info["State"]:
            assert info["State"]["Health"]["Status"] == "healthy"


def normalize_repository_paths(value, root=ROOT):
    """Normalize whole repository-local path values; leave outside paths/URLs alone."""
    if isinstance(value, dict):
        return {
            key: normalize_repository_paths(item, root) for key, item in value.items()
        }
    if isinstance(value, list):
        return [normalize_repository_paths(item, root) for item in value]
    if isinstance(value, str) and Path(value).is_absolute():
        path = Path(os.path.normpath(value))
        try:
            relative = path.relative_to(root)
        except ValueError:
            return value
        return "${REPO_ROOT}" + ("/" + relative.as_posix() if relative.parts else "")
    return value


def capture_configuration(stack):
    # JSON separates actual path values from image names, URLs and other text.
    rendered = json.loads(stack.compose("config", "--format", "json"))
    return json.dumps(normalize_repository_paths(rendered), indent=2)


def environment(stack):
    info = json.loads(command(["docker", "info", "--format", "{{json .}}"]))
    return {
        "platform": platform.platform(),
        "python": sys.version,
        "cpu": command(["sysctl", "-n", "machdep.cpu.brand_string"])
        if sys.platform == "darwin"
        else platform.processor(),
        "host_memory_bytes": int(command(["sysctl", "-n", "hw.memsize"]))
        if sys.platform == "darwin"
        else None,
        "docker": {
            k: info.get(k)
            for k in (
                "NCPU",
                "MemTotal",
                "ServerVersion",
                "Architecture",
                "OperatingSystem",
            )
        },
        "compose": command(["docker", "compose", "version", "--short"]),
        "git_head": command(["git", "rev-parse", "HEAD"], cwd=ROOT),
        "project": stack.project,
        "topic": "m8b." + stack.run,
        "consumer_group": "m8b-" + stack.run,
        "postgres": stack.sql("SELECT version()"),
        "node": command(["node", "--version"]),
        "broker": stack.compose("exec", "-T", "redpanda", "rpk", "version"),
        "nginx": command(
            stack.base + ["exec", "-T", "frontend", "nginx", "-v"],
            env=stack.env,
            stderr=subprocess.STDOUT,
        ),
        "backend_packages": stack.compose(
            "exec", "-T", "backend", "python", "-m", "pip", "freeze"
        ),
        "source_sha256": {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (ROOT / "benchmarks").glob("*")
            if p.suffix in (".py", ".mjs", ".yml")
        },
        "storage": "tmpfs; PostgreSQL durability across container/VM loss is not measured",
        "configuration": capture_configuration(stack),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--events", type=int)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--vehicles", type=int, default=20)
    parser.add_argument("--rate", type=float, default=0)
    args = parser.parse_args()
    custom = None
    if args.events is not None:
        custom = [
            Workload("custom", args.events, args.concurrency, args.vehicles, args.rate)
        ]
        custom[0].validate()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    stack = Stack()
    report = {
        "schema_version": 1,
        "started_at": datetime.now(UTC).isoformat(),
        "project": stack.project,
    }
    args.output.write_text(json.dumps(report, indent=2))
    try:
        stack.setup()
        report["environment"] = environment(stack)
        asyncio.run(execute(stack, report, args.output, custom))
        report["success"] = True
    except BaseException as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        try:
            stack.cleanup()
            report["cleanup_verified"] = True
        finally:
            report["finished_at"] = datetime.now(UTC).isoformat()
            args.output.write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
