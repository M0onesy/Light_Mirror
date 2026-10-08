"""Original exposed geometry and adjacent blank/numeric counterexamples."""
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path

import pytest

from mingjing.parser import _extract_table_facts

SOURCE=json.loads((Path(__file__).parent/'fixtures/statement_note_column_15.json').read_text('utf-8'))

def extract(pages):
    return _extract_table_facts(pages,path=Path('2017年年度报告（更正后）.pdf'),company=SOURCE['company'],
        effective_period='2017Y',doc_sha=SOURCE['doc_sha256'],disclosure_date='2020-06-08',disclosure_version='更正披露')

def test_actual_arabic_notes_never_reappear_as_values_and_blank_comparatives_stay_absent():
    facts=extract(SOURCE['pages'])
    actual={(f.account,f.scope,f.period):f.value for f in facts}
    assert actual['INVESTMENT_INCOME','合并','2017Y']==Decimal('59926.19')
    assert actual['INVESTMENT_INCOME','母公司','2017Y']==Decimal('68265.71')
    assert actual['OTHER_INCOME','合并','2017Y']==Decimal('4207262.84')
    assert not any(f.account in {'INVESTMENT_INCOME','OTHER_INCOME'} and f.period=='2016Y' for f in facts)
    assert not any(f.account in {'INVESTMENT_INCOME','OTHER_INCOME'} and f.value in {Decimal('5'),Decimal('33'),Decimal('34')} for f in facts)
    assert actual['REVENUE','合并','2016Y']==Decimal('30474933.97')

@pytest.mark.parametrize('amount',['33','34','0','-33.00'])
def test_legitimate_amount_column_values_equal_to_note_indexes_are_kept(amount):
    pages=deepcopy(SOURCE['pages'])
    for page in pages:
        if page['page']!=47:continue
        for block in page['blocks']:
            if '59,926.19' in block['text']:block['text']=block['text'].replace('59,926.19',amount)
        for line in page['lines']:
            if line['text'].strip()=='59,926.19':line['text']=amount
        page['text']=page['text'].replace('59,926.19',amount)
    facts=extract(pages)
    selected=[f for f in facts if f.account=='INVESTMENT_INCOME' and f.scope=='合并']
    assert [(f.period,f.value) for f in selected]==[('2017Y',Decimal(amount))]

def _simple_page(current,previous,note='六、33'):
    rows=[('合并利润表',[50,20,160,35]),('单位：元',[450,40,520,55]),
          ('项目',[50,70,80,85]),('附注',[300,70,330,85]),
          ('本期金额',[390,70,450,85]),('上期金额',[490,70,550,85]),
          ('投资收益',[50,100,130,115]),(note,[300,100,340,115])]
    if current is not None:rows.append((current,[390,100,450,115]))
    if previous is not None:rows.append((previous,[490,100,550,115]))
    header='\n'.join(t for t,b in rows[2:6])+'\n'
    value='\n'.join(t for t,b in rows[6:])+'\n'
    return [{'page':1,'text':'\n'.join(t for t,b in rows),'blocks':[
        {'text':rows[0][0],'bbox':rows[0][1]},{'text':rows[1][0],'bbox':rows[1][1]},
        {'text':header,'bbox':[50,70,550,85]},{'text':value,'bbox':[50,100,550,115]}],
        'lines':[{'text':t,'bbox':b} for t,b in rows]}]

@pytest.mark.parametrize('current,previous,expected',[
    ('34',None,[('2017Y','34')]),(None,'33',[('2016Y','33')]),
    ('—','34',[('2016Y','34')]),('33','—',[('2017Y','33')]),
    ('—','—',[]),(None,None,[]),('0','34',[('2017Y','0'),('2016Y','34')]),
])
def test_missing_columns_are_independent_without_zero_invention(current,previous,expected):
    facts=extract(_simple_page(current,previous))
    assert [(f.period,str(f.value)) for f in facts if f.account=='INVESTMENT_INCOME']==expected
