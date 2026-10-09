"""Strict authored canonical schema selection, preserving historical versions."""

import copy
import json
from functools import lru_cache
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker  # type: ignore[import-untyped]

from laip.canonical import normalize

SUPPORTED_VERSIONS = ("0.1.0", "0.2.0")


@lru_cache(maxsize=2)
def _cached_schema(version: str) -> dict[str, Any]:
    if version not in SUPPORTED_VERSIONS:
        raise ValueError("UNSUPPORTED_SCHEMA_VERSION")
    path = (
        "contracts/laip.schema.json" if version == "0.1.0" else "contracts/0.2.0/laip.schema.json"
    )
    return json.loads(files("laip").joinpath(path).read_text())  # type: ignore[no-any-return]


def schema_for_version(version: str) -> dict[str, Any]:
    if not isinstance(version, str) or version not in SUPPORTED_VERSIONS:
        raise ValueError("UNSUPPORTED_SCHEMA_VERSION")
    return copy.deepcopy(_cached_schema(version))


def validate_record(
    kind: str, record: dict[str, Any], *, version_key: str = "schema_version"
) -> dict[str, Any]:
    record = normalize(record)
    version = record.get(version_key)
    # Nested legacy value types (ProviderRef, SourceLocation, etc.) have no version field.
    legacy_definition = schema_for_version("0.1.0")["$defs"].get(kind, {})
    if (
        version is None
        and version_key == "schema_version"
        and (
            "schema_version" not in legacy_definition.get("properties", {})
            and kind in schema_for_version("0.1.0")["$defs"]
        )
    ):
        version = "0.1.0"
    if not isinstance(version, str):
        raise ValueError("UNSUPPORTED_SCHEMA_VERSION")
    schema = schema_for_version(version)
    if kind not in schema["$defs"]:
        raise ValueError("UNSUPPORTED_RECORD_KIND")
    Draft202012Validator(
        {"$ref": "#/$defs/" + kind, "$defs": schema["$defs"]},
        format_checker=FormatChecker(),
    ).validate(record)
    return record


def require_compatible_version(requested_version: str, record_versions: set[str]) -> None:
    schema_for_version(requested_version)
    if not record_versions.issubset(set(SUPPORTED_VERSIONS)):
        raise ValueError("UNSUPPORTED_SCHEMA_VERSION")
    if requested_version == "0.1.0" and not record_versions.issubset({"0.1.0"}):
        raise ValueError("INCOMPATIBLE_SNAPSHOT_VERSION")
