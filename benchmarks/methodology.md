# Milestone 8B: reproducible asynchronous performance benchmarking

The suite benchmarks the existing FastAPI → Redpanda → serial Python consumer →
PostgreSQL → LISTEN/NOTIFY → FastAPI SSE → Nginx path. A pinned React build is
served by Nginx. Controlled Python/httpx workers replace the C++ simulator for
load generation; C++ event generation performance is outside these measurements.
The existing C++ simulator and both CTest suites remain unchanged.

The repository baseline is main `199b5562779728d3a0eddcaf2f42f831b335da71`.
Historical owner-reported synchronous results remain in README unchanged. They
lack raw reports and hardware details and cannot be compared directly with new
broker acknowledgement latencies.

## Setup and execution

From the repository root, with Docker Desktop, Python 3.12, and Node.js 22+ installed:

```sh
python3.12 -m venv .venv312
.venv312/bin/python -m pip install -r backend/requirements.txt -r benchmarks/requirements.txt
.venv312/bin/python -m benchmarks.development_guard --output /private/tmp/m8b-before.json
.venv312/bin/python -m unittest discover -s benchmarks -p 'test_*.py' -v
.venv312/bin/python -m benchmarks.performance --output /private/tmp/m8b-results.json
.venv312/bin/python -m benchmarks.development_guard --output /private/tmp/m8b-after.json --compare /private/tmp/m8b-before.json
.venv312/bin/python -m benchmarks.report_performance --input /private/tmp/m8b-results.json --output /private/tmp/m8b-report
```

Optional bounded custom run (it still creates its own disposable stack):

```sh
.venv312/bin/python -m benchmarks.performance --events 500 --concurrency 8 --vehicles 50 --rate 40 --output /private/tmp/m8b-custom.json
```

The orchestrator creates a fresh `waypoint-m8b-<12 hex>` Compose project, a unique
three-partition topic and consumer group, and an empty `m8b` database. It passes
an empty Compose env file, uses no development networks or service references,
and derives every host endpoint from inspected project labels and loopback-only
port mappings. HTTP clients disable environment proxy use. There is deliberately no target URL/DSN/project option. It runs
only the isolated database migrations and checks that containers have no volume
mounts. Database and broker use capped tmpfs. Their containers, project networks,
and data disappear in `finally`; project image tags are also removed, while build cache is retained. Named Docker volumes are never deleted.

If interrupted, use its JSON manifest for scoped cleanup and removal of its
project image tags (build cache is retained):

```sh
.venv312/bin/python -m benchmarks.cleanup_performance /private/tmp/m8b-results.json
```

No generic `down`, volume prune, or development lifecycle command is needed.
The development snapshot helper alone reads the normal database, inside a
read-only transaction, and records row fingerprints, IDs, health, restart counts
and volume names. It is never used by the load generator.

## Workloads and safety gates

Default scenarios: 100/4, 500/8, 1000/8, 2500/16, and conditionally 5000/32
(events/concurrency), a 200-event 40/s steady-rate test, a 500/16 closed-loop burst,
and a second 1000/8 run. Vehicle cardinalities are retained per scenario. Each has
20 unique warm-up events that fully drain before measurement. Thirty additional
single-event vehicles at 10/s provide an unambiguous SSE probe. A duplicate probe
retries one persisted ID and verifies the database count remains unchanged.

Capped worker count ≤32, events ≤5000 per scenario, vehicles ≤1000, rate ≤500/s;
no unbounded task per event. Requests time out after 20 seconds; at most one
same-ID client retry follows a transport/5xx error, with 100 ms backoff. Failed
attempts remain in JSON and prevent escalation. Persistence/send waits are bounded
to 120 seconds, final offset drain to 20 seconds. Memory >85% of a benchmark
container limit stops further work. The 5000-event run additionally requires a
Docker memory allocation ≥6 GiB and the prior scenario's observed drain ≤30 s
and sampled lag ≤2500. For a standalone custom 5000-event run with no preceding
scenario, the existing 20-event warm-up supplies the lag/drain health preflight;
insufficient memory or an unhealthy preflight skips the measured workload.
Warm-up errors abort the run. Default scenario ordering and escalation limits
remain unchanged. Limits for all services are in `compose.performance.yml`.
Any failed integrity/error gate aborts the suite and preserves the partial JSON.
These are experiment safety gates, not recommended operational SLOs.

## Measurements and their limits

- HTTP 202 is validated with `X-Waypoint-Delivery: broker-acknowledged`. Attempt
  latency includes failures. Logical completion (including retries), timeout and
  transport errors, and acknowledgement throughput are retained separately.
- All duration calculations use the same host monotonic clock. UTC payload times
  only order latest-state updates. There is no cross-process clock subtraction.
- A read-only host observer scans newly committed IDs every ~75 ms plus query and
  metric-scrape overhead. Ascending-ID scanning assumes the current single serial
  writer; parallel consumers would require replacing this observer. Observations
  bound commit latency from above and include polling delay and instrumentation
  overhead. Every expected ID and payload is reconciled separately at completion.
- Consumer lag gauges and handled-event counters are sampled throughout each run.
  Gauge peaks may miss short spikes; end offsets per partition are retained with
  `rpk topic describe -p`. Broker acknowledgement throughput may exceed durable
  consumer throughput; drain time uses the last database observation.
- Two SSE clients connect before load. Counts distinguish generated frames,
  received hints, listener readiness and queue overflows. Reused vehicle IDs mean
  hints cannot be assigned one-to-one to event rows. Only the unique-vehicle probe
  correlates POST start with matching vehicle hint receipt. Server notification
  timestamp-to-client latency is unavailable; no timestamp is invented.
- The browser probe runs when macOS Chrome and Node (native WebSocket) are
  available. A new temporary browser profile targets only the isolated frontend.
  The first client-side fleet response omits one persisted vehicle; subsequent
  authoritative reads recover it. One client uses SSE open/resync, another closes
  only its own EventSource and recovers through five-second polling. CDP records
  actual DOM appearance. This measures snapshot recovery, not ingestion-to-render.
- Docker stats samples CPU, memory and PIDs roughly every three seconds. CPU 100%
  means one core. Short requests may fall inside one sampling window. PostgreSQL
  cache, transaction and temp-I/O counters are cumulative. No query-level causal
  profile or disk-bound durability claim is made.
- Repeat `s3`/`s3-repeat` under the same limits to assess observed variability;
  the database grows and caches warm, so this is not a randomized independent
  trial. Single-run p99 values, especially at 100 requests, have limited precision.

## Artifacts and review

Machine-readable JSON contains configuration, images, package/runtime versions,
per-request attempts, database observations, lag/resource samples, integrity,
health and cleanup. CSV provides scenario summaries. Markdown and PNG/SVG charts
are regenerated from JSON with the reporting command above. Large raw timing
artifacts may be kept outside Git; selected review artifacts live under
`benchmarks/results/`. Inspect the measured report before recommending caching,
consumer concurrency or indexes. No production semantics are altered by this suite.

Captured Compose configuration uses `${REPO_ROOT}` for repository-local absolute
paths. Future captures use JSON-formatted Compose configuration so path values
can be normalized without rewriting image names, ports or other settings. Paths
outside the repository are preserved rather than implicitly redacted; inspect
artifacts before publishing them. The retained local result had only its four
repository build-context paths normalized; measurements and original validation
metadata remain unchanged.
