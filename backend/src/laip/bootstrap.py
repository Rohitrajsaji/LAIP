"""One-shot migration owner; runtime credentials never receive DDL privileges."""

import os
import re
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql

from laip.migrations import migrate


def provision(
    connection: psycopg.Connection[Any], password: str, *, role: str = "laip_runtime"
) -> None:
    if not re.fullmatch(r"laip_[a-z0-9_]{1,54}", role) or len(password) < 16:
        raise ValueError("INVALID_RUNTIME_CREDENTIALS")
    schema = str(connection.execute("SELECT current_schema()").fetchone()[0])  # type: ignore[index]
    with connection.transaction():
        connection.execute("SELECT pg_advisory_xact_lock(1280198992)")
        if not connection.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
            connection.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(password)
                )
            )
        connection.execute(
            sql.SQL(
                "ALTER ROLE {} WITH NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
                "NOBYPASSRLS PASSWORD {}"
            ).format(sql.Identifier(role), sql.Literal(password))
        )
        connection.execute(
            sql.SQL("REVOKE CREATE ON SCHEMA {} FROM PUBLIC").format(sql.Identifier(schema))
        )
        connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                sql.Identifier(schema), sql.Identifier(role)
            )
        )
        tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname=%s", (schema,)
            ).fetchall()
        ]
        immutable_locks = {
            "systems": "system_namespace",
            "entities": "entity_id",
            "evidence": "evidence_id",
            "dependencies": "dependency_id",
            "rule_revisions": "revision_id",
            "blobs": "sha256",
        }
        for table in tables:
            qualified = sql.Identifier(schema, table)
            connection.execute(
                sql.SQL("GRANT SELECT ON {} TO {}").format(qualified, sql.Identifier(role))
            )
            if table not in ("schema_migrations", "schema_extensions"):
                connection.execute(
                    sql.SQL("GRANT INSERT ON {} TO {}").format(qualified, sql.Identifier(role))
                )
            if table in immutable_locks:
                connection.execute(
                    sql.SQL("GRANT UPDATE ({}) ON {} TO {}").format(
                        sql.Identifier(immutable_locks[table]), qualified, sql.Identifier(role)
                    )
                )
        mutable = {
            "runs": ["state", "completed_at"],
            "imports": ["state"],
            "run_artifacts": ["status"],
            "rule_heads": ["revision_id"],
            "review_heads": ["review_id"],
            "namespace_heads": ["snapshot_id"],
            "exports": ["state", "completed_at"],
            "retention_operations": ["state", "completed_at"],
            "job_attempts": ["outcome", "finished_at"],
            "jobs": [
                "state",
                "attempt",
                "fence",
                "worker_id",
                "lease_token",
                "heartbeat_at",
                "lease_expires_at",
                "cancel_requested",
                "cancel_reason",
                "available_at",
                "completed_at",
            ],
        }
        for table, columns in mutable.items():
            connection.execute(
                sql.SQL("GRANT UPDATE ({}) ON {} TO {}").format(
                    sql.SQL(",").join(map(sql.Identifier, columns)),
                    sql.Identifier(schema, table),
                    sql.Identifier(role),
                )
            )
        connection.execute(
            sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {} TO {}").format(
                sql.Identifier(schema), sql.Identifier(role)
            )
        )
        connection.execute(
            sql.SQL("REVOKE CREATE ON SCHEMA {} FROM {}").format(
                sql.Identifier(schema), sql.Identifier(role)
            )
        )


def main() -> None:
    url = Path(os.environ["LAIP_MIGRATION_URL_FILE"]).read_text().strip()
    password = Path(os.environ["LAIP_RUNTIME_PASSWORD_FILE"]).read_text().strip()
    try:
        with psycopg.connect(url, autocommit=True, connect_timeout=5) as connection:
            connection.execute("SET statement_timeout='30s'")
            migrate(connection)
            provision(connection, password)
    except (psycopg.Error, ValueError):
        raise SystemExit("Database migration/provisioning failed") from None
    print("Database migrations and runtime privileges ready.")


if __name__ == "__main__":
    main()
