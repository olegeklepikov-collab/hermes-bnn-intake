"""Strict action contracts. Descriptors are data, never executable instructions."""
from jsonschema import Draft202012Validator
import copy

PROFILES = ("eMVP", "MVP", "Standard", "eStandard", "High Stakes", "Critical Infrastructure")
ID = {"type": "string", "minLength": 1, "maxLength": 160, "pattern": "^[A-Za-z0-9_.:-]+$"}
TEXT = {"type": "string", "minLength": 1}
REV = {"type": "integer", "minimum": 0}


def obj(properties, required=None):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required, "additionalProperties": False}


def array(items, minimum=0):
    return {"type": "array", "items": items, "minItems": minimum, "uniqueItems": True}


HASH = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
RECORD_REF = obj({"kind": {"const": "record"}, "id": ID, "revision": {"type": "integer", "minimum": 1}})
DOCUMENT_REF = obj({"kind": {"const": "document_unit"}, "corpus_id": ID, "doc_id": ID,
                    "unit": REV, "start": REV, "end": REV, "source_sha256": HASH,
                    "unit_sha256": HASH,
                    "phase": {"enum": ["read", "plan", "collect", "analyse", "verify", "report", "G", "S", "D"]}})
RESEARCH_REF = obj({"kind": {"const": "research_result"}, "attempt_id": ID,
                    "receipt_sha256": HASH, "start": REV, "end": REV})
SOURCE_REF = {"oneOf": [RECORD_REF, DOCUMENT_REF, RESEARCH_REF]}
INPUT_REF = obj({"kind": {"const": "native_input"}, "id": ID,
                 "start": {"type": "integer", "minimum": 0},
                 "end": {"type": "integer", "minimum": 1}})
METHOD = obj({"id": ID, "name": TEXT, "kind": TEXT, "purpose": TEXT,
              "operation": TEXT, "required_inputs": array(TEXT, 1), "output": TEXT,
              "assumptions": array(TEXT), "limitations": array(TEXT),
              "source_refs": array(TEXT), "origin": {"enum": ["catalogue_adaptation", "external", "authorial"]}})
CONTRACT = obj({"goal": TEXT, "result": TEXT, "domain": TEXT, "object": TEXT,
                "scope": TEXT, "period": TEXT, "units": array(TEXT, 1),
                "profile": {"enum": list(PROFILES)}, "constraints": array(TEXT),
                "sufficiency": array(TEXT, 1)})
GAP = obj({"id": ID, "question": TEXT, "kind": {"enum": ["fact", "definition", "measurement", "relation", "method", "context", "preference", "permission"]},
           "material": {"type": "boolean"}, "affected": array(TEXT, 1), "required_result": TEXT})
PATH = obj({"id": ID, "gap_id": ID, "family": TEXT, "purpose": TEXT,
            "state": {"enum": ["planned", "not_applicable", "prohibited", "unavailable"]},
            "reason": TEXT})
EXPOSITION = obj({"id": ID, "purpose": TEXT, "question": TEXT,
                  "input_refs": array(SOURCE_REF), "depends_on": array(ID),
                  "method_ids": array(ID)})
DECISION = obj({"id": ID, "purpose": TEXT,
                "state": {"enum": ["sufficient", "limited", "resource_paused"]},
                "remaining": array(TEXT), "limits": array(TEXT), "next_step": TEXT})


def tagged(action, extra=None, mutating=False):
    properties = {"schema_version": {"const": 1}, "action": {"const": action}, "work_ref": ID}
    if mutating:
        properties.update(expected_revision=REV, operation_id=ID)
    properties.update(extra or {})
    return obj(properties)


CHANGES = [
    obj({"type": {"const": "add_gap"}, "gap": GAP}),
    obj({"type": {"const": "plan_path"}, "path": PATH}),
    obj({"type": {"const": "add_method"}, "descriptor": METHOD}),
    obj({"type": {"const": "record_response"}, "gap_id": ID,
         "response_state": {"enum": ["answered", "partial", "unknown", "no_data", "refused", "skipped", "not_applicable", "contradiction"]},
         "source_ref": INPUT_REF}),
]
WORK_ACTIONS = {
    "prepare": tagged("prepare", {"contract": CONTRACT}, True),
    "expose": tagged("expose", {"exposition": EXPOSITION}, True),
    "revise": tagged("revise", {"change": {"oneOf": CHANGES}}, True),
    "assess": tagged("assess", {"assessment": DECISION}, True),
    "pause": tagged("pause", {"reason": TEXT}, True),
    "resume": tagged("resume", {"reason": TEXT}, True),
    "cancel": tagged("cancel", {"reason": TEXT}, True),
    "status": tagged("status"),
    "profile_status": tagged("profile_status"),
    "acceptance_status": tagged("acceptance_status"),
    "acceptance_report": tagged("acceptance_report"),
    "adopt_profile": tagged("adopt_profile", {"reason": TEXT}, True),
    "history": tagged("history"),
    "handoff": tagged("handoff", {"exposition_id": ID}),
    "select_context": tagged("select_context", {"exposition_id": {"oneOf": [ID, {"type": "null"}]}}, True),
}
QUESTION = obj({"id": ID, "gap_id": ID, "topic": TEXT, "purpose": TEXT, "text": TEXT})
QUESTION_ACTIONS = {
    "status": tagged("status"),
    "plan_question": tagged("plan_question", {"question": QUESTION}, True),
    "present_question": tagged("present_question", {"question_id": ID}, True),
}
LIMITS = obj({"max_questions": {"oneOf": [{"type": "integer", "minimum": 0}, {"type": "null"}]},
              "blocked_topics": array(TEXT),
              "deadline": {"oneOf": [{"type": "number", "exclusiveMinimum": 0}, {"type": "null"}]}}, [])
LIMITS["minProperties"] = 1
INTERVIEW_CONTROLS = {
    action: obj({"action": {"const": action}, "work_ref": ID, "expected_revision": REV,
                 "operation_id": ID, **extra})
    for action, extra in [("pause", {}), ("stop", {}), ("resume", {}),
                           ("limit", {"limits": LIMITS}), ("allow_reask", {"gap_id": ID})]
}
METHOD_ACTIONS = {
    "classification": obj({"action": {"const": "classification"}, "dimension": {"enum": ["kinds", "families", "axes"]},
                           "ids": array(TEXT), "offset": REV, "limit": {"type": "integer", "minimum": 1, "maximum": 24}}),
    "read": obj({"action": {"const": "read"}, "ids": array(ID, 1)}),
    "search": obj({"action": {"const": "search"}, "query": TEXT,
                   "family": {"type": "string"}, "offset": REV,
                   "limit": {"type": "integer", "minimum": 1, "maximum": 12}}, ["action", "query"]),
}
DOCUMENT_ACTIONS = {
    "bind_corpus": tagged("bind_corpus", {"corpus_id": ID}, True),
    "plan": tagged("plan", {"corpus_id": ID, "fragment_characters": {"type": "integer", "minimum": 1, "maximum": 65536},
                           "offset": REV, "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                           "basis": {"oneOf": [HASH, {"type": "null"}]}}),
    "coverage": tagged("coverage", {"corpus_id": ID}),
}

# The method/axis/level/relation vocabularies remain open. Envelopes and effects
# are strict; a new analytical name does not authorize code or a data source.
OBLIGATION = obj({"id": ID, "description": TEXT, "level": TEXT})
APPLICATION = obj({"id": ID, "exposition_id": ID, "method_id": ID,
                   "purpose": TEXT, "selection_reason": TEXT, "axes": array(TEXT, 1), "families": array(TEXT, 1),
                   "classification_extensions": array(obj({"dimension": {"enum": ["kinds", "families", "axes"]},
                       "id": TEXT, "definition": TEXT, "related_to": array(TEXT), "relation_reason": TEXT})),
                   "trace": {"enum": ["custom", "concept_measurement", "method_foundation"]},
                   "level_definitions": array(obj({"id": ID, "meaning": TEXT}), 1),
                   "operations": array(TEXT, 1), "unit_of_analysis": TEXT,
                   "expected_product": TEXT, "acceptance_checks": array(OBLIGATION, 1),
                   "assumptions": array(TEXT), "limitations": array(TEXT),
                   "product_dependencies": array(obj({"product_id": ID, "sha256": HASH})),
                   "perspectives_not_selected": array(obj({"perspective": TEXT, "reason": TEXT}))})
CITATION = {"oneOf": [
    obj({"kind": {"const": "text"}, "source_id": ID, "start": REV, "end": REV, "quote": TEXT}),
    obj({"kind": {"const": "image"}, "source_id": ID, "image_sha256": HASH, "locator": TEXT}),
    obj({"kind": {"const": "product"}, "product_id": ID, "sha256": HASH, "node_id": ID})]}
NODE = obj({"id": ID, "kind": TEXT, "level": TEXT, "statement": TEXT,
            "context": TEXT, "unit_of_analysis": TEXT,
            "epistemic_status": {"enum": ["observation", "inference", "hypothesis", "proposal", "unknown"]},
            "citations": array(CITATION), "limitations": array(TEXT)})
EDGE = obj({"from": ID, "to": ID, "relation": TEXT, "meaning": TEXT,
            "epistemic_status": {"enum": ["inference", "hypothesis", "proposal", "unknown"]}})
PRODUCT = obj({"id": ID, "application_id": ID, "input_fingerprint": HASH,
               "nodes": array(NODE), "edges": array(EDGE),
               "checks": array(obj({"obligation_id": ID, "state": {"enum": ["addressed", "unresolved", "not_applicable"]},
                                    "node_ids": array(ID), "reason": TEXT}), 1),
               "uncovered": array(TEXT), "assumptions": array(TEXT), "limitations": array(TEXT)})
PRODUCT['properties']['profile_checks']=array(obj({'obligation_id':ID,'state':{'enum':['addressed','unresolved','not_applicable']},
                                                  'node_ids':array(ID),'reason':TEXT}))
ANALYSIS_ACTIONS = {
    "plan_method": tagged("plan_method", {"application": APPLICATION}, True),
    "select_application": tagged("select_application", {"application_id": {"oneOf": [ID, {"type": "null"}]}}, True),
    "submit_product": tagged("submit_product", {"product": PRODUCT}, True),
    "status": tagged("status", {"application_id": ID}),
    "read_product": tagged("read_product", {"product_id": ID}),
}
ANALYSIS_ACTIONS["status"]["properties"].update(offset=REV, limit={"type": "integer", "minimum": 1, "maximum": 100})

RESEARCH_ACTIONS = {
    "select_path": tagged("select_path", {"path_id": {"oneOf": [ID, {"type": "null"}]},
                      "tool_name": {"oneOf": [ID, {"type": "null"}]}}, True),
    "status": tagged("status", {"path_id": ID, "offset": REV,
                "limit": {"type": "integer", "minimum": 1, "maximum": 100}}),
    "read_result": tagged("read_result", {"attempt_id": ID, "receipt_sha256": HASH,
                "offset": REV, "limit": {"type": "integer", "minimum": 1, "maximum": 65536}}),
    "completion": tagged("completion"),
    "review_path": tagged("review_path",{'path_id':ID,'state':{'enum':['performed','no_result','unavailable','prohibited','not_applicable','incomplete']},
        'reason':TEXT,'receipts':array(obj({'attempt_id':ID,'receipt_sha256':HASH})),
        'source_refs':array(SOURCE_REF),'reading_level':TEXT,'remaining':array(TEXT)},True),
    "review_gap": tagged("review_gap",{'gap_id':ID,'state':{'enum':['resolved','partial','blocked']},
        'reason':TEXT,'path_ids':array(ID,1),'source_refs':array(SOURCE_REF),'remaining':array(TEXT)},True),
}
RESEARCH_ACTIONS["status"]["required"] = ["schema_version", "action", "work_ref"]
RESEARCH_ACTIONS["read_result"]["required"] = ["schema_version", "action", "work_ref", "attempt_id", "receipt_sha256"]

HANDOFF = obj({"id": ID, "module": {"enum": ["base", "research", "gsd"]},
               "role": {"enum": ["collector", "analyst", "reviewer", "parser", "computation", "G", "S", "D"]},
               "native_task_id": {"oneOf": [ID, {"type": "null"}]}, "purpose": TEXT,
               "exposition_id": ID, "application_id": {"oneOf": [ID, {"type": "null"}]}, "gap_ids": array(ID)})
NATIVE_ORIGIN = {"oneOf": [
    obj({"module": {"enum": ["base", "research"]}, "task_id": ID, "native_revision": {"type": "integer", "minimum": 1}}),
    obj({"module": {"const": "gsd"}, "job_id": ID})]}
DELEGATION_ACTIONS = {
    "plan_handoff": tagged("plan_handoff", {"handoff": HANDOFF}, True),
    "status": tagged("status"),
    "read_result": tagged("read_result", {"native_task_id": ID, "native_revision": {"type": "integer", "minimum": 1},
        "offset": REV, "limit": {"type": "integer", "minimum": 1, "maximum": 65536}}),
    "read_gsd_result": tagged("read_gsd_result", {"job_id": ID, "offset": REV,
        "limit": {"type": "integer", "minimum": 1, "maximum": 4096}}),
    "bind_research": tagged("bind_research", {"run_id": ID, "exposition_id": ID}, True),
    "read_research_basis": tagged("read_research_basis", {"run_id": ID}),
    "publish_acceptance": tagged("publish_acceptance", {"run_id": ID},True),
    "finish_research": tagged("finish_research", {'run_id':ID,'report':TEXT,'request_id':ID},True),
    "capture_product": tagged("capture_product", {"origin": NATIVE_ORIGIN, "product_sha256": HASH,
        "recipient_roles": array({"enum": ["collector", "analyst", "reviewer", "parser", "computation", "G", "S", "D"]}, 1)}, True),
    "read_capture": tagged("read_capture", {"capture_id": ID}),
}
DELEGATION_ACTIONS["read_result"]["required"] = ["schema_version", "action", "work_ref", "native_task_id", "native_revision"]
DELEGATION_ACTIONS["read_gsd_result"]["required"] = ["schema_version", "action", "work_ref", "job_id"]


def validate(args, actions=WORK_ACTIONS):
    if not isinstance(args, dict) or args.get("action") not in actions:
        raise ValueError("intake_action_unknown")
    errors = list(Draft202012Validator(actions[args["action"]]).iter_errors(args))
    if errors:
        first = errors[0]
        raise ValueError("intake_contract_invalid:" + "/".join(map(str, first.absolute_path)))
    return args


def tool_schema(name, description, actions):
    properties = {}
    for variant in actions.values():
        for key, value in variant["properties"].items():
            if key not in properties:
                properties[key] = copy.deepcopy(value)
            elif properties[key] != value:
                properties[key] = {"anyOf": [properties[key], copy.deepcopy(value)]}
    properties["action"] = {"type": "string", "enum": list(actions)}
    required = set.intersection(*(set(variant["required"]) for variant in actions.values()))
    return {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties, "required": sorted(required),
                           "additionalProperties": False, "oneOf": list(actions.values())}}
