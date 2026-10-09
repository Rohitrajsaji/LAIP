"""Bounded synchronous PostgreSQL repositories for immutable canonical records.

The caller supplies an authorized namespace. Inputs are validated before writes;
immutable inserts reuse an exact replay and reject a conflicting payload.
"""

import json
import uuid
from importlib.resources import files
from typing import Any, cast

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from laip.canonical import digest, identity_id, normalize, record_id

SCHEMA = json.loads(files("laip").joinpath("contracts/laip.schema.json").read_text())


def validate(kind: str, record: dict[str, Any]) -> dict[str, Any]:
    from laip.schema_registry import validate_record

    return validate_record(kind, record)


class Repository:
    def __init__(self, connection: psycopg.Connection[Any], namespace: str):
        self.connection = connection
        self.namespace = namespace

    def _one(self, query: str, params: tuple[Any, ...]) -> Any:
        row = self.connection.execute(query, params).fetchone()
        if row is None:
            raise ValueError("NOT_FOUND_OR_CROSS_NAMESPACE")
        return row

    def _insert(self, table: str, key: str, values: dict[str, Any]) -> bool:
        columns = list(values)
        query = sql.SQL(
            "INSERT INTO {} ({}) VALUES ({}) ON CONFLICT ({}) DO NOTHING RETURNING {}"
        ).format(
            sql.Identifier(table),
            sql.SQL(",").join(map(sql.Identifier, columns)),
            sql.SQL(",").join(sql.Placeholder() for _ in columns),
            sql.Identifier(key),
            sql.Identifier(key),
        )
        inserted = self.connection.execute(
            query, tuple(Jsonb(v) if isinstance(v, (dict, list)) else v for v in values.values())
        ).fetchone()
        if inserted:
            return True
        current = self.connection.execute(
            sql.SQL("SELECT {} FROM {} WHERE {}=%s").format(
                sql.SQL(",").join(map(sql.Identifier, columns)),
                sql.Identifier(table),
                sql.Identifier(key),
            ),
            (values[key],),
        ).fetchone()
        if current is None or any(a != b for a, b in zip(current, values.values(), strict=True)):
            raise ValueError("IDENTITY_PAYLOAD_MISMATCH")
        return False

    def create_system(self) -> None:
        with self.connection.transaction():
            self.connection.execute(
                "INSERT INTO systems(system_namespace) VALUES(%s) ON CONFLICT DO NOTHING",
                (self.namespace,),
            )

    def create_import(self, import_id: str, manifest: dict[str, Any]) -> None:
        manifest = normalize(manifest)
        with self.connection.transaction():
            self._insert(
                "imports",
                "import_id",
                {
                    "import_id": import_id,
                    "system_namespace": self.namespace,
                    "manifest_sha256": digest(manifest),
                    "payload": manifest,
                },
            )

    def create_run(
        self, run_id: str, import_id: str, payload: dict[str, Any], state: str = "queued"
    ) -> None:
        payload = normalize(payload)
        if any(
            key in payload and payload[key] != value
            for key, value in [
                ("run_id", run_id),
                ("import_id", import_id),
                ("system_namespace", self.namespace),
            ]
        ):
            raise ValueError("RUN_IDENTITY_MISMATCH")
        with self.connection.transaction():
            if state not in ("queued", "running", "cancel_requested"):
                raise ValueError("NEW_RUN_NOT_ACTIVE")
            self._insert(
                "runs",
                "run_id",
                {
                    "run_id": run_id,
                    "system_namespace": self.namespace,
                    "import_id": import_id,
                    "payload": payload,
                    "state": state,
                },
            )
            for provider in payload.get("providers", []):
                self.connection.execute(
                    "INSERT INTO "
                    "run_providers(run_id,provider_id,provider_version,source_revision,payload) "
                    "VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                    (
                        run_id,
                        provider["id"],
                        provider["version"],
                        provider.get("source_revision"),
                        Jsonb(provider),
                    ),
                )

    def _run(self, run_id: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            self._one(
                "SELECT payload FROM runs WHERE run_id=%s AND system_namespace=%s",
                (run_id, self.namespace),
            )[0],
        )

    def _entity(self, entity_id: str) -> str:
        return cast(
            str,
            self._one(
                "SELECT kind FROM entities WHERE entity_id=%s AND system_namespace=%s",
                (entity_id, self.namespace),
            )[0],
        )

    def _evidence(self, evidence_id: str, run_id: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            self._one(
                "SELECT payload FROM evidence WHERE evidence_id=%s AND run_id=%s AND "
                "system_namespace=%s",
                (evidence_id, run_id, self.namespace),
            )[0],
        )

    def put_entity(self, record: dict[str, Any], run_id: str) -> str:
        record = validate("Entity", record)
        identity = record["identity"]
        if (
            identity["system_namespace"] != self.namespace
            or identity_id(identity) != record["entity_id"]
        ):
            raise ValueError("INVALID_IDENTITY")
        observation = "obs_" + digest({"run_id": run_id, "record": record})
        with self.connection.transaction():
            self._run(run_id)
            if record["parent_id"]:
                self._entity(record["parent_id"])
            self._insert(
                "entities",
                "entity_id",
                {
                    "entity_id": record["entity_id"],
                    "system_namespace": self.namespace,
                    "kind": identity["kind"],
                    "qualified_identity": identity["qualified_identity"],
                    "identity": identity,
                    "parent_id": record["parent_id"],
                },
            )
            self._insert(
                "entity_observations",
                "observation_id",
                {
                    "observation_id": observation,
                    "run_id": run_id,
                    "entity_id": record["entity_id"],
                    "system_namespace": self.namespace,
                    "observation_digest": digest(record),
                    "payload": record,
                },
            )
        return observation

    def put_blob(self, sha256: str, byte_length: int) -> None:
        with self.connection.transaction():
            self._insert(
                "blobs",
                "sha256",
                {"sha256": sha256, "byte_length": byte_length, "state": "available"},
            )

    def put_artifact(self, record: dict[str, Any]) -> None:
        record = validate("Artifact", record)
        if record["artifact_id"] != "art_" + digest(
            {k: record[k] for k in ("import_id", "locator", "sha256")}
        ):
            raise ValueError("INVALID_ARTIFACT_ID")
        with self.connection.transaction():
            self._one(
                "SELECT import_id FROM imports WHERE import_id=%s AND system_namespace=%s",
                (record["import_id"], self.namespace),
            )
            self._one(
                "SELECT sha256 FROM blobs WHERE sha256=%s AND byte_length=%s AND "
                "state='available' FOR UPDATE",
                (record["sha256"], record["byte_length"]),
            )
            self._insert(
                "artifacts",
                "artifact_id",
                {
                    "artifact_id": record["artifact_id"],
                    "system_namespace": self.namespace,
                    "import_id": record["import_id"],
                    "locator": record["locator"],
                    "sha256": record["sha256"],
                    "byte_length": record["byte_length"],
                    "source_member_id": record["source_member_id"],
                    "payload": record,
                },
            )

    def put_origin_map(self, record: dict[str, Any]) -> None:
        record = validate("OriginMap", record)
        with self.connection.transaction():
            inserted = self._insert(
                "origin_maps",
                "origin_map_id",
                {
                    "origin_map_id": record["origin_map_id"],
                    "system_namespace": self.namespace,
                    "payload": record,
                },
            )
            previous_end = 0
            for index, segment in enumerate(record["segments"]):
                length = self._one(
                    "SELECT byte_length FROM artifacts "
                    "WHERE artifact_id=%s AND system_namespace=%s",
                    (segment["original_artifact_id"], self.namespace),
                )[0]
                if (
                    segment["decoded_byte_start"] < previous_end
                    or segment["decoded_byte_end"] < segment["decoded_byte_start"]
                    or segment["raw_byte_end"] < segment["raw_byte_start"]
                    or segment["raw_byte_end"] > length
                ):
                    raise ValueError("INVALID_ORIGIN_MAP")
                previous_end = segment["decoded_byte_end"]
                if inserted:
                    self.connection.execute(
                        "INSERT INTO origin_map_segments VALUES(%s,%s,%s,%s,%s,%s)",
                        (
                            record["origin_map_id"],
                            index,
                            segment["original_artifact_id"],
                            self.namespace,
                            segment["raw_byte_start"],
                            segment["raw_byte_end"],
                        ),
                    )

    def attach_artifact(self, run_id: str, artifact_id: str) -> None:
        with self.connection.transaction():
            self._one(
                "SELECT a.artifact_id FROM artifacts a JOIN runs r "
                "USING(import_id,system_namespace) "
                "WHERE a.artifact_id=%s AND r.run_id=%s AND a.system_namespace=%s",
                (artifact_id, run_id, self.namespace),
            )
            self.connection.execute(
                "INSERT INTO run_artifacts(run_id,artifact_id,system_namespace) "
                "VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                (run_id, artifact_id, self.namespace),
            )

    def put_evidence(self, record: dict[str, Any]) -> None:
        record = validate("Evidence", record)
        if record["evidence_id"] != record_id("ev_", record, "evidence_id"):
            raise ValueError("INVALID_EVIDENCE_ID")
        with self.connection.transaction():
            run = self._run(record["run_id"])
            upstream = record.get("upstream_record")
            if upstream:
                self._one(
                    "SELECT artifact_id FROM run_artifacts WHERE run_id=%s AND "
                    "artifact_id=%s AND system_namespace=%s",
                    (record["run_id"], upstream["payload_artifact_id"], self.namespace),
                )
            if record["provider"] not in run.get("providers", []):
                raise ValueError("PROVIDER_NOT_IN_RUN")
            if record["artifact_id"]:
                artifact = self._one(
                    "SELECT a.sha256,a.byte_length FROM artifacts a JOIN run_artifacts r "
                    "USING(artifact_id) WHERE a.artifact_id=%s AND r.run_id=%s AND "
                    "a.system_namespace=%s",
                    (record["artifact_id"], record["run_id"], self.namespace),
                )
                if artifact[0] != record["content_sha256"]:
                    raise ValueError("INVALID_CONTENT_DIGEST")
            elif not record["metadata"] or digest(record["metadata"]) != record["content_sha256"]:
                raise ValueError("INVALID_METADATA_DIGEST")
            for span in record["source_locations"]:
                mapping = self._one(
                    "SELECT payload FROM origin_maps "
                    "WHERE origin_map_id=%s AND system_namespace=%s",
                    (span["origin_map_id"], self.namespace),
                )[0]
                if mapping["lossy"] or not any(
                    segment["original_artifact_id"] == span["artifact_id"]
                    for segment in mapping["segments"]
                ):
                    raise ValueError("INVALID_ORIGIN_MAP")
                a = self._one(
                    "SELECT a.byte_length FROM artifacts a JOIN run_artifacts r "
                    "USING(artifact_id) WHERE a.artifact_id=%s AND r.run_id=%s AND "
                    "a.system_namespace=%s",
                    (span["artifact_id"], record["run_id"], self.namespace),
                )
                if (
                    (span["start_line"], span["start_column"])
                    > (span["end_line"], span["end_column"])
                    or span["byte_start"] > span["byte_end"]
                    or span["byte_end"] > a[0]
                ):
                    raise ValueError("INVALID_SPAN")
            for eid in record["supporting_evidence_ids"] + record["contradiction_evidence_ids"]:
                self._evidence(eid, record["run_id"])
            self._insert(
                "evidence",
                "evidence_id",
                {
                    "evidence_id": record["evidence_id"],
                    "run_id": record["run_id"],
                    "system_namespace": self.namespace,
                    "artifact_id": record["artifact_id"],
                    "upstream_artifact_id": upstream["payload_artifact_id"] if upstream else None,
                    "record_sha256": digest(record),
                    "content_sha256": record["content_sha256"],
                    "provider_id": record["provider"]["id"],
                    "provider_version": record["provider"]["version"],
                    "payload": record,
                },
            )
            for entity in record["subject_entity_ids"]:
                self.connection.execute(
                    "INSERT INTO evidence_subjects VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                    (record["evidence_id"], entity, self.namespace),
                )
            for table, key in [
                ("evidence_support", "supporting_evidence_ids"),
                ("evidence_contradictions", "contradiction_evidence_ids"),
            ]:
                for eid in record[key]:
                    self.connection.execute(
                        sql.SQL("INSERT INTO {} VALUES(%s,%s,%s) ON CONFLICT DO NOTHING").format(
                            sql.Identifier(table)
                        ),
                        (record["evidence_id"], eid, self.namespace),
                    )

    def put_dependency(self, record: dict[str, Any]) -> None:
        record = validate("Dependency", record)
        if (
            record["dependency_id"] != record_id("dep_", record, "dependency_id")
            or record["resolution_context"]["namespace"] != self.namespace
        ):
            raise ValueError("INVALID_DEPENDENCY")
        if len(set(record["candidate_entity_ids"])) != len(record["candidate_entity_ids"]):
            raise ValueError("DUPLICATE_CANDIDATE")
        with self.connection.transaction():
            self._run(record["run_id"])
            for eid in record["evidence_ids"]:
                self._evidence(eid, record["run_id"])
            self._insert(
                "dependencies",
                "dependency_id",
                {
                    "dependency_id": record["dependency_id"],
                    "run_id": record["run_id"],
                    "system_namespace": self.namespace,
                    "from_entity_id": record["from_entity_id"],
                    "to_entity_id": record["to_entity_id"],
                    "relationship": record["relationship"],
                    "resolution": record["resolution"],
                    "payload": record,
                },
            )
            for eid in record["candidate_entity_ids"]:
                self.connection.execute(
                    "INSERT INTO dependency_candidates VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                    (record["dependency_id"], eid, self.namespace),
                )
            for eid in record["evidence_ids"]:
                self.connection.execute(
                    "INSERT INTO dependency_evidence VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",
                    (record["dependency_id"], eid, self.namespace),
                )

    def _run_entity(self, entity_id: str, run_id: str) -> str:
        kind = self._entity(entity_id)
        self._one(
            "SELECT entity_id FROM entity_observations "
            "WHERE entity_id=%s AND run_id=%s AND system_namespace=%s",
            (entity_id, run_id, self.namespace),
        )
        return kind

    def put_fact_bundle(self, record: dict[str, Any]) -> str:
        from laip.analysis_facts import validate_fact_bundle

        record = validate_fact_bundle(record)
        if record["system_namespace"] != self.namespace:
            raise ValueError("NOT_FOUND_OR_CROSS_NAMESPACE")
        bundle_id = "facts_" + digest(record)
        with self.connection.transaction():
            run = self._run(record["run_id"])
            if record["provider"] not in run.get("providers", []):
                raise ValueError("FACT_PROVIDER_MISMATCH")
            self._one(
                "SELECT artifact_id FROM run_artifacts "
                "WHERE artifact_id=%s AND run_id=%s AND system_namespace=%s",
                (record["artifact_id"], record["run_id"], self.namespace),
            )
            for fact in record["facts"]:
                if fact["fact_id"] != record_id("fact_", fact, "fact_id"):
                    raise ValueError("INVALID_FACT_ID")
                evidence = [self._evidence(eid, record["run_id"]) for eid in fact["evidence_ids"]]
                for span in fact["source_locations"]:
                    if span["artifact_id"] != record["artifact_id"] or not any(
                        span["artifact_id"] == cited["artifact_id"]
                        and span["coordinate_space"] == cited["coordinate_space"]
                        and span["origin_map_id"] == cited["origin_map_id"]
                        and span["byte_start"] is not None
                        and span["byte_end"] is not None
                        and cited["byte_start"] is not None
                        and cited["byte_end"] is not None
                        and cited["byte_start"]
                        <= span["byte_start"]
                        <= span["byte_end"]
                        <= cited["byte_end"]
                        and (cited["start_line"], cited["start_column"])
                        <= (span["start_line"], span["start_column"])
                        <= (span["end_line"], span["end_column"])
                        <= (cited["end_line"], cited["end_column"])
                        for item in evidence
                        for cited in item["source_locations"]
                    ):
                        raise ValueError("UNCITED_SOURCE_SPAN")
                data = fact["data"]
                if fact["kind"] == "reference":
                    for target in [data["target_entity_id"], *data["candidate_entity_ids"]]:
                        if target is not None:
                            self._run_entity(target, record["run_id"])
            self._insert(
                "analyzer_fact_bundles",
                "bundle_id",
                {
                    "bundle_id": bundle_id,
                    "run_id": record["run_id"],
                    "artifact_id": record["artifact_id"],
                    "system_namespace": self.namespace,
                    "payload": record,
                },
            )
        return bundle_id

    def rule_support_fingerprint(self, record: dict[str, Any]) -> str:
        """Recompute versioned support from closed persisted run observations/evidence."""
        from laip.claim_support import validate_claim_support

        support = validate_claim_support(record["claim_support"])
        if (
            support["subject_entity_ids"] != record["subject_entity_ids"]
            or sorted(support["evidence_ids"]) != sorted(record["evidence_ids"])
            or support["controlling_predicates"] != record["conditions"]
        ):
            raise ValueError("CLAIM_SUPPORT_MISMATCH")
        run = self._run(record["run_id"])
        subjects = []
        for subject in record["subject_entity_ids"]:
            if self._run_entity(subject, record["run_id"]) not in (
                "SourceMember",
                "RecordFormat",
                "Field",
                "Screen",
                "Program",
                "Procedure",
            ):
                raise ValueError("INVALID_SUBJECT_KIND")
            observations = self.connection.execute(
                "SELECT observation_digest FROM entity_observations "
                "WHERE entity_id=%s AND run_id=%s AND system_namespace=%s "
                "ORDER BY observation_digest",
                (subject, record["run_id"], self.namespace),
            ).fetchall()
            subjects.append({"entity_id": subject, "observations": [o[0] for o in observations]})
        closed: dict[str, Any] = {}
        pending = list(record["evidence_ids"])
        while pending:
            if len(closed) > 20000:
                raise ValueError("SUPPORT_LIMIT")
            eid = pending.pop()
            if eid in closed:
                continue
            evidence = self._evidence(eid, record["run_id"])
            closed[eid] = {
                "evidence_id": eid,
                "content_sha256": evidence["content_sha256"],
                "record_sha256": digest(evidence),
            }
            pending.extend(evidence["supporting_evidence_ids"])
            pending.extend(evidence["contradiction_evidence_ids"])
        for decision in support["resolution_decisions"]:
            if decision["target_entity_id"] is not None:
                self._run_entity(decision["target_entity_id"], record["run_id"])
        fact_ids = [
            *support["symbol_dependencies"],
            *support["effect_dependencies"],
            *(decision["reference"] for decision in support["resolution_decisions"]),
        ]
        facts = {}
        if fact_ids:
            for row in self.connection.execute(
                "SELECT payload FROM analyzer_fact_bundles WHERE run_id=%s AND system_namespace=%s",
                (record["run_id"], self.namespace),
            ).fetchall():
                for fact in row[0]["facts"]:
                    if fact["fact_id"] in fact_ids:
                        facts[fact["fact_id"]] = fact
            if any(fid not in facts for fid in fact_ids) or any(
                facts[fid]["kind"] != kind
                for key, kind in (
                    ("symbol_dependencies", "symbol"),
                    ("effect_dependencies", "effect"),
                )
                for fid in support[key]
            ):
                raise ValueError("UNRESOLVED_FACT_SUPPORT")
        for decision in support["resolution_decisions"]:
            fact = facts[decision["reference"]]
            if (
                fact["kind"] != "reference"
                or fact["data"]["resolution"] != decision["resolution"]
                or fact["data"]["target_entity_id"] != decision["target_entity_id"]
            ):
                raise ValueError("RESOLUTION_SUPPORT_MISMATCH")
        if any(not set(fact["evidence_ids"]).issubset(closed) for fact in facts.values()):
            raise ValueError("UNCITED_FACT_SUPPORT")
        basis = {
            "support_schema_version": "0.2.0",
            "claim_support": support,
            "evidence": [closed[key] for key in sorted(closed)],
            "subjects": sorted(subjects, key=lambda item: item["entity_id"]),
            "facts": [facts[key] for key in sorted(facts)],
            "providers": sorted(run.get("providers", []), key=lambda p: (p["id"], p["version"])),
            "configuration_sha256": run["configuration_sha256"],
        }
        return digest(basis)

    def _verify_claim_effects(self, record: dict[str, Any]) -> None:
        ids = set(record["claim_support"]["effect_dependencies"])
        if not ids:
            return
        for row in self.connection.execute(
            "SELECT payload FROM analyzer_fact_bundles WHERE run_id=%s AND system_namespace=%s",
            (record["run_id"], self.namespace),
        ).fetchall():
            for fact in row[0]["facts"]:
                if fact["fact_id"] in ids and fact["data"].get("unknown"):
                    raise ValueError("UNRESOLVED_SUPPORT")

    def put_rule(self, record: dict[str, Any], expected_revision_id: str | None) -> None:
        record = validate("RuleRevision", record)
        if (
            record["revision_id"] != record_id("rev_", record, "revision_id")
            or record["previous_revision_id"] != expected_revision_id
        ):
            raise ValueError("INVALID_REVISION")
        with self.connection.transaction():
            self._run(record["run_id"])
            if self._entity(record["rule_entity_id"]) != "BusinessRule":
                raise ValueError("INVALID_RULE_KIND")
            self.connection.execute(
                "SELECT entity_id FROM entities WHERE entity_id=%s FOR UPDATE",
                (record["rule_entity_id"],),
            )
            head = self.connection.execute(
                "SELECT revision_id FROM rule_heads WHERE rule_entity_id=%s",
                (record["rule_entity_id"],),
            ).fetchone()
            if head and head[0] == record["revision_id"]:
                self._one(
                    "SELECT revision_id FROM rule_revisions WHERE revision_id=%s AND payload=%s",
                    (record["revision_id"], Jsonb(record)),
                )
                return
            if (head[0] if head else None) != expected_revision_id:
                raise ValueError("STALE_REVISION")
            evidence = [self._evidence(eid, record["run_id"]) for eid in record["evidence_ids"]]
            run = self._run(record["run_id"])
            support = {
                "evidence": sorted(
                    [
                        {"evidence_id": e["evidence_id"], "content_sha256": e["content_sha256"]}
                        for e in evidence
                    ],
                    key=lambda e: e["evidence_id"],
                ),
                "providers": sorted(
                    run.get("providers", []), key=lambda p: (p["id"], p["version"])
                ),
                "resolution_decisions": [],
                "configuration_sha256": run["configuration_sha256"],
            }
            expected_support = (
                self.rule_support_fingerprint(record)
                if record["schema_version"] == "0.2.0"
                else digest(support)
            )
            if expected_support != record["support_fingerprint"]:
                raise ValueError("INVALID_SUPPORT_FINGERPRINT")
            if record["schema_version"] == "0.2.0":
                from laip.rule_store import _validate_rule_spans

                _validate_rule_spans(record, self)
            self._insert(
                "rule_revisions",
                "revision_id",
                {
                    "revision_id": record["revision_id"],
                    "rule_entity_id": record["rule_entity_id"],
                    "run_id": record["run_id"],
                    "system_namespace": self.namespace,
                    "previous_revision_id": expected_revision_id,
                    "record_sha256": digest(record),
                    "support_fingerprint": record["support_fingerprint"],
                    "payload": record,
                },
            )
            for eid in record["evidence_ids"]:
                self.connection.execute(
                    "INSERT INTO rule_evidence VALUES(%s,%s,%s)",
                    (record["revision_id"], eid, self.namespace),
                )
            for key, kind in [
                ("application_entity_ids", "Application"),
                ("program_entity_ids", "Program"),
                ("procedure_entity_ids", "Procedure"),
            ]:
                for eid in record[key]:
                    actual_kind = (
                        self._run_entity(eid, record["run_id"])
                        if record["schema_version"] == "0.2.0"
                        else self._entity(eid)
                    )
                    if actual_kind != kind:
                        raise ValueError("INVALID_SCOPE_KIND")
                    self.connection.execute(
                        "INSERT INTO rule_scopes VALUES(%s,%s,%s,%s)",
                        (record["revision_id"], eid, self.namespace, kind),
                    )
            if record["schema_version"] == "0.2.0":
                for subject in record["subject_entity_ids"]:
                    self.connection.execute(
                        "INSERT INTO rule_subjects VALUES(%s,%s,%s)",
                        (record["revision_id"], subject, self.namespace),
                    )
            self.connection.execute(
                "INSERT INTO rule_heads VALUES(%s,%s) ON CONFLICT(rule_entity_id) DO UPDATE "
                "SET revision_id=excluded.revision_id",
                (record["rule_entity_id"], record["revision_id"]),
            )

    def review(
        self,
        subject_type: str,
        subject_id: str,
        expected_review_id: str | None,
        status: str,
        actor_id: str,
        reason: str,
    ) -> str:
        targets = {
            "evidence": ("evidence", "evidence_id"),
            "dependency": ("dependencies", "dependency_id"),
            "rule_revision": ("rule_revisions", "revision_id"),
        }
        if subject_type not in targets or not actor_id or not reason:
            raise ValueError("INVALID_REVIEW")
        table, column = targets[subject_type]
        review_id = "review_" + uuid.uuid4().hex
        with self.connection.transaction():
            self._one(
                "SELECT system_namespace FROM systems WHERE system_namespace=%s FOR UPDATE",
                (self.namespace,),
            )
            target = self.connection.execute(
                sql.SQL(
                    "SELECT payload FROM {} WHERE {}=%s AND system_namespace=%s FOR UPDATE"
                ).format(sql.Identifier(table), sql.Identifier(column)),
                (subject_id, self.namespace),
            ).fetchone()
            if target is None:
                raise ValueError("NOT_FOUND_OR_CROSS_NAMESPACE")
            if (
                subject_type == "rule_revision"
                and status == "verified"
                and target[0].get("schema_version") == "0.2.0"
            ):
                from laip.rule_store import _resolved_support, _validate_rule_spans

                _validate_rule_spans(target[0], self)
                _resolved_support(target[0], self)
                if self.rule_support_fingerprint(target[0]) != target[0]["support_fingerprint"]:
                    raise ValueError("INVALID_SUPPORT_FINGERPRINT")
            head = self.connection.execute(
                "SELECT review_id FROM review_heads WHERE subject_type=%s AND subject_id=%s",
                (subject_type, subject_id),
            ).fetchone()
            if (head[0] if head else None) != expected_review_id:
                raise ValueError("STALE_REVIEW")
            self.connection.execute(
                "INSERT INTO "
                "reviews(review_id,subject_type,subject_id,system_namespace,"
                "evidence_id,dependency_id,revision_id,subject_fingerprint,"
                "previous_review_id,status,actor_id,reason) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    review_id,
                    subject_type,
                    subject_id,
                    self.namespace,
                    subject_id if subject_type == "evidence" else None,
                    subject_id if subject_type == "dependency" else None,
                    subject_id if subject_type == "rule_revision" else None,
                    digest(target[0]),
                    expected_review_id,
                    status,
                    actor_id,
                    reason,
                ),
            )
            self.connection.execute(
                "INSERT INTO review_heads VALUES(%s,%s,%s) ON "
                "CONFLICT(subject_type,subject_id) DO UPDATE SET review_id=excluded.review_id",
                (subject_type, subject_id, review_id),
            )
        return review_id

    def _snapshot_closure(self, manifest: dict[str, list[str]]) -> None:
        selected_evidence = set(manifest.get("evidence", []))
        observation_payloads = self.connection.execute(
            "SELECT payload FROM entity_observations WHERE observation_id=ANY(%s) AND "
            "system_namespace=%s",
            (manifest.get("observations", []), self.namespace),
        ).fetchall()
        required_evidence: set[str] = {
            eid for row in observation_payloads for eid in row[0].get("evidence_ids", [])
        }
        required_entities: set[str] = set()
        for table, column, ids in [
            ("rule_evidence", "revision_id", manifest.get("revisions", [])),
            ("dependency_evidence", "dependency_id", manifest.get("dependencies", [])),
            ("evidence_support", "evidence_id", manifest.get("evidence", [])),
            ("evidence_contradictions", "evidence_id", manifest.get("evidence", [])),
        ]:
            evidence_column = (
                "supporting_id"
                if table == "evidence_support"
                else "contradicting_id"
                if table == "evidence_contradictions"
                else "evidence_id"
            )
            rows = self.connection.execute(
                sql.SQL("SELECT {} FROM {} WHERE {}=ANY(%s) AND system_namespace=%s").format(
                    sql.Identifier(evidence_column), sql.Identifier(table), sql.Identifier(column)
                ),
                (ids, self.namespace),
            ).fetchall()
            required_evidence.update(row[0] for row in rows)
        if not required_evidence.issubset(selected_evidence):
            raise ValueError("INVALID_SNAPSHOT_CITATION_CLOSURE")
        for table, column, ids in [
            ("rule_scopes", "revision_id", manifest.get("revisions", [])),
            ("rule_subjects", "revision_id", manifest.get("revisions", [])),
            ("dependency_candidates", "dependency_id", manifest.get("dependencies", [])),
            ("evidence_subjects", "evidence_id", manifest.get("evidence", [])),
        ]:
            required_entities.update(
                row[0]
                for row in self.connection.execute(
                    sql.SQL(
                        "SELECT entity_id FROM {} WHERE {}=ANY(%s) AND system_namespace=%s"
                    ).format(sql.Identifier(table), sql.Identifier(column)),
                    (ids, self.namespace),
                ).fetchall()
            )
        required_entities.update(
            row[0]
            for row in self.connection.execute(
                "SELECT rule_entity_id FROM rule_revisions WHERE revision_id=ANY(%s) "
                "AND system_namespace=%s",
                (manifest.get("revisions", []), self.namespace),
            ).fetchall()
        )
        for source, target in self.connection.execute(
            "SELECT from_entity_id,to_entity_id FROM dependencies WHERE "
            "dependency_id=ANY(%s) AND system_namespace=%s",
            (manifest.get("dependencies", []), self.namespace),
        ).fetchall():
            required_entities.add(source)
            if target:
                required_entities.add(target)
        observations = self.connection.execute(
            "SELECT o.entity_id,e.parent_id FROM entity_observations o JOIN entities "
            "e USING(entity_id) WHERE o.observation_id=ANY(%s) AND "
            "o.system_namespace=%s",
            (manifest.get("observations", []), self.namespace),
        ).fetchall()
        required_entities.update(row[1] for row in observations if row[1])
        if not required_entities.issubset({row[0] for row in observations}):
            raise ValueError("INVALID_SNAPSHOT_ENTITY_CLOSURE")

    def publish_snapshot(
        self,
        snapshot_id: str,
        run_id: str,
        manifest: dict[str, list[str]],
        promote: bool = False,
        *,
        schema_version: str = "0.1.0",
        record_versions: list[str] | None = None,
    ) -> None:
        from laip.schema_registry import schema_for_version

        schema_for_version(schema_version)
        allowed = {
            "observations": ("entity_observations", "observation_id"),
            "evidence": ("evidence", "evidence_id"),
            "dependencies": ("dependencies", "dependency_id"),
            "revisions": ("rule_revisions", "revision_id"),
        }
        if set(manifest) - set(allowed):
            raise ValueError("INVALID_MANIFEST")
        with self.connection.transaction():
            self._one(
                "SELECT system_namespace FROM systems WHERE system_namespace=%s FOR UPDATE",
                (self.namespace,),
            )
            self._snapshot_closure(manifest)
            actual_versions: set[str] = set()
            for kind, ids in manifest.items():
                table, column = allowed[kind]
                rows = self.connection.execute(
                    sql.SQL(
                        "SELECT payload FROM {} WHERE {}=ANY(%s) "
                        "AND system_namespace=%s AND run_id=%s"
                    ).format(sql.Identifier(table), sql.Identifier(column)),
                    (ids, self.namespace, run_id),
                ).fetchall()
                actual_versions.update(row[0].get("schema_version", "0.1.0") for row in rows)
            for version in actual_versions:
                schema_for_version(version)
            if schema_version == "0.1.0" and actual_versions - {"0.1.0"}:
                raise ValueError("INCOMPATIBLE_SNAPSHOT_VERSION")
            if record_versions is not None and sorted(set(record_versions)) != sorted(
                actual_versions
            ):
                raise ValueError("SNAPSHOT_RECORD_VERSION_MISMATCH")
            payload: dict[str, Any] = dict(manifest)
            if schema_version == "0.2.0":
                payload.update(
                    schema_version=schema_version, record_versions=sorted(actual_versions)
                )
            state = self._one(
                "SELECT state FROM runs WHERE run_id=%s AND system_namespace=%s FOR UPDATE",
                (run_id, self.namespace),
            )[0]
            if state not in ("succeeded", "partial") or (promote and state != "succeeded"):
                raise ValueError("RUN_NOT_PUBLISHABLE")
            existing = self.connection.execute(
                "SELECT manifest_sha256,run_id FROM snapshots "
                "WHERE snapshot_id=%s AND system_namespace=%s",
                (snapshot_id, self.namespace),
            ).fetchone()
            if existing:
                if existing != (digest(payload), run_id):
                    raise ValueError("IDENTITY_PAYLOAD_MISMATCH")
                return
            watermark = self.connection.execute(
                "SELECT coalesce(max(sequence),0) FROM reviews WHERE system_namespace=%s",
                (self.namespace,),
            ).fetchone()[0]  # type: ignore[index]
            inserted = self._insert(
                "snapshots",
                "snapshot_id",
                {
                    "snapshot_id": snapshot_id,
                    "system_namespace": self.namespace,
                    "run_id": run_id,
                    "manifest_sha256": digest(payload),
                    "payload": payload,
                    "review_sequence": watermark,
                },
            )
            if not inserted:
                return
            for kind, ids in manifest.items():
                table, column = allowed[kind]
                for ident in ids:
                    row = self.connection.execute(
                        sql.SQL(
                            "SELECT {} FROM {} WHERE {}=%s AND system_namespace=%s AND run_id=%s"
                        ).format(
                            sql.Identifier(column), sql.Identifier(table), sql.Identifier(column)
                        ),
                        (ident, self.namespace, run_id),
                    ).fetchone()
                    if row is None:
                        raise ValueError("INVALID_SNAPSHOT_LINEAGE")
                    self.connection.execute(
                        sql.SQL(
                            "INSERT INTO snapshot_members(snapshot_id,system_namespace,{}) "
                            "VALUES(%s,%s,%s)"
                        ).format(sql.Identifier(column)),
                        (snapshot_id, self.namespace, ident),
                    )
            if promote:
                self.connection.execute(
                    "INSERT INTO namespace_heads VALUES(%s,%s) ON CONFLICT(system_namespace) DO "
                    "UPDATE SET snapshot_id=excluded.snapshot_id",
                    (self.namespace, snapshot_id),
                )

    def put_chunk(
        self,
        chunk_id: str,
        snapshot_id: str,
        text: str,
        evidence_ids: list[str],
        policy_version: str,
        provider_version: str,
        tokenizer_version: str,
    ) -> None:
        if not evidence_ids or len(text) > 100000 or not text:
            raise ValueError("INVALID_CHUNK")
        content = {"text": text, "evidence_ids": sorted(set(evidence_ids))}
        with self.connection.transaction():
            for eid in evidence_ids:
                self._one(
                    "SELECT evidence_id FROM snapshot_members WHERE snapshot_id=%s AND "
                    "system_namespace=%s AND evidence_id=%s",
                    (snapshot_id, self.namespace, eid),
                )
            inserted = self._insert(
                "chunks",
                "chunk_id",
                {
                    "chunk_id": chunk_id,
                    "snapshot_id": snapshot_id,
                    "system_namespace": self.namespace,
                    "policy_version": policy_version,
                    "provider_version": provider_version,
                    "tokenizer_version": tokenizer_version,
                    "fingerprint": digest(content),
                    "text_content": text,
                },
            )
            if inserted:
                for eid in set(evidence_ids):
                    self.connection.execute(
                        "INSERT INTO chunk_links VALUES(%s,%s)", (chunk_id, eid)
                    )

    def search(
        self, snapshot_id: str, policy_version: str, query: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100 or len(query) > 1000:
            raise ValueError("INVALID_SEARCH_LIMIT")
        rows = self.connection.execute(
            "SELECT chunk_id,text_content FROM chunks WHERE system_namespace=%s AND "
            "snapshot_id=%s AND policy_version=%s AND search_vector @@ "
            "plainto_tsquery('simple',%s) ORDER BY "
            "ts_rank(search_vector,plainto_tsquery('simple',%s)) DESC,chunk_id LIMIT %s",
            (self.namespace, snapshot_id, policy_version, query, query, limit),
        ).fetchall()
        return [{"chunk_id": row[0], "text": row[1]} for row in rows]
