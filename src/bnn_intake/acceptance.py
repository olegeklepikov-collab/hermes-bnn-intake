"""System acceptance of a declared result, with non-blocking analyst advice.

No human approval, second task database, new executor or external action permit.
Source validity and unsettled execution are checked independently of advice.
"""
import copy
import json
import hashlib
from .storage import digest,read
from .profile_contract import RELEASE_POLICY,EXTERNAL,checked,make


def prepare(engine,sid,state):
    """Resolve real outputs and current source rights before the write transaction."""
    plan={'outputs':[],'anchors':[],'corpora':[]}
    if engine.analysis is None:
        from .analysis import Analysis
        engine.analysis=Analysis(engine)
    for product in state.get('analysis_products',{}).values():
        packet=engine.analysis.application_packet(sid,state['work_ref'],product['application_id'],active=False)
        if packet['input_fingerprint']!=product['input_fingerprint']:
            raise PermissionError('intake_acceptance_product_inputs_changed')
        plan['anchors']+=packet['source_anchors']
        plan['outputs'].append({'kind':'analysis','id':product['id'],'sha256':digest(product),'producer':product['producer']})
    with engine.store.db() as db:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        tasks=[dict(r) for r in db.execute('SELECT n.* FROM native_tasks n JOIN working_bindings b ON n.profile_id=b.profile_id AND n.root_sid=b.sid WHERE b.profile_id=? AND b.work_id=?',(engine.profile,state['work_ref']))] if 'native_tasks' in tables else []
        modules=[dict(r) for r in db.execute("SELECT n.* FROM module_attempts n JOIN working_bindings b ON n.profile_id=b.profile_id AND n.sid=b.sid WHERE b.profile_id=? AND b.work_id=? AND n.module='gsd' AND n.state='succeeded'",(engine.profile,state['work_ref']))] if 'module_attempts' in tables else []
        gsd_packets=[json.loads(r[0]) for r in db.execute('SELECT body FROM intake_delegations WHERE profile_id=? AND work_id=?',(engine.profile,state['work_ref']))] if modules else []
    for module in modules:
        body=json.loads(module['body']);result=body.get('result',{});jobs=result.get('native_role_jobs',{})
        if body.get('ok') is not True or not jobs or any(v.get('native_receipt',{}).get('terminal_state')!='SUCCEEDED' for v in jobs.values()):
            raise PermissionError('intake_acceptance_actual_GSD_roles_required')
        plan['outputs'].append({'kind':'gsd','id':module['id'],'sha256':digest(body),
            'job_ids':[v['id'] for v in jobs.values()], 'limitations':result.get('missing_components',[])+result.get('engine_result',{}).get('open_obligations',[])})
    for packet in gsd_packets:
        if packet.get('module')!='gsd':continue
        for item in packet['source_packet']['records']:
            ref=item['ref'];plan['anchors'].append({'ref':{k:ref[k] for k in ['kind','id','revision']},'content_sha256':ref['content_hash']})
        for ref in packet['source_packet'].get('documents',[]):plan['anchors'].append({'ref':ref,'content_sha256':ref['unit_sha256']})
    for task in tasks:
        body=json.loads(task['body'])
        if task['state'] not in {'succeeded','needs'} or body.get('product') is None:continue
        if engine.delegation is None:raise PermissionError('intake_acceptance_native_reader_unavailable')
        result=engine.delegation.call(sid,{'schema_version':1,'action':'read_result','work_ref':state['work_ref'],
            'native_task_id':task['id'],'native_revision':task['revision'],'limit':65536})
        if result['native_receipt']['terminal_state']!='SUCCEEDED':raise PermissionError('intake_acceptance_native_result_not_returned')
        packet=engine.delegation.child_packet(body['child_sid'],allow_inactive=True)['source_packet']
        for item in packet['records']:
            ref=item['ref'];plan['anchors'].append({'ref':{k:ref[k] for k in ['kind','id','revision']},'content_sha256':ref['content_hash']})
        for ref in packet.get('documents',[]):
            plan['anchors'].append({'ref':ref,'content_sha256':ref['unit_sha256']})
        plan['outputs'].append({'kind':'native_task','id':task['id'],'revision':task['revision'],'sha256':digest(body),
                                'product_sha256':result['product_sha256']})
    for cid in state.get('document_corpora',{}):
        if engine.documents is None:raise PermissionError('intake_acceptance_document_reader_unavailable')
        coverage=engine.documents.coverage(sid,state['work_ref'],cid,state)
        if not coverage['inventory_complete'] or any(p['units_confirmed']<p['units_expected'] for p in coverage['required_phases'].values()):
            raise PermissionError('intake_acceptance_available_corpus_work_unfinished')
        if coverage['missing_sources_count']:
            _,rows,_,_=engine.documents.bound(sid,state['work_ref'],cid,state)
            from bn_memory.documents import current_decoder
            if any(r['status']=='extracted' and not current_decoder(r) for r in rows):
                raise PermissionError('intake_acceptance_current_decoder_required')
            if any(r['status'] not in {'extracted','unsupported','failed','error'} for r in rows):
                raise PermissionError('intake_acceptance_corpus_processing_unfinished')
        plan['corpora'].append(coverage)
    plan['anchors']=list({digest(v):v for v in plan['anchors']}.values())
    return plan


def unsettled(engine,db,sid,state):
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    sessions=[r[0] for r in db.execute('SELECT sid FROM working_bindings WHERE profile_id=? AND work_id=?',(engine.profile,state['work_ref']))]
    if not sessions:return []
    from agent.tool_attempt import current_tool_attempt
    attempt=current_tool_attempt();own_nonce=attempt.nonce if attempt is not None else ''
    pending=[];marks=','.join('?' for _ in sessions)
    from bn_memory.action_retry import UNKNOWN
    for table,column,bad in [('model_attempts','state',['reserved','outcome_unknown']),
                             ('action_attempts','status',sorted(set(UNKNOWN)|{'reserved','admitted','unknown','outcome_unknown'})),
                             ('module_attempts','state',['preparing','running','unknown','launch_reserved','cancelling'])]:
        if table not in tables:continue
        cols={r[1] for r in db.execute('PRAGMA table_info('+table+')')}
        args=[engine.profile,*sessions,*bad]
        sql='SELECT * FROM '+table+' WHERE profile_id=? AND sid IN ('+marks+') AND '+column+' IN ('+','.join('?' for _ in bad)+')'
        for row in db.execute(sql,args):
            if 'nonce' in cols and row['nonce']==own_nonce:continue
            if table=='action_attempts' and row[column] in UNKNOWN:
                from bn_memory.action_review import latest_review,native_stopped
                reviewed=latest_review(db,engine.profile,row['nonce'])
                if reviewed and reviewed['outcome'] in {'performed','not_performed'} and native_stopped(db,engine.profile,row['nonce']):
                    continue  # Preserve raw history; the owned effect has actually been reconciled.
            if table=='module_attempts' and row['module']=='research' and row[column] in {'preparing','running'}:
                continue  # A durable research lifetime is not an executing leaf.
            pending.append({'kind':table,'state':row[column]})
    if 'native_tasks' in tables:
        for row in db.execute('SELECT n.id,n.state FROM native_tasks n JOIN working_bindings b ON n.profile_id=b.profile_id AND n.root_sid=b.sid WHERE b.profile_id=? AND b.work_id=?',(engine.profile,state['work_ref'])):
            if row['state'] in {'planned','running','launch_reserved','unknown','cancelling'}:pending.append({'kind':'native_task','id':row['id'],'state':row['state']})
    return pending


def recommendations(state,assessment,completion):
    values={}
    def add(aspect,priority,reason,refs=None):
        key=digest([aspect,priority])[:20]
        values[key]={'id':'AR-'+key,'priority':priority,'aspect':aspect,'reason':reason,
                     'evidence_refs':refs or [],'blocking':False,'human_response_required':False}
    for item in make(state['contract']['profile'])['obligations']:
        if item['id'] in EXTERNAL:add(item['requirement'],'important','Область повышенного профиля заслуживает последующего внимания аналитика',[item['id']])
    for text in assessment['remaining']+assessment['limits']:add(text,'important','Сохранённый предел результата или непройденный вопрос')
    for product in state.get('analysis_products',{}).values():
        for text in product.get('uncovered',[])+product.get('limitations',[]):add(text,'important','Ограничение аналитического продукта',[product['id']])
        for check in product.get('profile_checks',[]):
            if check['state']!='addressed':add(check['reason'],'important','Профильная проверка имеет ограничение',[product['id'],check['obligation_id']])
    if completion:
        for gap in completion['gaps']:
            if not gap['resolved']:
                for text in gap['remaining']:add(text,'important' if gap['material'] else 'desirable','Лакуна отражена в выпущенном результате',[gap['id']])
    if not values:add('Сопоставить выводы и границы отчёта с контекстом последующего применения','desirable','Последующее аналитическое ревью возможно и не является условием выпуска')
    return list(values.values())


def finish(engine,db,sid,state,assessment,completion,plan):
    if assessment['state']=='resource_paused':
        return {'status':'incomplete','system_acceptance_complete':False,'report_release_allowed':False,
                'human_review_required':False,'reason':'Available system work is paused, not complete'}
    pending=unsettled(engine,db,sid,state)
    if pending:raise PermissionError('intake_acceptance_unfinished_system_execution')
    outputs=copy.deepcopy(plan['outputs'])
    for output in outputs:
        if output['kind']=='analysis':
            row=db.execute('SELECT body,sha256 FROM intake_model_outcomes WHERE profile_id=? AND id=?',(engine.profile,output['producer']['outcome_id'])).fetchone()
            if row is None or row['sha256']!=output['producer']['outcome_sha256'] or digest(json.loads(row['body']))!=row['sha256'] or json.loads(row['body'])['status']!='returned':
                raise PermissionError('intake_acceptance_actual_product_origin_required')
        elif output['kind']=='native_task':
            row=db.execute('SELECT revision,body FROM native_tasks WHERE profile_id=? AND id=?',(engine.profile,output['id'])).fetchone()
            if row is None or row['revision']!=output['revision'] or digest(json.loads(row['body']))!=output['sha256']:
                raise PermissionError('intake_acceptance_native_result_changed')
        else:
            row=db.execute('SELECT state,body FROM module_attempts WHERE profile_id=? AND id=?',(engine.profile,output['id'])).fetchone()
            if row is None or row['state']!='succeeded' or digest(json.loads(row['body']))!=output['sha256']:
                raise PermissionError('intake_acceptance_GSD_result_changed')
    if completion:
        for key,path in completion['paths'].items():
            if path['verified_for_declared_scope']:
                outputs.append({'kind':'research_path','id':key,'review_sha256':digest(path['review'])})
    if not outputs:raise PermissionError('intake_acceptance_actual_output_evidence_required')
    engine.analysis.assert_sources_current(db,sid,plan['anchors'])
    advice=recommendations(state,assessment,completion)
    limitations=bool(assessment['remaining'] or assessment['limits'] or assessment['state']=='limited')
    if completion and any(not g['resolved'] for g in completion['gaps']):limitations=True
    if completion and any(p['review']['state']!='performed' for p in completion['paths'].values() if p.get('review')):limitations=True
    if any(p.get('uncovered') or p.get('limitations') or any(c['state']!='addressed' for c in p.get('profile_checks',[])) for p in state.get('analysis_products',{}).values()):limitations=True
    if any(c['missing_sources_count'] for c in plan.get('corpora',[])):limitations=True
    if any(o.get('limitations') for o in outputs):limitations=True
    result={'contract':'BNNSystemAcceptanceV1','status':'accepted_with_limitations' if limitations else 'accepted',
        'system_acceptance_complete':True,'report_release_allowed':True,'human_review_required':False,
        'analyst_review_recommendations':advice,'acceptance_scope':'declared_available_system_work_not_universal_scientific_truth',
        'work_ref':state['work_ref'],'evaluated_work_revision':state['revision'],'outputs':outputs,
        'accepted_revision':state['revision']+1,
        'system_checks':['actual_outputs','current_source_rights','version_integrity','execution_settled','declared_research_paths'],
        'corpus_coverage':plan.get('corpora',[]),
        'release_policy':copy.deepcopy(RELEASE_POLICY),'external_action_authorized':False}
    result['basis_sha256']=digest({'contract':state['contract'],'outputs':outputs,'sources':plan['anchors'],
        'paths':state['paths'],'gaps':state['gaps'],'profile':checked(state),'policy':RELEASE_POLICY,
        'expositions':{k:v['purpose_fingerprint'] for k,v in state['expositions'].items()},'corpora':state.get('document_corpora',{})})
    return result


def precheck(engine,sid,args):
    with engine.store.db() as db:state=read(db,engine.profile,args['work_ref'])
    if not isinstance(state,dict):raise ValueError('intake_prepare_required')
    if state['revision']!=args['expected_revision']:raise ValueError('intake_work_revision_conflict')
    plan=prepare(engine,sid,state) if args['assessment']['state']!='resource_paused' else {'outputs':[],'anchors':[]}
    with engine.store.db() as db:
        engine._apply(db,sid,copy.deepcopy(state),'assess',args,None,plan)


def current(engine,sid,state):
    if not isinstance(state,dict) or not state.get('assessments'):
        return {'current':False,'report_release_allowed':False,'state':'not_assessed','human_review_required':False}
    assessment=state['assessments'][-1];saved=assessment.get('system_acceptance')
    if not saved or not saved['system_acceptance_complete']:
        return {'current':False,'report_release_allowed':False,'state':'system_work_incomplete','human_review_required':False}
    if state['revision']!=saved['accepted_revision']:
        return {'current':False,'report_release_allowed':False,'state':'new_revision_requires_system_reacceptance','human_review_required':False}
    try:
        plan=prepare(engine,sid,state)
        with engine.store.db() as db:
            completion=engine.research.completion(db,sid,state) if engine.research is not None else None
            if completion and completion['unperformed_paths']:raise PermissionError('research_paths_changed')
            checked_result=finish(engine,db,sid,state,assessment,completion,plan)
        if saved['basis_sha256']!=checked_result['basis_sha256']:raise PermissionError('acceptance_basis_changed')
    except (PermissionError,ValueError) as exc:
        return {'current':False,'report_release_allowed':False,'state':'evidence_changed_requires_system_reacceptance',
                'reason':type(exc).__name__,'human_review_required':False}
    return {**copy.deepcopy(saved),'current':True}


def report(engine,sid,state):
    value=current(engine,sid,state)
    if not value.get('current') or not value['report_release_allowed']:raise PermissionError('intake_current_system_acceptance_required')
    lines=['# Системная приёмка результата','',
           'Статус: '+value['status']+'. Работа: '+value['work_ref']+'.',
           'Системная приёмка завершена. Ожидание человеческого ревью для выпуска не требуется.','',
           '## Выполненные системные проверки','']
    labels={'actual_outputs':'Фактические результаты и квитанции исполнителей','current_source_rights':'Текущие права на источники',
        'version_integrity':'Версии и целостность входов/выходов','execution_settled':'Отсутствие незавершённого/неизвестного исполнения',
        'declared_research_paths':'Завершение объявленных исследовательских путей'}
    lines += ['- '+labels.get(x,x)+'.' for x in value['system_checks']]
    lines += ['','## Рекомендации аналитику','',
              'Рекомендации не блокируют выпуск и не требуют ответа для завершения работы.','']
    for rec in value['analyst_review_recommendations']:
        label='Важно' if rec['priority']=='important' else 'Желательно'
        lines+=['- **'+label+':** '+rec['aspect']+'. Основание: '+rec['reason']+'.']
    lines+=['','## Область результата','',
            'Приёмка относится к объявленной доступной системной работе. Непроверенные свойства и недостаток свидетельств не объявлены установленными фактами.',
            'Приёмка не выдаёт разрешений на публикацию в интернете, оплату, удаление данных или иные внешние действия.','']
    text='\n'.join(lines)
    return {'markdown':text,'sha256':hashlib.sha256(text.encode()).hexdigest(),'current':True,'report_release_allowed':True,'human_review_required':False}
