"""Native hook timeout must not race mandatory preparation or lose a session."""
import copy,time
import pytest
from hermes_cli.plugins import PluginManager,PluginContext,PluginManifest
from hermes_cli.execution_control import ExecutionControlDenied
from test_native_context import native,model_admit


def incoming(text='Read the addressed source'):
    return {'model':'boundary-test-no-provider','instructions':'Stable system layer',
            'input':[{'role':'user','content':text}]}


def test_slow_preparation_runs_after_fast_native_hook_and_only_once(native,monkeypatch):
    manager=PluginManager(scope_key=str(native.home));ctx=PluginContext(
        PluginManifest(name='stage-test',version='1',source='project',path=str(native.home)),manager)
    import hermes_cli.plugins as plugins
    monkeypatch.setattr(plugins,'_resolve_hook_callback_timeout',lambda:0.01)
    prep=native.host._native_context_preparation;before=prep.before;calls=[]
    def delayed(**kw):
        calls.append(kw['turn_id']);time.sleep(.03);return before(**kw)
    prep.before=delayed
    handle=ctx.register_hook('pre_llm_call',native.host.stage_turn)
    try:
        manager.invoke_hook('pre_llm_call',session_id='parent',user_message='Read the addressed source',turn_id='slow-turn',platform='cli')
        assert calls==[] and prep.pending['parent']['state']=='staged'
        for attempt in range(2):
            prepared=native.host.request(incoming(),session_id='parent',turn_id='slow-turn',platform='cli')['request']
            assert 'BN_CONTEXT_ADMISSION' in str(prepared)
            assert model_admit(prepared,'slow-turn','slow-api-'+str(attempt))[0].allow
        assert calls==['slow-turn'] and prep.pending['parent']['input'] is None
    finally:handle.dispose()


def test_failed_preparation_never_dispatches_or_automatically_repeats(native):
    prep=native.host._native_context_preparation;calls=[]
    def denied(**kw):calls.append(1);raise PermissionError('fixture_required_dependency_unavailable')
    prep.before=denied;native.host.stage_turn(session_id='parent',user_message='Read the addressed source',turn_id='failed')
    for _ in range(2):
        with pytest.raises(PermissionError,match='fixture_required_dependency_unavailable'):
            native.host.request(incoming(),session_id='parent',turn_id='failed',platform='cli')
    assert calls==[1]
    with pytest.raises(ExecutionControlDenied):model_admit(incoming(),'failed','failed-api')


def test_deadline_retires_only_its_completed_admission(native):
    prep=native.host._native_context_preparation;prep.timeout=1;values=iter([0,2,2]);prep.clock=lambda:next(values)
    native.host.stage_turn(session_id='parent',user_message='Read the addressed source',turn_id='expired')
    with pytest.raises(PermissionError,match='context_preparation_deadline_exceeded'):
        native.host.request(incoming(),session_id='parent',turn_id='expired',platform='cli')
    with native.host.store.db() as db:
        assert db.execute("SELECT status FROM context_admissions WHERE sid='parent'").fetchone()[0]=='failed'
    with pytest.raises(ExecutionControlDenied):model_admit(incoming(),'expired','expired-api')


def test_second_turn_keeps_session_work_and_requires_its_own_input(native):
    binding=native.host.store.binding('parent');work=native.host.working.packet('parent')
    for turn,text in [('first','Read the addressed source'),('second','Continue this same session')]:
        native.host.stage_turn(session_id='parent',user_message=text,turn_id=turn,platform='cli')
        prepared=native.host.request(incoming(text),session_id='parent',turn_id=turn,platform='cli')['request']
        assert model_admit(prepared,turn,turn+'-api')[0].allow
    after=native.host.store.binding('parent')
    assert (after['project'],after['task'])==(binding['project'],binding['task'])
    assert native.host.working.packet('parent')==work
    with pytest.raises(PermissionError,match='native_context_stage_turn_mismatch'):
        native.host.request(incoming(),session_id='parent',turn_id='stale-turn',platform='cli')


def test_missing_stage_and_changed_same_turn_input_do_not_borrow_authority(native):
    with pytest.raises(PermissionError,match='current_turn_context_not_ready'):
        native.host.request(incoming(),session_id='parent',turn_id='missing',platform='cli')
    native.host.stage_turn(session_id='parent',user_message='Read the addressed source',turn_id='one')
    with pytest.raises(PermissionError,match='native_context_stage_input_changed'):
        native.host.stage_turn(session_id='parent',user_message='Changed source',turn_id='one')


def test_concurrent_replacement_is_denied_during_preparation(native):
    prep=native.host._native_context_preparation;before=prep.before
    def overlapping(**kw):
        with pytest.raises(PermissionError,match='native_context_preparation_in_progress'):
            native.host.stage_turn(session_id='parent',user_message='Other input',turn_id='overlap')
        return before(**kw)
    prep.before=overlapping;native.host.stage_turn(session_id='parent',user_message='Read the addressed source',turn_id='active')
    prepared=native.host.request(incoming(),session_id='parent',turn_id='active',platform='cli')['request']
    assert model_admit(prepared,'active','active-api')[0].allow


def test_old_observer_path_reproduces_skip_before_late_ready(native,monkeypatch):
    import threading
    manager=PluginManager(scope_key=str(native.home));ctx=PluginContext(
        PluginManifest(name='old-observer-test',version='1',source='project',path=str(native.home)),manager)
    import hermes_cli.plugins as plugins
    monkeypatch.setattr(plugins,'_resolve_hook_callback_timeout',lambda:0.005)
    done=threading.Event();before=native.host._native_context_preparation.before
    def old_observer(**kw):
        try:time.sleep(.04);return before(**kw)
        finally:done.set()
    handle=ctx.register_hook('pre_llm_call',old_observer)
    try:
        assert manager.invoke_hook('pre_llm_call',session_id='parent',user_message='Read the addressed source',turn_id='old-turn',platform='cli')==[]
        with pytest.raises(PermissionError,match='current_turn_context_not_ready'):
            native.host.request(incoming(),session_id='parent',turn_id='old-turn',platform='cli')
        assert done.wait(3)
        assert native.host.admission.row('parent','old-turn')['status']=='ready'
    finally:handle.dispose()
