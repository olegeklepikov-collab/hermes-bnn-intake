"""Exact native products as derived primary memory, with original source rights.

No model-supplied result, second task graph, or new receipt authority. This first
adapter qualifies native assignments with record inputs; other inputs fail closed.
"""
import copy
import hashlib
from .storage import canonical, digest


class ProductCapture:
    def __init__(self, delegation):
        self.delegation, self.engine, self.host, self.store = delegation, delegation.engine, delegation.host, delegation.store

    def product(self, sid, work, origin):
        if origin["module"] == "gsd":
            call = {"schema_version": 1, "action": "read_gsd_result", "work_ref": work, "job_id": origin["job_id"], "limit": 4096}
        else:
            task = self.delegation.native_task(origin["task_id"])
            import json
            if json.loads(task["body"]).get("module", "research") != origin["module"]:
                raise PermissionError("intake_capture_native_module_mismatch")
            call = {"schema_version": 1, "action": "read_result", "work_ref": work,
                    "native_task_id": origin["task_id"], "native_revision": origin["native_revision"], "limit": 4096}
        text, first, offset = "", None, 0
        while True:
            page = self.delegation.call(sid, {**call, "offset": offset})
            checksum = page.get("text_sha256", page.get("product_sha256"))
            if first is None:
                first = page; full_hash = checksum
                if origin["module"] != "gsd" and page["native_state"] != "succeeded":
                    raise PermissionError("intake_capture_completed_product_required")
            elif checksum != full_hash:
                raise PermissionError("intake_capture_native_product_changed_between_pages")
            text += page["text"]
            if page["next_offset"] is None:break
            if page["next_offset"] <= offset:raise PermissionError("intake_capture_native_page_not_progressing")
            offset = page["next_offset"]
        if hashlib.sha256(text.encode()).hexdigest() != full_hash:
            raise PermissionError("intake_capture_complete_product_hash_mismatch")
        if origin["module"] == "gsd":child = first["child_session_id"]
        else:
            with self.store.db() as db:
                row = db.execute("SELECT body FROM native_task_history WHERE profile_id=? AND id=? AND revision=?",
                    (self.store.profile, origin["task_id"], origin["native_revision"])).fetchone()
            child = json.loads(row[0])["child_sid"]
        return text, full_hash, child

    def dependencies(self, sid, child, roles):
        from bn_memory.assignment_resources import read as assigned
        from bn_memory.record_metadata import read as metadata
        from bn_memory.source_visibility import Visibility
        child_binding = self.store.binding(child)
        if child_binding["role"] in {"S","D"} and "G" in roles:
            raise PermissionError("intake_capture_S_D_to_G_needs_explicit_holdout_visibility_contract")
        with self.store.db() as db:
            grant = db.execute("SELECT permissions FROM action_grants WHERE profile_id=? AND assignment_id=?",
                (self.store.profile,child_binding["assignment_id"])).fetchone()
        import json
        record_tools = {"memory.read.record.child","memory.recall.cached.child","memory.compute.isolated","memory.recovery",
                        "memory.working.child","w3.discovery.describe","w3.discovery.search"}
        if grant is None or any(p["rule_id"] not in record_tools for p in json.loads(grant[0])):
            raise PermissionError("intake_capture_other_tool_inputs_need_explicit_source_adapter")
        selected = assigned(self.store, child_binding)
        if selected is None or any(selected[k] for k in ["files", "code", "documents", "library", "data"]):
            raise PermissionError("intake_capture_nonrecord_inputs_need_explicit_source_adapter")
        pending = [{"kind": "record", "id": key, **ref} for key,ref in selected["records"].items()]
        result, seen = [], set()
        while pending:
            ref = pending.pop(); key = digest(ref)
            if key in seen:continue
            if ref["kind"] != "record":raise PermissionError("intake_capture_nonrecord_dependency_needs_explicit_source_adapter")
            row = self.store.read(sid, ref["id"], ref["revision"])
            if row["content_hash"] != ref["content_hash"] or row.get("dependency_status", "current") != "current":
                raise PermissionError("intake_capture_source_stale_or_revoked")
            seen.add(key); result.append(ref)
            with self.store.db() as db:pending.extend(metadata(db, ref["id"], ref["revision"]).get("source_dependencies", []))
            if len(result) > 128:raise PermissionError("intake_capture_split_native_dependency_contract_required")
        binding = self.store.binding(sid)
        for role in roles:
            # This is a policy preview, not a grant; the actual native launch
            # later checks its OWN immutable assignment including every dependency.
            candidate = {**binding, "readonly": True, "role": role, "assignment_id": "capture-policy-preview"}
            with self.store.db() as db:Visibility(self.store).closure(db, candidate, result)
        return sorted(result, key=lambda ref: (ref["id"],ref["revision"]))

    def prepare(self, sid, args):
        self.delegation.state(sid, args["work_ref"], active=True)
        text, checksum, child = self.product(sid, args["work_ref"], args["origin"])
        if checksum != args["product_sha256"]:raise PermissionError("intake_capture_expected_product_hash_mismatch")
        roles = sorted(set(args["recipient_roles"]) | {"parent"})
        dependencies = self.dependencies(sid, child, roles)
        # 1024 Unicode characters fit the native 8192-byte whole-record bound,
        # including JSON escaping and the compact provenance header. Nothing is cut.
        parts = [text[start:start+1024] for start in range(0,len(text),1024)]
        if len(parts) + len(dependencies) > 256:raise PermissionError("intake_capture_split_native_assignment_contract_required")
        identity = {"work_ref": args["work_ref"], "origin": args["origin"], "product_sha256": checksum, "roles": roles}
        capture_id = "CAP-" + digest(identity)[:32]
        records = []
        for index, part in enumerate(parts):
            payload = {"contract": "BNNDerivedNativeFragmentV1", "capture_id": capture_id, "origin": args["origin"],
                "product_sha256": checksum, "fragment_index": index, "fragment_count": len(parts), "start": index*1024,
                "end": index*1024+len(part), "total_characters": len(text), "whole_product_in_this_record": len(parts)==1,
                "evidence_status": "unreviewed", "independent_source": False, "text": part}
            content = canonical(payload)
            if len(content.encode()) > 8192:raise PermissionError("intake_capture_record_requires_smaller_complete_fragment")
            records.append({"content": content, "id": "MEM-" + digest([self.store.namespace["owner_id"],self.store.profile,capture_id,index])[:24],
                "kind": "note", "source_ref": "derived:native-product:"+capture_id+":"+str(index), "scope": "project",
                "request_id": capture_id+":"+str(index), "horizon": "working", "memory_type": "episodic",
                "classification_reason": "Exact completed native product fragment; unreviewed derived data, never independent empirical evidence",
                "visibility": {"allowed_roles": roles, "evidence_use": "general"}, "source_dependencies": dependencies})
        return {"id": capture_id, "identity": identity, "records": records, "dependencies": dependencies,
                "origin_child_sid": child, "total_characters": len(text), "scientific_acceptance": False}

    def capture(self, sid, args):
        prepared = self.prepare(sid, args)
        state = self.delegation.state(sid, args["work_ref"], active=True)
        with self.store.db() as db:
            operation = db.execute("SELECT input_hash FROM intake_operations WHERE profile_id=? AND work_id=? AND operation_id=?",
                (self.engine.profile,args["work_ref"],args["operation_id"])).fetchone()
        if operation:
            if operation[0] != digest(args):raise ValueError("intake_idempotency_input_conflict")
            self.read(sid,args["work_ref"],prepared["id"])
            return self.engine.commit(sid,args,lambda db,current: (current,{}))
        if state["revision"] != args["expected_revision"]:raise ValueError("intake_work_revision_conflict")
        from bn_memory.record_metadata import read as metadata
        refs = []
        for record in prepared["records"]:
            with self.store.db() as db:
                row = db.execute("SELECT * FROM records WHERE id=? AND revision=1",(record["id"],)).fetchone()
            if row is None:
                value = self.store.record(sid, record)
            else:
                value = self.store.read(sid,record["id"],1)
                with self.store.db() as db:attributes = metadata(db,record["id"],1)
                if (value["content"] != record["content"] or value["source"] != record["source_ref"]
                        or attributes["visibility"] != record["visibility"] or attributes.get("source_dependencies") != prepared["dependencies"]):
                    raise PermissionError("intake_capture_existing_fragment_not_identical")
            refs.append({"kind": "record", "id": record["id"], "revision": 1, "content_hash": value["content_hash"]})
        saved = {key: copy.deepcopy(value) for key,value in prepared.items() if key != "records"}
        saved["records"] = refs
        saved["input_refs"] = [{key: ref[key] for key in ["kind","id","revision"]} for ref in refs]
        saved["required_resources"] = {"records": list(dict.fromkeys(r["id"] for r in refs+prepared["dependencies"]))}
        def update(db,current):
            for ref in refs+prepared["dependencies"]:
                row = db.execute("SELECT r.status,r.revision,r.content_hash FROM records r JOIN current c ON c.id=r.id AND c.revision=r.revision WHERE r.id=?",(ref["id"],)).fetchone()
                if not row or tuple(row)!=("recorded",ref["revision"],ref["content_hash"]):raise PermissionError("intake_capture_dependency_changed_before_commit")
            captures = current.setdefault("native_captures",{})
            if saved["id"] in captures and captures[saved["id"]] != saved:raise PermissionError("intake_capture_immutable_identity_conflict")
            captures[saved["id"]] = saved
            return current, {"capture": saved, "new_native_executor": False}
        return self.engine.commit(sid,args,update)

    def read(self, sid, work, capture_id):
        state = self.delegation.state(sid,work)
        saved = state.get("native_captures",{}).get(capture_id)
        if saved is None:raise PermissionError("intake_complete_capture_required")
        text = ""
        import json
        for index,ref in enumerate(saved["records"]):
            row = self.store.read(sid,ref["id"],ref["revision"])
            if row["content_hash"] != ref["content_hash"] or row.get("dependency_status","current")!="current":raise PermissionError("intake_capture_fragment_changed_or_revoked")
            part = json.loads(row["content"])
            if part["capture_id"]!=capture_id or part["fragment_index"]!=index or part["fragment_count"]!=len(saved["records"]) or part["start"]!=len(text):raise PermissionError("intake_capture_fragment_sequence_invalid")
            text += part["text"]
        if hashlib.sha256(text.encode()).hexdigest()!=saved["identity"]["product_sha256"]:raise PermissionError("intake_capture_reconstructed_product_hash_mismatch")
        # This is a durable snapshot of an already verified completed result.
        # Reading it must NOT restart/reopen the optional originating engine.
        # Current original-source rights and the complete captured hash remain
        # mandatory; creation can only enter through the genuine native reader.
        return {"capture": saved, "complete": True, "scientific_acceptance": False, "new_native_executor": False,
                "origin_verified_at_capture": True, "origin_module_reopened": False}
