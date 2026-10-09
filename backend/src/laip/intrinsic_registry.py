"""Versioned inert RPG intrinsic profile; no intrinsic executes source or a clock."""

from typing import Any

PROFILE_VERSION = "0.2.0"


def intrinsic_effect(expression: dict[str, Any], types: dict[str, str]) -> dict[str, Any]:
    name = expression["name"].casefold()
    arguments = expression["arguments"]
    effect: dict[str, Any] = {
        "kind": "intrinsic",
        "name": name,
        "profile_version": PROFILE_VERSION,
        "recognized": True,
        "result_type": "unknown",
        "pure": True,
        "nondeterministic": False,
        "may_raise": False,
        "diagnostics": [],
    }
    arity = {"%found": {1}, "%subst": {2, 3}, "%char": {1}, "%timestamp": {0}, "%time": {0}}
    if name not in arity:
        effect.update(recognized=False, pure=False, may_raise=True)
        effect["diagnostics"].append("UNKNOWN_INTRINSIC_OR_FUNCTION")
    elif len(arguments) not in arity[name]:
        effect.update(recognized=False, pure=False, may_raise=True)
        effect["diagnostics"].append("UNSUPPORTED_INTRINSIC_SIGNATURE")
    elif name in {"%timestamp", "%time"}:
        effect.update(result_type=name[1:], pure=False, nondeterministic=True)
        effect["value_semantics"] = "symbolic_clock_read"
    elif name == "%found":
        if arguments[0].get("node") != "reference":
            effect.update(recognized=False, pure=False)
            effect["diagnostics"].append("UNSUPPORTED_FILE_STATUS_ARGUMENT")
        else:
            effect.update(
                result_type="ind",
                pure=False,
                file=arguments[0]["name"].casefold(),
                status_producer_nodes=[],
            )
    elif name == "%char":
        effect.update(result_type="char", may_raise=True)
        effect["value_semantics"] = "symbolic_conversion_profile_format"
        effect["input_type"] = expression_type(arguments[0], types)
        if effect["input_type"] not in {
            "char",
            "varchar",
            "number",
            "int",
            "uns",
            "packed",
            "zoned",
            "float",
            "date",
            "time",
            "timestamp",
        }:
            effect["recognized"] = False
            effect["diagnostics"].append("INTRINSIC_INPUT_TYPE_UNRESOLVED")
    elif name == "%subst":
        effect.update(result_type="char", may_raise=True)
        effect["value_semantics"] = "symbolic_substring_with_range_errors"
        if expression_type(arguments[0], types) not in {"char", "varchar"}:
            effect.update(recognized=False)
            effect["diagnostics"].append("SUBSTRING_INPUT_TYPE_UNRESOLVED")
        for argument in arguments[1:]:
            if expression_type(argument, types) not in {"number", "int", "uns", "packed", "zoned"}:
                effect.update(recognized=False)
                effect["diagnostics"].append("SUBSTRING_RANGE_TYPE_UNRESOLVED")
    return effect


def expression_type(expression: dict[str, Any], types: dict[str, str]) -> str:
    node = expression.get("node")
    if node == "reference":
        name = expression["name"].casefold()
        return "ind" if name.startswith("*in") else types.get(name, "unknown")
    if node == "literal":
        if expression.get("literal_kind") == "number":
            return "number"
        if expression.get("value") in {"*on", "*off"}:
            return "ind"
        return "char"
    if node == "invocation":
        name = expression["name"].casefold()
        return {
            "%found": "ind",
            "%char": "char",
            "%subst": "char",
            "%timestamp": "timestamp",
            "%time": "time",
        }.get(name, "unknown")
    if node == "unary":
        return expression_type(expression["operand"], types)
    if node == "binary":
        if expression["operator"] in {"=", "<>", ">", "<", ">=", "<=", "and", "or"}:
            return "ind"
        return expression_type(expression["left"], types)
    return "unknown"
