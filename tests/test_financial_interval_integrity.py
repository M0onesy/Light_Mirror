from dataclasses import replace
from decimal import Decimal, getcontext, localcontext

import pytest

from mingjing.decimal_math import DecimalInterval, fact_interval, growth_interval, resolution, rounding_interval
from mingjing.facts import FactStore
from mingjing.judge import Judge
from mingjing.models import AtomicClaim, Fact


def fact(identifier='current', account='REVENUE', period='2023Y', value='100', **kwargs):
    return Fact(identifier, '甲公司', account, period, value=Decimal(value),
                publish_date=kwargs.pop('publish_date', '2024-04-01'),
                source_doc=kwargs.pop('source_doc', 'report'),
                evidence=kwargs.pop('evidence', (identifier + ':cell',)), **kwargs)


def claim(value='100', account='REVENUE', period='2023Y', unit='元', decimals=2,
          qualifier='', declared_source=None, **kwargs):
    raw = value + unit
    label = {'REVENUE': '营业收入', 'GROSS_MARGIN': '毛利率', 'DEBT_RATIO': '资产负债率'}.get(account, '财务指标')
    text = f'甲公司{period[:4]}年合并{label}{qualifier}{raw}。'
    canonical = Decimal(value) * resolution(unit, 0)
    return AtomicClaim('claim', text, text.index(raw), text.index(raw)+len(raw),
                       company='甲公司', account=account, period=period,
                       candidate_value=str(canonical), unit=unit, display_decimals=decimals,
                       caliber_hint=('合并', '未扣非'), numeric_text=raw,
                       source_numeric_start=text.index(raw), declared_source=declared_source, **kwargs)


def judge(facts):
    return Judge(FactStore(facts), '2024-04-22')


def test_query_outcomes_distinguish_absence_rejection_and_selected_version():
    cases = [([], 'missing', 'X1'), ([fact(publish_date='2024-05-01')], 'future_only', 'X1'),
             ([fact(publish_date='')], 'unknown_date', 'X4'),
             ([fact(parse_conf='0.1')], 'low_confidence', 'X4'),
             ([fact(), fact('conflict', value='110')], 'conflict', 'X4'),
             ([fact(display_unit='未登记单位')], 'invalid_interval', 'X4')]
    for rows, expected, code in cases:
        store = FactStore(rows)
        outcome = store.query_outcome('甲公司', 'REVENUE', '2023Y', cutoff='2024-04-22')
        assert outcome.status == expected and outcome.abstention_code == code
        assert store.query('甲公司', 'REVENUE', '2023Y', cutoff='2024-04-22') is None
        assert store.query_issue('甲公司', 'REVENUE', '2023Y', cutoff='2024-04-22') == outcome.reason
        if expected in {'future_only', 'unknown_date'}:
            assert not outcome.evidence and not outcome.candidate_fact_ids
    store = FactStore([fact('old-low', parse_conf='0.1', publish_date='2024-03-01'), fact()])
    assert store.query_outcome('甲公司', 'REVENUE', '2023Y', cutoff='2024-04-22').status == 'found'


@pytest.mark.parametrize('rejection', ['conflict', 'low_confidence', 'unknown_date'])
def test_disclosed_ratio_rejection_never_falls_back_to_a_convenient_formula(rejection):
    disclosed = fact('margin', 'GROSS_MARGIN', value='.4', display_unit='%')
    if rejection == 'conflict':
        rows = [disclosed, replace(disclosed, fact_id='conflict', value=Decimal('.5'))]
    elif rejection == 'low_confidence':
        rows = [replace(disclosed, parse_conf='0.1')]
    else:
        rows = [replace(disclosed, publish_date='')]
    rows += [fact(value='100'), fact('cost', 'COST', value='60')]
    for selected in (rows, list(reversed(rows))):
        result = judge(selected).verify(claim('40.0', 'GROSS_MARGIN', unit='%', decimals=1))
        assert result.status == 'abstained' and result.abstention_code == 'X4'
        assert not result.operands


def test_pinned_conflict_cannot_switch_to_another_report():
    rows = [fact(source_doc='cited'), fact('conflict', value='110', source_doc='cited'),
            fact('later', value='100', source_doc='other', publish_date='2024-04-10')]
    result = judge(rows).verify(claim(declared_source='cited'))
    assert result.abstention_code == 'X4' and 'A4' in result.advisories


def test_equal_amount_in_another_period_only_creates_an_unproved_hypothesis():
    result = judge([fact('prior', period='2022Y')]).verify(claim())
    assert result.status == 'abstained' and result.abstention_code == 'X1'
    assert result.diagnostic_hypotheses[0]['error_type'] == 'E2'
    assert result.diagnostic_hypotheses[0]['proof'] == 'numeric_interval_compatibility_only'
    proven = judge([fact('prior', period='2022Y')]).verify(claim(declared_source='prior:cell'))
    assert proven.status == 'violated' and proven.error_type == 'E2'


def test_an_ambiguous_whole_row_reference_cannot_prove_a_period_mismatch():
    rows = [fact('p1', period='2021Y', evidence=('shared-row',)),
            fact('p2', period='2022Y', evidence=('shared-row',))]
    result = judge(rows).verify(claim(declared_source='shared-row'))
    assert result.status == 'abstained' and result.abstention_code == 'X1'


@pytest.mark.parametrize('alternative,expected', [
    (fact('mother', value='80', scope='母公司'), 'E3'),
    (fact('old', value='80', publish_date='2024-03-01', version='原披露'), 'E9'),
])
def test_amount_coincidences_do_not_prove_caliber_or_version_errors(alternative, expected):
    current = fact(value='120', version='更正后')
    result = judge([current, alternative]).verify(claim('80'))
    assert result.error_type == 'E1'
    assert expected in {item['error_type'] for item in result.diagnostic_hypotheses}
    assert result.evidence == list(current.evidence)


def test_explicit_old_source_is_legal_but_an_explicit_latest_source_assertion_is_not():
    rows = [fact('old', value='100', source_doc='old-report', publish_date='2024-03-01'),
            fact('latest', value='120', source_doc='new-report', version='更正后')]
    cited = claim(declared_source='old-report')
    assert judge(rows).verify(cited).status == 'verified'
    assert judge(rows).verify(cited).advisories == ['A1']
    assert judge(rows).verify(replace(cited, text='最新披露营业收入100元。', numeric_text='', source_numeric_start=None)).error_type == 'E9'


def test_fine_value_is_compatible_with_coarse_disclosure_without_becoming_a_fact():
    source = fact(value='496000000', display_unit='亿元', decimals=2)
    result = judge([source]).verify(claim('495671818.88'))
    assert result.status == 'verified' and result.correction is None
    assert Decimal(result.expected_interval['lower']) == Decimal('495500000')
    assert Decimal(result.expected_interval['upper']) == Decimal('496500000')
    assert result.expected_interval['lower_closed'] is True
    assert result.expected_interval['upper_closed'] is False
    assert result.source_precision[0]['resolution'] == '1000000.00'
    assert result.verification_basis == 'disclosure_interval_compatibility'
    assert '精确位数' in result.precision_note
    assert result.operands[0]['value'] == '496000000'


def test_wrong_fine_value_gets_a_source_interval_not_an_invented_exact_correction():
    result = judge([fact(value='496000000', display_unit='亿元', decimals=2)]).verify(claim('490000000.00'))
    assert result.status == 'violated'
    assert result.correction['precision_basis'] == 'source_disclosure_interval'
    assert '至' in result.correction['correct_value'] and '上界不含' in result.correction['correct_value']


def test_equivalent_display_units_and_half_open_boundaries():
    source = fact(value='496000000', display_unit='亿元', decimals=2)
    same = claim('49600', unit='万元', decimals=0)
    assert judge([source]).verify(same).status == 'verified'
    assert fact_interval(source) == rounding_interval(Decimal('496000000'), '百万元', 0)
    left = rounding_interval(Decimal('100'), '元', 0)
    right = rounding_interval(Decimal('101'), '元', 0)
    assert not left.intersects(right)
    assert not rounding_interval(Decimal('-100'), '元', 0).intersects(rounding_interval(Decimal('-101'), '元', 0))
    assert left.intersects(DecimalInterval(Decimal('100.5'), Decimal('100.5'), True, True)) is False


def test_formula_propagates_disclosure_uncertainty_and_records_the_actual_rule():
    rows = [fact(value='1200000', display_unit='万元', decimals=0),
            fact('base', period='2022Y', value='1000000', display_unit='万元', decimals=0)]
    result = judge(rows).verify(claim('20.8', unit='%', decimals=1, relation='yoy', base_period='2022Y'))
    assert result.status == 'verified'  # .208 is within rounding propagation, not a scalar .2 match.
    assert result.rule_ids == ['E5-yoy']
    assert Decimal(result.expected_interval['lower']) < Decimal('.208') < Decimal(result.expected_interval['upper'])
    assert result.expected == '0.2'


def test_zero_touching_or_negative_formula_denominators_abstain():
    for base in [fact('base', period='2022Y', value='0'),
                 fact('base', period='2022Y', value='-1'),
                 fact('base', period='2022Y', value='.004', decimals=2)]:
        result = judge([fact(), base]).verify(claim('20', unit='%', decimals=0, relation='yoy', base_period='2022Y'))
        assert result.abstention_code == 'X2'
    result = judge([fact('liabilities', 'LIABILITIES', value='1'),
                    fact('assets', 'ASSETS', value='.004', decimals=2)]).verify(
                        claim('25', 'DEBT_RATIO', unit='%', decimals=0))
    assert result.abstention_code == 'X2'


def test_division_bounds_round_outward_without_mutating_the_decimal_context():
    before = repr(getcontext())
    interval = DecimalInterval(Decimal(1), Decimal(1), True, True).divide_positive(
        DecimalInterval(Decimal(3), Decimal(3), True, True))
    assert interval.lower < interval.upper
    assert repr(getcontext()) == before
    with localcontext() as context:
        context.prec = 80
        assert interval.lower * 3 <= 1 <= interval.upper * 3


def test_default_approximation_is_relative_two_percent_not_two_half_ulps():
    result = judge([fact(value='101.5')]).verify(claim('100.00', qualifier='约'))
    assert result.status == 'verified' and result.advisories == ['A2']
    assert Decimal(result.claim_interval['lower']) == Decimal('97.995')
    assert Decimal(result.claim_interval['upper']) == Decimal('102.005')
    narrowed = Judge(FactStore([fact(value='101.5')]), '2024-04-22', hedge_relative='0.005').verify(
        claim('100.00', qualifier='约'))
    assert narrowed.status == 'violated'


@pytest.mark.parametrize('word,source,expected', [('逾', '101', 'verified'), ('不到', '99', 'verified'),
                                                ('超过', '99', 'violated'), ('接近', '101', 'violated')])
def test_registered_positive_directional_hedges(word, source, expected):
    result = judge([fact(value=source)]).verify(claim('100', qualifier=word, decimals=0))
    assert result.status == expected and 'A2' in result.advisories


@pytest.mark.parametrize('value', ['-100', '0'])
@pytest.mark.parametrize('word', ['约', '逾', '接近'])
def test_unsigned_hedge_definitions_are_not_invented_for_zero_or_negative_values(value, word):
    result = judge([fact(value=value)]).verify(claim(value, qualifier=word, decimals=0))
    assert result.status == 'abstained' and result.abstention_code == 'X2'
    assert 'A2' in result.advisories


def test_scope_and_version_column_ranks_distinguish_before_and_after():
    rows = [fact('before', value='80', version='调整前'), fact('after', value='100', version='调整后')]
    assert FactStore(rows).query('甲公司', 'REVENUE', '2023Y').fact_id == 'after'
