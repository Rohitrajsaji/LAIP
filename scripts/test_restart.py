"""Restart only the isolated synthetic test database; production services are untouched."""

import copy
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import psycopg
from laip.canonical import digest, record_id
from laip.jobs import JobRepository, PublicationKey
from laip.migrations import migrate
from laip.persistence import Repository
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
URL = os.environ["LAIP_TEST_DATABASE_URL"]
SCHEMA = "restart_" + uuid.uuid4().hex
bundle = json.loads((ROOT / "docs/contracts/0.1.0/synthetic.example.json").read_text())
run = bundle["runs"][0]
rule = bundle["rules"][0]


def connect():
    connection = psycopg.connect(URL, autocommit=True, connect_timeout=3)
    connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(SCHEMA)))
    return connection


def seed(repo, bundle):
    run = bundle["runs"][0]
    repo.create_system()
    repo.create_import(run["import_id"], bundle["imports"][0])
    repo.create_run(run["run_id"], run["import_id"], run)
    for entity in bundle["entities"]:
        repo.put_entity(entity, run["run_id"])
    for artifact in bundle["artifacts"]:
        repo.put_blob(artifact["sha256"], artifact["byte_length"])
        repo.put_artifact(artifact)
        repo.attach_artifact(run["run_id"], artifact["artifact_id"])
    for mapping in bundle["origin_maps"]:
        repo.put_origin_map(mapping)
    pending = list(bundle["evidence"])
    while pending:
        for evidence in pending[:]:
            if not any(
                eid in {e["evidence_id"] for e in pending}
                for eid in evidence["supporting_evidence_ids"]
                + evidence["contradiction_evidence_ids"]
            ):
                repo.put_evidence(evidence)
                pending.remove(evidence)
    for edge in bundle["dependencies"]:
        repo.put_dependency(edge)


with psycopg.connect(URL, autocommit=True) as connection:
    connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(SCHEMA)))
try:
    with connect() as connection:
        migrate(connection)
        repo = Repository(connection, run["system_namespace"])
        seed(repo, bundle)
        repo.put_rule(rule, None)
        original_review = repo.review(
            "rule_revision",
            rule["revision_id"],
            None,
            "verified",
            "fixture",
            "synthetic review",
        )
        jobs = JobRepository(connection)
        jobs.enqueue("restart_job", run["run_id"], "analysis")
        lease = jobs.claim("before_restart", lease_seconds=1)
        assert lease
        provider = run["providers"][0]
        key = PublicationKey(
            bundle["artifacts"][0]["artifact_id"],
            provider["id"],
            provider["version"],
            provider["source_revision"],
            run["configuration_sha256"],
            "fixture",
        )
        jobs.publish_checkpoint(lease, key, "c" * 64, lambda _: {"synthetic": True})
        connection.execute(
            "UPDATE jobs SET lease_expires_at=clock_timestamp()-interval '1 second' "
            "WHERE job_id='restart_job'"
        )
        before = connection.execute(
            "SELECT (SELECT count(*) FROM entities),(SELECT count(*) FROM "
            "evidence),(SELECT count(*) FROM reviews)"
        ).fetchone()
    subprocess.run(
        ["docker", "compose", "-f", "compose.test.yaml", "restart", "database"],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 30
    while True:
        try:
            with connect() as probe:
                probe.execute("SELECT 1")
            break
        except psycopg.Error:
            if time.monotonic() > deadline:
                raise RuntimeError("Test PostgreSQL restart timed out") from None
            time.sleep(0.2)
    with connect() as connection:
        assert (
            before
            == connection.execute(
                "SELECT (SELECT count(*) FROM entities),(SELECT count(*) FROM "
                "evidence),(SELECT count(*) FROM reviews)"
            ).fetchone()
        )
        jobs = JobRepository(connection)
        assert jobs.recover() == 1
        connection.execute(
            "UPDATE jobs SET available_at=clock_timestamp() WHERE job_id='restart_job'"
        )
        lease = jobs.claim("after_restart")
        assert lease
        assert jobs.publish_checkpoint(
            lease, key, "c" * 64, lambda _: {"should_not_replace": True}
        ) == {"synthetic": True}
        assert (
            connection.execute("SELECT count(*) FROM artifact_results").fetchone()[0]
            == 1
        )
        jobs.finish(lease, "partial")
        repo = Repository(connection, run["system_namespace"])
        for entity in bundle["entities"]:
            repo.put_entity(entity, run["run_id"])
        for evidence in bundle["evidence"]:
            repo.put_evidence(evidence)
        assert (
            before
            == connection.execute(
                "SELECT (SELECT count(*) FROM entities),(SELECT count(*) FROM "
                "evidence),(SELECT count(*) FROM reviews)"
            ).fetchone()
        )
        next_run = {**run, "run_id": "repeat_run"}
        repo.create_run("repeat_run", run["import_id"], next_run)
        for artifact in bundle["artifacts"]:
            repo.attach_artifact("repeat_run", artifact["artifact_id"])
        mapping = {}
        pending = copy.deepcopy(bundle["evidence"])
        while pending:
            progressed = False
            for evidence in pending[:]:
                refs = (
                    evidence["supporting_evidence_ids"]
                    + evidence["contradiction_evidence_ids"]
                )
                if all(eid in mapping for eid in refs):
                    old = evidence["evidence_id"]
                    evidence["run_id"] = "repeat_run"
                    evidence["supporting_evidence_ids"] = [
                        mapping[eid] for eid in evidence["supporting_evidence_ids"]
                    ]
                    evidence["contradiction_evidence_ids"] = [
                        mapping[eid] for eid in evidence["contradiction_evidence_ids"]
                    ]
                    evidence["evidence_id"] = record_id("ev_", evidence, "evidence_id")
                    mapping[old] = evidence["evidence_id"]
                    repo.put_evidence(evidence)
                    pending.remove(evidence)
                    progressed = True
            assert progressed
        for entity in bundle["entities"]:
            observed = copy.deepcopy(entity)
            observed["evidence_ids"] = [
                mapping[eid] for eid in observed["evidence_ids"]
            ]
            repo.put_entity(observed, "repeat_run")
        next_rule = copy.deepcopy(rule)
        next_rule.update(
            run_id="repeat_run",
            previous_revision_id=rule["revision_id"],
            evidence_ids=[mapping[eid] for eid in rule["evidence_ids"]],
        )
        supporting = connection.execute(
            "SELECT evidence_id,content_sha256 FROM evidence WHERE "
            "evidence_id=ANY(%s) ORDER BY evidence_id",
            (next_rule["evidence_ids"],),
        ).fetchall()
        next_rule["support_fingerprint"] = digest(
            {
                "evidence": [
                    {"evidence_id": eid, "content_sha256": sha}
                    for eid, sha in supporting
                ],
                "providers": sorted(
                    run["providers"], key=lambda p: (p["id"], p["version"])
                ),
                "resolution_decisions": [],
                "configuration_sha256": run["configuration_sha256"],
            }
        )
        next_rule["revision_id"] = record_id("rev_", next_rule, "revision_id")
        repo.put_rule(next_rule, rule["revision_id"])
        assert (
            connection.execute("SELECT count(*) FROM entities").fetchone()[0]
            == before[0]
        )
        assert (
            connection.execute("SELECT count(*) FROM rule_revisions").fetchone()[0] == 2
        )
        assert (
            connection.execute(
                "SELECT status FROM reviews WHERE review_id=%s", (original_review,)
            ).fetchone()[0]
            == "verified"
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM reviews WHERE revision_id=%s",
                (next_rule["revision_id"],),
            ).fetchone()[0]
            == 0
        )
    print(
        "Database restart/repeat-analysis passed: history preserved, one checkpoint, "
        "stable entities, new support awaits review."
    )
finally:
    with psycopg.connect(URL, autocommit=True) as connection:
        connection.execute(
            sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(SCHEMA))
        )
