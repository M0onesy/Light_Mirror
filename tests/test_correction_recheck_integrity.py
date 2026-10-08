"""Corrections must be usable drafts without inventing source precision."""
from decimal import Decimal, localcontext

import pytest

from mingjing.compute import display_amount, display_percent
from mingjing.decimal_math import DecimalInterval, round_display_interval
from mingjing.facts import FactStore
from mingjing.judge import Judge
from mingjing.mentions import extract_mentions
from mingjing.models import Fact


def verify(text, facts):
    claims = extract_mentions(text, '甲公司')
    assert claims
    judge = Judge(FactStore(facts), '2025-03-31')
    return [judge.verify(claim) for claim in claims]


@pytest.mark.parametrize('unit,decimals,value', [
    ('亿元', 2, '496000000'), ('万元', 0, '1000000'),
    ('百万元', 1, '-123400000'),
])
def test_finer_wrong_draft_gets_reusable_original_disclosure(unit, decimals, value):
    fact = Fact('source', '甲公司', 'REVENUE', '2024Y', value=Decimal(value),
                display_unit=unit, decimals=decimals, publish_date='2025-03-01',
                evidence=('source-cell',))
    result = verify('甲公司2024年合并口径营业收入为990000000.00元。', [fact])[0]
    assert result.status == 'violated'
    assert result.correction['precision_basis'] == 'source_disclosure_interval'
    corrected = result.correction['replacement_text']
    assert display_amount(fact.value, unit, decimals) in corrected
    assert '上界不含' not in corrected  # The uncertainty stays in the detail field.
    rechecked = verify(corrected, [fact])
    assert len(rechecked) == 1 and rechecked[0].status == 'verified'
    assert rechecked[0].source_precision[0]['decimals'] == decimals
    assert result.correction['source'] == 'source-cell'


@pytest.mark.parametrize('draft,current,expected_word', [
    ('同比下降10%', '1200000.00', '同比增长'),
    ('同比增长10%', '800000.00', '同比下降'),
])
def test_growth_correction_changes_direction_and_rechecks(draft, current, expected_word):
    facts = [Fact('current', '甲公司', 'REVENUE', '2024Y', value=Decimal(current),
                  publish_date='2025-03-01', source_doc='same-report', evidence=('current-cell',)),
             Fact('base', '甲公司', 'REVENUE', '2023Y', value=Decimal('1000000.00'),
                  publish_date='2025-03-01', source_doc='same-report', evidence=('base-cell',))]
    result = verify('甲公司2024年合并口径营业收入' + draft + '。', facts)[0]
    assert result.status == 'violated'
    corrected = result.correction['replacement_text']
    assert expected_word in corrected
    assert all(item.status == 'verified' for item in verify(corrected, facts))


def test_display_decimal_context_is_local_and_deterministic():
    with localcontext() as context:
        context.prec = 2
        before = repr(context)
        assert display_amount(Decimal('-114165954.11'), '万元', 2) == '-11416.60万元'
        assert display_percent(Decimal('0.123456789'), 4) == '12.3457%'
        assert repr(context) == before


def test_formula_correction_does_not_copy_fine_draft_precision():
    facts = [Fact('current', '甲公司', 'REVENUE', '2024Y', value=Decimal('120000000'),
                  display_unit='万元', decimals=0, publish_date='2025-03-01', source_doc='same'),
             Fact('base', '甲公司', 'REVENUE', '2023Y', value=Decimal('100000000'),
                  display_unit='万元', decimals=0, publish_date='2025-03-01', source_doc='same')]
    result = verify('甲公司2024年合并口径营业收入同比增长90.00000000%。', facts)[0]
    assert result.status == 'violated'
    assert result.correction['precision_basis'] == 'propagated_interval_stable_rounding'
    assert int(result.correction['replacement_precision']) < 8
    assert '20.00000000%' not in result.correction['replacement_text']
    assert all(item.status == 'verified' for item in verify(result.correction['replacement_text'], facts))


def test_unstable_formula_correction_stays_an_explicit_interval_proposal():
    facts = [Fact('current', '甲公司', 'REVENUE', '2024Y', value=Decimal('1200000'),
                  display_unit='万元', decimals=0, publish_date='2025-03-01', source_doc='same'),
             Fact('base', '甲公司', 'REVENUE', '2023Y', value=Decimal('1000000'),
                  display_unit='万元', decimals=0, publish_date='2025-03-01', source_doc='same')]
    result = verify('甲公司2024年合并口径营业收入同比增长90.00000000%。', facts)[0]
    assert result.status == 'violated'
    assert result.correction['replacement_status'] == 'interval_requires_review'
    assert result.correction['precision_basis'] == 'propagated_interval_range'
    assert '至' in result.correction['correct_value']
    assert '同比变动率' in result.correction['replacement_text']
    assert all(item.status != 'verified' for item in verify(result.correction['replacement_text'], facts))


@pytest.mark.parametrize('lower,upper,lower_closed,upper_closed,expected', [
    ('1.995','2.005',True,False,'2.00'),
    ('-2.005','-1.995',False,True,'-2.00'),
])
def test_display_interval_respects_open_rounding_ties(lower, upper, lower_closed, upper_closed, expected):
    bounds = DecimalInterval(Decimal(lower), Decimal(upper), lower_closed, upper_closed)
    first, last, _, _ = round_display_interval(bounds, 2)
    assert first == last == Decimal(expected)


def test_percentage_point_correction_replaces_the_wrong_source_direction():
    facts = [Fact('current', '甲公司', 'GROSS_MARGIN', '2024Y', value=Decimal('.25'),
                  display_unit='%', decimals=2, publish_date='2025-03-01', source_doc='same'),
             Fact('base', '甲公司', 'GROSS_MARGIN', '2023Y', value=Decimal('.20'),
                  display_unit='%', decimals=2, publish_date='2025-03-01', source_doc='same')]
    result = verify('甲公司2024年合并口径毛利率较2023年收窄8.00000000个百分点。', facts)[0]
    assert result.status == 'violated'
    corrected = result.correction['replacement_text']
    assert '上升' in corrected and '收窄' not in corrected
    assert all(item.status == 'verified' for item in verify(corrected, facts))
