"""Four unchanged quarterly financial occurrences and neighboring traps."""
from collections import Counter
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path

import pytest

from mingjing.coverage import CoverageLedger
from mingjing.evidence import make_evidence_id
from mingjing.facts import FactStore
from mingjing.judge import Judge
from mingjing.source_quarter_quotation import (DEFINITION, MODE_DEFINITION, extract_source_facts,
    extract_source_gaps, attach_threshold_references, source_threshold_issue, narrative_identity, KINDS)
from mingjing.source_semantics import freeze_source_dimensions
from mingjing.source_table_reference import link_quoted_tables, validated_table_reference

DATA=json.loads((Path(__file__).parent/'fixtures/quoted_quarter_shares_p30_p31.json').read_text(encoding='utf8'))
CUTOFF='2024-04-22'


def facts(source=None):
    source=source or DATA['source']
    return extract_source_facts(source['pages'],path=source['filename'],company=DATA['company'],
        doc_sha=source['sha256'],disclosure_date=source['publish_date'],disclosure_version='原披露')


def registry():
    source=DATA['source']
    return [{'name':source['filename'],'companies':[DATA['company']],'sha256':source['sha256'],
        'publish_date':source['publish_date'],'report_id':source['id']}]


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
    return text,regions,ledger,extracted


def linked(pages=None,source=None,source_facts=None,reports=None,cutoff=CUTOFF):
    text,pages,ledger,extracted=claims(pages)
    linked,diagnostics=link_quoted_tables(extracted,text,facts(source) if source_facts is None else source_facts,
        registry() if reports is None else reports,cutoff,pages=pages)
    assert [(c.claim_id,c.start,c.end) for c in linked]==[(c.claim_id,c.start,c.end) for c in extracted]
    return ledger,linked,diagnostics


def selected(items):return [c for c in items if (validated_table_reference(c) or {}).get('quotation_kind') in KINDS]
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


def test_four_real_occurrences_keep_different_denominators_and_all_ledger_entries():
    ledger,items,diagnostics=linked();chosen=selected(items)
    assert len(chosen)==4 and not diagnostics
    assert Counter(narrative_identity(c)['quotation_kind'] for c in chosen)=={
        'quarter_revenue_share_narrative':3,'business_mode_quarter_share_caption':1}
    assert len(ledger.entries)>len(items)>4
    outcomes=[Judge(FactStore(facts()),CUTOFF,require_dual_binding=True).verify(accepted(c)) for c in chosen]
    assert [r.status for r in outcomes]==['verified']*4
    assert all(r.evidence and not r.calculation_steps and r.verification_basis=='explicit_disclosed_table_comparison' for r in outcomes)
    shares=[c for c in chosen if narrative_identity(c)['quotation_kind']=='quarter_revenue_share_narrative']
    assert [c.period for c in shares]==['2020Q4','2021Q4','2022Q4']
    assert all(c.table_context['definition_id']==DEFINITION for c in shares)
    assert all(any(':p183:' in e for e in r.evidence) for r in outcomes[:3])
    mode=chosen[-1];assert mode.table_context['definition_id']==MODE_DEFINITION
    assert mode.numeric_text=='75%' and mode.display_decimals==0
    assert outcomes[-1].operands[0]['value']=='0.7461'
    assert any(':p182:' in e for e in outcomes[-1].evidence)


def test_source_cells_are_addressed_by_year_quarter_column_and_independent_definitions():
    rows=facts();assert len(rows)==27
    assert len([f for f in rows if f.definition_id==DEFINITION])==3
    assert len([f for f in rows if f.definition_id==MODE_DEFINITION])==24
    assert all(f.scope=='未知' for f in rows)
    assert {f.period for f in rows if f.definition_id==MODE_DEFINITION}=={f'{y}Q{q}' for y in range(2020,2023) for q in range(1,5)}
    originals={make_evidence_id(DATA['source']['sha256'],p['page'],b['text'].strip(),b['bbox'])
        for p in DATA['source']['pages'] for b in p['blocks']}
    assert all(set(f.evidence)<=originals and set(f.source_table_evidence)<=originals for f in rows)


@pytest.mark.parametrize('old,new',[('37.56%','47.56%'),('35.96%','25.96%'),('31.89%','41.89%'),('达到75%','达到65%')])
def test_wrong_source_quoted_numbers_do_not_choose_different_period_or_mode(old,new):
    chosen=selected(linked(rewritten(DATA['draft_pages'],old,new))[1]);assert len(chosen)==4
    changed=new.removeprefix('达到')
    claim=next(c for c in chosen if c.numeric_text==changed)
    result=Judge(FactStore(facts()),CUTOFF,require_dual_binding=True).verify(accepted(claim))
    assert result.status=='violated' and result.error_type=='E1' and result.correction


@pytest.mark.parametrize('old,new',[
    ('公司逐步加大了','乙公司逐步加大了'),('公司逐步加大了','子公司逐步加大了'),
    ('公司逐步加大了','预计公司逐步加大了'),('由于公司超算云服务','由于乙公司超算云服务'),
    ('占超算云服务全年收','占公司全年收'),('2020-2022 年第四季度','2020-2025 年第四季度'),
    ('公司第二轮问询函回复','公司第一轮问询函回复'),('公司第二轮问询函回复','未引用公司第二轮问询函回复'),
    ('共建模式收入占比逐渐提高','共建模式毛利率逐渐提高'),
])
def test_subject_period_method_family_and_denied_source_cannot_be_replaced_by_matching_values(old,new):
    ledger,items,_=linked(rewritten(DATA['draft_pages'],old,new))
    assert not selected(items) and len(ledger.entries)>4


@pytest.mark.parametrize('old,new',[
    ('关于北京并行科技股份有限公司','关于乙公司股份有限公司'),
    ('第二轮审核问询函的回复','第一轮审核问询函的回复'),
    ('2020 年、2021 年和2022 年第四季度','2019 年、2021 年和2022 年第四季度'),
    ('2022 年第一季度','2023 年第一季度'),
    ('共建模式收','共建模式成本'),
])
def test_source_cover_chapter_years_quarter_rows_and_revenue_headers_are_required(old,new):
    source=deepcopy(DATA['source']);source['pages']=rewritten(source['pages'],old,new)
    assert not facts(source)


def test_lost_continuation_period_context_does_not_remove_valid_mode_grid():
    source=deepcopy(DATA['source']);source['pages']=rewritten(source['pages'],
        '共建模式的主要成本为固定资产折旧、IDC 租赁费等，与外购模式按实际消耗',
        '另一公司的主要成本为固定资产折旧、IDC 租赁费等，与外购模式按实际消耗')
    rows=facts(source);assert len(rows)==24 and all(f.definition_id==MODE_DEFINITION for f in rows)


def test_missing_one_mode_cell_retains_other_26_and_never_supplies_zero():
    source=deepcopy(DATA['source']);page=next(p for p in source['pages'] if p['page']==182)
    cell=next(l for l in page['lines'] if l['text'].strip()=='74.61%')
    page['lines'].remove(cell)
    rows=facts(source);assert len(rows)==26
    assert not any(f.period=='2022Q4' and f.dimension_id=='超算云服务/共建模式' for f in rows)
    chosen=selected(linked(source_facts=rows)[1]);assert len(chosen)==3
    original=next(c for c in linked(source_facts=rows)[1] if c.numeric_text=='75%')
    assert original.table_context['definition_id']==MODE_DEFINITION
    assert not validated_table_reference(original)


def test_missing_or_after_cutoff_disclosure_is_visible_not_substituted():
    assert not selected(linked(reports=[{**registry()[0],'publish_date':'2024-04-23'}])[1])
    assert not selected(linked(cutoff='2023-04-03')[1])
    ledger,items,diagnostics=linked(source_facts=[])
    assert not selected(items) and len(ledger.entries)>4
    assert len([d for d in diagnostics if d['kind']=='missing_quarter_source_table'])==4


def test_same_day_duplicate_documents_are_not_selected_by_matching_percentage():
    source_facts=facts();other=[replace(f,source_doc='a'*12,fact_id=f.fact_id+'-other',value=f.value+Decimal('1')) for f in source_facts]
    reports=registry()+[{**registry()[0],'sha256':'a'*64,'report_id':'report-'+'a'*20}]
    assert not selected(linked(source_facts=[*source_facts,*other],reports=reports)[1])


def test_signed_source_proof_cannot_authorize_a_forged_period_or_denominator():
    chosen=selected(linked()[1]);assert len(chosen)==4
    original=chosen[0]
    for changed in [replace(original,period='2021Q4'),replace(original,company='乙公司'),
                    replace(original,table_context={**original.table_context,'definition_id':MODE_DEFINITION})]:
        assert not validated_table_reference(changed)


def test_source_amount_mutation_changes_verdict_without_source_identity_change():
    source=deepcopy(DATA['source']);source['pages']=rewritten(source['pages'],'74.61%','64.61%')
    chosen=selected(linked(source=source)[1]);assert len(chosen)==4
    result=Judge(FactStore(facts(source)),CUTOFF,require_dual_binding=True).verify(accepted(chosen[-1]))
    assert result.status=='violated' and result.error_type=='E1'


@pytest.mark.parametrize('version', ['same','different'])
def test_source_conflict_or_different_version_stays_x4_without_value_selection(version):
    rows=facts();original=next(f for f in rows if f.definition_id==DEFINITION and f.period=='2020Q4')
    rows.append(replace(original,fact_id=original.fact_id+'-conflict',value=original.value+Decimal('.1'),
        version=original.version if version=='same' else '更正后'))
    chosen=selected(linked(source_facts=rows)[1]);assert len(chosen)==4
    result=Judge(FactStore(rows),CUTOFF,require_dual_binding=True).verify(accepted(chosen[0]))
    assert result.status=='abstained' and result.abstention_code=='X4'


def test_unknown_disclosure_date_cannot_authorize_an_original_quotation():
    assert not selected(linked(reports=[{**registry()[0],'publish_date':''}])[1])


def test_unchanged_original_values_are_reconstructed_not_taken_from_candidate_field():
    text,pages,_,extracted=claims();forged=[replace(c,candidate_value='999999') for c in extracted]
    linked_items,_=link_quoted_tables(forged,text,facts(),registry(),CUTOFF,pages=pages)
    chosen=selected(linked_items);assert len(chosen)==4
    assert [c.candidate_value for c in chosen]==['0.3756','0.3596','0.3189','0.75']
    assert all(Judge(FactStore(facts()),CUTOFF,require_dual_binding=True).verify(accepted(c)).status=='verified' for c in chosen)


def test_actual_source_p183_wrong_percentage_is_e1_at_original_2021_period():
    source=deepcopy(DATA['source']);source['pages']=rewritten(source['pages'],'35.96%','25.96%')
    chosen=selected(linked(source=source)[1]);assert len(chosen)==4
    claim=next(c for c in chosen if c.period=='2021Q4' and c.table_context['definition_id']==DEFINITION)
    result=Judge(FactStore(facts(source)),CUTOFF,require_dual_binding=True).verify(accepted(claim))
    assert result.status=='violated' and result.error_type=='E1' and result.expected=='0.2596'


def test_nonconsecutive_source_page_cannot_lend_periods_to_its_narrative():
    source=deepcopy(DATA['source']);next(p for p in source['pages'] if p['page']==183)['page']=184
    rows=facts(source);assert len(rows)==24 and all(f.definition_id==MODE_DEFINITION for f in rows)


def test_another_clause_between_source_continuation_and_seasonality_is_not_admitted():
    source=deepcopy(DATA['source']);source['pages']=rewritten(source['pages'],
        '外部算力结转算力成本相比，具有较为平稳的特点，由于公司超算云服务具有',
        '注：以下仅为子公司收入占比，具有较为平稳的特点，由于公司超算云服务具有')
    rows=facts(source);assert len(rows)==24 and all(f.definition_id==MODE_DEFINITION for f in rows)


def test_subsequent_unrelated_prediction_does_not_discard_four_historical_occurrences():
    pages=rewritten(DATA['draft_pages'],'公司2022 年以来大力开展','预计公司2022 年以来大力开展')
    assert len(selected(linked(pages)[1]))==4


def test_literal_lower_bound_has_provenance_but_never_point_fact_or_numeric_model_metadata():
    source=DATA['source']
    gaps=extract_source_gaps(source['pages'],path=source['filename'],company=DATA['company'],
        doc_sha=source['sha256'],disclosure_date=source['publish_date'])
    assert len(gaps)==1 and gaps[0]['period']=='2021Y'
    assert gaps[0]['gap_kind']=='source-threshold-disclosure'
    assert 'raw_value' not in gaps[0] and gaps[0]['eligible_as_fact'] is False
    assert gaps[0]['source_context']['eligible_as_point_fact'] is False
    assert any(':p182:' in e for e in gaps[0]['evidence'])
    text,pages,_,extracted=claims()
    linked_items,_=link_quoted_tables(extracted,text,facts(),registry(),CUTOFF,pages=pages)
    attached=attach_threshold_references(linked_items,text,pages,registry(),CUTOFF,gaps)
    lower=next(c for c in attached if c.numeric_text=='70%')
    from mingjing.source_quarter_quotation import validated_threshold_reference
    assert validated_threshold_reference(lower) and source_threshold_issue(lower) is None
    assert not validated_table_reference(lower)
    assert len(selected(attached))==4
    assert all(not(f.period=='2021Y' and f.value==Decimal('.7')) for f in facts())
    proof=lower.table_context['source_threshold_reference']
    assert source_threshold_issue(replace(lower,table_context={**lower.table_context,
        'source_threshold_reference':{**proof,'period':'2022Y'}})) is None
    assert source_threshold_issue(replace(lower,company='乙公司')) is None
    assert source_threshold_issue(replace(lower,numeric_text='75%')) is None


def test_future_or_missing_threshold_provenance_cannot_label_draft_as_source_supported():
    source=DATA['source'];gaps=extract_source_gaps(source['pages'],path=source['filename'],company=DATA['company'],
        doc_sha=source['sha256'],disclosure_date=source['publish_date'])
    text,pages,_,extracted=claims();linked_items,_=link_quoted_tables(extracted,text,facts(),registry(),CUTOFF,pages=pages)
    for bad in ([],[{**gaps[0],'publish_date':'2024-04-23'}], [{**gaps[0],'doc_sha256':'b'*64}]):
        attached=attach_threshold_references(linked_items,text,pages,registry(),CUTOFF,bad)
        assert not any(source_threshold_issue(c) for c in attached)
