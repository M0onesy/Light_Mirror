"""Actual offline CLI manifests feeding a controlled native question loop."""
import copy
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from mingjing import sessions as sessions_module
from mingjing.cli import build_parser, command_check
from test_agent_lifecycle import lifecycle, read


def kernel_run(lifecycle):
    args = build_parser().parse_args(['check', '--draft', lifecycle.request['draft'],
        '--corpus', lifecycle.request['corpus'], '--company', lifecycle.request['company'],
        '--period', lifecycle.request['period'], '--report-date', lifecycle.request['report_date'],
        '--out', str(lifecycle.out), '--run-id', 'kernel-followup-fixture', '--mode', 'kernel'])
    assert command_check(args) == 0
    assert not (lifecycle.out/'agent_lock.json').exists()

    class ConfigBoundary:
        def get(self):
            return copy.deepcopy(lifecycle.config)

        def secret(self, *_):
            raise AssertionError('Controlled question loop does not resolve API credentials')

    run = SimpleNamespace(out=lifecycle.out, request=lifecycle.request)
    return sessions_module.FinancialSessions(lifecycle.out.parent/'kernel-conversations', lambda _: run, ConfigBoundary())


def test_kernel_manifest_materials_support_original_page_lookup_and_guarded_question(lifecycle):
    sessions = kernel_run(lifecycle)
    original = {path: path.read_bytes() for path in lifecycle.out.rglob('*') if path.is_file()}
    sid = sessions.create('kernel-followup-fixture')['session_id']
    state = sessions.get(sid)
    resource_paths = {Path(item['path']) for item in state['locks']['resources']}
    assert resource_paths == {Path(lifecycle.request['draft']), Path(lifecycle.request['corpus'])/'report.txt'}
    assert not any(path.suffix in {'.json', '.csv'} for path in resource_paths)
    observed = []

    def question_plan(runtime, spec, text, call):
        hits = call('mj_search_blocks', {'query': '合并财务报表', 'limit': 2})
        observed.append(('search', hits))
        assert hits and hits[0]['report_id'].startswith('report-')
        context = call('mj_read_context', {'mention_id': hits[0]['block_id']})
        observed.append(('context', context))
        assert '甲公司合并财务报表' in context['text'] and context['page'] == 1
        frozen = call('mj_get_verdicts', {'query': 'REVENUE', 'limit': 2})
        assert frozen['slot_ids']
        call('mj_submit_report', {'template': frozen['slot_template'], 'slots': frozen['slot_ids']})

    lifecycle.boundary.question_plan = question_plan
    sessions.cancel_events[sid] = threading.Event()
    sessions._answer_inner(sid, '说明原报告营业收入的材料位置和冻结判定。')
    finished = sessions.get(sid)
    assert finished['status'] == 'ready', finished['messages']
    assert [event['tool'] for event in finished['events']] == ['mj_search_blocks', 'mj_read_context', 'mj_get_verdicts', 'mj_submit_report']
    assert len(observed) == 2 and finished['messages'][-1]['role'] == 'assistant'
    assert all(path.read_bytes() == content for path, content in original.items())
    assert all(runtime.closed for runtime in lifecycle.boundary.instances)


def test_kernel_session_rejects_changed_manifest_input_before_any_native_runtime(lifecycle):
    sessions = kernel_run(lifecycle)
    report = Path(lifecycle.request['corpus'])/'report.txt'
    report.write_text('被改写的财报输入。', encoding='utf-8')
    with pytest.raises(ValueError, match='冻结清单校验失败'):
        sessions.create('kernel-followup-fixture')
    assert lifecycle.boundary.instances == []
