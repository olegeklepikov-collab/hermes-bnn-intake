"""Explicit same-route inheritance through the public native lifecycle contract.

The SDK adapters pin provider/model, causing a fresh auth lookup. An isolated
profile may instead inherit the authenticated parent when the route is EXACTLY
the same. No key is read, copied, persisted or inserted into the request.
"""
import dataclasses
import os


class ParentRoute:
    def __init__(self, service):
        self.service = service

    def launch(self, request):
        from agent.subagent_lifecycle import get_active_subagent_parent, SubagentLaunchRequest
        from hermes_cli.config import load_config_readonly
        parent = get_active_subagent_parent()
        if (not isinstance(request, SubagentLaunchRequest) or parent is None
                or request.parent_session_id != parent.session_id):
            raise PermissionError("intake_route_requires_actual_active_parent")
        if request.provider == parent.provider and request.model == parent.model:
            if os.environ.get("HERMES_IGNORE_USER_CONFIG") == "1":
                raise PermissionError("intake_parent_route_requires_the_native_profile_config")
            config = (load_config_readonly() or {}).get("delegation") or {}
            if (any(config.get(key) for key in ["provider", "base_url", "api_key", "api_mode", "model", "request_overrides"])
                    or config.get("fallback_providers") != []):
                raise PermissionError("intake_parent_route_requires_explicit_no_fallback_and_no_override")
            # None is the documented pure-inherit branch of the native lifecycle.
            # The mandatory native grant still checks the resulting model/provider.
            request = dataclasses.replace(request, provider=None)
        return self.service.launch(request)

    def __getattr__(self, name):
        return getattr(self.service, name)


class Context:
    def __init__(self, context):
        self.context = context
        self.subagent_lifecycle = ParentRoute(context.subagent_lifecycle)

    def __getattr__(self, name):
        return getattr(self.context, name)
