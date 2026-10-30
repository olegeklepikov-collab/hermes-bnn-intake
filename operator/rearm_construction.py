"""Bounded operator recovery for the observed pre-child context-toolset failure.

Not a model tool. Preserves earlier Beads claims, provider usage and failed grants.
No generic unknown-outcome reset is provided.
"""
import json
from pathlib import Path
from bn_memory.store import canonical, sha
from bn_memory.base_work import BaseWork
from bn_memory.teams import Team


def rearm(host, sid, *, expected, reason, stopped_parent_attestation):
    root = Path(__file__).resolve().parents[1]
    if not Path(host.store.root).resolve().is_relative_to(root / ".test-state"):
        raise PermissionError("operator_probe_recovery_refuses_live_state")
    if stopped_parent_attestation is not True or not isinstance(reason, str) or not reason.strip():
        raise PermissionError("stopped_parent_operator_attestation_required")
    host.store.writable(sid)
    work = host.working.binding(sid); base = BaseWork(host); base.require_work(sid, work)
    team = Team(base); changed = []; prepared = []
    rows = team.rows(sid, work)
    if {r["id"]: r["revision"] for r in rows} != expected:
        raise PermissionError("native_plan_changed_before_operator_recovery")
    with host.store.db() as db:
        unresolved = db.execute("SELECT 1 FROM model_attempts WHERE profile_id=? AND root_sid=? AND state IN ('reserved','outcome_unknown')",
                                (host.store.profile, sid)).fetchone()
    if unresolved:
        raise PermissionError("unresolved_provider_outcome_cannot_be_reset")
    for row in rows:
        body = json.loads(row["body"])
        if row["state"] not in {"unknown", "planned"}:
            raise PermissionError("recovery_does_not_reset_started_or_completed_children")
        with host.store.db() as db:
            history = db.execute("SELECT body,sha256 FROM native_task_history WHERE profile_id=? AND id=? AND revision=?",
                                 (host.store.profile, row["id"], row["revision"])).fetchone()
            if history is None or history[0] != row["body"] or sha(history[0]) != history[1]:
                raise PermissionError("native_recovery_history_integrity_failed")
            grant = db.execute("SELECT * FROM launch_grants WHERE profile_id=? AND id=?", (host.store.profile, body.get("assignment_id"))).fetchone()
            evidence = db.execute("SELECT 1 FROM team_evidence WHERE profile_id=? AND assignment=?", (host.store.profile, body.get("assignment_id"))).fetchone()
        if body.get("child_sid") or body.get("receipt") or body.get("handle") or evidence:
            raise PermissionError("child_or_effect_exists_requires_actual_outcome_review")
        if row["state"] == "unknown":
            message = body.get("error_message") or ""
            known = message == "Unknown toolsets: context." or (
                host.config.get("inherit_current_parent_auth") is True and
                message.startswith("Cannot resolve delegation provider 'openai-codex': No Codex credentials stored."))
            if (body.get("error_class") != "SubagentLifecycleError" or not known
                    or grant is None or grant["state"] != "failed" or grant["child_sid"] or grant["subagent_id"]):
                raise PermissionError("exact_observed_preconstruction_failure_required")
        elif grant is not None or body.get("assignment_id"):
            raise PermissionError("planned_child_must_never_have_been_launched")
        issue = team.tasks.get({"issue_id": row["bead"]})["issue"]
        if row["state"] == "unknown" and issue.get("assignee") != "BNN-" + row["id"]:
            raise PermissionError("original_Beads_holder_changed")
        observation = {"input_version": body["input_version"], "product": None, "receipt": None,
            "prior_state": row["state"], "prior_body_sha256": sha(row["body"]), "failed_assignment_id": body.get("assignment_id"),
            "operator_observation": "Exact SDK rejection before child allocation; no child handle, admission or model product. Earlier Beads claim remains recorded." if row["state"] == "unknown" else "Dependent planned task was never launched; explicit native reading requirement retained.",
            "parent_stopped_is_operator_attestation_not_OS_proof": True, "reason": reason}
        body["previous_products"].append(observation)
        body["input_version"] += 1
        body["assignment_id"] = None; body["child_sid"] = None; body["receipt"] = None
        body.pop("error_class", None); body.pop("error_message", None)
        body["required_tools"] = list(dict.fromkeys(body.get("required_tools", []) + ["mcp__context__memory_read"]))
        read_goal = " Обязательно вызови mcp__context__memory_read для назначенной записи, сверь полный текст с intake-контекстом и верни нативный JSON-контракт. Это проверка действительного чтения, не новый источник."
        if read_goal not in body["goal"]:
            body["goal"] += read_goal
        prepared.append((row, body))
    # Validate the whole selection before the first write. Native per-row CAS
    # still applies; an intervening coordinator gets an explicit partial result.
    for index, (row, body) in enumerate(prepared):
        try:
            team.save(row, body, "planned")
        except Exception:
            return {"status": "partial_requires_observation", "rearmed": changed,
                    "remaining": [r["id"] for r, _ in prepared[index:]], "provider_usage_or_history_reset": False}
        changed.append({"task_id": row["id"], "bead_id": row["bead"], "input_version": body["input_version"], "native_revision": row["revision"]})
    return {"status": "complete", "rearmed": changed, "same_primary_tasks": True, "failed_grants_retained": True,
            "earlier_Beads_claims_retained": True, "provider_usage_or_history_reset": False, "reason": reason}
