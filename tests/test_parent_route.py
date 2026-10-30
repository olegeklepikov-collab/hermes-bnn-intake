from types import SimpleNamespace
import pytest
from agent.subagent_lifecycle import SubagentLaunchRequest, bind_subagent_parent
from bnn_intake.parent_route import ParentRoute


def test_exact_parent_route_uses_native_inheritance_without_a_credential_field(monkeypatch):
    from hermes_cli import config
    monkeypatch.setattr(config, "load_config_readonly", lambda: {"delegation": {"fallback_providers": []}})
    calls = []
    service = SimpleNamespace(launch=lambda request: calls.append(request) or "native-handle")
    parent = SimpleNamespace(session_id="parent", provider="openai-codex", model="gpt-6.1-sol")
    request = SubagentLaunchRequest(goal="One native leaf", role="leaf", parent_session_id="parent", provider=parent.provider, model=parent.model, correlation_id="unchanged")
    with bind_subagent_parent(parent):
        assert ParentRoute(service).launch(request) == "native-handle"
    assert calls[0].provider is None and calls[0].model == request.model
    assert calls[0].correlation_id == request.correlation_id and calls[0].goal == request.goal
    assert not hasattr(calls[0], "api_key") and request.provider == "openai-codex"


@pytest.mark.parametrize("override", [{"provider": "another"}, {"base_url": "http://127.0.0.1:1"}, {"fallback_providers": [{"provider": "another"}]}])
def test_parent_route_does_not_silently_follow_an_override(monkeypatch, override):
    from hermes_cli import config
    monkeypatch.setattr(config, "load_config_readonly", lambda: {"delegation": {"fallback_providers": [], **override}})
    calls = []; service = SimpleNamespace(launch=lambda request: calls.append(request))
    parent = SimpleNamespace(session_id="parent", provider="openai-codex", model="gpt-6.1-sol")
    with bind_subagent_parent(parent), pytest.raises(PermissionError, match="no_fallback_and_no_override"):
        ParentRoute(service).launch(SubagentLaunchRequest(goal="One leaf", parent_session_id="parent", provider=parent.provider, model=parent.model))
    assert calls == []


def test_other_explicit_provider_keeps_its_original_native_resolution():
    calls = []; service = SimpleNamespace(launch=lambda request: calls.append(request) or "native")
    parent = SimpleNamespace(session_id="parent", provider="openai-codex", model="gpt-6.1-sol")
    request = SubagentLaunchRequest(goal="Another allowed route", parent_session_id="parent", provider="other", model="other-model")
    with bind_subagent_parent(parent):
        ParentRoute(service).launch(request)
    assert calls == [request]


def test_parent_route_refuses_a_different_native_config_source(monkeypatch):
    monkeypatch.setenv("HERMES_IGNORE_USER_CONFIG", "1")
    calls = []; service = SimpleNamespace(launch=lambda request: calls.append(request))
    parent = SimpleNamespace(session_id="parent", provider="openai-codex", model="gpt-6.1-sol")
    with bind_subagent_parent(parent), pytest.raises(PermissionError, match="native_profile_config"):
        ParentRoute(service).launch(SubagentLaunchRequest(goal="One leaf", parent_session_id="parent", provider=parent.provider, model=parent.model))
    assert calls == []
