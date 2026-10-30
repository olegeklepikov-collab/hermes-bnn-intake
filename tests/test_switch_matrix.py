"""Eight mechanical switch combinations; no optional executor claimed here."""
import itertools
import pytest
from hermes_cli.execution_control import ExecutionControlDenied
from test_native_context import native
from test_delegation import delegated,start


@pytest.mark.parametrize("intake_on,gsd_on,research_on",list(itertools.product([False,True],repeat=3)))
def test_eight_switch_combinations_keep_native_base_launch_and_protection(delegated,intake_on,gsd_on,research_on):
    n=delegated
    n.host.config["modules"]={"gsd":{"files":{}},"research":{"files":{}}}
    p=n.host.admission.policy("parent",create=True)
    n.host.admission.revise_policy("parent",p["revision"],"auto" if gsd_on else "off",research_on)
    if not intake_on:n.engine.set_mode("parent","off",n.engine.mode("parent")["revision"],"owner")
    child,_=start(n)
    assert n.host.store.binding(child)["role"]=="analyst"
    status=n.host.modules.status("parent")
    assert status["GSD_mode"]==("auto" if gsd_on else "off")
    assert status["UDR_and_Foundation_enabled"]==research_on and status["base_protection_enabled"]
    if intake_on:assert n.engine.delegation.child_packet(child)["restrictions"]["delegate_again"] is False


def test_all_optional_switches_off_cannot_disable_mandatory_launch_control(delegated):
    n=delegated
    p=n.host.admission.policy("parent",create=True);n.host.admission.revise_policy("parent",p["revision"],"off",False)
    n.engine.set_mode("parent","off",n.engine.mode("parent")["revision"],"owner")
    n.controls[4].dispose()
    with pytest.raises(ExecutionControlDenied):start(n)
