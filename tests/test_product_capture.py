"""Real primary memory and native control fixtures; no model/executor in pytest."""
import json
import pytest
from conftest import request
from test_native_context import native
from test_delegation import delegated, stored_result_fixture, start
from test_exposition import exposition
from bnn_intake.storage import canonical, digest


@pytest.fixture
def captured(delegated):
    n = delegated
    task = stored_result_fixture(n)
    value = n.engine.delegation.call("parent",request("read_result",n.work,native_task_id=task,native_revision=1))
    n.capture_args = request("capture_product",n.work,5,"capture-one",origin={"module":"base","task_id":task,"native_revision":1},
        product_sha256=value["product_sha256"],recipient_roles=["analyst","reviewer","G","S","D"])
    return n


def test_capture_retains_exact_product_and_original_dependencies(captured):
    n = captured; result=n.engine.delegation.call("parent",n.capture_args); group=result["capture"]
    read=n.engine.delegation.call("parent",request("read_capture",n.work,capture_id=group["id"]))
    assert read["complete"] and not read["scientific_acceptance"] and not read["origin_module_reopened"]
    assert n.ref["id"] in group["required_resources"]["records"]
    parts=[json.loads(n.host.store.read("parent",r["id"],r["revision"])["content"]) for r in group["records"]]
    assert all(not p["independent_source"] and p["evidence_status"]=="unreviewed" for p in parts)
    assert digest(json.loads(''.join(p["text"] for p in parts)))==n.capture_args["product_sha256"]


def test_capture_does_not_accept_a_model_product_or_wrong_hash(captured):
    n=captured
    with n.host.store.db() as db:before=db.execute("SELECT count(*) FROM records").fetchone()[0]
    with pytest.raises(ValueError):n.engine.delegation.call("parent",{**n.capture_args,"text":"invented model product"})
    with pytest.raises(PermissionError,match="expected_product_hash"):n.engine.delegation.call("parent",{**n.capture_args,"product_sha256":"0"*64})
    with n.host.store.db() as db:assert db.execute("SELECT count(*) FROM records").fetchone()[0]==before


def test_capture_replay_and_new_session_reuse_identical_records(captured):
    n=captured; first=n.engine.delegation.call("parent",n.capture_args)
    again=n.engine.delegation.call("parent",n.capture_args)
    assert again["replayed"] and again["capture"]==first["capture"]
    n.host.store.bind("resumed")
    with n.host.store.db() as db:db.execute("INSERT INTO working_bindings VALUES(?,?,?)",(n.host.store.profile,"resumed",n.work))
    n.engine.set_mode("resumed","on",0,"owner")
    later=n.engine.delegation.call("resumed",{**n.capture_args,"expected_revision":6,"operation_id":"same-origin-in-new-session"})
    assert later["capture"]==first["capture"]


def test_capture_checks_extra_native_input_roles_before_writing(delegated):
    n=delegated
    extra=n.host.store.record("parent",{"kind":"note","scope":"project","request_id":"closed-extra","source_ref":"synthetic:closed-extra","content":"Extra native input not visible to G",
        "visibility":{"allowed_roles":["parent","analyst"],"evidence_use":"general"}})
    task=stored_result_fixture(n,extra)
    value=n.engine.delegation.call("parent",request("read_result",n.work,native_task_id=task,native_revision=1))
    args=request("capture_product",n.work,5,"cannot-widen",origin={"module":"base","task_id":task,"native_revision":1},product_sha256=value["product_sha256"],recipient_roles=["G"])
    with pytest.raises(PermissionError,match="visible_to_analytical_role"):n.engine.delegation.call("parent",args)
    assert not n.engine.status("parent",n.work)["state"].get("native_captures")


def test_partial_native_need_is_not_captured_as_completed(captured):
    n=captured
    with n.host.store.db() as db:
        db.execute("UPDATE native_task_history SET state='needs' WHERE id=?",(n.capture_args["origin"]["task_id"],))
    with pytest.raises(PermissionError,match="completed_product_required"):n.engine.delegation.call("parent",n.capture_args)


def test_capture_preserves_all_unicode_fragments_and_reconstructs_full_hash(captured):
    n=captured; task=n.capture_args["origin"]["task_id"]
    with n.host.store.db() as db:
        row=db.execute("SELECT body FROM native_task_history WHERE id=?",(task,)).fetchone();body=json.loads(row[0]);body["product"]["findings"]=["Ёж🙂\n"*3000]
        raw=canonical(body);db.execute("UPDATE native_task_history SET body=?,sha256=? WHERE id=?",(raw,digest(body),task))
        db.execute("UPDATE native_tasks SET body=? WHERE id=?",(raw,task))
    value=n.engine.delegation.call("parent",request("read_result",n.work,native_task_id=task,native_revision=1))
    result=n.engine.delegation.call("parent",{**n.capture_args,"product_sha256":value["product_sha256"]})
    group=result["capture"];assert len(group["records"])>10
    assert n.engine.delegation.call("parent",request("read_capture",n.work,capture_id=group["id"]))["complete"]
    assert all(len(n.host.store.read("parent",r["id"],1)["content"].encode())<=8192 for r in group["records"])


def test_capture_snapshot_requires_current_original_and_survives_module_unavailability(captured,monkeypatch):
    n=captured; group=n.engine.delegation.call("parent",n.capture_args)["capture"]
    monkeypatch.setattr(n.host.modules,"require",lambda *a:(_ for _ in ()).throw(RuntimeError("optional module unavailable")))
    assert n.engine.delegation.call("parent",request("read_capture",n.work,capture_id=group["id"]))["complete"]
    n.host.store.retract("parent",n.ref["id"],1)
    with pytest.raises(PermissionError):n.engine.delegation.call("parent",request("read_capture",n.work,capture_id=group["id"]))


def test_captured_record_closure_is_delivered_to_actual_assigned_child(captured):
    n=captured; group=n.engine.delegation.call("parent",n.capture_args)["capture"]
    exp=exposition("E-native-product",{"id":group["records"][0]["id"],"revision":1})
    n.engine.call("parent",request("expose",n.work,6,"expose-capture",exposition=exp))
    packet=n.engine.handoff("parent",n.work,"E-native-product")["packet"]
    assert {r["ref"]["id"] for r in packet["records"]}==set(group["required_resources"]["records"])
    spec={**n.spec,"id":"H-captured","exposition_id":"E-native-product"}
    n.engine.delegation.call("parent",request("plan_handoff",n.work,7,"handoff-capture",handoff=spec))
    child,_=start(n,resources=group["required_resources"])
    assert n.host.store.read(child,group["records"][0]["id"],1)["source_dependencies"]


def test_capture_incomplete_group_does_not_claim_full_result_after_local_CAS_failure(captured,monkeypatch):
    n=captured
    monkeypatch.setattr(n.engine,"commit",lambda *a,**k:(_ for _ in ()).throw(ValueError("intake_work_revision_conflict")))
    with pytest.raises(ValueError,match="revision_conflict"):n.engine.delegation.call("parent",n.capture_args)
    assert not n.engine.status("parent",n.work)["state"].get("native_captures")


def test_capture_closes_path_with_unqualified_external_tool_inputs(captured):
    n=captured
    with n.host.store.db() as db:
        row=db.execute("SELECT child_sid FROM intake_delegations").fetchone();binding=n.host.store.binding(row[0])
        db.execute("UPDATE action_grants SET permissions=? WHERE assignment_id=?",(canonical([{"rule_id":"external.unqualified.read","arguments_schema":{"type":"object"}}]),binding["assignment_id"]))
    with pytest.raises(PermissionError,match="other_tool_inputs"):n.engine.delegation.call("parent",n.capture_args)


def test_capture_does_not_promote_strict_output_into_G_context(delegated):
    from bnn_intake.product_capture import ProductCapture
    n=delegated;n.host.config.setdefault("modules",{})["gsd"]={"files":{}}
    spec={**n.spec,"module":"gsd","role":"S","id":"H-strict"}
    n.engine.delegation.call("parent",request("plan_handoff",n.work,5,"strict-handoff",handoff=spec))
    child,_=start(n,role="S",module="gsd")
    with pytest.raises(PermissionError,match="explicit_holdout_visibility_contract"):
        ProductCapture(n.engine.delegation).dependencies("parent",child,["parent","G"])


def test_record_source_dependency_requires_an_addressed_document_exposition(delegated):
    n=delegated
    record=n.host.store.record("parent",{"kind":"note","scope":"project","request_id":"document-derived","source_ref":"synthetic:document-derived","content":"Derived note with an unexposed original",
        "source_dependencies":[{"kind":"source","sha256":"b"*64}]})
    n.engine.call("parent",request("expose",n.work,5,"source-dependency-exposition",exposition=exposition("E-unexposed",record)))
    with pytest.raises(PermissionError,match="explicit_document_exposition"):n.engine.handoff("parent",n.work,"E-unexposed")


def test_capture_overflow_uses_native_split_contract_before_any_record_write(captured):
    n=captured;task=n.capture_args["origin"]["task_id"]
    with n.host.store.db() as db:
        body=json.loads(db.execute("SELECT body FROM native_task_history WHERE id=?",(task,)).fetchone()[0]);body["product"]["findings"]=["x"*270000]
        raw=canonical(body);db.execute("UPDATE native_task_history SET body=?,sha256=? WHERE id=?",(raw,digest(body),task));db.execute("UPDATE native_tasks SET body=? WHERE id=?",(raw,task))
        before=db.execute("SELECT count(*) FROM records").fetchone()[0]
    value=n.engine.delegation.call("parent",request("read_result",n.work,native_task_id=task,native_revision=1))
    with pytest.raises(PermissionError,match="split_native_assignment_contract"):
        n.engine.delegation.call("parent",{**n.capture_args,"product_sha256":value["product_sha256"]})
    with n.host.store.db() as db:assert db.execute("SELECT count(*) FROM records").fetchone()[0]==before


def test_capture_retry_after_known_local_CAS_failure_reuses_exact_fragments(captured,monkeypatch):
    n=captured;commit=n.engine.commit
    monkeypatch.setattr(n.engine,"commit",lambda *a,**k:(_ for _ in ()).throw(ValueError("intake_work_revision_conflict")))
    with pytest.raises(ValueError):n.engine.delegation.call("parent",n.capture_args)
    with n.host.store.db() as db:before=db.execute("SELECT count(*) FROM records").fetchone()[0]
    monkeypatch.setattr(n.engine,"commit",commit)
    result=n.engine.delegation.call("parent",n.capture_args)
    assert n.engine.delegation.call("parent",request("read_capture",n.work,capture_id=result["capture"]["id"]))["complete"]
    with n.host.store.db() as db:assert db.execute("SELECT count(*) FROM records").fetchone()[0]==before
