import json
from pathlib import Path

import pytest

from mingjing.smart.kb import KnowledgeBase
from mingjing.smart.llm_bind import config_from_app


def test_blob_mutation_cannot_be_hidden_by_parse_cache(tmp_path):
    kb = KnowledgeBase(tmp_path / 'kb')
    record = kb.add_bytes('公司年度报告营业收入为一亿元。'.encode(), 'source.txt')
    kb.document(record['doc_id'])
    blob = kb.blob_path(record['doc_id'], '.txt')
    blob.write_bytes(b'tampered')
    with pytest.raises(ValueError, match='SHA-256'):
        kb.document(record['doc_id'])


def test_journal_checkpoint_detects_truncation_and_empty_log(tmp_path):
    kb = KnowledgeBase(tmp_path / 'kb')
    kb._journal('first')
    kb._journal('second')
    lines = kb.journal_path.read_text(encoding='utf-8').splitlines()
    assert kb.verify_journal()['ok']
    kb.journal_path.write_text(lines[0] + '\n', encoding='utf-8')
    assert not kb.verify_journal()['ok']
    kb.journal_path.write_text('', encoding='utf-8')
    assert not kb.verify_journal()['ok']
    with pytest.raises(ValueError):
        kb._journal('cannot conceal deletion')


def test_historical_task_reference_protects_original(tmp_path):
    kb = KnowledgeBase(tmp_path / 'kb')
    record = kb.add_bytes(b'financial evidence', 'source.txt')
    kb.touch([record['doc_id']], 'run-test', 'check')
    with pytest.raises(PermissionError):
        kb.remove(record['doc_id'])
    assert kb.validated_blob(record['doc_id']).is_file()


def test_cache_checks_request_and_answer_bytes(tmp_path):
    kb = KnowledgeBase(tmp_path / 'kb')
    kb.cache_put('llm-bind', 'request', {'answers': ['source']})
    assert kb.cache_get('llm-bind', 'request') == {'answers': ['source']}
    path = next((kb.root / 'cache').rglob('*.json'))
    entry = json.loads(path.read_text(encoding='utf-8'))
    entry['value']['answers'] = ['forged']
    path.write_text(json.dumps(entry), encoding='utf-8')
    with pytest.raises(ValueError, match='完整性'):
        kb.cache_get('llm-bind', 'request')


def test_provider_denial_is_checked_before_resolving_secret():
    config = {'providers': {'p': {'enabled': True, 'material_access': False,
                                 'credential_ref': 'secret', 'models': [{'id': 'model'}]}},
              'agent': {'routing': {'default': {'provider': 'p', 'model': 'model'}}},
              'llm': {'enabled': True, 'base_url': 'https://example.com', 'model': 'legacy'}}
    calls = []
    result = config_from_app(config, lambda ref: calls.append(ref) or 'must not resolve')
    assert not result['enabled']
    assert calls == []
