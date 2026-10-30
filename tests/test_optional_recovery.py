"""Real Foundation SQLite and native locks; no model/provider invocation."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import pytest
from conftest import BASELINE, ROOT, prepare, request
from test_deployment import deployment
from bnn_intake.deployment import restore, snapshot
from bnn_intake.recovery import optional_witness, sqlite_root


@pytest.fixture
def research_recovery(deployment):
    root, settings, config, *_ = deployment
    baseline = json.loads((Path(BASELINE['hermes_core']).parent/'Memory/settings.json').read_text())['modules']['research']
    directory = root/'foundation'; directory.mkdir()
    module = {k:baseline[k] for k in ['source_paths','dolt_executable','files']}
    module['state_root'] = str(directory); config['modules'] = {'research':module}
    settings.write_text(json.dumps(config))
    sys.path[:0] = module['source_paths']
    from hermes_foundation_bridge.runtime import RuntimeCoordinator
    runtime = RuntimeCoordinator(directory); assert runtime.migration_receipt(apply=True)['status']=='applied'
    return (*deployment, runtime)


def test_optional_snapshot_restores_work_without_rewinding_foundation(research_recovery, contract):
    root, settings, config, store, engine, work, runtime = research_recovery
    prepare(engine,work,contract); original = engine.status('parent',work)
    before = optional_witness(root,config)
    checkpoint = snapshot(root,settings,offline_attested=True)
    engine.call('parent',request('pause',work,1,'later-pause',reason='later work state'))
    receipt = restore(root,settings,checkpoint['path'],checkpoint['manifest_sha256'],offline_attested=True)
    assert receipt['optional_runtime_preserved'] and before==optional_witness(root,config)
    assert engine.status('parent',work)==original


def test_sqlite_layout_is_not_confused_with_changed_history(research_recovery):
    *_,runtime = research_recovery
    import sqlite3
    before = sqlite_root(runtime.database)
    with sqlite3.connect(runtime.database) as db:db.execute('VACUUM')
    assert before==sqlite_root(runtime.database)


def test_changed_foundation_unknown_outcome_rejects_old_restore(research_recovery):
    root, settings, config, *_,runtime = research_recovery
    checkpoint = snapshot(root,settings,offline_attested=True)
    # Exact native journal schema, fixture outcome only; never a provider result.
    with runtime._connect() as db:
        db.execute("INSERT INTO operations VALUES('fixture-op','fixture-id','fixture-work',1,'unknown',NULL,'now','now')")
    with pytest.raises(PermissionError,match='rewind_optional_runtime'):
        restore(root,settings,checkpoint['path'],checkpoint['manifest_sha256'],offline_attested=True)
    assert not (root/'_retained').exists()
    fresh = snapshot(root,settings,offline_attested=True)
    restore(root,settings,fresh['path'],fresh['manifest_sha256'],offline_attested=True)
    with runtime._connect() as db:assert db.execute('SELECT status FROM operations').fetchone()[0]=='unknown'


def test_active_foundation_lease_closes_snapshot(research_recovery):
    root, settings, config, *_,runtime = research_recovery
    runtime.acquire_lease({'schema_version':1,'work_id':'fixture-work','holder_id':'fixture-holder','expected_revision':1,'ttl_seconds':60})
    with pytest.raises(PermissionError,match='active_lease_requires_pause'):
        snapshot(root,settings,offline_attested=True)
    assert not (root/'_snapshots').exists()


def test_project_beads_or_source_change_cannot_be_rewound(research_recovery):
    root, settings, config, *_ = research_recovery
    checkpoint = snapshot(root,settings,offline_attested=True)
    (Path(config['projects']['p'])/'later-source.md').write_text('A new source cannot disappear by restoring intake')
    with pytest.raises(PermissionError,match='rewind_optional_runtime_or_project'):
        restore(root,settings,checkpoint['path'],checkpoint['manifest_sha256'],offline_attested=True)
    assert not (root/'_retained').exists()


def test_writer_lock_is_the_same_inode_after_restore(research_recovery):
    root, settings, config, *_ = research_recovery
    checkpoint = snapshot(root,settings,offline_attested=True)
    writer = root/'foundation/.writer.lock'; profile = Path(config['profile_home'])/'.intake-runtime.lock'
    before = [p.stat().st_ino for p in [writer,profile]]
    restore(root,settings,checkpoint['path'],checkpoint['manifest_sha256'],offline_attested=True)
    assert before==[p.stat().st_ino for p in [writer,profile]]


def test_busy_native_writer_closes_snapshot_before_copy(research_recovery):
    root, settings, config, *_ = research_recovery
    lock = root/'foundation/.writer.lock'
    script="import fcntl,sys\nf=open(sys.argv[1],'a');fcntl.flock(f,fcntl.LOCK_EX);print('READY',flush=True);sys.stdin.read()"
    child = subprocess.Popen([sys.executable,'-B','-c',script,str(lock)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    try:
        assert child.stdout.readline().strip()=='READY'
        with pytest.raises(PermissionError,match='optional_worker_still_running'):
            snapshot(root,settings,offline_attested=True)
        assert not (root/'_snapshots').exists()
    finally:child.stdin.close();child.wait(timeout=10)


def test_changed_optional_supplier_closes_snapshot_before_copy(research_recovery):
    root, settings, config, *_ = research_recovery
    local_source=root/'fixture-adapter.py';local_source.write_text('fixture')
    config['modules']['research']['files']={str(local_source):'0'*64};settings.write_text(json.dumps(config))
    with pytest.raises(PermissionError,match='optional_source_integrity_failed'):
        snapshot(root,settings,offline_attested=True)
    assert not (root/'_snapshots').exists()


def test_later_source_policy_cannot_be_rolled_back(deployment):
    root, settings, config, store, *_ = deployment
    from bn_memory.source_visibility import Visibility
    checkpoint = snapshot(root,settings,offline_attested=True)
    Visibility(store).revise('a'*64,0,{'allowed_roles':['parent'],'evidence_use':'general'},'New owner restriction')
    with pytest.raises(PermissionError,match='rewind_usage_effects_or_owner_controls'):
        restore(root,settings,checkpoint['path'],checkpoint['manifest_sha256'],offline_attested=True)
    assert not (root/'_retained').exists()


def test_operator_loader_off_cannot_be_rolled_back(deployment):
    from bnn_intake.deployment import install, set_enabled
    root, settings, config, *_ = deployment
    install(root,settings,offline_attested=True)
    checkpoint=snapshot(root,settings,offline_attested=True)
    set_enabled(root,settings,False,offline_attested=True)
    with pytest.raises(PermissionError,match='rewind_operator_configuration'):
        restore(root,settings,checkpoint['path'],checkpoint['manifest_sha256'],offline_attested=True)
    assert not (root/'_retained').exists()


def test_current_symlink_refuses_before_any_files_are_moved(deployment):
    root, settings, config, *_ = deployment
    checkpoint=snapshot(root,settings,offline_attested=True)
    (root/'later-alias').symlink_to(root/'settings.json')
    before=settings.read_bytes()
    with pytest.raises(PermissionError,match='symlink'):
        restore(root,settings,checkpoint['path'],checkpoint['manifest_sha256'],offline_attested=True)
    assert settings.read_bytes()==before and not (root/'_retained').exists()


@pytest.fixture
def gsd_recovery(deployment):
    root, settings, config, *_ = deployment
    sys.path.insert(0,str(ROOT/'operator'))
    from isolated_modules import gsd
    module=gsd(root/'gsd',Path(config['projects']['p']))
    config['modules']={'gsd':module};settings.write_text(json.dumps(config))
    sys.path.insert(0,module['host_package']);from bn_gsd.bridge import Bridge
    bridge=Bridge(module['settings_file']);bridge.call('init')
    return (*deployment,bridge)


def test_gsd_pending_job_cannot_enter_a_recoverable_snapshot(gsd_recovery):
    root, settings, config, *_,bridge = gsd_recovery
    bridge.call('session',{'sid':'fixture-parent','platform':'cli'})
    bridge.call('job_create',{'sid':'fixture-parent','role':'G','operation':'fixture-read-only',
        'inputs':{'question':'Synthetic pending-job check; do not execute'},'request_id':'pending-fixture'})
    with pytest.raises(PermissionError,match='GSD_jobs_not_quiescent'):
        snapshot(root,settings,offline_attested=True)
    assert not (root/'_snapshots').exists()


def test_gsd_unsettled_anchor_intent_is_not_silently_repaired(gsd_recovery):
    root, settings, config, *_,bridge = gsd_recovery
    intent=Path(bridge.settings['anchor_file']).with_name('anchor-intent.json');intent.write_text('{}')
    with pytest.raises(PermissionError,match='GSD_unsettled_anchor_intent'):
        snapshot(root,settings,offline_attested=True)
    assert intent.read_text()=='{}' and not (root/'_snapshots').exists()


def test_tampered_gsd_anchor_refuses_before_any_replacement(gsd_recovery):
    root, settings, config, *_,bridge = gsd_recovery
    checkpoint=snapshot(root,settings,offline_attested=True)
    anchor=Path(bridge.settings['anchor_file']);original=anchor.read_bytes();value=json.loads(original)
    value['body']['recorded_at']+=1;anchor.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='anchor authentication failed'):
        restore(root,settings,checkpoint['path'],checkpoint['manifest_sha256'],offline_attested=True)
    assert not (root/'_retained').exists() and anchor.read_bytes()!=original


def test_gsd_worker_native_lock_blocks_snapshot(gsd_recovery):
    root, settings, config, *_,bridge = gsd_recovery
    lock=Path(bridge.settings['operator_dir'])/'worker.lock'
    script="import fcntl,sys\nf=open(sys.argv[1],'a');fcntl.flock(f,fcntl.LOCK_EX);print('READY',flush=True);sys.stdin.read()"
    child=subprocess.Popen([sys.executable,'-B','-c',script,str(lock)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    try:
        assert child.stdout.readline().strip()=='READY'
        with pytest.raises(PermissionError,match='optional_worker_still_running'):
            snapshot(root,settings,offline_attested=True)
        assert not (root/'_snapshots').exists()
    finally:child.stdin.close();child.wait(timeout=10)


def test_gsd_trust_key_must_retain_owner_only_permissions(gsd_recovery):
    root, settings, config, *_,bridge = gsd_recovery
    Path(bridge.settings['native_key']).chmod(0o644)
    with pytest.raises(PermissionError,match='key_permissions_or_size_invalid'):
        snapshot(root,settings,offline_attested=True)
    assert not (root/'_snapshots').exists()


def test_nested_supplier_source_is_checked_before_import(tmp_path):
    from bnn_intake.recovery import verify_gsd_bundle
    bundle=tmp_path/'supplier';(bundle/'engine').mkdir(parents=True)
    source=bundle/'store.py';source.write_text('original fixture; never imported')
    manifest=bundle/'manifest.json';manifest.write_text(json.dumps({'files':[{'path':'store.py','sha256':hashlib.sha256(source.read_bytes()).hexdigest()}]}))
    engine=bundle/'engine/manifest.json';engine.write_text(json.dumps({'files':[]}))
    settings={'bundle_root':str(bundle),'integration_manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),
              'engine_manifest_sha256':hashlib.sha256(engine.read_bytes()).hexdigest()}
    verify_gsd_bundle(settings)
    source.write_text('modified reader; must not be imported')
    with pytest.raises(PermissionError,match='supplier_source_changed'):verify_gsd_bundle(settings)
