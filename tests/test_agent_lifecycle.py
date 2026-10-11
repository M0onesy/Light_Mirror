"""Controlled lifecycle boundaries, not live API or native acceptance tests.

The runtime mock preserves strict session descriptors and routes all financial
operations through the real guarded dispatcher. Native loop fault tests live
separately in test_native_runtime_safety.py.
"""
from __future__ import annotations

import copy
import json
import threading
import time
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from mingjing import agent as agent_module
from mingjing import harness_client, sessions as sessions_module, message_replay as message_replay_module
from mingjing.agent import FinancialAgent, orchestration_fingerprint as actual_orchestration_fingerprint
from mingjing.config import AppConfig
from mingjing.harness_client import HarnessError
from mingjing.models import Fact
from mingjing.providers import ModelConfig, ProviderConfig
from dataclasses import asdict


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


class RuntimeBoundary:
    """No sockets or providers: enforce upstream descriptor equality in memory."""

    def __init__(self):
        self.descriptors = {}
        self.instances = []
        self.lifecycle = []
        self.provider_work = 0
        self.cancel_first_binding = True
        self.fail_first_question = False
        self.cancel_used = False
        self.question_failure_used = False
        self.prompt_hook = None
        self.after_binding = None
        self.after_tool = None
        self.batch_coordinator = False
        self.coordinator_verify = True
        self.binding_transform = None
        self.question_plan = None

    def factory(self, home, request_handler, event_handler=None, config=None, expected_runtime_lock=None):
        boundary = self

        class Runtime:
            def __init__(self):
                self.home = str(Path(home).resolve())
                self.handler = request_handler
                self.owner = request_handler.__self__
                self.turns = {}
                self.active = {}
                self.closed = False
                boundary.instances.append(self)

            def start(self):
                boundary.lifecycle.append(('start', self.home))
                return self.describe()

            def describe(self):
                return {'native_loop': False, 'fixture': 'controlled_lifecycle_boundary'}

            def open_session(self, **spec):
                key = (self.home, spec['session_id'])
                if key in boundary.descriptors:
                    raise HarnessError('Fixture descriptor already exists; use resume')
                boundary.descriptors[key] = copy.deepcopy(spec)
                self.active[spec['session_id']] = copy.deepcopy(spec)
                boundary.lifecycle.append(('open', spec['session_id']))
                return {'session_id': spec['session_id']}

            def resume(self, **spec):
                key = (self.home, spec['session_id'])
                # The production plugin requires full canonical spec equality,
                # including its stored per-session native budget.
                if boundary.descriptors.get(key) != spec:
                    raise HarnessError('Fixture persisted descriptor differs from resume spec')
                self.active[spec['session_id']] = copy.deepcopy(spec)
                boundary.lifecycle.append(('resume', spec['session_id']))
                return {'session_id': spec['session_id'], 'resumed': True}

            def prompt(self, sid, text, timeout_s=600, cancel_event=None):
                spec = self.active[sid]
                role = spec['role']
                if boundary.prompt_hook:
                    boundary.prompt_hook(self, spec, cancel_event)
                self.turns[sid] = self.turns.get(sid, 0) + 1
                # Distinct calls for each new turn; previously committed
                # semantic fields remain immutable in the host dispatcher.
                turn = len(boundary.lifecycle)
                boundary.lifecycle.append(('prompt', sid))
                params = dict(run_id=spec['run_id'], resource_lock=spec['resource_lock'], session_id=sid, role=role,
                              request_hash=f'fixture-{sid}-{turn}')
                cached = self.handler('mj/model.cache.get', params)
                if self.owner.replay_only and not cached['hit']:
                    # Match the native plugin's boundary: no credential lookup,
                    # provider work, tools or invented completion on cache miss.
                    raise HarnessError('REPLAY_CACHE_MISS: fixture request was not cached')
                boundary.provider_work += 1
                self.handler('mj/model.cache.put', {**params, 'chunks': [{'type': 'usage', 'usage': {'totalTokens': 7}}]})
                if role == 'question' and boundary.fail_first_question and not boundary.question_failure_used:
                    boundary.question_failure_used = True
                    return {'finish_reason': 'error', 'events': []}

                call_index = 0

                def call(name, arguments):
                    nonlocal call_index
                    call_index += 1
                    output = self.handler('mj/tool.dispatch', {**{key: params[key] for key in ('run_id', 'resource_lock', 'session_id', 'role')},
                                                              'call_id': f'fixture-{turn}-{call_index}', 'tool_name': name, 'params': arguments})
                    if boundary.after_tool:
                        boundary.after_tool(self, spec, name, arguments, output)
                    return output

                if role in ('planner', 'verifier'):
                    owned = self.owner.dispatcher.sessions[sid]['mentions']
                    for mid in self.owner.dispatcher.claims:
                        if mid not in owned:
                            continue
                        claim = self.owner.dispatcher.claims[mid]
                        binding = {'company': claim.company, 'account': claim.account, 'period': claim.period,
                                   'scope': self.owner.dispatcher.source_dimensions(claim).get('scope_header') or '',
                                   'relation': claim.relation, 'base_period': claim.base_period or ('2023Y' if claim.relation == 'yoy' else '')}
                        if boundary.binding_transform:
                            binding = boundary.binding_transform(copy.deepcopy(binding))
                        call('mj_submit_binding', {'mention_id': mid, 'binding': binding})
                        if boundary.after_binding:
                            boundary.after_binding(self, spec, mid)
                        if boundary.cancel_first_binding and not boundary.cancel_used:
                            boundary.cancel_used = True
                            cancel_event.set()
                            raise HarnessError('Fixture user cancellation after a partial binding')
                elif role == 'coordinator':
                    owned = sorted(self.owner.dispatcher.sessions[sid]['mentions'])
                    if self.owner.request.get('task_type') == 'analysis' and not owned:
                        reports = call('mj_list_reports', {})
                        call('mj_get_rule', {'rule_id': 'FIN-gross-margin'})
                        periods = sorted({period for report in reports for period in report['periods']})
                        arguments = {'analysis_id': 'financial_summary'}
                        if periods:
                            arguments['period'] = periods[-1]
                        if boundary.coordinator_verify:
                            call('mj_analyze', arguments)
                    elif boundary.batch_coordinator:
                        call('mj_get_facts', {'binding_ids': owned})
                        call('mj_get_rule', {'rule_id': 'SMART-registered-kernel' if hasattr(self.owner, 'smart_kernel') else 'E1'})
                        if boundary.coordinator_verify:
                            call('mj_verify_bindings', {'binding_ids': owned})
                    else:
                        for mid in owned:
                            call('mj_get_fact', {'binding_id': mid})
                            call('mj_get_rule', {'rule_id': 'E1'})
                            if boundary.coordinator_verify:
                                call('mj_verify', {'binding_id': mid})
                elif role in ('reporter', 'question'):
                    if role == 'question' and boundary.question_plan:
                        boundary.question_plan(self, spec, text, call)
                        return {'finish_reason': 'completed', 'events': []}
                    frozen = (call('mj_analyze', {'analysis_id': 'financial_summary'})
                              if role == 'reporter' and self.owner.request.get('task_type') == 'analysis'
                              else call('mj_get_verdicts', {}))
                    selected = next(iter(frozen['slots']))
                    call('mj_submit_report', {'template': '核查结果\n{{' + selected + '}}', 'slots': [selected]})
                return {'finish_reason': 'completed', 'events': []}

            def close_session(self, sid):
                self.active.pop(sid, None)
                boundary.lifecycle.append(('close_session', sid))
                return {'closed': True}

            def close(self):
                self.closed = True
                boundary.lifecycle.append(('close', self.home))

        return Runtime()


@pytest.fixture
def lifecycle(tmp_path, monkeypatch):
    boundary = RuntimeBoundary()
    monkeypatch.setattr(harness_client, 'HarnessRuntime', boundary.factory)
    fingerprint = {'modules': {'workflow': 'controlled-lifecycle-fixture'}, 'runtime': {'fixture': 'no_native_or_api_execution'}}
    monkeypatch.setattr(agent_module, 'orchestration_fingerprint', lambda config=None: copy.deepcopy(fingerprint))
    monkeypatch.setattr(sessions_module, 'orchestration_fingerprint', lambda config=None: copy.deepcopy(fingerprint))
    # This module is intentionally imported before the fixture patches the
    # function. A lazy import during _answer_inner would permanently capture
    # the temporary lambda in its from-import alias and poison later native
    # replay tests after monkeypatch restores the agent module.
    monkeypatch.setattr(message_replay_module, 'orchestration_fingerprint', lambda config=None: copy.deepcopy(fingerprint))
    cfg = AppConfig().to_dict()
    cfg['revision'] = 1
    cfg['providers'] = {'fixture': asdict(ProviderConfig(base_url='https://example.invalid/v1', credential_ref='fixture-key', models=[ModelConfig(id='fixture')]))}
    cfg['agent']['routing']['default'] = {'provider': 'fixture', 'model': 'fixture'}
    # These tests exercise a specific serial partial-binding checkpoint. The
    # independent parallel lifecycle is covered with coordinated barriers.
    cfg['agent']['budget'].update(max_model_calls=50, max_tool_calls=100, max_total_tokens=1000, max_runtime_s=60, max_subagents=1)
    draft = tmp_path/'draft.md'
    draft.write_text('甲公司2024年合并营业收入100元，同比增长25%。', encoding='utf-8')
    corpus = tmp_path/'corpus'
    corpus.mkdir()
    (corpus/'report.txt').write_text('甲公司合并财务报表\n2024年营业收入100元，2023年营业收入80元。', encoding='utf-8')
    facts = [Fact('fixture-2024', '甲公司', 'REVENUE', '2024Y', value=Decimal('100'), publish_date='2025-03-01', source_path='report.txt'),
             Fact('fixture-2023', '甲公司', 'REVENUE', '2023Y', value=Decimal('80'), publish_date='2025-03-01', source_path='report.txt')]
    (corpus/'facts.json').write_text(json.dumps([fact.to_dict() for fact in facts], ensure_ascii=False), encoding='utf-8')
    request = {'draft': str(draft), 'corpus': str(corpus), 'company': '甲公司', 'period': '2024Y', 'report_date': '2025-03-31'}
    return SimpleNamespace(boundary=boundary, config=cfg, request=request, out=tmp_path/'run', fingerprint=fingerprint)


def new_agent(fixture, *, config=None):
    return FinancialAgent(fixture.request, config or fixture.config,
                          lambda *_: (_ for _ in ()).throw(AssertionError('A lifecycle fixture cannot resolve API credentials')),
                          fixture.out, run_id='lifecycle-fixture')


def test_cancelled_partial_remains_mutable_and_resumes_with_cumulative_usage(lifecycle):
    original = new_agent(lifecycle)
    interrupted = original.run()
    saved = read(lifecycle.out/'agent_state.json')
    assert interrupted['model']['status'] == 'cancelled' and interrupted['partial']
    assert saved['stage'] == 'cancelled_partial'
    assert not (lifecycle.out/'run_manifest.json').exists()
    assert len(saved['bindings']['planner']) == 1 and not saved['bindings']['verifier']
    assert saved['native_request_count'] == 1 and saved['total_tokens'] == 7
    assert len(saved['tool_calls']) == 1
    # Simulate elapsed work before a restart, without waiting in the test.
    saved['elapsed_s'] = 5
    (lifecycle.out/'agent_state.json').write_text(json.dumps(saved, ensure_ascii=False), encoding='utf-8')
    resumed = new_agent(lifecycle)
    completed = resumed.run(resume=True)
    assert completed['model']['status'] == 'success'
    assert read(lifecycle.out/'agent_state.json')['stage'] == 'frozen'
    assert (lifecycle.out/'run_manifest.json').is_file()
    assert any(action == 'resume' for action, _ in lifecycle.boundary.lifecycle)
    assert resumed.restored_usage == {'requests': 1, 'tools': 1, 'tokens': 7, 'elapsed_s': 5}
    assert resumed.native_request_count == lifecycle.boundary.provider_work
    assert resumed.total_tokens == 7 * resumed.native_request_count
    assert resumed.dispatcher.calls.issuperset({tuple(item) for item in saved['tool_calls']})
    assert read(lifecycle.out/'agent_state.json')['elapsed_s'] >= 5
    assert all(runtime.closed for runtime in lifecycle.boundary.instances)


@pytest.mark.parametrize('limit', ['requests', 'tokens'])
def test_restored_budget_blocks_the_next_model_before_provider_work(lifecycle, limit):
    new_agent(lifecycle).run()
    saved = read(lifecycle.out/'agent_state.json')
    if limit == 'requests':
        saved['native_request_count'] = lifecycle.config['agent']['budget']['max_model_calls']
    else:
        saved['total_tokens'] = lifecycle.config['agent']['budget']['max_total_tokens']
    (lifecycle.out/'agent_state.json').write_text(json.dumps(saved, ensure_ascii=False), encoding='utf-8')
    before = lifecycle.boundary.provider_work
    resumed = new_agent(lifecycle)
    result = resumed.run(resume=True)
    assert result['model']['status'] == 'partial'
    assert result['partial']
    assert 'AgentBudgetExceeded' in result['model']['error']
    assert lifecycle.boundary.provider_work == before
    assert resumed.native_request_count >= saved['native_request_count']
    assert resumed.total_tokens >= saved['total_tokens']


@pytest.mark.parametrize('changed', ['configuration', 'agent'])
def test_resume_rejects_changed_locks_before_rewriting_partial_state(lifecycle, monkeypatch, changed):
    new_agent(lifecycle).run()
    before = (lifecycle.out/'agent_state.json').read_bytes()
    count = len(lifecycle.boundary.instances)
    config = copy.deepcopy(lifecycle.config)
    if changed == 'configuration':
        config['revision'] += 1
    else:
        monkeypatch.setattr(agent_module, 'orchestration_fingerprint', lambda config=None: {'modules': {'workflow': 'changed-code'}})
    with pytest.raises(ValueError, match='lock changed'):
        new_agent(lifecycle, config=config).run(resume=True)
    assert (lifecycle.out/'agent_state.json').read_bytes() == before
    assert len(lifecycle.boundary.instances) == count


@pytest.mark.parametrize('module_name', ['harness_client', 'http_transport', 'config', 'provider_api', 'llm'])
def test_real_fingerprint_rejects_transport_code_change_without_rewriting_checkpoint(lifecycle, monkeypatch, module_name):
    import importlib
    # Use the real module-code fingerprint while this fixture still supplies
    # a controlled runtime. The mutation exists only in this test process.
    monkeypatch.setattr(harness_client, 'runtime_fingerprint', lambda config=None: {'fixture': 'controlled-runtime'})
    monkeypatch.setattr(agent_module, 'orchestration_fingerprint', actual_orchestration_fingerprint)
    new_agent(lifecycle).run()
    before = frozen_bytes(lifecycle.out)
    native_count = len(lifecycle.boundary.instances)
    module = importlib.import_module('mingjing.'+module_name)

    def changed_transport_code():
        return 'Transport implementation was changed after the task lock'

    changed_transport_code.__module__ = module.__name__
    monkeypatch.setattr(module, '_test_transport_fingerprint_change', changed_transport_code, raising=False)
    with pytest.raises(ValueError, match='lock changed'):
        new_agent(lifecycle).run(resume=True)
    assert frozen_bytes(lifecycle.out) == before
    assert len(lifecycle.boundary.instances) == native_count


def frozen_bytes(out):
    return {path.relative_to(out): path.read_bytes() for path in out.rglob('*') if path.is_file()}


def test_resume_uses_locked_draft_when_external_original_was_edited(lifecycle):
    new_agent(lifecycle).run()
    saved = read(lifecycle.out/'agent_state.json')
    frozen = Path(read(lifecycle.out/'agent_lock.json')['resources'][0]['path'])
    before = frozen.read_bytes()
    Path(lifecycle.request['draft']).write_text('Edited original 999999', encoding='utf-8')
    resumed = new_agent(lifecycle)
    result = resumed.run(resume=True)
    assert result['model']['status'] == 'success'
    assert frozen.read_bytes() == before
    assert resumed.request['draft'] == str(frozen)
    assert resumed.restored_usage['tokens'] == saved['total_tokens']


@pytest.mark.parametrize('failure', ['parser', 'derived_snapshot'])
def test_resume_reconstruction_failure_preserves_every_checkpoint_byte(lifecycle, monkeypatch, failure):
    new_agent(lifecycle).run()
    before = frozen_bytes(lifecycle.out)
    resumed = new_agent(lifecycle)
    if failure == 'parser':
        def reject(*_args, **_kwargs):
            raise ValueError('Injected readonly parser failure')
        monkeypatch.setattr(agent_module, 'partition_document', reject)
    else:
        original = resumed._material_facts
        def changed(*args):
            from dataclasses import replace
            return [replace(fact, value=fact.value+Decimal('1')) for fact in original(*args)]
        monkeypatch.setattr(resumed, '_material_facts', changed)
    with pytest.raises(ValueError):
        resumed.run(resume=True)
    assert frozen_bytes(lifecycle.out) == before


def test_first_resume_checkpoint_cannot_reset_consumed_budget_or_bindings(lifecycle, monkeypatch):
    new_agent(lifecycle).run()
    previous = read(lifecycle.out/'agent_state.json')
    before = frozen_bytes(lifecycle.out)
    resumed = new_agent(lifecycle)
    original = resumed.save_state
    def crash(stage):
        if stage == 'prepared':
            raise AssertionError('Resume cannot publish a fresh prepared checkpoint')
        original(stage)
        if stage == 'resuming':
            raise RuntimeError('Injected crash after restored checkpoint commit')
    monkeypatch.setattr(resumed, 'save_state', crash)
    with pytest.raises(RuntimeError, match='restored checkpoint'):
        resumed.run(resume=True)
    restored = read(lifecycle.out/'agent_state.json')
    assert restored['stage'] == 'resuming'
    for key in ('bindings', 'request_count', 'native_request_count', 'total_tokens', 'tool_calls'):
        assert restored[key] == previous[key]
    assert restored['elapsed_s'] >= previous['elapsed_s']
    for path, raw in before.items():
        if path.as_posix() not in {'agent_state.json', 'coverage.json'}:
            assert (lifecycle.out/path).read_bytes() == raw


def test_resume_prevalidation_does_not_append_host_progress_to_old_run(lifecycle, monkeypatch):
    new_agent(lifecycle).run()
    before = frozen_bytes(lifecycle.out)
    def persistent_emit(event, **fields):
        with (lifecycle.out/'host_progress.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'event': event, **fields})+'\n')
    resumed = FinancialAgent(lifecycle.request, lifecycle.config, lambda _: None,
                             lifecycle.out, run_id='lifecycle-fixture', emit=persistent_emit)
    def fail(*_args, **_kwargs):
        raise ValueError('Injected readonly preparation failure')
    monkeypatch.setattr(agent_module, 'partition_document', fail)
    with pytest.raises(ValueError):
        resumed.run(resume=True)
    assert frozen_bytes(lifecycle.out) == before


@pytest.mark.parametrize('failure', ['unprepared', 'wrong_session', 'inactive', 'wrong_role', 'wrong_lock',
                                    'cancelled', 'deadline', 'sibling_stopped', 'wrong_route'])
def test_credentials_reject_invalid_execution_identity_before_resolving(lifecycle, failure):
    resolved = []
    agent = FinancialAgent(lifecycle.request, lifecycle.config, lambda ref: resolved.append(ref) or 'private',
                           lifecycle.out, run_id='lifecycle-fixture')
    params = dict(run_id=agent.run_id, session_id='credential-owner', role='planner',
                  credential_ref='fixture-key', provider_id='fixture')
    if failure != 'unprepared':
        agent.prepare()
        agent.dispatcher.register_session(params['session_id'], params['role'], [])
        params['resource_lock'] = agent.resource_lock
    if failure == 'wrong_session':
        params['session_id'] = 'not-an-owner'
    elif failure == 'inactive':
        agent.dispatcher.sessions[params['session_id']]['active'] = False
    elif failure == 'wrong_role':
        params['role'] = 'reporter'
    elif failure == 'wrong_lock':
        params['resource_lock'] = 'changed'
    elif failure == 'cancelled':
        agent.cancelled.set()
    elif failure == 'deadline':
        agent.started -= agent.budget['max_runtime_s']+1
    elif failure == 'sibling_stopped':
        agent._role_stops[params['session_id']] = threading.Event()
        agent._role_stops[params['session_id']].set()
    elif failure == 'wrong_route':
        params['credential_ref'] = 'another-roles-key'
    with pytest.raises((PermissionError, InterruptedError, agent_module.AgentBudgetExceeded)):
        agent._incoming('mj/credentials.resolve', params)
    assert resolved == []


def test_credentials_recheck_cancellation_after_blocking_resolver(lifecycle):
    agent = FinancialAgent(lifecycle.request, lifecycle.config, lambda _: None, lifecycle.out, run_id='lifecycle-fixture')
    agent.prepare()
    agent.dispatcher.register_session('credential-owner', 'planner', [])
    def resolve(_ref):
        agent.cancelled.set()
        return 'private-never-returned'
    agent.secret_resolver = resolve
    with pytest.raises(InterruptedError):
        agent._incoming('mj/credentials.resolve', dict(run_id=agent.run_id, resource_lock=agent.resource_lock,
            session_id='credential-owner', role='planner', credential_ref='fixture-key', provider_id='fixture'))
    assert not agent.resolved_secrets


@pytest.mark.parametrize('source', ['request', 'result'])
def test_session_material_update_rejects_dropping_existing_reparse_proof(lifecycle, source):
    lifecycle.boundary.cancel_first_binding = False
    new_agent(lifecycle).run()

    class ConfigBoundary:
        def get(self):
            return copy.deepcopy(lifecycle.config)

    launched = []
    holder = SimpleNamespace(out=lifecycle.out, request=lifecycle.request)
    sessions = sessions_module.FinancialSessions(lifecycle.out.parent/'conversations', lambda _: holder, ConfigBoundary())
    sessions.launch_run = lambda request: launched.append(request)
    sid = sessions.create('lifecycle-fixture')['session_id']
    if source == 'request':
        request = dict(lifecycle.request, reparse_packets=[{'packet_id': 'locked-existing-packet'}])
        agent_module.write_json(lifecycle.out/'run_request.json', request)
    else:
        result = read(lifecycle.out/'results.json')
        result['reparse_supplements'] = [{'packet_id': 'locked-existing-packet'}]
        agent_module.write_json(lifecycle.out/'results.json', result)
    before = frozen_bytes(lifecycle.out)
    transcript = sessions.path(sid).read_bytes()
    with pytest.raises(ValueError, match='补证材料'):
        sessions.add_materials(sid, {'scope_declaration': '合并'})
    assert launched == [] and frozen_bytes(lifecycle.out) == before
    assert sessions.path(sid).read_bytes() == transcript


def test_followup_first_open_is_persisted_before_failed_prompt_and_then_resumed(lifecycle):
    lifecycle.boundary.cancel_first_binding = False
    new_agent(lifecycle).run()
    lifecycle.boundary.fail_first_question = True

    class ConfigBoundary:
        def get(self):
            return copy.deepcopy(lifecycle.config)

        def secret(self, *_):
            raise AssertionError('No API credentials in lifecycle tests')

    run = SimpleNamespace(out=lifecycle.out, request=lifecycle.request)
    sessions = sessions_module.FinancialSessions(lifecycle.out.parent/'conversations', lambda _: run, ConfigBoundary())
    sid = sessions.create('lifecycle-fixture')['session_id']
    sessions.cancel_events[sid] = threading.Event()
    sessions._answer_inner(sid, '核查结果如何？')
    failed = sessions.get(sid)
    assert failed['status'] == 'failed' and failed['native_session_created']
    assert failed['native_spec']['session_id'] == sid
    assert not any(message['role'] == 'assistant' for message in failed['messages'])
    sessions.resume(sid)
    sessions.cancel_events[sid] = threading.Event()
    sessions._answer_inner(sid, '继续解释已有证据。')
    recovered = sessions.get(sid)
    assert recovered['status'] == 'ready'
    assert recovered['messages'][-1]['role'] == 'assistant'
    actions = [action for action, identifier in lifecycle.boundary.lifecycle if identifier == sid]
    assert actions == ['open', 'prompt', 'resume', 'prompt']
    assert all(runtime.closed for runtime in lifecycle.boundary.instances)


def test_analysis_comparison_followup_has_frozen_context_and_submits_tool_template(lifecycle):
    """Controlled lifecycle plus real dispatcher; not a live API acceptance."""
    Path(lifecycle.request['draft']).write_text('财报分析任务。', encoding='utf-8')
    lifecycle.request['task_type'] = 'analysis'
    lifecycle.boundary.cancel_first_binding = False
    result = new_agent(lifecycle).run()
    assert result['model']['status'] == 'success', result['model']['error']
    before = copy.deepcopy(result['analysis'])
    question_calls = []

    def comparison_plan(runtime, spec, prompt, call):
        assert '"task_type": "analysis"' in prompt and '"company": "甲公司"' in prompt
        assert '"scope": "合并"' in prompt and '"as_of_date": "2025-03-31"' in prompt
        assert '没有研报草稿判定' in prompt and 'REVENUE_YOY' in prompt
        assert '结构错误最多纠正一次' in prompt and 'slot_template' in prompt
        schema = next(tool for tool in spec['tool_definitions'] if tool['name'] == 'mj_analyze')['inputSchema']
        assert 'REVENUE_YOY' in schema['properties']['metric']['enum']
        frozen = call('mj_analyze', {'analysis_id': 'financial_summary', 'period': '2024Y', 'metric': 'REVENUE_YOY'})
        question_calls.append('mj_analyze')
        row = frozen['analysis'][0]['record']
        assert row['company'] == '甲公司' and row['value'] == '0.25'
        assert [operand['value'] for operand in row['operands']] == ['100', '80']
        call('mj_submit_report', {'template': frozen['slot_template'], 'slots': frozen['slot_ids']})
        question_calls.append('mj_submit_report')

    lifecycle.boundary.question_plan = comparison_plan

    class ConfigBoundary:
        def get(self):
            return copy.deepcopy(lifecycle.config)

        def secret(self, *_):
            raise AssertionError('No real API credentials in controlled lifecycle regression')

    run = SimpleNamespace(out=lifecycle.out, request=lifecycle.request)
    sessions = sessions_module.FinancialSessions(lifecycle.out.parent/'conversations', lambda _: run, ConfigBoundary())
    sid = sessions.create('lifecycle-fixture')['session_id']
    sessions.cancel_events[sid] = threading.Event()
    sessions._answer_inner(sid, '比较2024年与2023年营收，给出同比及证据。')
    state = sessions.get(sid)
    assert state['status'] == 'ready', state['messages']
    assert question_calls == ['mj_analyze', 'mj_submit_report']
    assert state['messages'][-1]['role'] == 'assistant'
    assert '区间 [24.98, 25.02]%' in state['messages'][-1]['text']
    assert '显示精度：' in state['messages'][-1]['text']
    assert '100.00 元' in state['messages'][-1]['text'] and '80.00 元' in state['messages'][-1]['text']
    assert [event['tool'] for event in state['events']] == question_calls
    assert state['native_spec']['session_id'] == sid
    assert read(lifecycle.out/'results.json')['analysis'] == before
    assert all(runtime.closed for runtime in lifecycle.boundary.instances)


def test_frozen_results_cannot_be_resumed(lifecycle):
    lifecycle.boundary.cancel_first_binding = False
    new_agent(lifecycle).run()
    before = (lifecycle.out/'results.json').read_bytes()
    with pytest.raises(ValueError, match='Frozen results'):
        new_agent(lifecycle).run(resume=True)
    assert (lifecycle.out/'results.json').read_bytes() == before


def test_agent_replay_cache_miss_never_promotes_kernel_fallback_to_pass(lifecycle, monkeypatch):
    from mingjing import agent_replay
    lifecycle.boundary.cancel_first_binding = False
    new_agent(lifecycle).run()
    monkeypatch.setattr(agent_replay, 'orchestration_fingerprint', lambda config=None: copy.deepcopy(lifecycle.fingerprint))
    before = lifecycle.boundary.provider_work
    original = (lifecycle.out/'results.json').read_bytes()
    # Replay's new sessions deliberately miss this mock's opaque request keys.
    # Successful replay is tested with actual native stream caches elsewhere.
    result = agent_replay.replay_agent(lifecycle.out)
    assert result['status'] == 'failed' and result['reason'] == 'HarnessError'
    assert 'REPLAY_CACHE_MISS' in result['details']
    assert result['network_calls'] == 0
    assert lifecycle.boundary.provider_work == before
    assert (lifecycle.out/'results.json').read_bytes() == original
    assert all(runtime.closed for runtime in lifecycle.boundary.instances)


def test_agent_replay_rejects_runtime_lock_change_before_any_new_session(lifecycle, monkeypatch):
    from mingjing import agent_replay
    lifecycle.boundary.cancel_first_binding = False
    new_agent(lifecycle).run()
    monkeypatch.setattr(agent_replay, 'orchestration_fingerprint', lambda config=None: {'runtime': {'changed_plugin': True}})
    before = len(lifecycle.boundary.instances)
    result = agent_replay.replay_agent(lifecycle.out)
    assert result['status'] == 'failed' and result['reason'] == 'integrity_mismatch'
    assert {'kind': 'agent_runtime_or_plugin_changed'} in result['issues']
    assert len(lifecycle.boundary.instances) == before


def test_parallel_binders_overlap_and_verifier_cannot_observe_committed_planner(lifecycle):
    lifecycle.config['agent']['budget']['max_subagents'] = 2
    lifecycle.boundary.cancel_first_binding = False
    barrier, planner_done = threading.Barrier(2), threading.Event()
    contexts, scopes = {}, {}

    def after_binding(runtime, spec, _):
        if spec['role'] == 'planner':
            owned = runtime.owner.dispatcher.sessions[spec['session_id']]['mentions']
            if owned.issubset(runtime.owner.dispatcher.bindings['planner']):
                planner_done.set()

    def prompt_hook(runtime, spec, cancellation):
        role = spec['role']
        if role not in {'planner', 'verifier'}:
            return
        sid = spec['session_id']
        scopes[role] = sid
        # A barrier fails if the implementation silently remains sequential.
        barrier.wait(timeout=3)
        if role == 'verifier':
            assert planner_done.wait(timeout=3)
        contexts[role] = runtime.handler('mj/tool.dispatch', {
            'run_id': spec['run_id'], 'resource_lock': spec['resource_lock'], 'session_id': sid,
            'role': role, 'call_id': 'independent-context', 'tool_name': 'mj_list_mentions', 'params': {},
        })
        assert all('100' not in item['text'] for item in contexts[role]['mentions'])
        assert cancellation.is_set() is False

    lifecycle.boundary.prompt_hook, lifecycle.boundary.after_binding = prompt_hook, after_binding
    owner = new_agent(lifecycle)
    result = owner.run()
    assert result['model']['status'] == 'success', result['model']['error']
    assert scopes['planner'] != scopes['verifier']
    assert contexts['planner'] == contexts['verifier']
    assert owner.dispatcher.bindings['planner'] == owner.dispatcher.bindings['verifier']
    assert all(record['completed'] for record in owner.session_records)
    assert owner.request_count == owner.native_request_count == 4
    assert owner.total_tokens == 28
    assert owner._prompt_reservations == 0 and not owner._role_stops
    saved = read(lifecycle.out/'agent_state.json')
    assert saved['request_count'] == 4 and saved['total_tokens'] == 28
    assert len(saved['sessions']) == 4
    assert not any(identity['active'] for identity in owner.dispatcher.sessions.values())


@pytest.mark.parametrize('failure', ['cancel', 'model_budget'])
def test_parallel_failure_waits_for_both_sessions_before_freezing(lifecycle, failure):
    lifecycle.config['agent']['budget']['max_subagents'] = 2
    lifecycle.boundary.cancel_first_binding = False
    barrier, stopped = threading.Barrier(2), threading.Event()
    if failure == 'model_budget':
        lifecycle.config['agent']['budget'].update(max_model_calls=1, max_requests=50)

    def prompt_hook(runtime, spec, cancellation):
        if spec['role'] not in {'planner', 'verifier'}:
            return
        barrier.wait(timeout=3)
        if failure == 'cancel':
            if spec['role'] == 'planner':
                cancellation.set()
                raise HarnessError('Fixture user cancellation with another active binder')
            deadline = time.monotonic()+3
            while not cancellation.is_set() and time.monotonic() < deadline:
                stopped.wait(.005)
            assert cancellation.is_set()
            stopped.set()
            raise HarnessError('Fixture sibling acknowledged cancellation')

    lifecycle.boundary.prompt_hook = prompt_hook
    owner = new_agent(lifecycle)
    result = owner.run()
    expected = 'cancelled' if failure == 'cancel' else 'partial'
    assert result['model']['status'] == expected, result['model']['error']
    assert result['partial']
    assert owner._prompt_reservations == 0 and not owner._role_stops
    assert not any(identity['active'] for identity in owner.dispatcher.sessions.values())
    assert all(runtime.closed and not runtime.active for runtime in lifecycle.boundary.instances)
    if failure == 'cancel':
        assert stopped.is_set() and owner.native_request_count == 0
        assert read(lifecycle.out/'agent_state.json')['stage'] == 'cancelled_partial'
    else:
        assert not owner.cancelled.is_set()
        assert owner.native_request_count == lifecycle.boundary.provider_work == 1


def test_parallel_prompt_reservation_never_oversubscribes_single_request_budget(lifecycle):
    lifecycle.config['agent']['budget'].update(max_subagents=2, max_requests=1)
    lifecycle.boundary.cancel_first_binding = False

    def prompt_hook(runtime, spec, cancellation):
        if spec['role'] not in {'planner', 'verifier'}:
            return
        deadline = time.monotonic()+3
        while not cancellation.is_set() and time.monotonic() < deadline:
            threading.Event().wait(.005)
        assert cancellation.is_set()
        raise HarnessError('Fixture binder acknowledged sibling budget stop')

    lifecycle.boundary.prompt_hook = prompt_hook
    owner = new_agent(lifecycle)
    result = owner.run()
    assert result['model']['status'] == 'fallback'
    assert 'AgentBudgetExceeded' in result['model']['error']
    assert owner.request_count <= 1
    assert owner._prompt_reservations == 0 and not owner._role_stops
    assert not owner.cancelled.is_set()
    assert all(runtime.closed and not runtime.active for runtime in lifecycle.boundary.instances)


def _multiple_source_batches(lifecycle, count=18):
    Path(lifecycle.request['draft']).write_text('甲公司2024年合并营业收入100元。\n' * count, encoding='utf-8')
    lifecycle.boundary.cancel_first_binding = False
    lifecycle.boundary.batch_coordinator = True
    lifecycle.config['agent']['budget'].update(max_tool_calls=200)


def test_each_batch_executes_native_facts_rule_and_verify_before_next_binding(lifecycle):
    _multiple_source_batches(lifecycle)
    owner = new_agent(lifecycle)
    result = owner.run()
    assert result['model']['status'] == 'success', result['model']['error']
    assert len(result['claims']) == len(result['results']) == 18
    assert result['model']['source_candidates'] == result['model']['semantic_claims'] == 18
    assert result['model']['binding_submissions'] == {'planner': 18, 'verifier': 18}
    assert result['pending_claim_ids'] == []
    sessions = owner.session_records
    assert [record['role'] for record in sessions] == ['planner', 'verifier', 'coordinator'] * 3 + ['reporter']
    for batch, coordinator in zip(owner.batch_records, [record for record in sessions if record['role'] == 'coordinator']):
        events = [event for event in result['tool_trace'] if event['session_id'] == coordinator['session_id']]
        assert [event['tool'] for event in events] == ['mj_get_facts', 'mj_get_rule', 'mj_verify_bindings']
        assert set(batch['verified_ids']) == set(batch['mention_ids'])
        assert batch['phase'] == 'completed'
        assert all(event['status'] == 'success' for event in events)
    for first, following in zip(owner.batch_records, owner.batch_records[1:]):
        last_verified = max(i for i, event in enumerate(result['tool_trace'])
                            if event['tool'] == 'mj_verify_bindings' and set(event['input']['binding_ids']) == set(first['mention_ids']))
        first_binding = min(i for i, event in enumerate(result['tool_trace'])
                            if event['tool'] == 'mj_submit_binding' and event['input']['mention_id'] in following['mention_ids'])
        assert last_verified < first_binding


def test_budget_after_first_batch_preserves_executed_results_and_unexecuted_partition(lifecycle):
    from mingjing.replay import replay_run
    _multiple_source_batches(lifecycle)
    lifecycle.config['agent']['budget'].update(max_requests=3, max_model_calls=3)
    owner = new_agent(lifecycle)
    result = owner.run()
    assert result['model']['status'] == 'partial', result['model']['error']
    assert len(result['claims']) == 18
    assert len(result['results']) == 8 and len(result['pending_claim_ids']) == 10
    assert sum(result['summary'].values()) == 8
    assert result['summary']['abstained'] == 0
    assert set(result['execution']['native_verified_claim_ids']) == {row['claim_id'] for row in result['results']}
    assert not set(result['pending_claim_ids']).intersection(result['execution']['native_verified_claim_ids'])
    assert len(owner.batch_records[0]['verified_ids']) == 8
    assert result['coverage']['partial']
    replayed = replay_run(lifecycle.out)
    assert replayed['status'] == 'passed', replayed
    assert replayed['claims_frozen'] == 18 and replayed['claims_recomputed'] == 8 and replayed['pending_claims'] == 10


def test_completed_coordinator_without_verify_is_pending_instead_of_abstained(lifecycle):
    lifecycle.boundary.cancel_first_binding = False
    lifecycle.boundary.batch_coordinator = True
    lifecycle.boundary.coordinator_verify = False
    owner = new_agent(lifecycle)
    result = owner.run()
    assert result['model']['status'] == 'partial'
    assert len(result['claims']) == len(result['pending_claim_ids']) == 2
    assert result['results'] == [] and sum(result['summary'].values()) == 0
    assert result['execution']['native_verified_claim_ids'] == []
    assert {row['reason'] for row in result['coverage']['mentions'] if row.get('claim_id')} == {'native_verification_not_executed'}


def test_missing_a_submission_remains_partial_after_checkpoint_and_repair(lifecycle, monkeypatch):
    lifecycle.boundary.cancel_first_binding = False
    owner = new_agent(lifecycle)
    events = []
    owner.emit = lambda kind, **values: events.append((kind, values))
    original = owner.run_role
    skipped = []
    def without_planner(role, group, prompt, **options):
        if role == 'planner':
            skipped.append(list(group))
            return {'finish_reason': 'completed'}
        return original(role, group, prompt, **options)
    monkeypatch.setattr(owner, 'run_role', without_planner)
    owner.prepare()
    owner.start_runtime()
    try:
        owner.bind()
        owner.calculate()
        saved = read(lifecycle.out/'agent_state.json')
        assert saved['batches'][0]['phase'] == 'partial'
        assert saved['batches'][0]['bound_ids'] == []
        assert saved['batches'][0]['submitted_ids']['planner'] == []
        assert len(saved['batches'][0]['submitted_ids']['verifier']) == 2
        assert len(saved['pending_claim_ids']) == 2 and owner.results == []
        assert len(skipped) == 2  # Initial plus one repair of this two-claim group.
        owner.batch_records = copy.deepcopy(saved['batches'])
        owner.bind()  # Same checkpoint omissions cannot turn all([]) into complete.
        assert owner.batch_records[0]['phase'] == 'partial'
        assert all(detail['bound'] == 0 for kind, detail in events if kind == 'batch')
        assert all(detail['phase'] == 'partial' for kind, detail in events if kind == 'batch')
        audit = [json.loads(line) for line in (lifecycle.out/'audit.jsonl').read_text(encoding='utf-8').splitlines()]
        completed = [row for row in audit if row['type'] == 'batch_completed']
        assert completed and all(row['phase'] == 'partial' and not row['bound_ids'] for row in completed)
        assert all(row['bound'] == 0 and row['submitted'] == {'planner': 0, 'verifier': 2} for row in completed)
        assert all(detail['submitted'] == {'planner': 0, 'verifier': 2} for kind, detail in events if kind == 'batch')
        assert owner.ledger.to_dict()['partial']
    finally:
        owner.runtime.close()


def test_fully_submitted_unknown_bindings_are_completed_x3_instead_of_pending(lifecycle):
    lifecycle.boundary.cancel_first_binding = False
    lifecycle.boundary.binding_transform = lambda fields: {**fields, 'account': '', 'scope': '', 'relation': ''}
    owner = new_agent(lifecycle)
    events = []
    owner.emit = lambda kind, **values: events.append((kind, values))
    result = owner.run()
    assert result['model']['status'] == 'success', result['model']['error']
    assert owner.batch_records[0]['phase'] == 'completed'
    assert len(owner.batch_records[0]['bound_ids']) == 2
    assert owner.batch_records[0]['agreed_ids'] == owner.batch_records[0]['verified_ids'] == []
    assert result['pending_claim_ids'] == []
    assert len(result['results']) == 2 and all(row['abstention_code'] == 'X3' for row in result['results'])
    assert next(detail['bound'] for kind, detail in events if kind == 'batch') == 2


def test_missing_a_batch_stays_partial_on_real_host_checkpoint_resume(lifecycle, monkeypatch):
    lifecycle.boundary.cancel_first_binding = False
    original = new_agent(lifecycle)
    def omit_planner(owner):
        native_role = owner.run_role
        def role(role_name, group, prompt, **options):
            if role_name == 'planner':
                return {'finish_reason': 'completed'}
            return native_role(role_name, group, prompt, **options)
        monkeypatch.setattr(owner, 'run_role', role)
    omit_planner(original)
    binding = original.bind
    def cancel_after_binding():
        binding()
        original.cancelled.set()
        raise InterruptedError('Fixture cancellation after missing-A batch checkpoint')
    monkeypatch.setattr(original, 'bind', cancel_after_binding)
    interrupted = original.run()
    checkpoint = read(lifecycle.out/'agent_state.json')
    assert interrupted['model']['status'] == 'cancelled'
    assert checkpoint['batches'][0]['phase'] == 'partial'
    assert checkpoint['batches'][0]['bound_ids'] == []
    assert len(checkpoint['pending_claim_ids']) == 2
    assert not (lifecycle.out/'run_manifest.json').exists()
    resumed = new_agent(lifecycle)
    omit_planner(resumed)
    result = resumed.run(resume=True)
    assert result['model']['status'] == 'partial'
    assert resumed.batch_records[0]['phase'] == 'partial'
    assert resumed.batch_records[0]['bound_ids'] == []
    assert len(result['pending_claim_ids']) == 2 and result['results'] == []
    assert result['coverage']['partial']


def test_cancelled_coordinator_resumes_original_scope_and_fact_rule_authority(lifecycle):
    lifecycle.boundary.cancel_first_binding = False
    cancelled = threading.Event()

    def after_tool(runtime, _spec, name, _args, _output):
        if name == 'mj_get_fact' and not cancelled.is_set():
            prior = runtime.owner.native_verified_ids()
            if prior:
                cancelled.set()
                runtime.owner.cancelled.set()
                raise HarnessError('Fixture cancellation after next fact was read')

    lifecycle.boundary.after_tool = after_tool
    original = new_agent(lifecycle)
    interrupted = original.run()
    coordinator = next(record for record in original.session_records if record['role'] == 'coordinator')
    assert interrupted['model']['status'] == 'cancelled'
    assert len(interrupted['results']) == len(interrupted['pending_claim_ids']) == 1
    assert len(original.native_verified_ids()) == 1
    assert coordinator['completed'] is False
    original_spec = copy.deepcopy(coordinator['spec'])
    restored = {}

    def prompt_hook(runtime, spec, _cancellation):
        if spec['role'] == 'coordinator':
            assert spec == original_spec
            identity = runtime.owner.dispatcher.sessions[spec['session_id']]
            restored['facts'] = set(identity['facts_read'])
            restored['rules'] = set(identity['rules_read'])

    lifecycle.boundary.after_tool = None
    lifecycle.boundary.prompt_hook = prompt_hook
    resumed = new_agent(lifecycle)
    completed = resumed.run(resume=True)
    assert completed['model']['status'] == 'success', completed['model']['error']
    assert restored['facts'] == set(coordinator['mention_ids'])
    assert restored['rules'] == {'E1-value'}
    resumed_coordinators = [record for record in resumed.session_records if record['role'] == 'coordinator']
    assert len(resumed_coordinators) == 1 and resumed_coordinators[0]['spec'] == original_spec
    assert len(completed['results']) == 2 and completed['pending_claim_ids'] == []


def test_native_verified_ids_require_complete_outputs_and_order_within_same_session(lifecycle):
    owner = new_agent(lifecycle)
    owner.prepare()
    mids = [claim.claim_id for claim in owner.claims]

    def event(sid, tool, params, output):
        return {'role': 'coordinator', 'session_id': sid, 'status': 'success', 'tool': tool, 'input': params, 'output': output}

    owner.dispatcher.trace = [
        event('a', 'mj_get_facts', {'binding_ids': mids}, {'items': [{'binding_id': mids[0], 'fact': None}]}),
        event('a', 'mj_get_rule', {'rule_id': 'missing'}, {'found': False}),
        event('a', 'mj_verify_bindings', {'binding_ids': mids}, {'items': [{'binding_id': mid, 'claim_id': mid, 'status': 'abstained'} for mid in mids]}),
        event('b', 'mj_get_rule', {'rule_id': 'E1'}, {'id': 'E1-value'}),
        event('b', 'mj_verify', {'binding_id': mids[0]}, {'claim_id': mids[0], 'status': 'verified'}),
    ]
    assert owner.native_verified_ids() == set()
    owner.dispatcher.trace += [
        event('a', 'mj_get_rule', {'rule_id': 'E1'}, {'id': 'E1-value'}),
        event('a', 'mj_verify_bindings', {'binding_ids': mids}, {'items': [{'binding_id': mid, 'claim_id': mid, 'status': 'abstained'} for mid in mids]}),
    ]
    assert owner.native_verified_ids() == {mids[0]}
    assert owner.native_verified_ids(session_id='b') == set()


def test_default_scope_without_source_proof_is_preflighted_without_deleting_mentions(lifecycle):
    draft = '甲公司2024年营业收入100元。\n' * 8 + '甲公司2024年合并营业收入100元。\n' * 2
    Path(lifecycle.request['draft']).write_text(draft, encoding='utf-8')
    lifecycle.boundary.cancel_first_binding = False
    owner = new_agent(lifecycle)
    result = owner.run()
    assert result['model']['status'] == 'success', result['model']['error']
    mids = [claim['claim_id'] for claim in result['claims']]
    assert len(mids) == len(result['results']) == 10
    assert result['model']['source_candidates'] == 10
    assert result['model']['semantic_claims'] == result['model']['mapped_claims'] == 2
    assert result['model']['binding_submissions'] == {'planner': 2, 'verifier': 2}
    first = next(record for record in owner.session_records if record['role'] == 'planner')
    assert first['mention_ids'] == mids[8:]
    assert result['execution']['preflight_claim_ids'] == mids[:8]
    assert all('口径声明' in row['reason'] for row in result['results'] if row['claim_id'] in mids[:8])
    assert result['pending_claim_ids'] == []
    assert result['summary']['verified'] == 2 and result['summary']['abstained'] == 8


def test_unsupported_source_definitions_use_exact_kernel_preflight_without_model_deletion(lifecycle):
    from mingjing.replay import replay_run
    Path(lifecycle.request['draft']).write_text(
        '甲公司2024年合并营业收入100元。\n甲公司2024年主营业务收入80元。\n甲公司2024年营业收入CAGR 20%。', encoding='utf-8')
    lifecycle.boundary.cancel_first_binding = False
    lifecycle.boundary.batch_coordinator = True
    lifecycle.config['agent']['budget'].update(max_requests=3, max_model_calls=3)
    owner = new_agent(lifecycle)
    result = owner.run()
    assert result['model']['status'] == 'success', result['model']['error']
    assert len(result['claims']) == len(result['results']) == 3
    preflight = set(result['execution']['preflight_claim_ids'])
    assert len(preflight) == 2
    assert all(preflight.isdisjoint(record['mention_ids']) for record in owner.session_records)
    assert {row['abstention_code'] for row in result['results'] if row['claim_id'] in preflight} == {'X2', 'X3'}
    assert not preflight.intersection(result['execution']['native_verified_claim_ids'])
    assert result['summary'] == {'verified': 1, 'violated': 0, 'abstained': 2}
    assert result['pending_claim_ids'] == []
    assert replay_run(lifecycle.out)['status'] == 'passed'


def test_analysis_runs_native_report_rule_analysis_chain_and_uses_frozen_report_slots(lifecycle):
    from mingjing.replay import replay_run
    Path(lifecycle.request['draft']).write_text('财报分析任务。', encoding='utf-8')
    lifecycle.request['task_type'] = 'analysis'
    lifecycle.boundary.cancel_first_binding = False
    owner = new_agent(lifecycle)
    result = owner.run()
    assert result['model']['status'] == 'success', result['model']['error']
    assert result['claims'] == [] and result['results'] == []
    assert result['model']['source_candidates'] == result['model']['semantic_claims'] == 0
    assert read(lifecycle.out/'run_manifest.json')['model']['mode'] == 'harness'
    assert result['execution']['native_analysis_completed']
    assert [record['role'] for record in owner.session_records] == ['coordinator', 'reporter']
    coordinator = owner.session_records[0]
    events = [event for event in result['tool_trace'] if event['session_id'] == coordinator['session_id']]
    assert [event['tool'] for event in events] == ['mj_list_reports', 'mj_get_rule', 'mj_analyze']
    source_periods = {period for report in events[0]['output'] for period in report['periods']}
    assert events[2]['input']['period'] in source_periods
    assert result['analysis']['company'] == '甲公司' and result['analysis']['metrics']
    assert result['agent_report'] and '{{' not in result['agent_report']
    offered = [definition['name'] for definition in coordinator['spec']['tool_definitions']]
    assert offered == ['mj_analyze', 'mj_get_rule', 'mj_list_reports']
    assert {definition['name'] for definition in owner.session_records[1]['spec']['tool_definitions']} == {'mj_analyze', 'mj_submit_report'}
    replayed = replay_run(lifecycle.out)
    assert replayed['status'] == 'passed' and replayed['analysis_recomputed']


def test_analysis_completed_text_without_native_analyze_falls_back_explicitly(lifecycle):
    Path(lifecycle.request['draft']).write_text('财报分析任务。', encoding='utf-8')
    lifecycle.request['task_type'] = 'analysis'
    lifecycle.boundary.cancel_first_binding = False
    lifecycle.boundary.coordinator_verify = False
    result = new_agent(lifecycle).run()
    assert result['model']['status'] == 'fallback'
    assert result['model']['mode'] == 'kernel'
    assert '分析工具闭环' in result['model']['error']
    assert result['execution']['native_analysis_completed'] is False
    assert read(lifecycle.out/'run_manifest.json')['model']['mode'] == 'kernel'
    assert result['analysis']['metrics']


def test_user_scope_clarification_is_locked_and_advised_without_changing_original_claims(lifecycle):
    from mingjing.evidence import sha256_file
    from mingjing.replay import replay_run
    text = '甲公司2024年营业收入100元。'
    Path(lifecycle.request['draft']).write_text(text, encoding='utf-8')
    lifecycle.request['scope_declaration'] = '合并'
    lifecycle.boundary.cancel_first_binding = False
    owner = new_agent(lifecycle)
    result = owner.run()
    assert result['model']['status'] == 'success', result['model']['error']
    resource = result['clarification_resource']
    assert resource['provenance'] == 'user_supplied_scope' and resource['draft_sha256'] == sha256_file(Path(lifecycle.request['draft']))
    assert resource['sha256'] == sha256_file(lifecycle.out/'clarifications.json')
    assert any(item['id'] == resource['resource_id'] and item['sha256'] == resource['sha256'] for item in owner.snapshot['resources'])
    assert result['draft_text'] == text
    assert result['claims'][0]['candidate_value'] == '100' and result['claims'][0]['numeric_text'] == '100元'
    assert result['claims'][0]['table_context']['scope_declaration']['provenance'] == 'user_supplied_scope'
    assert result['results'][0]['status'] == 'verified' and 'A3' in result['results'][0]['advisories']
    assert any('用户补充澄清，非原文明示' in basis for basis in result['results'][0]['basis'])
    assert result['execution']['preflight_claim_ids'] == []
    assert result['execution']['native_verified_claim_ids'] == [result['claims'][0]['claim_id']]
    assert replay_run(lifecycle.out)['status'] == 'passed'


def test_user_declaration_cannot_override_explicit_source_scope(lifecycle):
    lifecycle.request['scope_declaration'] = '母公司'
    with pytest.raises(ValueError, match='口径澄清与原文'):
        new_agent(lifecycle).run()
    assert not (lifecycle.out/'run_manifest.json').exists()


def test_analysis_selected_parent_scope_is_used_by_native_tool_and_frozen_kernel(lifecycle):
    from mingjing.replay import replay_run
    Path(lifecycle.request['draft']).write_text('财报分析任务。', encoding='utf-8')
    lifecycle.request.update(task_type='analysis', scope_declaration='母公司')
    facts_path = Path(lifecycle.request['corpus'])/'facts.json'
    facts = read(facts_path)
    for fact in facts:
        fact['scope'] = '母公司'
    facts_path.write_text(json.dumps(facts, ensure_ascii=False), encoding='utf-8')
    result = new_agent(lifecycle).run()
    assert result['model']['status'] == 'success', result['model']['error']
    assert result['execution']['native_analysis_completed']
    assert result['analysis']['scope'] == '母公司'
    analyzed = next(event for event in result['tool_trace'] if event['role'] == 'coordinator' and event['tool'] == 'mj_analyze')
    assert any(row['record'].get('status') == 'verified' for row in analyzed['output']['analysis'])
    assert replay_run(lifecycle.out)['status'] == 'passed'


@pytest.mark.parametrize('interruption', ['budget', 'cancel'])
def test_preflight_and_unresolved_results_complete_coverage_even_when_native_batch_interrupts(lifecycle, interruption):
    text = ('甲公司2024年营业收入100元。\n甲公司2024年主营业务收入80元。\n'
            '甲公司2024年财务资本质量示例比率3.4倍。\n' + '甲公司2024年合并营业收入100元。\n' * 10)
    Path(lifecycle.request['draft']).write_text(text, encoding='utf-8')
    lifecycle.boundary.batch_coordinator = True
    if interruption == 'budget':
        lifecycle.boundary.cancel_first_binding = False
        lifecycle.config['agent']['budget'].update(max_requests=3, max_model_calls=3)
    result = new_agent(lifecycle).run()
    assert result['model']['status'] == ('partial' if interruption == 'budget' else 'cancelled')
    rows = {row['claim_id']: row for row in result['coverage']['mentions'] if row.get('claim_id')}
    judged = {row['claim_id'] for row in result['results']}
    pending = set(result['pending_claim_ids'])
    assert set(rows) == {claim['claim_id'] for claim in result['claims']}
    assert set(rows) == judged | pending and judged.isdisjoint(pending)
    assert sum(row['status'] == 'pending' for row in rows.values()) == len(pending)
    assert sum(row['status'] != 'pending' for row in rows.values()) == len(result['results'])
    assert all(rows[mid]['status'] != 'pending' for mid in result['execution']['preflight_claim_ids'])
    assert all(row['claim_id'] in judged for row in result['claims'] if row['parse_status'] != 'parsed')


def test_prepare_archives_original_dimensions_before_any_model_and_includes_them_in_lock(lifecycle):
    from mingjing.evidence import sha256_file
    from mingjing.source_semantics import source_dimensions_from_text
    from dataclasses import replace
    owner = new_agent(lifecycle)
    owner.prepare()
    archived = read(lifecycle.out/'source_dimensions.json')
    assert archived['schema_version'] == 'source-dimensions.v1'
    assert len(archived['dimensions']) == len(owner.claims) == 2
    assert archived['draft_sha256'] == sha256_file(owner.draft)
    assert owner.source_dimensions_resource['sha256'] == sha256_file(lifecycle.out/'source_dimensions.json')
    assert any(item['id'] == 'source-dimensions' and item['sha256'] == owner.source_dimensions_resource['sha256']
               for item in owner.snapshot['resources'])
    assert lifecycle.boundary.instances == []
    assert all(not set(proof).intersection({'value', 'candidate_value', 'expected', 'observed'}) for proof in archived['dimensions'])
    original = owner.claims[0]
    altered = replace(original, account='NP', period='2023Y', relation='yoy', base_period='2022Y')
    dimensions = source_dimensions_from_text(altered)
    assert dimensions['account'] == 'REVENUE' and dimensions['period'] == '2024Y'
    assert dimensions['relation'] == 'value' and dimensions['base_period'] == ''
    assert original.candidate_value == altered.candidate_value == '100'


@pytest.mark.parametrize('wrong_binding', [{'account': 'NP'}, {'period': '2023Y'},
                                         {'relation': 'yoy', 'base_period': '2023Y'}])
def test_both_binders_cannot_verify_different_source_dimensions_by_matching_another_fact(lifecycle, wrong_binding):
    Path(lifecycle.request['draft']).write_text('甲公司2024年合并营业收入100元。', encoding='utf-8')
    lifecycle.boundary.cancel_first_binding = False
    lifecycle.boundary.binding_transform = lambda binding: {**binding, **wrong_binding}
    facts_path = Path(lifecycle.request['corpus'])/'facts.json'
    facts = read(facts_path)
    facts[0]['value'] = '200'
    facts[1]['value'] = '100'
    facts.append({**facts[0], 'fact_id': 'fixture-2024-np', 'account': 'NP', 'value': '100'})
    facts_path.write_text(json.dumps(facts, ensure_ascii=False), encoding='utf-8')
    (facts_path.parent/'report.txt').write_text(
        '甲公司合并财务报表。2024年营业收入200元，净利润100元，2023年营业收入100元。', encoding='utf-8')
    result = new_agent(lifecycle).run()
    assert result['summary']['verified'] == 0, result['results']
    # A model that repeats the contradicted binding after the bounded repair
    # has not submitted an admissible A/B binding. Do not turn that execution
    # omission into a completed X3 verdict or count it as native verification.
    assert len(result['claims']) == 1 and result['results'] == []
    proof = result['claims'][0]['table_context']['source_dimensions']
    assert proof['account'] == 'REVENUE' and proof['period'] == '2024Y' and proof['relation'] == 'value'
    assert result['model']['status'] == 'partial' and result['partial']
    assert result['pending_claim_ids'] == [result['claims'][0]['claim_id']]
    assert result['coverage']['counts']['pending'] == 1
    bindings = read(lifecycle.out/'bindings.json')
    assert not bindings['A'] and not bindings['B']
    rejected = [row for row in result['tool_trace'] if row['status'] == 'denied']
    assert len(rejected) == 4
    assert {row['role'] for row in rejected} == {'planner', 'verifier'}
    assert all(row['output']['error']['code'] == 'binding_source_conflict' for row in rejected)
    assert result['execution']['native_verified_claim_ids'] == []


def test_prepare_forwards_cancel_progress_and_one_budget_deadline_to_all_material_reads(lifecycle, monkeypatch):
    load, partition = agent_module.load_or_extract_facts, agent_module.partition_document
    reads, events = [], []

    def wrapped_load(*args, **kwargs):
        reads.append(('facts', kwargs))
        kwargs['progress_callback']('fixture-material-progress', current=1, total=1)
        return load(*args, **kwargs)

    def wrapped_partition(*args, **kwargs):
        reads.append(('partition', kwargs))
        return partition(*args, **kwargs)

    monkeypatch.setattr(agent_module, 'load_or_extract_facts', wrapped_load)
    monkeypatch.setattr(agent_module, 'partition_document', wrapped_partition)
    owner = new_agent(lifecycle)
    owner.emit = lambda kind, **values: events.append((kind, values))
    owner.prepare()
    assert [kind for kind, _ in reads] == ['partition', 'facts', 'partition']
    for _kind, options in reads:
        assert options['cancel_event'] is owner.cancelled
        assert options['deadline_monotonic'] == owner.started + owner.budget['max_runtime_s']
        assert options['progress_callback'].__self__ is owner
    assert ('phase', {'name': 'fixture-material-progress', 'current': 1, 'total': 1}) in events


def _mixed_company_facts(lifecycle):
    from mingjing.facts import fact_from_dict
    corpus = Path(lifecycle.request['corpus'])
    facts = read(corpus/'facts.json')
    for fact in facts:
        fact['source_path'] = str(corpus/'report.txt')
    peer = {**facts[0], 'fact_id': 'peer-revenue', 'company': '乙公司', 'value': '777777'}
    return [fact_from_dict(fact) for fact in [*facts, peer]]


@pytest.mark.parametrize('source', ['primary', 'fallback'])
def test_prepare_filters_external_fact_materials_to_exact_selected_company(lifecycle, monkeypatch, source):
    facts = _mixed_company_facts(lifecycle)
    if source == 'primary':
        monkeypatch.setattr(agent_module, 'load_or_extract_facts', lambda *args, **kwargs: facts)
    else:
        loader = agent_module.load_or_extract_facts
        primary = Path(lifecycle.request['corpus']).resolve()
        monkeypatch.setattr(agent_module, 'load_or_extract_facts',
            lambda path, *args, **kwargs: [] if Path(path).resolve() == primary else loader(path, *args, **kwargs))
        fallback = Path(lifecycle.request['draft']).parent/'mixed-facts.json'
        fallback.write_text(json.dumps([fact.to_dict() for fact in facts], ensure_ascii=False), encoding='utf-8')
        lifecycle.request['facts'] = str(fallback)
    owner = new_agent(lifecycle)
    owner.prepare()
    assert owner.company == '甲公司'
    assert owner.dispatcher.task_company == '甲公司'
    assert len(owner.facts) == 2 and {fact.company for fact in owner.facts} == {'甲公司'}
    assert {fact.company for fact in owner.dispatcher.store.facts} == {'甲公司'}
    assert all(report['companies'] == ['甲公司'] for report in owner.dispatcher.reports)
    assert all(str(fact.value) != '777777' for fact in owner.dispatcher.store.facts)
    assert lifecycle.boundary.instances == []


@pytest.mark.parametrize('source', ['primary', 'fallback'])
def test_prepare_rejects_unselected_multiple_companies_before_model_start(lifecycle, monkeypatch, source):
    facts = _mixed_company_facts(lifecycle)
    lifecycle.request['company'] = ''
    if source == 'primary':
        monkeypatch.setattr(agent_module, 'load_or_extract_facts', lambda *args, **kwargs: facts)
    else:
        loader = agent_module.load_or_extract_facts
        primary = Path(lifecycle.request['corpus']).resolve()
        monkeypatch.setattr(agent_module, 'load_or_extract_facts',
            lambda path, *args, **kwargs: [] if Path(path).resolve() == primary else loader(path, *args, **kwargs))
        fallback = Path(lifecycle.request['draft']).parent/'mixed-facts.json'
        fallback.write_text(json.dumps([fact.to_dict() for fact in facts], ensure_ascii=False), encoding='utf-8')
        lifecycle.request['facts'] = str(fallback)
    owner = new_agent(lifecycle)
    with pytest.raises(ValueError, match='多家公司.*选择公司'):
        owner.prepare()
    assert lifecycle.boundary.instances == []
    assert not hasattr(owner, 'dispatcher') and not (lifecycle.out/'agent_lock.json').exists()


def test_prepare_infers_only_a_single_unselected_material_company(lifecycle):
    lifecycle.request['company'] = ''
    owner = new_agent(lifecycle)
    owner.prepare()
    assert owner.company == '甲公司'
    assert {fact.company for fact in owner.facts} == {'甲公司'}
    assert {claim.company for claim in owner.claims} == {'甲公司'}
