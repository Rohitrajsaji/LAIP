"""Inert offline imports: prepare private bytes first; seal records in a fenced DB callback."""

import hashlib
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, fields, replace
from io import BytesIO
from pathlib import Path
from typing import Any, BinaryIO

from psycopg.types.json import Jsonb

from laip.artifacts import BlobRef, LocalArtifactStore
from laip.canonical import canonical, digest, identity_id, loads, record_id
from laip.import_manifest import parse_manifest, parse_metadata
from laip.import_readers import (
    DEFAULT_EXCLUSIONS,
    DEFAULT_LIMITS,
    ImportLimits,
    InputRecord,
    read_local,
    read_zip,
)
from laip.persistence import Repository, validate
from laip.source_decode import decode_source

IMPORT_PROVIDER = {"id": "laip-offline-import", "version": "0.1.0", "source_revision": None}


@dataclass(frozen=True)
class ImportContext:
    import_id: str
    run_id: str
    collected_at: str
    masking_policy_version: str
    job_id: str
    fence: int


@dataclass(frozen=True)
class PreparedItem:
    path: str
    scope_state: str
    status: str
    diagnostics: tuple[str, ...]
    artifact: dict[str, Any] | None = None
    raw_blob: BlobRef | None = None
    decoded_blob: BlobRef | None = None
    origin_map: dict[str, Any] | None = None
    entity: dict[str, Any] | None = None
    evidence: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class PreparedImport:
    context: ImportContext
    manifest: dict[str, Any]
    manifest_artifact: dict[str, Any]
    manifest_blob: BlobRef
    items: tuple[PreparedItem, ...]
    partial: bool
    configuration_sha256: str
    source_ref: str | None
    plan_artifact: dict[str, Any]
    plan_blob: BlobRef


def _artifact(
    context: ImportContext,
    path: str,
    raw: bytes,
    encoding: str | None,
    method: str,
    collected_at: str,
    member: str | None = None,
) -> dict[str, Any]:
    record = {
        "schema_version": "0.1.0",
        "import_id": context.import_id,
        "locator": path,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "byte_length": len(raw),
        "media_type": "application/octet-stream",
        "encoding": encoding,
        "source_member_id": member,
        "collected_at": collected_at,
        "collection_method": method,
        "decoded_sha256": None,
        "origin_map_id": None,
        "processing_status": "imported",
        "masking_policy_version": context.masking_policy_version,
    }
    record["artifact_id"] = "art_" + digest(
        {key: record[key] for key in ("import_id", "locator", "sha256")}
    )
    return record


def _blob(store: LocalArtifactStore, raw: bytes, context: ImportContext, maximum: int) -> BlobRef:
    return store.put(BytesIO(raw), max_bytes=maximum, job_id=context.job_id, fence=context.fence)


def _prepare(
    records: tuple[InputRecord, ...],
    manifest: dict[str, Any],
    manifest_raw: bytes,
    store: LocalArtifactStore,
    context: ImportContext,
    limits: ImportLimits,
    method: str,
    cancelled: Callable[[], bool] | None,
    source_ref: str | None = None,
) -> PreparedImport:
    declared = {item["relative_path"]: item for item in manifest["artifacts"]}
    received = {item.path: item for item in records}
    if any(
        path == "__laip__" or path.startswith("__laip__/") for path in set(declared) | set(received)
    ):
        raise ValueError("RESERVED_IMPORT_PATH")
    for path in set(declared) - set(received):
        received[path] = InputRecord(path, None, "failed", ("MISSING_SOURCE",))
    items: list[PreparedItem] = []
    segment_count = decoded_bytes = metadata_records = 0
    for path, item in sorted(received.items()):
        if cancelled is not None and cancelled():
            raise ValueError("CANCELLED")
        entry = declared.get(path)
        scope = "excluded" if item.status == "excluded" else "supplied"
        if item.raw is None:
            if item.status == "failed":
                scope = "missing"
            items.append(PreparedItem(path, scope, item.status, item.diagnostics))
            continue
        encoding = (entry["encoding"] or manifest["default_encoding"]) if entry else None
        identity = entry["source_member_identity"] if entry else None
        member = identity_id(identity) if identity else None
        artifact = _artifact(
            context,
            path,
            item.raw,
            encoding,
            method,
            entry["collected_at"] if entry else context.collected_at,
            member,
        )
        status = "unsupported"
        diagnostics: tuple[str, ...] = ("UNMANIFESTED_ARTIFACT",) if entry is None else ()
        decoded_blob = mapping = metadata = None
        if entry is not None:
            if segment_count >= 100000 or decoded_bytes >= limits.max_bytes:
                diagnostics = ("DECODE_IMPORT_BUDGET",)
                status = "partial"
            else:
                assert encoding is not None
                decoded = decode_source(
                    item.raw,
                    encoding,
                    artifact["artifact_id"],
                    max_raw_bytes=limits.max_file_bytes,
                    max_decoded_bytes=min(32 * 1024 * 1024, limits.max_bytes - decoded_bytes),
                    max_segments=100000 - segment_count,
                )
                status = "decoded" if decoded.status == "accepted" else decoded.status
                diagnostics = decoded.diagnostics
                mapping = decoded.origin_map
                if decoded.decoded is not None:
                    decoded_bytes += len(decoded.decoded)
                    assert mapping is not None
                    segment_count += len(mapping["segments"])
                    decoded_blob = _blob(store, decoded.decoded, context, max(1, limits.max_bytes))
                    artifact["decoded_sha256"] = decoded_blob.sha256
                    artifact["origin_map_id"] = mapping["origin_map_id"]
                    if entry["kind"] != "source":
                        try:
                            parsed = parse_metadata(
                                decoded.decoded,
                                kind=entry["kind"],
                                max_records=max(0, 100000 - metadata_records),
                            )
                            metadata_records += len(parsed.records)
                            metadata = parsed.payload
                            status = "imported"
                        except ValueError:
                            status = "failed"
                            diagnostics = ("INVALID_METADATA_ENVELOPE",)
        artifact["processing_status"] = status
        validate("Artifact", artifact)
        raw_blob = _blob(store, item.raw, context, limits.max_file_bytes)
        evidence = {
            "schema_version": "0.1.0",
            "run_id": context.run_id,
            "artifact_id": artifact["artifact_id"],
            "content_sha256": artifact["sha256"],
            "subject_entity_ids": [member] if member else [],
            "source_locations": [],
            "collection_method": method,
            "collected_at": context.collected_at,
            "provider": dict(IMPORT_PROVIDER),
            "authority": "source" if entry and entry["kind"] == "source" else "imported_metadata",
            "classification": "observed",
            "excerpt": None,
            "metadata": {
                "relative_path": path,
                "kind": entry["kind"] if entry else "unknown",
                "status": status,
                "diagnostics": list(diagnostics),
                "identity_basis": "supplied_manifest" if member else "unknown",
            },
            "limitations": [
                "Offline supplied material only; no live IBM i validation or execution."
            ],
            "supporting_evidence_ids": [],
            "contradiction_evidence_ids": [],
            "upstream_record": None,
        }
        evidence["evidence_id"] = record_id("ev_", evidence, "evidence_id")
        entity = None
        if identity:
            assert entry is not None
            entity = {
                "schema_version": "0.1.0",
                "entity_id": member,
                "identity": identity,
                "display_name": identity["qualified_identity"][-1],
                "original_names": [identity["qualified_identity"][-1]],
                "aliases": [],
                "parent_id": None,
                "source_availability": "available",
                "analysis_status": "not_analyzed",
                "attributes": {
                    "language": entry["language"],
                    "dialect": entry["dialect"],
                    "encoding": encoding,
                    "identity_basis": "supplied_manifest",
                },
                "evidence_ids": [evidence["evidence_id"]],
            }
        items.append(
            PreparedItem(
                path,
                scope,
                status,
                diagnostics,
                artifact,
                raw_blob,
                decoded_blob,
                mapping,
                entity,
                evidence,
                metadata,
            )
        )
    if cancelled is not None and cancelled():
        raise ValueError("CANCELLED")
    own_artifact = _artifact(
        context,
        "__laip__/manifest.json",
        manifest_raw,
        "utf-8",
        "laip-offline-import:manifest",
        context.collected_at,
    )
    own_artifact["media_type"] = "application/json"
    validate("Artifact", own_artifact)
    own_blob = _blob(store, manifest_raw, context, 1024 * 1024)
    configuration: dict[str, Any] = {
        "manifest": manifest,
        "limits": dict(limits.__dict__),
        "method": method,
        "masking_policy_version": context.masking_policy_version,
        "source_ref": source_ref,
        "occurrences": [
            {
                "path": item.path,
                "status": item.status,
                "sha256": item.raw_blob.sha256 if item.raw_blob else None,
                "decoded_sha256": item.decoded_blob.sha256 if item.decoded_blob else None,
            }
            for item in items
        ],
    }
    # Preserve an exact bounded timeout as text under the integer-only canonical profile.
    configuration["limits"]["timeout_seconds"] = str(limits.timeout_seconds)
    partial = any(item.status not in ("decoded", "imported", "excluded") for item in items)
    configuration_sha256 = digest(configuration)
    plan_payload = {
        "schema_version": "0.1.0",
        "kind": "sealed-import-input",
        "context": asdict(context),
        "manifest_artifact": own_artifact,
        "manifest_blob": asdict(own_blob),
        "items": [{**asdict(item), "diagnostics": list(item.diagnostics)} for item in items],
        "partial": partial,
        "configuration_sha256": configuration_sha256,
        "source_ref": source_ref,
    }
    plan_raw = canonical(plan_payload)
    if len(plan_raw) > 32 * 1024 * 1024:
        raise ValueError("SEALED_INPUT_PLAN_LIMIT")
    plan_artifact = _artifact(
        context,
        "__laip__/sealed-input.json",
        plan_raw,
        "utf-8",
        "laip-offline-import:sealed-input",
        context.collected_at,
    )
    plan_artifact["media_type"] = "application/json"
    validate("Artifact", plan_artifact)
    plan_blob = _blob(store, plan_raw, context, 32 * 1024 * 1024)
    if cancelled is not None and cancelled():
        raise ValueError("CANCELLED")
    return PreparedImport(
        context,
        manifest,
        own_artifact,
        own_blob,
        tuple(items),
        partial,
        configuration_sha256,
        source_ref,
        plan_artifact,
        plan_blob,
    )


def _options(
    manifest_raw: bytes, namespace: str, limits: ImportLimits
) -> tuple[dict[str, Any], ImportLimits]:
    parsed = parse_manifest(
        manifest_raw,
        system_namespace=namespace,
        max_files=limits.max_files,
        max_bytes=limits.max_bytes,
    )
    return parsed.payload, replace(limits, max_files=parsed.max_files, max_bytes=parsed.max_bytes)


def _budget(limits: ImportLimits, cancelled: Callable[[], bool] | None) -> Callable[[], bool]:
    deadline = time.monotonic() + limits.timeout_seconds

    def check() -> bool:
        if time.monotonic() >= deadline:
            raise ValueError("PREPARATION_TIMEOUT")
        return cancelled is not None and cancelled()

    return check


def prepare_zip(
    stream: BinaryIO,
    manifest_raw: bytes,
    *,
    system_namespace: str,
    store: LocalArtifactStore,
    context: ImportContext,
    limits: ImportLimits = DEFAULT_LIMITS,
    cancelled: Callable[[], bool] | None = None,
) -> PreparedImport:
    cancelled = _budget(limits, cancelled)
    if cancelled():
        raise ValueError("CANCELLED")
    manifest, effective = _options(manifest_raw, system_namespace, limits)
    records = read_zip(
        stream,
        limits=effective,
        exclusions=(*DEFAULT_EXCLUSIONS, *manifest["exclusions"]),
        cancelled=cancelled,
    )
    return _prepare(
        records, manifest, manifest_raw, store, context, effective, "offline_zip", cancelled
    )


def prepare_local(
    source_ref: str,
    configured_roots: Mapping[str, Path],
    manifest_raw: bytes,
    *,
    system_namespace: str,
    store: LocalArtifactStore,
    context: ImportContext,
    limits: ImportLimits = DEFAULT_LIMITS,
    cancelled: Callable[[], bool] | None = None,
) -> PreparedImport:
    cancelled = _budget(limits, cancelled)
    if cancelled():
        raise ValueError("CANCELLED")
    manifest, effective = _options(manifest_raw, system_namespace, limits)
    records = read_local(
        source_ref,
        configured_roots,
        limits=effective,
        exclusions=(*DEFAULT_EXCLUSIONS, *manifest["exclusions"]),
        cancelled=cancelled,
    )
    return _prepare(
        records,
        manifest,
        manifest_raw,
        store,
        context,
        effective,
        "offline_local",
        cancelled,
        source_ref,
    )


def _run_payload(prepared: PreparedImport) -> dict[str, Any]:
    return {
        "providers": [dict(IMPORT_PROVIDER)],
        "configuration_sha256": prepared.configuration_sha256,
        "masking_policy_version": prepared.context.masking_policy_version,
        "source_ref": prepared.source_ref,
    }


def register_import(prepared: PreparedImport, repository: Repository) -> dict[str, str]:
    """Register immutable owner/basis records before enqueueing the durable import job."""
    if prepared.manifest["system_namespace"] != repository.namespace:
        raise ValueError("MANIFEST_NAMESPACE_MISMATCH")
    context = prepared.context
    with repository.connection.transaction():
        repository.create_system()
        repository.connection.execute(
            "SELECT system_namespace FROM systems WHERE system_namespace=%s FOR UPDATE",
            (repository.namespace,),
        )
        existed = repository.connection.execute(
            "SELECT 1 FROM imports WHERE import_id=%s", (context.import_id,)
        ).fetchone()
        repository.create_import(context.import_id, prepared.manifest)
        if existed is None:
            repository.connection.execute(
                "UPDATE imports SET state='staging' WHERE import_id=%s", (context.import_id,)
            )
        current = repository.connection.execute(
            "SELECT import_id,payload FROM runs WHERE run_id=%s AND system_namespace=%s",
            (context.run_id, repository.namespace),
        ).fetchone()
        if current is None:
            repository.create_run(context.run_id, context.import_id, _run_payload(prepared))
        elif current[0] != context.import_id or current[1] != _run_payload(prepared):
            raise ValueError("IMPORT_RUN_CHANGED")
        repository.put_blob(prepared.manifest_blob.sha256, prepared.manifest_blob.byte_length)
        repository.put_artifact(prepared.manifest_artifact)
        repository.attach_artifact(context.run_id, prepared.manifest_artifact["artifact_id"])
        repository.put_blob(prepared.plan_blob.sha256, prepared.plan_blob.byte_length)
        repository.put_artifact(prepared.plan_artifact)
        repository.attach_artifact(context.run_id, prepared.plan_artifact["artifact_id"])
        repository._insert(
            "import_plans",
            "run_id",
            {
                "run_id": context.run_id,
                "import_id": context.import_id,
                "system_namespace": repository.namespace,
                "plan_artifact_id": prepared.plan_artifact["artifact_id"],
                "configuration_sha256": prepared.configuration_sha256,
            },
        )
        blobs = [prepared.manifest_blob, prepared.plan_blob] + [
            blob
            for item in prepared.items
            for blob in (item.raw_blob, item.decoded_blob)
            if blob is not None
        ]
        for blob in blobs:
            repository.put_blob(blob.sha256, blob.byte_length)
            repository.connection.execute(
                "INSERT INTO import_input_blobs VALUES(%s,%s) ON CONFLICT DO NOTHING",
                (context.run_id, blob.sha256),
            )
    return {"import_id": context.import_id, "run_id": context.run_id}


def persist_import(prepared: PreparedImport, repository: Repository) -> dict[str, Any]:
    """DB-only sealing; call from the durable import job's fenced publication callback."""
    if prepared.manifest["system_namespace"] != repository.namespace:
        raise ValueError("MANIFEST_NAMESPACE_MISMATCH")
    context = prepared.context
    with repository.connection.transaction():
        existing = repository.connection.execute(
            "SELECT import_id,payload FROM runs WHERE run_id=%s AND system_namespace=%s",
            (context.run_id, repository.namespace),
        ).fetchone()
        if (
            existing is None
            or existing[0] != context.import_id
            or existing[1] != _run_payload(prepared)
        ):
            raise ValueError("IMPORT_RUN_NOT_REGISTERED_OR_CHANGED")
        for item in prepared.items:
            if item.entity is not None:
                repository.put_entity(item.entity, context.run_id)
        for artifact, blob in [(prepared.manifest_artifact, prepared.manifest_blob)] + [
            (item.artifact, item.raw_blob) for item in prepared.items if item.artifact is not None
        ]:
            assert artifact is not None and blob is not None
            repository.put_blob(blob.sha256, blob.byte_length)
            repository.put_artifact(artifact)
            repository.attach_artifact(context.run_id, artifact["artifact_id"])
        for item in prepared.items:
            if item.decoded_blob is not None:
                repository.put_blob(item.decoded_blob.sha256, item.decoded_blob.byte_length)
            if item.origin_map is not None:
                repository.put_origin_map(item.origin_map)
            if item.evidence is not None:
                repository.put_evidence(item.evidence)
            values = {
                "import_id": context.import_id,
                "locator": item.path,
                "artifact_id": item.artifact["artifact_id"] if item.artifact else None,
                "system_namespace": repository.namespace,
                "scope_state": item.scope_state,
                "payload": {
                    "status": item.status,
                    "diagnostics": list(item.diagnostics),
                    "metadata": item.metadata,
                    "decoded_storage_key": item.decoded_blob.storage_key
                    if item.decoded_blob
                    else None,
                },
            }
            # Composite key replay uses immutable equality, never updates a status in place.

            old = repository.connection.execute(
                "SELECT artifact_id,system_namespace,scope_state,payload FROM import_entries "
                "WHERE import_id=%s AND locator=%s",
                (context.import_id, item.path),
            ).fetchone()
            if old is None:
                repository.connection.execute(
                    "INSERT INTO import_entries(import_id,locator,artifact_id,system_namespace,"
                    "scope_state,payload) "
                    "VALUES(%s,%s,%s,%s,%s,%s)",
                    (
                        context.import_id,
                        item.path,
                        values["artifact_id"],
                        repository.namespace,
                        item.scope_state,
                        Jsonb(values["payload"]),
                    ),
                )
            elif tuple(old) != (
                values["artifact_id"],
                repository.namespace,
                item.scope_state,
                values["payload"],
            ):
                raise ValueError("IDENTITY_PAYLOAD_MISMATCH")
    return {
        "import_id": context.import_id,
        "run_id": context.run_id,
        "outcome": "partial" if prepared.partial else "succeeded",
        "artifact_count": len(prepared.items),
    }


def restore_import(
    run_id: str, repository: Repository, store: LocalArtifactStore
) -> PreparedImport:
    """Verify and reload a durable accepted input without rereading the caller source."""
    row = repository.connection.execute(
        "SELECT a.payload,i.payload,p.configuration_sha256 FROM import_plans p "
        "JOIN artifacts a ON a.artifact_id=p.plan_artifact_id "
        "JOIN imports i ON i.import_id=p.import_id WHERE p.run_id=%s AND p.system_namespace=%s",
        (run_id, repository.namespace),
    ).fetchone()
    if row is None:
        raise ValueError("IMPORT_INPUT_UNAVAILABLE")
    plan_artifact, manifest, configuration_sha256 = row
    plan_blob = BlobRef(
        plan_artifact["sha256"],
        plan_artifact["byte_length"],
        f"objects/{plan_artifact['sha256'][:2]}/{plan_artifact['sha256']}",
    )
    if plan_blob.byte_length > 32 * 1024 * 1024:
        raise ValueError("SEALED_INPUT_PLAN_LIMIT")
    with store.open(plan_blob) as stream:
        plan = loads(stream.read(32 * 1024 * 1024 + 1).decode("utf-8"))
    if (
        not isinstance(plan, dict)
        or plan.get("schema_version") != "0.1.0"
        or plan.get("kind") != "sealed-import-input"
        or plan["context"]["run_id"] != run_id
        or plan["configuration_sha256"] != configuration_sha256
    ):
        raise ValueError("INVALID_SEALED_INPUT_PLAN")
    context = ImportContext(**plan["context"])
    parse_manifest(canonical(manifest), system_namespace=repository.namespace)
    items = []
    for record in plan["items"]:
        if set(record) != {field.name for field in fields(PreparedItem)}:
            raise ValueError("INVALID_SEALED_INPUT_PLAN")
        value = dict(record)
        for name in ("raw_blob", "decoded_blob"):
            if value[name] is not None:
                value[name] = BlobRef(**value[name])
                store.verify(value[name])
        value["diagnostics"] = tuple(value["diagnostics"])
        for name, kind in (
            ("artifact", "Artifact"),
            ("origin_map", "OriginMap"),
            ("entity", "Entity"),
            ("evidence", "Evidence"),
        ):
            if value[name] is not None:
                validate(kind, value[name])
        items.append(PreparedItem(**value))
    manifest_blob = BlobRef(**plan["manifest_blob"])
    store.verify(manifest_blob)
    return PreparedImport(
        context,
        manifest,
        plan["manifest_artifact"],
        manifest_blob,
        tuple(items),
        plan["partial"],
        configuration_sha256,
        plan["source_ref"],
        plan_artifact,
        plan_blob,
    )
