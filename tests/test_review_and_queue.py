"""Queue isolation and native dispatch: explicit finite local contracts."""
from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from bn_memory.store import canonical,sha
from test_native_context import native,turn


def queue(fixture):
    store,_,_=fixture
    from bn_memory.embedding_cache import Cache
    from bn_memory.source_fts import install
    with store.db() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='semantic_chunks'").fetchone():install(db)
        for i in range(34):db.execute("INSERT INTO semantic_chunks(id,scope,hash,text,state) VALUES(?,?,?,?, 'pending')",('c'+str(i),'project:p',sha('input-'+str(i)),'input-'+str(i)))
        contract=Cache(store,store.config,'document').contract['contract_hash']
        db.execute('INSERT INTO embedding_attempts VALUES(?,?,?,?,?,?,?)',('historical',contract,'document',canonical([sha('input-'+str(i)) for i in range(32)]),'outcome_or_usage_unknown',1,'{}'))
    return store,SimpleNamespace(store=store,config=store.config)


def test_unsettled_head_does_not_starve_independent_tail_or_replay_ledger(fixture):
    store,index=queue(fixture)
    from bn_memory.source_queue import eligible,deferred
    with store.db() as db:
        before=tuple(db.execute('SELECT * FROM embedding_attempts').fetchone())
        rows=eligible(index,db,['project:p'],1)
        assert rows[0]['id']=='c32' and deferred(index,db,['project:p'])==32
        assert eligible(index,db,['project:other'],1)==[]
        assert tuple(db.execute('SELECT * FROM embedding_attempts').fetchone())==before


def test_own_valid_cache_is_reused_without_replaying_historical_spend(fixture):
    store,index=queue(fixture)
    from bn_memory.embedding_cache import Cache
    from bn_memory.embeddings import Embeddings
    from bn_memory.source_queue import eligible
    vector=[1.0]+[0.0]*1023;cache=Cache(store,store.config,'document')
    with store.db() as db:cache.put(db,'input-0',vector,{'source':'synthetic valid cache'})
    def no_network(*a,**kw):raise AssertionError('cached vector must not call a provider')
    result=Embeddings(store,store.config,request=no_network).many(['input-0'],'document')
    assert result==[vector]
    with store.db() as db:
        assert eligible(index,db,['project:p'],1)[0]['id']=='c0'
        assert db.execute("SELECT status FROM embedding_attempts WHERE id='historical'").fetchone()[0]=='outcome_or_usage_unknown'


def test_query_cache_does_not_authorize_document_replay(fixture):
    store,index=queue(fixture)
    from bn_memory.embedding_cache import Cache
    from bn_memory.source_queue import eligible
    with store.db() as db:
        Cache(store,store.config,'query').put(db,'input-0',[1.0]+[0.0]*1023,{'source':'wrong purpose'})
        assert eligible(index,db,['project:p'],1)[0]['id']=='c32'


def test_profile_native_review_dispatch_and_no_legacy_fallback_on_refusal(tmp_path,monkeypatch):
    import contextvars
    from agent.background_review_adapter import register,prepare
    from agent.background_review import spawn_background_review_thread
    home=tmp_path/'profile';home.mkdir();monkeypatch.setenv('HERMES_HOME',str(home));called=[]
    marker=contextvars.ContextVar('review_test_marker',default='outside');seen=[]
    def native(**kw):called.append(kw);return lambda:seen.append(marker.get())
    remove=register(home,native)
    try:
        token=marker.set('profile-scoped')
        target,prompt=spawn_background_review_thread(SimpleNamespace(),[],review_memory=True,task_cfg={})
        marker.reset(token);target();assert len(called)==1 and seen==['profile-scoped']
        assert called[0]['review_memory'] is True
        monkeypatch.setenv('HERMES_HOME',str(tmp_path/'other'))
        assert prepare(SimpleNamespace(),[],'prompt',True,False,None,False) is None
    finally:remove()
    monkeypatch.setenv('HERMES_HOME',str(home))
    def denied(**kw):raise PermissionError('required_review_dependency_unavailable')
    remove=register(home,denied)
    try:
        with pytest.raises(PermissionError,match='required_review_dependency_unavailable'):
            spawn_background_review_thread(SimpleNamespace(),[],review_memory=True,task_cfg={})
    finally:remove()


def test_review_snapshot_keeps_current_turn_without_exporting_internal_reasoning(native):
    from bn_memory.background_review import scoped_input
    goal='Current completed task';turn(native,goal,'review-input')
    rows=[{'role':'user','content':'older unrelated task'}, {'role':'assistant','content':'old answer'},
          {'role':'user','content':goal},{'role':'assistant','content':'visible answer','reasoning_content':'PRIVATE_INTERNAL_TRACE'}]
    value=scoped_input(native.host,'parent','review-input',rows)
    assert len(value)==2 and value[0]['content']==goal and value[1]['content']=='visible answer'
    assert 'PRIVATE_INTERNAL_TRACE' not in canonical(value)


def test_readonly_leaf_cannot_start_recursive_background_review(tmp_path,monkeypatch):
    from agent.background_review_adapter import prepare
    from bn_memory.background_review import register
    home=tmp_path/'profile';home.mkdir();monkeypatch.setenv('HERMES_HOME',str(home))
    host=SimpleNamespace(admission=SimpleNamespace(profile_home=str(home)),store=SimpleNamespace(binding=lambda sid:{'readonly':True}))
    class NoExecutor:
        @property
        def subagent_lifecycle(self):raise AssertionError('leaf must not acquire another executor')
    remove=register(host,NoExecutor())
    try:prepare(SimpleNamespace(session_id='leaf',_current_turn_id='t'),[],'review',True,True,None,False)()
    finally:remove()


def test_cancelled_native_review_finishes_handshake_before_touching_executor(tmp_path,monkeypatch):
    from agent.background_review_adapter import prepare
    from agent.background_review import prepare_background_review_run
    from bn_memory.background_review import register
    home=tmp_path/'profile';home.mkdir();monkeypatch.setenv('HERMES_HOME',str(home));agent=SimpleNamespace();run=prepare_background_review_run(agent);run.cancel_requested.set()
    host=SimpleNamespace(admission=SimpleNamespace(profile_home=str(home)))
    remove=register(host,SimpleNamespace())
    try:
        prepare(agent,[],'review',True,True,run,False)()
        assert run.request_done.is_set() and agent._background_review_run is None
    finally:remove()


def test_native_worker_keeps_one_json_frame_when_library_prints_diagnostics(monkeypatch,capsys):
    import io
    from bn_memory import workers
    def noisy(*args):print('native diagnostic');return {'status':'healthy'}
    monkeypatch.setattr(workers,'agentmemory',noisy)
    monkeypatch.setattr(workers.sys,'stdin',io.StringIO(json.dumps({'config':{},'backend':'agentmemory','operation':'health'})))
    workers.main();captured=capsys.readouterr()
    assert json.loads(captured.out)=={'status':'healthy'}
    assert 'native diagnostic' in captured.err
