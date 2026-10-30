"""Actual core unwrapping and explicit metadata rules, never a broad dispatcher grant."""
import copy
import json
from types import SimpleNamespace
import pytest
from bnn_intake.policy import action_rules, extend_policy, TOOLS
from test_analysis import analytical_native, native, plan_method, product_call, response_for, generate, submit
from test_native_context import turn
from hermes_cli.middleware import run_tool_execution_middleware
from hermes_cli.execution_control import ExecutionControlDenied


def test_native_deferred_product_matches_the_core_unwrapped_call(analytical_native):
    n = analytical_native; plan_method(n.engine, n.work, select=True)
    args = product_call(n)
    bridge_args = {"calls": [{"name": "intake_analysis", "arguments": args}]}
    response = response_for(args)
    function = response["choices"][0]["message"]["tool_calls"][0]["function"]
    function.update(name="tool_call", arguments=json.dumps(bridge_args))
    generate(n, args, callback=lambda outgoing: response)
    from agent.tool_executor import _parse_tool_call
    parsed = _parse_tool_call(SimpleNamespace(enabled_toolsets=["bnn_intake"], disabled_toolsets=[]),
        SimpleNamespace(id="product-call", function=SimpleNamespace(name="tool_call", arguments=json.dumps(bridge_args))))
    assert parsed.name == "intake_analysis" and parsed.args == args and not parsed.scope_block
    assert submit(n, parsed.args)["ok"]
    saved = n.engine.analysis.state("parent", n.work)["analysis_products"]["P1"]
    assert saved["producer"]["wire_name"] == "tool_call"


def test_metadata_discovery_uses_original_limits_and_does_not_grant_dispatch(analytical_native):
    n = analytical_native; turn(n)
    import model_tools
    from tools.tool_search import dispatch_tool_describe
    definitions = model_tools.get_tool_definitions(enabled_toolsets=["bnn_intake"], quiet_mode=True, skip_tool_search_assembly=True)
    identity = {"session_id": "parent", "turn_id": "native-turn", "api_request_id": "metadata-api", "tool_call_id": "metadata-call", "platform": "cli"}
    args = {"names": ["intake_analysis"]}
    value = json.loads(run_tool_execution_middleware("tool_describe", args,
        lambda current: dispatch_tool_describe(current, current_tool_defs=definitions), **identity))
    assert "intake_analysis" in value["tools"]
    assert not any(r["tool"] == "tool_call" for r in action_rules())
    bad = {"names": ["intake_analysis"] * 11}
    with pytest.raises(ExecutionControlDenied):
        run_tool_execution_middleware("tool_describe", bad, lambda current: "must not execute",
            **{**identity, "tool_call_id": "too-many-schema-names"})


def test_existing_discovery_policy_is_extended_without_ambiguous_duplicates():
    base = [{"id": "existing.describe", "tool": "tool_describe", "effect": "read_local", "roles": ["parent", "G"],
             "arguments_schema": {"type": "object", "additionalProperties": False, "properties": {"names": {
                 "type": "array", "minItems": 1, "maxItems": 10, "items": {"enum": ["existing_tool"]}}}, "required": ["names"]}}]
    original = copy.deepcopy(base)
    result = extend_policy(base)
    describe = [r for r in result if r["tool"] == "tool_describe"]
    assert len(describe) == 1 and base == original
    assert set(describe[0]["arguments_schema"]["properties"]["names"]["items"]["enum"]) == set(TOOLS) | {"existing_tool"}
    assert describe[0]["arguments_schema"]["properties"]["names"]["maxItems"] == 10
    assert not any(r["tool"] == "tool_call" for r in result)


def test_actual_cold_probe_catalogue_arguments_work_through_native_bridge(analytical_native):
    n = analytical_native; turn(n)
    from agent.tool_executor import _parse_tool_call
    args = {"action": "search", "query": "измерение", "limit": 12, "offset": 0}
    wrapper = {"calls": [{"name": "intake_methods", "arguments": args}]}
    parsed = _parse_tool_call(SimpleNamespace(enabled_toolsets=["bnn_intake"], disabled_toolsets=[]),
        SimpleNamespace(id="catalogue-regression", function=SimpleNamespace(name="tool_call", arguments=json.dumps(wrapper))))
    assert parsed.name == "intake_methods" and not parsed.scope_block
    identity = {"session_id": "parent", "turn_id": "native-turn", "api_request_id": "catalogue-api", "tool_call_id": "catalogue-regression", "platform": "cli"}
    value = json.loads(run_tool_execution_middleware(parsed.name, parsed.args,
        lambda current: n.ctx.dispatch_tool(parsed.name, current, **identity), **identity))
    assert "matches" in value and value["offset"] == 0
