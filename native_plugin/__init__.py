"""Actual Hermes discovery entry for isolated qualification, never live activation.

Production integration uses adapter.attach on the existing common Host, before
its registrations. This entry owns exactly one Host in a fresh test profile.
"""
import json
from pathlib import Path


def register(ctx):
    import bnn_intake
    from hermes_constants import get_hermes_home
    from bn_memory.plugin import Host
    from bnn_intake.adapter import attach
    root = Path(bnn_intake.__file__).resolve().parents[2] / ".test-state"
    settings = Path(ctx.get_config("settings_file", "")).resolve()
    if not settings.is_relative_to(root.resolve()):
        raise PermissionError("intake_isolated_entry_requires_repository_test_state")
    config = json.loads(settings.read_text())
    if any(not Path(config[key]).resolve().is_relative_to(root.resolve()) for key in ["profile_home", "state_root"]):
        raise PermissionError("intake_isolated_entry_refuses_live_paths")
    if Path(config["profile_home"]).resolve() != get_hermes_home().resolve():
        raise PermissionError("intake_isolated_profile_binding_mismatch")
    from bnn_intake.isolation import validate_modules
    validate_modules(config, Path(config["profile_home"]).resolve().parent)
    if ctx.has_plugin("bn-common-memory"):
        raise PermissionError("intake_isolated_entry_cannot_add_a_second_common_host")
    host = Host(settings)
    host.ctx = ctx
    if config.get("inherit_current_parent_auth"):
        from bnn_intake.parent_route import Context
        host.ctx = Context(ctx)
    ctx.on_unload(host.close)
    attach(host, ctx)
    if config.get("intake_probe_source"):
        import hashlib
        from bn_memory.source_visibility import read_file
        source = Path(config["intake_probe_source"]).resolve()
        if not source.is_relative_to(root.resolve()):
            raise PermissionError("intake_probe_source_must_be_in_isolated_state")
        def original(args, **identity):
            if args != {"item_id": "rules"}:
                raise PermissionError("intake_probe_requires_assigned_item")
            raw = read_file(source, max_bytes=262144)[0]
            if hashlib.sha256(raw).hexdigest() != config["intake_probe_source_sha256"]:
                raise PermissionError("intake_probe_original_changed")
            return raw.decode("utf-8")
        ctx.register_tool(name="probe_original", toolset="bnn_intake", schema={"name": "probe_original",
            "description": "Только назначенный локальный синтетический оригинал проверки; не внешний поисковый сервис.",
            "parameters": {"type": "object", "properties": {"item_id": {"const": "rules"}}, "required": ["item_id"], "additionalProperties": False}}, handler=original)
    for kind, fn in [("tool", host.tool_control), ("tool_result", host.result_control),
                     ("llm", host.model_control), ("auxiliary", host.model_control), ("subagent_launch", host.launch_control)]:
        ctx.register_execution_control(kind, fn)
    ctx.register_hook("pre_llm_call", host.before_turn)
    ctx.register_hook("subagent_start", host.child_start)
    ctx.register_middleware("llm_request", host.request)
    ctx.register_middleware("llm_execution", host.execution)
    if config.get("native_context_tools"):
        from bnn_intake.native_memory import register as register_native_memory
        register_native_memory(host, ctx)
        ctx.register_middleware("tool_request", host.tool_request)
        ctx.register_middleware("tool_execution", host.tool_execution)
    if config.get("module_tool_catalog"):
        from bn_memory.module_tools import register as register_module_tools
        register_module_tools(host, ctx)
