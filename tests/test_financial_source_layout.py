from dataclasses import replace
from decimal import Decimal

from mingjing.coverage import CoverageLedger
from mingjing.facts import FactStore
from mingjing.judge import Judge
from mingjing.models import Fact
from mingjing.table_mentions import extract_table_mentions


def test_identifiers_do_not_remove_financial_value_on_same_line():
    text = ('图8：2023年归母净利润为-8050万元。\n'
            '表4：2023年公司营业收入为496百万元。\n'
            '3 / 44\n'
            '图7：2023年营业收入为496百万元.............. 7\n'
            '2024/4/22 公司2023年营业收入为496百万元。\n')
    pages = [{'start': 0, 'end': len(text), 'page': 1, 'id': 'page-1'}]
    ledger, claims = CoverageLedger.build(text, pages, [], '甲', '2023Y')
    assert len(claims) == 3
    assert all(c.account in {'REVENUE', 'NP_PARENT'} for c in claims)
    identifiers = [row for row in ledger.entries if row['reason'] in {
        'figure_or_table_identifier', 'page_number_ratio', 'table_of_contents_entry', 'calendar_date'}]
    assert identifiers and all(row['status'] == 'rule_excluded' for row in identifiers)
    assert any(row['claim_id'] and row['text'].strip() == '-8050万元' for row in ledger.entries)


def _side_by_side_page():
    # Original extraction order interleaves two real physical lanes. Monetary
    # left cells must not consume right percentage units, or vice versa.
    source = [
        ('合并资产负债表(万元)', 20, 20), ('2022A', 120, 20), ('2023A', 170, 20),
        ('主要财务比率', 320, 20), ('2022A', 420, 20), ('2023A', 470, 20),
        ('存货', 20, 40), ('30.00', 120, 40), ('40.00', 170, 40),
        ('毛利率(%)', 320, 40), ('20.0', 420, 40), ('25.0', 470, 40),
        ('合并现金流量表(万元)', 20, 60), ('2022A', 120, 60), ('2023A', 170, 60),
        ('净利率(%)', 320, 60), ('10.0', 420, 60), ('12.0', 470, 60),
        ('投资活动现金流', 20, 80), ('-12.00', 120, 80), ('-14.00', 170, 80),
        ('短期借款', 20, 100), ('5.00', 120, 100), ('6.00', 170, 100),
    ]
    text, lines = '', []
    for value, x, y in source:
        start = len(text)
        text += value + '\n'
        lines.append({'text': value, 'start': start, 'end': start+len(value), 'bbox': [x,y,x+len(value)*5,y+8]})
    return text, [{'start':0, 'end':len(text), 'page':1, 'lines':lines}]


def test_side_by_side_units_periods_and_cash_flow_stocks_are_isolated():
    text, pages = _side_by_side_page()
    claims = extract_table_mentions(text, pages, [], '甲', '2099Y')
    inventory = [c for c in claims if c.account == 'INVENTORY']
    margins = [c for c in claims if c.account == 'GROSS_MARGIN']
    cfi = [c for c in claims if c.account == 'CFI']
    assert [c.candidate_value for c in inventory] == ['300000.00', '400000.00']
    assert [c.candidate_value for c in margins] == ['0.200', '0.250']
    assert [c.candidate_value for c in cfi] == ['-120000.00', '-140000.00']
    assert all(c.period in {'2022Y','2023Y'} for c in claims)
    assert all(text[c.start:c.end] == c.numeric_text for c in claims)
    assert all(c.table_context['layout_source'] == 'pdf_text_line_lanes' for c in claims)
    assert all(c.text[c.source_numeric_start:c.source_numeric_start+len(c.numeric_text)] == c.numeric_text for c in claims)
    # Semantic context includes proven physical lane excerpts. It must not
    # include another table's caption/amount merely because source order was
    # interleaved. Every excerpt remains recoverable from the original spans.
    assert all('毛利率' not in c.text and '净利率' not in c.text for c in inventory + cfi)
    assert all('存货' not in c.text and '投资活动现金流' not in c.text for c in margins)
    assert all(c.table_context['row_label'] in c.text for c in claims)
    for claim in claims:
        assert claim.text == '\n'.join(text[s['start']:s['end']] for s in claim.table_context['source_spans'])
    # These are cash-flow changes, not closing balance-sheet loan stocks.
    borrowings = [c for c in claims if c.table_context['row_label'] == '短期借款']
    assert borrowings and all(c.account == 'SHORT_TERM_BORROWING_CHANGE_UNSPECIFIED'
                             and c.parse_status == 'parsed' and c.table_context['semantic_review_required'] for c in borrowings)
    assert all(Judge(FactStore([]),'2024-04-22').verify(c).abstention_code=='X3' for c in borrowings)
    store = FactStore([Fact('f', '甲', 'INVENTORY', '2023Y', value=Decimal('400000'), publish_date='2024-04-01')])
    assert Judge(store, '2024-04-22').verify(inventory[-1]).status == 'verified'
    assert Judge(store, '2024-04-22').verify(replace(inventory[-1], account='NP')).abstention_code == 'X3'
    assert Judge(store, '2024-04-22').verify(replace(inventory[-1], period='2022Y')).abstention_code == 'X3'
    assert Judge(store, '2024-04-22').verify(replace(inventory[-1], relation='yoy')).abstention_code == 'X3'


def test_disclosed_million_and_percentage_intervals_use_correct_scale():
    def fact(fid, value, unit, decimals):
        return Fact(fid, '甲', 'REVENUE', '2023Y', value=Decimal(value),
                    display_unit=unit, decimals=decimals, publish_date='2024-04-01')
    full = fact('raw', '495671818.88', '元', 2)
    rounded = fact('rounded', '496000000', '百万元', 0)
    assert FactStore([rounded, full]).query('甲','REVENUE','2023Y') == full
    assert FactStore([full, replace(rounded, value=Decimal('497000000'))]).query('甲','REVENUE','2023Y') is None
    pct = replace(fact('pct','0.585','%',1), account='GROSS_MARGIN')
    normalized = replace(fact('ratio','0.58520','',5), account='GROSS_MARGIN')
    assert FactStore([pct, normalized]).query('甲','GROSS_MARGIN','2023Y') == normalized
    incompatible = replace(normalized, value=Decimal('0.58600'))
    assert FactStore([pct, incompatible]).query('甲','GROSS_MARGIN','2023Y') is None


def test_prose_decrease_sign_and_growth_do_not_leak_to_next_absolute_ratio():
    from mingjing.mentions import extract_mentions
    text = '2023年营业收入为80万元，同比下降20%，毛利率25%。'
    claims = extract_mentions(text, '甲')
    assert [(c.account, c.relation, c.candidate_value) for c in claims] == [
        ('REVENUE', 'value', '800000'), ('REVENUE', 'yoy', '-0.20'),
        ('GROSS_MARGIN', 'value', '0.25')]
    facts = FactStore([
        Fact('current', '甲', 'REVENUE', '2023Y', value=Decimal('800000'), publish_date='2024-04-01'),
        Fact('base', '甲', 'REVENUE', '2022Y', value=Decimal('1000000'), publish_date='2024-04-01'),
        Fact('margin', '甲', 'GROSS_MARGIN', '2023Y', value=Decimal('.25'), publish_date='2024-04-01'),
    ])
    assert all(Judge(facts, '2024-04-22').verify(c).status == 'verified' for c in claims)
    for phrase in ('下滑', '减少', '降低', '回落'):
        claim = extract_mentions(f'2023年营业收入同比{phrase}20%。', '甲')[0]
        assert claim.candidate_value == '-0.20'


def test_explicit_interim_periods_are_not_promoted_to_whole_year():
    from mingjing.mentions import period_from_text, extract_mentions
    for text, period in [('2023年第二季度', '2023Q2'), ('2023年第四季度', '2023Q4'),
                         ('2023年下半年', '2023H2'), ('截至2023年6月30日', '2023H1'),
                         ('截至2023年9月30日', '20239M'), ('截至2023年12月31日', '2023Y')]:
        assert period_from_text(text) == period
    assert period_from_text('截至2023年4月15日', '2023Y') == ''
    assert extract_mentions('2023年第二季度营业收入为80万元。', '甲')[0].period == '2023Q2'


def test_basic_and_diluted_eps_are_explicit_and_unknown_roe_stays_unknown(tmp_path, monkeypatch):
    from mingjing import parser
    from mingjing.mentions import extract_mentions
    path = tmp_path / '2024-04-01_2023年年度报告.pdf'
    path.write_bytes(b'pdf-source')
    rows = ['合并利润表', '单位：万元', '2023年\n2022年',
            '基本每股收益\n1.20\n1.00', '稀释每股收益\n1.10\n0.90',
            '净资产收益率%\n12.0\n10.0', '加权平均净资产收益率%\n11.0\n9.0']
    monkeypatch.setattr(parser, 'extract_text', lambda _: [{'page': 1, 'text': '\n'.join(rows), 'blocks': [
        {'bbox': [0, n*30, 500, n*30+20], 'text': row} for n, row in enumerate(rows)]}])
    facts = parser.extract_facts(path, '甲', '2023Y')
    assert len(facts) == 6
    store = FactStore(facts)
    assert store.query('甲', 'EPS', '2023Y').value == Decimal('1.20')
    assert store.query('甲', 'EPS_DILUTED', '2023Y').value == Decimal('1.10')
    assert store.query('甲', 'ROE', '2023Y').value == Decimal('.110')
    explicit = extract_mentions('2023年稀释每股收益为1.10元。', '甲')[0]
    assert explicit.account == 'EPS_DILUTED' and Judge(store, '2024-04-22').verify(explicit).status == 'verified'
    for text in ('2023年每股收益为1.2元。', '2023年ROE为12%。', '2023年净资产收益率为12%。'):
        claim = extract_mentions(text, '甲')[0]
        assert claim.parse_status == 'parsed'
        assert claim.table_context['semantic_review_required'] is True
        assert Judge(store, '2024-04-22').verify(claim).abstention_code == 'X3'
    assert Judge(store, '2024-04-22').verify(replace(explicit, unit='%')).abstention_code == 'X3'


def test_wrapped_roe_rows_keep_historical_values_when_change_cell_is_blank():
    """A blank YoY cell must not discard the three ROE period cells."""
    from mingjing.parser import _align_known_cells

    original = (
        '加权平均净资产收益率%（依据\n'
        '归属于上市公司股东的净利润\n'
        '计算）\n-\n-63.55%\n-65.05%\n-245.96%'
    )
    values = [
        {'num': '-63.55', 'unit': '%'},
        {'num': '-65.05', 'unit': '%'},
        {'num': '-245.96', 'unit': '%'},
    ]
    # The helper accepts match objects in production; use the parser's regex
    # so the regression exercises the same token shape.
    import mingjing.parser as parser
    values = list(parser._NUMBER.finditer(original))[-3:]
    aligned = _align_known_cells(original, 10, values,
                                 ['2023Y', '2022Y', '2021Y'],
                                 [250.0, 338.0, 500.0], [], None)
    assert aligned == values


def test_fact_section_context_does_not_turn_prose_into_year_columns(tmp_path, monkeypatch):
    from mingjing import parser
    path = tmp_path / '2024-04-01_2023年年度报告.pdf'
    path.write_bytes(b'pdf-source')
    rows = ['合并利润表', '单位：元', '2023年\n2022年',
            '营业收入\n100.00\n80.00',
            '营业成本100.00元，其中服务成本80.00元',
            '净利润10.00元，报告期末预计20.00元']
    monkeypatch.setattr(parser, 'extract_text', lambda _: [{'page': 1, 'text': '\n'.join(rows), 'blocks': [
        {'bbox': [0, n*30, 500, n*30+20], 'text': row} for n, row in enumerate(rows)]}])
    facts = parser.extract_facts(path, '甲', '2023Y')
    assert len(facts) == 2 and all(f.account == 'REVENUE' for f in facts)


def test_million_yuan_unit_mismatches_are_classified_as_unit_errors():
    from mingjing.mentions import extract_mentions
    store = FactStore([Fact('f', '甲', 'REVENUE', '2023Y', value=Decimal('496000000'), publish_date='2024-04-01')])
    judge = Judge(store, '2024-04-22')
    for unit in ('元', '万元', '亿元'):
        claim = extract_mentions(f'2023年营业收入为496{unit}。', '甲')[0]
        result = judge.verify(claim)
        assert result.status == 'violated' and result.error_type == 'E4'
    claim = extract_mentions('2023年营业收入为496百万元。', '甲')[0]
    assert judge.verify(claim).status == 'verified'
