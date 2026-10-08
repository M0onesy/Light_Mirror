import json
import pytest
from jsonschema import ValidationError
from mingjing.financial_tools import FinancialDispatcher,ToolDenied,tool_definitions

def dispatcher(gaps=None):
    reports=[{'report_id':'r1','company':'甲公司','sha256':'a'*64,'publish_date':'2024-04-15'},
             {'report_id':'r2','company':'甲公司','sha256':'b'*64,'publish_date':'2024-04-23'}]
    default={'id':'sg-'+'a'*24,'doc_sha256':'a'*64,'company':'甲公司','period':'2020Y',
             'dimension_kind':'customer','dimension_id':'客户甲','account':'REVENUE','currency':'CNY',
             'scope':'未知','gap_kind':'scope_unproven','reason':'收入123.45万元未证明口径',
             'table_label':'客户表','evidence':['a'*12+':p1:cell'],'report_id':'r1','public_at_cutoff':True,
             'raw_value':'9999999','source_context':{'text':'绝不输出8888888元'},'needed_method':'报表口径的来源声明'}
    d=FinancialDispatcher('run','lock',[],[],[],reports,'2024-04-22',task_company='甲公司',source_gaps=[default] if gaps is None else gaps)
    d.register_session('p','planner')
    d.register_session('q','question')
    return d,default

def call(d,name='mj_list_source_gaps',params=None,role='planner',session='p',cid='call'):
    return d.dispatch({'run_id':'run','resource_lock':'lock','session_id':session,'role':role,
                        'call_id':cid,'tool_name':name,'params':params or {}})

def test_source_gap_tool_has_no_raw_numeric_values_and_is_not_a_fact():
    d,_=dispatcher()
    result=call(d)
    row=result['source_gaps'][0]
    assert row['account']=='REVENUE' and row['scope']=='未知' and row['eligible_as_fact'] is False
    text=json.dumps(result,ensure_ascii=False)
    assert all(value not in text for value in ['9999999','8888888','123.45','source_context','raw_value'])
    assert d.store.to_list()==[] and d.results==[]
    assert call(d,'mj_list_reports',cid='reports')==d.reports

def test_source_gap_visibility_revalidates_cutoff_even_if_host_flag_is_true():
    _,gap=dispatcher()
    future={**gap,'id':'sg-'+'b'*24,'doc_sha256':'b'*64,'report_id':'r2'}
    d,_=dispatcher([gap,future])
    result=call(d,params={'query':'客户甲','limit':1})
    assert result['returned']==1 and result['unavailable_at_cutoff']==1 and result['source_gaps'][0]['report_id']=='r1'
    assert call(d,params={'query':'9999999'},cid='raw-search')['returned']==0
    with pytest.raises(ToolDenied):call(d,params={'report_id':'outside'},cid='outside')

def test_source_gap_cannot_escape_company_hash_or_schema():
    _,gap=dispatcher()
    with pytest.raises(ValueError):dispatcher([{**gap,'doc_sha256':'f'*64}])
    d,_=dispatcher([{**gap,'company':'乙公司'}])
    assert call(d)['returned']==0
    with pytest.raises(ValidationError):call(d,params={'value':999},cid='hostile')
    assert any(x['name']=='mj_list_source_gaps' for x in tool_definitions('planner'))
    assert not any(x['name']=='mj_list_source_gaps' for x in tool_definitions('reporter'))
