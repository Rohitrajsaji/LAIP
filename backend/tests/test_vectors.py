import os
import uuid

import psycopg
import pytest
from psycopg import sql

from laip.migrations import migrate
from laip.vectors import VectorRepository


@pytest.fixture
def db():
    url = os.environ.get("LAIP_TEST_DATABASE_URL")
    if not url:
        pytest.skip("LAIP_TEST_DATABASE_URL is required for PostgreSQL integration")
    schema = "test_vector_" + uuid.uuid4().hex
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        connection.execute(sql.SQL("SET search_path TO {},public").format(sql.Identifier(schema)))
        migrate(connection)
        connection.execute("INSERT INTO systems VALUES('fixture',clock_timestamp())")
        connection.execute(
            "INSERT INTO imports(import_id,system_namespace,manifest_sha256,payload) "
            "VALUES('imp','fixture',%s,'{}')",
            ("a" * 64,),
        )
        connection.execute(
            "INSERT INTO runs(run_id,system_namespace,import_id,payload,state) "
            "VALUES('run','fixture','imp','{}','queued')"
        )
        connection.execute(
            "INSERT INTO "
            "snapshots(snapshot_id,system_namespace,run_id,manifest_sha256,payload,re"
            "view_sequence) VALUES('snap','fixture','run',%s,'{}',0)",
            ("a" * 64,),
        )
        for index in range(3):
            connection.execute(
                "INSERT INTO "
                "chunks(chunk_id,snapshot_id,system_namespace,policy_version,provider"
                "_version,tokenizer_version,fingerprint,text_content) "
                "VALUES(%s,'snap','fixture','test-policy','fixture','none',%s,'synthe"
                "tic')",
                (str(index), str(index)),
            )
        yield connection
        connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_optional_vectors_fail_closed_and_dimension_model_policy_isolation(db):
    repo = VectorRepository(db, "fixture")
    with pytest.raises(ValueError, match="VECTOR_PROVIDER_UNAVAILABLE"):
        repo.profile("fixture-model")
    with pytest.raises(ValueError, match="APPROVAL"):
        repo.install("fixture-model", "a" * 64, 3, "test-policy")
    repo.install("fixture-model", "a" * 64, 3, "test-policy", approved=True)
    assert repo.profile("fixture-model") == {
        "model_sha256": "a" * 64,
        "dimension": 3,
        "policy_version": "test-policy",
    }
    repo.install("fixture-model", "a" * 64, 3, "test-policy", approved=True)
    repo.put("fixture-model", "0", [1.0, 0.0, 0.0])
    repo.put("fixture-model", "1", [0.0, 1.0, 0.0])
    assert repo.nearest("fixture-model", [1.0, 0.0, 0.0], snapshot_id="snap", limit=1) == ["0"]
    with db.transaction():
        db.execute("SELECT set_config('statement_timeout','7000',true)")
        repo.nearest("fixture-model", [1.0, 0.0, 0.0], snapshot_id="snap", limit=1)
        assert db.execute("SHOW statement_timeout").fetchone()[0] == "7s"
    with pytest.raises(ValueError, match="DIMENSION"):
        repo.put("fixture-model", "2", [1.0])
    with pytest.raises(ValueError):
        repo.put("fixture-model", "2", [float("nan"), 0.0, 1.0])
    with pytest.raises(ValueError, match="MISMATCH"):
        repo.install("fixture-model", "b" * 64, 3, "test-policy", approved=True)
    with pytest.raises(ValueError):
        VectorRepository(db, "other").nearest("fixture-model", [1.0, 0.0, 0.0], snapshot_id="snap")
    with pytest.raises(ValueError):
        repo.nearest("fixture-model", [1.0, 0.0, 0.0], snapshot_id="snap", limit=1001)
    assert (
        db.execute(
            "SELECT count(*) FROM pg_indexes WHERE schemaname=current_schema() AND "
            "indexdef LIKE '%hnsw%'"
        ).fetchone()[0]
        == 1
    )
    assert (
        db.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()[0]
        == "0.8.7"
    )


def test_vector_reads_are_bound_to_selected_snapshot(db):
    repo = VectorRepository(db, "fixture")
    repo.install("fixture-model", "a" * 64, 3, "test-policy", approved=True)
    repo.put("fixture-model", "0", [1.0, 0.0, 0.0])
    db.execute(
        "INSERT INTO "
        "snapshots(snapshot_id,system_namespace,run_id,manifest_sha256,payload,review"
        "_sequence) VALUES('older','fixture','run',%s,'{}',0)",
        ("a" * 64,),
    )
    db.execute(
        "INSERT INTO "
        "chunks(chunk_id,snapshot_id,system_namespace,policy_version,provider_version"
        ",tokenizer_version,fingerprint,text_content) "
        "VALUES('old','older','fixture','test-policy','fixture','none','old','synthet"
        "ic')"
    )
    repo.put("fixture-model", "old", [1.0, 0.0, 0.0])
    assert repo.nearest("fixture-model", [1.0, 0.0, 0.0], snapshot_id="snap") == ["0"]
    assert repo.nearest("fixture-model", [1.0, 0.0, 0.0], snapshot_id="older") == ["old"]
