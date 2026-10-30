from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = ROOT / "backend/tests/docker-compose.kafka-smoke.yml"
VEHICLE_ID = "FI-ORDERING-1"
MAX_API_OUTAGE_SECONDS = 20.0
OFFSET_SNAPSHOT = """\
import asyncio
import json
from aiokafka import AIOKafkaConsumer
from aiokafka.admin import AIOKafkaAdminClient
from aiokafka.structs import TopicPartition

async def main():
    partitions = [TopicPartition("waypoint.telemetry", i) for i in range(3)]
    consumer = AIOKafkaConsumer(
        bootstrap_servers="redpanda:9092",
        group_id=None,
        enable_auto_commit=False,
    )
    consumer.assign(partitions)
    await consumer.start()
    try:
        ends = await consumer.end_offsets(partitions)
    finally:
        await consumer.stop()
    admin = AIOKafkaAdminClient(bootstrap_servers="redpanda:9092")
    await admin.start()
    try:
        committed = await admin.list_consumer_group_offsets(
            "waypoint-telemetry-persistence",
            partitions=partitions,
        )
    finally:
        await admin.close()
    print(json.dumps({
        str(partition.partition): {
            "end": ends[partition],
            "committed": max(0, committed.get(partition).offset)
            if committed.get(partition) is not None else 0,
        }
        for partition in partitions
    }))

asyncio.run(main())
"""


class ReliabilityFailure(RuntimeError):
    pass


def available_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class ReliabilityHarness:
    def __init__(self) -> None:
        self.project = f"waypoint-reliability-{uuid.uuid4().hex[:8]}"
        self.api_port = available_port()
        self.consumer_metrics_port = available_port()
        self.environment = os.environ.copy()
        self.environment["WAYPOINT_SMOKE_API_PORT"] = str(self.api_port)
        self.environment["WAYPOINT_SMOKE_CONSUMER_METRICS_PORT"] = str(
            self.consumer_metrics_port
        )
        self.api_url = f"http://127.0.0.1:{self.api_port}"
        self.consumer_metrics_url = f"http://127.0.0.1:{self.consumer_metrics_port}/metrics"

    def compose(self, *args: str, timeout: int = 180, check: bool = True) -> str:
        command = [
            "docker",
            "compose",
            "--project-name",
            self.project,
            "--file",
            str(COMPOSE_FILE),
            *args,
        ]
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if check and result.returncode:
            raise ReliabilityFailure(
                f"Command failed ({result.returncode}): {' '.join(command)}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return result.stdout + result.stderr

    def start(self) -> None:
        self.compose("up", "-d", "db", "redpanda")
        self.wait_for(lambda: self.database_healthy(), 90, "PostgreSQL health")
        self.wait_for(
            lambda: self.service_healthy("redpanda"),
            90,
            "Redpanda health",
        )
        self.sql_scalar(
            "CREATE ROLE waypoint_admin SUPERUSER LOGIN; SELECT 'admin_ready'"
        )
        self.compose("run", "--build", "--rm", "kafka-init")
        self.compose("run", "--build", "--rm", "backend", "alembic", "upgrade", "head")
        self.compose("up", "--build", "-d", "backend", "consumer")
        self.wait_for(
            lambda: self.http_status(f"{self.api_url}/health") == 200,
            90,
            "API health",
        )
        self.wait_for(
            lambda: self.metrics_available(self.consumer_metrics_url),
            90,
            "consumer metrics endpoint",
        )

    def http_status(self, url: str) -> int | None:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                return response.status
        except (OSError, urllib.error.URLError):
            return None

    def request_json(
        self,
        url: str,
        payload: dict[str, object] | None = None,
        *,
        timeout: float = 10,
    ) -> tuple[int, dict[str, str], object, float]:
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"} if data is not None else {},
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read()
                status_code = response.status
                headers = {
                    name.lower(): value for name, value in response.headers.items()
                }
        except urllib.error.HTTPError as error:
            body = error.read()
            status_code = error.code
            headers = {
                name.lower(): value for name, value in error.headers.items()
            }
        elapsed = time.monotonic() - started
        try:
            parsed_body: object = json.loads(body) if body else {}
        except json.JSONDecodeError:
            parsed_body = body.decode(errors="replace")
        return status_code, headers, parsed_body, elapsed

    def event(
        self,
        event_id: str,
        vehicle_id: str,
        battery: float,
        timestamp: str,
    ) -> dict[str, object]:
        return {
            "event_id": event_id,
            "vehicle_id": vehicle_id,
            "battery_percentage": battery,
            "status": "ready",
            "temperature": 24.5,
            "fault_code": None,
            "timestamp": timestamp,
        }

    def post_event(self, payload: dict[str, object]) -> tuple[int, float]:
        status_code, headers, body, elapsed = self.request_json(
            f"{self.api_url}/telemetry",
            payload,
        )
        if status_code != 202:
            raise ReliabilityFailure(
                f"POST /telemetry expected 202, got {status_code}: {body}"
            )
        if headers.get("x-waypoint-delivery") != "broker-acknowledged":
            raise ReliabilityFailure("Accepted response omitted broker acknowledgment header")
        return status_code, elapsed

    def history(self, vehicle_id: str) -> list[dict[str, object]]:
        status_code, _, body, _ = self.request_json(
            f"{self.api_url}/vehicles/{vehicle_id}/telemetry?limit=1000"
        )
        if status_code == 404:
            return []
        if status_code != 200 or not isinstance(body, list):
            raise ReliabilityFailure(f"History request failed for {vehicle_id}: {body}")
        return body

    def wait_for(self, condition: Callable[[], bool], timeout: float, description: str) -> float:
        started = time.monotonic()
        deadline = started + timeout
        while time.monotonic() < deadline:
            try:
                if condition():
                    return time.monotonic() - started
            except (OSError, urllib.error.URLError, subprocess.SubprocessError):
                pass
            time.sleep(0.1)
        raise ReliabilityFailure(f"Timed out after {timeout:.1f}s waiting for {description}")

    def metrics_available(self, url: str) -> bool:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                return response.status == 200
        except (OSError, urllib.error.URLError):
            return False

    def metrics(self, url: str) -> str:
        with urllib.request.urlopen(url, timeout=3) as response:
            return response.read().decode()

    @staticmethod
    def metric_value(metrics: str, name: str, labels: dict[str, str] | None = None) -> float:
        for line in metrics.splitlines():
            if not line or line.startswith("#"):
                continue
            sample, _, raw_value = line.partition(" ")
            metric_name, _, raw_labels = sample.partition("{")
            if metric_name != name:
                continue
            actual_labels: dict[str, str] = {}
            if raw_labels.endswith("}"):
                for label in re.finditer(r'([a-zA-Z_][a-zA-Z0-9_]*)="([^"]*)"', raw_labels):
                    actual_labels[label.group(1)] = label.group(2)
            if labels is None or actual_labels == labels:
                return float(raw_value)
        return 0.0

    def offsets(self) -> dict[str, dict[str, int]]:
        output = self.compose(
            "exec",
            "-T",
            "backend",
            "python",
            "-c",
            OFFSET_SNAPSHOT,
            timeout=20,
        )
        try:
            return json.loads(output.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError) as error:
            raise ReliabilityFailure(f"Could not parse Kafka offsets: {output}") from error

    def backlog(self, offsets: dict[str, dict[str, int]]) -> int:
        return sum(max(0, item["end"] - item["committed"]) for item in offsets.values())

    def sql_scalar(self, query: str) -> str:
        output = self.compose(
            "exec",
            "-T",
            "db",
            "psql",
            "-U",
            "waypoint_smoke",
            "-d",
            "waypoint_smoke",
            "-At",
            "-c",
            query,
            timeout=10,
        )
        return output.strip().splitlines()[-1]

    def admin_sql_scalar(self, query: str) -> str:
        output = self.compose(
            "exec",
            "-T",
            "db",
            "psql",
            "-U",
            "waypoint_admin",
            "-d",
            "waypoint_smoke",
            "-At",
            "-c",
            query,
            timeout=10,
        )
        return output.strip().splitlines()[-1]

    def container_id(self, service: str) -> str:
        output = self.compose("ps", "-q", service, check=True).strip().splitlines()
        if not output:
            raise ReliabilityFailure(f"No container found for service {service}")
        return output[-1]

    def database_healthy(self) -> bool:
        return self.service_healthy("db")

    def service_healthy(self, service_name: str) -> bool:
        output = self.compose("ps", "--format", "json", check=False)
        for line in output.splitlines():
            try:
                service = json.loads(line)
            except json.JSONDecodeError:
                continue
            if service.get("Service") == service_name:
                return "(healthy)" in service.get("Status", "")
        return False

    def consumer_logs(self) -> str:
        return self.compose("logs", "--no-color", "consumer", check=False)

    def diagnostics(self) -> str:
        state = self.compose("ps", "-a", check=False)
        logs = self.compose(
            "logs",
            "--no-color",
            "--tail=100",
            "backend",
            "consumer",
            "db",
            "redpanda",
            check=False,
        )
        return f"\n--- isolated compose state ---\n{state}\n--- recent logs ---\n{logs}"

    def core_scenarios(self) -> dict[str, object]:
        self.compose("stop", "-t", "20", "consumer")
        pending = [
            self.event(
                f"reliability-interrupt-{index}",
                "FI-INTERRUPT-1",
                80.0 - index,
                f"2026-10-29T12:0{index}:00Z",
            )
            for index in range(1, 4)
        ]
        accepted_at = time.monotonic()
        for payload in pending:
            self.post_event(payload)
        queued_offsets = self.offsets()
        queued_lag = self.backlog(queued_offsets)
        if queued_lag < len(pending):
            raise ReliabilityFailure(
                f"Expected at least {len(pending)} uncommitted records, observed lag {queued_lag}"
            )

        restarted_at = time.monotonic()
        self.compose("start", "consumer")
        expected_ids = {str(item["event_id"]) for item in pending}
        self.wait_for(
            lambda: expected_ids.issubset(
                {str(row["event_id"]) for row in self.history("FI-INTERRUPT-1")}
            ),
            30,
            "consumer interruption backlog persistence",
        )
        history = self.history("FI-INTERRUPT-1")
        if len(history) != len(pending):
            raise ReliabilityFailure(f"Expected 3 interruption rows, found {len(history)}")
        interruption_seconds = time.monotonic() - restarted_at

        duplicate = self.event(
            "reliability-duplicate",
            "FI-DUPLICATE-1",
            70.0,
            "2026-10-29T12:10:00Z",
        )
        self.post_event(duplicate)
        self.post_event(duplicate)
        self.wait_for(
            lambda: self.backlog(self.offsets()) == 0
            and len(self.history("FI-DUPLICATE-1")) == 1,
            20,
            "duplicate event persistence and offset commit",
        )
        if len(self.history("FI-DUPLICATE-1")) != 1:
            raise ReliabilityFailure("Duplicate event created more than one history row")

        conflict = self.event(
            "reliability-conflict",
            "FI-CONFLICT-1",
            70.0,
            "2026-10-29T12:11:00Z",
        )
        conflicting_payload = {**conflict, "battery_percentage": 60.0}
        self.post_event(conflict)
        self.post_event(conflicting_payload)
        failure_count = self.sql_scalar(
            "SELECT count(*) FROM telemetry_ingestion_failures "
            "WHERE event_id = 'reliability-conflict' AND reason = 'event_id_conflict'"
        )
        if failure_count != "1":
            raise ReliabilityFailure(f"Expected one conflict audit row, got {failure_count}")
        if len(self.history("FI-CONFLICT-1")) != 1:
            raise ReliabilityFailure("Conflicting event content created an extra history row")

        newer = self.event(
            "reliability-out-of-order-newer",
            VEHICLE_ID,
            90.0,
            "2026-10-29T12:20:00Z",
        )
        older = self.event(
            "reliability-out-of-order-older",
            VEHICLE_ID,
            35.0,
            "2026-10-29T12:00:00Z",
        )
        self.post_event(newer)
        self.post_event(older)
        self.wait_for(
            lambda: len(self.history(VEHICLE_ID)) == 2,
            20,
            "out-of-order event history",
        )
        latest = self.request_json(f"{self.api_url}/vehicles")[2]
        current = next(
            (row for row in latest if row["vehicle_id"] == VEHICLE_ID),
            None,
        )
        if current is None or current["battery_percentage"] != 90.0:
            raise ReliabilityFailure("Older telemetry overwrote the latest vehicle state")

        consumer_metrics = self.metrics(self.consumer_metrics_url)
        api_metrics = self.metrics(f"{self.api_url}/metrics")
        return {
            "scenario": "consumer_restart_duplicate_conflict_out_of_order",
            "events_queued_during_interruption": len(pending),
            "backlog_before_restart": queued_lag,
            "recovery_seconds": round(interruption_seconds, 3),
            "history_rows_after_replay": len(history),
            "duplicate_history_rows": len(self.history("FI-DUPLICATE-1")),
            "conflict_audit_rows": int(failure_count),
            "out_of_order_history_rows": len(self.history(VEHICLE_ID)),
            "latest_battery_percentage": current["battery_percentage"],
            "consumer_persisted_total": self.metric_value(
                consumer_metrics,
                "waypoint_kafka_consumer_events_total",
                {"outcome": "persisted"},
            ),
            "consumer_duplicate_total": self.metric_value(
                consumer_metrics,
                "waypoint_kafka_consumer_events_total",
                {"outcome": "duplicate"},
            ),
            "consumer_conflict_failure_total": self.metric_value(
                consumer_metrics,
                "waypoint_kafka_consumer_failures_total",
                {"reason": "event_id_conflict"},
            ),
            "api_publish_success_total": self.metric_value(
                api_metrics,
                "waypoint_telemetry_publish_events_total",
                {"outcome": "success"},
            ),
            "elapsed_since_acceptance_seconds": round(
                time.monotonic() - accepted_at,
                3,
            ),
        }

    def database_outage_scenario(self) -> dict[str, object]:
        self.wait_for(
            lambda: self.backlog(self.offsets()) == 0,
            20,
            "consumer to drain before PostgreSQL outage",
        )
        before = self.offsets()
        retry_logs_before = self.consumer_logs().count(
            "telemetry_message_processing_retry"
        )
        terminated_sessions = self.admin_sql_scalar(
            "ALTER ROLE waypoint_smoke NOLOGIN; "
            "SELECT count(*) FROM ("
            "SELECT pg_terminate_backend(pid) "
            "FROM pg_stat_activity "
            "WHERE usename = 'waypoint_smoke' AND pid <> pg_backend_pid()"
            ") AS terminated"
        )
        if terminated_sessions == "0":
            raise ReliabilityFailure(
                "Could not terminate an application PostgreSQL session for outage injection"
            )
        event = self.event(
            "reliability-database-outage",
            "FI-DB-OUTAGE-1",
            51.0,
            "2026-10-29T12:30:00Z",
        )
        self.post_event(event)

        max_observed_retry = 0.0
        max_observed_lag = 0

        def database_failure_observed() -> bool:
            nonlocal max_observed_retry, max_observed_lag
            try:
                metrics = self.metrics(self.consumer_metrics_url)
                max_observed_retry = max(
                    max_observed_retry,
                    self.metric_value(
                        metrics,
                        "waypoint_kafka_consumer_retries_total",
                        {"reason": "database"},
                    ),
                )
                max_observed_lag = max(
                    max_observed_lag,
                    int(
                        sum(
                            float(match.group(1))
                            for match in re.finditer(
                                r'^waypoint_kafka_consumer_lag\{[^}]*\} ([0-9.eE+-]+)$',
                                metrics,
                                re.MULTILINE,
                            )
                        )
                    ),
                )
            except (OSError, urllib.error.URLError):
                return False
            return max_observed_retry >= 1 and max_observed_lag >= 1

        self.wait_for(
            database_failure_observed,
            10,
            "consumer database retry and uncommitted lag metrics",
        )

        during = self.offsets()
        increased = [
            partition
            for partition, offsets in during.items()
            if offsets["end"] > before[partition]["end"]
        ]
        if not increased:
            raise ReliabilityFailure("Broker log end offset did not increase during DB outage")
        for partition in increased:
            if during[partition]["committed"] > before[partition]["committed"]:
                raise ReliabilityFailure(
                    f"Partition {partition} committed an offset while PostgreSQL was unavailable"
                )
        actual_backlog = self.backlog(during)
        if actual_backlog < 1:
            raise ReliabilityFailure("No uncommitted Kafka backlog was observed during DB outage")

        recovery_started = time.monotonic()
        self.admin_sql_scalar("ALTER ROLE waypoint_smoke LOGIN; SELECT 'login_restored'")
        self.compose("start", "consumer")

        def database_recovered() -> bool:
            offsets = self.offsets()
            return (
                len(self.history("FI-DB-OUTAGE-1")) == 1
                and self.backlog(offsets) == 0
                and any(
                    offsets[partition]["committed"]
                    > during[partition]["committed"]
                    for partition in increased
                )
            )

        self.wait_for(
            database_recovered,
            45,
            "PostgreSQL outage persistence and Kafka offset commit",
        )
        recovery_seconds = time.monotonic() - recovery_started
        after = self.offsets()
        persisted_rows = len(self.history("FI-DB-OUTAGE-1"))
        if persisted_rows != 1:
            raise ReliabilityFailure(f"Expected one recovered event row, found {persisted_rows}")
        if not any(
            after[partition]["committed"] > during[partition]["committed"]
            for partition in increased
        ):
            raise ReliabilityFailure(
                "Kafka offsets did not advance after PostgreSQL recovered"
            )
        if max_observed_retry < 1:
            raise ReliabilityFailure(
                "No PostgreSQL retry counter increment was observed during the outage; "
                f"maximum observed={max_observed_retry}\n"
                f"consumer metrics:\n{self.metrics(self.consumer_metrics_url)}\n"
                f"consumer logs:\n{self.consumer_logs()}"
            )
        logs = self.consumer_logs()
        database_retry_log_entries = max(
            0,
            logs.count("telemetry_message_processing_retry") - retry_logs_before,
        )
        return {
            "scenario": "postgres_unavailable_broker_available",
            "api_accepted_events": 1,
            "postgres_container_stayed_healthy": self.database_healthy(),
            "maximum_observed_consumer_retry_counter": max_observed_retry,
            "maximum_observed_metrics_lag": max_observed_lag,
            "uncommitted_backlog_before_recovery": actual_backlog,
            "recovery_seconds": round(recovery_seconds, 3),
            "persisted_rows_after_recovery": persisted_rows,
            "consumer_start_command_after_database_recovery": True,
            "database_retry_log_entries": database_retry_log_entries,
            "committed_offsets_advanced_after_recovery": any(
                after[partition]["committed"] > during[partition]["committed"]
                for partition in increased
            ),
        }

    def in_flight_restart_scenario(self) -> dict[str, object]:
        event_id = "reliability-inflight-event"
        self.sql_scalar(
            "CREATE OR REPLACE FUNCTION reliability_delay_insert() RETURNS trigger "
            "LANGUAGE plpgsql AS $$ BEGIN "
            f"IF NEW.event_id = '{event_id}' THEN PERFORM pg_sleep(5); END IF; "
            "RETURN NEW; END $$; "
            "CREATE TRIGGER reliability_delay_insert_trigger "
            "BEFORE INSERT ON telemetry_events FOR EACH ROW "
            "EXECUTE FUNCTION reliability_delay_insert(); "
            "SELECT 'trigger_installed'"
        )
        payload = self.event(
            event_id,
            "FI-INFLIGHT-1",
            66.0,
            "2026-10-29T12:40:00Z",
        )
        self.post_event(payload)
        self.wait_for(
            lambda: self.sql_scalar(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE state = 'active' "
                "AND query ILIKE 'INSERT INTO telemetry_events%'"
            )
            != "0",
            20,
            "consumer insert held in the test-only delay trigger",
        )
        restarted_at = time.monotonic()
        self.compose("kill", "-s", "SIGKILL", "consumer")
        self.compose("up", "-d", "consumer")
        self.wait_for(
            lambda: self.metrics_available(self.consumer_metrics_url),
            30,
            "consumer restart after in-flight termination",
        )
        self.wait_for(
            lambda: len(self.history("FI-INFLIGHT-1")) == 1,
            30,
            "in-flight record replay",
        )
        elapsed = time.monotonic() - restarted_at
        row_count = int(
            self.sql_scalar(
                "SELECT count(*) FROM telemetry_events WHERE event_id = 'reliability-inflight-event'"
            )
        )
        if row_count != 1:
            raise ReliabilityFailure(f"In-flight replay created {row_count} telemetry rows")
        return {
            "scenario": "consumer_killed_during_database_insert",
            "consumer_restart_action_count": 1,
            "recovery_seconds": round(elapsed, 3),
            "history_rows": row_count,
            "eventually_persisted": True,
        }

    def broker_outage_scenario(self) -> dict[str, object]:
        before = self.metrics(f"{self.api_url}/metrics")
        before_failures = self.metric_value(
            before,
            "waypoint_telemetry_publish_events_total",
            {"outcome": "failure"},
        )
        self.compose("stop", "redpanda")
        payload = self.event(
            "reliability-broker-outage",
            "FI-BROKER-OUTAGE-1",
            44.0,
            "2026-10-29T12:50:00Z",
        )
        status_code, _, response, elapsed = self.request_json(
            f"{self.api_url}/telemetry",
            payload,
            timeout=MAX_API_OUTAGE_SECONDS + 5,
        )
        if status_code != 503:
            raise ReliabilityFailure(f"Broker outage returned HTTP {status_code}, expected 503: {response}")
        if elapsed > MAX_API_OUTAGE_SECONDS:
            raise ReliabilityFailure(
                f"Broker outage response exceeded {MAX_API_OUTAGE_SECONDS}s: {elapsed:.3f}s"
            )
        if self.history("FI-BROKER-OUTAGE-1"):
            raise ReliabilityFailure("Broker outage event was persisted despite a 503 response")
        after = self.metrics(f"{self.api_url}/metrics")
        failures = self.metric_value(
            after,
            "waypoint_telemetry_publish_events_total",
            {"outcome": "failure"},
        )
        if failures <= before_failures:
            raise ReliabilityFailure("Broker outage did not increment the publish failure metric")
        self.compose("start", "redpanda")
        self.wait_for(
            lambda: self.service_healthy("redpanda"),
            45,
            "Redpanda health after restart",
        )
        return {
            "scenario": "redpanda_unavailable",
            "http_status": status_code,
            "response_seconds": round(elapsed, 3),
            "bounded_response_limit_seconds": MAX_API_OUTAGE_SECONDS,
            "publish_failure_counter_delta": failures - before_failures,
            "unacknowledged_event_history_rows": 0,
        }

    def run(self, scenario: str) -> dict[str, object]:
        self.start()
        report: dict[str, object] = {
            "project": self.project,
            "storage": "PostgreSQL and Redpanda data directories are tmpfs",
            "scenario": scenario,
            "results": [self.core_scenarios()],
        }
        if scenario == "all":
            report["results"].extend(
                [
                    self.database_outage_scenario(),
                    self.in_flight_restart_scenario(),
                    self.broker_outage_scenario(),
                ]
            )
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run isolated Waypoint telemetry fault-injection tests.")
    parser.add_argument(
        "--scenario",
        choices=("quick", "all"),
        default="quick",
        help="quick runs restart/idempotency/order checks; all adds database, broker, and in-flight failures",
    )
    args = parser.parse_args()
    if shutil.which("docker") is None:
        parser.error("Docker CLI is required")

    harness = ReliabilityHarness()
    failure: BaseException | None = None
    try:
        report = harness.run(args.scenario)
        print(json.dumps(report, indent=2, sort_keys=True))
    except BaseException as error:
        failure = error
        print(f"Reliability test failed: {error}", file=sys.stderr)
        print(harness.diagnostics(), file=sys.stderr)
    finally:
        cleanup = harness.compose("down", "--remove-orphans", "--timeout", "20", check=False)
        if cleanup.strip():
            print(cleanup, file=sys.stderr)
    return 1 if failure is not None else 0


if __name__ == "__main__":
    raise SystemExit(main())
