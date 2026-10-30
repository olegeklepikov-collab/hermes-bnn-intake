"""Signed lifecycle routing on actual native inputs; no autonomous model claimed."""
import copy
import json
import pytest
from bnn_intake.storage import digest
from conftest import contract, prepare, request
from test_native_context import native, turn, model_admit
from hermes_cli.execution_control import ExecutionControlDenied


def clear_intake_work(native):
    with native.host.store.db() as db:
        db.execute("DELETE FROM intake_operations")
        db.execute("DELETE FROM intake_revisions")


def payload(native):
    return native.attached["context_bridge"].frame("parent")


def test_on_routes_unprepared_work_from_complete_native_input_not_short_query(native):
    clear_intake_work(native)
    text = "Подготовить исследование. " * 100 + "Обязательное условие в конце: не проводить интервью."
    prepared = turn(native, text=text, turn_id="prepare-turn")
    rendered = json.dumps(prepared, ensure_ascii=False)
    assert "BN_INTAKE_PREPARATION" in rendered and "prepare_work" in rendered
    frame = payload(native)
    value = json.loads(frame["text"].split("\n", 2)[1])
    from bn_memory.admission import anchors_for
    assert value["native_request"]["input_anchors"] == anchors_for(text)
    assert value["native_request"]["input_anchors"][0]["bytes"] == len(text.encode())
    assert value["work_ref"] == native.work and not value["executor_started"]
    assert model_admit(prepared, turn_id="prepare-turn")[0].allow
    assert "не проводить интервью" in rendered  # Original native message kept intact.
    assert len(frame["text"].encode()) < 5000 and text not in frame["text"]


def test_preparation_updates_to_exposition_stage_after_prepare_in_same_turn(native, contract):
    clear_intake_work(native)
    old = turn(native)
    assert "prepare_work" in json.dumps(old)
    prepare(native.engine, native.work, contract)
    with pytest.raises(ExecutionControlDenied, match="current_context"):
        model_admit(old)
    refreshed = native.host.request(old, session_id="parent", turn_id="native-turn", platform="cli")["request"]
    assert "plan_exposition" in json.dumps(refreshed)
    assert model_admit(refreshed)[0].allow


def test_off_removes_preparation_header_and_survives_optional_outcome_failure(native):
    clear_intake_work(native)
    old = turn(native)
    with native.host.store.db() as db:
        db.execute("DROP TABLE intake_model_outcomes")
    from gateway.session_context import set_session_vars, clear_session_vars
    command = native.manager._plugin_commands["intake"]["handler"]
    tokens = set_session_vars(platform="cli", session_id="parent")
    try:
        assert json.loads(command("off"))["mode"] == "off"
    finally:
        clear_session_vars(tokens)
    refreshed = native.host.request(old, session_id="parent", turn_id="native-turn", platform="cli")["request"]
    assert "BN_INTAKE_PREPARATION" not in json.dumps(refreshed)
    assert model_admit(refreshed)[0].allow


def test_auto_does_not_create_a_work_or_contract_for_an_unbound_conversation(native):
    clear_intake_work(native)
    native.engine.set_mode("parent", "auto", 1, "owner")
    with native.host.store.db() as db:
        db.execute("DELETE FROM working_bindings WHERE profile_id=? AND sid='parent'", (native.host.store.profile,))
    prepared = turn(native, text="Спасибо")
    assert "BN_INTAKE_PREPARATION" not in json.dumps(prepared)
    assert model_admit(prepared)[0].allow
    with native.host.store.db() as db:
        assert db.execute("SELECT count(*) FROM intake_revisions").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM working_bindings").fetchone()[0] == 0


def test_on_unbound_routes_to_the_existing_native_work_binding_interface(native):
    clear_intake_work(native)
    with native.host.store.db() as db:
        db.execute("DELETE FROM working_bindings WHERE profile_id=? AND sid='parent'", (native.host.store.profile,))
    prepared = turn(native)
    assert "bind_native_work" in json.dumps(prepared) and "memory_working" in json.dumps(prepared)
    assert model_admit(prepared)[0].allow


def test_preparation_does_not_renew_paused_or_cancelled_work(native):
    revision = native.engine.status("parent", native.work)["state"]["revision"]
    native.engine.call("parent", request("pause", native.work, revision, "hold", reason="Ожидать исходный материал"))
    prepared = turn(native)
    assert '"stage"' not in payload(native)["text"]
    assert model_admit(prepared)[0].allow
    assert native.engine.status("parent", native.work)["state"]["lifecycle"] == "paused"
