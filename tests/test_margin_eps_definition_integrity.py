"""Original definitions and per-share units, with no provider or human-gold claim."""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pytest

from mingjing.coverage import CoverageLedger
from mingjing.facts import FactStore, load_index_facts
from mingjing.judge import Judge
from mingjing.mentions import extract_mentions, infer_account
from mingjing.models import Fact
from mingjing.replay import replay_run
from mingjing.source_semantics import freeze_source_dimensions, semantic_review_required
from mingjing.table_mentions import extract_table_mentions

from test_agent_lifecycle import lifecycle, new_agent


ROOT = Path(__file__).resolve().parents[1]
SOURCE_OBSERVATIONS = json.loads((ROOT/'tests/fixtures/numeric_semantic_review_03.json').read_text(encoding='utf-8'))


def tables(source, pages=None):
    return extract_table_mentions(source, pages or [{'start':0,'end':len(source),'page':1}], [], '甲公司', '2099Y')


def matching_facts():
    def fact(identifier, account, amount):
        return Fact(identifier, '甲公司', account, '2023Y', value=Decimal(amount),
                    source_doc='source-report', publish_date='2024-04-01', evidence=('evidence:'+identifier,))
    return FactStore([fact('np','NP','10'), fact('rev','REVENUE','100'),
                      fact('parent','NP_PARENT','15'), fact('matching','NET_MARGIN','.10')])


@pytest.mark.parametrize('label', ['净利率','净利润率'])
@pytest.mark.parametrize('origin', ['prose','table'])
def test_bare_margin_is_numeric_work_but_not_a_defined_ratio(label, origin, monkeypatch):
    source = ('甲公司2023年合并口径'+label+'为10%。' if origin=='prose'
              else '合并利润表(百万元)\n2023A\n'+label+'(%)\n10\n')
    claim = (extract_mentions(source,'甲公司')[0] if origin=='prose' else tables(source)[0])
    claim = freeze_source_dimensions(claim)
    assert claim.account=='NET_MARGIN_UNSPECIFIED' and claim.parse_status=='parsed'
    assert claim.candidate_value=='0.10' and semantic_review_required(claim)
    assert infer_account(label)=='NET_MARGIN_UNSPECIFIED'
    store = matching_facts()
    def forbidden(*args, **kwargs):
        raise AssertionError('Undefined source ratio must not consume even matching facts')
    for method in ('query','query_outcome','versions'):
        monkeypatch.setattr(store,method,forbidden)
    # Two independent model guesses cannot manufacture the missing definition.
    rebound = replace(claim,account='NET_MARGIN',binding_status='agreed')
    result = Judge(store,'2024-04-22',require_dual_binding=True).verify(rebound)
    assert result.status=='abstained' and result.abstention_code=='X3'
    assert result.expected is None and not result.evidence and not result.operands


@pytest.mark.parametrize('definition', [
    '净利率（净利润/营业收入）', '净利润率（净利润/营业收入）',
    '净利率=净利润/营业收入', '净利率即净利润除以营业收入',
    '以净利润为分子、营业收入为分母计算净利率',
])
def test_complete_np_revenue_definition_uses_only_the_authorized_operands(definition):
    source = '甲公司2023年合并口径'+definition+'为10%。'
    claim = freeze_source_dimensions(extract_mentions(source,'甲公司')[0])
    assert claim.account=='NET_MARGIN' and not semantic_review_required(claim)
    store = FactStore([f for f in matching_facts().facts if f.account!='NET_MARGIN'])
    result = Judge(store,'2024-04-22').verify(claim)
    assert result.status=='verified' and result.expected=='0.1'
    assert result.rule_ids==['FIN-net-margin'] and result.formula=='NP/REVENUE'
    assert [r['account'] for r in result.operands]==['NP','REVENUE']
    assert [Decimal(r['value']) for r in result.operands]==[Decimal('10'),Decimal('100')]
    assert set(result.evidence)=={'evidence:np','evidence:rev'}
    assert all(r['account']!='NP_PARENT' for r in result.operands)


@pytest.mark.parametrize('label', ['净利率','净利润率'])
def test_complete_table_definition_remains_a_ratio_instead_of_its_embedded_amount_labels(label):
    source = '合并利润表(百万元)\n2023A\n'+label+'（净利润/营业收入）(%)\n10\n'
    claim = freeze_source_dimensions(tables(source)[0])
    assert claim.account=='NET_MARGIN' and claim.unit=='%' and claim.candidate_value=='0.10'
    store = FactStore([f for f in matching_facts().facts if f.account!='NET_MARGIN'])
    result = Judge(store,'2024-04-22').verify(claim)
    assert result.status=='verified' and [r['account'] for r in result.operands]==['NP','REVENUE']


@pytest.mark.parametrize('definition', [
    '净利率（归母净利润/营业收入）',
    '净利润率=归属于母公司股东的净利润/营业收入',
])
@pytest.mark.parametrize('origin', ['prose','table'])
def test_explicit_parent_numerator_cannot_borrow_np_formula_or_matching_ratio(definition, origin, monkeypatch):
    source = ('甲公司2023年合并口径'+definition+'为10%。' if origin=='prose'
              else '合并利润表(百万元)\n2023A\n'+definition+'(%)\n10\n')
    claim = (extract_mentions(source,'甲公司')[0] if origin=='prose' else tables(source)[0])
    claim = freeze_source_dimensions(claim)
    assert claim.account=='NP_PARENT_MARGIN' and not semantic_review_required(claim)
    store = matching_facts()
    original = Judge(store,'2024-04-22').verify(replace(claim,binding_status='agreed'))
    assert original.status=='violated' and original.error_type=='E5'
    assert Decimal(original.expected)==Decimal('.15')
    assert original.rule_ids==['FIN-parent-net-margin'] and original.formula=='NP_PARENT/REVENUE'
    assert [r['account'] for r in original.operands]==['NP_PARENT','REVENUE']
    assert [Decimal(r['value']) for r in original.operands]==[Decimal('15'),Decimal('100')]
    assert set(original.evidence)=={'evidence:parent','evidence:rev'}
    def forbidden(*args, **kwargs):
        raise AssertionError('A source-defined parent margin cannot be changed to an NP ratio')
    for method in ('query','query_outcome','versions'):
        monkeypatch.setattr(store,method,forbidden)
    result = Judge(store,'2024-04-22',require_dual_binding=True).verify(
        replace(claim,account='NET_MARGIN',binding_status='agreed'))
    assert result.status=='abstained' and result.abstention_code=='X3'
    assert result.expected is None and not result.operands and not result.evidence


@pytest.mark.parametrize('source', [
    '甲公司2023年合并口径净利率10%，净利润10元。',
    '甲公司2023年合并口径净利率10%，归母净利润15元。',
    '甲公司2023年合并口径净利率按净利润口径计算为10%。',
    '甲公司2023年合并口径净利率（净利润为分子）为10%。',
])
def test_neighbor_amount_or_numerator_only_is_not_a_complete_definition(source):
    claim = freeze_source_dimensions(extract_mentions(source,'甲公司')[0])
    assert claim.account=='NET_MARGIN_UNSPECIFIED' and semantic_review_required(claim)
    assert Judge(matching_facts(),'2024-04-22').verify(claim).abstention_code=='X3'


@pytest.mark.parametrize('definition', ['净利率（以归母净利润为分子）', '净利润率按归母净利润口径'])
def test_parent_numerator_only_explains_missing_definition_not_missing_registered_rule(definition):
    claim = freeze_source_dimensions(extract_mentions('甲公司2023年合并口径'+definition+'为10%。','甲公司')[0])
    assert claim.account == 'NET_MARGIN_PARENT_UNSPECIFIED'
    result = Judge(matching_facts(),'2024-04-22').verify(claim)
    assert result.status == 'abstained' and result.abstention_code == 'X3'
    assert '分母' in result.reason and '已有归母净利率登记公式' in result.reason
    assert result.expected is None and not result.operands


@pytest.mark.parametrize('label', ['归母净利率','归母净利润率','扣非净利率','扣非净利润率'])
def test_outer_parent_or_adjusted_label_cannot_be_overwritten_by_inner_np_definition(label):
    source = '甲公司2023年合并口径'+label+'（净利润/营业收入）为10%。'
    claim = freeze_source_dimensions(extract_mentions(source,'甲公司')[0])
    assert claim.account!='NET_MARGIN'
    result = Judge(matching_facts(),'2024-04-22').verify(replace(claim,account='NET_MARGIN',binding_status='agreed'))
    assert result.status=='abstained' and result.expected is None and not result.operands


def test_definition_context_does_not_steal_a_following_amount_account():
    source = '甲公司2023年合并口径净利率（净利润/营业收入）10%，营业收入100元，归母净利润15元。'
    claims = extract_mentions(source,'甲公司')
    assert [c.account for c in claims]==['NET_MARGIN','REVENUE','NP_PARENT']
    assert [c.candidate_value for c in claims]==['0.10','100','15']


def test_latest_broker_dilution_uses_explicit_local_yuan_and_keeps_source_unit_proof():
    source = ('主要财务比率\n2022A\n2023A\n2024E\n'
              '每股指标（元）\n每股收益(最新摊薄)\n-1.96\n-1.38\n-0.63\n')
    claims = tables(source)
    assert [c.account for c in claims]==['EPS_BROKER_DILUTED_UNSPECIFIED']*3
    assert [c.candidate_value for c in claims]==['-1.96','-1.38','-0.63']
    assert [c.period for c in claims]==['2022Y','2023Y','2024Y']
    assert [c.modality for c in claims]==['fact','fact','forecast']
    assert all(c.unit=='元' and c.parse_status=='parsed' and semantic_review_required(c) for c in claims)
    for claim in claims:
        proof = claim.table_context['source_unit_proof']
        assert source[proof['start']:proof['end']]==proof['text']=='每股指标（元）'
        assert proof['table_title_sha256']==hashlib.sha256('主要财务比率'.encode()).hexdigest()
        assert source[claim.start:claim.end]==claim.numeric_text
        assert claim.text[claim.source_numeric_start:claim.source_numeric_start+len(claim.numeric_text)]==claim.numeric_text
    assert all(Judge(FactStore([]),'2024-04-22').verify(freeze_source_dimensions(c)).abstention_code=='X3' for c in claims[:2])


@pytest.mark.parametrize('unit', ['百万元','万元','亿元'])
def test_latest_dilution_cannot_inherit_a_statement_amount_scale(unit):
    source = '合并利润表('+unit+')\n2022A\n2023A\n2024E\n每股收益(最新摊薄)\n-1.96\n-1.38\n-0.63\n'
    claims = tables(source)
    assert len(claims)==3 and all(c.account=='EPS_BROKER_DILUTED_UNSPECIFIED' for c in claims)
    assert all(c.unit=='' and c.candidate_value is None and c.parse_status=='unresolved' for c in claims)
    assert [c.period for c in claims]==['2022Y','2023Y','2024Y']
    assert claims[-1].modality=='forecast'
    assert all('source_unit_proof' not in c.table_context for c in claims)
    assert all(Judge(FactStore([])).verify(c).abstention_code=='X6' for c in claims[:2])


@pytest.mark.parametrize('boundary', [
    '偿债能力\n',
    '单位：百万元\n',
    '合并利润表(百万元)\n2023A\n',
])
def test_local_per_share_unit_cannot_cross_new_sections_or_tables(boundary):
    source = ('主要财务比率\n2023A\n每股指标（元）\n每股收益(最新摊薄)\n1.20\n'
              +boundary+'每股收益(最新摊薄)\n1.20\n')
    claims = tables(source)
    assert len(claims)==2 and claims[0].candidate_value=='1.20'
    assert claims[1].candidate_value is None and claims[1].unit==''
    assert 'source_unit_proof' not in claims[1].table_context


def test_local_per_share_unit_is_page_local():
    first = '主要财务比率\n2023A\n每股指标（元）\n每股收益(最新摊薄)\n1.20\n'
    second = '主要财务比率\n2023A\n每股收益(最新摊薄)\n1.20\n'
    source = first+second
    claims = tables(source,[{'start':0,'end':len(first),'page':1},
                           {'start':len(first),'end':len(source),'page':2}])
    assert claims[0].candidate_value=='1.20' and claims[1].candidate_value is None


def test_yuan_subsection_cannot_cross_into_other_pdf_lane():
    rows = [('主要财务比率',20,20), ('2023A',120,30), ('每股指标（元）',20,40),
            ('每股收益(最新摊薄)',20,50), ('1.20',120,50),
            ('合并利润表(百万元)',320,20), ('2023A',420,30),
            ('每股收益(最新摊薄)',320,50), ('1.20',420,50)]
    source, lines = '', []
    for text,x,y in rows:
        start = len(source)
        source += text+'\n'
        lines.append({'text':text,'start':start,'end':start+len(text),'bbox':[x,y,x+len(text)*5,y+8]})
    claims = tables(source,[{'start':0,'end':len(source),'page':1,'lines':lines}])
    assert len(claims)==2
    assert claims[0].candidate_value=='1.20' and claims[1].candidate_value is None
    assert claims[0].table_context['source_unit_proof']['layout_lane']==1
    assert 'source_unit_proof' not in claims[1].table_context


@pytest.mark.parametrize('label,account', [('基本每股收益','EPS'),('稀释每股收益','EPS_DILUTED')])
def test_explicit_statutory_eps_remains_separate_and_usable(label,account):
    source = '合并利润表(百万元)\n2023A\n'+label+'(元)\n1.20\n'
    claim = freeze_source_dimensions(tables(source)[0])
    assert claim.account==account and claim.candidate_value=='1.20' and not semantic_review_required(claim)
    store = FactStore([Fact('eps','甲公司',account,'2023Y',value=Decimal('1.20'),publish_date='2024-04-01',evidence=('eps-page',))])
    result = Judge(store,'2024-04-22').verify(claim)
    assert result.status=='verified' and result.evidence==['eps-page']


def actual_development_rows():
    selected = []
    for window in SOURCE_OBSERVATIONS['windows']:
        ledger,claims = CoverageLedger.build(window['text'],[window['page']],window['blocks'],
                                              SOURCE_OBSERVATIONS['company'],SOURCE_OBSERVATIONS['period'])
        for claim in claims:
            original_start = claim.start+window['source_start']
            if original_start in {16748,52890,52897}:
                selected.append(claim)
    return sorted(selected,key=lambda c:c.start)


def test_three_actual_development_occurrences_are_numeric_semantic_work_with_addressable_units():
    selected = actual_development_rows()
    assert len(selected)==3
    assert {c.account for c in selected}=={'NET_MARGIN_UNSPECIFIED','EPS_BROKER_DILUTED_UNSPECIFIED'}
    assert all(c.parse_status=='parsed' and semantic_review_required(c) for c in selected)
    eps = [c for c in selected if c.account.startswith('EPS')]
    assert [c.period for c in eps]==['2022Y','2023Y']
    assert [c.candidate_value for c in eps]==['-1.96','-1.38']
    for claim in eps:
        window = next(w for w in SOURCE_OBSERVATIONS['windows'] if w['page']['page']==42)
        proof = claim.table_context['source_unit_proof']
        assert window['text'][proof['start']:proof['end']]==proof['text']
        assert proof['text'].strip()=='每股指标（元）' and proof['layout_lane']==2


def prepare_three_boundary(lifecycle):
    lifecycle.request.update(company=SOURCE_OBSERVATIONS['company'],period=SOURCE_OBSERVATIONS['period'])
    owner = new_agent(lifecycle)
    owner.prepare()
    owner.claims = [freeze_source_dimensions(replace(c,claim_id='new-review-'+str(index)))
                    for index,c in enumerate(actual_development_rows())]
    owner.dispatcher.claims = {c.claim_id:c for c in owner.claims}
    owner.ledger = CoverageLedger([],[],[{'id':c.claim_id,'claim_id':c.claim_id,'start':c.start,'end':c.end,
                                       'text':c.numeric_text,'status':'pending','reason':'original_financial_mention'}
                                       for c in owner.claims])
    lifecycle.boundary.batch_coordinator = True
    lifecycle.boundary.binding_transform = lambda b:{**b,'account':''}
    return owner


def test_three_new_semantic_items_really_enter_both_blind_sessions_and_kernel_replay(lifecycle):
    owner = prepare_three_boundary(lifecycle)
    lifecycle.boundary.cancel_first_binding = False
    owner.start_runtime()
    try:
        owner.bind()
        owner.calculate()
        owner.report()
        result = owner.finish('success')
    finally:
        owner.runtime.close()
    assert result['model']['semantic_review_candidates']==result['model']['semantic_review_submitted']==3
    assert len(owner.dispatcher.bindings['planner'])==len(owner.dispatcher.bindings['verifier'])==3
    assert len(result['results'])==3 and not result['pending_claim_ids']
    assert all(r['status']=='abstained' and r['abstention_code']=='X3' for r in result['results'])
    assert replay_run(lifecycle.out)['status']=='passed'


def test_cancel_before_both_sides_submit_keeps_new_semantics_pending(lifecycle):
    owner = prepare_three_boundary(lifecycle)
    owner.start_runtime()
    try:
        with pytest.raises(Exception,match='cancellation'):
            owner.bind()
        owner._freeze_current_bindings()
        owner.calculate_kernel(dual=True,interrupted=True)
    finally:
        owner.runtime.close()
    assert len(owner.pending_claim_ids)==3 and not owner.results and owner.ledger.to_dict()['partial']


def test_new_defined_demo_keeps_three_states_and_computes_the_np_revenue_margin():
    source = (ROOT/'samples/demo_draft_defined.md').read_text(encoding='utf-8')
    ledger,claims = CoverageLedger.build(source,[{'start':0,'end':len(source),'page':1}],[],'明镜示例公司','2024Y')
    results = [Judge(load_index_facts(ROOT/'samples/corpus'),'2025-03-31').verify(c) for c in claims]
    assert {r.status for r in results}=={'verified','violated','abstained'}
    margin = next((c,r) for c,r in zip(claims,results) if c.account=='NET_MARGIN')
    assert margin[1].status=='verified' and margin[1].rule_ids==['FIN-net-margin']
    assert [r['account'] for r in margin[1].operands]==['NP','REVENUE']
    assert Decimal(margin[1].expected)==Decimal('.13')
    assert margin[1].evidence
