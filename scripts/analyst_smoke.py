"""Explicit synthetic import-to-export check through the real localhost browser proxy.

Adds synthetic demo history to the configured local analyst namespace.
This HTTP check does not certify visual, keyboard or browser acceptance.
"""

import hashlib
import io
import json
import os
import time
import urllib.request
import zipfile

from laip.exports import ExportBundle, validate_bundle
from laip.persistence import validate

port = int(os.environ.get("LAIP_WEB_PORT", "3000"))
if not 1 <= port <= 65535:
    raise ValueError("Invalid local port")
base = f"http://127.0.0.1:{port}"


def call(path, arguments=None, binary=False):
    body = (
        json.dumps({"schema_version": "0.1.0", **arguments}).encode()
        if arguments
        else None
    )
    request = urllib.request.Request(
        base + "/api/analyst/" + path,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Origin": base,
            "Sec-Fetch-Site": "same-origin",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        data = response.read(32 * 1024 * 1024 + 1)
        assert len(data) <= 32 * 1024 * 1024
        return data if binary else json.loads(data)["data"]


def completed(group, ident, key):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        workspace = call("workspace")
        assert workspace["ai_enabled"] is False
        row = next((item for item in workspace[group] if item[key] == ident), None)
        if row and row["state"] in {"succeeded", "partial"}:
            return workspace
        if row and row["state"] in {"failed", "cancelled"}:
            raise RuntimeError("Synthetic workflow did not complete")
        time.sleep(1)
    raise RuntimeError("Synthetic workflow timed out")


fixture = call("fixtures/analyst")
accepted = call(
    "imports",
    {"archive_base64": fixture["archive_base64"], "manifest": fixture["manifest"]},
)
completed("runs", accepted["run_id"], "run_id")
analysis = call("runs", {"import_id": accepted["import_id"]})
workspace = completed("runs", analysis["run_id"], "run_id")
snapshot = workspace["snapshot_id"]
suffix = "?schema_version=0.2.0&snapshot_id=" + snapshot
inventory = call("inventory" + suffix)
assert inventory["records"]
dependencies = call("dependencies" + suffix)
assert any(r["payload"]["resolution"] == "dynamic" for r in dependencies["records"])
rule = next(
    item["revision"]
    for item in call("rules" + suffix)["rules"]
    if item["revision"].get("program_entity_ids")
)
assert call("entities/" + rule["program_entity_ids"][0] + suffix)["records"]
evidence = call("evidence/" + rule["evidence_ids"][0] + suffix)
span = next(
    r["payload"]["source_locations"][0]
    for r in evidence["records"]
    if r["type"] == "evidence"
)
source = call(
    "artifacts/"
    + span["artifact_id"]
    + "/source"
    + suffix
    + "&start_line=1&end_line=200"
)
assert source["text"] and source["origin_map_id"]
correction = call(
    "rules/" + rule["rule_entity_id"] + "/revisions",
    {
        # Correction transport stays 0.1.0; the persisted revision remains 0.2.0.
        "snapshot_id": snapshot,
        "expected_revision_id": rule["revision_id"],
        "name": rule["name"],
        "description": "Synthetic deployment check correction; source conditions and actions retained.",
        "conditions": rule["conditions"],
        "actions": rule["actions"],
        "evidence_ids": rule["evidence_ids"],
        "reason": "Explicit synthetic Docker acceptance check",
    },
)
snapshot = correction["snapshot_id"]
revisions = call("rules?schema_version=0.2.0&snapshot_id=" + snapshot)["rules"]
assert any(len(item["revisions"]) >= 2 for item in revisions)
assert call(
    "retrieval/query",
    {
        "schema_version": "0.2.0",
        "snapshot_id": snapshot,
        "mode": "keyword",
        "query": "Synthetic",
        "limit": 20,
    },
)["records"]
exported = call(
    "exports",
    {"schema_version": "0.2.0", "snapshot_id": snapshot, "source_included": False},
)
completed("exports", exported["export_id"], "export_id")
raw = call("exports/" + exported["export_id"] + "/download", binary=True)
with zipfile.ZipFile(io.BytesIO(raw)) as archive:
    manifest = json.loads(archive.read("manifest.json"))
    validate("ExportManifest", manifest)
    assert manifest["source_included"] is False
    validate_bundle(
        ExportBundle(
            manifest=manifest,
            files={name: archive.read(name) for name in archive.namelist()},
        )
    )
    for item in manifest["files"]:
        data = archive.read(item["relative_path"])
        assert len(data) == item["byte_length"]
        assert hashlib.sha256(data).hexdigest() == item["sha256"]
if destination := os.environ.get("LAIP_SYNTHETIC_EXPORT_PATH"):
    from pathlib import Path

    Path(destination).write_bytes(raw)

print(
    "Synthetic Docker HTTP workflow passed: import, partial analysis, source/evidence, immutable correction, retrieval, hashed export; AI off."
)
