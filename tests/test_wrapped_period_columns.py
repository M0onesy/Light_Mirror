"""Exposed three-column table and changes that break its header proof."""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import pytest
from mingjing.parser import _extract_table_facts

SOURCE=json.loads((Path(__file__).parent/'fixtures/wrapped_period_columns_15.json').read_text('utf-8'))

def extract(pages):
    return _extract_table_facts(pages,path=Path('2023招股说明书.pdf'),company=SOURCE['company'],
        effective_period='',doc_sha=SOURCE['doc_sha256'],disclosure_date=SOURCE['publish_date'],disclosure_version='原披露')

def test_pdf_reading_order_does_not_rotate_actual_wrapped_year_columns_on_next_page():
    actual={(f.period,f.scope):f.value for f in extract(SOURCE['pages']) if f.account=='ASSETS'}
    assert actual=={('2022Y','合并'):Decimal('744637748.34'),('2021Y','合并'):Decimal('732568513.25'),
                   ('2020Y','合并'):Decimal('343847977.09')}

def test_actual_sparse_continuation_rows_keep_columns_without_filling_missing_amounts():
    facts=extract(SOURCE['pages'])
    lease=[(f.period,str(f.value)) for f in facts if f.account=='LEASE_LIABILITIES' and f.scope=='合并']
    deferred=[(f.period,str(f.value)) for f in facts if f.account=='DEFERRED_INCOME' and f.scope=='合并']
    assert lease==[('2022Y','43488037.53'),('2021Y','69104564.72')]
    assert deferred==[('2021Y','6266.62'),('2020Y','1159595.43')]

def test_equal_values_do_not_mask_period_order_error():
    facts=[f for f in extract(SOURCE['pages']) if f.account=='GOODWILL' and f.scope=='合并']
    assert [f.period for f in facts]==['2022Y','2021Y','2020Y']
    assert {str(f.value) for f in facts}=={'5052333.86'}

@pytest.mark.parametrize('kind',['remove','move','duplicate'])
def test_unproven_date_continuation_does_not_keep_rotated_header_state(kind):
    pages=deepcopy(SOURCE['pages']);first=pages[0]
    line=next(l for l in first['lines'] if l['text'].strip()=='日')
    if kind=='remove':
        first['lines'].remove(line)
        for block in first['blocks']:
            if '2022 年12 月31' in block['text']:block['text']=block['text'].replace('\n日 ','')
    elif kind=='move':line['bbox']=[x+80 if i in (0,2) else x for i,x in enumerate(line['bbox'])]
    else:first['lines'].append({**line,'bbox':[x+2 if i in (0,2) else x for i,x in enumerate(line['bbox'])]})
    assert not any(f.account=='ASSETS' for f in extract(pages))

def test_amounts_and_years_follow_physical_order_even_when_input_blocks_are_reversed():
    pages=deepcopy(SOURCE['pages'])
    # Change only the physical header order, preserving the financial amount
    # row's geometry. No amount is used to choose a year.
    for line in pages[0]['lines']:
        if line['text'].startswith('2022 '):line['text']=line['text'].replace('2022','2020')
        elif line['text'].startswith('2020 '):line['text']=line['text'].replace('2020','2022')
    for block in pages[0]['blocks']:
        block['text']=block['text'].replace('2022','TEMP').replace('2020','2022').replace('TEMP','2020')
    actual={f.period:f.value for f in extract(pages) if f.account=='ASSETS'}
    assert actual=={'2020Y':Decimal('744637748.34'),'2021Y':Decimal('732568513.25'),'2022Y':Decimal('343847977.09')}
