"""Actual-source geometry cases, not financial recall or API acceptance scores."""
from collections import Counter
from copy import deepcopy

import pytest

from mingjing.coverage import CoverageLedger
from mingjing.layout_noise import chart_layout_exclusions


def document(rows, page=1):
    text = ''
    lines, blocks = [], []
    page_id = f'p{page}'
    for index, (value, box) in enumerate(rows):
        start = len(text)
        text += value + '\n'
        lines.append({'text': value, 'bbox': box, 'start': start, 'end': start + len(value)})
        blocks.append({'id': f'block-{index}', 'text': value + '\n', 'bbox': box,
                       'start': start, 'end': len(text), 'page': page, 'page_id': page_id})
    return text, [{'id': page_id, 'page': page, 'start': 0, 'end': len(text), 'lines': lines}], blocks


def figure10():
    # PDF p8: the y209 years are a SERIES LEGEND, not the x-axis baseline.
    return [
        ('图10：2023年超算云服务增长55%至4.14亿元（单位：万元）', [178.82, 75.091, 473.74, 86.781]),
        ('0', [208.8, 178.271, 213.3, 188.234]),
        ('10000', [190.78, 161.661, 213.352, 171.624]),
        ('20000', [190.78, 145.010, 213.376, 154.999]),
        ('30000', [190.78, 128.421, 213.352, 138.384]),
        ('40000', [190.78, 111.770, 213.376, 121.759]),
        ('50000', [190.78, 95.181, 213.352, 105.144]),
        ('超算云服务', [238.82, 190.219, 283.82, 199.219]),
        ('超算云系统集成', [310.54, 190.219, 373.54, 199.219]),
        ('超算软件与服务', [391.25, 190.219, 454.25, 199.219]),
        ('超算会议及其他', [471.96, 190.219, 535.104, 199.219]),
        ('2020', [318.48, 209.761, 336.525, 219.724]),
        ('2021', [349.75, 209.761, 367.795, 219.724]),
        ('2022', [381.05, 209.761, 399.095, 219.724]),
        ('2023', [412.34, 209.761, 430.385, 219.724]),
        ('数据来源：公司招股书、公司公告、开源证券研究所', [178.82, 232.721, 388.12, 242.684]),
    ]


def figure43():
    # PDF p31: 2000 is an amount tick, not a year.
    return [
        ('图43：2022年并行通用超算云与并行行业云更多的采取共建模式（万元）', [178.82, 388.081, 521.38, 399.771]),
        ('0', [203.4, 516.671, 207.9, 526.634]),
        ('2000', [189.89, 505.271, 207.935, 515.234]),
        ('4000', [189.89, 493.871, 207.935, 503.834]),
        ('6000', [189.89, 482.471, 207.935, 492.434]),
        ('8000', [189.89, 471.071, 207.935, 481.034]),
        ('10000', [185.38, 459.671, 207.862, 469.634]),
        ('12000', [185.38, 448.271, 207.862, 458.234]),
        ('14000', [185.38, 436.871, 207.862, 446.834]),
        ('16000', [185.38, 425.471, 207.862, 435.434]),
        ('18000', [185.38, 414.071, 207.862, 424.034]),
        ('并行通用超算云', [237.5, 528.619, 300.5, 537.619]),
        ('并行行业云', [353.47, 528.619, 398.47, 537.619]),
        ('并行AI云', [464.54, 528.331, 501.14, 538.294]),
        ('外采模式收入', [305.33, 548.659, 359.33, 557.659]),
        ('共建模式收入', [376.7, 548.659, 430.7, 557.659]),
        ('数据来源：公司第一轮问询函回复、开源证券研究所', [178.82, 576.931, 388.12, 586.894]),
    ]


def figure59():
    return [
        ('图59：并行科技拥有65万个CPU计算核数（万个）', [304.73, 318.361, 550.3, 330.051]),
        ('0', [340.27, 418.321, 344.77, 428.284]),
        ('50', [335.76, 402.961, 344.82, 412.924]),
        ('100', [331.27, 387.601, 344.761, 397.564]),
        ('150', [331.27, 372.241, 344.761, 382.204]),
        ('200', [331.27, 356.881, 344.761, 366.844]),
        ('250', [331.27, 341.521, 344.761, 351.484]),
        ('并行科技', [359.62, 430.249, 395.62, 439.249]),
        ('阿里云', [414.77, 430.249, 441.77, 439.249]),
        ('华为云', [465.34, 430.249, 492.34, 439.249]),
        ('腾讯云', [515.9, 430.249, 542.9, 439.249]),
        ('CPU计算核数', [423.34, 450.001, 477.196, 459.964]),
        ('数据来源：沙利文咨询、开源证券研究所', [304.73, 476.131, 469.03, 486.094]),
    ]


def figure42_without_axis_labels():
    return [
        ('图42：共建模式收入占比逐渐提高，2022Q4达到75%', [178.82, 75.091, 431.83, 86.781]),
        ('0%', [199.8, 172.130, 211.877, 182.119]),
        ('20%', [195.29, 158.181, 211.778, 168.144]),
        ('40%', [195.29, 144.211, 211.778, 154.174]),
        ('60%', [195.29, 130.200, 211.795, 140.189]),
        ('80%', [195.29, 116.251, 211.778, 126.214]),
        ('100%', [190.78, 102.281, 211.885, 112.244]),
        # These are series legends, 37.89pt below the tick's bottom box.
        ('外购模式收入占比', [249.17, 220.009, 321.17, 229.009]),
        ('共建模式收入占比', [345.19, 220.009, 417.19, 229.009]),
        ('总体毛利率', [441.22, 220.009, 486.22, 229.009]),
        ('外购模式毛利率', [249.17, 235.869, 312.17, 244.869]),
        ('共建模式毛利率', [345.19, 235.869, 408.19, 244.869]),
        ('数据来源：公司第二轮问询函回复、开源证券研究所', [178.82, 263.951, 388.12, 273.914]),
    ]


@pytest.mark.parametrize('rows,page,expected', [(figure10(), 8, 6), (figure43(), 31, 10), (figure59(), 40, 6)])
def test_source_category_baseline_proves_ticks_without_inventing_periods(rows, page, expected):
    text, pages, blocks = document(rows, page)
    proofs = chart_layout_exclusions(text, pages)
    assert Counter(q['reason'] for q in proofs.values()) == {'chart_axis_tick': expected}
    ledger, _ = CoverageLedger.build(text, pages, blocks, '并行科技', '2099Y')
    # Period/technical source rules also retain proof. This assertion is
    # specifically about geometry-backed axis ticks, not every exclusion.
    excluded = [q for q in ledger.entries if q.get('reason') == 'chart_axis_tick']
    assert len(excluded) == expected
    all_numeric_count = len(ledger.entries)
    no_geometry = deepcopy(pages)
    no_geometry[0]['lines'] = []
    no_geometry_ledger, _ = CoverageLedger.build(text, no_geometry, blocks, '并行科技', '2099Y')
    assert len(no_geometry_ledger.entries) == all_numeric_count
    assert {(q['start'], q['end']) for q in no_geometry_ledger.entries} == {(q['start'], q['end']) for q in ledger.entries}
    for entry in excluded:
        proof = entry['exclusion_basis']
        assert proof['baseline_kind'] == 'categories'
        assert proof['period_source_spans'] == []
        assert len(proof['category_source_spans']) >= 3
        assert proof['baseline_distance'] < 2
        assert text[slice(*proof['occurrence_span'])].strip() in entry['text']
        assert all(text[slice(*span)] == row['text'] for span, row in zip(
            proof['category_source_spans'], proof['baseline_source_lines']))


def test_p8_series_year_legend_is_not_promoted_to_an_axis_by_enlarging_year_tolerance():
    text, pages, _ = document(figure10(), 8)
    proofs = chart_layout_exclusions(text, pages)
    assert len(proofs) == 6
    for year in ['2020', '2021', '2022', '2023']:
        assert text.index(year + '\n') not in proofs
    no_category = deepcopy(pages)
    no_category[0]['lines'] = [q for q in no_category[0]['lines'] if q['bbox'][1] != 190.219]
    assert chart_layout_exclusions(text, no_category) == {}


def test_2000_amount_requires_a_proven_axis_with_non_year_amounts():
    text, pages, _ = document(figure43(), 31)
    start = text.index('2000\n')
    proof = chart_layout_exclusions(text, pages)[start]
    assert proof['reason'] == 'chart_axis_tick'
    assert proof['basis']['numeric_axis_period_candidate_spans'] == [[start, start + 4]]
    assert proof['basis']['value_step'] == '-2000'
    rows = figure43()
    rows[1:11] = [('2000', [185, 450, 208, 460]), ('2001', [185, 470, 208, 480]),
                  ('2002', [185, 490, 208, 500]), ('2003', [185, 516.671, 208, 526.634])]
    # Use equal geometry; pure year-like vertical labels still cannot be ticks.
    for index in range(1, 5):
        y = 456.671 + (index - 1) * 20
        rows[index] = (rows[index][0], [185, y, 208, y + 9.963])
    text, pages, _ = document(rows)
    assert chart_layout_exclusions(text, pages) == {}


def test_missing_quarter_or_category_baseline_keeps_all_six_percent_occurrences():
    text, pages, blocks = document(figure42_without_axis_labels(), 31)
    assert chart_layout_exclusions(text, pages) == {}
    ledger, _ = CoverageLedger.build(text, pages, blocks, '并行科技', '2099Y')
    expected = {'0%', '20%', '40%', '60%', '80%', '100%'}
    entries = [q for q in ledger.entries if q['text'].strip() in expected]
    assert {q['text'].strip() for q in entries} == expected
    assert not any(q.get('exclusion_basis') for q in entries)


def test_twelve_real_chart_data_labels_are_not_confused_with_ticks():
    percent_rows = [
        ('图19：主要客户为高等院校与科研机构', [178.82, 584.541, 476.62, 596.231]),
        ('7.93%', [247.39, 612.95, 272.170, 622.91]),
        ('10.10%', [325.58, 614.44, 355.482, 624.4]),
        ('17.59%', [406.32, 616.87, 436.150, 626.83]),
        ('24.25%', [487.06, 621.55, 516.890, 631.51]),
        ('0%', [199.8, 704.251, 211.857, 714.214]),
        ('20%', [195.29, 684.521, 211.778, 694.484]),
        ('40%', [195.29, 664.771, 211.778, 674.734]),
        ('60%', [195.29, 645.041, 211.778, 655.004]),
        ('80%', [195.29, 625.321, 211.778, 635.284]),
        ('100%', [190.78, 605.561, 211.885, 615.524]),
        ('2020 2021 2022 2023', [250.8, 714.841, 520.85, 724.804]),
        ('数据来源：公司招股书、开源证券研究所', [178.82, 757.917, 343.12, 767.88]),
    ]
    amount_rows = [
        ('图41：直接采购模式维持稳定，共建模式快速增长（单位：万核小时）', [178.82, 397.441, 508.18, 409.131]),
        ('87493', [248.4, 496.51, 273.610, 506.47]),
        ('279512', [316.97, 483.38, 347.220, 493.34]),
        ('475847', [388.03, 469.96, 418.280, 479.92]),
        ('264223', [459.1, 484.43, 489.470, 494.39]),
        ('42591', [248.4, 487.63, 273.610, 497.59]),
        ('37158', [319.49, 461.73, 344.700, 471.69]),
        ('48893', [390.55, 434.042, 415.772, 444.026]),
        ('30174', [461.62, 464.30, 486.926, 474.26]),
        ('0', [213.29, 501.381, 217.79, 511.344]),
        ('100000', [190.78, 487.701, 217.924, 497.664]),
        ('200000', [190.78, 474.041, 217.924, 484.004]),
        ('300000', [190.78, 460.361, 217.924, 470.324]),
        ('400000', [190.78, 446.681, 217.924, 456.644]),
        ('500000', [190.78, 433.001, 217.924, 442.964]),
        ('600000', [190.78, 419.321, 217.924, 429.284]),
        ('2020 2021 2022 2023', [251.98, 511.961, 490.85, 521.924]),
        ('数据来源：公司招股书、开源证券研究所', [178.82, 570.691, 343.12, 580.654]),
    ]
    for rows, expected in [(percent_rows, ['7.93%', '10.10%', '17.59%', '24.25%']),
                           (amount_rows, ['87493', '279512', '475847', '264223', '42591', '37158', '48893', '30174'])]:
        text, pages, blocks = document(rows)
        proofs = chart_layout_exclusions(text, pages)
        assert any(q['reason'] == 'chart_axis_tick' for q in proofs.values())
        assert all(text.index(value + '\n') not in proofs for value in expected)
        ledger, _ = CoverageLedger.build(text, pages, blocks, '并行科技', '2023Y')
        entries = [q for q in ledger.entries if q['text'].strip() in expected]
        assert len(entries) == len(expected)
        assert all(not q.get('exclusion_basis') for q in entries)


def compact_category_rows():
    return [('图1：业务变化', [20, 20, 190, 30]),
            ('0', [35, 160, 55, 170]), ('20', [35, 140, 55, 150]),
            ('40', [35, 120, 55, 130]), ('60', [35, 100, 55, 110]),
            ('甲业务', [75, 172, 100, 182]), ('乙业务', [130, 172, 155, 182]),
            ('丙业务', [185, 172, 210, 182]),
            ('数据来源：公司公告', [20, 210, 230, 220])]


@pytest.mark.parametrize('mutation', [
    'caption', 'source', 'category', 'unequal_positions', 'unequal_values', 'category_inside_plot',
    'category_far_below', 'category_irregular_centres', 'category_overlap', 'financial_rows', 'bad_offsets',
    'out_of_page', 'nonfinite', 'negative_width', 'invalid_bbox',
])
def test_incomplete_or_ambiguous_source_geometry_never_deletes_bare_numbers(mutation):
    rows = compact_category_rows()
    if mutation == 'unequal_positions':
        rows[2] = ('20', [35, 146, 55, 156])
    elif mutation == 'unequal_values':
        rows[2] = ('21', rows[2][1])
    elif mutation in {'category_inside_plot', 'category_far_below'}:
        y = 150 if mutation == 'category_inside_plot' else 193
        rows[5:8] = [(value, [box[0], y, box[2], y + 10]) for value, box in rows[5:8]]
    elif mutation == 'category_irregular_centres':
        rows[7] = ('丙业务', [230, 172, 255, 182])
    elif mutation == 'category_overlap':
        rows[6] = ('乙业务', [90, 172, 155, 182])
    elif mutation == 'financial_rows':
        rows.append(('营业收入', [70, 120, 100, 130]))
    text, pages, _ = document(rows)
    if mutation in {'caption', 'source', 'category'}:
        pages[0]['lines'].pop({'caption': 0, 'source': -1, 'category': 5}[mutation])
    elif mutation == 'bad_offsets':
        pages[0]['lines'][2]['start'] = 0
    elif mutation == 'out_of_page':
        pages[0]['start'] = pages[0]['lines'][1]['start']
    elif mutation == 'nonfinite':
        pages[0]['lines'][2]['bbox'][0] = float('nan')
    elif mutation == 'negative_width':
        pages[0]['lines'][2]['bbox'][0] = 100
    elif mutation == 'invalid_bbox':
        pages[0]['lines'][2]['bbox'][0] = 'invalid'
    assert chart_layout_exclusions(text, pages) == {}


def test_source_geometry_cannot_borrow_a_baseline_from_another_figure():
    left = compact_category_rows()
    left = [q for q in left if q[0] not in {'甲业务', '乙业务', '丙业务'}]
    right = [('图2：另一业务变化', [270, 20, 460, 30]),
             ('甲业务', [325, 172, 350, 182]), ('乙业务', [380, 172, 405, 182]),
             ('丙业务', [435, 172, 460, 182]), ('数据来源：另一公司公告', [270, 210, 480, 220])]
    text, pages, _ = document(left + right)
    assert chart_layout_exclusions(text, pages) == {}


def test_plain_text_category_names_and_equal_numbers_without_boxes_remain_visible():
    text, pages, blocks = document(compact_category_rows())
    for line in pages[0]['lines']:
        line['bbox'] = None
    assert chart_layout_exclusions(text, pages) == {}
    ledger, _ = CoverageLedger.build(text, pages, blocks, '甲公司', '2023Y')
    assert not any(q.get('exclusion_basis') for q in ledger.entries)
    assert all(any(q['text'].strip() == value for q in ledger.entries) for value in ['0', '20', '40', '60'])
