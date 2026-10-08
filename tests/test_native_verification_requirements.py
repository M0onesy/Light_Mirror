"""11b missing facts require a real rule read and a recoverable native denial."""
import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

from mingjing.agent import FinancialAgent
from mingjing.audit import AuditLog
from mingjing.config import AppConfig
from mingjing.financial_tools import FinancialDispatcher, FinancialRequirementError, ToolDenied
from mingjing.mentions import extract_mentions
from mingjing.models import Fact


FIXTURE = Path(__file__).parent/'fixtures/native_missing_rule_reparse_11b.json'


def owner_for(tmp_path, *, text=None, facts=()):
    record = json.loads(FIXTURE.read_text(encoding='utf-8'))
    claims = extract_mentions(text or record['claim_text'], record['company'])
    owner = FinancialAgent({}, AppConfig().to_dict(), lambda _: '', tmp_path, run_id='native-missing-contract')
    owner.resource_lock = 'trusted-lock'
    owner.audit = AuditLog(tmp_path, owner.run_id)
    owner.save_state = lambda *_: None
    owner.dispatcher = FinancialDispatcher(owner.run_id, owner.resource_lock, claims, list(facts), [], [],
                                            '2024-04-22', audit=owner.audit)
    for role in ('planner','verifier','coordinator'):
        owner.dispatcher.register_session(role,role)
    for role in ('planner','verifier'):
        for claim in claims:
            incoming(owner,'mj_submit_binding',{'mention_id':claim.claim_id,'binding':{
                'company':claim.company,'account':claim.account,'period':claim.period,'scope':'合并',
                'relation':claim.relation,'base_period':claim.base_period or ''}}, role+'-'+claim.claim_id, role=role)
    assert all(claim.binding_status=='agreed' for claim in owner.dispatcher.freeze_bindings())
    return owner,claims


def incoming(owner,name,params,call_id,*,role='coordinator',session=None):
    return owner._incoming('mj/tool.dispatch', {'run_id':owner.run_id,'resource_lock':owner.resource_lock,
        'session_id':session or role,'role':role,'tool_name':name,'params':params,'call_id':call_id})


def test_recorded_empty_requirements_no_longer_prevent_missing_fact_abstention(tmp_path):
    original = FIXTURE.read_bytes()
    record = json.loads(original)
    assert record['recorded_lookup']['required_rule_ids']==[]
    assert record['symptoms']['native_verify_denials']==2 and record['symptoms']['final_judgments']==0
    owner,claims = owner_for(tmp_path)
    ids = [claim.claim_id for claim in claims]
    queries = incoming(owner,'mj_get_facts',{'binding_ids':ids},'get-facts')
    assert queries['items'][0]['query_outcome']['status']=='missing'
    assert queries['items'][0]['required_rule_ids']==['E1-value']
    rejection = incoming(owner,'mj_verify_bindings',{'binding_ids':ids},'no-rule')
    assert rejection['status']=='rejected' and rejection['error']['code']=='verification_reads_required'
    assert rejection['required_rule_ids']==['E1-value'] and rejection['facts_required'] is False
    assert owner.dispatcher.trace[-1]['status']=='denied'
    card = incoming(owner,'mj_get_rule',{'rule_id':'E1'},'repair-rule')
    assert card['id']=='E1-value'
    result = incoming(owner,'mj_verify_bindings',{'binding_ids':ids},'repair-verify')['items'][0]
    assert result['status']=='abstained' and result['abstention_code']=='X1' and result['error_type']=='E8'
    assert owner.dispatcher.store.facts==[] and FIXTURE.read_bytes()==original


@pytest.mark.parametrize('text,missing', [
    ('甲公司2023年合并口径营业收入同比增长25%。','current'),
    ('甲公司2023年合并口径营业收入同比增长25%。','base'),
    ('甲公司2023年第二季度合并口径营业收入环比增长25%。','current'),
    ('甲公司2023年第二季度合并口径营业收入环比增长25%。','base'),
])
def test_only_growth_batch_with_missing_current_or_base_receives_legal_abstention(tmp_path,text,missing):
    claim = extract_mentions(text,'甲公司')[0]
    assert claim.relation in {'yoy','qoq'}
    period = claim.base_period if missing=='current' else claim.period
    fact = Fact('raw','甲公司','REVENUE',period,value=Decimal('100'),publish_date='2024-03-01',source_doc='annual')
    owner,claims = owner_for(tmp_path,text=text,facts=[fact])
    ids = [row.claim_id for row in claims]
    assert len(ids)==1
    query = incoming(owner,'mj_get_facts',{'binding_ids':ids},'facts')['items'][0]
    assert query['required_rule_ids']==['E1-value']
    incoming(owner,'mj_get_rule',{'rule_id':'E1-value'},'rule')
    result = incoming(owner,'mj_verify_bindings',{'binding_ids':ids},'verify')['items'][0]
    assert result['status']=='abstained' and result['abstention_code']=='X1'
    assert result.get('expected') is None


def test_requirement_rejection_lists_missing_fact_read_and_cannot_cross_session(tmp_path):
    owner,claims = owner_for(tmp_path)
    ids = [row.claim_id for row in claims]
    result = incoming(owner,'mj_verify_bindings',{'binding_ids':ids},'before-facts')
    assert result['facts_required'] is True and result['required_rule_ids']==['E1-value']
    incoming(owner,'mj_get_facts',{'binding_ids':ids},'facts')
    incoming(owner,'mj_get_rule',{'rule_id':'E1-value'},'rule')
    owner.dispatcher.register_session('other','coordinator',ids)
    result = incoming(owner,'mj_verify_bindings',{'binding_ids':ids},'other',session='other')
    assert result['status']=='rejected' and result['facts_required'] is True
    assert owner.dispatcher.trace[-1]['status']=='denied'


@pytest.mark.parametrize('tamper', ['message','rules','facts'])
def test_public_requirement_rejection_uses_only_registry_constants(tamper):
    secret = 'sk-private-do-not-echo-anywhere'
    error = FinancialRequirementError(['E1-value'],facts_required=True)
    if tamper=='message':
        error.args=(secret,)
    elif tamper=='rules':
        error.required_rule_ids=[secret,'E1-value']
    else:
        error.facts_required=secret
    result = error.rejection()
    assert secret not in json.dumps(result)
    assert result['required_rule_ids']==['E1-value']
    assert type(result['facts_required']) is bool


def test_other_authority_denials_never_become_repairable_tool_feedback(tmp_path):
    owner,_ = owner_for(tmp_path)
    with pytest.raises(ToolDenied,match='scope'):
        incoming(owner,'mj_verify_bindings',{'binding_ids':['foreign']},'foreign')
    with pytest.raises(ToolDenied,match='role cannot'):
        incoming(owner,'mj_verify_bindings',{'binding_ids':['claim-00001']},'wrong-role',role='planner')
