from copy import deepcopy
import csv
import json

import pytest

from mingjing.source_registry import canonical_report_resources
from mingjing.source_registry import indexed_source_metadata
from mingjing.evidence import sha256_file


def test_identical_report_disclosure_events_keep_paths_and_earliest_known_date():
    rows = [dict(path='later.pdf', sha256='a'*64, company='甲', period='', publish_date='2023-09-07'),
            dict(path='earlier.pdf', sha256='a'*64, company='甲', period='', publish_date='2023-08-01'),
            dict(path='unknown.pdf', sha256='a'*64, company='甲', period='', publish_date='')]
    original = deepcopy(rows)
    resource, = canonical_report_resources(rows)
    assert resource['path'] == 'earlier.pdf' and resource['publish_date'] == '2023-08-01'
    assert len(resource['disclosure_events']) == 3
    assert rows == original
    rows[0]['company'] = '乙'
    with pytest.raises(ValueError, match='发行人冲突'):
        canonical_report_resources(rows)


def test_same_path_multiple_publication_events_are_preserved_without_conflict(tmp_path):
    source = tmp_path / 'report.pdf'
    source.write_bytes(b'one frozen report with two publication events')
    row = dict(file=source.name, name='甲公司', date='2024-05-01',
               sha256=sha256_file(source), size_bytes=source.stat().st_size)
    with (tmp_path / 'index.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=row)
        writer.writeheader()
        writer.writerow(row)
        writer.writerow({**row, 'date': '2024-04-15'})
    metadata = indexed_source_metadata(tmp_path, '甲公司')
    resource = metadata[str(source.resolve())]
    assert resource['publish_date'] == '2024-04-15'
    assert [event['publish_date'] for event in resource['disclosure_events']] == ['2024-04-15', '2024-05-01']
    cache = tmp_path / 'facts.json'
    cache.write_text('[]', encoding='utf-8')
    assert indexed_source_metadata(cache, '甲公司') == metadata


def test_agent_uses_earliest_disclosure_of_identical_bytes_before_cutoff(tmp_path):
    from mingjing.agent import FinancialAgent
    from mingjing.config import AppConfig
    from mingjing.evidence import sha256_file
    corpus = tmp_path / 'materials'
    corpus.mkdir()
    text = '甲公司合并财务报表\n2023年营业收入100元。'
    early = corpus / 'z_early.txt'
    late = corpus / 'a_later.txt'
    early.write_text(text, encoding='utf-8')
    late.write_bytes(early.read_bytes())
    row = dict(file=late.name, name='甲公司', date='2024-05-01',
               sha256=sha256_file(late), size_bytes=late.stat().st_size)
    with (corpus / 'index.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=row)
        writer.writeheader()
        writer.writerow(row)
        writer.writerow({**row, 'file': early.name, 'date': '2024-04-15'})
    draft = tmp_path / 'draft.md'
    draft.write_text('甲公司2023年合并营业收入100元。', encoding='utf-8')
    config = AppConfig().ensure_defaults().to_dict()
    request = dict(draft=str(draft), corpus=str(corpus), company='甲公司', period='2023Y',
                   report_date='2024-04-22', mode='kernel')
    agent = FinancialAgent(request, config, lambda ref: '', tmp_path / 'out', run_id='duplicate-events')
    agent.prepare()
    resource, = [row for row in agent.snapshot['resources'] if row['id'].startswith('report-')]
    assert resource['path'] == str(early.resolve())
    assert resource['publish_date'] == '2024-04-15'
    assert len(resource['disclosure_events']) == 2


def test_mirror_provenance_is_frozen_and_conflicting_publication_cannot_be_hidden(tmp_path):
    source = tmp_path / '2020-06-08_2018年年度报告.pdf'
    source.write_bytes(b'original mirrored report bytes')
    record = dict(code='839493', name='并行科技', orgId='gfbj0839493', category='年报',
                  date='2020-06-08', title='2018年年度报告', file=source.name,
                  size_bytes=source.stat().st_size, sha256=sha256_file(source))
    with (tmp_path/'index.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=record)
        writer.writeheader(); writer.writerow(record)
    document = {**record, 'source_kind': 'mirror_of_official_disclosure',
                'provenance': 'publication_verified_bytes_not_crosschecked',
                'review_status': 'pending_independent_human_content_check'}
    manifest = tmp_path/'material_manifest.json'
    manifest.write_text(json.dumps({'documents':[document]}), encoding='utf-8')
    metadata = indexed_source_metadata(tmp_path, '并行科技')
    actual = metadata[str(source)]
    assert actual['source_provenance']['provenance'] == 'publication_verified_bytes_not_crosschecked'
    assert actual['source_manifest_sha256'] == sha256_file(manifest)
    document['date'] = '2024-04-23'
    manifest.write_text(json.dumps({'documents':[document]}), encoding='utf-8')
    with pytest.raises(ValueError, match='冲突'):
        indexed_source_metadata(tmp_path, '并行科技')
