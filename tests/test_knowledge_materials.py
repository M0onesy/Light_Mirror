"""External accounting materials are references, never issued financial truth."""
from __future__ import annotations

import copy
import json
import threading
import time
import zipfile
from pathlib import Path

import pytest

from mingjing.audit import digest
from mingjing.evidence import sha256_file
from mingjing.knowledge import KnowledgeRegistry, KnowledgeTools, KnowledgeError, METADATA_SCHEMA, RAW_STATUS


HEADER = '# 会计政策准则\n\n- 版本：测试版\n- 发布：2024-01-01\n- 生效：2024-07-01\n- 来源：https://example.invalid/policy\n- 状态：现行有效\n\n## 原文\n'


@pytest.fixture
def material(tmp_path):
    root, cache = tmp_path/'knowledge', tmp_path/'cache'
    root.mkdir()
    source = root/'policy.md'
    source.write_text(HEADER+'会计政策变更应当采用追溯调整法。数字100不构成事实库。', encoding='utf-8')
    return root, cache, source


def registry(material, **control):
    root, cache, _ = material
    return KnowledgeRegistry(root, cache, **control).scan()


def metadata_index(root, path, **metadata):
    (root/'knowledge_index.json').write_text(json.dumps({'schema_version': METADATA_SCHEMA,
        'documents': [{'file': path.relative_to(root).as_posix(), 'sha256': sha256_file(path), **metadata}]}, ensure_ascii=False), encoding='utf-8')


def tools(store, *, role='question', documents=None, **control):
    bridge = KnowledgeTools(store, run_id='run', resource_lock='financial-lock', as_of_date='2025-03-31', **control)
    bridge.register_session('session', role, documents)
    return bridge


def call(bridge, name, params=None, **identity):
    envelope = dict(session_id='session', role='question', run_id='run', resource_lock='financial-lock',
                    knowledge_lock=bridge.registry.resource_lock, call_id='call-'+str(len(bridge.calls)), tool_name=name, params=params or {})
    envelope.update(identity)
    return bridge.dispatch(envelope)


def test_single_primary_root_metadata_blocks_raw_reference_and_exact_hashes(material):
    store = registry(material)
    bridge = tools(store)
    listing = call(bridge, 'mj_kb_list')
    document = listing['documents'][0]
    assert document['metadata']['publish_date'] == '2024-01-01'
    assert document['metadata']['effective_date'] == '2024-07-01'
    assert document['metadata']['version'] == '测试版'
    assert document['metadata']['authority_status'] == 'declared_unverified'
    assert document['effective_status'] == 'declared_dates_only'
    assert not document['metadata']['authorizes_judgement']
    found = call(bridge, 'mj_kb_search', {'query': '追溯调整法'})
    hit = found['hits'][0]
    assert hit['reference_status'] == RAW_STATUS and not hit['authorizes_judgement']
    assert '100' in hit['text'] and hit['document_sha256'] == sha256_file(material[2])
    assert call(bridge, 'mj_kb_read', {'block_id': hit['block_id']}) == hit
    assert hit['page'] is None and hit['position_basis'] == 'logical_text'
    assert 'source_paths' not in json.dumps(listing) and str(material[0]) not in json.dumps(found)
    assert len(bridge.trace) == 3 and all(row['knowledge_lock'] == store.resource_lock for row in bridge.trace)


def test_equal_sha_copies_are_one_document_with_all_locked_aliases(material):
    root, _, source = material
    replica = root/'copies'
    replica.mkdir()
    (replica/'policy.txt').write_bytes(source.read_bytes())
    store = registry(material)
    assert len(store.documents) == 1
    assert len(next(iter(store.documents.values()))['source_paths']) == 2
    assert len(store.resources) == 2
    assert len(store.search('追溯调整法', as_of_date='2025-03-31')['hits']) == 1
    snapshot = store.snapshot()
    assert snapshot['documents'][0]['aliases'] == ['copies/policy.txt', 'policy.md']
    restored = KnowledgeRegistry.restore(snapshot, cache_root=material[1])
    assert restored.snapshot() == snapshot
    assert restored.search('追溯调整法', as_of_date='2025-03-31') == store.search('追溯调整法', as_of_date='2025-03-31')


def test_nested_chinese_sources_and_index_survive_frozen_restore(material):
    root, cache, source = material
    nested = root/'会计准则'/'实施问答'
    nested.mkdir(parents=True)
    moved = nested/'准则.txt'
    source.replace(moved)
    metadata_index(root, moved, publish_date='2024-01-01', effective_date='2024-07-01')
    store = registry(material)
    snapshot = store.snapshot()
    assert snapshot['documents'][0]['aliases'] == ['会计准则/实施问答/准则.txt']
    restored = KnowledgeRegistry.restore(snapshot, cache_root=cache)
    assert restored.snapshot() == snapshot
    found = restored.search('追溯调整法', as_of_date='2025-03-31')
    assert found['hits'] and found['hits'][0]['metadata']['metadata_basis'] == 'host_hash_locked_index'


def test_duplicate_logical_occurrences_keep_distinct_evidence_positions(material):
    material[2].write_text(HEADER+'\n追溯调整法原文。\n\n追溯调整法原文。\n\n', encoding='utf-8')
    store = registry(material)
    hits = store.search('追溯调整法', as_of_date='2025-03-31')['hits']
    assert len(hits) == 2 and hits[0]['text'] == hits[1]['text']
    assert hits[0]['span_sha256'] == hits[1]['span_sha256']
    assert hits[0]['start'] != hits[1]['start'] and hits[0]['end'] != hits[1]['end']
    assert hits[0]['block_id'] != hits[1]['block_id']
    assert hits[0]['evidence_id'] != hits[1]['evidence_id']
    assert all(hit['page'] is None and ':text:' in hit['evidence_id'] for hit in hits)


@pytest.mark.parametrize('role', ['planner', 'verifier'])
def test_blind_knowledge_roles_mask_labels_and_keep_usable_ids(material, role):
    material[2].write_text(HEADER.replace('会计政策准则', '案例100万元')+'追溯调整法原文100万元。', encoding='utf-8')
    store = registry(material)
    bridge = tools(store, role=role)
    listing = call(bridge, 'mj_kb_list', role=role)
    doc = listing['documents'][0]
    assert '100万元' not in doc['metadata']['title']
    assert doc['knowledge_id'] in store.documents
    assert doc['document_sha256'] == sha256_file(material[2])
    found = call(bridge, 'mj_kb_search', {'query': '追溯调整法', 'document_id': doc['knowledge_id']}, role=role)
    hit = found['hits'][0]
    assert '100万元' not in hit['text'] and '100万元' not in hit['metadata']['title']
    assert hit['block_id'] in store.blocks and hit['evidence_id'] == store.blocks[hit['block_id']]['evidence_id']
    assert call(bridge, 'mj_kb_read', {'block_id': hit['block_id']}, role=role) == hit


@pytest.mark.parametrize('cutoff', [None, '', '2025-02-31'])
@pytest.mark.parametrize('query', ['追溯调整法', '不存在的搜索词'])
def test_search_requires_cutoff_before_matching(material, cutoff, query):
    with pytest.raises(KnowledgeError, match='cutoff|ISO date'):
        registry(material).search(query, as_of_date=cutoff)


def test_frozen_restore_uses_locked_cache_and_changes_rejected(material, monkeypatch):
    from mingjing import knowledge
    store = registry(material)
    snapshot = store.snapshot()
    monkeypatch.setattr(knowledge, '_parse', lambda *_: (_ for _ in ()).throw(AssertionError('Frozen restores must use their locked cache')))
    # The loader-code fingerprint is checked independently; this test's
    # parser monkeypatch is excluded only at the local fixture construction.
    original_loader = store.loader_hash
    actual = knowledge._module_code_data
    monkeypatch.setattr(knowledge, '_module_code_data', lambda module: actual(module) if module.__name__ != knowledge.__name__ else {'fixture': 'unchanged-loader'})
    with pytest.raises(KnowledgeError, match='implementation changed'):
        KnowledgeRegistry.restore(snapshot, cache_root=material[1])
    monkeypatch.undo()
    restored = KnowledgeRegistry.restore(snapshot, cache_root=material[1])
    assert restored.loader_hash == original_loader and restored.snapshot() == snapshot
    cache = Path(next(iter(store.documents.values()))['cache_path'])
    cache.write_text('{}', encoding='utf-8')
    with pytest.raises(KnowledgeError, match='cache changed'):
        KnowledgeRegistry.restore(snapshot, cache_root=material[1])


def test_fresh_scan_does_not_trust_forged_self_authenticated_cache(material):
    store = registry(material)
    cache = Path(next(iter(store.documents.values()))['cache_path'])
    forged = json.loads(cache.read_text(encoding='utf-8'))
    forged['blocks'][0]['text'] = '伪造准则：公司营业收入999999。'
    cache.write_text(json.dumps(forged), encoding='utf-8')
    fresh = registry(material)
    assert not fresh.search('999999', as_of_date='2025-03-31')['hits']
    assert fresh.search('追溯调整法', as_of_date='2025-03-31')['hits']


@pytest.mark.parametrize('change', ['source', 'metadata', 'cache', 'duplicate'])
def test_locked_source_and_index_and_alias_changes_are_detected(material, change):
    root, cache, source = material
    metadata_index(root, source, publish_date='2024-01-01', effective_date='2024-07-01', source_url='https://example.invalid/policy')
    duplicate = root/'duplicate.md'
    duplicate.write_bytes(source.read_bytes())
    store = registry(material)
    if change == 'source':
        source.write_text('Changed source', encoding='utf-8')
    elif change == 'metadata':
        (root/'knowledge_index.json').write_text('{}', encoding='utf-8')
    elif change == 'cache':
        Path(next(iter(store.documents.values()))['cache_path']).write_text('{}', encoding='utf-8')
    else:
        duplicate.write_text('Changed alias', encoding='utf-8')
    with pytest.raises(KnowledgeError, match='changed'):
        store.search('追溯调整法', as_of_date='2025-03-31')


@pytest.mark.parametrize('text', ['2024年发布，自2025年执行，现行有效。', '# 准则\n\n现行有效。',
                                '# 准则\n\n- 发布：2026-01-01\n\n准则原文。'])
def test_prose_years_and_unknown_or_future_publication_never_become_effective_rules(material, text):
    material[2].write_text(text, encoding='utf-8')
    store = registry(material)
    doc = next(iter(store.documents.values()))
    result = store.search('准则', as_of_date='2025-03-31')
    assert not result['hits'] and result['unavailable_document_count'] == 1
    first = next(iter(store.blocks))
    raw = store.read(first, as_of_date='2025-03-31')
    assert not raw['available'] and 'text' not in raw
    assert doc['metadata']['authorizes_judgement'] is False


def test_future_effective_date_is_a_declared_reference_and_never_applicable_verdict(material):
    store = registry(material)
    result = store.search('追溯调整法', as_of_date='2024-03-31')
    assert result['hits'][0]['effective_status'] == 'not_yet_effective'
    assert result['hits'][0]['available'] and not result['hits'][0]['authorizes_judgement']


def test_docx_paragraphs_tables_and_no_fabricated_page(material):
    root, _, original = material
    original.unlink()
    docx = root/'policy.docx'
    with zipfile.ZipFile(docx, 'w') as archive:
        archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>会计估计变更应未来适用。</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>表格原文200元。</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
    metadata_index(root, docx, publish_date='2024-01-01')
    store = registry(material)
    hit = store.search('未来适用', as_of_date='2025-03-31')['hits'][0]
    assert hit['page'] is None and hit['paragraph'] == 1 and hit['position_basis'] == 'logical_text'
    assert store.search('表格原文', as_of_date='2025-03-31')['hits'][0]['paragraph'] == 2


@pytest.mark.parametrize('encoding', ['utf-8', 'utf-16', 'utf-16-le', 'utf-16-be'])
def test_docx_rejects_dtd_and_entities_in_every_supported_xml_encoding(material, encoding):
    from mingjing.knowledge import _docx_pages
    root, _, original = material
    original.unlink()
    docx = root/'entity.docx'
    declaration = 'UTF-8' if encoding == 'utf-8' else 'UTF-16'
    xml = f'<?xml version="1.0" encoding="{declaration}"?><!DOCTYPE w:document [<!ENTITY injected "Injected finance text">]><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>&injected;</w:t></w:r></w:p></w:body></w:document>'
    with zipfile.ZipFile(docx, 'w') as archive:
        archive.writestr('word/document.xml', xml.encode(encoding))
    with pytest.raises(KnowledgeError, match='entities are disabled'):
        _docx_pages(docx, {})
    store = registry(material)
    assert next(iter(store.documents.values()))['parse_status'] == 'parse_failed'
    assert not store.blocks


def test_pdf_text_and_page_box_are_original_and_scanned_page_not_quotable(material):
    fitz = pytest.importorskip('fitz')
    root, _, original = material
    original.unlink()
    pdf = root/'policy.pdf'
    document = fitz.open()
    page = document.new_page()
    page.insert_text((40, 70), '会计政策变更应采用追溯调整法。', fontname='china-s', fontsize=12)
    document.save(pdf)
    document.close()
    metadata_index(root, pdf, publish_date='2024-01-01')
    store = registry(material)
    hit = store.search('追溯调整法', as_of_date='2025-03-31')['hits'][0]
    assert hit['page'] == 1 and len(hit['bbox']) == 4 and ':p1:' in hit['evidence_id']
    assert hit['span_sha256'] and hit['document_sha256'] == sha256_file(pdf)
    pdf.unlink()
    document = fitz.open()
    document.new_page()
    document.save(pdf)
    document.close()
    metadata_index(root, pdf, publish_date='2024-01-01')
    scanned = registry(material)
    assert next(iter(scanned.documents.values()))['parse_status'] == 'no_text_layer'
    assert not scanned.search('追溯调整法', as_of_date='2025-03-31')['hits']


@pytest.mark.parametrize('params', [{'path': 'C:/private.pdf'}, {'candidate_value': '999999'}, {'formula': 'x+y'},
                                  {'status': 'verified'}, {'role': 'coordinator'}, {'as_of_date': '2099-01-01'}])
def test_model_cannot_supply_paths_facts_formulas_judgements_or_identity(material, params):
    bridge = tools(registry(material))
    with pytest.raises(KnowledgeError, match='forbidden'):
        call(bridge, 'mj_kb_search', {'query': '准则', **params})


@pytest.mark.parametrize('identity', [{'role': 'verifier'}, {'run_id': 'other'}, {'resource_lock': 'other'},
                                    {'knowledge_lock': 'other'}, {'session_id': 'missing'}])
def test_trusted_session_identity_and_both_resource_locks_are_required(material, identity):
    bridge = tools(registry(material))
    with pytest.raises(KnowledgeError):
        call(bridge, 'mj_kb_list', **identity)
    bridge.close_session('session')
    with pytest.raises(KnowledgeError, match='closed'):
        call(bridge, 'mj_kb_list')


def test_role_scope_duplicate_calls_cancellation_and_deadline_are_guarded(material):
    store = registry(material)
    with pytest.raises(KnowledgeError, match='role'):
        tools(store, role='reporter')
    limited = tools(store, documents=[])
    block = next(iter(store.blocks))
    with pytest.raises(KnowledgeError, match='session scope'):
        call(limited, 'mj_kb_read', {'block_id': block})
    bridge = tools(store, max_tool_calls=1)
    call(bridge, 'mj_kb_list', call_id='fixed')
    with pytest.raises(KnowledgeError, match='budget exhausted'):
        call(bridge, 'mj_kb_list', call_id='fixed')
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(InterruptedError):
        call(tools(store, cancel_event=cancelled), 'mj_kb_list')
    with pytest.raises(TimeoutError):
        call(tools(store, deadline_monotonic=time.monotonic()-1), 'mj_kb_list')


def test_symlinks_root_escape_and_metadata_hash_are_rejected(material, tmp_path):
    root, cache, source = material
    outside = tmp_path/'outside.md'
    outside.write_text('Other root', encoding='utf-8')
    with pytest.raises(ValueError, match='outside allowed roots'):
        KnowledgeRegistry(root, cache, allowed_roots=[str(cache)])
    with pytest.raises(KnowledgeError, match='outside'):
        KnowledgeRegistry(root, root/'cache')
    try:
        (root/'escape.md').symlink_to(outside)
    except OSError:
        pytest.skip('Windows symlink creation is unavailable to this user')
    with pytest.raises(ValueError, match='symbolic links'):
        registry(material)


@pytest.mark.parametrize('metadata', [{'publish_date': '2024-1-1'}, {'source_url': 'file:///private'},
                                      {'source_url': 'https://user:secret@example.invalid/path'},
                                      {'authorizes_judgement': True}, {'formula': 'net-revenue'}])
def test_metadata_index_cannot_smuggle_authority_or_invalid_fields(material, metadata):
    root, _, source = material
    metadata_index(root, source, **metadata)
    with pytest.raises(KnowledgeError):
        registry(material)


def test_rule_links_require_exact_issued_source_sha_and_never_replace_rule_cards(material):
    store = registry(material)
    identity, row = next(iter(store.documents.items()))
    signed = {'version_hash': 'signed-rule-version', 'sources': [{'id': 'CAS28', 'sha256': row['document_sha256']}]}
    cards = [{'id': 'CHANGE-policy', 'basis_refs': [{'source_id': 'CAS28'}]}]
    original = copy.deepcopy(cards)
    links = store.rule_links(identity, source_register=signed, rule_cards=cards)
    assert links['linked_rule_ids'] == ['CHANGE-policy'] and not links['external_material_authorizes_judgement']
    signed['sources'][0]['sha256'] = '0'*64
    assert store.rule_links(identity, source_register=signed, rule_cards=cards)['linked_rule_ids'] == []
    assert cards == original


def test_bounded_payload_progress_and_source_budget(material):
    material[2].write_text(HEADER+('\n\n准则原文100元，未独立核验。'+('内容'*1000))*20, encoding='utf-8')
    store = registry(material)
    found = store.search('准则原文', as_of_date='2025-03-31', limit=20)
    assert found['hits'] and len(json.dumps(found, ensure_ascii=False).encode('utf-8')) <= 16384
    assert found['next_cursor'] == len(found['hits'])
    with pytest.raises(KnowledgeError, match='byte budget'):
        registry(material, max_document_bytes=10)
    cancellation = threading.Event()
    cancellation.set()
    with pytest.raises(InterruptedError):
        registry(material, cancel_event=cancellation)


def test_registry_repeated_scan_has_stable_resource_inventory(material):
    store = registry(material)
    before = store.snapshot()
    store.scan()
    assert store.snapshot() == before
