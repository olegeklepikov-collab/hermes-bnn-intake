"""Concrete method applications and addressed, unreviewed analytical products.

Structural checks establish scope, quotation identity and provenance, not truth or
semantic entailment. All analytical vocabularies are explicitly extensible.
"""
import copy
import json
from pathlib import Path
from .contracts import ANALYSIS_ACTIONS, PRODUCT, RECORD_REF, DOCUMENT_REF, RESEARCH_REF, validate
from .storage import canonical, digest, read, verify

TRACES = {
    "concept_measurement": {"conceptualization", "dimensionalization", "operationalization", "instrumentation"},
    "method_foundation": {"paradigm", "theory", "model", "methodology", "method", "procedure", "instrument"},
}


class Analysis:
    def __init__(self, engine):
        self.engine, self.store = engine, engine.store

    def state(self, sid, work, active=False):
        self.engine._work(sid, work, writable=True)
        with self.store.db() as db:
            verify(db)
            value = read(db, self.engine.profile, work)
        if value is None:
            raise ValueError("intake_prepare_required")
        if active and (self.engine.mode(sid)["mode"] == "off" or value["lifecycle"] != "active"):
            raise PermissionError("intake_analysis_inactive")
        return value

    def method(self, state, method_id):
        card = state["local_methods"].get(method_id) or self.engine.catalogue.entries.get(method_id)
        if card is None:
            raise ValueError("intake_local_method_descriptor_required_not_prohibited")
        return card

    def anchors(self, sid, work, exposition_id, *, allow_inactive=False):
        packet = self.engine.handoff(sid, work, exposition_id, _read_only=allow_inactive)["packet"]
        result = []
        for record in packet["records"]:
            ref = {k: record["ref"][k] for k in RECORD_REF["properties"]}
            result.append({"id": "IN-" + digest(ref)[:32], "ref": ref, "content_sha256": record["ref"]["content_hash"],
                           "start": 0, "end": len(record["content"]), "image_sha256": None})
        for item in packet.get("documents", []):
            ref = {k: item[k] for k in DOCUMENT_REF["properties"]}
            image_hash = None
            if item["visual_required"]:
                with self.store.db() as db:
                    unit = db.execute("SELECT body FROM document_units WHERE doc=? AND unit=?", (ref["doc_id"], ref["unit"])).fetchone()
                image_hash = json.loads(unit[0])["image_sha256"]
            result.append({"id": "IN-" + digest(ref)[:32], "ref": ref, "content_sha256": ref["unit_sha256"],
                           "start": ref["start"], "end": ref["end"], "image_sha256": image_hash})
        for item in packet.get("research_results", []):
            ref = {k: item[k] for k in RESEARCH_REF["properties"]}
            result.append({"id": "IN-" + digest(ref)[:32], "ref": ref, "content_sha256": item["content_hash"],
                           "start": ref["start"], "end": ref["end"], "image_sha256": None})
        return packet, result

    def fingerprint(self, state, application, anchors):
        method = self.method(state, application["method_id"])
        from .profile_contract import for_application
        value={"contract": state["contract"], "application": {k: v for k, v in application.items() if k != "id"},
                       "exposition": state["expositions"][application["exposition_id"]]["purpose_fingerprint"],
                       "method_sha256": digest(method), "anchors": anchors}
        profile=for_application(state,application)
        if profile is not None:value['profile_contract']=profile
        return digest(value)

    def inputs(self, sid, work, state, app, *, allow_inactive=False):
        """Iterative closure over prior products; subject cycles never run code."""
        packet, primary = self.anchors(sid, work, app["exposition_id"], allow_inactive=allow_inactive)
        from .profile_contract import apply, for_application
        apply(packet,for_application(state,app))
        all_anchors = {a["id"]: a for a in primary}
        records = {(v["ref"]["id"], v["ref"]["revision"]): v for v in packet["records"]}
        expositions = dict.fromkeys(packet["dependencies"])
        documents = {digest(v): v for v in packet.get("documents", [])}
        research_results = {digest(v): v for v in packet.get("research_results", [])}
        pending, seen, products = list(app["product_dependencies"]), {app["id"]}, {}
        while pending:
            dependency = pending.pop()
            product = state.get("analysis_products", {}).get(dependency["product_id"])
            if product is None or digest(product) != dependency["sha256"]:
                raise PermissionError("intake_product_dependency_version_unavailable")
            products[product["id"]] = product
            producer_id = product["application_id"]
            if producer_id == app["id"]:
                raise PermissionError("intake_application_cannot_depend_on_its_own_product")
            if producer_id in seen:
                continue
            seen.add(producer_id)
            previous = state["method_applications"][producer_id]
            previous_app = previous["application"]
            source_packet, anchors = self.anchors(sid, work, previous_app["exposition_id"], allow_inactive=allow_inactive)
            if self.fingerprint(state, previous_app, anchors) != previous["input_fingerprint"]:
                raise PermissionError("intake_previous_product_inputs_changed")
            all_anchors.update((a["id"], a) for a in anchors)
            records.update(((v["ref"]["id"], v["ref"]["revision"]), v) for v in source_packet["records"])
            documents.update((digest(v), v) for v in source_packet.get("documents", []))
            research_results.update((digest(v), v) for v in source_packet.get("research_results", []))
            packet["methods"].update(source_packet["methods"])
            expositions.update(dict.fromkeys(source_packet["dependencies"]))
            packet["methods"][previous_app["method_id"]] = self.method(state, previous_app["method_id"])
            pending.extend(previous_app["product_dependencies"])
        packet["records"] = list(records.values())
        packet["dependencies"] = list(expositions)
        if documents:
            packet["documents"] = list(documents.values())
        if research_results:
            packet["research_results"] = list(research_results.values())
        if products:
            packet["derived_products"] = products
            packet["independence"] = "multiple_analyses_do_not_create_independent_primary_evidence"
        return packet, primary, list(all_anchors.values())

    def application_packet(self, sid, work, application_id, *, active=True):
        state = self.state(sid, work, active=active)
        saved = state.get("method_applications", {}).get(application_id)
        if saved is None:
            raise ValueError("intake_method_application_missing")
        app = saved["application"]
        source_packet, primary, anchors = self.inputs(sid, work, state, app, allow_inactive=not active)
        fingerprint = self.fingerprint(state, app, primary)
        if fingerprint != saved["input_fingerprint"]:
            raise PermissionError("intake_method_inputs_changed_prepare_new_application")
        method = self.method(state, app["method_id"])
        if app["method_id"] not in source_packet["methods"]:
            source_packet["methods"][app["method_id"]] = method
        return {"application": app, "input_fingerprint": fingerprint, "profile": state["contract"]["profile"],
                "method_sha256": digest(method), "source_packet": source_packet, "source_anchors": anchors,
                "result_instruction": "Return the concrete product through intake_analysis.submit_product; if tools are deferred use the native tool_call with one local calls entry. Quote only supplied spans. Mark gaps and assumptions. If source_packet.profile_contract exists, profile_checks must report EACH obligation ID with state, node_ids and reason; unresolved checks require uncovered remainder. Your addressed claim is not independent acceptance. Structure does not imply review or completion.",
                "product_contract": PRODUCT, "semantic_acceptance": "unreviewed"}

    def selected(self, sid):
        if self.engine.mode(sid)["mode"] == "off" or self.store.binding(sid)["readonly"]:
            return None
        with self.store.db() as db:
            binding = db.execute("SELECT work_id FROM working_bindings WHERE profile_id=? AND sid=?", (self.engine.profile, sid)).fetchone()
            state = read(db, self.engine.profile, binding[0]) if binding else None
        if not state or state["lifecycle"] != "active" or not state.get("selected_application"):
            return None
        packet = self.application_packet(sid, state["work_ref"], state["selected_application"])
        return {"work_ref": state["work_ref"], "application_id": state["selected_application"], "packet": packet}

    def prepare_application(self, sid, work, state, app):
        from .profile_contract import current_minimum
        current_minimum(self.engine,state)
        if app["exposition_id"] not in state["expositions"]:
            raise ValueError("intake_exposition_missing")
        card = self.method(state, app["method_id"])
        self.classifications(app, card)
        levels = {x["id"] for x in app["level_definitions"]}
        checks = {x["id"] for x in app["acceptance_checks"]}
        if len(levels) != len(app["level_definitions"]) or len(checks) != len(app["acceptance_checks"]):
            raise ValueError("intake_duplicate_level_or_obligation_id")
        required = TRACES.get(app["trace"], set())
        if not required <= levels or not required <= {x["level"] for x in app["acceptance_checks"]}:
            raise ValueError("intake_trace_requires_distinct_levels_and_obligations")
        if any(c["level"] not in levels for c in app["acceptance_checks"]):
            raise ValueError("intake_obligation_level_not_defined")
        _, primary, anchors = self.inputs(sid, work, state, app)
        return anchors, self.fingerprint(state, app, primary), digest(card)

    def call(self, sid, args):
        validate(args, ANALYSIS_ACTIONS)
        action, work = args["action"], args["work_ref"]
        state = self.state(sid, work, active=action not in {"status", "read_product"})
        if action == "status":
            saved = state.get("method_applications", {}).get(args["application_id"])
            if saved is None:
                raise ValueError("intake_method_application_missing")
            products = [self.product_summary(p) for p in state.get("analysis_products", {}).values() if p["application_id"] == args["application_id"]]
            offset, limit = args.get("offset", 0), args.get("limit", 20)
            with self.store.db() as db:
                rows = db.execute("SELECT body,sha256 FROM intake_model_outcomes WHERE profile_id=? AND work_id=? AND sid=? AND json_extract(body,'$.application_id')=? ORDER BY rowid LIMIT ? OFFSET ?", (self.engine.profile, work, sid, args["application_id"], limit + 1, offset)).fetchall()
            outcomes = []
            for row in rows[:limit]:
                body = json.loads(row["body"])
                if digest(body) != row["sha256"]:
                    raise PermissionError("intake_model_outcome_integrity_failed")
                outcomes.append({k: body[k] for k in ["id", "api_request_id", "retry_count", "status", "usage", "error_type"] if k in body})
            return {"application": saved, "products": products, "execution_outcomes": outcomes,
                    "next_offset": offset + limit if len(rows) > limit else None, "semantic_acceptance": "unreviewed"}
        if action == "read_product":
            product = state.get("analysis_products", {}).get(args["product_id"])
            if product is None:
                raise ValueError("intake_product_missing")
            self.application_packet(sid, work, product["application_id"], active=False)
            self.bound(product)
            return {"product": product, "scientific_acceptance": False}
        if action == "plan_method":
            app = args["application"]
            anchors, fingerprint, method_hash = self.prepare_application(sid, work, state, app)
            def update(db, current):
                self.assert_sources_current(db, sid, anchors)
                if digest(self.method(current, app["method_id"])) != method_hash:
                    raise PermissionError("intake_method_changed_before_planning")
                applications = current.setdefault("method_applications", {})
                if app["id"] in applications:
                    raise ValueError("intake_method_application_exists")
                if any(v["input_fingerprint"] == fingerprint for v in applications.values()):
                    raise ValueError("intake_exact_method_application_repeat")
                applications[app["id"]] = {"application": copy.deepcopy(app), "input_fingerprint": fingerprint,
                    "method_sha256": method_hash, "catalogue_version": self.engine.catalogue.version,
                    "profile": current["contract"]["profile"], "status": "planned_not_executed"}
                from .profile_contract import checked
                applications[app['id']]['profile_contract']=checked(current)
                return current, {"application_id": app["id"], "input_fingerprint": fingerprint}
        elif action == "select_application":
            def update(db, current):
                chosen = args["application_id"]
                saved = current.get("method_applications", {}).get(chosen) if chosen else None
                if chosen and saved is None:
                    raise ValueError("intake_method_application_missing")
                if saved and saved["status"] == "product_returned_unreviewed":
                    raise PermissionError("intake_application_has_product_read_it_or_prepare_new_purpose")
                current["selected_application"] = chosen
                if saved:
                    current["selected_exposition"] = saved["application"]["exposition_id"]
                return current, {}
        else:
            producer = self.producer(sid, work, args)
            product = args["product"]
            packet = self.application_packet(sid, work, product["application_id"])
            if product["input_fingerprint"] != packet["input_fingerprint"]:
                raise PermissionError("intake_product_input_fingerprint_mismatch")
            self.check_product(sid, packet, product)
            profile=packet['source_packet'].get('profile_contract')
            saved_product = self.stored_product(product, producer, digest(profile) if profile else None)
            self.bound(saved_product)
            def update(db, current):
                self.assert_sources_current(db, sid, packet["source_anchors"])
                outcome = db.execute("SELECT sha256 FROM intake_model_outcomes WHERE profile_id=? AND id=?", (self.engine.profile, producer["outcome_id"])).fetchone()
                if outcome is None or outcome[0] != producer["outcome_sha256"]:
                    raise PermissionError("intake_producer_changed_before_product_commit")
                products = current.setdefault("analysis_products", {})
                if product["id"] in products:
                    raise ValueError("intake_product_id_exists")
                saved = copy.deepcopy(saved_product)
                products[product["id"]] = saved
                current["method_applications"][product["application_id"]]["status"] = "product_returned_unreviewed"
                if current.get("selected_application") == product["application_id"]:
                    current["selected_application"] = None
                return current, {"product_id": product["id"], "semantic_review": "unreviewed"}
        return self.engine.commit(sid, args, update)

    def preflight(self, sid, args):
        validate(args, ANALYSIS_ACTIONS)
        state = self.state(sid, args["work_ref"], active=args["action"] not in {"status", "read_product"})
        if args["action"] == "plan_method":
            self.prepare_application(sid, args["work_ref"], state, args["application"])
        if args["action"] == "submit_product":
            producer = self.producer(sid, args["work_ref"], args)
            packet = self.application_packet(sid, args["work_ref"], args["product"]["application_id"])
            if args["product"]["input_fingerprint"] != packet["input_fingerprint"]:
                raise PermissionError("intake_product_input_fingerprint_mismatch")
            self.check_product(sid, packet, args["product"])
            profile=packet['source_packet'].get('profile_contract')
            self.bound(self.stored_product(args["product"], producer, digest(profile) if profile else None))

    @staticmethod
    def stored_product(product, producer,profile_hash=None):
        value={**copy.deepcopy(product), "producer": producer, "structural_checks": "passed",
               "semantic_review": "unreviewed", "scientific_acceptance": False}
        if profile_hash is not None:value['profile_contract_sha256']=profile_hash
        return value

    def bound(self, product):
        limit = self.store.config.get("intake_product_bytes", 65536)
        if type(limit) is not int or limit < 1 or len(canonical(product).encode()) > limit:
            raise PermissionError("intake_split_product_required_do_not_trim")

    def classifications(self, app, card):
        catalogue = self.engine.catalogue.classification
        known = {"kinds": set(catalogue["kinds"]), "families": {v["id"] for v in catalogue["families"]},
                 "axes": {v["id"] for v in catalogue["axes"]}}
        extensions = {(v["dimension"], v["id"]): v for v in app["classification_extensions"]}
        if len(extensions) != len(app["classification_extensions"]):
            raise ValueError("intake_duplicate_classification_extension")
        selected = {"kinds": [card.get("object_kind", card.get("kind"))], "families": app["families"], "axes": app["axes"]}
        for dimension, identifiers in selected.items():
            missing = [value for value in identifiers if value not in known[dimension] and (dimension, value) not in extensions]
            if missing:
                raise ValueError("intake_new_classification_requires_definition_not_permission:" + dimension + ":" + str(missing[0])[:100])
        for (dimension, identifier), extension in extensions.items():
            if identifier in known[dimension]:
                raise ValueError("intake_local_classification_must_not_redefine_builtin_id")
            allowed = known[dimension] | {key[1] for key in extensions if key[0] == dimension}
            if not set(extension["related_to"]) <= allowed:
                raise ValueError("intake_classification_relation_target_undefined")

    def assert_sources_current(self, db, sid, anchors):
        """Native policies in the effect transaction, with tracking writes disabled.

        The stock read path may write source-use tracking. Reopening that writer
        inside BEGIN IMMEDIATE would deadlock; use its same policy APIs here.
        """
        from bn_memory import record_metadata
        from bn_memory.source_visibility import Visibility
        from bn_memory.documents import current_decoder
        binding, visibility = self.store.binding(sid), Visibility(self.store)
        for anchor in anchors:
            ref = anchor["ref"]
            if ref["kind"] == "research_result":
                if self.engine.research is None:
                    raise PermissionError("intake_research_bridge_unavailable")
                row = db.execute("SELECT work_id FROM working_bindings WHERE profile_id=? AND sid=?", (self.engine.profile, sid)).fetchone()
                if row is None:
                    raise PermissionError("intake_native_work_binding_required")
                result = self.engine.research.resolve(sid, row[0], ref, db)
                if result["content_hash"] != anchor["content_sha256"]:
                    raise PermissionError("intake_research_result_changed_before_effect")
            elif ref["kind"] == "record":
                row = db.execute("SELECT * FROM records WHERE id=? AND revision=?", (ref["id"], ref["revision"])).fetchone()
                head = db.execute("SELECT r.* FROM records r JOIN current c ON c.id=r.id AND c.revision=r.revision WHERE r.id=?", (ref["id"],)).fetchone()
                if row is None or head is None or head["status"] == "retracted" or row["scope"] not in self.store.scopes(binding) or row["content_hash"] != anchor["content_sha256"]:
                    raise PermissionError("intake_source_changed_before_effect")
                for revision in {row["revision"], head["revision"]}:
                    metadata = record_metadata.read(db, ref["id"], revision)
                    record_metadata.enforce(binding, metadata)
                    visibility.record(db, binding, metadata, track=False)
            else:
                row = db.execute("SELECT * FROM document_sources WHERE id=?", (ref["doc_id"],)).fetchone()
                corpus = db.execute("SELECT scope FROM document_corpora WHERE id=?", (ref["corpus_id"],)).fetchone()
                if row is None or corpus is None or corpus[0] not in self.store.scopes(binding) or row["sha256"] != ref["source_sha256"] or row["status"] != "extracted" or not current_decoder(dict(row)):
                    raise PermissionError("intake_document_changed_before_effect")
                if row["corpus"] != ref["corpus_id"]:
                    part = db.execute("SELECT members FROM document_partitions WHERE id=?", (ref["corpus_id"],)).fetchone()
                    if part is None or [ref["doc_id"], ref["unit"]] not in json.loads(part[0]):
                        raise PermissionError("intake_document_membership_changed_before_effect")
                unit = db.execute("SELECT body FROM document_units WHERE doc=? AND unit=?", (ref["doc_id"], ref["unit"])).fetchone()
                if unit is None or json.loads(unit[0])["text_sha256"] != ref["unit_sha256"]:
                    raise PermissionError("intake_document_unit_changed_before_effect")
                visibility.closure(db, binding, [{"kind": "source", "sha256": row["sha256"]}], track=False)

    @staticmethod
    def product_summary(product):
        return {"id": product["id"], "application_id": product["application_id"], "sha256": digest(product),
                "nodes": len(product["nodes"]), "edges": len(product["edges"]), "semantic_review": "unreviewed",
                "reading": "summary_only_use_read_product"}

    def producer(self, sid, work, args):
        from agent.tool_attempt import current_tool_attempt
        attempt = current_tool_attempt()
        if (attempt is None or not attempt.active.is_set() or attempt.tool_name != "intake_analysis"
                or attempt.profile_home != str(Path(self.store.config["profile_home"]).resolve())
                or attempt.identity.get("session_id") != sid):
            raise PermissionError("intake_native_product_submission_required")
        api, call = attempt.identity.get("api_request_id"), attempt.identity.get("tool_call_id")
        with self.store.db() as db:
            rows = db.execute("SELECT body,sha256 FROM intake_model_outcomes WHERE profile_id=? AND work_id=? AND sid=? AND json_extract(body,'$.api_request_id')=? ORDER BY created DESC", (self.engine.profile, work, sid, api)).fetchall()
        for row in rows:
            value = json.loads(row["body"])
            if digest(value) != row["sha256"]:
                raise PermissionError("intake_model_outcome_integrity_failed")
            if value["api_request_id"] != api or value["status"] != "returned":
                continue
            expected = value.get("tool_calls", {}).get(call)
            if expected and {k: expected.get(k) for k in ["name", "arguments_sha256"]} == {"name": "intake_analysis", "arguments_sha256": digest(args)}:
                if value["application_id"] != args["product"]["application_id"] or value["input_fingerprint"] != args["product"]["input_fingerprint"]:
                    raise PermissionError("intake_producer_application_mismatch")
                return {"outcome_id": value["id"], "api_request_id": api, "tool_call_id": call,
                        "origin": "native_host_execution_return", "outcome_sha256": row["sha256"],
                        "wire_name": expected.get("wire_name", "intake_analysis")}
        raise PermissionError("intake_returned_producer_with_exact_product_call_required")

    def source_text(self, sid, anchor):
        ref = anchor["ref"]
        if ref["kind"] == "record":
            return self.store.read(sid, ref["id"], ref["revision"])["content"]
        if ref["kind"] == "research_result":
            with self.store.db() as db:
                row = db.execute("SELECT work_id FROM working_bindings WHERE profile_id=? AND sid=?", (self.engine.profile, sid)).fetchone()
                body = self.engine.research.checked_receipt(db, sid, row[0], ref["attempt_id"], ref["receipt_sha256"])
            return canonical(body["result"])
        from pathlib import Path
        from bn_memory.path_relocation import operating_path
        from bn_memory.source_visibility import read_file
        source = self.engine.documents.native.source(sid, ref["doc_id"])
        directory = operating_path(self.store.config, source["metadata"]["parse_output"])
        return read_file(Path(directory) / (str(ref["unit"]) + ".txt"), max_bytes=262144)[0].decode("utf-8")

    def check_product(self, sid, packet, product):
        from .profile_contract import check_product
        check_product(packet['source_packet'].get('profile_contract'),product)
        app = packet["application"]
        levels = {v["id"] for v in app["level_definitions"]}
        nodes = {v["id"]: v for v in product["nodes"]}
        if len(nodes) != len(product["nodes"]) or any(n["level"] not in levels for n in nodes.values()):
            raise ValueError("intake_product_duplicate_node_or_undefined_level")
        anchors = {v["id"]: v for v in packet["source_anchors"]}
        texts = {}
        for node in nodes.values():
            if node["epistemic_status"] == "observation" and not node["citations"]:
                raise ValueError("intake_observation_requires_source")
            for cite in node["citations"]:
                if cite["kind"] == "product":
                    previous = packet["source_packet"].get("derived_products", {}).get(cite["product_id"])
                    if previous is None or digest(previous) != cite["sha256"] or cite["node_id"] not in {n["id"] for n in previous["nodes"]}:
                        raise PermissionError("intake_product_citation_not_in_dependency_closure")
                    continue
                anchor = anchors.get(cite["source_id"])
                if anchor is None:
                    raise PermissionError("intake_product_source_not_assigned")
                if cite["kind"] == "image":
                    if not anchor["image_sha256"] or cite["image_sha256"] != anchor["image_sha256"]:
                        raise PermissionError("intake_image_citation_mismatch")
                else:
                    if not anchor["start"] <= cite["start"] < cite["end"] <= anchor["end"]:
                        raise PermissionError("intake_quote_outside_assigned_span")
                    if anchor["id"] not in texts:
                        texts[anchor["id"]] = self.source_text(sid, anchor)
                    if texts[anchor["id"]][cite["start"]:cite["end"]] != cite["quote"]:
                        raise PermissionError("intake_quote_not_exact_source_text")
        if any(e["from"] not in nodes or e["to"] not in nodes for e in product["edges"]):
            raise ValueError("intake_product_edge_endpoint_missing")
        expected = {v["id"]: v for v in app["acceptance_checks"]}
        checks = {v["obligation_id"]: v for v in product["checks"]}
        if len(checks) != len(product["checks"]) or checks.keys() != expected.keys():
            raise ValueError("intake_all_obligations_must_be_reported")
        for key, check in checks.items():
            if not set(check["node_ids"]) <= nodes.keys():
                raise ValueError("intake_check_node_missing")
            if check["state"] == "addressed" and (not check["node_ids"] or not any(nodes[n]["level"] == expected[key]["level"] for n in check["node_ids"])):
                raise ValueError("intake_addressed_check_requires_matching_level_product")
        if any(c["state"] == "unresolved" for c in checks.values()) and not product["uncovered"]:
            raise ValueError("intake_unresolved_checks_require_explicit_remainder")
