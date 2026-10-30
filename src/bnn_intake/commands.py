"""Authenticated native commands; no model argument can invoke owner controls."""
import datetime
import shlex
import uuid
from .interview import Interview
from .storage import canonical


def owner_command(host, engine, raw):
    from bn_memory.modules import command_identity
    identity = command_identity(host)
    if identity["profile_home"] != host.admission.profile_home:
        raise PermissionError("intake_command_profile_mismatch")
    sid = identity["session_id"]
    host.store.bind(sid); host.store.writable(sid)
    parts = shlex.split(raw)
    action = parts[0] if parts else "status"
    if action == "status" and len(parts) <= 1:
        return canonical(engine.mode(sid))
    if action != "interview":
        if action not in {"on", "off", "auto"} or len(parts) not in {1, 3} or len(parts) == 3 and parts[1] != "--revision":
            raise ValueError("use /intake on|off|auto|status [--revision N]")
        revision = int(parts[2]) if len(parts) == 3 else engine.mode(sid)["revision"]
        return canonical(engine.set_mode(sid, action, revision, identity["actor"]))
    work = host.working.binding(sid)
    interview = Interview(engine)
    sub = parts[1] if len(parts) > 1 else "status"
    status_args = {"schema_version": 1, "action": "status", "work_ref": work}
    if sub == "status" and len(parts) <= 2:
        return canonical(interview.call(sid, status_args))
    state = engine.status(sid, work)["state"]
    event = {"action": sub.replace("-", "_"), "work_ref": work,
             "expected_revision": state["revision"], "operation_id": "owner-" + uuid.uuid4().hex}
    if sub in {"pause", "stop", "resume"} and len(parts) == 2:
        pass
    elif sub == "allow-reask" and len(parts) == 3:
        event["gap_id"] = parts[2]
    elif sub == "limit":
        limits = {}; words = parts[2:]
        while words:
            option = words.pop(0)
            if option == "--clear-blocked-topics":
                limits["blocked_topics"] = []
                continue
            if option not in {"--max-questions", "--blocked-topic", "--until"} or not words:
                raise ValueError("intake_interview_limit_option_invalid")
            value = words.pop(0)
            if option == "--max-questions":
                limits["max_questions"] = None if value == "none" else int(value)
            elif option == "--blocked-topic":
                previous = state.get("interview", {}).get("blocked_topics", [])
                limits["blocked_topics"] = sorted(set(limits.get("blocked_topics", previous)) | {value})
            else:
                if value == "none":
                    limits["deadline"] = None
                else:
                    until = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
                    if until.tzinfo is None:
                        raise ValueError("intake_interview_deadline_timezone_required")
                    limits["deadline"] = until.timestamp()
        event["limits"] = limits
    else:
        raise ValueError("use /intake interview status|pause|stop|resume|limit|allow-reask")
    return canonical(interview.control(sid, event, identity["actor"]))
