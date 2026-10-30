"""Installation rollback preserves later work and never copies the environment."""
import json
from pathlib import Path
import pytest
from bnn_intake.activation import rollback
from bnn_intake.deployment import install,restore
from test_deployment import deployment


def test_narrow_activation_ignores_unmodified_software_symlinks_and_retains_native_entry(deployment):
    root,settings,config,store,*_=deployment;home=Path(config['profile_home'])
    folder=home/'plugins/bnn-common-memory';folder.mkdir(parents=True)
    (folder/'plugin.yaml').write_text('name: bn-common-memory\nversion: previous\n')
    original=b'# prior native registration\n';(folder/'__init__.py').write_bytes(original)
    (root/'unrelated-runtime-link').symlink_to(root/'project',target_is_directory=True)
    before=settings.read_bytes();receipt=install(root,settings,offline_attested=True,backup_scope='activation')
    backup=receipt['before_install_snapshot'];document=json.loads((Path(backup['path'])/'manifest.json').read_text())
    assert document['contract']=='BNNIntakeActivationBackupV1' and document['sealed']
    assert not any('unrelated' in n for n in document['before'])
    source=store.record('parent',{'kind':'note','scope':'project','request_id':'after-activation',
        'source_ref':'synthetic:post-activation','content':'Keep work recorded after installing the extension.'})
    result=rollback(root,settings,backup['path'],backup['manifest_sha256'],offline_attested=True)
    assert settings.read_bytes()==before and (folder/'__init__.py').read_bytes()==original
    assert result['current_database_and_history_preserved'] and not result['data_deleted']
    assert store.read('parent',source['id'],1)['content'].startswith('Keep work')
    assert not (folder/'common_policy.py').exists()
    assert (Path(result['retained_configuration'])/(folder/'common_policy.py').relative_to(root)).is_file()


def test_activation_rollback_does_not_override_later_operator_configuration(deployment):
    root,settings,config,*_=deployment;r=install(root,settings,offline_attested=True,backup_scope='activation')['before_install_snapshot']
    value=json.loads(settings.read_text());value['context_bytes']=222222;settings.write_text(json.dumps(value));before=settings.read_bytes()
    with pytest.raises(PermissionError,match='configuration_changed'):
        rollback(root,settings,r['path'],r['manifest_sha256'],offline_attested=True)
    assert settings.read_bytes()==before and not (root/'_retained').exists()


def test_activation_rollback_rejects_corrupt_preimage_before_changes(deployment):
    root,settings,config,*_=deployment;r=install(root,settings,offline_attested=True,backup_scope='activation')['before_install_snapshot']
    saved=Path(r['path'])/'files'/settings.relative_to(root);saved.write_text('tampered')
    before=settings.read_bytes()
    with pytest.raises(PermissionError,match='preimage_corrupt'):
        rollback(root,settings,r['path'],r['manifest_sha256'],offline_attested=True)
    assert settings.read_bytes()==before and not (root/'_retained').exists()


def test_activation_backup_cannot_be_used_as_full_state_restore(deployment):
    root,settings,config,*_=deployment;r=install(root,settings,offline_attested=True,backup_scope='activation')['before_install_snapshot']
    with pytest.raises(PermissionError,match='full_restore_contract'):
        restore(root,settings,r['path'],r['manifest_sha256'],offline_attested=True)
    assert not (root/'_retained').exists()


def test_install_cannot_bind_an_unrelated_package_library(deployment):
    root,settings,config,*_=deployment;library=root/'wrong-package';library.mkdir()
    before=settings.read_bytes()
    with pytest.raises(PermissionError,match='library_must_match'):
        install(root,settings,offline_attested=True,backup_scope='activation',library=library)
    assert settings.read_bytes()==before and not (root/'_snapshots').exists()
