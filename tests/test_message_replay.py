"""Actual pinned Node AgentLoop with an in-process, keyless model boundary."""
from __future__ import annotations

import copy
import json
import threading
from dataclasses import asdict
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from mingjing.cli import build_parser, command_check
from mingjing.config import AppConfig
from mingjing.evidence import make_evidence_id, sha256_file
from mingjing.agent import write_json, orchestration_fingerprint
from mingjing.models import Fact
from mingjing.providers import ModelConfig, ProviderConfig
from mingjing.sessions import FinancialSessions
from test_native_runtime_safety import loop_carrier


@pytest.fixture
def native_conversation(tmp_path, loop_carrier, request):
    counters = {'requests': 0, 'credentials': 0}
    use_knowledge = bool(getattr(request, 'param', {}).get('knowledge'))

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            step = counters['requests'] % 3
            counters['requests'] += 1
            if step == 0:
                name, params = ('mj_kb_search', {'query': '追溯调整法', 'limit': 1}) if use_knowledge else ('mj_search_blocks', {'query': '营业收入', 'limit': 1})
            elif step == 1:
                tool = next(row for row in reversed(payload['messages']) if row['role'] == 'tool')
                content = tool['content']
                if isinstance(content, list):
                    content = ''.join(row.get('text', '') for row in content)
                hits = json.loads(content)
                found = hits if use_knowledge else hits[0]
                assert found and found.get('slot_ids'), hits
                name, params = 'mj_submit_report', {'template': found['slot_template'], 'slots': found['slot_ids']}
            else:
                name = None
            delta = ({'tool_calls': [{'index': 0, 'id': 'local-'+str(counters['requests']), 'type': 'function',
                                     'function': {'name': name, 'arguments': json.dumps(params)}}]}
                     if name else {'content': 'Guarded answer submitted.'})
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            for chunk in (
                {'id': 'local', 'model': 'fixture', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
                {'id': 'local', 'model': 'fixture', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'tool_calls' if name else 'stop'}],
                 'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}},
            ):
                self.wfile.write(('data: '+json.dumps(chunk)+'\n\n').encode('utf-8'))
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()

    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    cfg = AppConfig().to_dict()
    cfg['revision'] = 1
    cfg['providers'] = {'fixture': asdict(ProviderConfig(protocol='deepseek',
        base_url=f'http://127.0.0.1:{server.server_port}', credential_ref='fixture-key', retries=0,
        models=[ModelConfig(id='fixture')]))}
    cfg['agent']['routing']['default'] = {'provider': 'fixture', 'model': 'fixture'}
    cfg['agent']['followup_budget'].update(max_runtime_s=60, max_model_calls=12, max_tool_calls=80)
    draft = tmp_path/'draft.md'
    draft.write_text('甲公司2024年合并营业收入100元。', encoding='utf-8')
    corpus = tmp_path/'corpus'
    corpus.mkdir()
    report = corpus/'report.txt'
    report.write_text('甲公司合并财务报表\n2024年营业收入100元。', encoding='utf-8')
    doc_sha = sha256_file(report)
    fact = Fact('revenue', '甲公司', 'REVENUE', '2024Y', value=Decimal('100'),
                publish_date='2025-03-01', source_doc=doc_sha[:12], source_path='report.txt',
                evidence=(make_evidence_id(doc_sha, 1, '2024年营业收入100元。'),))
    (corpus/'facts.json').write_text(json.dumps([fact.to_dict()], ensure_ascii=False), encoding='utf-8')
    run = tmp_path/'run'
    args = build_parser().parse_args(['check', '--draft', str(draft), '--corpus', str(corpus), '--company', '甲公司',
        '--period', '2024Y', '--report-date', '2025-03-31', '--out', str(run), '--run-id', 'message-replay-fixture', '--mode', 'kernel'])
    if use_knowledge:
        from test_knowledge_materials import HEADER
        knowledge = tmp_path/'accounting-knowledge'
        knowledge.mkdir()
        (knowledge/'policy.md').write_text(HEADER+'会计政策变更应采用追溯调整法。', encoding='utf-8')
        args.knowledge_root = str(knowledge)
    assert command_check(args) == 0

    class ConfigBoundary:
        def get(self):
            return copy.deepcopy(cfg)

        def secret(self, ref):
            assert ref == 'fixture-key'
            counters['credentials'] += 1
            return 'local-model-no-private-key'

    holder = SimpleNamespace(out=run)
    sessions = FinancialSessions(tmp_path/'conversations', lambda _: holder, ConfigBoundary())
    sid = sessions.create('message-replay-fixture')['session_id']
    try:
        yield SimpleNamespace(sessions=sessions, sid=sid, counts=counters, run=run)
    finally:
        sessions.cancel_running()
        sessions.wait_running(5)
        server.shutdown()
        server.server_close()


def complete_turn(fixture, text):
    fixture.sessions.submit(fixture.sid, text)
    worker = fixture.sessions.workers.get(fixture.sid)
    if worker:
        worker.join(30)
    state = fixture.sessions.get(fixture.sid)
    assert state['status'] == 'ready', state['messages']
    message = state['messages'][-1]
    lock = json.loads((fixture.sessions.root/fixture.sid/message['message_id']/'message_lock.json').read_text(encoding='utf-8'))
    assert lock['agent'] == orchestration_fingerprint(state['config']), 'A controlled test fingerprint leaked into the native message lock'
    return message


def test_native_message_replay_fingerprint_alias_is_not_a_controlled_fixture():
    # This is also exercised after the controlled lifecycle file in the
    # combined regression, so imported aliases cannot retain its test lambda.
    from mingjing import message_replay
    assert message_replay.orchestration_fingerprint is orchestration_fingerprint


def test_first_and_continuing_message_replay_real_loop_without_credentials_or_network(native_conversation):
    f = native_conversation
    parent_before = {path: path.read_bytes() for path in f.run.rglob('*') if path.is_file()}
    first = complete_turn(f, '请引用营业收入原文。')
    second = complete_turn(f, '再引用同一材料中的营业收入原文。')
    assert first['source_references'] and second['source_references']
    assert first['agent_message_manifest_sha256'] and second['agent_message_manifest_sha256']
    original = {path: path.read_bytes() for path in (f.sessions.root/f.sid).rglob('*') if path.is_file()}
    counts = dict(f.counts)
    for message in (first, second):
        replay = f.sessions.replay_message(f.sid, message['message_id'])
        assert replay['status'] == 'passed', replay
        assert replay['real_harness_loop'] and replay['network_calls'] == 0
        assert replay['answer_equal'] and replay['references_equal'] and replay['tool_trace_equal']
        assert replay['cached_model_requests'] == 3 and replay['tool_calls'] == 2
    assert f.counts == counts
    assert all(path.read_bytes() == raw for path, raw in original.items())
    assert all(path.read_bytes() == raw for path, raw in parent_before.items())


def test_message_replay_detects_frozen_cache_corruption_before_runtime(native_conversation):
    f = native_conversation
    message = complete_turn(f, '请引用营业收入原文。')
    root = f.sessions.root/f.sid/message['message_id']
    (root/'agent_model_cache.json').write_text('{}', encoding='utf-8')
    counts = dict(f.counts)
    replay = f.sessions.replay_message(f.sid, message['message_id'])
    assert replay['status'] == 'failed' and not replay['real_harness_loop']
    assert 'hash changed' in replay['details'] and f.counts == counts


def test_partial_but_consistently_sealed_cache_fails_closed_before_provider(native_conversation):
    f = native_conversation
    message = complete_turn(f, '请引用营业收入原文。')
    root = f.sessions.root/f.sid/message['message_id']
    cache_path = root/'agent_model_cache.json'
    cache = json.loads(cache_path.read_text(encoding='utf-8'))
    del cache[next(iter(cache))]
    write_json(cache_path, cache)
    # Simulate a host capture that lost one response before freezing, rather
    # than a byte-corruption failure. The real loop must reject its cache miss.
    manifest_path = root/'message_manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    next(row for row in manifest['outputs'] if row['path'] == cache_path.name).update(
        sha256=sha256_file(cache_path), bytes=cache_path.stat().st_size)
    write_json(manifest_path, manifest)
    state = f.sessions.get(f.sid)
    state['messages'][-1]['agent_message_manifest_sha256'] = sha256_file(manifest_path)
    f.sessions.save(state)
    counts = dict(f.counts)
    replay = f.sessions.replay_message(f.sid, message['message_id'])
    assert replay['status'] == 'failed' and 'REPLAY_CACHE_MISS' in replay['details']
    assert f.counts == counts and replay['network_calls'] == 0


@pytest.mark.parametrize('native_conversation', [{'knowledge': True}], indirect=True)
def test_knowledge_message_real_loop_persists_raw_md_and_replays_without_provider(native_conversation):
    f = native_conversation
    first = complete_turn(f, '请引用会计政策变更的准则原文。')
    second = complete_turn(f, '再引用同一准则材料中追溯调整法的原文。')
    for message in (first, second):
        reference = message['source_references'][0]
        assert reference['material_kind'] == 'accounting_knowledge' and reference['page'] is None
        assert reference['metadata']['authority_status'] == 'declared_unverified'
        counts = dict(f.counts)
        replay = f.sessions.replay_message(f.sid, message['message_id'])
        assert replay['status'] == 'passed', replay
        assert replay['real_harness_loop'] and replay['network_calls'] == 0
        assert replay['references_equal'] and replay['answer_equal'] and replay['tool_trace_equal']
        assert replay['cached_model_requests'] == 3 and replay['tool_calls'] == 2
        assert f.counts == counts
