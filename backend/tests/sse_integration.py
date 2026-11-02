"""Disposable PostgreSQL/broker/API/Nginx SSE acceptance test. Never targets the normal stack."""

from __future__ import annotations

import json
import os
import queue
import socket
import subprocess
import threading
import time
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROJECT = "waypoint-m8a-" + uuid.uuid4().hex[:10]


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    import tempfile

    env = dict(
        os.environ,
        WAYPOINT_SMOKE_API_PORT=str(free_port()),
        WAYPOINT_SMOKE_CONSUMER_METRICS_PORT=str(free_port()),
    )
    frontend_port = free_port()
    with tempfile.TemporaryDirectory(prefix="waypoint-m8a-") as temporary:
        overlay = Path(temporary) / "compose.json"
        overlay.write_text(
            json.dumps(
                {
                    "services": {
                        "frontend": {
                            "build": {"context": str(ROOT / "frontend")},
                            "ports": [f"127.0.0.1:{frontend_port}:80"],
                            "networks": ["database"],
                        }
                    }
                }
            )
        )
        command = [
            "docker",
            "compose",
            "-p",
            PROJECT,
            "-f",
            str(ROOT / "backend/tests/docker-compose.kafka-smoke.yml"),
            "-f",
            str(overlay),
        ]

        def compose(*args, input=None):
            result = subprocess.run(
                command + list(args),
                env=env,
                input=input,
                text=True,
                capture_output=True,
            )
            if result.returncode:
                raise RuntimeError(f"Isolated Compose {args}: {result.stderr}")
            return result.stdout

        base = f"http://127.0.0.1:{frontend_port}/api"

        def get(path):
            with urllib.request.urlopen(base + path, timeout=5) as response:
                return json.load(response)

        def post(payload):
            request = urllib.request.Request(
                base + "/telemetry",
                json.dumps(payload).encode(),
                {"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=5) as response:
                assert response.status == 202

        def stream():
            response = urllib.request.urlopen(base + "/events/vehicles", timeout=20)
            assert response.headers["Content-Type"].startswith("text/event-stream")
            assert response.headers["Cache-Control"] == "no-cache, no-transform"
            messages = queue.Queue()

            def read():
                try:
                    for line in response:
                        if line.startswith(b"data:"):
                            messages.put(json.loads(line[5:]))
                        elif line.startswith(b": heartbeat"):
                            messages.put({"heartbeat": True})
                except (OSError, ValueError):
                    pass
                finally:
                    response.close()

            reader = threading.Thread(target=read, daemon=True)
            reader.start()

            def close():
                # Interrupt the socket read first; only the reader closes its response.
                response.fp.raw._sock.shutdown(socket.SHUT_RDWR)
                reader.join(timeout=5)
                assert not reader.is_alive(), "SSE test reader did not stop"

            return close, messages

        def until(predicate, seconds=20):
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                if predicate():
                    return
                time.sleep(0.1)
            raise AssertionError("Timed out waiting for isolated state")

        def sql(statement):
            return compose(
                "exec",
                "-T",
                "db",
                "psql",
                "-U",
                "waypoint_smoke",
                "-d",
                "waypoint_smoke",
                "-Atc",
                statement,
            ).strip()

        try:
            compose("up", "-d", "--wait", "db", "redpanda")
            compose("build", "backend", "consumer", "kafka-init", "frontend")
            compose("run", "--rm", "--no-deps", "kafka-init")
            compose("run", "--rm", "--no-deps", "backend", "alembic", "upgrade", "head")
            compose("up", "-d", "--wait", "backend", "consumer")
            compose("up", "-d", "--wait", "frontend")
            until(lambda: get("/health") == {"status": "ok"})
            # The listener starts asynchronously during API startup.
            time.sleep(2)
            close_stream, messages = stream()
            assert messages.get(timeout=5)["reason"] == "connection_open"
            payload = {
                "event_id": "sse-new",
                "vehicle_id": "SSE-TEST",
                "battery_percentage": 80,
                "temperature": 24,
                "status": "ready",
                "timestamp": "2026-11-02T12:00:00Z",
            }
            post(payload)
            assert messages.get(timeout=10)["vehicle_id"] == "SSE-TEST"
            assert get("/vehicles")[0]["battery_percentage"] == 80
            post(payload)  # Identical replay must not notify or create another row.
            time.sleep(1)
            assert messages.empty()
            post(
                dict(
                    payload,
                    event_id="sse-old",
                    battery_percentage=10,
                    timestamp="2026-11-01T12:00:00Z",
                )
            )
            assert messages.get(timeout=10)["vehicle_id"] == "SSE-TEST"
            assert get("/vehicles")[0]["battery_percentage"] == 80
            assert len(get("/vehicles/SSE-TEST/telemetry")) == 2
            # PostgreSQL itself must withhold notifications before commit and discard rollback.
            transaction = subprocess.Popen(
                command
                + [
                    "exec",
                    "-T",
                    "db",
                    "psql",
                    "-U",
                    "waypoint_smoke",
                    "-d",
                    "waypoint_smoke",
                ],
                env=env,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
            )
            assert transaction.stdin is not None
            transaction.stdin.write(
                b"""BEGIN; SELECT pg_notify('waypoint_vehicle_changes', '{"vehicle_id":"ROLLBACK"}'); SELECT pg_sleep(2); ROLLBACK;\n"""
            )
            transaction.stdin.close()
            transaction.wait(timeout=10)
            assert transaction.returncode == 0 and messages.empty()
            # Kill only this project's LISTEN session, not the database or normal services.
            sql(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE query = 'LISTEN waypoint_vehicle_changes'"
            )
            assert messages.get(timeout=5)["reason"] == "listener_disconnected"
            assert messages.get(timeout=10)["reason"] == "listener_connected"
            assert get("/vehicles")[0]["battery_percentage"] == 80
            assert messages.get(timeout=20).get("heartbeat") is True
            close_stream()
            # A new stream requests a database refresh instead of pretending it can replay.
            post(
                dict(
                    payload,
                    event_id="sse-missed",
                    battery_percentage=90,
                    timestamp="2026-11-02T13:00:00Z",
                )
            )
            until(lambda: get("/vehicles")[0]["battery_percentage"] == 90)
            close_stream, messages = stream()
            assert messages.get(timeout=5)["reason"] == "connection_open"
            assert get("/vehicles")[0]["battery_percentage"] == 90
            close_stream()

            def clients_cleaned_up():
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{env['WAYPOINT_SMOKE_API_PORT']}/metrics",
                    timeout=5,
                ) as metrics:
                    return "waypoint_sse_clients 0.0" in metrics.read().decode()

            until(clients_cleaned_up)
            assert sql("SELECT count(*) FROM telemetry_events") == "3"
            # Open a real stream during shutdown in this disposable project only.
            close_stream, messages = stream()
            assert messages.get(timeout=5)["reason"] == "connection_open"
            stopped_at = time.monotonic()
            compose("stop", "-t", "10", "backend")
            assert time.monotonic() - stopped_at < 10
            backend_id = compose("ps", "-aq", "backend").strip()
            exit_code = subprocess.check_output(
                ["docker", "inspect", "--format", "{{.State.ExitCode}}", backend_id],
                text=True,
            ).strip()
            assert exit_code == "0", f"Backend shutdown exited {exit_code}"
            print(
                "PASS: proxied SSE, committed state, duplicate suppression, out-of-order history, rollback, listener reconnect, heartbeat, missed notification resync, bounded graceful shutdown; isolated rows=3"
            )
        except Exception:
            print(compose("logs", "--tail", "30", "backend", "consumer", "frontend"))
            raise
        finally:
            compose("down", "--remove-orphans")


if __name__ == "__main__":
    main()
