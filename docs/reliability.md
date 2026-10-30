# Telemetry reliability experiments

Waypoint includes an isolated Docker Compose harness for exercising the Kafka-to-PostgreSQL path under controlled failures. These are local development tests, not production load or availability tests.

## Safety and prerequisites

Run from the repository root with Python 3.12, Docker Engine/Desktop, and the Docker Compose plugin available:

```bash
python3.12 backend/tests/reliability.py --scenario quick
python3.12 backend/tests/reliability.py --scenario all
```

The `quick` scenario is the backend CI subset. It tests interruption/replay, duplicate delivery, conflicting event IDs, and out-of-order telemetry. `all` adds the PostgreSQL login outage, consumer termination during an in-flight insert, and broker outage experiments.

The harness creates a uniquely named Compose project per run. It uses separate PostgreSQL and Redpanda containers with `tmpfs` data directories, separate networks for database and broker traffic, and dynamically selected loopback-only API and metrics ports. It does not connect to the normal Waypoint Compose project or its PostgreSQL database. The harness cleans up only its own containers and networks with `docker compose down --remove-orphans`; it does not use `-v` or delete named volumes. Test records disappear with the disposable containers.

Every HTTP wait and Docker subprocess has a timeout. Event IDs are fixed and deterministic within the fresh database for a run. A failure prints the isolated Compose state and recent service logs before cleanup.

## Failure matrix

| Experiment | Injected condition | Assertions |
|---|---|---|
| Consumer interruption and replay | Stop the consumer, publish three records, then start it again. | Kafka backlog is observed; all three records persist once. A duplicate event creates one history row; conflicting content creates an audit row; older telemetry does not roll back current vehicle state. |
| PostgreSQL login outage | Temporarily disable login for the disposable application role and terminate its existing sessions while Redpanda remains available and PostgreSQL stays healthy. | The consumer records a database retry and lag; the broker offset does not advance while the event is not committed to PostgreSQL. Restoring login allows one history row to persist and the offset to advance. |
| In-flight consumer termination | A test-only PostgreSQL trigger delays a designated insert; SIGKILL the consumer while that insert is active, then start the consumer. | The accepted record is replayed and exactly one history row exists. This demonstrates at-least-once recovery with database idempotency, not exactly-once processing. |
| Broker outage | Stop Redpanda and POST one event. | The API returns `503` within the 20-second test bound, the publish-failure counter increments, and the unacknowledged event has no history row. |

The PostgreSQL experiment changes only the disposable test role; it does not stop or restart PostgreSQL. This avoids losing its `tmpfs` database while simulating the application's inability to connect. The in-flight insert trigger is installed only in the same disposable database and disappears when the harness removes that test project's container.

## One observed local run

The following values came from one successful `--scenario all` run on the local macOS/Docker environment on 2026-10-29. They are observations of that run, not repeatable performance targets or production guarantees.

| Measurement | Observed value |
|---|---:|
| Consumer interruption backlog | 3 records |
| Time from consumer restart to replayed history | 1.994 s |
| History rows after replay | 3 |
| Duplicate event history rows | 1 |
| Conflicting event audit rows | 1 |
| Out-of-order history rows / final battery state | 2 / 90% (older 35% event did not replace it) |
| PostgreSQL login outage backlog / maximum observed lag | 1 / 1 |
| PostgreSQL retry counter maximum / retry log entries | 1 / 2 |
| PostgreSQL recovery and offset-advance time | 1.638 s |
| Database-outage event rows after recovery | 1 |
| In-flight consumer-kill recovery time / history rows | 13.716 s / 1 |
| Broker-outage response | HTTP 503 in 16.076 s (20 s bound) |
| Publish-failure counter increase / broker-outage history rows | 1 / 0 |
| Core scenario API publish successes | 9 |
| Core scenario consumer outcomes | 7 persisted, 1 duplicate, 1 conflict rejection |

The simulator/backend container images used by this run were PostgreSQL `postgres:16-alpine` and Redpanda `redpandadata/redpanda:v24.3.8`. The measurements are single-run wall-clock observations; host resource allocation, Docker configuration, and raw per-event timing distributions were not recorded.

## Reliability metrics

The consumer exports low-cardinality metrics on its `/metrics` endpoint:

- `waypoint_kafka_consumer_lag` is labeled by topic and partition, not vehicle or event.
- `waypoint_kafka_consumer_retries_total` and `waypoint_kafka_consumer_failures_total` use bounded reason labels.
- `waypoint_kafka_consumer_recovered_events_total` and `waypoint_kafka_consumer_recovery_duration_seconds` describe successful handling after transient failures.
- `waypoint_kafka_consumer_processing_duration_seconds` records processing/commit duration by outcome.

The provisioned Grafana dashboard includes panels for consumer lag, retry/failure rates, and recovery. Counters and histograms are in-memory process metrics and reset when the consumer process restarts; they are not durable audit records. PostgreSQL's telemetry history and ingestion-failure table remain the durable record.

## Limitations

- The local Redpanda service is single-node and has no high availability. This harness does not test multi-broker quorum behavior or production Kafka.
- Delivery is at least once: a consumer can persist a row and fail before committing its offset, causing replay. Event ID uniqueness and persistence logic prevent duplicate history rows; this is not exactly-once processing.
- The PostgreSQL test simulates application connection rejection while the disposable PostgreSQL service remains healthy. It does not model every database failure mode, including storage exhaustion, corruption, failover, or long network partitions.
- Broker response duration and recovery times depend on the local Docker host and are not latency guarantees.
- Metrics are low-cardinality, process-local telemetry. A consumer restart resets counter history, and no alerting or external metrics retention is configured by this experiment.
