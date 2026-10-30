"""Real Hermes middleware/controls/registry with explicitly synthetic callbacks.

No provider is contacted. These tests qualify scope, products and lifecycle; they
do not certify scientific correctness or the ability of a real model to comply.
"""
import copy
import json
import os
import subprocess
import sys
from pathlib import Path
import pytest
from hermes_cli.execution_control import ExecutionControlDenied
from hermes_cli.middleware import run_llm_execution_middleware, run_tool_execution_middleware
from bnn_intake.analysis import Analysis, TRACES
from bnn_intake.storage import digest, install
from bnn_intake.contracts import PROFILES
from conftest import prepare, record, request
from test_exposition import exposition
from test_native_context import native, turn, model_admit


def application(exposition_id="E1", identifier="A1", trace="custom", method_id="FW-037"):
    levels = sorted(TRACES.get(trace, {"conceptualization"}))
    return {"id": identifier, "exposition_id": exposition_id, "method_id": method_id,
            "purpose": "Раскрыть понятие показателя и различить измерение и механизм",
            "selection_reason": "Карта понятий позволяет сохранить разные значения и связи",
            "axes": ["TR01", "LOCAL-contextual-meaning"], "families": ["MF03"], "trace": trace,
            "classification_extensions": [{"dimension": "axes", "id": "LOCAL-contextual-meaning",
                "definition": "Различение смысла показателя по контексту употребления", "related_to": ["TR01", "TR19"],
                "relation_reason": "Добавляет к понятию контекст исходного сообщения"}],
            "level_definitions": [{"id": level, "meaning": "Отдельный уровень: " + level} for level in levels],
            "operations": ["Выделить понятие", "Сопоставить значения и границы", "Сохранить неизвестные"],
            "unit_of_analysis": "Одно различимое утверждение с необходимым контекстом",
            "expected_product": "Типизированный граф с источниками и непокрытым остатком",
            "acceptance_checks": [{"id": "CHECK-" + level, "description": "Проверить уровень " + level, "level": level} for level in levels],
            "assumptions": ["Учебная модель"], "limitations": ["Не переносить два наблюдения на популяцию"],
            "product_dependencies": [],
            "perspectives_not_selected": [{"perspective": "Причинный эффект", "reason": "Нет независимого причинного дизайна"}]}


def plan_method(engine, work, app=None, select=False):
    if engine.analysis is None:
        engine.analysis = Analysis(engine)
    app = app or application()
    rev = engine.status("parent", work)["state"]["revision"]
    out = engine.analysis.call("parent", request("plan_method", work, rev, "plan-" + app["id"], application=app))
    if select:
        engine.analysis.call("parent", request("select_application", work, rev + 1, "select-" + app["id"], application_id=app["id"]))
    return out


@pytest.fixture
def analysis_env(fixture, contract):
    store, engine, work = fixture
    prepare(engine, work, contract)
    source = record(store)
    engine.call("parent", request("expose", work, 1, "exp", exposition=exposition("E1", source)))
    engine.analysis = Analysis(engine)
    return store, engine, work


@pytest.fixture
def analytical_native(native, monkeypatch):
    import hermes_cli.plugins as plugins
    monkeypatch.setattr(plugins, "_delivery_manager", lambda: native.manager)
    handle = native.ctx.register_middleware("llm_execution", native.host.execution)
    try:
        yield native
    finally:
        handle.dispose()


def product_call(native, identifier="P1", application_id="A1"):
    engine, work = native.engine, native.work
    packet = engine.analysis.application_packet("parent", work, application_id)
    source = packet["source_anchors"][0]
    text = engine.analysis.source_text("parent", source)
    app = packet["application"]
    level = app["level_definitions"][0]["id"]
    node = {"id": "N1", "kind": "measurement_statement", "level": level,
            "statement": "Источник содержит два значения; механизм изменения неизвестен",
            "context": "Синтетический пример 20 и 30", "unit_of_analysis": "Одна запись",
            "epistemic_status": "observation", "citations": [{"kind": "text", "source_id": source["id"],
                "start": source["start"], "end": source["end"], "quote": text[source["start"]:source["end"]]}],
            "limitations": ["Нет первичных событий"]}
    checks = [{"obligation_id": c["id"], "state": "addressed" if c["level"] == level else "unresolved",
               "node_ids": ["N1"] if c["level"] == level else [], "reason": "Проверка синтетического продукта"} for c in app["acceptance_checks"]]
    product = {"id": identifier, "application_id": application_id, "input_fingerprint": packet["input_fingerprint"],
               "nodes": [node], "edges": [], "checks": checks,
               "uncovered": ["Причины и независимая проверка не установлены"], "assumptions": ["Учебные данные"],
               "limitations": ["Не является реальным исследованием"]}
    if packet['source_packet'].get('profile_contract'):
        product['profile_checks']=[{'obligation_id':c['id'],'state':'unresolved','node_ids':[],
            'reason':'Synthetic fixture has no independent qualification'} for c in packet['source_packet']['profile_contract']['obligations']]
    revision = engine.status("parent", work)["state"]["revision"]
    return request("submit_product", work, revision, "submit-" + identifier, product=product)


def response_for(args, call="product-call", shape="chat"):
    function = {"name": "intake_analysis", "arguments": json.dumps(args, ensure_ascii=False)}
    if shape == "responses":
        return {"status": "completed", "output": [{"type": "function_call", "call_id": call, **function}],
                "usage": {"input_tokens": 0, "output_tokens": 31},
                "reasoning": "DO_NOT_CAPTURE_PRIVATE_REASONING"}
    return {"choices": [{"finish_reason": "tool_calls", "message": {"content": None,
                "tool_calls": [{"id": call, "type": "function", "function": function}],
                "reasoning_content": "DO_NOT_CAPTURE_PRIVATE_REASONING"}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 31}}


def generate(native, args, api="analysis-api", callback=None, shape="chat"):
    text = "Выполни выбранное применение метода"
    native.host.before_turn(session_id="parent", user_message=text, turn_id="analysis-turn", platform="cli")
    incoming = {"model": "synthetic-callback-no-provider", "instructions": "Stable instructions", "input": [{"role": "user", "content": text}]}
    def terminal(outgoing):
        assert outgoing["instructions"] == "Stable instructions"
        value = json.dumps(outgoing, ensure_ascii=False)
        assert "method_application" in value and "conceptualization" in value and "IN-" in value
        assert "FW-150" not in value  # No full-catalogue injection.
        return callback(outgoing) if callback else response_for(args, shape=shape)
    return run_llm_execution_middleware(incoming, terminal, session_id="parent", turn_id="analysis-turn",
                api_request_id=api, retry_count=0, platform="cli")


def submit(native, args, api="analysis-api", call="product-call"):
    identity = {"session_id": "parent", "turn_id": "analysis-turn", "api_request_id": api, "tool_call_id": call, "platform": "cli"}
    return json.loads(run_tool_execution_middleware("intake_analysis", args,
        lambda value: native.ctx.dispatch_tool("intake_analysis", value, **identity), **identity))


def test_v3_migration_keeps_existing_document_links_and_work(analysis_env):
    store, engine, work = analysis_env
    old = engine.status("parent", work)["content_hash"]
    with store.db() as db:
        db.execute("DROP TABLE intake_model_outcomes")
        db.execute("DROP TABLE intake_path_attempts")
        db.execute("DROP TABLE intake_delegations")
        db.execute("UPDATE meta SET value='3' WHERE key='semantic_intake_contract'")
        db.execute("INSERT INTO intake_document_links VALUES(?,?,?,?,?,?)", (store.profile, work, "parent", "fixture-token", "{}", 1.0))
    assert install(store) == {"schema_version": 6, "changed": True}
    assert engine.status("parent", work)["content_hash"] == old
    with store.db() as db:
        assert db.execute("SELECT count(*) FROM intake_document_links").fetchone()[0] == 1


def test_invalid_method_definition_is_rejected_before_write_reservation(analytical_native):
    n = analytical_native
    from agent.tool_attempt import tool_attempt_scope
    from hermes_cli.execution_control import ExecutionControlDenied
    from test_native_context import turn
    app = application(); app["families"] = ["missing-local-family-definition"]
    args = request("plan_method", n.work, 3, "bad-local-definition", application=app)
    turn(n)
    identity = {"session_id": "parent", "turn_id": "native-turn", "tool_call_id": "bad-method", "api_request_id": "bad-method-api"}
    with tool_attempt_scope("intake_analysis", identity, final_boundary="registry"):
        with pytest.raises(ExecutionControlDenied, match="requires_definition_not_permission:families"):
            n.ctx.dispatch_tool("intake_analysis", args, **identity)
    with n.host.store.db() as db:
        assert db.execute("SELECT count(*) FROM action_attempts WHERE call_id='bad-method'").fetchone()[0] == 0


@pytest.mark.parametrize("trace", ["concept_measurement", "method_foundation"])
def test_required_traces_preserve_distinct_levels_and_check_obligations(analysis_env, trace):
    store, engine, work = analysis_env
    app = application(trace=trace)
    result = plan_method(engine, work, app)
    packet = engine.analysis.application_packet("parent", work, app["id"])
    assert {x["id"] for x in packet["application"]["level_definitions"]} == TRACES[trace]
    assert packet["profile"] == "Standard" and result["local_effect_performed"]
    app = application(identifier="bad", trace=trace)
    app["level_definitions"].pop()
    with pytest.raises(ValueError, match="distinct_levels"):
        plan_method(engine, work, app)


def test_method_application_does_not_require_closed_family_axis_or_method_ids(analysis_env):
    store, engine, work = analysis_env
    local = {"id": "LOCAL-other", "name": "Контекстное различение", "kind": "Новый метаподход",
             "purpose": "Уточнить смысл", "operation": "Сопоставить употребления", "required_inputs": ["Запись"],
             "output": "Различённые смыслы", "assumptions": [], "limitations": ["Локальная авторская адаптация"],
             "source_refs": [], "origin": "authorial"}
    engine.call("parent", request("revise", work, 2, "local-method", change={"type": "add_method", "descriptor": local}))
    app = application(method_id="LOCAL-other")
    app["families"] = ["LOCAL-family"]
    app["classification_extensions"] += [
        {"dimension": "kinds", "id": "Новый метаподход", "definition": "Локальная авторская аналитическая позиция",
         "related_to": ["approach"], "relation_reason": "Ограниченная адаптация"},
        {"dimension": "families", "id": "LOCAL-family", "definition": "Контекстные различения измерительных высказываний",
         "related_to": ["MF03", "MF04"], "relation_reason": "Пересечение понятийной и измерительной задач"}]
    plan_method(engine, work, app)
    packet = engine.analysis.application_packet("parent", work, app["id"])
    assert "LOCAL-other" in packet["source_packet"]["methods"]
    assert "LOCAL-contextual-meaning" in packet["application"]["axes"]
    assert packet["application"]["families"] == ["LOCAL-family"]


def test_new_classification_requires_an_explicit_definition_but_no_new_permission(analysis_env):
    _, engine, work = analysis_env
    app = application(); app["classification_extensions"] = []
    with pytest.raises(ValueError, match="requires_definition_not_permission"):
        plan_method(engine, work, app)
    assert plan_method(engine, work, application())["ok"]


def test_method_classification_is_addressed_and_open(analysis_env):
    _, engine, _ = analysis_env
    value = engine.catalogue.call({"action": "classification", "dimension": "families", "ids": ["MF04"], "offset": 0, "limit": 5})
    assert len(value["items"]) == 1 and value["items"][0]["id"] == "MF04" and value["local_extensions_allowed"]


@pytest.mark.parametrize("profile", PROFILES)
def test_method_application_inherits_the_work_profile_without_local_downgrade(fixture, contract, profile):
    store, engine, work = fixture
    prepare(engine, work, {**contract, "profile": profile})
    source = record(store)
    engine.call("parent", request("expose", work, 1, "exp", exposition=exposition("E1", source)))
    plan_method(engine, work)
    assert engine.analysis.application_packet("parent", work, "A1")["profile"] == profile
    bad = application(identifier="downgrade"); bad["profile"] = "eMVP"
    with pytest.raises(ValueError, match="contract_invalid"):
        plan_method(engine, work, bad)


def test_method_state_is_readable_from_a_new_cli_process(analysis_env):
    store, engine, work = analysis_env; plan_method(engine, work)
    config = Path(store.config["state_root"]).parent / "analysis-config.json"
    config.write_text(json.dumps(store.config))
    args = request("status", work, application_id="A1")
    out = subprocess.run([sys.executable, "-B", "-m", "bnn_intake.cli", "--config", str(config),
                          "analysis", "--sid", "parent", "--request", "-"], input=json.dumps(args),
                         text=True, capture_output=True, check=True)
    value = json.loads(out.stdout)
    assert value["application"]["status"] == "planned_not_executed" and not value["products"]


def test_renaming_an_application_does_not_evade_exact_repeat_detection(analysis_env):
    _, engine, work = analysis_env
    plan_method(engine, work)
    with pytest.raises(ValueError, match="exact_method_application_repeat"):
        plan_method(engine, work, application(identifier="renamed"))
    app = application(identifier="new-purpose")
    app["purpose"] = "Найти другое существенное различение"
    assert plan_method(engine, work, app)["ok"]


@pytest.mark.parametrize("shape", ["chat", "responses"])
def test_real_middleware_product_provenance_and_addressed_read(analytical_native, shape):
    n = analytical_native; plan_method(n.engine, n.work, select=True)
    args = product_call(n)
    generate(n, args, shape=shape)
    assert submit(n, args)["ok"]
    stored = n.engine.analysis.call("parent", request("read_product", n.work, product_id="P1"))["product"]
    assert stored["nodes"][0]["epistemic_status"] == "observation"
    assert stored["semantic_review"] == "unreviewed" and not stored["scientific_acceptance"]
    assert stored["producer"]["api_request_id"] == "analysis-api"
    status = n.engine.status("parent", n.work)
    assert status["state_is_projection"] and "statement" not in json.dumps(status["state"]["analysis_products"])
    with n.host.store.db() as db:
        row = db.execute("SELECT body FROM intake_model_outcomes").fetchone()[0]
        assert "DO_NOT_CAPTURE_PRIVATE_REASONING" not in row
        outcome = json.loads(row)
        assert outcome["status"] == "returned" and outcome["usage"]["input_tokens"] == 0
        attempts = [dict(r) for r in db.execute("SELECT tool_name,status FROM action_attempts")]
    assert attempts == [{"tool_name": "intake_analysis", "status": "returned_unreviewed"}]
    target = os.environ.get("BNN_INTAKE_TEST_EVIDENCE")
    if target:
        (Path(target) / ("native-analysis-" + shape + ".json")).write_text(json.dumps({"outcome": outcome,
            "product": stored, "known_tool_attempts": attempts, "actual_PluginManager_and_middleware": True,
            "callback_is_synthetic": True, "external_provider_calls": 0, "semantic_acceptance": "not_tested"}, ensure_ascii=False, indent=2) + "\n")


def test_product_requires_returned_origin_not_only_context_preparation(analytical_native):
    n = analytical_native; plan_method(n.engine, n.work, select=True)
    args = product_call(n)
    turn(n, turn_id="analysis-turn")
    with pytest.raises(ExecutionControlDenied, match="returned_producer"):
        submit(n, args)
    with n.host.store.db() as db:
        assert db.execute("SELECT count(*) FROM action_attempts").fetchone()[0] == 0


@pytest.mark.parametrize("change", ["quote", "source", "endpoint", "level", "obligation"])
def test_invalid_returned_product_is_rejected_before_write_reservation(analytical_native, change):
    n = analytical_native; plan_method(n.engine, n.work, select=True)
    args = product_call(n)
    product = args["product"]
    if change == "quote": product["nodes"][0]["citations"][0]["quote"] = "Invented quote"
    if change == "source": product["nodes"][0]["citations"][0]["source_id"] = "IN-not-assigned"
    if change == "endpoint": product["edges"] = [{"from": "N1", "to": "missing", "relation": "precedes", "meaning": "Порядок", "epistemic_status": "hypothesis"}]
    if change == "level": product["nodes"][0]["level"] = "undefined"
    if change == "obligation": product["checks"][0]["obligation_id"] = "unknown"
    generate(n, args)
    with pytest.raises(ExecutionControlDenied):
        submit(n, args)
    with n.host.store.db() as db:
        assert db.execute("SELECT count(*) FROM action_attempts").fetchone()[0] == 0


def test_different_tool_arguments_cannot_borrow_a_valid_producer(analytical_native):
    n = analytical_native; plan_method(n.engine, n.work, select=True)
    args = product_call(n); generate(n, args)
    modified = copy.deepcopy(args); modified["product"]["nodes"][0]["statement"] = "Changed after return"
    with pytest.raises(ExecutionControlDenied, match="returned_producer"):
        submit(n, modified)


def test_missing_execution_wrapper_fails_at_final_controller(analytical_native):
    n = analytical_native; plan_method(n.engine, n.work, select=True)
    prepared = turn(n)
    with pytest.raises(ExecutionControlDenied, match="execution_wrapper"):
        model_admit(prepared)


def test_partial_response_cannot_produce_a_completed_method_product(analytical_native):
    n = analytical_native; plan_method(n.engine, n.work, select=True)
    args = product_call(n)
    value = response_for(args); value["choices"][0]["finish_reason"] = "length"
    generate(n, args, callback=lambda outgoing: value)
    with pytest.raises(ExecutionControlDenied, match="returned_producer"):
        submit(n, args)


def test_unknown_outcome_is_preserved_and_cannot_trigger_another_callback(analytical_native):
    n = analytical_native; plan_method(n.engine, n.work, select=True)
    args = product_call(n)
    def fail(outgoing):
        raise ConnectionError("synthetic uncertainty after admission")
    with pytest.raises(ConnectionError):
        generate(n, args, callback=fail)
    calls = []
    with pytest.raises(ExecutionControlDenied, match="not_settled"):
        generate(n, args, api="new-api", callback=lambda outgoing: calls.append(True))
    assert not calls
    with n.host.store.db() as db:
        value = json.loads(db.execute("SELECT body FROM intake_model_outcomes").fetchone()[0])
    assert value["status"] == "outcome_unknown" and value["error_type"] == "ConnectionError"
    status = n.engine.analysis.call("parent", request("status", n.work, application_id="A1"))
    assert status["execution_outcomes"][0]["status"] == "outcome_unknown"


def test_known_provider_rejection_does_not_become_unknown_spend_or_block_repair(analytical_native):
    n = analytical_native; plan_method(n.engine, n.work, select=True)
    args = product_call(n)
    class Rejected(Exception):
        status_code = 429
    def rejected(outgoing):
        raise Rejected("synthetic explicit HTTP rejection")
    with pytest.raises(Rejected):
        generate(n, args, callback=rejected)
    generate(n, args, api="repaired-api")
    assert submit(n, args, api="repaired-api")["ok"]
    with n.host.store.db() as db:
        first = json.loads(db.execute("SELECT body FROM intake_model_outcomes ORDER BY rowid").fetchone()[0])
    assert first["status"] == "rejected" and first["provider_status"] == 429
    assert first["usage"]["input_tokens"] is None and first["usage"]["output_tokens"] is None


def test_new_session_cannot_reset_an_unknown_outcome_of_the_same_work(analytical_native):
    n = analytical_native; plan_method(n.engine, n.work, select=True)
    args = product_call(n)
    def failed(outgoing):
        raise ConnectionError("synthetic unknown")
    with pytest.raises(ConnectionError):
        generate(n, args, callback=failed)
    sid = "continued-session"
    n.host.store.bind(sid)
    with n.host.store.db() as db:
        db.execute("INSERT INTO working_bindings VALUES(?,?,?)", (n.host.store.profile, sid, n.work))
    n.engine.set_mode(sid, "on", 0, "owner")
    n.host.before_turn(session_id=sid, user_message="Продолжить", turn_id="continued-turn", platform="cli")
    reached = []
    with pytest.raises(ExecutionControlDenied, match="not_settled"):
        run_llm_execution_middleware({"input": [{"role": "user", "content": "Продолжить"}]},
            lambda outgoing: reached.append(True), session_id=sid, turn_id="continued-turn",
            api_request_id="continued-api", platform="cli")
    assert not reached


def test_off_keeps_plain_work_available_after_optional_outcome_table_failure(analytical_native):
    n = analytical_native; plan_method(n.engine, n.work, select=True)
    n.engine.set_mode("parent", "off", 1, "owner")
    with n.host.store.db() as db:
        db.execute("DROP TABLE intake_model_outcomes")
    prepared = turn(n)
    assert model_admit(prepared)[0].allow
    assert "method_application" not in json.dumps(prepared)


def test_source_retraction_blocks_product_read_without_exposing_old_quotes(analytical_native):
    n = analytical_native; plan_method(n.engine, n.work, select=True)
    args = product_call(n); generate(n, args); submit(n, args)
    ref = n.engine.analysis.state("parent", n.work)["expositions"]["E1"]["input_refs"][0]
    n.host.store.retract("parent", ref["id"], ref["revision"])
    with pytest.raises(PermissionError, match="retracted"):
        n.engine.analysis.call("parent", request("read_product", n.work, product_id="P1"))
    assert "quote" not in json.dumps(n.engine.status("parent", n.work)["state"]["analysis_products"])


def test_source_revocation_between_validation_and_commit_closes_the_effect(analytical_native, monkeypatch):
    n = analytical_native; plan_method(n.engine, n.work, select=True)
    args = product_call(n); generate(n, args)
    original = n.engine.analysis.check_product
    count = []
    def revoke_after_second_validation(sid, packet, product):
        original(sid, packet, product)
        count.append(True)
        if len(count) == 2:  # Native preflight first, handler validation second.
            ref = packet["source_packet"]["records"][0]["ref"]
            n.host.store.retract("parent", ref["id"], ref["revision"])
    monkeypatch.setattr(n.engine.analysis, "check_product", revoke_after_second_validation)
    # Registry may encode the handler's error; either way the protected write must not occur.
    try:
        out = submit(n, args)
        assert not out.get("ok"), out
    except PermissionError:
        pass
    assert "P1" not in n.engine.analysis.state("parent", n.work).get("analysis_products", {})


def test_subject_cycle_does_not_create_recursive_execution(analytical_native):
    n = analytical_native; plan_method(n.engine, n.work, select=True)
    args = product_call(n)
    second = copy.deepcopy(args["product"]["nodes"][0])
    second.update(id="N2", epistemic_status="hypothesis", statement="Возможна обратная связь", citations=[])
    args["product"]["nodes"].append(second)
    args["product"]["edges"] = [{"from": left, "to": right, "relation": "possible_influence",
                                  "meaning": "Предметная обратная связь", "epistemic_status": "hypothesis"}
                                 for left, right in [("N1", "N2"), ("N2", "N1")]]
    generate(n, args); assert submit(n, args)["ok"]
    with n.host.store.db() as db:
        assert db.execute("SELECT count(*) FROM intake_model_outcomes").fetchone()[0] == 1


def test_next_method_receives_prior_product_and_original_evidence_once(analytical_native):
    n = analytical_native; plan_method(n.engine, n.work, select=True)
    first = product_call(n); generate(n, first); submit(n, first)
    prior = n.engine.analysis.state("parent", n.work)["analysis_products"]["P1"]
    revision = n.engine.status("parent", n.work)["state"]["revision"]
    exp = {"id": "E2", "purpose": "Сопоставить предшествующее различение", "question": "Какие альтернативы ещё открыты?",
           "input_refs": [], "depends_on": [], "method_ids": []}
    n.engine.call("parent", request("expose", n.work, revision, "new-exp", exposition=exp))
    app = application(exposition_id="E2", identifier="A2")
    app["purpose"] = "Углубить предыдущий продукт"
    app["product_dependencies"] = [{"product_id": "P1", "sha256": digest(prior)}]
    plan_method(n.engine, n.work, app, select=True)
    packet = n.engine.analysis.application_packet("parent", n.work, "A2")
    assert len(packet["source_packet"]["records"]) == 1 and len(packet["source_anchors"]) == 1
    assert packet["source_packet"]["derived_products"]["P1"] == prior
    assert "independent" in packet["source_packet"]["independence"]
    second = product_call(n, identifier="P2", application_id="A2")
    second["product"]["nodes"][0].update(epistemic_status="inference", citations=[
        {"kind": "product", "product_id": "P1", "sha256": digest(prior), "node_id": "N1"}])
    generate(n, second, api="analysis-api-2", callback=lambda outgoing: response_for(second, call="product-call-2"))
    assert submit(n, second, api="analysis-api-2", call="product-call-2")["ok"]
    assert len(n.engine.analysis.state("parent", n.work)["analysis_products"]) == 2
    ref = packet["source_packet"]["records"][0]["ref"]
    n.host.store.retract("parent", ref["id"], ref["revision"])
    with pytest.raises(PermissionError, match="retracted"):
        n.engine.analysis.call("parent", request("read_product", n.work, product_id="P2"))


def test_completed_application_does_not_silently_rerun_the_same_purpose(analytical_native):
    n = analytical_native; plan_method(n.engine, n.work, select=True)
    args = product_call(n); generate(n, args); submit(n, args)
    revision = n.engine.status("parent", n.work)["state"]["revision"]
    with pytest.raises(PermissionError, match="has_product"):
        n.engine.analysis.call("parent", request("select_application", n.work, revision, "repeat-completed", application_id="A1"))


@pytest.mark.parametrize("state", ["off", "pause", "cancel"])
def test_saved_product_remains_readable_after_stopping_new_work(analytical_native, state):
    n = analytical_native; plan_method(n.engine, n.work, select=True)
    args = product_call(n); generate(n, args); submit(n, args)
    if state == "off":
        n.engine.set_mode("parent", "off", 1, "owner")
    else:
        revision = n.engine.status("parent", n.work)["state"]["revision"]
        n.engine.call("parent", request(state, n.work, revision, "owner-stop", reason="Stop new work"))
    value = n.engine.analysis.call("parent", request("read_product", n.work, product_id="P1"))
    assert value["product"]["id"] == "P1" and not value["scientific_acceptance"]


def test_complete_stored_product_bound_is_checked_before_any_write(analytical_native):
    n = analytical_native; plan_method(n.engine, n.work, select=True)
    args = product_call(n); generate(n, args)
    # Raw product would fit; its required provenance makes it exceed the bound.
    from bnn_intake.storage import canonical
    n.host.store.config["intake_product_bytes"] = len(canonical(args["product"]).encode())
    with pytest.raises(ExecutionControlDenied, match="split_product_required"):
        submit(n, args)
    assert not n.engine.analysis.state("parent", n.work).get("analysis_products")


def test_pdf_evidence_and_image_citation_pass_through_native_execution(analytical_native):
    import shutil
    from test_documents import acquire, bind, plan, expose, PARSER_PYTHON
    from conftest import ROOT
    n = analytical_native
    n.host.config["document_python"] = PARSER_PYTHON
    path = Path(n.host.config["projects"]["p"]) / "analysis.pdf"
    shutil.copyfile(ROOT / "tests/fixtures/documents/control.pdf", path)
    docs = n.host.document_store()
    cid, _, _ = acquire(docs, n.host.store, path)
    bind(n.engine.documents, n.engine, n.work, cid)
    refs = [v["reference"] for v in plan(n.engine.documents, n.work, cid)["fragments"]]
    expose(n.engine, n.work, refs, name="PDF-E")
    plan_method(n.engine, n.work, application(exposition_id="PDF-E"), select=True)
    packet = n.engine.analysis.application_packet("parent", n.work, "A1")
    args = product_call(n)
    anchor = packet["source_anchors"][-1]
    args["product"]["nodes"][0]["citations"].append({"kind": "image", "source_id": anchor["id"],
        "image_sha256": anchor["image_sha256"], "locator": "Страница 2: учебный рисунок"})
    generate(n, args); assert submit(n, args)["ok"]
    saved = n.engine.analysis.call("parent", request("read_product", n.work, product_id="P1"))["product"]
    assert saved["nodes"][0]["citations"][1]["kind"] == "image"
    with n.host.store.db() as db:
        # Synthetic returning callback exercises the ledger's success path;
        # these two receipts are NOT evidence of a real model reading the PDF.
        assert db.execute("SELECT count(*) FROM document_deliveries").fetchone()[0] == 2
    target = os.environ.get("BNN_INTAKE_TEST_EVIDENCE")
    if target:
        (Path(target) / "native-analysis-pdf-fixture.json").write_text(json.dumps({"product": saved,
            "native_document_receipts": 2, "completion_callback_is_synthetic": True,
            "external_provider_calls": 0, "real_model_reading_confirmed": False}, ensure_ascii=False, indent=2) + "\n")
