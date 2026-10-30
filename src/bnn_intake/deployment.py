"""Operator-only installation and quiescent managed-profile recovery.

Never exposed as model tools. No vendor edits, deleted user data, inferred
permission to activate a live profile, or claim to back up external programs.
"""
import contextlib
import datetime
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
from . import __version__
from .storage import canonical

LOADER = '''from pathlib import Path
import fcntl,json,sys
def register(ctx):
    from hermes_constants import get_hermes_home
    settings=Path(ctx.get_config('config_file'));config=json.loads(settings.read_text())
    if Path(config['profile_home']).resolve()!=get_hermes_home().resolve():raise PermissionError('intake_loader_profile_mismatch')
    sys.path.insert(0,config['package_root'])
    lock=(get_hermes_home()/".intake-runtime.lock").open("a")
    fcntl.flock(lock,fcntl.LOCK_SH|fcntl.LOCK_NB)
    ctx.on_unload(lock.close)
    if ctx.get_config("intake_enabled",False):
        library=ctx.get_config('intake_library',None)
        if library:sys.path.insert(0,str(Path(library).resolve(strict=True)))
        from bnn_intake.bootstrap import register as loaded
    else:
        from .common_policy import register as loaded
    return loaded(ctx)
'''
MANIFEST = '''name: bn-common-memory
version: 0.11.0
manifest_version: 2
api_version: 1
kind: standalone
description: Explicit pinned common Host composition; optional intake
config_schema:
  config_file:
    type: str
    required: true
  intake_enabled:
    type: bool
    default: false
  intake_library:
    type: str
    required: false
'''


def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_app_config(path,value):
    """Existing BNN maintenance also reads JSON encoded config.yaml files."""
    import yaml
    try:json.loads(Path(path).read_text())
    except json.JSONDecodeError:Path(path).write_text(yaml.safe_dump(value,allow_unicode=True,sort_keys=False))
    else:write(path,value)


def local(root, path):
    root, given = Path(root).absolute(), Path(path).absolute()
    if ".." in given.parts or ".." in root.parts or any(p.is_symlink() for p in [given, *given.parents]):
        raise PermissionError("deployment_symlink_not_allowed")
    if not given.is_relative_to(root) or given == root:
        raise PermissionError("deployment_path_outside_managed_root")
    return given


def write(path, value):
    path = Path(path); tmp = path.with_name(path.name + ".new")
    if tmp.exists(): raise PermissionError("deployment_unsettled_temporary_file")
    with tmp.open("x") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
        stream.flush(); os.fsync(stream.fileno())
    os.replace(tmp, path)


@contextlib.contextmanager
def stopped(root, home, offline_attested):
    if offline_attested is not True: raise PermissionError("deployment_requires_stopped_profile_attestation")
    local(root, home)
    control = (Path(root) / ".intake-operator.lock").open("a")
    runtime = (Path(home) / ".intake-runtime.lock").open("a")
    try:
        fcntl.flock(control, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(runtime, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    except BlockingIOError as exc:
        raise PermissionError("deployment_profile_or_operator_still_running") from exc
    finally:
        runtime.close(); control.close()


def config_at(root, settings, *, inspect_modules=True):
    settings = local(root, settings); config = json.loads(settings.read_text())
    for name in ["profile_home", "state_root", "host_key"]: local(root, config[name])
    for path in config.get("projects", {}).values(): local(root, path)
    from .isolation import validate_modules
    validate_modules(config, root, inspect_mutable_settings=inspect_modules)
    return settings, config


def preservation_witness(dbfile):
    """Restoring files never authorizes a rewind of costs, effects or owner controls."""
    result = {}
    with sqlite3.connect(Path(dbfile).as_uri() + "?mode=ro", uri=True) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ["model_attempts", "action_attempts", "module_attempts", "native_tasks", "action_reviews", "action_permits", "action_permit_events", "intake_modes",
                      "module_mode_events", "session_policy", "research_bindings", "research_binding_history", "research_deliveries", "launch_grants", "assignment_resources",
                      "records", "current", "record_metadata", "source_policies"]:
            if table not in tables: result[table] = None; continue
            rows = sorted([[{"blob_sha256":hashlib.sha256(value).hexdigest(),"bytes":len(value)} if isinstance(value,bytes) else value
                            for value in row] for row in db.execute("SELECT * FROM " + table)], key=canonical)
            result[table] = hashlib.sha256(canonical(rows).encode()).hexdigest()
        controls = []
        if "intake_revisions" in tables:
            for profile, work, body in db.execute("SELECT profile_id,work_id,body FROM intake_revisions r WHERE revision=(SELECT MAX(revision) FROM intake_revisions v WHERE v.profile_id=r.profile_id AND v.work_id=r.work_id)"):
                controls.append([profile, work, json.loads(body).get("interview")])
        result["owner_interview_controls"] = hashlib.sha256(canonical(sorted(controls, key=canonical)).encode()).hexdigest()
    return result


def configuration_witness(settings, config):
    home = Path(config['profile_home'])
    return {str(path): digest(path) if path.is_file() else None for path in
            [Path(settings), home/'config.yaml', home/'execution-controls.json', Path(config['host_key'])]}


def enroll(root, settings):
    from .bootstrap import verify_native
    settings, config = config_at(root, settings); verify_native(config)
    value = {"contract": "BNNIntakeDeploymentV1", "version": __version__, "managed_root": str(Path(root).absolute()),
             "profile_home": config["profile_home"], "settings_file": str(settings), "settings_sha256": digest(settings)}
    key = Path(config["host_key"]).read_bytes()
    write(Path(config["profile_home"]) / "intake-deployment.json", {"deployment": value,
        "signature": hmac.new(key, canonical(value).encode(), hashlib.sha256).hexdigest()})
    return value


def common_plugin_target(root, home):
    """Keep the existing profile plugin directory; never add a second Host."""
    import yaml
    plugins=local(root,Path(home)/'plugins');found=[]
    if plugins.exists():
        for folder in sorted(plugins.iterdir()):
            manifest=folder/'plugin.yaml'
            if not manifest.exists():continue
            local(root,manifest)
            value=yaml.safe_load(manifest.read_text())
            if isinstance(value,dict) and value.get('name')=='bn-common-memory':
                found.append(local(root,folder))
    if len(found)>1:raise PermissionError('deployment_duplicate_common_plugin_requires_resolution')
    if found:return found[0]
    target=local(root,plugins/'bn-common-memory')
    if target.exists():raise PermissionError('deployment_unidentified_common_plugin_target')
    return target


def install(root, settings, *, offline_attested, backup_scope='managed', library=None):
    """Existing native state/config required; only explicit additive migration."""
    import yaml
    if backup_scope not in {'managed','activation'}:raise ValueError('deployment_unknown_backup_scope')
    settings, config = config_at(root, settings); home = Path(config["profile_home"])
    if library is not None:
        library=local(root,library)
        if (library/'bnn_intake/deployment.py').resolve()!=Path(__file__).resolve():
            raise PermissionError('deployment_library_must_match_running_installed_package')
    app = yaml.safe_load((home / "config.yaml").read_text()) or {}
    if "bnn-intake-isolated" in app.get("plugins", {}).get("enabled", []):
        raise PermissionError("cannot_overlay_the_development_Host_plugin")
    target=common_plugin_target(root,home)  # Before snapshot or profile mutation.
    from .bootstrap import verify_native
    verify_native(config)  # Before snapshot, migration, or profile mutation.
    with sqlite3.connect((Path(config['state_root'])/'memory.sqlite').as_uri()+'?mode=ro',uri=True) as db:
        marker = db.execute("SELECT value FROM meta WHERE key='semantic_intake_contract'").fetchone()
        if marker and marker[0] not in {'1','2','3','4','5','6'}:
            raise PermissionError('intake_unknown_state_version_requires_separate_migration')
    from .recovery import optional_stopped
    with stopped(root, home, offline_attested), optional_stopped(root, config):
        if backup_scope=='activation':
            from .activation import backup
            before_path=backup(root,settings,config,target)
        else:before = _snapshot(root, settings, "before-install")
        from bn_memory.store import Store
        from .storage import install as migrate
        from .policy import extend_policy
        config["tool_policy"]["rules"] = extend_policy(config["tool_policy"]["rules"])
        config["semantic_intake_bridge"] = True
        write(settings, config)
        store = Store(config["state_root"], config)
        migration = migrate(store)
        target.mkdir(parents=True, exist_ok=True)
        (target / "__init__.py").write_text(LOADER)
        from .common_policy import standalone_source
        (target/'common_policy.py').write_text(standalone_source())
        (target / "plugin.yaml").write_text(MANIFEST.replace("0.11.0", __version__))
        plugins = app.setdefault("plugins", {})
        plugins["enabled"] = list(dict.fromkeys([*plugins.get("enabled", []), "bn-common-memory"]))
        entry = plugins.setdefault("entries", {}).setdefault("bn-common-memory", {})
        entry.setdefault("settings", {}).update(config_file=str(settings), intake_enabled=True)
        if library is not None:entry['settings']['intake_library']=str(library)
        from hermes_cli.toolset_validation import parse_platform_toolsets_value
        changed_platforms = []
        for platform, raw in app.get('platform_toolsets', {}).items():
            chosen = parse_platform_toolsets_value(raw)
            # Preserve an explicit empty/disabled surface. No saved selection
            # means the core's default composition; don't replace it with one toolset.
            if chosen:
                app['platform_toolsets'][platform] = list(dict.fromkeys([*chosen, 'bnn_intake']))
                changed_platforms.append(platform)
        write_app_config(home/'config.yaml',app)
        certificate = enroll(root, settings)
        if backup_scope=='activation':
            from .activation import seal
            before=seal(root,before_path)
    return {"contract": "BNNIntakeInstallReceiptV1", "version": __version__, "before_install_snapshot": before,
            "migration": migration, "deployment": certificate, "session_default": "off", "live_activation": False,
            "common_plugin_directory":str(target),
            "saved_platform_toolsets_extended": changed_platforms, "explicit_empty_toolsets_preserved": True}


def set_enabled(root, settings, enabled, *, offline_attested):
    import yaml
    if type(enabled) is not bool: raise ValueError("deployment_enabled_boolean_required")
    _, config = config_at(root, settings, inspect_modules=False); home = Path(config["profile_home"])
    with stopped(root, home, offline_attested):
        app = yaml.safe_load((home / "config.yaml").read_text())
        app["plugins"]["entries"]["bn-common-memory"]["settings"]["intake_enabled"] = enabled
        write_app_config(home/'config.yaml',app)
    return {"intake_loader_enabled": enabled, "common_Host_disabled": False, "data_deleted": False}


def _snapshot(root, settings, name):
    root = Path(root).absolute(); _, config = config_at(root, settings, inspect_modules=False)
    dbfile = Path(config["state_root"]) / "memory.sqlite"
    unsettled = {}
    with sqlite3.connect(dbfile.as_uri() + "?mode=ro", uri=True) as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, where in [("model_attempts", "state IN ('reserved','outcome_unknown')"),
                             ("native_tasks", "state IN ('running','launch_reserved','unknown')")]:
            if table in tables:
                unsettled[table] = db.execute("SELECT count(*) FROM " + table + " WHERE " + where).fetchone()[0]
    from .recovery import optional_witness
    optional = optional_witness(root, config)
    protected_config = configuration_witness(settings, config)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = root / "_snapshots" / (stamp + "-" + name)
    destination.mkdir(parents=True, mode=0o700); files = {}
    for base, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [n for n in dirs if n != "__pycache__" and not (Path(base) == root and n in {"_snapshots", "_retained"})]
        for dirname in dirs:
            if (Path(base) / dirname).is_symlink(): raise PermissionError("snapshot_symlink_not_supported")
        for filename in names:
            source = Path(base) / filename
            if filename in {".intake-runtime.lock", ".intake-operator.lock"}: continue
            if filename.endswith(("-wal", "-shm")):
                main = source.with_name(filename[:-4])
                if main.is_file():
                    with main.open("rb") as stream:
                        if stream.read(16) == b"SQLite format 3\x00": continue
            local(root, source); relative = str(source.relative_to(root)); target = destination / "files" / relative
            if not stat.S_ISREG(source.stat().st_mode): raise PermissionError('snapshot_regular_files_only')
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open("rb") as stream: sqlite = stream.read(16) == b"SQLite format 3\x00"
            if sqlite:
                with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src, sqlite3.connect(target) as dst: src.backup(dst)
            else: shutil.copyfile(source, target)
            mode = source.stat().st_mode & 0o777; target.chmod(mode)
            files[relative] = {"sha256": digest(target), "mode": mode, "sqlite_backup": sqlite}
    if optional != optional_witness(root, config): raise PermissionError('snapshot_optional_state_changed_during_copy')
    if protected_config != configuration_witness(settings, config): raise PermissionError('snapshot_configuration_changed_during_copy')
    manifest = {"contract": "BNNIntakeManagedSnapshotV1", "managed_root": str(root), "version": __version__,
        "settings_file": str(Path(settings).relative_to(root)), "files": files,
        "preservation_witness": preservation_witness(dbfile),
        "optional_runtime_witness": optional,
        "protected_configuration": protected_config,
        "preserved_unsettled_execution_counts": unsettled, "resuming_these_is_not_authorized_by_restore": True,
        "exclusions": ["root/_snapshots", "root/_retained", "Python __pycache__", "runtime locks", "SQLite WAL/SHM absorbed into backup"],
        "external_software": {"package_root": config["package_root"], "included": False, "requires_pinned_native_installation": True}}
    write(destination / "manifest.json", manifest)
    return {"path": str(destination), "manifest_sha256": digest(destination / "manifest.json"), "files": len(files),
            "private_profile_snapshot": True, "external_native_programs_included": False}


def snapshot(root, settings, *, offline_attested):
    _, config = config_at(root, settings, inspect_modules=False)
    from .recovery import optional_stopped
    with stopped(root, config["profile_home"], offline_attested), optional_stopped(root, config):
        return _snapshot(root, settings, "checkpoint")


def restore(root, settings, checkpoint, expected_sha256, *, offline_attested):
    checkpoint = local(root, checkpoint); manifest_path = checkpoint / "manifest.json"
    if digest(manifest_path) != expected_sha256: raise PermissionError("snapshot_manifest_digest_mismatch")
    manifest = json.loads(manifest_path.read_text()); _, config = config_at(root, settings, inspect_modules=False)
    if manifest.get('contract')!='BNNIntakeManagedSnapshotV1':raise PermissionError('snapshot_full_restore_contract_required')
    if config.get('identity_runtime'):
        raise PermissionError('restore_identity_requires_qualified_adapter')
    from .bootstrap import verify_native
    verify_native(config)
    if manifest["managed_root"] != str(Path(root).absolute()): raise PermissionError("snapshot_relocation_not_qualified")
    for name, item in manifest["files"].items():
        source = local(checkpoint / "files", checkpoint / "files" / name)
        local(root, Path(root) / name)
        if digest(source) != item["sha256"]: raise PermissionError("snapshot_file_digest_mismatch")
    from .recovery import optional_stopped, optional_witness
    with stopped(root, config["profile_home"], offline_attested), optional_stopped(root, config):
        if manifest.get('protected_configuration') != configuration_witness(settings, config):
            raise PermissionError('restore_would_rewind_operator_configuration_requires_fresh_checkpoint')
        if manifest.get('preservation_witness') != preservation_witness(Path(config['state_root'])/'memory.sqlite'):
            raise PermissionError('restore_would_rewind_usage_effects_or_owner_controls')
        optional = optional_witness(root, config)
        if optional != manifest.get('optional_runtime_witness'):
            raise PermissionError('restore_would_rewind_optional_runtime_or_project')
        # Keep native lock INODES in place, including nested worker/writer locks.
        # Moving a locked directory and recreating its lock would admit a second
        # process against a different inode during restoration.
        locks = {'.intake-operator.lock','.intake-runtime.lock','worker.lock','.writer.lock'}
        moving = []
        for base, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = [n for n in dirs if not (Path(base)==Path(root) and n in {'_snapshots','_retained'})]
            for dirname in dirs: local(root,Path(base)/dirname)
            for name in names:
                path = local(root,Path(base)/name)
                if name in locks: continue
                if not stat.S_ISREG(path.stat().st_mode): raise PermissionError('restore_current_regular_files_only')
                moving.append(path)
        retained = Path(root) / "_retained" / datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        retained.mkdir(parents=True, mode=0o700)
        for path in moving:
            target = retained/path.relative_to(root); target.parent.mkdir(parents=True,exist_ok=True)
            shutil.move(str(path),target)
        for name, item in manifest["files"].items():
            if Path(name).name in locks: continue
            target = Path(root) / name; target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(checkpoint / "files" / name, target); target.chmod(item["mode"])
        if optional != optional_witness(root, config): raise PermissionError('restored_optional_state_differs')
    return {"restored_files": len(manifest["files"]), "previous_state_retained": str(retained), "data_deleted": False,
            "relocation_tested": False, "model_or_external_effect_replayed": False,
            "optional_runtime_preserved":optional is not None,"native_lock_inodes_preserved":True}
