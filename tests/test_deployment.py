"""Native primary state, real filesystem locks, additive install and recovery."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import pytest
import yaml
from bnn_intake.bootstrap import verify_deployment
from bnn_intake.deployment import install, local, restore, set_enabled, snapshot
from bnn_intake.engine import Intake
from bn_memory.store import Store
from conftest import BASELINE, prepare, request
from bnn_intake.policy import action_rules


@pytest.fixture
def deployment(fixture, tmp_path):
    store, engine, work = fixture
    home = Path(store.config["profile_home"]); home.mkdir()
    key = tmp_path / "test-signing-key"; key.write_bytes(os.urandom(32)); key.chmod(0o600)
    config = {**store.config, "host_key": str(key), "package_root": BASELINE["memory_release"],
              "tool_policy": {"version": 1, "rules": action_rules()}}
    settings = tmp_path / "settings.json"; settings.write_text(json.dumps(config))
    (home / "config.yaml").write_text(yaml.safe_dump({"execution_controls_required": True, "plugins": {
        "enabled": ["bn-common-memory"], "entries": {"bn-common-memory": {"settings": {"config_file": str(settings)}}}}}))
    return tmp_path, settings, config, store, engine, work


def test_additive_install_preserves_existing_work_and_authenticated_binding(deployment, contract):
    root, settings, config, store, engine, work = deployment
    prepare(engine, work, contract); before = engine.status("parent", work)
    receipt = install(root, settings, offline_attested=True)
    assert receipt["migration"]["changed"] is False
    assert engine.status("parent", work) == before
    assert receipt["before_install_snapshot"]["files"] > 0
    current = json.loads(settings.read_text())
    verify_deployment(current["profile_home"], settings, current)
    home = Path(current["profile_home"])
    loader = (home / "plugins/bn-common-memory/__init__.py").read_text()
    assert 'from .common_policy import register as loaded' in loader
    shared=(home/'plugins/bn-common-memory/common_policy.py').read_text()
    assert 'BN_SYSTEM_ACCEPTANCE_POLICY' in shared and 'BNN' in shared and 'GSD' in shared
    assert 'import bnn_intake' not in shared
    assert 'from bnn_intake.bootstrap import register as loaded' in loader


def test_install_rejects_incompatible_supplier_before_any_mutation(deployment):
    root, settings, config, *_ = deployment
    config["package_root"] = str(root / "wrong-native")
    settings.write_text(json.dumps(config)); before = settings.read_bytes()
    with pytest.raises(PermissionError, match="native_package_binding"):
        install(root, settings, offline_attested=True)
    assert settings.read_bytes() == before and not (root / "_snapshots").exists()


def test_install_reuses_existing_native_plugin_directory_without_second_Host(deployment):
    root,settings,config,*_=deployment;home=Path(config['profile_home'])
    plugin=home/'plugins/bnn-common-memory';plugin.mkdir(parents=True)
    (plugin/'plugin.yaml').write_text('name: bn-common-memory\nversion: native\n')
    (plugin/'__init__.py').write_text('# Existing native wrapper retained in preinstall snapshot\n')
    receipt=install(root,settings,offline_attested=True)
    assert receipt['common_plugin_directory']==str(plugin)
    assert not (home/'plugins/bn-common-memory').exists()
    assert 'common_policy' in (plugin/'__init__.py').read_text()
    assert (plugin/'common_policy.py').exists()
    before=Path(receipt['before_install_snapshot']['path'])/'files'/plugin.relative_to(root)/'__init__.py'
    assert before.read_text()=='# Existing native wrapper retained in preinstall snapshot\n'


def test_duplicate_native_plugin_names_refuse_before_install_mutation(deployment):
    root,settings,config,*_=deployment;home=Path(config['profile_home'])
    for name in ['bnn-common-memory','bn-common-memory']:
        folder=home/'plugins'/name;folder.mkdir(parents=True);(folder/'plugin.yaml').write_text('name: bn-common-memory\n')
    before=settings.read_bytes()
    with pytest.raises(PermissionError,match='duplicate_common_plugin'):
        install(root,settings,offline_attested=True)
    assert settings.read_bytes()==before and not (root/'_snapshots').exists()


def test_occupied_unidentified_common_directory_is_not_overwritten(deployment):
    root,settings,config,*_=deployment;home=Path(config['profile_home'])
    folder=home/'plugins/bn-common-memory';folder.mkdir(parents=True)
    wrapper=folder/'__init__.py';wrapper.write_text('other_plugin=True\n')
    with pytest.raises(PermissionError,match='unidentified_common_plugin_target'):
        install(root,settings,offline_attested=True)
    assert wrapper.read_text()=='other_plugin=True\n' and not (root/'_snapshots').exists()


def test_running_profile_lock_blocks_operator_configuration_change(deployment):
    root, settings, config, *_ = deployment; home = Path(config["profile_home"])
    code = "import fcntl,sys\nf=open(sys.argv[1],'a');fcntl.flock(f,fcntl.LOCK_SH);print('READY',flush=True);sys.stdin.read()"
    process = subprocess.Popen([sys.executable, "-B", "-c", code, str(home / ".intake-runtime.lock")], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "READY"
        before = (home / "config.yaml").read_bytes()
        with pytest.raises(PermissionError, match="still_running"):
            set_enabled(root, settings, False, offline_attested=True)
        assert (home / "config.yaml").read_bytes() == before
    finally:
        process.stdin.close(); process.wait(timeout=10)


def test_snapshot_restore_recovers_revision_and_retains_later_user_files(deployment, contract):
    root, settings, config, store, engine, work = deployment
    prepare(engine, work, contract); before = engine.status("parent", work)
    checkpoint = snapshot(root, settings, offline_attested=True)
    engine.call("parent", request("pause", work, 1, "pause-after-snapshot", reason="later state"))
    note = Path(config["projects"]["p"]) / "later-user-note.md"; note.write_text("Must be retained, never deleted")
    receipt = restore(root, settings, checkpoint["path"], checkpoint["manifest_sha256"], offline_attested=True)
    fresh = Intake(Store(config["state_root"], config))
    assert fresh.status("parent", work) == before
    assert (Path(receipt["previous_state_retained"]) / "project/later-user-note.md").read_text() == "Must be retained, never deleted"
    assert not receipt["data_deleted"] and not receipt["model_or_external_effect_replayed"]


def test_corrupt_snapshot_is_rejected_before_live_files_are_replaced(deployment, contract):
    root, settings, config, store, engine, work = deployment
    prepare(engine, work, contract); before = engine.status("parent", work)
    checkpoint = snapshot(root, settings, offline_attested=True)
    copied = Path(checkpoint["path"]) / "files/settings.json"; copied.write_text("tampered")
    with pytest.raises(PermissionError, match="snapshot_file_digest"):
        restore(root, settings, checkpoint["path"], checkpoint["manifest_sha256"], offline_attested=True)
    assert engine.status("parent", work) == before and not (root / "_retained").exists()


def test_operator_paths_reject_traversal_symlink_and_missing_stop_attestation(deployment):
    root, settings, config, *_ = deployment
    with pytest.raises(PermissionError): local(root, root / ".." / "another-profile")
    link = root / "alias"; link.symlink_to(Path(config["state_root"]))
    with pytest.raises(PermissionError, match="symlink"): local(root, link / "memory.sqlite")
    with pytest.raises(PermissionError, match="stopped_profile_attestation"):
        snapshot(root, settings, offline_attested=False)


def test_native_version_hash_mismatch_closes_install_before_snapshot(deployment, monkeypatch):
    root, settings, config, *_ = deployment
    from bnn_intake import bootstrap
    expected = bootstrap.compatibility()
    expected['memory_files']['manifest.json'] = '0' * 64
    monkeypatch.setattr(bootstrap, 'compatibility', lambda: expected)
    with pytest.raises(PermissionError, match='native_memory_version_not_qualified'):
        install(root, settings, offline_attested=True)
    assert not (root / '_snapshots').exists()


def test_changed_settings_are_rejected_before_native_Host_construction(deployment, monkeypatch):
    root, settings, config, *_ = deployment
    from bnn_intake import bootstrap
    from bn_memory import plugin as common
    from types import SimpleNamespace
    install(root, settings, offline_attested=True)
    changed = json.loads(settings.read_text()); changed['context_bytes'] = 12345
    settings.write_text(json.dumps(changed))
    monkeypatch.setenv('HERMES_HOME', config['profile_home'])
    opened = []
    def must_not_open(*a, **kw):opened.append(True);raise AssertionError('Host opened before deployment verification')
    monkeypatch.setattr(common,'Host',must_not_open)
    with pytest.raises(PermissionError, match='settings_changed_requires_operator'):
        bootstrap.register(SimpleNamespace(get_config=lambda name:str(settings)))
    assert not opened


def test_snapshot_cannot_rewind_a_later_owner_off(deployment, contract):
    root, settings, config, store, engine, work = deployment
    prepare(engine, work, contract)
    checkpoint = snapshot(root, settings, offline_attested=True)
    mode = engine.mode('parent'); engine.set_mode('parent','off',mode['revision'],'fixture-owner')
    with pytest.raises(PermissionError, match='rewind_usage_effects_or_owner_controls'):
        restore(root, settings, checkpoint['path'], checkpoint['manifest_sha256'], offline_attested=True)
    assert engine.mode('parent')['mode']=='off' and not (root/'_retained').exists()


def test_snapshot_cannot_rewind_later_accounting_or_effect_evidence(deployment):
    root, settings, config, store, engine, work = deployment
    from types import SimpleNamespace
    from bn_memory.model_accounting import Accounting
    from bn_memory.working import Working
    accounting = Accounting(SimpleNamespace(store=store,working=Working(store)))
    checkpoint = snapshot(root, settings, offline_attested=True)
    # Actual native accounting API with an explicitly synthetic response;
    # no provider call or real spend is claimed by this fixture.
    ref = accounting.reserve('parent',{'model':'fixture-no-provider'},{'api_request_id':'synthetic-budget-fixture','provider':'fixture'})
    accounting.finish(ref,{'usage':{'total_tokens':7}})
    with pytest.raises(PermissionError, match='rewind_usage_effects_or_owner_controls'):
        restore(root, settings, checkpoint['path'], checkpoint['manifest_sha256'], offline_attested=True)
    assert not (root/'_retained').exists()
    with store.db() as db:assert db.execute('SELECT usage_tokens FROM model_attempts WHERE id=?',(ref,)).fetchone()[0]==7


def test_preservation_witness_includes_binary_effect_evidence(tmp_path):
    from bnn_intake.deployment import preservation_witness
    import sqlite3
    path=tmp_path/'review-fixture.sqlite'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE action_reviews(id TEXT,evidence BLOB)')
        db.execute('INSERT INTO action_reviews VALUES(?,?)',('fixture',b'first binary proof'))
    before=preservation_witness(path)
    with sqlite3.connect(path) as db:db.execute('UPDATE action_reviews SET evidence=?',(b'different binary proof',))
    assert preservation_witness(path)['action_reviews']!=before['action_reviews']


def test_unknown_schema_refuses_install_without_mutating_settings(deployment):
    root, settings, config, store, *_ = deployment
    with store.db() as db:db.execute("UPDATE meta SET value='999' WHERE key='semantic_intake_contract'")
    before=settings.read_bytes()
    with pytest.raises(PermissionError, match='unknown_state_version'):
        install(root, settings, offline_attested=True)
    assert settings.read_bytes()==before and not (root/'_snapshots').exists()


def test_install_extends_saved_tools_without_replacing_existing_or_explicit_empty(deployment):
    root, settings, config, *_ = deployment
    path=Path(config['profile_home'])/'config.yaml';app=yaml.safe_load(path.read_text())
    app['platform_toolsets']={'cli':['context','skills'],'telegram':"['context']",'disabled':[],'default':None}
    path.write_text(yaml.safe_dump(app))
    result=install(root,settings,offline_attested=True)
    updated=yaml.safe_load(path.read_text())['platform_toolsets']
    assert updated['cli']==['context','skills','bnn_intake'] and updated['telegram']==['context','bnn_intake']
    assert updated['disabled']==[] and updated['default'] is None
    assert result['explicit_empty_toolsets_preserved']


def test_install_and_toggle_preserve_existing_JSON_encoded_application_config(deployment):
    root,settings,config,*_=deployment;path=Path(config['profile_home'])/'config.yaml'
    path.write_text(json.dumps(yaml.safe_load(path.read_text())))
    install(root,settings,offline_attested=True,backup_scope='activation')
    assert json.loads(path.read_text())['plugins']['entries']['bn-common-memory']['settings']['intake_enabled']
    set_enabled(root,settings,False,offline_attested=True)
    assert json.loads(path.read_text())['plugins']['entries']['bn-common-memory']['settings']['intake_enabled'] is False


def test_unavailable_optional_settings_do_not_break_base_deployment_or_off(deployment):
    root, settings, config, store, *_ = deployment
    from types import SimpleNamespace
    from bn_memory.modules import Modules
    path=root/'gsd-settings.json'
    fields=['runtime_root','workspace','state_dir','operator_dir','anchor_file','store_key','native_key','grant_key']
    path.write_text(json.dumps({name:str(root/'gsd'/name) for name in fields}))
    config['modules']={'gsd':{'settings_file':str(path),'files':{str(path):hashlib.sha256(path.read_bytes()).hexdigest()}}}
    settings.write_text(json.dumps(config)); install(root,settings,offline_attested=True)
    path.rename(root/'gsd-settings-retained.json')
    current=json.loads(settings.read_text())
    verify_deployment(current['profile_home'],settings,current)
    assert set_enabled(root,settings,False,offline_attested=True)['intake_loader_enabled'] is False
    # Actual native conditional integrity check still closes the dependent GSD
    # path; the base startup does not eagerly read this unavailable module.
    modules=Modules(SimpleNamespace(store=store,config=current))
    with pytest.raises(PermissionError,match='optional_module_integrity_failed'):
        modules._verify_enable('gsd')
