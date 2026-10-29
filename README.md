# Waypoint

Waypoint is a portfolio-quality real-time fleet telemetry platform for backend and infrastructure engineering internships. Milestone 3B adds idempotent telemetry ingestion, event ordering, and bounded simulator retries.

## Project structure

- `backend/app/main.py` – FastAPI application entrypoint and router registration.
- `backend/app/config.py` – environment-based application settings for PostgreSQL.
- `backend/app/database.py` – SQLAlchemy engine, session factory, and database session dependency.
- `backend/app/models.py` – SQLAlchemy vehicle and telemetry models.
- `backend/app/schemas.py` – Pydantic validation models for telemetry inputs and outputs.
- `backend/app/routes.py` – FastAPI endpoints for telemetry ingestion and vehicle queries.
- `backend/alembic/` – Alembic migration configuration and version files, including the telemetry idempotency migration.
- `backend/tests/test_health.py` – pytest smoke test for `/health`.
- `backend/tests/test_telemetry_api.py` – endpoint and database validation tests for Milestone 2.
- `backend/requirements.txt` – Python dependencies for the backend.
- `backend/Dockerfile` – container configuration for the API service.
- `simulator/` – C++17 telemetry generator, HTTP client, and CTest unit tests.
- `benchmarks/` – standalone asynchronous telemetry ingestion benchmark and Python tests.
- `docker-compose.yml` – local orchestration for the backend and PostgreSQL database.
- `.env.example` – template for environment variables.
- `.gitignore` – excludes local environment and dependency files.
- `README.md` – setup, migration, testing, and API usage instructions.

## Architectural decisions

- `SQLAlchemy 2.x` is used for ORM models and database sessions.
- `Pydantic` schemas validate incoming telemetry payloads and shape API responses.
- API routes stay in a dedicated file to keep the app modular and maintainable.
- Vehicle records are created automatically when a telemetry event arrives for a new vehicle ID.
- Each telemetry event remains immutable in the event table while the `vehicles` table stores the latest known state.
- Alembic handles schema migrations so the database can evolve without manual SQL.
- The app config is environment-driven; credentials are kept in `.env` rather than committed to source.

## macOS setup

1. Install Python 3.12 from python.org or using `brew install python@3.12`.
2. Install Docker Desktop for Mac from https://www.docker.com/products/docker-desktop/.
3. Open a terminal in the project root.
4. Create and activate a virtual environment:

   ```bash
   cd /Users/krishan/Desktop/WayPoint
   python3.12 -m venv .venv
   source .venv/bin/activate
   ```
5. Install backend dependencies:

   ```bash
   cd backend
   pip install --upgrade pip
   pip install -r requirements.txt
   ```
6. Copy the example environment file and adjust values if needed:

   ```bash
   cd ..
   cp .env.example .env
   ```
7. Start PostgreSQL and the API through Docker Compose:

   ```bash
   docker compose up --build
   ```
8. Confirm the API is responding:

   ```bash
   curl http://localhost:8000/health
   ```

   Expected response:

   ```json
   {"status": "ok"}
   ```

## Local development commands

Run the API directly with uvicorn:

```bash
cd backend
source ../.venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Create the database schema with Alembic:

```bash
cd backend
source ../.venv/bin/activate
alembic upgrade head
```

Run tests:

```bash
cd backend
source ../.venv/bin/activate
pytest -q
```

## C++ telemetry simulator

The simulator sends one event for each vehicle to `POST /telemetry` every two seconds. It uses CMake `FetchContent` to download nlohmann/json 3.11.3 and cpp-httplib 0.18.1 at configure time; no separate C++ library installation is needed. The first configure requires network access to fetch these dependencies.

Install the build tools:

```bash
# macOS
xcode-select --install
brew install cmake

# Debian/Ubuntu Linux
sudo apt-get update
sudo apt-get install build-essential cmake
```

Configure, build, and run the simulator's unit tests:

```bash
cmake -S simulator -B simulator/build
cmake --build simulator/build --parallel
ctest --test-dir simulator/build --output-on-failure
```

Start the backend with `docker compose up --build` in a separate terminal, then launch the simulator:

```bash
./simulator/build/waypoint-simulator
```

The API URL, vehicle count, and interval (in seconds) can be overridden:

```bash
./simulator/build/waypoint-simulator --url http://localhost:8000 --vehicles 5 --interval 2
```

Vehicle IDs are generated as `WP-1001`, `WP-1002`, and so on. Each event gets a UUID `event_id`; the simulator reuses that ID and the same JSON body for all retries of that event. Press Ctrl+C to stop. Connection failures, retry attempts, and final HTTP errors are logged.

### Reliable ingestion behavior

- Every `POST /telemetry` request must include a unique `event_id`. PostgreSQL enforces this with a unique constraint. The Alembic migration assigns UUIDs to existing rows before making the column non-null and unique, preserving existing telemetry.
- Repeating an event ID with the identical vehicle, measurements, fault code, and timestamp returns HTTP 200 without adding a second history record. Reusing the ID for different event data returns HTTP 409.
- Telemetry history retains valid events regardless of arrival order. The vehicle's current state changes only when an event timestamp is strictly newer than its current `last_updated` value; older events do not replace the latest state.
- The simulator retries connection failures and HTTP 429/5xx responses up to three times after the initial attempt. Delays use full jitter from 0 to an exponentially increasing upper bound (500 ms, 1 s, 2 s; capped at 8 s). HTTP 4xx responses other than 429, including validation errors, are not retried. Connect, read, and write timeouts are each five seconds.
- Retries with stable event IDs reduce duplicate event creation, but do not guarantee exactly-once delivery. A request can be accepted while its response is lost; callers should use the event ID to safely retry.

## Telemetry ingestion benchmarks

The standalone benchmark uses Python 3.12 and `httpx` to asynchronously submit valid telemetry directly to `POST /telemetry`. It does not change the API, retry failed requests, reset the database, or delete data. Warm-up and measured events are stored in PostgreSQL and use unique event IDs and run-scoped `BENCH-...` vehicle IDs.

### Safety and methodology

- The default target is `http://127.0.0.1:8000`. Only plain HTTP URLs whose host is `localhost` or a loopback IP are accepted; public, non-loopback, credential-bearing, and HTTPS targets are rejected.
- Concurrency is bounded by an async semaphore and HTTP connection-pool limits. The default is 8 requests at a time; the tool caps concurrency at 32 and measured events at 1,000 to limit local load.
- Five warm-up events run before the timed phase by default. They use the same run-scoped vehicle IDs as the measured phase, so requests exercise the warmed vehicle-update path; warm-up results are reported separately and excluded from measured duration and throughput.
- Request latency percentiles include all measured HTTP requests, including HTTP failures and transport errors. Successful events per second is successful 2xx responses divided by measured wall-clock duration. HTTP status failures and transport failures are reported separately from successful responses.
- Payload values are seeded for repeatable generation ranges (`--seed`, default `42`); event IDs and run IDs remain randomly unique. Use the same event, vehicle, concurrency, warm-up, and seed settings to compare runs on the same environment.
- This is a local end-to-end measurement, not a capacity guarantee. Results include client, Docker, API, and PostgreSQL effects. Repeated runs append events to the database and can affect later results; record the initial database state and run each scenario under comparable conditions.
- For this project, the local verification environment was Python 3.12.12 on macOS ARM64, Docker Engine 29.9.2, and PostgreSQL 16.15. The Compose file does not set container CPU or memory limits, so Docker Desktop's resource allocation applies. When recording or comparing results, also record host CPU/memory, Docker resource allocation, PostgreSQL version, database size, concurrency, and whether the database was warmed by prior runs. A single local run does not establish production capacity or scalability.

### Install and run

Use the project Python 3.12 environment. Install the benchmark's HTTP client if it is not already available:

```bash
python3.12 -m pip install httpx==0.27.2
```

Start the local backend with Docker Compose, then run each reproducible scenario from the repository root:

```bash
python3.12 benchmarks/run_benchmark.py --base-url http://127.0.0.1:8000 --events 100 --vehicles 20 --concurrency 4 --warmup-events 5 --seed 42
python3.12 benchmarks/run_benchmark.py --base-url http://127.0.0.1:8000 --events 500 --vehicles 50 --concurrency 8 --warmup-events 5 --seed 42
python3.12 benchmarks/run_benchmark.py --base-url http://127.0.0.1:8000 --events 1000 --vehicles 100 --concurrency 8 --warmup-events 5 --seed 42
```

Each command prints a JSON report containing configuration, warm-up results, measured duration, successful throughput, latency percentiles, successful HTTP status counts, HTTP error counts, and transport error counts. Adjust `--events`, `--vehicles`, `--concurrency`, `--warmup-events`, `--seed`, or `--base-url` within the documented safety limits.

Run the benchmark unit tests with:

```bash
python3.12 -m unittest discover -s benchmarks -p 'test_*.py' -v
```

## API testing examples

Send a telemetry event:

```bash
curl -X POST http://localhost:8000/telemetry \
  -H "Content-Type: application/json" \
  -d '{
    "event_id": "0df306c9-cc18-49cb-9f75-51489f8f3d71",
    "vehicle_id": "VF-1001",
    "battery_percentage": 88.5,
    "status": "ready",
    "temperature": 24.7,
    "fault_code": null,
    "timestamp": "2026-10-26T12:00:00Z"
  }'
```

List the latest state of each vehicle:

```bash
curl http://localhost:8000/vehicles
```

List recent telemetry history for one vehicle:

```bash
curl "http://localhost:8000/vehicles/VF-1001/telemetry?limit=20"
```

## Notes

- PostgreSQL is configured through Docker Compose and environment variables from `.env`.
- The project intentionally does not include Kafka, Redis, frontend work, or production deployment.
- If Docker or PostgreSQL is unavailable in the local environment, the Python tests still validate the API logic with SQLite-backed session overrides, but the actual Postgres integration itself remains unverified until Docker is available.
