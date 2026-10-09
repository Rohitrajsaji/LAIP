"""Bounded semantic views: source bytes remain a separate authorized capability."""

from typing import Any

from laip.masking import mask

_SOURCE_KEYS = {"physical_lines", "raw_source", "source_text", "source_lines", "body", "text"}


def semantic_payload(payload: dict[str, Any]) -> dict[str, Any]:
    def scrub(value: Any, private_metadata: bool = False) -> Any:
        if isinstance(value, dict):
            return {
                key: scrub(item, private_metadata or key in {"metadata", "attributes"})
                for key, item in value.items()
                if not (private_metadata and key in _SOURCE_KEYS)
            }
        if isinstance(value, list):
            return [scrub(item, private_metadata) for item in value]
        return value

    return mask(scrub(payload))  # type: ignore[no-any-return]
