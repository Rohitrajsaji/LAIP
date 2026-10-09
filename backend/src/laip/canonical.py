"""RFC8785 string/key rules with LAIP's safe-integer numeric profile."""

import hashlib
import json
import math
import unicodedata
from decimal import Decimal
from typing import Any

SAFE = 9007199254740991


def normalize(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        if abs(value) > SAFE:
            raise ValueError("UNSAFE_INTEGER")
        return value
    if isinstance(value, Decimal):
        if not value.is_finite() or value != value.to_integral_value() or abs(value) > SAFE:
            raise ValueError("INVALID_NUMBER")
        return int(value)
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer() or abs(value) > SAFE:
            raise ValueError("INVALID_NUMBER")
        return int(value)
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ValueError("INVALID_UNICODE") from exc
        return value
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise ValueError("INVALID_KEY")
        for key in value:
            normalize(key)
        return {
            key: normalize(value[key]) for key in sorted(value, key=lambda k: k.encode("utf-16-be"))
        }
    raise ValueError("INVALID_VALUE")


def canonical(value: Any) -> bytes:
    return json.dumps(
        normalize(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def loads(value: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, item in items:
            if key in result:
                raise ValueError("DUPLICATE_KEY")
            result[key] = item
        return result

    def invalid(_: str) -> Any:
        raise ValueError("INVALID_NUMBER")

    return normalize(
        json.loads(value, object_pairs_hook=pairs, parse_constant=invalid, parse_float=Decimal)
    )


def identity_id(identity: dict[str, Any]) -> str:
    identity = dict(identity)
    identity["qualified_identity"] = [
        unicodedata.normalize("NFC", s) for s in identity["qualified_identity"]
    ]
    return "ent_" + digest(identity)


def record_id(prefix: str, record: dict[str, Any], id_field: str) -> str:
    return prefix + digest({k: v for k, v in record.items() if k != id_field})
