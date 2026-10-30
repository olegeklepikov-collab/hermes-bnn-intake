"""Versioned preparation on an existing host work card. No networking or model calls."""
import copy
import json
import time
from . import SCHEMA_VERSION
from .contracts import PROFILES, validate
from .storage import append, canonical, digest, read, verify

MUTATIONS = {"prepare", "expose", "revise", "assess", "pause", "resume", "cancel", "select_context", "adopt_profile"}


class Intake:
    def __init__(self, store, catalogue=None, documents=None):
        self.store = store
        self.profile = store.profile
        self._catalogue = catalogue
        self.documents = documents
        self.analysis = None
        self.research = None
        self.delegation = None

    @property
    def catalogue(self):
        if self._catalogue is None:
            from .catalogue import Catalogue
            self._catalogue = Catalogue()
        return self._catalogue

    def _work(self, sid, work, writable=False):
        binding = self.store.writable(sid) if writable else self.store.binding(sid)
        with self.store.db() as db:
            row = db.execute("SELECT w.work_id,c.project FROM working_bindings w JOIN working_cards c ON c.profile_id=w.profile_id AND c.work_id=w.work_id WHERE w.profile_id=? AND w.sid=?", (self.profile, sid)).fetchone()
        if not row or row["work_id"] != work or row["project"] != binding["project"]:
            raise PermissionError("intake_native_work_binding_required")
        return binding

    def mode(self, sid):
        binding = self.store.binding(sid)
        with self.store.db() as db:
            marker = db.execute("SELECT value FROM meta WHERE key='semantic_intake_contract'").fetchone()
            if not marker:
                return {"mode": "off", "revision": 0, "initialized": False}
            verify(db, tables={"intake_modes"})
            # Children inherit their live parent mode; they cannot change it.
            cursor = sid
            while cursor:
                row = db.execute("SELECT mode,revision FROM intake_modes WHERE profile_id=? AND sid=?", (self.profile, cursor)).fetchone()
                if row:
                    return {**dict(row), "initialized": True, "inherited": cursor != sid}
                parent = db.execute("SELECT parent FROM sessions WHERE profile_id=? AND sid=?", (self.profile, cursor)).fetchone()
                cursor = parent[0] if parent else None
        return {"mode": "off", "revision": 0, "initialized": True}

    def set_mode(self, sid, mode, expected_revision, authenticated_actor):
        """Trusted owner command only. Never registered as a model-writable action."""
        self.store.writable(sid)
        if mode not in {"off", "auto", "on"} or type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("intake_mode_contract_invalid")
        if not isinstance(authenticated_actor, str) or not authenticated_actor.strip():
            raise PermissionError("intake_authenticated_actor_required")
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE"); verify(db, tables={"intake_modes"} if mode == "off" else None)
            old = db.execute("SELECT * FROM intake_modes WHERE profile_id=? AND sid=?", (self.profile, sid)).fetchone()
            revision = old["revision"] if old else 0
            if revision != expected_revision:
                raise ValueError("intake_mode_revision_conflict")
            if not old or old["mode"] != mode:
                revision += 1
                db.execute("INSERT INTO intake_modes VALUES(?,?,?,?,?) ON CONFLICT(profile_id,sid) DO UPDATE SET revision=excluded.revision,mode=excluded.mode,actor=excluded.actor", (self.profile, sid, revision, mode, authenticated_actor))
                db.execute("INSERT INTO audit(event,sid_hash,record_id,revision,at) VALUES(?,?,?,?,?)", (canonical({"event": "intake_mode", "mode": mode, "actor": authenticated_actor}), self.store.session_hash(sid), None, revision, time.time()))
        return self.mode(sid)

    def _source(self, sid, ref):
        if ref["kind"] == "research_result":
            if self.research is None:
                raise PermissionError("intake_research_bridge_unavailable")
            with self.store.db() as db:
                work = db.execute("SELECT work_id FROM working_bindings WHERE profile_id=? AND sid=?", (self.profile, sid)).fetchone()
            if not work:
                raise PermissionError("intake_native_work_binding_required")
            result = self.research.resolve(sid, work[0], ref)
            return {k: v for k, v in result.items() if k != "content"}
        if ref["kind"] == "document_unit":
            if self.documents is None:
                raise PermissionError("intake_document_bridge_unavailable")
            with self.store.db() as db:
                work = db.execute("SELECT work_id FROM working_bindings WHERE profile_id=? AND sid=?", (self.profile, sid)).fetchone()
            if not work:
                raise PermissionError("intake_native_work_binding_required")
            return self.documents.resolve(sid, work[0], ref)
        row = self.store.read(sid, ref["id"], ref["revision"])
        if row.get("dependency_status", "current") != "current":
            raise PermissionError("intake_source_dependency_unavailable")
        return {**ref, "content_hash": row["content_hash"], "scope": row["scope"],
                "source_ref": row["source"], "reading_level": "host_record",
                "semantic_review": "unreviewed"}

    def _inputs(self, sid, exposition):
        return [self._source(sid, ref) for ref in exposition["input_refs"]]

    def call(self, sid, args):
        validate(args)
        action = args["action"]
        binding = self._work(sid, args["work_ref"], action in MUTATIONS)
        if action == "status":
            return self.status(sid, args["work_ref"])
        if binding["readonly"]:
            raise PermissionError("intake_child_scoped_adapter_not_yet_qualified")
        if action == 'profile_status':
            from .profile_contract import status
            with self.store.db() as db:state=read(db,self.profile,args['work_ref'])
            if not isinstance(state,dict):raise ValueError('intake_prepare_required')
            return status(self,state)
        if action in {'acceptance_status','acceptance_report'}:
            from .acceptance import current,report
            with self.store.db() as db:state=read(db,self.profile,args['work_ref'])
            return report(self,sid,state) if action=='acceptance_report' else current(self,sid,state)
        if action == "history":
            with self.store.db() as db:
                verify(db)
                rows = db.execute("SELECT revision,sha256,created FROM intake_revisions WHERE profile_id=? AND work_id=? ORDER BY revision", (self.profile, args["work_ref"])).fetchall()
            return {"revisions": [dict(r) for r in rows]}
        if action == "handoff":
            return self.handoff(sid, args["work_ref"], args["exposition_id"])
        mode = self.mode(sid)
        if mode["mode"] == "off":
            raise PermissionError("intake_disabled_for_session")
        if action == "prepare":
            minimum = self.store.config.get("intake_minimum_profile", "eMVP")
            if minimum not in PROFILES:
                raise ValueError("intake_host_profile_invalid")
            if PROFILES.index(args["contract"]["profile"]) < PROFILES.index(minimum):
                raise PermissionError("intake_required_profile_not_met")
        sources = self._inputs(sid, args["exposition"]) if action == "expose" else None
        acceptance_plan=None
        if action=='assess':
            from .acceptance import prepare
            with self.store.db() as db:state=read(db,self.profile,args['work_ref'])
            acceptance_plan=prepare(self,sid,state) if args['assessment']['state']!='resource_paused' else {'outputs':[],'anchors':[]}
        def update(db, state):
            extra={}
            if action == "prepare":
                if state:
                    raise ValueError("intake_work_already_prepared")
                state = {"schema_version": SCHEMA_VERSION, "work_ref": args["work_ref"],
                         "project": binding["project"], "contract": args["contract"],
                         "lifecycle": "active", "expositions": {}, "gaps": {},
                         "paths": {}, "local_methods": {}, "responses": [], "assessments": [],
                         "semantic_acceptance": "unreviewed", "revision": 0}
                from .profile_contract import make
                state['profile_contract']=make(args['contract']['profile'])
            else:
                extra=self._apply(db, sid, state, action, args, sources,acceptance_plan) or {}
            return state, extra
        return self.commit(sid, args, update, allow_new=action == "prepare")

    def commit(self, sid, args, update, *, allow_new=False, require_on=True, allow_paused=False, on_replay=None):
        """Shared trusted transaction for work and interview; callbacks are code, not data."""
        binding = self._work(sid, args["work_ref"], writable=True)
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE"); verify(db)
            current = db.execute("SELECT s.readonly,s.project,w.work_id FROM sessions s JOIN working_bindings w ON w.profile_id=s.profile_id AND w.sid=s.sid WHERE s.profile_id=? AND s.sid=?", (self.profile, sid)).fetchone()
            if not current or current["readonly"] or current["project"] != binding["project"] or current["work_id"] != args["work_ref"]:
                raise PermissionError("intake_binding_changed_before_effect")
            policy = db.execute("SELECT mode FROM intake_modes WHERE profile_id=? AND sid=?", (self.profile, sid)).fetchone()
            if require_on and (not policy or policy[0] == "off"):
                raise PermissionError("intake_disabled_for_session")
            previous = db.execute("SELECT input_hash,result FROM intake_operations WHERE profile_id=? AND work_id=? AND operation_id=?", (self.profile, args["work_ref"], args["operation_id"])).fetchone()
            request_hash = digest(args)
            if previous:
                if previous["input_hash"] != request_hash:
                    raise ValueError("intake_idempotency_input_conflict")
                if on_replay is not None:
                    on_replay(db, read(db, self.profile, args["work_ref"]))
                return {**json.loads(previous["result"]), "replayed": True}
            state = read(db, self.profile, args["work_ref"])
            actual = state["revision"] if state else 0
            if actual != args["expected_revision"]:
                raise ValueError("intake_work_revision_conflict")
            if state is None and not allow_new:
                raise ValueError("intake_prepare_required")
            if state:
                state = copy.deepcopy(state)
                if state["lifecycle"] == "cancelled":
                    raise PermissionError("intake_work_cancelled")
                if not allow_paused and state["lifecycle"] == "paused" and args["action"] != "resume":
                    raise PermissionError("intake_work_paused")
            state, extra = update(db, state)
            from .interview import default_policy
            state.setdefault("interview", default_policy())
            state.setdefault("questions", {})
            state["schema_version"] = SCHEMA_VERSION
            state["revision"] = actual + 1
            state["last_action"] = args["action"]
            append(db, self.profile, args["work_ref"], state)
            result = {"ok": True, "work_ref": args["work_ref"], "revision": state["revision"],
                      "lifecycle": state["lifecycle"], "content_hash": digest(state),
                      "semantic_acceptance": "unreviewed", "external_effect_performed": False,
                      "local_effect_performed": True, "replayed": False, **extra}
            db.execute("INSERT INTO intake_operations VALUES(?,?,?,?,?)", (self.profile, args["work_ref"], args["operation_id"], request_hash, canonical(result)))
        return result

    def _apply(self, db, sid, state, action, args, sources,acceptance_plan=None):
        if action == 'adopt_profile':
            from .profile_contract import checked, current_minimum, make
            current_minimum(self,state)
            if checked(state) is not None:raise ValueError('intake_profile_contract_already_current')
            if state.get('selected_application'):raise PermissionError('intake_clear_selected_application_before_profile_adoption')
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='native_tasks'").fetchone():
                if db.execute("SELECT 1 FROM native_tasks n JOIN working_bindings b ON n.root_sid=b.sid AND n.profile_id=b.profile_id WHERE b.profile_id=? AND b.work_id=? AND n.state IN ('running','launch_reserved','cancelling','unknown') LIMIT 1",(self.profile,state['work_ref'])).fetchone():
                    raise PermissionError('intake_profile_adoption_requires_quiescent_children')
            state['profile_contract']=make(state['contract']['profile'])
            state['profile_adoption_reason']=args['reason'];state['active_handoffs']={}
            return
        if action == "select_context":
            selected = args["exposition_id"]
            if selected is not None and selected not in state["expositions"]:
                raise ValueError("intake_exposition_missing")
            state["selected_exposition"] = selected
            app = state.get("method_applications", {}).get(state.get("selected_application"))
            if app and app["application"]["exposition_id"] != selected:
                state["selected_application"] = None
            return
        if action in {"pause", "resume", "cancel"}:
            if action == "resume" and state["lifecycle"] != "paused":
                raise ValueError("intake_not_paused")
            state["lifecycle"] = {"pause": "paused", "resume": "active", "cancel": "cancelled"}[action]
            state["lifecycle_reason"] = args["reason"]
            return
        if action == "expose":
            exp = copy.deepcopy(args["exposition"])
            if exp["id"] in state["expositions"]:
                raise ValueError("intake_exposition_id_exists_use_new_purpose_version")
            fingerprint = digest({"purpose": exp["purpose"], "question": exp["question"],
                                  "sources": sorted(sources, key=lambda s: (s["kind"], s["id"], s["revision"]) if s["kind"] == "record" else (s["kind"], canonical(s))),
                                  "depends_on": sorted(exp["depends_on"]),
                                  "method_ids": sorted(exp["method_ids"]), "contract": state["contract"]})
            for old in state["expositions"].values():
                if old.get("purpose_fingerprint") == fingerprint:
                    raise ValueError("intake_exposition_exact_repeat_use_existing:" + old["id"])
            for dependency in exp["depends_on"]:
                if dependency not in state["expositions"]:
                    raise ValueError("intake_dependency_missing")
            for method in exp["method_ids"]:
                if method not in state["local_methods"] and method not in self.catalogue.entries:
                    raise ValueError("intake_local_method_descriptor_required_not_prohibited")
            exp.update(sources=sources, purpose_fingerprint=fingerprint,
                       status="prepared_not_executed", semantic_review="unreviewed")
            state["expositions"][exp["id"]] = exp
            return
        if action == "revise":
            change = args["change"]
            kind = change["type"]
            if kind == "add_gap":
                gap = copy.deepcopy(change["gap"])
                if gap["id"] in state["gaps"]:
                    raise ValueError("intake_gap_id_exists")
                gap["research_state"] = "open"
                gap["response_state"] = "not_asked"
                state["gaps"][gap["id"]] = gap
            elif kind == "plan_path":
                path = change["path"]
                if path["gap_id"] not in state["gaps"] or path["id"] in state["paths"]:
                    raise ValueError("intake_path_binding_invalid")
                from .research import path_basis
                if any(path_basis(state, old) == path_basis(state, path) for old in state["paths"].values()):
                    raise ValueError("intake_exact_path_repeat_reuse_existing")
                state["paths"][path["id"]] = {**path, "claim_review": "unreviewed", "execution_receipt": None}
            elif kind == "add_method":
                desc = change["descriptor"]
                if desc["id"] in state["local_methods"] or desc["id"] in self.catalogue.entries:
                    raise ValueError("intake_method_id_exists")
                state["local_methods"][desc["id"]] = {**desc, "status": "design_descriptor_not_executed"}
            elif kind == "record_response":
                if change["gap_id"] not in state["gaps"]:
                    raise ValueError("intake_gap_missing")
                source = change["source_ref"]
                row = db.execute("SELECT s.body,s.sha256,s.project,w.work_id FROM working_sources s JOIN working_bindings w ON w.profile_id=s.profile_id AND w.sid=s.sid WHERE s.profile_id=? AND s.id=?", (self.profile, source["id"])).fetchone()
                import hashlib
                if not row or row["work_id"] != state["work_ref"] or row["project"] != state["project"]:
                    raise PermissionError("intake_native_response_origin_required")
                if source["start"] >= source["end"] or source["end"] > len(row["body"]) or hashlib.sha256(row["body"].encode()).hexdigest() != row["sha256"]:
                    raise PermissionError("intake_native_response_range_or_hash_invalid")
                state["responses"].append({**change, "quote": row["body"][source["start"]:source["end"]], "state_interpretation": "unreviewed"})
                # Interview result never closes the research obligation.
                state["gaps"][change["gap_id"]]["response_state"] = change["response_state"]
            return
        if action == "assess":
            assessment = args["assessment"]
            material = [g for g in state["gaps"].values() if g["material"] and g["research_state"] != "resolved"]
            if assessment["state"] == "sufficient" and material:
                raise PermissionError("intake_unresolved_material_gaps")
            if assessment["state"] == "limited":
                for gap in material:
                    paths = [p for p in state["paths"].values() if p["gap_id"] == gap["id"]]
                    if not paths or any(not p.get("review") for p in paths):
                        raise PermissionError("intake_research_paths_not_verified")
            completion = None
            if self.research is not None:
                completion = self.research.completion(db, sid, state)
                if assessment["state"] in {"sufficient", "limited"}:
                    if completion["unperformed_paths"] or any(g["material"] and g["missing_paths"] for g in completion["gaps"]):
                        raise PermissionError("intake_research_paths_not_verified")
                    if assessment["state"] == "sufficient" and any(g["material"] and not g["resolved"] for g in completion["gaps"]):
                        raise PermissionError("intake_unresolved_material_gaps")
                    if assessment["state"] == "limited" and (not assessment["limits"] or not assessment["remaining"]):
                        raise ValueError("intake_limited_assessment_requires_explicit_remainders")
            from .profile_contract import assess
            profile_assessment=assess(self,state,assessment)
            from .acceptance import finish
            accepted=finish(self,db,sid,state,assessment,completion,acceptance_plan)
            state["assessments"].append({**assessment, "status": accepted['status'],
                                         "semantic_review": "system_checked_with_declared_limits", "completion_accepted": accepted['system_acceptance_complete'],
                                         "research_completion": completion,'profile_assessment':profile_assessment,'system_acceptance':accepted})
            return {'system_acceptance':accepted,'report_release_allowed':accepted['report_release_allowed'],
                    'human_review_required':False}
        raise ValueError("intake_action_not_implemented")

    def status(self, sid, work):
        binding = self._work(sid, work)
        policy = self.mode(sid)
        if not policy["initialized"]:
            return {"mode": policy, "work_ref": work, "state": "not_initialized"}
        with self.store.db() as db:
            verify(db); state = read(db, self.profile, work)
        if state is None:
            return {"mode": policy, "work_ref": work, "state": "not_prepared"}
        if binding["readonly"]:
            if self.delegation is not None and policy["mode"] != "off":
                packet = self.delegation.child_packet(sid)
                return {"mode": policy, "work_ref": work, "revision": state["revision"],
                        "lifecycle": state["lifecycle"], "profile": packet["profile"],
                        "handoff_id": packet["handoff_id"], "role": packet["analytical_role"],
                        "child_scope": "assigned_native_handoff_read_only", "semantic_acceptance": "unreviewed"}
            return {"mode": policy, "work_ref": work, "revision": state["revision"],
                    "lifecycle": state["lifecycle"], "semantic_acceptance": "unreviewed",
                    "child_scope": "status_only_pending_qualified_handoff"}
        projection = state
        if state.get("analysis_products"):
            from .analysis import Analysis
            projection = {**state, "analysis_products": {key: Analysis.product_summary(value) for key, value in state["analysis_products"].items()}}
        return {"mode": policy, "state": projection, "content_hash": digest(state),
                "state_is_projection": projection is not state,
                "next": "prepare_or_execute_relevant_paths" if state["gaps"] else "perform_assigned_exposition",
                "semantic_acceptance": "unreviewed"}

    def handoff(self, sid, work, exposition_id, *, _read_only=False):
        self._work(sid, work, writable=True)
        state = self.status(sid, work)["state"]
        if not _read_only and (self.mode(sid)["mode"] == "off" or state["lifecycle"] != "active"):
            raise PermissionError("intake_handoff_inactive")
        from .profile_contract import checked, current_minimum, apply
        if not _read_only:current_minimum(self,state)
        if exposition_id not in state["expositions"]:
            raise ValueError("intake_exposition_missing")
        ordered = []; seen = set(); stack = [(exposition_id, False)]
        while stack:
            eid, expanded = stack.pop()
            if expanded:
                ordered.append(state["expositions"][eid])
                continue
            if eid in seen:
                continue
            seen.add(eid)
            stack.append((eid, True))
            stack.extend((parent, False) for parent in reversed(state["expositions"][eid]["depends_on"]))
        records = []; source_keys = set(); methods = {}; document_refs = []; research_results = {}
        for exp in ordered:
            for source in exp["sources"]:
                if source["kind"] == "research_result":
                    if self.research is None:
                        raise PermissionError("intake_research_bridge_unavailable")
                    from .contracts import RESEARCH_REF
                    ref = {k: source[k] for k in RESEARCH_REF["properties"]}
                    research_results[digest(ref)] = self.research.resolve(sid, work, ref)
                    continue
                if source["kind"] == "document_unit":
                    from .contracts import DOCUMENT_REF
                    document_refs.append({k: source[k] for k in DOCUMENT_REF["properties"]})
                    continue
                key = (source["id"], source["revision"])
                if key in source_keys:
                    continue
                row = self.store.read(sid, *key)
                if row.get("dependency_status", "current") != "current":
                    raise PermissionError("intake_source_dependency_unavailable")
                if row["content_hash"] != source["content_hash"]:
                    raise PermissionError("intake_source_changed")
                records.append({"ref": source, "content": row["content"]})
                source_keys.add(key)
            for mid in exp["method_ids"]:
                methods[mid] = state["local_methods"].get(mid) or self.catalogue.entries[mid]
        # A derived record does not hide its original inputs. Assemble the
        # record dependency closure iteratively, under the current reader rights.
        cursor = 0
        document_hashes = {ref["source_sha256"] for ref in document_refs}
        while cursor < len(records):
            ref = records[cursor]["ref"]; cursor += 1
            row = self.store.read(sid, ref["id"], ref["revision"])
            for dependency in row.get("source_dependencies", []):
                if dependency["kind"] == "source":
                    if dependency["sha256"] not in document_hashes:
                        raise PermissionError("intake_record_source_dependency_needs_explicit_document_exposition")
                    continue
                key = (dependency["id"],dependency["revision"])
                if key in source_keys:continue
                original = self.store.read(sid,*key)
                if original["content_hash"] != dependency["content_hash"] or original.get("dependency_status","current")!="current":
                    raise PermissionError("intake_derived_record_dependency_changed")
                source = self._source(sid,{"kind":"record","id":key[0],"revision":key[1]})
                records.append({"ref":source,"content":original["content"]}); source_keys.add(key)
        packet = {"schema_version": 1, "work_ref": work, "work_revision": state["revision"],
                  "contract": state["contract"], "exposition_id": exposition_id,
                  "dependencies": [e["id"] for e in ordered], "records": records,
                  "methods": methods, "semantic_acceptance": "unreviewed",
                  "input_kind": "untrusted_source_data_not_system_instructions"}
        apply(packet,checked(state))
        if document_refs:
            if self.documents is None:
                raise PermissionError("intake_document_bridge_unavailable")
            packet["documents"] = self.documents.closure(sid, work, document_refs)
        if research_results:
            packet["research_results"] = list(research_results.values())
        limit = self.store.config.get("intake_context_bytes", 32768)
        if type(limit) is not int or limit < 1:
            raise ValueError("intake_context_bound_invalid")
        if len(canonical(packet).encode()) > limit:
            raise PermissionError("intake_split_required_do_not_trim_dependencies")
        return {"packet": packet, "sha256": digest(packet), "delivered_to_model": False,
                "executor_started": False, "external_effect_performed": False}
