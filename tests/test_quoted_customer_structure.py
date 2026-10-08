"""Eight unchanged financial occurrences and neighboring semantic traps."""
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path

import pytest

from mingjing.coverage import CoverageLedger
from mingjing.facts import FactStore
from mingjing.financial_notes import _extract_major_customer_facts
from mingjing.financial_tools import FinancialDispatcher
from mingjing.judge import Judge
from mingjing.source_customer_quotation import DEFINITION, extract_source_facts, identity
from mingjing.source_semantics import freeze_source_dimensions, source_binding_issue
from mingjing.source_table_reference import link_quoted_tables, validated_table_reference

DATA=json.loads((Path(__file__).parent/'fixtures/quoted_customer_structure_p13_p14.json').read_text(encoding='utf-8'))
CUTOFF='2024-04-22'


def facts(source=None):
    source=source or DATA['source']
    kw=dict(path=source['filename'],company=DATA['company'],doc_sha=source['sha256'],
        disclosure_date=source['publish_date'],disclosure_version='原披露')
    return [*extract_source_facts(source['pages'],**kw),
        *_extract_major_customer_facts(source['pages'],effective_period='2023H1',**kw)]


def registry():
    source=DATA['source']
    return [{'name':source['filename'],'companies':[DATA['company']],'sha256':source['sha256'],
        'publish_date':source['publish_date'],'report_id':source['id'],
        'source_document_variant':source['source_document_variant']}]


def claims(pages=None):
    pages=deepcopy(pages or DATA['draft_pages']);text='';regions=[];blocks=[]
    for page in pages:
        offset=len(text);text+=page['text']+'\n'
        regions.append({**page,'id':f'page-{page["page"]}','start':offset,'end':offset+len(page['text']),
            'lines':[{**line,'start':offset+line['start'],'end':offset+line['end']} for line in page['lines']]})
        cursor=0
        for i,block in enumerate(page['blocks']):
            start=page['text'].find(block['text'],cursor)
            if start<0:continue
            cursor=start+len(block['text'])
            blocks.append({**block,'id':f'block-{page["page"]}-{i}','page':page['page'],
                'page_id':f'page-{page["page"]}','start':offset+start,'end':offset+cursor})
    ledger,extracted=CoverageLedger.build(text,regions,blocks,DATA['company'],'2023Y')
    return text,ledger,extracted


def linked(pages=None,source=None,source_facts=None,reports=None):
    text,ledger,extracted=claims(pages)
    linked,diagnostics=link_quoted_tables(extracted,text,facts(source) if source_facts is None else source_facts,
        registry() if reports is None else reports,CUTOFF)
    assert [(c.claim_id,c.start,c.end) for c in linked]==[(c.claim_id,c.start,c.end) for c in extracted]
    return ledger,linked,diagnostics


def selected(claims):
    return [c for c in claims if (validated_table_reference(c) or {}).get('quotation_kind') in {
        'customer_concentration_narrative','customer_type_narrative','customer_type_chart'}]


def accepted(claim):return freeze_source_dimensions(replace(claim,binding_status='agreed'))


def rewritten(pages,old,new):
    pages=deepcopy(pages)
    for page in pages:
        page['text']=page['text'].replace(old,new);cursor=0
        for line in page['lines']:
            line['text']=line['text'].replace(old,new)
            start=page['text'].find(line['text'],cursor);assert start>=0
            line.update(start=start,end=start+len(line['text']));cursor=line['end']
        for block in page['blocks']:block['text']=block['text'].replace(old,new)
    return pages


def test_eight_real_unchanged_occurrences_retain_distinct_denominators_and_all_ledger_entries():
    ledger,linked_claims,diagnostics=linked()
    chosen=selected(linked_claims)
    assert len(chosen)==8 and not diagnostics
    assert len(ledger.entries)>len(linked_claims)>8
    assert Counter(identity(c)['quotation_kind'] for c in chosen)=={
        'customer_concentration_narrative':2,'customer_type_narrative':2,'customer_type_chart':4}
    outcomes=[Judge(FactStore(facts()),CUTOFF,require_dual_binding=True).verify(accepted(c)) for c in chosen]
    assert [r.status for r in outcomes]==['verified']*8
    assert all(r.evidence and r.verification_basis=='explicit_disclosed_table_comparison' and not r.calculation_steps for r in outcomes)
    for c,r in zip(chosen,outcomes):
        if identity(c)['quotation_kind']=='customer_concentration_narrative':
            assert r.advisories==[]
            assert all(any(':p205:' in e or ':p416:' in e for e in operand['evidence']) for operand in r.operands)
        else:
            assert r.advisories==['A4']
            assert any('超算云服务收入' in b and '分母' in b for b in r.basis)
            assert all(operand['definition_id']==DEFINITION for operand in r.operands)
            assert any(':p203:' in e for e in r.evidence)


def test_chart_uses_source_swatch_bar_and_period_even_when_labels_are_wrong():
    pages=rewritten(DATA['draft_pages'],'7.93%','37.93%')
    chosen=selected(linked(pages)[1]);chart=[c for c in chosen if identity(c)['quotation_kind']=='customer_type_chart']
    assert len(chart)==4
    wrong=next(c for c in chart if c.numeric_text=='37.93%')
    assert wrong.period=='2020Y' and wrong.table_context['dimension_id']=='企业'
    result=Judge(FactStore(facts()),CUTOFF,require_dual_binding=True).verify(accepted(wrong))
    assert result.status=='violated' and result.error_type=='E1'


@pytest.mark.parametrize('old,new', [('26.44%','36.44%'),('16.7%','26.7%'),('0.07%','0.97%')])
def test_wrong_narrative_values_are_e1_without_source_reselection(old,new):
    chosen=selected(linked(rewritten(DATA['draft_pages'],old,new))[1])
    wrong=next(c for c in chosen if c.numeric_text==new)
    result=Judge(FactStore(facts()),CUTOFF,require_dual_binding=True).verify(accepted(wrong))
    assert result.status=='violated' and result.error_type=='E1' and result.correction


@pytest.mark.parametrize('change', ['missing_colour','wrong_swatch','moved_label','moved_year','duplicate_bar'])
def test_matching_chart_numbers_cannot_override_colour_and_geometry_failures(change):
    pages=deepcopy(DATA['draft_pages']);page=pages[1]
    enterprise=next(l for l in page['lines'] if l['text'].strip()=='企业')
    marker=next(s for s in page['vector_rectangles'] if 0<=enterprise['bbox'][0]-s['bbox'][2]<=8
        and s['bbox'][3]-s['bbox'][1]<=8 and abs((s['bbox'][1]+s['bbox'][3])-(enterprise['bbox'][1]+enterprise['bbox'][3]))<=6)
    if change=='missing_colour':page['vector_rectangles']=[]
    elif change=='wrong_swatch':marker['fill']=[.123,.456,.789]
    elif change=='moved_label':
        line=next(l for l in page['lines'] if l['text'].strip()=='7.93%');line['bbox']=[x+40 if i%2==0 else x for i,x in enumerate(line['bbox'])]
    elif change=='moved_year':
        line=next(l for l in page['lines'] if l['text'].strip()=='2020');line['bbox']=[x+40 if i%2==0 else x for i,x in enumerate(line['bbox'])]
    else:
        bar=next(s for s in page['vector_rectangles'] if s['fill']==marker['fill'] and s['bbox'][2]-s['bbox'][0]>15)
        page['vector_rectangles'].append(deepcopy(bar))
    chart=[c for c in selected(linked(pages)[1]) if identity(c)['quotation_kind']=='customer_type_chart']
    assert len(chart)<4


@pytest.mark.parametrize('old,new', [
    ('公司整体客户集中度较低','乙公司整体客户集中度较低'),
    ('前五大客户营收占总营收','前五大客户数量占总数量'),
    ('相比2022 年的','相比2025 年的'),
    ('服务群体主要为','乙公司服务群体主要为'),
    ('数据来源：公司招股书','数据来源：未引用公司招股书'),
    ('图19：公司主要客户','图19：乙公司主要客户'),
    ('图19：公司主要客户','图19：公司与乙公司主要客户'),
])
def test_other_subject_period_definition_and_denied_source_remain_visible(old,new):
    ledger,items,_=linked(rewritten(DATA['draft_pages'],old,new))
    chosen=selected(items)
    if old.startswith('公司整体') or old.startswith('前五大') or old.startswith('相比'):
        assert not any(identity(c)['quotation_kind']=='customer_concentration_narrative' for c in chosen)
    else:assert not any(identity(c)['quotation_kind'].startswith('customer_type') for c in chosen)
    assert len(ledger.entries)>8


def test_type_table_source_cover_identity_units_and_local_heading_are_required():
    assert len([f for f in facts() if f.definition_id==DEFINITION])==40
    for old,new in [('北京并行科技股份有限公司','乙公司股份有限公司'),('按客户类型','按客户数量'),
                    ('（万元）','（人数）')]:
        source=deepcopy(DATA['source']);source['pages']=rewritten(source['pages'],old,new)
        assert not extract_source_facts(source['pages'],path=source['filename'],company=DATA['company'],
            doc_sha=source['sha256'],disclosure_date=source['publish_date'],disclosure_version='原披露')


def test_every_customer_type_evidence_id_roundtrips_to_an_original_pdf_block():
    from mingjing.evidence import make_evidence_id
    source=DATA['source']
    original_ids={make_evidence_id(source['sha256'],page['page'],block['text'].strip(),block['bbox'])
        for page in source['pages'] for block in page['blocks']}
    typed=[f for f in facts() if f.definition_id==DEFINITION]
    assert len(typed)==40
    assert all(set(f.evidence)<=original_ids and set(f.source_table_evidence)<=original_ids for f in typed)


@pytest.mark.parametrize('prefix', ['甲','乙','子','公司与乙','预计','假设','并非'])
def test_concentration_source_match_cannot_drop_a_prefix_to_invent_task_issuer(prefix):
    pages=rewritten(DATA['draft_pages'],'公司整体客户集中度较低',prefix+'公司整体客户集中度较低')
    assert not any(identity(c)['quotation_kind']=='customer_concentration_narrative' for c in selected(linked(pages)[1]))


@pytest.mark.parametrize('prefix', ['乙公司','公司子公司','预计','假设','并非'])
def test_type_paragraph_source_match_cannot_drop_a_prefix_to_invent_task_issuer(prefix):
    pages=rewritten(DATA['draft_pages'],'服务群体主要为',prefix+'服务群体主要为')
    assert not any(identity(c)['quotation_kind'].startswith('customer_type') for c in selected(linked(pages)[1]))


def test_missing_source_cell_keeps_other_seven_and_never_zero():
    source_facts=[f for f in facts() if not(f.definition_id==DEFINITION and f.dimension_id=='企业'
        and f.account=='REVENUE_SHARE' and f.period=='2020Y')]
    chosen=selected(linked(source_facts=source_facts)[1]);assert len(chosen)==8
    results=[Judge(FactStore(source_facts),CUTOFF,require_dual_binding=True).verify(accepted(c)) for c in chosen]
    assert Counter(r.status for r in results)=={'verified':7,'abstained':1}
    assert next(r for r in results if r.status=='abstained').abstention_code=='X1'


def test_same_day_two_sources_cannot_be_selected_by_matching_values():
    source_facts=facts();other=[replace(f,source_doc='a'*12,fact_id=f.fact_id+'-other',value=f.value+Decimal('1')) for f in source_facts]
    reports=registry()+[{**registry()[0],'sha256':'a'*64,'report_id':'report-'+'a'*20}]
    chosen=selected(linked(source_facts=[*source_facts,*other],reports=reports)[1])
    assert not chosen


@pytest.mark.parametrize('variant', ['注册稿','旧版'])
def test_explicit_wrong_source_variant_does_not_use_misleading_filename(variant):
    pages=rewritten(DATA['draft_pages'],'公司招股书','公司招股书（'+variant+'）')
    assert not selected(linked(pages)[1])


def test_dual_binding_metadata_has_period_type_definition_without_original_value():
    chosen=selected(linked()[1]);dispatcher=FinancialDispatcher('sample','lock',chosen,facts(),[],registry(),CUTOFF,
        task_company=DATA['company'])
    for role in ('planner','verifier'):
        for claim in chosen:
            view=dispatcher.mention_context(claim,blind=True,compact=True)
            assert view['source_dimensions']['dimension_id']==claim.table_context['dimension_id']
            assert claim.numeric_text not in view['text']
            dispatcher.bindings[role][claim.claim_id]={'company':DATA['company'],'account':'REVENUE_SHARE',
                'period':claim.period,'scope':'来源表','relation':'value','base_period':''}
    bound=dispatcher.freeze_bindings()
    assert all(dispatcher.agreements[c.claim_id]['agreed'] and source_binding_issue(c,strict=True) is None for c in bound)
