"""Original p17/p18 and p202/p203 product-grid regressions and neighbors."""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path

import pytest

from mingjing.evidence import make_evidence_id
from mingjing.source_product_financial import (DEFINITION, SHARE_DEFINITION,
    caption_series, extract_source_facts, narrative_series, narrative_subject)

SOURCES=json.loads((Path(__file__).parent/'fixtures/product_financial_source_16.json').read_text(encoding='utf8'))
ANNUAL,PROSPECTUS=SOURCES
NARRATIVE=('公司主营业务按产品或服务划分可主要划分为四类：超算云服务、超算云系统\n'
    '集成、超算软件与技术服务、超算会议及其他服务。从产品收入结构来看，2022 年\n'
    '超算云服务为主要创收模块，营收2.67 亿元， 2023 年进一步增长55%至4.14 亿元。')
CAPTION='图10：2023 年超算云服务增长55%至4.14 亿元（单位：万元）'


def facts(source):
    return extract_source_facts(source['pages'],path=source['filename'],company=source['company'],
        doc_sha=source['doc_sha256'],disclosure_date=source['publish_date'],disclosure_version='原披露')


def replace_text(source,old,new):
    """Maintain exact page offsets after a source-text edit, not stale fixtures."""
    for page in source['pages']:
        page['text']=page['text'].replace(old,new)
        for collection in ('lines','blocks'):
            for line in page[collection]:line['text']=line['text'].replace(old,new)
        cursor=0
        for line in page['lines']:
            line['start']=page['text'].find(line['text'],cursor)
            line['end']=line['start']+len(line['text'])
            cursor=line['end']


def test_annual_exact_product_has_five_distinct_source_measures_not_accounting_scope():
    rows=facts(ANNUAL)
    assert len(rows)==32
    cloud=[f for f in rows if f.dimension_id=='超算云服务']
    assert {(f.account,f.source_relation,f.value) for f in cloud}=={
        ('REVENUE','value',Decimal('414115382.02')),('COST','value',Decimal('302149034.86')),
        ('GROSS_MARGIN','value',Decimal('.2704')),('REVENUE','yoy',Decimal('.5501')),
        ('COST','yoy',Decimal('.5571'))}
    assert all(f.period=='2023Y' and f.scope=='未知' and not f.source_scope_evidence
               and f.definition_id==DEFINITION for f in rows)
    assert len({f.source_table_id for f in rows})==1
    assert not any(f.dimension_id in {'超算云','超算云业务'} for f in rows)


def test_prospectus_cross_page_parent_and_leaf_headers_keep_exact_2022_product():
    rows=facts(PROSPECTUS)
    assert len(rows)==32 and len({f.fact_id for f in rows})==32
    cloud=[f for f in rows if f.dimension_id=='超算云服务']
    revenue={f.period:f.value for f in cloud if f.account=='REVENUE'}
    assert revenue=={'2023H1':Decimal('157701200'),'2022Y':Decimal('267149800'),
                     '2021Y':Decimal('172125500'),'2020Y':Decimal('98253700')}
    shares={f.period:f.value for f in cloud if f.account=='REVENUE_SHARE'}
    assert shares['2022Y']==Decimal('.8541')
    assert all(f.definition_id==SHARE_DEFINITION for f in cloud if f.account=='REVENUE_SHARE')
    assert all(f.scope=='未知' and f.source_relation=='value' for f in rows)
    assert len({f.source_table_id for f in rows})==1


@pytest.mark.parametrize('source',SOURCES)
def test_all_evidence_ids_are_original_pdf_blocks_not_synthesized_lines(source):
    known={make_evidence_id(source['doc_sha256'],p['page'],b['text'].strip(),b['bbox'])
           for p in source['pages'] for b in p['blocks'] if b.get('bbox')}
    for f in facts(source):
        assert f.evidence and set(f.evidence)<=known
        assert f.source_table_evidence and set(f.source_table_evidence)<=known
        assert f.evidence[0] not in f.source_table_evidence


@pytest.mark.parametrize('old,new,account,relation',[
    ('414,115,382.02','—','REVENUE','value'),('302,149,034.86','—','COST','value'),
    ('27.04%','27.04','GROSS_MARGIN','value'),('55.01%','—','REVENUE','yoy')])
def test_one_absent_or_dimensionally_wrong_cell_keeps_other_product_cells(old,new,account,relation):
    source=deepcopy(ANNUAL)
    replace_text(source,old,new)
    rows=facts(source)
    assert len(rows)==31
    assert not any(f.dimension_id=='超算云服务' and f.account==account and f.source_relation==relation for f in rows)
    assert sum(f.dimension_id=='超算云服务' for f in rows)==4


def test_missing_historical_product_cell_does_not_drop_neighbor_period_or_share():
    source=deepcopy(PROSPECTUS)
    replace_text(source,'26,714.98','—')
    rows=facts(source)
    assert len(rows)==31
    assert not any(f.dimension_id=='超算云服务' and f.period=='2022Y' and f.account=='REVENUE' for f in rows)
    assert any(f.dimension_id=='超算云服务' and f.period=='2022Y' and f.account=='REVENUE_SHARE' for f in rows)


@pytest.mark.parametrize('old,new',[
    ('按产品分类分析：','按地区分类分析：'),('单位：元','单位：美元'),
    ('公司年度报告','附属公司报告'),('2023 年年度报告','2021 年年度报告')])
def test_annual_neighbor_headings_cannot_donate_product_unit_or_year(old,new):
    source=deepcopy(ANNUAL)
    replace_text(source,old,new)
    assert facts(source)==[]


@pytest.mark.parametrize('old,new',[
    ('报告期内，公司主营业务收入按产品或服务类型划分情况如下：','报告期内，公司采购金额按产品或服务类型划分情况如下：'),
    ('（万元）','（万美元）'),('2022 年度','2022 年度预计')])
def test_prospectus_wrong_measure_currency_or_prediction_rejects_grid(old,new):
    source=deepcopy(PROSPECTUS)
    replace_text(source,old,new)
    assert facts(source)==[]


@pytest.mark.parametrize('source',SOURCES)
def test_wrong_issuer_and_missing_original_source_blocks_are_not_admitted(source):
    alternate=deepcopy(source)
    alternate['company']='测试股份有限公司'
    assert facts(alternate)==[]
    alternate=deepcopy(source)
    for p in alternate['pages']:p['blocks']=[]
    assert facts(alternate)==[]


def test_cross_page_table_requires_real_adjacent_physical_pages():
    source=deepcopy(PROSPECTUS)
    source['pages'][-1]['page']=204
    assert facts(source)==[]


def test_original_amount_perturbation_does_not_change_table_or_label_selection():
    before=facts(ANNUAL)
    source=deepcopy(ANNUAL)
    replace_text(source,'414,115,382.02','111,111,111.11')
    after=facts(source)
    identities=lambda rows:{(f.source_table_id,f.dimension_id,f.period,f.account,f.source_relation) for f in rows}
    assert identities(before)==identities(after)
    assert next(f for f in after if f.dimension_id=='超算云服务' and f.account=='REVENUE' and f.source_relation=='value').value==Decimal('111111111.11')


def test_negative_percentage_remains_one_registered_product_cell():
    rows=facts(ANNUAL)
    assert any(f.dimension_id=='超算软件与技术服务' and f.account=='REVENUE'
               and f.source_relation=='yoy' and f.value==Decimal('-.0108') for f in rows)
    assert any(f.dimension_id=='超算会议及其他服务' and f.account=='GROSS_MARGIN'
               and f.value==Decimal('-.1427') for f in rows)


def test_local_line_offset_reconstruction_matches_full_source_extraction():
    source=deepcopy(ANNUAL)
    for p in source['pages']:
        for line in p['lines']:
            line.pop('start',None);line.pop('end',None)
    assert [f.to_dict() for f in facts(source)]==[f.to_dict() for f in facts(ANNUAL)]


def test_complete_original_narrative_and_caption_keep_all_five_numeric_occurrences():
    assert narrative_subject(NARRATIVE,'并行科技')
    rows=[cell for _,cells in narrative_series(NARRATIVE) for cell in cells]
    assert [(r['period'],r['relation'],r['unit']) for r in rows]==[
        ('2022Y','value','亿元'),('2023Y','yoy','%'),('2023Y','value','亿元')]
    for cell in rows:
        assert NARRATIVE[cell['start']:cell['end']]
        assert cell['dimension_id']=='超算云服务'
    caption=[cell for _,cells in caption_series(CAPTION,'并行科技',narrative=NARRATIVE) for cell in cells]
    assert [CAPTION[c['start']:c['end']] for c in caption]==['55%','4.14 亿元']
    changed=CAPTION.replace('55%','99%').replace('4.14','9.99')
    assert len(list(caption_series(changed,'并行科技',narrative=NARRATIVE)))==1


@pytest.mark.parametrize('change',[
    lambda s:s.replace('超算云服务','超算云'),lambda s:s.replace('公司主营业务','另一科技主营业务'),
    lambda s:s.replace('从产品收入结构来看','子公司从产品收入结构来看'),
    lambda s:'预计'+s,lambda s:s.replace('2023 年进一步','2025 年进一步')])
def test_narrative_short_alias_subject_prediction_or_wrong_period_not_guessed(change):
    narrative=change(NARRATIVE)
    assert not narrative_subject(narrative,'并行科技')
    assert list(caption_series(CAPTION,'并行科技',narrative=narrative))==[]


def test_caption_omitted_revenue_measure_requires_complete_adjacent_narrative():
    short='2023 年超算云服务收入4.14亿元。'
    assert list(caption_series(CAPTION,'并行科技',narrative=short))==[]
    assert list(caption_series(CAPTION.replace('超算云服务','通用超算云'),'并行科技',narrative=NARRATIVE))==[]
