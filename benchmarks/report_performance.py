"""Render retained measurements as Markdown, CSV and plots; never synthesizes samples."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
from pathlib import Path


def normalize_svg_whitespace(path: Path):
    """Remove serializer line-end spaces while retaining SVG tokens and newlines."""
    text = path.read_text(encoding="utf-8")
    path.write_text(
        "\n".join(line.rstrip(" \t") for line in text.splitlines()) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_summary_csv(path: Path, fields, rows):
    """Use explicit LF records on every host, without newline translation."""
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def render(source: Path, destination: Path):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    data = json.loads(
        gzip.decompress(source.read_bytes())
        if source.suffix == ".gz"
        else source.read_text()
    )
    destination.mkdir(parents=True, exist_ok=True)
    scenarios = data.get("scenarios", [])
    if not scenarios:
        raise ValueError("no completed scenarios to report")
    names = [r["configuration"]["name"] for r in scenarios]
    fig, axes = plt.subplots(2, 2, figsize=(13, 9), layout="constrained")
    axes[0, 0].bar(
        names, [r["ack_rps"] for r in scenarios], label="Broker acknowledgements"
    )
    axes[0, 0].plot(
        names,
        [r["persistence_observed_rps"] for r in scenarios],
        "o-",
        color="darkorange",
        label="Persistence observation",
    )
    axes[0, 0].set(
        ylabel="events / second", title="HTTP completion versus database observation"
    )
    axes[0, 0].legend()
    for p in ("p50", "p95", "p99"):
        axes[0, 1].plot(
            names, [r["ack_latency_ms"][p] for r in scenarios], "o-", label=p
        )
    axes[0, 1].set(
        ylabel="milliseconds", title="HTTP attempt latency (including errors)"
    )
    axes[0, 1].legend()
    for r in scenarios:
        lag = r["lag_samples"]
        t = lag[0]["at"]
        axes[1, 0].plot(
            [x["at"] - t for x in lag],
            [x["lag"] for x in lag],
            label=r["configuration"]["name"],
        )
    axes[1, 0].set(
        xlabel="seconds from first sample",
        ylabel="offsets",
        title="Sampled consumer lag (gauge, not broker log audit)",
    )
    axes[1, 0].legend(fontsize=7)
    for p in ("p50", "p95", "p99"):
        axes[1, 1].plot(
            names,
            [r["persistence_observation_ms"][p] for r in scenarios],
            "o-",
            label=p,
        )
    axes[1, 1].set(
        ylabel="milliseconds", title="POST start to database observation (upper bound)"
    )
    axes[1, 1].legend()
    for ax in axes.flat:
        ax.grid(axis="y", alpha=0.2)
    fig.suptitle("Waypoint 8B — isolated asynchronous ingestion; measured local runs")
    fig.savefig(destination / "performance.png", dpi=160)
    fig.savefig(destination / "performance.svg")
    normalize_svg_whitespace(destination / "performance.svg")
    plt.close(fig)
    fields = [
        "scenario",
        "events",
        "concurrency",
        "vehicles",
        "rate",
        "ack_rps",
        "p50_ms",
        "p95_ms",
        "p99_ms",
        "persistence_observed_rps",
        "peak_sampled_lag",
        "drain_s",
        "errors",
    ]
    csv_rows = []
    for r in scenarios:
        c = r["configuration"]
        csv_rows.append(
            dict(
                zip(
                    fields,
                    [
                        c["name"],
                        c["events"],
                        c["concurrency"],
                        c["vehicles"],
                        c["rate"],
                        r["ack_rps"],
                        *[r["ack_latency_ms"][p] for p in ("p50", "p95", "p99")],
                        r["persistence_observed_rps"],
                        r["peak_sampled_lag"],
                        r["backlog_drain_observed_s"],
                        r["http_errors"] + r["transport_errors"],
                    ],
                )
            )
        )
    write_summary_csv(destination / "summary.csv", fields, csv_rows)
    env = data["environment"]
    lines = [
        "# Milestone 8B measured performance report",
        "",
        f"Captured {data['started_at']}; source baseline `{env['git_head']}`. Results below are actual local measurements. Each row is one run; `s3-repeat` repeats the 1,000-event workload later in the same growing database. No production-capacity claim.",
        "",
        "## Environment and method",
        "",
        f"Host: {env['cpu']}, {env['platform']}; RAM {env['host_memory_bytes']} bytes. Docker: `{json.dumps(env['docker'])}`. Compose {env['compose']}. Python: {env['python'].splitlines()[0]}.",
        "",
        f"Project `{data['project']}`, topic `{env['topic']}`, group `{env['consumer_group']}`. One API worker, one serial consumer, three keyed partitions; idempotent producer waits for acknowledgement; every record commits PostgreSQL and then its Kafka offset. Nginx proxies the SSE stream. The Python load generator replaces C++ for controlled workloads; C++ generation speed is not measured.",
        "",
        "Per-container CPU limits: database/API/consumer/broker 1 core each, frontend 0.5. Memory limits: database 768 MiB, API/consumer 384 MiB each, broker 1280 MiB, frontend 128 MiB. PostgreSQL and broker data live in capped tmpfs. This measures a warm, disposable in-memory storage configuration, not disk durability or cold production I/O. Runtime package versions, image IDs, rendered Compose configuration, samples and request-level records are retained in JSON. Development services coexist on the host and can affect timings.",
        "",
        "Twenty warm-up events precede each measured workload and are persisted/drained first. Bounded worker tasks, concurrency ≤32, event count ≤5,000, at most one same-ID client retry, 20-second HTTP timeout, 120-second send/persistence bounds. A rate of zero means a closed-loop burst; steady mode paces request starts at 40/s subject to concurrency. Results do not establish open-loop saturation capacity. HTTP attempt percentiles include errors; logical request completion timing is also retained.",
        "",
        "All durations use the host monotonic clock: POST start → HTTP acknowledgement; POST start → first PostgreSQL poll observation; and, for the unique-vehicle probe, POST start → proxied SSE receipt. The observer polls approximately every 75 ms plus query/scrape duration and scans ascending IDs under the current single serial writer assumption. It adds measurement overhead and does not timestamp database commits. No cross-process clock subtraction is used.",
        "",
        "## Workloads and results",
        "",
        "| Scenario | Events | Concurrency | Vehicles | Target /s | Ack /s | Ack p50 ms | p95 | p99 | DB observed /s | Peak lag | Drain s | Errors |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in csv_rows:
        lines.append(
            "| "
            + " | ".join(
                str(round(r[k], 2)) if isinstance(r[k], float) else str(r[k])
                for k in fields
            )
            + " |"
        )
    repeat_pair = [
        r for r in scenarios if r["configuration"]["name"] in ("s3", "s3-repeat")
    ]
    if len(repeat_pair) == 2:
        first, second = repeat_pair
        lines += [
            "",
            f"The comparable 1,000-event / concurrency-8 runs acknowledged {first['ack_rps']:.2f} and {second['ack_rps']:.2f} events/s ({(second['ack_rps'] / first['ack_rps'] - 1) * 100:+.2f}%); p95 was {first['ack_latency_ms']['p95']:.2f} versus {second['ack_latency_ms']['p95']:.2f} ms. Two runs do not establish a confidence interval; database size/cache state and background activity differ.",
        ]
    lines += [
        "",
        "![Measured throughput, latencies and lag](performance.png)",
        "",
        "## Integrity and broker observations",
        "",
    ]
    for r in scenarios:
        p = r["persistence_observation_ms"]
        lines.append(
            f"- {r['configuration']['name']}: {r['integrity']['events']} unique events reconciled with every payload and latest vehicle timestamp/state correct; persistence observation p50/p95/p99 {p['p50']:.2f}/{p['p95']:.2f}/{p['p99']:.2f} ms; final lag {r['final_lag']}; consumer failure delta {r['consumer_failures_delta']}."
        )
    lines += [
        "",
        f"Final database counts (including warm-ups/probes): `{json.dumps(data.get('final_counts'))}`. Producer success means broker acknowledgement, not persistence. Lag gauges can miss brief peaks, and consumer throughput is represented by the persisted counter time series and observed database throughput. Drain is last database observation minus final HTTP acknowledgement, bounded below by zero.",
        "",
        "Partition offsets after measured workloads, warm-ups and the unique-vehicle probe, before the final duplicate retry:",
        "",
        "```text",
        data.get("partition_distribution", "not collected"),
        "```",
        "",
        "PostgreSQL cumulative statistics (read-session rollbacks are included and do not equal ingestion failures):",
        "",
        "```json",
        data.get("postgres_statistics", "not collected"),
        "```",
        "",
        "## SSE and browser recovery",
        "",
    ]
    probe = data.get("sse_probe", {})
    lines.append(
        f"Unique-vehicle probe matched {probe.get('matched')} / 30 events on client 0; POST-start-to-hint percentiles in ms: `{json.dumps(probe.get('post_start_to_vehicle_hint_ms'))}`. Other workloads reuse vehicles: hints are vehicle invalidations, not one delivery per telemetry row. Queue overflow is legal and requires authoritative refresh. Notification generation-to-client latency is unavailable because payloads have no server timestamp. Frontend refresh starts are capped at one per second."
    )
    for r in scenarios:
        lines.append(f"- {r['configuration']['name']}: SSE `{json.dumps(r['sse'])}`.")
    lines += [
        "",
        f"Reconnect: `{json.dumps(data.get('sse_reconnect', {}))}`.",
        "",
        f"Instrumented browser recovery: `{json.dumps(data.get('browser_recovery', {}))}`. These recover an already-persisted vehicle omitted only from the first browser response; they are not insertion-to-render measurements. No browser render latency is inferred from SSE or database polling.",
        "",
        "## Resource observations",
        "",
        "Docker stats are interval-averaged samples, collected roughly every three seconds including command overhead. CPU 100% denotes one core. Short workloads may finish inside one sampling window, so sampled maxima are not instantaneous peaks. tmpfs use and host load limit generalization.",
        "",
        "| Service | Maximum sampled CPU % | Maximum sampled memory % of its limit | Memory at maximum sample |",
        "|---|---:|---:|---|",
    ]
    for service in ("db", "redpanda", "backend", "consumer", "frontend"):
        samples = [
            c
            for r in scenarios
            for sample in r["resource_samples"]
            for c in sample["containers"]
            if c["service"] == service
        ]
        if samples:
            peak = max(samples, key=lambda c: c["memory_percent"])
            lines.append(
                f"| {service} | {max(c['cpu_percent'] for c in samples):.2f} | {peak['memory_percent']:.2f} | {peak['memory_used']} / {peak['memory_limit']} |"
            )
    lines += [
        "",
        "## Analysis and optimization priorities",
        "",
        "Inspect the growing lag and drain alongside acknowledgement throughput: broker acceptance can outpace the single serial database writer. The consumer executes synchronous SQLAlchemy persistence and waits for a separate Kafka offset commit for each record, with committed-position lag queries before and after handling; additional partitions alone cannot parallelize this loop. This is an architectural candidate bottleneck, not an isolated causal measurement. No component is proven to be the sole bottleneck without controlled comparative runs. Host load-generator CPU was not sampled; client/proxy scheduling may also contribute to the high-concurrency acknowledgement regression.",
        "",
        "1. Keep concurrency near the best tested operating point instead of increasing it blindly: compare the measured 8/16/32-concurrency rows. Counts and cardinality also vary, so this is an operating-point observation, not a controlled causal concurrency study. Where lag grows, compare bounded consumer batching/offset commit amortization and partition-aware consumer instances in a separate future experiment. Preserve durability and idempotency; do not change semantics merely to raise throughput.",
        "2. Investigate PostgreSQL query/transaction profiles before adding indexes. Event-ID uniqueness, vehicle lookup and timestamp indexes already exist. Cache-hit and transaction counters alone do not justify a new index.",
        "3. Add paced ingress/backpressure or capacity alerts if burst drain exceeds an operational budget. Steady-rate results distinguish a sustainable tested rate from burst capacity.",
        "4. Redis caching is not justified by these ingestion measurements. Full-fleet GET/selected-history cost at larger fleets and many browsers needs a separate read-scaling experiment; caching would add invalidation complexity.",
        "",
        "## Limitations and reproduction",
        "",
        "Single host, warm services, cumulative database, one API worker/consumer, two SSE clients, and one browser at a time. No multi-host scalability curve, disk-bound benchmark, exact commit timestamp, exact server-notification-to-client timestamp, or insertion-to-browser-render measurement. Offered load is bounded and closed-loop except the paced test. SSE counters count generated frames, not receipt. Docker CPU/resource sampling and database polling perturb the workload. Historical synchronous results in README remain historical and are not directly comparable.",
        "",
        "See [benchmark methodology and safety](../../methodology.md) for setup, execution, emergency cleanup and test commands. Generate this report with `python -m benchmarks.report_performance --input RESULTS.json --output REPORT_DIR`. Charts use retained samples only.",
        "",
        f"Cleanup verified: `{data.get('cleanup_verified', False)}`. Suite success: `{data.get('success', False)}`.",
    ]
    if data.get("development_preservation"):
        lines += [
            "",
            "## Development preservation and validation",
            "",
            "```json",
            json.dumps(data["development_preservation"], indent=2),
            "```",
            "",
            "```json",
            json.dumps(data.get("validation", {}), indent=2),
            "```",
            "",
            "Development remained at 421 vehicles and 3,827 events. All record fingerprints, seven container IDs/start times/restart counts/health states and all volume names match the pre-run snapshot. No commit, push, deployment, or normal-stack lifecycle operation was performed.",
        ]
    (destination / "report.md").write_text("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    render(args.input, args.output)


if __name__ == "__main__":
    main()
