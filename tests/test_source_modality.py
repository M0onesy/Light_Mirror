"""Source lineage regressions; these are not independent financial gold."""
from decimal import Decimal

from mingjing.coverage import CoverageLedger
from mingjing.facts import FactStore
from mingjing.judge import Judge
from mingjing.mentions import VALUE_RE
from mingjing.source_modality import prose_modality_exclusions
from mingjing.source_semantics import semantic_review_required
from mingjing.table_mentions import extract_table_mentions


def build(text, pages=None):
    pages = pages or [{'id': 'page-1', 'page': 1, 'start': 0, 'end': len(text)}]
    return CoverageLedger.build(text, pages, [], '甲公司', '2023Y')


def occurrences(ledger, value):
    return [row for row in ledger.entries if row['text'].strip() == value]


def test_wrapped_profit_eps_and_ps_keep_precise_forecast_lineage_and_all_occurrences():
    text = ('2023年合并营业收入为100万元，我们预计公司2024-2026 年归\n'
            '母净利润为-0.37/0.07/0.45 亿元，对应EPS 分别为-0.63/0.13/0.77 元/股，对应当\n'
            '前股价的PS 分别为3.6/2.8/2.1 倍。2023年合并经营现金流为80万元。')
    ledger, claims = build(text)
    forecast = [row for row in ledger.entries if row['reason'] == 'forecast_prose_lineage']
    assert len(forecast) == 9
    assert all(row['status'] == 'out_of_scope' and row['claim_id'] is None for row in forecast)
    assert all(not any(c.start == row['start'] for c in claims) for row in forecast)
    assert occurrences(ledger, '100万元')[0]['claim_id']
    assert occurrences(ledger, '80万元')[0]['claim_id']
    assert len(ledger.entries) == len(list(VALUE_RE.finditer(text)))
    assert not any(row['status'] == 'pending' and not row['claim_id'] for row in ledger.entries)
    for row in forecast:
        basis = row['modality_basis']
        assert basis['schema_version'] == 'source-prose-modality.v1'
        assert basis['predicate']['text'] == '预计'
        source = basis['source_span']
        assert text[source['start']:source['end']] == source['text']
        assert source['text'].startswith('预计') and '100万元' not in source['text']
        assert basis['declared_periods'][0]['text'] == '2024-2026 年'
        assert text[slice(*basis['occurrence_span'])] == row['text']


def test_a_new_historical_clause_stops_forecast_without_deleting_either_history():
    text = '2023年合并营业收入100万元，预计2024年营业收入120万元，而公司2023年实际净利润20万元。'
    ledger, _ = build(text)
    assert occurrences(ledger, '120万元')[0]['reason'] == 'forecast_prose_lineage'
    assert occurrences(ledger, '100万元')[0]['claim_id']
    assert occurrences(ledger, '20万元')[0]['claim_id']
    assert occurrences(ledger, '20万元')[0]['status'] == 'pending'
    assert occurrences(ledger, '120万元')[0]['modality_basis']['boundary']['kind'] == 'explicit_historical_clause'


def test_an_explicit_outside_period_stops_lineage_even_without_an_actual_marker():
    text = '我们预计公司2024年营业收入120万元，2023年营业收入100万元。'
    ledger, _ = build(text)
    assert occurrences(ledger, '120万元')[0]['status'] == 'out_of_scope'
    assert occurrences(ledger, '100万元')[0]['claim_id']


def test_comparison_year_does_not_turn_forecast_growth_into_historical_fact():
    text = '预计2024年营业收入120万元，较2023年增长20%，对应EPS为1.5元。'
    ledger, _ = build(text)
    for value in ['120万元', '20%', '1.5元']:
        assert occurrences(ledger, value)[0]['reason'] == 'forecast_prose_lineage'


def test_explicit_historical_comparison_amount_is_protected_but_forecast_growth_is_not():
    text = '预计2024年营业收入120万元，较2023年营业收入100万元增长20%。'
    ledger, _ = build(text)
    assert occurrences(ledger, '100万元')[0]['claim_id']
    assert occurrences(ledger, '100万元')[0]['status'] == 'pending'
    assert occurrences(ledger, '120万元')[0]['status'] == 'out_of_scope'
    assert occurrences(ledger, '20%')[0]['status'] == 'out_of_scope'
    protected = occurrences(ledger, '20%')[0]['modality_basis']['protected_historical_operands']
    assert len(protected) == 1 and protected[0]['text'] == '100万元'


def test_sentence_blank_paragraph_and_page_boundaries_prevent_lineage_leak():
    for separator in ['。', '；', '\n\n']:
        text = '预计2024年营业收入120万元' + separator + '2023年合并净利润20万元。'
        ledger, _ = build(text)
        assert occurrences(ledger, '120万元')[0]['status'] == 'out_of_scope'
        assert occurrences(ledger, '20万元')[0]['claim_id']
    first = '预计2024年营业收入120万元，\n'
    text = first + '合并净利润20万元。'
    pages = [{'id': 'p1', 'page': 1, 'start': 0, 'end': len(first)},
             {'id': 'p2', 'page': 2, 'start': len(first), 'end': len(text)}]
    ledger, _ = build(text, pages)
    assert occurrences(ledger, '120万元')[0]['status'] == 'out_of_scope'
    assert occurrences(ledger, '20万元')[0]['claim_id']


def test_negation_provision_account_and_forecast_metadata_are_not_forecast_exclusions():
    for text in ['公司并未预测2024年营业收入120万元。',
                 '公司预计负债2023年为120万元。',
                 '预测模型采用2024年营业收入100万元作为历史输入。',
                 '模型的预测误差为2023万元。']:
        assert not prose_modality_exclusions(text, [])


def test_undated_forecast_financial_amount_is_not_a_year_or_a_later_historical_amount():
    for value in ('2023万元', '120万元'):
        text = '公司预计营业收入'+value+'，2023年实际营业收入100万元。'
        ledger, _ = build(text)
        row = occurrences(ledger, value)[0]
        assert row['reason'] == 'forecast_prose_without_period'
        assert row['modality_basis']['declared_periods'] == []
        assert occurrences(ledger, '100万元')[0]['claim_id']


def test_market_snapshot_heading_cannot_govern_unrelated_following_rows():
    text = '当前股价(元)\n42.80\n一年最高最低(元)\n80.93/32.99\n营业收入\n100万元\n'
    assert not prose_modality_exclusions(text, [])


def test_explicit_english_forecast_and_valuation_do_not_consume_previous_or_next_history():
    text = ('2023年营业收入100万元，we forecast revenue of 120万元 for 2024-2026.\n'
            '2023年净利润20万元。目标价为30元，2023年净利润40万元。')
    ledger, _ = build(text)
    assert occurrences(ledger, '120万元')[0]['reason'] == 'forecast_prose_lineage'
    assert occurrences(ledger, '30元')[0]['reason'] == 'valuation_prose_lineage'
    assert all(occurrences(ledger, value)[0]['claim_id'] for value in ['100万元', '20万元', '40万元'])


def test_table_title_stops_prose_forecast_and_retains_historical_cells():
    text = ('预计2024年营业收入120万元\n合并利润表(万元)\n2022A 2023A 2024E\n'
            '营业收入\n80\n100\n120\n')
    ledger, claims = build(text)
    history = [c for c in claims if c.numeric_text.strip() in {'80', '100'}]
    assert len(history) == 2 and all(c.parse_status == 'parsed' for c in history)
    assert any(row['reason'] == 'forecast_table_column' for row in ledger.entries)


def test_unsupported_english_rows_keep_forecast_headers_and_historical_x6():
    text = ('主要财务比率\n2022A 2023A 2024E 2025E 2026E\n'
            'EBITDA\n-47\n9\n50\n122\n181\n'
            'ROIC(%)\n-60.4\n-13.8\n-6.5\n2.3\n8.5\n'
            'EV/EBITDA\n-51.8\n247.2\n50.0\n19.9\n12.5\n')
    pages = [{'id': 'p1', 'page': 1, 'start': 0, 'end': len(text)}]
    table = extract_table_mentions(text, pages, [], '甲公司', '2099Y')
    assert len(table) == 15
    # Label recognition records source dimensions, without authorizing a
    # formula. EBITDA still lacks an amount unit; ROIC states percent units.
    assert all(c.account == 'EBITDA_UNSPECIFIED' and c.candidate_value is None
               and c.parse_status == 'unresolved' and not semantic_review_required(c) for c in table[:5])
    assert all(c.account == 'ROIC_UNSPECIFIED' and c.parse_status == 'parsed'
               and c.unit == '%' and semantic_review_required(c) for c in table[5:10])
    assert [Decimal(c.candidate_value) for c in table[5:10]] == [Decimal(v) for v in ['-0.604', '-0.138', '-0.065', '0.023', '0.085']]
    assert all(c.account == 'VALUATION' and c.candidate_value is None for c in table[10:])
    assert [c.modality for c in table] == ['fact', 'fact', 'forecast', 'forecast', 'forecast'] * 2 + ['opinion', 'opinion', 'forecast', 'forecast', 'forecast']
    assert all(c.table_context['modality_source'] == 'explicit_forecast_header' for c in table if c.modality == 'forecast')
    assert all(text[c.start:c.end] == c.numeric_text for c in table)
    ledger, claims = build(text, pages)
    assert len(claims) == 4 and all(c.modality == 'fact' for c in claims)
    judge = Judge(FactStore([]))
    assert [judge.verify(c).abstention_code for c in claims] == ['X6', 'X6', 'X2', 'X2']
    valuations = [row for row in ledger.entries if row['reason'] == 'valuation_table_row']
    assert len(valuations) == 2 and all(row['status'] == 'out_of_scope' and row['claim_id'] is None for row in valuations)
    forecasts = [row for row in ledger.entries if row['reason'] == 'forecast_table_column']
    assert len(forecasts) == 9
    assert all(row['modality_basis']['period'] in {'2024Y', '2025Y', '2026Y'} for row in forecasts)
    assert all(row['modality_basis']['headers'][0]['text'] == '2022A 2023A 2024E 2025E 2026E' for row in forecasts)


def test_unknown_english_table_units_cannot_leak_into_following_known_account():
    text = ('合并利润表(万元)\n2022A 2023A 2024E\nROIC(%)\n1\n2\n3\n'
            '营业收入\n100\n120\n130\n')
    table = extract_table_mentions(text, [], [], '甲', '2099Y')
    assert len(table) == 6 and all(c.unit == '%' for c in table[:3])
    assert [Decimal(c.candidate_value) for c in table[3:]] == [Decimal('1000000'), Decimal('1200000'), Decimal('1300000')]
    assert all(c.account == 'REVENUE' and c.unit == '万元' for c in table[3:])
