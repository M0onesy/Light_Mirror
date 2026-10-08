"""Explicit market targets retain source lineage without consuming history."""
import pytest

from mingjing.coverage import CoverageLedger
from mingjing.mentions import VALUE_RE
from mingjing.source_modality import prose_modality_exclusions


def build(text):
    return CoverageLedger.build(text, [{'id': 'p', 'page': 1, 'start': 0, 'end': len(text)}], [], '甲公司', '2023Y')


@pytest.mark.parametrize('source,amount', [
    ('图45：超算服务市场规模预计2025年达到466亿元。', '466亿元'),
    ('超算服务市场规模将预计2025年达到466亿元。', '466亿元'),
    ('预计2025年超算服务市场规模达到466亿元。', '466亿元'),
    ('公司营业收入预计2025年达到466亿元。', '466亿元'),
    ('全球市场份额预计2025年达到20%。', '20%'),
    ('市场渗透率预计2025年上升至20%。', '20%'),
])
def test_target_before_or_after_forecast_predicate_is_retained_as_addressed_outside_scope(source, amount):
    ledger, claims = build(source)
    target = next(row for row in ledger.entries if row['text'].strip() == amount)
    assert target['status'] == 'out_of_scope' and target['reason'] == 'forecast_prose_lineage'
    assert target['claim_id'] is None and not any(claim.start == target['start'] for claim in claims)
    assert len(ledger.entries) == len(list(VALUE_RE.finditer(source)))
    basis = target['modality_basis']
    assert basis['predicate']['text'] == '预计' and basis['declared_periods'][0]['text'] == '2025年'
    assert source[slice(*basis['occurrence_span'])] == target['text']
    if 'measure_subject' in basis:
        subject = basis['measure_subject']
        assert source[subject['start']:subject['end']] == subject['text']
        assert subject['kind'] == 'directly_preceding_measure_caption'


@pytest.mark.parametrize('source', [
    '市场规模为466亿元。',
    '市场规模预计达到466亿元。',
    '未预计2025年市场规模达到466亿元。',
    '市场规模并未预计2025年达到466亿元。',
    '2023年市场规模为466亿元，预计2025年天气更好。',
    '公司营业收入预计2025年温度达到30度。',
    '市场规模预计模型采用2025年466亿元作为输入。',
    '市场规模\n\n预计2025年达到466亿元。',
    '市场规模预计2025年用户数量达到466万个。',
])
def test_history_ambiguity_negation_and_operational_counts_do_not_receive_forecast_exclusions(source):
    exclusions = prose_modality_exclusions(source, [])
    # A direct undated target is retained as a forecast ledger item; it does
    # not create a financial claim. Negated, historical, operational and model
    # input uses of “预计” remain ordinary text and must not be excluded.
    if source == '市场规模预计达到466亿元。':
        assert exclusions and next(iter(exclusions.values()))['reason'] == 'forecast_prose_without_period'
    else:
        assert not exclusions


def test_precise_target_caption_cannot_delete_adjacent_historical_market_or_issuer_amounts():
    text = ('2021年超算服务市场规模为196.6亿元。图45：超算服务市场规模预计2025年达到466亿元。'
            '2023年合并营业收入100万元。')
    ledger, _ = build(text)
    entries = {row['text'].strip(): row for row in ledger.entries}
    assert entries['466亿元']['status'] == 'out_of_scope'
    assert entries['196.6亿元']['reason'] != 'forecast_prose_lineage'
    assert entries['100万元']['status'] == 'pending' and entries['100万元']['claim_id']
    assert len(ledger.entries) == len(list(VALUE_RE.finditer(text)))


def test_new_historical_clause_resets_market_target_without_deleting_explicit_history():
    text = '市场规模预计2025年达到466亿元，而2023年实际市场规模为200亿元。'
    ledger, _ = build(text)
    entries = {row['text'].strip(): row for row in ledger.entries}
    assert entries['466亿元']['reason'] == 'forecast_prose_lineage'
    assert entries['200亿元']['reason'] != 'forecast_prose_lineage'


def test_market_label_cannot_cross_a_new_financial_table():
    text = '市场规模预计2025年达到466亿元\n合并利润表(万元)\n2022A 2023A\n营业收入\n80\n100\n'
    ledger, claims = build(text)
    assert next(row for row in ledger.entries if row['text'].strip() == '466亿元')['status'] == 'out_of_scope'
    assert {claim.numeric_text.strip() for claim in claims if claim.parse_status == 'parsed'} == {'80', '100'}
