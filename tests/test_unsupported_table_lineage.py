"""Unsupported financial captions retain occurrences and source E columns."""
from dataclasses import replace
from decimal import Decimal

import pytest

from mingjing.facts import FactStore
from mingjing.financial_tools import BIND_ACCOUNTS
from mingjing.judge import Judge
from mingjing.models import Fact
from mingjing.source_semantics import freeze_source_dimensions, semantic_review_required
from mingjing.table_mentions import extract_table_mentions


def extract(text):
    return extract_table_mentions(text, [{'page': 42, 'start': 0, 'end': len(text)}], [], '公司甲', '2099Y')


def test_capital_increase_keeps_real_header_periods_but_never_becomes_balance():
    text = ('现金流量表(百万元)\n2022A\n2023A\n2024E\n2025E\n2026E\n'
            '资本公积增加\n6\n284\n0\n0\n0\n数据来源：披露原文\n')
    claims = extract(text)
    assert len(claims) == 5
    assert [claim.period for claim in claims] == ['2022Y', '2023Y', '2024Y', '2025Y', '2026Y']
    assert [claim.modality for claim in claims] == ['fact', 'fact', 'forecast', 'forecast', 'forecast']
    assert {claim.account for claim in claims} == {'CAPITAL_RESERVE_CHANGE_UNSPECIFIED'}
    assert {claim.parse_status for claim in claims} == {'parsed'}
    assert all(semantic_review_required(c) for c in claims)
    assert [Decimal(c.candidate_value) for c in claims] == [Decimal(v) for v in ['6000000', '284000000', '0', '0', '0']]
    assert all(Judge(FactStore([]), '2024-04-22').verify(c).abstention_code == 'X3' for c in claims[:2])
    assert {claim.unit for claim in claims} == {'百万元'}
    assert {claim.caliber_hint for claim in claims} == {('未知', '未扣非')}
    assert all(text[claim.start:claim.end] == claim.numeric_text for claim in claims)
    for claim in claims:
        source = claim.table_context
        assert source['row_label'] == '资本公积增加'
        assert source['period_source'] == 'source_header'
        assert source['modality_source'] == ('explicit_forecast_header' if claim.modality == 'forecast' else 'source_header')
        assert [span['text'] for span in source['headers']] == ['2022A', '2023A', '2024E', '2025E', '2026E']
        assert all(text[span['start']:span['end']] == span['text'] for span in source['headers'])


def test_known_balance_and_growth_row_remain_authorized():
    text = ('合并资产负债表(万元)\n2022A\n2023A\n2024E\n'
            '资本公积\n6\n284\n300\nYOY(%)\n20\n4633.3\n5.6\n')
    claims = extract(text)
    assert len(claims) == 6
    assert {claim.account for claim in claims} == {'CAPITAL_RESERVE'}
    assert {claim.parse_status for claim in claims} == {'parsed'}
    assert {claim.caliber_hint for claim in claims} == {('合并', '未扣非')}
    assert all(claim.relation == 'yoy' for claim in claims[3:])


def test_unsupported_flow_resets_previous_account_and_cannot_donate_capital_balance():
    text = ('合并现金流量表(万元)\n2022A\n2023A\n2024E\n'
            '资本公积\n6\n284\n300\n资本公积增加\n2\n10\n20\nYOY(%)\n20\n30\n40\n')
    claims = extract(text)
    assert len(claims) == 9
    assert {claim.account for claim in claims[3:]} == {'CAPITAL_RESERVE_CHANGE_UNSPECIFIED'}
    assert all(claim.parse_status == 'parsed' and semantic_review_required(claim) for claim in claims[3:])
    assert all(claim.account != 'CAPITAL_RESERVE' for claim in claims[3:])
    assert all(claim.relation == 'yoy' for claim in claims[6:])
    assert all(claim.table_context['account_lineage']['label_text'] == '资本公积增加' for claim in claims[6:])
    assert all(Judge(FactStore([]), '2024-04-22').verify(c).abstention_code == 'X3'
               for c in claims[3:] if c.modality == 'fact')
    assert all(claim.period != '2099Y' for claim in claims)


def test_unsupported_unit_or_segment_qualifier_does_not_relabel_known_account():
    text = ('合并利润表\n2022A\n2023A\n2024E\n'
            '营业收入（美元）\n100\n200\n300\n营业收入分产品\n10\n20\n30\n')
    claims = extract(text)
    assert len(claims) == 6
    assert {claim.account for claim in claims} == {'UNKNOWN'}
    assert {claim.parse_status for claim in claims} == {'unresolved'}
    assert not any(semantic_review_required(c) for c in claims)
    assert all(Judge(FactStore([]), '2024-04-22').verify(c).abstention_code == 'X6' for c in claims)
    assert [claim.modality for claim in claims] == ['fact', 'fact', 'forecast'] * 2
    assert not any(claim.account == 'REVENUE' for claim in claims)


def test_arbitrary_prose_and_date_text_do_not_become_aligned_rows():
    text = ('合并利润表(万元)\n2022A\n2023A\n2024E\n'
            '营业收入预计未来达到100万元\n1\n2\n3\n'
            '资本公积增加，公司经营改善。\n4\n5\n6\n')
    assert extract(text) == []


def test_unknown_caption_without_column_proof_is_still_unresolved():
    text = '合并现金流量表(万元)\n资本公积增加\n6\n284\n0\n'
    claims = extract(text)
    assert len(claims) == 3
    assert {claim.period for claim in claims} == {''}
    assert {claim.modality for claim in claims} == {'fact'}
    assert {claim.account for claim in claims} == {'CAPITAL_RESERVE_CHANGE_UNSPECIFIED'}
    assert {claim.parse_status for claim in claims} == {'unresolved'}
    assert not any(semantic_review_required(c) for c in claims)
    assert all(Judge(FactStore([]), '2024-04-22').verify(c).abstention_code == 'X6' for c in claims)


def test_turnover_decimal_cells_keep_history_and_never_become_receivable_balances():
    from mingjing.coverage import CoverageLedger
    text = ('主要财务比率\n2022A\n2023A\n2024E\n'
            '应收账款周转率\n10.7\n11.7\n13.0\n应付账款周转率\n4.4\n4.8\n5.0\n')
    ledger, claims = CoverageLedger.build(text, [{'page': 42, 'start': 0, 'end': len(text)}], [], '公司甲', '2099Y')
    assert len(claims) == 4
    assert [claim.account for claim in claims] == ['TURNOVER_AR_UNSPECIFIED'] * 2 + ['TURNOVER_AP_UNSPECIFIED'] * 2
    assert {claim.parse_status for claim in claims} == {'parsed'}
    assert all(c.unit == '倍' and semantic_review_required(c) for c in claims)
    assert all(Judge(FactStore([]), '2024-04-22').verify(c).abstention_code == 'X2' for c in claims)
    assert [claim.period for claim in claims] == ['2022Y', '2023Y'] * 2
    assert {claim.numeric_text for claim in claims} == {'10.7', '11.7', '4.4', '4.8'}
    assert ledger.to_dict()['counts']['out_of_scope'] == 2
    assert all(entry['reason'] == 'forecast_table_column' for entry in ledger.entries if entry['status'] == 'out_of_scope')


@pytest.mark.parametrize('label,annotation,source_code,canonical,expected_code', [
    ('资本公积增加', '(万元)', 'CAPITAL_RESERVE_CHANGE_UNSPECIFIED', 'CAPITAL_RESERVE', 'X3'),
    ('应收账款周转率', '(倍)', 'TURNOVER_AR_UNSPECIFIED', 'AR', 'X2'),
    ('应付账款周转率', '(倍)', 'TURNOVER_AP_UNSPECIFIED', 'AP', 'X2'),
    ('ROIC', '(%)', 'ROIC_UNSPECIFIED', 'ROE', 'X2'),
])
def test_matching_balance_or_roe_cannot_authorize_source_semantic_substitution(
        monkeypatch, label, annotation, source_code, canonical, expected_code):
    text = '合并现金流量表(万元)\n2023A\n' + label + annotation + '\n10.7\n'
    claims = extract(text)
    assert len(claims) == 1
    claim = claims[0]
    assert claim.account == source_code and claim.parse_status == 'parsed' and semantic_review_required(claim)
    assert source_code not in BIND_ACCOUNTS
    assert canonical in BIND_ACCOUNTS
    assert text[claim.start:claim.end] == claim.numeric_text
    frozen = freeze_source_dimensions(claim)
    matching = Fact('coincidental-balance', '公司甲', canonical, '2023Y',
                    value=Decimal(claim.candidate_value), publish_date='2024-04-01')
    store = FactStore([matching])

    def cannot_query_matching_fact(*args, **kwargs):
        raise AssertionError('Source semantic uncertainty must be resolved before fact lookup')

    for method in ('query', 'query_outcome', 'versions'):
        monkeypatch.setattr(store, method, cannot_query_matching_fact)
    result = Judge(store, '2024-04-22', require_dual_binding=True).verify(
        replace(frozen, account=canonical, binding_status='agreed'))
    assert result.status == 'abstained' and result.abstention_code == expected_code
    assert result.expected is None and not result.operands and not result.evidence
