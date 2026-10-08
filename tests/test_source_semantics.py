"""Explicit source dimensions survive A/B semantic substitutions."""
from dataclasses import replace
from decimal import Decimal

import pytest

from mingjing.audit import kernel_fingerprint
from mingjing.facts import FactStore
from mingjing.financial_tools import FinancialDispatcher
from mingjing.judge import Judge
from mingjing.mentions import extract_mentions
from mingjing.models import Fact
from mingjing.scope import (apply_scope_clarification, create_scope_clarification,
                            scope_clarification_metadata, source_scope_dimensions)
from mingjing.source_semantics import (freeze_source_dimensions, source_binding_issue,
                                      source_dimensions_from_text)
from mingjing.table_mentions import extract_table_mentions


def claim(source='甲公司2023年合并营业收入100元。'):
    return freeze_source_dimensions(extract_mentions(source, '甲公司')[0])


def facts():
    return FactStore([Fact('rev', '甲公司', 'REVENUE', '2023Y', value=Decimal('200'), publish_date='2024-04-01'),
                      Fact('np', '甲公司', 'NP', '2023Y', value=Decimal('100'), publish_date='2024-04-01'),
                      Fact('old', '甲公司', 'REVENUE', '2022Y', value=Decimal('100'), publish_date='2024-04-01')])


@pytest.mark.parametrize('change', [{'account': 'NP'}, {'period': '2022Y'}, {'company': '乙公司'},
                                    {'relation': 'yoy', 'base_period': '2022Y'}])
def test_prose_substitutions_cannot_turn_wrong_revenue_into_verified(change):
    original = claim()
    assert Judge(facts(), '2024-04-22').verify(original).error_type == 'E1'
    forged = replace(original, **change, binding_status='agreed')
    result = Judge(facts(), '2024-04-22', require_dual_binding=True).verify(forged)
    assert result.status == 'abstained' and result.abstention_code == 'X3'


def test_amount_free_frozen_source_proof_survives_changed_binding_and_replay():
    original = claim()
    dimensions = source_dimensions_from_text(original)
    assert {field: dimensions[field] for field in ('company', 'account', 'period', 'scope', 'relation', 'base_period')} == {
        'company': '甲公司', 'account': 'REVENUE', 'period': '2023Y', 'scope': '合并', 'relation': 'value', 'base_period': ''}
    assert not set(dimensions) & {'value', 'candidate_value', 'expected', 'formula', 'verdict'}
    rebound = replace(original, account='NP', period='2022Y')
    assert freeze_source_dimensions(rebound) is rebound
    assert source_dimensions_from_text(rebound) == dimensions
    dimensions['explicit']['account'] = False
    assert source_dimensions_from_text(original)['explicit']['account']
    assert 'module:source_semantics' in kernel_fingerprint()
    assert 'module:mentions' in kernel_fingerprint()


def test_unknown_period_does_not_borrow_requested_fallback_or_fact_period():
    original = extract_mentions('甲公司合并营业收入100元。', '甲公司', '2023Y')[0]
    assert original.period == '2023Y'
    original = freeze_source_dimensions(original)
    assert source_dimensions_from_text(original)['period'] == ''
    result = Judge(facts(), '2024-04-22').verify(original)
    assert result.status == 'abstained' and result.abstention_code == 'X3'


def test_absolute_ratio_cannot_be_relabelled_as_another_ratio():
    original = claim('甲公司2023年合并口径毛利率25%。')
    store = FactStore([Fact('debt', '甲公司', 'DEBT_RATIO', '2023Y', value=Decimal('.25'),
                           display_unit='%', publish_date='2024-04-01')])
    result = Judge(store, '2024-04-22').verify(replace(original, account='DEBT_RATIO'))
    assert result.status == 'abstained' and result.abstention_code == 'X3'


def test_statutory_diluted_eps_cannot_use_basic_eps_truth():
    original = claim('甲公司2023年合并口径稀释每股收益1.10元。')
    store = FactStore([Fact('diluted', '甲公司', 'EPS_DILUTED', '2023Y', value=Decimal('1.20'), publish_date='2024-04-01'),
                      Fact('basic', '甲公司', 'EPS', '2023Y', value=Decimal('1.10'), publish_date='2024-04-01')])
    assert Judge(store, '2024-04-22').verify(original).status == 'violated'
    result = Judge(store, '2024-04-22').verify(replace(original, account='EPS'))
    assert result.status == 'abstained' and result.abstention_code == 'X3'


def test_expense_ratio_registered_definition_cannot_be_rebound_as_amount_growth():
    original = claim('甲公司2023年合并口径管理费用率为9.66%。')
    assert source_dimensions_from_text(original)['account'] == 'ADMIN_EXPENSE_RATIO'
    assert source_dimensions_from_text(original)['relation'] == 'value'
    result = Judge(FactStore([]), '2024-04-22').verify(replace(original, account='EXP_ADMIN', relation='yoy'))
    assert original.parse_status == 'parsed'
    assert result.status == 'abstained' and result.abstention_code == 'X3'


def test_growth_and_comparison_base_periods_are_source_proven():
    original = claim('甲公司2023年合并营业收入同比增长25%。')
    dimensions = source_dimensions_from_text(original)
    assert dimensions['relation'] == 'yoy' and dimensions['base_period'] == '2022Y'
    assert source_binding_issue(replace(original, base_period='2021Y'))[0] == 'X3'
    comparison = claim('2023年合并口径营业收入高于2022年。')
    dimensions = source_dimensions_from_text(comparison)
    assert dimensions['period'] == '2023Y' and dimensions['relation'] == 'gt' and dimensions['base_period'] == '2022Y'
    assert source_binding_issue(replace(comparison, base_period='2021Y'))[0] == 'X3'


def test_duplicate_values_are_bound_by_source_offset_not_first_match():
    originals = extract_mentions('甲公司2023年合并营业收入100元，营业成本100元。', '甲公司')
    frozen = [freeze_source_dimensions(c) for c in originals]
    assert [source_dimensions_from_text(c)['account'] for c in frozen] == ['REVENUE', 'COST']
    assert source_binding_issue(replace(frozen[1], account='REVENUE'))[0] == 'X3'


def test_invalid_frozen_text_hash_cannot_be_silently_recomputed():
    original = claim()
    altered = replace(original, text=original.text.replace('营业收入', '净利润'))
    with pytest.raises(ValueError, match='冻结源维度'):
        freeze_source_dimensions(altered)
    assert source_binding_issue(altered)[0] == 'X3'


def test_table_headers_remain_authoritative_and_broker_dilution_is_undefined():
    source = '合并利润表(元)\n2023A\n2022A\nEPS(摊薄/元)\n1.10\n1.20\n'
    tables = extract_table_mentions(source, [{'start': 0, 'end': len(source), 'page': 1}], [], '甲公司', '')
    assert len(tables) == 2
    original = freeze_source_dimensions(tables[0])
    dimensions = source_dimensions_from_text(original)
    assert dimensions['provenance'] == 'deterministic_table_headers'
    assert dimensions['account'] == 'EPS_BROKER_DILUTED_UNSPECIFIED' and dimensions['period'] == '2023Y'
    assert original.parse_status == 'parsed' and original.table_context['semantic_review_required']
    assert Judge(FactStore([])).verify(replace(original, account='EPS_DILUTED')).abstention_code == 'X3'


def test_scope_positive_and_negative_conflict_is_never_a_unique_scope():
    original = extract_mentions('甲公司2023年合并口径营业收入100元，但本段不按合并口径。', '甲公司')[0]
    dimensions = source_scope_dimensions(original)
    assert dimensions['scope_header'] == '' and dimensions['source_scope_conflict']
    resource = create_scope_clarification('合并', draft_sha256='a' * 64)
    metadata = scope_clarification_metadata(resource, 'b' * 64)
    with pytest.raises(ValueError, match='冲突'):
        apply_scope_clarification([original], metadata)


@pytest.mark.parametrize('negative', ['没有使用', '不再采用', '并非', '未能采用'])
def test_negated_scope_cannot_be_reinterpreted_as_positive_evidence(negative):
    original = extract_mentions(f'甲公司2023年{negative}合并口径营业收入100元。', '甲公司')[0]
    assert source_scope_dimensions(original)['scope_header'] == ''


def dispatcher_binding(source, *, account='REVENUE', period='2023Y', relation='value', base=''):
    originals = extract_mentions(source, '甲公司')
    store = [Fact('rev', '甲公司', 'REVENUE', '2023Y', value=Decimal('100'), publish_date='2024-04-01')]
    dispatcher = FinancialDispatcher('review', 'lock', originals, store, [], [], '2024-04-22')
    mid = originals[0].claim_id
    for role in ('planner', 'verifier'):
        dispatcher.register_session(role, role)
        dispatcher.dispatch({'session_id': role, 'role': role, 'run_id': 'review', 'resource_lock': 'lock',
                             'call_id': role, 'tool_name': 'mj_submit_binding', 'params': {
                                 'mention_id': mid, 'binding': {'company': '甲公司', 'account': account,
                                 'period': period, 'scope': '合并', 'relation': relation, 'base_period': base}}})
    bound = dispatcher.freeze_bindings()[0]
    return dispatcher, bound


@pytest.mark.parametrize('subject', ['乙公司', '乙科技', '乙', '甲公司及乙公司', '甲公司与乙公司', '甲公司的母公司'])
def test_explicit_other_parallel_or_related_issuer_cannot_be_reassigned_to_selected_company(subject):
    source = subject + '2023年合并营业收入100元。'
    dispatcher, bound = dispatcher_binding(source)
    assert not dispatcher.agreements[bound.claim_id]['agreed']
    result = Judge(dispatcher.store, '2024-04-22', require_dual_binding=True).verify(bound)
    assert result.status == 'abstained' and result.abstention_code == 'X3'
    # Pure Judge has the same protection even without a model or dispatcher.
    original = freeze_source_dimensions(extract_mentions(source, '甲公司')[0])
    assert Judge(dispatcher.store, '2024-04-22').verify(original).abstention_code == 'X3'


def test_local_issuer_subject_is_checked_per_numeric_occurrence():
    source = '甲公司2023年合并营业收入100元，乙公司2023年合并营业收入80元。'
    originals = [freeze_source_dimensions(c) for c in extract_mentions(source, '甲公司')]
    assert source_dimensions_from_text(originals[0])['company_provenance'] == 'explicit_source_subject'
    assert source_binding_issue(originals[0]) is None
    assert source_binding_issue(originals[1])[0] == 'X3'


@pytest.mark.parametrize('prefix', ['1. ', '1）', '（1）', '一、', '1.1. '])
def test_numbered_company_pronoun_uses_locked_issuer_context_without_claiming_source_name(prefix):
    dispatcher, bound = dispatcher_binding(prefix + '公司合并口径2023年营业收入100元。')
    assert dispatcher.agreements[bound.claim_id]['agreed']
    dimensions = source_dimensions_from_text(bound)
    assert dimensions['company'] == '甲公司' and dimensions['company_provenance'] == 'host_locked_issuer_context'
    assert not dimensions['explicit']['company'] and not dimensions['company_source_explicit']
    assert Judge(dispatcher.store, '2024-04-22', require_dual_binding=True).verify(bound).status == 'verified'


def test_corporate_full_name_alias_and_name_connector_are_not_fake_other_companies():
    original = freeze_source_dimensions(extract_mentions('北京甲公司股份有限公司2023年合并营业收入100元。', '甲公司')[0])
    assert source_binding_issue(original) is None
    connector = freeze_source_dimensions(extract_mentions('和而泰2023年合并营业收入100元。', '和而泰')[0])
    assert source_binding_issue(connector) is None


def test_explicit_current_and_comparison_years_never_freeze_last_year_as_current():
    source = '公司2024年合并营业收入较2023年增长20%。'
    original = freeze_source_dimensions(extract_mentions(source, '甲公司')[0])
    dimensions = source_dimensions_from_text(original)
    assert dimensions['period'] == '2024Y' and dimensions['base_period'] == '2023Y'
    assert dimensions['relation'] == 'yoy' and dimensions['period_comparison_explicit']
    assert source_binding_issue(replace(original, period='2023Y', base_period='2022Y'))[0] == 'X3'
    assert source_binding_issue(replace(original, period='2024Y', base_period='2023Y')) is None


def test_nonadjacent_growth_periods_do_not_borrow_ordinary_year_on_year_formula():
    original = freeze_source_dimensions(extract_mentions('公司2024年合并营业收入较2022年增长20%。', '甲公司')[0])
    dimensions = source_dimensions_from_text(original)
    assert dimensions['period'] == '2024Y' and dimensions['base_period'] == '2022Y'
    assert dimensions['relation'] == '' and dimensions['unsupported_source_relation']
    assert source_binding_issue(original)[0] == 'X3'


@pytest.mark.parametrize('qualifier', ['不是', '并非', '不等于', '不低于', '不少于', '不超过', '至少'])
def test_unregistered_numeric_comparison_and_negation_never_become_equal_value(qualifier):
    source = f'甲公司2023年合并营业收入{qualifier}100元。'
    dispatcher, bound = dispatcher_binding(source)
    assert not dispatcher.agreements[bound.claim_id]['agreed']
    dimensions = source_dimensions_from_text(bound)
    assert dimensions['relation'] == '' and dimensions['unsupported_source_relation']
    assert Judge(dispatcher.store, '2024-04-22', require_dual_binding=True).verify(bound).abstention_code == 'X3'


@pytest.mark.parametrize('word,amount', [('超过', '101'), ('不足', '99'), ('不到', '99')])
def test_registered_positive_hedges_keep_source_identity_and_use_relative_intervals(word, amount):
    source = f'甲公司2023年合并营业收入{word}100元。'
    dispatcher, bound = dispatcher_binding(source)
    assert dispatcher.agreements[bound.claim_id]['agreed']
    dimensions = source_dimensions_from_text(bound)
    assert dimensions['relation'] == 'value' and not dimensions.get('unsupported_source_relation')
    store = FactStore([Fact('source', '甲公司', 'REVENUE', '2023Y', value=Decimal(amount),
                            publish_date='2024-04-01')])
    result = Judge(store, '2024-04-22', require_dual_binding=True).verify(bound)
    assert result.status == 'verified' and 'A2' in result.advisories


def test_previous_clause_period_comparison_cannot_poison_later_absolute_margin():
    source = '甲公司2023年合并营业收入较2022年增长20%，2024年毛利率25%。'
    original = freeze_source_dimensions(extract_mentions(source, '甲公司')[-1])
    dimensions = source_dimensions_from_text(original)
    assert dimensions['period'] == '2024Y' and dimensions['relation'] == 'value'
    assert dimensions['base_period'] == '' and not dimensions.get('period_comparison_explicit')
    store = FactStore([Fact('old', '甲公司', 'GROSS_MARGIN', '2023Y', value=Decimal('.25'),
                           display_unit='%', publish_date='2024-04-01'),
                      Fact('new', '甲公司', 'GROSS_MARGIN', '2024Y', value=Decimal('.30'),
                           display_unit='%', publish_date='2025-04-01')])
    assert Judge(store, '2025-04-22').verify(original).status == 'violated'
    poisoned = replace(original, period='2023Y', base_period='2022Y', binding_status='agreed')
    assert Judge(store, '2025-04-22', require_dual_binding=True).verify(poisoned).abstention_code == 'X3'


def test_ambiguous_shared_current_and_baseline_years_do_not_donate_last_year():
    source = '甲公司2024年合并营业收入较2023年增长20%，毛利率25%。'
    original = freeze_source_dimensions(extract_mentions(source, '甲公司')[-1])
    dimensions = source_dimensions_from_text(original)
    assert dimensions['period'] == '' and dimensions['base_period'] == ''
    assert source_binding_issue(original)[0] == 'X3'
