"""Emergency cleanup for the exact project in a retained benchmark JSON manifest."""

import argparse
import json
import os
import re
from pathlib import Path

from benchmarks.performance import ROOT, Stack, assert_owned, command


def cleanup(manifest):
    project = json.loads(manifest.read_text())["project"]
    if not re.fullmatch(r"waypoint-m8b-[0-9a-f]{12}", project):
        raise ValueError("refusing non-benchmark project")
    # Enumerate and inspect all owned containers, including one-off init containers.
    ids = command(
        [
            "docker",
            "ps",
            "-aq",
            "--filter",
            "label=com.docker.compose.project=" + project,
        ]
    ).splitlines()
    for cid in ids:
        info = json.loads(command(["docker", "inspect", cid]))[0]
        service = info["Config"]["Labels"]["com.docker.compose.service"]
        if service not in (
            "db",
            "redpanda",
            "backend",
            "consumer",
            "frontend",
            "kafka-init",
        ):
            raise ValueError("unexpected benchmark service")
        assert_owned(info, project, service)
    stack = Stack()
    stack.project = project
    stack.run = project.removeprefix("waypoint-m8b-")
    stack.env = {**os.environ, "M8B_RUN": stack.run}
    stack.base = [
        "docker",
        "compose",
        "--env-file",
        os.devnull,
        "-p",
        project,
        "-f",
        str(ROOT / "benchmarks/compose.performance.yml"),
    ]
    stack.cleanup()
    # Remove only the tags built by this project; never prune shared image layers.
    for service in ("backend", "consumer", "frontend", "kafka-init"):
        tag = project + "-" + service
        existing = command(["docker", "image", "ls", "-q", tag])
        if existing:
            command(["docker", "image", "rm", tag])
    print("Verified cleanup:", project)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    cleanup(parser.parse_args().manifest)


if __name__ == "__main__":
    main()
