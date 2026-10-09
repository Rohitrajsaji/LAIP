"""Deterministic inert offline sample; no caller paths or live IBM i access."""

import base64
import io
import json
import zipfile
from typing import Any


def _dds(
    name: str = "",
    spec: str = "",
    *,
    length: str = "",
    dtype: str = "",
    usage: str = "",
    row: str = "",
    column: str = "",
    keywords: str = "",
) -> str:
    return (
        "     A"
        + " " * 10
        + spec.ljust(1)
        + " "
        + name.ljust(10)
        + " "
        + length.rjust(5)
        + dtype.ljust(1)
        + "  "
        + usage.ljust(1)
        + row.rjust(3)
        + column.rjust(3)
        + keywords
        + "\n"
    )


def fixture_payload(namespace: str) -> dict[str, Any]:
    """Return a fresh namespace-qualified manifest and fixed synthetic ZIP."""

    def identity(kind: str, *parts: str) -> dict[str, Any]:
        return {"system_namespace": namespace, "kind": kind, "qualified_identity": list(parts)}

    entries = [
        (
            "DEMO/QCLSRC/ENTRY.clle",
            "CLLE",
            "clle",
            "Program",
            "QCLSRC",
            "ENTRY",
            "PGM\nDCL VAR(&COUNT) TYPE(*DEC) LEN(5 0)\nDCL VAR(&TARGET) TYPE(*CHAR) LEN(10)\n"
            "CHGVAR VAR(&COUNT) VALUE(1)\nIF COND(&COUNT *GT 0) THEN(CALL PGM(DEMO/ORDER))\n"
            "CALL PGM(ORDER)\nCALL PGM(DEMO/CUSTOMER)\nCALL PGM(&TARGET)\nENDPGM\n",
        ),
        (
            "DEMO/QRPGLESRC/ORDER.rpgle",
            "RPGLE",
            "fully_free",
            "Program",
            "QRPGLESRC",
            "ORDER",
            "**free\ndcl-s balance packed(9:2) inz(0);\ndcl-f ORDERS usage(*input) keyed;\n"
            "dcl-proc validate;\nif balance > 100;\n  balance = 100;\nelse;\n"
            "  balance = 0;\nendif;\nreturn;\nend-proc;\n",
        ),
        (
            "OTHER/QRPGLESRC/ORDER.rpgle",
            "RPGLE",
            "fully_free",
            "Program",
            "QRPGLESRC",
            "ORDER",
            "**free\ndcl-s balance packed(9:2);\nif balance < 0;\nbalance = 0;\nendif;\n",
        ),
        (
            "DEMO/QRPGLESRC/CUSTOMER.rpgle",
            "RPGLE",
            "fully_free",
            "Program",
            "QRPGLESRC",
            "CUSTOMER",
            "**free\n/copy DEMO/QRPGLESRC,MISSING\ndcl-s credit packed(9:2);\n"
            "if credit > 100;\ncredit = 100;\nendif;\n",
        ),
        (
            "DEMO/QDDSSRC/ORDERS.pf",
            "DDS",
            "physical",
            "Table",
            "QDDSSRC",
            "ORDERS",
            _dds("ORDERREC", "R")
            + _dds("ORDERID", length="5", dtype="P", keywords="RANGE(1 99999)")
            + _dds("ORDERID", "K"),
        ),
        (
            "DEMO/QDDSSRC/ORDERIDX.lf",
            "DDS",
            "logical",
            "Table",
            "QDDSSRC",
            "ORDERIDX",
            _dds("ORDERREC", "R", keywords="PFILE(DEMO/ORDERS)")
            + _dds("ORDERID")
            + _dds("ORDERID", "K"),
        ),
        (
            "DEMO/QDDSSRC/ORDERDSP.dspf",
            "DDS",
            "display",
            "Screen",
            "QDDSSRC",
            "ORDERDSP",
            _dds("ORDERFORM", "R")
            + _dds(
                "ORDERID",
                length="5",
                dtype="A",
                usage="B",
                row="2",
                column="10",
                keywords="CHECK(ME)",
            )
            + _dds(row="1", column="2", keywords="'Synthetic order entry'"),
        ),
    ]
    artifacts: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zipped:
        for path, language, dialect, kind, source_file, member, source in entries:
            library = path.split("/", 1)[0]
            object_identity = identity(
                kind, library, "*PGM" if kind == "Program" else "*FILE", member
            )
            artifacts.append(
                {
                    "relative_path": path,
                    "language": language,
                    "dialect": dialect,
                    "source_member_identity": identity(
                        "SourceMember", library, source_file, member
                    ),
                    "object_identity": object_identity,
                    "encoding": "utf-8",
                    "kind": "source",
                    "collection_method": "synthetic-offline-fixture",
                    "collected_at": "2026-10-08T00:00:00Z",
                }
            )
            records.append({"record_type": "entity", "identity": object_identity})
            zipped.writestr(zipfile.ZipInfo(path, (2026, 10, 8, 0, 0, 0)), source.encode())
        records.append(
            {
                "record_type": "reference",
                "from_identity": identity("Program", "DEMO", "*PGM", "ENTRY"),
                "relationship": "CALLS",
                "target": {"kind": "Program", "name": "ORDER", "library": "DEMO", "dynamic": False},
            }
        )
        records.append(
            {
                "record_type": "reference",
                "from_identity": identity("Program", "DEMO", "*PGM", "ENTRY"),
                "relationship": "CALLS",
                "target": {"kind": "Program", "name": "ORDER", "library": None, "dynamic": False},
            }
        )
        path = "metadata/inventory.json"
        zipped.writestr(
            zipfile.ZipInfo(path, (2026, 10, 8, 0, 0, 0)),
            json.dumps(
                {"schema_version": "0.1.0", "kind": "inventory", "records": records}, sort_keys=True
            ).encode(),
        )
    artifacts.append(
        {
            "relative_path": "metadata/inventory.json",
            "language": None,
            "dialect": None,
            "source_member_identity": None,
            "object_identity": None,
            "encoding": "utf-8",
            "kind": "inventory",
            "collection_method": "synthetic-offline-fixture",
            "collected_at": "2026-10-08T00:00:00Z",
        }
    )
    manifest = {
        "schema_version": "0.1.0",
        "system_namespace": namespace,
        "system_display_name": "Synthetic customer-validation fixture",
        "library_list": ["DEMO", "OTHER"],
        "default_encoding": "utf-8",
        "artifacts": artifacts,
        "application_memberships": [
            {
                "application_identity": identity("Application", "ORDER_APP"),
                "member_identity": artifacts[0]["source_member_identity"],
                "evidence_path": artifacts[0]["relative_path"],
            },
            {
                "application_identity": identity("Application", "ORDER_APP"),
                "member_identity": identity("SourceMember", "DEMO", "QRPGLESRC", "CUSTOMER"),
                "evidence_path": "DEMO/QRPGLESRC/CUSTOMER.rpgle",
            },
        ],
        "exclusions": [],
        "limits": {"max_files": 20, "max_bytes": 1048576},
    }
    return {
        "archive_base64": base64.b64encode(archive.getvalue()).decode(),
        "manifest": manifest,
        "label": "Synthetic customer-validation fixture",
        "limitations": [
            "Offline synthetic source only; no live IBM i validation.",
            "The dynamic CL call and same-named unqualified ORDER target remain unresolved.",
            "CUSTOMER has an unavailable include; unsupported conclusions are blocked.",
        ],
    }
