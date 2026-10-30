import copy
import json
import pytest
from bnn_intake.engine import Intake
from bnn_intake.storage import install
from conftest import prepare, request


def test_explicit_migration_idempotent_on_the_same_primary_db(fixture):
    store, engine, work = fixture
    assert install(store) == {"schema_version": 6, "changed": False}
    assert store.path.name == "memory.sqlite"
    assert list(store.root.glob("*.sqlite")) == [store.path]


def test_prepare_is_real_revision_and_survives_new_engine(fixture, contract):
    store, engine, work = fixture
    result = prepare(engine, work, contract)
    assert result["revision"] == 1
    assert Intake(store).status("parent", work)["state"]["contract"] == contract
    with store.db() as db:
        assert db.execute("SELECT count(*) FROM intake_revisions").fetchone()[0] == 1


def test_off_does_not_import_catalogue_or_modify_work(fixture, contract):
    store, engine, work = fixture
    engine.set_mode("parent", "off", 1, "owner")
    assert engine._catalogue is None
    with pytest.raises(PermissionError, match="disabled"):
        prepare(engine, work, contract)
    assert engine.status("parent", work)["state"] == "not_prepared"


def test_same_id_and_input_replay_does_not_create_second_revision(fixture, contract):
    store, engine, work = fixture
    first = prepare(engine, work, contract)
    replay = prepare(engine, work, contract)
    assert replay["replayed"] is True and replay["content_hash"] == first["content_hash"]
    with store.db() as db:
        assert db.execute("SELECT count(*) FROM intake_revisions").fetchone()[0] == 1


def test_idempotency_key_changed_input_is_rejected(fixture, contract):
    store, engine, work = fixture
    prepare(engine, work, contract)
    changed = {**contract, "scope": "other"}
    with pytest.raises(ValueError, match="idempotency_input_conflict"):
        prepare(engine, work, changed)


def test_stale_revision_cannot_overwrite_state(fixture, contract):
    store, engine, work = fixture
    prepare(engine, work, contract)
    with pytest.raises(ValueError, match="revision_conflict"):
        engine.call("parent", request("pause", work, 0, "stale", reason="pause"))
    assert engine.status("parent", work)["state"]["lifecycle"] == "active"


def test_pause_resume_cancel_preserve_history(fixture, contract):
    store, engine, work = fixture
    prepare(engine, work, contract)
    engine.call("parent", request("pause", work, 1, "p", reason="Ресурсная пауза"))
    engine.call("parent", request("resume", work, 2, "r", reason="Продолжение после паузы"))
    engine.call("parent", request("cancel", work, 3, "c", reason="Остановка"))
    assert len(engine.call("parent", request("history", work))["revisions"]) == 4
    with pytest.raises(PermissionError, match="cancelled"):
        engine.call("parent", request("resume", work, 4, "x", reason="Недопустимое возобновление"))


def test_extra_fields_bool_revision_and_unknown_action_are_rejected(fixture, contract):
    store, engine, work = fixture
    for bad in [request("prepare", work, True, "b", contract=contract),
                {**request("prepare", work, 0, "x", contract=contract), "shell": "touch /tmp/unsafe"},
                request("execute_shell", work)]:
        with pytest.raises(ValueError):
            engine.call("parent", bad)


def test_tampered_persisted_revision_fails_closed(fixture, contract):
    store, engine, work = fixture
    prepare(engine, work, contract)
    with store.db() as db:
        db.execute("UPDATE intake_revisions SET body='{}'")
    with pytest.raises(PermissionError, match="integrity"):
        engine.status("parent", work)


def test_no_implicit_migration_in_off_status(fixture):
    store, engine, work = fixture
    with store.db() as db:
        for table in ["intake_delegations", "intake_path_attempts", "intake_modes", "intake_revisions", "intake_operations", "intake_publications", "intake_document_links", "intake_model_outcomes"]:
            db.execute("DROP TABLE " + table)
        db.execute("DELETE FROM meta WHERE key='semantic_intake_contract'")
    assert engine.status("parent", work)["state"] == "not_initialized"
    with store.db() as db:
        assert db.execute("SELECT count(*) FROM sqlite_master WHERE name LIKE 'intake_%'").fetchone()[0] == 0


def test_work_is_not_created_as_secondary_task_registry(fixture, contract):
    store, engine, work = fixture
    with pytest.raises(PermissionError, match="native_work_binding"):
        prepare(engine, "WORK-not-bound", contract)


def test_profile_minimum_is_preserved(fixture, contract):
    store, engine, work = fixture
    store.config["intake_minimum_profile"] = "High Stakes"
    with pytest.raises(PermissionError, match="required_profile"):
        prepare(engine, work, contract)


def test_child_is_status_only_until_scoped_adapter_qualified(fixture, contract):
    store, engine, work = fixture
    prepare(engine, work, contract)
    store.bind("child", parent="parent", subagent=True)
    store.assign("parent", "child", "parser", "limited test", "fixture-assignment")
    result = engine.call("child", request("status", work))
    assert "contract" not in result and "state" not in result
    assert result["child_scope"].startswith("status_only")
    with pytest.raises(PermissionError):
        engine.call("child", request("history", work))
    with pytest.raises(PermissionError):
        engine.set_mode("child", "on", 0, "child")
