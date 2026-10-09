"""Typed deterministic analyst reads shared by REST and stdio MCP."""

import copy
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Annotated, Any, Literal
from uuid import uuid4

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from laip.canonical import canonical, digest
from laip.context import build_context
from laip.masking import POLICY
from laip.persistence import Repository
from laip.public_projection import semantic_payload
from laip.retrieval import DEFAULT_LIMITS, RetrievalService
from laip.schema_registry import schema_for_version

Id = Annotated[str, Field(min_length=1, max_length=256)]
EntityId = Annotated[str, Field(pattern=r"^ent_[a-f0-9]{64}$")]
EvidenceId = Annotated[str, Field(pattern=r"^ev_[a-f0-9]{64}$")]
Relationship = Literal[
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
]


class ReadInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    schema_version: Literal["0.1.0", "0.2.0"]
    snapshot_id: Id


class EntityInput(ReadInput):
    entity_id: EntityId


class EvidenceInput(ReadInput):
    evidence_id: EvidenceId


class SearchInput(ReadInput):
    query: Annotated[str, Field(min_length=1, max_length=1000)]
    limit: Annotated[int, Field(ge=1, le=100)] = 20
    mode: Literal["keyword"] = "keyword"


class GraphInput(ReadInput):
    root_entity_ids: Annotated[list[EntityId], Field(min_length=1, max_length=100)]
    direction: Literal["inbound", "outbound", "both"] = "outbound"
    depth: Annotated[int, Field(ge=0, le=5)] = 2
    max_nodes: Annotated[int, Field(ge=1, le=1000)] = 200
    max_edges: Annotated[int, Field(ge=1, le=2500)] = 500
    relationships: Annotated[list[Relationship], Field(max_length=13)] = Field(default_factory=list)
    include_unresolved: bool = False


class ContextInput(ReadInput):
    level: Literal["system", "application", "program", "rule", "evidence"]
    entity_ids: Annotated[list[EntityId], Field(max_length=100)]
    query: Annotated[str, Field(max_length=1000)] = ""
    max_context_tokens: Annotated[int, Field(ge=1, le=32768)] = 4096
    reserved_output_tokens: Annotated[int, Field(ge=0, le=32767)] = 1024
    tokenizer_id: Literal["utf8-byte-upper-bound-v1"] = "utf8-byte-upper-bound-v1"

    @model_validator(mode="after")
    def bounded(self) -> "ContextInput":
        if self.reserved_output_tokens >= self.max_context_tokens:
            raise ValueError("INVALID_CONTEXT_BUDGET")
        if self.level != "system" and not self.entity_ids:
            raise ValueError("CONTEXT_SCOPE_REQUIRED")
        return self


INPUT_MODELS: dict[str, type[ReadInput]] = {
    "entity": EntityInput,
    "search": SearchInput,
    "graph": GraphInput,
    "evidence": EvidenceInput,
    "context": ContextInput,
}

_RECORD = {
    "type": "object",
    "required": [
        "type",
        "id",
        "payload",
        "evidence_ids",
        "classification",
        "review_status",
        "review_id",
    ],
    "properties": {
        "type": {"enum": ["entity", "dependency", "rule", "evidence", "chunk", "workflow"]},
        "id": {"type": "string"},
        "payload": {"type": "object"},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
        "classification": {"enum": [None, "observed", "inferred", "unresolved"]},
        "review_status": {
            "enum": ["not_applicable", "pending_review", "verified", "rejected", "invalidated"]
        },
        "review_id": {"type": ["null", "string"]},
        "rank": {"type": "integer", "minimum": 0},
        "boundary_reason": {"type": "string"},
        "record_sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
        "masking_policy_version": {"const": POLICY},
    },
    "additionalProperties": False,
}
_RETRIEVAL: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "snapshot_id",
        "system_namespace",
        "run_id",
        "entity_ids",
        "records",
        "boundaries",
        "truncated",
        "diagnostics",
    ],
    "properties": {
        "snapshot_id": {"type": "string"},
        "system_namespace": {"type": "string"},
        "run_id": {"type": "string"},
        "entity_ids": {"type": "array", "items": {"type": "string"}},
        "records": {"type": "array", "items": _RECORD},
        "boundaries": {"type": "array", "items": _RECORD},
        "truncated": {"type": "boolean"},
        "diagnostics": {"type": "array", "items": {"type": "object"}},
    },
}


def output_schema(operation: str, schema_version: str = "0.1.0") -> dict[str, Any]:
    if operation not in INPUT_MODELS:
        raise ValueError("UNKNOWN_READ_OPERATION")
    definitions = schema_for_version(schema_version)["$defs"]
    retrieval = copy.deepcopy(_RETRIEVAL)
    if schema_version == "0.2.0":
        retrieval["properties"]["fact_coverage"] = {"$ref": "#/$defs/FactCoverage"}
        legacy = schema_for_version("0.1.0")["$defs"]
        from laip.exports import _export_schema

        legacy["Workflow"] = copy.deepcopy(_export_schema("0.1.0")["$defs"]["ExportWorkflow"])

        def legacy_refs(value: Any) -> Any:
            if isinstance(value, dict):
                return {
                    key: item.replace("#/$defs/", "#/$defs/Legacy")
                    if key == "$ref"
                    else legacy_refs(item)
                    for key, item in value.items()
                }
            if isinstance(value, list):
                return [legacy_refs(item) for item in value]
            return value

        definitions.update({"Legacy" + key: legacy_refs(value) for key, value in legacy.items()})
        record = retrieval["properties"]["records"]["items"]
        record["allOf"] = []
        for record_type, kind in (
            ("entity", "Entity"),
            ("dependency", "Dependency"),
            ("rule", "RuleRevision"),
            ("evidence", "Evidence"),
            ("workflow", "Workflow"),
        ):
            record["allOf"].append(
                {
                    "if": {"properties": {"type": {"const": record_type}}},
                    "then": {
                        "properties": {
                            "payload": {
                                "oneOf": [
                                    {"$ref": "#/$defs/" + kind},
                                    *(
                                        [{"$ref": "#/$defs/Legacy" + kind}]
                                        if kind in legacy
                                        else []
                                    ),
                                ]
                            }
                        }
                    },
                }
            )
        retrieval["properties"]["boundaries"]["items"] = copy.deepcopy(record)
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["schema_version", "request_id", "data"],
        "properties": {
            "schema_version": {"const": schema_version},
            "request_id": {"type": "string"},
            "data": {"$ref": "#/$defs/ContextPackage"} if operation == "context" else retrieval,
        },
        "$defs": definitions,
    }


def negotiated_output_schema(operation: str) -> dict[str, Any]:
    """MCP advertises both strict envelope versions with root-resolvable references."""
    definitions: dict[str, Any] = {}
    alternatives = []

    def rewrite(value: Any, prefix: str) -> Any:
        if isinstance(value, dict):
            return {
                key: item.replace("#/$defs/", "#/$defs/" + prefix)
                if key == "$ref" and isinstance(item, str)
                else rewrite(item, prefix)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [rewrite(item, prefix) for item in value]
        return value

    for version, prefix in (("0.1.0", "V010"), ("0.2.0", "V020")):
        schema = output_schema(operation, version)
        definitions.update(
            {prefix + key: rewrite(value, prefix) for key, value in schema.pop("$defs").items()}
        )
        alternatives.append(rewrite(schema, prefix))
    return {"oneOf": alternatives, "$defs": definitions}


def error_envelope(code: str) -> dict[str, Any]:
    return {
        "schema_version": "0.1.0",
        "request_id": str(uuid4()),
        "error": {
            "code": code,
            "message": "Read request could not be completed",
            "retryable": code == "DEPENDENCY_UNAVAILABLE",
            "details": {},
        },
    }


def safe_error(error: Exception) -> tuple[int, str]:
    if isinstance(error, (ValidationError, TypeError)):
        return 400, "INVALID_REQUEST"
    if isinstance(error, ValueError):
        code = str(error)
        if code in {"INCOMPATIBLE_SNAPSHOT_VERSION", "UNSUPPORTED_SCHEMA_VERSION"}:
            return 400, code
        if "NOT_IN_SNAPSHOT" in code or "NOT_FOUND" in code or "CROSS_NAMESPACE" in code:
            return 404, "NOT_FOUND"
        if "CANCEL" in code:
            return 409, "CANCELLED"
        if "LIMIT" in code or "BUDGET" in code:
            return 413, "RESOURCE_LIMIT"
        return 400, "INVALID_REQUEST"
    return 503, "DEPENDENCY_UNAVAILABLE"


def projection(result: dict[str, Any]) -> dict[str, Any]:
    """Masked read views retain original record hashes and never rewrite provenance."""
    view = {**result}
    for key in ("records", "boundaries"):
        view[key] = [
            {
                **record,
                "payload": semantic_payload(record["payload"]),
                "record_sha256": digest(record["payload"]),
                "masking_policy_version": POLICY,
            }
            for record in result[key]
        ]
    return view


class ReadService:
    def __init__(self, repository: Repository, *, policy_version: str = POLICY):
        self.repository = repository
        self.policy_version = policy_version

    def call(
        self,
        operation: str,
        arguments: dict[str, Any],
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        if operation not in INPUT_MODELS:
            raise ValueError("UNKNOWN_READ_OPERATION")
        request = INPUT_MODELS[operation].model_validate(arguments)
        deadline = time.monotonic() + DEFAULT_LIMITS.timeout_seconds
        caller_cancelled = cancelled

        def bounded_cancelled() -> bool:
            if time.monotonic() >= deadline:
                raise ValueError("READ_WORK_LIMIT")
            return bool(caller_cancelled and caller_cancelled())

        cancelled = bounded_cancelled
        service = RetrievalService(self.repository, request.snapshot_id, deadline=deadline)
        service.require_version(request.schema_version)
        if isinstance(request, EntityInput):
            result = service.exact(request.entity_id, cancelled=cancelled)
            if not any(r["type"] == "entity" for r in result["records"]):
                raise ValueError("ENTITY_NOT_IN_SNAPSHOT")
        elif isinstance(request, EvidenceInput):
            result = service.evidence(request.evidence_id, cancelled=cancelled)
        elif isinstance(request, SearchInput):
            result = service.search(
                request.query,
                policy_version=self.policy_version,
                limit=request.limit,
                cancelled=cancelled,
            )
        elif isinstance(request, GraphInput):
            service = RetrievalService(
                self.repository,
                request.snapshot_id,
                deadline=deadline,
                limits=replace(
                    DEFAULT_LIMITS,
                    max_nodes=request.max_nodes,
                    max_edges=request.max_edges,
                    max_records=5000,
                    max_evidence=2000,
                ),
            )
            result = service.graph(
                request.root_entity_ids,
                depth=request.depth,
                direction={"inbound": "incoming", "outbound": "outgoing", "both": "both"}[
                    request.direction
                ],
                relationships=request.relationships,
                cancelled=cancelled,
            )
            # Unknown paths remain explicit boundaries even when omitted from graph edges.
            if not request.include_unresolved:
                result["records"] = [
                    r
                    for r in result["records"]
                    if r["type"] != "dependency"
                    or (
                        r["payload"]["resolution"] == "resolved"
                        and r["classification"] != "unresolved"
                    )
                ]
        elif isinstance(request, ContextInput):
            result = self._context(service, request, cancelled)
            envelope = {
                "schema_version": request.schema_version,
                "request_id": str(uuid4()),
                "data": result,
            }
            Draft202012Validator(output_schema(operation, request.schema_version)).validate(
                envelope
            )
            return envelope
        else:
            raise ValueError("UNKNOWN_READ_OPERATION")
        if len(canonical(result)) > 8 * 1024 * 1024:
            raise ValueError("READ_OUTPUT_LIMIT")
        envelope = {
            "schema_version": request.schema_version,
            "request_id": str(uuid4()),
            "data": projection(result),
        }
        Draft202012Validator(output_schema(operation, request.schema_version)).validate(envelope)
        return envelope

    def _context(
        self, service: RetrievalService, request: ContextInput, cancelled: Callable[[], bool] | None
    ) -> dict[str, Any]:
        if request.entity_ids:
            results = [
                service.exact(eid, cancelled=cancelled) for eid in sorted(set(request.entity_ids))
            ]
            if any(not any(r["type"] == "entity" for r in res["records"]) for res in results):
                raise ValueError("ENTITY_NOT_IN_SNAPSHOT")
            result = {**results[0], "records": [], "boundaries": []}
            records = {(r["type"], r["id"]): r for res in results for r in res["records"]}
            result["records"] = list(records.values())
            result["entity_ids"] = sorted(set(request.entity_ids))
        elif request.query.strip():
            result = service.search(
                request.query, policy_version=self.policy_version, limit=100, cancelled=cancelled
            )
        else:
            result = service.collect(policy_version=self.policy_version, cancelled=cancelled)
        if request.entity_ids and request.query.strip():
            # Query within the exact requested entity/evidence scope, never broaden it.
            words = request.query.casefold().split()
            result["records"] = [
                r
                for r in result["records"]
                if r["type"] == "evidence"
                or all(word in canonical(r["payload"]).decode().casefold() for word in words)
            ]
        return build_context(
            result,
            schema_version=request.schema_version,
            snapshot_id=request.snapshot_id,
            context_id="context_" + digest(request.model_dump()),
            level=request.level,
            entity_ids=request.entity_ids,
            max_context_tokens=request.max_context_tokens,
            reserved_output_tokens=request.reserved_output_tokens,
            tokenizer_id=request.tokenizer_id,
            cancelled=cancelled,
        )
