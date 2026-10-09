"""Read-only retrieval tied to immutable snapshot members and review watermark."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Any

import psycopg

from laip.canonical import identity_id
from laip.masking import POLICY
from laip.persistence import Repository, validate


@dataclass(frozen=True)
class RetrievalLimits:
    max_records: int = 2000
    max_nodes: int = 500
    max_edges: int = 1000
    max_depth: int = 8
    max_evidence: int = 1000
    max_work: int = 20000
    timeout_seconds: int = 30

    def __post_init__(self) -> None:
        if any(type(v) is not int or v < 1 for v in asdict(self).values()):
            raise ValueError("INVALID_RETRIEVAL_LIMITS")


DEFAULT_LIMITS = RetrievalLimits()


class RetrievalService:
    def __init__(
        self,
        repository: Repository,
        snapshot_id: str,
        *,
        limits: RetrievalLimits = DEFAULT_LIMITS,
        deadline: float | None = None,
    ):
        if not isinstance(snapshot_id, str) or not snapshot_id or len(snapshot_id) > 256:
            raise ValueError("INVALID_SNAPSHOT")
        row = repository.connection.execute(
            "SELECT run_id,review_sequence,payload FROM snapshots WHERE snapshot_id=%s AND "
            "system_namespace=%s",
            (snapshot_id, repository.namespace),
        ).fetchone()
        if row is None:
            raise ValueError("SNAPSHOT_NOT_FOUND_OR_CROSS_NAMESPACE")
        self.repository = repository
        self.snapshot_id = snapshot_id
        self.namespace = repository.namespace
        self.run_id = str(row[0])
        self.review_sequence = int(row[1])
        self.snapshot_schema_version = row[2].get("schema_version", "0.1.0")
        self.record_versions = set(row[2].get("record_versions", ["0.1.0"]))
        self.limits = limits
        self.deadline = deadline

    def require_version(self, requested_version: str) -> None:
        """Negotiate the whole frozen snapshot, including its authored envelope."""
        from laip.schema_registry import require_compatible_version

        require_compatible_version(
            requested_version, self.record_versions | {self.snapshot_schema_version}
        )

    def _guard(self, cancelled: Callable[[], bool] | None) -> Callable[[], int]:
        deadline = time.monotonic() + self.limits.timeout_seconds
        if self.deadline is not None:
            deadline = min(deadline, self.deadline)
        work = 0

        def check() -> int:
            nonlocal work
            work += 1
            if (
                work > self.limits.max_work
                or time.monotonic() > deadline
                or (cancelled and cancelled())
            ):
                raise ValueError("RETRIEVAL_RESOURCE_LIMIT_OR_CANCELLED")
            return max(1, int((deadline - time.monotonic()) * 1000))

        check()
        return check

    def _query(
        self, check: Callable[[], int], query: str, params: tuple[Any, ...]
    ) -> psycopg.Cursor[Any]:
        remaining = check()
        try:
            with self.repository.connection.transaction():
                old = self.repository.connection.execute("SHOW statement_timeout").fetchone()
                self.repository.connection.execute(
                    "SELECT set_config('statement_timeout',%s,true)", (str(remaining),)
                )
                cursor = self.repository.connection.execute(query, params)
                check()
                if old is not None:
                    self.repository.connection.execute(
                        "SELECT set_config('statement_timeout',%s,true)", (old[0],)
                    )
            return cursor
        except psycopg.errors.QueryCanceled:
            raise ValueError("RETRIEVAL_RESOURCE_LIMIT_OR_CANCELLED") from None

    def _record(self, kind: str, ident: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": kind,
            "id": ident,
            "payload": payload,
            "evidence_ids": list(payload.get("evidence_ids", [])),
            "classification": payload.get(
                "classification",
                "inferred"
                if kind == "chunk"
                else payload.get("attributes", {}).get("classification"),
            ),
            "review_status": "pending_review"
            if kind in {"evidence", "dependency", "rule", "workflow"}
            else "not_applicable",
            "review_id": None,
        }

    def _workflows(self, check: Callable[[], int]) -> list[dict[str, Any]]:
        if self.snapshot_schema_version == "0.1.0":
            return []
        rows = self._query(
            check,
            "SELECT DISTINCT w.workflow_id,w.payload FROM workflow_revisions w "
            "JOIN entity_observations o ON o.entity_id=w.entity_id "
            "JOIN snapshot_members m ON m.observation_id=o.observation_id "
            "WHERE m.snapshot_id=%s AND m.system_namespace=%s AND o.system_namespace=%s "
            "AND w.system_namespace=%s AND w.run_id=%s ORDER BY w.workflow_id LIMIT %s",
            (
                self.snapshot_id,
                self.namespace,
                self.namespace,
                self.namespace,
                self.run_id,
                self.limits.max_records + 1,
            ),
        ).fetchall()
        if len(rows) > self.limits.max_records:
            raise ValueError("RETRIEVAL_RECORD_LIMIT")
        result = []
        for ident, payload in rows:
            if payload.get("schema_version") == "0.1.0":
                from laip.exports import _validate_projection

                _validate_projection("ExportWorkflow", payload, "0.1.0")
            else:
                validate("Workflow", payload)
            result.append(self._record("workflow", ident, payload))
        return result

    def _entities(self, ids: Sequence[str], check: Callable[[], int]) -> list[dict[str, Any]]:
        check()
        rows = self._query(
            check,
            "SELECT o.observation_id,o.payload FROM snapshot_members m JOIN "
            "entity_observations o ON o.observation_id=m.observation_id "
            "WHERE m.snapshot_id=%s AND m.system_namespace=%s AND o.system_namespace=%s AND "
            "o.entity_id=ANY(%s) "
            "ORDER BY o.entity_id,o.observation_id LIMIT %s",
            (
                self.snapshot_id,
                self.namespace,
                self.namespace,
                list(ids),
                self.limits.max_records + 1,
            ),
        ).fetchall()
        if len(rows) > self.limits.max_records:
            raise ValueError("RETRIEVAL_RECORD_LIMIT")
        return [self._record("entity", row[0], row[1]) for row in rows]

    def _assemble(
        self,
        records: Sequence[dict[str, Any]],
        check: Callable[[], int],
        *,
        boundaries: Sequence[dict[str, Any]] = (),
        truncated: bool = False,
        diagnostics: Sequence[dict[str, Any]] = (),
    ) -> dict[str, Any]:
        unique = {(r["type"], r["id"]): r for r in records}
        todo = {eid for r in records for eid in r["evidence_ids"]}
        todo.update(r["id"] for r in records if r["type"] == "evidence")
        evidence_seen: set[str] = set()
        while todo:
            check()
            batch = sorted(todo - evidence_seen)
            if not batch:
                break
            if len(evidence_seen) + len(batch) > self.limits.max_evidence:
                raise ValueError("RETRIEVAL_CITATION_LIMIT")
            rows = self._query(
                check,
                "SELECT e.evidence_id,e.payload FROM snapshot_members m JOIN evidence e ON "
                "e.evidence_id=m.evidence_id "
                "WHERE m.snapshot_id=%s AND m.system_namespace=%s AND e.system_namespace=%s AND "
                "e.evidence_id=ANY(%s)",
                (self.snapshot_id, self.namespace, self.namespace, batch),
            ).fetchall()
            if {r[0] for r in rows} != set(batch):
                raise ValueError("CITATION_NOT_IN_SNAPSHOT")
            todo = set()
            for eid, payload in rows:
                check()
                validate("Evidence", payload)
                if payload["run_id"] != self.run_id:
                    raise ValueError("INVALID_SNAPSHOT_LINEAGE")
                record = self._record("evidence", eid, payload)
                record["evidence_ids"] = (
                    [eid]
                    + list(payload["supporting_evidence_ids"])
                    + list(payload["contradiction_evidence_ids"])
                )
                unique[("evidence", eid)] = record
                evidence_seen.add(eid)
                todo.update(record["evidence_ids"])
        if len(unique) > self.limits.max_records:
            raise ValueError("RETRIEVAL_RECORD_LIMIT")
        reviewed = [r for r in unique.values() if r["type"] in {"evidence", "dependency", "rule"}]
        if reviewed:
            rows = self._query(
                check,
                "SELECT DISTINCT ON(subject_type,subject_id) "
                "subject_type,subject_id,review_id,status FROM reviews "
                "WHERE system_namespace=%s AND sequence<=%s AND subject_id=ANY(%s) "
                "ORDER BY subject_type,subject_id,sequence DESC",
                (self.namespace, self.review_sequence, [r["id"] for r in reviewed]),
            ).fetchall()
            review_map = {(r[0], r[1]): (r[2], r[3]) for r in rows}
            for record in reviewed:
                check()
                subject = "rule_revision" if record["type"] == "rule" else record["type"]
                if (subject, record["id"]) in review_map:
                    record["review_id"], record["review_status"] = review_map[
                        (subject, record["id"])
                    ]
        entities = {r["payload"]["entity_id"] for r in unique.values() if r["type"] == "entity"}
        for record in unique.values():
            payload = record["payload"]
            entities.update(payload.get("subject_entity_ids", []))
            for key in ("from_entity_id", "to_entity_id", "rule_entity_id"):
                if payload.get(key):
                    entities.add(payload[key])
            for key in (
                "application_entity_ids",
                "program_entity_ids",
                "procedure_entity_ids",
                "candidate_entity_ids",
            ):
                entities.update(payload.get(key, []))
        check()
        result = {
            "snapshot_id": self.snapshot_id,
            "system_namespace": self.namespace,
            "run_id": self.run_id,
            "entity_ids": sorted(entities),
            "records": list(unique.values()),
            "boundaries": list(boundaries),
            "truncated": truncated,
            "diagnostics": list(diagnostics),
        }
        if self.snapshot_schema_version == "0.2.0":
            rows = self._query(
                check,
                "SELECT payload FROM artifact_results WHERE run_id=%s "
                "ORDER BY result_id DESC LIMIT 1",
                (self.run_id,),
            ).fetchall()
            result["fact_coverage"] = rows[0][0].get("fact_coverage", []) if rows else []
        return result

    def exact(
        self,
        entity_id: str | None = None,
        *,
        kind: str | None = None,
        qualified_identity: Sequence[str] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        check = self._guard(cancelled)
        if entity_id is None:
            if kind is None or qualified_identity is None:
                raise ValueError("EXACT_IDENTITY_REQUIRED")
            identity = {
                "system_namespace": self.namespace,
                "kind": kind,
                "qualified_identity": list(qualified_identity),
            }
            from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

            from laip.schema_registry import schema_for_version

            schema = schema_for_version(self.snapshot_schema_version)
            Draft202012Validator({"$ref": "#/$defs/Identity", "$defs": schema["$defs"]}).validate(
                identity
            )
            entity_id = identity_id(identity)
        elif kind is not None or qualified_identity is not None:
            raise ValueError("AMBIGUOUS_EXACT_REQUEST")
        records = self._entities([entity_id], check)
        rows = (
            self._query(
                check,
                "SELECT DISTINCT r.revision_id,r.payload FROM snapshot_members m JOIN "
                "rule_revisions r ON r.revision_id=m.revision_id "
                "LEFT JOIN rule_scopes s ON s.revision_id=r.revision_id "
                "LEFT JOIN rule_subjects t ON t.revision_id=r.revision_id WHERE m.snapshot_id=%s "
                "AND m.system_namespace=%s "
                "AND r.system_namespace=%s AND (r.rule_entity_id=%s OR s.entity_id=%s "
                "OR t.entity_id=%s) ORDER BY "
                "r.revision_id LIMIT %s",
                (
                    self.snapshot_id,
                    self.namespace,
                    self.namespace,
                    entity_id,
                    entity_id,
                    entity_id,
                    self.limits.max_records + 1,
                ),
            ).fetchall()
            if records
            else []
        )
        if len(rows) > self.limits.max_records:
            raise ValueError("RETRIEVAL_RECORD_LIMIT")
        records.extend(self._record("rule", r[0], r[1]) for r in rows)
        if records:
            records.extend(
                workflow
                for workflow in self._workflows(check)
                if entity_id
                in {
                    workflow["payload"]["workflow_entity_id"],
                    *workflow["payload"]["program_entity_ids"],
                    *workflow["payload"].get("procedure_entity_ids", []),
                    *(step["entity_id"] for step in workflow["payload"].get("steps", [])),
                }
            )
        diagnostics = [] if records else [{"code": "ENTITY_NOT_IN_SNAPSHOT"}]
        return self._assemble(records, check, diagnostics=diagnostics)

    def collect(
        self, *, policy_version: str = POLICY, cancelled: Callable[[], bool] | None = None
    ) -> dict[str, Any]:
        """Collect a bounded, complete selected snapshot for local exports/context."""
        check = self._guard(cancelled)
        records: list[dict[str, Any]] = []
        # SQL identifiers are fixed here; caller data is always parameterized.
        for kind, table, key, member in (
            ("entity", "entity_observations", "observation_id", "observation_id"),
            ("dependency", "dependencies", "dependency_id", "dependency_id"),
            ("rule", "rule_revisions", "revision_id", "revision_id"),
            ("evidence", "evidence", "evidence_id", "evidence_id"),
        ):
            rows = self._query(
                check,
                f"SELECT r.{key},r.payload FROM snapshot_members m JOIN {table} r "
                f"ON r.{key}=m.{member} WHERE m.snapshot_id=%s AND m.system_namespace=%s "
                f"AND r.system_namespace=%s ORDER BY r.{key} LIMIT %s",
                (self.snapshot_id, self.namespace, self.namespace, self.limits.max_records + 1),
            ).fetchall()
            if len(records) + len(rows) > self.limits.max_records:
                raise ValueError("RETRIEVAL_RECORD_LIMIT")
            records.extend(self._record(kind, row[0], row[1]) for row in rows)
        records.extend(self._workflows(check))
        if len(records) > self.limits.max_records:
            raise ValueError("RETRIEVAL_RECORD_LIMIT")
        rows = self._query(
            check,
            "SELECT c.chunk_id,c.text_content,coalesce(array_agg(l.evidence_id ORDER BY "
            "l.evidence_id) FILTER(WHERE l.evidence_id IS NOT NULL),ARRAY[]::text[]) "
            "FROM chunks c LEFT JOIN chunk_links l ON l.chunk_id=c.chunk_id WHERE "
            "c.snapshot_id=%s AND c.system_namespace=%s AND c.policy_version=%s "
            "GROUP BY c.chunk_id,c.text_content ORDER BY c.chunk_id LIMIT %s",
            (self.snapshot_id, self.namespace, policy_version, self.limits.max_records + 1),
        ).fetchall()
        if len(records) + len(rows) > self.limits.max_records:
            raise ValueError("RETRIEVAL_RECORD_LIMIT")
        for ident, text, eids in rows:
            if not eids:
                raise ValueError("UNCITED_CHUNK")
            records.append(
                self._record(
                    "chunk", ident, {"chunk_id": ident, "text": text, "evidence_ids": list(eids)}
                )
            )
        boundaries = [
            {**r, "boundary_reason": "unresolved"}
            for r in records
            if r["type"] == "dependency"
            and (r["payload"]["resolution"] != "resolved" or r["classification"] == "unresolved")
        ]
        return self._assemble(records, check, boundaries=boundaries)

    def evidence(
        self, evidence_ids: str | Sequence[str], *, cancelled: Callable[[], bool] | None = None
    ) -> dict[str, Any]:
        check = self._guard(cancelled)
        ids = [evidence_ids] if isinstance(evidence_ids, str) else list(evidence_ids)
        if not ids or len(ids) > self.limits.max_evidence:
            raise ValueError("INVALID_EVIDENCE_REQUEST")
        records = [
            self._record("evidence", eid, {"supporting_evidence_ids": []})
            for eid in sorted(set(ids))
        ]
        return self._assemble(records, check)

    def chunks(
        self, ids: Sequence[str], *, limit: int = 100, cancelled: Callable[[], bool] | None = None
    ) -> dict[str, Any]:
        check = self._guard(cancelled)
        if type(limit) is not int or not 1 <= limit <= self.limits.max_records or len(ids) > limit:
            raise ValueError("INVALID_CHUNK_REQUEST")
        rows = self._query(
            check,
            "SELECT c.chunk_id,c.text_content,coalesce(array_agg(l.evidence_id ORDER BY "
            "l.evidence_id) FILTER(WHERE l.evidence_id IS NOT NULL),ARRAY[]::text[]) "
            "FROM chunks c LEFT JOIN chunk_links l ON l.chunk_id=c.chunk_id WHERE "
            "c.snapshot_id=%s AND c.system_namespace=%s "
            "AND c.chunk_id=ANY(%s) GROUP BY c.chunk_id,c.text_content ORDER BY c.chunk_id",
            (self.snapshot_id, self.namespace, list(ids)),
        ).fetchall()
        if {r[0] for r in rows} != set(ids):
            raise ValueError("CHUNK_NOT_IN_SNAPSHOT")
        records = []
        for ident, text, eids in rows:
            if not eids:
                raise ValueError("UNCITED_CHUNK")
            records.append(
                self._record(
                    "chunk", ident, {"chunk_id": ident, "text": text, "evidence_ids": list(eids)}
                )
            )
        return self._assemble(records, check)

    def search(
        self,
        query: str,
        *,
        policy_version: str,
        limit: int = 20,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        check = self._guard(cancelled)
        if (
            not isinstance(query, str)
            or not query.strip()
            or len(query) > 1000
            or type(limit) is not int
            or not 1 <= limit <= 100
        ):
            raise ValueError("INVALID_SEARCH_REQUEST")
        rows = self._query(
            check,
            "SELECT chunk_id,text_content FROM chunks WHERE system_namespace=%s "
            "AND snapshot_id=%s AND policy_version=%s AND search_vector @@ "
            "plainto_tsquery('simple',%s) ORDER BY "
            "ts_rank(search_vector,plainto_tsquery('simple',%s)) DESC,chunk_id LIMIT %s",
            (self.namespace, self.snapshot_id, policy_version, query, query, limit),
        ).fetchall()
        hits = [{"chunk_id": row[0], "text": row[1]} for row in rows]
        result = self.chunks([h["chunk_id"] for h in hits], limit=limit, cancelled=cancelled)
        ranks = {h["chunk_id"]: i for i, h in enumerate(hits)}
        for record in result["records"]:
            if record["type"] == "chunk":
                record["rank"] = ranks[record["id"]]
        result["truncated"] = len(hits) == limit
        check()
        return result

    def graph(
        self,
        entity_ids: str | Sequence[str],
        *,
        depth: int = 2,
        direction: str = "outgoing",
        relationships: Sequence[str] = (),
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        check = self._guard(cancelled)
        seeds = [entity_ids] if isinstance(entity_ids, str) else list(entity_ids)
        if (
            not seeds
            or len(seeds) > self.limits.max_nodes
            or type(depth) is not int
            or not 0 <= depth <= self.limits.max_depth
            or direction not in {"outgoing", "incoming", "both"}
            or any(
                r
                not in {
                    "CALLS",
                    "READS",
                    "WRITES",
                    "UPDATES",
                    "DELETES",
                    "USES_SERVICE_PROGRAM",
                    "REFERENCES_FILE",
                    "DISPLAYS_SCREEN",
                    "EXECUTES_JOB",
                    "DEPENDS_ON",
                    "MEMBER_OF",
                    "SOURCE_OF",
                    "CONTAINS",
                }
                for r in relationships
            )
        ):
            raise ValueError("INVALID_GRAPH_REQUEST")
        records = self._entities(seeds, check)
        if {r["payload"]["entity_id"] for r in records} != set(seeds):
            raise ValueError("ENTITY_NOT_IN_SNAPSHOT")
        seen = set(seeds)
        frontier = set(seeds)
        edges: set[str] = set()
        boundaries = []
        truncated = False
        for level in range(depth + 1):
            if not frontier:
                break
            check()
            rows = self._query(
                check,
                "SELECT d.dependency_id,d.payload FROM snapshot_members m JOIN dependencies d ON "
                "d.dependency_id=m.dependency_id "
                "WHERE m.snapshot_id=%s AND m.system_namespace=%s AND d.system_namespace=%s AND "
                "((%s AND d.from_entity_id=ANY(%s)) OR (%s AND d.to_entity_id=ANY(%s))) "
                "AND (cardinality(%s::text[])=0 OR d.relationship=ANY(%s)) ORDER BY "
                "d.dependency_id LIMIT %s",
                (
                    self.snapshot_id,
                    self.namespace,
                    self.namespace,
                    direction in {"outgoing", "both"},
                    list(frontier),
                    direction in {"incoming", "both"},
                    list(frontier),
                    list(relationships),
                    list(relationships),
                    self.limits.max_edges + 1,
                ),
            ).fetchall()
            following: set[str] = set()
            for ident, payload in rows:
                check()
                if ident in edges:
                    continue
                if len(edges) >= self.limits.max_edges:
                    truncated = True
                    break
                edges.add(ident)
                record = self._record("dependency", ident, payload)
                records.append(record)
                resolved = (
                    payload["resolution"] == "resolved"
                    and payload["classification"] != "unresolved"
                    and payload.get("to_entity_id") is not None
                )
                if not resolved:
                    boundaries.append({**record, "boundary_reason": "unresolved"})
                    continue
                targets = (
                    {payload["to_entity_id"]}
                    if direction == "outgoing"
                    else {payload["from_entity_id"]}
                    if direction == "incoming"
                    else {payload["from_entity_id"], payload["to_entity_id"]}
                )
                targets -= seen
                if targets and (
                    level == depth or len(seen) + len(following | targets) > self.limits.max_nodes
                ):
                    boundaries.append({**record, "boundary_reason": "traversal_limit"})
                    truncated = True
                    continue
                following.update(targets)
            added = self._entities(sorted(following), check) if following else []
            if {r["payload"]["entity_id"] for r in added} != following:
                raise ValueError("INVALID_SNAPSHOT_ENTITY_CLOSURE")
            records.extend(added)
            seen.update(following)
            frontier = following
        return self._assemble(records, check, boundaries=boundaries, truncated=truncated)
