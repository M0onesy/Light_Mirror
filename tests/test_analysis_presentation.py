import copy
import json
from dataclasses import replace
from decimal import Decimal, localcontext

import pytest

from mingjing.financial_tools import FinancialDispatcher, ToolDenied
from mingjing.judge import Judge
from mingjing.mentions import extract_mentions
from mingjing.models import Fact
from mingjing.presentation import analysis_record_text, display_standard_value, metric_display_metadata, display_metric_value
from test_agent_guards import call, fixture


def test_readable_metric_preserves_standard_amount_unit_formula_and_citations():
    record = {'company': '甲公司', 'scope': '合并', 'period': '2024Y', 'metric': 'REVENUE',
        'status': 'verified', 'value': '1000000000', 'formula': '原报告披露值', 'evidence': ['doc:p1:span'],
        'operands': [{'fact_id': 'fact-1', 'account': 'REVENUE', 'period': '2024Y', 'scope': '合并',
            'value': '1000000000', 'display_unit': '亿元', 'source_doc': 'doc',
            'publish_date': '2025-03-01', 'evidence': ['doc:p1:span']}]}
    before = copy.deepcopy(record)
    text = analysis_record_text('metrics', record)
    assert '## 营业收入' in text and '2024年' in text and '口径：合并' in text
    assert '1000000000 元' in text and '原披露单位：亿元' in text
    assert '1000000000 亿元' not in text
    assert '公式：原报告披露值' in text and '操作数：' in text
    assert 'doc\\:p1\\:span' in text and 'fact\\-1' in text
    assert record == before


@pytest.mark.parametrize('metric,value,expected', [
    ('GROSS_MARGIN', '0.30', '30%（标准值 0.3，无量纲比例）'),
    ('REVENUE_YOY', '-0.1250', '-12.5%（标准值 -0.125，无量纲比例）'),
    ('NP_PARENT_YOY', '1.200', '120%（标准值 1.2，无量纲比例）'),
    ('CFO_YOY', '0', '0%（标准值 0，无量纲比例）'),
    ('EPS', '0.37', '0.37 元/股'),
])
def test_metric_display_uses_registered_units_including_yoy(metric, value, expected):
    record = {'metric': metric, 'value': value, 'period': '2024Y', 'status': 'verified',
              'formula': '(current-base)/base', 'operands': [], 'evidence': []}
    assert expected in analysis_record_text('metrics', record)
    with localcontext() as context:
        context.prec = 2
        assert display_standard_value('0.123456789012345678901234567890', '无量纲比例') == (
            '12.345678901234567890123456789%（标准值 0.12345678901234567890123456789，无量纲比例）')


def test_missing_metric_remains_abstained_and_never_displays_zero():
    record = {'metric': 'CASH_CONTENT', 'status': 'abstained', 'value': None, 'period': '2024Y',
              'formula': 'CFO/NP', 'reason': '缺操作数或分母非正', 'abstention_code': 'X2', 'evidence': []}
    text = analysis_record_text('metrics', record)
    assert '数值：未计算' in text and '状态：弃权' in text
    assert 'X2' in text and '缺操作数或分母非正' in text and '需要补充可定位材料' in text
    assert '数值：0' not in text


def test_signal_is_a_registered_review_prompt_with_existing_threshold_source():
    record = {'kind': 'non_recurring_share', 'company': '甲公司', 'scope': '合并', 'periods': ['2024Y'],
              'value': '0.4', 'threshold': '0.3', 'parameter_source': 'team_parameter',
              'rule_id': 'FIN-non-recurring-share', 'evidence': ['evidence-1']}
    text = analysis_record_text('signals', record)
    assert '复核提示' in text and '不构成投资建议或舞弊判断' in text
    assert '登记阈值：30%（标准值 0.3，无量纲比例）' in text
    assert '团队登记参数' in text and 'FIN\\-non\\-recurring\\-share' in text
    without_threshold = analysis_record_text('signals', dict(record, threshold=None))
    assert '不能补写阈值' in without_threshold and '登记阈值：30%' not in without_threshold


def test_source_fields_cannot_become_markdown_links_html_or_slots():
    hostile = '[来源](https://evil.example/) <img src=x onerror=alert(1)> {{foreign.result}}'
    record = {'kind': 'accounting_policy_change', 'company': hostile, 'scope': '合并',
              'source_text': hostile, 'reason': hostile, 'evidence': [hostile]}
    text = analysis_record_text('events', record)
    assert '{{foreign.result}}' not in text and '\\<img' in text and '](' not in text
    mistune = pytest.importorskip('mistune')
    rendered = mistune.create_markdown(escape=False, plugins=['url'])(text)
    assert '<a ' not in rendered and '<img' not in rendered and '<script' not in rendered


def test_analysis_text_slots_are_default_while_raw_records_require_authorized_reads():
    d, _ = fixture()
    analysis = {'company': '甲公司', 'scope': '合并', 'periods': ['2024Y'], 'parameters': {},
        'metrics': [{'metric': 'REVENUE', 'period': '2024Y', 'status': 'verified', 'value': '100',
                     'formula': '原报告披露值', 'operands': [], 'evidence': ['e']}], 'signals': [], 'events': []}
    before = copy.deepcopy(analysis)
    d.freeze_analysis(analysis)
    raw = d.analysis_prefix(analysis) + '.metrics.0'
    original_json = d.slots[raw]
    with pytest.raises(ToolDenied, match='not read'):
        call(d, 'reporter', 'mj_submit_report', {'template': '{{'+raw+'}}', 'slots': [raw]}, 'raw-unread')
    result = call(d, 'reporter', 'mj_analyze', {'analysis_id': 'financial_summary'}, 'read')
    assert result['slot_ids'] == [raw+'.text']
    assert result['analysis'][0]['raw_slot_id'] == raw
    assert result['analysis'][0]['record'] == json.loads(original_json)
    assert all(not value.startswith('{') for value in result['slots'].values())
    assert raw not in d.sessions['reporter']['revealed_slots']
    with pytest.raises(ToolDenied, match='not read'):
        call(d, 'reporter', 'mj_submit_report', {'template': '{{'+raw+'}}', 'slots': [raw]}, 'raw-still-unread')
    assert call(d, 'reporter', 'mj_submit_report', {'template': result['slot_template'],
             'slots': result['slot_ids']}, 'text-submit')['accepted']
    assert '营业收入' in d.templates['reporter'] and '100 元' in d.templates['reporter']
    assert d.slots[raw] == original_json and analysis == before


def _mixed_facts():
    return [Fact('alpha', '甲公司', 'REVENUE', '2024Y', value=Decimal('100'), publish_date='2025-03-01'),
            Fact('beta', '乙公司', 'REVENUE', '2024Y', value=Decimal('999'), publish_date='2025-03-01')]


def test_question_analysis_and_dimensions_never_reveal_another_task_company():
    claims = extract_mentions('甲公司合并口径2024年营业收入100元。', '甲公司')
    reports = [{'report_id': 'alpha-doc', 'companies': ['甲公司'], 'publish_date': '2025-03-01', 'sha256': 'a'*64},
               {'report_id': 'beta-doc', 'companies': ['乙公司'], 'publish_date': '2025-03-01', 'sha256': 'b'*64},
               {'report_id': 'fixture-no-company'}]
    blocks = [{'id': 'a', 'report_id': 'alpha-doc', 'text': '甲公司营业收入100'},
              {'id': 'b', 'report_id': 'beta-doc', 'text': '乙公司营业收入999'}]
    d = FinancialDispatcher('run', 'lock', claims, _mixed_facts(), blocks, reports, '2025-03-31')
    d.register_session('question', 'question')
    d.register_session('planner', 'planner')
    assert d.task_company == '甲公司' and {f.company for f in d.store.facts} == {'甲公司'}
    result = call(d, 'question', 'mj_analyze', {'analysis_id': 'financial_summary', 'metric': 'REVENUE'})
    assert {item['record']['company'] for item in result['analysis']} == {'甲公司'}
    assert result['analysis'][0]['record']['value'] == '100'
    dimensions = call(d, 'planner', 'mj_fact_dimensions', {'mention_id': claims[0].claim_id})
    assert dimensions['companies'] == ['甲公司']
    selected_reports = call(d, 'question', 'mj_list_reports', {}, 'reports')
    assert {r['report_id'] for r in selected_reports} == {'alpha-doc', 'fixture-no-company'}
    hits = call(d, 'question', 'mj_search_blocks', {'query': '营业收入'}, 'search')
    assert {row['block_id'] for row in hits} == {'a'}
    assert '999' not in json.dumps(result, ensure_ascii=False)
    with pytest.raises(ToolDenied, match='task company'):
        d.freeze_analysis({'company': '乙公司', 'periods': ['2024Y'], 'metrics': []})


def test_empty_claims_mixed_facts_need_a_host_company_not_tool_arguments():
    d = FinancialDispatcher('run', 'lock', [], _mixed_facts(), [], [], '2025-03-31')
    d.register_session('question', 'question')
    assert d.task_company == '' and not d.store.facts
    with pytest.raises(ToolDenied, match='trusted task company'):
        call(d, 'question', 'mj_analyze', {'analysis_id': 'financial_summary'})
    explicit = FinancialDispatcher('run', 'lock', [], _mixed_facts(), [], [], '2025-03-31', task_company='甲公司')
    explicit.register_session('question', 'question')
    result = call(explicit, 'question', 'mj_analyze', {'analysis_id': 'financial_summary', 'metric': 'REVENUE'})
    assert result['analysis'][0]['record']['company'] == '甲公司'
    with pytest.raises(Exception):
        call(explicit, 'question', 'mj_analyze', {'analysis_id': 'financial_summary', 'task_company': '乙公司'}, 'spoof')


def test_foreign_snapshot_records_or_operands_are_rejected_before_slots_are_published():
    d = FinancialDispatcher('run', 'lock', [], _mixed_facts(), [], [], '2025-03-31', task_company='甲公司')
    record = {'metric': 'REVENUE', 'period': '2024Y', 'status': 'verified', 'value': '999', 'company': '乙公司'}
    for foreign in (record, dict(record, company='甲公司', operands=[{'company': '乙公司', 'value': '999'}])):
        with pytest.raises(ToolDenied, match='task company'):
            d.freeze_analysis({'company': '甲公司', 'scope': '合并', 'periods': ['2024Y'], 'metrics': [foreign]})
        assert not d.slots and not d.analysis_snapshots


def test_peer_source_numbers_stay_unknown_instead_of_task_company_truth():
    claims = extract_mentions('乙公司合并口径2024年营业收入999元。', '甲公司')
    d = FinancialDispatcher('run', 'lock', claims, _mixed_facts(), [], [], '2025-03-31', task_company='甲公司')
    claim = next(iter(d.claims.values()))
    result = Judge(d.store, '2025-03-31').verify(claim)
    assert result.status == 'abstained' and result.abstention_code == 'X3'
    assert not result.expected


def _derived_metric(value, lower, upper, *, lower_closed=True, upper_closed=False):
    return {'metric':'REVENUE_YOY', 'value':value, 'status':'verified',
            'verification_basis':'authorized_formula_interval_propagation',
            'expected_interval':{'lower':lower,'upper':upper,
                                 'lower_closed':lower_closed,'upper_closed':upper_closed}}


def test_derived_display_uses_source_interval_and_never_publishes_sixty_four_digits():
    raw='0.5847453994101539733459958075464185082978624917186549385299747642'
    record=_derived_metric(raw,'0.584745398','0.584745401')
    original=copy.deepcopy(record)
    view=metric_display_metadata(record)
    assert view['display_value']=='约58.47%'
    assert view['display_precision_status']=='interval_stable_rounded'
    assert '整个来源区间' in view['display_precision_note']
    text=analysis_record_text('metrics',record)
    assert '约58.47%' in text and raw not in text
    assert record==original and record['value']==raw


@pytest.mark.parametrize('lower,upper,closed,expected',[
    ('0.58465','0.58475',(True,False),'约58.47%'),
    ('-0.58475','-0.58465',(False,True),'约-58.47%'),
    ('0.58465','0.58475',(True,True),'区间 [58.46, 58.48]%'),
    ('0.19','0.21',(True,False),'区间 [19.00, 21.00]%'),
    ('0.25004999','0.25005001',(True,False),'区间 [25.00, 25.01]%'),
])
def test_interval_display_accounts_for_open_rounding_boundaries_and_negative_values(lower,upper,closed,expected):
    record=_derived_metric(lower,lower,upper,lower_closed=closed[0],upper_closed=closed[1])
    with localcontext() as context:
        context.prec=2
        assert display_metric_value(record)==expected


@pytest.mark.parametrize('metric,value,unit,decimals,expected',[
    ('REVENUE','1000000000','亿元',2,'10.00 亿元'),
    ('REVENUE','123456789.12','元',2,'123456789.12 元'),
    ('ROE','0.1830','%',2,'18.30%'),
    ('EPS','0.370','元',3,'0.370 元/股'),
])
def test_direct_disclosure_preserves_its_original_unit_and_printed_precision(metric,value,unit,decimals,expected):
    fact={'fact_id':'f','account':metric,'value':value,'display_unit':unit,'decimals':decimals}
    record={'metric':metric,'value':value,'formula':'原报告披露值','operands':[fact]}
    with localcontext() as context:
        context.prec=2
        view=metric_display_metadata(record)
    assert view['display_value']==expected and view['display_precision']==decimals
    assert view['display_precision_status']=='source_disclosure'


def test_missing_or_inconsistent_source_precision_is_visible_and_does_not_gain_precision():
    record={'metric':'REVENUE_YOY','value':'0.5847453994101539733459958075464185'}
    assert display_metric_value(record)=='约58.47%'
    assert metric_display_metadata(record)['display_precision_status']=='source_precision_unknown'
    invalid={'metric':'REVENUE','value':'123.456','formula':'原报告披露值',
        'operands':[{'fact_id':'f','account':'REVENUE','value':'123.456','display_unit':'元','decimals':2}]}
    assert metric_display_metadata(invalid)['display_precision_status']=='source_precision_unknown'
    assert '来源披露精度未提供' in analysis_record_text('metrics',invalid)
    wrong_unit={'metric':'ROE','value':'.1830','formula':'原报告披露值',
        'operands':[{'fact_id':'f','account':'ROE','value':'.1830','display_unit':'元','decimals':4}]}
    assert metric_display_metadata(wrong_unit)['display_precision_status']=='source_precision_unknown'
    assert '元' not in display_metric_value(wrong_unit)


def test_analysis_serializes_readable_display_and_keeps_the_full_derived_decimal():
    from mingjing.analysis import analyze_financials
    from mingjing.facts import FactStore
    facts=[Fact('current','甲公司','REVENUE','2024Y',value=Decimal('120.00'),
                publish_date='2025-03-01',source_doc='annual'),
           Fact('base','甲公司','REVENUE','2023Y',value=Decimal('80.00'),
                publish_date='2025-03-01',source_doc='annual')]
    result=analyze_financials(FactStore(facts),'甲公司',['2024Y'],'2025-03-31')
    revenue=next(row for row in result['metrics'] if row['metric']=='REVENUE')
    growth=next(row for row in result['metrics'] if row['metric']=='REVENUE_YOY')
    assert revenue['value']=='120.00' and revenue['display_value']=='120.00 元'
    assert growth['value']=='0.5' and growth['display_precision_status']=='interval_range'
    assert growth['display_value']==display_metric_value(growth)
    assert all('display_value' in row and 'display_precision_status' in row for row in result['metrics'])
    assert '显示精度：' in analysis_record_text('metrics',growth)


def test_reports_resolve_legacy_metric_precision_without_mutating_frozen_value():
    from mingjing.report import html_report, markdown_report
    raw = '0.5847453994101539733459958075464185082978624917186549385299747642'
    metric = dict(_derived_metric(raw, '0.584745398', '0.584745401'), period='2024Y')
    payload = {'task_type': 'analysis', 'analysis': {'metrics': [metric]}, 'results': []}
    frozen = copy.deepcopy(payload)
    markdown, html = markdown_report(payload), html_report(payload)
    assert '- 数值：约58.47%' in markdown
    assert '"display_value": "约58.47%"' in html and 'm.display_value??' in html
    assert '展示精度说明' in html and 'display_precision_note' in html
    assert raw in html  # Raw JSON remains available for audit, not main display.
    assert payload == frozen
