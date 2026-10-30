"""Scoped research receipts from the existing native controller, never a runner.

Only the trusted result boundary can capture a result. Selection authorizes no
tool. Scoped system reviews use authenticated receipts and grant no capabilities.
"""
import copy
import dataclasses
import hashlib
import json
import time
from jsonschema import Draft202012Validator
from .contracts import RESEARCH_ACTIONS, RESEARCH_REF, validate
from .storage import canonical, digest, read, verify


def path_basis(state, path):
    gap = state["gaps"][path["gap_id"]]
    return digest({"contract": state["contract"],
                   "gap": {k: gap[k] for k in ["id", "question", "kind", "material", "affected", "required_result"]},
                   "path": {k: path[k] for k in ["family", "purpose", "state", "reason"]}})


class Research:
    def __init__(self, engine, host):
        self.engine, self.store, self.host = engine, engine.store, host

    def state(self, sid, work, active=False):
        self.engine._work(sid, work, writable=True)
        with self.store.db() as db:
            verify(db); state = read(db, self.engine.profile, work)
        if state is None:
            raise ValueError("intake_prepare_required")
        if active and (self.engine.mode(sid)["mode"] == "off" or state["lifecycle"] != "active"):
            raise PermissionError("intake_research_inactive")
        return state

    def rule(self, tool, args=None):
        # The same immutable operator policy used by the common action control.
        # A mixed write/read tool must match exactly one read rule for these args.
        if tool.startswith("intake_") or tool in {"tool_call", "tool_search", "tool_describe"}:
            raise PermissionError("intake_research_requires_a_source_tool")
        candidates = [r for r in self.host.actions.rules.values()
                      if r["tool"] == tool and "parent" in r["roles"]
                      and (args is None or Draft202012Validator(r["arguments_schema"]).is_valid(args))]
        if args is None:
            candidates = [r for r in candidates if r["effect"] in {"read_local", "query_remote"}]
            if candidates:
                return candidates[0]  # Eligibility only; actual args still require exactly one rule.
        if len(candidates) != 1 or candidates[0]["effect"] not in {"read_local", "query_remote"}:
            raise PermissionError("intake_research_exact_read_rule_required")
        return candidates[0]

    def latest(self, db, nonce):
        row = db.execute("SELECT * FROM intake_path_attempts WHERE profile_id=? AND nonce=? ORDER BY revision DESC LIMIT 1",
                         (self.engine.profile, nonce)).fetchone()
        if row is None:
            return None
        body = json.loads(row["body"])
        if digest(body) != row["sha256"]:
            raise PermissionError("intake_path_receipt_integrity_failed")
        return body

    def save(self, db, body):
        db.execute("INSERT INTO intake_path_attempts VALUES(?,?,?,?,?,?,?,?,?)",
                   (self.engine.profile, body["attempt_id"], body["revision"], body["work_ref"],
                    body["path_id"], body["sid"], canonical(body), digest(body), time.time()))

    def rows(self, db, work, path=None):
        where, params = "profile_id=? AND work_id=?", [self.engine.profile, work]
        if path is not None:
            where += " AND path_id=?"; params.append(path)
        nonces = db.execute("SELECT DISTINCT nonce FROM intake_path_attempts WHERE " + where + " ORDER BY nonce", params).fetchall()
        return [self.latest(db, row[0]) for row in nonces]

    @staticmethod
    def summary(body):
        return {k: body[k] for k in ["attempt_id", "revision", "path_id", "tool_name", "status",
                "arguments_sha256", "result_sha256", "result_characters", "result_is_error"] if k in body} | {"receipt_sha256": digest(body)}

    def selection(self, sid):
        if self.engine.mode(sid)["mode"] == "off" or self.store.binding(sid)["readonly"]:
            return None
        with self.store.db() as db:
            binding = db.execute("SELECT work_id FROM working_bindings WHERE profile_id=? AND sid=?", (self.engine.profile, sid)).fetchone()
            state = read(db, self.engine.profile, binding[0]) if binding else None
        if not state or state["lifecycle"] != "active" or not state.get("selected_path"):
            return None
        return state, state["selected_path"]

    def prepare(self, payload, identity):
        """Pre-dispatch check; metadata tools and other normal work are untouched."""
        sid, tool = identity.get("session_id", ""), payload["tool_name"]
        selected = self.selection(sid)
        if selected is None or tool != selected[1]["tool_name"]:
            return None
        state, selection = selected
        from agent.tool_attempt import current_tool_attempt
        attempt = current_tool_attempt()
        if (attempt is None or not attempt.active.is_set() or attempt.tool_name != tool
                or attempt.profile_home != self.host.admission.profile_home
                or attempt.identity.get("session_id") != sid):
            raise PermissionError("intake_research_native_attempt_required")
        path = state["paths"][selection["path_id"]]
        if path["state"] != "planned":
            raise PermissionError("intake_excluded_path_requires_revised_plan")
        rule = self.rule(tool, payload.get("args", {}))
        from bn_memory.store import SENSITIVE
        if SENSITIVE.search(canonical(payload.get("args", {}))):
            raise PermissionError("intake_no_secret_arguments_in_research_receipts")
        from bn_memory.action_retry import arguments_hash
        fingerprint = arguments_hash(tool, payload.get("args", {}))
        with self.store.db() as db:
            old = self.latest(db, attempt.nonce)
            for prior in self.rows(db, state["work_ref"]):
                if prior["attempt_id"] == attempt.nonce:
                    continue
                if (prior["path_basis"] == path_basis(state, path) and prior["tool_name"] == tool
                        and prior["arguments_sha256"] == fingerprint):
                    raise PermissionError("intake_research_exact_repeat_use_receipt_or_new_information_purpose")
        info = {"attempt_id": attempt.nonce, "work_ref": state["work_ref"], "sid": sid,
                "path_id": path["id"], "path_basis": path_basis(state, path), "tool_name": tool,
                "arguments": copy.deepcopy(payload.get("args", {})), "arguments_sha256": fingerprint,
                "rule_id": rule["id"], "rule_sha256": digest(rule)}
        if old and any(old[k] != v for k, v in info.items()):
            raise PermissionError("intake_research_attempt_binding_changed")
        return info

    def admitted(self, info, identity):
        if info is None:
            return
        from agent.tool_attempt import current_tool_attempt
        attempt = current_tool_attempt()
        if attempt is None or attempt.nonce != info["attempt_id"]:
            raise PermissionError("intake_research_native_attempt_required")
        if identity.get("boundary") not in {attempt.final_boundary, "bn_document_read_adapter"}:
            return  # The core's outer precheck has not dispatched the executor.
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE"); verify(db)
            row = db.execute("SELECT a.*,w.work_id FROM action_attempts a JOIN working_actions w ON w.profile_id=a.profile_id AND w.nonce=a.nonce WHERE a.profile_id=? AND a.nonce=?",
                             (self.engine.profile, info["attempt_id"])).fetchone()
            if (row is None or row["sid"] != info["sid"] or row["work_id"] != info["work_ref"]
                    or row["tool_name"] != info["tool_name"] or row["rule_id"] != info["rule_id"]
                    or row["status"] != "admitted_outcome_unsettled"):
                raise PermissionError("intake_research_common_admission_required")
            current = read(db, self.engine.profile, info["work_ref"])
            policy = db.execute("SELECT mode FROM intake_modes WHERE profile_id=? AND sid=?", (self.engine.profile, info["sid"])).fetchone()
            if (not policy or policy[0] == "off" or current["lifecycle"] != "active"
                    or current.get("selected_path") != {"path_id": info["path_id"], "tool_name": info["tool_name"]}
                    or path_basis(current, current["paths"][info["path_id"]]) != info["path_basis"]):
                raise PermissionError("intake_research_selection_changed_before_dispatch")
            if self.latest(db, info["attempt_id"]) is None:
                for prior in self.rows(db, info["work_ref"]):
                    if (prior["attempt_id"] != info["attempt_id"] and prior["path_basis"] == info["path_basis"]
                            and prior["tool_name"] == info["tool_name"] and prior["arguments_sha256"] == info["arguments_sha256"]):
                        raise PermissionError("intake_research_exact_repeat_at_final_admission")
                self.save(db, {**info, "revision": 1, "status": "admitted", "reading_level": "not_yet_returned"})

    def returned(self, payload, identity):
        """Called after successful common result control, inside the native attempt."""
        from agent.tool_attempt import current_tool_attempt
        attempt = current_tool_attempt()
        if attempt is None or attempt.profile_home != self.host.admission.profile_home:
            return
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE"); verify(db)
            body = self.latest(db, attempt.nonce)
            if body is None:
                return
            row = db.execute("SELECT * FROM action_attempts WHERE profile_id=? AND nonce=?", (self.engine.profile, attempt.nonce)).fetchone()
            from bn_memory.admission import canonical as native_canonical, digest as native_digest
            result = payload.get("result")
            if dataclasses.is_dataclass(result):
                result = dataclasses.asdict(result)
            raw = canonical(result)
            if (not attempt.active.is_set() or body["status"] != "admitted"
                    or identity.get("session_id") != body["sid"] or payload["tool_name"] != body["tool_name"]
                    or row is None or row["status"] != "returned_unreviewed"
                    or row["result_hash"] != native_digest(native_canonical(result))
                    or payload.get("args", {}) != body["arguments"]):
                raise PermissionError("intake_research_native_result_mismatch")
            state = read(db, self.engine.profile, body["work_ref"])
            mode = db.execute("SELECT mode FROM intake_modes WHERE profile_id=? AND sid=?", (self.engine.profile, body["sid"])).fetchone()
            current = db.execute("SELECT work_id FROM working_bindings WHERE profile_id=? AND sid=?", (self.engine.profile, body["sid"])).fetchone()
            stale = (not mode or mode[0] == "off" or state["lifecycle"] != "active"
                     or not current or current[0] != body["work_ref"]
                     or path_basis(state, state["paths"][body["path_id"]]) != body["path_basis"])
            from bn_memory.store import SENSITIVE
            sensitive = bool(SENSITIVE.search(raw))
            from bn_memory.action_retry import returned_error
            # MCP may return a typed error inside a text content envelope.
            def is_error(value):
                if returned_error(value):
                    return True
                if isinstance(value, str):
                    try:
                        value = json.loads(value)
                    except (ValueError, TypeError):
                        return False
                if isinstance(value, dict) and isinstance(value.get("content"), list):
                    for part in value["content"]:
                        if isinstance(part, dict) and part.get("type") == "text":
                            try:
                                nested = json.loads(part.get("text", ""))
                            except (ValueError, TypeError):
                                continue
                            if returned_error(nested):
                                return True
                return False
            updated = {**body, "revision": 2, "status": "capture_rejected" if sensitive else "returned_stale" if stale else "returned",
                       "result_sha256": digest(result), "result_characters": len(raw),
                       "result_is_error": is_error(result), "native_result_hash": row["result_hash"],
                       "reading_level": "returned_tool_result_not_model_reading",
                       "semantic_review": "unreviewed", "scientific_acceptance": False}
            if not sensitive:
                updated["result"] = result
            self.save(db, updated)
        if sensitive:
            # Commit the rejection metadata before denying the result. Rolling
            # back here would leave an unexplained admitted attempt instead.
            raise PermissionError("intake_sensitive_result_not_retained")

    def checked_receipt(self, db, sid, work, nonce, expected):
        body = self.latest(db, nonce)
        if body is None or body["work_ref"] != work or digest(body) != expected:
            raise PermissionError("intake_research_receipt_version_unavailable")
        binding = self.store.binding(sid)
        card = db.execute("SELECT project FROM working_cards WHERE profile_id=? AND work_id=?", (self.engine.profile, work)).fetchone()
        if binding["readonly"] or card is None or binding["project"] != card[0]:
            raise PermissionError("intake_research_receipt_scope_denied")
        rule = self.rule(body["tool_name"], body["arguments"])
        if digest(rule) != body["rule_sha256"]:
            raise PermissionError("intake_research_source_policy_changed")
        row = db.execute("SELECT result_hash,status FROM action_attempts WHERE profile_id=? AND nonce=?", (self.engine.profile, nonce)).fetchone()
        if (body["status"] != "returned" or row is None or row["status"] != "returned_unreviewed"
                or row["result_hash"] != body["native_result_hash"] or digest(body["result"]) != body["result_sha256"]):
            raise PermissionError("intake_research_result_not_usable")
        return body

    def resolve(self, sid, work, ref, db=None):
        if db is None:
            self.engine._work(sid, work, writable=True)
            with self.store.db() as connection:
                return self.resolve(sid, work, ref, connection)
        body = self.checked_receipt(db, sid, work, ref["attempt_id"], ref["receipt_sha256"])
        raw = canonical(body["result"])
        if ref["start"] >= ref["end"] or ref["end"] > len(raw):
            raise ValueError("intake_research_result_range_invalid")
        return {**ref, "content_hash": body["result_sha256"], "content": raw[ref["start"]:ref["end"]],
                "tool_name": body["tool_name"], "path_id": body["path_id"],
                "reading_level": body["reading_level"], "result_is_error": body["result_is_error"],
                "semantic_review": "unreviewed", "scope": "parent_in_same_work"}

    def call(self, sid, args):
        validate(args, RESEARCH_ACTIONS)
        work, action = args["work_ref"], args["action"]
        state = self.state(sid, work, active=action in {"select_path","review_path","review_gap"})
        if action in {'review_path','review_gap'}:
            # Actor comes from the authenticated native scope, never model args.
            review=OperatorReview(self)
            payload={k:v for k,v in args.items() if k not in {'schema_version','action','work_ref','expected_revision','operation_id','path_id','gap_id'}}
            target='path' if action=='review_path' else 'gap'
            return getattr(review,target)(sid,args,actor='system:'+sid,**payload)
        if action == "select_path":
            def update(db, current):
                path_id, tool = args["path_id"], args["tool_name"]
                if (path_id is None) != (tool is None):
                    raise ValueError("intake_research_selection_pair_required")
                if path_id is not None:
                    if path_id not in current["paths"] or current["paths"][path_id]["state"] != "planned":
                        raise ValueError("intake_planned_research_path_required")
                    self.rule(tool)
                current["selected_path"] = {"path_id": path_id, "tool_name": tool} if path_id else None
                return current, {"tool_authorized": False}
            return self.engine.commit(sid, args, update)
        with self.store.db() as db:
            if action == "completion":
                return self.completion(db, sid, state)
            if action == "status":
                path_id = args.get("path_id") or (state.get("selected_path") or {}).get("path_id")
                if path_id is None and len(state["paths"]) == 1:
                    path_id = next(iter(state["paths"]))
                if path_id not in state["paths"]:
                    raise ValueError("intake_path_missing")
                rows = self.rows(db, work, path_id)
                start, limit = args.get("offset", 0), args.get("limit", 20)
                return {"path": state["paths"][path_id], "attempts": [self.summary(r) for r in rows[start:start+limit]],
                        "next_offset": start + limit if len(rows) > start + limit else None,
                        "semantic_review": "unreviewed"}
            receipt = self.checked_receipt(db, sid, work, args["attempt_id"], args["receipt_sha256"])
        raw = canonical(receipt["result"])
        start, limit = args.get("offset", 0), args.get("limit", 4096)
        if start > len(raw):
            raise ValueError("intake_research_offset_outside_result")
        end = min(len(raw), start + limit)
        return {"receipt": self.summary(receipt), "text": raw[start:end], "start": start, "end": end,
                "fragment_sha256": hashlib.sha256(raw[start:end].encode()).hexdigest(), "next_offset": end if end < len(raw) else None,
                "complete": start == 0 and end == len(raw),
                "source_ref": {"kind": "research_result", "attempt_id": receipt["attempt_id"], "receipt_sha256": digest(receipt), "start": start, "end": end},
                "reading_level": receipt["reading_level"], "scientific_acceptance": False}

    def preflight(self, sid, args):
        validate(args, RESEARCH_ACTIONS)
        state = self.state(sid, args["work_ref"], active=args["action"] in {"select_path","review_path","review_gap"})
        if args['action'] in {'review_path','review_gap'}:
            refs=args.get('source_refs',[]);OperatorReview(self).anchors(sid,refs)
        if args["action"] == "select_path":
            path, tool = args["path_id"], args["tool_name"]
            if (path is None) != (tool is None):
                raise ValueError("intake_research_selection_pair_required")
            if path is not None:
                if path not in state["paths"] or state["paths"][path]["state"] != "planned":
                    raise ValueError("intake_planned_research_path_required")
                self.rule(tool)

    def completion(self, db, sid, state):
        paths, unperformed = {}, []
        for key, path in state["paths"].items():
            attempts = self.rows(db, state["work_ref"], key)
            review = path.get("review")
            valid = bool(review and review["path_basis"] == path_basis(state, path))
            if valid:
                valid = {r["attempt_id"] for r in review["receipts"]} == {r["attempt_id"] for r in attempts}
            if valid:
                for ref in review["receipts"]:
                    self.checked_receipt(db, sid, state["work_ref"], ref["attempt_id"], ref["receipt_sha256"])
                self.engine.analysis.assert_sources_current(db, sid, review["source_anchors"])
                valid = review["state"] != "incomplete"
            if not valid or any(r["status"] == "admitted" for r in attempts):
                unperformed.append(key)
            paths[key] = {"plan": {k: path[k] for k in ["gap_id", "family", "purpose", "state", "reason"]},
                          "attempts": [self.summary(r) for r in attempts], "review": review,
                          "verified_for_declared_scope": valid}
        gaps = []
        for gap in state["gaps"].values():
            review = gap.get("review")
            if review:
                applicable = {key for key, path in state["paths"].items() if path["gap_id"] == gap["id"]}
                if set(review["path_reviews"]) != applicable:
                    review = None
            if review:
                for key, basis in review["path_reviews"].items():
                    current = state["paths"][key].get("review")
                    if current is None or digest(current) != basis:
                        review = None; break
                if review:
                    self.engine.analysis.assert_sources_current(db, sid, review["source_anchors"])
            resolved = bool(review and review["state"] == "resolved")
            missing_paths = not any(p["gap_id"] == gap["id"] for p in state["paths"].values())
            gaps.append({"id": gap["id"], "material": gap["material"], "resolved": resolved,
                         "missing_paths": missing_paths, "remaining": review["remaining"] if review else [gap["required_result"]]})
        return {"kind": "ResearchCompletionAssessment", "work_ref": state["work_ref"], "work_revision": state["revision"],
                "contract_sha256": digest(state["contract"]), "paths": paths, "gaps": gaps,
                "unperformed_paths": unperformed,
                "corpora": copy.deepcopy(state.get("document_corpora", {})),
                "products": {k: {"sha256": digest(v), "semantic_review": v["semantic_review"]}
                             for k, v in state.get("analysis_products", {}).items()},
                "scope": "declared_work_paths_only_not_exhaustion_of_world_knowledge",
                "semantic_review": "unreviewed", "completion_accepted": False}


class OperatorReview:
    """Shared evidence validator; historical class name retained for callers.

    The public system route fixes its actor and validates typed receipt/source
    references. The local compatibility route is optional, never a human gate.
    Neither route changes native permissions or certifies scientific truth.
    """
    def __init__(self, research):
        self.research, self.engine = research, research.engine

    def anchors(self, sid, refs):
        from .contracts import SOURCE_REF
        if not isinstance(refs, list):
            raise ValueError("intake_review_source_refs_required")
        anchors = []
        for ref in refs:
            if not Draft202012Validator(SOURCE_REF).is_valid(ref):
                raise ValueError("intake_review_source_ref_invalid")
            source = self.engine._source(sid, ref)
            anchors.append({"ref": copy.deepcopy(ref), "content_sha256": source.get("content_hash", source.get("unit_sha256"))})
        return anchors

    def path(self, sid, args, *, actor, state, reason, receipts, source_refs, reading_level, remaining):
        if (state not in {"performed", "no_result", "unavailable", "prohibited", "not_applicable", "incomplete"}
                or not all(isinstance(v, str) and v.strip() for v in [actor, reason, reading_level])
                or not isinstance(remaining, list) or any(not isinstance(v, str) or not v for v in remaining)):
            raise ValueError("intake_operator_path_review_invalid")
        anchors = self.anchors(sid, source_refs)
        if (not isinstance(receipts, list) or any(not isinstance(ref, dict) or set(ref) != {"attempt_id", "receipt_sha256"}
                for ref in receipts)):
            raise ValueError("intake_operator_receipt_references_required")
        def update(db, current):
            path = current["paths"].get(args["path_id"])
            if path is None:
                raise ValueError("intake_path_missing")
            checked = [self.research.checked_receipt(db, sid, current["work_ref"], ref["attempt_id"], ref["receipt_sha256"]) for ref in receipts]
            if any(r["path_id"] != path["id"] or r["path_basis"] != path_basis(current, path) for r in checked):
                raise PermissionError("intake_review_receipt_wrong_path")
            if state in {"performed", "no_result"} and (not checked or any(r["result_is_error"] for r in checked)):
                raise PermissionError("intake_success_requires_actual_non_error_result")
            if state in {"unavailable", "prohibited", "not_applicable"} and not checked and not anchors:
                raise PermissionError("intake_exclusion_requires_evidence_not_model_claim")
            self.engine.analysis.assert_sources_current(db, sid, anchors)
            review = {"state": state, "actor": actor, "reason": reason, "receipts": copy.deepcopy(receipts),
                      "review_origin":"system" if args.get('action')=='review_path' else 'operator',
                      "human_review_required":False,
                      "source_anchors": anchors, "reading_level": reading_level, "remaining": remaining,
                      "path_basis": path_basis(current, path), "scientific_acceptance": False}
            path.setdefault("review_history", []).append(review)
            path["review"] = review; path["claim_review"] = "reviewed"
            return current, {"review_sha256": digest(review), "scientific_acceptance": False}
        payload = {"actor": actor, "state": state, "reason": reason, "receipts": receipts,
                   "source_refs": source_refs, "reading_level": reading_level, "remaining": remaining}
        def replay(db, current):
            for ref in receipts:
                self.research.checked_receipt(db, sid, current["work_ref"], ref["attempt_id"], ref["receipt_sha256"])
            self.engine.analysis.assert_sources_current(db, sid, anchors)
        action='review_path' if args.get('action')=='review_path' else 'operator_path_review'
        return self.engine.commit(sid, {**args, "action": action, "review": payload}, update, on_replay=replay)

    def gap(self, sid, args, *, actor, state, reason, path_ids, source_refs, remaining):
        if (state not in {"resolved", "partial", "blocked"} or not all(isinstance(v, str) and v.strip() for v in [actor, reason])
                or not isinstance(remaining, list) or (state == "resolved" and remaining) or (state != "resolved" and not remaining)):
            raise ValueError("intake_operator_gap_review_invalid")
        anchors = self.anchors(sid, source_refs)
        def update(db, current):
            gap = current["gaps"][args["gap_id"]]
            if gap["kind"] in {"preference", "permission"} and state == "resolved":
                raise PermissionError("intake_user_preference_or_permission_requires_separate_owner_resolution")
            applicable = {key: p for key, p in current["paths"].items() if p["gap_id"] == gap["id"]}
            if set(path_ids) != set(applicable) or not applicable:
                raise PermissionError("intake_gap_review_requires_all_declared_paths")
            check = self.research.completion(db, sid, current)
            if any(not check["paths"][key]["verified_for_declared_scope"] for key in path_ids):
                raise PermissionError("intake_gap_paths_not_reviewed")
            if state == "resolved" and (not anchors or not any(p["review"]["state"] == "performed" for p in applicable.values())):
                raise PermissionError("intake_gap_resolution_requires_obtained_evidence")
            self.engine.analysis.assert_sources_current(db, sid, anchors)
            review = {"state": state, "actor": actor, "reason": reason,
                      "review_origin":"system" if args.get('action')=='review_gap' else 'operator',
                      "human_review_required":False,
                      "path_reviews": {key: digest(applicable[key]["review"]) for key in path_ids},
                      "source_anchors": anchors, "remaining": remaining, "scientific_acceptance": False}
            gap.setdefault("review_history", []).append(review); gap["review"] = review
            gap["research_state"] = state
            return current, {"review_sha256": digest(review), "scientific_acceptance": False}
        payload = {"actor": actor, "state": state, "reason": reason, "path_ids": path_ids,
                   "source_refs": source_refs, "remaining": remaining}
        action='review_gap' if args.get('action')=='review_gap' else 'operator_gap_review'
        return self.engine.commit(sid, {**args, "action": action, "review": payload}, update)
