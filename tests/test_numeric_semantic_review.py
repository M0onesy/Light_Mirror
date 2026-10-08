"""Archived source observations and keyless A/B boundaries, not human gold/API acceptance."""
from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path

import pytest

from mingjing.coverage import CoverageLedger
from mingjing.facts import FactStore
from mingjing.judge import Judge
from mingjing.mentions import extract_mentions, infer_account
from mingjing.models import Fact
from mingjing.metrics import metric_spec
from mingjing.source_semantics import freeze_source_dimensions, semantic_review_required

from test_agent_lifecycle import lifecycle, new_agent, read


OBSERVATIONS = json.loads((Path(__file__).parent/'fixtures/numeric_semantic_review_03.json').read_text(encoding='utf-8'))
# Keep the archived observations immutable. The two written management-fee
# ratios now have an issued registry identity; all 24 numeric occurrences and
# their original values/periods/units remain in the task population.
REGISTERED_SOURCE_UPGRADES = {'claim-00020': 'ADMIN_EXPENSE_RATIO',
                              'claim-00021': 'ADMIN_EXPENSE_RATIO'}


def rebuilt_source():
    rows, entries, pages, blocks = {}, [], [], []
    for window in OBSERVATIONS['windows']:
        ledger, claims = CoverageLedger.build(window['text'], [window['page']], window['blocks'],
                                              OBSERVATIONS['company'], OBSERVATIONS['period'])
        for claim in claims:
            source_start = window['source_start']
            rows[(claim.start+source_start, claim.end+source_start)] = claim
        entries.extend(ledger.entries)
        pages.extend(ledger.pages)
        blocks.extend(ledger.blocks)
    return rows, entries, pages, blocks


REBUILT, _, _, _ = rebuilt_source()


@pytest.mark.parametrize('observed', OBSERVATIONS['observations'], ids=lambda row: row['original_claim_id'])
def test_real_24_numbers_keep_source_positions_values_and_separate_registered_ratios(observed):
    assert OBSERVATIONS['origin']=='development_observation_not_human_gold'
    claim = REBUILT[(observed['start'], observed['end'])]
    registered = observed['original_claim_id'] in REGISTERED_SOURCE_UPGRADES
    assert semantic_review_required(claim) is (not registered) and claim.parse_status=='parsed'
    assert claim.account == REGISTERED_SOURCE_UPGRADES.get(observed['original_claim_id'], observed['account'])
    for field in ('period','candidate_value','unit','numeric_text','text'):
        assert getattr(claim,field)==observed[field]
    if registered:
        assert claim.table_context['definition_id'] == 'metric:admin-expense-ratio'
        assert metric_spec(claim.account, claim.table_context['definition_id']) is not None
    assert claim.text[claim.source_numeric_start:claim.source_numeric_start+len(claim.numeric_text)]==claim.numeric_text
    result = Judge(FactStore([]),'2024-04-22').verify(freeze_source_dimensions(claim))
    assert result.status=='abstained'
    assert result.abstention_code==('X2' if claim.account.startswith('EXP_') else 'X3')
    assert result.expected is None and not result.operands and not result.evidence


@pytest.mark.parametrize('observed', OBSERVATIONS['observations'], ids=lambda row: row['original_claim_id'])
def test_matching_truth_or_two_canonical_guesses_cannot_authorize_source(observed,monkeypatch):
    original = freeze_source_dimensions(REBUILT[(observed['start'],observed['end'])])
    canonical = {'ROE_UNSPECIFIED':'ROE','EPS_UNSPECIFIED':'EPS',
                 'EPS_BROKER_DILUTED_UNSPECIFIED':'EPS_DILUTED','ASSET_CASH_UNSPECIFIED':'ASSET_CASH',
                 'NET_MARGIN_UNSPECIFIED':'NET_MARGIN','INVESTMENTS_UNSPECIFIED':'INVEST_LONG_EQUITY'}.get(original.account,'EXP_ADMIN')
    store = FactStore([Fact('matching-canonical',original.company,canonical,original.period,
                          value=Decimal(original.candidate_value),publish_date='2024-04-01')])
    def forbidden(*_args,**_kwargs):
        raise AssertionError('An undefined source cannot consume numerically matching canonical facts')
    monkeypatch.setattr(store,'query',forbidden)
    monkeypatch.setattr(store,'versions',forbidden)
    rebound = replace(original,account=canonical,binding_status='agreed',caliber_hint=('合并','未扣非'))
    result = Judge(store,'2024-04-22',require_dual_binding=True).verify(rebound)
    assert result.status=='abstained' and result.abstention_code==('X2' if original.account.startswith('EXP_') else 'X3')
    assert result.correction is None and not result.operands
    assert rebound.table_context['source_dimensions']['account']==original.account


def prepare_24_boundary(lifecycle):
    """Real source/parser rows supplied to an explicitly controlled runtime."""
    lifecycle.request['company']=OBSERVATIONS['company']
    lifecycle.request['period']=OBSERVATIONS['period']
    owner = new_agent(lifecycle)
    owner.prepare()
    rows, _, _, _ = rebuilt_source()
    selected=[]
    for index, observed in enumerate(OBSERVATIONS['observations']):
        original = rows[(observed['start'],observed['end'])]
        selected.append(freeze_source_dimensions(replace(original,claim_id='review-'+str(index))))
    owner.claims=selected
    owner.dispatcher.claims={c.claim_id:c for c in selected}
    owner.ledger=CoverageLedger([],[],[{'id':c.claim_id,'claim_id':c.claim_id,'start':c.start,'end':c.end,
                                      'text':c.numeric_text,'status':'pending','reason':'fixture_original_financial_mention'} for c in selected])
    lifecycle.boundary.batch_coordinator=True
    lifecycle.boundary.binding_transform=lambda b:{**b,'account':'' if b['account'].endswith('_UNSPECIFIED') else b['account']}
    return owner


def test_22_semantics_are_blind_bound_and_two_registered_ratios_preflight_without_losing_24(lifecycle):
    from mingjing.replay import replay_run
    owner=prepare_24_boundary(lifecycle)
    lifecycle.boundary.cancel_first_binding=False
    owner.start_runtime()
    try:
        owner.bind()
        owner.calculate()
        owner.report()
        result=owner.finish('success')
    finally:
        owner.runtime.close()
    assert result['model']['semantic_review_candidates']==22
    assert result['model']['semantic_review_submitted']==22
    assert len(owner.dispatcher.bindings['planner'])==len(owner.dispatcher.bindings['verifier'])==22
    ratios = {c.claim_id for c in owner.claims if c.account == 'ADMIN_EXPENSE_RATIO'}
    assert len(ratios) == 2 and set(owner.preflight_claim_ids) == ratios
    assert not result['pending_claim_ids']
    assert len(result['results'])==24
    assert {r['abstention_code'] for r in result['results']}=={'X2','X3'}
    assert {r['abstention_code'] for r in result['results'] if r['claim_id'] in ratios} == {'X3'}
    assert all(owner.dispatcher.source_dimensions(c)['scope'] == '' for c in owner.claims
               if c.claim_id in ratios)
    sessions=owner.dispatcher.sessions
    a={sid for sid,spec in sessions.items() if spec['role']=='planner'}
    b={sid for sid,spec in sessions.items() if spec['role']=='verifier'}
    assert len(a)==len(b)==3 and a.isdisjoint(b)
    assert all(sessions[sid]['mentions'].isdisjoint(ratios) for sid in a | b)
    assert set().union(*(sessions[sid]['mentions'] for sid in a)) | ratios == set(owner.dispatcher.claims)
    assert all(sessions[sa]['mentions']==sessions[sb]['mentions'] for sa in a for sb in b
               if sessions[sa]['mentions'] & sessions[sb]['mentions'])
    for claim in owner.claims:
        for compact in (True,False):
            blind=owner.dispatcher.mention_context(claim,compact=compact)
            assert claim.numeric_text.strip() not in blind['text']
            assert 'candidate_value' not in blind and 'expected' not in blind
            if claim.claim_id not in ratios:
                assert blind['table_context']['semantic_review_required'] is True
            else:
                assert not blind['table_context'].get('semantic_review_required')
                assert blind['source_dimensions']['definition_id'] == 'metric:admin-expense-ratio'
    assert replay_run(lifecycle.out)['status']=='passed'
    frozen = read(lifecycle.out/'results.json')
    assert frozen['model']['semantic_review_submitted']==22 and len(frozen['claims']) == 24
    assert len(frozen['coverage']['mentions']) == 24


def test_cancelled_unsubmitted_semantics_remain_pending_instead_of_becoming_X3(lifecycle):
    owner=prepare_24_boundary(lifecycle)
    owner.start_runtime()
    try:
        with pytest.raises(Exception,match='cancellation'):
            owner.bind()
        owner._freeze_current_bindings()
        owner.calculate_kernel(dual=True,interrupted=True)
    finally:
        owner.runtime.close()
    ratios = {c.claim_id for c in owner.claims if c.account == 'ADMIN_EXPENSE_RATIO'}
    assert len(owner.pending_claim_ids)==22 and set(owner.pending_claim_ids).isdisjoint(ratios)
    assert {r['claim_id'] for r in owner.results} == ratios
    assert {r['abstention_code'] for r in owner.results} == {'X3'}
    assert len(owner.ledger.entries) == 24
    assert owner.ledger.to_dict()['partial']
    assert len(owner.dispatcher.bindings['planner'])==1 and not owner.dispatcher.bindings['verifier']


def test_bare_EPS_is_not_deleted_and_English_substrings_are_not_financial_labels():
    source='甲公司2023年合并口径EPS为1.23元。'
    ledger,claims=CoverageLedger.build(source,[{'start':0,'end':len(source),'page':1}],[],'甲公司','2023Y')
    assert len(claims)==1 and semantic_review_required(claims[0])
    assert claims[0].account=='EPS_UNSPECIFIED' and claims[0].candidate_value=='1.23'
    assert any(e['claim_id']==claims[0].claim_id for e in ledger.entries)
    for label in ('STEPS','GROES','EPSILON','EPS2','X_EPS','EPS_2'):
        source='甲公司2023年合并口径'+label+'为1.23元。'
        ledger,claims=CoverageLedger.build(source,[{'start':0,'end':len(source),'page':1}],[],'甲公司','2023Y')
        assert not any(c.parse_status=='parsed' for c in claims) and infer_account(source)=='UNKNOWN'
        assert any(e['text']=='1.23元' and e['status']=='rule_excluded' for e in ledger.entries)
    for label in ('eps','Eps','roe'):
        source='甲公司2023年合并口径'+label+'为1.23元。'
        assert len(extract_mentions(source,'甲公司','2023Y'))==1


@pytest.mark.parametrize('source',[
    '甲公司2023年合并口径营业收入98765元且ROE为12.34%。',
    '甲公司2023年合并口径营业收入98765元并实现净利润4321元。',
])
@pytest.mark.parametrize('role',['planner','verifier'])
def test_blind_tool_payload_masks_neighbor_amounts_in_nested_source_proof_without_mutation(source,role):
    from mingjing.financial_tools import FinancialDispatcher
    claims=extract_mentions(source,'甲公司','2023Y')
    claim=claims[-1]
    claim=replace(claim,table_context={**claim.table_context,'nested_source':{'subjects':['营业收入98765元','净利润4321元']}})
    dispatcher=FinancialDispatcher('run','lock',[claim],[],[],[],'2024-04-22')
    dispatcher.register_session(role,role)
    original=json.dumps(dispatcher.claims[claim.claim_id].to_dict(),ensure_ascii=False,sort_keys=True)
    assert '98765' in original
    for index,(name,params) in enumerate([
        ('mj_list_mentions',{}),('mj_read_context',{'mention_id':claim.claim_id}),
        ('mj_fact_dimensions',{'mention_id':claim.claim_id}),
    ]):
        result=dispatcher.dispatch({'session_id':role,'role':role,'run_id':'run','resource_lock':'lock',
                                    'call_id':str(index),'tool_name':name,'params':params})
        payload=json.dumps(result,ensure_ascii=False)
        assert '98765' not in payload and '4321' not in payload and '12.34' not in payload
        assert '2023Y' in payload  # Stable typed periods remain useful to binders.
    assert json.dumps(dispatcher.claims[claim.claim_id].to_dict(),ensure_ascii=False,sort_keys=True)==original


@pytest.mark.parametrize('source,reason',[
    ('2023年11-12月活动结束。','declared_month_range'),
    ('HPC China2023举办。','technical_event_edition'),
    ('2A实例型号服务器。','technical_model_identifier'),
    ('安装4套存储设备。','technical_quantity'),
])
def test_declared_noise_has_proof_but_financial_values_on_same_line_survive(source,reason):
    source+='2023年合并营业收入1.23亿元，同比增长12%。'
    ledger,claims=CoverageLedger.build(source,[{'start':0,'end':len(source),'page':1}],[],'甲公司','2023Y')
    excluded=[e for e in ledger.entries if e['reason']==reason]
    assert excluded and all(e['status'] in {'rule_excluded','out_of_scope'} and e.get('exclusion_basis') for e in excluded)
    assert {c.numeric_text for c in claims}=={'1.23亿元','12%'}
