"""Offline, pinned adapters for optional runtime preservation. No executor is started.

This is same-root recovery, not permission to rewind a module or replay a job.
The GSD read adapter uses the pinned supplier Store/anchor representation without
constructing Runtime (whose constructor recovers dead owners).
"""
import contextlib
import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3
import stat
import sys
from .storage import canonical


def connect(path):
    return sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True)


def sqlite_root(path):
    """Logical digest survives SQLite backup; all schema and table rows included."""
    sha = hashlib.sha256()
    with connect(path) as db:
        db.execute('BEGIN')
        schema = db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall()
        sha.update(canonical(schema).encode())
        for kind, name, *_ in schema:
            if kind != 'table': continue
            quoted = '"' + name.replace('"', '""') + '"'
            columns = len(db.execute('SELECT * FROM ' + quoted + ' LIMIT 0').description)
            order = ','.join(str(i + 1) for i in range(columns))
            for row in db.execute('SELECT * FROM ' + quoted + ' ORDER BY ' + order):
                value = [{'bytes':len(v),'sha256':hashlib.sha256(v).hexdigest()} if isinstance(v,bytes) else v for v in row]
                sha.update(canonical([name, value]).encode() + b'\n')
    return sha.hexdigest()


def file_root(path):
    if not stat.S_ISREG(path.stat().st_mode): raise PermissionError('recovery_regular_files_only')
    with path.open('rb') as stream:
        if stream.read(16) == b'SQLite format 3\x00': return 'sqlite:' + sqlite_root(path)
        stream.seek(0); return 'bytes:' + hashlib.file_digest(stream, 'sha256').hexdigest()


def inventory(root, paths):
    from .deployment import local
    found = {}
    for path in paths:
        path = local(root, path)
        if not path.exists():
            found[str(path.relative_to(root))] = {'absent':True}; continue
        for item in [path, *sorted(path.rglob('*'))] if path.is_dir() else [path]:
            local(root, item)
            if item.is_dir():
                found[str(item.relative_to(root))] = {'directory_mode':stat.S_IMODE(item.stat().st_mode)}
                continue
            if item.name in {'worker.lock','.writer.lock'}: continue  # Same inode held throughout recovery.
            if item.name.endswith(('-wal','-shm')):
                main = item.with_name(item.name[:-4])
                if main.is_file():
                    with main.open('rb') as stream:
                        if stream.read(16) == b'SQLite format 3\x00': continue
            found[str(item.relative_to(root))] = {'root':file_root(item),'mode':stat.S_IMODE(item.stat().st_mode)}
    return found


def verify_gsd_bundle(settings):
    """Check nested supplier sources BEFORE importing its signed Store reader."""
    root=Path(settings['bundle_root'])
    for base, name, key in [(root,'manifest.json','integration_manifest_sha256'),
                            (root/'engine','manifest.json','engine_manifest_sha256')]:
        manifest=base/name
        if file_root(manifest)!='bytes:'+settings[key]:raise PermissionError('recovery_GSD_supplier_manifest_changed')
        for entry in json.loads(manifest.read_text())['files']:
            relative=Path(entry['path']);path=base/relative
            if relative.is_absolute() or '..' in relative.parts or any(p.is_symlink() for p in [path,*path.parents]):
                raise PermissionError('recovery_GSD_supplier_path_invalid')
            if file_root(path)!='bytes:'+entry['sha256']:
                raise PermissionError('recovery_GSD_supplier_source_changed')


def gsd_state(config):
    """Exact pinned, read-only adapter; no Runtime/Store constructor or repair."""
    settings = json.loads(Path(config['settings_file']).read_text())
    dbfile = Path(settings['state_dir']) / 'state.sqlite3'
    anchor = Path(settings['anchor_file'])
    if not dbfile.exists() and not anchor.exists(): return {'initialized':False}
    if anchor.with_name('anchor-intent.json').exists(): raise PermissionError('recovery_GSD_unsettled_anchor_intent')
    verify_gsd_bundle(settings)
    for name in ['store_key','native_key','grant_key']:
        key=Path(settings[name])
        if key.stat().st_mode & 0o077 or len(key.read_bytes())!=32:
            raise PermissionError('recovery_GSD_key_permissions_or_size_invalid')
    sys.path[:0] = [config['host_package'], settings['bundle_root']]
    from bn_gsd.anchor_store import _read, _root
    from gsd_bridge.store import Store
    seal = _read(settings, anchor)  # Signature and exact engine binding; no write.
    if seal.get('version') != 2 or seal.get('logical_sha256') != _root(settings):
        raise PermissionError('recovery_GSD_anchor_mismatch')
    store = Store.__new__(Store); store.key = Path(settings['store_key']).read_bytes()
    with connect(dbfile) as db:
        for identifier, *_ in db.execute('SELECT id FROM records'): store.get(db, identifier)
        for scope, request, fp in db.execute('SELECT scope,request_id,fingerprint FROM requests'): store.prior(db,scope,request,fp)
        try: _, ledger = store.get(db, 'BN.HOST.RUNTIME')
        except ValueError as exc:
            if getattr(exc,'code',None) != 'NOT_FOUND': raise
            ledger = {'families':{},'jobs':{},'requests':{}}
    if any(j['status'] in {'pending','running','cancelling'} for j in ledger['jobs'].values()):
        raise PermissionError('recovery_GSD_jobs_not_quiescent')
    return {'initialized':True,'families':ledger['families'],
            'job_ids':sorted(ledger['jobs']),
            'unsettled_requests':sorted(k for k,v in ledger['requests'].items() if v['state'] in {'reserved','outcome_unknown'})}


@contextlib.contextmanager
def optional_stopped(root, config):
    """Additional native worker/writer locks, inside the outer stopped-profile lock."""
    from .deployment import local
    from .isolation import validate_modules
    validate_modules(config, root)
    with contextlib.ExitStack() as stack:
        paths = []
        for name, module in config.get('modules',{}).items():
            # Native Module.require's conditional integrity check, without a Host.
            for filename, expected in module['files'].items():
                path = Path(filename)
                if path.is_symlink() or not path.is_file() or file_root(path) != 'bytes:' + expected:
                    raise PermissionError('recovery_optional_source_integrity_failed:' + name)
            if name == 'gsd':
                settings = json.loads(Path(module['settings_file']).read_text())
                paths.append(local(root, Path(settings['operator_dir'])/'worker.lock'))
            elif name == 'research':
                paths.append(local(root, Path(module['state_root'])/'.writer.lock'))
        try:
            for path in paths:
                if not path.parent.exists(): continue  # Configured but never initialized; no worker owns a lock here.
                if not path.parent.is_dir(): raise PermissionError('recovery_module_state_unprepared')
                stream = stack.enter_context(path.open('a+b'))
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PermissionError('recovery_optional_worker_still_running') from exc
        yield


def optional_witness(root, config):
    modules = config.get('modules',{})
    if not modules: return None
    paths = [Path(p) for p in config.get('projects',{}).values()]
    states = {}
    with connect(Path(config['state_root'])/'memory.sqlite') as db:
        tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, predicate in [('native_tasks',"state IN ('running','launch_reserved','cancelling')"),
                                 ('research_bindings',"state IN ('preparing','active','finishing')")]:
            if table in tables and db.execute('SELECT 1 FROM '+table+' WHERE '+predicate+' LIMIT 1').fetchone():
                raise PermissionError('recovery_module_not_quiescent:' + table)
    for name, module in modules.items():
        if name == 'gsd':
            value = json.loads(Path(module['settings_file']).read_text())
            paths += [Path(module['settings_file'])] + [Path(value[k]) for k in
                ['runtime_root','workspace','state_dir','operator_dir','anchor_file','store_key','native_key','grant_key']]
            states['gsd'] = gsd_state(module)
        elif name == 'research':
            directory = Path(module['state_root']); paths.append(directory)
            dbfile = directory/'runtime/runtime.sqlite3'
            if dbfile.exists():
                with connect(dbfile) as db:
                    if db.execute("SELECT 1 FROM leases WHERE status='active' LIMIT 1").fetchone():
                        raise PermissionError('recovery_Foundation_active_lease_requires_pause')
                    if db.execute("SELECT 1 FROM operations WHERE status='started' LIMIT 1").fetchone():
                        raise PermissionError('recovery_Foundation_operation_not_quiescent')
            states['research'] = {'runtime_initialized':dbfile.exists()}
    entries = inventory(Path(root), paths)
    return {'contract':'BNNIntakeOptionalRecoveryV1', 'modules':states,
            'protected_files':entries, 'configuration_sha256':hashlib.sha256(canonical(modules).encode()).hexdigest(),
            'automatic_replay':False,'deadline_renewal':False}
