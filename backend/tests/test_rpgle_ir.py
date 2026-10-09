from __future__ import annotations

from typing import Any

import pytest

from laip.rpgle_ir import IrError, IrLimits, build_ir


def statement(
    kind: str, *, uses: tuple[str, ...] = (), defines: tuple[str, ...] = (), barrier: bool = False
) -> dict[str, Any]:
    return {
        "kind": kind,
        "text": kind,
        "start": 0,
        "end": 1,
        "attributes": {},
        "uses": list(uses),
        "defines": list(defines),
        "barrier": barrier,
        "diagnostics": [],
    }


def edges(result: dict[str, Any]) -> set[tuple[int, int, str]]:
    return {(edge["from"], edge["to"], edge["kind"]) for edge in result["edges"]}


def test_branch_join_distinguishes_may_and_definite() -> None:
    result = build_ir(
        (
            statement("if"),
            statement("assign", defines=("x",)),
            statement("else"),
            statement("assign", defines=("x",)),
            statement("endif"),
            statement("assign", uses=("x",)),
        )
    )
    assert (0, 1, "true") in edges(result)
    assert (0, 3, "false") in edges(result)
    use = result["data_flow"]["uses"][-1]
    assert use["definitions"] == [1, 3]
    assert use["definitely_defined"] is True


def test_one_branch_assignment_is_not_definite() -> None:
    result = build_ir(
        (
            statement("if"),
            statement("assign", defines=("x",)),
            statement("endif"),
            statement("assign", uses=("x",)),
        )
    )
    assert result["data_flow"]["uses"][-1]["definitely_defined"] is False


def test_unknown_barrier_clears_reaching_definitions() -> None:
    result = build_ir(
        (
            statement("assign", defines=("x",)),
            statement("unknown", barrier=True),
            statement("assign", uses=("x",)),
        )
    )
    assert not result["conclusions_allowed"]
    assert result["data_flow"]["uses"][-1]["definitions"] == []


def test_loop_leave_iter_and_return() -> None:
    result = build_ir(
        (
            statement("dow"),
            statement("iter"),
            statement("leave"),
            statement("enddo"),
            statement("return"),
            statement("assign"),
        )
    )
    assert (1, 3, "iterate") in edges(result)
    assert (2, 4, "leave") in edges(result)
    assert not any(edge["from"] == 4 for edge in result["edges"])


def test_procedure_is_separate_entry() -> None:
    result = build_ir(
        (
            statement("assign"),
            statement("dcl_proc"),
            statement("assign", defines=("x",)),
            statement("end_proc"),
            statement("assign", uses=("x",)),
        )
    )
    assert (0, 4, "next") in edges(result)
    assert (0, 1, "next") not in edges(result)
    assert result["data_flow"]["uses"][-1]["definitions"] == []


def test_malformed_structure_blocks_conclusions() -> None:
    result = build_ir((statement("endif"),))
    assert not result["conclusions_allowed"]
    assert result["barriers"]


def test_limits_and_cancellation() -> None:
    with pytest.raises(IrError, match="node"):
        build_ir((statement("assign"), statement("assign")), limits=IrLimits(max_nodes=1))
    with pytest.raises(IrError, match="cancel"):
        build_ir((), cancelled=lambda: True)


def test_select_and_monitor_control_paths() -> None:
    result = build_ir(
        (
            statement("select"),
            statement("when"),
            statement("assign"),
            statement("other"),
            statement("assign"),
            statement("endsl"),
            statement("monitor"),
            statement("assign", defines=("x",)),
            statement("on_error"),
            statement("assign", uses=("x",)),
            statement("endmon"),
        )
    )
    assert (0, 1, "case") in edges(result)
    assert (0, 3, "case") in edges(result)
    assert (2, 5, "join") in edges(result)
    assert (7, 8, "exception") in edges(result)
    assert result["data_flow"]["uses"][-1]["definitions"] == []


def test_calls_kill_facts_and_unreachable_uses_are_not_claimed() -> None:
    result = build_ir(
        (
            statement("assign", defines=("x",)),
            statement("call"),
            statement("assign", uses=("x",)),
            statement("return"),
            statement("assign", uses=("x",)),
        )
    )
    assert len(result["data_flow"]["uses"]) == 1
    assert result["data_flow"]["uses"][0]["definitions"] == []


def test_nested_depth_and_work_budgets() -> None:
    with pytest.raises(IrError, match="depth"):
        build_ir((statement("if"), statement("if")), limits=IrLimits(max_depth=1))
    with pytest.raises(IrError, match="work"):
        build_ir((statement("assign"),), limits=IrLimits(max_work=1))
    with pytest.raises(ValueError):
        IrLimits(max_nodes=0)


def test_unclosed_and_outside_loop_are_barriers() -> None:
    result = build_ir((statement("leave"), statement("else"), statement("if")))
    assert len(result["barriers"]) == 3
    assert not result["conclusions_allowed"]


def test_expression_invocations_block_certainty_with_real_parser() -> None:
    from laip.rpgle_parser import parse_statements

    parsed = parse_statements(
        "x = 1; y = unknownProc(x); if unknownPredicate(x); y = x; endif; z = y;"
    )
    result = build_ir(parsed)
    assert not result["conclusions_allowed"]
    assert {barrier["node"] for barrier in result["barriers"]} == {1, 2}
    assert not any(
        use["definitely_defined"] for use in result["data_flow"]["uses"] if use["node"] >= 3
    )


def test_for_iteration_visits_increment_boundary_with_real_parser() -> None:
    from laip.rpgle_parser import parse_statements

    parsed = parse_statements("for i = 1 to 10; x = i; iter; endfor; y = i;")
    result = build_ir(parsed)
    assert (2, 3, "iterate") in edges(result)
    assert result["data_flow"]["uses"][0]["variable"] == "i"
    assert result["data_flow"]["uses"][0]["definitely_defined"]


def test_empty_if_preserves_distinct_conditional_edges():
    result = build_ir(
        (
            statement("if"),
            statement("else"),
            statement("endif"),
            statement("assignment", defines=("x",)),
        )
    )
    assert (0, 2, "true") in edges(result)
    assert (0, 2, "false") in edges(result)


@pytest.mark.parametrize(
    "kinds",
    [
        ("if", "else", "else", "endif"),
        ("if", "else", "elseif", "endif"),
        ("select", "other", "when", "endsl"),
    ],
)
def test_malformed_branch_sequence_is_barrier(kinds):
    assert not build_ir(tuple(statement(kind) for kind in kinds))["conclusions_allowed"]


def test_dou_condition_is_read_after_body_and_repeat_skips_header():
    result = build_ir(
        (
            statement("dou", uses=("x",)),
            statement("assignment", defines=("x",)),
            statement("iter"),
            statement("enddo"),
            statement("assignment", uses=("x",)),
        )
    )
    assert (3, 1, "repeat") in edges(result)
    assert (2, 3, "iterate") in edges(result)
    use = next(value for value in result["data_flow"]["uses"] if value["node"] == 3)
    assert use["definitions"] == [1] and use["definitely_defined"]
    assert not any(value["node"] == 0 for value in result["data_flow"]["uses"])


def test_exfmt_invalidates_external_fields():
    result = build_ir(
        (
            statement("declaration", defines=("a",)),
            statement("exfmt"),
            statement("assignment", uses=("a",)),
        )
    )
    assert not result["data_flow"]["uses"][-1]["definitely_defined"]


def test_loop_in_if_keeps_repeat_and_exit_skips_else_body():
    result = build_ir(
        (
            statement("if"),
            statement("dow"),
            statement("assignment"),
            statement("enddo"),
            statement("else"),
            statement("assignment"),
            statement("endif"),
        )
    )
    assert (3, 1, "repeat") in edges(result)
    assert (1, 6, "join") in edges(result)
    assert (0, 5, "false") in edges(result)


@pytest.mark.parametrize(
    "kinds,repeat,join",
    [
        (
            ("select", "when", "dow", "assignment", "enddo", "other", "assignment", "endsl"),
            (4, 2, "repeat"),
            (2, 7, "join"),
        ),
        (
            ("monitor", "dow", "assignment", "enddo", "on_error", "assignment", "endmon"),
            (3, 1, "repeat"),
            (1, 6, "normal"),
        ),
    ],
)
def test_nested_loop_survives_select_and_monitor_join(kinds, repeat, join):
    result = build_ir(tuple(statement(kind) for kind in kinds))
    assert repeat in edges(result)
    assert join in edges(result)


@pytest.mark.parametrize(
    "kinds,source,target",
    [
        (("if", "dow", "leave", "enddo", "else", "assignment", "endif"), 2, 6),
        (("select", "when", "dow", "leave", "enddo", "other", "assignment", "endsl"), 3, 7),
        (("monitor", "dow", "leave", "enddo", "on_error", "assignment", "endmon"), 2, 6),
    ],
)
def test_leave_at_branch_boundary_reaches_join_not_next_arm(kinds, source, target):
    result = build_ir(tuple(statement(kind) for kind in kinds))
    assert (source, target, "leave") in edges(result)
