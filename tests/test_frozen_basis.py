"""Past handoffs keep exact Foundation references; new launches use current plans."""
import pytest
from conftest import request
from test_native_context import native
from test_delegation import delegated,start
from bnn_intake.storage import read


def change_basis(n,reference,operation,revision):
    return n.engine.commit("parent",request("fixture-basis",n.work,revision,operation),lambda db,state:(dict(state,research_basis=reference),{}))


def test_new_basis_does_not_rewrite_old_assigned_child_packet(delegated):
    n=delegated;child,_=start(n);old=n.engine.delegation.child_packet(child)
    change_basis(n,{"r-test":{"object_id":"IBS-new","source":"fixture only"}},"new-basis",5)
    assert n.engine.delegation.child_packet(child)==old
    with pytest.raises(Exception,match="needs_new_handoff_plan"):start(n)


def test_legacy_08_handoff_recovers_exact_first_revision_not_current_basis(delegated):
    n=delegated
    # This private fixture represents the old 0.8 shape; recompute its revision
    # integrity so that the test exercises supported source history, not corruption.
    from bnn_intake.storage import canonical,digest
    with n.host.store.db() as db:
        for row in db.execute("SELECT revision FROM intake_revisions WHERE work_id=?",(n.work,)).fetchall():
            state=read(db,n.engine.profile,n.work,row[0])
            for plan in state.get("handoff_plans",{}).values():plan.pop("basis_refs",None)
            db.execute("UPDATE intake_revisions SET body=?,sha256=? WHERE work_id=? AND revision=?",(canonical(state),digest(state),n.work,row[0]))
    child,_=start(n);old=n.engine.delegation.child_packet(child)
    with n.host.store.db() as db:
        row=db.execute("SELECT body FROM intake_delegations WHERE child_sid=?",(child,)).fetchone()
        import json
        binding=json.loads(row[0]);binding.pop("basis_refs",None)
        db.execute("UPDATE intake_delegations SET body=?,sha256=? WHERE child_sid=?",(canonical(binding),digest(binding),child))
    change_basis(n,{"r-test":{"object_id":"IBS-later"}},"later-basis",5)
    assert n.engine.delegation.child_packet(child)==old
