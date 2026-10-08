"""Real dispatcher regressions; these do not claim live-model acceptance."""
import copy
import json
from decimal import Decimal

import pytest

from mingjing.analysis import analyze_financials
from mingjing.financial_tools import ANALYSIS_METRICS, FinancialDispatcher, ToolDenied, tool_definitions
from mingjing.models import Fact
from test_agent_guards import call


def financial_task(task_period='2024Y'):
    facts = [Fact('a-' + period, '甲公司', 'REVENUE', period, value=Decimal(value),
                  publish_date='2025-03-01', source_doc='alpha-doc', evidence=('alpha:p1:' + period,))
             for period, value in [('2021Y', '40'), ('2022Y', '60'), ('2023Y', '80'), ('2024Y', '100')]]
    facts.append(Fact('foreign', '乙公司', 'REVENUE', '2024Y', value=Decimal('999999'),
                      publish_date='2025-03-01', source_doc='beta-doc', evidence=('beta:p2',)))
    d = FinancialDispatcher('run', 'lock', [], facts, [], [], '2025-03-31',
                            task_company='甲公司', task_period=task_period)
    d.register_session('question', 'question')
    d.register_session('reporter', 'reporter')
    d.freeze_analysis(analyze_financials(d.store, '甲公司', ['2021Y', '2022Y', '2023Y', '2024Y'], '2025-03-31'))
    return d


def test_registered_metric_directory_is_discoverable_without_amounts():
    d = financial_task()
    definition = next(tool for tool in tool_definitions('question') if tool['name'] == 'mj_analyze')
    assert set(definition['inputSchema']['properties']['metric']['enum']) == set(ANALYSIS_METRICS)
    assert {'REVENUE_YOY', 'NP_PARENT_YOY', 'CFO_YOY', 'EPS_DILUTED'} <= set(ANALYSIS_METRICS)
    before = copy.deepcopy(d.analysis_snapshots)
    catalog = call(d, 'question', 'mj_analyze', {'analysis_id': 'financial_summary', 'catalog_only': True})
    revenue_yoy = next(row for row in catalog['registered_metrics'] if row['metric'] == 'REVENUE_YOY')
    assert revenue_yoy == {'metric': 'REVENUE_YOY', 'label': '营业收入同比', 'kind': 'computed',
                           'rule_ids': ['E5-yoy', 'TIME-same-report']}
    assert not any('value' in row or 'operands' in row for row in catalog['registered_metrics'])
    assert catalog['analysis'] == [] and catalog['slots'] == {}
    assert '999999' not in json.dumps(catalog, ensure_ascii=False)
    assert d.analysis_snapshots == before


def test_analysis_followup_empty_verdict_then_comparison_closes_with_frozen_slots():
    d = financial_task()
    before = copy.deepcopy(d.analysis_snapshots)
    empty = call(d, 'question', 'mj_get_verdicts', {}, 'verdicts')
    assert empty['reason'] == 'no_draft_verdicts'
    assert empty['next_action']['tool'] == 'mj_analyze'
    assert 'slot_template' in empty['slot_instruction']
    result = call(d, 'question', 'mj_analyze', {'analysis_id': 'financial_summary',
                  'period': '2024Y', 'metric': 'REVENUE_YOY'}, 'comparison')
    record = result['analysis'][0]['record']
    assert record['status'] == 'verified' and record['value'] == '0.25'
    assert record['base_period'] == '2023Y' and record['rule_id'] == 'E5-yoy'
    assert [item['value'] for item in record['operands']] == ['100', '80']
    assert record['evidence'] == ['alpha:p1:2024Y', 'alpha:p1:2023Y']
    assert result['selection']['page_periods'] == ['2024Y']
    accepted = call(d, 'question', 'mj_submit_report', {'template': result['slot_template'],
                    'slots': result['slot_ids']}, 'answer')
    assert accepted['accepted'] and record['display_value'] in d.templates['question']
    assert record['display_precision_status'] == 'interval_range'
    assert '24.98' in record['display_value'] and '25.02' in record['display_value']
    assert '999999' not in d.templates['question'] and '乙公司' not in d.templates['question']
    assert d.analysis_snapshots == before
    assert [event['tool'] for event in d.trace] == ['mj_get_verdicts', 'mj_analyze', 'mj_submit_report']


@pytest.mark.parametrize('task_period,expected,mode', [('2023Y', '2023Y', 'task_period'),
                                                    (None, '2024Y', 'latest_frozen')])
def test_reporter_default_period_and_explicit_history_are_visible_and_bounded(task_period, expected, mode):
    d = financial_task(task_period)
    before = copy.deepcopy(d.analysis_snapshots)
    page = call(d, 'reporter', 'mj_analyze', {'analysis_id': 'financial_summary'}, 'current')
    assert page['selection']['selected_period'] == expected and page['selection']['mode'] == mode
    assert page['returned'] <= 5 and page['selection']['page_periods'] == [expected]
    assert {item['record']['period'] for item in page['analysis']} == {expected}
    history = call(d, 'reporter', 'mj_analyze', {'analysis_id': 'financial_summary',
                  'period': '2021Y', 'metric': 'REVENUE'}, 'history')
    assert history['selection']['mode'] == 'explicit' and history['analysis'][0]['record']['value'] == '40'
    assert d.analysis_snapshots == before
    assert len(json.dumps(page, ensure_ascii=False).encode('utf-8')) <= 16384


def test_unavailable_task_period_does_not_silently_substitute_history():
    d = financial_task('2025Y')
    empty = call(d, 'reporter', 'mj_analyze', {'analysis_id': 'financial_summary'})
    assert empty['analysis'] == [] and empty['selection']['selected_period'] == '2025Y'
    assert empty['reason'] == 'no_registered_analysis_matches'
    assert empty['next_action']['arguments']['catalog_only']
    assert empty['selection']['available_periods'] == ['2021Y', '2022Y', '2023Y', '2024Y']


def test_report_structure_has_only_one_correction_and_keeps_amount_guard():
    d = financial_task()
    result = call(d, 'question', 'mj_analyze', {'analysis_id': 'financial_summary', 'metric': 'REVENUE_YOY'}, 'read')
    with pytest.raises(ToolDenied, match='registered headings'):
        call(d, 'question', 'mj_submit_report', {'template': result['slot_template'] + '\n营收同比25%',
                                              'slots': result['slot_ids']}, 'wrong')
    assert call(d, 'question', 'mj_submit_report', {'template': result['slot_template'],
                'slots': result['slot_ids']}, 'correct')['accepted']
    bad = financial_task()
    for index in range(2):
        with pytest.raises(ToolDenied):
            call(bad, 'question', 'mj_submit_report', {'template': '伪造999999元', 'slots': []}, str(index))
    with pytest.raises(ToolDenied, match='one correction only'):
        call(bad, 'question', 'mj_submit_report', {'template': '# 财报分析', 'slots': []}, 'again')
    assert 'question' not in bad.templates


def test_question_cannot_submit_empty_or_inherited_but_unread_slots():
    d = financial_task()
    catalog = call(d, 'question', 'mj_analyze', {'analysis_id': 'financial_summary', 'catalog_only': True}, 'catalog')
    assert not catalog['slot_ids']
    with pytest.raises(ToolDenied, match='at least one frozen slot'):
        call(d, 'question', 'mj_submit_report', {'template': '# 财报分析', 'slots': []}, 'empty')
    previous = next(slot for slot in d.slots if slot.endswith('.text'))
    d.sessions['question']['revealed_slots'].add(previous)
    with pytest.raises(ToolDenied, match='read in this turn'):
        call(d, 'question', 'mj_submit_report', {'template': '{{'+previous+'}}', 'slots': [previous]}, 'unread')
    assert 'question' not in d.templates


def test_metric_tool_cannot_accept_a_model_amount_company_or_free_formula():
    d = financial_task()
    before = copy.deepcopy(d.analysis_snapshots)
    for key, value in [('candidate_value', '999999'), ('company', '乙公司'), ('formula', 'current/base')]:
        with pytest.raises(Exception):
            call(d, 'question', 'mj_analyze', {'analysis_id': 'financial_summary', 'metric': 'REVENUE_YOY',
                                              key: value}, key)
    assert d.analysis_snapshots == before
