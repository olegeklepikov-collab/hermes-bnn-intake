"""Versioned preparation obligations, not an evidence grade or deployment permit."""
import copy
from .contracts import PROFILES
from .storage import digest

OBLIGATIONS = [
    ('scope','eMVP','Ограничить назначение, область и неизвестные; не подменять подготовку допуском.'),
    ('alternatives','eMVP','Назвать существенную альтернативу и опасный пропуск.'),
    ('bounded_cycle','MVP','Завершить ограниченный проход получения, раскрытия и проверки либо сохранить ожидание настоящего результата.'),
    ('provenance','Standard','Сохранить воспроизводимые источники, различения, процедуры, зависимости, версии и непройденный остаток.'),
    ('revision','Standard','Покрыть назначенную работу и сохранить журнал пересмотра без автоматического повторного исполнения.'),
    ('plural_models','eStandard','Рассмотреть несколько моделей/проекций, взаимодействия уровней и альтернативные операционализации.'),
    ('sensitivity','eStandard','Проверить чувствительность и применимую состязательную альтернативу.'),
    ('independent_review','High Stakes','Нужна независимая компетентная проверка существенных оснований, методов, допуска и границ применения.'),
    ('independent_defences','Critical Infrastructure','Нужны независимые защиты, анализ общих причин отказа и безопасных режимов.'),
    ('recovery_admission','Critical Infrastructure','Нужны проверенное восстановление и необходимый эксплуатационный допуск.'),
]
EXTERNAL = {'PC-independent_review','PC-independent_defences','PC-recovery_admission'}  # Legacy IDs, not external-review gates.
RELEASE_POLICY = {'contract':'BNNSystemAcceptancePolicyV2','version':2,
    'human_review_required':False,'analyst_review_is_advisory':True,'human_review_blocks_release':False,
    'system_must_finish_available_required_checks':True,'external_action_permissions_are_separate':True,
    'overrides_legacy_external_review_release_gates':True}


def legacy(profile):
    if profile not in PROFILES: raise ValueError('intake_profile_unknown')
    rank=PROFILES.index(profile)
    obligations=[{'id':'PC-'+key,'introduced_at':level,'requirement':text,
                  'verification':'independent_qualified_path_required' if 'PC-'+key in EXTERNAL else 'model_report_is_unreviewed'}
                 for key,level,text in OBLIGATIONS if PROFILES.index(level)<=rank]
    return {'contract':'BNNIntakeProfileV1','version':1,'profile':profile,
            'source_refs':['Spec-1.0:5.1','SP-006','SP-007','SP-079','SP-113'],
            'obligations':obligations,'no_profile_to_E_level_mapping':True,
            'model_product_is_independent_acceptance':False,'deployment_admission':False}


def make(profile):
    value=legacy(profile);value['contract']='BNNIntakeProfileV2';value['version']=2
    texts={'PC-independent_review':'Выполнить доступную системную состязательную проверку оснований и методов; предметные рекомендации аналитику не блокируют выпуск.',
           'PC-independent_defences':'Проверить доступные защиты, общие причины отказа и безопасные режимы; непроверенные свойства явно отразить в результате.',
           'PC-recovery_admission':'Провести необходимые доступные испытания восстановления в области задачи; ограничения и рекомендации аналитику сохранить без ожидания человеческого допуска.'}
    for item in value['obligations']:
        item['verification']='system_check_with_evidence'
        if item['id'] in texts:item['requirement']=texts[item['id']]
    value['release_policy']=copy.deepcopy(RELEASE_POLICY)
    return value


def checked(state):
    value=state.get('profile_contract')
    if value is None: return None  # Legacy, immutable bases remain interpretable.
    if value not in [make(state['contract']['profile']),legacy(state['contract']['profile'])]:
        raise PermissionError('intake_profile_contract_changed_or_unsupported')
    return copy.deepcopy(value)


def current_minimum(engine,state):
    minimum=engine.store.config.get('intake_minimum_profile','eMVP')
    if minimum not in PROFILES: raise ValueError('intake_host_profile_invalid')
    if PROFILES.index(state['contract']['profile'])<PROFILES.index(minimum):
        raise PermissionError('intake_current_required_profile_not_met')


def udr_bridge(value):
    if value is None: return None
    return {'contract':'BNNIntakeUDRProfileBridgeV1','intake_profile_sha256':digest(value),
            'evidence_level':None,'profile_is_not_evidence_standard':True,
            'native_contract_fields_requiring_explicit_decision':['claim_types','error_cost','purpose','required_evidence_level'],
            'intake_obligation_ids':[o['id'] for o in value['obligations']],
            'unsupported_or_unverified':[o['id'] for o in value['obligations'] if o['id'] in EXTERNAL],
            'native_EvidenceStandard_created':False,'scientific_acceptance':False}


def apply(packet,value):
    packet.pop('profile_contract',None)
    packet.pop('profile_contract_sha256',None)
    if value is not None:
        packet['profile_contract']=copy.deepcopy(value);packet['profile_contract_sha256']=digest(value)
    return packet


def for_application(state,app):
    saved=state.get('method_applications',{}).get(app['id'])
    if saved is None:return checked(state)
    value=saved.get('profile_contract')
    if value is not None and value not in [make(state['contract']['profile']),legacy(state['contract']['profile'])]:
        raise PermissionError('intake_application_profile_contract_changed')
    return copy.deepcopy(value)


def check_product(value,product):
    reports=product.get('profile_checks')
    if value is None:
        if reports: raise PermissionError('intake_legacy_profile_requires_explicit_adoption')
        return
    expected={o['id'] for o in value['obligations']}
    if not isinstance(reports,list) or len(reports)!=len({r['obligation_id'] for r in reports}) or {r['obligation_id'] for r in reports}!=expected:
        raise ValueError('intake_all_profile_obligations_must_be_reported')
    nodes={n['id'] for n in product['nodes']}
    if any(not set(r['node_ids'])<=nodes for r in reports):raise ValueError('intake_profile_check_node_missing')
    if any(r['state']=='addressed' and not r['node_ids'] for r in reports):
        raise ValueError('intake_profile_addressed_check_requires_product_node')
    if any(r['state']=='unresolved' for r in reports) and not product['uncovered']:
        raise ValueError('intake_profile_unresolved_requires_explicit_remainder')


def status(engine,state):
    value=checked(state)
    if value is None:return {'state':'legacy_without_profile_contract','adoption_required_for_profile_qualification':True,'scientific_acceptance':False}
    products=state.get('analysis_products',{})
    reports=[]
    for product in products.values():
        # Products from before an explicit adoption are not new-profile products.
        if product.get('profile_contract_sha256')==digest(value):
            reports.append({'product_id':product['id'],'model_reports':product['profile_checks'],
                            'semantic_review':'unreviewed'})
    external=[]
    return {'profile_contract':value,'sha256':digest(value),'model_product_reports':reports,
            'independent_assurance_pending':external,'independent_assurance_verified':False,
            'sufficient_final_assessment_permitted':True,'human_review_required':False,
            'release_policy':copy.deepcopy(RELEASE_POLICY),
            'analyst_review_topics':[o for o in value['obligations'] if o['id'] in EXTERNAL],
            'scientific_acceptance':False,'deployment_admission':False,'UDR':udr_bridge(value)}


def assess(engine,state,assessment):
    summary=status(engine,state)
    return {'profile_contract_sha256':summary.get('sha256'),'independent_assurance_pending':[],
            'release_policy':copy.deepcopy(RELEASE_POLICY),'human_review_required':False,
            'model_product_refs':[{'id':p['id'],'sha256':digest(p)} for p in state.get('analysis_products',{}).values()],
            'semantic_acceptance':'unreviewed','deployment_admission':False}
