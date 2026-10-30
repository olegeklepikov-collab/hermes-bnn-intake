"""Reader/ownership fixtures only. Real canonical jobs are qualified separately."""
import hashlib
from types import SimpleNamespace
import pytest
from conftest import request
from test_delegation import delegated, start
from test_native_context import native


@pytest.fixture
def gsd_reader(delegated, monkeypatch):
    n = delegated
    n.host.config.setdefault("modules", {})["gsd"] = {"files": {}}
    spec = {**n.spec, "id": "H-canonical-S", "module": "gsd", "role": "S"}
    n.engine.delegation.call("parent", request("plan_handoff", n.work, 5, "plan-canonical", handoff=spec))
    child, _ = start(n, role="S", module="gsd")
    n.child = child
    n.job = {"id": "J." + "a" * 32, "parent_session_id": "parent", "child_session_id": child,
             "role": "S", "status": "completed", "native_receipt": {"terminal_state": "SUCCEEDED"}}
    class RuntimeFixture:
        """Private-ledger stand-in, not an actual GSD receipt or provider."""
        bridge = SimpleNamespace(call=lambda operation, args: dict(n.job))
        def read_role(self, args, sid):
            assert sid == "parent" and args["view"] == "validated"
            text = '{"component":"S","status":"unreviewed","fixture":true}'
            start, limit = args["offset"], args["max_chars"]
            if start > len(text):raise ValueError("completed_role_offset_outside_text")
            end = min(start + limit, len(text))
            return {"text": text[start:end], "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                    "next_offset": end if end < len(text) else None, "executor_repeated": False}
    monkeypatch.setattr(n.host.modules, "require", lambda sid, module: None)
    import bn_memory.gsd_module as native
    monkeypatch.setattr(native, "get", lambda host: SimpleNamespace(runtime=RuntimeFixture()))
    n.args = request("read_gsd_result", n.work, job_id=n.job["id"], limit=4096)
    return n


def test_gsd_reader_preserves_canonical_origin_and_new_session_rights(gsd_reader):
    n = gsd_reader
    first = n.engine.delegation.call("parent", n.args)
    n.host.store.bind("resumed")
    with n.host.store.db() as db:db.execute("INSERT INTO working_bindings VALUES(?,?,?)", (n.host.store.profile, "resumed", n.work))
    later = n.engine.delegation.call("resumed", n.args)
    assert first["text"] == later["text"] and first["text_sha256"] == later["text_sha256"]
    assert later["origin_parent_sid"] == "parent" and later["reader_sid"] == "resumed"
    assert later["scientific_acceptance"] is False and later["executor_repeated"] is False


@pytest.mark.parametrize("change", [{"status": "failed"}, {"child_session_id": None}, {"native_receipt": {"terminal_state": "UNKNOWN"}}])
def test_gsd_reader_does_not_turn_missing_executor_into_success(gsd_reader, change):
    n = gsd_reader; n.job.update(change)
    with pytest.raises(PermissionError, match="successful_native_product"):
        n.engine.delegation.call("parent", n.args)


@pytest.mark.parametrize("change", [{"role": "D"}, {"parent_session_id": "foreign-parent"}])
def test_gsd_reader_rejects_foreign_role_or_parent(gsd_reader, change):
    n = gsd_reader; n.job.update(change)
    with pytest.raises(PermissionError, match="handoff_mismatch"):
        n.engine.delegation.call("parent", n.args)


def test_gsd_reader_checks_current_source_visibility_before_native_read(gsd_reader):
    n = gsd_reader
    n.host.store.retract("parent", n.ref["id"], 1)
    with pytest.raises(PermissionError):n.engine.delegation.call("parent", n.args)


def test_gsd_reader_exact_contract_and_native_grant_remain_required(gsd_reader):
    n = gsd_reader
    with pytest.raises(ValueError):n.engine.delegation.call("parent", {**n.args, "job_id": "TASK-plain-analyst"})
    with pytest.raises(ValueError):n.engine.delegation.call("parent", {**n.args, "limit": 4097})
    with n.host.store.db() as db:db.execute("UPDATE launch_grants SET state='failed' WHERE child_sid=?", (n.child,))
    with pytest.raises(PermissionError, match="original_native_grant"):
        n.engine.delegation.call("parent", n.args)
