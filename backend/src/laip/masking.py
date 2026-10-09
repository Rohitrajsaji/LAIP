"""Bounded local display policy; canonical evidence is never modified."""

import re
from typing import Any

POLICY = "local-credential-redaction-v1"
_KEYS = {
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "private_key",
    "access_token",
    "refresh_token",
    "bearer_token",
}
_ASSIGNMENT = re.compile(
    r"\b(password|passwd|token|api[_-]?key|secret|authorization)[\"']?\s*[:=]\s*"
    r"(?:\"[^\"\n]*\"|'[^'\n]*'|[^\s,;]+)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_URI = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*://)[^\s/@:]+:[^\s/@]+@")


def mask(value: Any, *, max_nodes: int = 100000, max_depth: int = 128) -> Any:
    nodes = 0

    def project(item: Any, depth: int) -> Any:
        nonlocal nodes
        nodes += 1
        if nodes > max_nodes or depth > max_depth:
            raise ValueError("MASKING_RESOURCE_LIMIT")
        if isinstance(item, str):
            item = _ASSIGNMENT.sub(lambda found: found.group(1) + "=[REDACTED]", item)
            item = _BEARER.sub("Bearer [REDACTED]", item)
            return _URI.sub(r"\1[REDACTED]@", item)
        if isinstance(item, list):
            return [project(child, depth + 1) for child in item]
        if isinstance(item, dict):
            return {
                key: "[REDACTED]"
                if key.lower().replace("-", "_") in _KEYS
                else project(child, depth + 1)
                for key, child in item.items()
            }
        return item

    return project(value, 0)
