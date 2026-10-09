import os
import uuid

import psycopg
import pytest
from psycopg import sql

from laip.bootstrap import provision
from laip.migrations import migrate


def test_runtime_role_is_not_migration_owner_and_history_is_immutable():
    url = os.environ.get("LAIP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("LAIP_TEST_DATABASE_URL required")
    schema = "test_role_" + uuid.uuid4().hex
    role = "laip_test_" + uuid.uuid4().hex
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
        migrate(conn)
        provision(conn, "fixture-password", role=role)
        try:
            conn.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
            conn.execute("INSERT INTO systems(system_namespace) VALUES('fixture')")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("CREATE TABLE forbidden(x int)")
            with pytest.raises(psycopg.Error):
                conn.execute("UPDATE systems SET system_namespace='rewritten'")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("DELETE FROM systems")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(
                    "INSERT INTO schema_migrations VALUES('fake','fake',clock_timestamp())"
                )
            conn.execute("RESET ROLE")
            assert conn.execute(
                "SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls FROM pg_roles "
                "WHERE rolname=%s",
                (role,),
            ).fetchone() == (False, False, False, False)
        finally:
            conn.execute("RESET ROLE")
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
            conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def test_bootstrap_cli_protects_failure_details(monkeypatch, tmp_path, capsys):
    from unittest.mock import MagicMock

    from laip import bootstrap

    url = tmp_path / "url"
    password = tmp_path / "password"
    url.write_text("postgresql://private-host/private-db")
    password.write_text("fixture-password")
    monkeypatch.setenv("LAIP_MIGRATION_URL_FILE", str(url))
    monkeypatch.setenv("LAIP_RUNTIME_PASSWORD_FILE", str(password))
    connect = MagicMock(side_effect=psycopg.OperationalError("private-host secret details"))
    monkeypatch.setattr(bootstrap.psycopg, "connect", connect)
    with pytest.raises(SystemExit, match="Database migration/provisioning failed") as failure:
        bootstrap.main()
    assert "private-host" not in str(failure.value)
    connect.side_effect = None
    connection = MagicMock()
    connect.return_value.__enter__.return_value = connection
    migrate = MagicMock()
    provision = MagicMock()
    monkeypatch.setattr(bootstrap, "migrate", migrate)
    monkeypatch.setattr(bootstrap, "provision", provision)
    bootstrap.main()
    assert "privileges ready" in capsys.readouterr().out
    migrate.assert_called_once_with(connection)
    provision.assert_called_once_with(connection, "fixture-password")
