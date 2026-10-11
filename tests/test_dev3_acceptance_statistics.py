import pytest

from scripts.acceptance_dev3 import acceptance_statistics


def test_historical_open_definitions_and_duplicate_locations_are_not_hidden():
    payload = {'items': [
        {'cid': 'c1', 'assertion_id': 'a1', 'category': 'core', 'status': 'verified', 'execution_status': 'judged'},
        {'cid': 'c2', 'assertion_id': 'a1', 'category': 'core', 'status': 'verified', 'execution_status': 'judged'},
        *[{'cid': f'x{i}', 'category': 'open_metric', 'status': 'unverifiable', 'execution_status': 'judged'}
          for i in range(10)],
        {'cid': 'f1', 'category': 'forecast', 'status': 'skipped', 'execution_status': 'judged'},
    ], 'partial': False, 'execution_statistics': {'pending_ledger_mentions': 0}}
    stats = acceptance_statistics(payload)
    assert stats['historical_financial']['occurrences'] == 12
    assert stats['historical_financial']['assertions'] == 11
    assert stats['historical_financial']['unresolved'] == 10
    assert not stats['historical_financial']['target_at_most_9']
    assert stats['groups']['forecast']['occurrences'] == 1
    assert stats['ledger_complete']


def test_processed_missing_material_is_distinct_from_unexecuted_ledger():
    payload = {'items': [{'cid': 'c1', 'category': 'core', 'status': 'unverifiable',
                          'execution_status': 'judged'}], 'partial': True,
               'execution_statistics': {'pending_ledger_mentions': 3}}
    stats = acceptance_statistics(payload)
    assert stats['groups']['core']['pending'] == 0
    assert stats['historical_financial']['unresolved'] == 1
    assert stats['unmodeled_ledger_mentions'] == 3
    assert not stats['ledger_complete']
    assert stats['historical_financial']['target_at_most_9'] is None
    assert stats['recall'] == 'unknown_requires_independent_gold'


def occurrence(cid, category='core', status='verified', **changes):
    return {'cid': cid, 'assertion_id': cid, 'category': category, 'status': status,
            'execution_status': 'judged', 'entity': 'company', 'forecast': False,
            'modality': 'fact', 'period': '2023Y', **changes}


def complete_payload(items):
    return {'items': items, 'report': {'report_date': '2024-04-22'}, 'partial': False,
            'execution_statistics': {'pending_ledger_mentions': 0}}


def test_all_company_history_keeps_54_operating_and_unbound_occurrences_in_target():
    payload = complete_payload([
        occurrence('financial', status='unverifiable'),
        occurrence('financial-repeat', status='unverifiable', assertion_id='financial'),
        *[occurrence(f'business-{i}', category=('other_fact', 'operational', 'unbound')[i % 3],
                     status='unverifiable', entity='segment' if i % 2 else 'company') for i in range(54)],
        occurrence('industry', category='external', entity='external', status='unverifiable'),
        occurrence('market', category='market', entity='market', status='skipped'),
        occurrence('template', category='template'),
    ])
    stats = acceptance_statistics(payload)
    assert stats['historical_financial']['occurrences'] == 2
    assert stats['historical_financial']['target_at_most_9'] is True
    main = stats['company_historical_acceptance']
    assert (main['occurrences'], main['assertions'], main['unresolved'], main['unresolved_assertions']) == (56, 55, 56, 55)
    assert main['target_at_most_9'] is False
    assert stats['company_business_operating_and_unbound']['occurrences'] == 54
    assert stats['industry_external']['occurrences'] == 1
    assert stats['market_inputs']['occurrences'] == 1
    assert stats['template']['occurrences'] == 1


def test_forecast_uses_period_and_modality_without_hiding_same_sentence_history():
    sentence = '2023年实际收入及预计2025年收入，行业未来预测均需保留。'
    stats = acceptance_statistics(complete_payload([
        occurrence('actual', text=sentence),
        occurrence('future-period', text=sentence, category='other_fact', period='2025Y'),
        occurrence('inference', category='unbound', modality='inference'),
        occurrence('explicit-forecast', category='forecast', forecast=True),
        occurrence('external-inference', category='external', entity='external', modality='inference'),
        occurrence('historical-operation', category='operational', period='2023H1'),
    ]))
    assert stats['historical_financial']['occurrences'] == 1
    assert stats['company_historical_acceptance']['occurrences'] == 2
    assert stats['company_business_operating_and_unbound']['occurrences'] == 1
    assert stats['forecast']['occurrences'] == 4
    assert stats['industry_external']['occurrences'] == 1  # Explicitly overlapping scope.


@pytest.mark.parametrize('execution_status', ['pending', 'unmapped', '', None])
def test_unprocessed_target_cannot_pass_even_when_aggregate_counter_says_zero(execution_status):
    stats = acceptance_statistics(complete_payload([
        occurrence('not-executed', execution_status=execution_status),
    ]))
    assert stats['modeled_pending_occurrences'] == 1
    assert not stats['ledger_complete']
    for key in ('core', 'historical_financial', 'company_historical_acceptance'):
        assert stats[key]['unresolved_ids'] == ['not-executed']
        assert stats[key]['judged'] == 0
        assert stats[key]['target_at_most_9'] is None
        assert stats[key]['target_assessment'] == 'not_assessed_incomplete_ledger'


@pytest.mark.parametrize('pending', [None, -1, True, '0'])
def test_unknown_or_invalid_pending_counter_cannot_establish_target(pending):
    payload = complete_payload([occurrence('actual')])
    payload['execution_statistics']['pending_ledger_mentions'] = pending
    stats = acceptance_statistics(payload)
    assert not stats['ledger_complete']
    assert stats['company_historical_acceptance']['target_at_most_9'] is None


def test_empty_historical_scope_and_missing_execution_proof_are_not_success():
    stats = acceptance_statistics(complete_payload([occurrence('future', period='2025Y')]))
    assert stats['company_historical_acceptance']['occurrences'] == 0
    assert stats['company_historical_acceptance']['target_at_most_9'] is None
    assert stats['company_historical_acceptance']['target_assessment'] == 'not_assessed_empty_scope'
    payload = complete_payload([occurrence('actual')])
    payload['items'][0].pop('execution_status')
    assert not acceptance_statistics(payload)['ledger_complete']
    payload = complete_payload([occurrence('actual', status='pending')])
    assert acceptance_statistics(payload)['company_historical_acceptance']['target_at_most_9'] is None
