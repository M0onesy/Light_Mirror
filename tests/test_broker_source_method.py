"""Two exposed pages: semantics without amount matching or numeric authority."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from decimal import Decimal

import pytest

from mingjing.source_method import attach_source_methods, derive_source_method, validated_source_method
from mingjing.source_semantics import freeze_source_dimensions, semantic_review_required
from mingjing.table_mentions import extract_table_mentions
from mingjing.financial_tools import FinancialDispatcher, FinancialArgumentError
from mingjing.facts import FactStore
from mingjing.judge import Judge
from mingjing.models import Fact


SOURCE = json.loads((Path(__file__).parent/'fixtures/broker_method_p1_p42.json').read_text(encoding='utf-8'))


@pytest.fixture(scope='module')
def original_claims():
    return extract_table_mentions(SOURCE['text'], SOURCE['pages'], [], SOURCE['company'], '2099Y')


def target(claims, caption, year='2023Y'):
    return next(c for c in claims if c.period == year and c.table_context.get('row_label','').strip() == caption)


def derive(claim, claims, *, pages=None, text=None, cutoff='2024-04-22'):
    return derive_source_method(claim, claims, text or SOURCE['text'], pages or SOURCE['pages'], cutoff)


@pytest.mark.parametrize('caption,account,numerator', [
    ('每股收益(最新摊薄)', 'BROKER_EPS_LATEST', 'NP_PARENT'),
    ('每股经营现金流(最新摊薄)', 'BROKER_CFO_PER_SHARE_LATEST', 'CFO'),
    ('每股净资产(最新摊薄)', 'BROKER_EQUITY_PER_SHARE_LATEST', 'EQUITY_PARENT'),
])
def test_three_latest_methods_have_original_geometry_and_dated_total_shares(original_claims, caption, account, numerator):
    claim = target(original_claims, caption)
    proof = derive(claim, original_claims)
    assert proof['method_authorization'] == 'proposal_only'
    assert proof['account'] == account and proof['numerator_account'] == numerator
    assert proof['denominator_account'] == 'END_SHARES'
    assert proof['snapshot_date'] == '2024-04-19'
    anchor = proof['denominator_anchor']
    assert anchor['unit'] == '亿股'
    assert anchor['total_shares_caption']['text'].strip() == '总股本(亿股)'
    assert anchor['date_value']['text'].strip() == '2024/4/19'
    assert anchor['total_shares_caption']['page'] == 1
    assert proof['source_row']['page'] == proof['numerator_source']['page'] == 42
    assert proof['statutory_equivalence'] is False
    raw = json.dumps(proof, ensure_ascii=False)
    assert 'candidate_value' not in raw and 'fact_value' not in raw
    assert '0.58' not in raw and '0.25' not in raw and '58,230,000' not in raw
    assert 'text' not in anchor['total_shares_cell']
    assert proof == derive(replace(claim,candidate_value='999999999'), original_claims)


def test_unspecified_and_statutory_eps_are_not_latest_by_equal_amount(original_claims):
    claim = target(original_claims, 'EPS(元)')
    assert claim.candidate_value == target(original_claims,'每股收益(最新摊薄)').candidate_value
    assert derive(claim, original_claims) is None
    for caption in ('基本每股收益','稀释每股收益','每股收益(加权平均)','每股收益(摊薄)'):
        changed = replace(claim,table_context={**claim.table_context,'row_label':caption})
        assert derive(changed,original_claims) is None


@pytest.mark.parametrize('cutoff', [None, '2024-04-18', 'invalid-date'])
def test_snapshot_future_or_unknown_cutoff_does_not_authorize_latest(original_claims, cutoff):
    assert derive(target(original_claims,'每股经营现金流(最新摊薄)'),original_claims,cutoff=cutoff) is None


def test_missing_or_multiple_market_total_share_dates_remain_missing(original_claims):
    claim = target(original_claims,'每股净资产(最新摊薄)')
    for missing in ('日期','2024/4/19','总股本(亿股)'):
        pages = deepcopy(SOURCE['pages'])
        pages[0]['lines'] = [l for l in pages[0]['lines'] if l['text'].strip() != missing]
        assert derive(claim,original_claims,pages=pages) is None
    assert derive(replace(claim,company='另一公司'),original_claims) is None
    pages = deepcopy(SOURCE['pages'])
    pages.append(deepcopy(pages[0]))
    assert derive(claim,original_claims,pages=pages) is None


def test_newest_share_method_requires_source_section_and_declared_numerator(original_claims):
    claim = target(original_claims,'每股经营现金流(最新摊薄)')
    pages = deepcopy(SOURCE['pages'])
    pages[-1]['lines'] = [l for l in pages[-1]['lines'] if l['text'].strip() != '每股指标（元）']
    assert derive(claim,original_claims,pages=pages) is None
    without_cfo = [c for c in original_claims if c.account != 'CFO']
    assert derive(claim,without_cfo) is None
    # An identically named unrelated numerator elsewhere is not in this summary.
    unrelated = replace(next(c for c in original_claims if c.account=='CFO'),
        table_context={'row_label':'经营活动现金流','title':'另一公司现金流量表'})
    assert derive(claim,without_cfo+[unrelated]) is None


@pytest.mark.parametrize('caption,account', [
    ('财务费用','BROKER_CFO_FINANCE'), ('投资损失','CF_BRIDGE_INVESTMENT_LOSS'),
])
def test_cashflow_components_need_the_bounded_indirect_group(original_claims, caption, account):
    claim = next(c for c in original_claims if c.period=='2023Y'
                 and c.table_context.get('row_label','').strip()==caption
                 and '现金流量表' in c.table_context.get('title',''))
    proof = derive(claim,original_claims)
    assert proof['account'] == account
    assert proof['source_group_total'] == 'CFO'
    assert proof['method_kind'] == 'broker_indirect_cashflow_component'
    assert proof['statutory_equivalence'] is False
    assert proof['requires_registered_component_rule'] is True
    assert proof['method_authorization'] == 'proposal_only'
    other_group = [c for c in original_claims if c.table_context.get('row_label','').strip()!='投资活动现金流']
    assert derive(claim,other_group) is None
    unknown_method = replace(claim,table_context={**claim.table_context,'title':'合并利润表'})
    assert derive(unknown_method,original_claims) is None
    if caption=='财务费用':
        income = next(c for c in original_claims if c.period=='2023Y'
            and c.table_context.get('row_label','').strip()==caption and '利润表' in c.table_context.get('title',''))
        assert income.candidate_value == claim.candidate_value
        assert derive(income,original_claims) is None  # Equal amounts cannot pick the method.


def test_attaching_proposal_keeps_account_guard_and_every_occurrence(original_claims):
    changed = attach_source_methods(original_claims,SOURCE['text'],SOURCE['pages'],'2024-04-22')
    assert len(changed) == len(original_claims)
    for old, new in zip(original_claims, changed):
        assert (old.claim_id,old.start,old.end,old.account,old.candidate_value,old.modality) == (
            new.claim_id,new.start,new.end,new.account,new.candidate_value,new.modality)
        assert semantic_review_required(old) == semantic_review_required(new)
        if 'source_method_proposal' in new.table_context:
            proof = validated_source_method(new)
            assert proof and proof['method_authorization']=='proposal_only'
    proof_claims = [c for c in changed if 'source_method_proposal' in c.table_context]
    assert len(proof_claims)==25  # Five rows, two historical + three forecasts; none removed.
    assert sum(c.modality=='fact' for c in proof_claims)==10
    frozen = [freeze_source_dimensions(c) for c in original_claims]
    assert attach_source_methods(frozen,SOURCE['text'],SOURCE['pages'],'2024-04-22') == frozen


def test_proposal_does_not_authorize_model_account_or_complete_blind_binding(original_claims):
    claim = target(original_claims,'每股收益(最新摊薄)')
    proof = derive(claim,original_claims)
    proposed = replace(claim,table_context={**claim.table_context,'source_method_proposal':proof})
    dispatcher = FinancialDispatcher('method-proposal','source-lock',[proposed],[],[],[],SOURCE['cutoff'],
                                      task_company=SOURCE['company'])
    for role in ('planner','verifier'):
        dispatcher.register_session(role,role,[claim.claim_id])
        arguments={'mention_id':claim.claim_id,'binding':{
            'company':claim.company,'account':proof['account'],'period':claim.period,
            'scope':'合并','relation':'value','base_period':''}}
        with pytest.raises(FinancialArgumentError) as caught:
            dispatcher.validated_binding(dispatcher.sessions[role],role,arguments)
        assert caught.value.rejection()['accepted'] is False
        assert not dispatcher.bindings[role]
        context=dispatcher.mention_context(dispatcher.claims[claim.claim_id],blind=True)
        assert context['source_definition']['supported'] is False
        assert proof['account'] not in context['account_labels'].values()
        assert claim.numeric_text.strip() not in context['text']
    assert dispatcher.freeze_bindings()[0].binding_status=='disagreed'


@pytest.mark.parametrize('year', ['2022Y','2023Y'])
@pytest.mark.parametrize('candidate_substitute', ['EXP_FINANCE','CF_BRIDGE_FINANCE'])
def test_real_cashflow_finance_cells_cannot_be_verified_by_either_similar_caption(original_claims, year, candidate_substitute):
    claim = next(c for c in original_claims if c.period==year and c.modality=='fact'
                 and c.table_context.get('row_label','').strip()=='财务费用'
                 and '现金流量表' in c.table_context.get('title',''))
    assert claim.account=='BROKER_CFO_FINANCE_UNSPECIFIED'
    assert semantic_review_required(claim)
    # The failure must be semantic even if the independent fact happens to
    # be numerically compatible. No amount-selected sign/method is allowed.
    fact = Fact('compatible-'+candidate_substitute,claim.company,candidate_substitute,year,
                value=Decimal(claim.candidate_value),source_doc='a'*12,publish_date='2024-04-15',
                evidence=('a'*12+':p168:1,2,3,4:deadbeef',),label='财务费用')
    judge = Judge(FactStore([fact]),'2024-04-22',require_dual_binding=True,strict_caliber=True)
    source = freeze_source_dimensions(replace(claim,binding_status='agreed'))
    result = judge.verify(source)
    assert result.status=='abstained' and result.abstention_code=='X3'
    assert '券商模型调整项' in result.reason
    assert result.expected is None
    substituted=replace(source,account=candidate_substitute)
    rejected=judge.verify(substituted)
    assert rejected.status=='abstained' and rejected.abstention_code=='X3'
    assert rejected.expected is None


@pytest.mark.parametrize('year', ['2022Y','2023Y'])
def test_neighboring_real_profit_statement_finance_cells_still_verify(original_claims, year):
    claim = next(c for c in original_claims if c.period==year and c.modality=='fact'
                 and c.table_context.get('row_label','').strip()=='财务费用'
                 and '利润表' in c.table_context.get('title',''))
    assert claim.account=='EXP_FINANCE' and not semantic_review_required(claim)
    fact = Fact('independent-finance-'+year,claim.company,'EXP_FINANCE',year,
                value=Decimal(claim.candidate_value),source_doc='a'*12,publish_date='2024-04-15',
                evidence=('a'*12+':p85:1,2,3,4:deadbeef',),label='财务费用')
    source=freeze_source_dimensions(replace(claim,binding_status='agreed'))
    result=Judge(FactStore([fact]),'2024-04-22',require_dual_binding=True,strict_caliber=True).verify(source)
    assert result.status=='verified' and result.expected==claim.candidate_value


@pytest.mark.parametrize('mutation', ['row','title','offset','layout','text','proof_hash','authorization'])
def test_method_proof_does_not_survive_source_or_identity_mutation(original_claims, mutation):
    claim = target(original_claims,'每股收益(最新摊薄)')
    proof = derive(claim,original_claims)
    context = deepcopy(claim.table_context)
    context['source_method_proposal'] = deepcopy(proof)
    prepared = replace(claim,table_context=context)
    assert validated_source_method(prepared)
    if mutation=='row':
        context['row_label']='每股收益(加权平均)'
    elif mutation=='title':
        context['title_source']['text']='另一个表格'
    elif mutation=='offset':
        prepared=replace(prepared,start=prepared.start+1)
    elif mutation=='layout':
        context['layout_lane']=1
    elif mutation=='text':
        prepared=replace(prepared,text=prepared.text+' changed')
    elif mutation=='proof_hash':
        context['source_method_proposal']['denominator_anchor']['date']='2023-12-31'
    elif mutation=='authorization':
        context['source_method_proposal']['method_authorization']='approved'
    assert validated_source_method(prepared) is None
