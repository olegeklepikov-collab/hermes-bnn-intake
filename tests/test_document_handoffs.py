"""Real parser/source/launch controls. No provider executes in these tests."""
import copy
import sqlite3
from pathlib import Path
import pytest
from bn_memory.source_visibility import Visibility
from bnn_intake.documents import ROLE_PHASES
from conftest import request
from test_delegation import delegated, start
from test_documents import PARSER_PYTHON, bind, plan, expose
from test_native_context import native


def add_document(n, phases, roles=None):
    n.host.config["document_python"] = PARSER_PYTHON
    path = Path(n.host.config["projects"]["p"]) / "original.md"
    path.write_text("Синтетический оригинал: before=20; after=30. Единицы неизвестны.\n")
    docs = n.host.document_store()
    cid = docs.catalogue("parent", {"path": str(path), "request_id": "role-doc", "required_phases": phases})["corpus_id"]
    with n.host.store.db() as db:
        did, sha = db.execute("SELECT id,sha256 FROM document_sources WHERE corpus=?", (cid,)).fetchone()
    Visibility(n.host.store).revise(sha, 0, {"allowed_roles": roles or ["parent", *ROLE_PHASES],
        "evidence_use": "general"}, "explicit operator permission for synthetic fixture only")
    parsed = docs.extract("parent", {"doc_id": did})
    assert parsed["status"] == "extracted"
    bind(n.engine.documents, n.engine, n.work, cid)
    refs = [x["reference"] for x in plan(n.engine.documents, n.work, cid)["fragments"]]
    expose(n.engine, n.work, refs, name="E-document")
    return cid, did, refs


def spec(n, role):
    return {**n.spec, "id": "H-document-" + role, "module": "gsd" if role in {"G", "S", "D"} else "base",
            "role": role, "exposition_id": "E-document"}


def install_plan(n, role):
    state = n.engine.status("parent", n.work)["state"]
    n.engine.delegation.call("parent", request("plan_handoff", n.work, state["revision"], "handoff-doc-" + role, handoff=spec(n, role)))


@pytest.mark.parametrize("role,phase", ROLE_PHASES.items())
def test_explicit_role_phase_preserves_original_and_creates_no_read_credit(delegated, role, phase):
    n = delegated; cid, did, refs = add_document(n, ["read", "collect", "analyse", "verify", "G", "S", "D"])
    state = n.engine.status("parent", n.work)["state"]
    packet = n.engine.delegation.packet("parent", state, spec(n, role))
    original = n.engine.handoff("parent", n.work, "E-document")["packet"]["documents"]
    assigned = packet["source_packet"]["documents"]
    assert assigned == [{**r, "phase": phase} for r in original]
    assert not packet["document_obligations"]["reading_credit_created"]
    assert not packet["document_obligations"]["inherited_reading_credit"]
    with n.host.store.db() as db:
        assert db.execute("SELECT count(*) FROM document_deliveries").fetchone()[0] == 0
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='assignment_resources'").fetchone()
        assert not exists or db.execute("SELECT count(*) FROM assignment_resources").fetchone()[0] == 0


@pytest.mark.parametrize("role", ["collector", "analyst", "reviewer", "parser", "computation"])
def test_actual_native_child_gets_own_phase_and_assignment(delegated, role):
    n = delegated; cid, did, refs = add_document(n, ["read", "collect", "analyse", "verify"])
    install_plan(n, role)
    child, _ = start(n, {"documents": [did]}, role=role)
    packet = n.engine.delegation.child_packet(child)
    assert {r["phase"] for r in packet["source_packet"]["documents"]} == {ROLE_PHASES[role]}
    assert n.host.document_role(child) == ROLE_PHASES[role]
    assert not n.host.document_store().status(child, {"corpus_id": cid, "_recipient_sid": child,
        "_phases": [ROLE_PHASES[role]]})["model_delivery_complete"]


def test_undeclared_phase_is_denied_without_changing_corpus_or_read_ledger(delegated):
    n = delegated; cid, did, refs = add_document(n, ["read"])
    before = n.engine.status("parent", n.work)["state"]
    with pytest.raises(PermissionError, match="target_phase_not_declared"):
        install_plan(n, "analyst")
    assert n.engine.status("parent", n.work)["state"] == before
    with n.host.store.db() as db:
        assert db.execute("SELECT phases FROM document_corpora WHERE id=?", (cid,)).fetchone()[0] == '["read"]'
        assert db.execute("SELECT count(*) FROM document_deliveries").fetchone()[0] == 0


def test_G_holdout_source_rights_are_not_broadened_by_phase_mapping(delegated):
    n = delegated; cid, did, refs = add_document(n, ["read", "G"], roles=["parent", "S", "D", "reviewer"])
    with pytest.raises(PermissionError, match="role"):
        install_plan(n, "G")


def test_unassigned_document_does_not_launch_even_with_correct_phase(delegated):
    n = delegated; cid, did, refs = add_document(n, ["read", "analyse"])
    install_plan(n, "analyst")
    from hermes_cli.execution_control import ExecutionControlDenied
    with pytest.raises(ExecutionControlDenied, match="resource_not_assigned"):
        start(n, {}, role="analyst")


def test_source_revocation_closes_an_existing_child_without_rewriting_history(delegated):
    n = delegated; cid, did, refs = add_document(n, ["read", "analyse"])
    install_plan(n, "analyst"); child, _ = start(n, {"documents": [did]})
    old = copy.deepcopy(n.engine.delegation.binding(child))
    row = n.host.document_store().source("parent", did)
    Visibility(n.host.store).revise(row["sha256"], 1,
        {"allowed_roles": ["parent"], "evidence_use": "general"}, "synthetic withdrawal")
    with pytest.raises(PermissionError):
        n.engine.delegation.child_packet(child)
    assert n.engine.delegation.binding(child) == old


def test_SQLite_structural_closure_survives_role_phase_mapping(delegated):
    n = delegated; n.host.config["document_python"] = PARSER_PYTHON
    path = Path(n.host.config["projects"]["p"]) / "measurements.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE measurements (state TEXT,value INTEGER)")
        db.executemany("INSERT INTO measurements VALUES(?,?)", [("before", 20), ("after", 30)])
    docs = n.host.document_store()
    cid = docs.catalogue("parent", {"path": str(path), "request_id": "sqlite-role", "required_phases": ["read", "analyse"]})["corpus_id"]
    with n.host.store.db() as db:
        did, sha = db.execute("SELECT id,sha256 FROM document_sources WHERE corpus=?", (cid,)).fetchone()
    Visibility(n.host.store).revise(sha, 0, {"allowed_roles": ["parent", "analyst"], "evidence_use": "general"}, "synthetic schema fixture")
    assert docs.extract("parent", {"doc_id": did})["status"] == "extracted"
    bind(n.engine.documents, n.engine, n.work, cid)
    data = next(v["reference"] for v in plan(n.engine.documents, n.work, cid)["fragments"] if "row_start" in v["locator"])
    expose(n.engine, n.work, [data], name="E-document"); install_plan(n, "analyst")
    child, _ = start(n, {"documents": [did]})
    packet = n.engine.delegation.child_packet(child)
    assigned = packet["source_packet"]["documents"]
    assert len(assigned) == 3 and {r["phase"] for r in assigned} == {"analyse"}
    assert {r["locator"].get("kind") for r in assigned} >= {"schema", "complete_database_schema", None}
    with n.host.store.db() as db:
        assert db.execute("SELECT count(*) FROM document_deliveries").fetchone()[0] == 0
