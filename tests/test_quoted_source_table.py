"""Source identity must resolve a quotation without inventing group scope."""
from dataclasses import replace
from decimal import Decimal

import pytest

from mingjing.dimension_tables import _claim
from mingjing.facts import FactStore, fact_from_dict
from mingjing.judge import Judge
from mingjing.models import Fact
from mingjing.scope import source_scope_dimensions
from mingjing.source_semantics import freeze_source_dimensions
from mingjing.source_table_reference import (link_quoted_customer_tables,
    validated_table_reference, query_quoted_table)


def sample(*, citation='资料来源：公司招股书、证券研究所', published='2023-10-16',
           anonymous=False, other_table=False):
    chunks = ['表4：2023H1公司主要客户', '营业收入（万元）', '2023H1', '清华大学', '604',
              '北京大学', '328', *(['客户H','708'] if anonymous else []), citation]
    text, lines = '', []
    for index, raw in enumerate(chunks):
        start = len(text)
        text += raw+'\n'
        lines.append({'text': raw, 'start': start, 'end': start+len(raw), 'bbox': [0,index*20,500,index*20+10]})
    claims = [_claim(text, '甲公司', lines[index+1], title=lines[0], headers=lines[1:3],
        labels=[lines[index]], account='REVENUE', period='2023H1', unit='万元',
        dimension_kind='customer', dimension_id=lines[index]['text']) for index in range(3,len(lines)-1,2)]
    table = 'source-table-'+'b'*24
    rows = [('清华大学','603.56'), ('北京大学','328.32'), *([('客户H','708.40')] if anonymous else [])]
    facts = [Fact('row-'+str(index),'甲公司','REVENUE','2023H1',scope='未知',
        value=Decimal(value)*10000,display_unit='万元',source_doc='a'*12, publish_date=published,
        evidence=('a'*12+':p205:1,2,3,4:'+str(index).zfill(8),),
        dimension_kind='customer',dimension_id=label, source_table_id=table,
        source_table_evidence=('a'*12+':p205:5,6,7,8:deadbeef',)) for index,(label,value) in enumerate(rows)]
    if other_table:
        facts.append(replace(facts[0],fact_id='other-table',source_table_id='source-table-'+'c'*24,value=Decimal(1)))
    reports=[{'name':'甲公司招股说明书.pdf','companies':['甲公司'],'sha256':'a'*64,
              'report_id':'report-'+'a'*20,'publish_date':published}]
    return text, claims, facts, reports


def linked_sample(**kwargs):
    text, claims, facts, reports = sample(**kwargs)
    linked, _ = link_quoted_customer_tables(claims,text,facts,reports,'2024-04-22')
    return [freeze_source_dimensions(replace(c,binding_status='agreed')) for c in linked], facts


def test_explicit_source_table_correct_and_wrong_quotations_are_decided_without_group_scope():
    claims, facts = linked_sample()
    assert all(f.scope=='未知' for f in facts)
    assert all(source_scope_dimensions(c)['scope_header']=='来源表' for c in claims)
    judge = Judge(FactStore(facts),'2024-04-22',require_dual_binding=True)
    results = [judge.verify(claim) for claim in claims]
    assert [r.status for r in results]==['verified','verified']
    assert all(r.verification_basis=='explicit_disclosed_table_comparison' for r in results)
    wrong = replace(claims[0],candidate_value='7000000')
    result = judge.verify(wrong)
    assert result.status=='violated' and result.error_type=='E1'
    assert result.correction and len(result.correction)>=5
    assert result.operands[0]['scope']=='未知'


@pytest.mark.parametrize('title', ['表4：2023H1乙公司主要客户', '表4：甲公司与乙公司主要客户'])
def test_customer_table_other_issuer_does_not_inherit_task_issuer(title):
    text, claims, facts, reports = sample()
    text = text.replace('表4：2023H1公司主要客户',title)
    delta = len(title)-len('表4：2023H1公司主要客户')
    from copy import deepcopy
    edited = []
    for c in claims:
        context = deepcopy(c.table_context)
        context['title'] = title
        context['title_source'].update(text=title,end=len(title))
        context['dimension_source']['caption'] = context['title_source']
        for ref in context['dimension_source']['headers']+context['dimension_source']['label_spans']+[context['dimension_source']['cell']]:
            ref['start']+=delta;ref['end']+=delta
        edited.append(replace(c,start=c.start+delta,end=c.end+delta,table_context=context))
    linked, _ = link_quoted_customer_tables(edited,text,facts,reports,'2024-04-22')
    assert not any(validated_table_reference(c) for c in linked)
    assert all(Judge(FactStore(facts),'2024-04-22').verify(freeze_source_dimensions(
        replace(c,binding_status='agreed'))).abstention_code=='X3' for c in linked)


@pytest.mark.parametrize('citation,published', [
    ('资料来源：互联网','2023-10-16'), ('资料来源：公司年报','2023-10-16'),
    ('资料来源：公司招股书',''), ('资料来源：公司招股书','2024-04-23')])
def test_unreferenced_unknown_or_future_sources_cannot_authorize_table(citation,published):
    claims, _=linked_sample(citation=citation,published=published)
    assert not any(validated_table_reference(c) for c in claims)


def test_source_selection_ignores_values_and_disallows_similar_names_or_extra_tables():
    text, claims, facts, reports=sample(other_table=True)
    changed=[replace(f,value=Decimal('999999999')) for f in facts]
    linked,_=link_quoted_customer_tables(claims,text,changed,reports,'2024-04-22')
    assert all(validated_table_reference(c) for c in linked)
    assert {c.table_context['source_table_reference']['source_table_id'] for c in linked}=={'source-table-'+'b'*24}
    # Losing a second exact customer removes the cohort proof entirely.
    changed=[replace(f,dimension_id=f.dimension_id+'实验中心') if f.dimension_id=='北京大学' else f for f in facts]
    assert not any(validated_table_reference(c) for c in link_quoted_customer_tables(claims,text,changed,reports,'2024-04-22')[0])


def test_anonymous_customer_requires_complete_source_cohort_and_explicit_reference():
    claims,facts=linked_sample(anonymous=True)
    anonymous=claims[-1]
    assert validated_table_reference(anonymous)
    assert query_quoted_table(FactStore(facts),anonymous,'2024-04-22').status=='found'
    text,original,facts,reports=sample(anonymous=True)
    facts.append(replace(facts[0],fact_id='new-customer',dimension_id='客户E'))
    linked,_=link_quoted_customer_tables(original,text,facts,reports,'2024-04-22')
    assert not validated_table_reference(linked[-1])


def test_missing_or_conflicting_cell_never_uses_group_fact_or_another_table():
    claims,facts=linked_sample()
    target=claims[0]
    missing=[f for f in facts if f.dimension_id!='清华大学']
    missing.append(replace(facts[0],fact_id='group',source_table_id='',scope='合并'))
    result=Judge(FactStore(missing),'2024-04-22',require_dual_binding=True).verify(target)
    assert result.status=='abstained' and result.abstention_code=='X1'
    conflict=[*facts,replace(facts[0],fact_id='conflict',value=Decimal('1'))]
    result=Judge(FactStore(conflict),'2024-04-22',require_dual_binding=True).verify(target)
    assert result.status=='abstained' and result.abstention_code=='X4'


def test_frozen_source_reference_and_both_binders_remain_required():
    claims,facts=linked_sample()
    judge=Judge(FactStore(facts),'2024-04-22',require_dual_binding=True)
    assert judge.verify(replace(claims[0],binding_status='disagreed')).abstention_code=='X3'
    proof={**claims[0].table_context['source_table_reference'],'source_doc':'f'*12}
    tampered=replace(claims[0],table_context={**claims[0].table_context,'source_table_reference':proof})
    assert judge.verify(tampered).abstention_code=='X3'
    assert query_quoted_table(FactStore(facts),claims[0],'2024-04-21').status=='source_required'
    assert not validated_table_reference(replace(claims[0],relation='yoy'))


def test_table_and_scope_evidence_survive_fact_json_round_trip():
    _, facts=linked_sample()
    assert fact_from_dict(facts[0].to_dict())==facts[0]


def test_agent_tools_bind_independently_and_consume_only_the_cited_table():
    from mingjing.financial_tools import FinancialDispatcher
    claims, facts=linked_sample()
    dispatcher=FinancialDispatcher('test','lock',claims,facts,[],[], '2024-04-22',task_company='甲公司')
    def call(role,tool,params):
        return dispatcher.dispatch({'session_id':role,'role':role,'run_id':'test',
            'resource_lock':'lock','tool_name':tool,'params':params,'call_id':f'{role}-{tool}'})
    claim=claims[0]
    for role in ('planner','verifier'):
        dispatcher.register_session(role,role,[claim.claim_id])
        listed=call(role,'mj_list_mentions',{})
        assert listed['mentions'][0]['source_dimensions']['scope']=='来源表'
        binding=dict(company=claim.company,account=claim.account,period=claim.period,
                     scope='来源表',relation='value',base_period='')
        assert call(role,'mj_submit_binding',{'mention_id':claim.claim_id,'binding':binding})['accepted']
    assert dispatcher.freeze_bindings()[0].binding_status=='agreed'
    dispatcher.register_session('coordinator','coordinator',[claim.claim_id])
    fact=call('coordinator','mj_get_fact',{'binding_id':claim.claim_id})
    assert fact['query_outcome']['status']=='found' and fact['fact']['scope']=='未知'
    assert fact['required_rule_ids']==['E1-value']
    call('coordinator','mj_get_rule',{'rule_id':'E1-value'})
    result=call('coordinator','mj_verify',{'binding_id':claim.claim_id})
    assert result['status']=='verified'
    assert result['verification_basis']=='explicit_disclosed_table_comparison'


def test_fake_table_scope_binding_does_not_promote_an_unreferenced_claim():
    from mingjing.financial_tools import FinancialDispatcher
    text,claims,facts,_=sample(citation='资料来源：互联网')
    dispatcher=FinancialDispatcher('test','lock',claims,facts,[],[],'2024-04-22')
    fake=dict(company='甲公司',account='REVENUE',period='2023H1',scope='来源表',relation='value',base_period='')
    for role in ('planner','verifier'):
        dispatcher.bindings[role][claims[0].claim_id]=fake
    assert dispatcher.freeze_bindings()[0].binding_status=='disagreed'
    assert not dispatcher.agreements[claims[0].claim_id]['scope_source_proven']


def test_repeated_tables_in_the_same_document_must_all_agree():
    text,claims,facts,reports=sample()
    repeated=[replace(f,fact_id=f.fact_id+'-repeated',source_table_id='source-table-'+'c'*24) for f in facts]
    linked,_=link_quoted_customer_tables(claims,text,[*facts,*repeated],reports,'2024-04-22')
    claim=freeze_source_dimensions(replace(linked[0],binding_status='agreed'))
    assert len(validated_table_reference(claim)['source_table_ids'])==2
    assert Judge(FactStore([*facts,*repeated]),'2024-04-22',require_dual_binding=True).verify(claim).status=='verified'
    repeated[0]=replace(repeated[0],value=Decimal('1'))
    assert Judge(FactStore([*facts,*repeated]),'2024-04-22',require_dual_binding=True).verify(claim).abstention_code=='X4'


def test_rehashed_source_reference_cannot_change_the_frozen_row_identity():
    from mingjing.source_table_reference import _digest
    claims,facts=linked_sample()
    original=claims[0]
    proof={**original.table_context['source_table_reference'],'source_dimension_id':'北京大学'}
    proof['sha256']=_digest({key:value for key,value in proof.items() if key!='sha256'})
    changed=replace(original,table_context={**original.table_context,'source_table_reference':proof})
    assert validated_table_reference(changed) is None
    assert Judge(FactStore(facts),'2024-04-22',require_dual_binding=True).verify(changed).abstention_code=='X3'


@pytest.mark.parametrize('requested,available,allowed',[
    ('中科院力学研究所','中国科学院力学研究所',False),
    ('三台县梓丰现代农业','三台县梓丰现代农业发展有限公司',False),
    ('国防科技大学','国防科技大学实验中心',False),
    ('中国航天','中国航天空气动力技术研究院',False),
    ('某大学附属','某大学附属医院',False),
    ('中国航空气动力技术研究院','中国航天空气动力技术研究院',False),
    ('北京海淀甲科技','北京海淀甲乙科技有限公司',False),
])
def test_row_local_abbreviation_is_not_a_global_customer_alias(requested,available,allowed):
    from mingjing.source_table_reference import _quoted_abbreviation
    from mingjing.dimensions import dimension_ids_compatible
    assert _quoted_abbreviation(requested,available) is allowed
    assert not dimension_ids_compatible('customer',requested,available)


def ordinal_sample(requested='客户H', available='客户H'):
    """Three complete rows with actual draft ordinals, never guessed ones."""
    rows = [('清华大学', '604'), ('北京大学', '328'), (requested, '708')]
    text = ''; lines = []
    for index, raw in enumerate(['表4：2023H1公司主要客户', '营业收入（万元）', '2023H1',
                                *[part for n, row in enumerate(rows, 1) for part in (str(n), *row)],
                                '资料来源：公司招股书、证券研究所']):
        start = len(text); text += raw + '\n'
        lines.append({'start': start, 'end': start+len(raw), 'text': raw,
                      'bbox': [0,index*20,500,index*20+10]})
    claims = []
    for n, index in enumerate((3, 6, 9), 1):
        ordinal = {**lines[index], 'bbox': lines[index+2]['bbox']}
        c = _claim(text, '甲公司', lines[index+2], title=lines[0], headers=lines[1:3],
                   labels=[lines[index+1]], account='REVENUE', period='2023H1', unit='万元',
                   dimension_kind='customer', dimension_id=lines[index+1]['text'],
                   additional={'source_row_index': n, 'source_row_index_evidence': ordinal})
        claims.append(c)
    _, _, facts, reports = sample(anonymous=True)
    facts = [replace(f, source_row_index=n, dimension_id=available if n==3 else f.dimension_id)
             for n, f in enumerate(facts, 1)]
    return text, claims, facts, reports


@pytest.mark.parametrize('requested,available', [
    ('中国航空气动力技术研究院', '中国航天空气动力技术研究院'),
    ('北京海淀甲科技', '北京海淀甲乙科技有限公司'),
])
def test_near_name_error_with_correct_ordinals_cannot_be_verified(requested, available):
    text, claims, facts, reports = ordinal_sample(requested, available)
    linked, _ = link_quoted_customer_tables(claims, text, facts, reports, '2024-04-22')
    assert not validated_table_reference(linked[-1])
    assert Judge(FactStore(facts), '2024-04-22').verify(
        freeze_source_dimensions(replace(linked[-1], binding_status='agreed'))).status!='verified'


def test_anonymous_ordinal_cannot_bypass_incomplete_customer_roster():
    text, claims, facts, reports = ordinal_sample()
    linked, _ = link_quoted_customer_tables(claims, text, facts, reports, '2024-04-22')
    assert validated_table_reference(linked[-1])
    facts.append(replace(facts[0], fact_id='customer-E', dimension_id='客户E', source_row_index=4))
    linked, _ = link_quoted_customer_tables(claims, text, facts, reports, '2024-04-22')
    assert not validated_table_reference(linked[-1])


@pytest.mark.parametrize('requested,available', [
    ('中科院力学研究所', '中国科学院力学研究所'),
    ('三台县梓丰现代农业', '三台县梓丰现代农业发展有限公司'),
])
def test_registered_mapping_requires_issuer_report_table_and_actual_ordinal(requested, available):
    from mingjing.source_table_reference import _quoted_abbreviation
    args = dict(company='并行科技',
        report_sha256='9f351a9852deaf4b6365dafb9c8195debee341277352bf91b69060c27c069ed1',
        source_table_ids=['source-table-cc560059afb2881d4eb4f99e'], row_index=3)
    assert _quoted_abbreviation(requested, available, **args)
    for field, value in [('company', '乙公司'), ('report_sha256', 'a'*64),
                         ('source_table_ids', ['source-table-'+'b'*24]), ('row_index', 0)]:
        assert not _quoted_abbreviation(requested, available, **{**args,field:value})


@pytest.mark.parametrize('pin,citation', [
    ('a'*12, '资料来源：公司招股书、证券研究所'),
    ('', '资料来源：公司招股书（2022年10月16日原版，调整前）'),
])
def test_explicit_old_report_constraint_is_preserved(pin, citation):
    text, claims, facts, reports = sample(citation=citation, published='2022-10-16')
    claims = [replace(c, declared_source=pin or None,
                      query_strategy='pinned' if pin else 'latest_as_of') for c in claims]
    newer = [replace(f, fact_id=f.fact_id+'-new', source_doc='d'*12,
                     publish_date='2023-10-16', value=Decimal(1)) for f in facts]
    reports += [{**reports[0], 'sha256':'d'*64, 'publish_date':'2023-10-16'}]
    linked, _ = link_quoted_customer_tables(claims, text, [*facts,*newer], reports, '2024-04-22')
    assert all(c.declared_source=='a'*12 for c in linked)
    assert all(Judge(FactStore([*facts,*newer]), '2024-04-22').verify(
        freeze_source_dimensions(replace(c,binding_status='agreed'))).status=='verified' for c in linked)


def test_same_document_repeated_table_versions_cannot_suppress_a_conflict():
    text, claims, facts, reports = sample()
    repeated = [replace(f, fact_id=f.fact_id+'-adjusted', source_table_id='source-table-'+'c'*24,
                        version='调整后', value=Decimal('1')) for f in facts]
    linked, _ = link_quoted_customer_tables(claims, text, [*facts,*repeated], reports, '2024-04-22')
    claim = freeze_source_dimensions(replace(linked[0], binding_status='agreed'))
    assert Judge(FactStore([*facts,*repeated]), '2024-04-22').verify(claim).abstention_code=='X4'


@pytest.mark.parametrize('field,value', [('company','乙公司'), ('period','2022Y'),
    ('account','NP_PARENT'), ('declared_source','c'*12), ('query_strategy','latest_as_of')])
def test_direct_query_rejects_drifted_claim_identity(field, value):
    claims, facts = linked_sample()
    changed = replace(claims[0], **{field:value})
    assert validated_table_reference(changed) is None
    assert query_quoted_table(FactStore(facts), changed, '2024-04-22').status=='source_required'


def test_query_requires_each_independent_admitted_table_cell_to_exist():
    text, claims, facts, reports = sample()
    repeated = [replace(f, fact_id=f.fact_id+'-repeated', source_table_id='source-table-'+'c'*24) for f in facts]
    linked, _ = link_quoted_customer_tables(claims, text, [*facts,*repeated], reports, '2024-04-22')
    claim = freeze_source_dimensions(replace(linked[0], binding_status='agreed'))
    incomplete = [*facts, repeated[1]]
    assert query_quoted_table(FactStore(incomplete), claim, '2024-04-22').status=='missing'


def test_sales_share_without_revenue_denominator_is_not_an_admitted_revenue_share_table():
    text, claims, facts, reports = sample()
    share_claims = []
    for c in claims:
        headers = [*c.table_context['dimension_source']['headers'],
                   {'text':'占营业收入比例', 'start':0, 'end':7, 'bbox':[0,0,1,1]}]
        source = {**c.table_context['dimension_source'], 'headers':headers}
        context = {**c.table_context, 'account_hint':'REVENUE_SHARE', 'definition_id':'metric:revenue-share',
                   'dimension_source':source, 'unit_hint':'%'}
        share_claims.append(replace(c, account='REVENUE_SHARE', unit='%', candidate_value='0.03',table_context=context))
    repeated = [replace(f,fact_id=f.fact_id+'-sales',source_table_id='source-table-'+'c'*24) for f in facts]
    shares = [replace(f,fact_id=f.fact_id+'-share',account='REVENUE_SHARE',value=Decimal('0.03'),
                      display_unit='%',definition_id='metric:revenue-share') for f in facts]
    linked, _ = link_quoted_customer_tables(share_claims,text,[*facts,*repeated,*shares],reports,'2024-04-22')
    assert all(validated_table_reference(c)['source_table_ids']==['source-table-'+'b'*24] for c in linked)


@pytest.mark.parametrize('citation', [
    '资料来源：非公司招股书，仅为模拟资料', '资料来源：公司招股书（模拟示例）',
    '资料来源：不是公司招股书', '资料来源：公司招股书（旧版）',
])
def test_negative_or_unresolved_old_citation_is_not_positive_source_proof(citation):
    claims, _ = linked_sample(citation=citation)
    assert not any(validated_table_reference(c) for c in claims)
