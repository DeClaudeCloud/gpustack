"""Verify image upgrades on disposable PostgreSQL databases before publication."""

import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

from migration_history import check_history, read_revisions

PROBE = Path(__file__).with_name("database_probe.py").resolve()
REPAIRS = Path(__file__).with_name("migration_repairs.json")


def docker(*args, capture=False, check=True):
    return subprocess.run(
        ["docker", *args],
        check=check,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        timeout=600,
    )


def probe(image: str, action: str, network: str, database: str = "fresh") -> str:
    url = f"postgresql://postgres:upgrade-test@postgres/{database}"
    return docker(
        "run",
        "--rm",
        "--network",
        network,
        "--mount",
        f"type=bind,src={PROBE},dst=/probe.py,readonly",
        "--env",
        f"GPUSTACK_UPGRADE_DATABASE_URL={url}",
        "--entrypoint",
        "python3",
        image,
        "/probe.py",
        action,
        capture=True,
    ).stdout


def snapshot(image: str, directory: Path, network: str) -> dict:
    contents = json.loads(probe(image, "snapshot", network))
    directory.mkdir()
    for name, content in contents.items():
        if Path(name).name != name or not name.endswith(".py"):
            raise ValueError(f"Invalid migration snapshot path: {name}")
        (directory / name).write_text(content)
    return read_revisions(directory)


def wait_for_postgres(container: str) -> None:
    for _ in range(60):
        if (
            docker(
                "exec",
                container,
                "pg_isready",
                "-U",
                "postgres",
                capture=True,
                check=False,
            ).returncode
            == 0
        ):
            return
        time.sleep(1)
    raise RuntimeError("Disposable PostgreSQL did not become ready")


def compare_schemas(expected: dict, actual: dict) -> None:
    differences = [
        name
        for name in sorted(set(expected) | set(actual))
        if expected.get(name) != actual.get(name)
    ]
    if differences:
        raise ValueError(
            f"Upgrade differs from a fresh install in tables: {differences}"
        )


def verify_images(candidate: str, previous: list, postgres_image: str) -> None:
    network = f"fork-upgrade-{uuid.uuid4().hex[:12]}"
    container = f"{network}-postgres"
    repairs = json.loads(REPAIRS.read_text()) if REPAIRS.exists() else {}
    docker("network", "create", network, capture=True)
    try:
        docker(
            "run",
            "--detach",
            "--name",
            container,
            "--network",
            network,
            "--network-alias",
            "postgres",
            "--env",
            "POSTGRES_PASSWORD=upgrade-test",
            "--env",
            "POSTGRES_DB=fresh",
            postgres_image,
            capture=True,
        )
        wait_for_postgres(container)
        with tempfile.TemporaryDirectory(prefix="fork-migration-history-") as directory:
            root = Path(directory)
            current = snapshot(candidate, root / "candidate", network)
            probe(candidate, "migrate", network)
            expected = json.loads(probe(candidate, "schema", network))
            for index, image in enumerate(previous):
                print(f"Checking upgrade from {image} to {candidate}", flush=True)
                old = snapshot(image, root / f"previous-{index}", network)
                check_history(old, current, repairs)
                database = f"upgrade_{index}"
                docker("exec", container, "createdb", "-U", "postgres", database)
                probe(image, "migrate", network, database)
                probe(image, "seed", network, database)
                probe(candidate, "migrate", network, database)
                probe(candidate, "migrate", network, database)
                probe(candidate, "verify", network, database)
                actual = json.loads(probe(candidate, "schema", network, database))
                compare_schemas(expected, actual)
                print(f"Upgrade passed: {image}", flush=True)
    finally:
        docker("rm", "--force", "--volumes", container, capture=True, check=False)
        docker("network", "rm", network, capture=True, check=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--previous", action="append", required=True)
    parser.add_argument("--postgres-image", default="postgres:16-alpine")
    args = parser.parse_args()
    verify_images(args.candidate, args.previous, args.postgres_image)


if __name__ == "__main__":
    main()
