"""Small corpus tests for cancellable, selective material preparation."""
import csv
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from mingjing import parser
from mingjing.coverage import partition_document
from test_server_analysis_product import local_web


def test_cancelled_or_expired_parse_does_not_open_material(tmp_path, monkeypatch):
    stopped = threading.Event()
    stopped.set()
    monkeypatch.setattr(parser, '_read_text', lambda *a, **k: pytest.fail('cancelled material was opened'))
    with pytest.raises(InterruptedError, match='cancelled'):
        parser.load_or_extract_facts(tmp_path, cancel_event=stopped)
    with pytest.raises(TimeoutError, match='deadline'):
        parser.extract_text(tmp_path/'report.md', deadline_monotonic=time.monotonic()-1)


def test_empty_frozen_index_selection_never_scans_unindexed_reports(tmp_path, monkeypatch):
    selected = tmp_path/'reports'/'920001_公司甲'/'report.md'
    selected.parent.mkdir(parents=True)
    selected.write_text('不能回退读取的未登记报告', encoding='utf-8')
    indexed = tmp_path/'reports'/'920002_公司乙'/'report.md'
    indexed.parent.mkdir(parents=True)
    indexed.write_text('其他公司', encoding='utf-8')
    with (tmp_path/'index.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=['file', 'code', 'name'])
        writer.writeheader()
        writer.writerow({'file': indexed.relative_to(tmp_path).as_posix(), 'code': '920002', 'name': '公司乙'})
    monkeypatch.setattr(parser.os, 'walk', lambda *a, **k: pytest.fail('empty index triggered directory scan'))
    monkeypatch.setattr(parser, 'extract_facts', lambda *a, **k: pytest.fail('unindexed material was parsed'))
    events = []
    assert parser.load_or_extract_facts(tmp_path/'reports', '公司甲', progress_callback=lambda p, **f: events.append((p, f))) == []
    assert ('语料索引筛选完成', {'selected_documents': 0, 'company': '公司甲'}) in events


def test_unindexed_scan_is_single_pass_and_prunes_other_company_directories(tmp_path, monkeypatch):
    root = tmp_path/'reports'
    for code, company in (('920001', '公司甲'), ('920002', '公司乙')):
        folder = root/f'{code}_{company}'/'年报'
        folder.mkdir(parents=True)
        (folder/'report.md').write_text('小型财报材料', encoding='utf-8')
        (folder/'report.PDF').write_bytes(b'pdf')
    visited, walks, parsed = [], [], []
    original = parser.os.walk
    def tracked_walk(*args, **kwargs):
        walks.append(args[0])
        for row in original(*args, **kwargs):
            visited.append(Path(row[0]))
            yield row
    monkeypatch.setattr(parser.os, 'walk', tracked_walk)
    monkeypatch.setattr(Path, 'rglob', lambda *a, **k: pytest.fail('a repeated recursive glob was used'))
    monkeypatch.setattr(parser, 'extract_facts', lambda path, *a, **k: parsed.append(Path(path)) or [])
    assert parser.load_or_extract_facts(root, '公司甲') == []
    assert walks == [root] and len(parsed) == 2
    assert all('920001_公司甲' in str(path) for path in parsed)
    assert not any('920002_公司乙' in str(path) for path in visited)


def test_pruning_other_issuers_does_not_authorize_anonymous_root_material(tmp_path, monkeypatch):
    root = tmp_path/'reports'
    other = root/'920002_公司乙'
    other.mkdir(parents=True)
    (other/'report.md').write_text('其他公司财报', encoding='utf-8')
    (root/'anonymous.md').write_text('无法确认公司的材料', encoding='utf-8')
    monkeypatch.setattr(parser, 'extract_facts', lambda *a, **k: pytest.fail('anonymous report was relabeled'))
    assert parser.load_or_extract_facts(root, '公司甲') == []


def test_pdf_page_cancel_is_not_swallowed_as_text_layer_fallback(tmp_path, monkeypatch):
    stopped, observed = threading.Event(), []
    class Page:
        def get_text(self, mode=None):
            observed.append(mode)
            return [] if mode == 'blocks' else {'blocks': []} if mode == 'dict' else 'report text'
    class Document:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def __len__(self): return 20
        def __iter__(self): return iter([Page() for _ in range(20)])
    monkeypatch.setitem(sys.modules, 'fitz', SimpleNamespace(open=lambda _: Document()))
    monkeypatch.setattr(parser, '_pdftotext', lambda *a, **k: pytest.fail('cancellation triggered fallback'))
    progress = []
    def on_page(phase, **fields):
        progress.append((phase, fields))
        stopped.set()
    with pytest.raises(InterruptedError, match='cancelled'):
        parser.extract_text(tmp_path/'report.pdf', cancel_event=stopped, progress_callback=on_page)
    assert progress[0][1]['page'] == 1 and len(observed) <= 3


def test_cancelling_pdf_converter_terminates_only_its_owned_process(tmp_path, monkeypatch):
    stopped, children = threading.Event(), []
    def no_text_layer(_): raise ValueError('no text layer')
    monkeypatch.setitem(sys.modules, 'fitz', SimpleNamespace(open=no_text_layer))
    original = subprocess.Popen
    def converter(command, **kwargs):
        child = original([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
        children.append(child)
        stopped.set()
        return child
    monkeypatch.setattr(parser.subprocess, 'Popen', converter)
    begin = time.monotonic()
    with pytest.raises(InterruptedError, match='cancelled'):
        parser.extract_text(tmp_path/'report.pdf', cancel_event=stopped)
    assert time.monotonic()-begin < 3
    assert len(children) == 1 and children[0].poll() is not None


def test_cached_facts_cancel_midway_without_returning_partial_database(tmp_path):
    rows = [{'fact_id': f'fact-{i}', 'company': '公司甲', 'account': 'REVENUE',
             'period': '2023Y', 'value': str(i)} for i in range(600)]
    (tmp_path/'facts.json').write_text(json.dumps(rows), encoding='utf-8')
    stopped = threading.Event()
    observed = []
    def stop_after_batch(phase, **fields):
        observed.append(fields)
        if fields.get('fact_rows', 0) >= 256:
            stopped.set()
    with pytest.raises(InterruptedError, match='cancelled'):
        parser.load_or_extract_facts(tmp_path, '公司甲', cancel_event=stopped, progress_callback=stop_after_batch)
    assert max(row.get('fact_rows', 0) for row in observed) == 256
    assert len(json.loads((tmp_path/'facts.json').read_text(encoding='utf-8'))) == 600


def test_partition_document_forwards_cancel_without_losing_source_offsets(tmp_path):
    path = tmp_path/'report.md'
    path.write_text('合并利润表\n2023年营业收入100元。', encoding='utf-8')
    original = partition_document(path)
    assert partition_document(path, cancel_event=threading.Event()) == original
    stopped = threading.Event()
    stopped.set()
    with pytest.raises(InterruptedError, match='cancelled'):
        partition_document(path, cancel_event=stopped)


def test_source_web_task_receives_parser_phase_progress(local_web):
    state, request = local_web
    root = Path(__file__).resolve().parents[1]
    result = request('/api/run', {'draft': str(root/'samples'/'demo_draft.md'),
        'corpus': str(root/'samples'/'corpus'), 'report_date': '2025-03-31', 'mode': 'kernel'}, 'POST')
    task = state.tasks[result['id']]
    task.thread.join(15)
    assert not task.thread.is_alive() and task.status == 'completed', task.error
    events = [row for row in task.events if row['event'] == 'phase']
    assert any(row.get('name') == '读取语料清单' for row in events)
    assert any(row.get('name') == '读取已选事实索引' and row.get('fact_rows') == 1 for row in events)
