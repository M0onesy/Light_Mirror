"""Native host material lifecycle through controlled runtime boundaries."""
import copy
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from mingjing import agent as agent_module, sessions as sessions_module
from mingjing.evidence import sha256_file
from mingjing.replay import replay_run
from test_agent_lifecycle import lifecycle, new_agent, read


def extra_corpus(lifecycle, name='additional'):
    root = Path(lifecycle.request['corpus']).parent / name
    root.mkdir()
    (root/'report.txt').write_text('甲公司合并财务报表。2022年营业收入60元。', encoding='utf-8')
    original = read(Path(lifecycle.request['corpus'])/'facts.json')[0]
    fact = {**original, 'fact_id': name + '-2022', 'period': '2022Y', 'value': '60', 'source_path': 'report.txt'}
    (root/'facts.json').write_text(json.dumps([fact], ensure_ascii=False), encoding='utf-8')
    (root/'index.csv').write_text('file,name,year\n', encoding='utf-8')
    return root


def test_prepare_keeps_both_source_roots_and_freezes_every_material_index(lifecycle):
    primary = Path(lifecycle.request['corpus'])
    added = extra_corpus(lifecycle)
    lifecycle.request['corpus_additions'] = [str(added), str(primary), str(added)]
    owner = new_agent(lifecycle)
    owner.prepare()
    assert owner.request['corpus'] == str(primary)
    assert owner.request['corpus_additions'] == [str(added)]
    assert {fact.period for fact in owner.facts} == {'2022Y', '2023Y', '2024Y'}
    assert {Path(fact.source_path) for fact in owner.facts} == {primary/'report.txt', added/'report.txt'}
    assert len(owner.dispatcher.reports) == 2
    assert any('2022年' in block['text'] for block in owner.dispatcher.blocks.values())
    resources = {Path(item['path']): item['sha256'] for item in owner.snapshot['resources']}
    for path in [primary/'facts.json', added/'facts.json', added/'index.csv', primary/'report.txt', added/'report.txt']:
        assert resources[path] == sha256_file(path)
    assert owner.snapshot['material_sources'] == [str(primary), str(added)]
    assert read(lifecycle.out/'agent_state.json')['request']['corpus_additions'] == [str(added)]
    assert lifecycle.boundary.instances == []


def test_native_added_materials_pass_kernel_replay_and_tampered_new_index_fails(lifecycle):
    added = extra_corpus(lifecycle)
    lifecycle.request.update(task_type='analysis', corpus_additions=[str(added)])
    lifecycle.boundary.cancel_first_binding = False
    result = new_agent(lifecycle).run()
    assert result['model']['status'] == 'success', result['model']['error']
    assert result['analysis']['periods'] == ['2022Y', '2023Y', '2024Y']
    assert replay_run(lifecycle.out)['status'] == 'passed'
    manifest = read(lifecycle.out/'run_manifest.json')
    assert str(added/'facts.json') in {item['path'] for item in manifest['inputs']}
    (added/'facts.json').write_text('[]', encoding='utf-8')
    failed = replay_run(lifecycle.out)
    assert failed['status'] == 'failed' and not failed['integrity_ok']


def test_qualitative_pdf_without_facts_is_locked_with_blocks_and_primary_fallback(lifecycle):
    fitz = pytest.importorskip('fitz')
    primary = Path(lifecycle.request['corpus'])
    index = primary/'facts.json'
    fallback = primary.parent/'fallback.json'
    facts = read(index)
    for fact in facts:
        fact['source_path'] = str(primary/'report.txt')
    fallback.write_text(json.dumps(facts, ensure_ascii=False), encoding='utf-8')
    index.write_text('[]', encoding='utf-8')
    pdf = primary.parent/'policy-only.pdf'
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), 'Accounting policy notice. Source material without numeric facts.')
    document.save(pdf)
    document.close()
    lifecycle.request.update(facts=str(fallback), corpus_additions=[str(pdf)])
    owner = new_agent(lifecycle)
    owner.prepare()
    assert {fact.period for fact in owner.facts} == {'2023Y', '2024Y'}
    resource = next(item for item in owner.snapshot['resources'] if item['path'] == str(pdf))
    assert resource['id'].startswith('report-') and resource['sha256'] == sha256_file(pdf)
    assert any('Accounting policy' in block['text'] for block in owner.dispatcher.blocks.values())
    assert str(fallback) in owner.snapshot['material_sources']
    assert lifecycle.boundary.instances == []


def test_cancel_during_addition_stops_before_runtime_and_uses_one_deadline(lifecycle, monkeypatch):
    added = extra_corpus(lifecycle)
    lifecycle.request['corpus_additions'] = [str(added)]
    owner = new_agent(lifecycle)
    actual = agent_module.load_or_extract_facts
    options_seen = []

    def controlled(source, **options):
        options_seen.append(options)
        if Path(source) == added:
            owner.cancelled.set()
        return actual(source, **options)

    monkeypatch.setattr(agent_module, 'load_or_extract_facts', controlled)
    with pytest.raises(InterruptedError):
        owner.prepare()
    assert len(options_seen) == 2
    assert all(options['cancel_event'] is owner.cancelled for options in options_seen)
    assert len({options['deadline_monotonic'] for options in options_seen}) == 1
    assert all(callable(options['progress_callback']) for options in options_seen)
    assert not (lifecycle.out/'agent_lock.json').exists() and lifecycle.boundary.instances == []


def test_material_message_appends_and_reuses_frozen_draft_without_modifying_old_run(lifecycle):
    prior = extra_corpus(lifecycle, 'prior')
    next_source = extra_corpus(lifecycle, 'next')
    lifecycle.request['corpus_additions'] = [str(prior)]
    lifecycle.boundary.cancel_first_binding = False
    result = new_agent(lifecycle).run()
    original_run = {path: path.read_bytes() for path in lifecycle.out.rglob('*') if path.is_file()}

    class ConfigBoundary:
        def get(self):
            return copy.deepcopy(lifecycle.config)

        def secret(self, *_):
            raise AssertionError('No real API in material version tests')

    run = SimpleNamespace(out=lifecycle.out, request=lifecycle.request)
    sessions = sessions_module.FinancialSessions(lifecycle.out.parent/'material-conversations', lambda _: run, ConfigBoundary())
    sid = sessions.create('lifecycle-fixture')['session_id']
    captured = []
    sessions.launch_run = lambda request: captured.append(request) or SimpleNamespace(id='new-material-version', status='queued')
    response = sessions.add_materials(sid, {'corpus': str(next_source), 'scope_declaration': '合并'})
    request = captured[0]
    assert response['new_version'] and response['parent_run_id'] == 'lifecycle-fixture'
    assert request['corpus'] == lifecycle.request['corpus']
    assert request['corpus_additions'] == [str(prior), str(next_source)]
    assert request['draft'] == result['draft']
    assert request['scope_declaration'] == '合并'
    assert request['source_session_id'] == sid
    assert all(path.read_bytes() == content for path, content in original_run.items())
