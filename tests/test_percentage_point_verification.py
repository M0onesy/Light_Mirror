"""An explicit rate delta uses the locked rule and source-bound operands."""
from dataclasses import replace
from decimal import Decimal

import pytest

from mingjing.facts import FactStore
from mingjing.financial_tools import FinancialDispatcher, BIND_RELATIONS, TOOL_SCHEMAS, ToolDenied
from mingjing.judge import Judge
from mingjing.mentions import extract_mentions
from mingjing.models import Fact
from mingjing.source_semantics import freeze_source_dimensions, source_dimensions_from_text


SOURCE = '甲公司2023年合并口径毛利率较2022年上升5.0个百分点。'


def parsed(source=SOURCE):
    claims = extract_mentions(source, '甲公司')
    assert len(claims) == 1
    return freeze_source_dimensions(claims[0])


def fact(id, period, value, *, account='GROSS_MARGIN', document='annual-2023', **changes):
    defaults = dict(value=Decimal(value), source_doc=document, publish_date='2024-04-01',
                    display_unit='%', decimals=2, evidence=(f'{id}:p1:0,0,1,1:hash',))
    defaults.update(changes)
    return Fact(id, '甲公司', account, period, **defaults)


def store(current='.25', base='.20'):
    return FactStore([fact('current', '2023Y', current), fact('base', '2022Y', base)])


@pytest.mark.parametrize('source,current,base,expected', [
    (SOURCE, '.25', '.20', '.050'),
    ('甲公司2023年合并口径毛利率较2022年下降5.0个百分点。', '.20', '.25', '-.050'),
    ('甲公司2023年合并口径毛利率较2022年下降-5.0个百分点。', '.20', '.25', '-.050'),
    ('甲公司2023年合并口径毛利率同比提高5.0个百分点。', '.25', '.20', '.050'),
    ('甲公司2023年合并口径毛利率同比下降5.0个百分点。', '.20', '.25', '-.050'),
    ('甲公司2023年合并口径毛利率较上年提高5.0个百分点。', '.25', '.20', '.050'),
    ('甲公司2023年合并口径资产负债率较2022年提高5.0个百分点。', '.25', '.20', '.050'),
])
def test_exact_source_direction_and_baseline_reach_locked_difference_rule(source, current, base, expected):
    claim = parsed(source)
    assert claim.relation == 'pp_difference' and claim.period == '2023Y' and claim.base_period == '2022Y'
    assert Decimal(claim.candidate_value) == Decimal(expected)
    rate_store = FactStore([fact('current', '2023Y', current, account=claim.account),
                           fact('base', '2022Y', base, account=claim.account)])
    result = Judge(rate_store, '2024-04-22').verify(claim)
    assert result.status == 'verified' and Decimal(result.expected) == Decimal(expected)
    assert result.rule_ids == ['FIN-pp-difference']
    assert result.display_value.endswith('百分点') and not result.display_value.endswith('%')
    assert len(result.operands) == 2 and len(result.evidence) == 2
    assert result.expected_interval and result.claim_interval and result.source_precision
    proof = source_dimensions_from_text(claim)
    assert proof['period'] == '2023Y' and proof['base_period'] == '2022Y'
    assert proof['relation'] == 'pp_difference'
    assert not {'candidate_value', 'value', 'expected', 'formula', 'verdict'} & set(proof)


def test_quarter_relative_difference_uses_only_the_adjacent_registered_base():
    claim = parsed('甲公司2023Q2合并口径毛利率环比上升5.0个百分点。')
    assert claim.period == '2023Q2' and claim.base_period == '2023Q1'
    result = Judge(FactStore([fact('current', '2023Q2', '.25'), fact('base', '2023Q1', '.2')]),
                   '2024-04-22').verify(claim)
    assert result.status == 'verified'


@pytest.mark.parametrize('source', [
    '甲公司2023年合并口径毛利率上升5.0个百分点。',
    '甲公司合并口径毛利率较上年上升5.0个百分点。',
    '甲公司2023年合并口径毛利率较2022年为5.0个百分点。',
    '甲公司2023年合并口径营业收入较2022年上升5.0个百分点。',
    '甲公司2023年合并口径ROE较2022年上升5.0个百分点。',
    '甲公司2023年合并口径净利率较2022年上升5.0个百分点。',
    '甲公司2023年合并口径毛利率较2022H1上升5.0个百分点。',
    '甲公司2023年合并口径毛利率同比环比上升5.0个百分点。',
])
def test_missing_authorized_definition_period_or_direction_cannot_borrow_available_facts(source):
    result = Judge(store(), '2024-04-22').verify(parsed(source))
    assert result.status == 'abstained' and result.abstention_code in {'X2', 'X3'}


@pytest.mark.parametrize('source', [
    '甲公司2023年合并口径毛利率较2022年上升-5.0个百分点。',
    '甲公司2023年合并口径毛利率较2022年下降+5.0个百分点。',
])
def test_direction_sign_conflict_is_visible(source):
    result = Judge(store(), '2024-04-22').verify(parsed(source))
    assert result.status == 'abstained' and result.abstention_code == 'X3'


@pytest.mark.parametrize('change', [
    {'relation': 'yoy'}, {'relation': 'value'}, {'period': '2022Y'},
    {'base_period': '2021Y'}, {'account': 'DEBT_RATIO'}, {'unit': '%'},
])
def test_matching_model_substitutions_do_not_rewrite_source_delta(change):
    claim = replace(parsed(), **change, binding_status='agreed')
    result = Judge(store(), '2024-04-22', require_dual_binding=True).verify(claim)
    assert result.status == 'abstained' and result.abstention_code == 'X3'


def test_wrong_delta_is_e5_and_supporting_operands_are_the_original_cells():
    result = Judge(store(), '2024-04-22').verify(parsed(SOURCE.replace('5.0', '8.0')))
    assert result.status == 'violated' and result.error_type == 'E5'
    assert Decimal(result.expected) == Decimal('.05')
    assert {operand['fact_id'] for operand in result.operands} == {'current', 'base'}
    assert result.correction and result.correction['source']


@pytest.mark.parametrize('current,base', [('.05', '0'), ('-.05', '-.10')])
def test_percentage_point_subtraction_does_not_apply_positive_growth_denominator_rule(current, base):
    result = Judge(store(current, base), '2024-04-22').verify(parsed())
    assert result.status == 'verified' and Decimal(result.expected) == Decimal('.05')


def test_same_report_base_rejects_an_older_report_even_with_matching_amount():
    facts = [fact('current', '2023Y', '.25'), fact('base', '2022Y', '.20', document='annual-2022')]
    result = Judge(FactStore(facts), '2024-04-22').verify(parsed())
    assert result.status == 'abstained' and result.abstention_code == 'X1'


@pytest.mark.parametrize('changes', [{'publish_date': ''}, {'parse_conf': '.5'}])
def test_unknown_date_or_low_confidence_rate_cannot_be_bypassed_by_formula(changes):
    facts = [fact('current', '2023Y', '.25', **changes), fact('base', '2022Y', '.20')]
    result = Judge(FactStore(facts), '2024-04-22').verify(parsed())
    assert result.status == 'abstained' and result.abstention_code == 'X4'


def test_conflicting_disclosed_rate_stops_formula_fallback():
    facts = [fact('current', '2023Y', '.25'), fact('conflict', '2023Y', '.35'),
             fact('base', '2022Y', '.20')]
    result = Judge(FactStore(facts), '2024-04-22').verify(parsed())
    assert result.status == 'abstained' and result.abstention_code == 'X4'


def test_two_registered_ratio_calculations_keep_all_four_amount_operands():
    facts = [fact('rev-now', '2023Y', '100', account='REVENUE', display_unit='元', decimals=2),
             fact('cost-now', '2023Y', '75', account='COST', display_unit='元', decimals=2),
             fact('rev-base', '2022Y', '100', account='REVENUE', display_unit='元', decimals=2),
             fact('cost-base', '2022Y', '80', account='COST', display_unit='元', decimals=2)]
    result = Judge(FactStore(facts), '2024-04-22').verify(parsed())
    assert result.status == 'verified' and Decimal(result.expected) == Decimal('.05')
    assert {operand['fact_id'] for operand in result.operands} == {fact.fact_id for fact in facts}
    assert result.expected_interval and len(result.evidence) == 4
    assert '当期依据：FIN-gross-margin' in result.basis
    assert result.rule_ids == ['FIN-pp-difference', 'FIN-gross-margin']


def test_plain_roe_cannot_borrow_weighted_but_source_explicit_weighted_rate_can():
    claim = parsed('甲公司2023年合并口径加权平均净资产收益率较2022年上升5.0个百分点。')
    result = Judge(FactStore([fact('current', '2023Y', '.25', account='ROE'),
        fact('base', '2022Y', '.2', account='ROE')]), '2024-04-22').verify(claim)
    assert result.status == 'verified'


def test_touching_open_disclosure_intervals_remain_a_violation():
    # Source rates imply delta [0.0499, 0.0501), while this claim is
    # [0.0501, 0.0503). Sharing only that excluded endpoint is insufficient.
    claim = parsed(SOURCE.replace('5.0', '5.02'))
    facts = [fact('current', '2023Y', '.25', decimals=2), fact('base', '2022Y', '.2', decimals=2)]
    result = Judge(FactStore(facts), '2024-04-22').verify(claim)
    assert result.status == 'violated' and result.error_type == 'E5'


def test_operand_conflict_cannot_be_erased_by_derived_rate_or_difference():
    facts = [fact('rev-now', '2023Y', '100', account='REVENUE', display_unit='元'),
             fact('cost-now', '2023Y', '75', account='COST', display_unit='元'),
             fact('cost-conflict', '2023Y', '74', account='COST', display_unit='元'),
             fact('rev-base', '2022Y', '100', account='REVENUE', display_unit='元'),
             fact('cost-base', '2022Y', '80', account='COST', display_unit='元')]
    result = Judge(FactStore(facts), '2024-04-22').verify(parsed())
    assert result.status == 'abstained' and result.abstention_code == 'X4'


def version_facts():
    return [fact('old-current', '2023Y', '.25', document='old-report', publish_date='2024-03-01'),
            fact('old-base', '2022Y', '.20', document='old-report', publish_date='2024-03-01'),
            fact('new-current', '2023Y', '.28', document='new-report', publish_date='2024-04-15', version='更正后'),
            fact('new-base', '2022Y', '.22', document='new-report', publish_date='2024-04-15', version='更正后')]


def test_explicit_legal_old_source_keeps_its_delta_and_only_advises_a1():
    claim = replace(parsed(), declared_source='old-report')
    result = Judge(FactStore(version_facts()), '2024-04-22').verify(claim)
    assert result.status == 'verified' and Decimal(result.expected) == Decimal('.05')
    assert 'A1' in result.advisories and 'E9-version' not in result.rule_ids


def test_explicit_latest_word_with_a_superseded_reference_is_e9_and_uses_new_same_report_base():
    claim = replace(parsed(SOURCE.replace('毛利率', '毛利率最新披露值')), declared_source='old-report')
    result = Judge(FactStore(version_facts()), '2024-04-22').verify(claim)
    assert result.status == 'violated' and result.error_type == 'E9'
    assert Decimal(result.expected) == Decimal('.06') and 'E9-version' in result.rule_ids
    assert {operand['fact_id'] for operand in result.operands} == {'old-current', 'new-current', 'new-base'}


def test_native_tools_allow_independent_pp_binding_but_reject_forged_baseline():
    claim = parsed()
    dispatcher = FinancialDispatcher('run', 'lock', [claim], store().facts, [], [], '2024-04-22')
    for role in ('planner', 'verifier', 'coordinator'):
        dispatcher.register_session(role, role)
    def call(role, tool, params, id):
        return dispatcher.dispatch(dict(session_id=role, role=role, run_id='run', resource_lock='lock',
            call_id=id, tool_name=tool, params=params))
    binding = dict(company='甲公司', account='GROSS_MARGIN', period='2023Y', scope='合并',
                   relation='pp_difference', base_period='2022Y')
    assert 'pp_difference' in BIND_RELATIONS
    assert 'pp_difference' in TOOL_SCHEMAS['mj_submit_binding']['properties']['binding']['properties']['relation']['enum']
    for role in ('planner', 'verifier'):
        call(role, 'mj_submit_binding', {'mention_id': claim.claim_id, 'binding': binding}, role)
    bound = dispatcher.freeze_bindings()[0]
    assert bound.binding_status == 'agreed'
    read = call('coordinator', 'mj_get_fact', {'binding_id': claim.claim_id}, 'facts')
    assert read['required_rule_ids'] == ['FIN-pp-difference']
    assert read['query_dimensions']['relation'] == 'pp_difference'
    assert read['query_dimensions']['base_period'] == '2022Y'
    call('coordinator', 'mj_get_rule', {'rule_id': 'FIN-pp-difference'}, 'rule')
    result = call('coordinator', 'mj_verify', {'binding_id': claim.claim_id}, 'verify')
    assert result['status'] == 'verified' and result['rule_ids'] == ['FIN-pp-difference']
    dispatcher = FinancialDispatcher('run', 'lock', [claim], store().facts, [], [], '2024-04-22')
    for role in ('planner', 'verifier', 'coordinator'):
        dispatcher.register_session(role, role)
    for role in ('planner', 'verifier'):
        call(role, 'mj_submit_binding', {'mention_id': claim.claim_id,
             'binding': {**binding, 'base_period': '2021Y'}}, role + '-forged')
    dispatcher.freeze_bindings()
    with pytest.raises(ToolDenied, match='no consistent binding'):
        call('coordinator', 'mj_verify', {'binding_id': claim.claim_id}, 'forged-verify')
