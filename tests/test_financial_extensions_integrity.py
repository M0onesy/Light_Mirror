"""Real disclosure precision remains authoritative throughout analysis and parsing."""
from dataclasses import replace
from decimal import Decimal, getcontext
from pathlib import Path

import pytest

from mingjing.analysis import analyze_financials
from mingjing.decimal_math import DecimalInterval, fact_interval
from mingjing.facts import FactStore
from mingjing.models import Fact
from mingjing.models import AtomicClaim
from mingjing.judge import Judge
from mingjing import parser
from mingjing.rules import RuleError, execute_rule, execute_rule_interval, intervals_compatible, load_rule_cards, evaluate_rule, validate_rulepack


def fact(account, value, period='2023Y', **kwargs):
    return Fact(account+'-'+period, '公司甲', account, period, value=Decimal(value),
                source_doc=kwargs.pop('source_doc', 'report'), publish_date=kwargs.pop('publish_date', '2024-04-15'),
                evidence=(account+':cell',), nr='扣非' if account == 'NP_PARENT_ADJ' else '未扣非', **kwargs)


def metric(rows, name):
    result = analyze_financials(FactStore(rows), '公司甲', ['2023Y'], '2024-04-22')
    return next(item for item in result['metrics'] if item['metric'] == name)


@pytest.mark.parametrize('rejection', ['conflict', 'low_confidence', 'unknown_date'])
def test_analysis_does_not_replace_rejected_disclosed_ratio_with_convenient_formula(rejection):
    source = fact('GROSS_MARGIN', '.4', display_unit='%')
    if rejection == 'conflict':
        rows = [source, replace(source, fact_id='conflict', value=Decimal('.5'))]
    elif rejection == 'low_confidence':
        rows = [replace(source, parse_conf='.5')]
    else:
        rows = [replace(source, publish_date='')]
    rows += [fact('REVENUE', '100'), fact('COST', '60')]
    result = metric(rows, 'GROSS_MARGIN')
    assert result['status'] == 'abstained' and result['abstention_code'] == 'X4'
    assert result['value'] is None and not result['operands']


def test_analysis_uses_registered_formula_and_propagates_growth_disclosure_intervals():
    rows = [fact('REVENUE', '1200000', display_unit='万元', decimals=0),
            fact('REVENUE', '1000000', '2022Y', display_unit='万元', decimals=0)]
    result = metric(rows, 'REVENUE_YOY')
    assert result['value'] == '0.2' and result['rule_id'] == 'E5-yoy'
    assert Decimal(result['expected_interval']['lower']) < Decimal('.208') < Decimal(result['expected_interval']['upper'])
    assert result['source_precision'][0]['resolution'] == '10000'


def test_non_recurring_comparison_propagates_all_four_inputs_instead_of_scalar_tolerance():
    rows = [fact(account, value, decimals=0) for account, value in
            [('NP_PARENT', '130'), ('NRI_TOTAL', '20'), ('NRI_TAX_EFFECT', '3'), ('NRI_MINORITY', '2')]]
    rows.append(fact('NP_PARENT_ADJ', '116.5', decimals=1))
    result = metric(rows, 'NP_PARENT_RECOMPUTED')
    assert result['value'] == '115' and result['match_disclosure']
    assert Decimal(result['expected_interval']['lower']) == 113
    assert Decimal(result['expected_interval']['upper']) == 117
    assert result['rule_id'] == 'FIN-non-recurring' and len(result['operands']) == 5


def test_source_interval_touching_zero_prevents_analysis_division():
    result = metric([fact('LIABILITIES', '1'), fact('ASSETS', '.004', decimals=2)], 'DEBT_RATIO')
    assert result['status'] == 'abstained' and result['abstention_code'] == 'X2'
    assert result['value'] is None


def test_threshold_signals_do_not_assume_a_coarse_interval_is_entirely_below_threshold():
    rows = [fact(account, value, period, decimals=1 if account == 'CFO' else 0)
            for period in ('2022Y', '2023Y') for account, value in [('CFO', '49.9'), ('NP', '100')]]
    result = analyze_financials(FactStore(rows), '公司甲', ['2022Y', '2023Y'], '2024-04-22')
    assert not any(item['kind'] == 'low_cash_content' for item in result['signals'])


def test_interval_rule_checks_half_open_touch_and_explicit_closed_endpoints():
    a = DecimalInterval(Decimal(0), Decimal(1))
    b = DecimalInterval(Decimal(1), Decimal(2))
    assert not intervals_compatible(a, b)
    assert intervals_compatible(replace(a, upper_closed=True), b)
    assert validate_rulepack()['status'] == 'ok'
    assert validate_rulepack()['version'] == '0.6.1'


def test_rule_execution_keeps_global_decimal_context_and_refuses_forged_boolean():
    before = repr(getcontext())
    value = execute_rule('FIN-net-margin', {'NP': '1', 'REVENUE': '3'})
    assert value > 0 and repr(getcontext()) == before
    card = next(item for item in load_rule_cards() if item['id'] == 'E1-value')
    operands = dict(card['examples']['positive']['operands'], observed_lower_closed='2')
    with pytest.raises(RuleError, match='布尔'):
        evaluate_rule(card, operands)


def test_generic_interval_ast_rejects_extra_operands_and_handles_signed_multiplication():
    a = DecimalInterval(Decimal(-2), Decimal(3))
    b = DecimalInterval(Decimal(0), Decimal(4))
    product = a.multiply(b)
    assert product.lower == -8 and product.upper == 12
    assert not product.lower_closed and not product.upper_closed
    assert product.absolute().lower == 0
    with pytest.raises(RuleError, match='未授权'):
        execute_rule_interval('FIN-gross-margin', {'REVENUE': a, 'COST': b, 'forged': a})


@pytest.mark.parametrize('current,base,relation,status', [
    ('100','99','gt','verified'), ('99','100','gt','violated'),
    ('100','100','gt','abstained'), ('100','100','eq','abstained'),
    ('99','100','lt','verified'), ('100','99','le','violated'),
])
def test_logic_comparisons_execute_registered_rule_and_preserve_precision_uncertainty(current, base, relation, status):
    store = FactStore([fact('REVENUE', current, decimals=0), fact('REVENUE', base, '2022Y', decimals=0)])
    claim = AtomicClaim('c','公司甲2023年合并营业收入高于2022年。',0,1,
        company='公司甲',account='REVENUE',period='2023Y',base_period='2022Y',relation=relation,
        caliber_hint=('合并','未扣非'))
    result = Judge(store, '2024-04-22').verify(claim)
    assert result.status == status
    if status == 'abstained':
        assert result.abstention_code == 'X4'
    else:
        assert result.rule_ids == ['E7-comparison'] and result.source_precision


def extract(tmp_path, monkeypatch, rows, *, strict=False):
    path = tmp_path / '2024-04-15_2023年年度报告.pdf'
    path.write_bytes(b'original-report')
    blocks = [{'bbox': [0, n*30, 500, n*30+20], 'text': row} for n, row in enumerate(rows)]
    pages = [{'page': 1, 'text': '\n'.join(rows), 'blocks': blocks}]
    if strict:
        issues = []
        result = parser._extract_table_facts(pages, path=path, company='公司甲', effective_period='2023Y',
            doc_sha='a'*64, disclosure_date='2024-04-15', disclosure_version='原披露', window_issues=issues)
        return result, issues
    monkeypatch.setattr(parser, 'extract_text', lambda _: pages)
    return parser.extract_facts(path, '公司甲', '2023Y'), []


@pytest.mark.parametrize('strict', [False, True])
def test_explicit_adjustment_subcaptions_keep_before_and_after_facts_distinct(tmp_path, monkeypatch, strict):
    rows = ['合并利润表', '单位：元',
            '2023年（调整后）|2022年（调整前）|2022年（调整后）', '营业收入 120.00 80.00 100.00']
    facts, issues = extract(tmp_path, monkeypatch, rows, strict=strict)
    assert len(facts) == 3 and not issues
    prior = FactStore(facts).query('公司甲', 'REVENUE', '2022Y')
    assert prior.value == 100 and prior.version.endswith('/调整后')
    assert '调整后' in prior.column_label
    assert any(item.period == '2022Y' and item.version.endswith('/调整前') and item.value == 80 for item in facts)


def test_duplicate_periods_without_version_proof_never_choose_by_amount(tmp_path, monkeypatch):
    facts, issues = extract(tmp_path, monkeypatch,
        ['合并利润表', '单位：元', '2023年|2022年|2022年', '营业收入 120.00 80.00 100.00'], strict=True)
    assert not facts and any(item['code'] == 'period_unproven' for item in issues)


def test_ratio_percent_markers_and_cost_account_identity_are_local(tmp_path, monkeypatch):
    facts, _ = extract(tmp_path, monkeypatch, ['合并利润表', '单位：万元', '2023年|2022年',
        '营业总成本 130.00 100.00', '营业成本 60.00 50.00', '加权平均净资产收益率（%）11.0 10.0',
        '净利率（%）12.0 9.0', '净资产收益率（%）13.0 12.0'])
    store = FactStore(facts)
    assert store.query('公司甲', 'TOTAL_COST', '2023Y').value == 1300000
    assert store.query('公司甲', 'COST', '2023Y').value == 600000
    assert store.query('公司甲', 'ROE', '2023Y').value == Decimal('.110')
    assert store.query('公司甲', 'NET_MARGIN', '2023Y').value == Decimal('.120')
    assert len(facts) == 8  # Unqualified ROE remains unsupported.


def test_physical_two_level_header_proves_a_prior_adjustment_pair(tmp_path):
    path = tmp_path / 'report.pdf'
    blocks = [
        {'text': '合并利润表', 'bbox': [0,0,100,8]}, {'text': '单位：元', 'bbox': [0,12,100,20]},
        {'text': '2023年', 'bbox': [190,30,230,40]}, {'text': '2022年', 'bbox': [320,30,360,40]},
        {'text': '调整前', 'bbox': [265,47,295,57]}, {'text': '调整后', 'bbox': [375,47,405,57]},
        {'text': '营业收入 120.00 80.00 100.00', 'bbox': [0,80,500,90]},
    ]
    page = {'page':1,'text':'\n'.join(item['text'] for item in blocks), 'blocks':blocks}
    issues = []
    result = parser._extract_table_facts([page], path=path, company='公司甲', effective_period='2023Y',
        doc_sha='a'*64, disclosure_date='2024-04-15', disclosure_version='原披露', window_issues=issues)
    assert len(result) == 3 and not issues
    assert FactStore(result).query('公司甲', 'REVENUE', '2022Y').value == 100
    prior = next(item for item in result if item.period == '2022Y' and item.value == 100)
    assert len(prior.evidence) >= 7  # Both period captions and version cells are retained.


def test_weighted_roe_definition_and_row_scopes_remain_distinct(tmp_path, monkeypatch):
    facts, _ = extract(tmp_path, monkeypatch, ['会计数据和财务指标', '单位：元', '2023年|2022年',
        '加权平均净资产收益率%（依据归属于上市公司股东的净利润计算） -63.55% -65.05%',
        '加权平均净资产收益率%（依据归属于上市公司股东的扣除非经常性损益后的净利润计算） -68.55% -65.72%',
        '资产负债率%（母公司） 55.38% 65.46%', '资产负债率%（合并） 69.44% 82.83%'])
    store = FactStore(facts)
    assert store.query('公司甲', 'ROE', '2023Y').value == Decimal('-.6355')
    assert store.query('公司甲', 'ROE', '2023Y', nr='扣非').value == Decimal('-.6855')
    assert store.query('公司甲', 'DEBT_RATIO', '2023Y', scope='母公司').value == Decimal('.5538')
    assert store.query('公司甲', 'DEBT_RATIO', '2023Y').value == Decimal('.6944')
    assert len(facts) == 8


@pytest.mark.parametrize('reference', ['五、（二）、1', '十五、（四）', '六、30'])
def test_printed_chinese_note_reference_does_not_count_as_financial_amount(tmp_path, monkeypatch, reference):
    facts, _ = extract(tmp_path, monkeypatch, ['合并利润表', '单位：元', '项目|附注|2023年|2022年',
        '其中：营业成本\n'+reference+'\n60.00\n50.00'])
    assert len(facts) == 2
    assert FactStore(facts).query('公司甲','COST','2023Y').value == 60


def test_decimal_note_reference_requires_an_explicit_note_column(tmp_path, monkeypatch):
    rows = ['合并利润表','单位：元','项目|附注|2023年|2022年','营业成本\n16.4\n60.00\n50.00']
    facts, _ = extract(tmp_path, monkeypatch, rows)
    assert len(facts) == 2 and FactStore(facts).query('公司甲','COST','2023Y').value == 60
    rows[2] = '项目|2023年|2022年'
    facts, _ = extract(tmp_path, monkeypatch, rows)
    assert not facts


def test_notes_caption_is_not_a_primary_statement(tmp_path, monkeypatch):
    facts, _ = extract(tmp_path, monkeypatch, ['合并利润表项目注释','单位：元','2023年|2022年','营业成本 60.00 50.00'])
    assert not facts


@pytest.mark.parametrize('amount_y,expected', [(60, True), (82, False)])
def test_wrapped_reference_block_joins_only_physical_same_row(tmp_path, monkeypatch, amount_y, expected):
    path = tmp_path/'2024-04-01_2023年年度报告.pdf'
    path.write_bytes(b'original-source')
    blocks = [{'bbox':[0,0,100,10], 'text':'合并利润表'},
              {'bbox':[0,15,100,25], 'text':'单位：元'},
              {'bbox':[0,30,500,40], 'text':'项目\n附注\n2023年\n2022年'},
              {'bbox':[0,54,325,70], 'text':'其中：营业成本\n五、（三',
               'geometry_lines':[{'text':'其中：营业成本','bbox':[0,60,100,70]},
                                 {'text':'五、（三','bbox':[280,54,325,64]}]},
              {'bbox':[285,amount_y,500,amount_y+17],
               'text':'十九）\n60.00\n50.00'}]
    # The PDF text-line geometry is preserved by the row normalization.
    lines = []
    text = ''
    for block in blocks:
        for index, part in enumerate(block['text'].splitlines()):
            start = len(text); text += part+'\n'
            local = next((line for line in block.get('geometry_lines', []) if line['text'] == part), None)
            box = local['bbox'] if local else block['bbox']
            lines.append({'text':part, 'bbox':box,'start':start,'end':start+len(part)})
    monkeypatch.setattr(parser, 'extract_text', lambda _: [{'page':1,'text':text,'blocks':blocks,'lines':lines}])
    facts = parser.extract_facts(path, '公司甲', '2023Y')
    selected = FactStore(facts).query('公司甲','COST','2023Y')
    assert bool(selected) is expected
    if expected:
        from mingjing.evidence import make_evidence_id, sha256_file
        original_ids = {make_evidence_id(sha256_file(path), 1, block['text'].strip(), block['bbox'])
                        for block in blocks}
        assert selected.value == 60 and set(selected.evidence) == original_ids
        assert len(original_ids) == 5  # Two physical row spans plus table/unit/period proofs.


def test_wrapped_summary_label_uses_full_row_height(tmp_path, monkeypatch):
    path = tmp_path/'2024-04-01_2023年年度报告.pdf'
    path.write_bytes(b'original-source')
    blocks = [{'bbox':[0,0,200,10], 'text':'会计数据和财务指标'},
              {'bbox':[0,15,100,25], 'text':'单位：元'},
              {'bbox':[0,30,500,40], 'text':'2023年\n2022年'},
              {'bbox':[0,55,150,91],
               'text':'加权平均净资产收益率%（依据\n归属于上市公司股东的净利润\n计算）'},
              {'bbox':[260,65,500,75], 'text':'18.30%\n49.56%'},
              {'bbox':[0,120,150,145], 'text':'归属于上市公司股东的扣除非\n经常性损益后的净利润'},
              {'bbox':[260,127,500,137], 'text':'60.00\n50.00'}]
    lines=[]; text=''
    for block in blocks:
        for part in block['text'].splitlines():
            start=len(text); text+=part+'\n'
            lines.append({'text':part,'bbox':block['bbox'],'start':start,'end':start+len(part)})
    monkeypatch.setattr(parser,'extract_text',lambda _: [{'page':1,'text':text,'blocks':blocks,'lines':lines}])
    facts=parser.extract_facts(path,'公司甲','2023Y')
    store=FactStore(facts)
    assert store.query('公司甲','ROE','2023Y').value == Decimal('.1830')
    assert store.query('公司甲','NP_PARENT_ADJ','2023Y',nr='扣非').value == 60
