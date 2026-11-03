"""Add intake to the existing signed host envelope and final host admission.

No separate mandatory controller. Activation must precede common-controller registration.
The only host-table adapter refreshes one already-authorized context token using CAS;
it keeps its input anchors, role, policy and all retired envelopes intact.
"""
import json
import time
import uuid
from .interview import default_policy
from .storage import canonical, digest, read, verify


def safe_reason(exc):
    try:
        from agent.redact import redact_for_egress
        return type(exc).__name__ + ":" + redact_for_egress(str(exc))[:300]
    except Exception:
        return type(exc).__name__  # Missing redaction cannot expose the original message.


class ContextBridge:
    def __init__(self, engine, host, preflight=None):
        self.engine, self.host = engine, host
        self.preflight = preflight
        if engine.research is None:
            from .research import Research
            engine.research = Research(engine, host)
        if engine.delegation is None:
            from .delegation import Delegation
            engine.delegation = Delegation(engine, host)
        self.originals = {name: getattr(host, name) for name in ["assembled_context", "request", "model_control", "tool_control", "result_control", "launch_control", "execution"]}
        self.wrappers = {"assembled_context": self.assembled, "request": self.request,
                         "model_control": self.control, "tool_control": self.tool_control, "result_control": self.result_control,
                         "launch_control": self.launch_control, "execution": self.execution}
        from .execution import Outcomes
        self.outcomes = Outcomes(engine, host)
        from .preparation import Preparation
        self.preparation = Preparation(engine, host)
        from .visuals import Visuals
        self.visuals = Visuals(engine, host)

    def install(self):
        if getattr(self.host, "_intake_context_bridge", None) is not None:
            raise RuntimeError("intake_context_bridge_already_installed")
        self.host._intake_context_bridge = self
        for name, wrapper in self.wrappers.items():
            setattr(self.host, name, wrapper)

    def close(self):
        for name, wrapper in self.wrappers.items():
            if getattr(self.host, name, None) is wrapper:
                setattr(self.host, name, self.originals[name])
        if getattr(self.host, "_intake_context_bridge", None) is self:
            del self.host._intake_context_bridge

    def storage_admission(self):
        storage=getattr(self.host,'storage',None)
        if storage:
            try:storage.require('primary')
            except Exception as exc:
                from bn_memory.storage_guard import fault
                if not fault(exc):raise
                from hermes_cli.execution_control import ExecutionDecision
                return ExecutionDecision(False,str(exc))
        return None

    def tool_control(self, payload, **identity):
        storage_failure=self.storage_admission()
        if storage_failure is not None:return storage_failure
        from hermes_cli.execution_control import ExecutionDecision
        if self.preflight is not None:
            try:
                self.preflight(payload, **identity)
            except Exception as exc:
                return ExecutionDecision(False, "intake_preflight:" + safe_reason(exc))
        try:
            info = self.engine.research.prepare(payload, identity)
        except Exception as exc:
            return ExecutionDecision(False, "intake_research_preflight:" + safe_reason(exc))
        decision = self.originals["tool_control"](payload, **identity)
        if decision.allow:
            try:
                self.engine.research.admitted(info, identity)
            except Exception as exc:
                return ExecutionDecision(False, "intake_research_admission:" + safe_reason(exc))
        return decision

    def result_control(self, payload, **identity):
        if getattr(self.host,'storage',None):self.host.storage.finalizing()
        from hermes_cli.execution_control import ExecutionDecision
        decision = self.originals["result_control"](payload, **identity)
        if decision.allow:
            try:
                # With intake off and no tracked attempt this must remain optional.
                if self.engine.research.selection(identity.get("session_id", "")) is not None:
                    self.engine.research.returned(payload, identity)
                else:
                    from agent.tool_attempt import current_tool_attempt
                    attempt = current_tool_attempt()
                    if attempt is not None:
                        with self.engine.store.db() as db:
                            exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='intake_path_attempts'").fetchone()
                            tracked = exists and db.execute("SELECT 1 FROM intake_path_attempts WHERE profile_id=? AND nonce=?", (self.engine.profile, attempt.nonce)).fetchone()
                        if tracked:
                            self.engine.research.returned(payload, identity)
            except Exception as exc:
                return ExecutionDecision(False, "intake_research_result:" + safe_reason(exc))
        return decision

    def execution(self, request, next_call, **identity):
        storage=getattr(self.host,'storage',None)
        if storage:
            try:storage.require('primary',probe=True)
            except Exception as exc:
                from bn_memory.storage_guard import denied
                failure=denied(exc,'application_preparation')
                if failure:raise failure from exc
                raise
        def checked(outgoing):
            if identity.get("execution_kind") != "auxiliary":
                try:
                    self.visuals.validate(identity.get("session_id", ""), outgoing)
                except Exception as exc:
                    from hermes_cli.execution_control import ExecutionControlDenied
                    raise ExecutionControlDenied("intake_visual_provider_boundary:" + safe_reason(exc)) from exc
            return next_call(outgoing)
        return self.outcomes.execute(request, checked, self.originals["execution"], **identity)

    def launch_control(self, payload, **identity):
        storage_failure=self.storage_admission()
        if storage_failure is not None:return storage_failure
        from hermes_cli.execution_control import ExecutionDecision
        try:
            plan = self.engine.delegation.launch_plan(payload, identity)
        except Exception as exc:
            return ExecutionDecision(False, "intake_launch_preflight:" + safe_reason(exc))
        decision = self.originals["launch_control"](payload, **identity)
        if decision.allow:
            try:
                self.engine.delegation.bind(plan, identity.get("child_session_id", ""))
            except Exception as exc:
                if decision.on_abort:
                    decision.on_abort("intake_handoff_binding_failed")
                return ExecutionDecision(False, "intake_launch_binding:" + safe_reason(exc))
        return decision

    def frame(self, sid):
        engine = self.engine
        mode = engine.mode(sid)
        if mode["mode"] == "off":
            return self.owner_controls_frame(sid, mode)
        binding = engine.store.binding(sid)
        with engine.store.db() as db:
            work = db.execute("SELECT work_id FROM working_bindings WHERE profile_id=? AND sid=?", (engine.profile, sid)).fetchone()
            state = read(db, engine.profile, work[0]) if work else None
        if not state:
            prepared = self.preparation.describe(sid, mode, work[0] if work else None, None)
            if prepared is None:
                return {"basis": digest({"mode": mode, "prepared": False}), "text": None, "work_ref": None, "receipt_required": False}
            return self.rendered_frame(prepared, {"mode": mode, "preparation": prepared["request_fingerprint"]},
                                       work[0] if work else None, tag="BN_INTAKE_PREPARATION")
        policy = state.get("interview", default_policy())
        controls = {k: policy[k] for k in ["state", "max_questions", "blocked_topics", "deadline", "issued_count"]}
        if binding["readonly"]:
            payload = engine.delegation.child_packet(sid)
            basis = {"mode": mode, "child_assignment": payload["assignment_id"], "packet_sha256": digest(payload)}
        else:
            payload = {"work_ref": state["work_ref"], "work_revision": state["revision"],
                       "contract": state["contract"], "effective_interview_controls": controls,
                       "lifecycle": state["lifecycle"], "semantic_acceptance": "unreviewed"}
            from .profile_contract import checked
            profile_contract=checked(state)
            if state.get("research_basis"):
                payload["native_research_basis_references"] = state["research_basis"]
            if state.get("selected_exposition") and state["lifecycle"] == "active":
                payload["addressed_source_data"] = engine.handoff(sid, state["work_ref"], state["selected_exposition"])["packet"]
            if state.get("selected_application") and state["lifecycle"] == "active":
                application = engine.analysis.application_packet(sid, state["work_ref"], state["selected_application"])
                if application["application"]["exposition_id"] != state.get("selected_exposition"):
                    raise PermissionError("intake_selected_application_exposition_mismatch")
                payload["addressed_source_data"] = application.pop("source_packet")
                payload["method_application"] = application
            # Exactly one complete addressed profile contract. Old applications
            # keep their own frozen version; the work revision is not rewritten.
            if 'addressed_source_data' not in payload and profile_contract is not None:
                payload['profile_contract']=profile_contract
            basis = {"mode": mode, "work": state["work_ref"], "revision": state["revision"], "state_hash": digest(state)}
            prepared = self.preparation.describe(sid, mode, state["work_ref"], state)
            if prepared is not None:
                payload["preparation"] = prepared
                basis["preparation"] = prepared["request_fingerprint"]
        return self.rendered_frame(payload, basis, state["work_ref"])

    def rendered_frame(self, payload, basis, work, tag="BN_INTAKE_CONTEXT"):
        text = '<' + tag + ' sha256="' + digest(payload) + '">\n' + canonical(payload) + '\n</' + tag + '>'
        limit = self.engine.store.config.get("intake_context_bytes", 32768)
        if type(limit) is not int or limit < 1 or len(text.encode()) > limit:
            raise PermissionError("intake_split_required_do_not_trim_dependencies")
        return {"basis": digest(basis), "text": text, "work_ref": work, "receipt_required": True}

    def owner_controls_frame(self, sid, mode):
        """Off removes semantic intake, not the owner's persistent interview restrictions."""
        with self.engine.store.db() as db:
            work = db.execute("SELECT work_id FROM working_bindings WHERE profile_id=? AND sid=?", (self.engine.profile, sid)).fetchone()
            state = read(db, self.engine.profile, work[0]) if work else None
        policy = state.get("interview", default_policy()) if state else default_policy()
        text = None
        if policy["owner_events"]:
            controls = {k: policy[k] for k in ["state", "max_questions", "blocked_topics", "deadline", "issued_count"]}
            if self.engine.store.binding(sid)["readonly"]:
                controls = {"state": policy["state"], "direct_interview_permitted": False}
            payload = {"source": "authenticated_owner_commands", "work_ref": work[0],
                       "interview_controls": controls, "semantic_intake_enabled": False}
            text = '<BN_OWNER_INTERVIEW_CONTROLS sha256="' + digest(payload) + '">\n' + canonical(payload) + '\n</BN_OWNER_INTERVIEW_CONTROLS>'
            limit = self.engine.store.config.get("intake_context_bytes", 32768)
            if type(limit) is not int or limit < 1 or len(text.encode()) > limit:
                raise PermissionError("owner_constraints_require_addressed_split_do_not_trim")
        return {"basis": digest({"mode": mode, "owner_controls": text}), "text": text,
                "work_ref": work[0] if work else None, "receipt_required": False}

    def _publication(self, sid, token):
        with self.engine.store.db() as db:
            verify(db)
            row = db.execute("SELECT body,sha256 FROM intake_publications WHERE profile_id=? AND sid=? AND token=?", (self.engine.profile, sid, token)).fetchone()
        if row is None:
            return None
        value = json.loads(row["body"])
        if digest(value) != row["sha256"]:
            raise PermissionError("intake_publication_integrity_failed")
        return value

    def assembled(self, sid, query, turn):
        original = self.originals["assembled_context"](sid, query, turn)
        from .common_policy import frame as common_policy_frame
        original+='\n'+common_policy_frame()  # Common signed layer, even with intake/GSD/UDR off.
        frame = self.frame(sid)
        if not frame["receipt_required"]:
            return original + ("\n" + frame["text"] if frame["text"] else "")
        with self.engine.store.db() as db:
            row = db.execute("SELECT token,status FROM context_admissions WHERE profile_id=? AND sid=? AND turn_id=?", (self.engine.profile, sid, turn)).fetchone()
            if not row or row["status"] != "preparing":
                raise PermissionError("intake_native_context_preparation_required")
            value = {"basis": frame["basis"], "context_hash": digest(frame["text"]),
                     "work_ref": frame["work_ref"], "status": "prepared_not_dispatched", "attempts": []}
            db.execute("INSERT INTO intake_publications VALUES(?,?,?,?,?,?,?)", (self.engine.profile, sid, turn, row["token"], canonical(value), digest(value), time.time()))
        if self.frame(sid)["basis"] != frame["basis"]:
            raise PermissionError("intake_basis_changed_during_preparation")
        return original + ("\n" + frame["text"] if frame["text"] else "")

    def request(self, request, session_id="", platform="", **kw):
        if not session_id or kw.get("execution_kind") == "auxiliary":
            return self.originals["request"](request, session_id=session_id, platform=platform, **kw)
        from agent.auxiliary_attempt import current_auxiliary_attempt
        if current_auxiliary_attempt() is not None:
            return self.originals["request"](request, session_id=session_id, platform=platform, **kw)
        turn = kw.get("turn_id")
        # Native compression may wrap the current goal. Restore it only from
        # the exact committed checkpoint BEFORE checking the Intake envelope.
        # Without that receipt restore_goal remains fail-closed.
        if self.host.modules:
            request=__import__('copy').deepcopy(request)
            self.host.checkpoints.restore_goal(session_id,turn,request)
        row = self.host.admission.ready(session_id, turn, request, refresh=True)
        frame = self.frame(session_id)
        if frame["receipt_required"]:
            publication = self._publication(session_id, row["token"])
            need_refresh = publication is None or publication["basis"] != frame["basis"]
        else:
            old = row.get("envelope") or ""
            need_refresh = (frame["text"] is not None and frame["text"] not in old
                            or '<BN_INTAKE_CONTEXT sha256=' in old
                            or '<BN_INTAKE_PREPARATION sha256=' in old
                            or frame["text"] is None and '<BN_OWNER_INTERVIEW_CONTROLS sha256=' in old)
        if row["status"] == "ready" and need_refresh:
            token = uuid.uuid4().hex
            retired = json.loads(row["retired"]) + [row["envelope"]]
            with self.engine.store.db() as db:
                changed = db.execute("UPDATE context_admissions SET status='preparing',token=?,context_hash=NULL,envelope=NULL,retired=? WHERE profile_id=? AND sid=? AND turn_id=? AND token=? AND status='ready'", (token, canonical(retired), self.engine.profile, session_id, turn, row["token"])).rowcount
                if changed != 1:
                    raise PermissionError("intake_native_context_refresh_superseded")
                db.execute("UPDATE sessions SET context=NULL,turn=NULL WHERE profile_id=? AND sid=?", (self.engine.profile, session_id))
            row = {**row, "token": token, "status": "preparing"}
        if row["status"] == "preparing":
            try:
                self.host.admission.publish(session_id, row["token"], self.host.assembled_context(session_id, row["query"], turn))
            except BaseException:
                self.host.admission.fail(session_id, row["token"])
                raise
        if self.engine.documents is not None and not self.engine.store.binding(session_id)["readonly"]:
            packet = self.frame(session_id)
            work = packet["work_ref"]
            refs = []
            if work and frame["receipt_required"]:
                state = self.engine.status(session_id, work)["state"]
                if isinstance(state, dict) and state["lifecycle"] == "active" and state.get("selected_exposition"):
                    if state.get("selected_application"):
                        sources = self.engine.analysis.application_packet(session_id, work, state["selected_application"])["source_packet"]
                    else:
                        sources = self.engine.handoff(session_id, work, state["selected_exposition"])["packet"]
                    refs = sources.get("documents", [])
            # Off/empty paths do not initialize or require the optional native ledger.
            with self.engine.store.db() as db:
                exists = db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('document_pending','intake_document_links')").fetchone()[0] == 2
            if refs:
                self.engine.documents.stage(session_id, work, refs)
            elif exists:
                self.engine.documents.retire_unselected(session_id, work, [])
        result = self.originals["request"](request, session_id=session_id, platform=platform, **kw)
        if result and isinstance(result.get("request"), dict):
            result = {**result, "request": self.visuals.inject(session_id, result["request"])}
        return result

    def validate(self, sid, turn):
        row = self.host.admission.row(sid, turn)
        frame = self.frame(sid)
        if not frame["receipt_required"]:
            if (frame["text"] is not None and frame["text"] not in row["envelope"]
                    or '<BN_INTAKE_CONTEXT sha256=' in row["envelope"]
                    or '<BN_INTAKE_PREPARATION sha256=' in row["envelope"]
                    or frame["text"] is None and '<BN_OWNER_INTERVIEW_CONTROLS sha256=' in row["envelope"]):
                raise PermissionError("owner_constraints_or_intake_off_not_delivered")
            return None
        publication = self._publication(sid, row["token"])
        if not publication or publication["basis"] != frame["basis"] or publication["context_hash"] != digest(frame["text"]):
            raise PermissionError("intake_current_context_not_delivered")
        # The common signature validates the complete envelope at the final boundary.
        return row["token"]

    def control(self, payload, session_id="", platform="", execution_kind="", **kw):
        storage_failure=self.storage_admission()
        if storage_failure is not None:return storage_failure
        from hermes_cli.execution_control import ExecutionDecision
        if execution_kind == "auxiliary":
            return self.originals["model_control"](payload, session_id=session_id, platform=platform, execution_kind=execution_kind, **kw)
        try:
            token = self.validate(session_id, kw.get("turn_id"))
            visual_sources = self.visuals.validate(session_id, payload["request"])
        except Exception as exc:
            return ExecutionDecision(False, "intake_context_control:" + safe_reason(exc))
        decision = self.originals["model_control"](payload, session_id=session_id, platform=platform, execution_kind=execution_kind, **kw)
        if decision.allow:
            try:
                self.validate(session_id, kw.get("turn_id"))
                self.visuals.validate(session_id, payload["request"])
            except Exception as exc:
                if decision.on_abort:
                    decision.on_abort("intake_basis_changed_at_final_boundary")
                return ExecutionDecision(False, "intake_context_control:" + safe_reason(exc))
        if decision.allow and token:
            value = self._publication(session_id, token)
            attempt = {"api_request_id": kw.get("api_request_id"), "retry": kw.get("retry_count", 0),
                       "request_hash": digest({k: v for k, v in payload["request"].items() if k not in {"timeout", "http_client"}}),
                       "retained_visual_sources": visual_sources,
                       "status": "common_controller_admitted_not_provider_outcome"}
            if attempt not in value["attempts"]:
                value["attempts"].append(attempt)
                with self.engine.store.db() as db:
                    db.execute("UPDATE intake_publications SET body=?,sha256=? WHERE profile_id=? AND sid=? AND token=?", (canonical(value), digest(value), self.engine.profile, session_id, token))
        if decision.allow:
            try:
                abort = self.outcomes.admit(session_id, kw.get("api_request_id"), kw.get("retry_count", 0), payload["request"])
            except Exception as exc:
                if decision.on_abort:
                    decision.on_abort("intake_application_admission_failed")
                return ExecutionDecision(False, "intake_application_control:" + safe_reason(exc))
            if abort is not None:
                def combined_abort(reason):
                    try:
                        if decision.on_abort:
                            decision.on_abort(reason)
                    finally:
                        abort(reason)
                return ExecutionDecision(True, reservation_id=decision.reservation_id, on_abort=combined_abort)
        return decision
