"""Exposed source layout regressions; not independent human financial gold."""
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path

from mingjing.coverage import CoverageLedger
from mingjing.dimension_tables import extract_dimension_tables
from mingjing.layout_noise import chart_layout_exclusions
from mingjing.mentions import VALUE_RE, extract_mentions
from mingjing.scope import source_scope_dimensions
from mingjing.source_semantics import freeze_source_dimensions, source_dimensions_from_text
from mingjing.table_mentions import extract_table_mentions

FIXTURE = json.loads((Path(__file__).parent/'fixtures'/'financial_source_geometry_14.json').read_text(encoding='utf-8'))


def source():
    assert FIXTURE['origin'] == 'exposed_development_source_not_independent_gold'
    return FIXTURE['text'], FIXTURE['pages']


def test_customer_merged_periods_wrapped_names_and_adjacent_page_continuation():
    text, pages = source()
    claims = extract_dimension_tables(text, pages, '并行科技')
    customers = [claim for claim in claims if claim.table_context['dimension_kind'] == 'customer']
    assert len(customers) == 36
    assert {claim.period for claim in customers} == {'2023H1','2022Y','2021Y'}
    assert {claim.table_context['dimension_id'] for claim in customers if claim.period=='2021Y'} == {
        '凌空天行','清华大学','国防科技大学','中科院力学研究所','北京理工大学','前五大客户'}
    qingdao = [claim for claim in customers if claim.table_context['dimension_id']=='青岛国实科技集团有限公司']
    assert [(claim.account, Decimal(claim.candidate_value)) for claim in qingdao] == [
        ('REVENUE',Decimal('29670000')),('REVENUE_SHARE',Decimal('0.1465'))]
    for claim in customers:
        assert text[claim.start:claim.end] == claim.numeric_text
        assert claim.text[claim.source_numeric_start:claim.source_numeric_start+len(claim.numeric_text)] == claim.numeric_text
        dimensions = source_dimensions_from_text(freeze_source_dimensions(claim))
        assert dimensions['company'] == '并行科技'
        assert dimensions['dimension_id'] == claim.table_context['dimension_id']
        assert not dimensions.get('source_company_conflict')
    assert all(claim.table_context['customer_identity_requires_source_mapping'] for claim in customers
               if claim.table_context['dimension_id'] in {'客户E','客户H'})


def test_continuation_requires_matching_columns_and_adjacent_page():
    text, pages = source()
    changed = json.loads(json.dumps(pages))
    p14 = next(page for page in changed if page['page']==14)
    for line in p14['lines']:
        if line['bbox'][0] < 410:
            line['bbox'] = [line['bbox'][0]+80,line['bbox'][1],line['bbox'][2]+80,line['bbox'][3]]
    assert len([claim for claim in extract_dimension_tables(text,changed,'并行科技')
                if claim.table_context['dimension_kind']=='customer']) == 12
    p14['page'] = 16
    assert len([claim for claim in extract_dimension_tables(text,changed,'并行科技')
                if claim.table_context['dimension_kind']=='customer']) == 12


def test_business_multilevel_year_amount_growth_columns_are_separate():
    text, pages = source()
    claims = [claim for claim in extract_dimension_tables(text,pages,'并行科技')
              if claim.table_context['dimension_kind'] in {'business', 'purchase_mode'}]
    assert len(claims) == 18
    assert len([c for c in claims if c.table_context['dimension_kind']=='business']) == 6
    assert len([c for c in claims if c.table_context['dimension_kind']=='purchase_mode']) == 12
    cloud = [claim for claim in claims if claim.table_context['dimension_id']=='超算云服务']
    assert [(claim.period, claim.relation, Decimal(claim.candidate_value)) for claim in cloud] == [
        ('2020Y','value',Decimal('98253700')),('2020Y','yoy',Decimal('.5787')),
        ('2021Y','value',Decimal('172125500')),('2021Y','yoy',Decimal('.7518')),
        ('2022Y','value',Decimal('267149800')),('2022Y','yoy',Decimal('.5521'))]
    assert all(claim.account=='PURCHASE_AMOUNT' for claim in claims if claim.table_context['dimension_id']=='算力资源')
    assert all(claim.account=='REVENUE' for claim in claims if claim.table_context['dimension_id']=='外采算力资源产生的超算云服务')
    assert all(claim.table_context['dimension_kind']=='purchase_mode' for claim in claims
               if claim.table_context['dimension_id'] in {'算力资源','外采算力资源产生的超算云服务'})
    # An unavailable amount is not restored from an adjacent growth cell.
    modified = text.replace('17212.55', '--------')
    altered = json.loads(json.dumps(pages))
    for page in altered:
        for line in page['lines']:
            if '17212.55' in line['text']:
                line['text'] = line['text'].replace('17212.55','--------')
    remaining = extract_dimension_tables(modified,altered,'并行科技')
    assert not any(claim.table_context['dimension_id']=='超算云服务' and claim.period=='2021Y' and claim.relation=='value'
                   for claim in remaining)
    assert any(claim.table_context['dimension_id']=='超算云服务' and claim.period=='2021Y' and claim.relation=='yoy'
               for claim in remaining)


def test_same_table_parent_and_minority_structure_not_same_page_proximity():
    text, pages = source()
    claims = extract_table_mentions(text,pages,[],'并行科技','2099Y')
    statements = [claim for claim in claims if claim.modality=='fact' and claim.table_context['title'] in
                  {'资产负债表(百万元)','利润表(百万元)'}]
    assert len(statements) == 98
    assert all(source_scope_dimensions(claim)['scope_header']=='合并' for claim in statements)
    cashflows = [claim for claim in claims if claim.table_context['title']=='现金流量表(百万元)']
    assert cashflows and all(source_scope_dimensions(claim)['scope_header']=='合并' for claim in cashflows)
    assert all(claim.table_context['scope_proof']['schema_version']=='source-financial-summary-structure.v1' for claim in cashflows)
    assert all(claim.table_context['scope_proof']['professional_signoff_status']=='pending' for claim in cashflows)
    # An adjacent financial table outside this summary cannot borrow its proof.
    other='现金流量表(百万元)\n2022A\n2023A\n经营活动现金流\n10\n20\n'
    outsider=extract_table_mentions(other,[],[],'并行科技','2023Y')[0]
    moved = replace(outsider, table_context={**outsider.table_context,'scope_proof':statements[0].table_context['scope_proof']})
    assert not source_scope_dimensions(moved)['scope_header']
    corrupted = replace(statements[0], table_context={**statements[0].table_context,
                        'scope_proof':{**statements[0].table_context['scope_proof'],'sha256':'0'*64}})
    assert not source_scope_dimensions(corrupted)['scope_header']


def test_broker_residuals_are_never_statutory_same_name_items():
    text, pages = source()
    claims = extract_table_mentions(text,pages,[],'并行科技','2099Y')
    residuals = [claim for claim in claims if claim.modality=='fact' and claim.account.startswith('BROKER_OTHER_')]
    assert len(residuals)==8
    assert all(claim.table_context['table_profile']=='broker_compact' for claim in residuals)
    assert all(claim.table_context['definition_id'].startswith('metric:broker-other-') for claim in residuals)
    ordinary = '合并资产负债表(万元)\n2022A\n2023A\n其他流动负债\n10\n20\n'
    statutory = extract_table_mentions(ordinary, [], [], '甲', '2099Y')
    assert statutory and all(claim.account=='OTHER_CURRENT_LIABILITIES' for claim in statutory)


def test_combined_summary_requires_every_independent_source_condition():
    text,pages=source()
    def broken(replacements, geometry=False):
        altered=json.loads(json.dumps(pages))
        modified=text
        for old,new,only_cash_header in replacements:
            assert len(old)==len(new)
            for page in altered:
                for line in page['lines']:
                    if old not in line['text']:
                        continue
                    if only_cash_header and not (page['page']==42 and 523<line['bbox'][1]<533):
                        continue
                    modified=modified[:line['start']]+modified[line['start']:line['end']].replace(old,new)+modified[line['end']:]
                    line['text']=line['text'].replace(old,new)
        if geometry:
            for page in altered:
                if page['page']==42:
                    page['lines']=[]
        claims=extract_table_mentions(modified,altered,[],'并行科技','2023Y')
        cashflows=[claim for claim in claims if claim.table_context.get('title')=='现金流量表(百万元)']
        assert cashflows and all(not source_scope_dimensions(claim)['scope_header'] for claim in cashflows)
    broken([('财务预测摘要','其他财务事项',False)])
    broken([('数据来源：聚源','披露说明：聚源',False)])
    broken([('少数股东权益','其他股东权益',False)])
    broken([('少数股东损益','其他股东损益',False)])
    broken([('2023A','2021A',True)])
    broken([],geometry=True)
    # Explicit mixed financial scopes take precedence over an otherwise
    # complete source entity. A user cannot overwrite its mother scope.
    claims=extract_table_mentions(text,pages,[],'并行科技','2023Y')
    cash=next(claim for claim in claims if claim.table_context['title']=='现金流量表(百万元)')
    prefix='合并报表；母公司报表；'
    mixed=replace(cash,text=prefix+cash.text,source_numeric_start=cash.source_numeric_start+len(prefix))
    assert not source_scope_dimensions(mixed)['scope_header']


def test_real_figure_42_ticks_need_source_vector_axes_without_baseline_text():
    text, pages = source()
    p31 = next(page for page in pages if page['page']==31)
    proof = chart_layout_exclusions(text,[p31])
    ticks = [item for item in proof.values() if item['basis']['source_line']['text'] in {'0%','20%','40%','60%','80%','100%'}]
    assert len(ticks)==6 and all(item['basis']['vector_axes']['kind']=='source_pdf_perpendicular_axes' for item in ticks)
    without_vectors = {**p31,'vector_lines':[]}
    assert not any(item['basis']['source_line']['text'] in {'0%','20%','40%','60%','80%','100%'}
                   for item in chart_layout_exclusions(text,[without_vectors]).values())
    # Actual finance percentages in prose and captions are never chart ticks.
    assert not any(text[slice(*item['basis']['occurrence_span'])] in {'75%','51.62%','78.53%','81.27%'} for item in proof.values())


def test_all_occurrences_kept_and_scope_classification_has_source_basis():
    text, pages = source()
    ledger, claims = CoverageLedger.build(text,pages,[],'并行科技','2023Y')
    assert len(ledger.entries)==len(list(VALUE_RE.finditer(text)))
    assert all('evaluation_scope' in row and row['classification_basis']['schema_version']=='source-evaluation-scope.v1' for row in ledger.entries)
    cells = [claim for claim in claims if claim.table_context.get('dimension_kind') in {'customer','business','purchase_mode'}]
    assert len(cells)==55
    assert len([claim for claim in cells if claim.table_context.get('layout_source')=='pdf_dimension_table_columns'])==54
    assert all(claim.table_context['evaluation_scope']=='issuer_historical_financial' for claim in cells)


def test_historical_operand_in_forecast_sentence_remains_and_cagr_periods_are_source_bound():
    text = 'IDC预计2027年中国市场规模将从2022年的195.0亿美元增长至300亿美元。'
    ledger, _ = CoverageLedger.build(text,[],[], '甲', '2023Y')
    historic = next(row for row in ledger.entries if row['text'].strip()=='195.0亿')
    assert historic['evaluation_scope']!='forecast' and historic['claim_id']
    cagr = extract_mentions('2018-2023年合并营业收入CAGR为41%。','甲')[0]
    dimensions = source_dimensions_from_text(freeze_source_dimensions(cagr))
    assert (dimensions['account'],dimensions['period'],dimensions['base_period'],dimensions['relation']) == ('REVENUE','2023Y','2018Y','cagr')


def test_explicit_business_dimension_does_not_erase_a_different_issuer_subject():
    selected = extract_mentions('2023年甲公司合并超算云服务营业收入100万元。','甲公司')[0]
    assert selected.table_context['dimension_id']=='超算云服务'
    assert source_dimensions_from_text(selected)['company']=='甲公司'
    another = extract_mentions('2023年乙公司合并超算云服务营业收入100万元。','甲公司')[0]
    assert source_dimensions_from_text(another).get('source_company_conflict')


def test_free_definition_id_and_table_account_hint_are_not_source_proof():
    text = '合并利润表(万元)\n2022A\n2023A\n营业收入\n10\n20\n'
    claim = extract_table_mentions(text, [], [], '甲', '2023Y')[0]
    invented = replace(claim, account='CURRENT_RATIO', table_context={**claim.table_context,
                       'account_hint':'CURRENT_RATIO', 'definition_id':'metric:current-ratio'})
    proof = source_dimensions_from_text(invented)
    assert proof['account']=='REVENUE' and proof['definition_id']==''
    forged = replace(claim, table_context={**claim.table_context,'dimension_kind':'customer',
                                          'dimension_id':'客户E','definition_id':'metric:revenue-share'})
    proof = source_dimensions_from_text(forged)
    assert proof['dimension_kind']=='total' and proof['dimension_id']=='' and proof['definition_id']==''
    original = extract_mentions('2023年合并销售费用率为10%。','甲')[0]
    frozen = freeze_source_dimensions(original)
    changed = replace(frozen, table_context={**frozen.table_context,'definition_id':'metric:admin-expense-ratio'})
    import pytest
    with pytest.raises(ValueError, match='冻结金融维度'):
        source_dimensions_from_text(changed)


def test_future_cagr_and_explicit_price_assumptions_do_not_hide_historical_operands():
    text = '2021年市场规模为196.6亿元，并预计在2025年达到466亿元，2021年-2025年CAGR为24.1%。'
    ledger, _ = CoverageLedger.build(text, [], [], '甲','2023Y')
    by_text = {row['text'].strip():row for row in ledger.entries}
    assert by_text['196.6亿元']['evaluation_scope']=='industry_historical'
    assert by_text['466亿元']['evaluation_scope']=='forecast'
    assert by_text['24.1%']['evaluation_scope']=='forecast'
    assumption='按照每张GPU每小时定价4美元计算，4年共可收入25亿美元。2023年公司合并营业收入20万元。'
    ledger, _ = CoverageLedger.build(assumption, [], [], '甲','2023Y')
    by_text = {row['text'].strip():row for row in ledger.entries}
    assert by_text['25亿']['evaluation_scope']=='hypothesis'
    assert by_text['20万元']['evaluation_scope']=='issuer_historical_financial'


def test_operating_chart_data_is_preserved_with_source_unit_caption():
    text, pages = source()
    ledger, _ = CoverageLedger.build(text,pages,[],'并行科技','2023Y')
    pg = next(page for page in pages if page['page']==30)
    labels = [row for row in ledger.entries if pg['start']<=row['start']<pg['end'] and row['text'].strip() in
              {'87493','279512','475847','264223','42591','37158','48893','30174'}]
    assert len(labels)==8
    assert all(row['status']=='pending' and row['evaluation_scope']=='issuer_operating' and row['claim_id'] for row in labels)
    assert all(row['classification_basis']['rule']=='explicit_chart_measure_caption' for row in labels)


def test_an_industry_subject_cannot_move_a_later_explicit_issuer_financial_amount():
    text='全球AI服务器市场同比增长39.1%，公司2023年合并营业收入为100万元。'
    ledger,_=CoverageLedger.build(text,[],[],'甲','2023Y')
    company=next(row for row in ledger.entries if row['text'].strip()=='100万元')
    assert company['evaluation_scope']=='issuer_historical_financial'


def test_undated_explicit_forecast_caption_retains_ledger_without_inventing_period():
    text = '图32：AIGC成核心驱动力，预计全球CAGR超90%。'
    ledger, claims = CoverageLedger.build(text, [], [], '甲公司', '2023Y')
    row = next(entry for entry in ledger.entries if entry['text'].strip() == '90%')
    assert row['status'] == 'out_of_scope' and row['evaluation_scope'] == 'forecast'
    assert row['reason'] == 'forecast_prose_without_period' and row['claim_id'] is None
    basis = row['modality_basis']
    assert basis['declared_periods'] == [] and basis['period_status'] == 'source_unspecified'
    assert basis['predicate']['text'] == '预计'
    assert basis['source_span']['text'] == '预计全球CAGR超90%'
    assert not claims


def test_undated_forecast_does_not_swallow_adjacent_historical_issuer_or_customer_figures():
    text = ('2023年公司合并营业收入100万元，预计全球CAGR超90%，'
            '公司2023年合并超算云服务收入80万元。'
            '预计营业收入达到200万元，2022年公司合并营业收入90万元。'
            'IDC预计市场规模增长30%，公司2023年前五大客户收入占总营收20%。')
    ledger, claims = CoverageLedger.build(text, [], [], '甲公司', '2023Y')
    by_text = {entry['text'].strip(): entry for entry in ledger.entries}
    for numeric in ('100万元', '80万元', '90万元', '20%'):
        assert by_text[numeric]['evaluation_scope'] == 'issuer_historical_financial'
        assert by_text[numeric]['claim_id'] is not None
    for numeric in ('90%', '200万元', '30%'):
        assert by_text[numeric]['evaluation_scope'] == 'forecast'
        assert by_text[numeric]['status'] == 'out_of_scope'
    assert len({(entry['start'], entry['end']) for entry in ledger.entries}) == len(ledger.entries)
    assert len(claims) == 4


def test_negated_or_metadata_forecast_words_do_not_reclassify_historical_finance():
    for source in ('公司未预计营业收入100万元。', '公司预测模型历史输入营业收入100万元。',
                   '公司预计负债100万元。'):
        ledger, _ = CoverageLedger.build(source, [], [], '甲公司', '2023Y')
        row = next(entry for entry in ledger.entries if entry['text'].strip() == '100万元')
        assert row['evaluation_scope'] != 'forecast'


def test_wrapped_industry_ratio_uses_full_source_subject_and_keeps_historical_operand():
    text = ('预计到2026年，全球人工智能服务器市场将达到347亿美元，将占整个服务器市场的21.7%，较\n'
            '2022年的16.6%占比持续提升，且中国市场预计将达123.37亿美元。\n'
            '据IDC数据，2021年国内专用于人工\n智能的公有云服务市场规模达到44.1亿元人民币，'
            '占整体人工智能软件市场的13.4%。')
    ledger, claims = CoverageLedger.build(text, [], [], '甲公司', '2023Y')
    for numeric in ('16.6%', '13.4%'):
        row = next(entry for entry in ledger.entries if entry['text'].strip() == numeric)
        assert row['evaluation_scope'] == 'industry_historical' and row['claim_id']
        assert row['status'] == 'pending'
        proof = row['classification_basis']['industry_lineage']
        for key in ('subject', 'continuation'):
            excerpt = proof[key]
            assert text[excerpt['start']:excerpt['end']] == excerpt['text']
        assert proof['occurrence_span'] == [row['start'], row['end']]
    assert next(entry for entry in ledger.entries if entry['text'].strip() == '347亿')['evaluation_scope'] == 'forecast'
    assert next(claim for claim in claims if claim.numeric_text == '16.6%').period == '2022Y'


def test_industry_ratio_lineage_stops_at_issuer_customer_business_and_sentence_boundaries():
    samples = [
        ('全球AI市场规模10亿元，公司2023年合并毛利率26%。', '26%'),
        ('全球AI市场规模10亿元，公司2023年合并超算云服务收入占总营收20%。', '20%'),
        ('全球AI市场规模10亿元，公司2023年前五大客户营收占总营收20%。', '20%'),
        ('全球AI市场规模10亿元。2023年占比20%。', '20%'),
    ]
    for text, numeric in samples:
        ledger, _ = CoverageLedger.build(text, [], [], '甲公司', '2023Y')
        target = next(entry for entry in ledger.entries if entry['text'].strip() == numeric)
        assert target['evaluation_scope'] == 'issuer_historical_financial'
        assert target['claim_id']
