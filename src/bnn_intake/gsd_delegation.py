"""Addressed reader of the canonical GSD ledger, never a second GSD executor."""
import re


def read_result(delegation, sid, args):
    if not re.fullmatch(r"J\.[a-f0-9]{32}", args["job_id"]):
        raise ValueError("intake_canonical_GSD_job_id_required")
    host = delegation.host
    host.modules.require(sid, "gsd")
    from bn_memory.gsd_module import get
    adapter = get(host)
    job = adapter.runtime.bridge.call("job_get", {"job_id": args["job_id"]})
    receipt = job.get("native_receipt") or {}
    child = job.get("child_session_id")
    if not child or job["status"] != "completed" or receipt.get("terminal_state") != "SUCCEEDED":
        raise PermissionError("intake_GSD_successful_native_product_required")
    binding = delegation.binding(child)
    if (binding["work_ref"] != args["work_ref"] or binding["spec"]["module"] != "gsd"
            or binding["spec"]["role"] != job["role"] or binding["parent_sid"] != job["parent_session_id"]):
        raise PermissionError("intake_GSD_result_handoff_mismatch")
    actual = host.store.binding(child)
    with host.store.db() as db:
        grant = db.execute("SELECT * FROM launch_grants WHERE profile_id=? AND id=?",
                           (host.store.profile, binding["assignment_id"])).fetchone()
    if (grant is None or grant["module"] != "gsd" or grant["role"] != job["role"]
            or grant["state"] != "admitted" or grant["child_sid"] != child
            or grant["parent_sid"] != binding["parent_sid"] or actual["assignment_id"] != grant["id"]):
        raise PermissionError("intake_GSD_original_native_grant_required")
    delegation.verify_reader_sources(sid, child)
    # Native reader verifies the actual saved product hash and family ownership.
    # The new caller has already proved SAME work + its OWN current source rights.
    value = adapter.runtime.read_role({"job_id": job["id"], "view": "validated",
        "offset": args.get("offset", 0), "max_chars": args.get("limit", 4096)}, binding["parent_sid"])
    value.update(handoff_basis=binding["basis"], origin_parent_sid=binding["parent_sid"], reader_sid=sid,
                 scientific_acceptance=False, reading_level="canonical_GSD_product_not_independent_source")
    return value
