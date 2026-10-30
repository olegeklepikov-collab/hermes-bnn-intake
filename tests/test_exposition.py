import copy
from concurrent.futures import ThreadPoolExecutor
import pytest
from bnn_intake.catalogue import Catalogue
from conftest import prepare, record, request


def exposition(identifier, ref, purpose="Проверить исходные значения", dependencies=None, methods=None):
    return {"id": identifier, "purpose": purpose, "question": "Что подтверждено источником?",
            "input_refs": [{"kind": "record", "id": ref["id"], "revision": ref["revision"]}],
            "depends_on": dependencies or [], "method_ids": methods or []}


def test_exact_primary_content_is_returned_without_claim_of_model_reading(fixture, contract):
    store, engine, work = fixture
    ref = record(store); prepare(engine, work, contract)
    engine.call("parent", request("expose", work, 1, "exp-1", exposition=exposition("E1", ref)))
    packet = engine.call("parent", request("handoff", work, exposition_id="E1"))
    assert packet["packet"]["records"][0]["content"] == store.read("parent", ref["id"])["content"]
    assert packet["delivered_to_model"] is False and packet["executor_started"] is False
    assert packet["packet"]["semantic_acceptance"] == "unreviewed"


def test_new_purpose_on_the_same_material_creates_new_exposition(fixture, contract):
    store, engine, work = fixture
    ref = record(store); prepare(engine, work, contract)
    engine.call("parent", request("expose", work, 1, "op1", exposition=exposition("E1", ref)))
    engine.call("parent", request("expose", work, 2, "op2", exposition=exposition("E2", ref, "Найти альтернативную интерпретацию", ["E1"])))
    result = engine.handoff("parent", work, "E2")
    assert result["packet"]["dependencies"] == ["E1", "E2"]
    assert len(result["packet"]["records"]) == 1


def test_new_ids_do_not_evade_exact_purpose_input_repeat_detection(fixture, contract):
    store, engine, work = fixture
    ref = record(store); prepare(engine, work, contract)
    engine.call("parent", request("expose", work, 1, "op1", exposition=exposition("E1", ref)))
    with pytest.raises(ValueError, match="exact_repeat_use_existing:E1"):
        engine.call("parent", request("expose", work, 2, "op2", exposition=exposition("renamed-E1", ref)))
    assert engine.status("parent", work)["state"]["revision"] == 2


def test_missing_dependency_cannot_create_exposition(fixture, contract):
    store, engine, work = fixture
    ref = record(store); prepare(engine, work, contract)
    with pytest.raises(ValueError, match="dependency_missing"):
        engine.call("parent", request("expose", work, 1, "bad", exposition=exposition("E1", ref, dependencies=["missing"])))
    assert engine.status("parent", work)["state"]["revision"] == 1


def test_context_overflow_requires_split_without_trimming(fixture, contract):
    store, engine, work = fixture
    ref = record(store, content="BEGIN\n" + "исходное содержание " * 80 + "\nEND")
    prepare(engine, work, contract)
    engine.call("parent", request("expose", work, 1, "exp", exposition=exposition("E1", ref)))
    store.config["intake_context_bytes"] = 200
    with pytest.raises(PermissionError, match="split_required"):
        engine.handoff("parent", work, "E1")
    assert store.read("parent", ref["id"])["content"].endswith("END")


def test_retracted_source_does_not_leak_through_saved_handoff(fixture, contract):
    store, engine, work = fixture
    ref = record(store); prepare(engine, work, contract)
    engine.call("parent", request("expose", work, 1, "exp", exposition=exposition("E1", ref)))
    store.retract("parent", ref["id"], ref["revision"])
    with pytest.raises(PermissionError, match="retracted"):
        engine.handoff("parent", work, "E1")


def test_unrelated_record_does_not_invalidate_work_revision(fixture, contract):
    store, engine, work = fixture
    prepare(engine, work, contract)
    before = engine.status("parent", work)["content_hash"]
    record(store, "unrelated", "Другой синтетический предмет")
    assert engine.status("parent", work)["content_hash"] == before


def test_method_outside_catalogue_can_be_used_with_local_descriptor(fixture, contract):
    store, engine, work = fixture
    prepare(engine, work, contract)
    method = {"id": "LOCAL-measurement-contrast", "name": "Сопоставление правил подсчёта",
              "kind": "локальная аналитическая трасса", "purpose": "Различить измерение и изменение объекта",
              "operation": "Применить два правила к одним событиям", "required_inputs": ["события", "правила"],
              "output": "матрица контрастов", "assumptions": ["сопоставимость событий"],
              "limitations": ["без событий результат остаётся условным"], "source_refs": [], "origin": "authorial"}
    engine.call("parent", request("revise", work, 1, "method", change={"type": "add_method", "descriptor": method}))
    ref = record(store)
    engine.call("parent", request("expose", work, 2, "exp", exposition=exposition("E1", ref, methods=[method["id"]])))
    assert method["id"] in engine.handoff("parent", work, "E1")["packet"]["methods"]


def test_addressed_catalogue_is_open_and_does_not_return_all_cards():
    catalogue = Catalogue()
    assert len(catalogue.entries) == 150
    out = catalogue.call({"action": "read", "ids": ["FW-001", "LOCAL-other"]})
    assert len(out["cards"]) == 1 and out["missing"] == ["LOCAL-other"]
    assert out["missing_id_is_not_a_method_prohibition"]


def test_concurrent_same_revision_has_single_winner(fixture, contract):
    store, engine, work = fixture
    prepare(engine, work, contract)
    def pause(op):
        try:
            return engine.call("parent", request("pause", work, 1, op, reason="concurrency"))["revision"]
        except ValueError as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(pause, ["first", "second"]))
    assert results.count(2) == 1 and results.count("intake_work_revision_conflict") == 1
