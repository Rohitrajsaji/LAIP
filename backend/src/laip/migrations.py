"""Ordered atomic migrations, serialized and checksum verified."""

import hashlib
from importlib.resources import files
from pathlib import Path
from typing import Any

import psycopg


def migrate(connection: psycopg.Connection[Any], directory: Path | None = None) -> list[str]:
    root = directory or Path(str(files("laip").joinpath("migrations")))
    scripts = sorted(root.glob("[0-9][0-9][0-9]_*.sql"))
    applied = []
    with connection.transaction():
        connection.execute("SELECT pg_advisory_xact_lock(1280198992)")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations(version text PRIMARY "
            "KEY,checksum text NOT NULL,applied_at timestamptz NOT NULL DEFAULT "
            "clock_timestamp())"
        )
        existing = dict(
            connection.execute("SELECT version,checksum FROM schema_migrations").fetchall()
        )
        available = {script.name for script in scripts}
        if set(existing) - available:
            raise ValueError("UNKNOWN_MIGRATION")
        for script in scripts:
            raw = script.read_bytes()
            checksum = hashlib.sha256(raw).hexdigest()
            if script.name in existing:
                if existing[script.name] != checksum:
                    raise ValueError("MIGRATION_CHECKSUM_MISMATCH")
                continue
            connection.execute(raw.decode("utf-8"))
            connection.execute(
                "INSERT INTO schema_migrations(version,checksum) VALUES(%s,%s)",
                (script.name, checksum),
            )
            applied.append(script.name)
    return applied


def migration_checksums() -> dict[str, str]:
    root = Path(str(files("laip").joinpath("migrations")))
    return {
        script.name: hashlib.sha256(script.read_bytes()).hexdigest()
        for script in sorted(root.glob("[0-9][0-9][0-9]_*.sql"))
    }
