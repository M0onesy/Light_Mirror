"""Execute the report's embedded browser code, rather than only searching JSON."""
import copy
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from mingjing.report import html_report, markdown_report, write_reports


def _payload():
    operand = {'fact_id': 'ratio-fact', 'company': '甲', 'period': '2023Y', 'scope': '合并',
               'nr': '未扣非', 'account': 'GROSS_MARGIN', 'value': '0.25', 'display_unit': '%'}
    results = [{'claim_id': f'c{i}', 'status': 'abstained', 'claim_text': f'原文{i}',
                'abstention_code': f'X{i}', 'diagnostics': [f'X{i}'], 'advisories': [f'A{min(i,5)}'],
                'rule_ids': [f'RULE-{i}'], 'error_type': 'E8', 'reason': f'原因{i}',
                'observed': '0', 'expected': '0', 'tolerance': '0', 'formula': 'REGISTERED_FORMULA',
                'evidence': ['source-id'], 'operands': [operand], 'basis': ['冻结依据']}
               for i in range(1, 7)]
    return {'run_id': 'render-test', 'summary': {'verified': 0, 'violated': 0, 'abstained': 6},
            'model': {'status': 'fallback', 'error': '模型超时'}, 'partial': True,
            'draft_text': '</script><script>throw new Error("injected")</script>',
            'results': results,
            'coverage': {'partial': True, 'financial_candidates': 8, 'processed': 6,
                         'counts': {'pending': 2, 'abstained': 6}, 'recall': 'unknown_requires_independent_gold',
                         'mentions': [{'id': 'pending-id', 'text': '未处理表格数字', 'start': 10, 'end': 12,
                                       'page_id': 'page-42', 'status': 'pending', 'reason': 'budget_exhausted'},
                                      {'id': 'out-id', 'text': '2025E', 'start': 20, 'end': 25,
                                       'status': 'out_of_scope', 'reason': 'forecast_table_column'}]},
            'analysis': {'company': '甲', 'scope': '合并', 'periods': ['2023Y'],
                         'metrics': [{'period': '2023Y', 'metric': 'GROSS_MARGIN', 'status': 'verified', 'value': '0.25',
                                      'formula': '(REVENUE-COST)/REVENUE', 'operands': [operand], 'evidence': ['source-id']},
                                     {'period': '2023Y', 'metric': 'CASH_CONTENT', 'status': 'abstained',
                                      'value': None, 'abstention_code': 'X2', 'reason': '净利润非正'}],
                         'signals': [{'kind': 'low_cash_content', 'code': 'A5', 'periods': ['2022Y', '2023Y'],
                                      'threshold': '0.5', 'parameter_source': 'team_parameter', 'evidence': ['source-id']}],
                         'events': [{'kind': 'accounting_policy_change', 'code': 'A5',
                                     'status': 'disclosure_event_requires_professional_review', 'evidence': ['source-id']}],
                         'parameters': {'source': 'team_parameter', 'cash_threshold': '0.5'}},
            'agent_report': '<script>alert("unsafe")</script>',
            'evidence_resources': {'source-id': {'image': 'evidence/page1.png'}}}


def _render_html_in_js(payload, tmp_path):
    node = shutil.which('node')
    if not node:
        pytest.skip('Node is used only to execute development HTML render checks')
    rendered = html_report(payload)
    script = rendered.split('<script>', 1)[1].rsplit('</script>', 1)[0]
    harness = r'''
const vm = require('node:vm');
const fs = require('node:fs');
const elements = {};
const element=()=>({innerHTML:'',textContent:'',hidden:false,before(node){this.beforeElement=node},appendChild(node){(this.children||=[]).push(node)}});
const document = {getElementById(id){return elements[id] ||= element()},querySelectorAll(){return []},createElement(){return element()},querySelector(name){return this.getElementById(name)}};
const context = {document};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),context);
const details = {};
for(const item of context.output.results){context.show(item.claim_id);details[item.claim_id] = elements.detail.innerHTML;}
process.stdout.write(JSON.stringify({data:context.output,elements,details}));
'''
    code_path, runner = tmp_path/'report.js', tmp_path/'runner.cjs'
    code_path.write_text(script+'\noutput = D;', encoding='utf-8')
    runner.write_text(harness, encoding='utf-8')
    executed = subprocess.run([node, str(runner), str(code_path)], capture_output=True,
                              text=True, encoding='utf-8', timeout=10, check=True)
    return json.loads(executed.stdout)


def test_html_and_markdown_display_partial_coverage_analysis_and_codes(tmp_path):
    payload = _payload()
    original = copy.deepcopy(payload)
    md = markdown_report(payload)
    browser = _render_html_in_js(payload, tmp_path)
    assert browser['data'] == payload and payload == original
    assert 'partial' in md and 'partial' in browser['elements']['coverage-summary']['textContent']
    for value in ('pending-id', '未处理表格数字', 'budget_exhausted', 'out-id', 'forecast_table_column'):
        assert value in md and value in browser['elements']['coverage-ledger']['innerHTML']
    for value in ('GROSS_MARGIN', '0.25', '(REVENUE-COST)/REVENUE', 'CASH_CONTENT', 'X2',
                  'low_cash_content', 'A5', 'accounting_policy_change', 'team_parameter'):
        assert value in md and value in browser['elements']['analysis']['innerHTML']
    for result in payload['results']:
        view = browser['details'][result['claim_id']]
        for value in (result['abstention_code'], *result['advisories'], *result['rule_ids'], result['formula'], 'source-id'):
            assert value in md and value in view
        assert '观测值：0' in view and '容差：0' in view  # Zero cannot become an em dash.
    assert '0.25 比率' in md and '0.25 比率' in browser['details']['c1']
    assert '0.25 元' not in md


def test_report_sources_inert_and_evidence_does_not_open_external_network(tmp_path):
    payload = _payload()
    payload['evidence_resources']['source-id']['image'] = 'javascript:alert(1)'
    browser = _render_html_in_js(payload, tmp_path)
    detail = browser['details']['c1']
    assert 'href="javascript:' not in detail and '<img' not in detail
    assert 'javascript:alert' not in markdown_report(payload)
    assert '<script>' not in browser['elements']['source']['innerHTML']
    assert '&lt;script&gt;' in browser['elements']['source']['innerHTML']
    assert browser['elements']['agent-report']['textContent'] == payload['agent_report']


def test_write_reports_keeps_original_json_and_five_correction_fields(tmp_path):
    payload = _payload()
    correction = {'correct_value':'120', 'source':'source-id', 'calculation':'120 / 1',
                  'basis':'E1：原披露', 'replacement_text':'2023年营业收入为120元。'}
    payload['results'][0]['correction'] = correction
    saved = tmp_path/'results.json'
    saved.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    original = saved.read_bytes()
    write_reports(payload, tmp_path)
    assert saved.read_bytes() == original
    browser = _render_html_in_js(payload, tmp_path)
    md = (tmp_path/'report.md').read_text(encoding='utf-8')
    for value in correction.values():
        assert value in md and value in browser['details']['c1']


def test_blocker_chain_and_registered_units_are_consistent_in_reports(tmp_path):
    payload = _payload()
    result = payload['results'][0]
    result['source_dimensions'] = {'dimension_kind': 'business', 'dimension_id': '超算云服务',
                                   'definition_id': 'FIN-current-ratio'}
    result['blockers'] = [{'code': 'X1', 'kind': 'operand_missing', 'reason': '缺少期初应收账款',
                          'account': 'RECEIVABLES', 'period': '2022Y'}]
    result['operands'][0].update(account='CURRENT_RATIO', value='0.8', display_unit='倍')
    original = copy.deepcopy(payload)
    view = _render_html_in_js(payload, tmp_path)['details']['c1']
    md = markdown_report(payload)
    for fragment in ('超算云服务', 'FIN-current-ratio', '缺少期初应收账款', 'RECEIVABLES', '0.8 倍'):
        assert fragment in view and fragment in md
    assert payload == original


def test_integrated_financial_coverage_is_shared_by_markdown_and_browser(tmp_path):
    from mingjing.coverage_stats import attach_coverage_statistics, PRIMARY_SCOPE
    from test_coverage_statistics import population, record
    payload = _payload()
    coverage, claims, verdicts = population(record('core', 'verified'), record('missing', 'abstained'),
                                           record('industry', 'abstained', 'industry_historical'))
    payload.update(coverage=coverage, claims=claims, results=verdicts)
    attach_coverage_statistics(payload)
    view = _render_html_in_js(payload, tmp_path)
    assert view['data']['coverage_statistics'] == payload['coverage_statistics']
    summary = view['elements']['coverage-summary']['textContent']
    assert '处理完成率 100.0%' in summary and '有效判定覆盖率 50.0%' in summary
    assert '主范围 2 处' in summary and '待处理 0' in summary
    assert '核实 1 / 违背 0 / 弃权 1' in view['elements']['summary']['textContent']
    pane = view['elements']['list']['beforeElement']['innerHTML']
    assert '公司历史财务' in pane and '行业历史事实' in pane and '3 个出现位置' in pane
    markdown = markdown_report(payload)
    assert '有效判定覆盖率 50.0%' in markdown and '行业历史事实' in markdown


def _analysis_payload():
    payload = _payload()
    payload.update(task_type='analysis', results=[], claims=[], partial=False,
                   summary={'verified': 98, 'violated': 99, 'abstained': 100},
                   coverage={'financial_candidates': 0, 'processed': 0, 'counts': {}, 'mentions': []})
    return payload


def test_analysis_report_counts_metrics_without_rewriting_claim_summary(tmp_path):
    payload = _analysis_payload()
    original = copy.deepcopy(payload)
    markdown = markdown_report(payload)
    rendered = html_report(payload)
    browser = _render_html_in_js(payload, tmp_path)
    header = browser['elements']['summary']['textContent']
    assert markdown.startswith('# 明镜财报分析报告\n')
    assert '<title>明镜财报分析报告</title>' in rendered
    for text in ('指标计算项 2；可用 1；发现差异 0；弃权 1', '指标弃权码：X2 1', '财务复核提示 1；披露事件 1'):
        assert text in markdown and text in header
    assert '核实 98' not in markdown and '核实 98' not in header
    assert '原文覆盖账本' not in markdown and '金融候选提及 0' not in markdown
    assert browser['elements']['coverage-panel']['hidden']
    assert '金融候选 0' not in browser['elements']['coverage-summary']['textContent']
    assert browser['elements']['analysis-panel']['open']
    assert browser['elements']['results-heading']['textContent'] == '财报分析'
    assert browser['elements']['source-heading']['textContent'] == '材料与任务说明'
    assert '公式、操作数与证据' in browser['elements']['detail']['textContent']
    assert browser['data'] == original and payload == original


def test_analysis_partial_unknown_metrics_and_supplemental_draft_coverage_stay_visible(tmp_path):
    payload = _analysis_payload()
    payload['partial'] = True
    payload['analysis']['metrics'].append({'metric': 'REVENUE', 'status': 'pending', 'period': '2024Y'})
    payload['coverage'] = _payload()['coverage']
    markdown = markdown_report(payload)
    browser = _render_html_in_js(payload, tmp_path)
    assert '指标计算项 3；可用 1；发现差异 0；弃权 1' in markdown
    assert '其他状态指标 1' in markdown and '其他状态指标 1' in browser['elements']['summary']['textContent']
    assert '部分任务阶段尚未完成' in markdown
    assert '候选提及或表格单元格未处理' not in markdown
    assert 'pending-id' in markdown
    assert not browser['elements']['coverage-panel']['hidden']
    assert 'partial' in browser['elements']['coverage-summary']['textContent']


def test_empty_analysis_does_not_claim_completion(tmp_path):
    payload = _analysis_payload()
    payload['analysis'].update(metrics=[], signals=[], events=[])
    markdown = markdown_report(payload)
    browser = _render_html_in_js(payload, tmp_path)
    assert '尚无冻结分析指标' in markdown
    assert '不能据此宣称分析已完成' in markdown
    assert '不能据此宣称分析完成' in browser['elements']['coverage-summary']['textContent']
    assert '当前候选提及处理完成' not in browser['elements']['coverage-summary']['textContent']


def test_html_highlight_uses_frozen_codepoints_after_non_bmp_text(tmp_path):
    text = '😀𠮷公司：收入10亿元。尾部🚀'
    payload = _payload()
    payload.update(draft_text=text, results=[{'claim_id': 'income', 'status': 'verified',
        'claim_text': '收入10亿元', 'source_span': {'start': 5, 'end': 11}}])
    before = copy.deepcopy(payload)
    browser = _render_html_in_js(payload, tmp_path)
    source = browser['elements']['source']['innerHTML']
    assert '<mark class="verified" data-id="income">收入10亿元</mark>' in source
    assert re.sub(r'<[^>]*>', '', source) == text
    assert browser['data'] == before and payload == before


def test_html_ignores_invalid_spans_and_preserves_all_original_codepoints(tmp_path):
    text = '😀𠮷公司：收入10亿元。尾部🚀'
    payload = _payload()
    spans = [{'start': 0, 'end': 0}, {'start': 1, 'end': 0}, {'start': -1, 'end': 2},
             {'start': 0.5, 'end': 2}, {'start': 14, 'end': 18}]
    payload.update(draft_text=text, results=[{'claim_id': f'invalid-{i}', 'status': 'verified',
        'source_span': span} for i, span in enumerate(spans)])
    browser = _render_html_in_js(payload, tmp_path)
    assert browser['elements']['source']['innerHTML'] == text
