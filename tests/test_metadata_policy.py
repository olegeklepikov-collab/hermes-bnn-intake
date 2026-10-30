"""Mixed metadata queries use one native route; effect grants stay separate."""
import json
from pathlib import Path
import pytest
from conftest import BASELINE
from test_native_context import native,turn
from bn_memory.action_control import ActionControl
from bnn_intake.policy import extend_policy,action_rules


def test_native_mixed_metadata_has_one_unambiguous_rule_without_effect_grants(native):
    n=native
    reference=json.loads((Path(BASELINE["hermes_core"]).parent/"Memory/settings.json").read_text())
    rules=[r for r in reference["tool_policy"]["rules"] if r["id"] in {"w3.discovery.describe","w3.discovery.search"}]
    # Also support the prior 0.8 shape with redundant intake rules present.
    n.host.config["tool_policy"]["rules"]=extend_policy(rules+action_rules())
    n.host.actions=ActionControl(n.host);turn(n)
    rule,_=n.host.actions._permission("parent","native-turn","tool_describe",{"names":["intake_delegation","udr_workspace","gsd_research","mcp__context__memory_read"]})
    assert rule["id"]=="w3.discovery.describe"
    rule,_=n.host.actions._permission("parent","native-turn","tool_search",{"queries":["intake_delegation","gsd_research"],"limit":1})
    assert rule["id"]=="w3.discovery.search"
    with pytest.raises(PermissionError):n.host.actions._permission("parent","native-turn","tool_search",{"queries":["a"]*5,"limit":1})
    with pytest.raises(PermissionError):n.host.actions._permission("parent","native-turn","udr_workspace",{"action":"read_result"})
