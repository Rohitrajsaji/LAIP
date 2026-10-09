import base64
import io
import json
import zipfile

from laip.analyst_fixture import fixture_payload
from laip.artifacts import LocalArtifactStore
from laip.canonical import identity_id
from laip.cl_analysis import analyze_cl
from laip.dds_analysis import analyze_dds
from laip.import_manifest import parse_manifest
from laip.inventory import build_inventory
from laip.rpgle_analysis import analyze_rpgle
from laip.rules import extract_rules
from laip.source_import import ImportContext, prepare_zip


def test_fixture_is_repeatable_namespace_qualified_and_inert():
    payload = fixture_payload("synthetic:browser")
    assert payload == fixture_payload("synthetic:browser")
    manifest = payload["manifest"]
    parse_manifest(json.dumps(manifest).encode(), system_namespace="synthetic:browser")
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(payload["archive_base64"]))) as archive:
        assert sorted(archive.namelist()) == sorted(
            a["relative_path"] for a in manifest["artifacts"]
        )
        assert all(info.date_time == (2026, 10, 8, 0, 0, 0) for info in archive.infolist())
    assert fixture_payload("other:namespace")["manifest"]["system_namespace"] == "other:namespace"


def test_fixture_real_providers_produce_rules_and_partial_dynamic_path(tmp_path):
    tmp_path.chmod(0o700)
    payload = fixture_payload("synthetic:browser")
    store = LocalArtifactStore(tmp_path)
    prepared = prepare_zip(
        io.BytesIO(base64.b64decode(payload["archive_base64"])),
        json.dumps(payload["manifest"]).encode(),
        system_namespace="synthetic:browser",
        store=store,
        context=ImportContext(
            "fixture", "import_run", "2026-10-08T00:00:00Z", "fixture-v1", "job", 1
        ),
    )
    inventory = build_inventory(prepared, run_id="inventory_run")
    assert inventory.entities
    for entry in payload["manifest"]["artifacts"]:
        path = entry["relative_path"]
        if entry["language"] == "CLLE":
            result = analyze_cl(prepared, path, store, run_id="cl_run")
            assert result.ir["nodes"]
        elif entry["language"] == "RPGLE":
            result = analyze_rpgle(prepared, path, store, run_id="rpg_run")
            rules = extract_rules(
                result,
                run_id=result.run_id,
                system_namespace=result.system_namespace,
                created_at="2026-10-08T00:00:00Z",
                program_entity_ids=(identity_id(entry["object_identity"]),),
            )
            assert rules["rule_revisions"]
        elif entry["language"] == "DDS":
            result = analyze_dds(
                prepared, path, store, run_id="dds_run", file_type=entry["dialect"]
            )
            assert result.ir["nodes"]


def test_customer_validation_fixture_exposes_collisions_and_missing_include(tmp_path):
    tmp_path.chmod(0o700)
    payload = fixture_payload("synthetic:acceptance")
    store = LocalArtifactStore(tmp_path)
    prepared = prepare_zip(
        io.BytesIO(base64.b64decode(payload["archive_base64"])),
        json.dumps(payload["manifest"]).encode(),
        system_namespace="synthetic:acceptance",
        store=store,
        context=ImportContext(
            "fixture", "import_run", "2026-10-08T00:00:00Z", "fixture-v1", "job", 1
        ),
    )
    inventory = build_inventory(prepared, run_id="inventory_run")
    orders = [
        e
        for e in inventory.entities
        if e["identity"]["kind"] == "Program" and e["identity"]["qualified_identity"][-1] == "ORDER"
    ]
    assert {e["identity"]["qualified_identity"][0] for e in orders} == {"DEMO", "OTHER"}
    assert len({e["entity_id"] for e in orders}) == 2
    ambiguous = [d for d in inventory.dependencies if d["resolution"] == "ambiguous"]
    assert ambiguous and set(ambiguous[0]["candidate_entity_ids"]) == {
        e["entity_id"] for e in orders
    }
    assert ambiguous[0]["to_entity_id"] is None and ambiguous[0]["evidence_ids"]
    assert len(inventory.accounting) == len(prepared.items)
    result = analyze_rpgle(prepared, "DEMO/QRPGLESRC/CUSTOMER.rpgle", store, run_id="missing_run")
    assert "INCLUDE_MISSING" in result.ir["include_diagnostics"]
    assert not result.ir["conclusions_allowed"]
    assert any(e["metadata"]["barrier"] for e in result.evidence)
