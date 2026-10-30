import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
from bn_memory.namespace import register_profile
from bn_memory.store import Store
from bnn_intake.engine import Intake
from bnn_intake.storage import append
from conftest import prepare, record, request
from test_exposition import exposition


def test_modes_are_profile_local_in_the_shared_primary_db(fixture, contract):
    store, engine, work = fixture
    prepare(engine, work, contract)
    home = str(store.root.parent / "other-profile")
    with store.db() as db:
        register_profile(db, store.namespace["owner_id"], "other-profile", home)
    config = {**store.config, "namespace": {"owner_id": store.namespace["owner_id"], "profile_id": "other-profile"}, "profile_home": home}
    other = Store(store.root, config); other.bind("parent")
    second = Intake(other)
    assert second.mode("parent")["mode"] == "off"
    second.set_mode("parent", "auto", 0, "owner")
    assert engine.mode("parent")["mode"] == "on"
    with pytest.raises(PermissionError, match="native_work_binding"):
        second.call("parent", request("status", work))


def test_work_binding_change_before_commit_is_rejected(fixture, contract, monkeypatch):
    store, engine, work = fixture
    prepare(engine, work, contract); ref = record(store)
    real_inputs = engine._inputs
    def changing_inputs(sid, exp):
        out = real_inputs(sid, exp)
        with store.db() as db:
            db.execute("DELETE FROM working_bindings WHERE profile_id=? AND sid=?", (store.profile, sid))
        return out
    monkeypatch.setattr(engine, "_inputs", changing_inputs)
    with pytest.raises(PermissionError, match="binding_changed|native_work_binding"):
        engine.call("parent", request("expose", work, 1, "race", exposition=exposition("E1", ref)))
    with store.db() as db:
        assert db.execute("SELECT count(*) FROM intake_revisions").fetchone()[0] == 1


def test_mode_off_before_commit_is_rejected(fixture, contract, monkeypatch):
    store, engine, work = fixture
    prepare(engine, work, contract); ref = record(store)
    real_inputs = engine._inputs
    def changing_inputs(sid, exp):
        out = real_inputs(sid, exp)
        engine.set_mode(sid, "off", 1, "owner")
        return out
    monkeypatch.setattr(engine, "_inputs", changing_inputs)
    with pytest.raises(PermissionError, match="disabled"):
        engine.call("parent", request("expose", work, 1, "race", exposition=exposition("E1", ref)))
    assert engine.status("parent", work)["state"]["revision"] == 1


def test_long_dependency_chain_does_not_use_python_recursion(fixture, contract):
    store, engine, work = fixture
    prepare(engine, work, contract)
    state = engine.status("parent", work)["state"]
    for i in range(1100):
        state["expositions"][f"E{i}"] = {"id": f"E{i}", "purpose": "dependency fixture", "depends_on": [f"E{i-1}"] if i else [], "sources": [], "method_ids": []}
    state["revision"] = 2
    with store.db() as db:
        append(db, store.profile, work, state)
    out = engine.handoff("parent", work, "E1099")
    assert len(out["packet"]["dependencies"]) == 1100


def test_cli_reads_same_persistent_state_in_another_process(fixture, contract, tmp_path):
    store, engine, work = fixture
    prepare(engine, work, contract)
    config = tmp_path / "config.json"; config.write_text(json.dumps(store.config))
    req = tmp_path / "request.json"; req.write_text(json.dumps(request("status", work)))
    result = subprocess.run([sys.executable, "-B", "-m", "bnn_intake.cli", "--config", str(config), "call", "--sid", "parent", "--request", str(req)], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["state"]["contract"] == contract


def test_cli_refuses_live_config_outside_development(fixture, tmp_path):
    result = subprocess.run([sys.executable, "-B", "-m", "bnn_intake.cli", "--config", "/outside-managed-profile/settings.json", "mode", "--sid", "parent", "--mode", "on"], text=True, capture_output=True)
    assert result.returncode == 2 and "isolated config" in result.stderr
