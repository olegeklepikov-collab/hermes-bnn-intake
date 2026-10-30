"""Profile transmission, typed accounting and gates. No real model in pytest."""
import copy
import json
import pytest
from bnn_intake.profile_contract import make,checked,check_product,udr_bridge
from bnn_intake.contracts import PROFILES
from bnn_intake.storage import append,digest,read
from conftest import prepare,request,record
from test_exposition import exposition
from test_analysis import application,plan_method,product_call
from test_native_context import native,turn
from test_delegation import delegated,start


@pytest.mark.parametrize('contract',PROFILES,indirect=True)
def test_profile_contract_is_versioned_cumulative_and_not_a_permit(fixture,contract):
    store,engine,work=fixture;prepare(engine,work,contract)
    result=engine.call('parent',request('profile_status',work))
    value=result['profile_contract'];assert value==make(contract['profile'])
    assert result['sha256']==digest(value) and not result['scientific_acceptance'] and not result['deployment_admission']
    rank=PROFILES.index(contract['profile'])
    if rank:assert {x['id'] for x in make(PROFILES[rank-1])['obligations']}<{x['id'] for x in value['obligations']}
    assert result['UDR']['evidence_level'] is None and not result['UDR']['native_EvidenceStandard_created']


@pytest.mark.parametrize('contract',PROFILES,indirect=True)
def test_real_grant_inherits_exact_profile_obligations_once(delegated,contract):
    n=delegated;child,_=start(n)
    value=n.engine.delegation.child_packet(child)
    original=make(contract['profile']);assert value['source_packet']['profile_contract']==original
    frame=n.attached['context_bridge'].frame(child)
    assert frame['text'].count('"contract":"BNNIntakeProfileV2"')==1
    assert not value['restrictions']['lower_profile']


def test_profile_product_cannot_omit_or_duplicate_required_checks(native):
    n=native;plan_method(n.engine,n.work,select=True)
    args=product_call(n);packet=n.engine.analysis.application_packet('parent',n.work,'A1')
    n.engine.analysis.check_product('parent',packet,args['product'])
    missing=copy.deepcopy(args['product']);missing.pop('profile_checks')
    with pytest.raises(ValueError,match='all_profile_obligations'):n.engine.analysis.check_product('parent',packet,missing)
    duplicate=copy.deepcopy(args['product']);duplicate['profile_checks'].append(duplicate['profile_checks'][0])
    with pytest.raises(ValueError,match='all_profile_obligations'):n.engine.analysis.check_product('parent',packet,duplicate)


@pytest.mark.parametrize('contract',['High Stakes','Critical Infrastructure'],indirect=True)
def test_prepared_only_high_profile_requires_actual_output_evidence(fixture,contract):
    store,engine,work=fixture;prepare(engine,work,contract);before=engine.status('parent',work)
    value={'id':'DEC','purpose':'Preparation check','state':'sufficient','remaining':[],'limits':[],'next_step':'Apply'}
    with pytest.raises(PermissionError,match='actual_output_evidence_required'):
        engine.call('parent',request('assess',work,1,'self-approval',assessment=value))
    assert before==engine.status('parent',work)
    assert engine.call('parent',request('profile_status',work))['human_review_required'] is False


def legacy(engine,work):
    """Exact old payload shape as a fixture, not a successful runtime migration."""
    with engine.store.db() as db:
        state=read(db,engine.profile,work);state.pop('profile_contract');state['revision']+=1
        append(db,engine.profile,work,state)


def test_explicit_adoption_preserves_the_old_application_and_basis(fixture,contract):
    store,engine,work=fixture;prepare(engine,work,contract);legacy(engine,work)
    ref=record(store);engine.call('parent',request('expose',work,2,'legacy-expose',exposition=exposition('E1',ref)))
    plan_method(engine,work)
    before=engine.analysis.application_packet('parent',work,'A1',active=False)
    assert 'profile_contract' not in before['source_packet']
    rev=engine.status('parent',work)['state']['revision']
    engine.call('parent',request('adopt_profile',work,rev,'adopt',reason='Explicit additive adoption for future assignments'))
    after=engine.analysis.application_packet('parent',work,'A1',active=False)
    assert after['source_packet'].pop('work_revision')==before['source_packet'].pop('work_revision')+1
    assert before==after
    plan_method(engine,work,app=application(identifier='A2'))
    later=engine.analysis.application_packet('parent',work,'A2')
    assert later['source_packet']['profile_contract']==make(contract['profile']) and later['input_fingerprint']!=before['input_fingerprint']
    assert engine.status('parent',work)['state']['profile_adoption_reason']


def test_profile_adoption_does_not_rewrite_selected_old_execution(fixture,contract):
    store,engine,work=fixture;prepare(engine,work,contract);legacy(engine,work)
    source=record(store);engine.call('parent',request('expose',work,2,'expose-old',exposition=exposition('E1',source)))
    plan_method(engine,work,select=True);rev=engine.status('parent',work)['state']['revision']
    with pytest.raises(PermissionError,match='clear_selected_application'):
        engine.call('parent',request('adopt_profile',work,rev,'cannot-adopt',reason='Must not replace in-flight input'))


def test_changed_minimum_blocks_new_analysis_without_hiding_saved_status(fixture,contract):
    store,engine,work=fixture;prepare(engine,work,contract)
    source=record(store);engine.call('parent',request('expose',work,1,'expose',exposition=exposition('E1',source)))
    store.config['intake_minimum_profile']='Critical Infrastructure'
    assert engine.status('parent',work)['state']['contract']==contract
    with pytest.raises(PermissionError,match='current_required_profile'):plan_method(engine,work)


def test_self_reported_independent_review_does_not_produce_independent_assurance(fixture,contract):
    store,engine,work=fixture;high={**contract,'profile':'High Stakes'};prepare(engine,work,high)
    value=make('High Stakes');reports=[{'obligation_id':o['id'],'state':'addressed','node_ids':['N1'],'reason':'Claimed by model'} for o in value['obligations']]
    check_product(value,{'profile_checks':reports,'nodes':[{'id':'N1'}],'uncovered':[]})
    status=engine.call('parent',request('profile_status',work))
    assert not status['independent_assurance_pending'] and status['sufficient_final_assessment_permitted']
    assert status['human_review_required'] is False and status['analyst_review_topics']


def test_tampered_profile_is_not_rebuilt_silently(fixture,contract):
    store,engine,work=fixture;prepare(engine,work,contract)
    state=engine.status('parent',work)['state'];state['profile_contract']['obligations'].pop()
    with pytest.raises(PermissionError,match='changed_or_unsupported'):checked(state)


@pytest.mark.parametrize('contract',['High Stakes','Critical Infrastructure'],indirect=True)
def test_empty_result_gate_blocks_before_native_action_reservation(native,contract):
    from agent.tool_attempt import tool_attempt_scope
    from hermes_cli.execution_control import ExecutionControlDenied
    n=native;turn(n);before=n.engine.status('parent',n.work)
    with n.host.store.db() as db:count=db.execute('SELECT count(*) FROM action_attempts').fetchone()[0]
    args=request('assess',n.work,3,'deny-before-effect',assessment={'id':'D','purpose':'Premature acceptance',
        'state':'sufficient','remaining':[],'limits':[],'next_step':'Deploy'})
    identity={'session_id':'parent','turn_id':'native-turn','tool_call_id':'profile-gate','api_request_id':'profile-gate-api'}
    with tool_attempt_scope('intake_work',identity,final_boundary='registry'):
        with pytest.raises(ExecutionControlDenied,match='actual_output_evidence_required'):
            n.ctx.dispatch_tool('intake_work',args,**identity)
    with n.host.store.db() as db:assert db.execute('SELECT count(*) FROM action_attempts').fetchone()[0]==count
    assert before==n.engine.status('parent',n.work)
