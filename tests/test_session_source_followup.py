"""Controlled native turns with real PDF quotation persistence and lookup."""
import copy
import csv
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from mingjing import sessions as sessions_module
from mingjing.session_evidence import load_source_evidence
from mingjing.evidence import sha256_file
from test_agent_lifecycle import lifecycle, new_agent, read


def conversation(lifecycle):
    lifecycle.request['task_type'] = 'analysis'
    Path(lifecycle.request['draft']).write_text('财报分析任务。', encoding='utf-8')
    lifecycle.boundary.cancel_first_binding = False
    result = new_agent(lifecycle).run()
    assert result['model']['status'] == 'success', result['model']['error']

    class ConfigBoundary:
        def get(self):
            return copy.deepcopy(lifecycle.config)

        def secret(self, *_):
            raise AssertionError('No live API credentials in controlled source quotation tests')

    run = SimpleNamespace(out=lifecycle.out, request=lifecycle.request)
    sessions = sessions_module.FinancialSessions(lifecycle.out.parent/'quote-conversations', lambda _: run, ConfigBoundary())
    sid = sessions.create('lifecycle-fixture')['session_id']
    sessions.cancel_events[sid] = threading.Event()
    return sessions, sid, result


def register_pdf_disclosure(pdf):
    # Execution years inside policy prose are not publication dates. This
    # synthetic external index explicitly supplies the fixture's disclosure.
    with (pdf.parent/'index.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['file','name','code','year','category','date','size_bytes','sha256'])
        writer.writeheader()
        writer.writerow({'file': pdf.name, 'name': '甲公司', 'code': '', 'year': '2024', 'category': '年报',
                         'date': '2025-03-01', 'size_bytes': pdf.stat().st_size, 'sha256': sha256_file(pdf)})


def test_policy_original_question_submits_quote_and_persists_verified_page_proof(lifecycle):
    fitz = pytest.importorskip('fitz')
    pdf = Path(lifecycle.request['corpus']).parent/'policy.pdf'
    document = fitz.open()
    page = document.new_page()
    page.insert_textbox(fitz.Rect(40, 40, 520, 250),
        '会计政策变更\n本公司自2024年执行新会计政策，调整金额100万元。', fontname='china-s', fontsize=12)
    document.save(pdf)
    document.close()
    register_pdf_disclosure(pdf)
    lifecycle.request['corpus_additions'] = [str(pdf)]
    sessions, sid, result = conversation(lifecycle)
    old_files = {path: path.read_bytes() for path in lifecycle.out.rglob('*') if path.is_file()}
    quoted = []

    def quote_plan(runtime, spec, prompt, call):
        assert '会计政策变更' in prompt and '不得以营业收入' in prompt and '未核查引用' in prompt
        hits = call('mj_search_blocks', {'query': '会计政策变更', 'limit': 2})
        selected = next(hit for hit in hits if hit.get('source_references'))
        assert '100万元' in selected['text']
        quoted.extend(runtime.owner.dispatcher.source_references[row['slot_id']]
                      for row in selected['source_references'])
        call('mj_submit_report', {'template': selected['slot_template'], 'slots': selected['slot_ids']})

    lifecycle.boundary.question_plan = quote_plan
    sessions._answer_inner(sid, '请给出会计政策变更原文及来源。')
    state = sessions.get(sid)
    assert state['status'] == 'ready', state['messages']
    message = state['messages'][-1]
    assert message['role'] == 'assistant' and '原文引用，未作数字核查' in message['text']
    assert '营业收入' not in message['text'] and len(message['source_references']) == 1
    assert message['message_id'].startswith('message-') and message['source_reference_manifest_sha256']
    public = message['source_references'][0]
    assert public['reference_status'] == 'unverified_source_quotation' and 'source_path' not in public
    loaded = load_source_evidence(sessions.root, state, message['message_id'], public['evidence_id'], lifecycle.config)
    assert loaded['source_path'] == str(pdf) and loaded['page'] == 1
    assert Path(loaded['_image_path']).read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
    assert loaded['quote_sha256'] == quoted[0]['quote_sha256']
    assert read(lifecycle.out/'results.json')['facts'] == result['facts']
    assert all(path.read_bytes() == raw for path, raw in old_files.items())
    assert [event['tool'] for event in state['events']] == ['mj_search_blocks', 'mj_submit_report']


def test_fragmented_policy_body_is_answered_with_real_pdf_proofs_instead_of_column_headers(lifecycle):
    fitz = pytest.importorskip('fitz')
    pdf = Path(lifecycle.request['corpus']).parent/'fragmented-policy.pdf'
    document = fitz.open()
    page = document.new_page()
    lines = ['1、重要会计政策变更', '本公司执行新的规定，涉及单项交易产生的递延所得税，',
             '初始确认豁免的会计处理需要调整。', '相关资产和负债分别确认，政策内容详见本说明。',
             '2、其他重要事项']
    for index, text in enumerate(lines):
        page.insert_text((40, 60 + index * 40), text, fontname='china-s', fontsize=12)
    page = document.new_page()
    page.insert_text((40, 60), '会计政策变更的内容和原因\n受影响的报表项目\n对期初余额的影响金额',
                     fontname='china-s', fontsize=12)
    page.insert_text((40, 140), '合并\n母公司', fontname='china-s', fontsize=12)
    document.save(pdf)
    document.close()
    register_pdf_disclosure(pdf)
    lifecycle.request['corpus_additions'] = [str(pdf)]
    sessions, sid, result = conversation(lifecycle)

    def body_plan(runtime, spec, prompt, call):
        assert '标题、栏目名' in prompt and '原文问题无需额外查询分析登记' in prompt
        selected = call('mj_search_blocks', {'query': '会计政策变更', 'limit': 1})[0]
        assert selected['page'] == 1 and selected['includes_substantive_context']
        raw = '\n'.join([selected['text'], *[row['text'] for row in selected['related_contexts']]])
        assert '单项交易' in raw and '初始确认豁免' in raw
        assert '影响金额' not in raw and '其他重要事项' not in raw
        assert all('quote_text' not in row for row in selected['source_references'])
        call('mj_submit_report', {'template': selected['slot_template'], 'slots': selected['slot_ids']})

    lifecycle.boundary.question_plan = body_plan
    sessions._answer_inner(sid, '请给出会计政策变更内容与原因的原文和页级证据。')
    state = sessions.get(sid)
    assert state['status'] == 'ready', state['messages']
    answer = state['messages'][-1]
    assert '单项交易' in answer['text'] and '初始确认豁免' in answer['text']
    assert len(answer['source_references']) == 4
    assert {reference['page'] for reference in answer['source_references']} == {1}
    for reference in answer['source_references']:
        loaded = load_source_evidence(sessions.root, state, answer['message_id'], reference['evidence_id'], lifecycle.config)
        assert loaded['reference_status'] == 'unverified_source_quotation'
        assert loaded['source_path'] == str(pdf)
    assert read(lifecycle.out/'results.json')['facts'] == result['facts']
    assert [event['tool'] for event in state['events']] == ['mj_search_blocks', 'mj_submit_report']


def test_kernel_task_preserves_indexed_qualitative_pdf_for_followup(lifecycle):
    from mingjing.cli import build_parser, command_check
    fitz = pytest.importorskip('fitz')
    pdf = Path(lifecycle.request['corpus']).parent/'kernel-policy.pdf'
    document = fitz.open()
    page = document.new_page()
    page.insert_textbox(fitz.Rect(40, 40, 520, 250),
        '会计政策变更\n本公司采用新的会计确认方法，政策内容详见本说明。', fontname='china-s', fontsize=12)
    document.save(pdf)
    document.close()
    register_pdf_disclosure(pdf)
    arguments = ['check', '--draft', lifecycle.request['draft'], '--corpus', lifecycle.request['corpus'],
                 '--corpus-addition', str(pdf), '--company', '甲公司', '--period', '2024Y',
                 '--report-date', '2025-03-31', '--out', str(lifecycle.out), '--run-id', 'lifecycle-fixture',
                 '--mode', 'kernel']
    assert command_check(build_parser().parse_args(arguments)) == 0
    data = read(lifecycle.out/'results.json')
    report_id = 'report-'+sha256_file(pdf)[:20]
    assert not any(Path(row.get('source_path', '')).resolve() == pdf.resolve() for row in data['facts'])
    assert data['source_registry'][report_id]['publish_date'] == '2025-03-01'
    manifest = read(lifecycle.out/'run_manifest.json')
    assert {str(pdf.resolve()), str((pdf.parent/'index.csv').resolve())} <= {row['path'] for row in manifest['inputs']}

    class ConfigBoundary:
        def get(self):
            return copy.deepcopy(lifecycle.config)

        def secret(self, *_):
            raise AssertionError('A qualitative source followup test cannot access API credentials')

    run = SimpleNamespace(out=lifecycle.out, request=lifecycle.request)
    sessions = sessions_module.FinancialSessions(lifecycle.out.parent/'kernel-conversations', lambda _: run, ConfigBoundary())
    sid = sessions.create('lifecycle-fixture')['session_id']
    locked = sessions.get(sid)
    source = next(row for row in locked['locks']['resources'] if row['id'] == report_id)
    assert source['publish_date'] == '2025-03-01' and source['publish_date_basis'] == 'frozen_corpus_index'
    sessions.cancel_events[sid] = threading.Event()

    def quote_plan(runtime, spec, prompt, call):
        hits = call('mj_search_blocks', {'query': '会计政策变更', 'limit': 1})
        selected = next(hit for hit in hits if hit.get('source_references'))
        assert selected['report_id'] == report_id
        assert '会计确认方法' in str(selected)
        call('mj_submit_report', {'template': selected['slot_template'], 'slots': selected['slot_ids']})

    lifecycle.boundary.question_plan = quote_plan
    sessions._answer_inner(sid, '会计政策变更的原文是什么？')
    state = sessions.get(sid)
    assert state['status'] == 'ready', state['messages']
    assert '会计确认方法' in state['messages'][-1]['text']
    assert state['messages'][-1]['source_references']


@pytest.mark.parametrize('path', ['source', 'analysis'])
def test_missing_policy_original_or_registered_event_has_lawful_non_numeric_answer(lifecycle, path):
    sessions, sid, result = conversation(lifecycle)

    def missing_plan(runtime, spec, prompt, call):
        if path == 'source':
            selected = call('mj_search_blocks', {'query': '会计政策变更'},)[0]
            assert selected['reason'] == 'source_material_required'
        else:
            selected = call('mj_analyze', {'analysis_id': 'financial_summary', 'category': 'events', 'kind': 'accounting_policy_change'})
            assert selected['diagnostics'][0]['status'] == 'analysis_record_not_found'
            assert not selected['diagnostics'][0]['verified']
        call('mj_submit_report', {'template': selected['slot_template'], 'slots': selected['slot_ids']})

    lifecycle.boundary.question_plan = missing_plan
    sessions._answer_inner(sid, '会计政策变更原文或登记事件是什么？')
    state = sessions.get(sid)
    assert state['status'] == 'ready', state['messages']
    answer = state['messages'][-1]
    assert answer['role'] == 'assistant' and answer['source_references'] == []
    assert not answer['source_reference_manifest_sha256']
    assert '营业收入' not in answer['text'] and ('不能证明' in answer['text'] or '不能据此断言' in answer['text'])
    assert read(lifecycle.out/'results.json')['facts'] == result['facts']
