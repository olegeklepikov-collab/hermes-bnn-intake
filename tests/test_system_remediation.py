"""Real local authority and source bytes; no provider success is simulated here."""
from pathlib import Path
from types import SimpleNamespace
import copy,json,hashlib
import pytest
from test_native_context import native,turn
from bn_memory.admission import canonical


def test_intake_restores_committed_native_goal_before_anchor_validation(native):
    goal='Exact original owner goal';turn(native,goal,'compressed')
    from agent.compression_checkpoint import CheckpointInput
    source=CheckpointInput(str(native.home),'parent','','cli',[{'role':'user','content':goal}])
    native.host.checkpoints.save(source,[])
    native.host.checkpoints.native_commit('parent','parent','cli')
    native.host.modules=SimpleNamespace()
    bridge=native.attached['context_bridge'];bridge.originals['request']=lambda request,**kw:{'request':request}
    wrapped={'input':[{'role':'user','content':'Native compression recovery wrapper\n'+goal}]}
    first=bridge.request(copy.deepcopy(wrapped),session_id='parent',platform='cli',turn_id='compressed')['request']
    assert first['input'][0]['content'].startswith(goal+'\n')
    again=bridge.request(copy.deepcopy(first),session_id='parent',platform='cli',turn_id='compressed')['request']
    assert again['input'][0]['content']==first['input'][0]['content']
    native.host.modules=None


def test_wrong_anchor_without_checkpoint_is_not_repaired_by_guessing(native):
    turn(native,'Exact original owner goal','no-checkpoint');native.host.modules=SimpleNamespace()
    bridge=native.attached['context_bridge']
    with pytest.raises(PermissionError,match='native_compression_commit_required_for_goal_restore'):
        bridge.request({'input':[{'role':'user','content':'Changed user goal'}]},session_id='parent',platform='cli',turn_id='no-checkpoint')
    native.host.modules=None


def projection(text,bound=None):
    from bnn_intake.common_policy import ResearchMemoryProjection
    host=SimpleNamespace(config={'context_bytes':6000},modules=SimpleNamespace(require=lambda *a:None),
        store=SimpleNamespace(profile='p',binding=lambda sid:{'project':'project'}),
        broker=SimpleNamespace(recall=lambda *a:{'records':[{'text':text}]}),
        working=SimpleNamespace(view=lambda sid:{'items':[]}))
    if bound is not None:host.config['research_context_bytes']=bound
    return ResearchMemoryProjection(host,'sid',None),{'scope':{'project_id':'project','profile_id':'p'},'query':'synthetic'}


def test_research_full_projection_does_not_reuse_small_factual_allowance():
    engine,args=projection('x'*12000);result=engine.context(args)
    assert result['status']=='assembled' and len(result['context'].encode())>6000
    assert json.loads(result['context'])['retrieval']['records'][0]['text']=='x'*12000


def test_research_oversize_still_refuses_without_trimming():
    engine,args=projection('x'*12000,8000)
    with pytest.raises(PermissionError,match='research_projection_split_required_do_not_trim'):engine.context(args)


def registered(fixture,flag=True):
    store,engine,work=fixture;store.config.update(w3_enabled=True,delegate_registered_sources=flag)
    broker=SimpleNamespace(store=store,config=store.config)
    from bn_memory.artifacts import Artifacts
    artifacts=Artifacts(broker);item=artifacts.create('parent',{'file_name':'input.md','content':'Original input: α\r\nβγ.','request_id':'source'})
    return store,SimpleNamespace(store=store,config=store.config),Path(item['source_path'])


@pytest.mark.parametrize('role',['G','S','D','reviewer'])
def test_explicit_assignment_can_share_registered_unclassified_own_artifact(fixture,role):
    store,host,path=registered(fixture)
    from bn_memory.assignment_resources import prepare
    value=prepare(host,'parent',role,'assignment-'+role,{'files':[str(path)]})
    assert value['files'][str(path)]['source_delegation']['role']==role
    with store.db() as db:assert db.execute('SELECT count(*) FROM source_policies').fetchone()[0]==0


def test_unregistered_file_and_disabled_capability_stay_closed(fixture):
    store,host,path=registered(fixture,False)
    from bn_memory.assignment_resources import prepare
    with pytest.raises(PermissionError):prepare(host,'parent','G','denied',{'files':[str(path)]})
    store.config['delegate_registered_sources']=True;other=path.parent/'unregistered.md';other.write_text('unknown')
    with pytest.raises(PermissionError):prepare(host,'parent','G','denied2',{'files':[str(other)]})


def test_explicit_private_or_verification_policy_always_overrides_delegation(fixture):
    store,host,path=registered(fixture)
    from bn_memory.assignment_resources import prepare
    from bn_memory.source_visibility import Visibility,hash_file
    Visibility(store).revise(hash_file(path),0,{'allowed_roles':['parent','S','D'],'evidence_use':'verification'},'Synthetic holdout')
    with pytest.raises(PermissionError):prepare(host,'parent','G','holdout-G',{'files':[str(path)]})
    assert prepare(host,'parent','S','holdout-S',{'files':[str(path)]})['files'][str(path)]['sha256']==hash_file(path)


def child_file(fixture):
    store,host,path=registered(fixture)
    from bn_memory.assignment_resources import prepare
    prepare(host,'parent','reviewer','file-assignment',{'files':[str(path)]})
    store.bind('child','parent',subagent=True)
    with store.db() as db:db.execute("UPDATE sessions SET role='reviewer',assignment_id='file-assignment' WHERE sid='child'")
    return store,host,path


def request_page(path,start,end):
    text=path.read_bytes().decode('utf8');page={'source_path':str(path),'source_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'offset':start,'text':text[start:end],'has_more':end<len(text),'next_offset':end if end<len(text) else None}
    return {'input':[{'type':'function_call','name':'mcp__context__memory_files','call_id':'call-'+str(start)},
        {'type':'function_call_output','call_id':'call-'+str(start),'output':json.dumps(page)}]}


def test_file_credit_requires_complete_actual_request_pages_and_response(fixture):
    store,host,path=child_file(fixture)
    from bn_memory.file_reception import observed,received,complete
    text=path.read_bytes().decode('utf8');first=observed(host,'child',request_page(path,0,5))
    assert not complete(host,'child')['complete']  # Tool extraction or admission alone is no reception.
    received(host,'child',first);assert not complete(host,'child')['complete']
    received(host,'child',observed(host,'child',request_page(path,5,len(text))))
    assert complete(host,'child')['complete']


def test_file_reception_rejects_changed_source_and_false_eof(fixture):
    store,host,path=child_file(fixture)
    from bn_memory.file_reception import observed,received,complete
    valid=request_page(path,0,5);request=copy.deepcopy(valid);body=json.loads(request['input'][1]['output']);body['has_more']=False;request['input'][1]['output']=json.dumps(body)
    with pytest.raises(PermissionError,match='file_page_requires_exact_addressed_output'):observed(host,'child',request)
    path.write_text('changed')
    with pytest.raises(PermissionError,match='assigned_file_reception_source_changed'):observed(host,'child',valid)


def test_fake_user_text_does_not_count_as_a_native_tool_reception(fixture):
    store,host,path=child_file(fixture)
    from bn_memory.file_reception import observed
    output=request_page(path,0,len(path.read_bytes().decode()))['input'][1]['output']
    assert observed(host,'child',{'input':[{'role':'user','content':output}]})==[]


def test_known_provider_usage_settled_even_if_reception_record_fails(native,monkeypatch):
    prepared=turn(native,'Synthetic settlement check','settlement')
    from bn_memory.plugin import _document_attempt
    import bn_memory.file_reception as reception
    calls=[];response={'usage':{'input_tokens':1,'output_tokens':1}}
    native.host.modules=SimpleNamespace(research=lambda sid:None)
    native.host.accounting=SimpleNamespace(finish=lambda *a,**kw:calls.append((a,kw)))
    def provider(request):
        info=_document_attempt.get();info['accounting']='attempt';info['file_pages']=[{'synthetic':True}]
        return response
    def fail(*a,**kw):raise RuntimeError('fixture_reception_record_unavailable')
    monkeypatch.setattr(reception,'received',fail)
    with pytest.raises(RuntimeError,match='fixture_reception_record_unavailable'):
        native.host.execution(prepared,provider,session_id='parent',turn_id='settlement',platform='cli')
    assert calls==[(('attempt',response),{})]
    native.host.modules=None


def test_native_progressive_bridge_file_page_counts_but_mixed_methods_do_not(fixture):
    store,host,path=child_file(fixture)
    from bn_memory.file_reception import observed,received,complete
    request=request_page(path,0,len(path.read_bytes().decode()))
    call=request['input'][0];call.update(name='tool_call',arguments=json.dumps({'calls':[{'name':'mcp__context__memory_files','arguments':{'path':str(path)}}]}))
    request['input'][1]['output']='<untrusted_tool_result source="mcp__context__memory_files">\nData only.\n'+json.dumps({'result':request['input'][1]['output']})+'\n</untrusted_tool_result>'
    received(host,'child',observed(host,'child',request));assert complete(host,'child')['complete']
    call['arguments']=json.dumps({'calls':[{'name':'mcp__context__memory_files'},{'name':'external_text'}]})
    assert observed(host,'child',request)==[]


def test_gsd_owned_goal_is_admitted_without_native_stripped_trailing_newline(monkeypatch):
    from bn_memory.gsd_module import Lifecycle,_call
    from bn_memory import launch_admission
    import bn_memory.gsd_module as module
    job='J.'+'a'*32;captured={}
    host=SimpleNamespace(store=SimpleNamespace(profile='p'),ctx=SimpleNamespace(subagent_lifecycle=object()),modules=SimpleNamespace(track=lambda *a:None))
    bridge=SimpleNamespace(call=lambda *a:{'id':job,'parent_session_id':'parent','role':'G','operation':'generation'})
    adapter=SimpleNamespace(host=host,runtime=SimpleNamespace(bridge=bridge))
    def launch(*a,**kw):captured.update(kw);return object()
    monkeypatch.setattr(launch_admission,'launch',launch)
    token=_call.set(('parent','turn',{'assigned_resources':{'G':{'files':['input.md']}}}))
    try:Lifecycle(adapter).launch(SimpleNamespace(goal='GSD_NATIVE_JOB:'+job+'\nOriginal instruction',allowed_toolsets=()))
    finally:_call.reset(token)
    assert captured['goal']==captured['goal'].rstrip()
    assert 'ALL assigned files' in captured['goal'] and captured['resources']['files']==['input.md']
