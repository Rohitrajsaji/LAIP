"""Bounded versioned analyzer facts; validation never executes provider payloads."""

from typing import Any

from laip.canonical import canonical
from laip.schema_registry import validate_record


def validate_fact_bundle(record: dict[str, Any]) -> dict[str, Any]:
    record = validate_record("AnalyzerFactBundle", record, version_key="fact_schema_version")
    if len(canonical(record)) > 32 * 1024 * 1024:
        raise ValueError("FACT_OUTPUT_LIMIT")
    ids = [fact["fact_id"] for fact in record["facts"]]
    if len(ids) != len(set(ids)):
        raise ValueError("DUPLICATE_FACT")
    return record
