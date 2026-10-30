"""Small common-Host policy, independent of optional intake state and mode."""
import inspect
import json
from .profile_contract import RELEASE_POLICY
from .storage import canonical,digest


def policy():
    return {**RELEASE_POLICY,'applies_to':['BNN','GSD','UDR','Foundation','Intake'],
        'rules':[
            'Complete all necessary available system checks and release the result without waiting for external human review.',
            'Keep material uncertainty and unavailable evidence explicit; do not call unperformed system work complete.',
            'Provide specific important/desirable analyst review recommendations in the final report; recommendations never block release.',
            'Preserve exact native leaf schemas; put relevant limitations in their existing fields. The coordinator supplies the final user report.',
            'Historical unreviewed/scientific_acceptance=false tags describe evidence, not a requirement to wait for a human.',
            'This policy grants no external action, publication, payment, deletion, access or unknown-outcome retry permission.']}


def frame():
    value=policy()
    return '<BN_SYSTEM_ACCEPTANCE_POLICY sha256="'+digest(value)+'">\n'+canonical(value)+'\n</BN_SYSTEM_ACCEPTANCE_POLICY>'


class ResearchMemoryProjection:
    """Foundation projection must not overwrite the parent's admitted context."""
    def __init__(self,host,sid,original):self.host,self.sid,self.original=host,sid,original
    def context(self,request):
        from bn_memory.store import canonical,sha
        self.host.modules.require(self.sid,'research')
        binding=self.host.store.binding(self.sid);scope=request['scope']
        if scope.get('project_id')!=binding['project'] or scope.get('profile_id')!=self.host.store.profile:
            raise PermissionError('research_memory_projection_scope_mismatch')
        found=self.host.broker.recall(self.sid,{'query':request['query'],'mode':'hybrid' if self.host.config.get('semantic_context') else 'local','limit':request.get('limit',5)})
        value={'contract':'BNResearchMemoryProjectionV1','scope':scope,'query':request['query'],
            'retrieval':found,'working':self.host.working.view(self.sid),
            'extraction_is_not_complete_original_reading':True}
        text=canonical(value)
        if len(text.encode())>self.host.config.get('context_bytes',200000):raise PermissionError('research_projection_split_required_do_not_trim')
        return {'status':'assembled','scope':scope,'context':text,'context_hash':sha(text),
                'adapter':'scoped public recall; native parent context remains unchanged'}
    def __getattr__(self,name):return getattr(self.original,name)


def install_runtime_adapters(host):
    if not host.modules or getattr(host,'_system_acceptance_research_adapter',False):return
    original=host.modules.require
    def require(sid,module):
        result=original(sid,module)
        if module=='research':
            from bn_memory.research_module import Research
            if not hasattr(host,'_research_module'):host._research_module=Research(host)
            native=host._research_module
            if isinstance(native,Research) and not getattr(native,'_pure_memory_projection',False):
                session=native.session
                def bound(receiver,body):
                    value=session(receiver,body);value.memory=ResearchMemoryProjection(host,receiver,value.memory)
                    return value
                native.session=bound;native._pure_memory_projection=True
        return result
    host.modules.require=require;host._system_acceptance_research_adapter=True


def standalone_source():
    """Install self-contained off-loader code; no optional package import at run time."""
    from .bootstrap import compatibility,register_common
    from .parent_route import ParentRoute,Context
    prefix='''from pathlib import Path
import hashlib,json,dataclasses,os

def register(ctx):
    from bn_memory.plugin import Host
    from hermes_cli import execution_control
    from hermes_constants import get_hermes_home
    settings=Path(ctx.get_config('config_file'));config=json.loads(settings.read_text())
    if Path(config['profile_home']).resolve()!=get_hermes_home().resolve():raise PermissionError('common_policy_profile_mismatch')
    import bn_memory.plugin as common
    for base,files in [(Path(common.__file__).resolve().parent.parent,PINNED['memory_files']),(Path(execution_control.__file__).resolve().parent.parent,PINNED['core_files'])]:
        for name,expected in files.items():
            path=base/name
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!=expected:raise PermissionError('common_policy_native_source_changed')
    host=Host(settings);host.ctx=ctx;ctx.on_unload(host.close)
    if config.get('inherit_current_parent_auth'):host.ctx=Context(ctx)
    install_runtime_adapters(host)
    original=host.assembled_context
    host.assembled_context=lambda sid,query,turn:original(sid,query,turn)+'\\n'+POLICY_FRAME
    register_common(host,ctx)
'''
    return 'PINNED='+repr(compatibility())+'\nPOLICY_FRAME='+repr(frame())+'\n'+prefix+'\n'+inspect.getsource(ParentRoute)+'\n'+inspect.getsource(Context)+'\n'+inspect.getsource(ResearchMemoryProjection)+'\n'+inspect.getsource(install_runtime_adapters)+'\n'+inspect.getsource(register_common)
