"""Contract/race fixtures; actual Foundation/Dolt/UDR is a separate operator probe."""
import copy
import json
from types import SimpleNamespace
import pytest
from conftest import request
from test_native_context import native
from test_delegation import delegated
from bnn_intake.research_basis import ResearchBasis, unpack_basis
from bnn_intake.storage import canonical, digest


@pytest.fixture
def basis(delegated):
    n = delegated; n.host.config.setdefault("modules", {})["research"] = {"files": {}}
    card, _ = n.host.working._card("parent")
    body = {"bead": json.loads(card["primary_ref"])["id"], "project": "p", "host_run": "HOST-fixture", "binding": {}}
    class StateFixture:
        def __init__(self):self.objects = {}; self.writes = 0
        def get(self, args):
            return copy.deepcopy(self.objects.get(args["object_id"], {"status": "not_found"}))
        def put(self, args):
            assert args["content_hash"] == digest(args["object"])
            self.writes += 1
            self.objects[args["object_id"]] = {"status": "found", "object": copy.deepcopy(args["object"]),
                "object_ref": {"revision": 1, "schema_id": args["schema_id"], "content_hash": args["content_hash"]}}
            return {"status": "committed", "commit_created": True, "fixture_not_actual_Dolt": True}
    def load(sid, run):
        if run != "r-fixture":raise PermissionError("owned_research_run_required")
        return {"sid": "parent", "state": "active"}, body
    n.foundation = StateFixture()
    n.host._research_module = SimpleNamespace(load=load, state=n.foundation,
        session=lambda sid, value: SimpleNamespace(restore=lambda binding: None, check=lambda: {}))
    n.bridge = ResearchBasis(n.engine.delegation)
    n.args = request("bind_research", n.work, 5, "bind-research-fixture", run_id="r-fixture", exposition_id="E-child")
    n.native_body = body
    return n


def test_research_basis_uses_existing_native_work_and_idempotent_Foundation_reference(basis):
    n = basis; first = n.bridge.bind("parent", n.args)
    again = n.bridge.bind("parent", n.args)
    assert first["research_basis"] == again["research_basis"] and again["replayed"]
    assert n.foundation.writes == 1
    read = n.bridge.read("parent", n.work, "r-fixture")
    assert read["basis"]["contract"]["profile"] == "Standard"
    assert read["source_rights_rechecked"] and not read["scientific_acceptance"]


def test_research_basis_rejects_another_primary_Bead_before_Foundation_write(basis):
    n = basis; n.native_body["bead"] = "other-bead"
    with pytest.raises(PermissionError, match="primary_work_mismatch"):n.bridge.bind("parent", n.args)
    assert not n.foundation.writes


def test_research_basis_rechecks_revoked_source_before_write_and_read(basis):
    n = basis; n.bridge.bind("parent", n.args)
    n.host.store.retract("parent", n.ref["id"], 1)
    with pytest.raises(PermissionError):n.bridge.read("parent", n.work, "r-fixture")
    with pytest.raises(PermissionError):n.bridge.bind("parent", n.args)
    assert n.foundation.writes == 1


def test_research_basis_cannot_silently_follow_a_changed_Foundation_object(basis):
    n = basis; bound = n.bridge.bind("parent", n.args)
    stored = n.foundation.objects[bound["research_basis"]["object_id"]]
    stored["object_ref"]["revision"] += 1
    with pytest.raises(PermissionError, match="version_changed"):n.bridge.read("parent", n.work, "r-fixture")


def test_research_basis_failed_local_CAS_leaves_only_an_unreferenced_object(basis, monkeypatch):
    n = basis
    def lost(*args, **kw):raise ValueError("intake_work_revision_conflict")
    monkeypatch.setattr(n.engine, "commit", lost)
    with pytest.raises(ValueError, match="revision_conflict"):n.bridge.bind("parent", n.args)
    assert len(n.foundation.objects) == 1
    assert not n.engine.status("parent", n.work)["state"].get("research_basis")


def test_disabled_UDR_does_not_remove_intake_or_allow_research_basis_use(basis):
    n = basis
    p = n.host.admission.policy("parent", create=True)
    n.host.admission.revise_policy("parent", p["revision"], p["gsd_mode"], False)
    with pytest.raises(PermissionError, match="disabled_for_session"):n.bridge.bind("parent", n.args)
    assert n.engine.status("parent", n.work)["state"]["contract"]["profile"] == "Standard"


def test_research_handoff_cannot_be_planned_without_actual_native_run(delegated):
    n = delegated
    spec = {**n.spec, "id": "H-research", "module": "research"}
    with pytest.raises(PermissionError, match="active_native_research"):
        n.engine.delegation.call("parent", request("plan_handoff", n.work, 5, "premature-research-handoff", handoff=spec))
    assert n.engine.status("parent", n.work)["state"]["revision"] == 5


def test_research_handoff_requires_saved_Foundation_basis_before_planning(basis, monkeypatch):
    n = basis
    n.host._research_module.active = lambda sid: ({"id": "r-fixture"}, n.native_body)
    monkeypatch.setattr(n.host.modules, "research", lambda sid: n.host._research_module)
    spec = {**n.spec, "id": "H-research", "module": "research"}
    args = request("plan_handoff", n.work, 5, "requires-basis", handoff=spec)
    with pytest.raises(PermissionError, match="bind_native_research_basis_first"):n.engine.delegation.call("parent", args)
    n.engine.delegation.call("parent", n.args)
    value = n.engine.delegation.call("parent", {**args, "expected_revision": 6})
    assert value["revision"] == 7


def test_Foundation_envelope_preserves_float_geometry_exactly(basis, monkeypatch):
    n = basis; original = n.bridge.basis
    def with_geometry(*a, **kw):
        packet = original(*a, **kw)
        packet["synthetic_geometry"] = [0.0, 0.0, 520.0, 680.0, 0.125]
        return packet
    monkeypatch.setattr(n.bridge, "basis", with_geometry)
    bound = n.bridge.bind("parent", n.args)
    stored = n.foundation.objects[bound["research_basis"]["object_id"]]["object"]
    assert stored["contract"] == "BNNIntakeBasisV2" and "basis" not in stored
    assert '[0.0,0.0,520.0,680.0,0.125]' in stored["basis_json"]
    read = n.bridge.read("parent", n.work, "r-fixture")
    assert all(type(v) is float for v in read["basis"]["synthetic_geometry"])
    assert canonical(read["basis"]) == stored["basis_json"]


@pytest.mark.parametrize("encoded", ['{"x":520}', '{ "x":520.0}', '{"x":520.0,"x":520.0}', '{"x":NaN}'])
def test_Foundation_exact_encoding_rejects_numeric_change_noncanonical_and_nonfinite(encoded):
    with pytest.raises(PermissionError):
        unpack_basis({"contract": "BNNIntakeBasisV2", "basis_json": encoded, "basis_sha256": digest({"x": 520.0})})


def test_old_V1_basis_is_read_without_invented_migration():
    original = {"contract": {"profile": "Standard"}, "source_packet": {"records": []}}
    assert unpack_basis({"contract": "BNNIntakeBasisV1", "basis": original}) == original
