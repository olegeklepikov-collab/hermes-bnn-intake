from types import SimpleNamespace
import pytest
from agent.tool_attempt import tool_attempt_scope
from bnn_intake.adapter import attach
from conftest import request


class Handle:
    def __init__(self):
        self.disposed = False
    def dispose(self):
        self.disposed = True


class ContextFixture:
    """Registration harness only; not a real gateway or execution controller."""
    def __init__(self, fail=None):
        self.tools = {}; self.commands = {}; self.handles = []; self.fail = fail
    def register_tool(self, name, **kw):
        self.tools[name] = kw
        handle = Handle(); self.handles.append(handle)
        return None if name == self.fail else handle
    def register_command(self, name, handler, **kw):
        self.commands[name] = handler
        handle = Handle(); self.handles.append(handle)
        return handle


def host(store, ready=None):
    return SimpleNamespace(store=store, config=store.config, before_turn=lambda **kw: None, request=lambda *a,**kw: None,
        modules=None, broker=SimpleNamespace(store=store, config=store.config), document_role=lambda sid: None, admission=SimpleNamespace(
        profile_home=store.config["profile_home"], ready=ready or (lambda sid, turn: None)))


def test_native_handler_cannot_be_called_with_forged_identity_kwargs(fixture):
    store, engine, work = fixture
    ctx = ContextFixture(); attach(host(store), ctx)
    with pytest.raises(PermissionError, match="native_tool_attempt_required"):
        ctx.tools["intake_work"]["handler"](request("status", work), session_id="parent", turn_id="fake")


def test_native_handler_checks_actual_attempt_profile_before_effect(fixture, monkeypatch):
    store, engine, work = fixture
    ctx = ContextFixture(); attach(host(store), ctx)
    monkeypatch.setenv("HERMES_HOME", str(store.root.parent / "wrong-profile"))
    with tool_attempt_scope("intake_work", {"session_id": "parent", "turn_id": "t"}, final_boundary="registry"):
        with pytest.raises(PermissionError, match="native_tool_attempt_required"):
            ctx.tools["intake_work"]["handler"](request("status", work))


def test_required_host_admission_failure_is_not_masked(fixture, monkeypatch):
    store, engine, work = fixture
    def fail(sid, turn):
        raise PermissionError("required_common_admission_unavailable")
    ctx = ContextFixture(); attach(host(store, fail), ctx)
    monkeypatch.setenv("HERMES_HOME", store.config["profile_home"])
    with tool_attempt_scope("intake_work", {"session_id": "parent", "turn_id": "t"}, final_boundary="registry"):
        with pytest.raises(PermissionError, match="required_common_admission"):
            ctx.tools["intake_work"]["handler"](request("status", work))


def test_incomplete_registration_rolls_back_registered_handles(fixture):
    store, engine, work = fixture
    ctx = ContextFixture(fail="intake_methods")
    with pytest.raises(RuntimeError, match="registration_incomplete"):
        attach(host(store), ctx)
    # Only successfully registered handles are disposable registrations.
    assert ctx.handles[0].disposed and ctx.handles[-1].disposed


def test_control_reason_is_redacted_without_losing_the_error_type():
    from bnn_intake.context import safe_reason
    value = safe_reason(PermissionError("api_key=sk-proj-123456789012345678901234567890"))
    assert "PermissionError" in value and "123456789012345678901234567890" not in value
