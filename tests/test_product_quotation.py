"""All five original p7 paragraph/p8 caption observations, no count pruning."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path

import pytest

from mingjing.coverage import CoverageLedger
from mingjing.facts import FactStore
from mingjing.judge import Judge
from mingjing.source_product_financial import (extract_source_facts,link,narrative_identity,
    allowed_reference,validate_reference_bounds)
from mingjing.source_semantics import freeze_source_dimensions,source_binding_issue
from mingjing.source_table_reference import validated_table_reference

FIXTURES=Path(__file__).parent/'fixtures'
SOURCE=json.loads((FIXTURES/'product_financial_source_16.json').read_text(encoding='utf8'))
DRAFT=json.loads((FIXTURES/'product_quotation_p7_p8.json').read_text(encoding='utf8'))


def facts(sources=None):
    return [fact for source in (sources or SOURCE) for fact in extract_source_facts(source['pages'],
        path=source['filename'],company=source['company'],doc_sha=source['doc_sha256'],
        disclosure_date=source['publish_date'],disclosure_version='原披露')]


def registry():
    return [dict(name=s['filename'],companies=[s['company']],sha256=s['doc_sha256'],
        publish_date=s['publish_date'],report_id='report-'+s['doc_sha256'][:20]) for s in SOURCE]


def extracted(pages=None):
    text='';regions=[];blocks=[]
    for page in (DRAFT['draft_pages'] if pages is None else pages):
        offset=len(text);page_id='page-'+str(page['page'])
        regions.append({**page,'id':page_id,'start':offset,'end':offset+len(page['text']),
            'lines':[{**line,'start':offset+line['start'],'end':offset+line['end']} for line in page['lines']]})
        cursor=0
        for index,block in enumerate(page['blocks']):
            start=page['text'].find(block['text'],cursor)
            if start<0:continue
            cursor=start+len(block['text'])
            blocks.append({**block,'start':offset+start,'end':offset+cursor,'page':page['page'],
                'page_id':page_id,'id':page_id+'-block-'+str(index)})
        text+=page['text']+'\n'
    ledger,claims=CoverageLedger.build(text,regions,blocks,DRAFT['company'],'2023Y')
    return text,regions,ledger,claims


def linked(pages=None,source_facts=None,reports=None):
    text,regions,ledger,claims=extracted(pages)
    rows,diagnostics=link(claims,text,facts() if source_facts is None else source_facts,
        registry() if reports is None else reports,DRAFT['cutoff'],pages=regions)
    assert [(c.claim_id,c.start,c.end) for c in rows]==[(c.claim_id,c.start,c.end) for c in claims]
    assert len(rows)==len(claims) and len(rows)>=5
    return rows,ledger,diagnostics


def accepted(claim):return freeze_source_dimensions(replace(claim,binding_status='agreed'))


def results(rows,source_facts=None):
    judge=Judge(FactStore(facts() if source_facts is None else source_facts),DRAFT['cutoff'],require_dual_binding=True)
    return [judge.verify(accepted(c)) for c in rows]


def rewritten(old,new):
    pages=deepcopy(DRAFT['draft_pages'])
    for page in pages:
        page['text']=page['text'].replace(old,new)
        for collection in ('lines','blocks'):
            for line in page[collection]:line['text']=line['text'].replace(old,new)
        cursor=0
        for line in page['lines']:
            line['start']=page['text'].find(line['text'],cursor)
            assert line['start']>=0
            line['end']=line['start']+len(line['text']);cursor=line['end']
    return pages


def test_all_five_original_source_occurrences_receive_external_financial_verification():
    rows,ledger,diagnostics=linked()
    assert not diagnostics and len(rows)==5 and len(ledger.entries)>5
    assert all(narrative_identity(c) for c in rows)
    for c in rows:
        proof=c.table_context['source_table_reference']
        assert allowed_reference(proof,c.table_context,c)
        assert validate_reference_bounds(proof,c.table_context,c)
        assert validated_table_reference(c)
        assert source_binding_issue(accepted(c),strict=True) is None
    output=results(rows)
    assert [r.status for r in output]==['verified']*5
    assert [r.verification_basis for r in output]==['explicit_disclosed_table_comparison']*5
    assert all(r.evidence and r.operands for r in output)
    assert rows[0].declared_source==SOURCE[1]['doc_sha256'][:12]
    assert all(c.declared_source==SOURCE[0]['doc_sha256'][:12] for c in rows[1:])


@pytest.mark.parametrize('old,new', [('2.67','9.67'),('55%','99%'),('4.14','9.14')])
def test_wrong_original_values_are_E1_without_reselecting_document_or_definition(old,new):
    rows,_,_=linked(rewritten(old,new))
    baseline=linked()[0]
    assert [c.declared_source for c in rows]==[c.declared_source for c in baseline]
    output=results(rows)
    for claim,result in zip(rows,output):
        expected='violated' if new in claim.numeric_text else 'verified'
        assert result.status==expected
        if expected=='violated':
            assert result.error_type=='E1' and result.correction and len(result.correction)>=5


@pytest.mark.parametrize('old,new',[
    ('公司主营业务','乙科技主营业务'),('公司主营业务','公司与乙公司主营业务'),
    ('从产品收入结构来看','子公司从产品收入结构来看'),
    ('超算云服务','通用超算云'),('数据来源：公司招股书','数据来源：未引用公司招股书'),
    ('公司公告','非公司公告'),('公司招股书','公司招股书（旧版）'),
])
def test_wrong_subject_alias_or_citation_does_not_borrow_product_grid(old,new):
    rows,_,_=linked(rewritten(old,new))
    assert not any(validated_table_reference(c) for c in rows)
    assert not any(r.status=='verified' for r in results(rows))


@pytest.mark.parametrize('field,value',[
    ('publish_date','2024-04-23'),('publish_date',''),('companies',['乙公司']),
    ('sha256','a'*64),('name','无关运营材料.pdf')])
def test_future_unknown_or_other_source_keeps_all_observations_unverified(field,value):
    reports=registry()
    for row in reports:row[field]=value
    rows,_,_=linked(reports=reports)
    assert not any(validated_table_reference(c) for c in rows)
    assert not any(r.status=='verified' for r in results(rows))


def test_missing_2023_cell_is_X1_and_other_four_observations_stay_judged():
    source_facts=[f for f in facts() if not (f.source_doc==SOURCE[0]['doc_sha256'][:12]
        and f.dimension_id=='超算云服务' and f.account=='REVENUE' and f.source_relation=='value')]
    rows,_,_=linked(source_facts=source_facts)
    output=results(rows,source_facts)
    assert [r.status for r in output]==['verified','verified','abstained','verified','abstained']
    assert output[2].abstention_code==output[4].abstention_code=='X1'


def test_conflicting_disclosed_cell_is_X4_not_convenient_value_selection():
    source_facts=facts()
    original=next(f for f in source_facts if f.source_doc==SOURCE[0]['doc_sha256'][:12]
        and f.dimension_id=='超算云服务' and f.account=='REVENUE' and f.source_relation=='value')
    source_facts.append(replace(original,fact_id=original.fact_id+'-conflict',value=original.value+Decimal('100000000')))
    rows,_,_=linked(source_facts=source_facts)
    output=results(rows,source_facts)
    assert output[2].status==output[4].status=='abstained'
    assert output[2].abstention_code==output[4].abstention_code=='X4'


def test_without_original_geometry_and_moved_citation_no_source_approval():
    text,regions,_,claims=extracted()
    rows,_=link(claims,text,facts(),registry(),DRAFT['cutoff'])
    assert not any(validated_table_reference(c) for c in rows)
    pages=deepcopy(DRAFT['draft_pages'])
    citation=next(l for l in pages[1]['lines'] if '数据来源' in l['text'])
    citation['bbox']=[170,60,550,69]
    rows,_,_=linked(pages)
    assert not any(validated_table_reference(c) for c in rows)


def test_model_supplied_candidate_is_rebuilt_only_from_original_numeric_characters():
    text,regions,_,claims=extracted()
    poisoned=[replace(c,candidate_value='999999') for c in claims]
    rows,_=link(poisoned,text,facts(),registry(),DRAFT['cutoff'],pages=regions)
    assert [c.candidate_value for c in rows]==[c.candidate_value for c in linked()[0]]
    assert [r.status for r in results(rows)]==['verified']*5


def test_frozen_product_geometry_tamper_cannot_reuse_signed_reference():
    claim=accepted(linked()[0][0])
    proof=deepcopy(claim.table_context['source_table_reference'])
    proof['source_geometry']['narrative']['bbox'][0]+=5
    forged=replace(claim,table_context={**claim.table_context,'source_table_reference':proof})
    assert validated_table_reference(forged) is None
    assert results([forged])[0].status=='abstained'
