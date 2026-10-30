"""Real PluginManager, registry and five common controllers; no provider/Telegram."""
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace
import pytest
from agent.tool_attempt import tool_attempt_scope
from bn_memory.plugin import Host
from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest
from hermes_cli.execution_control import authorize, ExecutionControlDenied
from bnn_intake.adapter import attach
from bnn_intake.policy import action_rules
from conftest import BASELINE, prepare, record, request
from test_exposition import exposition


@pytest.fixture
def native(fixture, contract, tmp_path, monkeypatch):
    store, initial, work = fixture
    home = Path(store.config["profile_home"]); home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    required = {kind: ["bn-common-memory"] for kind in ["tool", "tool_result", "llm", "auxiliary", "subagent_launch"]}
    (home / "config.yaml").write_text("execution_controls_required: true\n")
    (home / "execution-controls.json").write_text(json.dumps({"version": 1, "required": required, "timeout_seconds": 10}))
    key = tmp_path / "test-only-signing-key"; key.write_bytes(os.urandom(32)); key.chmod(0o600)
    config = {**store.config, "package_root": BASELINE["memory_release"], "host_key": str(key),
              "context_bytes": 200000, "max_request_bytes": 1048576,
              "background_sync_enabled": False, "semantic_context": False,
              "semantic_intake_bridge": True, "command_owners": ["fixture-owner"],
              "tool_policy": {"version": 1, "rules": action_rules()}}
    config_file = tmp_path / "native-config.json"; config_file.write_text(json.dumps(config))
    host = Host(config_file)
    manager = PluginManager(scope_key=str(home))
    ctx = PluginContext(PluginManifest(name="bn-common-memory", version="intake-candidate", source="project", path=str(tmp_path)), manager)
    host.ctx = ctx
    attached = attach(host, ctx)  # Before common control registration, as the candidate requires.
    handles = []
    for kind, fn in [("tool", host.tool_control), ("tool_result", host.result_control),
                     ("llm", host.model_control), ("auxiliary", host.model_control), ("subagent_launch", host.launch_control)]:
        handles.append(ctx.register_execution_control(kind, fn))
    handles += [ctx.register_hook("pre_llm_call", host.before_turn), ctx.register_middleware("llm_request", host.request)]
    engine = attached["engine"]
    prepare(engine, work, contract)
    ref = record(host.store)
    engine.call("parent", request("expose", work, 1, "exp", exposition=exposition("E1", ref)))
    engine.call("parent", request("select_context", work, 2, "select", exposition_id="E1"))
    value = SimpleNamespace(host=host, manager=manager, ctx=ctx, engine=engine, work=work,
                            controls=handles, attached=attached, home=home)
    try:
        yield value
    finally:
        for handle in reversed(handles + attached["registrations"]):
            if handle is not None:
                handle.dispose()
        host.close()


def turn(native, text="Прочитать адресный исходник", turn_id="native-turn"):
    results = native.manager.invoke_hook("pre_llm_call", session_id="parent", user_message=text, turn_id=turn_id, platform="cli")
    assert results and "context" in results[-1]
    incoming = {"model": "boundary-test-no-provider", "instructions": "Stable system layer",
                "input": [{"role": "user", "content": text}]}
    changes = native.manager.invoke_middleware("llm_request", _payload_key="request", request=incoming,
                original_request=copy.deepcopy(incoming), session_id="parent", turn_id=turn_id, platform="cli")
    assert changes and "request" in changes[-1]
    return changes[-1]["request"]


def model_admit(prepared, turn_id="native-turn", api="boundary-1"):
    return authorize("llm", {"request": prepared}, session_id="parent", turn_id=turn_id,
                     api_request_id=api, platform="cli")


def test_real_native_signed_envelope_contains_addressed_input(native):
    prepared = turn(native)
    assert prepared["instructions"] == "Stable system layer"
    text = json.dumps(prepared, ensure_ascii=False)
    assert "BN_INTAKE_CONTEXT" in text and "before=20, after=30" in text
    assert model_admit(prepared)[0].allow
    with native.host.store.db() as db:
        count = db.execute("SELECT count(*) FROM context_attempts").fetchone()[0]
    assert count == 1
    target = os.environ.get("BNN_INTAKE_TEST_EVIDENCE")
    if target:
        with native.host.store.db() as db:
            context_receipts = [dict(r) for r in db.execute("SELECT * FROM context_attempts")]
            intake_receipts = [dict(r) for r in db.execute("SELECT * FROM intake_publications")]
        proof = {"prepared_request": prepared, "common_context_receipts": context_receipts,
                 "intake_context_receipts": intake_receipts, "required_controls": json.loads((native.home / "execution-controls.json").read_text()),
                 "provider_called": False, "stage": "real_native_final_admission_only", "data": "synthetic"}
        (Path(target) / "native-final-admission.json").write_text(json.dumps(proof, ensure_ascii=False, indent=2) + "\n")


def test_stripping_intake_from_the_signed_input_is_denied(native):
    prepared = turn(native)
    damaged = copy.deepcopy(prepared)
    rows = damaged["input"][0]["content"]
    if isinstance(rows, str):
        damaged["input"][0]["content"] = rows.replace("before=20, after=30", "truncated")
    else:
        for part in rows:
            if isinstance(part, dict) and "text" in part:
                part["text"] = part["text"].replace("before=20, after=30", "truncated")
    with pytest.raises(ExecutionControlDenied):
        model_admit(damaged)


def test_same_turn_changes_require_fresh_signed_intake_without_repeating_user(native):
    original = turn(native)
    from bnn_intake.interview import Interview
    Interview(native.engine).control("parent", {"action": "stop", "work_ref": native.work,
        "expected_revision": 3, "operation_id": "native-stop"}, "native-owner")
    with pytest.raises(ExecutionControlDenied, match="intake_current_context"):
        model_admit(original)
    refreshed = native.host.request(original, session_id="parent", turn_id="native-turn", platform="cli")["request"]
    assert model_admit(refreshed, api="boundary-fresh")[0].allow
    rendered = json.dumps(refreshed, ensure_ascii=False)
    assert rendered.count("BN_CONTEXT_ADMISSION signature") == 1
    assert 'stopped' in rendered


def test_off_removes_new_intake_context_and_preserves_common_protection(native):
    original = turn(native)
    native.engine.set_mode("parent", "off", 1, "owner")
    updated = native.host.request(original, session_id="parent", turn_id="native-turn", platform="cli")["request"]
    assert "BN_INTAKE_CONTEXT" not in json.dumps(updated)
    assert model_admit(updated)[0].allow
    with pytest.raises(ExecutionControlDenied):
        model_admit({"input": [{"role": "user", "content": "Прочитать адресный исходник"}]})


def test_missing_required_common_controller_is_not_replaced_by_intake(native):
    prepared = turn(native)
    # Remove only this fixture's actual registered LLM controller; restore by fixture cleanup.
    native.controls[2].dispose()
    with pytest.raises(ExecutionControlDenied, match="unavailable"):
        model_admit(prepared)


def test_real_registry_dispatch_and_result_controller(native):
    turn(native)
    args = request("status", native.work)
    identity = {"session_id": "parent", "turn_id": "native-turn", "tool_call_id": "registry-status", "api_request_id": "registry-call"}
    with tool_attempt_scope("intake_work", identity, final_boundary="registry"):
        result = native.ctx.dispatch_tool("intake_work", args, **identity)
        assert json.loads(result)["state"]["revision"] == 3
        assert authorize("tool_result", {"tool_name": "intake_work", "args": args, "result": result}, **identity)[0].allow


def test_native_write_question_gate_and_known_outcome_settlement(native):
    from test_gaps import gap
    from bnn_intake.interview import Interview
    turn(native)
    def dispatch(name, args, call):
        identity = {"session_id": "parent", "turn_id": "native-turn", "tool_call_id": call, "api_request_id": call + "-api"}
        with tool_attempt_scope(name, identity, final_boundary="registry"):
            raw = native.ctx.dispatch_tool(name, args, **identity)
            value = json.loads(raw)
            assert value.get("ok") is True, value
            authorize("tool_result", {"tool_name": name, "args": args, "result": raw}, **identity)
        return value
    dispatch("intake_work", request("revise", native.work, 3, "native-gap", change={"type": "add_gap", "gap": gap()}), "write-gap")
    question = {"id": "QN", "gap_id": "G1", "topic": "measurement", "purpose": "Уточнить правило", "text": "Как задан показатель?"}
    dispatch("intake_questions", request("plan_question", native.work, 4, "native-plan", question=question), "write-question")
    args = request("present_question", native.work, 5, "native-present", question_id="QN")
    output = dispatch("intake_questions", args, "issue-question")
    assert not output["received_by_user"] and not output["outbound_message_sent"]
    Interview(native.engine).control("parent", {"action": "stop", "work_ref": native.work,
        "expected_revision": 6, "operation_id": "stop-after-issue"}, "native-owner")
    identity = {"session_id": "parent", "turn_id": "native-turn", "tool_call_id": "denied-repeat", "api_request_id": "denied-repeat-api"}
    with tool_attempt_scope("intake_questions", identity, final_boundary="registry"):
        with pytest.raises(ExecutionControlDenied, match="interview_stopped"):
            native.ctx.dispatch_tool("intake_questions", args, **identity)
    with native.host.store.db() as db:
        rows = [dict(row) for row in db.execute("SELECT tool_name,call_id,status FROM action_attempts")]
    assert len(rows) == 3 and all(row["status"] == "returned_unreviewed" for row in rows)
    target = os.environ.get("BNN_INTAKE_TEST_EVIDENCE")
    if target:
        (Path(target) / "native-tool-write-settlement.json").write_text(json.dumps({"attempts": rows,
            "denied_repeat_created_an_attempt": False, "question_result": output,
            "actual_messages_sent": 0, "data": "synthetic"}, ensure_ascii=False, indent=2) + "\n")


def test_owner_command_uses_real_native_session_identity_and_denies_other_gateway_user(native):
    from gateway.session_context import set_session_vars, clear_session_vars
    command = native.manager._plugin_commands["intake"]["handler"]
    tokens = set_session_vars(platform="cli", session_id="parent")
    try:
        assert json.loads(command("interview stop"))["interview_state"] == "stopped"
    finally:
        clear_session_vars(tokens)
    tokens = set_session_vars(platform="telegram", session_id="parent", user_id="unregistered-user")
    try:
        with pytest.raises(PermissionError, match="owner_required"):
            command("interview resume")
    finally:
        clear_session_vars(tokens)


def test_off_path_survives_failure_of_optional_receipt_table(native):
    from bnn_intake.interview import Interview
    Interview(native.engine).control("parent", {"action": "stop", "work_ref": native.work,
        "expected_revision": 3, "operation_id": "stop-before-off"}, "native-owner")
    native.engine.set_mode("parent", "off", 1, "owner")
    with native.host.store.db() as db:
        db.execute("DROP TABLE intake_publications")
    prepared = turn(native)
    assert model_admit(prepared)[0].allow
    assert "BN_OWNER_INTERVIEW_CONTROLS" in json.dumps(prepared)
    assert "stopped" in json.dumps(prepared)
    assert "BN_INTAKE_CONTEXT" not in json.dumps(prepared)


def test_lifecycle_hook_failure_cannot_authorize_model_execution(native):
    native.host.store.config["intake_context_bytes"] = 10
    results = native.manager.invoke_hook("pre_llm_call", session_id="parent", user_message="bounded", turn_id="too-large", platform="cli")
    assert results == []
    with pytest.raises(ExecutionControlDenied):
        model_admit({"input": [{"role": "user", "content": "bounded"}]}, turn_id="too-large")


def test_change_during_common_admission_is_rechecked_before_dispatch(native):
    prepared = turn(native)
    bridge = native.attached["context_bridge"]
    original = bridge.originals["model_control"]
    def change_policy(payload, **identity):
        decision = original(payload, **identity)
        from bnn_intake.interview import Interview
        Interview(native.engine).control("parent", {"action": "stop", "work_ref": native.work,
            "expected_revision": 3, "operation_id": "during-admission"}, "native-owner")
        return decision
    bridge.originals["model_control"] = change_policy
    with pytest.raises(ExecutionControlDenied, match="intake_current_context"):
        model_admit(prepared)


def test_interview_change_does_not_invalidate_an_unrelated_work(native):
    from bnn_intake.interview import Interview
    store = native.host.store
    store.bind("second-parent")
    work = "WORK-independent"
    with store.db() as db:
        db.execute("INSERT INTO working_cards VALUES(?,?,?,?)", (store.profile, work, "p", json.dumps({"id": "synthetic-second-bead"})))
        db.execute("INSERT INTO working_bindings VALUES(?,?,?)", (store.profile, "second-parent", work))
    native.engine.set_mode("second-parent", "on", 0, "owner")
    contract = native.engine.status("parent", native.work)["state"]["contract"]
    native.engine.call("second-parent", request("prepare", work, 0, "independent-prepare", contract=contract))
    native.host.before_turn(session_id="second-parent", user_message="Независимая работа", turn_id="second-turn", platform="cli")
    incoming = {"input": [{"role": "user", "content": "Независимая работа"}]}
    prepared = native.host.request(incoming, session_id="second-parent", turn_id="second-turn", platform="cli")["request"]
    Interview(native.engine).control("parent", {"action": "stop", "work_ref": native.work,
        "expected_revision": 3, "operation_id": "unrelated-owner-stop"}, "native-owner")
    result = authorize("llm", {"request": prepared}, session_id="second-parent", turn_id="second-turn",
                       api_request_id="independent-check", platform="cli")
    assert result[0].allow
