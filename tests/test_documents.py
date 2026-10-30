"""Actual native sandboxed parsers; isolated corpus and pre-provider boundaries.

Synthetic receipt rows in the attribution test are labelled as fixtures, never as
evidence that a model read anything. All execution-boundary runs abort pre-provider.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
import pytest
from bn_memory.documents import Documents
from bnn_intake.documents import DocumentBridge
from bnn_intake.storage import install
from conftest import BASELINE, ROOT, prepare, request
from test_native_context import native, model_admit, turn

PARSER_PYTHON = BASELINE["document_python"]


def connect(store, engine):
    store.config["document_python"] = PARSER_PYTHON
    broker = SimpleNamespace(store=store, config=store.config)
    documents = Documents(broker)  # Explicit native initialization, not an intake side effect.
    engine.documents = DocumentBridge(engine, broker)
    return documents, engine.documents


def acquire(documents, store, path, name="source", parse=True):
    cid = documents.catalogue("parent", {"path": str(path), "request_id": name, "required_phases": ["read"]})["corpus_id"]
    with store.db() as db:
        sources = [dict(r) for r in db.execute("SELECT id,path,format,status FROM document_sources WHERE corpus=? ORDER BY path", (cid,))]
    results = []
    if parse:
        for source in sources:
            if source["status"] == "registered":
                for _ in range(12):  # Fixture guard, not a production document/page cap.
                    result = documents.extract("parent", {"doc_id": source["id"]})
                    results.append(result)
                    preserve_parser_receipt(result, source["id"])
                    if result["status"] != "extracting":
                        break
                assert result["status"] == "extracted", result
    return cid, sources, results


def preserve_parser_receipt(result, did):
    target = os.environ.get("BNN_INTAKE_TEST_EVIDENCE")
    metadata = result.get("metadata", {})
    source = metadata.get("execution_receipt_ref")
    if target and source:
        destination = Path(target) / "parser-executions"
        destination.mkdir(exist_ok=True)
        original = Path(source)
        shutil.copyfile(original, destination / (did + "-" + original.name))


def bind(bridge, engine, work, cid):
    rev = engine.status("parent", work)["state"]["revision"]
    return bridge.call("parent", request("bind_corpus", work, rev, "bind-" + cid, corpus_id=cid))


def plan(bridge, work, cid, step=65536, offset=0, limit=100, basis=None):
    return bridge.call("parent", request("plan", work, corpus_id=cid, fragment_characters=step,
                                         offset=offset, limit=limit, basis=basis))


def expose(engine, work, refs, name="DOC-E", select=False):
    rev = engine.status("parent", work)["state"]["revision"]
    exp = {"id": name, "purpose": "Адресно проверить документарный материал", "question": "Что действительно предъявлено?",
           "input_refs": refs, "depends_on": [], "method_ids": []}
    engine.call("parent", request("expose", work, rev, "expose-" + name, exposition=exp))
    if select:
        engine.call("parent", request("select_context", work, rev + 1, "select-" + name, exposition_id=name))
    return engine.handoff("parent", work, name)


@pytest.fixture
def docenv(fixture, contract):
    store, engine, work = fixture
    prepare(engine, work, contract)
    documents, bridge = connect(store, engine)
    project = Path(store.config["projects"]["p"])
    return SimpleNamespace(store=store, engine=engine, work=work, native=documents, bridge=bridge, project=project)


def test_schema_v2_upgrade_preserves_snapshots_and_operations(fixture, contract):
    store, engine, work = fixture
    original = prepare(engine, work, contract)
    with store.db() as db:
        db.execute("DROP TABLE intake_path_attempts")
        db.execute("DROP TABLE intake_delegations")
        db.execute("DROP TABLE intake_model_outcomes")
        db.execute("DROP TABLE intake_document_links")
        db.execute("UPDATE meta SET value='2' WHERE key='semantic_intake_contract'")
    assert install(store) == {"schema_version": 6, "changed": True}
    assert prepare(engine, work, contract)["replayed"]
    assert engine.status("parent", work)["content_hash"] == original["content_hash"]


@pytest.mark.parametrize("name,body", [
    ("large.md", "BEGIN\n" + "Русский материал 20→30.\r\n" * 6000 + "END"),
    ("data.json", '{"before":20,"after":30,"instruction":"untrusted data"}'),
    ("data.xml", '<values><before>20</before><after>30</after></values>'),
    ("data.html", '<html><body><p>Before 20. After 30.</p><script>unsafe()</script></body></html>'),
    ("data.sql", 'CREATE TABLE forbidden(value INTEGER); INSERT INTO forbidden VALUES(30);'),
    ("data.csv", 'state,value\nbefore,20\nafter,30\n'),
], ids=["large-md", "json", "xml", "html", "passive-sql", "csv"])
def test_native_parser_and_addressed_plan_do_not_claim_model_reading(docenv, name, body):
    d = docenv
    target = d.project / name; target.write_bytes(body.encode())
    cid, sources, parsed = acquire(d.native, d.store, target)
    bind(d.bridge, d.engine, d.work, cid)
    first = plan(d.bridge, d.work, cid, step=1000, limit=1)
    refs = [first["fragments"][0]["reference"]]
    page = first
    while page["has_more"]:
        page = plan(d.bridge, d.work, cid, step=1000, offset=page["next_offset"], limit=100, basis=first["basis"])
        refs.extend(x["reference"] for x in page["fragments"])
    assert len(refs) == first["fragments_total"] and len(refs) == len({json.dumps(r, sort_keys=True) for r in refs})
    assert not first["coverage"]["model_delivery_complete"] and not first["model_received"]
    assert first["coverage"]["files_extracted"] == 1
    with d.store.db() as db:
        units = [json.loads(r[0]) for r in db.execute("SELECT body FROM document_units WHERE doc=? ORDER BY unit", (sources[0]["id"],))]
        assert db.execute("SELECT count(*) FROM document_pending").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM records").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM sqlite_master WHERE name='forbidden'").fetchone()[0] == 0
    for unit in units:
        ranges = [r for r in refs if r["unit"] == unit["index"]]
        assert ranges[0]["start"] == 0 and ranges[-1]["end"] == unit["characters"]
        assert all(a["end"] == b["start"] for a, b in zip(ranges, ranges[1:]))
    handoff = expose(d.engine, d.work, refs[:1])
    assert handoff["packet"]["documents"][0]["source_sha256"] == sources_hash(d, sources[0]["id"])
    assert not handoff["delivered_to_model"] and handoff["executor_started"] is False
    if name == "large.md":
        texts = [Path(parsed[-1]["metadata"]["parse_output"], str(u["index"]) + ".txt").read_bytes().decode() for u in units]
        assert "".join(texts) == body  # Includes CRLF and multibyte boundary, no memory-record truncation.


def sources_hash(d, did):
    return d.native.source("parent", did)["sha256"]


def test_sqlite_rows_include_complete_schema_and_table_schema(docenv):
    d = docenv; path = d.project / "data.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE measurements (state TEXT, value INTEGER)")
        db.executemany("INSERT INTO measurements VALUES(?,?)", [("before", 20), ("after", 30)])
    cid, _, _ = acquire(d.native, d.store, path)
    bind(d.bridge, d.engine, d.work, cid)
    page = plan(d.bridge, d.work, cid)
    data = next(x["reference"] for x in page["fragments"] if "row_start" in x["locator"])
    handoff = expose(d.engine, d.work, [data])
    locators = [r["locator"] for r in handoff["packet"]["documents"]]
    assert {x.get("kind") for x in locators} >= {"complete_database_schema", "schema", None}
    assert len(locators) == 3 and len({r["unit"] for r in handoff["packet"]["documents"]}) == 3


def test_partial_parse_invalidates_continuation_without_hiding_missing_sources(docenv):
    d = docenv; path = d.project / "control.pdf"
    shutil.copyfile(ROOT / "tests/fixtures/documents/control.pdf", path)
    d.store.config["parser_limits"] = {"max_units": 1}
    cid, sources, _ = acquire(d.native, d.store, path, parse=False)
    bind(d.bridge, d.engine, d.work, cid)
    empty = plan(d.bridge, d.work, cid)
    assert empty["fragments_total"] == 0 and empty["coverage"]["missing_sources_count"] == 1
    outcomes = []
    for _ in range(6):
        result = d.native.extract("parent", {"doc_id": sources[0]["id"]})
        preserve_parser_receipt(result, sources[0]["id"])
        outcomes.append(result["status"])
        if result["status"] == "extracted":
            break
    assert outcomes[-1] == "extracted" and "extracting" in outcomes
    with pytest.raises(PermissionError, match="plan_changed"):
        plan(d.bridge, d.work, cid, basis=empty["basis"])
    done = plan(d.bridge, d.work, cid)
    assert done["fragments_total"] == 2 and done["coverage"]["missing_sources_count"] == 0
    assert all(f["visual_required"] for f in done["fragments"])


def test_thousand_sources_inventory_is_complete_but_not_a_reading_claim(docenv):
    d = docenv; root = d.project / "many"; root.mkdir()
    for i in range(1000):
        (root / f"{i:04d}.md").write_text(f"Synthetic record {i}\n")
    (root / "unknown.unsupported").write_bytes(b"unhandled")
    cid, sources, _ = acquire(d.native, d.store, root, parse=False)
    bind(d.bridge, d.engine, d.work, cid)
    page = plan(d.bridge, d.work, cid)
    assert len(sources) == page["coverage"]["entries_total"] == 1001
    assert page["coverage"]["missing_sources_count"] == 1001
    assert page["coverage"]["files_extracted"] == page["fragments_total"] == 0
    assert not page["coverage"]["model_delivery_complete"]


def test_unsupported_and_corrupt_input_remain_explicitly_unfinished(docenv):
    d = docenv; root = d.project / "broken"; root.mkdir()
    (root / "unknown.unknown").write_bytes(b"not a supported format")
    (root / "corrupt.pdf").write_bytes(b"This is not a PDF")
    cid, sources, _ = acquire(d.native, d.store, root, parse=False)
    bind(d.bridge, d.engine, d.work, cid)
    pdf = next(r for r in sources if r["format"] == "pdf")
    result = d.native.extract("parent", {"doc_id": pdf["id"]})
    preserve_parser_receipt(result, pdf["id"])
    assert result["status"] == "parse_blocked"
    coverage = d.bridge.coverage("parent", d.work, cid)
    assert coverage["entries_total"] == coverage["missing_sources_count"] == 2
    assert {r["status"] for r in coverage["missing_sources"]} == {"unsupported", "parse_blocked"}
    assert not coverage["model_delivery_complete"] and not coverage["semantic_understanding_verified"]
    with pytest.raises(ValueError, match="repair_required_before_retry"):
        d.native.extract("parent", {"doc_id": pdf["id"]})


def test_partition_cannot_silently_drop_a_required_table_schema(docenv):
    d = docenv; path = d.project / "partition.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE records(id INTEGER PRIMARY KEY,value TEXT)")
        db.executemany("INSERT INTO records VALUES(?,?)", [(i, "Synthetic row " + "x" * 90) for i in range(240)])
    cid, _, _ = acquire(d.native, d.store, path)
    partitions = d.native.partition("parent", {"corpus_id": cid, "max_characters": 1024})
    assert partitions["partitions_total"] > 1
    part = partitions["partitions"][-1]["corpus_id"]
    bind(d.bridge, d.engine, d.work, part)
    data = plan(d.bridge, d.work, part)["fragments"][0]["reference"]
    with pytest.raises(PermissionError, match="structural_dependency_unassigned"):
        expose(d.engine, d.work, [data])


def text_corpus(d, content="Before 20. After 30."):
    path = d.project / "source.md"; path.write_text(content)
    cid, sources, _ = acquire(d.native, d.store, path)
    bind(d.bridge, d.engine, d.work, cid)
    return cid, sources, plan(d.bridge, d.work, cid)["fragments"][0]["reference"]


def test_staging_reuses_pending_and_retires_only_owned_unselected_fragments(docenv):
    d = docenv; cid, sources, ref = text_corpus(d)
    external = d.native.read("parent", {"corpus_id": cid, "doc_id": ref["doc_id"], "unit": ref["unit"], "max_chars": 2})["token"]
    first = d.bridge.stage("parent", d.work, [ref])
    assert len(first["created_tokens"]) == 1
    assert not d.bridge.stage("parent", d.work, [ref])["created_tokens"]
    d.bridge.retire_unselected("parent", d.work, [])
    assert [r["token"] for r in d.native.pending("parent")] == [external]
    assert not d.bridge.stage("parent", d.work, [ref])["created_tokens"]
    assert len(d.native.pending("parent")) == 2


def test_no_implicit_document_install_in_intake_off_path(fixture):
    store, engine, work = fixture
    engine.documents = DocumentBridge(engine, SimpleNamespace(store=store, config=store.config))
    engine.set_mode("parent", "off", 1, "owner")
    assert engine.mode("parent")["mode"] == "off"
    with store.db() as db:
        assert db.execute("SELECT count(*) FROM sqlite_master WHERE name='document_pending'").fetchone()[0] == 0
    with pytest.raises(PermissionError, match="native_document_ledger_required"):
        engine.documents.inventory("parent", "missing")
    with store.db() as db:
        assert db.execute("SELECT count(*) FROM sqlite_master WHERE name='document_pending'").fetchone()[0] == 0


def test_document_staging_stops_with_owner_switch(docenv):
    d = docenv; _, _, ref = text_corpus(d)
    d.engine.set_mode("parent", "off", 1, "owner")
    with pytest.raises(PermissionError, match="document_path_inactive"):
        d.bridge.stage("parent", d.work, [ref])
    assert not d.native.pending("parent")


def test_staging_change_mid_read_leaves_no_unowned_pending_fragment(docenv, monkeypatch):
    d = docenv; _, _, ref = text_corpus(d)
    original = d.bridge.native.read
    def changed(sid, args):
        out = original(sid, args)
        d.engine.set_mode("parent", "off", 1, "owner")
        return out
    monkeypatch.setattr(d.bridge.native, "read", changed)
    with pytest.raises(PermissionError, match="changed_during_staging"):
        d.bridge.stage("parent", d.work, [ref])
    assert not d.native.pending("parent")
    with d.store.db() as db:
        assert db.execute("SELECT count(*) FROM intake_document_links").fetchone()[0] == 0
        assert db.execute("SELECT state FROM document_pending").fetchone()[0] == "intake_inactive"


def test_separate_cli_process_reads_the_same_work_corpus(docenv):
    d = docenv; cid, _, _ = text_corpus(d)
    config = d.project.parent / "isolated-config.json"; config.write_text(json.dumps(d.store.config))
    args = request("plan", d.work, corpus_id=cid, fragment_characters=1024, offset=0, limit=10, basis=None)
    result = subprocess.run([sys.executable, "-B", "-m", "bnn_intake.cli", "--config", str(config),
                             "documents", "--sid", "parent", "--request", "-"], input=json.dumps(args),
                            text=True, capture_output=True, check=True)
    value = json.loads(result.stdout)
    assert value["fragments_total"] == 1 and not value["coverage"]["model_delivery_complete"]


def test_unbound_corpus_and_changed_frozen_inventory_are_rejected(docenv):
    d = docenv; cid, sources, ref = text_corpus(d)
    missing = {**ref, "corpus_id": "CORP-unbound"}
    with pytest.raises(PermissionError, match="not_bound_to_work"):
        d.bridge.resolve("parent", d.work, missing)
    with d.store.db() as db:
        db.execute("UPDATE document_sources SET sha256=? WHERE id=?", ("0" * 64, sources[0]["id"]))
    with pytest.raises(PermissionError, match="frozen_corpus_changed"):
        plan(d.bridge, d.work, cid)


@pytest.mark.parametrize("target", ["source", "unit", "role", "range", "hash"])
def test_changed_sources_and_forged_references_close_document_path(docenv, target):
    d = docenv; cid, sources, ref = text_corpus(d)
    if target in {"source", "unit"}:
        row = d.native.source("parent", ref["doc_id"])
        path = Path(row["metadata"]["snapshot"] if target == "source" else row["metadata"]["parse_output"] + "/0.txt")
        path.chmod(0o600); path.write_text("corrupted")
    elif target == "role":
        ref["phase"] = "G"
    elif target == "range":
        ref["end"] += 100
    else:
        ref["unit_sha256"] = "0" * 64
    with pytest.raises((PermissionError, ValueError)):
        d.bridge.stage("parent", d.work, [ref])
    assert not d.native.pending("parent")


def test_work_scoped_coverage_ignores_a_synthetic_receipt_of_another_work(docenv, contract):
    d = docenv; cid, _, ref = text_corpus(d)
    first_token = d.bridge.stage("parent", d.work, [ref])["created_tokens"][0]
    # Explicit synthetic ledger fixture, no provider has been called.
    with d.store.db() as db:
        db.execute("UPDATE document_pending SET state='received',api_id='synthetic-not-model' WHERE token=?", (first_token,))
        db.execute("INSERT INTO document_deliveries VALUES(?,?,?,?)", (first_token, "synthetic-not-model", "read", 1.0))
    first = d.bridge.coverage("parent", d.work, cid)
    assert first["confirmed_deliveries"] == 1 and first["model_delivery_complete"]
    assert not first["semantic_understanding_verified"]
    other = "WORK-other"
    with d.store.db() as db:
        db.execute("INSERT INTO working_cards VALUES(?,?,?,?)", (d.store.profile, other, "p", json.dumps({"id": "other-fixture"})))
        db.execute("UPDATE working_bindings SET work_id=? WHERE profile_id=? AND sid='parent'", (other, d.store.profile))
    d.engine.call("parent", request("prepare", other, 0, "other-prepare", contract=contract))
    bind(d.bridge, d.engine, other, cid)
    second = d.bridge.coverage("parent", other, cid)
    assert second["confirmed_deliveries"] == second["linked_fragments"] == 0
    assert not second["model_delivery_complete"]


class PreProviderStop(RuntimeError):
    pass


def test_pdf_native_request_transports_exact_text_and_image_without_false_receipt(native):
    host, engine, work = native.host, native.engine, native.work
    host.config["document_python"] = PARSER_PYTHON
    path = Path(host.config["projects"]["p"]) / "control.pdf"
    shutil.copyfile(ROOT / "tests/fixtures/documents/control.pdf", path)
    docs = host.document_store()
    cid, sources, _ = acquire(docs, host.store, path)
    bind(engine.documents, engine, work, cid)
    fragments = plan(engine.documents, work, cid)["fragments"]
    expose(engine, work, [r["reference"] for r in fragments], select=True)
    incoming = {"model": "boundary-test-no-provider", "instructions": "Unchanged system", "input": [{"role": "user", "content": "Read the selected document"}]}
    host.before_turn(session_id="parent", user_message="Read the selected document", turn_id="pdf-turn", platform="cli")
    proof = {}
    def final_boundary(outgoing):
        assert outgoing["instructions"] == incoming["instructions"]
        text = json.dumps(outgoing, ensure_ascii=False)
        assert text.count('BN_DOCUMENT_DATA token=') == 2
        assert "Before" in text and "After" in text
        images = [p for row in outgoing["input"] if isinstance(row["content"], list) for p in row["content"] if p.get("type") == "input_image"]
        assert len(images) == 2 and all(p["image_url"].startswith("data:image/png;base64,") for p in images)
        admitted = model_admit(outgoing, turn_id="pdf-turn", api="pdf-pre-provider")
        assert all(d.allow for d in admitted)
        proof.update(prepared_request=outgoing, native_tokens=docs.pending("parent"), admitted=True)
        raise PreProviderStop("intentional stop before any provider dispatch")
    with pytest.raises(PreProviderStop):
        host.execution(incoming, final_boundary, session_id="parent", turn_id="pdf-turn", platform="cli")
    coverage = engine.documents.coverage("parent", work, cid)
    assert coverage["confirmed_deliveries"] == 0 and not coverage["model_delivery_complete"]
    with host.store.db() as db:
        rows = [dict(r) for r in db.execute("SELECT token,state,api_id,image_hash FROM document_pending")]
        assert len(rows) == 2 and all(r["state"] == "failed" for r in rows)
        assert db.execute("SELECT count(*) FROM document_deliveries").fetchone()[0] == 0
    target = os.environ.get("BNN_INTAKE_TEST_EVIDENCE")
    if target:
        proof.update(native_attempts_after_abort=rows, coverage=coverage, provider_called=False,
                     semantic_reading_executed=False, source_data="synthetic", boundary="actual_Host.execution_and_llm_controller")
        (Path(target) / "native-document-pre-provider.json").write_text(json.dumps(proof, ensure_ascii=False, indent=2) + "\n")


def test_removing_pdf_image_is_denied_by_actual_native_controller(native):
    host = native.host; host.config["document_python"] = PARSER_PYTHON
    path = Path(host.config["projects"]["p"]) / "control.pdf"
    shutil.copyfile(ROOT / "tests/fixtures/documents/control.pdf", path)
    docs = host.document_store(); cid, _, _ = acquire(docs, host.store, path)
    bind(native.engine.documents, native.engine, native.work, cid)
    ref = plan(native.engine.documents, native.work, cid)["fragments"][1]["reference"]
    expose(native.engine, native.work, [ref], select=True)
    host.before_turn(session_id="parent", user_message="PDF negative", turn_id="pdf-negative", platform="cli")
    from hermes_cli.execution_control import ExecutionControlDenied
    def damaged_boundary(outgoing):
        damaged = copy.deepcopy(outgoing)
        for row in damaged["input"]:
            if isinstance(row["content"], list):
                row["content"] = [p for p in row["content"] if p.get("type") != "input_image"]
        with pytest.raises(ExecutionControlDenied, match="document_visual_not_delivered_intact"):
            model_admit(damaged, turn_id="pdf-negative", api="missing-image")
        raise PreProviderStop()
    with pytest.raises(PreProviderStop):
        host.execution({"input": [{"role": "user", "content": "PDF negative"}]}, damaged_boundary,
                       session_id="parent", turn_id="pdf-negative", platform="cli")
    assert native.engine.documents.coverage("parent", native.work, cid)["confirmed_deliveries"] == 0


def test_document_tool_uses_actual_registry_and_known_write_settlement(native):
    from agent.tool_attempt import tool_attempt_scope
    from hermes_cli.execution_control import authorize
    host = native.host; host.config["document_python"] = PARSER_PYTHON
    path = Path(host.config["projects"]["p"]) / "registered.md"; path.write_text("Synthetic 20 to 30")
    cid, _, _ = acquire(host.document_store(), host.store, path)
    turn(native)
    args = request("bind_corpus", native.work, 3, "native-bind", corpus_id=cid)
    identity = {"session_id": "parent", "turn_id": "native-turn", "tool_call_id": "document-bind", "api_request_id": "doc-bind-api"}
    with tool_attempt_scope("intake_documents", identity, final_boundary="registry"):
        raw = native.ctx.dispatch_tool("intake_documents", args, **identity)
        assert json.loads(raw)["ok"]
        assert authorize("tool_result", {"tool_name": "intake_documents", "args": args, "result": raw}, **identity)[0].allow
    with host.store.db() as db:
        result = db.execute("SELECT status FROM action_attempts WHERE tool_name='intake_documents'").fetchone()
    assert result[0] == "returned_unreviewed"


def test_whole_request_bound_rejects_instead_of_trimming_document(native):
    from hermes_cli.execution_control import ExecutionControlDenied
    host = native.host; host.config["document_python"] = PARSER_PYTHON
    path = Path(host.config["projects"]["p"]) / "bounded.md"; path.write_text("START\n" + "Synthetic data. " * 1000 + "\nEND")
    docs = host.document_store(); cid, _, _ = acquire(docs, host.store, path)
    bind(native.engine.documents, native.engine, native.work, cid)
    refs = [r["reference"] for r in plan(native.engine.documents, native.work, cid)["fragments"]]
    expose(native.engine, native.work, refs, select=True)
    host.before_turn(session_id="parent", user_message="Bound check", turn_id="bound-turn", platform="cli")
    def boundary(outgoing):
        from bnn_intake.storage import canonical
        text = json.dumps(outgoing)
        assert "START" in text and "END" in text
        host.config["max_request_bytes"] = len(canonical(outgoing).encode()) - 1
        with pytest.raises(ExecutionControlDenied, match="native_request_size_exceeded"):
            model_admit(outgoing, turn_id="bound-turn", api="one-byte-too-large")
        raise PreProviderStop()
    with pytest.raises(PreProviderStop):
        host.execution({"input": [{"role": "user", "content": "Bound check"}]}, boundary,
                       session_id="parent", turn_id="bound-turn", platform="cli")
    assert native.engine.documents.coverage("parent", native.work, cid)["confirmed_deliveries"] == 0
