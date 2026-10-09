"""Deterministic masked knowledge bundles; publication is an immutable private blob.

No source path or command is accepted. The caller owns authorization and durable job
fencing; these functions do not create jobs or publish a database download reference.
"""

import hashlib
import html
import io
import json
import re
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files as package_files
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]

from laip.artifacts import BlobRef, LocalArtifactStore
from laip.canonical import canonical, digest, loads
from laip.masking import POLICY, mask
from laip.persistence import Repository, validate
from laip.public_projection import semantic_payload
from laip.retrieval import RetrievalService
from laip.schema_registry import schema_for_version


@dataclass(frozen=True)
class ExportLimits:
    max_bytes: int = 512 * 1024 * 1024
    max_files: int = 20000
    max_records: int = 20000
    max_source_bytes: int = 16 * 1024 * 1024
    excerpt_chars: int = 2000
    timeout_seconds: int = 60

    def __post_init__(self) -> None:
        if any(type(value) is not int or value < 1 for value in vars(self).values()):
            raise ValueError("INVALID_EXPORT_LIMIT")


@dataclass(frozen=True)
class ExportBundle:
    manifest: dict[str, Any]
    files: dict[str, bytes]


DEFAULT_LIMITS = ExportLimits()
EXPORT_SCHEMA = cast(
    dict[str, Any],
    json.loads(package_files("laip").joinpath("contracts/export.schema.json").read_text()),
)


@lru_cache(maxsize=2)
def _export_schema(version: str) -> dict[str, Any]:
    schema_for_version(version)
    if version == "0.1.0":
        return EXPORT_SCHEMA
    return cast(
        dict[str, Any],
        json.loads(
            package_files("laip").joinpath("contracts/0.2.0/export.schema.json").read_text()
        ),
    )


def _validate_projection(
    kind: str, record: dict[str, Any], schema_version: str | None = None
) -> None:
    version = schema_version or record.get("schema_version", "0.1.0")
    authored = _export_schema(version)
    schema = {"$ref": "#/$defs/" + kind, "$defs": authored["$defs"]}
    try:
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(record)
    except Exception:
        raise ValueError("INVALID_EXPORT_SCHEMA") from None


def _filename(kind: str, ident: str, suffix: str = "json") -> str:
    # Names and imported IDs are never used as filesystem components.
    return f"{kind}/{hashlib.sha256(ident.encode()).hexdigest()}.{suffix}"


def _markdown(value: str) -> str:
    value = html.escape(value, quote=True)
    value = re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", value)
    return value.replace("\r", " ").replace("\n", " ")


def _label(value: str) -> str:
    # Numeric entities prevent Mermaid control syntax/directives and HTML tags.
    return "".join(c if c.isalnum() or c == " " else f"#{ord(c)};" for c in value[:200])


def _category(record: dict[str, Any]) -> str:
    if record["type"] == "evidence":
        return "evidence"
    if record["type"] == "rule":
        return "rule"
    if record["type"] == "workflow":
        return "workflow"
    if record["type"] != "entity":
        return "records"
    kind = record["payload"]["identity"]["kind"]
    if kind in {"System", "Application", "Program", "Workflow"}:
        return str(kind).lower()
    if kind in {"Table", "Field", "Database", "Screen"}:
        return "database"
    return "records"


def _view(
    record: dict[str, Any], limits: ExportLimits, schema_version: str = "0.1.0"
) -> dict[str, Any]:
    payload = record["payload"]
    canonical_kinds = {
        "entity": "Entity",
        "dependency": "Dependency",
        "rule": "RuleRevision",
        "evidence": "Evidence",
    }
    if record["type"] in canonical_kinds:
        validate(canonical_kinds[record["type"]], payload)
    else:
        _validate_projection(
            "ExportChunk" if record["type"] == "chunk" else "ExportWorkflow", payload
        )
    if record["type"] == "evidence":
        result = {
            "schema_version": schema_version,
            "evidence_id": record["id"],
            "record_sha256": digest(payload),
            "classification": payload["classification"],
            "collected_at": payload["collected_at"],
            "provider": payload["provider"],
            "source_locations": payload["source_locations"],
            "display_excerpt": mask(payload.get("excerpt")),
            "display_metadata": semantic_payload(payload).get("metadata", {}),
            "limitations": mask(payload["limitations"]),
            "supporting_evidence_ids": payload["supporting_evidence_ids"],
            "masking_policy_version": POLICY,
            "content_available": True,
            "review": {
                "status": record["review_status"],
                "review_id": record["review_id"],
                "stale": record["review_status"] == "stale",
            },
        }
        if result["display_excerpt"] and len(result["display_excerpt"]) > limits.excerpt_chars:
            result["display_excerpt"] = result["display_excerpt"][: limits.excerpt_chars]
            result["limitations"] = [*result["limitations"], "Supporting excerpt truncated."]
        validate("EvidenceView", result)
    else:
        result = semantic_payload(payload)
    limitations = list(payload.get("limitations", []))
    if record["type"] == "chunk" and len(result["text"]) > limits.excerpt_chars:
        result["text"] = result["text"][: limits.excerpt_chars]
        limitations.append("Context excerpt truncated; full source is excluded by default.")
    if record["type"] == "evidence":
        limitations = list(result["limitations"])
    return {
        "schema_version": schema_version,
        "type": record["type"],
        "id": record["id"],
        "record_sha256": digest(payload),
        "classification": record["classification"],
        "review_status": record["review_status"],
        "review_id": record["review_id"],
        "evidence_ids": sorted(set(record["evidence_ids"])),
        "supporting_evidence_ids": sorted(set(payload.get("supporting_evidence_ids", []))),
        "contradiction_evidence_ids": sorted(set(payload.get("contradiction_evidence_ids", []))),
        "masking_policy_version": POLICY,
        "limitations": mask(limitations),
        "view": result,
    }


def build_export(
    repository: Repository,
    snapshot_id: str,
    *,
    export_id: str,
    schema_version: str = "0.1.0",
    created_at: str,
    source_included: bool = False,
    source_reader: Callable[[dict[str, Any]], bytes] | None = None,
    cancelled: Callable[[], bool] | None = None,
    limits: ExportLimits = DEFAULT_LIMITS,
) -> ExportBundle:
    """Assemble one snapshot. source_reader is a separately authorized raw-byte capability.

    The explicit choice alone grants no source access. Raw bytes are never masked;
    approved source inclusion retains exact artifact SHA256 and byte length.
    """
    if type(source_included) is not bool or not export_id or len(export_id) > 256:
        raise ValueError("INVALID_EXPORT_REQUEST")
    if source_included and source_reader is None:
        raise ValueError("SOURCE_ACCESS_DENIED")
    deadline = time.monotonic() + limits.timeout_seconds

    def check() -> None:
        if cancelled and cancelled():
            raise ValueError("CANCELLED")
        if time.monotonic() >= deadline:
            raise ValueError("EXPORT_LIMIT")

    check()
    retrieval = RetrievalService(repository, snapshot_id, deadline=deadline)
    retrieval.require_version(schema_version)
    result = retrieval.collect(cancelled=cancelled, policy_version=POLICY)
    check()
    if result["truncated"] or result["diagnostics"]:
        raise ValueError("EXPORT_INCOMPLETE_SNAPSHOT")
    records = list(result["records"])
    query_guard = retrieval._guard(cancelled)
    providers = [
        row[0]
        for row in retrieval._query(
            query_guard,
            "SELECT payload FROM run_providers WHERE run_id=%s "
            "ORDER BY provider_id,provider_version",
            (retrieval.run_id,),
        ).fetchall()
    ]
    workflows = retrieval._query(
        query_guard,
        "SELECT DISTINCT w.workflow_id,w.payload FROM workflow_revisions w "
        "JOIN entity_observations o "
        "ON o.entity_id=w.entity_id JOIN snapshot_members m ON m.observation_id=o.observation_id "
        "WHERE m.snapshot_id=%s AND m.system_namespace=%s AND o.system_namespace=%s "
        "AND w.system_namespace=%s AND w.run_id=%s ORDER BY w.workflow_id LIMIT %s",
        (
            snapshot_id,
            repository.namespace,
            repository.namespace,
            repository.namespace,
            retrieval.run_id,
            limits.max_records + 1,
        ),
    ).fetchall()
    evidence_ids = {r["id"] for r in records if r["type"] == "evidence"}
    selected_workflows = {r["id"] for r in records if r["type"] == "workflow"}
    for ident, payload in workflows:
        check()
        if ident in selected_workflows:
            continue
        support = payload.get("evidence_ids", [])
        if not set(support) <= evidence_ids:
            raise ValueError("INVALID_LINK")
        records.append(
            {
                "type": "workflow",
                "id": ident,
                "payload": payload,
                "evidence_ids": support,
                "classification": payload["classification"],
                "review_status": "pending_review",
                "review_id": None,
            }
        )
    if len(records) > limits.max_records:
        raise ValueError("EXPORT_LIMIT")
    records.sort(key=lambda r: (r["type"], r["id"]))
    files: dict[str, bytes] = {}
    media: dict[str, str] = {}
    total = 0

    def add(path: str, value: bytes, media_type: str = "application/json") -> None:
        nonlocal total
        check()
        total += len(value)
        if len(files) >= limits.max_files or total > limits.max_bytes:
            raise ValueError("EXPORT_LIMIT")
        if path in files:
            raise ValueError("INVALID_LINK")
        files[path] = value
        media[path] = media_type

    links = {(r["type"], r["id"]): _filename(_category(r), r["id"]) for r in records}
    views = []
    indexes: dict[str, list[str]] = {
        key: [] for key in ("system", "application", "program", "database", "rule", "workflow")
    }
    for record in records:
        check()
        view = _view(record, limits, schema_version)
        view["links"] = [links[("evidence", eid)] for eid in view["evidence_ids"]]
        path = links[(record["type"], record["id"])]
        add(path, canonical(view))
        views.append(
            {
                "schema_version": schema_version,
                "type": record["type"],
                "id": record["id"],
                "relative_path": path,
            }
        )
        category = _category(record)
        if category in indexes:
            name = mask(record["payload"].get("display_name", record["id"]))
            text = f"# {_markdown(str(name))}\n\n"
            text += (
                f"Classification: {_markdown(str(record['classification'] or 'not_applicable'))}. "
            )
            text += f"Review: {_markdown(record['review_status'])}.\n\n"
            text += f"[Structured record]({path.rsplit('/', 1)[1]})\n"
            for eid in view["evidence_ids"]:
                text += f"\n[Evidence](../{links[('evidence', eid)]})\n"
            md_path = _filename(category, record["id"], "md")
            add(md_path, text.encode(), "text/markdown")
            indexes[category].append(f"- [{_markdown(str(name))}]({md_path.rsplit('/', 1)[1]})")
    for category, items in indexes.items():
        add(
            f"{category}/index.md",
            (f"# {category.title()}\n\n" + "\n".join(items) + "\n").encode(),
            "text/markdown",
        )
    knowledge: dict[str, Any] = {
        "schema_version": schema_version,
        "snapshot_id": snapshot_id,
        "system_namespace": repository.namespace,
        "run_id": retrieval.run_id,
        "review_sequence": retrieval.review_sequence,
        "masking_policy_version": POLICY,
        "records": views,
        "limitations": ["Static supplied-artifact knowledge; runtime behavior is not established."],
    }
    if schema_version == "0.2.0":
        summaries = retrieval._query(
            query_guard,
            "SELECT payload FROM artifact_results WHERE run_id=%s ORDER BY result_id DESC LIMIT 1",
            (retrieval.run_id,),
        ).fetchall()
        knowledge["fact_coverage"] = (
            semantic_payload(summaries[0][0]).get("fact_coverage", []) if summaries else []
        )
        knowledge["limitations"].append(
            "Package validation does not establish complete extraction; "
            "coverage is profile bounded."
        )
    add("knowledge.json", canonical(knowledge))
    add(
        "evidence/index.jsonl",
        b"".join(
            canonical({**v, "links": [v["relative_path"]]}) + b"\n"
            for v in views
            if v["type"] == "evidence"
        ),
        "application/x-ndjson",
    )
    chunks = [_view(r, limits, schema_version) for r in records if r["type"] == "chunk"]
    for chunk in chunks:
        chunk["links"] = [links[("evidence", eid)] for eid in chunk["evidence_ids"]]
    add(
        "context/chunks.jsonl",
        b"".join(canonical(c) + b"\n" for c in chunks),
        "application/x-ndjson",
    )
    nodes = [r for r in records if r["type"] == "entity"]
    edges = [r for r in records if r["type"] == "dependency"]
    node_ids = {r["payload"]["entity_id"]: f"n{i}" for i, r in enumerate(nodes)}
    graph_edges = [_view(r, limits, schema_version) for r in edges]
    for edge in graph_edges:
        edge["links"] = [links[("evidence", eid)] for eid in edge["evidence_ids"]]
    graph = {
        "schema_version": schema_version,
        "snapshot_id": snapshot_id,
        "nodes": [semantic_payload(r["payload"]) for r in nodes],
        "edges": graph_edges,
        "links": [links[(r["type"], r["id"])] for r in nodes + edges],
    }
    add("graph.json", canonical(graph))
    lines = ["flowchart LR"]
    for record in nodes:
        payload = record["payload"]
        label = _label(str(mask(payload.get("display_name", record["id"]))))
        lines.append(f'  {node_ids[payload["entity_id"]]}["{label}"]')
    for record in edges:
        payload = record["payload"]
        source, target = payload["from_entity_id"], payload.get("to_entity_id")
        if (
            payload["resolution"] == "resolved"
            and payload["classification"] != "unresolved"
            and source in node_ids
            and target in node_ids
        ):
            label = _label(
                payload["relationship"]
                + ": "
                + payload["classification"]
                + "; "
                + record["review_status"]
            )
            lines.append(f'  {node_ids[source]} -->|"{label}"| {node_ids[target]}')
    add("graph.mmd", ("\n".join(lines) + "\n").encode(), "text/vnd.mermaid")
    add(
        "README.md",
        b"# Knowledge export\n\n[Snapshot knowledge](knowledge.json)\n\n[Graph](graph.json)\n",
        "text/markdown",
    )
    if source_included:
        artifacts = retrieval._query(
            query_guard,
            "SELECT a.payload FROM artifacts a JOIN run_artifacts r ON r.artifact_id=a.artifact_id "
            "WHERE r.run_id=%s AND r.system_namespace=%s AND a.system_namespace=%s "
            "ORDER BY a.artifact_id LIMIT %s",
            (retrieval.run_id, repository.namespace, repository.namespace, limits.max_files + 1),
        ).fetchall()
        if len(artifacts) > limits.max_files:
            raise ValueError("EXPORT_LIMIT")
        source_index = []
        assert source_reader is not None
        for (artifact,) in artifacts:
            check()
            validate("Artifact", artifact)
            if artifact["byte_length"] > limits.max_source_bytes:
                raise ValueError("EXPORT_LIMIT")
            value = source_reader(artifact)
            check()
            if (
                not isinstance(value, bytes)
                or len(value) != artifact["byte_length"]
                or hashlib.sha256(value).hexdigest() != artifact["sha256"]
            ):
                raise ValueError("INVALID_DIGEST")
            path = _filename("source", artifact["artifact_id"], "bin")
            add(path, value, "application/octet-stream")
            source_index.append(
                {
                    "artifact_id": artifact["artifact_id"],
                    "sha256": artifact["sha256"],
                    "byte_length": len(value),
                    "relative_path": path,
                }
            )
        add(
            "source/index.json",
            canonical({"schema_version": schema_version, "artifacts": source_index}),
        )
    manifest = {
        "schema_version": schema_version,
        "export_id": export_id,
        "snapshot_id": snapshot_id,
        "run_ids": [retrieval.run_id],
        "created_at": created_at,
        "providers": providers,
        "source_included": source_included,
        "masking_policy_version": POLICY,
        "limitations": knowledge["limitations"],
        "files": [
            {
                "relative_path": path,
                "sha256": hashlib.sha256(files[path]).hexdigest(),
                "byte_length": len(files[path]),
                "media_type": media[path],
            }
            for path in sorted(files)
        ],
    }
    validate("ExportManifest", manifest)
    add("manifest.json", canonical(manifest))
    bundle = ExportBundle(manifest, files)
    validate_bundle(bundle)
    check()
    return bundle


def validate_bundle(bundle: ExportBundle) -> None:
    """Validate exact listed bytes, safe paths, versioned records and internal links."""
    validate("ExportManifest", bundle.manifest)
    if not {
        "knowledge.json",
        "evidence/index.jsonl",
        "context/chunks.jsonl",
        "graph.json",
        "graph.mmd",
        "README.md",
    } <= set(bundle.files):
        raise ValueError("INVALID_LINK")
    if bundle.files.get("manifest.json") != canonical(bundle.manifest):
        raise ValueError("INVALID_DIGEST")
    paths = [item["relative_path"] for item in bundle.manifest["files"]]
    if len(set(paths)) != len(paths) or set(bundle.files) != set(paths) | {"manifest.json"}:
        raise ValueError("INVALID_LINK")
    for path in bundle.files:
        if (
            not re.fullmatch(r"[a-zA-Z0-9_./-]+", path)
            or any(part in {"", ".", ".."} for part in path.split("/"))
            or path.startswith("/")
        ):
            raise ValueError("INVALID_LINK")
    record_index: dict[tuple[str, str], str] = {}
    citation_ids: set[str] = set()
    projected: list[dict[str, Any]] = []

    def check_links(record: Any) -> None:
        if isinstance(record, dict):
            for link in record.get("links", []):
                if link not in bundle.files:
                    raise ValueError("INVALID_LINK")
            if "relative_path" in record and record["relative_path"] not in bundle.files:
                raise ValueError("INVALID_LINK")
            for child in record.values():
                if isinstance(child, (dict, list)):
                    check_links(child)
        elif isinstance(record, list):
            for child in record:
                check_links(child)

    for item in bundle.manifest["files"]:
        path, value = item["relative_path"], bundle.files[item["relative_path"]]
        if hashlib.sha256(value).hexdigest() != item["sha256"] or len(value) != item["byte_length"]:
            raise ValueError("INVALID_DIGEST")
        if path.endswith((".json", ".jsonl")):
            records = (
                [loads(line.decode()) for line in value.splitlines()]
                if path.endswith(".jsonl")
                else [loads(value.decode())]
            )
            for record in records:
                kind = {
                    "knowledge.json": "ExportKnowledge",
                    "graph.json": "ExportGraph",
                    "source/index.json": "ExportSourceIndex",
                    "evidence/index.jsonl": "ExportEvidenceIndex",
                }.get(path, "ExportRecord")
                _validate_projection(kind, record, bundle.manifest["schema_version"])
                check_links(record)
                if kind == "ExportRecord":
                    projected.append(record)
                    if path != "context/chunks.jsonl":
                        key = (record["type"], record["id"])
                        if key in record_index:
                            raise ValueError("INVALID_LINK")
                        record_index[key] = path
                    if record["type"] == "evidence":
                        citation_ids.add(record["id"])
                        if record["record_sha256"] != record["view"]["record_sha256"]:
                            raise ValueError("INVALID_DIGEST")
        elif path.endswith(".md"):
            for link in re.findall(r"(?<!\\)\]\(([^)]+)\)", value.decode()):
                base = path.rsplit("/", 1)[0] if "/" in path else ""
                parts = (base + "/" + link).split("/") if base else link.split("/")
                normalized: list[str] = []
                for part in parts:
                    if part == "..":
                        if not normalized:
                            raise ValueError("INVALID_LINK")
                        normalized.pop()
                    else:
                        normalized.append(part)
                if "/".join(normalized) not in bundle.files:
                    raise ValueError("INVALID_LINK")
    for record in projected:
        if (
            not set(
                record["evidence_ids"]
                + record["supporting_evidence_ids"]
                + record["contradiction_evidence_ids"]
            )
            <= citation_ids
        ):
            raise ValueError("INVALID_LINK")
    knowledge = loads(bundle.files["knowledge.json"].decode())
    expected = {
        (record["type"], record["id"]): record["relative_path"] for record in knowledge["records"]
    }
    if len(expected) != len(knowledge["records"]) or expected != record_index:
        raise ValueError("INVALID_LINK")
    if (
        knowledge["snapshot_id"] != bundle.manifest["snapshot_id"]
        or knowledge["run_id"] not in bundle.manifest["run_ids"]
        or knowledge["masking_policy_version"] != bundle.manifest["masking_policy_version"]
    ):
        raise ValueError("INVALID_EXPORT_SCHEMA")
    primary = {
        (record["type"], record["id"]): record
        for record in projected
        if (record["type"], record["id"]) in record_index
    }
    graph = loads(bundle.files["graph.json"].decode())
    if graph["snapshot_id"] != bundle.manifest["snapshot_id"]:
        raise ValueError("INVALID_EXPORT_SCHEMA")
    expected_nodes = [record["view"] for record in primary.values() if record["type"] == "entity"]
    expected_edges = [record for record in primary.values() if record["type"] == "dependency"]
    if sorted(graph["nodes"], key=canonical) != sorted(expected_nodes, key=canonical) or sorted(
        graph["edges"], key=canonical
    ) != sorted(expected_edges, key=canonical):
        raise ValueError("INVALID_LINK")
    for record in projected:
        if record["type"] != "entity":
            payload_key = {
                "dependency": "dependency_id",
                "rule": "revision_id",
                "evidence": "evidence_id",
                "chunk": "chunk_id",
                "workflow": "workflow_id",
            }[record["type"]]
            if record["id"] != record["view"][payload_key]:
                raise ValueError("INVALID_LINK")
        if record != primary[(record["type"], record["id"])]:
            raise ValueError("INVALID_LINK")
    exported_entities = {
        record["view"]["entity_id"] for record in primary.values() if record["type"] == "entity"
    }
    exported_dependencies = {
        record["id"] for record in primary.values() if record["type"] == "dependency"
    }
    for record in expected_edges:
        edge = record["view"]
        targets = [edge["from_entity_id"], *edge["candidate_entity_ids"]]
        if edge["to_entity_id"]:
            targets.append(edge["to_entity_id"])
        if not set(targets) <= exported_entities:
            raise ValueError("INVALID_LINK")
    for record in primary.values():
        if record["type"] == "rule":
            rule = record["view"]
            subjects = [rule["rule_entity_id"], *rule.get("subject_entity_ids", [])]
            for scope_key in (
                "application_entity_ids",
                "program_entity_ids",
                "procedure_entity_ids",
            ):
                subjects.extend(rule.get(scope_key, []))
            if not set(subjects) <= exported_entities:
                raise ValueError("INVALID_LINK")
        if record["type"] == "workflow":
            workflow = record["view"]
            if (
                not set([workflow["workflow_entity_id"], *workflow["program_entity_ids"]])
                <= exported_entities
                or not set(workflow["dependency_ids"]) <= exported_dependencies
            ):
                raise ValueError("INVALID_LINK")
    actual_evidence_index = [
        loads(line.decode()) for line in bundle.files["evidence/index.jsonl"].splitlines()
    ]
    expected_evidence_index = [
        {**record, "links": [record["relative_path"]]}
        for record in knowledge["records"]
        if record["type"] == "evidence"
    ]
    if actual_evidence_index != expected_evidence_index:
        raise ValueError("INVALID_LINK")
    context_chunks = [
        loads(line.decode()) for line in bundle.files["context/chunks.jsonl"].splitlines()
    ]
    expected_chunks = [record for record in primary.values() if record["type"] == "chunk"]
    if sorted(context_chunks, key=canonical) != sorted(expected_chunks, key=canonical):
        raise ValueError("INVALID_LINK")
    if "source/index.json" in bundle.files:
        index = loads(bundle.files["source/index.json"].decode())
        source_paths = {artifact["relative_path"] for artifact in index["artifacts"]}
        if len(source_paths) != len(index["artifacts"]) or source_paths != {
            path
            for path in bundle.files
            if path.startswith("source/") and path != "source/index.json"
        }:
            raise ValueError("INVALID_LINK")
        for artifact in index["artifacts"]:
            value = bundle.files[artifact["relative_path"]]
            if (
                hashlib.sha256(value).hexdigest() != artifact["sha256"]
                or len(value) != artifact["byte_length"]
            ):
                raise ValueError("INVALID_DIGEST")
    has_source = any(path.startswith("source/") for path in paths)
    if has_source != bundle.manifest["source_included"]:
        raise ValueError("SOURCE_ACCESS_DENIED")


def archive_bytes(bundle: ExportBundle) -> bytes:
    validate_bundle(bundle)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        for path in sorted(bundle.files):
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100600 << 16
            archive.writestr(info, bundle.files[path])
    return stream.getvalue()


def publish_export(
    bundle: ExportBundle, store: LocalArtifactStore, *, job_id: str, fence: int
) -> BlobRef:
    value = archive_bytes(bundle)
    return store.put(io.BytesIO(value), max_bytes=len(value), job_id=job_id, fence=fence)
