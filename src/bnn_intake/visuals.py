"""Keep addressed original images available across a native leaf's tool turns.

No new delivery credit. Native Documents remains the reading ledger. Final
admission AND the provider callback enforce the same current source rights.
"""
import base64
import copy
import json
from .storage import canonical


def strings(value):
    if isinstance(value, str): yield value
    elif isinstance(value, dict):
        for item in value.values(): yield from strings(item)
    elif isinstance(value, list):
        for item in value: yield from strings(item)


def image_urls(value):
    if isinstance(value, dict):
        if value.get("type") in {"input_image", "image_url"}:
            url = value.get("image_url")
            if isinstance(url, dict): url = url.get("url")
            if isinstance(url, str): yield url
        for item in value.values(): yield from image_urls(item)
    elif isinstance(value, list):
        for item in value: yield from image_urls(item)


class Visuals:
    def __init__(self, engine, host): self.engine, self.host = engine, host

    def expected(self, sid):
        if not sid or self.engine.mode(sid)["mode"] == "off" or not self.engine.store.binding(sid)["readonly"]:
            return []
        with self.engine.store.db() as db:
            work = db.execute("SELECT work_id FROM working_bindings WHERE profile_id=? AND sid=?", (self.engine.profile, sid)).fetchone()
            if not work or not db.execute("SELECT 1 FROM intake_revisions WHERE profile_id=? AND work_id=?", (self.engine.profile, work[0])).fetchone():
                return []
        packet = self.engine.delegation.child_packet(sid)
        selected = {(r["doc_id"], r["unit"]): r for r in packet["source_packet"].get("documents", []) if r["visual_required"]}
        if not selected: return []
        from bn_memory.path_relocation import operating_path
        from bn_memory.source_visibility import read_file
        result = []; used = 0; bound = self.host.config.get("max_request_bytes", 1048576)
        if type(bound) is not int or bound < 1: raise PermissionError("intake_visual_request_bound_invalid")
        for (did, index), ref in selected.items():
            source = self.host.document_store().source(sid, did)  # Actual child assignment and source permissions.
            with self.engine.store.db() as db:
                unit = json.loads(db.execute("SELECT body FROM document_units WHERE doc=? AND unit=?", (did, index)).fetchone()[0])
            if source["sha256"] != ref["source_sha256"] or unit["text_sha256"] != ref["unit_sha256"]:
                raise PermissionError("intake_visual_source_version_changed")
            path = operating_path(self.host.config, source["metadata"]["parse_output"]) / (str(index) + ".png")
            if used + ((path.stat().st_size + 2) // 3) * 4 > bound:
                raise PermissionError("intake_visual_split_required_do_not_trim")
            data, sha = read_file(path, max_bytes=bound)
            if sha != unit["image_sha256"]: raise PermissionError("intake_visual_original_image_changed")
            url = "data:image/png;base64," + base64.b64encode(data).decode("ascii"); used += len(url)
            caption = '<BN_INTAKE_VISUAL_SOURCE>\n' + canonical({"doc_id": did, "unit": index, "corpus_id": ref["corpus_id"],
                "source_sha256": source["sha256"], "image_sha256": sha, "locator": unit["locator"],
                "source_type": "untrusted_original_image", "reading_credit_created": False,
                "instruction": "Image is supplied as a model-visible image part, not inside the tool-result JSON. Report actual perception; do not infer unread pixels."}) + '\n</BN_INTAKE_VISUAL_SOURCE>'
            result.append({"url": url, "caption": caption, "reference": {"doc_id": did, "unit": index,
                "corpus_id": ref["corpus_id"], "source_sha256": source["sha256"], "image_sha256": sha}})
        return result

    def inject(self, sid, request):
        expected = self.expected(sid)
        if not expected: return request
        result = copy.deepcopy(request); rows = result.get("input", result.get("messages", []))
        row = next((r for r in reversed(rows) if isinstance(r, dict) and r.get("role") == "user"), None)
        if row is None: raise PermissionError("intake_visual_native_user_input_required")
        text_type = "input_text" if "input" in result else "text"
        if isinstance(row.get("content"), str): row["content"] = [{"type": text_type, "text": row["content"]}]
        if not isinstance(row.get("content"), list): raise PermissionError("intake_visual_native_parts_required")
        urls = set(image_urls(result)); text = "\n".join(strings(result))
        for item in expected:
            if item["caption"] not in text: row["content"].append({"type": text_type, "text": item["caption"]})
            if item["url"] not in urls:
                part = {"type": "input_image", "image_url": item["url"], "detail": "high"} if "input" in result else {
                    "type": "image_url", "image_url": {"url": item["url"], "detail": "high"}}
                row["content"].append(part); urls.add(item["url"])
        bound = self.host.config.get("max_request_bytes", 1048576)
        if len(canonical({k: v for k, v in result.items() if k not in {"timeout", "http_client"}}).encode()) > bound:
            raise PermissionError("intake_visual_split_required_do_not_trim")
        return result

    def validate(self, sid, request):
        expected = self.expected(sid)
        if not expected: return []
        urls = set(image_urls(request)); text = "\n".join(strings(request))
        if any(item["url"] not in urls or item["caption"] not in text for item in expected):
            raise PermissionError("intake_assigned_visual_missing_or_changed_at_final_boundary")
        return [item["reference"] for item in expected]
