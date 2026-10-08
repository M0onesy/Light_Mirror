from decimal import Decimal
from mingjing.cli import _semantic_filter_claims
from mingjing.facts import FactStore
from mingjing.judge import Judge
from mingjing.mentions import extract_mentions
from mingjing.models import Fact
from mingjing.source_semantics import freeze_source_dimensions, source_dimensions_from_text

def test_rounded_amounts_use_actual_display_precision():
    store = FactStore([
        Fact('rev', 'A', 'REVENUE', '2023Y', value=Decimal('495671818.88'), evidence=('re',)),
        Fact('np', 'A', 'NP_PARENT', '2023Y', value=Decimal('-80499535.36'), evidence=('ne',)),
    ])
    claims = extract_mentions('2023年营业收入4.96亿元，归母净利润-8050万元。', 'A')
    assert len(claims) == 2
    results = [Judge(store).verify(c) for c in claims]
    assert [r.status for r in results] == ['verified', 'verified']
    # Both raw fact disclosure (0.01 yuan) and claim (1万元) contribute
    # half a resolution; neither side is treated as an exact point.
    assert Decimal(results[1].tolerance) == Decimal('5000.005')
    wrong = extract_mentions('2023年归母净利润-8050.00万元。', 'A')[0]
    assert Judge(store).verify(wrong).status == 'violated'

def test_noise_forecasts_and_chart_axes_do_not_become_claims():
    text = ('图8：2023年归母净利润-8050万元 ........... 7\n'
            '2023H1\n1\n2024年4月22日\n证书编号：S0790522080002\n'
            '营业收入（亿）\n10%\n20%\n30%\n'
            '我们预计公司2024-2026年归\n母净利润为-0.37/0.07/0.45亿元。')
    assert extract_mentions(text, 'A', '2023Y') == []

def test_same_clause_labels_and_periods_are_resolved_before_values():
    text = '2022年营业收入3.13亿元，2023年营业收入4.96亿元，归母净利润-8050万元。'
    claims = extract_mentions(text, 'A')
    assert [(c.account, c.period) for c in claims] == [('REVENUE', '2022Y'), ('REVENUE', '2023Y'), ('NP_PARENT', '2023Y')]

def test_segment_and_share_never_compare_with_consolidated_revenue():
    text = '2023年超算云服务营收4.14亿元。2023H1前五大客户营收占总营收的比例为26.44%。'
    claims = extract_mentions(text, 'A')
    assert len(claims) == 2
    assert [c.account for c in claims] == ['REVENUE', 'REVENUE_SHARE']
    assert [c.table_context['dimension_kind'] for c in claims] == ['business', 'customer']
    assert [c.table_context['dimension_id'] for c in claims] == ['超算云服务', '前五大客户']
    assert claims[1].period == '2023H1'
    # An exactly matching issuer total is not evidence for a business/customer
    # dimension. These source-defined aggregates require their own facts.
    totals = FactStore([
        Fact('total', 'A', 'REVENUE', '2023Y', value=Decimal('414000000'), evidence=('te',)),
        Fact('total-share', 'A', 'REVENUE_SHARE', '2023H1', value=Decimal('.2644'), evidence=('se',)),
    ])
    assert all(Judge(totals).verify(freeze_source_dimensions(c)).status == 'abstained' for c in claims)
    for claim in claims:
        source = source_dimensions_from_text(freeze_source_dimensions(claim))
        assert source['company'] == 'A' and not source.get('source_company_conflict')


def test_source_cagr_duration_is_not_a_company_name_but_other_subjects_are_preserved():
    text = '公司持续高速发展，2023年实现营收4.96亿元（+58%），2018-2023年5年营收CAGR达41%。'
    cagr = next(c for c in extract_mentions(text, 'A') if c.relation == 'cagr')
    source = source_dimensions_from_text(freeze_source_dimensions(cagr))
    assert (source['company'], source['period'], source['base_period']) == ('A', '2023Y', '2018Y')
    assert not source.get('source_company_conflict')
    for statement in ('2018-2023年乙公司营收CAGR达41%。',
                      '2023H1乙公司前五大客户营收占总营收的比例为26.44%。'):
        claim = extract_mentions(statement, 'A')[0]
        observed = source_dimensions_from_text(freeze_source_dimensions(claim))
        assert observed['company'] == '' and observed['source_company_conflict']
    unclear = extract_mentions('2023H1前五大客户收入占比26.44%。', 'A')[0]
    assert unclear.account == 'UNKNOWN' and not unclear.table_context.get('definition_id')

def test_missing_period_is_not_automatically_an_error():
    facts = FactStore([Fact('f', 'A', 'REVENUE', '2023Y', value=Decimal('1000000'), evidence=('e',))])
    claim = extract_mentions('2023年上半年营业收入60万元。', 'A')[0]
    result = Judge(facts).verify(claim)
    assert (result.status, result.error_type) == ('abstained', 'E8')

def test_negative_growth_base_abstains():
    facts = FactStore([Fact('f', 'A', 'NP_PARENT', '2023Y', value=Decimal('-80')), Fact('b', 'A', 'NP_PARENT', '2022Y', value=Decimal('-114'))])
    claim = extract_mentions('2023年归母净利润同比增长29%。', 'A')[0]
    result = Judge(facts).verify(claim)
    assert result.diagnostics == ['X2'] and result.abstention_code == 'X2'

def test_semantic_matching_maps_original_offsets_and_filters_all_forecasts():
    text = '图1：营业收入4.96亿元 .... 6\n2023年营业收入4.96\n亿元。预计2024年营业收入6亿元。'
    claims = extract_mentions(text, 'A', '2023Y')
    selected, count = _semantic_filter_claims(text, claims, [
        {'source': '2023年营业收入4.96亿元', 'candidate_value': '999999'},
        {'source': '预计2024年营业收入6亿元'},
    ])
    assert count == 1 and selected[0].candidate_value == '496000000.00'
    assert text[selected[0].start:selected[0].end] == '4.96\n亿元'
