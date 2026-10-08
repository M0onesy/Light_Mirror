"""Exposed business captions need source identity, not equal-value aliases."""
from dataclasses import replace

from mingjing.coverage import CoverageLedger
from mingjing.layout_noise import chart_layout_exclusions
from mingjing.mentions import extract_mentions
from mingjing.mentions import listed_periods
from mingjing.source_semantics import freeze_source_dimensions,source_dimensions_from_text


def test_short_business_name_is_preserved_without_equating_it_to_service_aggregate():
    for statement in ('超算云收入2023年达4.14亿元，其中AI云面向高校。',
                      '业务：AI云引领增长，超算云整体收入2023年达4.14亿元。'):
        claim=extract_mentions(statement,'并行科技','2023Y')[0]
        assert claim.account=='REVENUE'
        source=source_dimensions_from_text(freeze_source_dimensions(claim))
        assert source['dimension_kind']=='business' and source['dimension_id']=='超算云'
        assert source['company']=='并行科技' and not source.get('scope')
        assert source['dimension_id']!='超算云服务'


def test_written_revenue_subject_survives_bounded_growth_continuations():
    text='从产品收入结构来看，2022年\n超算云服务为主要创收模块，营收2.67亿元，2023年进一步增长55%至4.14亿元。'
    claims=extract_mentions(text,'并行科技','2023Y',preserve_occurrences=True)
    assert len(claims)==3
    assert [(c.account,c.period,c.relation) for c in claims]==[
        ('REVENUE','2022Y','value'),('REVENUE','2023Y','yoy'),('REVENUE','2023Y','value')]
    for claim in claims:
        source=source_dimensions_from_text(freeze_source_dimensions(claim))
        assert source['company']=='并行科技' and source['dimension_id']=='超算云服务'
        assert source['dimension_source']['caption_relation']=='bounded_same_subject_continuation'
        assert not source['scope']


def test_business_subject_cannot_cross_a_new_issuer_total_business_or_sentence():
    for continuation in ('乙公司营收2.67亿元','公司整体营收2.67亿元','并行行业云营收2.67亿元',
                         '总营收2.67亿元','公司营业成本2.67亿元'):
        text='2022年超算云服务为主要创收模块，'+continuation+'。'
        claim=extract_mentions(text,'并行科技','2023Y')[0]
        assert claim.table_context.get('dimension_source',{}).get('caption_relation')!='bounded_same_subject_continuation'
    explicit=extract_mentions('2022年乙公司超算云服务为主要创收模块，营收2.67亿元。','并行科技')[0]
    source=source_dimensions_from_text(freeze_source_dimensions(explicit))
    assert source['company']=='' and source['source_company_conflict']
    total=extract_mentions('2022年超算云服务为主要创收模块。2023年公司整体营收2.67亿元。','并行科技')[0]
    assert total.table_context.get('dimension_kind','total')=='total'


def test_model_cannot_rewrite_a_short_business_identity_as_a_service_name():
    claim=freeze_source_dimensions(extract_mentions('2023年超算云收入4.14亿元。','并行科技')[0])
    tampered=replace(claim,table_context={**claim.table_context,'dimension_id':'超算云服务'})
    import pytest
    with pytest.raises(ValueError,match='来源定义'):
        source_dimensions_from_text(tampered)


def test_money_growth_caption_parses_both_numbers_without_inventing_revenue_method():
    text='图10：2023年超算云服务增长55%至4.14亿元（单位：万元）'
    ledger,claims=CoverageLedger.build(text,[],[],'并行科技','2023Y')
    assert len(claims)==2
    assert [claim.candidate_value for claim in claims]==['0.55','414000000.00']
    for claim in claims:
        assert claim.account=='UNKNOWN' and claim.parse_status=='parsed'
        source=source_dimensions_from_text(freeze_source_dimensions(claim))
        assert source['company']=='并行科技' and source['dimension_id']=='超算云服务'
        assert source['account']=='' and not source.get('definition_id') and not source['scope']
        assert source['dimension_source']['measure_method_unresolved']
    assert not extract_mentions('2023年超算云服务用户增长55%。','并行科技','2023Y')
    assert len(ledger.entries)>len(claims)  # Caption number/year remain addressed.


def test_wrapped_financial_percent_is_parsed_but_numeric_axis_cannot_borrow_the_clause():
    text=('公司逐步加大了对专用设备的采购以丰富共建模式下自有算力资源池，至2021\n'
          '年，公司以共建模式提供的超算云服务收入占总体超算云服务收入的比例已超过\n70%。')
    claims=extract_mentions(text,'并行科技','2023Y')
    assert len(claims)==1 and claims[0].candidate_value=='0.70'
    assert claims[0].parse_status=='parsed' and claims[0].numeric_text=='70%'
    axes='图1：公司营业收入增长\n0%\n20%\n40%\n60%\n数据来源：原披露材料'
    assert extract_mentions(axes,'并行科技','2023Y')==[]
    ratios=('公司2022年以来大力开展共建模式优化自有算力资源池，目前内实现了由外采\n'
            '算力模式向共建模式的过渡，以并行通用超算云为例，2020-2022年共建模式下形成\n'
            '的收入分别为4437.80万元、10158.77万元和13166.95万元，占比分别为51.62%、\n'
            '78.53%和81.27%，随着共建模式的逐步成型，公司超算云业务综合毛利率及细分\n'
            '业务并行通用超算云和并行行业云毛利率均受一定影响。')
    ledger,claims=CoverageLedger.build(ratios,[],[],'并行科技','2023Y')
    middle=next(claim for claim in claims if claim.numeric_text=='78.53%')
    assert middle.parse_status=='parsed' and middle.candidate_value=='0.7853'
    assert next(row for row in ledger.entries if row['text']=='78.53%')['claim_id']==middle.claim_id


def test_gpu_identifier_is_recorded_but_never_treated_as_a_financial_amount():
    text='公司采购22台A800型号显卡AI算力服务器，采购金额687万元。'
    ledger,claims=CoverageLedger.build(text,[],[],'并行科技','2023Y')
    model=next(row for row in ledger.entries if row['text'].strip()=='800')
    assert model['status']=='rule_excluded' and model['reason']=='technical_model_identifier'
    assert model['exclusion_basis']['source_span']['reason']=='technical_model_identifier'
    assert any(row['text']=='687万元' and row['claim_id'] for row in ledger.entries)
    assert all(claim.numeric_text.strip()!='800' for claim in claims)


def test_mixed_historical_forecast_chart_years_need_a_real_axis_and_citation():
    rows=[('图32：全球AI计算市场规模（亿美元）',[50,20,340,30]),
          ('0',[70,170,80,180]),('100',[60,140,80,150]),('200',[60,110,80,120]),
          ('300',[60,80,80,90]),('400',[60,50,80,60]),
          ('2022',[110,183,134,195]),('2023E',[160,183,190,195]),
          ('2024E',[210,183,240,195]),('2025E',[260,183,290,195]),
          ('数据来源：原披露材料',[50,225,320,237])]
    text='';lines=[]
    for raw,box in rows:
        start=len(text);text+=raw+'\n'
        lines.append({'text':raw,'bbox':box,'start':start,'end':start+len(raw)})
    pages=[{'id':'p24','page':24,'start':0,'end':len(text),'lines':lines}]
    proofs=chart_layout_exclusions(text,pages)
    first=next(line for line in lines if line['text']=='2022')['start']
    assert proofs[first]['reason']=='chart_period_label'
    assert len(proofs[first]['basis']['baseline_source_lines'])==4
    no_axis={**pages[0],'lines':[line for line in lines if line['text'] not in {'0','100','200','300','400'}]}
    assert first not in chart_layout_exclusions(text,[no_axis])
    no_citation={**pages[0],'lines':lines[:-1]}
    assert first not in chart_layout_exclusions(text,[no_citation])


def test_core_business_qualifier_is_not_an_issuer_and_preserves_other_or_parallel_companies():
    for prefix, company, conflict, ambiguous in [
        ('其中核心业务', '并行科技', False, False),
        ('甲公司核心业务', '', True, False),
        ('并行科技核心业务', '并行科技', False, False),
        ('乙公司核心业务', '', True, False),
        ('并行科技与乙公司核心业务', '', False, True),
    ]:
        text = '2023年' + prefix + '超算云服务毛利率为27%。'
        claim, = extract_mentions(text, '并行科技', '2023Y')
        source = source_dimensions_from_text(freeze_source_dimensions(claim))
        assert source['dimension_id'] == '超算云服务' and source['account'] == 'GROSS_MARGIN'
        assert source['company'] == company
        assert bool(source.get('source_company_conflict')) is conflict
        assert bool(source.get('source_company_ambiguous')) is ambiguous
        assert source['scope'] == ''  # Business grammar never grants reporting scope.


def test_general_supercomputing_cloud_is_a_distinct_source_business_without_value_aliases():
    from decimal import Decimal
    from mingjing.facts import FactStore
    from mingjing.judge import Judge
    from mingjing.models import Fact
    text = '从收入规模角度，2021年公司通用超算云收入约1.3亿元，据沙利文测算。'
    claim, = extract_mentions(text, '并行科技', '2023Y')
    frozen = freeze_source_dimensions(claim)
    source = source_dimensions_from_text(frozen)
    assert source['company'] == '并行科技' and not source.get('source_company_conflict')
    assert source['dimension_id'] == '通用超算云' and source['dimension_kind'] == 'business'
    assert source['period'] == '2021Y' and source['account'] == 'REVENUE'
    assert source['dimension_id'] not in {'并行通用超算云', '超算云', '超算云服务'}
    store = FactStore([Fact('same-value-different-name', '并行科技', 'REVENUE', '2021Y',
                            value=Decimal('130000000'), publish_date='2022-04-01',
                            dimension_kind='business', dimension_id='并行通用超算云')])
    # Even supplying a valid task scope cannot convert two written names
    # into aliases merely because the numeric values happen to agree.
    from mingjing.scope import apply_scope_clarification, create_scope_clarification
    from mingjing.audit import digest
    clarification = create_scope_clarification('合并', draft_sha256='a'*64)
    clarified = apply_scope_clarification([claim], dict(clarification, sha256=digest(clarification)))[0]
    result = Judge(store, '2024-04-22').verify(freeze_source_dimensions(clarified))
    assert result.status == 'abstained' and result.abstention_code == 'X1'
    assert result.expected is None and not result.operands
    import pytest
    with pytest.raises(ValueError, match='来源定义'):
        source_dimensions_from_text(replace(frozen, table_context={**frozen.table_context,
                                      'dimension_id': '并行通用超算云'}))


def test_secondary_explicit_ratio_list_revalidates_all_three_periods_without_authorizing_denominator():
    from mingjing.facts import FactStore
    from mingjing.judge import Judge
    text = ('公司2022年以来大力开展共建模式优化自有算力资源池，目前内实现了由外采\n'
            '算力模式向共建模式的过渡，以并行通用超算云为例，2020-2022年共建模式下形成\n'
            '的收入分别为4437.80万元、10158.77万元和13166.95万元，占比分别为51.62%、\n'
            '78.53%和81.27%，随着共建模式的逐步成型，公司超算云业务综合毛利率及细分。')
    ledger, claims = CoverageLedger.build(text, [], [], '并行科技', '2023Y')
    ratios = [claim for claim in claims if claim.numeric_text in {'51.62%', '78.53%', '81.27%'}]
    assert len(ratios) == 3 and [claim.period for claim in ratios] == ['2020Y', '2021Y', '2022Y']
    for index, claim in enumerate(ratios):
        basis = claim.table_context['source_period_list']
        assert basis['source'] == 'explicit_secondary_ordered_period_list' and basis['ambiguous'] is False
        assert len(basis['parent_occurrence_spans']) == len(basis['occurrence_spans']) == 3
        assert basis['occurrence_index'] == index
        frozen = freeze_source_dimensions(claim)
        assert source_dimensions_from_text(frozen)['period'] == claim.period
        assert source_dimensions_from_text(frozen)['definition_id'] == ''
        assert claim.account == 'UNKNOWN'
        result = Judge(FactStore([]), '2024-04-22').verify(frozen)
        assert result.status == 'abstained' and result.abstention_code == 'X2'
        assert not result.operands and result.expected is None
        assert any(row['start'] == claim.start and row['claim_id'] == claim.claim_id for row in ledger.entries)


def test_secondary_period_mapping_requires_complete_adjacent_primary_and_ratio_lists():
    samples = [
        '2020-2022年收入分别为10万元、20万元和30万元，占比分别为10%、20%。',
        '2020-2022年收入分别为10万元、30万元，占比分别为10%、20%、30%。',
        '2020-2022年收入分别为10万元、20万元和30万元，乙公司占比分别为10%、20%、30%。',
        '2020-2022年收入分别为10万元、20万元和30万元。占比分别为10%、20%、30%。',
        '2020-2022年收入分别为10万元、20万元和30万元，占比分别为10万元、20万元、30万元。',
    ]
    for text in samples:
        target = text.index('占比分别为') + len('占比分别为')
        period, basis = listed_periods(text, target)
        assert not period
        assert not basis or basis.get('source') != 'explicit_secondary_ordered_period_list'
