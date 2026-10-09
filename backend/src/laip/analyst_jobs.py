"""Durable, inert offline analyst workflow orchestration. No imported code executes."""

import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from io import BytesIO
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb

from laip.analyzer_publication import Publication, build_publication
from laip.artifacts import LocalArtifactStore
from laip.canonical import canonical, digest, record_id
from laip.cl_analysis import CL_PROVIDER, analyze_cl
from laip.config import Settings
from laip.dds_analysis import DDS_PROVIDER, analyze_dds
from laip.exports import build_export, publish_export
from laip.import_readers import ImportLimits
from laip.inventory import INVENTORY_PROVIDER, InventoryLimits, build_inventory
from laip.jobs import JobRepository, Lease, LeaseLost, PublicationKey
from laip.masking import POLICY
from laip.persistence import Repository
from laip.rpgle_analysis import RPGLE_PROVIDER, PreparedAnalysis, analyze_rpgle
from laip.rule_store import persist_bundle, register_bundle, workflow_support_record
from laip.rules import extract_rules, extract_rules_v2
from laip.schema_registry import require_compatible_version
from laip.snapshot_index import index_snapshot
from laip.source_import import (
    IMPORT_PROVIDER,
    ImportContext,
    PreparedImport,
    persist_import,
    prepare_zip,
    register_import,
    restore_import,
)
from laip.workflows import compose_local_workflows, compose_workflows

PROVIDER: dict[str, Any] = {
    "id": "laip-analyst-pipeline",
    "version": "0.1.0",
    "source_revision": None,
}
PROVIDERS = [
    PROVIDER,
    IMPORT_PROVIDER,
    INVENTORY_PROVIDER,
    RPGLE_PROVIDER,
    CL_PROVIDER,
    DDS_PROVIDER,
]
MAX_ARCHIVE = 24 * 1024 * 1024


class AnalystJobs:
    def __init__(self, connection: psycopg.Connection[Any], settings: Settings):
        self.connection = connection
        self.settings = settings
        self.repository = Repository(connection, settings.analyst_namespace)
        self.jobs = JobRepository(connection)
        self.store = LocalArtifactStore(settings.artifact_root)

    def _plan(self, job_id: str, payload: dict[str, Any]) -> None:
        self.connection.execute(
            "INSERT INTO analyst_job_plans VALUES(%s,%s,%s)",
            (job_id, self.repository.namespace, Jsonb(payload)),
        )

    def accept_import(self, zip_bytes: bytes, manifest: dict[str, Any]) -> dict[str, str]:
        if not isinstance(zip_bytes, bytes) or not zip_bytes or len(zip_bytes) > MAX_ARCHIVE:
            raise ValueError("IMPORT_ARCHIVE_LIMIT")
        manifest_raw = canonical(manifest)
        if len(manifest_raw) > 1024 * 1024:
            raise ValueError("MANIFEST_LIMIT")
        import_id, run_id, job_id = (
            "imp_" + uuid4().hex,
            "run_" + uuid4().hex,
            "job_" + uuid4().hex,
        )
        now = datetime.now(UTC).isoformat()
        prepared = prepare_zip(
            BytesIO(zip_bytes),
            manifest_raw,
            system_namespace=self.repository.namespace,
            store=self.store,
            context=ImportContext(import_id, run_id, now, POLICY, job_id, 1),
            limits=ImportLimits(
                max_archive_bytes=MAX_ARCHIVE, max_files=1000, max_bytes=32 * 1024 * 1024
            ),
        )
        with self.connection.transaction():
            register_import(prepared, self.repository)
            self.jobs.enqueue(job_id, run_id, "import")
            self._plan(job_id, {"kind": "import", "import_run_id": run_id, "created_at": now})
        return {"job_id": job_id, "import_id": import_id, "run_id": run_id}

    def queue_analysis(self, import_run_id: str) -> dict[str, str]:
        row = self.connection.execute(
            "SELECT r.import_id,r.state FROM runs r JOIN import_plans p USING(run_id) "
            "WHERE r.run_id=%s AND r.system_namespace=%s",
            (import_run_id, self.repository.namespace),
        ).fetchone()
        if row is None or row[1] not in ("succeeded", "partial"):
            raise ValueError("IMPORT_NOT_READY")
        run_id, job_id = "run_" + uuid4().hex, "job_" + uuid4().hex
        now = datetime.now(UTC).isoformat()
        basis = {
            "stage": "offline_inventory",
            "pipeline": "analyst-0.1.0",
            "providers": PROVIDERS,
            "configuration_sha256": digest(
                {"import_run_id": import_run_id, "providers": PROVIDERS}
            ),
            "masking_policy_version": POLICY,
            "import_run_id": import_run_id,
        }
        with self.connection.transaction():
            self.repository.create_run(run_id, row[0], basis)
            # The sealed plan carries the actual manifest artifact identifier.
            prepared = restore_import(import_run_id, self.repository, self.store)
            self.repository.attach_artifact(run_id, prepared.manifest_artifact["artifact_id"])
            self.jobs.enqueue(job_id, run_id, "analysis")
            self._plan(
                job_id, {"kind": "analysis", "import_run_id": import_run_id, "created_at": now}
            )
        return {"job_id": job_id, "run_id": run_id, "import_id": row[0]}

    def queue_export(
        self, snapshot_id: str, *, source_included: bool = False, schema_version: str = "0.1.0"
    ) -> dict[str, str]:
        if source_included is not False:
            raise ValueError("SOURCE_ACCESS_DENIED")
        row = self.connection.execute(
            "SELECT run_id,payload FROM snapshots WHERE snapshot_id=%s AND system_namespace=%s",
            (snapshot_id, self.repository.namespace),
        ).fetchone()
        if row is None:
            raise ValueError("SNAPSHOT_NOT_FOUND")
        require_compatible_version(
            schema_version,
            set(row[1].get("record_versions", ["0.1.0"])) | {row[1].get("schema_version", "0.1.0")},
        )
        export_id, job_id = "exp_" + uuid4().hex, "job_" + uuid4().hex
        now = datetime.now(UTC).isoformat()
        with self.connection.transaction():
            self.connection.execute(
                "INSERT INTO exports(export_id,run_id,snapshot_id,options_sha256,"
                "masking_policy_version,schema_version) "
                "VALUES(%s,%s,%s,%s,%s,%s)",
                (
                    export_id,
                    row[0],
                    snapshot_id,
                    digest({"source_included": False, "schema_version": schema_version}),
                    POLICY,
                    schema_version,
                ),
            )
            self.jobs.enqueue(job_id, row[0], "export", export_id)
            self._plan(
                job_id,
                {
                    "kind": "export",
                    "snapshot_id": snapshot_id,
                    "schema_version": schema_version,
                    "export_id": export_id,
                    "created_at": now,
                },
            )
        return {"job_id": job_id, "export_id": export_id, "snapshot_id": snapshot_id}

    def execute_one(self) -> bool:
        self.jobs.recover()
        ids = [
            row[0]
            for row in self.connection.execute(
                "SELECT p.job_id FROM analyst_job_plans p JOIN jobs j USING(job_id) "
                "WHERE p.system_namespace=%s AND j.state IN ('queued','retry_wait') "
                "ORDER BY j.created_at LIMIT 100",
                (self.repository.namespace,),
            ).fetchall()
        ]
        if not ids:
            return False
        lease = self.jobs.claim("analyst-" + uuid4().hex, lease_seconds=300, authorized_job_ids=ids)
        if lease is None:
            return False
        plan = self.connection.execute(
            "SELECT payload FROM analyst_job_plans WHERE job_id=%s AND system_namespace=%s",
            (lease.job_id, self.repository.namespace),
        ).fetchone()[0]  # type: ignore[index]
        last_heartbeat = time.monotonic()
        deadline = last_heartbeat + 240

        def cancelled() -> bool:
            nonlocal last_heartbeat
            if time.monotonic() >= deadline:
                raise ValueError("PIPELINE_TIMEOUT")
            row = self.connection.execute(
                "SELECT cancel_requested FROM jobs WHERE job_id=%s", (lease.job_id,)
            ).fetchone()
            if row is None or row[0]:
                return True
            if time.monotonic() - last_heartbeat > 10:
                if not self.jobs.heartbeat(lease, lease_seconds=300):
                    raise LeaseLost("lease lost")
                last_heartbeat = time.monotonic()
            return False

        try:
            if lease.kind == "import":
                prepared = restore_import(plan["import_run_id"], self.repository, self.store)
                key = PublicationKey(
                    prepared.manifest_artifact["artifact_id"],
                    "laip-offline-import",
                    "0.1.0",
                    None,
                    prepared.configuration_sha256,
                    "import",
                )
                with self.connection.transaction():
                    result = self.jobs.publish_checkpoint(
                        lease,
                        key,
                        digest(prepared.configuration_sha256),
                        lambda _: persist_import(prepared, self.repository),
                    )
                    self.jobs.finish(lease, result["outcome"])
            elif lease.kind == "analysis":
                self._analyze(lease, plan, cancelled)
            elif lease.kind == "export":
                bundle = build_export(
                    self.repository,
                    plan["snapshot_id"],
                    export_id=plan["export_id"],
                    created_at=plan["created_at"],
                    schema_version=plan.get("schema_version", "0.1.0"),
                    cancelled=cancelled,
                )
                blob = publish_export(bundle, self.store, job_id=lease.job_id, fence=lease.fence)
                with self.connection.transaction():
                    self.jobs.publish_export(
                        lease,
                        digest(bundle.manifest),
                        lambda _: {
                            "sha256": blob.sha256,
                            "byte_length": blob.byte_length,
                            "storage_key": blob.storage_key,
                            "source_included": False,
                            "manifest": bundle.manifest,
                            "blob": {
                                "sha256": blob.sha256,
                                "byte_length": blob.byte_length,
                                "storage_key": blob.storage_key,
                            },
                        },
                    )
                    self.jobs.finish(lease, "succeeded")
            else:
                raise ValueError("UNSUPPORTED_ANALYST_JOB")
        except LeaseLost:
            try:
                self.jobs.finish(lease, "cancelled")
            except LeaseLost:
                pass
        except Exception:  # Job boundary: malformed inputs cannot stop the worker loop.
            try:
                self.jobs.finish(lease, "failed")
            except LeaseLost:
                pass
        return True

    def _analyze(self, lease: Lease, plan: dict[str, Any], cancelled: Callable[[], bool]) -> None:
        prepared = restore_import(plan["import_run_id"], self.repository, self.store)
        prepared = replace(
            prepared, context=replace(prepared.context, job_id=lease.job_id, fence=lease.fence)
        )
        inventory = build_inventory(
            prepared,
            run_id=lease.run_id,
            cancelled=cancelled,
            limits=InventoryLimits(
                max_entities=10000, max_dependencies=20000, max_evidence=20000, max_records=20000
            ),
        )
        analyses: list[PreparedAnalysis] = []
        output_bytes = 0
        node_count = 0
        by_path = {item.path: item for item in prepared.items}
        available_descriptions: list[dict[str, Any]] = []
        dds_languages = {"DDS", "PF", "LF", "DSPF", "DDS-PF", "DDS-LF", "DDS-DSPF"}
        ordered_entries = sorted(
            prepared.manifest["artifacts"],
            key=lambda entry: 0 if (entry["language"] or "").upper() in dds_languages else 1,
        )
        for entry in ordered_entries:
            if cancelled():
                raise ValueError("CANCELLED")
            item = by_path.get(entry["relative_path"])
            if entry["kind"] != "source" or item is None or item.decoded_blob is None:
                continue
            language = (entry["language"] or "").upper()
            parser = {
                "RPGLE": analyze_rpgle,
                "CL": analyze_cl,
                "CLLE": analyze_cl,
                "DDS": analyze_dds,
                "PF": analyze_dds,
                "LF": analyze_dds,
                "DSPF": analyze_dds,
                "DDS-PF": analyze_dds,
                "DDS-LF": analyze_dds,
                "DDS-DSPF": analyze_dds,
            }.get(language)
            if parser:
                parser_options: dict[str, Any] = (
                    {"available_descriptions": tuple(available_descriptions)}
                    if parser is analyze_rpgle
                    else {}
                )
                analyses.append(
                    parser(
                        prepared,
                        entry["relative_path"],
                        self.store,
                        run_id=lease.run_id,
                        cancelled=cancelled,
                        **parser_options,
                    )
                )
                if parser is analyze_dds and entry.get("object_identity"):
                    available_descriptions.append(
                        {
                            "identity": entry["object_identity"],
                            "artifact_id": item.artifact["artifact_id"] if item.artifact else "",
                            "definitions": analyses[-1].ir["definitions"],
                        }
                    )
                output_bytes += analyses[-1].blob.byte_length
                node_count += len(analyses[-1].ir["nodes"])
                if output_bytes > 64 * 1024 * 1024 or node_count > 20000:
                    raise ValueError("PIPELINE_OUTPUT_LIMIT")
        # Each member gets its own analysis artifact, even within the same language/run.
        for index, analysis in enumerate(analyses):
            artifact = dict(analysis.artifact)
            artifact["locator"] = (
                artifact["locator"].removesuffix(".json")
                + "-"
                + analysis.configuration_sha256
                + ".json"
            )
            artifact["artifact_id"] = "art_" + digest(
                {key: artifact[key] for key in ("import_id", "locator", "sha256")}
            )
            analyses[index] = replace(analysis, artifact=artifact)
        publication = build_publication(prepared, inventory, analyses, cancelled=cancelled)
        inventory = replace(
            inventory,
            entities=inventory.entities + publication.entities,
            dependencies=inventory.dependencies
            + self._source_calls(inventory, analyses, prepared)
            + publication.dependencies,
        )
        previous = [
            row[0]
            for row in self.connection.execute(
                "SELECT r.payload FROM rule_revisions r JOIN rule_heads h USING(revision_id) "
                "WHERE r.system_namespace=%s",
                (self.repository.namespace,),
            ).fetchall()
        ]
        rules: dict[str, Any] = {"entities": [], "rule_revisions": [], "diagnostics": []}
        for analysis in analyses:
            subjects = {eid for ev in analysis.evidence for eid in ev["subject_entity_ids"]}
            programs = [
                entity["entity_id"]
                for entity in inventory.entities
                if entity["identity"]["kind"] == "Program"
                and any(
                    edge["to_entity_id"] == entity["entity_id"]
                    and edge["from_entity_id"] in subjects
                    and edge["relationship"] == "SOURCE_OF"
                    for edge in inventory.dependencies
                )
            ]
            is_canonical = bool(analysis.evidence) and analysis.evidence[0]["provider"]["id"] in {
                "laip-rpgle-lark",
                "laip-dds-lark",
            }
            extractor = extract_rules_v2 if is_canonical else extract_rules
            extra_args = [publication] if is_canonical else []
            extracted = extractor(
                analysis,
                *extra_args,
                run_id=lease.run_id,
                system_namespace=self.repository.namespace,
                created_at=plan["created_at"],
                program_entity_ids=programs,
                previous_revisions=previous,
                cancelled=cancelled,
            )
            for field in rules:
                rules[field].extend(extracted[field])
        evidence = list(inventory.evidence) + [
            ev for analysis in analyses for ev in analysis.evidence
        ]
        workflows = compose_workflows(
            inventory.dependencies,
            run_id=lease.run_id,
            system_namespace=self.repository.namespace,
            created_at=plan["created_at"],
            program_entities=inventory.entities,
            evidence=evidence,
            cancelled=cancelled,
        )
        local_workflows = compose_local_workflows(
            [
                result
                for result in analyses
                if result.evidence and result.evidence[0]["provider"]["id"] == "laip-rpgle-lark"
            ],
            publication,
            run_id=lease.run_id,
            system_namespace=self.repository.namespace,
            created_at=plan["created_at"],
            cancelled=cancelled,
        )
        for field in workflows:
            workflows[field].extend(local_workflows[field])
        basis = self.repository._run(lease.run_id)
        evidence_index = {ev["evidence_id"]: ev for ev in evidence}
        for revision in rules["rule_revisions"]:
            if revision["schema_version"] == "0.2.0":
                continue
            revision["support_fingerprint"] = digest(
                {
                    "evidence": sorted(
                        [
                            {
                                "evidence_id": eid,
                                "content_sha256": evidence_index[eid]["content_sha256"],
                            }
                            for eid in revision["evidence_ids"]
                        ],
                        key=lambda ev: ev["evidence_id"],
                    ),
                    "providers": sorted(PROVIDERS, key=lambda p: (p["id"], p["version"])),
                    "resolution_decisions": [],
                    "configuration_sha256": basis["configuration_sha256"],
                }
            )
            revision["revision_id"] = record_id("rev_", revision, "revision_id")
        register_bundle(
            lease.run_id,
            prepared.context.import_id,
            basis["configuration_sha256"],
            PROVIDERS,
            self.repository,
        )
        key = PublicationKey(
            prepared.manifest_artifact["artifact_id"],
            PROVIDER["id"],
            "0.1.0",
            None,
            basis["configuration_sha256"],
            "analyst_pipeline",
        )
        fingerprint = digest(
            {
                "inventory": inventory.configuration_sha256,
                "analyses": [result.configuration_sha256 for result in analyses],
                "rules": rules,
                "workflows": workflows,
                "facts": list(publication.bundles),
            }
        )

        def publish(_: psycopg.Connection[Any]) -> dict[str, Any]:
            return self._persist(
                lease.run_id, prepared, inventory, analyses, rules, workflows, publication
            )

        # Publication, terminal state, and selected snapshot are one database transaction.
        with self.connection.transaction():
            result = self.jobs.publish_checkpoint(lease, key, fingerprint, publish)
            outcome = self.jobs.finish(lease, result["outcome"])
            if outcome in ("succeeded", "partial"):
                self._snapshot(lease.run_id)

    def _source_calls(
        self, inventory: Any, analyses: list[PreparedAnalysis], prepared: PreparedImport
    ) -> tuple[dict[str, Any], ...]:
        """Resolve explicit static CL calls only; procedure/runtime targets stay boundaries."""
        edges = []
        programs = [e for e in inventory.entities if e["identity"]["kind"] == "Program"]
        associations = {
            edge["from_entity_id"]: edge["to_entity_id"]
            for edge in inventory.dependencies
            if edge["relationship"] == "SOURCE_OF" and edge["resolution"] == "resolved"
        }
        for analysis in analyses:
            for ev in analysis.evidence:
                for ref in ev["metadata"].get("references", []):
                    if ref["kind"] != "call":
                        continue
                    target = ref.get("target")
                    if not isinstance(target, str) or not target:
                        continue
                    parts = target.upper().split("/")
                    matches = [
                        p["entity_id"]
                        for p in programs
                        if p["identity"]["qualified_identity"][-1] == parts[-1]
                        and (
                            len(parts) == 2
                            and p["identity"]["qualified_identity"][0] == parts[0]
                            or len(parts) == 1
                            and p["identity"]["qualified_identity"][0]
                            in prepared.manifest["library_list"]
                        )
                    ]
                    dynamic = ref.get("dynamic", False)
                    if dynamic:
                        matches = []
                    resolution = (
                        "dynamic"
                        if dynamic
                        else "resolved"
                        if len(matches) == 1
                        else "ambiguous"
                        if matches
                        else "missing"
                    )
                    for member in ev["subject_entity_ids"]:
                        source = associations.get(member)
                        if source is None or source not in {p["entity_id"] for p in programs}:
                            continue
                        edge = {
                            "schema_version": "0.1.0",
                            "run_id": inventory.run_id,
                            "from_entity_id": source,
                            "relationship": "CALLS",
                            "to_entity_id": matches[0] if resolution == "resolved" else None,
                            "target_expression": target,
                            "resolution": resolution,
                            "candidate_entity_ids": sorted(matches)
                            if resolution == "ambiguous"
                            else [],
                            "resolution_context": {
                                "library_list": prepared.manifest["library_list"],
                                "namespace": self.repository.namespace,
                                "method": "static_cl_supplied_scope",
                            },
                            "classification": "observed",
                            "evidence_ids": [ev["evidence_id"]],
                            "limitations": [
                                "Static source call only; runtime target and effects "
                                "are not established."
                            ],
                        }
                        edge["dependency_id"] = record_id("dep_", edge, "dependency_id")
                        edges.append(edge)
        return tuple(edges)

    def _persist(
        self,
        run_id: str,
        prepared: PreparedImport,
        inventory: Any,
        analyses: list[PreparedAnalysis],
        rules: dict[str, Any],
        workflows: dict[str, Any],
        publication: Publication | None = None,
    ) -> dict[str, Any]:
        repo = self.repository
        # Analysis status belongs to the selected observation, not canonical identity.
        # Associate parser results only through source evidence and explicit SOURCE_OF.
        statuses: dict[str, str] = {}
        supports: dict[str, set[str]] = {}
        for analysis in analyses:
            status = "analyzed" if analysis.ir["conclusions_allowed"] else "partial"
            for ev in analysis.evidence:
                for entity_id in ev["subject_entity_ids"]:
                    if statuses.get(entity_id) != "partial":
                        statuses[entity_id] = status
                    supports.setdefault(entity_id, set()).add(ev["evidence_id"])
        for edge in inventory.dependencies:
            if edge["relationship"] == "SOURCE_OF" and edge["resolution"] == "resolved":
                source, target = edge["from_entity_id"], edge["to_entity_id"]
                if source in statuses and target:
                    if statuses.get(target) != "partial":
                        statuses[target] = statuses[source]
                    supports.setdefault(target, set()).update(supports[source])
        pending = {}
        for entity in inventory.entities:
            observation = dict(entity)
            if entity["entity_id"] in statuses:
                observation["analysis_status"] = statuses[entity["entity_id"]]
                observation["evidence_ids"] = sorted(
                    set(entity["evidence_ids"]) | supports[entity["entity_id"]]
                )
            pending[entity["entity_id"]] = observation
        while pending:
            ready = [entity for entity in pending.values() if entity["parent_id"] not in pending]
            if not ready:
                raise ValueError("ENTITY_PARENT_CYCLE")
            for entity in ready:
                repo.put_entity(entity, run_id)
                del pending[entity["entity_id"]]
        for item in prepared.items:
            if item.artifact:
                repo.attach_artifact(run_id, item.artifact["artifact_id"])
        evidence = list(inventory.evidence)
        for analysis in analyses:
            repo.put_blob(analysis.blob.sha256, analysis.blob.byte_length)
            repo.put_artifact(analysis.artifact)
            repo.attach_artifact(run_id, analysis.artifact["artifact_id"])
            evidence.extend(analysis.evidence)
        pending_ev = {ev["evidence_id"]: ev for ev in evidence}
        while pending_ev:
            ready = [
                ev
                for ev in pending_ev.values()
                if not set(ev["supporting_evidence_ids"] + ev["contradiction_evidence_ids"])
                & pending_ev.keys()
            ]
            if not ready:
                raise ValueError("EVIDENCE_CYCLE")
            for ev in ready:
                repo.put_evidence(ev)
                del pending_ev[ev["evidence_id"]]
        for edge in inventory.dependencies:
            repo.put_dependency(edge)
        if publication is not None:
            for bundle in publication.bundles:
                repo.put_fact_bundle(bundle)
        for revision in rules["rule_revisions"]:
            if revision["schema_version"] == "0.2.0":
                revision["support_fingerprint"] = repo.rule_support_fingerprint(revision)
                revision["revision_id"] = record_id("rev_", revision, "revision_id")
        for workflow in workflows["workflows"]:
            if workflow["schema_version"] == "0.2.0":
                workflow["support_fingerprint"] = repo.rule_support_fingerprint(
                    workflow_support_record(workflow)
                )
                workflow["workflow_id"] = record_id("wf_", workflow, "workflow_id")
        persisted = persist_bundle(run_id, rules, workflows, repo)
        repo._insert(
            "inventory_reports",
            "run_id",
            {
                "run_id": run_id,
                "import_id": prepared.context.import_id,
                "system_namespace": repo.namespace,
                "configuration_sha256": repo._run(run_id)["configuration_sha256"],
                "payload": {
                    "schema_version": "0.1.0",
                    "accounting": list(inventory.accounting),
                    "completeness": "supplied_material_only",
                },
            },
        )
        eligible_sources = sum(
            1
            for item in prepared.items
            if item.decoded_blob is not None
            and any(
                entry["kind"] == "source" and entry["relative_path"] == item.path
                for entry in prepared.manifest["artifacts"]
            )
        )
        partial = (
            len(analyses) < eligible_sources
            or prepared.partial
            or not analyses
            or persisted["outcome"] == "partial"
            or any(not result.ir["conclusions_allowed"] for result in analyses)
            or any(edge["resolution"] != "resolved" for edge in inventory.dependencies)
        )
        # Persist bounded, source-backed summary codes, never imported source text.
        diagnostics: list[dict[str, Any]] = []
        limitations = ["Offline analysis covers supplied artifacts and metadata only."]
        for result in analyses:
            for code in result.ir.get("include_diagnostics", []):
                diagnostics.append({"code": code, "artifact_id": result.artifact["artifact_id"]})
            if not result.ir["conclusions_allowed"]:
                diagnostics.append(
                    {"code": "UNKNOWN_EFFECTS", "artifact_id": result.artifact["artifact_id"]}
                )
        unresolved = [edge for edge in inventory.dependencies if edge["resolution"] != "resolved"]
        for edge in unresolved:
            diagnostics.append(
                {
                    "code": "UNRESOLVED_DEPENDENCY",
                    "dependency_id": edge["dependency_id"],
                    "resolution": edge["resolution"],
                    "evidence_ids": edge["evidence_ids"][:10],
                }
            )
        for item in inventory.accounting:
            if item["processing_status"] not in {"decoded", "imported"} or item["diagnostics"]:
                diagnostics.append(
                    {
                        "code": "ARTIFACT_ACCOUNTING",
                        "artifact_id": item["artifact_id"],
                        "processing_status": item["processing_status"],
                        "scope_state": item["scope_state"],
                        "evidence_ids": item["evidence_ids"][:10],
                    }
                )
        if any(not result.ir["conclusions_allowed"] for result in analyses):
            limitations.append(
                "Unknown constructs or unavailable includes block complete business conclusions."
            )
        if unresolved:
            limitations.append(
                "Dynamic, ambiguous, or unavailable dependency targets remain unresolved; "
                "no runtime execution was performed."
            )
        if len(analyses) < eligible_sources:
            limitations.append(
                "Some supplied source artifacts have no supported language analysis."
            )
        if persisted["outcome"] == "partial":
            limitations.append("Syntactic business-rule candidates require analyst review.")
        if prepared.partial:
            limitations.append(
                "The import has missing, excluded, or partially processed supplied artifacts."
            )
        if len(diagnostics) > 200:
            limitations.append(
                "Overview diagnostics are limited to 200; inspect evidence and "
                "artifact accounting for full detail."
            )
        return {
            "outcome": "partial" if partial else "succeeded",
            "run_id": run_id,
            "diagnostics": diagnostics[:200],
            "limitations": limitations,
            "fact_coverage": [
                {
                    "artifact_id": bundle["artifact_id"],
                    "profile_id": bundle["profile_id"],
                    "profile_version": bundle["profile_version"],
                    "accounting": bundle["accounting"],
                    "partial_reasons": sorted(
                        {
                            fact["data"]["code"]
                            for fact in bundle["facts"]
                            if fact["kind"] == "diagnostic" and fact["data"]["blocks_claim"]
                        }
                    ),
                }
                for bundle in (publication.bundles if publication else ())
            ],
            "analysis_count": len(analyses),
            "artifact_count": len(inventory.accounting),
        }

    def _snapshot(self, run_id: str) -> str:
        repo = self.repository
        manifest = {}
        for kind, table, column in [
            ("observations", "entity_observations", "observation_id"),
            ("evidence", "evidence", "evidence_id"),
            ("dependencies", "dependencies", "dependency_id"),
            ("revisions", "rule_revisions", "revision_id"),
        ]:
            manifest[kind] = [
                row[0]
                for row in self.connection.execute(
                    f"SELECT {column} FROM {table} WHERE run_id=%s AND system_namespace=%s "
                    f"ORDER BY {column}",
                    (run_id, repo.namespace),
                ).fetchall()
            ]
        snapshot_id = "snap_" + digest({"run_id": run_id, "manifest": manifest})
        record_versions: set[str] = set()
        for table in ("entity_observations", "evidence", "dependencies", "rule_revisions"):
            record_versions.update(
                row[0]
                for row in self.connection.execute(
                    f"SELECT DISTINCT payload->>'schema_version' FROM {table} "
                    "WHERE run_id=%s AND system_namespace=%s",
                    (run_id, repo.namespace),
                ).fetchall()
            )
        version = "0.2.0" if "0.2.0" in record_versions else "0.1.0"
        repo.publish_snapshot(
            snapshot_id,
            run_id,
            manifest,
            schema_version=version,
            record_versions=sorted(record_versions),
        )
        index_snapshot(repo, snapshot_id)
        self.connection.execute(
            "INSERT INTO audit_events(actor_id,action,resource_id,payload) VALUES(%s,%s,%s,%s)",
            (
                "local-worker",
                "analyst_snapshot",
                snapshot_id,
                Jsonb({"system_namespace": repo.namespace, "run_id": run_id}),
            ),
        )
        return snapshot_id
