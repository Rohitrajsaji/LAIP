"""Immutable source-backed rule publication and analyst review operations."""

from typing import Any, cast

from psycopg.types.json import Jsonb

from laip.canonical import canonical, digest
from laip.persistence import Repository, validate


def register_bundle(
    run_id: str,
    import_id: str,
    configuration_sha256: str,
    providers: list[dict[str, Any]],
    repository: Repository,
) -> None:
    basis = {
        "stage": "rule_extraction",
        "configuration_sha256": configuration_sha256,
        "providers": providers,
    }
    if len(configuration_sha256) != 64 or any(
        c not in "0123456789abcdef" for c in configuration_sha256
    ):
        raise ValueError("INVALID_CONFIGURATION")
    for provider in providers:
        validate("ProviderRef", provider)
    with repository.connection.transaction():
        repository._one(
            "SELECT system_namespace FROM systems WHERE system_namespace=%s FOR UPDATE",
            (repository.namespace,),
        )
        imported = repository.connection.execute(
            "SELECT state FROM imports WHERE import_id=%s AND system_namespace=%s FOR SHARE",
            (import_id, repository.namespace),
        ).fetchone()
        if imported is None or imported[0] not in ("sealed", "partial"):
            raise ValueError("IMPORT_NOT_READY")
        old_run = repository.connection.execute(
            "SELECT import_id,payload FROM runs WHERE run_id=%s AND system_namespace=%s",
            (run_id, repository.namespace),
        ).fetchone()
        if old_run is None:
            raise ValueError("RULE_BASIS_MISSING")
        elif (
            old_run[0] != import_id
            or old_run[1].get("configuration_sha256") != configuration_sha256
            or old_run[1].get("providers") != providers
        ):
            raise ValueError("RULE_BASIS_MISMATCH")
        old = repository.connection.execute(
            "SELECT payload FROM rule_bundle_bases WHERE run_id=%s", (run_id,)
        ).fetchone()
        if old is not None:
            if old[0] != basis:
                raise ValueError("RULE_BASIS_MISMATCH")
        else:
            repository.connection.execute(
                "INSERT INTO rule_bundle_bases VALUES(%s,%s,%s)",
                (run_id, repository.namespace, Jsonb(basis)),
            )


def persist_bundle(
    run_id: str,
    rules: dict[str, Any],
    workflows: dict[str, Any],
    repository: Repository,
    *,
    expected_missing_heads: dict[str, tuple[str, str | None]] | None = None,
) -> dict[str, Any]:
    """Database-only publication: invoke within an active job's fenced checkpoint."""
    with repository.connection.transaction():
        repository._one(
            "SELECT system_namespace FROM systems WHERE system_namespace=%s FOR UPDATE",
            (repository.namespace,),
        )
        basis = repository.connection.execute(
            "SELECT payload FROM rule_bundle_bases WHERE run_id=%s AND system_namespace=%s",
            (run_id, repository.namespace),
        ).fetchone()
        if basis is None:
            raise ValueError("RULE_BASIS_MISSING")
        run = repository._run(run_id)
        if any(run.get(key) != basis[0][key] for key in ("providers", "configuration_sha256")):
            raise ValueError("RULE_BASIS_MISMATCH")
        if len(canonical([rules, workflows])) > 32 * 1024 * 1024:
            raise ValueError("RULE_OUTPUT_LIMIT")
        all_entities = rules["entities"] + workflows["entities"]
        if (
            len(all_entities) > 20000
            or len(rules["rule_revisions"]) > 20000
            or len(workflows["workflows"]) > 20000
        ):
            raise ValueError("RULE_OUTPUT_LIMIT")
        pending = {entity["entity_id"]: entity for entity in all_entities}
        if len(pending) != len(all_entities):
            raise ValueError("DUPLICATE_ENTITY")
        while pending:
            ready = [
                pending[key] for key in sorted(pending) if pending[key]["parent_id"] not in pending
            ]
            if not ready:
                raise ValueError("ENTITY_PARENT_CYCLE")
            for entity in ready:
                repository.put_entity(entity, run_id)
                del pending[entity["entity_id"]]
        for revision in sorted(rules["rule_revisions"], key=lambda value: value["rule_entity_id"]):
            if revision["run_id"] != run_id:
                raise ValueError("RULE_RUN_MISMATCH")
            _validate_rule_spans(revision, repository)
            repository.put_rule(revision, revision["previous_revision_id"])
        for workflow in workflows["workflows"]:
            workflow_id, entity_id = _validate_workflow(workflow, run_id, repository)
            old = repository.connection.execute(
                "SELECT payload FROM workflow_revisions WHERE workflow_id=%s", (workflow_id,)
            ).fetchone()
            if old is not None:
                if old[0] != workflow:
                    raise ValueError("WORKFLOW_COLLISION")
            else:
                repository.connection.execute(
                    "INSERT INTO workflow_revisions VALUES(%s,%s,%s,%s,%s)",
                    (workflow_id, run_id, entity_id, repository.namespace, Jsonb(workflow)),
                )
        if expected_missing_heads:
            present = {revision["rule_entity_id"] for revision in rules["rule_revisions"]}
            if present & expected_missing_heads.keys():
                raise ValueError("INVALID_MISSING_SCOPE")
            invalidate_scope(
                expected_missing_heads,
                "system:rule-extraction",
                f"Candidate absent from explicit reanalysis scope in {run_id}",
                repository,
            )
    return {
        "run_id": run_id,
        "rule_count": len(rules["rule_revisions"]),
        "workflow_count": len(workflows["workflows"]),
        "outcome": "partial"
        if rules.get("diagnostics") or workflows.get("diagnostics")
        else "succeeded",
    }


def _current_locked(
    rule_entity_id: str, expected_revision_id: str, repository: Repository
) -> dict[str, Any]:
    repository.connection.execute(
        "SELECT system_namespace FROM systems WHERE system_namespace=%s FOR UPDATE",
        (repository.namespace,),
    )
    repository._one(
        "SELECT entity_id FROM entities WHERE entity_id=%s AND system_namespace=%s FOR UPDATE",
        (rule_entity_id, repository.namespace),
    )
    row = repository.connection.execute(
        "SELECT r.payload FROM rule_heads h JOIN rule_revisions r USING(revision_id) "
        "WHERE h.rule_entity_id=%s AND r.system_namespace=%s FOR UPDATE OF r",
        (rule_entity_id, repository.namespace),
    ).fetchone()
    if row is None:
        raise ValueError("RULE_NOT_FOUND")
    if row[0]["revision_id"] != expected_revision_id:
        raise ValueError("STALE_REVISION")
    return cast(dict[str, Any], row[0])


def _resolved_support(rule: dict[str, Any], repository: Repository) -> None:
    if rule.get("schema_version") == "0.2.0":
        support = rule["claim_support"]
        repository._verify_claim_effects(rule)
        if (
            support["state"] != "supported_within_profile"
            or support["barriers"]
            or any(d["resolution"] != "resolved" for d in support["resolution_decisions"])
            or repository.rule_support_fingerprint(rule) != rule["support_fingerprint"]
        ):
            raise ValueError("UNRESOLVED_SUPPORT")
        if support["symbol_dependencies"] or support["effect_dependencies"]:
            # Fact-closed 0.2 support is claim-local. The legacy file-global
            # conclusions flag does not override a closed, barrier-free slice.
            pending = list(rule["evidence_ids"])
            seen = set()
            while pending:
                eid = pending.pop()
                if eid in seen:
                    continue
                seen.add(eid)
                if len(seen) > 20000:
                    raise ValueError("SUPPORT_LIMIT")
                evidence = repository._evidence(eid, rule["run_id"])
                if (
                    evidence["classification"] == "unresolved"
                    or evidence["contradiction_evidence_ids"]
                    or evidence["metadata"].get("barrier")
                ):
                    raise ValueError("UNRESOLVED_SUPPORT")
                if not evidence["supporting_evidence_ids"] and (
                    evidence["classification"] != "observed"
                    or evidence["authority"] != "source"
                    or not evidence["source_locations"]
                ):
                    raise ValueError("UNRESOLVED_SUPPORT")
                pending.extend(evidence["supporting_evidence_ids"])
            if any(
                expression["node"] == "opaque"
                for expression in [*rule["conditions"], *rule["actions"]]
            ):
                raise ValueError("UNRESOLVED_SUPPORT")
            return
    if rule["classification"] == "unresolved" or any(
        limitation == "Unknown constructs or effects prevent complete business conclusions."
        for limitation in rule["limitations"]
    ):
        raise ValueError("UNRESOLVED_SUPPORT")
    if any(
        expression["node"] == "opaque" for expression in [*rule["conditions"], *rule["actions"]]
    ):
        raise ValueError("UNRESOLVED_SUPPORT")
    if any(not isinstance(action.get("value"), dict) for action in rule["actions"]):
        raise ValueError("UNRESOLVED_SUPPORT")
    if any(
        action["value"].get("operation")
        in ("call", "callp", "exsr", "submit_job", "submitted", "submit")
        for action in rule["actions"]
    ):
        raise ValueError("UNRESOLVED_SUPPORT")
    _verify_support(rule["evidence_ids"], rule["run_id"], repository)


def _verify_support(
    evidence_ids: list[str], run_id: str, repository: Repository, *, workflow: bool = False
) -> set[str]:
    colors: dict[str, int] = {}
    stack = [(eid, False) for eid in evidence_ids]
    work = 0
    while stack:
        eid, exiting = stack.pop()
        work += 1
        if work > 100000 or len(colors) > 20000:
            raise ValueError("SUPPORT_LIMIT")
        if exiting:
            colors[eid] = 2
            continue
        if colors.get(eid) == 1:
            raise ValueError("UNRESOLVED_SUPPORT")
        if colors.get(eid) == 2:
            continue
        colors[eid] = 1
        evidence = repository._evidence(eid, run_id)
        if evidence["classification"] == "unresolved" or evidence["contradiction_evidence_ids"]:
            raise ValueError("UNRESOLVED_SUPPORT")
        metadata = evidence["metadata"]
        if metadata.get("barrier") or metadata.get("conclusions_allowed") is False:
            raise ValueError("UNRESOLVED_SUPPORT")
        for key in ("references", "relationships", "calls"):
            for reference in metadata.get(key, []):
                if reference.get("dynamic") or (
                    reference.get("resolution") in ("unresolved", "dynamic", "ambiguous", "missing")
                    if workflow
                    else reference.get("resolution") != "resolved"
                ):
                    raise ValueError("UNRESOLVED_SUPPORT")
        supports = evidence["supporting_evidence_ids"]
        if not supports and (
            evidence["classification"] != "observed"
            or evidence["authority"]
            not in ("source", "imported_metadata", "historical_reference", "system_service")
            if workflow
            else (
                evidence["classification"] != "observed"
                or evidence["authority"] != "source"
                or not evidence["source_locations"]
            )
        ):
            raise ValueError("UNRESOLVED_SUPPORT")
        stack.append((eid, True))
        stack.extend((support, False) for support in supports)
    if not colors:
        raise ValueError("UNRESOLVED_SUPPORT")
    return set(colors)


def approve_rule(
    rule_entity_id: str,
    expected_revision_id: str,
    expected_review_id: str | None,
    actor_id: str,
    reason: str,
    repository: Repository,
) -> str:
    with repository.connection.transaction():
        rule = _current_locked(rule_entity_id, expected_revision_id, repository)
        _validate_rule_spans(rule, repository)
        _resolved_support(rule, repository)
        return repository.review(
            "rule_revision", expected_revision_id, expected_review_id, "verified", actor_id, reason
        )


def reject_rule(
    rule_entity_id: str,
    expected_revision_id: str,
    expected_review_id: str | None,
    actor_id: str,
    reason: str,
    repository: Repository,
) -> str:
    with repository.connection.transaction():
        _current_locked(rule_entity_id, expected_revision_id, repository)
        return repository.review(
            "rule_revision", expected_revision_id, expected_review_id, "rejected", actor_id, reason
        )


def correct_rule(
    revision: dict[str, Any],
    expected_revision_id: str,
    expected_review_id: str | None,
    actor_id: str,
    reason: str,
    repository: Repository,
) -> str:
    with repository.connection.transaction():
        _current_locked(revision["rule_entity_id"], expected_revision_id, repository)
        if (
            revision["interpretation_method"] != "analyst"
            or revision["classification"] == "observed"
        ):
            raise ValueError("INVALID_CORRECTION")
        _validate_rule_spans(revision, repository)
        repository.review(
            "rule_revision",
            expected_revision_id,
            expected_review_id,
            "pending_review",
            actor_id,
            reason,
        )
        repository.put_rule(revision, expected_revision_id)
        return repository.review(
            "rule_revision", revision["revision_id"], None, "pending_review", actor_id, reason
        )


def current_rule(rule_entity_id: str, repository: Repository) -> dict[str, Any]:
    row = repository.connection.execute(
        "SELECT r.payload,v.review_id,v.status FROM rule_heads h "
        "JOIN rule_revisions r USING(revision_id) "
        "LEFT JOIN review_heads rh ON rh.subject_type='rule_revision' "
        "AND rh.subject_id=r.revision_id "
        "LEFT JOIN reviews v ON v.review_id=rh.review_id "
        "WHERE h.rule_entity_id=%s AND r.system_namespace=%s",
        (rule_entity_id, repository.namespace),
    ).fetchone()
    if row is None:
        raise ValueError("RULE_NOT_FOUND")
    return {"revision": row[0], "review_id": row[1], "review_status": row[2] or "pending_review"}


def review_history(rule_entity_id: str, repository: Repository) -> list[dict[str, Any]]:
    return [
        {
            "review_id": row[0],
            "revision_id": row[1],
            "status": row[2],
            "actor_id": row[3],
            "reason": row[4],
        }
        for row in repository.connection.execute(
            "SELECT v.review_id,v.revision_id,v.status,v.actor_id,v.reason FROM reviews v "
            "JOIN rule_revisions r USING(revision_id) "
            "WHERE r.rule_entity_id=%s AND r.system_namespace=%s ORDER BY v.sequence",
            (rule_entity_id, repository.namespace),
        ).fetchall()
    ]


def invalidate_scope(
    expected_heads: dict[str, tuple[str, str | None]],
    actor_id: str,
    reason: str,
    repository: Repository,
) -> None:
    """Explicit, atomic missing-candidate invalidation for a declared reanalysis scope."""
    if len(expected_heads) > 20000 or not expected_heads:
        raise ValueError("INVALID_SCOPE")
    with repository.connection.transaction():
        for entity_id, (revision_id, review_id) in sorted(expected_heads.items()):
            _current_locked(entity_id, revision_id, repository)
            repository.review(
                "rule_revision", revision_id, review_id, "pending_review", actor_id, reason
            )


def _validate_workflow(
    workflow: dict[str, Any], run_id: str, repository: Repository
) -> tuple[str, str]:
    if workflow.get("schema_version") == "0.2.0":
        return _validate_workflow_v2(workflow, run_id, repository)
    keys = {
        "schema_version",
        "workflow_id",
        "workflow_entity_id",
        "run_id",
        "program_entity_ids",
        "dependency_ids",
        "evidence_ids",
        "classification",
        "review_status",
        "limitations",
        "created_at",
    }
    if (
        set(workflow) != keys
        or workflow["schema_version"] != "0.1.0"
        or workflow["run_id"] != run_id
        or workflow["classification"] != "inferred"
        or workflow["review_status"] != "pending_review"
        or workflow["workflow_id"]
        != "wf_" + digest({k: v for k, v in workflow.items() if k != "workflow_id"})
    ):
        raise ValueError("INVALID_WORKFLOW")
    from datetime import datetime

    try:
        if datetime.fromisoformat(workflow["created_at"].replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("INVALID_WORKFLOW")
    except (TypeError, AttributeError, ValueError):
        raise ValueError("INVALID_WORKFLOW") from None
    programs = workflow["program_entity_ids"]
    if (
        not 2 <= len(programs) <= 128
        or len(programs) != len(set(programs))
        or len(workflow["dependency_ids"]) != len(programs) - 1
    ):
        raise ValueError("INVALID_WORKFLOW")
    identity = {
        "system_namespace": repository.namespace,
        "kind": "Workflow",
        "qualified_identity": ["possible-call-path", *programs],
    }
    from laip.canonical import identity_id as canonical_entity_id

    entity_id = canonical_entity_id(identity)
    if workflow["workflow_entity_id"] != entity_id or repository._entity(entity_id) != "Workflow":
        raise ValueError("INVALID_WORKFLOW_IDENTITY")
    if not workflow["limitations"] or any(
        not isinstance(value, str) or not value for value in workflow["limitations"]
    ):
        raise ValueError("INVALID_WORKFLOW")
    for program in programs:
        program_row = repository._one(
            "SELECT identity FROM entities WHERE entity_id=%s AND system_namespace=%s",
            (program, repository.namespace),
        )[0]
        parts = program_row["qualified_identity"]
        if repository._entity(program) != "Program" or len(parts) != 3 or parts[1] != "*PGM":
            raise ValueError("INVALID_WORKFLOW_PROGRAM")
    support: set[str] = set()
    for index, dependency_id in enumerate(workflow["dependency_ids"]):
        row = repository.connection.execute(
            "SELECT payload FROM dependencies WHERE dependency_id=%s "
            "AND run_id=%s AND system_namespace=%s",
            (dependency_id, run_id, repository.namespace),
        ).fetchone()
        if row is None:
            raise ValueError("INVALID_WORKFLOW_DEPENDENCY")
        edge = row[0]
        if (
            edge["relationship"] != "CALLS"
            or edge["classification"] == "unresolved"
            or edge["resolution"] != "resolved"
            or edge["from_entity_id"] != programs[index]
            or edge["to_entity_id"] != programs[index + 1]
        ):
            raise ValueError("INVALID_WORKFLOW_DEPENDENCY")
        support.update(edge["evidence_ids"])
    seen = _verify_support(sorted(support), run_id, repository, workflow=True)
    if not seen or workflow["evidence_ids"] != sorted(seen):
        raise ValueError("INVALID_WORKFLOW_SUPPORT")
    return workflow["workflow_id"], entity_id


def _validate_rule_spans(rule: dict[str, Any], repository: Repository) -> None:
    validate("RuleRevision", rule)
    sources: dict[tuple[str, str, str | None], list[dict[str, Any]]] = {}
    for evidence_id in rule["evidence_ids"]:
        evidence = repository._evidence(evidence_id, rule["run_id"])
        for span in evidence["source_locations"]:
            key = (span["artifact_id"], span["coordinate_space"], span["origin_map_id"])
            sources.setdefault(key, []).append(span)
    work = 0
    for expression in [*rule["conditions"], *rule["actions"]]:
        if not expression["source_locations"]:
            raise ValueError("UNCITED_SOURCE_SPAN")
        for span in expression["source_locations"]:
            if (span["start_line"], span["start_column"]) > (span["end_line"], span["end_column"]):
                raise ValueError("UNCITED_SOURCE_SPAN")
            key = (span["artifact_id"], span["coordinate_space"], span["origin_map_id"])
            covered = False
            for source in sources.get(key, []):
                work += 1
                if work > 2000000:
                    raise ValueError("RULE_OUTPUT_LIMIT")
                if (
                    source["byte_start"] is not None
                    and source["byte_end"] is not None
                    and span["byte_start"] is not None
                    and span["byte_end"] is not None
                    and source["byte_start"]
                    <= span["byte_start"]
                    <= span["byte_end"]
                    <= source["byte_end"]
                    and (source["start_line"], source["start_column"])
                    <= (span["start_line"], span["start_column"])
                    and (span["end_line"], span["end_column"])
                    <= (source["end_line"], source["end_column"])
                ):
                    covered = True
                    break
            if not covered:
                raise ValueError("UNCITED_SOURCE_SPAN")


def workflow_support_record(workflow: dict[str, Any]) -> dict[str, Any]:
    """Repository's shared closed support basis, without inventing a Rule revision."""
    support = workflow["claim_support"]
    return {
        "run_id": workflow["run_id"],
        "subject_entity_ids": support["subject_entity_ids"],
        "evidence_ids": workflow["evidence_ids"],
        "conditions": support["controlling_predicates"],
        "claim_support": support,
    }


def _validate_workflow_v2(
    workflow: dict[str, Any], run_id: str, repository: Repository
) -> tuple[str, str]:
    validate("Workflow", workflow)
    if workflow["run_id"] != run_id or workflow["workflow_id"] != "wf_" + digest(
        {k: v for k, v in workflow.items() if k != "workflow_id"}
    ):
        raise ValueError("INVALID_WORKFLOW")
    if workflow["claim_support"]["barriers"] or workflow["claim_support"]["state"] not in {
        "conditional",
        "supported_within_profile",
    }:
        raise ValueError("UNRESOLVED_WORKFLOW")
    entity_id = workflow["workflow_entity_id"]
    if repository._entity(entity_id) != "Workflow":
        raise ValueError("INVALID_WORKFLOW_IDENTITY")
    for entity, kind in [
        (workflow["program_entity_ids"][0], "Program"),
        (workflow["procedure_entity_ids"][0], "Procedure"),
    ]:
        if repository._run_entity(entity, run_id) != kind:
            raise ValueError("INVALID_WORKFLOW_SUBJECT")
    if [step["operation"] for step in workflow["steps"]] != ["update", "call", "write"]:
        raise ValueError("INVALID_WORKFLOW_STEPS")
    basis = workflow_support_record(workflow)
    if repository.rule_support_fingerprint(basis) != workflow["support_fingerprint"]:
        raise ValueError("WORKFLOW_SUPPORT_MISMATCH")
    repository._verify_claim_effects(basis)
    facts = {
        fact["fact_id"]: fact
        for row in repository.connection.execute(
            "SELECT payload FROM analyzer_fact_bundles WHERE run_id=%s AND system_namespace=%s",
            (run_id, repository.namespace),
        ).fetchall()
        for fact in row[0]["facts"]
    }
    program = workflow["program_entity_ids"][0]
    procedure = workflow["procedure_entity_ids"][0]
    procedure_identity, procedure_parent = repository._one(
        "SELECT qualified_identity,parent_id FROM entities "
        "WHERE entity_id=%s AND system_namespace=%s",
        (procedure, repository.namespace),
    )
    if procedure_parent != program:
        raise ValueError("INVALID_WORKFLOW_PROCEDURE_OWNER")
    for step, expected_actor, expected_scope in zip(
        workflow["steps"],
        (program, procedure, procedure),
        (
            program + ":main",
            program + ":main",
            program + ":procedure:" + str(procedure_identity[-1]).casefold(),
        ),
        strict=True,
    ):
        if step["entity_id"] != expected_actor:
            raise ValueError("INVALID_WORKFLOW_STEP_ACTOR")
        if not set(step["evidence_ids"]).issubset(workflow["evidence_ids"]) or not set(
            step["fact_ids"]
        ).issubset(workflow["claim_support"]["effect_dependencies"]):
            raise ValueError("UNCITED_WORKFLOW_STEP")
        selected = [facts[fid] for fid in step["fact_ids"]]
        if any(
            fact["kind"] != "effect"
            or fact["scope_id"] != expected_scope
            or not set(fact["evidence_ids"]).issubset(step["evidence_ids"])
            for fact in selected
        ):
            raise ValueError("INVALID_WORKFLOW_EFFECT_SCOPE")
        if not any(
            fact["kind"] == "effect" and fact["data"]["operation"] == step["operation"]
            for fact in selected
        ):
            raise ValueError("INVALID_WORKFLOW_EFFECT")
        fact_spans = [span for fact in selected for span in fact["source_locations"]]
        if any(span not in fact_spans for span in step["source_locations"]):
            raise ValueError("UNCITED_WORKFLOW_FACT_SPAN")
        supported_spans = [
            span
            for fid in step["evidence_ids"]
            for span in repository._evidence(fid, run_id)["source_locations"]
        ]
        if any(span not in supported_spans for span in step["source_locations"]):
            raise ValueError("UNCITED_WORKFLOW_SPAN")
    return workflow["workflow_id"], entity_id
