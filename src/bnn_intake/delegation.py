"""Addressed inheritance on existing native grants. No launcher or task registry."""
import copy
import hashlib
import json
import time
from .contracts import DELEGATION_ACTIONS, validate
from .interview import default_policy
from .storage import canonical, digest, read, verify


class Delegation:
    def __init__(self, engine, host):
        self.engine, self.host, self.store = engine, host, engine.store

    @staticmethod
    def selector(spec):
        return canonical([spec["module"], spec["role"], spec["native_task_id"]])

    def state(self, sid, work, active=False):
        self.engine._work(sid, work, writable=True)
        with self.store.db() as db:
            verify(db); state = read(db, self.engine.profile, work)
        if state is None:
            raise ValueError("intake_prepare_required")
        if active and (self.engine.mode(sid)["mode"] == "off" or state["lifecycle"] != "active"):
            raise PermissionError("intake_handoff_inactive")
        return state

    def packet(self, parent, state, spec, *, basis_refs=None):
        if spec["role"] in {"G", "S", "D"} and spec["module"] != "gsd":
            raise PermissionError("intake_GSD_role_requires_canonical_module")
        if any(key not in state["gaps"] for key in spec["gap_ids"]):
            raise ValueError("intake_handoff_gap_missing")
        packet = self.engine.handoff(parent, state["work_ref"], spec["exposition_id"], _read_only=True)["packet"]
        app = None
        if spec["application_id"]:
            value = self.engine.analysis.application_packet(parent, state["work_ref"], spec["application_id"], active=False)
            app = value["application"]
            if app["exposition_id"] != spec["exposition_id"]:
                raise ValueError("intake_handoff_application_exposition_mismatch")
            packet = value["source_packet"]
        if packet.get("research_results") or packet.get("derived_products"):
            # These parent-only sources have no qualified native child visibility
            # contract yet. A parent summary cannot replace their source rights.
            raise PermissionError("intake_parent_only_derived_source_needs_explicit_child_adapter")
        packet = copy.deepcopy(packet)
        from .profile_contract import apply, checked
        apply(packet,checked(state))
        packet.pop("work_revision", None)  # Unrelated parent bookkeeping is not new evidence.
        document_obligations = None
        if packet.get("documents"):
            packet["documents"], document_obligations = self.engine.documents.role_references(
                parent, state["work_ref"], packet["documents"], spec["role"])
            from bn_memory.assignment_resources import prepare
            prepare(self.host, parent, spec["role"], "intake-document-preview",
                    {"documents": list(dict.fromkeys(ref["doc_id"] for ref in packet["documents"]))}, persist=False)
        gaps = {key: {field: state["gaps"][key][field] for field in ["id", "question", "kind", "material", "affected", "required_result"]}
                for key in spec["gap_ids"]}
        result = {"work_ref": state["work_ref"], "profile": state["contract"]["profile"],
                  "handoff_id": spec["id"], "module": spec["module"], "analytical_role": spec["role"],
                  "purpose": spec["purpose"], "contract": state["contract"], "gaps": gaps,
                  "source_packet": packet, "method_application": app,
                  "scientific_acceptance": False,
                  "restrictions": {"direct_owner_interview": False, "delegate_again": False,
                      "change_parent_state": False, "lower_profile": False, "new_budget_created": False},
                  "result_contract": "use_native_module_leaf_contract; return_typed_needs_to_parent; unreviewed"}
        if document_obligations is not None:
            result["document_obligations"] = document_obligations
        refs = state.get("research_basis", {}) if basis_refs is None else basis_refs
        if spec["module"] == "research" or refs:
            # Reference only: originals/methods already occur once in source_packet.
            result["research_basis_references"] = copy.deepcopy(refs)
        self.bound(result)
        return result

    def bound(self, value):
        limit = self.store.config.get("intake_context_bytes", 32768)
        if type(limit) is not int or limit < 1 or len(canonical(value).encode()) > limit:
            raise PermissionError("intake_handoff_split_required_do_not_trim")

    def preflight(self, sid, args):
        validate(args, DELEGATION_ACTIONS)
        state = self.state(sid, args["work_ref"], active=args["action"] in {"plan_handoff", "bind_research", "capture_product", "publish_acceptance", "finish_research"})
        if args['action'] in {'publish_acceptance','finish_research'}:
            from .research_basis import ResearchBasis
            ResearchBasis(self).native(sid,args['work_ref'],args['run_id'])
            from .acceptance import current
            if not current(self.engine,sid,state)['report_release_allowed']:raise PermissionError('intake_current_system_acceptance_required')
        if args["action"] == "capture_product":
            from .product_capture import ProductCapture
            ProductCapture(self).prepare(sid, args)
        if args["action"] == "bind_research":
            from .research_basis import ResearchBasis
            bridge = ResearchBasis(self)
            bridge.native(sid, args["work_ref"], args["run_id"])
            bridge.basis(sid, state, args["exposition_id"])
        if args["action"] == "plan_handoff":
            from .profile_contract import current_minimum
            current_minimum(self.engine,state)
            spec = args["handoff"]
            if spec["module"] == "research":
                self.require_research_basis(sid, args["work_ref"])
            self.packet(sid, state, spec)
            if spec["native_task_id"] is not None:
                task = self.native_task(spec["native_task_id"])
                body = json.loads(task["body"])
                if (task["root_sid"] != sid or body["role"] != spec["role"]
                        or body.get("module", "research") != spec["module"]):
                    raise PermissionError("intake_handoff_native_task_mismatch")

    def native_task(self, task_id):
        with self.store.db() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='native_tasks'").fetchone():
                raise PermissionError("intake_native_task_registry_unavailable")
            row = db.execute("SELECT * FROM native_tasks WHERE profile_id=? AND id=?", (self.engine.profile, task_id)).fetchone()
        if row is None:
            raise PermissionError("intake_native_task_not_found")
        return dict(row)

    def call(self, sid, args):
        self.preflight(sid, args)
        action, work = args["action"], args["work_ref"]
        state = self.state(sid, work)
        if action == "plan_handoff":
            spec = args["handoff"]; packet = self.packet(sid, state, spec)
            anchors = self.engine.analysis.anchors(sid, work, spec["exposition_id"], allow_inactive=True)[1]
            def update(db, current):
                self.engine.analysis.assert_sources_current(db, sid, anchors)
                if current["contract"] != state["contract"]:
                    raise PermissionError("intake_handoff_contract_changed")
                plans = current.setdefault("handoff_plans", {})
                if spec["id"] in plans:
                    raise ValueError("intake_handoff_id_exists")
                plans[spec["id"]] = {"spec": copy.deepcopy(spec), "basis": digest(packet),
                                      "basis_refs": copy.deepcopy(state.get("research_basis", {}))}
                current.setdefault("active_handoffs", {})[self.selector(spec)] = spec["id"]
                return current, {"handoff_id": spec["id"], "basis": digest(packet), "child_started": False}
            return self.engine.commit(sid, args, update)
        if action == "status":
            with self.store.db() as db:
                rows = db.execute("SELECT assignment_id,child_sid,sha256 FROM intake_delegations WHERE profile_id=? AND work_id=? AND parent_sid=? ORDER BY created",
                                  (self.engine.profile, work, sid)).fetchall()
            return {"plans": state.get("handoff_plans", {}), "active_handoffs": state.get("active_handoffs", {}),
                    "native_bindings": [dict(r) for r in rows], "scientific_acceptance": False}
        if action == "read_gsd_result":
            from .gsd_delegation import read_result
            return read_result(self, sid, args)
        if action in {"bind_research", "read_research_basis", "publish_acceptance", "finish_research"}:
            from .research_basis import ResearchBasis
            bridge = ResearchBasis(self)
            if action=='publish_acceptance':return bridge.publish_acceptance(sid,args)
            if action=='finish_research':return bridge.finish_research(sid,args)
            return bridge.bind(sid, args) if action == "bind_research" else bridge.read(sid, work, args["run_id"])
        if action in {"capture_product", "read_capture"}:
            from .product_capture import ProductCapture
            bridge = ProductCapture(self)
            return bridge.capture(sid, args) if action == "capture_product" else bridge.read(sid, work, args["capture_id"])
        task = self.native_task(args["native_task_id"])
        with self.store.db() as db:
            row = db.execute("SELECT * FROM native_task_history WHERE profile_id=? AND id=? AND revision=?",
                             (self.engine.profile, task["id"], args["native_revision"])).fetchone()
        if row is None or hashlib.sha256(row["body"].encode()).hexdigest() != row["sha256"]:
            raise PermissionError("intake_native_task_history_integrity_failed")
        body = json.loads(row["body"])
        if row["state"] not in {"succeeded", "needs"} or body.get("product") is None:
            raise PermissionError("intake_native_task_has_no_usable_result")
        binding = self.binding(body["child_sid"])
        if (binding["work_ref"] != work or binding["assignment_id"] != body["assignment_id"]
                or task["root_sid"] != binding["parent_sid"]):
            raise PermissionError("intake_native_result_handoff_mismatch")
        self.verify_reader_sources(sid, body["child_sid"])
        receipt = body.get("receipt") or {}
        if receipt.get("terminal_state") != "SUCCEEDED" or not receipt.get("result_hash"):
            raise PermissionError("intake_actual_native_terminal_receipt_required")
        raw = canonical(body["product"])
        start, limit = args.get("offset", 0), args.get("limit", 4096)
        if start > len(raw):
            raise ValueError("intake_native_result_offset_outside_product")
        end = min(len(raw), start + limit)
        return {"native_task_id": task["id"], "native_revision": row["revision"], "native_state": row["state"],
                "text": raw[start:end], "start": start, "end": end,
                "product_sha256": hashlib.sha256(raw.encode()).hexdigest(),
                "fragment_sha256": hashlib.sha256(raw[start:end].encode()).hexdigest(),
                "next_offset": end if end < len(raw) else None, "complete": start == 0 and end == len(raw),
                "native_receipt": receipt, "handoff_basis": binding["basis"], "scientific_acceptance": False,
                "origin_parent_sid": task["root_sid"], "reader_sid": sid,
                "reading_level": "parsed_native_child_product_not_raw_transport_or_independent_evidence"}

    def verify_reader_sources(self, sid, child):
        packet = self.child_packet(child, allow_inactive=True)
        # A resumed session may read the same durable work, but it must possess
        # its OWN current source rights; the old child's grant isn't a deputy.
        from bn_memory.assignment_resources import read as assigned_resources, prepare as prepare_resources
        child_binding = self.store.binding(child)
        assigned = assigned_resources(self.store, child_binding)
        if assigned is None:
            raise PermissionError("intake_native_assignment_resources_required")
        # Include ALL native inputs, even those added to a continuation outside
        # the initial intake exposition. A revoked extra source is still a dependency.
        for identifier, ref in assigned["records"].items():
            self.store.read(child, identifier, ref["revision"])
            current = self.store.read(sid, identifier, ref["revision"])
            if current["content_hash"] != ref["content_hash"]:
                raise PermissionError("intake_resumed_reader_source_changed")
        other = {key: list(assigned[key]) for key in ["files", "code", "documents", "library", "data"] if assigned[key]}
        if other:
            checked = prepare_resources(self.host, sid, child_binding["role"], "readback-check", other, persist=False)
            if any(checked[key] != assigned[key] for key in ["files", "code", "documents", "library", "data"]):
                raise PermissionError("intake_native_extra_source_version_changed")
        for ref in packet["source_packet"].get("documents", []):
            self.host.document_store().source(sid, ref["doc_id"])

    def launch_plan(self, payload, identity):
        parent = identity.get("session_id", "")
        if self.engine.mode(parent)["mode"] == "off":
            return None
        self.store.writable(parent)
        with self.store.db() as db:
            work = db.execute("SELECT work_id FROM working_bindings WHERE profile_id=? AND sid=?", (self.engine.profile, parent)).fetchone()
            state = read(db, self.engine.profile, work[0]) if work else None
            assignment = (payload.get("handle") or {}).get("correlation_id")
            grant = db.execute("SELECT * FROM launch_grants WHERE profile_id=? AND id=?", (self.engine.profile, assignment)).fetchone()
            task = None
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='native_tasks'").fetchone():
                task = db.execute("SELECT id FROM native_tasks WHERE profile_id=? AND json_extract(body,'$.assignment_id')=?", (self.engine.profile, assignment)).fetchone()
        if not state:
            return None
        if state["lifecycle"] != "active":
            raise PermissionError("intake_parent_work_not_active")
        if grant is None or grant["state"] != "prepared" or grant["parent_sid"] != parent:
            raise PermissionError("intake_prepared_native_launch_required")
        if not self.host.config.get("w3_enabled"):
            raise PermissionError("intake_native_assignment_resources_required")
        if grant["module"] == "research":
            self.require_research_basis(parent, state["work_ref"])
        exact = canonical([grant["module"], grant["role"], task[0] if task else None])
        default = canonical([grant["module"], grant["role"], None])
        selected = state.get("active_handoffs", {}).get(exact) or state.get("active_handoffs", {}).get(default)
        if selected is None:
            raise PermissionError("intake_plan_role_handoff_before_launch")
        plan = state["handoff_plans"][selected]
        refs = self.frozen_basis_refs(parent, state["work_ref"], selected, plan)
        if refs != state.get("research_basis", {}):
            raise PermissionError("intake_current_research_basis_needs_new_handoff_plan")
        packet = self.packet(parent, state, plan["spec"], basis_refs=refs)
        if digest(packet) != plan["basis"]:
            raise PermissionError("intake_planned_handoff_basis_changed")
        return {"assignment_id": assignment, "parent_sid": parent, "work_ref": state["work_ref"],
                "spec": plan["spec"], "basis": plan["basis"], "basis_refs": refs}

    def frozen_basis_refs(self, parent, work, plan_id, plan):
        if "basis_refs" in plan:return copy.deepcopy(plan["basis_refs"])
        # Explicit compatibility with 0.8: recover ONLY the exact immutable
        # revision in which this handoff first appeared, never today's basis.
        with self.store.db() as db:
            revisions = db.execute("SELECT revision FROM intake_revisions WHERE profile_id=? AND work_id=? ORDER BY revision",
                (self.engine.profile, work)).fetchall()
            for row in revisions:
                state = read(db,self.engine.profile,work,row[0])
                original = state.get("handoff_plans",{}).get(plan_id)
                if original and original["basis"] == plan["basis"] and original["spec"] == plan["spec"]:
                    return copy.deepcopy(state.get("research_basis",{}))
        raise PermissionError("intake_original_handoff_basis_revision_required")

    def require_research_basis(self, sid, work):
        from .research_basis import ResearchBasis
        # The existing module determines its current run; user JSON cannot pick
        # a foreign Foundation work or invent a lease/admission.
        native = self.host.modules.research(sid)
        if native is None:raise PermissionError("intake_active_native_research_required")
        row, _ = native.active(sid)
        return ResearchBasis(self).read(sid, work, row["id"])

    def bind(self, plan, child):
        if plan is None:
            return
        actual = self.store.binding(child)
        with self.store.db() as db:
            grant = db.execute("SELECT * FROM launch_grants WHERE profile_id=? AND id=?", (self.engine.profile, plan["assignment_id"])).fetchone()
        if (grant is None or grant["state"] != "admitted" or grant["child_sid"] != child
                or actual["parent"] != plan["parent_sid"] or actual["assignment_id"] != plan["assignment_id"]
                or actual["role"] != plan["spec"]["role"]):
            raise PermissionError("intake_admitted_child_binding_required")
        # Check every original using the CHILD'S actual source rights before saving.
        self.validated_packet(plan, child)
        with self.store.db() as db:
            db.execute("BEGIN IMMEDIATE"); verify(db)
            row = db.execute("SELECT work_id FROM working_bindings WHERE profile_id=? AND sid=?", (self.engine.profile, child)).fetchone()
            if not row or row[0] != plan["work_ref"]:
                raise PermissionError("intake_child_work_binding_changed")
            db.execute("INSERT INTO intake_delegations VALUES(?,?,?,?,?,?,?,?)", (self.engine.profile,
                plan["assignment_id"], plan["work_ref"], plan["parent_sid"], child, canonical(plan), digest(plan), time.time()))

    def binding(self, child):
        actual = self.store.binding(child)
        with self.store.db() as db:
            verify(db, {"intake_delegations"})
            row = db.execute("SELECT body,sha256 FROM intake_delegations WHERE profile_id=? AND assignment_id=? AND child_sid=?",
                             (self.engine.profile, actual["assignment_id"], child)).fetchone()
        if row is None:
            raise PermissionError("intake_native_child_handoff_required")
        value = json.loads(row["body"])
        if digest(value) != row["sha256"]:
            raise PermissionError("intake_delegation_integrity_failed")
        if actual["parent"] != value["parent_sid"] or actual["role"] != value["spec"]["role"]:
            raise PermissionError("intake_child_handoff_identity_changed")
        return value

    def validated_packet(self, binding, child):
        state = self.state(binding["parent_sid"], binding["work_ref"])
        refs = self.frozen_basis_refs(binding["parent_sid"], binding["work_ref"], binding["spec"]["id"], binding)
        packet = self.packet(binding["parent_sid"], state, binding["spec"], basis_refs=refs)
        if digest(packet) != binding["basis"]:
            raise PermissionError("intake_assigned_handoff_basis_changed")
        for record in packet["source_packet"]["records"]:
            ref = record["ref"]
            actual = self.store.read(child, ref["id"], ref["revision"])
            if actual["content_hash"] != ref["content_hash"]:
                raise PermissionError("intake_assigned_record_version_changed")
        for ref in packet["source_packet"].get("documents", []):
            row = self.host.document_store().source(child, ref["doc_id"])
            if row["sha256"] != ref["source_sha256"]:
                raise PermissionError("intake_assigned_document_version_changed")
            if self.host.document_role(child) != ref["phase"]:
                raise PermissionError("intake_document_recipient_phase_mismatch")
            if not self.host.document_store().membership(child, ref["corpus_id"], ref["doc_id"], ref["unit"]):
                raise PermissionError("intake_document_recipient_membership_required")
        return packet

    def child_packet(self, child, allow_inactive=False):
        binding = self.binding(child)
        state = self.state(binding["parent_sid"], binding["work_ref"], active=not allow_inactive)
        packet = self.validated_packet(binding, child)
        packet["assignment_id"] = binding["assignment_id"]
        packet["interview_controls"] = {k: default_policy().get(k) for k in ["state", "max_questions", "blocked_topics", "deadline"]}
        packet["interview_controls"].update({k: state.get("interview", default_policy())[k] for k in packet["interview_controls"]})
        self.bound(packet)
        return packet
