import asyncio
import csv
import io
import json
import os
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import httpx

from benchmarks.performance import (
    Workload,
    assert_owned,
    bounded_wait,
    local_client,
    metrics,
    quantiles,
    reconcile,
    send_one,
    total,
)


class PerformanceTests(unittest.TestCase):
    def test_environment_proxy_cannot_redirect_load(self):
        async def scenario():
            with patch.dict(
                "os.environ",
                {
                    "HTTP_PROXY": "http://127.0.0.1:8000",
                    "ALL_PROXY": "http://127.0.0.1:8000",
                },
            ):
                async with local_client() as client:
                    self.assertFalse(client.trust_env)

        asyncio.run(scenario())

    def test_development_preservation_detects_drift(self):
        from benchmarks.development_guard import compare

        baseline = {
            "vehicles": {"1": "hash"},
            "events": {"1": "hash"},
            "failures": 0,
            "revision": "head",
            "services": {},
            "volumes": ["preserved"],
        }
        self.assertTrue(compare(baseline, baseline)["volumes_equal"])
        with self.assertRaises(AssertionError):
            compare(baseline, {**baseline, "events": {}})
        with self.assertRaises(AssertionError):
            compare(baseline, {**baseline, "volumes": []})

    def test_configuration_bounds(self):
        for kwargs in (
            {"events": 0},
            {"events": 5001},
            {"concurrency": 33},
            {"rate": 501},
            {"vehicles": 0},
        ):
            with self.assertRaises(ValueError):
                Workload(
                    **({"name": "test", "events": 100, "concurrency": 4} | kwargs)
                ).validate()
        Workload("bounded", 5000, 32, 1000, 500).validate()

    def test_percentiles_interpolate(self):
        self.assertEqual(quantiles([1, 2, 3, 4])["p50"], 2.5)
        self.assertAlmostEqual(quantiles([1, 2, 3, 4])["p95"], 3.85)
        self.assertIsNone(quantiles([])["p99"])

    def test_owned_container_guard(self):
        info = {
            "Config": {
                "Labels": {
                    "com.docker.compose.project": "waypoint-m8b-012345abcdef",
                    "com.docker.compose.service": "db",
                }
            },
            "Mounts": [],
        }
        assert_owned(info, "waypoint-m8b-012345abcdef", "db")
        for project in ("waypoint", "waypoint-m8b-aaaaaaaaaaaa"):
            with self.assertRaises(ValueError):
                assert_owned(info, project, "db")
        info["Mounts"] = [{"Type": "volume"}]
        with self.assertRaises(ValueError):
            assert_owned(info, "waypoint-m8b-012345abcdef", "db")

    def test_bounded_timeout(self):
        async def never():
            return False

        with self.assertRaises(TimeoutError):
            asyncio.run(bounded_wait(never, 0.01, 0.001))

    def test_timeout_retries_bounded_same_event(self):
        calls = []

        def handler(request):
            calls.append(request.content)
            raise httpx.ReadTimeout("test only")

        async def scenario():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
                return await send_one(
                    c,
                    "http://owned.invalid/telemetry",
                    {"event_id": "same", "vehicle_id": "v"},
                    retries=1,
                )

        result = asyncio.run(scenario())
        self.assertFalse(result["ok"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(result["attempts"][0]["error"], "ReadTimeout")

    def test_metrics_label_sum(self):
        sample = metrics(
            'waypoint_kafka_consumer_lag{partition="0"} 2\nwaypoint_kafka_consumer_lag{partition="1"} 3\n'
        )
        self.assertEqual(total(sample, "waypoint_kafka_consumer_lag"), 5)

    def test_integrity_detects_payload_latest_and_duplicates(self):
        e = {
            "event_id": "1",
            "vehicle_id": "v",
            "battery_percentage": 50,
            "temperature": 25,
            "status": "ready",
            "fault_code": None,
            "timestamp": "2026-11-02T12:00:00+00:00",
        }
        latest = {"v": {**e, "last_updated": e["timestamp"]}}
        self.assertTrue(reconcile([e], [e], latest)["payloads_and_latest_correct"])
        with self.assertRaises(AssertionError):
            reconcile([e], [e, e], latest)
        with self.assertRaises(AssertionError):
            reconcile([e], [{**e, "event_id": "1-warm"}], latest)
        with self.assertRaises(AssertionError):
            reconcile([e], [{**e, "temperature": 90}], latest)
        with self.assertRaises(AssertionError):
            reconcile([e], [e], {"v": {**latest["v"], "battery_percentage": 0}})

    def test_no_custom_target_argument(self):
        from benchmarks.performance import main

        with (
            patch(
                "sys.argv",
                [
                    "performance",
                    "--output",
                    "unused.json",
                    "--base-url",
                    "http://localhost:8000",
                ],
            ),
            self.assertRaises(SystemExit),
        ):
            main()


class ScenarioRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def run_plan(
        self, custom=None, memory=8 * 1024**3, unhealthy=None, failure=False
    ):
        from benchmarks.performance import run_scenarios

        calls = []

        async def measured(_stack, spec, _streams):
            calls.append(spec)
            if failure:
                raise TimeoutError("preflight failed")
            return {
                "configuration": {"name": spec.name, "events": spec.events},
                "integrity": {"events": spec.events},
                "ack_rps": 100,
                "ack_latency_ms": {},
                "persistence_observation_ms": {},
                "backlog_drain_observed_s": 31 if spec.name == unhealthy else 1,
                "peak_sampled_lag": 0,
            }

        report = {"scenarios": [], "warmups": []}
        with (
            patch(
                "benchmarks.performance.workload", new=AsyncMock(side_effect=measured)
            ),
            patch(
                "benchmarks.performance.command",
                return_value=json.dumps({"MemTotal": memory}),
            ) as command_mock,
        ):
            await run_scenarios(None, report, Mock(), None, custom)
        return report, calls, command_mock

    async def test_custom_5000_uses_healthy_warmup_preflight(self):
        report, calls, _ = await self.run_plan([Workload("custom", 5000, 32)])
        self.assertEqual(
            [(s.name, s.events) for s in calls], [("warm-custom", 20), ("custom", 5000)]
        )
        self.assertEqual(len(report["scenarios"]), 1)
        self.assertNotIn("skipped", report)

    async def test_smaller_custom_scenarios_do_not_require_history_or_docker_info(self):
        for count in (1, 100, 1000, 4999):
            report, calls, command_mock = await self.run_plan(
                [Workload("custom", count, 4)]
            )
            self.assertEqual([s.events for s in calls], [20, count])
            self.assertEqual(len(report["scenarios"]), 1)
            command_mock.assert_not_called()

    async def test_default_order_and_warmups_unchanged(self):
        report, calls, _ = await self.run_plan()
        names = ["s1", "s2", "s3", "s4", "s5", "steady", "burst", "s3-repeat"]
        self.assertEqual(
            [s["configuration"]["name"] for s in report["scenarios"]], names
        )
        self.assertEqual(
            [s.name for s in calls],
            [n for name in names for n in ("warm-" + name, name)],
        )

    async def test_insufficient_memory_skips_large_custom_before_warmup(self):
        report, calls, _ = await self.run_plan(
            [Workload("custom", 5000, 32)], memory=5 * 1024**3
        )
        self.assertEqual(calls, [])
        self.assertEqual(len(report["skipped"]), 1)

    async def test_unhealthy_custom_preflight_stops_before_measured_load(self):
        report, calls, _ = await self.run_plan(
            [Workload("custom", 5000, 32)], unhealthy="warm-custom"
        )
        self.assertEqual([s.name for s in calls], ["warm-custom"])
        self.assertEqual(report["scenarios"], [])
        self.assertEqual(
            report["skipped"][0]["reason"], "warm-up preflight escalation gate"
        )

    async def test_unhealthy_default_predecessor_skips_large_scenario(self):
        report, calls, _ = await self.run_plan(unhealthy="s4")
        self.assertNotIn("s5", [s.name for s in calls])
        self.assertNotIn("warm-s5", [s.name for s in calls])
        self.assertEqual(report["skipped"][0]["scenario"]["name"], "s5")

    async def test_preflight_failure_propagates_and_stops(self):
        with self.assertRaises(TimeoutError):
            await self.run_plan([Workload("custom", 5000, 32)], failure=True)

    async def test_invalid_scenarios_rejected_before_connection(self):
        from benchmarks.performance import execute

        for custom in (
            [],
            [Workload("custom", 0, 4)],
            [Workload("custom", 5001, 4)],
            [Workload("custom", 100, 33)],
        ):
            with (
                patch("benchmarks.performance.Streams") as streams,
                self.assertRaises(ValueError),
            ):
                await execute(SimpleNamespace(api="unused"), {}, Mock(), custom)
            streams.assert_not_called()

    def test_escalation_boundaries_and_missing_evidence(self):
        from benchmarks.performance import escalation_allowed

        healthy = {"backlog_drain_observed_s": 30, "peak_sampled_lag": 2500}
        self.assertTrue(escalation_allowed(healthy, 6 * 1024**3))
        self.assertFalse(escalation_allowed(None, 8 * 1024**3))
        self.assertFalse(
            escalation_allowed({**healthy, "peak_sampled_lag": 2501}, 8 * 1024**3)
        )
        self.assertFalse(escalation_allowed(healthy, 6 * 1024**3 - 1))


class PortableConfigurationTests(unittest.TestCase):
    def test_repository_paths_normalized_without_changing_configuration(self):
        from benchmarks.performance import normalize_repository_paths

        root = Path("/workspace/WayPoint")
        original = {
            "services": {
                "backend": {
                    "build": {"context": str(root / "backend")},
                    "image": "registry.example/backend:latest",
                    "ports": ["127.0.0.1:1234:8000"],
                    "cpus": 1.0,
                    "mem_limit": "384m",
                    "environment": {"URL": "http://backend:8000", "ENABLED": True},
                }
            }
        }
        actual = normalize_repository_paths(original, root)
        self.assertEqual(
            actual["services"]["backend"]["build"]["context"], "${REPO_ROOT}/backend"
        )
        restored = json.loads(json.dumps(actual).replace("${REPO_ROOT}", str(root)))
        self.assertEqual(restored, original)
        self.assertEqual(
            original["services"]["backend"]["build"]["context"], str(root / "backend")
        )

    def test_outside_sibling_relative_urls_and_image_names_unchanged(self):
        from benchmarks.performance import normalize_repository_paths

        root = Path("/workspace/WayPoint")
        for value in (
            "/other/home/data",
            "/workspace/WayPoint-other/backend",
            "/workspace/WayPoint/../outside",
            "../backend",
            "http://host/workspace/WayPoint/backend",
            "image:/workspace/WayPoint/backend:tag",
            "${REPO_ROOT}/backend",
            384,
            None,
            False,
        ):
            self.assertEqual(normalize_repository_paths(value, root), value)
        self.assertEqual(normalize_repository_paths(str(root), root), "${REPO_ROOT}")
        self.assertEqual(
            normalize_repository_paths(str(root / "folder with spaces"), root),
            "${REPO_ROOT}/folder with spaces",
        )

    def test_future_capture_uses_portable_structured_configuration(self):
        from benchmarks.performance import ROOT, capture_configuration

        stack = Mock()
        stack.compose.return_value = json.dumps(
            {
                "services": {
                    "backend": {
                        "build": {"context": str(ROOT / "backend")},
                        "ports": ["127.0.0.1:1234:8000"],
                    }
                }
            }
        )
        actual = json.loads(capture_configuration(stack))
        self.assertEqual(
            actual["services"]["backend"]["build"]["context"], "${REPO_ROOT}/backend"
        )
        self.assertEqual(
            actual["services"]["backend"]["ports"], ["127.0.0.1:1234:8000"]
        )
        stack.compose.assert_called_once_with("config", "--format", "json")


class GeneratedFormattingTests(unittest.TestCase):
    def assert_staged_git_check(self, directory, filename):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
        for args in (
            ["init", "--quiet"],
            ["add", "--", filename],
            ["diff", "--cached", "--check"],
        ):
            result = subprocess.run(
                ["git", "-C", str(directory), *args],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_svg_geometry_and_labels_survive_staged_whitespace_check(self):
        from benchmarks.report_performance import normalize_svg_whitespace

        original = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">\n<path d="M 0 0 \nL 10 10 \nz\n"/>\n<text x="1" y="2">p95: 13.79 ms</text>\n</svg>\n'
        before = ET.fromstring(original)
        with tempfile.TemporaryDirectory(prefix="m8b-svg-test-") as name:
            path = Path(name) / "performance.svg"
            path.write_text(original, encoding="utf-8")
            normalize_svg_whitespace(path)
            after = ET.fromstring(path.read_text(encoding="utf-8"))
            self.assertEqual(before.attrib, after.attrib)
            self.assertEqual(
                before[0].attrib["d"].split(), after[0].attrib["d"].split()
            )
            self.assertEqual(before[1].attrib, after[1].attrib)
            self.assertEqual(before[1].text, after[1].text)
            first = path.read_bytes()
            normalize_svg_whitespace(path)
            self.assertEqual(path.read_bytes(), first)
            self.assert_staged_git_check(path.parent, path.name)

    def test_csv_values_and_order_survive_lf_staged_whitespace_check(self):
        from benchmarks.report_performance import write_summary_csv

        fields = ["scenario", "events", "p95_ms"]
        rows = [
            {"scenario": "s3", "events": 1000, "p95_ms": 13.787929085083283},
            {"scenario": "steady", "events": 200, "p95_ms": 4.161677119554952},
        ]
        original = io.StringIO(newline="")
        writer = csv.DictWriter(original, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
        with tempfile.TemporaryDirectory(prefix="m8b-csv-test-") as name:
            path = Path(name) / "summary.csv"
            write_summary_csv(path, fields, rows)
            actual = path.read_bytes()
            self.assertNotIn(b"\r", actual)
            self.assertEqual(
                list(csv.reader(io.StringIO(original.getvalue(), newline=""))),
                list(csv.reader(io.StringIO(actual.decode(), newline=""))),
            )
            self.assert_staged_git_check(path.parent, path.name)
