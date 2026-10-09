"""Bounded DDS definitions and source-backed relationships, without compilation."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any

from laip.canonical import digest, identity_id
from laip.persistence import validate
from laip.rpgle_ir import IrError, IrLimits

DEFAULT_LIMITS = IrLimits()


def build_ir(
    statements: Sequence[dict[str, Any]],
    *,
    file_type: str,
    source_identity: dict[str, Any],
    available_descriptions: Sequence[dict[str, Any]] = (),
    limits: IrLimits = DEFAULT_LIMITS,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    if file_type not in {"physical", "logical", "display"}:
        raise ValueError("INVALID_DDS_FILE_TYPE")
    validate("Identity", source_identity)
    deadline = time.monotonic() + limits.timeout_seconds
    work = 0

    def check() -> None:
        nonlocal work
        work += 1
        if work > limits.max_work or time.monotonic() > deadline or (cancelled and cancelled()):
            raise IrError("DDS IR resource limit or cancellation")

    check()
    if len(statements) > limits.max_nodes or len(available_descriptions) > limits.max_nodes:
        raise IrError("DDS IR node/description limit")
    nodes = [{"id": i, "stmt_index": i, "statement": dict(s)} for i, s in enumerate(statements)]
    definitions: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []
    barriers: dict[int, list[str]] = {}
    file_keywords: list[dict[str, Any]] = []
    function_keys: list[dict[str, Any]] = []
    subfile_controls: list[tuple[int, dict[str, Any], str]] = []
    record: str | None = None
    owner: dict[str, Any] | None = None
    refs: list[str] | None = None
    record_sources: dict[str, list[dict[str, Any]]] = {}
    local_fields: dict[tuple[str, str], list[dict[str, Any]]] = {}
    by_name: dict[tuple[str, str | None, str | None], list[dict[str, Any]]] = {}
    descriptions: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for description in available_descriptions:
        check()
        identity = description["identity"]
        validate("Identity", identity)
        parts = identity["qualified_identity"]
        if identity["system_namespace"] != source_identity["system_namespace"]:
            continue
        if identity["kind"] != "Table" or len(parts) != 3 or parts[1] != "*FILE":
            continue
        if len(description["definitions"]) > limits.max_nodes:
            raise IrError("DDS description definition limit")
        descriptions.setdefault((parts[0], parts[2]), []).append(description)

    def barrier(node: int, message: str) -> None:
        check()
        barriers.setdefault(node, []).append(message)

    def args(keyword: dict[str, Any]) -> list[str]:
        return list(keyword.get("arguments", []))

    def target(
        file: str | None, field: str | None = None, format_name: str | None = None
    ) -> dict[str, Any]:
        parts = file.split("/") if file else []
        return {
            "library": parts[0] if len(parts) == 2 else None,
            "file": parts[-1] if parts else None,
            "record": format_name,
            "field": field,
        }

    def relate(
        node: int,
        kind: str,
        source: str | None,
        wanted: dict[str, Any],
        candidates: list[dict[str, Any]],
        artifacts: list[str],
        reason: str = "Description unavailable",
    ) -> dict[str, Any]:
        check()
        state = "resolved" if len(candidates) == 1 else "ambiguous" if candidates else "unresolved"
        relationship = {
            "node": node,
            "kind": kind,
            "source_id": source,
            "target": wanted,
            "target_id": candidates[0]["id"] if state == "resolved" else None,
            "resolution": state,
            "diagnostics": [] if state == "resolved" else [reason],
            "supporting_artifact_ids": sorted(set(artifacts)),
        }
        relationships.append(relationship)
        if state != "resolved":
            barrier(node, f"{kind}: {state}: {reason}")
        return relationship

    def external(wanted: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
        check()
        if wanted["library"] is None or wanted["file"] is None:
            return [], []
        supplied = descriptions.get((wanted["library"], wanted["file"]), [])
        found: list[dict[str, Any]] = []
        artifacts: list[str] = []
        for description in supplied:
            check()
            if wanted["field"] is None and wanted["record"] is None:
                if any(d["kind"] == "record" and d["complete"] for d in description["definitions"]):
                    found.append({"id": identity_id(description["identity"])})
                    artifacts.append(description["artifact_id"])
                continue
            for definition in description["definitions"]:
                check()
                if not definition["complete"]:
                    continue
                if wanted["field"] is None:
                    matches = definition["kind"] == "record"
                else:
                    matches = (
                        definition["kind"] == "field" and definition["name"] == wanted["field"]
                    )
                if matches and (
                    wanted["record"] is None
                    or definition.get("record") == wanted["record"]
                    or (definition["kind"] == "record" and definition["name"] == wanted["record"])
                ):
                    found.append(definition)
                    artifacts.append(description["artifact_id"])
        return found, artifacts

    for node, statement in enumerate(statements):
        check()
        attrs = dict(statement["attributes"])
        kind, name = statement["kind"], str(attrs.get("name") or "")
        if statement["barrier"]:
            barrier(node, "; ".join(statement["diagnostics"]) or "Unsupported DDS construct")
            if kind == "opaque" and attrs.get("specification") == "R":
                record, owner = None, None
            elif kind == "opaque" and name:
                owner = None
            elif kind in {"opaque", "keyword"} and owner is not None:
                owner["complete"] = False
                barrier(owner["node"], "Unsupported keyword on definition")
        if kind == "record":
            record, owner = name, None
        if kind in {"record", "field", "constant", "key"}:
            if kind != "record" and record is None:
                barrier(node, "Definition without a preceding record")
            scope = record if kind != "record" else None
            key = (kind, scope, name)
            definition = {
                "id": "dds_"
                + digest(
                    {
                        "identity": source_identity,
                        "kind": kind,
                        "record": scope,
                        "name": name,
                        "constant_node": node if kind == "constant" else None,
                    }
                ),
                "kind": kind,
                "name": name,
                "record": scope,
                "node": node,
                "attributes": attrs,
                "complete": not statement["barrier"],
            }
            if kind != "constant" and key in by_name:
                barrier(node, "Duplicate DDS definition")
                definition["complete"] = False
                for old in by_name[key]:
                    old["complete"] = False
                    barrier(old["node"], "Duplicate DDS definition")
            by_name.setdefault(key, []).append(definition)
            definitions.append(definition)
            owner = definition
            if kind == "field" and record is not None:
                local_fields.setdefault((record, name), []).append(definition)
            if kind == "key":
                candidates = local_fields.get((record, name), []) if record else []
                relate(
                    node,
                    "key",
                    definition["id"],
                    target(None, name, record),
                    [d for d in candidates if d["complete"]],
                    [],
                    "Key field missing or ambiguous",
                )
        if kind == "keyword" and owner is not None:
            owner["attributes"].setdefault("conditional_keywords", []).append(
                {
                    "node": node,
                    "keywords": attrs.get("keywords", []),
                    "indicators": attrs.get("indicators", []),
                    "indicator_connector": attrs.get("indicator_connector"),
                }
            )
        if kind == "keyword" and record is None:
            file_keywords.append(
                {
                    "node": node,
                    "keywords": attrs.get("keywords", []),
                    "indicators": attrs.get("indicators", []),
                    "indicator_connector": attrs.get("indicator_connector"),
                }
            )
        keyword_names = {keyword["name"] for keyword in attrs.get("keywords", [])}
        if {"PFILE", "JFILE"} <= keyword_names:
            barrier(node, "PFILE and JFILE are mutually exclusive")
        for keyword in attrs.get("keywords", []):
            check()
            keyword_name = keyword["name"]
            parameters = args(keyword)
            if keyword_name in {"DATFMT", "TIMFMT"} and (owner is None or owner["kind"] != "field"):
                barrier(node, f"{keyword_name} requires field level")
            if keyword_name == "UNIQUE" and (record is not None or attrs.get("indicators")):
                barrier(node, "UNIQUE requires unconditional file level")
            if keyword_name.startswith(("CA", "CF")) and keyword_name[2:].isdigit():
                if owner is not None and owner["kind"] not in {"record"}:
                    barrier(node, "Function-key keyword requires file or record level")
                function_keys.append(
                    {
                        "node": node,
                        "record": record,
                        "keyword": keyword_name,
                        "key_number": int(keyword_name[2:]),
                        "response_indicator": int(parameters[0]) if parameters else None,
                        "text": parameters[1] if len(parameters) > 1 else None,
                        "transmits_input": keyword_name.startswith("CF"),
                        "validates_input": keyword_name.startswith("CF"),
                        "indicators": attrs.get("indicators", []),
                        "indicator_connector": attrs.get("indicator_connector"),
                        "basis": "ibm-i-7.4-display-CAnn/CFnn",
                        "runtime_effects_established": False,
                    }
                )
            if keyword_name in {
                "SFL",
                "SFLCTL",
                "SFLPAG",
                "SFLSIZ",
                "SFLDSP",
                "SFLDSPCTL",
                "SFLCLR",
                "SFLEND",
                "OVERLAY",
            }:
                if owner is None or owner["kind"] != "record":
                    barrier(node, f"{keyword_name} requires record level")
                elif keyword_name == "SFLCTL":
                    subfile_controls.append((node, owner, parameters[0]))
                elif keyword_name in {
                    "SFLPAG",
                    "SFLSIZ",
                    "SFLDSP",
                    "SFLDSPCTL",
                    "SFLCLR",
                    "SFLEND",
                }:
                    if not any(
                        k["name"] == "SFLCTL" for k in owner["attributes"].get("keywords", [])
                    ):
                        barrier(node, f"{keyword_name} requires a subfile-control record")
                    if keyword_name in {"SFLCLR", "SFLEND"} and not attrs.get("indicators"):
                        barrier(node, f"{keyword_name} requires an option indicator within profile")
            if keyword_name == "DSPSIZ" and record is not None:
                barrier(node, "DSPSIZ requires file level")
            if keyword_name == "REF":
                refs = parameters
            elif keyword_name in {"PFILE", "JFILE"}:
                if (
                    file_type != "logical"
                    or record is None
                    or owner is None
                    or owner["kind"] != "record"
                ):
                    barrier(node, "PFILE/JFILE requires a logical record definition")
                if record is not None:
                    sources = record_sources.setdefault(record, [])
                    for file in parameters:
                        wanted = target(file)
                        found, artifacts = external(wanted)
                        relationship = relate(
                            node,
                            "based_on",
                            owner["id"] if owner else None,
                            wanted,
                            found,
                            artifacts,
                        )
                        sources.append(relationship)
            elif keyword_name == "REFFLD" and (owner is None or owner["kind"] != "field"):
                barrier(node, "REFFLD requires a field definition")
        if kind != "field" or owner is None:
            continue
        referenced = next((k for k in attrs.get("keywords", []) if k["name"] == "REFFLD"), None)
        reference = attrs.get("reference") in ("R", True)
        if referenced is not None or reference:
            if referenced is not None and not reference:
                barrier(node, "REFFLD requires reference R in column 29")
            values = args(referenced) if referenced is not None else [name]
            field_parts = values[0].split("/")
            field_name = field_parts[-1]
            format_name = field_parts[0] if len(field_parts) == 2 else None
            file = values[1] if len(values) > 1 else (refs[0] if refs else "*SRC")
            if format_name is None and refs and len(refs) > 1 and file != "*SRC":
                format_name = refs[1]
            wanted = target(None if file == "*SRC" else file, field_name, format_name)
            if file == "*SRC":
                candidates = []
                for (format_key, field_key), fields in local_fields.items():
                    check()
                    if field_key == field_name and (
                        format_name is None or format_name == format_key
                    ):
                        candidates.extend(d for d in fields if d["node"] < node and d["complete"])
                artifacts = []
            else:
                candidates, artifacts = external(wanted)
            relation = relate(node, "field_reference", owner["id"], wanted, candidates, artifacts)
            if relation["resolution"] == "resolved":
                inherited = candidates[0]["attributes"]
                if inherited.get("derived_description"):
                    attrs["inherited_description"] = {
                        **inherited["derived_description"],
                        "definition_id": candidates[0]["id"],
                        "supporting_artifact_ids": relation["supporting_artifact_ids"],
                    }
                for property in ("length", "datatype", "decimals"):
                    if attrs.get(property) is None:
                        attrs[property] = inherited.get(property)
                owner["attributes"] = attrs
                owner["attributes"]["inherited_from"] = candidates[0]["id"]
        elif file_type == "logical" and attrs.get("length") is None:
            candidates, artifacts = [], []
            for source in record_sources.get(record or "", []):
                wanted = {**source["target"], "field": name}
                found, supplied = external(wanted)
                candidates.extend(found)
                artifacts.extend(supplied)
            wanted = (
                {**record_sources[record or ""][0]["target"], "field": name}
                if record_sources.get(record or "")
                else target(None, name)
            )
            relation = relate(node, "field_reference", owner["id"], wanted, candidates, artifacts)
            if relation["resolution"] == "resolved":
                inherited = candidates[0]["attributes"]
                attrs["inherited_from"] = candidates[0]["id"]
                if inherited.get("derived_description"):
                    attrs["inherited_description"] = {
                        **inherited["derived_description"],
                        "definition_id": candidates[0]["id"],
                        "supporting_artifact_ids": relation["supporting_artifact_ids"],
                    }
                for property in ("length", "datatype", "decimals"):
                    attrs[property] = inherited.get(property)
                owner["attributes"] = attrs
        if "DATFMT" in keyword_names and attrs.get("datatype") != "L":
            barrier(node, "DATFMT conversion outside declared temporal-field profile")
        if "TIMFMT" in keyword_names and attrs.get("datatype") != "T":
            barrier(node, "TIMFMT requires a time field within profile")
        attrs["declared_length"] = statement["attributes"].get("length")
        if attrs.get("datatype") in {"L", "T", "Z"} and file_type in {"physical", "logical"}:
            dtype = attrs["datatype"]
            if statement["attributes"].get("decimals") is not None:
                barrier(node, "Temporal DDS decimal positions must be absent")
            if attrs["declared_length"] is not None:
                barrier(node, "Temporal DDS length must be absent in source")
            if attrs.get("inherited_from") and not attrs.get("inherited_description"):
                barrier(node, "Inherited temporal format/profile unavailable")
            inherited_description = attrs.get("inherited_description", {})
            datefmt = next(
                (args(k)[0].upper() for k in attrs.get("keywords", []) if k["name"] == "DATFMT"),
                inherited_description.get("format") or "*ISO",
            )
            timefmt = next(
                (args(k)[0].upper() for k in attrs.get("keywords", []) if k["name"] == "TIMFMT"),
                inherited_description.get("format") or "*ISO",
            )
            attrs["derived_description"] = {
                "semantic_type": {"L": "date", "T": "time", "Z": "timestamp"}[dtype],
                "length": (
                    10
                    if datefmt in {"*ISO", "*USA", "*EUR", "*JIS"}
                    else 6
                    if datefmt == "*JUL"
                    else 8
                )
                if dtype == "L"
                else 8
                if dtype == "T"
                else 26,
                "format": datefmt if dtype == "L" else timefmt if dtype == "T" else None,
                "basis": "ibm-i-7.5-physical-logical-length/DATFMT/TIMFMT",
                "source_declared_length": attrs["declared_length"],
                "inherited_from": attrs.get("inherited_from"),
                "supporting_artifact_ids": inherited_description.get("supporting_artifact_ids", []),
                "profile": "dds-banking-0.2.0",
                "runtime_description_established": False,
            }
            unsupported_format = any(
                any(name in message for name in ("DATFMT", "DATSEP", "TIMFMT", "TIMSEP"))
                for message in statement.get("diagnostics", [])
            )
            if unsupported_format or (attrs.get("inherited_from") and not inherited_description):
                attrs["derived_description"].update(
                    length=None,
                    format=None,
                    resolution="unresolved",
                    limitation="Temporal format unavailable or outside declared profile",
                )
            else:
                attrs["derived_description"]["resolution"] = "supported-within-profile"
            attrs["derived_length"] = attrs["derived_description"]["length"]
            owner["attributes"] = attrs
        elif attrs.get("length") is None:
            barrier(node, "Field description incomplete: length unavailable")
        if record is None or node in barriers:
            owner["complete"] = False
    for node, control, target_name in subfile_controls:
        candidates = [
            definition
            for definition in definitions
            if definition["kind"] == "record"
            and definition["name"].casefold() == target_name.casefold()
            and definition["complete"]
            and any(
                keyword["name"] == "SFL" for keyword in definition["attributes"].get("keywords", [])
            )
        ]
        relate(
            node,
            "subfile_control",
            control["id"],
            target(None, format_name=target_name),
            candidates,
            [],
            "Subfile record missing or ambiguous",
        )
    key_modes: dict[tuple[str | None, int], set[str]] = {}
    for function_key in function_keys:
        modes = key_modes.setdefault((function_key["record"], function_key["key_number"]), set())
        modes.add(function_key["keyword"][:2])
        if len(modes) > 1:
            barrier(function_key["node"], "Same function key cannot be both CA and CF within scope")
    # Record-level unknowns invalidate claims that its field descriptions are complete.
    invalid_records = {
        d["name"] for d in definitions if d["kind"] == "record" and d["node"] in barriers
    }
    for definition in definitions:
        check()
        if definition["node"] in barriers or definition.get("record") in invalid_records:
            definition["complete"] = False
        if (
            definition["kind"] == "record"
            and file_type == "logical"
            and not record_sources.get(definition["name"])
        ):
            barrier(definition["node"], "Logical record missing PFILE/JFILE description")
            definition["complete"] = False
    return {
        "schema_version": "0.1.0",
        "language": "DDS",
        "file_type": file_type,
        "source_identity": source_identity,
        "nodes": nodes,
        "edges": [],
        "scopes": [],
        "definitions": definitions,
        "file_keywords": file_keywords,
        "function_keys": function_keys,
        "profile": "dds-banking-0.2.0",
        "relationships": relationships,
        "barriers": [
            {"node": n, "diagnostics": messages} for n, messages in sorted(barriers.items())
        ],
        "conclusions_allowed": not barriers,
        "limitations": [
            "Declared positional DDS subset; no compilation, "
            "runtime access path or screen behavior.",
            "Unqualified external names and unavailable descriptions remain unresolved.",
            "Inherited attributes are supplied source facts, not live IBM i descriptions.",
        ],
    }
