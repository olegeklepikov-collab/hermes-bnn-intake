import time
import pytest
from bn_memory.working import Working
from bnn_intake.interview import Interview
from conftest import prepare, request
from test_gaps import add_gap


def plan(interview, work, rev, identifier="Q1", topic="measurement", text="Как определён показатель?"):
    return interview.call("parent", request("plan_question", work, rev, "plan-" + identifier,
        question={"id": identifier, "gap_id": "G1", "topic": topic, "purpose": "Уточнить измерение", "text": text}))


def control(interview, work, rev, action, **extra):
    return interview.control("parent", {"action": action, "work_ref": work, "expected_revision": rev,
        "operation_id": "control-" + action + "-" + str(rev), **extra}, "native-test-owner")


def setup(fixture, contract):
    store, engine, work = fixture
    prepare(engine, work, contract); add_gap(engine, work)
    interview = Interview(engine)
    plan(interview, work, 2)
    return store, engine, work, interview


def test_stop_prevents_question_but_not_research_path(fixture, contract):
    store, engine, work, interview = setup(fixture, contract)
    control(interview, work, 3, "stop")
    with pytest.raises(PermissionError, match="interview_stopped"):
        interview.call("parent", request("present_question", work, 4, "present", question_id="Q1"))
    path = {"id": "P1", "gap_id": "G1", "family": "official", "purpose": "Исследовать без интервью", "state": "planned", "reason": "Иной разрешённый путь"}
    engine.call("parent", request("revise", work, 4, "research", change={"type": "plan_path", "path": path}))
    assert engine.status("parent", work)["state"]["gaps"]["G1"]["research_state"] == "open"


def test_work_resume_and_new_engine_do_not_resume_interview(fixture, contract):
    store, engine, work, interview = setup(fixture, contract)
    control(interview, work, 3, "stop")
    engine.call("parent", request("pause", work, 4, "p", reason="Позже"))
    engine.call("parent", request("resume", work, 5, "r", reason="Продолжить исследование"))
    from bnn_intake.engine import Intake
    assert Interview(Intake(store)).call("parent", request("status", work))["policy"]["state"] == "stopped"


def test_owner_can_stop_with_module_off_and_work_paused(fixture, contract):
    store, engine, work, interview = setup(fixture, contract)
    engine.call("parent", request("pause", work, 3, "p", reason="pause"))
    engine.set_mode("parent", "off", 1, "owner")
    control(interview, work, 4, "stop")
    assert interview.call("parent", request("status", work))["policy"]["state"] == "stopped"


def test_owner_limit_is_checked_before_text_is_issued(fixture, contract):
    store, engine, work, interview = setup(fixture, contract)
    control(interview, work, 3, "limit", limits={"max_questions": 0})
    with pytest.raises(PermissionError, match="question_limit"):
        interview.call("parent", request("present_question", work, 4, "present", question_id="Q1"))
    assert engine.status("parent", work)["state"]["questions"]["Q1"]["state"] == "planned"


def test_reserved_question_is_not_claimed_sent_and_repeat_is_idempotent(fixture, contract):
    store, engine, work, interview = setup(fixture, contract)
    args = request("present_question", work, 3, "present", question_id="Q1")
    first = interview.call("parent", args); again = interview.call("parent", args)
    assert again["replayed"] and first["question"]["issue_id"] == again["question"]["issue_id"]
    assert not first["received_by_user"] and not first["outbound_message_sent"]
    assert interview.call("parent", request("status", work))["policy"]["issued_count"] == 1


def test_cached_question_cannot_bypass_later_owner_stop(fixture, contract):
    store, engine, work, interview = setup(fixture, contract)
    args = request("present_question", work, 3, "present", question_id="Q1")
    interview.call("parent", args)
    control(interview, work, 4, "stop")
    with pytest.raises(PermissionError, match="interview_stopped"):
        interview.call("parent", args)


def test_blocked_topic_and_expired_deadline_are_independent_checks(fixture, contract):
    store, engine, work, interview = setup(fixture, contract)
    control(interview, work, 3, "limit", limits={"blocked_topics": ["measurement"]})
    with pytest.raises(PermissionError, match="topic_blocked"):
        interview.call("parent", request("present_question", work, 4, "p1", question_id="Q1"))
    control(interview, work, 4, "limit", limits={"deadline": time.time() - 1})
    with pytest.raises(PermissionError, match="deadline_reached"):
        interview.call("parent", request("present_question", work, 5, "p2", question_id="Q1"))


def test_refusal_cannot_be_reasked_by_renaming_question(fixture, contract):
    store, engine, work, interview = setup(fixture, contract)
    text = "Не хочу отвечать"
    Working(store).capture("parent", "turn-refusal", text)
    with store.db() as db:
        source = db.execute("SELECT id FROM working_sources WHERE turn_id='turn-refusal'").fetchone()[0]
    engine.call("parent", request("revise", work, 3, "refusal", change={"type": "record_response", "gap_id": "G1", "response_state": "refused",
        "source_ref": {"kind": "native_input", "id": source, "start": 0, "end": len(text)}}))
    plan(interview, work, 4, "Q2", text="Скажите, как считали метрику?")
    with pytest.raises(PermissionError, match="existing_response"):
        interview.call("parent", request("present_question", work, 5, "present", question_id="Q2"))
    control(interview, work, 5, "allow_reask", gap_id="G1")
    assert interview.call("parent", request("present_question", work, 6, "allowed", question_id="Q2"))["question"]["id"] == "Q2"


def test_model_tool_cannot_resume_or_raise_interview_limits(fixture, contract):
    store, engine, work, interview = setup(fixture, contract)
    for action in ["resume", "limit", "owner_interview_resume"]:
        with pytest.raises(ValueError):
            interview.call("parent", request(action, work, 3, "forged"))


def test_child_cannot_ask_even_when_parent_interview_is_open(fixture, contract):
    store, engine, work, interview = setup(fixture, contract)
    store.bind("child", parent="parent", subagent=True)
    store.assign("parent", "child", "parser", "scoped", "A1-fixture")
    assert not interview.call("child", request("status", work))["direct_interview_permitted"]
    with pytest.raises(PermissionError):
        interview.call("child", request("present_question", work, 3, "child", question_id="Q1"))


def test_schema_v1_migration_keeps_old_revisions_and_operations(fixture, contract):
    store, engine, work = fixture
    result = prepare(engine, work, contract)
    with store.db() as db:
        db.execute("DROP TABLE intake_path_attempts")
        db.execute("DROP TABLE intake_delegations")
        db.execute("DROP TABLE intake_model_outcomes")
        db.execute("DROP TABLE intake_document_links")
        db.execute("DROP TABLE intake_publications")
        db.execute("UPDATE meta SET value='1' WHERE key='semantic_intake_contract'")
    from bnn_intake.storage import install
    assert install(store) == {"schema_version": 6, "changed": True}
    assert prepare(engine, work, contract)["replayed"]
    assert engine.status("parent", work)["content_hash"] == result["content_hash"]
