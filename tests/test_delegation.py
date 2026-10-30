"""Real common launch control and source policies; fixture service has NO executor.

The separate operator probe qualifies actual Hermes children/model execution.
"""
import copy
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
import uuid
import pytest
from hermes_cli.execution_control import authorize, ExecutionControlDenied
from bn_memory.launch_admission import launch
from bn_memory.modules import Modules
from bn_memory.model_accounting import Accounting
from bnn_intake.contracts import PROFILES
from bnn_intake.delegation import Delegation
from bnn_intake.storage import digest, install
from conftest import request
from test_native_context import native, turn
from test_exposition import exposition


@pytest.fixture
def delegated(native):
    n = native
    for cfg in [n.host.config, n.host.store.config, n.host.broker.config]:
        cfg.update(w3_enabled=True, w4_enabled=True)
    n.host.modules = Modules(n.host); n.host.accounting = Accounting(n.host)
    n.ref = n.host.store.record("parent", {"kind": "note", "scope": "project", "request_id": "assigned-original",
        "source_ref": "synthetic:assigned-only", "content": "Assigned synthetic original: before=17; after=23; units unknown.",
        "visibility": {"allowed_roles": ["parent", "collector", "analyst", "reviewer", "parser", "computation", "G", "S", "D"], "evidence_use": "general"}})
    n.engine.call("parent", request("expose", n.work, 3, "child-exposition", exposition=exposition("E-child", n.ref)))
    n.spec = {"id": "H-analyst", "module": "base", "role": "analyst", "native_task_id": None,
              "purpose": "Прочитать назначенную учебную запись, сохранив неизвестные единицы", "exposition_id": "E-child", "application_id": None, "gap_ids": []}
    n.engine.delegation.call("parent", request("plan_handoff", n.work, 4, "handoff-plan", handoff=n.spec))
    return n


class NoExecutor:
    """Mechanical launch service fixture; never claims a returned native product."""
    def __init__(self, n):
        self.n = n; self.children = []
    def launch(self, request):
        child = "child-" + uuid.uuid4().hex
        self.n.host.child_start(parent_session_id="parent", child_session_id=child, child_goal=request.goal)
        data = {"parent_session_id": "parent", "role": "leaf", "depth": 1, "correlation_id": request.correlation_id,
                "subagent_id": "fixture-" + child, "model": request.model, "provider": request.provider}
        authorize("subagent_launch", {"handle": data, "goal": request.goal}, session_id="parent", turn_id="native-turn", child_session_id=child, platform="cli")
        self.children.append(child)
        return SimpleNamespace(subagent_id=data["subagent_id"], to_dict=lambda: data)
    def cancel(self, *a, **kw):
        return SimpleNamespace(accepted=True)


def start(n, resources=None, service=None, role="analyst", module="base"):
    turn(n)
    service = service or NoExecutor(n)
    handle = launch(n.host, service, parent_sid="parent", turn_id="native-turn", goal="One assigned leaf; no executor in fixture",
             analytical_role=role, purpose="Bounded synthetic reading", module=module,
             resources={"records": [n.ref["id"]]} if resources is None else resources,
             tool_permissions=[], model="synthetic-no-provider", provider="fixture")
    return service.children[-1], handle


@pytest.mark.parametrize("contract", PROFILES, indirect=True)
def test_native_grant_receives_complete_addressed_intake_with_unchanged_profile(delegated, contract):
    n = delegated; child, handle = start(n)
    packet = n.engine.delegation.child_packet(child)
    assert packet["profile"] == contract["profile"] and packet["analytical_role"] == "analyst"
    assert not packet["scientific_acceptance"]
    assert packet["source_packet"]["records"][0]["content"].startswith("Assigned synthetic original")
    assert len(packet["source_packet"]["records"]) == 1
    assert packet["restrictions"] == {"direct_owner_interview": False, "delegate_again": False, "change_parent_state": False, "lower_profile": False, "new_budget_created": False}
    assert packet["assignment_id"] == n.host.store.binding(child)["assignment_id"]
    with n.host.store.db() as db:
        grant = dict(db.execute("SELECT * FROM launch_grants WHERE child_sid=?", (child,)).fetchone())
    assert grant["state"] == "admitted" and grant["role"] == "analyst"
    out = os.environ.get("BNN_INTAKE_TEST_EVIDENCE")
    if out:
        (Path(out) / "native-intake-child-binding.json").write_text(json.dumps({"packet": packet, "native_grant": grant,
            "executor_present": False, "provider_called": False, "scope": "actual launch control with fixture service only"}, ensure_ascii=False, indent=2) + "\n")


def test_unassigned_original_is_rejected_after_native_assignment_before_executor(delegated):
    n = delegated; service = NoExecutor(n)
    with pytest.raises(ExecutionControlDenied, match="resource_not_assigned"):
        start(n, resources={}, service=service)
    assert not service.children
    with n.host.store.db() as db:
        grant = db.execute("SELECT state,child_sid FROM launch_grants ORDER BY rowid DESC LIMIT 1").fetchone()
        assert grant["state"] == "failed"
        assert db.execute("SELECT count(*) FROM intake_delegations").fetchone()[0] == 0
    assert n.host.store.binding(grant["child_sid"])["role"] == "unassigned"


def test_no_planned_role_cannot_silently_get_parent_intake(delegated):
    with pytest.raises(ExecutionControlDenied, match="plan_role_handoff"):
        start(delegated, role="collector")


def test_GSD_role_cannot_be_relabelled_as_an_ordinary_analyst(delegated):
    n = delegated; spec = {**n.spec, "id": "fake-S", "role": "S", "module": "base"}
    with pytest.raises(PermissionError, match="requires_canonical_module"):
        n.engine.delegation.call("parent", request("plan_handoff", n.work, 5, "fake-canonical", handoff=spec))


def test_child_cannot_change_work_profile_or_prepare_a_second_handoff(delegated):
    n = delegated; child, _ = start(n)
    args = request("plan_handoff", n.work, 5, "child-escalation", handoff={**n.spec, "id": "H-child"})
    with pytest.raises(PermissionError):
        n.engine.delegation.call(child, args)
    bad = {**request("plan_handoff", n.work, 5, "lower", handoff={**n.spec, "id": "H-lower"}), "profile": "eMVP"}
    with pytest.raises(ValueError, match="contract_invalid"):
        n.engine.delegation.call("parent", bad)


def test_updated_owner_interview_restriction_is_in_the_child_signed_frame(delegated):
    from bnn_intake.interview import Interview
    n = delegated; child, _ = start(n)
    Interview(n.engine).control("parent", {"action": "stop", "work_ref": n.work, "expected_revision": 5, "operation_id": "owner-stop"}, "owner")
    frame = n.attached["context_bridge"].frame(child)
    assert '"state":"stopped"' in frame["text"]
    assert '"direct_owner_interview":false' in frame["text"]
    assert "before=20, after=30" not in frame["text"]  # Unassigned parent input isn't copied.


def test_unrelated_parent_revision_does_not_invalidate_an_assigned_source(delegated):
    n = delegated; child, _ = start(n)
    before = n.engine.delegation.child_packet(child)
    n.engine.call("parent", request("revise", n.work, 5, "new-unrelated-method", change={"type": "add_method", "descriptor": {
        "id": "M-other", "name": "Other scope", "kind": "procedure", "purpose": "Different question", "operation": "Read other input",
        "required_inputs": ["other input"], "output": "separate result", "assumptions": [], "limitations": [], "source_refs": [], "origin": "authorial"}}))
    assert n.engine.delegation.child_packet(child) == before


def test_parent_work_pause_blocks_child_semantic_input(delegated):
    n = delegated; child, _ = start(n)
    n.engine.call("parent", request("pause", n.work, 5, "pause-parent", reason="Requested pause"))
    with pytest.raises(PermissionError, match="handoff_inactive"):
        n.engine.delegation.child_packet(child)


def test_off_survives_missing_delegation_journal_and_keeps_native_launch_control(delegated):
    n = delegated; n.engine.set_mode("parent", "off", 1, "owner")
    with n.host.store.db() as db:
        db.execute("DROP TABLE intake_delegations")
    child, _ = start(n)
    assert n.host.store.binding(child)["role"] == "analyst"
    assert not n.attached["context_bridge"].frame(child)["receipt_required"]


def test_mandatory_native_launch_controller_is_not_replaced_by_the_handoff(delegated):
    n = delegated; n.controls[4].dispose()
    with pytest.raises(ExecutionControlDenied, match="unavailable"):
        start(n)


def test_handoff_overflow_requires_split_before_any_child_launch(delegated):
    n = delegated; n.host.store.config["intake_context_bytes"] = 20
    with pytest.raises(PermissionError, match="split_required"):
        n.engine.delegation.call("parent", request("plan_handoff", n.work, 5, "too-large", handoff={**n.spec, "id": "H-too-large"}))
    with n.host.store.db() as db:
        assert db.execute("SELECT count(*) FROM launch_grants").fetchone()[0] == 0


def test_new_adapter_reads_the_same_persistent_child_binding(delegated):
    n = delegated; child, _ = start(n)
    assert Delegation(n.engine, n.host).child_packet(child) == n.engine.delegation.child_packet(child)


@pytest.fixture(scope="session")
def real_beads(tmp_path_factory):
    import sys
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "operator"))
    from isolated_beads import create
    return create(tmp_path_factory.getbasetemp() / "actual-beads")


def test_actual_Beads_work_binding_dependency_and_idempotency(native, real_beads, contract):
    from bn_memory.task_authority import Tasks
    from bn_memory.working_beads import lookup
    n = native
    for cfg in [n.host.config, n.host.store.config, n.host.broker.config]:
        cfg.update(w3_enabled=True, working_beads=real_beads["working_beads"], task_pin_probe=real_beads["task_pin_probe"])
    n.host.modules = Modules(n.host); n.host.accounting = Accounting(n.host)
    bound = n.host.working.bind("parent", {"beads_id": real_beads["primary_issue"]["id"]})
    work = bound["work_id"]
    prepared = n.engine.call("parent", request("prepare", work, 0, "actual-prepare", contract=contract))
    tasks = Tasks(n.host, module="base")
    plan = {"request_id": "actual-native-beads-plan", "parent_bead": real_beads["primary_issue"]["id"], "tasks": [
        {"key": "collect", "title": "Synthetic intake original", "goal": "Read assigned original", "depends_on": []},
        {"key": "analyse", "title": "Synthetic intake comparison", "goal": "Distinguish unknowns", "depends_on": ["collect"]}]}
    value = tasks.plan("parent", plan)
    assert tasks.plan("parent", plan) == value
    assert value["primary"] == "Beads" and not value["competing_graph_created"]
    blocked = tasks.run(["blocked"])
    assert any(row["id"] == value["tasks"]["analyse"] for row in blocked)
    first = value["tasks"]["collect"]
    tasks.claim("parent", first, "intake-test-holder", "actual-claim")
    tasks.close_owned("parent", first, "intake-test-holder", "actual-close", "Synthetic primary task check; no research performed")
    ready = tasks.run(["ready"])
    assert any(row["id"] == value["tasks"]["analyse"] for row in ready)
    assert lookup(n.host.config, first)["status"] == "closed"
    assert n.engine.status("parent", work)["state"]["revision"] == 1
    out = os.environ.get("BNN_INTAKE_TEST_EVIDENCE")
    if out:
        (Path(out) / "actual-beads-primary-graph.json").write_text(json.dumps({"beads_version": real_beads["version"],
            "work_binding": bound, "intake_preparation": prepared, "plan": value, "blocked_before": blocked, "ready_after": ready,
            "provider_called": False, "actual_Beads_executed": True, "fixture_primary_binding": False}, ensure_ascii=False, indent=2) + "\n")


def test_missing_native_product_cannot_be_read_as_a_completed_result(delegated):
    n = delegated; child, _ = start(n)
    with pytest.raises(PermissionError, match="native_task_registry_unavailable"):
        n.engine.delegation.call("parent", request("read_result", n.work, native_task_id="missing-executor", native_revision=1))


def test_assigned_child_read_uses_existing_native_RPC_and_primary_store(delegated, monkeypatch):
    from bn_memory.action_control import ActionControl
    from bnn_intake.native_memory import register
    from hermes_cli.middleware import run_tool_execution_middleware
    n = delegated
    import hermes_cli.plugins as plugins
    monkeypatch.setattr(plugins, "_delivery_manager", lambda: n.manager)
    schema = {"type": "object", "properties": {"id": {"type": "string"}, "revision": {"type": "integer", "minimum": 1}}, "required": ["id"], "additionalProperties": False}
    n.host.config["tool_policy"]["rules"].append({"id": "test.native.record.child", "tool": "mcp__context__memory_read", "effect": "read_local", "roles": ["analyst"], "arguments_schema": schema})
    n.host.actions = ActionControl(n.host)
    n.attached["registrations"].extend(register(n.host, n.ctx))
    n.attached["registrations"].append(n.ctx.register_middleware("tool_execution", n.host.tool_execution))
    turn(n)
    service = NoExecutor(n)
    launch(n.host, service, parent_sid="parent", turn_id="native-turn", goal="Assigned read with no model executor",
        analytical_role="analyst", purpose="Native RPC boundary", resources={"records": [n.ref["id"]]},
        tool_permissions=[{"rule_id": "test.native.record.child", "arguments_schema": {"type": "object"}}],
        model="synthetic-no-provider", provider="fixture")
    child = service.children[0]
    n.host.before_turn(session_id=child, turn_id="child-read", user_message="Read only the assigned source", platform="subagent")
    identity = {"session_id": child, "turn_id": "child-read", "tool_call_id": "child-native-read", "api_request_id": "child-read-api", "platform": "subagent"}
    value = json.loads(run_tool_execution_middleware("mcp__context__memory_read", {"id": n.ref["id"]},
        lambda current: n.ctx.dispatch_tool("mcp__context__memory_read", current, **identity), **identity))
    assert value["content"].startswith("Assigned synthetic original")
    with n.host.store.db() as db:
        rows = list(db.execute("SELECT state FROM rpc_dispatches"))
    assert len(rows) == 1 and rows[0][0] == "returned_unreviewed"


def test_schema_v5_upgrade_keeps_existing_handoffs_and_revisions(delegated):
    n = delegated; old = n.engine.status("parent", n.work)["content_hash"]
    with n.host.store.db() as db:
        db.execute("DROP TABLE intake_delegations")
        db.execute("UPDATE meta SET value='5' WHERE key='semantic_intake_contract'")
    assert install(n.host.store) == {"schema_version": 6, "changed": True}
    assert n.engine.status("parent", n.work)["content_hash"] == old


def stored_result_fixture(n, extra_record=None):
    """Trusted-ledger fixture, NOT an executor receipt or model qualification."""
    from bn_memory.teams import install as install_teams
    from bn_memory.store import canonical, sha
    resources = {"records": [n.ref["id"]] + ([extra_record["id"]] if extra_record else [])}
    child, _ = start(n, resources=resources)
    assignment = n.host.store.binding(child)["assignment_id"]
    product = {"task_id": "TASK-ledger-fixture", "role": "analyst", "status": "unreviewed", "findings": ["Synthetic fixture only"], "sources": [n.ref["id"]], "needs": [], "limitations": ["No executor in pytest fixture"]}
    body = {"role": "analyst", "module": "base", "resources": resources, "child_sid": child, "assignment_id": assignment,
            "product": product, "receipt": {"terminal_state": "SUCCEEDED", "result_hash": sha("synthetic private-ledger fixture")}}
    raw = canonical(body); install_teams(n.host)
    with n.host.store.db() as db:
        db.execute("INSERT INTO native_tasks VALUES(?,?,?,?,?,?,?,?)", (n.host.store.profile, product["task_id"], "parent", n.work, "fixture-bead", 1, "succeeded", raw))
        db.execute("INSERT INTO native_task_history VALUES(?,?,?,?,?,?)", (n.host.store.profile, product["task_id"], 1, "succeeded", raw, sha(raw)))
    return product["task_id"]


def test_new_session_reads_durable_result_with_its_own_current_source_rights(delegated):
    n = delegated; task = stored_result_fixture(n)
    first = n.engine.delegation.call("parent", request("read_result", n.work, native_task_id=task, native_revision=1))
    n.host.store.bind("resumed")
    with n.host.store.db() as db:
        db.execute("INSERT INTO working_bindings VALUES(?,?,?)", (n.host.store.profile, "resumed", n.work))
    later = n.engine.delegation.call("resumed", request("read_result", n.work, native_task_id=task, native_revision=1))
    assert later["text"] == first["text"] and later["product_sha256"] == first["product_sha256"]
    assert later["reader_sid"] == "resumed" and later["origin_parent_sid"] == "parent"
    assert later["scientific_acceptance"] is False


def test_revoked_extra_native_source_closes_result_even_outside_intake_exposition(delegated):
    n = delegated
    extra = n.host.store.record("parent", {"kind": "note", "scope": "project", "request_id": "extra-native-source",
        "source_ref": "synthetic:additional", "content": "Extra assigned source outside E-child",
        "visibility": {"allowed_roles": ["parent", "analyst"], "evidence_use": "general"}})
    task = stored_result_fixture(n, extra)
    n.host.store.retract("parent", extra["id"], 1)
    with pytest.raises(PermissionError, match="record_retracted"):
        n.engine.delegation.call("parent", request("read_result", n.work, native_task_id=task, native_revision=1))


def test_native_result_still_requires_its_exact_history_and_terminal_receipt(delegated):
    n = delegated; task = stored_result_fixture(n)
    with n.host.store.db() as db:
        db.execute("UPDATE native_task_history SET sha256='changed' WHERE id=?", (task,))
    with pytest.raises(PermissionError, match="history_integrity"):
        n.engine.delegation.call("parent", request("read_result", n.work, native_task_id=task, native_revision=1))
