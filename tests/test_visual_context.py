"""Actual parser/grants/request boundary; synthetic callback is NOT a model read."""
import copy
from pathlib import Path
import shutil
import pytest
from bn_memory.source_visibility import Visibility
from test_native_context import native
from test_delegation import delegated, start
from test_documents import PARSER_PYTHON, ROOT, bind, plan, expose
from test_document_handoffs import install_plan
from bnn_intake.visuals import image_urls


@pytest.fixture
def visual_child(delegated):
    n = delegated; n.host.config["document_python"] = PARSER_PYTHON
    path = Path(n.host.config["projects"]["p"]) / "visual.pdf"
    shutil.copyfile(ROOT / "tests/fixtures/documents/control.pdf", path)
    docs = n.host.document_store()
    cid = docs.catalogue("parent", {"path": str(path), "request_id": "visual-source", "required_phases": ["read", "analyse"]})["corpus_id"]
    with n.host.store.db() as db:
        did, sha = db.execute("SELECT id,sha256 FROM document_sources WHERE corpus=?", (cid,)).fetchone()
    Visibility(n.host.store).revise(sha, 0, {"allowed_roles": ["parent", "analyst"], "evidence_use": "general"}, "synthetic visual fixture")
    assert docs.extract("parent", {"doc_id": did})["status"] == "extracted"
    bind(n.engine.documents, n.engine, n.work, cid)
    expose(n.engine, n.work, [v["reference"] for v in plan(n.engine.documents, n.work, cid)["fragments"]], name="E-document")
    install_plan(n, "analyst"); n.child, _ = start(n, {"documents": [did]})
    n.docs, n.cid, n.did = docs, cid, did
    n.bridge = n.attached["context_bridge"]
    n.incoming = {"model": "synthetic-no-provider", "input": [{"role": "user", "content": "Read the assigned pages"}]}
    return n


def legacy_request(n, turn):
    n.host.before_turn(session_id=n.child, parent_session_id="parent", turn_id=turn, platform="subagent", user_message=n.incoming["input"][0]["content"])
    return n.bridge.originals["request"](copy.deepcopy(n.incoming), session_id=n.child, turn_id=turn, platform="subagent")["request"]


def test_native_transient_visual_loss_reproduced_then_retained_without_new_read_credit(visual_child):
    n = visual_child
    for unit in [0, 1]:
        n.docs.read(n.child, {"corpus_id": n.cid, "doc_id": n.did, "unit": unit, "phase": "analyse", "max_chars": 65536})
    first = legacy_request(n, "before-fixture-response")
    assert len(set(image_urls(first))) == 2
    # Explicitly synthetic response callback, no provider. This reproduces the
    # native request lifecycle without calling it evidence of actual reading.
    n.docs.schedule_delivery(n.child, first, "synthetic-fixture-no-provider", "analyse")
    n.docs.confirm_response(n.child, "synthetic-fixture-no-provider")
    second = legacy_request(n, "after-fixture-response")
    assert not list(image_urls(second))  # Actual legacy loss, not a mock request.
    with n.host.store.db() as db: before = db.execute("SELECT count(*) FROM document_deliveries").fetchone()[0]
    held = n.bridge.visuals.inject(n.child, second)
    assert len(set(image_urls(held))) == 2
    n.bridge.visuals.validate(n.child, held)
    with n.host.store.db() as db: assert db.execute("SELECT count(*) FROM document_deliveries").fetchone()[0] == before


def test_visuals_are_deduplicated_and_available_before_explicit_read_without_credit(visual_child):
    n = visual_child; first = n.bridge.visuals.inject(n.child, n.incoming)
    second = n.bridge.visuals.inject(n.child, first)
    assert first == second and len(list(image_urls(second))) == 2
    with n.host.store.db() as db:
        assert db.execute("SELECT count(*) FROM document_pending").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM document_deliveries").fetchone()[0] == 0


@pytest.mark.parametrize("damage", ["image", "caption", "image_as_text"])
def test_missing_visual_is_rejected_at_real_intake_final_control_before_native_executor(visual_child, damage):
    n = visual_child; prepared = n.bridge.visuals.inject(n.child, legacy_request(n, "visual-boundary"))
    row = prepared["input"][-1]
    if damage == "caption":
        row["content"] = [p for p in row["content"] if "BN_INTAKE_VISUAL_SOURCE" not in p.get("text", "")]
    else:
        part = next(p for p in row["content"] if p.get("type") == "input_image")
        row["content"].remove(part)
        if damage == "image_as_text": row["content"].append({"type": "input_text", "text": part["image_url"]})
    decision = n.bridge.control({"request": prepared}, session_id=n.child, turn_id="visual-boundary", platform="subagent")
    assert not decision.allow and "assigned_visual_missing" in decision.reason


def test_visual_overflow_requires_split_and_does_not_trim_or_create_receipts(visual_child):
    n = visual_child; n.host.config["max_request_bytes"] = 128
    with pytest.raises(PermissionError, match="visual_split_required"):
        n.bridge.visuals.inject(n.child, n.incoming)
    with n.host.store.db() as db: assert db.execute("SELECT count(*) FROM document_pending").fetchone()[0] == 0


def test_visuals_stop_after_source_revocation_or_owner_off(visual_child):
    n = visual_child; n.bridge.visuals.inject(n.child, n.incoming)
    source = n.docs.source("parent", n.did)
    Visibility(n.host.store).revise(source["sha256"], 1, {"allowed_roles": ["parent"], "evidence_use": "general"}, "synthetic revoke")
    with pytest.raises(PermissionError): n.bridge.visuals.inject(n.child, n.incoming)
    mode = n.engine.mode("parent"); n.engine.set_mode("parent", "off", mode["revision"], "fixture-owner")
    assert n.bridge.visuals.inject(n.child, n.incoming) == n.incoming
