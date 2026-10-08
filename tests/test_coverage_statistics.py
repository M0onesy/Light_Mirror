from copy import deepcopy

import pytest

from mingjing.coverage_stats import PRIMARY_SCOPE, attach_coverage_statistics, coverage_statistics


def record(claim_id, status, scope=PRIMARY_SCOPE, *, amount='100', unit='元'):
    claim = dict(claim_id=claim_id, company='甲公司', account='REVENUE', period='2023Y',
                 caliber_hint=['合并', '未扣非'], parse_status='parsed', relation='value',
                 candidate_value=amount, unit=unit, display_decimals=0, approximate=False,
                 table_context={'evaluation_scope': scope})
    ledger_status = 'judged' if status in {'verified', 'violated'} else status
    entry = dict(id=claim_id, claim_id=claim_id, status=ledger_status, evaluation_scope=scope)
    verdict = dict(claim_id=claim_id, status=status)
    return claim, entry, verdict


def population(*records):
    claims, entries, verdicts = map(list, zip(*records))
    return dict(mentions=entries), claims, verdicts


def test_settled_abstentions_are_not_financial_coverage():
    args = population(record('a', 'verified'), record('b', 'abstained', amount='200'),
                      record('c', 'abstained', amount='300'))
    stat = coverage_statistics(*args)
    assert stat['core']['processing_completion_rate'] == 1
    assert stat['core']['judgement_coverage'] == 1 / 3
    assert stat['core']['abstention_rate'] == 2 / 3
    assert stat['core']['unique_assertions'] == 3
    assert stat['core']['source_supported_judgement_coverage'] == 1 / 3
    assert stat['core']['conditional_judged'] == 0


def test_auxiliary_sources_are_explicit_and_repetition_does_not_reduce_target():
    args = population(*(record(str(i), 'abstained') for i in range(10)),
                      record('industry', 'abstained', 'industry_historical'),
                      record('forecast', 'out_of_scope', 'forecast'))
    stat = coverage_statistics(*args)
    assert stat['core']['counts']['abstained'] == 10
    assert stat['core']['unique_assertions'] == 1
    assert stat['abstention_target_met'] is False
    assert stat['groups']['industry_historical']['counts']['abstained'] == 1
    assert stat['groups']['forecast']['occurrences'] == 1
    assert stat['occurrences'] == 12


def test_pending_cannot_be_hidden_by_cached_verdict_or_conflicting_exclusion():
    args = population(record('a', 'verified'), record('b', 'out_of_scope'))
    args[0]['mentions'][0]['status'] = 'pending'
    stat = coverage_statistics(*args)
    assert stat['core']['counts']['pending'] == 2
    assert stat['core']['judgement_coverage'] == 0
    assert stat['abstention_target_met'] is False


def test_unknown_dimensions_are_not_silently_deduplicated_or_removed():
    args = population(record('a', 'abstained'), record('b', 'abstained'))
    for claim in args[1]:
        claim['account'] = 'UNKNOWN'
        claim['parse_status'] = 'unresolved'
        claim['table_context'] = {}
    stat = coverage_statistics(*args)
    assert stat['core']['unique_assertions'] == 2
    assert stat['core']['financial_candidates'] == 2


def test_empty_population_cannot_pass_and_duplicates_are_rejected():
    assert coverage_statistics({}, [], [])['abstention_target_met'] is False
    args = population(record('a', 'verified'))
    args[0]['mentions'].append(deepcopy(args[0]['mentions'][0]))
    with pytest.raises(ValueError, match='原文出现'):
        coverage_statistics(*args)


def test_statistics_do_not_rewrite_legacy_coverage_or_results():
    args = population(record('a', 'verified'), record('b', 'verified', unit='万元'))
    coverage = dict(args[0], coverage=1.0, schema_version=1)
    payload = dict(coverage=coverage, claims=args[1], results=args[2])
    before = deepcopy(payload)
    attach_coverage_statistics(payload)
    assert payload['coverage'] == before['coverage']
    assert payload['results'] == before['results']
    assert payload['coverage_statistics']['verified_fact_dimensions'] == 1
    assert payload['coverage_statistics']['core']['unique_assertions'] == 2


def test_amount_and_disclosed_growth_are_distinct_financial_dimensions():
    args = population(record('amount', 'verified'), record('growth', 'verified'))
    args[1][0]['relation'] = 'value'
    args[1][1].update(relation='yoy', base_period='2022Y')
    stat = coverage_statistics(*args)
    assert stat['verified_fact_dimensions'] == 2
    assert stat['core']['counts']['verified'] == 2


def test_ledger_deletion_or_reclassification_cannot_hide_abstentions():
    args = population(record('a', 'verified'), record('b', 'abstained'))
    deleted = deepcopy(args)
    deleted[0]['mentions'].pop()
    with pytest.raises(ValueError, match='映射不完整'):
        coverage_statistics(*deleted)
    args[0]['mentions'][1]['evaluation_scope'] = 'industry_historical'
    with pytest.raises(ValueError, match='核查范围冲突'):
        coverage_statistics(*args)


def test_scalar_financial_ratio_does_not_display_as_percent():
    from mingjing.presentation import metric_display_metadata, display_standard_value, standard_unit
    record = dict(metric='CURRENT_RATIO', value='0.8053927',
                  expected_interval={'lower':'0.8053926', 'upper':'0.8053928',
                                     'lower_closed':True, 'upper_closed':False})
    shown = metric_display_metadata(record)
    assert standard_unit('CURRENT_RATIO') == '倍'
    assert display_standard_value('0.8', '倍') == '0.8 倍'
    assert shown['display_unit'] == '倍'
    assert '%' not in shown['display_value']


def test_conditional_judgements_keep_all_counts_but_are_not_source_supported():
    args = population(record('source', 'verified'), record('conditional', 'verified'),
                      record('wrong', 'violated'), record('missing', 'abstained'))
    args[2][1]['source_dimensions'] = {'scope_provenance': 'user_supplied_scope'}
    args[2][2]['source_dimensions'] = {'provenance': 'user_supplied_scope'}
    before = deepcopy(args)
    stat = coverage_statistics(*args)
    core = stat['core']
    assert core['counts']['verified'] == 2 and core['counts']['violated'] == 1
    assert core['judged'] == 3 and core['judgement_coverage'] == 3 / 4
    assert core['conditional_counts']['verified'] == 1 and core['conditional_counts']['violated'] == 1
    assert core['conditional_judged'] == 2 and core['source_supported_judged'] == 1
    assert core['source_supported_judgement_coverage'] == 1 / 4
    assert stat['unconditional_unresolved'] == 3
    assert stat['schema_version'] == 'financial-coverage.v3' and args == before


def test_conditionally_decided_occurrences_cannot_hide_abstention_target_failure():
    args = population(*(record(str(index), 'verified') for index in range(10)))
    for verdict in args[2]:
        verdict['source_dimensions'] = {'scope_provenance': 'user_supplied_scope'}
    stat = coverage_statistics(*args)
    assert stat['core']['counts']['abstained'] == 0
    assert stat['abstention_target_met'] is True
    assert stat['unconditional_abstention_target_met'] is False
    assert stat['unconditional_unresolved'] == 10
    assert stat['core']['source_supported_judgement_coverage'] == 0
    assert stat['core']['unique_assertions'] == 1
    assert stat['core']['conditional_judged'] == 10


def test_actual_result_scope_wins_over_pre_binding_conditional_assumption():
    args = population(record('table', 'verified'), record('source', 'verified'))
    for claim in args[1]:
        claim['table_context']['source_dimensions'] = {'scope_provenance': 'user_supplied_scope'}
    args[2][0]['source_dimensions'] = {'scope_provenance': 'explicit_disclosed_table_reference',
                                     'provenance': 'user_supplied_scope'}
    args[2][1]['source_dimensions'] = {'provenance': 'source_account_definition'}
    stat = coverage_statistics(*args)
    assert stat['core']['conditional_judged'] == 0
    assert stat['core']['source_supported_judgement_coverage'] == 1


@pytest.mark.parametrize('result_dimensions', [None, {}, {'provenance': 'source_text_deterministic'}])
def test_old_result_falls_back_to_frozen_per_claim_scope_proof(result_dimensions):
    args = population(record('a', 'verified'), record('b', 'verified'))
    args[1][0]['table_context']['source_dimensions'] = {'scope_provenance': 'user_supplied_scope'}
    if result_dimensions is not None:
        args[2][0]['source_dimensions'] = result_dimensions
    stat = coverage_statistics(*args)
    assert stat['core']['conditional_counts']['verified'] == 1
    assert stat['core']['source_supported_judgement_coverage'] == 1 / 2


def test_unrelated_global_declaration_does_not_mark_every_occurrence_conditional():
    args = population(record('a', 'verified'), record('b', 'verified'))
    for claim in args[1]:
        claim['table_context']['scope_declaration'] = {'scope': '合并', 'provenance': 'user_supplied_scope'}
    payload = {'coverage': args[0], 'claims': args[1], 'results': args[2], 'scope_declaration': '合并'}
    attach_coverage_statistics(payload)
    stat = payload['coverage_statistics']
    assert stat['core']['conditional_judged'] == 0
    assert stat['core']['source_supported_judgement_coverage'] == 1


def test_conditional_abstentions_pending_and_auxiliary_categories_remain_separate():
    args = population(record('unknown', 'abstained'), record('pending', 'pending'),
                      record('industry', 'verified', 'industry_historical'))
    for verdict in args[2]:
        verdict['source_dimensions'] = {'scope_provenance': 'user_supplied_scope'}
    stat = coverage_statistics(*args)
    core = stat['core']
    assert core['conditional_counts']['abstained'] == 1
    assert core['conditional_counts']['pending'] == 1
    assert core['conditional_judged'] == 0
    assert core['pending'] == 1
    assert stat['unconditional_abstention_target_met'] is False
    assert stat['groups']['industry_historical']['conditional_judged'] == 1
    assert stat['groups']['industry_historical']['source_supported_judgement_coverage'] == 0


def test_conditional_occurrence_counts_are_not_deduplicated_and_empty_core_cannot_pass():
    args = population(record('same', 'verified'))
    args[2][0]['source_dimensions'] = {'scope_provenance': 'user_supplied_scope'}
    args[0]['mentions'].append(dict(args[0]['mentions'][0], id='other-occurrence'))
    stat = coverage_statistics(*args)
    assert stat['core']['unique_assertions'] == 1
    assert stat['core']['conditional_counts']['verified'] == 2
    assert stat['core']['conditional_judged'] == 2
    empty = coverage_statistics({}, [], [])
    assert empty['core']['conditional_counts'] == dict.fromkeys(
        ('verified', 'violated', 'abstained', 'pending', 'out_of_scope', 'rule_excluded'), 0)
    assert empty['core']['source_supported_judgement_coverage'] is None
    assert empty['unconditional_abstention_target_met'] is False
