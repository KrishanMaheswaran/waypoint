# Waypoint

Waypoint is a portfolio-quality real-time fleet telemetry platform for backend and infrastructure engineering internships. Milestone 1 established the project foundation; Milestone 2 adds the telemetry API for vehicle state and recent historical events.

## Project structure

- `backend/app/main.py` – FastAPI application entrypoint and router registration.
- `backend/app/config.py` – environment-based application settings for PostgreSQL.
- `backend/app/database.py` – SQLAlchemy engine, session factory, and database session dependency.
- `backend/app/models.py` – SQLAlchemy vehicle and telemetry models.
- `backend/app/schemas.py` – Pydantic validation models for telemetry inputs and outputs.
- `backend/app/routes.py` – FastAPI endpoints for telemetry ingestion and vehicle queries.
- `backend/alembic/` – Alembic migration configuration and version files.
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

Vehicle IDs are generated as `WP-1001`, `WP-1002`, and so on. Press Ctrl+C to stop. Connection failures and non-2xx API responses are logged, and the simulator continues sending subsequent events.

## API testing examples

Send a telemetry event:

```bash
curl -X POST http://localhost:8000/telemetry \
  -H "Content-Type: application/json" \
  -d '{
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
- The project is intentionally kept minimal for Milestone 3A and does not include Kafka, Redis, frontend work, or production deployment.
- If Docker or PostgreSQL is unavailable in the local environment, the Python tests still validate the API logic with SQLite-backed session overrides, but the actual Postgres integration itself remains unverified until Docker is available.
