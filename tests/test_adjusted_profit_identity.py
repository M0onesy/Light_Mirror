"""Adjusted parent profit cannot borrow its shorter unadjusted label."""
from decimal import Decimal

import pytest

from mingjing.facts import FactStore
from mingjing.judge import Judge
from mingjing.mentions import extract_mentions
from mingjing.models import Fact
from mingjing.source_semantics import freeze_source_dimensions
from mingjing.taxonomy import exact_row_label


@pytest.mark.parametrize('label,account,nr', [
    ('扣非归母净利润', 'NP_PARENT_ADJ', '扣非'),
    ('扣非后的归母净利润', 'NP_PARENT_ADJ', '扣非'),
    ('未扣非归母净利润', 'NP_PARENT', '未扣非'),
])
def test_exact_adjustment_label_is_used_in_prose_and_source_rows(label, account, nr):
    text = '甲公司2024年合并口径' + label + '为100万元。'
    claim = freeze_source_dimensions(extract_mentions(text, '甲公司')[0])
    assert claim.account == account and nr in claim.caliber_hint
    assert exact_row_label(label)[1] == account
    facts = [Fact('adjusted', '甲公司', 'NP_PARENT_ADJ', '2024Y', nr='扣非',
                  value=Decimal('1000000'), publish_date='2025-03-01', evidence=('adjusted-cell',)),
             Fact('unadjusted', '甲公司', 'NP_PARENT', '2024Y',
                  value=Decimal('1200000'), publish_date='2025-03-01', evidence=('unadjusted-cell',))]
    result = Judge(FactStore(facts), '2025-03-31').verify(claim)
    assert result.status == ('verified' if account == 'NP_PARENT_ADJ' else 'violated')
    assert result.operands[0]['account'] == account


def test_adjustment_qualifier_does_not_leak_across_financial_occurrences():
    text = ('甲公司2024年合并口径扣非归母净利润100万元，归母净利润120万元，'
            '营业收入1000万元，加权平均净资产收益率（扣非后）5%。')
    claims = extract_mentions(text, '甲公司')
    assert [claim.account for claim in claims] == ['NP_PARENT_ADJ', 'NP_PARENT', 'REVENUE', 'ROE']
    assert [claim.caliber_hint[-1] for claim in claims] == ['扣非', '未扣非', '未扣非', '扣非']
