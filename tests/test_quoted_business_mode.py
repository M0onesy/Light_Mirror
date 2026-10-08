"""Real first-inquiry grid and all six original paragraph occurrences.

The quotation result is separate from independent denominator authorization;
amount, issuer, source, period, unit and missing-cell counterexamples coexist.
"""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path

import pytest

from mingjing.coverage import CoverageLedger
from mingjing.facts import FactStore
from mingjing.financial_tools import FinancialDispatcher
from mingjing.judge import Judge, _source_semantic_issue
from mingjing.source_business_mode import DEFINITION, extract_source_facts
from mingjing.source_semantics import freeze_source_dimensions, source_binding_issue
from mingjing.source_table_reference import link_quoted_tables, validated_table_reference

DATA=json.loads((Path(__file__).parent/'fixtures/quoted_business_mode_p31.json').read_text(encoding='utf-8'))
CUTOFF='2024-04-22'


def source_facts(source=None):
    source=DATA['source'] if source is None else source
    return extract_source_facts(source['pages'],path=source['filename'],company=DATA['company'],
        doc_sha=source['sha256'],disclosure_date=source['publish_date'],disclosure_version='原披露')


def reports():
    source=DATA['source']
    return [dict(name=source['filename'],companies=[DATA['company']],sha256=source['sha256'],
        publish_date=source['publish_date'],report_id=source['id'])]


def page_claims(page=None):
    page=DATA['draft_page'] if page is None else page
    text=page['text']
    region={**page,'start':0,'end':len(text),'id':'page-31'}
    blocks=[]
    cursor=0
    for index,block in enumerate(page['blocks']):
        start=text.find(block['text'],cursor)
        if start<0:continue
        end=start+len(block['text']);cursor=end
        blocks.append({**block,'start':start,'end':end,'id':f'block-{index}',
            'page':31,'page_id':'page-31'})
    return text,CoverageLedger.build(text,[region],blocks,DATA['company'],'2023Y')


def rewritten(page,old,new):
    """Modify original characters before extraction; remap original geometry."""
    page=deepcopy(page)
    page['text']=page['text'].replace(old,new)
    cursor=0
    for line in page['lines']:
        line['text']=line['text'].replace(old,new)
        start=page['text'].find(line['text'],cursor)
        assert start>=0
        line.update(start=start,end=start+len(line['text']))
        cursor=line['end']
    for block in page['blocks']:block['text']=block['text'].replace(old,new)
    return page


def linked(page=None,facts=None,registry=None):
    text,(ledger,claims)=page_claims(page)
    result,diagnostics=link_quoted_tables(claims,text,source_facts() if facts is None else facts,
        reports() if registry is None else registry,CUTOFF)
    assert len(result)==len(claims)
    assert [(claim.claim_id,claim.start,claim.end) for claim in result]==[(claim.claim_id,claim.start,claim.end) for claim in claims]
    return result,ledger,diagnostics


def accepted(claim):
    return freeze_source_dimensions(replace(claim,binding_status='agreed'))


def quotation_claims(claims):
    return [claim for claim in claims if validated_table_reference(claim)]


def test_six_original_occurrences_have_their_cited_business_and_mode_identity():
    claims,ledger,diagnostics=linked()
    quoted=quotation_claims(claims)
    assert len(quoted)==6 and not diagnostics
    assert len(ledger.entries)>6  # The adjacent figure and tick ledger survive.
    assert [(claim.account,claim.period) for claim in quoted]==[
        ('REVENUE','2020Y'),('REVENUE','2021Y'),('REVENUE','2022Y'),
        ('REVENUE_SHARE','2020Y'),('REVENUE_SHARE','2021Y'),('REVENUE_SHARE','2022Y')]
    facts=source_facts()
    assert len(facts)==34  # The two 2020 AI-cloud dashes are absent, never zero.
    assert all(fact.scope=='未知' and fact.definition_id==DEFINITION for fact in facts)
    judge=Judge(FactStore(facts),CUTOFF,require_dual_binding=True)
    results=[judge.verify(accepted(claim)) for claim in quoted]
    assert [result.status for result in results]==['verified']*6
    for claim,result in zip(quoted,results):
        assert result.verification_basis=='explicit_disclosed_table_comparison'
        assert result.rule_ids==['E1-value']
        assert result.operands[0]['dimension_id']=='并行通用超算云/共建模式'
        assert all(':p262:' in item for item in result.evidence)
        assert not result.calculation_steps
        if claim.account=='REVENUE_SHARE':
            assert result.advisories==['A4']
            assert any('分母' in basis and '未' in basis for basis in result.basis)
        else:assert not result.advisories


def test_native_two_role_bindings_use_compound_dimension_without_candidate_values():
    quoted=quotation_claims(linked()[0])
    dispatcher=FinancialDispatcher('sample','lock',quoted,source_facts(),[],reports(),CUTOFF,
        task_company=DATA['company'])
    for role in ('planner','verifier'):
        for claim in quoted:
            view=dispatcher.mention_context(claim,blind=True,compact=True)
            assert view['source_dimensions']['definition_id']==DEFINITION
            assert view['source_dimensions']['dimension_id']=='并行通用超算云/共建模式'
            assert claim.numeric_text not in view['text']
            dispatcher.bindings[role][claim.claim_id]={'company':DATA['company'],
                'account':claim.account,'period':claim.period,'scope':'来源表','relation':'value','base_period':''}
    bound=dispatcher.freeze_bindings()
    assert all(dispatcher.agreements[claim.claim_id]['agreed'] for claim in bound)
    assert all(source_binding_issue(claim,strict=True) is None for claim in bound)


@pytest.mark.parametrize('old,new', [('4437.80','9437.80'),('78.53%','68.53%')])
def test_wrong_original_amount_or_percentage_is_detected_without_source_reselection(old,new):
    page=rewritten(DATA['draft_page'],old,new)
    quoted=quotation_claims(linked(page)[0])
    assert len(quoted)==6
    result=Judge(FactStore(source_facts()),CUTOFF,require_dual_binding=True).verify(
        accepted(next(claim for claim in quoted if new in claim.numeric_text)))
    assert result.status=='violated' and result.error_type=='E1'
    assert result.correction and len(result.correction)>=5
    assert all(validated_table_reference(claim)['source_doc']==DATA['source']['sha256'][:12] for claim in quoted)


@pytest.mark.parametrize('old,new', [
    ('公司2022 年以来','乙公司2022 年以来'),
    ('公司2022 年以来','公司与乙公司2022 年以来'),
    ('公司2022 年以来','公司子公司2022 年以来'),
    ('2020-2022 年共建','2020-2023 年共建'),
    ('以并行通用超算云为例','以并行行业云模拟为例'),
    ('占比分别为','占公司总营收的比例分别为'),
    ('数据来源：公司第一轮问询函回复','数据来源：公司第二轮问询函回复'),
    ('数据来源：公司第一轮问询函回复','数据来源：未引用公司第一轮问询函回复'),
    ('数据来源：公司第一轮问询函回复','数据来源：公司第一轮问询函回复（旧版）'),
])
def test_adjacent_subject_period_denominator_and_citation_counterexamples_do_not_borrow_the_grid(old,new):
    page=rewritten(DATA['draft_page'],old,new)
    claims,_,_=linked(page)
    assert not quotation_claims(claims)


@pytest.mark.parametrize('field,value', [
    ('publish_date','2024-04-23'),('publish_date',''),('companies',['乙公司']),
    ('name','并行科技关于第二轮问询回复.pdf'),('sha256','a'*64),
])
def test_unknown_future_or_different_disclosure_never_authorizes_a_quotation(field,value):
    registry=reports();registry[0][field]=value
    assert not quotation_claims(linked(registry=registry)[0])


@pytest.mark.parametrize('field,value', [
    ('dimension_id','并行行业云/共建模式'),('definition_id','metric:revenue-share'),
    ('account_hint','NP'),('period_hint','2023Y'),('relation_hint','yoy'),
])
def test_tampered_compound_or_share_definition_cannot_reuse_a_frozen_quotation(field,value):
    claim=accepted(quotation_claims(linked()[0])[0])
    changed=replace(claim,table_context={**claim.table_context,field:value})
    assert validated_table_reference(changed) is None
    assert Judge(FactStore(source_facts()),CUTOFF,require_dual_binding=True).verify(changed).status=='abstained'


def test_missing_one_official_cell_does_not_remove_other_periods_or_replace_it_with_zero():
    source=deepcopy(DATA['source'])
    source['pages'][0]=rewritten(source['pages'][0],'13,166.95','-')
    facts=source_facts(source)
    assert not any(fact.dimension_id=='并行通用超算云/共建模式' and fact.account=='REVENUE'
                   and fact.period=='2022Y' for fact in facts)
    quoted=quotation_claims(linked(facts=facts)[0])
    assert len(quoted)==6
    results=[Judge(FactStore(facts),CUTOFF,require_dual_binding=True).verify(accepted(claim)) for claim in quoted]
    assert [result.status for result in results]==['verified','verified','abstained','verified','verified','verified']
    assert results[2].abstention_code=='X1'


def test_matching_older_disclosure_cannot_rescue_a_changed_latest_source():
    facts=source_facts()
    newer=[replace(fact,fact_id=fact.fact_id+'-new',source_doc='b'*12,
        publish_date='2023-04-05',value=fact.value+(Decimal('.01') if fact.account=='REVENUE_SHARE' else Decimal('10000')),
        evidence=tuple(e.replace(DATA['source']['sha256'][:12], 'b'*12) for e in fact.evidence),
        source_table_evidence=tuple(e.replace(DATA['source']['sha256'][:12], 'b'*12) for e in fact.source_table_evidence)) for fact in facts]
    registry=reports()+[{**reports()[0],'sha256':'b'*64,'publish_date':'2023-04-05','report_id':'report-'+'b'*20}]
    quoted=quotation_claims(linked(facts=[*facts,*newer],registry=registry)[0])
    assert len(quoted)==6
    assert all(validated_table_reference(claim)['source_doc']=='b'*12 for claim in quoted)
    assert Judge(FactStore([*facts,*newer]),CUTOFF,require_dual_binding=True).verify(accepted(quoted[0])).status=='violated'


def test_different_issuer_or_count_unit_header_cannot_create_revenue_source_facts():
    source=deepcopy(DATA['source'])
    source['pages'][0]=rewritten(source['pages'][0],'关于北京并行科技股份有限公司公开发行股票',
        '关于北京并行科技股份有限公司及乙公司公开发行股票')
    assert not source_facts(source)


@pytest.mark.parametrize('old,new', [
    ('过渡，以并行通用超算云为例','过渡，乙公司以并行通用超算云为例'),
    ('过渡，以并行通用超算云为例','过渡，公司子公司以并行通用超算云为例'),
    ('过渡，以并行通用超算云为例','过渡，并非真实披露，以并行通用超算云为例'),
    ('2020-2022 年共建','2025-2027 年共建'),
    ('81.27%，随着','81.27%（本处占比分母为公司总营收），随着'),
    ('图43：2022 年并行通用超算云','图43：2022 年乙公司并行通用超算云'),
    ('图43：2022 年并行通用超算云','图43：2022 年并行科技与乙公司并行通用超算云'),
    ('数据来源：公司第一轮问询函回复','图44：邻接图\n数据来源：公司第一轮问询函回复'),
])
def test_local_subject_switch_denial_future_definition_and_neighbor_citation_cannot_be_borrowed(old,new):
    page=rewritten(DATA['draft_page'],old,new)
    assert not quotation_claims(linked(page)[0])


@pytest.mark.parametrize('header', [
    '关于北京并行科技股份有限公司子公司公开发行股票并在北交所上市申请文件的审核问询函的回复',
    '关于乙公司公开发行股票并在北交所上市申请文件的审核问询函的回复（涉及关于北京并行科技股份有限公司公开发行股票）',
    '某公司引用关于北京并行科技股份有限公司公开发行股票并在北交所上市申请文件的审核问询函的回复',
    '关于839493公开发行股票并在北交所上市申请文件的审核问询函的回复',
])
def test_source_issuer_is_a_complete_registered_legal_header_not_a_body_substring(header):
    source=deepcopy(DATA['source'])
    source['pages'][0]=rewritten(source['pages'][0],source['pages'][0]['lines'][0]['text'].strip(),header)
    assert not source_facts(source)


def test_header_in_body_and_simulated_caption_do_not_create_source_facts():
    source=deepcopy(DATA['source'])
    source['pages'][0]['lines'][0]['bbox']=[117,95,482,105]
    assert not source_facts(source)
    source=deepcopy(DATA['source'])
    source['pages'][0]=rewritten(source['pages'][0],'3、结合细分业务收入结构','3、模拟结合细分业务收入结构')
    assert not source_facts(source)


def test_same_day_conflicting_documents_are_source_ambiguous_and_not_selected_by_amount():
    facts=source_facts()
    other=[replace(fact,fact_id=fact.fact_id+'-other',source_doc='b'*12,
        value=fact.value+Decimal('10000')) for fact in facts]
    registry=reports()+[{**reports()[0],'sha256':'b'*64,'report_id':'report-'+'b'*20}]
    claims,ledger,diagnostics=linked(facts=[*facts,*other],registry=registry)
    assert not quotation_claims(claims)
    assert diagnostics[0]['kind']=='ambiguous_quoted_business_mode_table'
    assert len(ledger.entries)>6


def test_same_document_conflicting_value_is_x4_not_formula_or_matching_cell_fallback():
    facts=source_facts()
    original=next(fact for fact in facts if fact.dimension_id=='并行通用超算云/共建模式'
        and fact.account=='REVENUE' and fact.period=='2020Y')
    facts.append(replace(original,fact_id=original.fact_id+'-conflict',value=original.value+Decimal('10000')))
    claim=accepted(quotation_claims(linked(facts=facts)[0])[0])
    result=Judge(FactStore(facts),CUTOFF,require_dual_binding=True).verify(claim)
    assert result.status=='abstained' and result.abstention_code=='X4'
    assert not result.calculation_steps


@pytest.mark.parametrize('change', [
    {'source_numeric_start':None}, {'source_numeric_start':0},
    {'numeric_text':'999999万元'}, {'start':0,'end':10},
])
def test_unlocatable_numeric_occurrence_cannot_reuse_a_verified_reference(change):
    claim=accepted(quotation_claims(linked()[0])[0])
    changed=replace(claim,**change)
    assert validated_table_reference(changed) is None
    assert Judge(FactStore(source_facts()),CUTOFF,require_dual_binding=True).verify(changed).status=='abstained'


def test_unrelated_negation_in_previous_figure_footer_does_not_delete_a_valid_paragraph():
    page=rewritten(DATA['draft_page'],'数据来源：公司公告、开源证券研究所',
        '数据来源：公司公告、开源证券研究所（其他资料未引用）')
    quoted=quotation_claims(linked(page)[0])
    assert len(quoted)==6
    assert all(Judge(FactStore(source_facts()),CUTOFF,require_dual_binding=True).verify(accepted(claim)).status=='verified'
        for claim in quoted)
    source=deepcopy(DATA['source'])
    source['pages'][0]=rewritten(source['pages'][0],'单位：万元','单位：个')
    assert not source_facts(source)
