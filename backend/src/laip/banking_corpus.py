"""Frozen private corpus inputs; inert bytes, explicit test-only identities."""

import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any, cast

FIXTURES = Path(__file__).resolve().parents[2] / "tests/fixtures/banking"


def load_oracle() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((FIXTURES / "oracle.json").read_text()))


def load_corpus(directory: Path) -> dict[str, bytes]:
    """Reject absent, symlinked, oversized or changed originals; never normalize."""
    manifest = json.loads((FIXTURES / "manifest.json").read_text())
    result = {}
    for entry in manifest["sources"]:
        path = directory / entry["name"]
        if path.is_symlink() or not path.is_file():
            raise ValueError("BANKING_SOURCE_UNAVAILABLE")
        with path.open("rb") as stream:
            raw = stream.read(entry["bytes"] + 1)
        if len(raw) != entry["bytes"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise ValueError("BANKING_HASH_MISMATCH")
        result[entry["name"]] = raw
    return result


def upload_payload(corpus: dict[str, bytes], *, namespace: str) -> tuple[bytes, dict[str, Any]]:
    """No live identities are claimed; supplied associations are fixture assumptions."""
    frozen = json.loads((FIXTURES / "manifest.json").read_text())["sources"]
    if set(corpus) != {entry["name"] for entry in frozen}:
        raise ValueError("BANKING_SOURCE_UNAVAILABLE")
    artifacts = []
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        for entry in frozen:
            name, raw = entry["name"], corpus[entry["name"]]
            if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
                raise ValueError("BANKING_HASH_MISMATCH")
            member = name.split(".")[0]
            source_file = "QRPGLESRC" if entry["language"] == "RPGLE" else "QDDSSRC"

            def identity(kind: str, parts: list[str]) -> dict[str, Any]:
                return {"system_namespace": namespace, "kind": kind, "qualified_identity": parts}

            artifacts.append(
                {
                    "relative_path": name,
                    "language": entry["language"],
                    "dialect": entry["dialect"],
                    "source_member_identity": identity(
                        "SourceMember", ["UPLOAD", source_file, member]
                    ),
                    "object_identity": identity(
                        entry["kind"],
                        ["UPLOAD", "*PGM" if entry["kind"] == "Program" else "*FILE", member],
                    ),
                    "encoding": "utf-8",
                    "kind": "source",
                    "collection_method": "private-banking-test-assumption",
                    "collected_at": "2026-10-08T00:00:00Z",
                }
            )
            archive.writestr(zipfile.ZipInfo(name, (2026, 10, 8, 0, 0, 0)), raw)
    return stream.getvalue(), {
        "schema_version": "0.1.0",
        "system_namespace": namespace,
        "system_display_name": "Private banking corpus test",
        "library_list": ["UPLOAD"],
        "default_encoding": "utf-8",
        "artifacts": artifacts,
        "application_memberships": [],
        "exclusions": [],
        "limits": {"max_files": 4, "max_bytes": 16384},
    }
