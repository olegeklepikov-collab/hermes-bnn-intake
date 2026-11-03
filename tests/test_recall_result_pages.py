from types import SimpleNamespace
import json,pytest
from bn_memory.store import canonical,sha
from bn_memory.recall_results import retain,read


def setup(fixture):
 store,_,_=fixture
 broker=SimpleNamespace(store=store)
 result={'records':[{'id':'MEM-'+'a'*24,'revision':1,'content_hash':sha('x'),'content':'α\n'*9000}],
         'source_fragments':[],'derived':{'zvec':{'status':'queried','matches':[{'id':'MEM-'+'a'*24}]}},'has_more':False,'next_offset':None}
 return store,broker,result


def test_large_result_losslessly_read_by_pages_without_file_permission(fixture):
 store,b,r=setup(fixture);saved=retain(b,'parent',r,store.retrieval_basis('parent'))
 assert saved['contract']=='BNNRecallReferenceV1' and 'content' not in saved['record_refs'][0]
 assert saved['derived']['zvec']['returned_ids']==['MEM-'+'a'*24]
 text='';offset=0
 while True:
  p=read(b,'parent',{'id':saved['result_id'],'offset':offset,'max_chars':2048})
  assert sha(p['text'])==p['fragment_sha256'];assert p['offset']==len(text);text+=p['text']
  if not p['has_more']:break
  offset=p['next_offset']
 assert text==canonical(r) and sha(text)==saved['result_sha256']


def test_small_result_unchanged(fixture):
 store,b,_=setup(fixture);r={'records':[]};assert retain(b,'parent',r,store.retrieval_basis('parent')) is r


def test_result_cannot_be_read_by_another_session_or_project(fixture):
 store,b,r=setup(fixture);saved=retain(b,'parent',r,store.retrieval_basis('parent'));store.bind('other')
 with pytest.raises(PermissionError,match='owned_recall_result_required'):read(b,'other',{'id':saved['result_id']})
 with store.db() as db:db.execute("UPDATE sessions SET task='changed' WHERE sid='parent'")
 with pytest.raises(PermissionError,match='recall_basis_changed'):read(b,'parent',{'id':saved['result_id']})


def test_changed_basis_cannot_be_published(fixture):
 store,b,r=setup(fixture)
 with pytest.raises(PermissionError,match='recall_basis_changed'):retain(b,'parent',r,'stale')


def test_corruption_and_bad_offsets_fail_closed(fixture):
 store,b,r=setup(fixture);saved=retain(b,'parent',r,store.retrieval_basis('parent'));sid=saved['result_id']
 for a in [{'offset':-1},{'offset':True},{'max_chars':0},{'max_chars':4097},{'offset':saved['result_chars']+1}]:
  with pytest.raises(ValueError):read(b,'parent',{'id':sid,**a})
 with store.db() as db:db.execute('UPDATE recall_results SET body=? WHERE id=?',('changed',sid))
 with pytest.raises(PermissionError,match='integrity'):read(b,'parent',{'id':sid})


def test_unregistered_result_id_does_not_become_a_path(fixture):
 store,b,_=setup(fixture)
 with pytest.raises(ValueError):read(b,'parent',{'id':'../../private/key'})
 with pytest.raises(PermissionError):read(b,'parent',{'id':'RCL-'+'f'*32})


def test_result_reader_capability_is_delegated_only_with_recall():
 from bn_memory.action_control import ActionControl
 rule=lambda name,tool:{'id':name,'tool':tool,'effect':'read_local','roles':['reviewer'],'arguments_schema':{'type':'object'}}
 control=ActionControl(SimpleNamespace(config={'tool_policy':{'version':1,'rules':[rule('recall','mcp__context__memory_recall'),rule('other','mcp__context__memory_status'),rule('memory.read.result.child','mcp__context__memory_read')]}}))
 p=[{'rule_id':'recall','arguments_schema':{'type':'object'}}]
 got=control.delegation('reviewer',p);assert len(got)==2 and len(p)==1
 assert control.delegation('reviewer',got)==got
 assert len(control.delegation('reviewer',[{'rule_id':'other','arguments_schema':{'type':'object'}}]))==1
