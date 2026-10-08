"""Exposed customer/procurement geometry; no candidate-value matching."""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path

import pytest

from mingjing.evidence import make_evidence_id
from mingjing.source_gap_catalog import extract_source_gaps, validate_source_gap

SOURCE=json.loads((Path(__file__).parent/'fixtures/source_gap_tables_14.json').read_text('utf-8'))


def extract(source):
    return extract_source_gaps(source['pages'],path=source['filename'],company=source['company'],
        doc_sha=source['doc_sha256'],disclosure_date=source['publish_date'])


def test_source_customer_catalog_keeps_all_48_cells_and_every_original_occurrence():
    cells=extract(SOURCE[0])
    assert len(cells)==48 and len({cell['id'] for cell in cells})==48
    original={make_evidence_id(SOURCE[0]['doc_sha256'],page['page'],block['text'].strip(),block['bbox'])
              for page in SOURCE[0]['pages'] for block in page['blocks']}
    for cell in cells:
        assert set(cell['evidence'])<=original
        assert cell['scope']=='未知' and cell['eligible_as_fact'] is False
        assert cell['gap_kind']=='source-scope-pending' and 'issuer_scope' in cell['missing_dimensions']
    assert {cell['period'] for cell in cells}=={'2020Y','2021Y','2022Y','2023H1'}
    cross_page=next(cell for cell in cells if cell['dimension_id']=='北京大学' and cell['account']=='REVENUE')
    assert cross_page['raw_value']=='2761100.00'
    assert {int(item.split(':p')[1].split(':')[0]) for item in cross_page['evidence']}=={205,206}


def test_customer_aggregation_does_not_become_issuer_consolidated_scope_or_global_alias():
    cells=extract(SOURCE[0])
    anonymous=[cell for cell in cells if cell['dimension_id'] in {'客户E','客户H'}]
    assert anonymous and all(cell['dimensions']['definition_id'].startswith('customer-source:9f351a9852de:') for cell in anonymous)
    assert all(cell['scope']=='未知' for cell in anonymous)
    assert all(cell['source_context']['method']['issuer_scope']=='未声明' for cell in anonymous)
    assert all('匿名客户不跨文件映射' in cell['source_context']['method']['customer_aggregation_scope'] for cell in anonymous)


def test_procurement_grid_keeps_all_18_cells_negative_yoy_and_source_definition():
    cells=extract(SOURCE[1])
    assert len(cells)==18
    assert {cell['period'] for cell in cells}=={'2020Y','2021Y','2022Y'}
    purchase=next(cell for cell in cells if cell['account']=='PROCUREMENT_AMOUNT' and cell['period']=='2020Y'
                  and cell['dimensions']['relation']=='yoy')
    assert Decimal(purchase['raw_value'])==Decimal('-.1557')
    assert '非营业成本' in purchase['source_context']['method']['measure']
    assert not any(cell['account']=='COST' for cell in cells)


def test_one_missing_customer_amount_keeps_its_independent_percentage():
    source=deepcopy(SOURCE[0])
    for line in source['pages'][0]['lines']:
        if line['text'].strip()=='708.40':
            line['text']='—'
    cells=extract(source)
    chosen=[cell for cell in cells if cell['dimension_id']=='客户H' and cell['period']=='2023H1']
    assert len(chosen)==1 and chosen[0]['account']=='REVENUE_SHARE'
    assert chosen[0]['raw_value']=='0.0350'


@pytest.mark.parametrize('title',['可比公司甲前五大客户销售情况','4、前五大客户销售情况预测'])
def test_other_subject_and_forecast_title_cannot_activate_source_catalog(title):
    source=deepcopy(SOURCE[0])
    for page in source['pages']:
        for line in page['lines']:
            if line['text'].strip()=='4、前五大客户销售情况':
                line['text']=title
    assert extract(source)==[]


@pytest.mark.parametrize('changes',[{'scope':'合并'},{'eligible_as_fact':True},{'doc_sha256':'123'},
    {'period':'2023'},{'currency':'UNKNOWN'},{'evidence':['other:p1:0,0,0,0:hash']}])
def test_catalog_cannot_be_promoted_to_fact_or_detached_from_original_source(changes):
    cell=dict(extract(SOURCE[0])[0],**changes)
    with pytest.raises(ValueError):
        validate_source_gap(cell)
