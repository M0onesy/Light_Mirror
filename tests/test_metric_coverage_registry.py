from dataclasses import replace
from decimal import Decimal
import pytest

from mingjing.models import Fact, AtomicClaim
from mingjing.facts import FactStore,fact_from_dict
from mingjing.financial_derivations import derive_metric,derive_registered_metric
from mingjing.metrics import METRIC_SPECS
from mingjing.rules import execute_rule,execute_rule_interval,validate_rulepack,RuleError,evaluate_rule
from mingjing.decimal_math import DecimalInterval
from mingjing.root_math import integer_root_bounds
from mingjing.judge import Judge

def fact(account,value,**extra):
    defaults=dict(company='测试公司',account=account,period='2023Y',value=Decimal(value),source_doc='report',publish_date='2024-04-15',evidence=('report:p1:cell',))
    defaults.update(extra)
    return Fact(fact_id=f'{account}-{value}-{extra}',**defaults)

def test_dimension_queries_never_mix_parts_currency_or_definitions():
    total=fact('REVENUE','100')
    business=fact('REVENUE','25',dimension_kind='business',dimension_id='超算云服务')
    usd=fact('REVENUE','10',currency='USD')
    defined=fact('REVENUE','50',definition_id='custom')
    store=FactStore([business,usd,defined,total])
    assert store.query('测试公司','REVENUE','2023Y').value==100
    assert store.query('测试公司','REVENUE','2023Y',dimension_kind='business',dimension_id='超算云服务').value==25
    assert store.query('测试公司','REVENUE','2023Y',currency='USD').value==10
    assert len(store.versions('测试公司','REVENUE','2023Y'))==1
    assert fact_from_dict(business.to_dict())==business
    with pytest.raises(ValueError):fact('REVENUE','1',dimension_kind='total',dimension_id='client')

def test_anonymous_customer_identity_cannot_be_selected_by_disclosure_recency():
    first = fact('REVENUE', '25', dimension_kind='customer', dimension_id='客户E', source_doc='first')
    second = fact('REVENUE', '30', dimension_kind='customer', dimension_id='客户E', source_doc='second',
                  publish_date='2024-04-20', evidence=('second:p2:cell',))
    store = FactStore([first, second])
    dims = dict(dimension_kind='customer', dimension_id='客户E')
    unresolved = store.query_outcome('测试公司', 'REVENUE', '2023Y', cutoff='2024-04-22', **dims)
    assert unresolved.status == 'ambiguous_source' and unresolved.fact is None
    assert set(unresolved.candidate_fact_ids) == {first.fact_id, second.fact_id}
    assert store.query('测试公司', 'REVENUE', '2023Y', cutoff='2024-04-22',
                       strategy='pinned', source_doc='first', **dims) == first
    assert store.query('测试公司', 'REVENUE', '2023Y', cutoff='2024-04-22',
                       strategy='same_report', source_doc='second', **dims) == second
    assert store.query_outcome('测试公司', 'REVENUE', '2023Y', cutoff='2024-04-19', **dims).status == 'source_required'
    assert store.query('测试公司', 'REVENUE', '2023Y', cutoff='2024-04-19',
                       strategy='pinned', source_doc='first', **dims) == first


def test_anonymous_customer_same_document_and_named_customer_are_not_false_conflicts():
    original = fact('REVENUE', '25', dimension_kind='customer', dimension_id='客户甲')
    duplicate = replace(original, fact_id='other-evidence', evidence=('report:p2:cell',))
    store = FactStore([original, duplicate])
    assert store.query_outcome('测试公司', 'REVENUE', '2023Y',
        dimension_kind='customer', dimension_id='客户甲').status == 'source_required'
    assert store.query_outcome('测试公司', 'REVENUE', '2023Y',
        dimension_kind='customer', dimension_id='客户甲', strategy='same_report', source_doc='report').status == 'found'
    named = replace(original, dimension_id='青岛国实科技集团有限公司', source_doc='first')
    newer = replace(named, fact_id='newer', value=Decimal('30'), source_doc='second', publish_date='2024-04-20')
    assert FactStore([named, newer]).query('测试公司', 'REVENUE', '2023Y', cutoff='2024-04-22',
                                          dimension_kind='customer', dimension_id=named.dimension_id) == newer


@pytest.mark.parametrize('account,inputs,expected',[
    ('CURRENT_RATIO',{'CURRENT_ASSETS':'200','CURRENT_LIABILITIES':'100'},'2'),
    ('NP_PARENT_MARGIN',{'NP_PARENT':'-10','REVENUE':'100'},'-0.1'),
    ('SELLING_EXPENSE_RATIO',{'EXP_SELLING':'5','REVENUE':'100'},'0.05'),
    ('RD_EXPENSE_RATIO',{'EXP_RD':'5','REVENUE':'100'},'0.05'),
    ('CASH_EQUIVALENTS_CHANGE',{'CFO':'10','CFI':'-5','CFF':'3','FX_CASH_EFFECT':'0'},'8'),
])
def test_registered_dependencies_compute_decimal_with_source_evidence(account,inputs,expected):
    result=derive_metric(FactStore([fact(k,v) for k,v in inputs.items()]),'测试公司',account,'2023Y','合并','未扣非','2024-04-22')
    assert result.value==Decimal(expected)
    assert result.interval is not None and result.evidence and result.rule['id']==METRIC_SPECS[account].rule_id

def test_source_definition_is_required_before_quick_ratio_and_broker_residual():
    store=FactStore([fact('CURRENT_ASSETS','200'),fact('INVENTORY','50'),fact('CURRENT_LIABILITIES','100')])
    assert derive_metric(store,'测试公司','QUICK_RATIO','2023Y','合并','未扣非','2024-04-22').abstention_code=='X2'
    result=derive_metric(store,'测试公司','QUICK_RATIO','2023Y','合并','未扣非','2024-04-22',definition_id='metric:quick-ratio-inventory')
    assert result.value==Decimal('1.5')
    residual=FactStore([fact('CURRENT_LIABILITIES','100'),fact('SHORT_TERM_BORROWING','10'),fact('AP_NOTES','20'),fact('OTHER_CURRENT_LIABILITIES','1')])
    result=derive_metric(residual,'测试公司','BROKER_OTHER_CURRENT_LIABILITIES','2023Y','合并','未扣非','2024-04-22',definition_id='metric:broker-other-current-liabilities')
    assert result.value==70 and all(x.account!='OTHER_CURRENT_LIABILITIES' for x in result.operands)

def test_part_share_is_not_company_total_and_needs_total_same_currency():
    inputs=[fact('REVENUE','25',dimension_kind='customer',dimension_id='客户甲'),fact('REVENUE','100')]
    result=derive_metric(FactStore(inputs),'测试公司','REVENUE_SHARE','2023Y','合并','未扣非','2024-04-22',strategy='pinned',source_doc='report',dimension_kind='customer',dimension_id='客户甲',definition_id='metric:revenue-share')
    assert result.value==Decimal('0.25')
    result=derive_metric(FactStore(inputs[:1]+[replace(inputs[1],currency='USD')]),'测试公司','REVENUE_SHARE','2023Y','合并','未扣非','2024-04-22',strategy='pinned',source_doc='report',dimension_kind='customer',dimension_id='客户甲',definition_id='metric:revenue-share')
    assert result.value is None and result.blockers[0]['account']=='REVENUE'

def test_every_missing_operand_visible_and_conflicts_cannot_be_recomputed_away():
    result=derive_registered_metric(FactStore(),'测试公司','PERIOD_EXPENSE_RATIO','2023Y','合并','未扣非','2024-04-22',definition_id='metric:period-expense-four')
    assert {x['account'] for x in result.blockers}=={'EXP_SELLING','EXP_ADMIN','EXP_RD','EXP_FINANCE','REVENUE'}
    store=FactStore([fact('CURRENT_RATIO','1'),fact('CURRENT_RATIO','2'),fact('CURRENT_ASSETS','200'),fact('CURRENT_LIABILITIES','100')])
    result=derive_metric(store,'测试公司','CURRENT_RATIO','2023Y','合并','未扣非','2024-04-22')
    assert result.value is None and result.abstention_code=='X4'

@pytest.mark.parametrize('account,definition,unresolved',[
    ('BROKER_OTHER_CURRENT_ASSETS','metric:broker-other-current-assets','ASSET_CASH_UNSPECIFIED'),
    ('BROKER_OTHER_NONCURRENT_ASSETS','metric:broker-other-noncurrent-assets','INVESTMENTS_UNSPECIFIED')])
def test_broker_asset_residual_never_invents_cash_or_investment_definition(account,definition,unresolved):
    raw=[fact('CURRENT_ASSETS','100'),fact('CASH_FUNDS','20'),fact('AR_NOTES','10'),fact('INVENTORY','5'),
         fact('NONCURRENT_ASSETS','100'),fact('PPE','20'),fact('INTANGIBLES','10'),fact('LONG_TERM_EQUITY_INVESTMENTS','5')]
    result=derive_metric(FactStore(raw),'测试公司',account,'2023Y','合并','未扣非','2024-04-22',definition_id=definition)
    assert result.value is None and result.abstention_code=='X2'
    assert any(x.get('account')==unresolved and x['kind']=='definition' for x in result.blockers)

def test_cagr_registered_decimal_root_preserves_enclosure_and_invalid_years():
    interval=integer_root_bounds(Decimal('2'),2)
    assert interval.lower**2<=2<=interval.upper**2
    assert execute_rule('FIN-cagr',{'current':'121','base':'100','years':'2'})==Decimal('0.1')
    for years in ('0','-1','1.5','101'):
        assert execute_rule('FIN-cagr',{'current':'121','base':'100','years':years}) is None
    assert validate_rulepack()['status']=='ok'
    card={'id':'hostile','formula':'cagr(current,base,years)','inputs':{'current':'currency','base':'currency','years':'ratio'},'dimension':'ratio'}
    with pytest.raises(RuleError):evaluate_rule(card,{'current':1,'base':1,'years':1})

def test_currency_native_fact_requires_no_exchange_rate():
    claim=AtomicClaim('usd','测试公司2023年合并营业收入为10美元。',0,26,company='测试公司',account='REVENUE',period='2023Y',candidate_value='10',unit='元',caliber_hint=('合并',),numeric_text='10',source_numeric_start=19,table_context={'currency':'USD'})
    # Legacy programmatic occurrence can use its unique printed numeric span.
    claim=replace(claim,source_numeric_start=None)
    result=Judge(FactStore([fact('REVENUE','10',currency='USD')]),'2024-04-22').verify(claim)
    assert result.status=='verified' and result.source_dimensions['currency']=='USD'

@pytest.mark.parametrize('value,status,code',[('2000','verified',None),('4000','violated',None),('1000','abstained','X4')])
def test_range_uses_whole_source_assertion_not_an_endpoint(value,status,code):
    from mingjing.mentions import extract_mentions
    claims=extract_mentions('测试公司2023年合并营业收入为1000至3000元。','测试公司')
    assert claims
    result=Judge(FactStore([fact('REVENUE',value,decimals=0)]),'2024-04-22').verify(claims[-1])
    assert result.status==status and result.abstention_code==code
    if status!='abstained':
        assert result.rule_ids==['FIN-range'] and result.claim_interval['lower']=='1000'
    if result.correction:assert '1000至' not in result.correction['replacement_text']

def test_cagr_source_periods_and_ratio_growth_are_bound_to_raw_operands():
    from mingjing.mentions import extract_mentions
    cagr=extract_mentions('测试公司2021-2023年合并营业收入CAGR为10%。','测试公司')[-1]
    facts=[fact('REVENUE','121'),fact('REVENUE','100',period='2021Y')]
    result=Judge(FactStore(facts),'2024-04-22').verify(cagr)
    assert result.status=='verified' and result.rule_ids==['FIN-cagr']
    growth=extract_mentions('测试公司2023年合并毛利率同比增长20%。','测试公司')[-1]
    facts=[fact('REVENUE','100'),fact('COST','70'),fact('REVENUE','100',period='2022Y'),fact('COST','75',period='2022Y')]
    result=Judge(FactStore(facts),'2024-04-22').verify(growth)
    assert result.status=='verified' and set(result.rule_ids)=={'E5-yoy','FIN-gross-margin'}

def test_four_input_nonrecurring_missing_component_is_never_zero():
    inputs=[fact('NP_PARENT','100'),fact('NRI_TOTAL','15'),fact('NRI_TAX_EFFECT','3'),fact('NRI_MINORITY','2')]
    full=derive_metric(FactStore(inputs),'测试公司','NP_PARENT_ADJ','2023Y','合并','扣非','2024-04-22')
    assert full.value==90 and full.rule['id']=='FIN-non-recurring'
    missing=derive_metric(FactStore(inputs[:-1]),'测试公司','NP_PARENT_ADJ','2023Y','合并','扣非','2024-04-22')
    assert missing.value is None and any(x.get('account')=='NRI_MINORITY' for x in missing.blockers)

def test_frozen_scope_blockers_identical_in_native_and_kernel_replay():
    from mingjing.mentions import extract_mentions
    from mingjing.source_semantics import freeze_source_dimensions
    claim=freeze_source_dimensions(replace(extract_mentions('测试公司2023年营业收入100元。','测试公司')[-1],
                               binding_status='agreed',caliber_hint=('合并',)))
    store=FactStore([fact('REVENUE','100')])
    native=Judge(store,'2024-04-22',require_dual_binding=True).verify(claim)
    replay=Judge(store,'2024-04-22',require_dual_binding=False).verify(claim)
    assert native.to_dict()==replay.to_dict() and native.status=='abstained'
    assert any(x['kind']=='source_scope' for x in native.blockers)

def test_invalid_binding_full_blocker_reporting_does_not_query_facts(monkeypatch):
    from mingjing.mentions import extract_mentions
    from mingjing.source_semantics import freeze_source_dimensions
    claim=freeze_source_dimensions(extract_mentions('测试公司2023年合并营业收入100元。','测试公司')[-1])
    store=FactStore([fact('REVENUE','100')])
    monkeypatch.setattr(store,'query_outcome',lambda *a,**k:pytest.fail('source substitution queried factual values'))
    result=Judge(store,'2024-04-22').verify(replace(claim,account='COST',binding_status='agreed'))
    assert result.status=='abstained' and result.abstention_code=='X3'
    assert any(x.get('lookup_status')=='not_executed' for x in result.blockers)

def test_frozen_report_slots_keep_dimension_interval_and_blocker_audit_fields():
    import json
    from mingjing.mentions import extract_mentions
    from mingjing.financial_tools import FinancialDispatcher
    claim=extract_mentions('测试公司2023年合并营业收入100元。','测试公司')[-1]
    store=FactStore([])
    result=Judge(store,'2024-04-22').verify(claim).to_dict()
    dispatcher=FinancialDispatcher('run','lock',[claim],[],[],[],'2024-04-22',task_company='测试公司')
    dispatcher.freeze_results([result])
    detail=json.loads(dispatcher.slots[claim.claim_id+'.detail'])
    assert detail['blockers']==result['blockers'] and detail['source_dimensions']==result['source_dimensions']
    assert detail['source_dimensions']['scope_header']=='合并'
    record=fact('CURRENT_RATIO','2',display_unit='倍').to_dict()
    operand=dispatcher.compact_operand(record)
    assert operand['value_unit']=='倍'
    assert all(operand[k]==record[k] for k in ('dimension_kind','dimension_id','currency','definition_id','decimals'))
