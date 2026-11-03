"""Pinned composition adapter for ONE native common Host, no vendor monkeypatch.

Installed by an explicit operator action. The off loader retains shared native
controls and acceptance policy without importing this optional package.
"""
import hashlib
import hmac
import json
from importlib.resources import files
from pathlib import Path
from .storage import canonical


def compatibility():
    return json.loads(files("bnn_intake").joinpath("data/native-compatibility.json").read_text())


def verify_native(config):
    from bn_memory import plugin as common
    from hermes_cli import execution_control
    pinned = compatibility(); root = Path(common.__file__).resolve().parent.parent
    core = Path(execution_control.__file__).resolve().parent.parent
    if Path(config["package_root"]).resolve() != root:
        raise PermissionError("intake_native_package_binding_mismatch")
    for name, digest in pinned["memory_files"].items():
        path = root / name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise PermissionError("intake_native_memory_version_not_qualified:" + name)
    for name, digest in pinned["core_files"].items():
        path = core / name
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise PermissionError("intake_native_core_version_not_qualified:" + name)
    return pinned


def verify_deployment(home, settings, config):
    home, settings = Path(home).resolve(), Path(settings).resolve()
    envelope = json.loads((home / "intake-deployment.json").read_text())
    value = envelope["deployment"]
    from . import __version__
    if value.get("version") != __version__:
        raise PermissionError("intake_deployment_package_version_requires_operator_update")
    root = Path(value["managed_root"]).resolve()
    from .isolation import validate_modules
    validate_modules(config, root, inspect_mutable_settings=False)
    if (value["profile_home"] != str(home) or value["settings_file"] != str(settings)
            or not home.is_relative_to(root) or not settings.is_relative_to(root)
            or Path(config["profile_home"]).resolve() != home):
        raise PermissionError("intake_deployment_profile_binding_mismatch")
    if hashlib.sha256(settings.read_bytes()).hexdigest() != value["settings_sha256"]:
        raise PermissionError("intake_deployment_settings_changed_requires_operator")
    key = Path(config["host_key"]).read_bytes()
    expected = hmac.new(key, canonical(value).encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(envelope["signature"], expected):
        raise PermissionError("intake_deployment_signature_invalid")


def register(ctx):
    from hermes_constants import get_hermes_home
    from bn_memory.plugin import Host
    settings = Path(ctx.get_config("config_file"))
    config = json.loads(settings.read_text())
    verify_native(config)
    verify_deployment(get_hermes_home(), settings, config)
    host = Host(settings)
    host.ctx = ctx
    if config.get("inherit_current_parent_auth"):
        from .parent_route import Context
        host.ctx = Context(ctx)
    ctx.on_unload(host.close)
    from .adapter import attach
    attach(host, ctx)  # Required BEFORE any bound callbacks are registered.
    register_common(host, ctx)
    if config.get("native_context_tools"):
        from .native_memory import register as native_transport
        native_transport(host, ctx)


def register_common(host, ctx):
    """The pinned native register() sequence, with its original public interfaces.

    Changes to supplier code require reviewing this adapter, not silently
    executing an edited vendor function or replacing its Host factory.
    """
    from bnn_intake.context_preparation import install_context_preparation
    install_context_preparation(host)
    if host.config.get('native_background_review',False):
        from bn_memory.background_review import register as register_native_review
        ctx.on_unload(register_native_review(host,ctx))
    if host.identity:
        from agent.identity_provider import register_identity_provider
        ctx.on_unload(register_identity_provider(host.admission.profile_home, host.identity.resolve))
    from agent.compression_checkpoint import register_child_checkpoint_adapter
    ctx.on_unload(register_child_checkpoint_adapter("bn-common-memory", host.checkpoints.child_adapter,
        profile_home=host.admission.profile_home,
        **({"on_compression_complete": host.checkpoints.native_commit} if host.modules else {})))
    for name, fn in [("pre_llm_call", getattr(host, "stage_turn", host.before_turn)), ("subagent_start", host.child_start)]:
        ctx.register_hook(name, fn)
    for name, fn in [("tool_request", host.tool_request), ("tool_execution", host.tool_execution),
                     ("llm_request", host.request), ("llm_execution", host.execution)]:
        ctx.register_middleware(name, fn)
    for name, fn in [("tool", host.tool_control), ("tool_result", host.result_control), ("llm", host.model_control),
                     ("auxiliary", host.model_control), ("subagent_launch", host.launch_control)]:
        ctx.register_execution_control(name, fn)
    if host.config.get("service_integration"):
        from bn_memory.service_engine import owner_command
        ctx.register_command("services", lambda raw: owner_command(host, raw), description="Просмотр и согласование точного изменения сервиса", args_hint="inspect|approve|deny <id>")
    if host.modules:
        ctx.register_hook("post_auxiliary_call", host.accounting.auxiliary_finished)
        from bn_memory.modules import register as modules
        from bn_memory.module_tools import register as module_tools
        modules(host, ctx); module_tools(host, ctx)
        ctx.register_skill("expert-work", Path(host.config["package_root"]) / "skills/expert-work/SKILL.md", description="Экспертная задача, исследование и команда исполнителей.")
        for item in host.config.get("service_integration", {}).get("skills", []):
            ctx.register_skill(item["name"], Path(host.config["package_root"]) / item["path"], description=item["description"])
