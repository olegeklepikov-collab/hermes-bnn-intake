"""Work-scoped interview controls. Refusal constrains questions, not research."""
import copy
import time
import uuid
from .contracts import INTERVIEW_CONTROLS, QUESTION_ACTIONS, validate
from .storage import digest, read


def default_policy():
    return {"state": "open", "max_questions": None, "deadline": None,
            "blocked_topics": [], "issued_count": 0, "reask_permits": {}, "owner_events": []}


class Interview:
    def __init__(self, engine):
        self.engine = engine

    def control(self, sid, args, authenticated_actor):
        """Only authenticated native owner command; never a model tool action."""
        validate(args, INTERVIEW_CONTROLS)
        if not isinstance(authenticated_actor, str) or not authenticated_actor.strip():
            raise PermissionError("intake_authenticated_actor_required")
        payload = {**args, "action": "owner_interview_" + args["action"], "actor": authenticated_actor}
        def update(db, state):
            policy = state.setdefault("interview", default_policy())
            action = args["action"]
            if action in {"pause", "stop", "resume"}:
                policy["state"] = {"pause": "paused", "stop": "stopped", "resume": "open"}[action]
            elif action == "limit":
                policy.update(copy.deepcopy(args["limits"]))
            elif action == "allow_reask":
                if args["gap_id"] not in state["gaps"]:
                    raise ValueError("intake_gap_missing")
                policy["reask_permits"][args["gap_id"]] = self._response_count(state, args["gap_id"])
            policy["owner_events"].append({"action": action, "actor": authenticated_actor,
                                           "at": time.time(), "work_revision_before": state["revision"]})
            return state, {"interview_state": policy["state"], "research_stopped": False}
        return self.engine.commit(sid, payload, update, require_on=False, allow_paused=True)

    @staticmethod
    def _response_count(state, gap):
        return sum(r["gap_id"] == gap for r in state["responses"])

    @staticmethod
    def gate(state, question, now=None, replay=False):
        policy = state.get("interview", default_policy())
        if policy["state"] != "open":
            raise PermissionError("intake_interview_" + policy["state"])
        if policy["deadline"] is not None and (time.time() if now is None else now) >= policy["deadline"]:
            raise PermissionError("intake_interview_deadline_reached")
        if question["topic"] in policy["blocked_topics"]:
            raise PermissionError("intake_interview_topic_blocked")
        if policy["max_questions"] is not None and policy["issued_count"] + (0 if replay else 1) > policy["max_questions"]:
            raise PermissionError("intake_interview_question_limit_reached")
        gap = state["gaps"][question["gap_id"]]
        if gap["response_state"] in {"refused", "unknown", "no_data", "skipped"}:
            if policy["reask_permits"].get(gap["id"]) != Interview._response_count(state, gap["id"]):
                raise PermissionError("intake_interview_existing_response_requires_owner_reask")

    def check_presentation(self, state, args, replay=False):
        if not state or state["lifecycle"] != "active":
            raise PermissionError("intake_interview_work_inactive")
        question = state.get("questions", {}).get(args["question_id"])
        if not question or question["state"] != ("issued" if replay else "planned"):
            raise ValueError("intake_question_not_pending")
        self.gate(state, question, replay=replay)
        return question

    def preflight(self, sid, args):
        """Read-only guard before the common action controller reserves an effect."""
        validate(args, QUESTION_ACTIONS)
        self.engine._work(sid, args["work_ref"], writable=args["action"] != "status")
        if args["action"] == "status":
            return
        if self.engine.mode(sid)["mode"] == "off":
            raise PermissionError("intake_disabled_for_session")
        if args["action"] != "present_question":
            return
        with self.engine.store.db() as db:
            previous = db.execute("SELECT input_hash FROM intake_operations WHERE profile_id=? AND work_id=? AND operation_id=?", (self.engine.profile, args["work_ref"], args["operation_id"])).fetchone()
            if previous and previous[0] != digest(args):
                raise ValueError("intake_idempotency_input_conflict")
            state = read(db, self.engine.profile, args["work_ref"])
        self.check_presentation(state, args, replay=previous is not None)

    def call(self, sid, args):
        validate(args, QUESTION_ACTIONS)
        binding = self.engine._work(sid, args["work_ref"], writable=args["action"] != "status")
        if args["action"] == "status":
            state = self.engine.status(sid, args["work_ref"]).get("state")
            if binding["readonly"]:
                return {"direct_interview_permitted": False, "next": "return_typed_need_to_parent"}
            if not isinstance(state, dict):
                return {"state": "not_prepared", "direct_interview_permitted": False}
            policy = state.get("interview", default_policy())
            visible_policy = {k: v for k, v in policy.items() if k != "owner_events"}
            allowed = (policy["state"] == "open"
                       and (policy["deadline"] is None or time.time() < policy["deadline"])
                       and (policy["max_questions"] is None or policy["issued_count"] < policy["max_questions"]))
            return {"policy": visible_policy, "owner_events_count": len(policy["owner_events"]),
                    "planned_questions": len(state.get("questions", {})),
                    "direct_interview_permitted": allowed,
                    "topic_and_gap_gates_still_required": True,
                    "issued_means_prepared_for_dialogue_not_received_by_user": True}
        def update(db, state):
            policy = state.setdefault("interview", default_policy())
            questions = state.setdefault("questions", {})
            if args["action"] == "plan_question":
                question = copy.deepcopy(args["question"])
                if question["id"] in questions or question["gap_id"] not in state["gaps"]:
                    raise ValueError("intake_question_binding_invalid")
                fingerprint = digest({k: question[k] for k in ["gap_id", "topic", "purpose", "text"]})
                if any(q["fingerprint"] == fingerprint for q in questions.values()):
                    raise ValueError("intake_question_exact_repeat")
                question.update(state="planned", fingerprint=fingerprint)
                questions[question["id"]] = question
                return state, {"question_id": question["id"], "delivery_state": "not_presented"}
            question = self.check_presentation(state, args)
            policy["issued_count"] += 1
            policy["reask_permits"].pop(question["gap_id"], None)
            question.update(state="issued", issued_at=time.time(), issue_id=uuid.uuid4().hex)
            return state, {"question": {k: question[k] for k in ["id", "gap_id", "topic", "text", "issue_id"]},
                           "delivery_state": "prepared_for_owner_dialogue", "received_by_user": False,
                           "outbound_message_sent": False}
        replay_guard = (lambda db, state: self.check_presentation(state, args, replay=True)) if args["action"] == "present_question" else None
        return self.engine.commit(sid, args, update, on_replay=replay_guard)
