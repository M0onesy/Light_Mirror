from pathlib import Path

import pytest

from mingjing.financial_notes import _extract_major_customer_facts
from mingjing.evidence import make_evidence_id


def _page(number, text):
    lines = [line for line in text.splitlines() if line.strip()]
    return {
        "page": number,
        "text": text,
        "blocks": [
            {"bbox": [0, float(i), 500, float(i + 10)], "text": line}
            for i, line in enumerate(lines)
        ],
    }


def test_customer_table_handles_split_headers_wrapped_names_and_page_break():
    pages = [
        _page(
            205,
            """4、前五大客户销售情况
年度
序号
客户名称
营业收入
（万元）
占营业收入
比重
主要销售内容
2023
年
1-6
月
1
青岛国实科技集团
有限公司
2,967.44
14.65%
超算云系统集成
2
清华大学
603.56
2.98%超算云服务
年份
序号
客户名称
营业收入
（万元）
2020
年
1
清华大学
829.72
6.77%
2
自然资源部第一海
洋研究所
687.10
5.60%
""",
        ),
        _page(
            206,
            """3
中国科学院力学研
究所
415.58
3.39%
4
国防科技大学
328.32
2.68%
5
北京大学
276.11
2.25%
合计
2,536.82
20.69%
（二）采购情况及主要供应商
""",
        ),
    ]
    facts = _extract_major_customer_facts(
        pages,
        path=Path("customer.pdf"),
        company="并行科技",
        effective_period="2023Y",
        doc_sha="a" * 64,
        disclosure_date="2023-10-16",
        disclosure_version="原披露",
    )

    assert len(facts) == 10
    assert {(f.period, f.dimension_id, f.value) for f in facts if f.account == 'REVENUE'} == {
        ("2023H1", "青岛国实科技集团有限公司", 29674400),
        ("2023H1", "清华大学", 6035600),
        ("2020Y", "清华大学", 8297200),
        ("2020Y", "自然资源部第一海洋研究所", 6871000),
        ("2020Y", "中国科学院力学研究所", 4155800),
        ("2020Y", "国防科技大学", 3283200),
        ("2020Y", "北京大学", 2761100),
        ("2020Y", "前五大客户", 25368200),
    }
    assert all(f.dimension_kind == "customer" for f in facts)
    assert all(f.display_unit == "万元" for f in facts if f.account == 'REVENUE')
    assert {(f.period, f.dimension_id, str(f.value)) for f in facts if f.account == 'REVENUE_SHARE'} == {
        ('2023H1', '青岛国实科技集团有限公司', '0.1465'),
        ('2023H1', '清华大学', '0.0298'),
    }
    assert all(f.scope == '未知' for f in facts)
    assert len({f.source_table_id for f in facts}) == 1
    assert all(":p205:" in f.evidence[0] or ":p206:" in f.evidence[0] for f in facts)


def test_customer_parser_does_not_open_on_contract_liability_or_forecast_tables():
    pages = [
        _page(
            10,
            """报告期各期末，合同负债前五大客户情况如下所示：
单位：万元
客户
金额
占比
清华大学
1,000.00
5.00%
""",
        ),
        _page(
            11,
            """财务预测摘要
项目
2024E
客户
1
999
""",
        ),
    ]
    assert _extract_major_customer_facts(
        pages,
        path=Path("noise.pdf"),
        company="并行科技",
        effective_period="2023Y",
        doc_sha="b" * 64,
        disclosure_date="2023-10-16",
        disclosure_version="原披露",
    ) == []


def _extract(pages):
    return _extract_major_customer_facts(
        pages, path=Path('customer.pdf'), company='并行科技',
        effective_period='2023Y', doc_sha='c' * 64,
        disclosure_date='2023-10-16', disclosure_version='原披露')


def _table(caption='前五名客户情况', *, period='2022年度', share_label='占营业收入比重'):
    return f'{caption}\n单位：万元\n{period}\n序号\n客户名称\n销售金额\n{share_label}\n'


@pytest.mark.parametrize('boundary', [
    '科目具体情况及说明：', '科目具体情况及分析说明：',
    '前五名供应商情况', '前五大供应商情况', '采购情况及主要供应商',
    '其他披露事项', '营业收入总体分析', '营业成本分析',
    '募集资金使用情况', '募投项目', '项目投资概算',
])
def test_customer_section_boundaries_never_consume_supplier_or_project_amounts(boundary):
    page = _page(354, _table() + '1\n清华大学\n1,742.52\n5.57%\n'
                 '2\n未完成客户\n' + boundary + '\n'
                 '2022年度\n序号\n客户名称\n销售金额\n单位：万元\n'
                 '1\n京东\n3,783.39\n10.00%\n'
                 '2\n设备购置费\n27,066.20\n20.00%\n')
    facts = _extract([page])
    assert {(f.account, f.dimension_id) for f in facts} == {
        ('REVENUE', '清华大学'), ('REVENUE_SHARE', '清华大学')}
    assert all(f.period == '2022Y' and f.scope == '未知' for f in facts)


def test_customer_phrase_in_narrative_cannot_reopen_closed_section():
    page = _page(1, _table() + '1\n清华大学\n100.00\n5.00%\n'
                 '科目具体情况及说明：\n报告期内，公司前五大客户销售情况如下：\n'
                 '单位：万元\n2022年度\n序号\n客户名称\n营业收入\n'
                 '1\n设备购置费\n999.00\n20.00%\n')
    assert {f.dimension_id for f in _extract([page])} == {'清华大学'}


def test_disjoint_page_window_cannot_continue_an_incomplete_customer_row():
    pages = [_page(10, _table() + '1\n清华大学\n100.00\n'),
             _page(12, '5.00%\n2\n设备购置费\n999.00\n20.00%\n')]
    assert _extract(pages) == []


def test_contiguous_page_window_preserves_split_row_occurrence_evidence():
    pages = [_page(10, _table() + '1\n清华大学\n100.00\n'),
             _page(11, '5.00%\n合计\n100.00\n5.00%\n科目具体情况及说明：\n')]
    facts = _extract(pages)
    assert len(facts) == 4
    amount = next(f for f in facts if f.account == 'REVENUE' and f.dimension_id == '清华大学')
    share = next(f for f in facts if f.account == 'REVENUE_SHARE' and f.dimension_id == '清华大学')
    assert ':p10:' in amount.evidence[0]
    assert ':p11:' in share.evidence[0]
    assert any(':p11:' in eid for eid in amount.evidence)
    assert len({f.source_table_id for f in facts}) == 1


@pytest.mark.parametrize('caption,footnote,scope', [
    ('主要客户情况', '注1：同一院校下设不同院系已合并计算销售额；', '未知'),
    ('主要客户情况（合并口径）', '注：公司与客户不存在关联关系。', '合并'),
    ('前五名客户情况（母公司）', '', '母公司'),
    ('主要客户情况', '注：本表按合并口径披露。', '合并'),
    ('主要客户情况', '注：本表不按合并口径披露。', '未知'),
    ('主要客户情况（合并口径）', '注：本表按母公司口径披露。', '未知'),
])
def test_customer_scope_requires_affirmative_source_declaration(caption, footnote, scope):
    page = _page(1, _table(caption) + '1\n清华大学\n100.00\n5.00%\n'
                 + footnote + '\n科目具体情况及说明：\n')
    facts = _extract([page])
    assert len(facts) == 2
    assert {f.scope for f in facts} == {scope}
    if footnote:
        footnote_evidence = make_evidence_id('c' * 64, 1, footnote,
                                          next(b['bbox'] for b in page['blocks'] if b['text'] == footnote))
        assert all(footnote_evidence in f.source_table_evidence for f in facts)
        assert all(footnote_evidence in f.evidence for f in facts)
    if scope in {'合并', '母公司'}:
        assert all(f.source_scope_evidence for f in facts)
    if '合并计算' in footnote:
        assert all(not f.source_scope_evidence for f in facts)


def test_independent_customer_tables_keep_separate_source_identities():
    pages = [_page(1, _table('主要客户情况') + '1\n清华大学\n100.00\n5.00%\n科目具体情况及说明：\n'),
             _page(2, _table('主要客户情况') + '1\n清华大学\n100.00\n5.00%\n科目具体情况及说明：\n')]
    facts = _extract(pages)
    assert len(facts) == 4
    assert len({f.source_table_id for f in facts}) == 2


def test_repeated_customer_name_keeps_each_periods_original_amount_and_share_block():
    page = _page(1, _table(period='2022年度') + '1\n清华大学\n1,742.52\n5.57%\n'
                 + '2021年度\n序号\n客户名称\n营业收入\n占营业收入比重\n'
                 '1\n清华大学\n1,148.55\n5.22%\n科目具体情况及说明：\n')
    facts = _extract([page])
    assert len(facts) == 4
    source = {b['text']: make_evidence_id('c' * 64, 1, b['text'], b['bbox']) for b in page['blocks']}
    expected = {('2022Y', 'REVENUE'): '1,742.52', ('2021Y', 'REVENUE'): '1,148.55',
                ('2022Y', 'REVENUE_SHARE'): '5.57%', ('2021Y', 'REVENUE_SHARE'): '5.22%'}
    for fact in facts:
        assert fact.evidence[0] == source[expected[(fact.period, fact.account)]]
        assert source[expected[(fact.period, fact.account)]] not in fact.source_table_evidence


def test_undefined_annual_sales_percentage_is_not_assumed_to_mean_revenue_share():
    page = _page(1, _table(share_label='年度销售额占比（%）')
                 + '1\n清华大学\n100.00\n5.00%\n科目具体情况及说明：\n')
    facts = _extract([page])
    assert len(facts) == 1 and facts[0].account == 'REVENUE'


def test_integer_customer_amount_and_missing_next_table_unit_do_not_inherit():
    page = _page(1, _table() + '1\n清华大学\n1\n5.00%\n合计\n1\n5.00%\n'
                 '科目具体情况及说明：\n主要客户情况\n2021年度\n序号\n客户名称\n营业收入\n'
                 '1\n北京大学\n999.00\n5.00%\n')
    facts = _extract([page])
    assert len(facts) == 4
    assert all(f.dimension_id in {'清华大学', '前五大客户'} for f in facts)
    assert all(f.value == 10000 for f in facts if f.account == 'REVENUE')


def test_split_aggregate_caption_keeps_original_top_five_disclosure():
    page = _page(1, _table() + '1\n清华大学\n100.00\n5.00%\n合\n100.00\n5.00%\n'
                 '科目具体情况及说明：\n')
    facts = _extract([page])
    assert len(facts) == 4
    assert {(f.account, f.dimension_id) for f in facts} == {
        ('REVENUE', '清华大学'), ('REVENUE_SHARE', '清华大学'),
        ('REVENUE', '前五大客户'), ('REVENUE_SHARE', '前五大客户')}


def test_customer_source_row_index_uses_original_serial_cell_and_not_name_prefix():
    page = _page(1, _table(period='2022年度')
                 + '1\n北京京东科技公司\n100.00\n5.00%\n'
                 '2\n北京京东尚科公司\n200.00\n10.00%\n'
                 '合计\n300.00\n15.00%\n'
                 '2021年度\n序号\n客户名称\n营业收入\n占营业收入比重\n'
                 '1\n北京京东尚科公司\n90.00\n4.50%\n'
                 '2\n北京京东科技公司\n180.00\n9.00%\n科目具体情况及说明：\n')
    facts = _extract([page])
    assert len(facts) == 10
    serial_blocks = [block for block in page['blocks'] if block['text'] in {'1', '2'}]
    serial_evidence = [make_evidence_id('c' * 64, 1, block['text'], block['bbox'])
                       for block in serial_blocks]
    expected = {
        ('2022Y', '北京京东科技公司'): (1, serial_evidence[0]),
        ('2022Y', '北京京东尚科公司'): (2, serial_evidence[1]),
        ('2021Y', '北京京东尚科公司'): (1, serial_evidence[2]),
        ('2021Y', '北京京东科技公司'): (2, serial_evidence[3]),
    }
    for fact in facts:
        if fact.dimension_id == '前五大客户':
            assert fact.source_row_index == 0
        else:
            row_index, index_evidence = expected[(fact.period, fact.dimension_id)]
            assert fact.source_row_index == row_index
            assert index_evidence in fact.evidence
    assert len({f.source_table_id for f in facts}) == 1
