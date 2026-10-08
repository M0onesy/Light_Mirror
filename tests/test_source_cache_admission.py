"""A stale machine cache cannot turn a forged old amount into a new fact."""
import csv
import hashlib
import json
import threading
from decimal import Decimal

import pytest

from mingjing import parser, source_cache
from mingjing.facts import FactStore
from mingjing.models import Fact


def material(tmp_path, *, indexed=True):
    source = tmp_path/'2024-04-15_2023年年度报告.pdf'
    source.write_bytes(b'locked original PDF bytes')
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    if indexed:
        with (tmp_path/'index.csv').open('w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=['file', 'name', 'date', 'size_bytes', 'sha256'])
            writer.writeheader()
            writer.writerow(dict(file=source.name, name='公司甲', date='2024-04-15',
                                 size_bytes=source.stat().st_size, sha256=checksum))
    row = dict(fact_id='old-machine', company='公司甲', account='REVENUE', period='2023Y',
               value='999999', source_path=source.name, source_doc=checksum[:12],
               source_type='financial_summary', extractor_version='financial-tables-v2')
    return source, row


def write_cache(tmp_path, row, declaration=None):
    payload = {'facts': [row]}
    if declaration is not None:
        payload['source_fact_cache'] = declaration
    cache = tmp_path/'facts.json'
    cache.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    return cache


def rebuild_mock(monkeypatch):
    calls = []
    def extract(path, company, period, published, **control):
        calls.append((path, company, published))
        return [Fact('rebuilt', company, 'REVENUE', '2023Y', value=Decimal('100'),
                     source_path=str(path), source_type='financial_summary')]
    monkeypatch.setattr(parser, 'extract_facts', extract)
    return calls


@pytest.mark.parametrize('signature', ['missing', 'legacy', 'changed', 'incomplete', 'invalid'])
@pytest.mark.parametrize('direct_file', [False, True])
def test_stale_machine_cache_is_reparsed_without_editing_old_bytes(tmp_path, monkeypatch, signature, direct_file):
    source, row = material(tmp_path)
    declaration = {'schema_version': source_cache.CACHE_SCHEMA,
                   'modules': source_cache.extractor_source_modules()}
    if signature == 'missing':
        declaration = None
    elif signature == 'legacy':
        declaration['schema_version'] = 'mingjing-source-cache.v2'
    elif signature == 'changed':
        declaration['modules']['financial_notes.py'] = '0'*64
    elif signature == 'incomplete':
        declaration['modules'].pop('issuer_identity.py')
    elif signature == 'invalid':
        declaration['modules']['../outside.py'] = '0'*64
    cache = write_cache(tmp_path, row, declaration)
    original_cache, original_index = cache.read_bytes(), (tmp_path/'index.csv').read_bytes()
    calls, events = rebuild_mock(monkeypatch), []
    facts = parser.load_or_extract_facts(cache if direct_file else tmp_path, '公司甲',
        progress_callback=lambda phase, **fields: events.append((phase, fields)))
    assert [fact.value for fact in facts] == [Decimal('100')]
    assert calls == [(source.resolve(), '公司甲', '2024-04-15')]
    assert any(fields.get('cache_status') == 'reparse_read_only' for _, fields in events)
    assert cache.read_bytes() == original_cache and (tmp_path/'index.csv').read_bytes() == original_index


def test_current_machine_cache_requires_original_source_identity(tmp_path, monkeypatch):
    source, row = material(tmp_path)
    row['value'] = '100'
    declaration = {'schema_version': source_cache.CACHE_SCHEMA,
                   'modules': source_cache.extractor_source_modules()}
    write_cache(tmp_path, row, declaration)
    monkeypatch.setattr(parser, 'extract_facts', lambda *a, **k: pytest.fail('valid cache re-parsed'))
    loaded = parser.load_or_extract_facts(tmp_path, '公司甲')
    assert loaded[0].value == Decimal('100') and loaded[0].source_path == str(source.resolve())


@pytest.mark.parametrize('absence', ['index', 'source', 'registered_source'])
def test_old_machine_cache_without_rebuildable_original_is_rejected(tmp_path, monkeypatch, absence):
    source, row = material(tmp_path, indexed=absence != 'index')
    cache = write_cache(tmp_path, row)
    if absence == 'source':
        source.unlink()
    elif absence == 'registered_source':
        unregistered = tmp_path/'unregistered.pdf'
        unregistered.write_bytes(b'unregistered')
        row['source_path'] = unregistered.name
        cache = write_cache(tmp_path, row)
    original = cache.read_bytes()
    monkeypatch.setattr(parser, 'extract_facts', lambda *a, **k: pytest.fail('invalid source used'))
    with pytest.raises(ValueError):
        parser.load_or_extract_facts(tmp_path, '公司甲')
    assert cache.read_bytes() == original


def test_cache_lock_cannot_authorize_changed_original_pdf(tmp_path, monkeypatch):
    source, row = material(tmp_path)
    declaration = {'schema_version': source_cache.CACHE_SCHEMA,
                   'modules': source_cache.extractor_source_modules()}
    write_cache(tmp_path, row, declaration)
    source.write_bytes(b'same length changed PDF!!!')
    monkeypatch.setattr(parser, 'extract_facts', lambda *a, **k: pytest.fail('altered frozen source used'))
    with pytest.raises(ValueError, match='大小|SHA-256'):
        parser.load_or_extract_facts(tmp_path, '公司甲')


def test_manually_structured_pdf_facts_need_no_machine_lock(tmp_path, monkeypatch):
    source, row = material(tmp_path, indexed=False)
    row.update(source_type='structured', extractor_version='legacy-structured', value='123')
    cache = write_cache(tmp_path, row)
    monkeypatch.setattr(parser, 'extract_facts', lambda *a, **k: pytest.fail('manual facts treated as machine cache'))
    assert parser.load_or_extract_facts(cache, '公司甲')[0].value == Decimal('123')
    assert parser.load_or_extract_facts(tmp_path, '公司甲')[0].source_path == str(source.resolve())


def test_declared_machine_payload_cannot_hide_behind_missing_row_marker(tmp_path, monkeypatch):
    _, row = material(tmp_path)
    row.pop('source_type')
    row.pop('extractor_version')
    write_cache(tmp_path, row, {'schema_version': 'legacy-machine-cache'})
    calls = rebuild_mock(monkeypatch)
    assert [fact.value for fact in parser.load_or_extract_facts(tmp_path, '公司甲')] == [Decimal('100')]
    assert len(calls) == 1


def test_frozen_replay_reader_remains_read_only_and_not_current_cache_admission(tmp_path):
    _, row = material(tmp_path, indexed=False)
    cache = write_cache(tmp_path, row)
    original = cache.read_bytes()
    # Replay verifies the old run's hashes and uses frozen facts. It does not
    # pretend those bytes are a freshly extracted current-source cache.
    assert FactStore.from_json(cache).facts[0].value == Decimal('999999')
    assert cache.read_bytes() == original


def test_frozen_exe_without_python_sources_reparses_registered_pdf(tmp_path, monkeypatch):
    _, row = material(tmp_path)
    write_cache(tmp_path, row, {'schema_version': source_cache.CACHE_SCHEMA,
                              'modules': source_cache.extractor_source_modules()})
    monkeypatch.setattr(source_cache, 'extractor_source_modules',
                        lambda **kw: (_ for _ in ()).throw(ValueError('source unavailable')))
    calls, events = rebuild_mock(monkeypatch), []
    facts = parser.load_or_extract_facts(tmp_path, '公司甲',
        progress_callback=lambda phase, **fields: events.append(fields))
    assert facts[0].value == Decimal('100') and len(calls) == 1
    assert any(event.get('cache_reason') == 'extractor_source_unavailable' for event in events)


def test_cancellation_before_cache_admission_does_not_read_material(tmp_path, monkeypatch):
    _, row = material(tmp_path)
    write_cache(tmp_path, row)
    stopped = threading.Event()
    stopped.set()
    monkeypatch.setattr(parser, '_read_text', lambda *a, **k: pytest.fail('cancelled cache read'))
    with pytest.raises(InterruptedError):
        parser.load_or_extract_facts(tmp_path, cancel_event=stopped)


@pytest.mark.parametrize('entrypoint', ['store', 'agent', 'cli_fallback'])
def test_new_task_entrypoints_cannot_bypass_stale_machine_cache(tmp_path, monkeypatch, entrypoint):
    _, row = material(tmp_path)
    cache = write_cache(tmp_path, row)
    original = cache.read_bytes()
    calls = rebuild_mock(monkeypatch)
    if entrypoint == 'store':
        facts = FactStore.from_corpus(tmp_path).facts
    elif entrypoint == 'agent':
        from mingjing.agent import FinancialAgent
        from mingjing.config import AppConfig
        config = AppConfig().ensure_defaults().to_dict()
        agent = FinancialAgent({}, config, lambda ref: '', tmp_path / 'out', run_id='admission')
        from mingjing.audit import AuditLog
        agent.audit = AuditLog(tmp_path / 'out', 'admission')
        facts = agent._material_facts(cache, '公司甲', '2023Y')
    else:
        from mingjing.cli import build_parser, command_check
        draft = tmp_path / 'draft.md'
        draft.write_text('公司甲2023年合并营业收入100元。', encoding='utf-8')
        empty = tmp_path / 'empty.txt'
        empty.write_text('没有财务数值', encoding='utf-8')
        out = tmp_path / 'checked'
        args = build_parser().parse_args(['check', '--draft', str(draft), '--corpus', str(empty),
            '--facts', str(cache), '--company', '公司甲', '--period', '2023Y',
            '--report-date', '2024-04-22', '--out', str(out), '--mode', 'kernel'])
        assert command_check(args) == 0
        facts = FactStore.from_json(out / 'facts.json').facts
    assert [fact.value for fact in facts] == [Decimal('100')]
    assert len(calls) == 1 and cache.read_bytes() == original


@pytest.mark.parametrize('filename', ['facts.jsonl', 'fact_store.json'])
def test_legacy_index_filenames_keep_admission_guard(tmp_path, monkeypatch, filename):
    _, row = material(tmp_path)
    cache = tmp_path / filename
    cache.write_text(json.dumps(row if filename.endswith('.jsonl') else [row]), encoding='utf-8')
    calls = rebuild_mock(monkeypatch)
    assert [fact.value for fact in FactStore.from_corpus(tmp_path).facts] == [Decimal('100')]
    assert len(calls) == 1


def test_malformed_jsonl_is_rejected_instead_of_silently_deleting_a_fact(tmp_path):
    _, row = material(tmp_path)
    row.update(source_type='structured', extractor_version='legacy-structured')
    cache = tmp_path / 'facts.jsonl'
    cache.write_text(json.dumps(row) + '\n{broken row\n', encoding='utf-8')
    with pytest.raises(json.JSONDecodeError):
        FactStore.from_corpus(tmp_path)
