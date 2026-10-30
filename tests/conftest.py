"""Real BNN primary store, isolated namespace and files; no live credentials."""
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/"test_support"))
from native_config import load
BASELINE = load(ROOT)
sys.path[:0] = [str(ROOT / "src"), BASELINE["memory_release"], BASELINE["hermes_core"]]
import bn_memory

if Path(bn_memory.__file__).resolve().parent.parent != Path(BASELINE["memory_release"]):
    raise RuntimeError("test_wrong_bn_memory_import")

import pytest
from bn_memory.store import Store
from bnn_intake.storage import install
from bnn_intake.engine import Intake


@pytest.fixture
def fixture(tmp_path):
    project = tmp_path / "project"; project.mkdir()
    config = {"namespace": {"owner_id": "intake-test-owner", "profile_id": "intake-test"},
              "profile_home": str(tmp_path / "profile"), "state_root": str(tmp_path / "state"),
              "default_project": "p", "projects": {"p": str(project)},
              "working_beads": {"fixture": True}, "intake_context_bytes": 32768}
    store = Store(config["state_root"], config)
    store.initialize(); store.bind("parent")
    # A1 fixture of an already bound Beads work. No Beads execution is claimed.
    work = "WORK-intake-test"
    with store.db() as db:
        db.execute("INSERT INTO working_cards VALUES(?,?,?,?)", (store.profile, work, "p", json.dumps({"id": "fixture-bead", "source": "A1 synthetic fixture"})))
        db.execute("INSERT INTO working_bindings VALUES(?,?,?)", (store.profile, "parent", work))
    install(store)
    engine = Intake(store)
    engine.set_mode("parent", "on", 0, "authenticated-test-owner")
    return store, engine, work


@pytest.fixture
def contract(request):
    return {"goal": "Исследовать изменение 20→30", "result": "Ограниченный проверяемый вывод",
            "domain": "Синтетический учёт", "object": "Показатель", "scope": "Один случай",
            "period": "Два состояния", "units": ["наблюдение"], "profile": getattr(request, "param", "Standard"),
            "constraints": ["Не устанавливать причинность по двум значениям"],
            "sufficiency": ["Указать источник и границы вывода"]}


def request(action, work, revision=None, operation=None, **extra):
    out = {"schema_version": 1, "action": action, "work_ref": work, **extra}
    if revision is not None:
        out.update(expected_revision=revision, operation_id=operation)
    return out


def prepare(engine, work, contract):
    return engine.call("parent", request("prepare", work, 0, "prepare-1", contract=contract))


def record(store, name="source-1", content="Atlas: before=20, after=30. Original records are in SQLite."):
    return store.record("parent", {"content": content, "source_ref": "synthetic:" + name,
                         "kind": "note", "request_id": name, "scope": "project"})
