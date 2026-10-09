"""Version compatibility and claim-local support contract acceptance."""

import copy
import json
from pathlib import Path

import pytest
from jsonschema import ValidationError

from laip.analysis_facts import validate_fact_bundle
from laip.claim_support import support_fingerprint, validate_claim_support
from laip.persistence import validate

ROOT = Path(__file__).resolve().parents[2]


def example():
    return json.loads((ROOT / "docs/contracts/0.2.0/banking.example.json").read_text())


def test_legacy_remains_valid():
    bundle = json.loads((ROOT / "docs/contracts/0.1.0/synthetic.example.json").read_text())
    for entity in bundle["entities"]:
        assert validate("Entity", entity) == entity
    for revision in bundle["rules"]:
        assert validate("RuleRevision", revision) == revision


def test_source_only_rule_and_record_format():
    bundle = example()
    validate("Entity", bundle["record_format"])
    rule = validate("RuleRevision", bundle["source_rule"])
    assert rule["program_entity_ids"] == []
    assert rule["subject_entity_ids"]
    assert rule["claim_support"]["state"] == "supported_within_profile"


def test_old_schema_cannot_accept_new_format_or_source_rule():
    bundle = example()
    for kind, record in [
        ("Entity", bundle["record_format"]),
        ("RuleRevision", bundle["source_rule"]),
    ]:
        record["schema_version"] = "0.1.0"
        with pytest.raises(ValidationError):
            validate(kind, record)


@pytest.mark.parametrize("version", [None, "99.0.0"])
def test_unknown_version_fails(version):
    entity = example()["record_format"]
    if version is None:
        del entity["schema_version"]
    else:
        entity["schema_version"] = version
    with pytest.raises(ValueError, match="UNSUPPORTED_SCHEMA_VERSION"):
        validate("Entity", entity)


def test_claim_support_fingerprint_changes_for_binding_or_barrier():
    support = example()["source_rule"]["claim_support"]
    validate_claim_support(support)
    changed = copy.deepcopy(support)
    changed["barriers"].append({"code": "UNKNOWN_EFFECT", "evidence_ids": support["evidence_ids"]})
    changed["state"] = "blocked"
    assert support_fingerprint(support) != support_fingerprint(changed)
    changed = copy.deepcopy(support)
    changed["resolution_decisions"].append(
        {"reference": "SFL_OPT", "resolution": "missing", "target_entity_id": None}
    )
    assert support_fingerprint(support) != support_fingerprint(changed)


def test_claim_requires_subject_and_evidence_and_no_unknown_properties():
    support = example()["source_rule"]["claim_support"]
    for key, value in [
        ("subject_entity_ids", []),
        ("evidence_ids", []),
        ("state", "verified"),
        ("execute", True),
    ]:
        changed = copy.deepcopy(support)
        changed[key] = value
        with pytest.raises((ValidationError, ValueError)):
            validate_claim_support(changed)


def test_fact_bundle_namespace_and_bounds():
    facts = example()["fact_bundle"]
    assert validate_fact_bundle(facts) == facts
    invalid = copy.deepcopy(facts)
    invalid["system_namespace"] = ""
    with pytest.raises(ValidationError):
        validate_fact_bundle(invalid)
    invalid = copy.deepcopy(facts)
    invalid["facts"][0]["execute"] = True
    with pytest.raises(ValidationError):
        validate_fact_bundle(invalid)


def test_explicit_consumer_gate():
    from laip.schema_registry import require_compatible_version

    require_compatible_version("0.1.0", {"0.1.0"})
    require_compatible_version("0.2.0", {"0.1.0", "0.2.0"})
    with pytest.raises(ValueError, match="INCOMPATIBLE_SNAPSHOT_VERSION"):
        require_compatible_version("0.1.0", {"0.1.0", "0.2.0"})
    with pytest.raises(ValueError, match="UNSUPPORTED_SCHEMA_VERSION"):
        require_compatible_version("0.2.0", {"9.0.0"})
    with pytest.raises(ValueError, match="UNSUPPORTED_RECORD_KIND"):
        validate("Imaginary", {"schema_version": "0.2.0"})


def test_duplicate_facts_fail():
    facts = example()["fact_bundle"]
    facts["facts"].append(copy.deepcopy(facts["facts"][0]))
    with pytest.raises(ValueError, match="DUPLICATE_FACT"):
        validate_fact_bundle(facts)


def test_kind_specific_fact_payload_is_closed():
    facts = example()["fact_bundle"]
    facts["facts"][0]["data"]["command"] = "run imported program"
    with pytest.raises(ValidationError):
        validate_fact_bundle(facts)


def test_unresolved_support_is_not_a_resolved_target():
    support = example()["source_rule"]["claim_support"]
    support["resolution_decisions"] = [
        {
            "reference": "missing",
            "resolution": "dynamic",
            "target_entity_id": support["subject_entity_ids"][0],
        }
    ]
    with pytest.raises(ValidationError):
        validate_claim_support(support)


def test_schema_mirror_and_new_identity_do_not_retype_old_entities():
    from laip.canonical import identity_id
    from laip.schema_registry import schema_for_version

    schema = schema_for_version("0.2.0")
    assert "RecordFormat" in schema["$defs"]["Identity"]["properties"]["kind"]["enum"]
    assert (
        "RecordFormat"
        not in schema_for_version("0.1.0")["$defs"]["Identity"]["properties"]["kind"]["enum"]
    )
    assert (ROOT / "docs/contracts/0.2.0/laip.schema.json").read_bytes() == (
        ROOT / "backend/src/laip/contracts/0.2.0/laip.schema.json"
    ).read_bytes()
    identity = example()["record_format"]["identity"]
    assert identity_id(identity) == example()["record_format"]["entity_id"]
    changed = copy.deepcopy(identity)
    changed["system_namespace"] += ":other"
    assert identity_id(changed) != identity_id(identity)


def test_unversioned_legacy_nested_value_types_still_validate():
    bundle = json.loads((ROOT / "docs/contracts/0.1.0/synthetic.example.json").read_text())
    provider = bundle["runs"][0]["providers"][0]
    assert "schema_version" not in provider
    assert validate("ProviderRef", provider) == provider


@pytest.mark.parametrize("version", [[], {}, 1, False])
def test_malformed_schema_version_is_named_error(version):
    entity = example()["record_format"]
    entity["schema_version"] = version
    with pytest.raises(ValueError, match="UNSUPPORTED_SCHEMA_VERSION"):
        validate("Entity", entity)


def test_empty_snapshot_and_schema_accessor_immutability():
    from laip.schema_registry import require_compatible_version, schema_for_version

    require_compatible_version("0.1.0", set())
    schema_for_version("0.2.0")["$defs"]["Identity"]["properties"]["kind"]["enum"].clear()
    assert (
        "RecordFormat"
        in schema_for_version("0.2.0")["$defs"]["Identity"]["properties"]["kind"]["enum"]
    )


def test_frozen_legacy_contract_bytes():
    import hashlib

    old_schema_hash = "dc2888453ba3ba8b3d140079d6e919986e741375c94df636c2f3e6384b348c09"
    old_example_hash = "f05c927a88a5258d1974c205c1f744ee6db1a8804b9e909975973f3a8888cc51"
    old_export_hash = "00e7b9625e071d930a0e9824a40d165ecfb465133c7f89bac778bdc20d11103e"
    expected = {
        "docs/contracts/0.1.0/laip.schema.json": old_schema_hash,
        "backend/src/laip/contracts/laip.schema.json": old_schema_hash,
        "docs/contracts/0.1.0/synthetic.example.json": old_example_hash,
        "backend/src/laip/contracts/export.schema.json": old_export_hash,
    }
    for path, sha256 in expected.items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == sha256


@pytest.mark.parametrize("resolution", ["dynamic", "missing", "ambiguous", "syntactic"])
def test_fact_unresolved_target_must_remain_null(resolution):
    facts = example()["fact_bundle"]
    fact = facts["facts"][0]
    target = example()["record_format"]["entity_id"]
    fact["kind"] = "reference"
    fact["data"] = {
        "name": "unknown",
        "relationship": "CALLS",
        "resolution": resolution,
        "target_entity_id": target,
        "candidate_entity_ids": [],
    }
    with pytest.raises(ValidationError):
        validate_fact_bundle(facts)


def test_resolved_reference_and_ambiguity_candidates_are_typed():
    facts = example()["fact_bundle"]
    fact = facts["facts"][0]
    target = example()["record_format"]["entity_id"]
    fact["kind"] = "reference"
    fact["data"] = {
        "name": "SFLRCD",
        "relationship": "CONTAINS",
        "resolution": "resolved",
        "target_entity_id": target,
        "candidate_entity_ids": [],
    }
    validate_fact_bundle(facts)
    fact["data"]["target_entity_id"] = None
    with pytest.raises(ValidationError):
        validate_fact_bundle(facts)
    fact["data"].update(resolution="ambiguous", candidate_entity_ids=[target, target])
    with pytest.raises(ValidationError):
        validate_fact_bundle(facts)
