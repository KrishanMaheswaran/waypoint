"""Read-only development snapshots; never used as a load target or benchmark connection."""

import argparse
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path


def snapshot():
    def run(args, **kwargs):
        return subprocess.check_output(args, text=True, timeout=30, **kwargs)

    services = {}
    for service in (
        "backend",
        "consumer",
        "db",
        "frontend",
        "grafana",
        "prometheus",
        "redpanda",
    ):
        info = json.loads(run(["docker", "inspect", "waypoint-" + service]))[0]
        services[service] = {
            "id": info["Id"],
            "image": info["Image"],
            "started": info["State"]["StartedAt"],
            "restarts": info["RestartCount"],
            "health": info["State"]
            .get("Health", {})
            .get("Status", info["State"]["Status"]),
            "mounts": [
                {
                    "type": m["Type"],
                    "name": m.get("Name"),
                    "destination": m["Destination"],
                }
                for m in info["Mounts"]
            ],
        }
    sql = """BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
    SET LOCAL TIME ZONE 'UTC';
    SELECT json_build_object('vehicles',(SELECT json_object_agg(id,md5(row_to_json(v)::text)) FROM vehicles v),
    'events',(SELECT json_object_agg(id,md5(row_to_json(t)::text)) FROM telemetry_events t),
    'failures',(SELECT count(*) FROM telemetry_ingestion_failures),
    'revision',(SELECT version_num FROM alembic_version)); COMMIT;"""
    output = run(
        [
            "docker",
            "exec",
            "-i",
            "waypoint-db",
            "sh",
            "-c",
            'psql -X -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -v ON_ERROR_STOP=1',
        ],
        input=sql,
    )
    data = json.loads(
        next(line for line in output.splitlines() if line.startswith("{"))
    )
    return {
        **data,
        "captured_at": datetime.now(UTC).isoformat(),
        "services": services,
        "volumes": run(["docker", "volume", "ls", "-q"]).splitlines(),
    }


def compare(before, after):
    for key in ("vehicles", "events", "failures", "revision", "services", "volumes"):
        if before[key] != after[key]:
            raise AssertionError("development baseline changed: " + key)
    return {
        "vehicles": len(after["vehicles"]),
        "events": len(after["events"]),
        "all_record_fingerprints_equal": True,
        "container_ids_starts_restarts_health_equal": True,
        "volumes_equal": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    data = snapshot()
    args.output.write_text(json.dumps(data, indent=2))
    print(
        json.dumps(
            compare(json.loads(args.compare.read_text()), data)
            if args.compare
            else {"vehicles": len(data["vehicles"]), "events": len(data["events"])}
        )
    )


if __name__ == "__main__":
    main()
