"""Geometry/source regressions, not manual financial gold or recall claims."""
from collections import Counter
from copy import deepcopy

from mingjing.coverage import CoverageLedger
from mingjing.layout_noise import chart_layout_exclusions


def document(rows):
    text = ''
    lines, blocks = [], []
    for index, (value, box) in enumerate(rows):
        start = len(text)
        text += value + '\n'
        lines.append({'text': value, 'bbox': box, 'start': start, 'end': start + len(value)})
        blocks.append({'id': f'block-{index}', 'text': value + '\n', 'bbox': box,
                       'start': start, 'end': len(text), 'page': 1, 'page_id': 'p1'})
    return text, [{'id': 'p1', 'page': 1, 'start': 0, 'end': len(text), 'lines': lines}], blocks


def chart_rows():
    return [('图1：营业收入变化', [20, 20, 190, 30]),
            ('0%', [35, 160, 55, 170]), ('20%', [35, 140, 55, 150]),
            ('40%', [35, 120, 55, 130]), ('60%', [35, 100, 55, 110]),
            ('2019 2020 2021 2022', [70, 175, 240, 185]),
            ('数据来源：公司公告', [20, 210, 230, 220])]


def test_tick_and_year_proofs_retain_every_source_occurrence_in_ledger():
    text, pages, blocks = document(chart_rows())
    proofs = chart_layout_exclusions(text, pages)
    assert Counter(proof['reason'] for proof in proofs.values()) == {
        'chart_axis_tick': 4, 'chart_period_label': 4}
    ledger, claims = CoverageLedger.build(text, pages, blocks, '甲公司', '2023Y')
    excluded = [entry for entry in ledger.entries if entry.get('exclusion_basis')]
    assert len(excluded) == 8
    assert all(entry['status'] == 'rule_excluded' and entry['claim_id'] is None for entry in excluded)
    assert all(not any(claim.start == entry['start'] for claim in claims) for entry in excluded)
    for entry in excluded:
        proof = entry['exclusion_basis']
        assert text[slice(*proof['occurrence_span'])].strip() in entry['text']
        assert proof['schema_version'] == 'pdf-chart-layout.v1'
        assert proof['figure']['text'] == '图1：营业收入变化'
        assert proof['source_boundary']['text'] == '数据来源：公司公告'
        assert len(proof['tick_source_spans']) == 4 and len(proof['period_source_spans']) == 4
    assert len(ledger.entries) >= 9  # Figure ID occurrence is retained too.


def test_real_chart_data_labels_are_not_removed_as_numeric_block_noise():
    rows = chart_rows()[:-1] + [
        ('7.93%', [90, 115, 115, 125]), ('10.10%', [130, 110, 160, 120]),
        ('17.59%', [170, 105, 200, 115]), ('24.25%', [210, 100, 240, 110]),
    ] + chart_rows()[-1:]
    text, pages, blocks = document(rows)
    proofs = chart_layout_exclusions(text, pages)
    for value in ['7.93%', '10.10%', '17.59%', '24.25%']:
        assert text.index(value) not in proofs
    ledger, _ = CoverageLedger.build(text, pages, blocks, '甲公司', '2023Y')
    for entry in ledger.entries:
        if entry['text'].strip() in {'7.93%', '10.10%', '17.59%', '24.25%'}:
            assert entry['reason'] not in {'chart_axis_tick', 'chart_period_label'}


def test_caption_citation_and_exact_source_geometry_are_all_required():
    text, pages, _ = document(chart_rows())
    for mutate in [
        lambda p: p[0].update(lines=[]),
        lambda p: p[0]['lines'].pop(0),
        lambda p: p[0]['lines'].pop(),
        lambda p: [line.update(start=0) for line in p[0]['lines'][1:-1]],
    ]:
        changed = deepcopy(pages)
        mutate(changed)
        assert chart_layout_exclusions(text, changed) == {}


def test_equal_values_without_equal_geometry_cannot_be_axis_ticks():
    rows = chart_rows()
    rows[2] = ('20%', [35, 145, 55, 155])
    text, pages, _ = document(rows)
    assert chart_layout_exclusions(text, pages) == {}


def test_years_do_not_become_axis_labels_without_a_proven_numeric_axis():
    rows = [chart_rows()[0], ('2019 2020 2021 2022', [70, 70, 240, 80]), chart_rows()[-1]]
    text, pages, _ = document(rows)
    assert chart_layout_exclusions(text, pages) == {}


def test_ordinary_financial_table_equal_amounts_and_forecast_dimensions_are_preserved():
    rows = [('合并利润表(万元)', [20, 20, 200, 30]),
            ('2021A 2022A 2023A 2024E', [70, 40, 240, 50]),
            ('营业收入', [20, 70, 60, 80]), ('100', [80, 70, 100, 80]),
            ('200', [80, 90, 100, 100]), ('300', [80, 110, 100, 120]),
            ('400', [80, 130, 100, 140]), ('数据来源：公司公告', [20, 170, 230, 180])]
    text, pages, blocks = document(rows)
    assert chart_layout_exclusions(text, pages) == {}
    ledger, _ = CoverageLedger.build(text, pages, blocks, '甲公司', '2023Y')
    assert not any(entry.get('exclusion_basis') for entry in ledger.entries)
    forecast = [entry for entry in ledger.entries if entry['reason'] == 'forecast_table_column']
    assert len(forecast) == 1 and forecast[0]['text'].strip() == '400'


def test_figure_with_financial_rows_is_not_assumed_to_be_a_plot():
    rows = [('图1：财务数据展示', [20, 20, 190, 30]),
            ('营业收入', [25, 90, 75, 100]), ('100', [80, 90, 105, 100]),
            ('营业成本', [25, 110, 75, 120]), ('200', [80, 110, 105, 120]),
            ('净利润', [25, 130, 75, 140]), ('300', [80, 130, 105, 140]),
            ('归母净利润', [25, 150, 75, 160]), ('400', [80, 150, 105, 160]),
            ('2019 2020 2021 2022', [120, 170, 290, 180]),
            ('数据来源：公司公告', [20, 200, 300, 210])]
    text, pages, _ = document(rows)
    assert chart_layout_exclusions(text, pages) == {}


def test_explicit_currency_amounts_never_become_ticks():
    rows = chart_rows()
    for index, value in enumerate(['0元', '20元', '40元', '60元'], 1):
        rows[index] = (value, rows[index][1])
    text, pages, _ = document(rows)
    assert chart_layout_exclusions(text, pages) == {}


def test_source_from_another_page_or_later_table_cannot_bound_a_chart():
    rows = chart_rows()[:-1] + [('表1：财务数据', [20, 195, 200, 205]), chart_rows()[-1]]
    text, pages, _ = document(rows)
    assert chart_layout_exclusions(text, pages) == {}
    text, pages, _ = document(chart_rows())
    boundary = pages[0]['lines'].pop()
    pages.append({'page': 2, 'lines': [boundary]})
    assert chart_layout_exclusions(text, pages) == {}


def test_page_numbers_without_a_chart_keep_their_existing_identifier_reason():
    text, pages, blocks = document([('7 / 44', [500, 770, 550, 780])])
    ledger, claims = CoverageLedger.build(text, pages, blocks, '甲公司', '2023Y')
    assert not claims and all(entry['reason'] == 'page_number_ratio' for entry in ledger.entries)
    assert not any(entry.get('exclusion_basis') for entry in ledger.entries)
