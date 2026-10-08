"""Knowledge material freezing, source slots and both kernel/native hosts."""
import copy
import io
import json
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from mingjing.agent import write_json
from mingjing.cli import build_parser, command_check
from mingjing.knowledge import KnowledgeRegistry
from mingjing.financial_tools import FinancialDispatcher, ToolDenied, tool_definitions
from mingjing.mcp_services import ReadOnlyMCP, load_dispatcher
from mingjing.replay import replay_run
from mingjing.session_evidence import load_source_evidence, SessionEvidenceError
from mingjing.sessions import FinancialSessions
from test_agent_lifecycle import lifecycle, new_agent, read, frozen_bytes
from test_knowledge_materials import HEADER


def add_knowledge(lifecycle, *, text=None):
    root = lifecycle.out.parent/'accounting-kb'
    root.mkdir()
    source = root/'policy.md'
    source.write_text(HEADER+(text or '会计政策变更应当采用追溯调整法，原文举例100万元。'), encoding='utf-8')
    lifecycle.request['knowledge_root'] = str(root)
    return source


def test_native_host_freezes_distinct_knowledge_inputs_without_fact_contamination(lifecycle):
    source = add_knowledge(lifecycle)
    original_facts = read(Path(lifecycle.request['corpus'])/'facts.json')
    lifecycle.boundary.cancel_first_binding = False
    agent = new_agent(lifecycle)
    result = agent.run()
    assert result['model']['status'] == 'success'
    assert result['knowledge']['enabled'] and result['knowledge']['documents'] == 1
    assert result['knowledge']['authorizes_judgement'] is False
    assert [row['value'] for row in result['facts']] == [row['value'] for row in original_facts]
    assert not any(Path(row['source_path']) == source for row in result['facts'])
    lock = read(lifecycle.out/'agent_lock.json')
    assert lock['knowledge']['knowledge_lock'] == result['knowledge']['knowledge_lock']
    assert 'knowledge-lock' in {row['id'] for row in lock['resources']}
    manifest = read(lifecycle.out/'run_manifest.json')
    assert str(source) in {row['path'] for row in manifest['inputs']}
    outputs = {row['path']: row for row in manifest['outputs']}
    knowledge_inputs = [row for row in manifest['inputs'] if Path(row['path']).is_relative_to(lifecycle.out)]
    for row in knowledge_inputs:
        relative = Path(row['path']).relative_to(lifecycle.out).as_posix()
        if relative == 'knowledge_lock.json' or relative.startswith('knowledge-cache/'):
            assert outputs[relative]['sha256'] == row['sha256'] and outputs[relative]['bytes'] == row['bytes']
    assert replay_run(lifecycle.out)['status'] == 'passed'
    assert 'mj_kb_search' in {tool['name'] for tool in tool_definitions('planner')}
    assert 'mj_kb_search' not in {tool['name'] for tool in tool_definitions('reporter')}


def test_resume_reuses_locked_knowledge_cache_without_rewriting_parent(lifecycle, monkeypatch):
    add_knowledge(lifecycle)
    new_agent(lifecycle).run()
    parent = frozen_bytes(lifecycle.out)
    from mingjing import knowledge
    # Observe calls, without changing knowledge module's guarded code hash.
    original_scan = KnowledgeRegistry.scan
    calls = []
    def scan(self):
        calls.append(self.root)
        return original_scan(self)
    monkeypatch.setattr(KnowledgeRegistry, 'scan', scan)
    # Controlled lifecycle tests intentionally freeze a fixture code/runtime
    # fingerprint, so the dedicated registry's implementation check remains
    # active and correctly rejects changed parser code before any mutation.
    with pytest.raises(ValueError, match='implementation changed'):
        new_agent(lifecycle).run(resume=True)
    assert frozen_bytes(lifecycle.out) == parent and not calls


def test_question_kb_slots_are_raw_blind_roles_mask_values_and_reporter_cannot_use_them(lifecycle):
    source = add_knowledge(lifecycle)
    source.write_text(source.read_text(encoding='utf-8').replace('会计政策准则', '案例100万元'), encoding='utf-8')
    agent = new_agent(lifecycle)
    agent.prepare()
    d = agent.dispatcher
    for role in ('question', 'planner', 'reporter'):
        d.register_session(role, role)
    def call(role, tool, params):
        return d.dispatch(dict(session_id=role, role=role, run_id=d.run_id, resource_lock=d.resource_lock,
                               call_id=str(len(d.calls)), tool_name=tool, params=params))
    raw = call('question', 'mj_kb_search', {'query': '追溯调整法'})
    assert raw['slot_ids'] and '100万元' in raw['hits'][0]['text']
    assert raw['hits'][0]['material_kind'] == 'accounting_knowledge'
    blind = call('planner', 'mj_kb_search', {'query': '追溯调整法'})
    assert '100万元' not in str(blind) and not blind['slot_ids']
    listing = call('planner', 'mj_kb_list', {})
    assert '100万元' not in listing['documents'][0]['metadata']['title']
    assert listing['documents'][0]['knowledge_id'] == raw['hits'][0]['knowledge_id']
    block_id = blind['hits'][0]['block_id']
    assert block_id == raw['hits'][0]['block_id']
    assert call('planner', 'mj_kb_read', {'block_id': block_id})['block_id'] == block_id
    before = copy.deepcopy(d.store.to_list())
    assert call('question', 'mj_submit_report', {'template': raw['slot_template'], 'slots': raw['slot_ids']})['accepted']
    assert '不属于签发规则' in d.templates['question'] and d.store.to_list() == before
    with pytest.raises(ToolDenied):
        call('reporter', 'mj_kb_search', {'query': '追溯调整法'})


def test_kernel_task_mcp_and_followup_use_locked_md_reference_without_fabricated_page(lifecycle):
    source = add_knowledge(lifecycle, text='\n会计政策变更应当采用追溯调整法。\n\n会计政策变更应当采用追溯调整法。\n\n')
    knowledge_root = source.parent
    nested = knowledge_root/'会计准则'/'实施问答'
    nested.mkdir(parents=True)
    moved = nested/source.name
    source.replace(moved)
    source = moved
    args = build_parser().parse_args(['check', '--draft', lifecycle.request['draft'], '--corpus', lifecycle.request['corpus'],
        '--company', '甲公司', '--period', '2024Y', '--report-date', '2025-03-31', '--knowledge-root', str(knowledge_root),
        '--mode', 'kernel', '--out', str(lifecycle.out), '--run-id', 'lifecycle-fixture'])
    assert command_check(args) == 0
    data = read(lifecycle.out/'results.json')
    assert data['knowledge']['enabled'] and replay_run(lifecycle.out)['status'] == 'passed'
    dispatcher = load_dispatcher(lifecycle.out)
    facade = ReadOnlyMCP('mj-rules', dispatcher)
    assert 'mj_kb_search' in facade.names
    found = facade.call('mj_kb_search', {'query': '追溯调整法'})
    assert found['slot_ids'] and not any(row['id'].startswith('kb-') for row in dispatcher.reparse_resources)
    from mingjing.reparse_inputs import load_parent_context
    parent_context = load_parent_context(lifecycle.out)
    kb_resources = [row for row in parent_context['source_resources'] if Path(row['path']) == source]
    assert kb_resources and all(row['id'].startswith('kb-input-') for row in kb_resources)

    class Config:
        def get(self):
            return copy.deepcopy(lifecycle.config)
        def secret(self, *_):
            raise AssertionError('No API in controlled knowledge followup')
    holder = SimpleNamespace(out=lifecycle.out, request=lifecycle.request)
    sessions = FinancialSessions(lifecycle.out.parent/'kb-conversations', lambda _: holder, Config())
    sid = sessions.create('lifecycle-fixture')['session_id']
    sessions.cancel_events[sid] = threading.Event()
    def plan(runtime, spec, prompt, call):
        assert 'mj_kb_search' in prompt and '最少槽位' in prompt
        found = call('mj_kb_search', {'query': '追溯调整法', 'limit': 3})
        call('mj_submit_report', {'template': found['slot_template'], 'slots': found['slot_ids']})
    lifecycle.boundary.question_plan = plan
    before = frozen_bytes(lifecycle.out)
    sessions._answer_inner(sid, '准则要求会计政策变更怎样处理？')
    state = sessions.get(sid)
    assert state['status'] == 'ready', state['messages']
    answer = state['messages'][-1]
    assert len(answer['source_references']) == 2
    assert len({row['evidence_id'] for row in answer['source_references']}) == 2
    proof = answer['source_references'][0]
    assert proof['page'] is None and proof['material_kind'] == 'accounting_knowledge'
    assert proof['metadata']['authority_status'] == 'declared_unverified'
    evidence = load_source_evidence(sessions.root, state, answer['message_id'], proof['evidence_id'], lifecycle.config)
    assert evidence['quote_text'] and evidence['source_path'] == str(source)
    assert evidence['page'] is None and not evidence['image_available']
    assert frozen_bytes(lifecycle.out) == before
    from mingjing.server import _export_run
    class ExportConfig:
        def get(self):
            return copy.deepcopy(lifecycle.config)
        def secret(self, *_):
            return None
    (lifecycle.out/'knowledge-cache'/'not-registered.json').write_text('{"unregistered": true}', encoding='utf-8')
    exported = _export_run(SimpleNamespace(id='lifecycle-fixture', out=lifecycle.out), ExportConfig(), sessions)
    with zipfile.ZipFile(io.BytesIO(exported)) as archive:
        assert archive.read('knowledge_lock.json') == (lifecycle.out/'knowledge_lock.json').read_bytes()
        caches = [name for name in archive.namelist() if name.startswith('knowledge-cache/')]
        assert len(caches) == 1
        assert archive.read(caches[0]) == (lifecycle.out/caches[0]).read_bytes()
    denied_config = copy.deepcopy(lifecycle.config)
    denied_config['security'].update(allow_external_paths=False, allowed_roots=[str(lifecycle.out)])
    with pytest.raises(SessionEvidenceError) as denied:
        load_source_evidence(sessions.root, state, answer['message_id'], proof['evidence_id'], denied_config)
    assert denied.value.status == 403
    source.write_text('Changed original knowledge source', encoding='utf-8')
    with pytest.raises(SessionEvidenceError) as error:
        load_source_evidence(sessions.root, state, answer['message_id'], proof['evidence_id'], lifecycle.config)
    assert error.value.status == 409
    (lifecycle.out/caches[0]).write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='Frozen output changed'):
        _export_run(SimpleNamespace(id='lifecycle-fixture', out=lifecycle.out), ExportConfig(), sessions)


def test_kb_source_changed_after_slot_read_cannot_be_submitted(lifecycle):
    source = add_knowledge(lifecycle)
    agent = new_agent(lifecycle)
    agent.prepare()
    d = agent.dispatcher
    d.register_session('question', 'question')
    envelope = dict(session_id='question', role='question', run_id=d.run_id, resource_lock=d.resource_lock)
    found = d.dispatch(dict(envelope, call_id='read', tool_name='mj_kb_search', params={'query': '追溯调整法'}))
    source.write_text('Modified external standard', encoding='utf-8')
    with pytest.raises(ToolDenied, match='cutoff'):
        d.dispatch(dict(envelope, call_id='submit', tool_name='mj_submit_report', params={'template': found['slot_template'], 'slots': found['slot_ids']}))
    assert not d.templates
