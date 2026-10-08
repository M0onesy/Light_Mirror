"""Financial session behavior with a local provider boundary, no live API."""
import copy
import json
import threading
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from mingjing import harness_client, sessions as session_module
from mingjing.analysis import analyze_financials
from mingjing.config import AppConfig
from mingjing.evidence import sha256_file
from mingjing.facts import FactStore
from mingjing.models import Fact
from mingjing.providers import ModelConfig, ProviderConfig


@pytest.fixture
def conversation(tmp_path, monkeypatch):
    cfg = AppConfig().to_dict()
    cfg['providers'] = {'fixture': asdict(ProviderConfig(base_url='https://example.invalid/v1', models=[ModelConfig(id='fixture')]))}
    cfg['agent']['routing']['default'] = {'provider': 'fixture', 'model': 'fixture'}
    run = tmp_path/'run'
    run.mkdir()
    draft = run/'draft.md'
    report = run/'report.txt'
    draft.write_text('财报分析任务。', encoding='utf-8')
    report.write_text('甲公司2024年合并营业收入900元，母公司营业收入100元。', encoding='utf-8')
    facts = [Fact('parent', '甲公司', 'REVENUE', '2024Y', value=Decimal('100'), scope='母公司',
                  publish_date='2025-03-01', evidence=('parent-evidence',), source_path=str(report)),
             Fact('group', '甲公司', 'REVENUE', '2024Y', value=Decimal('900'), scope='合并',
                  publish_date='2025-03-01', evidence=('group-evidence',), source_path=str(report))]
    data = {'run_id': 'session-fixture', 'claims': [], 'results': [], 'facts': [f.to_dict() for f in facts],
            'report_date': '2025-03-31', 'analysis': analyze_financials(FactStore(facts), '甲公司', ['2024Y'], '2025-03-31', '母公司')}
    (run/'results.json').write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    (run/'run_manifest.json').write_text('{}', encoding='utf-8')
    resources = []
    for rid, path in [('draft', draft), ('report-real', report), ('fact-index', run/'facts.json'), ('source-dimensions', run/'source_dimensions.json')]:
        if not path.exists():
            path.write_text('{}', encoding='utf-8')
        resources.append({'id': rid, 'path': str(path), 'sha256': sha256_file(path)})
    (run/'agent_lock.json').write_text(json.dumps({'config': cfg, 'resources': resources}), encoding='utf-8')

    class ConfigBoundary:
        def get(self):
            return copy.deepcopy(cfg)

        def secret(self, *_):
            raise AssertionError('No credentials in this boundary fixture')

    holder = SimpleNamespace(out=run)
    sessions = session_module.FinancialSessions(tmp_path/'sessions', lambda _: holder, ConfigBoundary())
    sid = sessions.create('session-fixture')['session_id']
    sessions.cancel_events[sid] = threading.Event()
    captured = {'starts': 0, 'materials': []}
    real_partition = session_module.partition_document

    def partition(path, **options):
        captured['materials'].append((Path(path), options))
        return real_partition(path, **options)

    monkeypatch.setattr(session_module, 'partition_document', partition)

    class RuntimeBoundary:
        def __init__(self, home, request_handler, **_):
            self.handler = request_handler

        def start(self):
            captured['starts'] += 1

        def open_session(self, **spec):
            self.spec = spec

        def prompt(self, sid, prompt, **_):
            def call(name, params):
                return self.handler('mj/tool.dispatch', {**{k: self.spec[k] for k in ('session_id', 'role', 'run_id', 'resource_lock')},
                                                       'call_id': name, 'tool_name': name, 'params': params})
            captured['reports'] = call('mj_list_reports', {})
            result = call('mj_analyze', {'analysis_id': 'financial_summary', 'period': '2024Y', 'metric': 'REVENUE'})
            captured['analysis'] = result
            call('mj_submit_report', {'template': result['slot_template'], 'slots': result['slot_ids']})
            return {'finish_reason': 'completed'}

        def close(self):
            captured['closed'] = True

    monkeypatch.setattr(harness_client, 'HarnessRuntime', RuntimeBoundary)
    return SimpleNamespace(sessions=sessions, sid=sid, capture=captured, draft=draft, report=report, real_partition=real_partition)


def test_followup_keeps_frozen_parent_scope_instead_of_recomputing_group_values(conversation):
    c = conversation
    c.sessions._answer_inner(c.sid, '2024年营业收入是多少？')
    state = c.sessions.get(c.sid)
    assert state['status'] == 'ready'
    record = c.capture['analysis']['analysis'][0]['record']
    assert record['scope'] == '母公司' and record['value'] == '100'
    assert record['evidence'] == ['parent-evidence']
    assert 'group-evidence' not in state['messages'][-1]['text']


def test_followup_reads_only_original_documents_with_cancel_and_one_deadline(conversation):
    c = conversation
    c.sessions._answer_inner(c.sid, '核查营业收入')
    assert [path for path, _ in c.capture['materials']] == [c.draft, c.report]
    controls = [options for _, options in c.capture['materials']]
    assert all(options['cancel_event'] is c.sessions.cancel_events[c.sid] for options in controls)
    assert len({options['deadline_monotonic'] for options in controls}) == 1
    assert all(callable(options['progress_callback']) for options in controls)
    assert [report['report_id'] for report in c.capture['reports']] == ['report-real']
    assert c.capture['reports'][0]['companies'] == ['甲公司']
    assert c.capture['reports'][0]['periods'] == ['2024Y']


def test_followup_cancels_between_materials_before_starting_the_runtime(conversation, monkeypatch):
    c = conversation
    def partition(path, **options):
        result = c.real_partition(path, **options)
        c.sessions.cancel_events[c.sid].set()
        return result
    monkeypatch.setattr(session_module, 'partition_document', partition)
    c.sessions._answer(c.sid, '查询材料')
    assert c.sessions.get(c.sid)['status'] == 'cancelled'
    assert c.capture['starts'] == 0
