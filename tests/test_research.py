"""Actual native registry/admission/result boundaries with a local synthetic source.

No external search, model or gateway is claimed by these tests.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import pytest
from agent.tool_attempt import tool_attempt_scope
from bn_memory.action_control import ActionControl
from hermes_cli.execution_control import authorize, ExecutionControlDenied
from bnn_intake.research import OperatorReview, path_basis
from bnn_intake.storage import canonical, digest, install
from conftest import record, request
from test_native_context import native, turn
from test_gaps import gap, assessment


@pytest.fixture
def research(native):
    engine, host, work = native.engine, native.host, native.work
    engine.call("parent", request("revise", work, 3, "gap", change={"type": "add_gap", "gap": gap()}))
    path = {"id": "PATH1", "gap_id": "G1", "family": "Предоставленные оригиналы", "purpose": "Прочитать оба правила", "state": "planned", "reason": "Нужны первичные правила подсчёта"}
    engine.call("parent", request("revise", work, 4, "path", change={"type": "plan_path", "path": path}))
    schema = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"], "additionalProperties": False}
    rule = {"id": "fixture.original.read", "tool": "intake_test_source", "effect": "read_local", "roles": ["parent"], "arguments_schema": schema}
    # Name intentionally lacks the reserved intake_ prefix in runtime checks.
    rule["tool"] = "fixture_original"
    host.config["tool_policy"]["rules"].append(rule)
    host.actions = ActionControl(host)
    native.fixture_rule = rule
    native.source_calls = []
    def source(args, **kw):
        native.source_calls.append(args)
        if hasattr(native, "fixture_result"):
            return native.fixture_result if isinstance(native.fixture_result, str) else json.dumps(native.fixture_result, ensure_ascii=False)
        return json.dumps({"source": "synthetic:rules", "old_rule": "Считать все записи", "new_rule": "Считать уникальные записи", "query": args["query"]}, ensure_ascii=False)
    native.source_handler = source
    handle = native.ctx.register_tool(name="fixture_original", toolset="intake_test_source", schema={"name": "fixture_original", "description": "Synthetic original", "parameters": schema}, handler=source)
    native.attached["registrations"].append(handle)
    engine.research.call("parent", request("select_path", work, 5, "select-path", path_id="PATH1", tool_name="fixture_original"))
    return native


def dispatch(research, query="правила", call="read-original", handler_result=None):
    turn(research)
    args = {"query": query}
    identity = {"session_id": "parent", "turn_id": "native-turn", "tool_call_id": call, "api_request_id": call + "-api"}
    with tool_attempt_scope("fixture_original", identity, final_boundary="registry") as attempt:
        if handler_result is not None:
            research.fixture_result = handler_result
        raw = research.ctx.dispatch_tool("fixture_original", args, **identity)
        authorize("tool_result", {"tool_name": "fixture_original", "args": args, "result": raw}, **identity)
        nonce = attempt.nonce
    with research.host.store.db() as db:
        receipt = research.engine.research.latest(db, nonce)
    return receipt


def review_path(research, receipt, **changes):
    engine = research.engine
    revision = engine.status("parent", research.work)["state"]["revision"]
    args = request("operator_path_review", research.work, revision, "review-path", path_id="PATH1")
    payload = {"actor": "local-test-operator", "state": "performed", "reason": "Оба текста получены; применимость трактовки ограничена синтетическим примером",
               "receipts": [{"attempt_id": receipt["attempt_id"], "receipt_sha256": digest(receipt)}], "source_refs": [],
               "reading_level": "Ответ локального инструмента; не научная статья", "remaining": ["Нужен пересчёт первичных событий"]}
    payload.update(changes)
    return OperatorReview(engine.research).path("parent", args, **payload)


def test_real_source_dispatch_produces_receipt_and_exact_paginated_result(research):
    receipt = dispatch(research)
    assert receipt["status"] == "returned" and receipt["revision"] == 2
    assert not receipt["scientific_acceptance"] and receipt["reading_level"] == "returned_tool_result_not_model_reading"
    research_api = research.engine.research
    original = canonical(receipt["result"]); fragments = []; offset = 0
    while True:
        value = research_api.call("parent", request("read_result", research.work,
            attempt_id=receipt["attempt_id"], receipt_sha256=digest(receipt), offset=offset, limit=17))
        fragments.append(value["text"])
        assert value["fragment_sha256"] == hashlib.sha256(value["text"].encode()).hexdigest()
        if value["next_offset"] is None:
            break
        offset = value["next_offset"]
    assert "".join(fragments) == original
    assert research.source_calls == [{"query": "правила"}]
    target = os.environ.get("BNN_INTAKE_TEST_EVIDENCE")
    if target:
        with research.host.store.db() as db:
            native_row = dict(db.execute("SELECT * FROM action_attempts WHERE nonce=?", (receipt["attempt_id"],)).fetchone())
        (Path(target) / "native-research-receipt.json").write_text(json.dumps({"data": "synthetic", "receipt": receipt, "native_action": native_row,
            "result_restored_exactly": True, "provider_called": False, "external_search_performed": False}, ensure_ascii=False, indent=2) + "\n")


def test_source_receipt_enters_addressed_analysis_without_becoming_primary_independent_evidence(research):
    receipt = dispatch(research)
    ref = {"kind": "research_result", "attempt_id": receipt["attempt_id"], "receipt_sha256": digest(receipt), "start": 0, "end": len(canonical(receipt["result"]))}
    exp = {"id": "E-result", "purpose": "Сравнить полученные правила", "question": "Что именно изменено?", "input_refs": [ref], "depends_on": [], "method_ids": []}
    research.engine.call("parent", request("expose", research.work, 6, "result-exposition", exposition=exp))
    packet, anchors = research.engine.analysis.anchors("parent", research.work, "E-result")
    assert packet["research_results"][0]["content"] == canonical(receipt["result"])
    assert anchors[0]["content_sha256"] == receipt["result_sha256"]
    assert research.engine.analysis.source_text("parent", anchors[0]) == canonical(receipt["result"])
    with research.host.store.db() as db:
        research.engine.analysis.assert_sources_current(db, "parent", anchors)
    assert packet["semantic_acceptance"] == "unreviewed"


def test_selected_path_does_not_create_a_source_tool_permission(research):
    research.host.config["tool_policy"]["rules"].remove(research.fixture_rule)
    research.host.actions = ActionControl(research.host)
    with pytest.raises(ExecutionControlDenied, match="exact_read_rule"):
        dispatch(research)
    assert research.source_calls == []


def test_write_action_cannot_masquerade_as_read_on_a_selected_path(research):
    research.fixture_rule["effect"] = "write_local"
    research.host.actions = ActionControl(research.host)
    with pytest.raises(ExecutionControlDenied, match="exact_read_rule"):
        dispatch(research)
    assert research.source_calls == []


def test_receipt_requires_common_result_control_not_forged_payload(research):
    turn(research)
    args = {"query": "правила"}
    identity = {"session_id": "parent", "turn_id": "native-turn", "tool_call_id": "forged", "api_request_id": "forged-api"}
    with tool_attempt_scope("fixture_original", identity, final_boundary="registry"):
        research.ctx.dispatch_tool("fixture_original", args, **identity)
        with pytest.raises(PermissionError, match="native_result_mismatch"):
            research.engine.research.returned({"tool_name": "fixture_original", "args": args, "result": "invented"}, identity)
    with research.host.store.db() as db:
        assert research.engine.research.rows(db, research.work)[0]["status"] == "admitted"


def test_repeated_exact_search_does_not_start_another_executor(research):
    dispatch(research)
    with pytest.raises(ExecutionControlDenied, match="exact_repeat"):
        dispatch(research, call="same-purpose-new-id")
    assert len(research.source_calls) == 1
    # A substantively different query is an explicit new action in this path.
    receipt = dispatch(research, query="контрпример", call="different-question")
    assert receipt["status"] == "returned" and len(research.source_calls) == 2


def test_operator_review_and_limited_assessment_preserve_actual_remainder(research):
    receipt = dispatch(research)
    review_path(research, receipt)
    value = assessment("limited"); value["remaining"] = ["Первичные события недоступны"]; value["limits"] = ["Причинность не установлена"]
    research.engine.call("parent", request("assess", research.work, 7, "limited-with-evidence", assessment=value))
    saved = research.engine.status("parent", research.work)["state"]["assessments"][-1]
    assert saved["completion_accepted"] and saved["system_acceptance"]["status"] == "accepted_with_limitations"
    assert not saved["system_acceptance"]["human_review_required"]
    assert saved["research_completion"]["unperformed_paths"] == []
    assert not saved["research_completion"]["gaps"][0]["resolved"]
    assert saved["remaining"] == ["Первичные события недоступны"]


def test_model_cannot_submit_operator_review_or_resolve_its_own_gap(research):
    args = request("operator_path_review", research.work, 6, "fake", path_id="PATH1", actor="owner", state="performed")
    with pytest.raises(ValueError, match="action_unknown"):
        research.engine.research.call("parent", args)
    with pytest.raises(ValueError, match="action_unknown"):
        research.engine.call("parent", args)


def test_error_result_does_not_qualify_as_success_or_evidence_of_absence(research, monkeypatch):
    # Use the real native boundary with a deliberately returned error envelope.
    monkeypatch.setattr(research, "source_calls", [])
    receipt = dispatch(research, handler_result={"ok": False, "error": "synthetic_source_unavailable"})
    assert receipt["result_is_error"]
    with pytest.raises(PermissionError, match="non_error_result"):
        review_path(research, receipt)
    review_path(research, receipt, state="unavailable", remaining=["Один источник недоступен; другие пути ещё не оценены"])
    with pytest.raises(PermissionError, match="unresolved_material"):
        research.engine.call("parent", request("assess", research.work, 7, "not-sufficient", assessment=assessment("sufficient")))


def test_result_receipt_policy_revocation_blocks_read_and_completion(research):
    receipt = dispatch(research)
    review_path(research, receipt)
    research.host.config["tool_policy"]["rules"].remove(research.fixture_rule)
    research.host.actions = ActionControl(research.host)
    with pytest.raises(PermissionError, match="exact_read_rule"):
        research.engine.research.call("parent", request("read_result", research.work, attempt_id=receipt["attempt_id"], receipt_sha256=digest(receipt), offset=0, limit=20))
    with pytest.raises(PermissionError, match="exact_read_rule"):
        research.engine.research.call("parent", request("completion", research.work))


def test_new_unreviewed_path_invalidates_gap_resolution_without_repeating_finished_path(research):
    receipt = dispatch(research); review_path(research, receipt)
    source = record(research.host.store, "operator-evidence", "В синтетическом материале присутствуют оба правила")
    ref = {"kind": "record", "id": source["id"], "revision": source["revision"]}
    OperatorReview(research.engine.research).gap("parent", request("operator_gap_review", research.work, 7, "gap-review", gap_id="G1"),
        actor="local-test-operator", state="resolved", reason="Точный синтетический вопрос о текстах правил удовлетворён",
        path_ids=["PATH1"], source_refs=[ref], remaining=[])
    research.engine.call("parent", request("assess", research.work, 8, "sufficient-scope", assessment=assessment("sufficient")))
    path = {"id": "PATH2", "gap_id": "G1", "family": "Журнал событий", "purpose": "Проверить применение правила", "state": "planned", "reason": "Новое уточнение требует отдельного основания"}
    research.engine.call("parent", request("revise", research.work, 9, "new-path", change={"type": "plan_path", "path": path}))
    with pytest.raises(PermissionError, match="paths_not_verified"):
        research.engine.call("parent", request("assess", research.work, 10, "invalid-now", assessment=assessment("sufficient")))
    with research.host.store.db() as db:
        assert len(research.engine.research.rows(db, research.work, "PATH1")) == 1


def test_path_review_idempotency_cannot_hide_changed_operator_evidence(research):
    receipt = dispatch(research)
    args = request("operator_path_review", research.work, 6, "stable-review-id", path_id="PATH1")
    review = OperatorReview(research.engine.research)
    payload = dict(actor="operator", state="performed", reason="Сверены оба текста", receipts=[{"attempt_id": receipt["attempt_id"], "receipt_sha256": digest(receipt)}],
                   source_refs=[], reading_level="tool result", remaining=[])
    first = review.path("parent", args, **payload)
    assert review.path("parent", args, **payload)["replayed"]
    with pytest.raises(ValueError, match="idempotency_input_conflict"):
        review.path("parent", args, **{**payload, "reason": "Другой вывод"})
    assert first["revision"] == 7


def test_path_receipt_state_survives_new_host_adapter_and_explicit_v4_upgrade(research):
    from bnn_intake.research import Research
    receipt = dispatch(research)
    with research.host.store.db() as db:
        history = [dict(r) for r in db.execute("SELECT revision,sha256 FROM intake_revisions")]
        db.execute("DROP TABLE intake_path_attempts")
        db.execute("DROP TABLE intake_delegations")
        db.execute("UPDATE meta SET value='4' WHERE key='semantic_intake_contract'")
    assert install(research.host.store) == {"schema_version": 6, "changed": True}
    with research.host.store.db() as db:
        assert [dict(r) for r in db.execute("SELECT revision,sha256 FROM intake_revisions")] == history
    # Upgrade intentionally began from a v4 DB that had no new receipt table.
    assert Research(research.engine, research.host).call("parent", request("completion", research.work))["unperformed_paths"] == ["PATH1"]


def test_off_retains_saved_receipt_but_does_not_capture_new_normal_work(research):
    receipt = dispatch(research)
    research.engine.set_mode("parent", "off", 1, "owner")
    value = research.engine.research.call("parent", request("read_result", research.work,
        attempt_id=receipt["attempt_id"], receipt_sha256=digest(receipt), offset=0, limit=65536))
    assert value["complete"]
    # Plain approved reads can continue, but their results aren't credited to intake.
    with research.host.store.db() as db:
        db.execute("DROP TABLE intake_path_attempts")
        db.execute("DROP TABLE intake_delegations")
    turn(research)
    identity = {"session_id": "parent", "turn_id": "native-turn", "tool_call_id": "plain-after-off", "api_request_id": "plain-api"}
    with tool_attempt_scope("fixture_original", identity, final_boundary="registry"):
        raw = research.ctx.dispatch_tool("fixture_original", {"query": "обычная работа"}, **identity)
        assert authorize("tool_result", {"tool_name": "fixture_original", "args": {"query": "обычная работа"}, "result": raw}, **identity)[0].allow


def test_mode_change_inside_common_control_closes_path_before_source_executor(research):
    bridge = research.attached["context_bridge"]
    original = bridge.originals["tool_control"]
    def changed(payload, **identity):
        decision = original(payload, **identity)
        if payload["tool_name"] == "fixture_original":
            research.engine.set_mode("parent", "off", 1, "owner")
        return decision
    bridge.originals["tool_control"] = changed
    with pytest.raises(ExecutionControlDenied, match="changed_before_dispatch"):
        dispatch(research)
    assert research.source_calls == []


def test_result_after_work_change_remains_preserved_but_unusable(research, monkeypatch):
    original = research.engine.research.returned
    def changed(payload, identity):
        if payload["tool_name"] == "fixture_original":
            research.engine.set_mode("parent", "off", 1, "owner")
        return original(payload, identity)
    monkeypatch.setattr(research.engine.research, "returned", changed)
    receipt = dispatch(research)
    assert receipt["status"] == "returned_stale" and "result" in receipt
    with pytest.raises(PermissionError, match="not_usable"):
        research.engine.research.call("parent", request("read_result", research.work, attempt_id=receipt["attempt_id"], receipt_sha256=digest(receipt), offset=0, limit=20))


def test_mcp_error_inside_text_envelope_remains_an_error(research):
    receipt = dispatch(research, handler_result={"content": [{"type": "text", "text": '{"ok":false,"error":"synthetic denied"}'}]})
    assert receipt["result_is_error"]
    with pytest.raises(PermissionError, match="non_error_result"):
        review_path(research, receipt, state="no_result")


def test_sensitive_source_output_is_not_retained_or_credited(research):
    with pytest.raises(ExecutionControlDenied, match="sensitive_result"):
        dispatch(research, handler_result={"api_key": "sk-proj-123456789012345678901234567890"})
    with research.host.store.db() as db:
        receipt = research.engine.research.rows(db, research.work)[0]
    assert receipt["status"] == "capture_rejected" and "result" not in receipt
    assert "123456789012345678901234567890" not in canonical(receipt)


def test_receipt_for_another_work_cannot_be_adopted(research):
    receipt = dispatch(research)
    with research.host.store.db() as db:
        db.execute("INSERT INTO working_cards VALUES(?,?,?,?)", (research.host.store.profile, "WORK-other", "p", "{}"))
        db.execute("UPDATE working_bindings SET work_id='WORK-other' WHERE sid='parent'")
        with pytest.raises(PermissionError, match="version_unavailable"):
            research.engine.research.checked_receipt(db, "parent", "WORK-other", receipt["attempt_id"], digest(receipt))


def test_tampered_receipt_and_native_hash_cannot_become_source(research):
    receipt = dispatch(research)
    with research.host.store.db() as db:
        db.execute("UPDATE action_attempts SET result_hash='changed' WHERE nonce=?", (receipt["attempt_id"],))
    with pytest.raises(PermissionError, match="not_usable"):
        research.engine.research.call("parent", request("read_result", research.work, attempt_id=receipt["attempt_id"], receipt_sha256=digest(receipt), offset=0, limit=20))
    with research.host.store.db() as db:
        db.execute("UPDATE intake_path_attempts SET body='{}' WHERE revision=2")
    with pytest.raises(PermissionError, match="integrity_failed"):
        research.engine.research.call("parent", request("status", research.work, path_id="PATH1", offset=0, limit=10))


def test_optional_unfinished_path_still_prevents_false_complete_assessment(research):
    receipt = dispatch(research); review_path(research, receipt, state="incomplete")
    value = assessment("limited"); value["remaining"] = ["Осталась исполнимая проверка"]; value["limits"] = ["Часть работы не выполнена"]
    with pytest.raises(PermissionError, match="paths_not_verified"):
        research.engine.call("parent", request("assess", research.work, 7, "cannot-hide-work", assessment=value))
    pause = assessment("resource_paused"); pause["remaining"] = value["remaining"]
    research.engine.call("parent", request("assess", research.work, 7, "actual-pause", assessment=pause))
    assert research.engine.status("parent", research.work)["state"]["assessments"][-1]["state"] == "resource_paused"


def test_renaming_same_path_does_not_evade_repeat_detection(research):
    state = research.engine.status("parent", research.work)["state"]
    path = {k: v for k, v in state["paths"]["PATH1"].items() if k not in {"claim_review", "execution_receipt"}}
    path["id"] = "renamed"
    with pytest.raises(ValueError, match="exact_path_repeat"):
        research.engine.call("parent", request("revise", research.work, 6, "same-path-new-id", change={"type": "plan_path", "path": path}))


def test_owner_permission_is_not_resolved_by_external_publications(research):
    receipt = dispatch(research); review_path(research, receipt)
    # The guarded semantic kind is frozen in the legitimate task revision.
    with research.host.store.db() as db:
        from bnn_intake.storage import read, append
        state = read(db, research.host.store.profile, research.work)
        state["revision"] += 1; state["gaps"]["G1"]["kind"] = "permission"
        append(db, research.host.store.profile, research.work, state)
    with pytest.raises(PermissionError, match="separate_owner_resolution"):
        OperatorReview(research.engine.research).gap("parent", request("operator_gap_review", research.work, 8, "permission-not-inferred", gap_id="G1"),
            actor="operator", state="resolved", reason="Публичный материал не является согласием владельца", path_ids=["PATH1"], source_refs=[], remaining=[])


def test_stopped_interview_does_not_block_an_independent_approved_source(research):
    from bnn_intake.interview import Interview
    Interview(research.engine).control("parent", {"action": "stop", "work_ref": research.work,
        "expected_revision": 6, "operation_id": "stop-only-interview"}, "owner")
    receipt = dispatch(research)
    assert receipt["status"] == "returned" and len(research.source_calls) == 1
    state = research.engine.status("parent", research.work)["state"]
    assert state["interview"]["state"] == "stopped" and state["gaps"]["G1"]["research_state"] == "open"


def test_new_returned_result_reopens_path_review_without_erasing_the_old_one(research):
    first = dispatch(research); review_path(research, first)
    second = dispatch(research, query="Проверить исключение", call="counterevidence")
    value = research.engine.research.call("parent", request("completion", research.work))
    assert value["unperformed_paths"] == ["PATH1"]
    assert value["paths"]["PATH1"]["review"]["receipts"][0]["attempt_id"] == first["attempt_id"]
    assert len(value["paths"]["PATH1"]["attempts"]) == 2 and second["status"] == "returned"


def test_actual_model_status_arguments_use_bounded_defaults(research):
    receipt = dispatch(research)
    # Exact failed arguments observed in the separate genuine model episode.
    value = research.engine.research.call("parent", request("status", research.work))
    assert value["attempts"][0]["attempt_id"] == receipt["attempt_id"]
    content = research.engine.research.call("parent", request("read_result", research.work,
        attempt_id=receipt["attempt_id"], receipt_sha256=digest(receipt)))
    assert content["complete"] and content["text"] == canonical(receipt["result"])
    with pytest.raises(ValueError, match="contract_invalid"):
        research.engine.research.call("parent", request("status", research.work, limit=1000000))


def test_preparation_routes_to_saved_receipt_instead_of_repeating_source(research):
    receipt = dispatch(research)
    router = research.attached["context_bridge"].preparation
    state = research.engine.status("parent", research.work)["state"]
    value = router.describe("parent", research.engine.mode("parent"), research.work, state)
    assert value["stage"] == "use_saved_research_result"
    args = value["next_action"]["known_arguments"]
    assert args["attempt_id"] == receipt["attempt_id"] and args["receipt_sha256"] == digest(receipt)
    assert len(research.source_calls) == 1


def test_invalid_source_selection_is_rejected_before_write_reservation(research):
    turn(research)
    args = request("select_path", research.work, 6, "not-an-authorized-source", path_id="PATH1", tool_name="not_installed")
    identity = {"session_id": "parent", "turn_id": "native-turn", "tool_call_id": "bad-source", "api_request_id": "bad-source-api"}
    with tool_attempt_scope("intake_research", identity, final_boundary="registry"):
        with pytest.raises(ExecutionControlDenied, match="exact_read_rule"):
            research.ctx.dispatch_tool("intake_research", args, **identity)
    with research.host.store.db() as db:
        assert db.execute("SELECT count(*) FROM action_attempts WHERE call_id='bad-source'").fetchone()[0] == 0
    assert research.engine.status("parent", research.work)["state"]["revision"] == 6


def test_parallel_identical_calls_are_serialized_before_the_source_effect(research, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    turn(research)
    barrier = Barrier(2)
    original = research.engine.research.prepare
    def together(payload, identity):
        info = original(payload, identity)
        if info:
            barrier.wait(timeout=4)
        return info
    monkeypatch.setattr(research.engine.research, "prepare", together)
    def worker(number):
        identity = {"session_id": "parent", "turn_id": "native-turn", "tool_call_id": "parallel-" + str(number), "api_request_id": "parallel-api-" + str(number)}
        try:
            with tool_attempt_scope("fixture_original", identity, final_boundary="registry"):
                result = research.ctx.dispatch_tool("fixture_original", {"query": "Один и тот же вопрос"}, **identity)
                authorize("tool_result", {"tool_name": "fixture_original", "args": {"query": "Один и тот же вопрос"}, "result": result}, **identity)
            return "returned"
        except ExecutionControlDenied as exc:
            assert "exact_repeat" in str(exc)
            return "blocked_duplicate"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, [1, 2]))
    assert sorted(results) == ["blocked_duplicate", "returned"]
    assert len(research.source_calls) == 1


def test_real_outer_precheck_then_agent_final_dispatch_then_registry(research):
    from hermes_cli.middleware import run_tool_execution_middleware
    turn(research)
    identity = {"session_id": "parent", "turn_id": "native-turn", "tool_call_id": "native-final-route", "api_request_id": "native-final-api", "platform": "cli"}
    args = {"query": "Фактический модельный маршрут"}
    def terminal(current):
        # Same distinct final dispatch as AIAgent, rather than a direct registry call.
        authorize("tool", {"tool_name": "fixture_original", "args": current}, boundary="agent_final_dispatch", **identity)
        return research.ctx.dispatch_tool("fixture_original", current, **identity)
    result = run_tool_execution_middleware("fixture_original", args, terminal, execution_target="agent_final_dispatch", **identity)
    assert "Считать уникальные записи" in result
    with research.host.store.db() as db:
        receipt = research.engine.research.rows(db, research.work)[0]
        row = db.execute("SELECT boundaries,status FROM action_attempts WHERE nonce=?", (receipt["attempt_id"],)).fetchone()
    assert receipt["status"] == "returned" and row["status"] == "returned_unreviewed"
    assert json.loads(row["boundaries"]) == ["execution_middleware", "agent_final_dispatch", "registry"]
