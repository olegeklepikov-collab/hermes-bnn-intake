"""Work-scoped references to the host's sole document ledger and sandboxed parser.

No decoder, model executor or parallel receipt ledger lives here. A link is only
attribution to a work; document_deliveries remains the delivery authority.
"""
import json
import hashlib
import time
from .contracts import DOCUMENT_ACTIONS, DOCUMENT_REF, validate
from .storage import canonical, digest, read, verify

ROLE_PHASES = {"collector": "collect", "analyst": "analyse", "reviewer": "verify",
               "parser": "read", "computation": "read", "G": "G", "S": "S", "D": "D"}


class DocumentBridge:
    def __init__(self, engine, broker, role_resolver=None):
        self.engine, self.broker = engine, broker
        self.store = engine.store
        self.role_resolver = role_resolver or (lambda sid: None)
        self._native = None

    @property
    def native(self):
        # Optional status/off hooks must never install the document subsystem.
        with self.store.db() as db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"document_corpora", "document_sources", "document_units", "document_partitions",
                    "document_pending", "document_deliveries"}
        if not required <= tables:
            raise PermissionError("intake_native_document_ledger_required")
        if self._native is None:
            from bn_memory.documents import Documents
            self._native = Documents(self.broker)
        return self._native

    def state(self, sid, work, active=False):
        self.engine._work(sid, work, writable=True)
        with self.store.db() as db:
            verify(db)
            state = read(db, self.engine.profile, work)
        if not state:
            raise ValueError("intake_prepare_required")
        if active and (self.engine.mode(sid)["mode"] == "off" or state["lifecycle"] != "active"):
            raise PermissionError("intake_document_path_inactive")
        return state

    def inventory(self, sid, cid):
        corpus = self.native.corpus(sid, cid)
        with self.store.db() as db:
            part = db.execute("SELECT parent,members FROM document_partitions WHERE id=?", (cid,)).fetchone()
            original = part["parent"] if part else cid
            rows = [dict(r) for r in db.execute("SELECT id,path,sha256,format,status,body FROM document_sources WHERE corpus=? ORDER BY id", (original,))]
        members = {tuple(v) for v in json.loads(part["members"])} if part else None
        if members is not None:
            rows = [r for r in rows if any(key[0] == r["id"] for key in members)]
        manifest = {k: corpus[k] for k in ["id", "scope", "root", "fingerprint", "phases"]}
        manifest["originals"] = [{k: r[k] for k in ["id", "path", "sha256", "format"]} for r in rows]
        manifest["partition_members"] = sorted(members) if members is not None else None
        return corpus, rows, members, digest(manifest)

    def bound(self, sid, work, cid, state=None):
        state = state or self.state(sid, work)
        binding = state.get("document_corpora", {}).get(cid)
        if not binding:
            raise PermissionError("intake_corpus_not_bound_to_work")
        data = self.inventory(sid, cid)
        if binding["manifest_sha256"] != data[3]:
            raise PermissionError("intake_frozen_corpus_changed_catalogue_new_version")
        return data

    def call(self, sid, args):
        validate(args, DOCUMENT_ACTIONS)
        state = self.state(sid, args["work_ref"], active=True)
        if args["action"] == "bind_corpus":
            corpus, rows, _, fingerprint = self.inventory(sid, args["corpus_id"])
            if corpus["status"] not in {"inventoried", "incomplete_inventory"}:
                raise PermissionError("intake_inventory_not_settled")
            value = {"corpus_id": corpus["id"], "manifest_sha256": fingerprint,
                     "entries": len(rows), "native_inventory_status": corpus["status"],
                     "required_phases": json.loads(corpus["phases"]), "reading_level": "inventory_only"}
            def update(db, current):
                bindings = current.setdefault("document_corpora", {})
                old = bindings.get(corpus["id"])
                if old and old != value:
                    raise PermissionError("intake_frozen_corpus_binding_conflict")
                if self.inventory(sid, corpus["id"])[3] != fingerprint:
                    raise PermissionError("intake_corpus_changed_before_binding")
                bindings[corpus["id"]] = value
                return current, {"corpus": value, "model_received": False}
            return self.engine.commit(sid, args, update)
        if args["action"] == "plan":
            return self.plan(sid, args, state)
        return self.coverage(sid, args["work_ref"], args["corpus_id"], state)

    def units(self, rows, members):
        from bn_memory.documents import current_decoder
        for row in rows:
            if row["status"] != "extracted" or not current_decoder(row):
                continue
            with self.store.db() as db:
                for unit in db.execute("SELECT body FROM document_units WHERE doc=? ORDER BY unit", (row["id"],)):
                    meta = json.loads(unit[0])
                    if members is None or (row["id"], meta["index"]) in members:
                        yield row, meta

    def plan(self, sid, args, state=None):
        corpus, rows, members, fingerprint = self.bound(sid, args["work_ref"], args["corpus_id"], state)
        step = args["fragment_characters"]
        selected, index, representation = [], 0, hashlib.sha256()
        # Iterate the complete index; only the addressed output page enters context.
        for row, unit in self.units(rows, members):
            representation.update((canonical([row["id"], unit]) + "\n").encode())
            size = unit["characters"]
            for start in range(0, max(1, size), step):
                ref = {"kind": "document_unit", "corpus_id": corpus["id"], "doc_id": row["id"],
                       "unit": unit["index"], "start": start, "end": min(start + step, size),
                       "source_sha256": row["sha256"], "unit_sha256": unit["text_sha256"], "phase": "read"}
                if args["offset"] <= index < args["offset"] + args["limit"]:
                    selected.append({"reference": ref, "locator": unit["locator"],
                                     "visual_required": bool(unit.get("visual_required")),
                                     "unhandled_visuals": bool(unit.get("unhandled_visuals"))})
                index += 1
        basis = digest({"manifest": fingerprint, "states": [[r["id"], r["status"], json.loads(r["body"]).get("decoder_revision", 1)] for r in rows],
                        "representations_sha256": representation.hexdigest(), "fragment_characters": step})
        if args["basis"] is not None and args["basis"] != basis:
            raise PermissionError("intake_plan_changed_restart_paging")
        if args["offset"] and args["basis"] is None:
            raise ValueError("intake_continuation_basis_required")
        if args["offset"] > index:
            raise ValueError("intake_plan_offset_out_of_range")
        return {"corpus_id": corpus["id"], "basis": basis, "fragments_total": index, "fragments": selected,
                "has_more": args["offset"] + args["limit"] < index,
                "next_offset": args["offset"] + len(selected) if args["offset"] + len(selected) < index else None,
                "coverage": self.coverage(sid, args["work_ref"], corpus["id"], state),
                "reading_level": "inventory_and_fragment_plan_only", "model_received": False,
                "semantic_dependency_review": "not_performed"}

    def resolve(self, sid, work, ref):
        from jsonschema import Draft202012Validator
        if not Draft202012Validator(DOCUMENT_REF).is_valid(ref):
            raise ValueError("intake_document_reference_invalid")
        _, _, members, _ = self.bound(sid, work, ref["corpus_id"])
        row = self.native.source(sid, ref["doc_id"])
        if not self.native.membership(sid, ref["corpus_id"], row["id"], ref["unit"]):
            raise PermissionError("intake_document_membership_required")
        from bn_memory.documents import current_decoder
        if row["status"] != "extracted" or not current_decoder(row):
            raise PermissionError("intake_current_extracted_document_required")
        with self.store.db() as db:
            item = db.execute("SELECT body FROM document_units WHERE doc=? AND unit=?", (ref["doc_id"], ref["unit"])).fetchone()
        if not item:
            raise ValueError("intake_document_unit_missing")
        unit = json.loads(item[0])
        if row["sha256"] != ref["source_sha256"] or unit["text_sha256"] != ref["unit_sha256"]:
            raise PermissionError("intake_document_reference_hash_mismatch")
        size = unit["characters"]
        if not (0 <= ref["start"] < ref["end"] <= size or size == ref["start"] == ref["end"] == 0):
            raise ValueError("intake_document_reference_range_invalid")
        if ref["end"] - ref["start"] > 65536:
            raise ValueError("intake_split_document_fragment_required")
        actual = self.role_resolver(sid)
        if ref["phase"] in {"G", "S", "D"} and actual != ref["phase"]:
            raise PermissionError("intake_native_role_required_for_phase")
        if actual is not None and actual != ref["phase"]:
            raise PermissionError("intake_document_phase_role_mismatch")
        from bn_memory.source_visibility import Visibility, read_file
        from bn_memory.path_relocation import operating_path
        Visibility(self.store).verify_snapshot(row["metadata"]["snapshot"], row["sha256"])
        directory = operating_path(self.broker.config, row["metadata"]["parse_output"])
        text, sha = read_file(directory / (str(ref["unit"]) + ".txt"), max_bytes=262144)
        if sha != unit["text_sha256"] or len(text.decode("utf-8")) != size:
            raise PermissionError("intake_document_unit_integrity_failed")
        if unit.get("visual_required"):
            from bn_memory.source_visibility import hash_file
            if hash_file(directory / (str(ref["unit"]) + ".png")) != unit.get("image_sha256"):
                raise PermissionError("intake_document_image_integrity_failed")
        return {**ref, "locator": unit["locator"], "visual_required": bool(unit.get("visual_required")),
                "unhandled_visuals": bool(unit.get("unhandled_visuals")), "reading_level": "reference_only_not_delivered",
                "semantic_review": "unreviewed"}

    def closure(self, sid, work, refs):
        """Known structural dependencies only. Never invent a semantic dependency."""
        output, indices = {}, {}
        for ref in refs:
            resolved = self.resolve(sid, work, ref)
            output[digest(ref)] = resolved
            index_key = (ref["corpus_id"], ref["doc_id"])
            if index_key not in indices:
                _, rows, members, _ = self.bound(sid, work, ref["corpus_id"])
                indices[index_key] = [(r, u) for r, u in self.units([r for r in rows if r["id"] == ref["doc_id"]], None)
                                      if u["locator"].get("kind") in {"complete_database_schema", "parquet_schema", "schema"}]
            locator = resolved["locator"]
            for row, unit in indices[index_key]:
                kind = unit["locator"].get("kind")
                needed = kind in {"complete_database_schema", "parquet_schema"} or (kind == "schema" and unit["locator"].get("table") == locator.get("table"))
                if not needed:
                    continue
                if not self.native.membership(sid, ref["corpus_id"], row["id"], unit["index"]):
                    raise PermissionError("intake_partition_structural_dependency_unassigned")
                for start in range(0, max(1, unit["characters"]), 65536):
                    dep = {**ref, "unit": unit["index"], "start": start,
                           "end": min(start + 65536, unit["characters"]), "unit_sha256": unit["text_sha256"]}
                    output[digest(dep)] = self.resolve(sid, work, dep)
        return list(output.values())

    def role_references(self, sid, work, refs, role):
        """Assign obligations, never inherited model reception or new authority.

        Original references have already passed the complete parent closure.
        Source permissions and actual child assignment are checked separately
        by native launch admission and again at the child's protected boundary.
        """
        phase = ROLE_PHASES.get(role)
        if phase is None:
            raise PermissionError("intake_document_role_phase_unqualified")
        result = []
        for ref in refs:
            corpus, _, _, _ = self.bound(sid, work, ref["corpus_id"])
            if phase not in json.loads(corpus["phases"]):
                raise PermissionError("intake_document_target_phase_not_declared")
            result.append({**ref, "phase": phase})
        return result, {"recipient_phase": phase, "references": len(result),
                        "reading_credit_created": False, "inherited_reading_credit": False,
                        "requirement": "native_recipient_delivery_for_all_assigned_document_units_and_fragments",
                        "scientific_acceptance": False}

    def retire_unselected(self, sid, work, refs):
        wanted = {canonical({k: r[k] for k in DOCUMENT_REF["properties"]}) for r in refs}
        with self.store.db() as db:
            # Do not alter an in-flight or received attempt, nor a non-intake read.
            rows = db.execute("SELECT l.work_id,l.reference,p.token,p.state FROM intake_document_links l JOIN document_pending p ON p.token=l.token WHERE l.profile_id=? AND l.sid=? AND p.profile_id=? AND p.sid=?", (self.engine.profile, sid, self.engine.profile, sid)).fetchall()
            for row in rows:
                if (row["work_id"] != work or row["reference"] not in wanted) and row["state"] in {"pending", "failed"}:
                    db.execute("UPDATE document_pending SET state='intake_inactive' WHERE profile_id=? AND token=? AND state IN ('pending','failed')", (self.engine.profile, row["token"]))

    def stage(self, sid, work, refs):
        initial = self.state(sid, work, active=True)
        refs = [{k: r[k] for k in DOCUMENT_REF["properties"]} for r in refs]
        for ref in refs:
            self.resolve(sid, work, ref)
        self.retire_unselected(sid, work, refs)
        created, active = [], []
        try:
            for ref in refs:
                value = canonical(ref)
                with self.store.db() as db:
                    old = db.execute("SELECT p.token,p.state FROM intake_document_links l JOIN document_pending p ON p.token=l.token WHERE l.profile_id=? AND l.work_id=? AND l.sid=? AND l.reference=? AND p.profile_id=? AND p.sid=? AND p.state IN ('pending','failed','intake_inactive') ORDER BY l.created DESC LIMIT 1", (self.engine.profile, work, sid, value, self.engine.profile, sid)).fetchone()
                    if old:
                        db.execute("UPDATE document_pending SET state='pending' WHERE profile_id=? AND token=? AND state='intake_inactive'", (self.engine.profile, old["token"]))
                if old:
                    active.append(old["token"])
                    continue
                result = self.native.read(sid, {"corpus_id": ref["corpus_id"], "doc_id": ref["doc_id"],
                    "unit": ref["unit"], "phase": ref["phase"], "offset": ref["start"], "max_chars": max(1, ref["end"] - ref["start"])})
                created.append(result["token"])
                active.append(result["token"])
                if len(result["text"]) != ref["end"] - ref["start"]:
                    raise PermissionError("intake_document_exact_fragment_required")
                with self.store.db() as db:
                    db.execute("BEGIN IMMEDIATE")
                    current = read(db, self.engine.profile, work)
                    binding = db.execute("SELECT work_id FROM working_bindings WHERE profile_id=? AND sid=?", (self.engine.profile, sid)).fetchone()
                    if current["revision"] != initial["revision"] or not binding or binding[0] != work or self.engine.mode(sid)["mode"] == "off":
                        raise PermissionError("intake_document_work_changed_during_staging")
                    db.execute("INSERT INTO intake_document_links VALUES(?,?,?,?,?,?)", (self.engine.profile, work, sid, result["token"], value, time.time()))
            if self.state(sid, work, active=True)["revision"] != initial["revision"]:
                raise PermissionError("intake_document_work_changed_during_staging")
        except BaseException:
            with self.store.db() as db:
                for token in active:
                    db.execute("UPDATE document_pending SET state='intake_inactive' WHERE profile_id=? AND token=? AND state IN ('pending','failed')", (self.engine.profile, token))
            raise
        return {"created_tokens": created, "model_received": False}

    def coverage(self, sid, work, cid, state=None):
        from bn_memory.documents import current_decoder
        corpus, rows, members, _ = self.bound(sid, work, cid, state)
        units = list(self.units(rows, members))
        from bn_memory.source_visibility import Visibility
        for row in rows:
            if row["status"] == "extracted":
                current = self.native.source(sid, row["id"])
                Visibility(self.store).verify_snapshot(current["metadata"]["snapshot"], row["sha256"])
        with self.store.db() as db:
            links = [dict(r) for r in db.execute("SELECT p.*,l.reference,EXISTS(SELECT 1 FROM document_deliveries d WHERE d.token=p.token AND d.phase=p.phase AND d.api_id=p.api_id) AS confirmed FROM intake_document_links l JOIN document_pending p ON p.token=l.token AND p.sid=l.sid WHERE l.profile_id=? AND l.work_id=? AND p.profile_id=? AND p.corpus=?", (self.engine.profile, work, self.engine.profile, cid))]
        by_unit = {(r["id"], u["index"]): (r, u) for r, u in units}
        phases, confirmed_ranges = {}, {}
        for item in links:
            row, unit = by_unit.get((item["doc"], item["unit"]), (None, None))
            reference = json.loads(item["reference"])
            current = (row is not None and reference["source_sha256"] == row["sha256"] and reference["unit_sha256"] == unit["text_sha256"]
                       and reference["start"] == item["begin"] and reference["end"] == item["end"] and reference["phase"] == item["phase"])
            item["confirmed"] = bool(item["confirmed"] and current)
            if item["confirmed"]:
                confirmed_ranges.setdefault((item["phase"], item["doc"], item["unit"]), []).append((item["begin"], item["end"]))
        for phase in json.loads(corpus["phases"]):
            complete = 0
            for row, unit in units:
                end, seen, gap = 0, False, False
                ranges = sorted(confirmed_ranges.get((phase, row["id"], unit["index"]), []))
                for start, stop in ranges:
                    seen = True
                    if start > end:
                        gap = True
                    end = max(end, stop)
                complete += bool(seen and not gap and end >= unit["characters"] and not unit.get("unhandled_visuals"))
            phases[phase] = {"units_expected": len(units), "units_confirmed": complete}
        missing = [{"doc_id": r["id"], "status": r["status"] if r["status"] != "extracted" else "decoder_stale"}
                   for r in rows if r["status"] != "extracted" or not current_decoder(r)]
        return {"corpus_id": cid, "entries_total": len(rows), "files_extracted": len(rows) - len(missing),
                "missing_sources_count": len(missing), "missing_sources": missing[:20], "inventory_complete": corpus["status"] == "inventoried",
                "native_units": len(units), "linked_fragments": len(links),
                "fragments_prepared_for_model": sum(r["state"] in {"pending", "failed", "scheduled", "received"} for r in links),
                "confirmed_deliveries": sum(r["confirmed"] for r in links), "required_phases": phases,
                "model_delivery_complete": bool(rows) and bool(units) and not missing and corpus["status"] == "inventoried" and all(v["units_confirmed"] == len(units) for v in phases.values()),
                "semantic_processing": "not_executed_by_intake", "semantic_understanding_verified": False,
                "scientific_acceptance": False}
