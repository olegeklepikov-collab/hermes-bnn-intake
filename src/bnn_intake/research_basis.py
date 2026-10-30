"""Explicit agreed intake basis in the actual Foundation Dolt object store.

No research runner, model loop, lease, primary task, or separate database is created.
The actual native UDR work must already be bound before intake is prepared there.
"""
import copy
import json
from .storage import canonical, digest, read


def unpack_basis(document):
    if document.get("contract") == "BNNIntakeBasisV1":
        return document["basis"]
    if document.get("contract") != "BNNIntakeBasisV2" or not isinstance(document.get("basis_json"), str):
        raise PermissionError("intake_Foundation_basis_encoding_unrecognized")
    try:
        basis = json.loads(document["basis_json"])
        exact = canonical(basis)
    except (ValueError, TypeError) as exc:
        raise PermissionError("intake_Foundation_basis_encoding_invalid") from exc
    if exact != document["basis_json"] or digest(basis) != document["basis_sha256"]:
        raise PermissionError("intake_Foundation_basis_exact_bytes_changed")
    return basis


class ResearchBasis:
    def __init__(self, delegation):
        self.delegation, self.engine, self.host = delegation, delegation.engine, delegation.host

    def native(self, sid, work, run):
        self.engine._work(sid, work, writable=True)
        self.host.modules.require(sid, "research")
        from bn_memory.research_module import Research
        if not hasattr(self.host, "_research_module"):
            self.host._research_module = Research(self.host)
        native = self.host._research_module
        row, body = native.load(sid, run)
        if row["sid"] != sid or row["state"] != "active":
            raise PermissionError("intake_current_active_native_research_required")
        card, _ = self.host.working._card(sid)
        import json
        if json.loads(card["primary_ref"])["id"] != body["bead"]:
            raise PermissionError("intake_research_primary_work_mismatch")
        session = native.session(sid, body); session.restore(body["binding"]); session.check()
        return native, body

    def basis(self, sid, state, exposition):
        packet = copy.deepcopy(self.engine.handoff(sid, state["work_ref"], exposition, _read_only=True)["packet"])
        packet.pop("work_revision", None)
        from .profile_contract import apply, checked
        apply(packet,checked(state))
        return {"contract": copy.deepcopy(state["contract"]), "source_packet": packet,
                "semantic_acceptance": "unreviewed", "independent_evidence_created": False}

    def bind(self, sid, args):
        state = self.delegation.state(sid, args["work_ref"], active=True)
        with self.engine.store.db() as db:
            old_operation = db.execute("SELECT input_hash FROM intake_operations WHERE profile_id=? AND work_id=? AND operation_id=?",
                (self.engine.profile, args["work_ref"], args["operation_id"])).fetchone()
        if old_operation:
            if old_operation["input_hash"] != digest(args):raise ValueError("intake_idempotency_input_conflict")
            self.read(sid, args["work_ref"], args["run_id"])
            def never_write(db, current):
                raise RuntimeError("unexpected_replay_mutation")
            return self.engine.commit(sid, args, never_write)
        if state["revision"] != args["expected_revision"]:
            raise ValueError("intake_work_revision_conflict")
        native, body = self.native(sid, args["work_ref"], args["run_id"])
        packet = self.basis(sid, state, args["exposition_id"])
        anchors = self.engine.analysis.anchors(sid, args["work_ref"], args["exposition_id"])[1]
        # Dolt JSON canonicalizes 520.0 to 520. Python equality misses that
        # change, but exact source hashes correctly reject it. Preserve the
        # COMPLETE canonical basis as a JSON string in the typed envelope.
        # No rounding, source rewriting or vendor-storage bypass is involved.
        document = {"contract": "BNNIntakeBasisV2", "work_ref": args["work_ref"], "run_id": args["run_id"],
            "profile_id": self.engine.profile, "project_id": body["project"], "bead_id": body["bead"],
            "host_run_id": body["host_run"], "intake_revision": state["revision"], "exposition_id": args["exposition_id"],
            "basis_json": canonical(packet), "basis_sha256": digest(packet), "does_not_grant_source_or_model_rights": True}
        from .profile_contract import checked, udr_bridge
        bridge=udr_bridge(checked(state))
        if bridge is not None:document['profile_bridge']=bridge
        identifier = "IBS-" + digest(document)[:24]
        query = {"schema_version": 1, "database": "kw_context", "project_id": body["project"], "object_id": identifier}
        old = native.state.get(query)
        written = native.state.put({**query, "expected_revision": old["object_ref"]["revision"] if old["status"] == "found" else 0,
            "content_hash": digest(document), "schema_id": "BNNIntakeBasisV2", "object": document,
            "operation_id": identifier, "run_id": body["host_run"]})
        if written["status"] not in {"committed", "no_op"}:
            raise PermissionError("intake_Foundation_basis_not_committed")
        saved = native.state.get(query)
        if (saved["status"] != "found" or saved["object"] != document
                or saved["object_ref"]["content_hash"] != digest(document)):
            raise PermissionError("intake_Foundation_basis_readback_failed")
        reference = {"run_id": args["run_id"], "exposition_id": args["exposition_id"], "object_id": identifier, "object_ref": saved["object_ref"],
                     "intake_revision": state["revision"], "basis_sha256": digest(packet), "host_run_id": body["host_run"],
                     "scientific_acceptance": False}
        def update(db, current):
            self.engine.analysis.assert_sources_current(db, sid, anchors)
            current.setdefault("research_basis", {})[args["run_id"]] = reference
            return current, {"Foundation": written, "research_basis": reference}
        # The Foundation object is immutable and harmless if this subsequent CAS
        # loses a race. It grants no rights; only the local committed link is active.
        return self.engine.commit(sid, args, update)

    def read(self, sid, work, run):
        state = self.delegation.state(sid, work)
        reference = state.get("research_basis", {}).get(run)
        if reference is None:raise PermissionError("intake_bind_native_research_basis_first")
        native, body = self.native(sid, work, run)
        ref = reference["object_ref"]
        saved = native.state.get({"schema_version": 1, "database": "kw_context", "project_id": body["project"], "object_id": reference["object_id"]})
        if saved["status"] != "found" or saved["object_ref"] != ref:
            raise PermissionError("intake_Foundation_basis_version_changed")
        value = saved["object"]
        packet = unpack_basis(value)
        with self.engine.store.db() as db:
            origin = read(db, self.engine.profile, work, reference["intake_revision"])
        if (origin is None or value["run_id"] != run or value["work_ref"] != work or value["host_run_id"] != body["host_run"]
                or value["bead_id"] != body["bead"] or canonical(packet) != canonical(self.basis(sid, origin, reference["exposition_id"]))
                or digest(packet) != reference["basis_sha256"] or digest(value) != ref["content_hash"]):
            raise PermissionError("intake_research_basis_integrity_or_source_rights_failed")
        return {"research_basis": reference, "basis": packet, "current_owner_interview_controls": state["interview"],
                "source_rights_rechecked": True, "scientific_acceptance": False}

    def publish_acceptance(self,sid,args):
        from .acceptance import current
        work=args['work_ref'];state=self.delegation.state(sid,work,active=True)
        accepted=current(self.engine,sid,state)
        if not accepted['report_release_allowed']:raise PermissionError('intake_current_system_acceptance_required')
        native,body=self.native(sid,work,args['run_id'])
        with self.engine.store.db() as db:
            previous=db.execute('SELECT input_hash,result FROM intake_operations WHERE profile_id=? AND work_id=? AND operation_id=?',
                (self.engine.profile,work,args['operation_id'])).fetchone()
        if previous:
            if previous['input_hash']!=digest(args):raise ValueError('intake_idempotency_input_conflict')
            reference=json.loads(previous['result'])['Foundation']
            saved=native.state.get({'schema_version':1,'database':'kw_context','project_id':body['project'],'object_id':reference['object_id']})
            if saved['status']!='found' or saved['object_ref']!=reference['object_ref']:raise PermissionError('intake_Foundation_acceptance_reference_changed')
            def no_repeat(db,latest):raise RuntimeError('unexpected_acceptance_replay_write')
            return self.engine.commit(sid,args,no_repeat)
        document={'contract':'BNNSystemAcceptanceRecordV1','work_ref':work,'run_id':args['run_id'],
                  'acceptance_json':canonical(accepted),'acceptance_sha256':digest(accepted),'human_review_required':False}
        identifier='SAC-'+digest([work,accepted['basis_sha256']])[:24]
        query={'schema_version':1,'database':'kw_context','project_id':body['project'],'object_id':identifier}
        old=native.state.get(query)
        written=native.state.put({**query,'expected_revision':old['object_ref']['revision'] if old['status']=='found' else 0,
            'content_hash':digest(document),'schema_id':document['contract'],'object':document,
            'operation_id':args['operation_id'],'run_id':body['host_run']})
        saved=native.state.get(query)
        if written['status'] not in {'committed','no_op'} or saved['status']!='found' or saved['object']!=document:
            raise PermissionError('intake_Foundation_acceptance_readback_failed')
        reference={'object_id':identifier,'object_ref':saved['object_ref'],'acceptance_sha256':digest(accepted)}
        def update(db,latest):
            last=latest['assessments'][-1]['system_acceptance']
            if last['basis_sha256']!=accepted['basis_sha256']:raise PermissionError('intake_acceptance_changed_before_Foundation_link')
            last['Foundation']=reference;last['accepted_revision']=latest['revision']+1
            return latest,{'Foundation':reference,'system_acceptance_complete':True,'report_release_allowed':True,'human_review_required':False}
        return self.engine.commit(sid,args,update)

    def finish_research(self,sid,args):
        """Complete accepted work; preserve native citation/evidence limitations."""
        import re
        from agent.tool_attempt import current_tool_attempt
        from .acceptance import current
        state=self.delegation.state(sid,args['work_ref'],active=True);accepted=current(self.engine,sid,state)
        if not accepted['report_release_allowed']:raise PermissionError('intake_current_system_acceptance_required')
        if not accepted.get('Foundation'):raise PermissionError('intake_publish_acceptance_before_finishing_research')
        native,body=self.native(sid,args['work_ref'],args['run_id'])
        from hermes_research_report.research_workspace import connect,root_for
        with connect(root_for(args['run_id'])) as db:known={r['id'] for r in db.execute('SELECT id FROM sources')}
        if set(re.findall(r'\[(S-[A-Za-z0-9_-]+)\]',args['report']))-known:raise PermissionError('intake_report_contains_unregistered_citation')
        attempt=current_tool_attempt()
        if attempt is None or attempt.identity.get('session_id')!=sid:raise PermissionError('intake_native_finish_attempt_required')
        value=native.call(sid,attempt.identity['turn_id'],'udr_finish',{'run_id':args['run_id'],'report':args['report'],
            'status':'complete','request_id':args['request_id']})
        if value['state'] not in {'complete','partial'}:raise PermissionError('intake_native_report_not_saved')
        closed=None
        if value['state']=='partial':
            closed=native.tasks.close_owned(sid,body['bead'],body['holder'],'system-accepted-close-'+args['run_id'],
                'System work accepted with explicit citation/evidence limits; human review is advisory.')
        return {'system_acceptance_complete':True,'report_release_allowed':True,'human_review_required':False,
                'native_research_state':value['state'],'native_receipt':value,'closed_owned_work':closed,
                'analyst_review_recommendations':accepted['analyst_review_recommendations'],'scientific_limits_preserved':True}
