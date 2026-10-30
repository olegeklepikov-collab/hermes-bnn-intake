"""Capture the existing Host.execution result; no second model runner or budget.

Only returned public tool calls are projected. Hidden reasoning and credentials
are neither requested nor stored. An admitted call is not a returned outcome.
"""
from contextvars import ContextVar
import json
import time
import uuid
from .storage import canonical, digest, verify

CURRENT = ContextVar("bnn_intake_model_outcome", default=None)


def field(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def projection(response):
    choices = field(response, "choices")
    if isinstance(choices, (list, tuple)) and choices:
        finish = field(choices[0], "finish_reason")
        if finish not in {"stop", "tool_calls", "function_call"}:
            return "returned_incomplete", {}, {}
        calls = field(field(choices[0], "message"), "tool_calls", []) or []
        calls = [(field(c, "id"), field(field(c, "function"), "name"), field(field(c, "function"), "arguments")) for c in calls]
    elif isinstance(field(response, "output"), (list, tuple)):
        if field(response, "status") != "completed":
            return "returned_incomplete", {}, {}
        calls = [(field(c, "call_id"), field(c, "name"), field(c, "arguments")) for c in field(response, "output") if field(c, "type") == "function_call"]
    else:
        return "returned_unsupported_projection", {}, {}
    output = {}
    for call_id, name, args in calls:
        if name not in {"intake_analysis", "tool_call"}:
            continue
        wire_name = name
        value = json.loads(args) if isinstance(args, str) else args
        if not isinstance(value, dict):
            raise ValueError("intake_response_call_arguments_invalid")
        if name == "tool_call":
            from tools.tool_search import resolve_underlying_call
            name, value, error = resolve_underlying_call(value)
            if error or name != "intake_analysis":
                continue
        if not isinstance(call_id, str) or not call_id or call_id in output:
            raise ValueError("intake_response_call_identity_invalid")
        output[call_id] = {"name": name, "arguments_sha256": digest(value), "wire_name": wire_name}
    usage = field(response, "usage", {}) or {}
    def number(*names):
        for name in names:
            value = field(usage, name)
            if type(value) is int and value >= 0:
                return value
        return None
    return "returned", output, {"input_tokens": number("input_tokens", "prompt_tokens"),
                                "output_tokens": number("output_tokens", "completion_tokens"),
                                "source": "returned_response_fields_not_exact_request_counter"}


class Outcomes:
    def __init__(self, engine, host):
        self.engine, self.host, self.store = engine, host, engine.store

    def save(self, value, new=False):
        with self.store.db() as db:
            verify(db, tables={"intake_model_outcomes"})
            if new:
                db.execute("BEGIN IMMEDIATE")
                previous = db.execute("SELECT body,sha256 FROM intake_model_outcomes WHERE profile_id=? AND work_id=?", (self.engine.profile, value["work_ref"])).fetchall()
                for row in previous:
                    old = json.loads(row["body"])
                    if digest(old) != row["sha256"]:
                        raise PermissionError("intake_model_outcome_integrity_failed")
                    if old["input_fingerprint"] == value["input_fingerprint"] and old["status"] in {"preparing", "admitted", "outcome_unknown"}:
                        raise PermissionError("intake_previous_model_outcome_not_settled")
                    if (old["sid"], old["api_request_id"], old["retry_count"]) == (value["sid"], value["api_request_id"], value["retry_count"]):
                        raise PermissionError("intake_model_attempt_identity_already_used")
                db.execute("INSERT INTO intake_model_outcomes VALUES(?,?,?,?,?,?,?)", (self.engine.profile, value["id"], value["work_ref"], value["sid"], canonical(value), digest(value), time.time()))
            else:
                changed = db.execute("UPDATE intake_model_outcomes SET body=?,sha256=? WHERE profile_id=? AND id=?", (canonical(value), digest(value), self.engine.profile, value["id"])).rowcount
                if changed != 1:
                    raise PermissionError("intake_model_outcome_missing_before_settlement")

    def scope(self, sid, api, retry):
        info = CURRENT.get()
        if info is None or info["owner"] is not self or (info["value"]["sid"], info["value"]["api_request_id"], info["value"]["retry_count"]) != (sid, api, retry):
            raise PermissionError("intake_selected_application_requires_native_execution_wrapper")
        return info

    def admit(self, sid, api, retry, request):
        selected = self.engine.analysis.selected(sid)
        if selected is None:
            return None
        info = self.scope(sid, api, retry)
        value = info["value"]
        if selected["application_id"] != value["application_id"] or selected["packet"]["input_fingerprint"] != value["input_fingerprint"]:
            raise PermissionError("intake_selected_application_changed_before_dispatch")
        value.update(status="admitted", request_sha256=digest({k: v for k, v in request.items() if k not in {"timeout", "http_client"}}),
                     request_model=request.get("model"), requested_reasoning=request.get("reasoning", request.get("reasoning_effort")))
        self.save(value)
        def abort(reason):
            value.update(status="not_dispatched", abort_reason="required_control_rejected")
            self.save(value)
        return abort

    def execute(self, request, next_call, original, **identity):
        from hermes_cli.execution_control import ExecutionControlDenied
        from .context import safe_reason
        sid = identity.get("session_id", "")
        if not sid or identity.get("execution_kind") == "auxiliary":
            return original(request, next_call, **identity)
        try:
            selected = self.engine.analysis.selected(sid)
        except Exception as exc:
            raise ExecutionControlDenied("intake_application_preparation_failed:" + safe_reason(exc)) from exc
        if selected is None:
            return original(request, next_call, **identity)
        if CURRENT.get() is not None:
            raise ExecutionControlDenied("intake_recursive_model_execution_forbidden")
        api, retry = identity.get("api_request_id"), identity.get("retry_count", 0)
        if not isinstance(api, str) or not api or type(retry) is not int or retry < 0:
            raise ExecutionControlDenied("intake_native_model_attempt_identity_required")
        value = {"id": "IM-" + uuid.uuid4().hex, "sid": sid, "work_ref": selected["work_ref"],
                 "application_id": selected["application_id"], "input_fingerprint": selected["packet"]["input_fingerprint"],
                 "api_request_id": api, "retry_count": retry, "turn_id": identity.get("turn_id"),
                 "api_mode": identity.get("api_mode"),
                 "status": "preparing", "tool_calls": {}, "origin": "existing_Host.execution_callback",
                 "semantic_review": "unreviewed"}
        try:
            self.save(value, new=True)
        except Exception as exc:
            raise ExecutionControlDenied("intake_model_attempt_unavailable:" + safe_reason(exc)) from exc
        token = CURRENT.set({"owner": self, "value": value})
        try:
            response = original(request, next_call, **identity)
            if value["status"] != "admitted":
                raise ExecutionControlDenied("intake_model_response_without_final_admission")
            try:
                status, calls, usage = projection(response)
            except Exception:
                status, calls, usage = "returned_invalid_projection", {}, {}
            reported_model = field(response, "model")
            value.update(status=status, tool_calls=calls, usage=usage,
                         provider_reported_model=reported_model if isinstance(reported_model, str) else None)
            try:
                current = self.engine.analysis.selected(sid)
                if current is None or current["packet"]["input_fingerprint"] != value["input_fingerprint"]:
                    value["status"] = "returned_stale"
            except Exception:
                value["status"] = "returned_stale"
            self.save(value)
            return response
        except BaseException as exc:
            if value["status"] in {"preparing", "admitted"}:
                cause, seen = exc, set()
                while cause.__cause__ is not None and id(cause) not in seen:
                    seen.add(id(cause)); cause = cause.__cause__
                status = getattr(cause, "status_code", None)
                rejected = type(status) is int and status in {400, 401, 403, 404, 405, 413, 415, 422, 429}
                value["status"] = ("rejected" if rejected else "outcome_unknown") if value["status"] == "admitted" else "not_dispatched"
                if rejected:
                    value.update(provider_status=status, usage={"input_tokens": None, "output_tokens": None,
                                                              "source": "usage_unavailable_for_error_response"})
                value["error_type"] = type(cause).__name__
            try:
                self.save(value)
            except Exception as journal_error:
                raise ExecutionControlDenied("intake_required_model_outcome_settlement_failed") from journal_error
            raise
        finally:
            CURRENT.reset(token)
