import pytest
from bn_memory.working import Working
from conftest import prepare, request


def gap():
    return {"id": "G1", "question": "Как изменилось правило подсчёта?", "kind": "fact",
            "material": True, "affected": ["причинное объяснение изменения"], "required_result": "Текст обоих правил"}


def add_gap(engine, work):
    return engine.call("parent", request("revise", work, 1, "gap", change={"type": "add_gap", "gap": gap()}))


def assessment(state):
    return {"id": "A1", "purpose": "Оценить достаточность", "state": state,
            "remaining": [], "limits": [], "next_step": "Получить необходимые основания"}


def test_user_refusal_is_a_native_input_and_does_not_close_gap(fixture, contract):
    store, engine, work = fixture
    prepare(engine, work, contract); add_gap(engine, work)
    text = "Не хочу отвечать на этот вопрос"
    Working(store).capture("parent", "test-turn", text)
    with store.db() as db:
        row = db.execute("SELECT id FROM working_sources WHERE sid='parent'").fetchone()
    engine.call("parent", request("revise", work, 2, "response", change={"type": "record_response", "gap_id": "G1", "response_state": "refused",
                "source_ref": {"kind": "native_input", "id": row[0], "start": 0, "end": len(text)}}))
    state = engine.status("parent", work)["state"]
    assert state["gaps"]["G1"]["response_state"] == "refused"
    assert state["gaps"]["G1"]["research_state"] == "open"
    assert state["responses"][0]["quote"] == text
    path = {"id": "P1", "gap_id": "G1", "family": "официальные документы", "purpose": "Найти правила независимо от интервью", "state": "planned", "reason": "Отказ отвечать не запрещает исследование"}
    engine.call("parent", request("revise", work, 3, "path", change={"type": "plan_path", "path": path}))
    assert engine.status("parent", work)["state"]["paths"]["P1"]["state"] == "planned"


def test_fake_response_without_native_origin_is_rejected(fixture, contract):
    store, engine, work = fixture
    prepare(engine, work, contract); add_gap(engine, work)
    with pytest.raises(PermissionError, match="native_response_origin"):
        engine.call("parent", request("revise", work, 2, "fake", change={"type": "record_response", "gap_id": "G1", "response_state": "answered",
                    "source_ref": {"kind": "native_input", "id": "invented", "start": 0, "end": 1}}))


def test_unresolved_material_gap_cannot_be_declared_sufficient(fixture, contract):
    store, engine, work = fixture
    prepare(engine, work, contract); add_gap(engine, work)
    with pytest.raises(PermissionError, match="unresolved_material"):
        engine.call("parent", request("assess", work, 2, "assess", assessment=assessment("sufficient")))


def test_limited_result_cannot_replace_actual_research_with_plan(fixture, contract):
    store, engine, work = fixture
    prepare(engine, work, contract); add_gap(engine, work)
    path = {"id": "P1", "gap_id": "G1", "family": "официальные документы", "purpose": "Поиск", "state": "planned", "reason": "Есть полезный путь"}
    engine.call("parent", request("revise", work, 2, "path", change={"type": "plan_path", "path": path}))
    with pytest.raises(PermissionError, match="paths_not_verified"):
        engine.call("parent", request("assess", work, 3, "assess", assessment=assessment("limited")))


def test_model_cannot_mark_its_own_exclusion_as_verified(fixture, contract):
    store, engine, work = fixture
    prepare(engine, work, contract); add_gap(engine, work)
    path = {"id": "P1", "gap_id": "G1", "family": "частная база", "purpose": "Получение", "state": "unavailable", "reason": "Нет доступа"}
    engine.call("parent", request("revise", work, 2, "path", change={"type": "plan_path", "path": path}))
    with pytest.raises(PermissionError, match="paths_not_verified"):
        engine.call("parent", request("assess", work, 3, "assess", assessment=assessment("limited")))


def test_resource_pause_remains_unfinished_and_never_semantic_acceptance(fixture, contract):
    store, engine, work = fixture
    prepare(engine, work, contract); add_gap(engine, work)
    result = engine.call("parent", request("assess", work, 2, "paused", assessment=assessment("resource_paused")))
    state = engine.status("parent", work)["state"]
    assert state["assessments"][0]["completion_accepted"] is False
    assert result["semantic_acceptance"] == "unreviewed"
    assert state["gaps"]["G1"]["research_state"] == "open"
