"""Complete exposed broker partition, independent official operands and gaps."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path

import pytest

from mingjing.facts import FactStore, fact_from_dict
from mingjing.financial_derivations import (BROKER_COMPOSITIONS,
    attach_broker_compositions, broker_source_composition,
    derive_registered_metric, validated_broker_composition)
from mingjing.metrics import METRIC_SPECS
from mingjing.source_semantics import freeze_source_dimensions
from mingjing.table_mentions import extract_table_mentions


SOURCE = json.loads((Path(__file__).parent/'fixtures/broker_residual_p42_official_balance.json').read_text(encoding='utf-8'))


@pytest.fixture(scope='module')
def source_claims():
    return extract_table_mentions(SOURCE['research_text'], SOURCE['research_pages'], [],
                                  SOURCE['company'], '2099Y')


def target(claims, account, period='2023Y'):
    return next(x for x in claims if x.account == account and x.period == period)


def store():
    return FactStore([fact_from_dict(x) for x in SOURCE['official_facts']])


def compute(claim, facts=None):
    return derive_registered_metric(facts or store(), claim.company, claim.account,
        claim.period, '合并', '未扣非', SOURCE['cutoff'],
        definition_id=claim.table_context['definition_id'],
        source_doc=SOURCE['official_sha256'][:12], source_claim=claim)


@pytest.mark.parametrize('account', tuple(BROKER_COMPOSITIONS))
@pytest.mark.parametrize('period', ['2022Y', '2023Y'])
def test_eight_original_partitions_include_every_printed_row_and_following_boundary(source_claims, account, period):
    claim = target(source_claims, account, period)
    proof = broker_source_composition(claim, source_claims)
    assert proof['status'] == 'complete' and not proof['blockers']
    assert [(x['caption'], x['account']) for x in proof['rows']] == [
        *BROKER_COMPOSITIONS[account]['rows'], BROKER_COMPOSITIONS[account]['boundary']]
    assert proof['rows'][-1]['is_boundary']
    assert proof['numeric_authority'] == 'external_facts_only'
    assert proof['cash_or_investment_definition_selected'] is False
    assert 'candidate_value' not in json.dumps(proof)


def test_current_asset_spec_subtracts_both_original_additional_siblings():
    spec = METRIC_SPECS['BROKER_OTHER_CURRENT_ASSETS']
    assert {x.name for x in spec.inputs} == {
        'CURRENT_ASSETS', 'CASH_SOURCE', 'AR_NOTES', 'OTHER_RECEIVABLES', 'PREPAYMENTS', 'INVENTORY'}
    assert BROKER_COMPOSITIONS[spec.account]['formula'] == (
        'CURRENT_ASSETS-CASH_SOURCE-AR_NOTES-OTHER_RECEIVABLES-PREPAYMENTS-INVENTORY')


@pytest.mark.parametrize('missing_account', ['OTHER_RECEIVABLES', 'PREPAYMENTS', 'INVENTORY', 'NONCURRENT_ASSETS'])
def test_missing_same_year_source_sibling_cannot_use_shorter_formula(source_claims, missing_account):
    claim = target(source_claims, 'BROKER_OTHER_CURRENT_ASSETS')
    fewer = [x for x in source_claims if not (x.period == '2023Y' and x.account == missing_account)]
    proof = broker_source_composition(claim, fewer)
    assert proof['status'] == 'incomplete'
    assert any(x.get('account') == missing_account for x in proof['blockers'])
    # A missing 2023 cell must not destroy the independently intact 2022 column.
    assert broker_source_composition(target(fewer, claim.account, '2022Y'), fewer)['status'] == 'complete'


@pytest.mark.parametrize('mutation', ['lane', 'headers', 'unit', 'duplicate', 'period', 'label', 'numeric_span'])
def test_adjacent_table_or_forged_sibling_never_supplies_a_missing_row(source_claims, mutation):
    original = target(source_claims, 'OTHER_RECEIVABLES')
    context = deepcopy(original.table_context)
    changed = replace(original, table_context=context)
    if mutation == 'lane':
        context['layout_lane'] = 2
    elif mutation == 'headers':
        context['headers'][1]['text'] = '2024A '
    elif mutation == 'unit':
        changed = replace(changed, unit='万元')
    elif mutation == 'period':
        changed = replace(changed, period='2022Y')
    elif mutation == 'label':
        context['row_label'] = '非流动资产 '
    elif mutation == 'numeric_span':
        changed = replace(changed, start=changed.start+100, end=changed.end+100)
    fewer = [x for x in source_claims if x is not original]
    rows = [*fewer, changed, changed] if mutation == 'duplicate' else [*fewer, changed]
    proof = broker_source_composition(target(source_claims, 'BROKER_OTHER_CURRENT_ASSETS'), rows)
    assert proof['status'] == 'incomplete' and proof['blockers']


def test_unknown_extra_printed_row_invalidates_the_registered_partition(source_claims):
    row = target(source_claims, 'OTHER_RECEIVABLES')
    context = deepcopy(row.table_context)
    context['row_label'] = '特殊受限资产'
    context['account_hint'] = 'UNRECOGNIZED_ASSET'
    extra = replace(row, claim_id='extra-row', start=row.start+1, end=row.end+1,
                    account='UNRECOGNIZED_ASSET', table_context=context)
    proof = broker_source_composition(target(source_claims, 'BROKER_OTHER_CURRENT_ASSETS'),
                                     [*source_claims, extra])
    assert proof['status'] == 'incomplete'
    assert any('额外组成行' in x['reason'] for x in proof['blockers'])


@pytest.mark.parametrize('mutation', ['row', 'header', 'text', 'hash', 'formula', 'authorization'])
def test_frozen_proof_cannot_survive_identity_or_authority_mutation(source_claims, mutation):
    claim = target(attach_broker_compositions(source_claims), 'BROKER_OTHER_CURRENT_ASSETS')
    context = deepcopy(claim.table_context)
    changed = replace(claim, table_context=context)
    if mutation == 'row':
        context['row_label'] = '其他非流动资产 '
    elif mutation == 'header':
        context['headers'][1]['text'] = '2024A '
    elif mutation == 'text':
        changed = replace(changed, text=claim.text+' altered')
    elif mutation == 'hash':
        context['broker_composition']['sha256'] = '0'*64
    elif mutation == 'formula':
        context['broker_composition']['formula'] = 'CURRENT_ASSETS'
    elif mutation == 'authorization':
        context['broker_composition']['numeric_authority'] = 'model_candidates'
    assert validated_broker_composition(changed) is None


def test_candidate_values_never_change_composition_or_independent_calculation(source_claims):
    altered = [replace(x, candidate_value='999999999') for x in source_claims]
    for account in BROKER_COMPOSITIONS:
        old = target(source_claims, account)
        new = target(altered, account)
        assert broker_source_composition(old, source_claims) == broker_source_composition(new, altered)
    attached = attach_broker_compositions(source_claims)
    claim = target(attached, 'BROKER_OTHER_CURRENT_LIABILITIES', '2022Y')
    assert compute(claim).value == compute(replace(claim, candidate_value='999999999')).value


@pytest.mark.parametrize('account,period,expected', [
    ('BROKER_OTHER_CURRENT_LIABILITIES', '2022Y', '453243654.54'),
    ('BROKER_OTHER_NONCURRENT_LIABILITIES', '2022Y', '46125487.51'),
])
def test_complete_2022_liability_residuals_consume_only_independent_official_facts(source_claims, account, period, expected):
    result = compute(target(attach_broker_compositions(source_claims), account, period))
    assert result.value == Decimal(expected) and not result.blockers
    assert all(x.source_doc == SOURCE['official_sha256'][:12] and x.evidence for x in result.operands)
    assert all(not x.account.startswith('BROKER_') for x in result.operands)
    assert result.interval.lower < result.value < result.interval.upper
    assert result.rule['source_composition']['status'] == 'complete'


@pytest.mark.parametrize('account,missing', [
    ('BROKER_OTHER_CURRENT_LIABILITIES', 'NOTES_PAYABLE'),
    ('BROKER_OTHER_NONCURRENT_LIABILITIES', 'LONG_TERM_BORROWING'),
])
def test_2023_dashes_remain_visible_and_never_create_zero_facts(source_claims, account, missing):
    result = compute(target(attach_broker_compositions(source_claims), account))
    assert result.value is None and any(x.get('account') == missing for x in result.blockers)
    assert all(x.account != missing for x in result.operands)


def test_current_asset_blockers_include_unknown_cash_and_each_absent_official_sibling(source_claims):
    claim = target(attach_broker_compositions(source_claims), 'BROKER_OTHER_CURRENT_ASSETS')
    rows = [fact_from_dict(x) for x in SOURCE['official_facts']
            if x['account'] not in {'OTHER_RECEIVABLES', 'PREPAYMENTS'}]
    result = compute(claim, FactStore(rows))
    assert result.value is None
    assert {'ASSET_CASH_UNSPECIFIED', 'OTHER_RECEIVABLES', 'PREPAYMENTS'} <= {
        x.get('account') for x in result.blockers}
    assert any(x['kind'] == 'definition' for x in result.blockers)
    assert all(x.account != 'CASH_FUNDS' for x in result.operands)


def test_source_composition_failure_does_not_hide_downstream_missing_operand(source_claims):
    fewer = [x for x in source_claims if not (x.period == '2023Y' and x.account == 'LONG_TERM_BORROWING')]
    claim = target(attach_broker_compositions(fewer), 'BROKER_OTHER_NONCURRENT_LIABILITIES')
    result = compute(claim)
    assert result.value is None
    assert any(x['kind'] == 'source_composition' for x in result.blockers)
    assert any(x['kind'] == 'operand' and x.get('account') == 'LONG_TERM_BORROWING' for x in result.blockers)


def test_frozen_source_claims_are_not_rewritten_by_later_attachment(source_claims):
    frozen = [freeze_source_dimensions(x) for x in attach_broker_compositions(source_claims)]
    assert attach_broker_compositions(frozen) == frozen


def test_complete_fixture_retains_blank_and_nested_rows_in_official_balance():
    text = '\n'.join(x['text'] for x in SOURCE['official_pages'])
    for label in ('合并资产负债表', '应付票据', '长期借款', '其中：应付利息',
                  '流动负债合计', '非流动负债合计', '归属于母公司所有者权益（或'):
        assert label in text
    assert [x['page'] for x in SOURCE['official_pages']] == [79, 80, 81, 82]


def test_older_rule_card_with_omitted_siblings_is_rejected_even_after_source_binding(source_claims, monkeypatch):
    import mingjing.financial_derivations as module
    cards = deepcopy(module.load_rule_cards())
    card = next(x for x in cards if x['id'] == 'FIN-broker-other-current-assets')
    card['formula'] = 'CURRENT_ASSETS-CASH_SOURCE-AR_NOTES-INVENTORY'
    card['inputs'].pop('OTHER_RECEIVABLES')
    card['inputs'].pop('PREPAYMENTS')
    monkeypatch.setattr(module, 'load_rule_cards', lambda: cards)
    result = compute(target(attach_broker_compositions(source_claims), 'BROKER_OTHER_CURRENT_ASSETS'))
    assert result.value is None and any(x['kind'] == 'registry_composition' for x in result.blockers)
    assert any(x.get('account') == 'ASSET_CASH_UNSPECIFIED' for x in result.blockers)
