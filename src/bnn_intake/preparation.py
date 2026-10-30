"""Addressed lifecycle routing; no invented contract, source text or model call.

The ordinary Hermes model supplies semantic decisions through the existing
typed tools. This router advertises the next actual need, never its completion.
"""
import json
import hashlib
from .contracts import CONTRACT
from .storage import digest


class Preparation:
    def __init__(self, engine, host):
        self.engine, self.host, self.store = engine, host, engine.store

    def describe(self, sid, mode, work, state):
        if mode["mode"] == "off" or self.store.binding(sid)["readonly"]:
            return None
        if state and (state["lifecycle"] != "active" or state.get("selected_application")):
            return None
        if mode["mode"] == "auto" and not work:
            return None  # No implicit task creation for greetings or an unbound conversation.
        with self.store.db() as db:
            native = db.execute("SELECT turn_id,anchors FROM context_admissions WHERE profile_id=? AND sid=?", (self.engine.profile, sid)).fetchone()
        current = {"turn_id": native["turn_id"], "input_anchors": json.loads(native["anchors"])} if native else None
        goals, working_ref = [], None
        if work:
            view = self.host.working.view(sid)
            if view["work_id"] != work:
                raise PermissionError("intake_preparation_native_work_changed")
            working_ref = {"work_id": work, "revision": view["revision"], "sha256": view["source_revision_sha256"]}
            goals = [{"id": item["id"], "source": item["source"], "text_sha256": hashlib.sha256(item["text"].encode()).hexdigest()}
                     for item in view["items"] if item["kind"] == "goal" and item["status"] == "active"]
        if not work:
            stage, next_action = "bind_native_work", {"native_interface": "memory_working", "need": "bind_the_existing_or_explicitly_created_Beads_task"}
        elif state is None:
            stage, next_action = "prepare_work", {"tool": "intake_work", "action": "prepare", "required_fields": list(CONTRACT["properties"])}
        elif state.get("selected_path"):
            selected = state["selected_path"]
            path = state["paths"][selected["path_id"]]
            with self.store.db() as db:
                receipts = self.engine.research.rows(db, work, path["id"])
            returned = [r for r in receipts if r["status"] == "returned"]
            known = {"work_ref": work, "schema_version": 1}
            if returned:
                receipt = returned[-1]
                expos = {key for key, value in state["expositions"].items() if any(
                    s["kind"] == "research_result" and s["attempt_id"] == receipt["attempt_id"] for s in value["sources"])}
                used = any(state["method_applications"][p["application_id"]]["application"]["exposition_id"] in expos
                           for p in state.get("analysis_products", {}).values())
                if used:
                    stage, next_action = "assess_remaining_research_needs", {"tool": "intake_research", "action": "completion", "known_arguments": known}
                else:
                    stage, next_action = "use_saved_research_result", {"tool": "intake_research", "action": "read_result",
                        "known_arguments": {**known, "attempt_id": receipt["attempt_id"], "receipt_sha256": digest(receipt), "offset": 0, "limit": 4096},
                        "need": "Read_saved_evidence_and_plan_analysis;_do_not_repeat_the_source_call"}
            elif receipts:
                stage, next_action = "verify_research_outcome", {"tool": "intake_research", "action": "status",
                    "known_arguments": {**known, "path_id": path["id"]}, "need": "Verify_the_existing_attempt_before_any_repeat"}
            else:
                stage, next_action = "execute_selected_research_path", {"tool": selected["tool_name"],
                        "path_id": path["id"], "gap_id": path["gap_id"], "purpose": path["purpose"],
                        "need": "Use_the_existing_admitted_read_interface_then_read_the_receipt;_selection_grants_no_permission"}
        elif not state["expositions"]:
            stage, next_action = "plan_exposition", {"tool": "intake_work", "action": "expose"}
        elif any(a["status"] == "planned_not_executed" for a in state.get("method_applications", {}).values()):
            stage, next_action = "select_prepared_application", {"tool": "intake_analysis", "action": "select_application"}
        elif not state.get("analysis_products"):
            stage, next_action = "plan_method", {"tool": "intake_analysis", "action": "plan_method"}
        else:
            stage, next_action = "assess_remaining_needs", {"tool": "intake_work", "action": "status"}
        if next_action.get("action") in {"prepare", "expose", "plan_method", "select_application"}:
            next_action["known_arguments"] = {"work_ref": work, "expected_revision": state["revision"] if state else 0,
                                             "schema_version": 1}
            if stage == "select_prepared_application":
                ready = [key for key, value in state["method_applications"].items() if value["status"] == "planned_not_executed"]
                if len(ready) == 1:
                    next_action["known_arguments"]["application_id"] = ready[0]
        result = {"stage": stage, "work_ref": work, "native_request": current, "working_ref": working_ref,
                  "existing_goal_refs": goals, "mode": mode["mode"], "next_action": next_action,
                  "semantic_decision_owner": "ordinary_Hermes_execution_with_existing_controls",
                  "applicability": "assess_for_current_request" if mode["mode"] == "auto" else "explicit_owner_request",
                  "rules": ["Use the current native message and existing working goal, not a truncated search query.",
                            "Reuse the bound work. Do not invent missing facts, permissions or completed research.",
                            "Record material unknowns; preserve owner interview restrictions and research obligations.",
                            "Read addressed method cards; select a justified sufficient set, including local additions when needed.",
                            "Use the revision in the successful mutation result or current signed context. Do not reread status after every successful write; reread on conflict, missing outcome or recovery.",
                            "A returned product is unreviewed. New work needs a distinct question or purpose, not an automatic self-review loop.",
                            "When the existing result suffices for this request, report its actual scope and remaining needs; do not generate another pass merely for this notice."],
                  "progress": {"expositions": len(state["expositions"]) if state else 0,
                               "applications": len(state.get("method_applications", {})) if state else 0,
                               "products": len(state.get("analysis_products", {})) if state else 0,
                               "open_material_gaps": sum(g["material"] and g["research_state"] != "resolved" for g in state["gaps"].values()) if state else 0},
                  "executor_started": False, "semantic_acceptance": "unreviewed"}
        result["request_fingerprint"] = digest(result)
        return result
