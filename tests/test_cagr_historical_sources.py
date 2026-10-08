"""The locked CAGR rule requires endpoints in the same disclosure version."""
from dataclasses import replace
from decimal import Decimal

import pytest

from mingjing.facts import FactStore
from mingjing.judge import Judge
from mingjing.mentions import extract_mentions
from mingjing.models import Fact
from mingjing.source_semantics import freeze_source_dimensions


COMPANY = '\u7532\u516c\u53f8'


def _claim(relation='cagr'):
    text = COMPANY + '2021-2023\u5e74\u5408\u5e76\u8425\u4e1a\u6536\u5165CAGR\u4e3a10%\u3002'
    claim = freeze_source_dimensions(replace(extract_mentions(text, COMPANY)[-1], binding_status='agreed'))
    return replace(claim, relation=relation)


def _facts(*, historical_date='2022-04-15', base_value='100', base_conf='1', base_source='historical-report'):
    return [
        Fact('base', COMPANY, 'REVENUE', '2021Y', source_doc=base_source,
             publish_date=historical_date, value=Decimal(base_value), parse_conf=base_conf,
             evidence=(base_source + ':p3:revenue',)),
        Fact('current', COMPANY, 'REVENUE', '2023Y', source_doc='current-report',
             publish_date='2024-04-15', value=Decimal('121'), evidence=('current-report:p5:revenue',)),
    ]


def test_explicit_cagr_cannot_splice_matching_audited_endpoints_from_different_reports():
    result = Judge(FactStore(_facts()), '2024-04-22').verify(_claim())
    assert result.status == 'abstained' and result.abstention_code == 'X1'
    assert 'FIN-cagr' in result.reason and '同一报告版本' in result.reason
    assert result.expected is None and result.operands == []


def test_same_report_historical_endpoint_remains_usable_without_amount_matching():
    result = Judge(FactStore(_facts(base_source='current-report')), '2024-04-22').verify(_claim())
    assert result.status == 'verified' and result.rule_ids == ['FIN-cagr']
    assert Decimal(result.expected) == Decimal('0.1')
    assert {row['source_doc'] for row in result.operands} == {'current-report'}
    assert set(result.evidence) == {'current-report:p3:revenue', 'current-report:p5:revenue'}


@pytest.mark.parametrize('date,confidence,code', [
    ('2024-04-23', '1', 'X1'),
    ('', '1', 'X4'),
    ('2022-04-15', '0.5', 'X4'),
])
def test_historical_cagr_endpoint_retains_date_and_quality_gates(date, confidence, code):
    result = Judge(FactStore(_facts(historical_date=date, base_conf=confidence, base_source='current-report')),
                   '2024-04-22').verify(_claim())
    assert result.status == 'abstained'
    assert result.abstention_code == code
    assert result.expected is None


def test_pinned_source_cagr_does_not_silently_add_another_document():
    claim = replace(_claim(), declared_source='current-report')
    result = Judge(FactStore(_facts()), '2024-04-22').verify(claim)
    assert result.status == 'abstained' and result.abstention_code == 'X1'


def test_yoy_lookup_still_requires_baseline_in_current_report():
    current = _facts()[1]
    baseline = replace(_facts()[0], period='2022Y')
    claim = replace(_claim(), relation='yoy', base_period='2022Y')
    outcome = Judge(FactStore([baseline, current]), '2024-04-22')._base_outcome(
        claim, current, current.scope, current.nr)
    assert outcome.status == 'missing'


def test_cagr_same_document_with_distinct_explicit_version_groups_is_not_comparable():
    facts = _facts(base_source='current-report')
    facts[0] = replace(facts[0], version_group='before-correction')
    facts[1] = replace(facts[1], version_group='after-correction')
    result = Judge(FactStore(facts), '2024-04-22').verify(_claim())
    assert result.status == 'abstained' and result.abstention_code == 'X2'
    assert '版本组' in result.reason and result.expected is None


def test_adjusted_comparative_column_in_same_report_version_is_authorized():
    facts = _facts(base_source='current-report')
    facts[0] = replace(facts[0], version='调整后', version_group='current-report')
    facts[1] = replace(facts[1], version='原披露', version_group='current-report')
    result = Judge(FactStore(facts), '2024-04-22').verify(_claim())
    assert result.status == 'verified' and Decimal(result.expected) == Decimal('0.1')


def test_anonymous_current_operand_does_not_authorize_a_sourced_historical_operand():
    facts = _facts()
    facts[1] = replace(facts[1], source_doc='', source_path='')
    result = Judge(FactStore(facts), '2024-04-22').verify(_claim())
    assert result.status == 'abstained' and result.abstention_code == 'X2'
    assert result.expected is None


@pytest.mark.parametrize('baseline_group,expected_status', [('same-report', 'verified'), ('old-version', 'abstained')])
def test_cagr_of_disclosed_ratios_retains_same_report_version_guard(baseline_group, expected_status):
    text = COMPANY + '2021-2023年合并口径流动比率CAGR为10%。'
    claim = freeze_source_dimensions(replace(extract_mentions(text, COMPANY)[-1], binding_status='agreed'))
    facts = [Fact('ratio-' + period, COMPANY, 'CURRENT_RATIO', period, value=Decimal(value),
                  source_doc='same-report', publish_date='2024-04-15', display_unit='倍',
                  version_group=baseline_group if period == '2021Y' else 'same-report',
                  evidence=('same-report:p5:current-ratio-' + period,))
             for period, value in [('2021Y', '1'), ('2023Y', '1.21')]]
    result = Judge(FactStore(facts), '2024-04-22').verify(claim)
    assert result.status == expected_status
    if expected_status == 'verified':
        assert Decimal(result.expected) == Decimal('.1') and result.rule_ids[0] == 'FIN-cagr'
    else:
        assert result.abstention_code == 'X2' and '版本组' in result.reason
        assert result.expected is None and result.operands == []
