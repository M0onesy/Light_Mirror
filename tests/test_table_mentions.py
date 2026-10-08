from decimal import Decimal

from mingjing.table_mentions import extract_table_mentions
from mingjing.models import Fact
from mingjing.facts import FactStore
from mingjing.judge import Judge
from mingjing.mentions import extract_mentions, unit_multiplier


def _parse(text):
    pages = [{'start': 0, 'end': len(text), 'page': 1}]
    blocks = [{'start': 0, 'end': len(text), 'text': text, 'bbox': [0, 0, 500, 500], 'table_candidate': True}]
    return extract_table_mentions(text, pages, blocks, '公司甲', '2099Y')


def test_historical_and_future_columns_with_original_offsets():
    text = '指标\n2022A\n2023A\n2024E\n营业收入(百万元)\n313\n496\n701\nYOY(%)\n42.1\n58.5\n41.3\n'
    claims = _parse(text)
    assert len(claims) == 6
    assert [(c.period, c.modality) for c in claims[:3]] == [('2022Y', 'fact'), ('2023Y', 'fact'), ('2024Y', 'forecast')]
    assert claims[1].candidate_value == '496000000'
    assert claims[4].account == 'REVENUE' and claims[4].relation == 'yoy'
    assert claims[4].base_period == '2022Y' and claims[4].candidate_value == '0.585'
    assert all(text[c.start:c.end] == c.numeric_text for c in claims)
    assert claims[0].caliber_hint[0] == '未知'  # No silent consolidated-scope assumption.


def test_wrap_label_multiple_periods_and_repeated_occurrences():
    text = ('合并利润表(万元)\n2023年\n2022年\n'
            '归属于上市公司股东的扣除非\n经常性损益后的净利润\n-8.00\n-10.00\n'
            '归属于上市公司股东的扣除非\n经常性损益后的净利润\n-8.00\n-10.00\n')
    claims = _parse(text)
    assert len(claims) == 4
    assert all(c.account == 'NP_PARENT_ADJ' and c.caliber_hint == ('合并', '扣非') for c in claims)
    assert len({c.start for c in claims}) == 4
    assert claims[0].candidate_value == '-80000.00'


def test_row_unit_does_not_leak_and_new_table_resets_unit():
    text = ('合并利润表(万元)\n2023A\n2022A\n营业收入(亿元)\n1.2\n1.0\n'
            '营业成本\n600.00\n500.00\n母公司利润表\n2023A\n2022A\n营业收入\n5\n4\n')
    claims = _parse(text)
    assert claims[0].candidate_value == '120000000.0'
    assert claims[2].candidate_value == '6000000.00'
    assert all(c.parse_status == 'unresolved' and c.candidate_value is None for c in claims[-2:])
    result = Judge(FactStore([])).verify(claims[-1])
    assert result.status == 'abstained' and result.abstention_code == 'X6'


def test_unknown_columns_or_unsupported_numbers_preserved_as_x6():
    claims = _parse('合并利润表(万元)\n营业收入\n100\n80\n')
    assert len(claims) == 2 and all(c.period == '' and c.parse_status == 'unresolved' for c in claims)
    claims = _parse('合并利润表(万元)\n2023A\n2022A\n营业收入\n一百\n80\n')
    assert len(claims) == 2 and claims[0].candidate_value is None
    assert Judge(FactStore([])).verify(claims[0]).abstention_code == 'X6'


def test_chart_and_customer_table_not_whole_company_claims():
    assert _parse('图6：公司收入趋势\n2019\n2020\n2021\n营业收入\n10\n20\n30\n') == []
    assert _parse('主要客户收入\n2022A\n2023A\n营业收入(万元)\n10\n20\n') == []


def test_supported_rows_verify_and_percentage_rows_do_not_become_money():
    text = ('合并利润表(百万元)\n2022A\n2023A\n营业收入\n313\n496\n'
            '营业收入(%)\n42.1\n58.5\n归母净利润\n-114\n-80\n')
    claims = _parse(text)
    store = FactStore([
        Fact('r23', '公司甲', 'REVENUE', '2023Y', value=Decimal('495671818.88'), publish_date='2024-04-15'),
        Fact('r22', '公司甲', 'REVENUE', '2022Y', value=Decimal('312776998.45'), publish_date='2024-04-15'),
        Fact('n23', '公司甲', 'NP_PARENT', '2023Y', value=Decimal('-80499535.36'), publish_date='2024-04-15'),
    ])
    judge = Judge(store, '2024-04-22')
    assert judge.verify(claims[1]).status == 'verified'
    assert judge.verify(claims[3]).status == 'verified'
    assert judge.verify(claims[-1]).status == 'verified'
    assert claims[3].candidate_value == '0.585' and claims[3].relation == 'yoy'


def test_explicit_diluted_eps_definition_is_separate_from_basic_and_percent_ratios():
    claims = _parse('合并利润表(百万元)\n2022A\n2023A\n稀释每股收益(元)\n-1.96\n-1.38\n毛利率(%)\n28.3\n25.6\n')
    assert claims[0].account == 'EPS_DILUTED' and claims[0].parse_status == 'parsed'
    store = FactStore([Fact('eps', '公司甲', 'EPS_DILUTED', '2023Y', value=Decimal('-1.38'), publish_date='2024-04-01')])
    assert Judge(store, '2024-04-22').verify(claims[1]).status == 'verified'
    assert claims[-1].account == 'GROSS_MARGIN' and claims[-1].candidate_value == '0.256'
    broker = _parse('合并利润表(百万元)\n2022A\n2023A\nEPS(摊薄/元)\n-1.96\n-1.38\n')
    assert all(c.account == 'EPS_BROKER_DILUTED_UNSPECIFIED' and c.parse_status == 'parsed' for c in broker)
    assert all(c.table_context['semantic_review_required'] is True for c in broker)
    assert all(Judge(store, '2024-04-22').verify(c).status == 'abstained' for c in broker)


def test_unknown_scope_and_ratio_definitions_cannot_default_to_consolidated():
    claims = _parse('指标\n2022A\n2023A\n营业收入(万元)\n100\n120\n净利率(%)\n8\n10\nROE(%)\n12\n15\n')
    store = FactStore([Fact('f', '公司甲', 'REVENUE', '2023Y', value=Decimal('1200000'), publish_date='2024-04-01')])
    assert Judge(store, '2024-04-22').verify(claims[1]).abstention_code == 'X3'
    assert all(c.parse_status == 'parsed' and c.table_context['semantic_review_required'] for c in claims[2:])
    assert all(Judge(store, '2024-04-22').verify(c).abstention_code == 'X3' for c in claims[2:])


def test_valuation_out_of_scope_and_unknown_row_has_no_guessed_dimension():
    claims = _parse('指标\n2022A\n2023A\nP/E(倍)\n-22.1\n-31.4\n现金周转天数\n20\n30\n')
    assert all(c.modality == 'opinion' for c in claims[:2])
    assert all(c.candidate_value is None and c.parse_status == 'unresolved' for c in claims[2:])


def test_million_yuan_prose_preserves_value():
    assert unit_multiplier('百万元') == Decimal('1000000')
    claims = extract_mentions('2023年营业收入为496百万元。', '甲', '2023Y')
    assert len(claims) == 1 and claims[0].candidate_value == '496000000'


def test_fact_parser_row_units_and_unknown_date(tmp_path, monkeypatch):
    from mingjing import parser
    path = tmp_path / '2023年年度报告.pdf'
    path.write_bytes(b'pdf-source')
    rows = ['合并利润表', '单位：万元', '2023年\n2022年',
            '营业收入(百万元)\n120.00\n100.00', '营业成本\n60.00\n50.00',
            '毛利率(%)\n50.0\n50.0', '营业收入增长率%\n20.0%\n25.0%',
            '母公司利润表', '2023年\n2022年', '营业收入\n10\n8']
    pages = [{'page': 1, 'text': '\n'.join(rows), 'blocks': [
        {'bbox': [0, n*30, 500, n*30+20], 'text': row} for n, row in enumerate(rows)]}]
    monkeypatch.setattr(parser, 'extract_text', lambda _: pages)
    facts = parser.extract_facts(path, '甲', '2099Y', '')
    assert len(facts) == 6
    store = FactStore(facts)
    assert store.query('甲', 'REVENUE', '2023Y').value == Decimal('120000000.00')
    assert store.query('甲', 'COST', '2023Y').value == Decimal('600000.00')
    assert store.query('甲', 'GROSS_MARGIN', '2023Y').value == Decimal('0.500')
    assert all(f.publish_date == '' for f in facts)
    assert store.query('甲', 'REVENUE', '2023Y', cutoff='2024-04-22') is None
    assert not any(f.scope == '母公司' for f in facts)  # Missing new-table unit stays missing.


def test_fact_parser_signed_nonrecurring_component_is_not_formula_operand(tmp_path, monkeypatch):
    from mingjing import parser
    path = tmp_path/'2024-04-15_2023年年度报告.pdf'
    path.write_bytes(b'pdf-source')
    rows = ['非经常性损益项目和金额', '单位：元', '2023年\n2022年',
            '非经常性损益合计\n6998.00\n1511.00',
            '所得税影响数\n-\n-', '少数股东权益影响额\n（税后）\n-656.00\n-339.00']
    monkeypatch.setattr(parser, 'extract_text', lambda _: [{'page': 1, 'text':'\n'.join(rows), 'blocks':[
        {'bbox':[0,n*30,500,n*30+20],'text':row} for n,row in enumerate(rows)]}])
    facts = parser.extract_facts(path,'甲','2023Y')
    assert any(f.account=='NRI_TOTAL' and f.value==Decimal('6998.00') for f in facts)
    assert any(f.account=='NRI_MINORITY_RAW' and f.value==Decimal('-656.00') for f in facts)
    assert not any(f.account in {'NRI_MINORITY','NRI_TAX_EFFECT'} for f in facts)
