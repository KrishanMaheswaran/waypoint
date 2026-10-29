from __future__ import annotations

import asyncio
import random
import unittest
from datetime import datetime, timezone

import httpx

from run_benchmark import (
    MAX_CONCURRENCY,
    generate_event,
    percentile,
    summarize,
    validate_base_url,
)


class EventGenerationTests(unittest.TestCase):
    def test_event_matches_api_schema_and_is_unique(self) -> None:
        rng = random.Random(12)
        timestamp = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
        first = generate_event(
            run_id="test-run",
            sequence=0,
            vehicle_count=3,
            rng=rng,
            timestamp=timestamp,
        )
        second = generate_event(
            run_id="test-run",
            sequence=1,
            vehicle_count=3,
            rng=rng,
            timestamp=timestamp,
        )

        self.assertEqual(len(first["event_id"]), 36)
        self.assertNotEqual(first["event_id"], second["event_id"])
        self.assertEqual(first["vehicle_id"], "BENCH-test-run-0001")
        self.assertEqual(second["vehicle_id"], "BENCH-test-run-0002")
        self.assertGreaterEqual(first["battery_percentage"], 20)
        self.assertLessEqual(first["battery_percentage"], 100)
        self.assertIn(first["status"], {"idle", "charging", "ready", "maintenance"})
        self.assertEqual(first["timestamp"], "2026-10-29T12:00:00Z")
        self.assertEqual(
            set(first),
            {
                "event_id",
                "vehicle_id",
                "battery_percentage",
                "status",
                "temperature",
                "fault_code",
                "timestamp",
            },
        )

    def test_rejects_non_utc_naive_timestamp(self) -> None:
        with self.assertRaises(ValueError):
            generate_event(
                run_id="test",
                sequence=0,
                vehicle_count=1,
                rng=random.Random(1),
                timestamp=datetime(2026, 10, 29),
            )

    def test_warmup_and_measurement_can_share_vehicle_without_sharing_event_id(self) -> None:
        rng = random.Random(13)
        warmup = generate_event(
            run_id="same-run",
            sequence=0,
            vehicle_count=2,
            rng=rng,
        )
        measured = generate_event(
            run_id="same-run",
            sequence=0,
            vehicle_count=2,
            rng=rng,
        )

        self.assertEqual(warmup["vehicle_id"], measured["vehicle_id"])
        self.assertNotEqual(warmup["event_id"], measured["event_id"])


class StatisticsTests(unittest.TestCase):
    def test_percentile_interpolation(self) -> None:
        self.assertEqual(percentile([1, 2, 3, 4], 50), 2.5)
        self.assertEqual(percentile([], 95), None)

    def test_summarize_separates_success_and_failures(self) -> None:
        result = summarize(
            requested_events=5,
            successful_events=3,
            status_codes=[201, 201, 503, 422],
            transport_errors=["ConnectError"],
            latency_seconds=[0.01, 0.02, 0.03, 0.04, 0.05],
            duration_seconds=2.0,
        )

        self.assertEqual(result.successful_events, 3)
        self.assertEqual(result.success_status_counts, {"201": 2})
        self.assertEqual(result.http_error_count, 2)
        self.assertEqual(result.status_counts, {"422": 1, "503": 1})
        self.assertEqual(result.transport_error_count, 1)
        self.assertEqual(result.transport_error_counts, {"ConnectError": 1})
        self.assertEqual(result.successful_events_per_second, 1.5)
        self.assertAlmostEqual(result.request_latency_ms["p50"], 30.0)


class SafetyAndConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    def test_only_loopback_targets_are_allowed(self) -> None:
        self.assertEqual(validate_base_url("http://localhost:8000/"), "http://localhost:8000")
        self.assertEqual(validate_base_url("http://[::1]:8000"), "http://[::1]:8000")
        for target in (
            "https://localhost:8000",
            "http://example.com",
            "http://localhost.example.com",
            "http://user:pass@127.0.0.1:8000",
        ):
            with self.subTest(target=target), self.assertRaises(ValueError):
                validate_base_url(target)

    async def test_async_requests_respect_concurrency_limit(self) -> None:
        active = 0
        maximum_active = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return httpx.Response(201)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            from run_benchmark import send_events

            events = [
                generate_event(
                    run_id="concurrency",
                    sequence=index,
                    vehicle_count=4,
                    rng=random.Random(index),
                )
                for index in range(12)
            ]
            result = await send_events(client, "http://127.0.0.1:8000/telemetry", events, 3)

        self.assertEqual(result[0], 12)
        self.assertLessEqual(maximum_active, 3)
        self.assertEqual(MAX_CONCURRENCY, 32)


if __name__ == "__main__":
    unittest.main()
