import pytest

from laip.cl_ir import build_ir
from laip.cl_parser import parse_statements
from laip.rpgle_ir import IrError, IrLimits


def ir(text):
    return build_ir(parse_statements(text))


def node(result, kind, occurrence=0):
    return [n["id"] for n in result["nodes"] if n["statement"]["kind"] == kind][occurrence]


def test_if_else_groups_have_separate_paths_and_join():
    result = ir(
        "IF COND(&N *GT 0) THEN(DO)\nCHGVAR VAR(&N) VALUE(1)\nENDDO\n"
        "ELSE CMD(DO)\nCHGVAR VAR(&N) VALUE(2)\nENDDO\nRETURN"
    )
    assert not result["barriers"]
    condition = node(result, "if")
    assignments = [n["id"] for n in result["nodes"] if n["statement"]["kind"] == "assignment"]
    assert any(
        e["from"] == condition and e["to"] == assignments[0] and e["kind"] == "true"
        for e in result["edges"]
    )
    assert not any(
        e["from"] == assignments[0] and e["to"] == assignments[1] for e in result["edges"]
    )
    assert any(e["from"] == condition and e["kind"] == "false" for e in result["edges"])


def test_inline_assignment_is_conditional_and_join_preserves_may_facts():
    result = ir(
        "DCL VAR(&N) TYPE(*INT) VALUE(0)\nIF COND(&N *EQ 0) THEN(CHGVAR VAR(&N) VALUE(1))\n"
        "CHGVAR VAR(&M) VALUE(&N)"
    )
    assert not result["barriers"]
    last = node(result, "assignment", 1)
    use = next(
        u for u in result["data_flow"]["uses"] if u["node"] == last and u["variable"] == "&n"
    )
    assert len(use["definitions"]) == 2
    assert use["definitely_defined"]


@pytest.mark.parametrize(
    "command,loopkind",
    [
        ("DOWHILE COND(&N *LT 3)", "dow"),
        ("DOUNTIL COND(&N *GE 3)", "dou"),
        ("DOFOR VAR(&N) FROM(1) TO(3)", "for"),
    ],
)
def test_loops_retest_at_correct_point(command, loopkind):
    result = ir(command + "\nCHGVAR VAR(&N) VALUE(&N + 1)\nITERATE\nENDDO\nRETURN")
    header = node(result, loopkind)
    end = node(result, "enddo")
    iterate = node(result, "iter")
    assert any(e["from"] == iterate and e["to"] == end for e in result["edges"])
    target = header + 1 if loopkind == "dou" else header
    assert any(
        e["from"] == end and e["to"] == target and e["kind"] == "repeat" for e in result["edges"]
    )
    if loopkind == "dou":
        assert result["nodes"][end]["condition_source_stmt_index"] == 0
        assert not any(u["node"] == header for u in result["data_flow"]["uses"])


def test_leave_in_nested_do_targets_enclosing_loop():
    result = ir("DOWHILE COND(&OK)\nDO\nLEAVE\nENDDO\nENDDO\nRETURN")
    leave = node(result, "leave")
    ret = node(result, "return")
    assert any(e["from"] == leave and e["to"] == ret for e in result["edges"])


def test_command_message_handler_is_exception_only():
    result = ir("CALL PGM(LIB/P)\nMONMSG MSGID(CPF0000) EXEC(CHGVAR VAR(&N) VALUE(-1))\nRETURN")
    call = node(result, "call")
    handler = node(result, "assignment")
    assert any(e["from"] == call and e["kind"] == "exception" for e in result["edges"])
    assert not any(
        e["from"] == call and e["to"] == handler and e["kind"] == "next" for e in result["edges"]
    )


def test_submitted_command_has_reference_not_current_job_execution():
    result = ir("SBMJOB CMD(CALL PGM(LIB/BATCH) PARM(&N)) JOB(TEST)")
    assert not any(n["statement"]["kind"] == "call" for n in result["nodes"])
    assert any(
        r["target"] == "LIB/BATCH" and r["context"] == "submitted_job" for r in result["references"]
    )


def test_dynamic_override_and_unknowns_are_explicit():
    result = ir(
        "OVRDBF FILE(INPUT) TOFILE(LIB/DATA) OVRSCOPE(*JOB)\n"
        "CALL PGM(&TARGET)\nRUNSQL SQL('never run')"
    )
    assert not result["conclusions_allowed"]
    override = next(r for r in result["references"] if r["kind"] == "override")
    assert override["resolution"] == "unresolved"
    assert override["scope"] == "*JOB"
    assert any(r["dynamic"] for r in result["references"])


@pytest.mark.parametrize(
    "source",
    [
        "ELSE CMD(DO)\nENDDO",
        "ENDDO",
        "DOWHILE COND(&OK)",
        "LEAVE",
        "PGM\nMONMSG MSGID(CPF0000) EXEC(RETURN)\nENDPGM",
    ],
)
def test_unmatched_or_program_level_handler_blocks(source):
    assert not ir(source)["conclusions_allowed"]


def test_ir_limits_and_cancellation():
    with pytest.raises(IrError):
        build_ir(parse_statements("PGM\nENDPGM"), limits=IrLimits(max_nodes=1))
    with pytest.raises(IrError):
        build_ir(parse_statements("PGM"), cancelled=lambda: True)


def test_nested_inline_conditions_preserve_inner_command():
    result = ir("IF COND(&A) THEN(IF COND(&B) THEN(CHGVAR VAR(&N) VALUE(1)))")
    assert len([n for n in result["nodes"] if n["statement"]["kind"] == "if"]) == 2
    assert any(n["statement"]["kind"] == "assignment" for n in result["nodes"])


def test_monmsg_group_handlers_do_not_fall_through():
    result = ir(
        "CALL PGM(P)\nMONMSG MSGID(CPF0000) EXEC(DO)\nCHGVAR VAR(&N) VALUE(-1)\nENDDO\nRETURN"
    )
    call = node(result, "call")
    handler = node(result, "assignment")
    assert any(e["from"] == call and e["kind"] == "exception" for e in result["edges"])
    assert not any(e["from"] == call and e["to"] == handler for e in result["edges"])


def test_nested_condition_source_is_cited_as_assignment(tmp_path):
    from test_cl_analysis import imported

    from laip.cl_analysis import analyze_cl

    prepared, store, root = imported(tmp_path, "IF COND(&A) THEN(CHGVAR VAR(&N) VALUE(1))")
    result = analyze_cl(prepared, root, store, run_id="inline")
    assert any(e["metadata"]["statement_kind"] == "assignment" for e in result.evidence)
