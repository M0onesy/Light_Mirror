from decimal import Decimal

import pytest

from mingjing.facts import FactStore
from mingjing.financial_derivations import derive_metric, derive_registered_metric
from mingjing.financial_tools import FinancialDispatcher, FinancialRequirementError
from mingjing.judge import Judge
from mingjing.mentions import extract_mentions
from mingjing.models import Fact
from mingjing.rules import validate_rulepack


def fact(account, value, **fields):
    data = dict(fact_id=account + str(value), company='测试公司', account=account,
                period='2023Y', value=Decimal(value), source_doc='annual',
                publish_date='2024-04-15', evidence=('annual:p1:' + account,),
                source_type='primary_statement', decimals=2)
    data.update(fields)
    return Fact(**data)


@pytest.mark.parametrize('metric, operands, expected', [
    ('AR_NOTES', {'AR': '40', 'NOTES_RECEIVABLE': '2'}, '42'),
    ('AP_NOTES', {'AP': '60', 'NOTES_PAYABLE': '20'}, '80'),
    ('LIABILITIES_EQUITY', {'LIABILITIES': '60', 'EQUITY_PARENT': '30', 'EQUITY_MINORITY': '10'}, '100'),
])
def test_explicit_aggregate_uses_every_external_operand_with_propagated_interval(metric, operands, expected):
    result = derive_metric(FactStore([fact(k, v) for k, v in operands.items()]),
                          '测试公司', metric, '2023Y', '合并', '未扣非', '2024-04-22')
    assert result.value == Decimal(expected) and result.interval is not None
    assert {row.account for row in result.operands} == set(operands)
    assert all(step['rule_version_hash'] for step in result.calculation_steps)
    assert result.calculation_steps[-1]['value'] == expected


def test_missing_notes_is_not_an_implicit_zero_and_rejected_direct_aggregate_cannot_be_overridden():
    incomplete = FactStore([fact('AP', '60')])
    result = derive_metric(incomplete, '测试公司', 'AP_NOTES', '2023Y', '合并', '未扣非', '2024-04-22')
    assert result.value is None and result.abstention_code == 'X1'
    assert any(row.get('account') == 'NOTES_PAYABLE' for row in result.blockers)
    conflicting = FactStore([fact('AR_NOTES', '42'), fact('AR_NOTES', '50'),
                             fact('AR', '40'), fact('NOTES_RECEIVABLE', '2')])
    for fn in (derive_metric, derive_registered_metric):
        result = fn(conflicting, '测试公司', 'AR_NOTES', '2023Y', '合并', '未扣非', '2024-04-22')
        assert result.value is None and result.abstention_code == 'X4'


def test_nested_broker_residual_retains_child_rule_raw_facts_and_permissions():
    store = FactStore([fact('CURRENT_LIABILITIES', '100'), fact('SHORT_TERM_BORROWING', '10'),
                       fact('AP', '60'), fact('NOTES_PAYABLE', '20')])
    result = derive_metric(store, '测试公司', 'BROKER_OTHER_CURRENT_LIABILITIES', '2023Y', '合并', '未扣非',
                           '2024-04-22', definition_id='metric:broker-other-current-liabilities')
    assert result.value == Decimal('10')
    assert result.rule_ids == ['FIN-payables-notes-sum', 'FIN-broker-other-current-liabilities']
    assert [step['rule_id'] for step in result.calculation_steps] == result.rule_ids
    ap_input = result.calculation_steps[-1]['inputs']['AP_NOTES']
    assert ap_input['derived_rule_id'] == 'FIN-payables-notes-sum'
    assert set(ap_input['source_fact_ids']) == {'AP60', 'NOTES_PAYABLE20'}
    # Public read dependencies must require both actual executed rules.
    # This source proof is representative of the explicit bounded summary.
    claim = extract_mentions('测试公司2023年合并应付票据及应付账款为80元。', '测试公司')[-1]
    dispatcher = FinancialDispatcher('run', 'lock', [claim], store.facts, [], [], '2024-04-22', task_company='测试公司')
    assert dispatcher._required_verification_rules(claim) == ['FIN-payables-notes-sum']
    identity = {'facts_read': {claim.claim_id}, 'rules_read': set()}
    with pytest.raises(FinancialRequirementError):
        dispatcher._require_verification_reads(identity, claim)


def test_nested_sum_never_resolves_conflicts_or_splices_documents_or_mother_equity():
    store = FactStore([fact('CURRENT_LIABILITIES', '100'), fact('SHORT_TERM_BORROWING', '10'),
                       fact('AP_NOTES', '80'), fact('AP_NOTES', '70'), fact('AP', '60'), fact('NOTES_PAYABLE', '20')])
    result = derive_metric(store, '测试公司', 'BROKER_OTHER_CURRENT_LIABILITIES', '2023Y', '合并', '未扣非',
                           '2024-04-22', definition_id='metric:broker-other-current-liabilities')
    assert result.value is None and result.abstention_code == 'X4'
    different = FactStore([fact('AR', '40'), fact('NOTES_RECEIVABLE', '2', source_doc='other')])
    assert derive_metric(different, '测试公司', 'AR_NOTES', '2023Y', '合并', '未扣非', '2024-04-22').value is None
    mother = FactStore([fact('EQUITY_PARENT', '30', scope='母公司'), fact('EQUITY_MINORITY', '10', scope='母公司')])
    result = derive_metric(mother, '测试公司', 'EQUITY', '2023Y', '母公司', '未扣非', '2024-04-22')
    assert result.value is None and result.abstention_code == 'X2'


def test_judgment_publishes_nested_calculations_and_rulepack_remains_pending_human_review():
    store = FactStore([fact('LIABILITIES', '60'), fact('EQUITY_PARENT', '30'), fact('EQUITY_MINORITY', '10')])
    claim = extract_mentions('测试公司2023年合并负债和股东权益为100元。', '测试公司')[-1]
    result = Judge(store, '2024-04-22').verify(claim)
    assert result.status == 'verified'
    assert result.rule_ids == ['FIN-consolidated-equity-sum', 'FIN-liabilities-equity-sum']
    assert len(result.calculation_steps) == 2
    assert {row['account'] for row in result.operands} == {'LIABILITIES', 'EQUITY_PARENT', 'EQUITY_MINORITY'}
    assert validate_rulepack()['status'] == 'ok'
