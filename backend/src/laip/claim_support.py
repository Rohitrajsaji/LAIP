"""Versioned claim support, independent of certainty and analyst review status."""

from typing import Any

from laip.canonical import digest
from laip.schema_registry import validate_record


def validate_claim_support(record: dict[str, Any]) -> dict[str, Any]:
    return validate_record("ClaimSupport", record, version_key="support_schema_version")


def support_fingerprint(record: dict[str, Any]) -> str:
    """Support-slice digest; repository adds closed evidence/provider/run basis."""
    return digest(
        {"support_schema_version": "0.2.0", "claim_support": validate_claim_support(record)}
    )
