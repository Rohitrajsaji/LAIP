import json
from pathlib import Path

import pytest

from laip.canonical import canonical, digest, identity_id, loads

ROOT = Path(__file__).resolve().parents[2]


def test_golden_cases():
    fixture = json.loads((ROOT / "docs/contracts/0.1.0/canonicalization.example.json").read_text())
    for case in fixture["cases"]:
        assert canonical(case["input"]).decode() == case["canonical_utf8"]
        assert digest(case["input"]) == case["sha256"]
    for case in fixture["reject_cases"]:
        with pytest.raises(ValueError):
            canonical(case["input"])


def test_normalization_and_invalid_json():
    assert canonical({"n": 1.0}) == b'{"n":1}'
    assert loads('{"n":1.0}') == {"n": 1}
    with pytest.raises(ValueError):
        loads('{"n":9007199254740991.1}')
    assert identity_id(
        {"system_namespace": "x", "kind": "Procedure", "qualified_identity": ["Cafe\u0301"]}
    ) == identity_id({"system_namespace": "x", "kind": "Procedure", "qualified_identity": ["Café"]})
    for invalid in ['{"x":1,"x":2}', "NaN", "Infinity", '{"x":"\\ud800"}']:
        with pytest.raises(ValueError):
            loads(invalid)
    for invalid in [float("nan"), float("inf"), "\ud800", {1: "x"}, object()]:
        with pytest.raises(ValueError):
            canonical(invalid)
