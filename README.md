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
