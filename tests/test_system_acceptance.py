"""Autonomous result acceptance; analyst recommendations never grant permits."""
import copy
import json
import pytest
from bnn_intake.contracts import PROFILES
from bnn_intake.profile_contract import legacy,RELEASE_POLICY
from bnn_intake.storage import append,read,digest
from conftest import request
from test_native_context import native,turn,model_admit
from test_analysis import analytical_native,plan_method,product_call,generate,submit
from test_research import research,dispatch
from test_gaps import assessment
from test_research_basis import basis
from test_delegation import delegated


@pytest.mark.parametrize('contract',PROFILES,indirect=True)
def test_every_profile_completes_system_acceptance_without_human(analytical_native,contract):
    n=analytical_native;plan_method(n.engine,n.work,select=True)
    args=product_call(n);generate(n,args);submit(n,args)
    rev=n.engine.status('parent',n.work)['state']['revision'];a=assessment('limited')
    a.update(remaining=['Неизвестны первичные события'],limits=['Нет причинного дизайна'],next_step='Выдать отчёт с рекомендациями аналитику')
    result=n.engine.call('parent',request('assess',n.work,rev,'system-accept',assessment=a))
    accepted=result['system_acceptance']
    assert accepted['system_acceptance_complete'] and accepted['report_release_allowed']
    assert accepted['status']=='accepted_with_limitations' and not accepted['human_review_required']
    assert accepted['analyst_review_recommendations'] and all(not x['blocking'] for x in accepted['analyst_review_recommendations'])
    current=n.engine.call('parent',request('acceptance_status',n.work));assert current['current']
    assert current['external_action_authorized'] is False
    n.engine.call('parent',request('revise',n.work,rev+1,'new-evidence',change={'type':'add_gap','gap':{'id':'NEW','question':'Новая вводная','kind':'fact','material':True,'affected':['Вывод'],'required_result':'Проверить новую вводную'}}))
    assert not n.engine.call('parent',request('acceptance_status',n.work))['current']


def test_legacy_high_profile_no_longer_waits_for_external_review(analytical_native):
    n=analytical_native
    with n.host.store.db() as db:
        state=read(db,n.engine.profile,n.work);state['contract']['profile']='High Stakes';state['profile_contract']=legacy('High Stakes');state['revision']+=1;append(db,n.engine.profile,n.work,state)
    plan_method(n.engine,n.work,select=True);args=product_call(n);generate(n,args);submit(n,args)
    rev=n.engine.status('parent',n.work)['state']['revision'];a=assessment('limited');a.update(remaining=['Ограниченный исходник'],limits=['Не исследована популяция'])
    result=n.engine.call('parent',request('assess',n.work,rev,'accept-legacy',assessment=a))
    assert result['report_release_allowed'] and result['human_review_required'] is False
    assert n.engine.status('parent',n.work)['state']['profile_contract']['version']==1


def test_unknown_execution_is_not_converted_into_completed_acceptance(analytical_native):
    n=analytical_native;plan_method(n.engine,n.work,select=True);args=product_call(n);generate(n,args);submit(n,args)
    # Exact common action table, explicit interrupted-execution fixture only.
    with n.host.store.db() as db:
        db.execute("UPDATE action_attempts SET status='unknown' WHERE nonce=(SELECT nonce FROM action_attempts LIMIT 1)")
    rev=n.engine.status('parent',n.work)['state']['revision']
    with pytest.raises(PermissionError,match='unfinished_system_execution'):
        n.engine.call('parent',request('assess',n.work,rev,'not-done',assessment=assessment('sufficient')))


def test_agent_can_review_source_and_gap_without_operator(research):
    n=research;receipt=dispatch(n);rev=n.engine.status('parent',n.work)['state']['revision']
    result=n.engine.research.call('parent',request('review_path',n.work,rev,'system-path',path_id='PATH1',state='performed',
        reason='Получены оба текста правил',receipts=[{'attempt_id':receipt['attempt_id'],'receipt_sha256':digest(receipt)}],
        source_refs=[],reading_level='Ответ инструмента',remaining=['Первичные события отсутствуют']))
    source=n.host.store.record('parent',{'kind':'note','scope':'project','request_id':'system-source','source_ref':'synthetic:system-review','content':'Тексты обоих правил получены; причинные выводы не делались.'})
    ref={'kind':'record','id':source['id'],'revision':1}
    n.engine.research.call('parent',request('review_gap',n.work,result['revision'],'system-gap',gap_id='G1',state='partial',
        reason='Тексты проверены, событий нет',path_ids=['PATH1'],source_refs=[ref],remaining=['Нет первичных событий']))
    state=n.engine.status('parent',n.work)['state']
    assert state['paths']['PATH1']['review']['actor']=='system:parent'
    assert state['gaps']['G1']['review']['review_origin']=='system'
    a=assessment('limited');a.update(remaining=['Нет первичных событий'],limits=['Причинность не установлена'])
    result=n.engine.call('parent',request('assess',n.work,state['revision'],'accept-reviewed',assessment=a))
    assert result['report_release_allowed'] and result['system_acceptance']['system_acceptance_complete']


def test_system_review_cannot_forge_actor_or_source_success(research):
    n=research;receipt=dispatch(n,handler_result={'ok':False,'error':'unavailable'})
    args=request('review_path',n.work,6,'false-success',path_id='PATH1',state='performed',reason='Claim',
        receipts=[{'attempt_id':receipt['attempt_id'],'receipt_sha256':digest(receipt)}],source_refs=[],reading_level='Tool response',remaining=[])
    with pytest.raises(PermissionError,match='non_error_result'):n.engine.research.call('parent',args)
    args['actor']='owner'
    with pytest.raises(ValueError,match='contract_invalid'):n.engine.research.call('parent',args)


def test_shared_policy_remains_in_base_context_when_intake_is_off(native):
    n=native;mode=n.engine.mode('parent');n.engine.set_mode('parent','off',mode['revision'],'owner')
    outgoing=turn(n);text=json.dumps(outgoing)
    assert 'BN_SYSTEM_ACCEPTANCE_POLICY' in text and 'human_review_required' in text
    assert 'BN_INTAKE_CONTEXT' not in text
    assert model_admit(outgoing)[0].allow
    assert all(x in text for x in ['BNN','GSD','UDR','Foundation','Intake'])


def test_accepted_result_can_be_published_to_Foundation_and_replayed_without_new_write(basis,monkeypatch):
    n=basis;n.bridge.bind('parent',n.args)
    import hermes_cli.plugins as plugins
    monkeypatch.setattr(plugins,'_delivery_manager',lambda:n.manager)
    n.attached['registrations'].append(n.ctx.register_middleware('llm_execution',n.host.execution))
    plan_method(n.engine,n.work,select=True);args=product_call(n);generate(n,args);submit(n,args)
    rev=n.engine.status('parent',n.work)['state']['revision'];a=assessment('limited')
    a.update(remaining=['Нет первичных событий'],limits=['Одна учебная запись'])
    accepted=n.engine.call('parent',request('assess',n.work,rev,'accept-before-Foundation',assessment=a))
    args=request('publish_acceptance',n.work,accepted['revision'],'publish-Foundation',run_id='r-fixture')
    before=n.foundation.writes;first=n.bridge.publish_acceptance('parent',args)
    assert n.foundation.writes==before+1 and first['report_release_allowed']
    second=n.bridge.publish_acceptance('parent',args)
    assert second['replayed'] and n.foundation.writes==before+1
    current=n.engine.call('parent',request('acceptance_status',n.work));assert current['current'] and current['Foundation']
    saved=n.foundation.objects[first['Foundation']['object_id']]['object'];data=json.loads(saved['acceptance_json'])
    assert data['system_acceptance_complete'] and not data['human_review_required']


def test_current_source_revocation_invalidates_release_without_rewriting_history(analytical_native):
    n=analytical_native;plan_method(n.engine,n.work,select=True);args=product_call(n);generate(n,args);submit(n,args)
    state=n.engine.status('parent',n.work)['state'];a=assessment('limited');a.update(remaining=['Нет событий'],limits=['Учебная запись'])
    n.engine.call('parent',request('assess',n.work,state['revision'],'accept-before-revoke',assessment=a))
    source=n.engine.analysis.application_packet('parent',n.work,'A1',active=False)['source_anchors'][0]['ref']
    n.host.store.retract('parent',source['id'],source['revision'])
    status=n.engine.call('parent',request('acceptance_status',n.work))
    assert not status['current'] and not status['report_release_allowed']
    assert n.engine.status('parent',n.work)['state']['assessments'][-1]['system_acceptance']['system_acceptance_complete']


def test_research_projection_does_not_replace_admitted_parent_context(basis):
    from bnn_intake.common_policy import ResearchMemoryProjection
    n=basis;prepared=turn(n);before=n.host.store.binding('parent')
    projection=ResearchMemoryProjection(n.host,'parent',object())
    value=projection.context({'query':'Синтетические значения показателя','limit':5,'scope':{'project_id':'p','profile_id':n.host.store.profile}})
    after=n.host.store.binding('parent')
    assert value['status']=='assembled' and after['context']==before['context'] and after['turn']==before['turn']
    assert n.host.admission.ready('parent','native-turn',prepared)['status']=='ready'
    with pytest.raises(PermissionError,match='scope_mismatch'):
        projection.context({'query':'other project','scope':{'project_id':'foreign','profile_id':n.host.store.profile}})


def test_native_status_split_is_exact_and_idempotent():
    from bnn_intake.policy import extend_policy
    from jsonschema import Draft202012Validator
    rules=[{'id':'native.team','tool':'udr_team','effect':'write_local','roles':['parent'],
        'arguments_schema':{'type':'object','properties':{'action':{'enum':['status','start']},'run_id':{'type':'string'}},
                            'required':['action','run_id'],'additionalProperties':False}}]
    once=extend_policy(rules);assert extend_policy(once)==once
    for action,effect in [('status','read_local'),('start','write_local')]:
        matching=[r for r in once if r['tool']=='udr_team' and Draft202012Validator(r['arguments_schema']).is_valid({'action':action,'run_id':'r-test'})]
        assert len(matching)==1 and matching[0]['effect']==effect
    assert rules[0]['arguments_schema']['properties']['action']['enum']==['status','start']


def test_common_policy_is_exactly_the_same_with_or_without_optional_package():
    from bnn_intake.common_policy import frame,standalone_source
    source=standalone_source();namespace={};exec(compile(source,'<owned-common-policy>','exec'),namespace)
    assert namespace['POLICY_FRAME']==frame() and 'import bnn_intake' not in source
