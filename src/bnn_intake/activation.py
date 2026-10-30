"""Small installation preimages and configuration-only rollback; no data rewind.

Operator-only companion to deployment.install. It never snapshots executables,
follows environment symlinks, deletes files, or restores a former SQLite state.
"""
import datetime
import json
from pathlib import Path
import shutil
import sqlite3
import stat


CONTRACT='BNNIntakeActivationBackupV1'


def stamp():return datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')


def fingerprint(path):
    from .deployment import digest
    if not path.exists():return {'exists':False}
    if not stat.S_ISREG(path.stat().st_mode):raise PermissionError('activation_regular_file_required')
    return {'exists':True,'sha256':digest(path),'mode':stat.S_IMODE(path.stat().st_mode)}


def backup(root,settings,config,target):
    from .deployment import local,write,digest
    from .recovery import sqlite_root
    root=Path(root).absolute();home=Path(config['profile_home'])
    paths=[Path(settings),home/'config.yaml',home/'intake-deployment.json',
           target/'__init__.py',target/'plugin.yaml',target/'common_policy.py']
    before={str(local(root,p).relative_to(root)):fingerprint(p) for p in paths}
    database=local(root,Path(config['state_root'])/'memory.sqlite');logical=sqlite_root(database)
    destination=root/'_snapshots'/(stamp()+'-activation');destination.mkdir(parents=True,mode=0o700)
    for name,value in before.items():
        if not value['exists']:continue
        saved=destination/'files'/name;saved.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        shutil.copyfile(root/name,saved);saved.chmod(value['mode'])
        if fingerprint(saved)!=value:raise PermissionError('activation_preimage_changed')
    saved=destination/'data/memory.sqlite';saved.parent.mkdir(mode=0o700)
    with sqlite3.connect(database.as_uri()+'?mode=ro',uri=True) as source,sqlite3.connect(saved) as target_db:source.backup(target_db)
    saved.chmod(0o600)
    if sqlite_root(saved)!=logical or sqlite_root(database)!=logical or any(fingerprint(root/n)!=v for n,v in before.items()):
        raise PermissionError('activation_state_changed_during_backup')
    document={'contract':CONTRACT,'managed_root':str(root),'settings_file':str(Path(settings).relative_to(root)),
        'profile_home':str(home.relative_to(root)),'before':before,'sealed':False,
        'database_backup':{'path':'data/memory.sqlite','sha256':digest(saved),'logical_sha256':logical},
        'rollback_scope':'configuration_only_preserve_current_database_and_effect_history',
        'native_software_included':False,'credentials_or_identity_copied':False}
    write(destination/'manifest.json',document)
    return destination


def seal(root,destination):
    from .deployment import local,write,digest
    path=local(root,Path(destination)/'manifest.json');value=json.loads(path.read_text())
    if value['contract']!=CONTRACT or value['managed_root']!=str(Path(root).absolute()) or value['sealed']:
        raise PermissionError('activation_backup_binding_invalid')
    value['after']={name:fingerprint(local(root,Path(root)/name)) for name in value['before']}
    value['sealed']=True;write(path,value)
    return {'path':str(destination),'manifest_sha256':digest(path),'files':sum(v['exists'] for v in value['before'].values())+1,
        'private_profile_snapshot':True,'installation_only':True,'whole_environment_backup':False,
        'rollback_rewinds_database':False}


def rollback(root,settings,checkpoint,expected_sha256,*,offline_attested):
    from .deployment import local,config_at,digest,stopped
    from .bootstrap import verify_native
    from .recovery import optional_stopped
    root=Path(root).absolute();checkpoint=local(root,checkpoint);path=checkpoint/'manifest.json'
    if digest(path)!=expected_sha256:raise PermissionError('activation_manifest_digest_mismatch')
    value=json.loads(path.read_text());settings,config=config_at(root,settings,inspect_modules=False)
    if (value.get('contract')!=CONTRACT or not value.get('sealed') or value['managed_root']!=str(root)
            or value['settings_file']!=str(settings.relative_to(root)) or value['profile_home']!=str(Path(config['profile_home']).relative_to(root))):
        raise PermissionError('activation_rollback_binding_invalid')
    verify_native(config)
    with stopped(root,config['profile_home'],offline_attested),optional_stopped(root,config):
        if set(value['before'])!=set(value['after']):raise PermissionError('activation_manifest_file_set_changed')
        for name,item in value['before'].items():
            current=local(root,root/name)
            if fingerprint(current)!=value['after'][name]:raise PermissionError('activation_configuration_changed_requires_review')
            if item['exists'] and fingerprint(local(checkpoint,checkpoint/'files'/name))!=item:
                raise PermissionError('activation_backup_preimage_corrupt')
        dbcopy=local(checkpoint,checkpoint/value['database_backup']['path'])
        if digest(dbcopy)!=value['database_backup']['sha256']:raise PermissionError('activation_database_backup_corrupt')
        # Retain the currently installed files; no delete and no database restore.
        retained=root/'_retained'/('activation-'+stamp());retained.mkdir(parents=True,mode=0o700)
        for name,item in value['before'].items():
            current=local(root,root/name)
            if current.exists():
                keep=retained/name;keep.parent.mkdir(parents=True,exist_ok=True,mode=0o700);current.rename(keep)
            if item['exists']:
                current.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(checkpoint/'files'/name,current);current.chmod(item['mode'])
        return {'configuration_rolled_back':True,'current_database_and_history_preserved':True,
            'retained_configuration':str(retained),'data_deleted':False,'executors_started':False}
