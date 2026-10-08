"""Exercise the real role gate against matching but incorrect A/B bindings."""
from dataclasses import replace
from decimal import Decimal

import pytest

from mingjing.financial_tools import FinancialArgumentError, FinancialDispatcher, ToolDenied
from mingjing.mentions import extract_mentions
from mingjing.models import Fact


def fixture():
    claim = extract_mentions('甲公司2023年合并营业收入100元。', '甲公司')[0]
    facts = [Fact('rev', '甲公司', 'REVENUE', '2023Y', value=Decimal('200'), publish_date='2024-04-01'),
             Fact('np', '甲公司', 'NP', '2023Y', value=Decimal('100'), publish_date='2024-04-01'),
             Fact('old', '甲公司', 'REVENUE', '2022Y', value=Decimal('100'), publish_date='2024-04-01')]
    dispatcher = FinancialDispatcher('run', 'lock', [claim], facts, [], [], '2024-04-22')
    for role in ('planner', 'verifier', 'coordinator'):
        dispatcher.register_session(role, role)
    return dispatcher, claim


def call(dispatcher, role, name, params, call_id):
    return dispatcher.dispatch(dict(session_id=role, role=role, run_id='run', resource_lock='lock',
                                    call_id=call_id, tool_name=name, params=params))


@pytest.mark.parametrize('change', [{'account': 'NP'}, {'period': '2022Y'},
                                    {'relation': 'yoy', 'base_period': '2022Y'}])
def test_matching_wrong_bindings_never_open_the_fact_gate(change):
    dispatcher, claim = fixture()
    binding = dict(company='甲公司', account='REVENUE', period='2023Y', scope='合并', relation='value', base_period='')
    binding.update(change)
    for role in ('planner', 'verifier'):
        with pytest.raises(FinancialArgumentError) as caught:
            call(dispatcher, role, 'mj_submit_binding', {'mention_id': claim.claim_id, 'binding': binding}, role)
        assert caught.value.rejection()['error']['code'] == 'binding_source_conflict'
        assert not dispatcher.bindings[role]
    bound = dispatcher.freeze_bindings()[0]
    assert bound.binding_status == 'disagreed'
    assert not dispatcher.agreements[claim.claim_id]['agreed']
    assert bound.account == 'REVENUE' and bound.period == '2023Y'
    with pytest.raises(ToolDenied, match='no consistent binding'):
        call(dispatcher, 'coordinator', 'mj_get_fact', {'binding_id': claim.claim_id}, 'fact')


def test_fact_boundary_checks_source_proof_even_with_an_incorrect_agreement():
    dispatcher, original = fixture()
    frozen = dispatcher.claims[original.claim_id]
    dispatcher.claims[original.claim_id] = replace(frozen, account='NP', binding_status='agreed')
    dispatcher.agreements[original.claim_id] = {'agreed': True}
    dispatcher.store.query = lambda *a, **k: (_ for _ in ()).throw(AssertionError('must not read unrelated fact'))
    result = call(dispatcher, 'coordinator', 'mj_get_fact', {'binding_id': original.claim_id}, 'fact')
    assert result['fact'] is None and result['source_definition_unavailable']


def test_host_freeze_is_not_recomputed_from_rebound_claim_fields():
    dispatcher, original = fixture()
    frozen = dispatcher.claims[original.claim_id]
    restored = FinancialDispatcher('resume', 'lock', [replace(frozen, account='NP')], [], [], [], '2024-04-22')
    dimensions = restored.source_dimensions(restored.claims[original.claim_id])
    assert dimensions['account'] == 'REVENUE'
    assert not set(dimensions) & {'value', 'candidate_value', 'expected', 'formula', 'verdict'}
