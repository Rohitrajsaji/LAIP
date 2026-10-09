"""Strict versioned offline manifests and descriptive metadata envelopes."""

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from jsonschema.exceptions import ValidationError  # type: ignore[import-untyped]

from laip.canonical import identity_id, loads
from laip.persistence import validate

METADATA_KINDS = frozenset({"inventory", "schema", "binding", "job", "dsp_pgmref"})


@dataclass(frozen=True)
class ParsedManifest:
    payload: dict[str, Any]
    max_files: int
    max_bytes: int


@dataclass(frozen=True)
class MetadataResult:
    payload: dict[str, Any]
    records: tuple[dict[str, Any], ...]


def normalized_relative_path(path: str) -> str:
    """Require one unambiguous portable path spelling; never silently repair it."""
    if (
        not isinstance(path, str)
        or not path
        or len(path.encode("utf-8")) > 4096
        or path != unicodedata.normalize("NFC", path)
        or "\\" in path
        or ":" in path
        or path.startswith("/")
        or any(ord(char) < 32 or ord(char) == 127 for char in path)
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        raise ValueError("INVALID_RELATIVE_PATH")
    return path


def _json(raw: bytes, max_bytes: int) -> dict[str, Any]:
    if len(raw) > max_bytes:
        raise ValueError("INPUT_BYTE_LIMIT")
    try:
        payload = loads(raw.decode("utf-8"))
    except (UnicodeError, RecursionError) as exc:
        raise ValueError("INVALID_JSON_ENCODING_OR_DEPTH") from exc
    if not isinstance(payload, dict):
        raise ValueError("INVALID_JSON_OBJECT")
    return payload


def _identity(identity: dict[str, Any], namespace: str) -> str:
    if identity["system_namespace"] != namespace:
        raise ValueError("IDENTITY_NAMESPACE_MISMATCH")
    parts = identity["qualified_identity"]
    if any(
        part != unicodedata.normalize("NFC", part)
        or len(part) > 256
        or any(ord(char) < 32 for char in part)
        for part in parts
    ):
        raise ValueError("INVALID_IDENTITY")
    return identity_id(identity)


def parse_manifest(
    raw: bytes,
    *,
    system_namespace: str,
    max_manifest_bytes: int = 1024 * 1024,
    max_files: int = 10000,
    max_bytes: int = 256 * 1024 * 1024,
) -> ParsedManifest:
    payload = _json(raw, max_manifest_bytes)
    try:
        validate("ImportManifest", payload)
    except (ValidationError, RecursionError) as exc:
        raise ValueError("INVALID_MANIFEST_SCHEMA") from exc
    if payload["system_namespace"] != system_namespace:
        raise ValueError("MANIFEST_NAMESPACE_MISMATCH")
    if min(max_files, max_bytes) < 1:
        raise ValueError("INVALID_OPERATOR_LIMIT")
    ceiling_files = min(max_files, payload["limits"]["max_files"])
    ceiling_bytes = min(max_bytes, payload["limits"]["max_bytes"])
    if len(payload["artifacts"]) > ceiling_files:
        raise ValueError("MANIFEST_FILE_LIMIT")
    aliases: set[str] = set()
    paths: dict[str, set[str]] = {}
    identities: set[str] = set()
    source_member_ids: set[str] = set()
    for artifact in payload["artifacts"]:
        path = normalized_relative_path(artifact["relative_path"])
        alias = path.casefold()
        if alias in aliases:
            raise ValueError("DUPLICATE_ARTIFACT_PATH")
        aliases.add(alias)
        member_ids: set[str] = set()
        for field in ("source_member_identity", "object_identity"):
            identity = artifact[field]
            if identity is None:
                continue
            key = _identity(identity, system_namespace)
            if field == "source_member_identity":
                if identity["kind"] != "SourceMember" or len(identity["qualified_identity"]) != 3:
                    raise ValueError("INVALID_SOURCE_MEMBER_IDENTITY")
                if key in source_member_ids:
                    raise ValueError("DUPLICATE_ARTIFACT_IDENTITY")
                source_member_ids.add(key)
            elif identity["kind"] in {"System", "Application", "SourceMember"}:
                raise ValueError("INVALID_OBJECT_IDENTITY")
            identities.add(key)
            member_ids.add(key)
        paths[path] = member_ids
    libraries = payload["library_list"]
    if len(libraries) > 1024 or any(
        len(name) > 256
        or name != unicodedata.normalize("NFC", name)
        or any(ord(char) < 32 for char in name)
        for name in libraries
    ):
        raise ValueError("LIBRARY_LIST_LIMIT")
    if len({unicodedata.normalize("NFC", name).casefold() for name in libraries}) != len(libraries):
        raise ValueError("DUPLICATE_LIBRARY")
    memberships: set[tuple[str, str, str]] = set()
    for membership in payload["application_memberships"]:
        app = membership["application_identity"]
        if app["kind"] != "Application":
            raise ValueError("INVALID_MEMBERSHIP_APPLICATION")
        app_id = _identity(app, system_namespace)
        member_id = _identity(membership["member_identity"], system_namespace)
        evidence_path = normalized_relative_path(membership["evidence_path"])
        membership_key = (app_id, member_id, evidence_path)
        if (
            member_id not in identities
            or evidence_path not in paths
            or membership_key in memberships
        ):
            raise ValueError("INVALID_MEMBERSHIP_REFERENCE")
        memberships.add(membership_key)
    for exclusion in payload["exclusions"]:
        # Glob metacharacters are descriptive; traversal and absolute forms remain invalid.
        normalized_relative_path(exclusion)
    return ParsedManifest(payload, ceiling_files, ceiling_bytes)


def parse_metadata(
    raw: bytes, *, kind: str, max_bytes: int = 16 * 1024 * 1024, max_records: int = 100000
) -> MetadataResult:
    payload = _json(raw, max_bytes)
    if (
        kind not in METADATA_KINDS
        or set(payload) != {"schema_version", "kind", "records"}
        or payload["schema_version"] != "0.1.0"
        or payload["kind"] != kind
        or not isinstance(payload["records"], list)
    ):
        raise ValueError("INVALID_METADATA_ENVELOPE")
    records = payload["records"]
    if len(records) > max_records:
        raise ValueError("METADATA_RECORD_LIMIT")
    if any(not isinstance(record, dict) or not record for record in records):
        raise ValueError("INVALID_METADATA_RECORD")
    # Each record is an opaque canonical JSON object. No imported name becomes an instruction.
    if any(
        not re.fullmatch(r"[A-Za-z0-9_. -]{1,128}", key) for record in records for key in record
    ):
        raise ValueError("INVALID_METADATA_FIELD")
    return MetadataResult(payload, tuple(records))
