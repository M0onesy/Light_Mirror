"""Derived values require real, same-session rule reads before any judgment."""
from decimal import Decimal

import pytest

from mingjing.financial_tools import FinancialDispatcher, ToolDenied
from mingjing.judge import Judge
from mingjing.mentions import extract_mentions
from mingjing.models import Fact


def fact(fid, account, period, value, **kwargs):
    values = dict(source_doc='annual', publish_date='2025-04-01', evidence=('e-'+fid,),
                  label=account, scope='合并', decimals=2)
    values.update(kwargs)
    return Fact(fid, '甲公司', account, period, value=Decimal(value), **values)


def call(d, name, params, call_id, session='coordinator'):
    return d.dispatch({'session_id':session, 'role':'coordinator', 'run_id':'run', 'resource_lock':'lock',
                       'call_id':call_id, 'tool_name':name, 'params':params})


def fixture(direct=False, problem=None):
    claims = extract_mentions('甲公司2024年第二季度合并口径营业收入30元。'
                              '甲公司2024年合并口径基本每股收益1.60元。', '甲公司')
    facts = [fact('h1','REVENUE','2024H1','120'), fact('q1','REVENUE','2024Q1','90'),
             fact('ordinary','NP_PARENT_ORDINARY','2024Y','80', label='归属于母公司普通股股东的净利润'),
             fact('weighted','WEIGHTED_SHARES','2024Y','50', display_unit='股', label='发行在外普通股的加权平均数')]
    if direct:
        facts.extend([fact('quarter','REVENUE','2024Q2','30'), fact('eps','EPS','2024Y','1.6', display_unit='元')])
    if problem:
        for claim in claims:
            if problem=='unknown_date':
                facts.append(fact('unknown-'+claim.account,claim.account,claim.period,'999',publish_date=''))
            elif problem=='low_confidence':
                facts.append(fact('low-'+claim.account,claim.account,claim.period,'999',parse_conf='0.5'))
            else:
                facts.extend([fact('bad-'+claim.account,claim.account,claim.period,'999'),
                              fact('conflict-'+claim.account,claim.account,claim.period,'888')])
    d = FinancialDispatcher('run','lock',claims,facts,[],[],'2025-04-30')
    for role in ('planner','verifier','coordinator'):
        d.register_session(role,role)
    for role in ('planner','verifier'):
        for claim in claims:
            d.dispatch({'session_id':role,'role':role,'run_id':'run','resource_lock':'lock',
                        'call_id':role+'-'+claim.claim_id,'tool_name':'mj_submit_binding',
                        'params':{'mention_id':claim.claim_id,'binding':{
                            'company':claim.company,'account':claim.account,'period':claim.period,
                            'scope':'合并','relation':'value','base_period':''}}})
    assert all(claim.binding_status=='agreed' for claim in d.freeze_bindings())
    return d,claims


@pytest.mark.parametrize('claim_index,rule', [(0,'FIN-single-quarter'),(1,'FIN-EPS')])
@pytest.mark.parametrize('read_facts,read_rule', [(False,False),(True,False),(False,True)])
def test_derived_judgment_requires_its_actual_rule_and_fact_reads(claim_index,rule,read_facts,read_rule):
    d,claims = fixture()
    mid = claims[claim_index].claim_id
    if read_facts:
        query = call(d,'mj_get_fact',{'binding_id':mid},'facts')
        assert query['fact'] is None and query['query_outcome']['status']=='missing'
        assert query['required_rule_ids']==[rule]
        assert not {'candidate_value','observed','expected','formula'} & set(query['query_dimensions'])
    call(d,'mj_get_rule',{'rule_id':rule if read_rule else 'E1-value'},'rule')
    with pytest.raises(ToolDenied,match=rule):
        call(d,'mj_verify',{'binding_id':mid},'rejected')
    if not read_facts:
        call(d,'mj_get_fact',{'binding_id':mid},'repair-facts')
    if not read_rule:
        call(d,'mj_get_rule',{'rule_id':rule},'repair-rule')
    result = call(d,'mj_verify',{'binding_id':mid},'repaired')
    assert result['status']=='verified' and result['rule_ids']==[rule]


def test_batch_with_one_missing_derived_rule_executes_no_partial_judgments(monkeypatch):
    d,claims = fixture()
    ids = [row.claim_id for row in claims]
    call(d,'mj_get_facts',{'binding_ids':ids},'facts')
    call(d,'mj_get_rule',{'rule_id':'FIN-single-quarter'},'quarter-rule')
    calls = []
    original = Judge.verify
    def track(self,claim):
        calls.append(claim.claim_id)
        return original(self,claim)
    monkeypatch.setattr(Judge,'verify',track)
    with pytest.raises(ToolDenied,match='FIN-EPS'):
        call(d,'mj_verify_bindings',{'binding_ids':ids},'rejected')
    assert calls==[]
    call(d,'mj_get_rule',{'rule_id':'FIN-EPS'},'eps-rule')
    result = call(d,'mj_verify_bindings',{'binding_ids':ids},'repaired')
    assert calls==ids and all(row['status']=='verified' for row in result['items'])


@pytest.mark.parametrize('claim_index,rule', [(0,'FIN-single-quarter'),(1,'FIN-EPS')])
def test_second_session_cannot_inherit_other_sessions_formula_authorization(claim_index,rule):
    d,claims = fixture()
    mid = claims[claim_index].claim_id
    call(d,'mj_get_fact',{'binding_id':mid},'facts')
    call(d,'mj_get_rule',{'rule_id':rule},'rule')
    d.register_session('other','coordinator')
    with pytest.raises(ToolDenied,match=rule):
        call(d,'mj_verify',{'binding_id':mid},'other-denied',session='other')
    call(d,'mj_get_fact',{'binding_id':mid},'other-facts',session='other')
    call(d,'mj_get_rule',{'rule_id':rule},'other-rule',session='other')
    assert call(d,'mj_verify',{'binding_id':mid},'other-success',session='other')['status']=='verified'


@pytest.mark.parametrize('problem', ['conflict','unknown_date','low_confidence'])
def test_direct_X4_never_advertises_or_executes_a_derivation(problem):
    d,claims = fixture(problem=problem)
    for claim in claims:
        query = call(d,'mj_get_fact',{'binding_id':claim.claim_id},'facts-'+claim.claim_id)
        assert query['required_rule_ids']==['E1-value'] and query['query_outcome']['status']==problem
        call(d,'mj_get_rule',{'rule_id':'E1-value'},'rule-'+claim.claim_id)
        result = call(d,'mj_verify',{'binding_id':claim.claim_id},'verify-'+claim.claim_id)
        assert result['status']=='abstained' and result['abstention_code']=='X4'
        # Keep the existing future-task reparsing route, without deriving a
        # value or changing this frozen task's conflicting raw disclosure.
        assert claim.claim_id in d.sessions['coordinator']['facts_missing']


def test_direct_disclosures_keep_existing_E1_workflow():
    d,claims = fixture(direct=True)
    for claim in claims:
        query = call(d,'mj_get_fact',{'binding_id':claim.claim_id},'facts-'+claim.claim_id)
        assert query['fact'] and query['required_rule_ids']==['E1-value']
        call(d,'mj_get_rule',{'rule_id':'E1-value'},'rule-'+claim.claim_id)
        assert call(d,'mj_verify',{'binding_id':claim.claim_id},'verify-'+claim.claim_id)['status']=='verified'
