"""Namespace-authorized analyst projections and explicit immutable corrections."""

import base64
import copy
from datetime import UTC, datetime
from typing import Any, BinaryIO
from uuid import uuid4

import psycopg
from pydantic import BaseModel, ConfigDict, Field

from laip.artifacts import BlobRef, LocalArtifactStore
from laip.canonical import canonical, digest, record_id
from laip.jobs import JobRepository
from laip.masking import POLICY, mask
from laip.persistence import Repository
from laip.read_service import projection
from laip.retrieval import RetrievalService
from laip.rule_store import correct_rule, current_rule
from laip.source_decode import decode_source


class Mutation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    schema_version: str = Field(pattern="^0\\.1\\.0$")


class ImportRequest(Mutation):
    archive_base64: str = Field(min_length=1, max_length=12 * 1024 * 1024)
    manifest: dict[str, Any]


class RunRequest(Mutation):
    import_id: str = Field(min_length=1, max_length=256)


class ExportRequest(Mutation):
    schema_version: str = Field(pattern="^0\\.[12]\\.0$")
    snapshot_id: str = Field(min_length=1, max_length=256)
    source_included: bool = False


class CancelRequest(Mutation):
    reason: str = Field(min_length=1, max_length=2000)


class CorrectionRequest(Mutation):
    snapshot_id: str = Field(min_length=1, max_length=256)
    expected_revision_id: str = Field(pattern="^rev_[a-f0-9]{64}$")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=4000)
    conditions: list[dict[str, Any]] = Field(max_length=200)
    actions: list[dict[str, Any]] = Field(min_length=1, max_length=200)
    evidence_ids: list[str] = Field(min_length=1, max_length=1000)
    reason: str = Field(min_length=1, max_length=2000)


class AnalystService:
    def __init__(self, connection: psycopg.Connection[Any], settings: Any):
        self.connection = connection
        self.settings = settings
        self.namespace = settings.analyst_namespace
        self.repository = Repository(connection, self.namespace)

    def _rows(self, query: str, params: tuple[Any, ...]) -> list[Any]:
        return self.connection.execute(query, params).fetchall()

    def _snapshot(self, snapshot_id: str) -> RetrievalService:
        return RetrievalService(self.repository, snapshot_id)

    def _summary(self, run_id: str) -> dict[str, Any]:
        rows = self._rows(
            "SELECT payload FROM artifact_results WHERE run_id=%s ORDER BY result_id DESC LIMIT 1",
            (run_id,),
        )
        summary = dict(rows[0][0]) if rows else {}
        snapshots = self._rows(
            "SELECT s.snapshot_id FROM snapshots s LEFT JOIN "
            "(SELECT resource_id,max(sequence) AS sequence FROM audit_events WHERE "
            "action='analyst_snapshot' AND payload->>'system_namespace'=%s "
            "GROUP BY resource_id) a ON a.resource_id=s.snapshot_id "
            "WHERE s.run_id=%s AND s.system_namespace=%s "
            "ORDER BY a.sequence DESC NULLS LAST,s.snapshot_id DESC LIMIT 1",
            (self.namespace, run_id, self.namespace),
        )
        summary["snapshot_id"] = snapshots[0][0] if snapshots else None
        return summary

    def workspace(self) -> dict[str, Any]:
        imports = []
        for import_id, state, count, run_id in self._rows(
            "SELECT i.import_id,i.state,(SELECT count(*) FROM import_entries e WHERE "
            "e.import_id=i.import_id),"
            "(SELECT r.run_id FROM runs r JOIN jobs j ON j.run_id=r.run_id WHERE "
            "r.import_id=i.import_id "
            "AND j.kind='import' ORDER BY j.created_at DESC LIMIT 1) FROM imports i "
            "WHERE i.system_namespace=%s ORDER BY i.import_id DESC LIMIT 100",
            (self.namespace,),
        ):
            imports.append(
                {
                    "import_id": import_id,
                    "state": state,
                    "artifact_count": count,
                    "run_id": run_id,
                    "diagnostics": [],
                    "limitations": [],
                }
            )
        runs = []
        for run_id, import_id, state, kind, job_id in self._rows(
            "SELECT r.run_id,r.import_id,r.state,j.kind,j.job_id FROM runs r JOIN jobs j "
            "ON j.run_id=r.run_id "
            "WHERE r.system_namespace=%s AND j.kind IN ('import','analysis') ORDER BY "
            "j.created_at DESC LIMIT 100",
            (self.namespace,),
        ):
            sequence = self.connection.execute(
                "SELECT coalesce(max(sequence),0),count(*) FROM job_events WHERE job_id=%s",
                (job_id,),
            ).fetchone()
            summary = self._summary(run_id)
            runs.append(
                {
                    "run_id": run_id,
                    "import_id": import_id,
                    "state": state,
                    "kind": kind,
                    "job_id": job_id,
                    "snapshot_id": summary.get("snapshot_id"),
                    "progress": {
                        "sequence": sequence[0] if sequence else 0,
                        "completed": 1 if state in {"succeeded", "partial"} else 0,
                        "total": 1,
                        "stage": state,
                    },
                    "fact_coverage": mask(summary.get("fact_coverage", [])),
                    "diagnostics": mask(summary.get("diagnostics", [])[:200]),
                    "limitations": mask(summary.get("limitations", [])),
                }
            )
        # Correction audit ordering survives refresh; partial snapshots are UI choices,
        # not promoted complete analyses.
        snapshots = self._rows(
            "SELECT s.snapshot_id FROM snapshots s JOIN runs r ON r.run_id=s.run_id LEFT JOIN "
            "(SELECT resource_id,max(sequence) AS sequence FROM audit_events WHERE "
            "action='analyst_snapshot' "
            "AND payload->>'system_namespace'=%s GROUP BY resource_id) a ON "
            "a.resource_id=s.snapshot_id "
            "WHERE s.system_namespace=%s ORDER BY a.sequence DESC NULLS "
            "LAST,r.completed_at DESC NULLS LAST,s.snapshot_id DESC LIMIT 1",
            (self.namespace, self.namespace),
        )
        exports = [
            self.export(export_id)
            for (export_id,) in self._rows(
                "SELECT e.export_id FROM exports e JOIN runs r ON r.run_id=e.run_id WHERE "
                "r.system_namespace=%s "
                "ORDER BY e.export_id DESC LIMIT 100",
                (self.namespace,),
            )
        ]
        return {
            "namespace": self.namespace,
            "snapshot_id": snapshots[0][0] if snapshots else None,
            "imports": imports,
            "runs": runs,
            "exports": exports,
            "ai_enabled": False,
            "supported_schema_versions": ["0.1.0", "0.2.0"],
            "limitations": [
                "Offline supplied-artifact analysis; no live IBM i or runtime equivalence "
                "established."
            ],
        }

    def inventory(self, snapshot_id: str, schema_version: str = "0.1.0") -> dict[str, Any]:
        service = self._snapshot(snapshot_id)
        service.require_version(schema_version)
        result = service.collect()
        result["records"] = [r for r in result["records"] if r["type"] == "entity"]
        return projection(result)

    def dependencies(self, snapshot_id: str, schema_version: str = "0.1.0") -> dict[str, Any]:
        service = self._snapshot(snapshot_id)
        service.require_version(schema_version)
        result = service.collect()
        result["records"] = [r for r in result["records"] if r["type"] == "dependency"]
        return projection(result)

    def rules(self, snapshot_id: str, schema_version: str = "0.1.0") -> dict[str, Any]:
        service = self._snapshot(snapshot_id)
        service.require_version(schema_version)
        selected = service.collect()
        rules = []
        for record in selected["records"]:
            if record["type"] != "rule":
                continue
            rule = record["payload"]
            # Only ancestors of the selected revision are inspectable; later heads do
            # not leak into old snapshots.
            revisions = []
            pending = rule
            for _ in range(100):
                revisions.append(mask(pending))
                previous = pending["previous_revision_id"]
                if previous is None:
                    break
                rows = self._rows(
                    "SELECT payload FROM rule_revisions WHERE revision_id=%s AND "
                    "system_namespace=%s AND rule_entity_id=%s",
                    (previous, self.namespace, rule["rule_entity_id"]),
                )
                if not rows:
                    raise ValueError("INVALID_REVISION_LINEAGE")
                pending = rows[0][0]
            else:
                raise ValueError("REVISION_LIMIT")
            reviews = [
                dict(
                    zip(
                        ("review_id", "revision_id", "status", "actor_id", "reason"),
                        row,
                        strict=True,
                    )
                )
                for row in self._rows(
                    "SELECT review_id,revision_id,status,actor_id,reason FROM reviews "
                    "WHERE system_namespace=%s AND revision_id=ANY(%s) AND sequence<=%s ORDER BY "
                    "sequence",
                    (
                        self.namespace,
                        [rev["revision_id"] for rev in revisions],
                        service.review_sequence,
                    ),
                )
            ]
            rules.append(
                {
                    "revision": mask(rule),
                    "review_status": record["review_status"],
                    "review_id": record["review_id"],
                    "revisions": revisions,
                    "reviews": mask(reviews),
                }
            )
        return {
            "rules": rules,
            "snapshot_id": snapshot_id,
            "limitations": ["Candidates retain source certainty and require analyst review."],
        }

    def source(
        self,
        artifact_id: str,
        snapshot_id: str,
        start_line: int,
        end_line: int,
        *,
        schema_version: str = "0.1.0",
    ) -> dict[str, Any]:
        if (
            type(start_line) is not int
            or type(end_line) is not int
            or start_line < 1
            or end_line < start_line
            or end_line - start_line + 1 > 200
        ):
            raise ValueError("INVALID_SOURCE_WINDOW")
        selected = self._snapshot(snapshot_id)
        selected.require_version(schema_version)
        rows = self._rows(
            "SELECT a.payload FROM artifacts a JOIN run_artifacts r ON "
            "r.artifact_id=a.artifact_id WHERE r.run_id=%s AND r.system_namespace=%s AND "
            "a.system_namespace=%s AND a.artifact_id=%s",
            (selected.run_id, self.namespace, self.namespace, artifact_id),
        )
        if not rows:
            raise ValueError("ARTIFACT_NOT_IN_SNAPSHOT")
        artifact = rows[0][0]
        if artifact["byte_length"] > 16 * 1024 * 1024 or not artifact["encoding"]:
            raise ValueError("SOURCE_UNAVAILABLE_OR_LIMIT")
        store = LocalArtifactStore(self.settings.artifact_root)
        ref = BlobRef(
            artifact["sha256"],
            artifact["byte_length"],
            f"objects/{artifact['sha256'][:2]}/{artifact['sha256']}",
        )
        with store.open(ref) as stream:
            raw = stream.read(16 * 1024 * 1024 + 1)
        decoded = decode_source(raw, artifact["encoding"], artifact_id)
        if decoded.decoded is None or decoded.origin_map is None:
            raise ValueError("SOURCE_UNAVAILABLE")
        text = "".join(
            decoded.decoded.decode("utf-8").splitlines(keepends=True)[start_line - 1 : end_line]
        )
        if len(text.encode()) > 65536:
            raise ValueError("SOURCE_WINDOW_LIMIT")
        return {
            "artifact_id": artifact_id,
            "content_sha256": artifact["sha256"],
            "decoded_sha256": decoded.origin_map["decoded_sha256"],
            "origin_map_id": decoded.origin_map["origin_map_id"],
            "range": {"start_line": start_line, "end_line": end_line},
            "text": mask(text),
            "masking_policy_version": POLICY,
            "limitations": ["Supplied source only; window text is untrusted data."],
        }

    def correct(self, rule_entity_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        request = CorrectionRequest.model_validate(arguments)
        with self.connection.transaction():
            self.connection.execute(
                "SELECT system_namespace FROM systems WHERE system_namespace=%s FOR UPDATE",
                (self.namespace,),
            )
            selected = self._snapshot(request.snapshot_id).collect()
            current = next(
                (
                    r["payload"]
                    for r in selected["records"]
                    if r["type"] == "rule" and r["payload"]["rule_entity_id"] == rule_entity_id
                ),
                None,
            )
            if current is None:
                raise ValueError("RULE_NOT_IN_SNAPSHOT")
            if current["revision_id"] != request.expected_revision_id:
                raise ValueError("STALE_REVISION")
            head = current_rule(rule_entity_id, self.repository)
            if head["revision"]["revision_id"] != request.expected_revision_id:
                raise ValueError("STALE_REVISION")
            evidence = {
                r["id"]: r["payload"] for r in selected["records"] if r["type"] == "evidence"
            }
            if not set(request.evidence_ids) <= evidence.keys():
                raise ValueError("CITATION_NOT_IN_SNAPSHOT")
            revision = copy.deepcopy(current)
            revision.update(
                name=request.name,
                description=request.description,
                conditions=request.conditions,
                actions=request.actions,
                evidence_ids=sorted(set(request.evidence_ids)),
                previous_revision_id=current["revision_id"],
                interpretation_method="analyst",
                classification="inferred",
                created_at=datetime.now(UTC).isoformat(),
            )
            if revision["schema_version"] == "0.2.0":
                support = revision["claim_support"]
                support["evidence_ids"] = revision["evidence_ids"]
                support["controlling_predicates"] = copy.deepcopy(revision["conditions"])
                # Corrections cannot remove existing barriers or invent resolved effects.
                revision["support_fingerprint"] = self.repository.rule_support_fingerprint(revision)
            else:
                run = self.repository._run(current["run_id"])
                revision["support_fingerprint"] = digest(
                    {
                        "evidence": sorted(
                            [
                                {
                                    "evidence_id": eid,
                                    "content_sha256": evidence[eid]["content_sha256"],
                                }
                                for eid in revision["evidence_ids"]
                            ],
                            key=lambda e: e["evidence_id"],
                        ),
                        "providers": sorted(
                            run.get("providers", []), key=lambda p: (p["id"], p["version"])
                        ),
                        "resolution_decisions": [],
                        "configuration_sha256": run["configuration_sha256"],
                    }
                )
            revision["revision_id"] = record_id("rev_", revision, "revision_id")
            review_id = correct_rule(
                revision,
                current["revision_id"],
                head["review_id"],
                "local-analyst",
                request.reason,
                self.repository,
            )
            row = self.connection.execute(
                "SELECT payload FROM snapshots WHERE snapshot_id=%s AND system_namespace=%s",
                (request.snapshot_id, self.namespace),
            ).fetchone()
            assert row
            snapshot_schema_version = row[0].get("schema_version", "0.1.0")
            manifest = {
                key: copy.deepcopy(row[0][key])
                for key in ("observations", "evidence", "dependencies", "revisions")
            }
            manifest["revisions"] = [
                revision["revision_id"] if ident == current["revision_id"] else ident
                for ident in manifest["revisions"]
            ]
            snapshot_id = "snap_" + uuid4().hex
            self.repository.publish_snapshot(
                snapshot_id, current["run_id"], manifest, schema_version=snapshot_schema_version
            )
            from laip.snapshot_index import index_snapshot

            index_snapshot(self.repository, snapshot_id)
            self.connection.execute(
                "INSERT INTO audit_events(actor_id,action,resource_id,payload) "
                "VALUES('local-analyst','analyst_snapshot',%s,%s)",
                (
                    snapshot_id,
                    psycopg.types.json.Jsonb(
                        {"system_namespace": self.namespace, "run_id": current["run_id"]}
                    ),
                ),
            )
        return {"snapshot_id": snapshot_id, "revision": mask(revision), "review_id": review_id}

    def accept_import(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from laip.analyst_jobs import AnalystJobs

        request = ImportRequest.model_validate(arguments)
        if len(canonical(request.manifest)) > 1024 * 1024:
            raise ValueError("MANIFEST_LIMIT")
        try:
            archive = base64.b64decode(request.archive_base64, validate=True)
        except ValueError:
            raise ValueError("INVALID_ARCHIVE") from None
        if len(archive) > 8 * 1024 * 1024:
            raise ValueError("ARCHIVE_LIMIT")
        return AnalystJobs(self.connection, self.settings).accept_import(archive, request.manifest)

    def queue_analysis(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from laip.analyst_jobs import AnalystJobs

        request = RunRequest.model_validate(arguments)
        rows = self._rows(
            "SELECT r.run_id FROM runs r JOIN jobs j ON j.run_id=r.run_id WHERE "
            "r.import_id=%s AND r.system_namespace=%s AND j.kind='import' AND r.state IN "
            "('succeeded','partial') ORDER BY j.created_at DESC LIMIT 1",
            (request.import_id, self.namespace),
        )
        if not rows:
            raise ValueError("IMPORT_NOT_READY")
        return AnalystJobs(self.connection, self.settings).queue_analysis(rows[0][0])

    def cancel(self, run_id: str, arguments: dict[str, Any]) -> dict[str, Any]:
        request = CancelRequest.model_validate(arguments)
        rows = self._rows(
            "SELECT j.job_id FROM jobs j JOIN runs r ON r.run_id=j.run_id WHERE "
            "r.run_id=%s AND r.system_namespace=%s AND j.kind IN ('import','analysis')",
            (run_id, self.namespace),
        )
        if not rows:
            raise ValueError("RUN_NOT_FOUND")
        state = JobRepository(self.connection).cancel(rows[0][0], request.reason)
        return {"run_id": run_id, "state": state}

    def queue_export(self, arguments: dict[str, Any]) -> dict[str, Any]:
        from laip.analyst_jobs import AnalystJobs

        request = ExportRequest.model_validate(arguments)
        if request.source_included:
            raise ValueError("SOURCE_ACCESS_DENIED")
        self._snapshot(request.snapshot_id).require_version(request.schema_version)
        return AnalystJobs(self.connection, self.settings).queue_export(
            request.snapshot_id, source_included=False, schema_version=request.schema_version
        )

    def export(self, export_id: str) -> dict[str, Any]:
        rows = self._rows(
            "SELECT e.snapshot_id,e.state,x.payload FROM exports e JOIN runs r ON "
            "r.run_id=e.run_id LEFT JOIN export_results x ON x.export_id=e.export_id WHERE "
            "e.export_id=%s AND r.system_namespace=%s",
            (export_id, self.namespace),
        )
        if not rows:
            raise ValueError("EXPORT_NOT_FOUND")
        snapshot, state, result = rows[0]
        result = result or {}
        return {
            "export_id": export_id,
            "snapshot_id": snapshot,
            "state": state,
            "manifest": result.get("manifest"),
            "download_url": f"/api/v1/exports/{export_id}/download"
            if state in {"succeeded", "partial"} and result
            else None,
            "limitations": result.get("manifest", {}).get("limitations", []),
        }

    def download(self, export_id: str) -> tuple[BinaryIO, int]:
        view = self.export(export_id)
        if view["download_url"] is None:
            raise ValueError("EXPORT_NOT_READY")
        row = self.connection.execute(
            "SELECT payload FROM export_results WHERE export_id=%s", (export_id,)
        ).fetchone()
        assert row
        blob = row[0]["blob"]
        ref = BlobRef(**blob)
        if ref.byte_length > 32 * 1024 * 1024:
            raise ValueError("EXPORT_DOWNLOAD_LIMIT")
        return LocalArtifactStore(self.settings.artifact_root).open(ref), ref.byte_length
