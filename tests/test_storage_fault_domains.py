import errno,json,os,sqlite3
from pathlib import Path
from types import SimpleNamespace
import pytest
from test_native_context import native
from bn_memory.storage_guard import Storage,StorageUnavailable,fault,denied


def config(tmp_path):
 state=tmp_path/'state';home=tmp_path/'home';state.mkdir();home.mkdir()
 return {'state_root':str(state),'profile_home':str(home),'storage_policy':{'version':1,'primary_free_bytes':8*1024**2,'background_free_bytes':16*1024**2,'reserve_bytes':1024**2,'probe_interval_seconds':1}}


def free(s,amount):
 s.status=lambda:{'domains':{'one':{'free_bytes':amount,'roles':['control','profile']}}}


def test_primary_and_producers_have_different_admission(tmp_path):
 s=Storage(config(tmp_path));free(s,12*1024**2);s.require('primary')
 with pytest.raises(StorageUnavailable,match='low_space'):s.require('background')
 with pytest.raises(StorageUnavailable):s.require('evidence',extra_bytes=1024)
 free(s,20*1024**2);s.require('background')
 with pytest.raises(StorageUnavailable):s.require('evidence',extra_bytes=6*1024**2)


def test_shared_paths_are_reported_as_one_capacity_domain(tmp_path):
 s=Storage(config(tmp_path));v=s.status();assert len(v['domains'])==1;assert len(next(iter(v['domains'].values()))['roles'])==2


def test_missing_or_changed_volume_is_not_recreated(tmp_path):
 s=Storage(config(tmp_path));s.roots['control']=(s.roots['control'][0],-1)
 with pytest.raises(StorageUnavailable,match='volume_changed'):s.require()
 s.roots['control']=(tmp_path/'absent',None)
 with pytest.raises(StorageUnavailable,match='path_unavailable'):s.require()
 assert not (tmp_path/'absent').exists()


def test_physically_allocated_reserve_is_released_only_as_own_scratch(tmp_path):
 s=Storage(config(tmp_path));s.ensure_reserve();p=s.reserve;assert p.stat().st_blocks*512>=s.policy['reserve_bytes']
 free(s,0);s.finalizing();assert not p.exists()
 free(s,20*1024**2);s.ensure_reserve();assert p.exists()
 p.write_bytes(b'user data')
 with pytest.raises(StorageUnavailable,match='reserve_invalid'):s.release_reserve()
 assert p.read_bytes()==b'user data'


def test_local_idempotent_settlement_retries_once_without_remote_retry(tmp_path):
 s=Storage(config(tmp_path));s.ensure_reserve();calls=[]
 def local():
  calls.append(True)
  if len(calls)==1:raise OSError(errno.ENOSPC,'disk full')
  return 'settled'
 assert s.persist(local)=='settled' and len(calls)==2 and not s.reserve.exists()


def test_non_storage_errors_are_not_retried(tmp_path):
 s=Storage(config(tmp_path));s.ensure_reserve();calls=[]
 def local():calls.append(True);raise ValueError('application defect')
 with pytest.raises(ValueError):s.persist(local)
 assert len(calls)==1 and s.reserve.exists()


@pytest.mark.parametrize('code',[sqlite3.SQLITE_FULL,sqlite3.SQLITE_IOERR_FSYNC,sqlite3.SQLITE_READONLY,sqlite3.SQLITE_CORRUPT])
def test_storage_sqlite_codes_are_distinguished_without_guessing(code):
 e=sqlite3.OperationalError('test');e.sqlite_errorcode=code;e.sqlite_errorname='SYNTHETIC_CODE'
 assert fault(e).startswith('storage_database_')
 before=denied(e,'preparation',False);after=denied(e,'result_settlement',True)
 assert before.effects_may_have_run is False and after.effects_may_have_run is True
 assert before.non_retryable and after.non_retryable


def test_busy_is_not_mislabelled_as_disk_damage():
 e=sqlite3.OperationalError('busy');e.sqlite_errorcode=sqlite3.SQLITE_BUSY
 assert fault(e) is None


def test_faulted_durable_probe_never_admits_work(tmp_path,monkeypatch):
 s=Storage(config(tmp_path));monkeypatch.setattr(os,'fsync',lambda fd:(_ for _ in ()).throw(OSError(errno.EIO,'test media fault')))
 with pytest.raises(StorageUnavailable,match='EIO'):s.require('primary',probe=True)


def test_failed_allocation_preserves_cause_and_removes_only_created_scratch(tmp_path,monkeypatch):
 s=Storage(config(tmp_path));monkeypatch.setattr(os,'urandom',lambda n:(_ for _ in ()).throw(OSError(errno.ENOSPC,'test full')))
 with pytest.raises(OSError) as e:s.ensure_reserve()
 assert e.value.errno==errno.ENOSPC and not s.reserve.exists()


def test_post_execution_notice_never_claims_not_processed():
 from agent.turn_failure_copy import failed_turn_notice,PARTIAL_FAILED_TURN_NOTICE
 assert failed_turn_notice([],effects_may_have_run=True)==PARTIAL_FAILED_TURN_NOTICE


def test_intake_pressure_stops_before_any_executor(native,monkeypatch):
 from test_native_context import turn
 from hermes_cli.execution_control import ExecutionControlDenied
 turn(native,'Synthetic storage boundary','storage-boundary');calls=[]
 def reject(*a,**kw):raise StorageUnavailable('storage_low_space',{'class':'primary'})
 monkeypatch.setattr(native.host.storage,'require',reject)
 bridge=native.attached['context_bridge']
 with pytest.raises(ExecutionControlDenied) as caught:
  bridge.execution({'messages':[]},lambda r:calls.append(True),session_id='parent',turn_id='storage-boundary',platform='cli')
 assert calls==[] and caught.value.effects_may_have_run is False


def test_storage_failure_after_response_never_reexecutes_provider(native,monkeypatch):
 from test_native_context import turn
 from hermes_cli.execution_control import ExecutionControlDenied
 from bn_memory.plugin import _document_attempt
 turn(native,'Synthetic finalization boundary','storage-finish');calls=[]
 monkeypatch.setattr(native.host,'request',lambda request,**kw:{'request':request})
 native.host.modules=SimpleNamespace(research=lambda sid:None)
 e=sqlite3.OperationalError('synthetic I/O');e.sqlite_errorcode=sqlite3.SQLITE_IOERR;e.sqlite_errorname='SQLITE_IOERR'
 def fail(*a,**kw):raise e
 native.host.accounting=SimpleNamespace(finish=fail)
 def remote(request):calls.append(True);_document_attempt.get()['accounting']='physical-1';return {'usage':{'total_tokens':2}}
 # Invoke the saved common executor, retaining its real settlement sequence.
 original=native.attached['context_bridge'].originals['execution']
 with pytest.raises(ExecutionControlDenied) as caught:original({'messages':[]},remote,session_id='parent',turn_id='storage-finish',platform='cli')
 assert len(calls)==1 and caught.value.effects_may_have_run is True
 native.host.modules=None
