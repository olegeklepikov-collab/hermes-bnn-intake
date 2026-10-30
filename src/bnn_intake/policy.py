"""Exact proposed common-action rules; installation never happens on import."""
from .contracts import ANALYSIS_ACTIONS, DELEGATION_ACTIONS, DOCUMENT_ACTIONS, METHOD_ACTIONS, QUESTION_ACTIONS, RESEARCH_ACTIONS, WORK_ACTIONS, tool_schema
from .engine import MUTATIONS
from .contracts import array, obj, TEXT
import copy

TOOLS = ["intake_work", "intake_methods", "intake_questions", "intake_documents", "intake_analysis", "intake_research", "intake_delegation"]


def action_rules(*, discovery=True):
    rules = []
    for tool, actions in [("intake_work", WORK_ACTIONS), ("intake_methods", METHOD_ACTIONS), ("intake_questions", QUESTION_ACTIONS), ("intake_documents", DOCUMENT_ACTIONS), ("intake_analysis", ANALYSIS_ACTIONS), ("intake_research", RESEARCH_ACTIONS), ("intake_delegation", DELEGATION_ACTIONS)]:
        for action, schema in actions.items():
            write = action in MUTATIONS or action in {"plan_question", "present_question", "bind_corpus", "plan_method", "select_application", "submit_product", "select_path", "plan_handoff", "bind_research", "capture_product", "review_path", "review_gap", "publish_acceptance", "finish_research"}
            rules.append({"id": "intake." + tool + "." + action, "tool": tool,
                          "effect": "write_local" if write else "read_local", "roles": ["parent"],
                          "arguments_schema": schema})
    if discovery:
        rules += [
            {"id": "intake.discovery.describe", "tool": "tool_describe", "effect": "read_local", "roles": ["parent"],
             "arguments_schema": obj({"names": {"type": "array", "minItems": 1, "maxItems": 10, "items": {"enum": TOOLS}}})},
            {"id": "intake.discovery.search", "tool": "tool_search", "effect": "read_local", "roles": ["parent"],
             "arguments_schema": obj({"queries": {"type": "array", "minItems": 1, "maxItems": 4, "items": TEXT},
                                      "limit": {"type": "integer", "minimum": 1, "maximum": 10}}, ["queries"])}]
    return rules


def extend_policy(base):
    """Operator-only builder for the pinned simple metadata rules; no live writes.

    Keep the original restrictions and effect roles. Never permit an arbitrary
    tool_call dispatcher: the core unwraps local calls before target admission.
    """
    rules = [copy.deepcopy(r) for r in base if not r["id"].startswith("intake.")]
    # Exact pinned status branches are read-only; an error is not an unknown write.
    for rule in list(rules):
        if rule['tool'] not in {'work_team','udr_team','udr_workspace'} or rule['effect']!='write_local':continue
        action=rule.get('arguments_schema',{}).get('properties',{}).get('action',{})
        if 'status' not in action.get('enum',[]):continue
        read_rule=copy.deepcopy(rule);read_rule['id']=rule['id']+'.readonly_status';read_rule['effect']='read_local'
        read_rule['arguments_schema']['properties']['action']={'const':'status'}
        action['enum']=[x for x in action['enum'] if x!='status'];rules.append(read_rule)
    describe = [r for r in rules if r["tool"] == "tool_describe" and "parent" in r["roles"]]
    if len(describe) > 1:
        raise ValueError("intake_discovery_policy_adapter_requires_one_parent_rule")
    metadata = {r["tool"]: r for r in action_rules() if r["id"].startswith("intake.discovery.")}
    if describe:
        schema = describe[0]["arguments_schema"]
        names = schema.get("properties", {}).get("names", {}).get("items", {}).get("enum")
        if not isinstance(names, list):
            from jsonschema import Draft202012Validator
            if not Draft202012Validator(schema).is_valid({"names": TOOLS}):
                raise ValueError("intake_unknown_discovery_schema_requires_qualification")
        else:
            schema["properties"]["names"]["items"]["enum"] = sorted(set(names) | set(TOOLS))
    else:
        rules.append(metadata["tool_describe"])
    if not any(r["tool"] == "tool_search" and "parent" in r["roles"] for r in rules):
        rules.append(metadata["tool_search"])
    return rules + action_rules(discovery=False)
