# Waypoint

Waypoint is a local fleet telemetry system built around FastAPI, a Kafka-compatible event stream, and PostgreSQL. A C++17 simulator generates vehicle readings, a React dashboard displays current state and history, and Prometheus/Grafana provide service observability. The repository also includes a loopback-only asynchronous ingestion benchmark and GitHub Actions CI.

The project is intended for local development and demonstration. It is not a highly available or production deployment.

## Engineering features

- Validated telemetry ingestion with durable PostgreSQL event history and Alembic-managed schema changes.
- Kafka-backed asynchronous ingestion with vehicle-keyed partitioning, manual consumer offset commits, and database-enforced event ID idempotency.
- Safe handling of identical retries, durable recording of conflicting/invalid broker records, and timestamp-aware latest vehicle state.
- C++17 simulator with configurable target, vehicle count, and interval; bounded retries for transient errors.
- Responsive React + TypeScript fleet dashboard using the existing API, with status/source filters, search, pagination, and historical trends.
- Low-cardinality Prometheus HTTP and ingestion metrics, a configured scrape target, and a provisioned Grafana dashboard.
- Async benchmark tool with bounded concurrency, warm-up, latency statistics, and loopback-only target validation.
- Independent backend, simulator, and frontend GitHub Actions jobs.

## Architecture

The solid arrows below are runtime traffic. The dashed arrows belong to CI and its disposable test database; CI does not deploy the application.

```mermaid
flowchart LR
  subgraph Runtime["Runtime — Docker Compose"]
    Simulator["C++17 telemetry simulator"] -->|"POST /telemetry"| API["FastAPI producer"]
    API -->|"key = vehicle_id"| Broker["Redpanda / Kafka-compatible broker"]
    Broker -->|"manual offset commit after DB handling"| Consumer["Telemetry consumer"]
    Consumer -->|"SQLAlchemy persistence"| PostgreSQL[("PostgreSQL 16")]
    Browser["Operator browser"] -->|"HTTP"| Nginx["Nginx frontend"]
    Nginx -->|"/api/* (prefix removed)"| API
    API -->|"vehicle state/history reads"| PostgreSQL
    Alembic["Alembic migrations"] -.->|"schema changes"| PostgreSQL
    Prometheus["Prometheus"] -->|"scrape GET /metrics"| API
    Prometheus -->|"scrape consumer metrics"| Consumer
    Grafana["Grafana dashboard"] -->|"PromQL"| Prometheus
  end

  subgraph CI["GitHub Actions — CI only"]
    Actions["GitHub Actions"] --> BackendTests["Backend pytest"]
    Actions --> SimulatorTests["CMake build + CTest"]
    Actions --> FrontendTests["Frontend test + build + npm audit"]
    BackendTests -.->|"disposable service"| TestPostgreSQL[("PostgreSQL 16 test database")]
  end
```

### Technology

| Area | Components |
|---|---|
| API and validation | Python 3.12, FastAPI, Pydantic |
| Persistence and stream | PostgreSQL 16, SQLAlchemy 2, Alembic, Redpanda (Kafka API), aiokafka |
| Simulator | C++17, CMake, cpp-httplib, nlohmann/json |
| Web UI | React 18, TypeScript, Vite |
| Serving and monitoring | Nginx, Prometheus, Grafana |
| CI | GitHub Actions |

## Quick start with Docker Compose

Prerequisites: Docker Desktop or Docker Engine with the Compose plugin, and Git.

1. Create a local environment file and replace the database and Grafana password placeholders with unique local passwords. Keep `.env` private; it is ignored by Git.

   ```bash
   cp -n .env.example .env
   ```

   If `.env` already exists, preserve it and merge any missing keys from `.env.example`. Updating `.env` does not rotate credentials already stored in initialized PostgreSQL or Grafana volumes.

2. Build the backend image, start PostgreSQL, and apply migrations. This command applies schema changes to the existing named PostgreSQL volume without deleting or resetting its data:

   ```bash
   docker compose build backend
   docker compose up -d db
   docker compose run --rm backend alembic upgrade head
   ```

3. Build and start the application, Redpanda broker, telemetry consumer, and monitoring services:

   ```bash
   docker compose up --build -d
   docker compose ps
   ```

4. Check the frontend and API:

   ```bash
   curl --fail http://127.0.0.1:8080/
   curl --fail http://127.0.0.1:8080/api/health
   curl --fail http://127.0.0.1:8000/health
   ```

| Service | Local address |
|---|---|
| React dashboard via Nginx | http://127.0.0.1:8080 |
| FastAPI | http://127.0.0.1:8000 |
| Kafka-compatible broker (host clients) | localhost:19092 |
| Prometheus | http://127.0.0.1:9090 |
| Grafana | http://127.0.0.1:3000 |

The dashboard reaches the API through Nginx at `/api/...`; Nginx removes the `/api` prefix before proxying. Direct API access is also published for local development. Published ports bind to loopback by default. Compose keeps `postgres_data`, `redpanda_data`, `prometheus_data`, and `grafana_data` as named persistent volumes. Use `docker compose stop` to stop services without removing their data.

The single-node Redpanda broker is for local development only; it is not highly available. The API publishes events to `waypoint.telemetry`, partitioned by `vehicle_id`, and the consumer stores them in PostgreSQL. Broker durability and database persistence are separate stages.

To apply later schema migrations, run `docker compose run --rm backend alembic upgrade head`. The Milestone 7A migration adds the ingestion-rejection audit table; it does not delete or rewrite existing telemetry. Migrations are not run automatically when the backend container starts.

### Troubleshooting

- Use `docker compose ps` and `docker compose logs --tail=100 db redpanda kafka-init backend consumer frontend prometheus grafana` to inspect service state and startup errors.
- The backend depends on a healthy database. Confirm its Compose connection uses host `db` and container port `5432`; `127.0.0.1` is for host-side connections.
- If the broker is not healthy, confirm `redpanda` is running and `kafka-init` completed successfully; backend and consumer use the internal address `redpanda:9092`.
- A successful `POST /telemetry` means the broker acknowledged the event, not that the consumer has committed it to PostgreSQL. Check consumer logs and metrics while waiting for persistence.
- Compose requires `POSTGRES_PASSWORD`, `GRAFANA_ADMIN_USER`, and `GRAFANA_ADMIN_PASSWORD` in `.env`. Never commit the local `.env`.
- If an initialized PostgreSQL or Grafana volume contains different credentials, changing `.env` alone does not change the persisted account password.
- If `/api/health` fails, check backend health and frontend logs, and confirm Nginx can resolve the `backend` Compose service.

## API reference

The public API is implemented in [`backend/app/routes.py`](backend/app/routes.py), with request/response validation in [`backend/app/schemas.py`](backend/app/schemas.py).

| Method and path | Behavior |
|---|---|
| `GET /health` | Returns `{"status":"ok"}` when the API process responds. |
| `GET /metrics` | Exposes Prometheus text-format metrics; excluded from HTTP request instrumentation. |
| `POST /telemetry` | Validates and publishes one telemetry event to Kafka. Returns `202` after broker acknowledgment, not after PostgreSQL persistence; invalid payload: `422`; broker unavailable after bounded retries: `503`. |
| `GET /vehicles` | Returns each vehicle's latest known state. |
| `GET /vehicles/{vehicle_id}/telemetry?limit=50` | Returns recent event history, newest timestamp first. `limit` must be between 1 and 1000; default is 50. |

`POST /telemetry` accepts this JSON shape:

```json
{
  "event_id": "0df306c9-cc18-49cb-9f75-51489f8f3d71",
  "vehicle_id": "WP-1001",
  "battery_percentage": 88.5,
  "status": "ready",
  "temperature": 24.7,
  "fault_code": null,
  "timestamp": "2026-10-26T12:00:00Z"
}
```

| Field | Type and validation |
|---|---|
| `event_id` | String, 1–100 characters; unique across stored telemetry events. |
| `vehicle_id` | String, 1–100 characters. |
| `battery_percentage` | Number from 0 through 100. |
| `status` | One of `idle`, `charging`, `ready`, or `maintenance`. |
| `temperature` | Number from -100 through 200. |
| `fault_code` | String up to 100 characters, or `null`. |
| `timestamp` | Datetime accepted by Pydantic; clients emit UTC ISO 8601 timestamps. |

### Idempotency and event ordering

- The `telemetry_events.event_id` unique constraint is enforced by PostgreSQL. The Alembic migration backfills IDs for existing rows before making the field non-null and unique, preserving existing telemetry.
- `POST /telemetry` returns `202 Accepted` when Kafka acknowledges the keyed record. The `X-Waypoint-Delivery: broker-acknowledged` response header identifies this boundary; it is not a database-persistence acknowledgment. A `503` means bounded publish attempts failed and includes `Retry-After: 1`; retry with the same `event_id`.
- The consumer commits Kafka offsets only after successful PostgreSQL persistence. Identical event replay is recognized by comparing vehicle ID, measurements, status, fault code, and timestamp; it creates no second history row. Database uniqueness and conflict recovery make duplicate delivery safe. This is at-least-once processing with database-enforced idempotency, not exactly-once processing.
- The same `event_id` with different event content is detected by the consumer after the API may already have returned `202`. The conflicting record is durably recorded in `telemetry_ingestion_failures`, logged, and then its offset is committed so it does not block the partition. There is no synchronous conflict response or event-status lookup route.
- Valid older events are retained in telemetry history. The `vehicles` latest-state row is updated only when the incoming timestamp is strictly newer than `last_updated`; a delayed event therefore cannot roll current state backward.
- Messages are partitioned by `vehicle_id` to preserve broker order within each vehicle's partition. This does not guarantee timestamp order; the database's strict-newer timestamp check remains the safeguard for vehicle state.
- The API producer and consumer use bounded retries with jitter. Producer publish attempts are limited to three with five-second per-attempt timeouts; the simulator treats every `2xx` response, including `202`, as success and retries connection failures and HTTP `429`/`5xx` responses up to three times after the initial attempt. Stable event IDs make retry/replay safe.
- Consumer validation failures and reused event IDs with conflicting content are written to `telemetry_ingestion_failures` before offsets are committed. Transient database or broker commit errors are retried a bounded number of times; an exhausted retry causes the consumer process to exit so Compose can restart it from the last committed offset. The worker handles SIGINT/SIGTERM and Compose allows 20 seconds for orderly shutdown.

## C++ telemetry simulator

The simulator sends one event per configured vehicle in each cycle. Defaults are five vehicles, a two-second interval, and `http://localhost:8000`. IDs are `WP-1001`, `WP-1002`, and onward. Each event receives a UUID and retains the same ID and body across retries. Ctrl+C requests a graceful stop.

Install build tools:

```bash
# macOS
xcode-select --install
brew install cmake

# Debian/Ubuntu
sudo apt-get update
sudo apt-get install build-essential cmake
```

Configure, build, and test:

```bash
cmake -S simulator -B simulator/build
cmake --build simulator/build --parallel
ctest --test-dir simulator/build --output-on-failure
```

CMake fetches pinned nlohmann/json 3.11.3 and cpp-httplib 0.18.1 sources during configure; the initial configure therefore needs network access.

Run against the local API:

```bash
./simulator/build/waypoint-simulator
./simulator/build/waypoint-simulator --url http://127.0.0.1:8000 --vehicles 5 --interval 2
```

The CLI accepts `--url`, `--vehicles`, and `--interval` (seconds). Errors and retry attempts are logged; the simulator continues after a failed event rather than terminating.

## Fleet dashboard

The React + TypeScript frontend reads vehicle state and history from `GET /vehicles` and `GET /vehicles/{vehicle_id}/telemetry`. It polls every five seconds, displays loading/error/empty states, and plots timestamped battery and temperature history. The client classifies IDs beginning with `BENCH-` (case-insensitive) as benchmark vehicles; this is a documented naming convention, not an API field. The default source is non-benchmark vehicles when present, otherwise the dashboard falls back to all vehicles with an explanation.

For frontend development outside Docker, start the Compose backend first:

```bash
cd frontend
npm ci
npm run dev
```

Vite prints its local URL (normally `http://localhost:5173`) and proxies `/api` to `http://127.0.0.1:8000`. `VITE_API_BASE_URL` can override the frontend API base URL; do not put credentials in it.

## Monitoring

Prometheus scrapes `backend:8000/metrics` and the consumer metrics endpoint on port `8001` every five seconds on the private Compose network. FastAPI records HTTP request totals by normalized method, route template, and status code, plus request duration by method and route template. Kafka publish outcomes, consumer outcomes/failures, and partition lag are also exposed. Labels use bounded outcome/reason values and topic/partition; vehicle IDs and event IDs are not metric labels. `/metrics` is excluded from HTTP request metrics.

Sign in to Grafana using the local credentials configured through `GRAFANA_ADMIN_USER` and `GRAFANA_ADMIN_PASSWORD` in `.env`. The provisioned **Waypoint API** dashboard is available at http://127.0.0.1:3000/d/waypoint-api/waypoint-api and shows HTTP request rate, p50/p95 request latency, HTTP 4xx/5xx rate, and telemetry ingestion rate. Dashboard rate queries use a five-minute window; allow time and traffic for them to become informative. Histogram quantiles are estimates from configured buckets. This stack provides basic local observability, not alerting or high availability.

## Telemetry ingestion benchmarks

[`benchmarks/run_benchmark.py`](benchmarks/run_benchmark.py) is a standalone Python 3.12 tool using asynchronous `httpx` requests. It generates schema-valid events with run-scoped `BENCH-...` vehicle IDs and unique event IDs. Requests have a bounded semaphore and connection pool; concurrency is capped at 32 and measured event count at 1,000. The default target is `http://127.0.0.1:8000`; only plain HTTP loopback targets are accepted. It does not retry failed requests.

Five warm-up events run before measurement by default. Warm-up and measured events are both published to the broker. The report separates successful HTTP responses from HTTP errors and transport errors. Latency percentiles cover all measured requests, including failures; successful events per second is successful responses divided by measured wall-clock duration. With asynchronous ingestion, an HTTP success measures broker acknowledgment, not completion of PostgreSQL persistence; inspect consumer lag separately.

Reproducible local scenarios (each uses seed `42`, five warm-up events):

```bash
python3.12 -m pip install httpx==0.27.2
python3.12 benchmarks/run_benchmark.py --base-url http://127.0.0.1:8000 --events 100 --vehicles 20 --concurrency 4 --warmup-events 5 --seed 42
python3.12 benchmarks/run_benchmark.py --base-url http://127.0.0.1:8000 --events 500 --vehicles 50 --concurrency 8 --warmup-events 5 --seed 42
python3.12 benchmarks/run_benchmark.py --base-url http://127.0.0.1:8000 --events 1000 --vehicles 100 --concurrency 8 --warmup-events 5 --seed 42
```

### Results and limitations

The values below are historical local measurements reported by the project owner, from before the Kafka-backed Milestone 7A ingestion path. They were not rerun or independently verified for this documentation update and do not measure broker-acknowledgment performance. Each value represents one local run, not a repeated or statistically sampled result. No new load run was performed because benchmark events are written to the persistent PostgreSQL database.

| Events | Concurrency | Successful throughput (events/s) | p50 latency | p95 latency | p99 latency |
|---:|---:|---:|---:|---:|---:|
| 100 | 4 | 493.51 | 7.95 ms | 10.47 ms | 12.13 ms |
| 500 | 8 | 408.92 | 18.64 ms | 27.53 ms | 35.83 ms |
| 1,000 | 8 | 385.70 | 20.01 ms | 29.54 ms | 38.43 ms |

Run-specific environment details, vehicle counts, warm-up counts, and raw benchmark reports were not retained, so they are not inferred here. These are single-machine local measurements, not production capacity or scalability guarantees; results depend on the host, container resources, database state, concurrency, and background load. Future reports should retain the tool's JSON output and environment details.

The benchmark tool reports percentiles across all measured requests, including HTTP and transport failures; throughput is successful measured responses divided by measured wall-clock duration.

Run benchmark unit tests with:

```bash
python3.12 -m unittest discover -s benchmarks -p 'test_*.py' -v
```

## Screenshots

The dashboard screenshot below was captured from the running local Docker stack; it shows live API-backed state, not mock data.

![Waypoint fleet dashboard](docs/screenshots/fleet-dashboard.png)

Grafana requires authentication and the available browser session was not logged in, so no Grafana dashboard image is presented as a verified screenshot. To add one, start the monitoring stack, sign in at `http://127.0.0.1:3000` with local `.env` credentials, open the **Waypoint API** dashboard, choose a useful time range, and save a desktop capture as `docs/screenshots/grafana-dashboard.png`.

## Tests and CI

Backend tests (run from `backend/`):

```bash
python3.12 -m venv ../.venv
source ../.venv/bin/activate
python -m pip install -r requirements.txt
pytest -q
```

By default, the API and consumer tests use isolated in-memory SQLite. CI uses a disposable PostgreSQL 16 service and applies Alembic migrations before `pytest`; it also runs the Kafka ingestion smoke test in a separate Compose project with temporary PostgreSQL and Redpanda storage. The API test fixture drops and recreates ORM tables when `WAYPOINT_TEST_DATABASE_URL` is set, so that variable must only point to a disposable test database, never to the persistent local/shared/production database.

Run the end-to-end Kafka smoke test in its separate Compose project. Its PostgreSQL and Redpanda data directories are tmpfs, and it does not connect to the normal Waypoint PostgreSQL volume:

```bash
docker compose -f backend/tests/docker-compose.kafka-smoke.yml -p waypoint-m7a-smoke up -d db redpanda
docker compose -f backend/tests/docker-compose.kafka-smoke.yml -p waypoint-m7a-smoke run --build --rm kafka-init
docker compose -f backend/tests/docker-compose.kafka-smoke.yml -p waypoint-m7a-smoke run --build --rm backend alembic upgrade head
docker compose -f backend/tests/docker-compose.kafka-smoke.yml -p waypoint-m7a-smoke up --build -d backend consumer
curl --fail -i -H 'Content-Type: application/json' \
  -d '{"event_id":"replace-with-a-unique-id","vehicle_id":"M7A-SMOKE-1","battery_percentage":80,"status":"ready","temperature":24,"fault_code":null,"timestamp":"2026-10-30T12:00:00Z"}' \
  http://127.0.0.1:18000/telemetry
curl --fail http://127.0.0.1:18000/vehicles
docker compose -f backend/tests/docker-compose.kafka-smoke.yml -p waypoint-m7a-smoke stop
```

The POST response should be `202` with `X-Waypoint-Delivery: broker-acknowledged`; poll `GET /vehicles` until the consumer has persisted the event. Use a fresh event ID for each run. `stop` preserves the isolated containers while running; no command here alters the normal Waypoint volumes.

Run the isolated reliability experiments with `python3.12 backend/tests/reliability.py --scenario quick` (the CI subset) or `python3.12 backend/tests/reliability.py --scenario all` for consumer interruption, database access failure, in-flight restart, and broker outage coverage. The harness uses its own uniquely named Compose project and disposable `tmpfs` storage; details and one-run observed measurements are in [docs/reliability.md](docs/reliability.md).

Simulator tests are the CMake/CTest commands shown above. Frontend tests and a production build (run from `frontend/`) are:

```bash
npm ci
npm test
npm run build
npm audit --audit-level=high
```

Benchmark tests use the `unittest` command in the benchmark section. GitHub Actions is defined in [`.github/workflows/ci.yml`](.github/workflows/ci.yml) and runs independent backend, simulator, and frontend jobs on pushes to `main` and pull requests targeting `main`. CI grants `contents: read`; it does not deploy.

## Engineering tradeoffs

- Redpanda decouples API acceptance from database persistence. A single local broker is easy to run but is neither highly available nor a production Kafka deployment.
- PostgreSQL stores event history and a separately maintained latest-state row. Kafka offsets are committed only after persistence, so restarts can replay records; database idempotency prevents duplicate history rows but processing is not exactly once.
- A broker-acknowledged request may later be rejected by the consumer for an event ID conflict or invalid broker record. The rejection is logged and stored, but the client has no delivery-status query endpoint.
- The dashboard uses five-second polling instead of a persistent stream, keeping the browser and backend integration simple.
- The benchmark is intentionally loopback-only, bounded, and small enough for local testing. Its writes are not automatically cleaned up.
- Docker Compose provides a convenient single-host development stack. It does not provide production secrets management, TLS ingress, alerting, horizontal scaling, or high availability.
