"""Local-table acceptance must preserve source text and disclose its scope."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from mingjing.evidence import sha256_file
from scripts import source_customer_table_acceptance as probe


def args(tmp_path, **changes):
    values = dict(seed_run=tmp_path/'seed', out=tmp_path/'local', prepare_only=False,
                  model_calls=512, tool_calls=4000, tokens=2000000, timeout=3600)
    values.update(changes)
    return SimpleNamespace(**values)


@pytest.mark.parametrize('changes', [
    {'tokens': True}, {'tokens': 0}, {'tool_calls': -1}, {'model_calls': 0}, {'timeout': '60'},
    {'prepare_only': 'false'}, {'out': Path('unexpected')/'seed'},
])
def test_invalid_parameters_never_read_sources_or_credentials(tmp_path, monkeypatch, changes):
    if 'out' in changes:
        changes['out'] = tmp_path/'seed'/'inside-old-record'
    def forbidden(*_, **__):
        pytest.fail('Invalid local acceptance reached source/output/model preparation')
    monkeypatch.setattr(probe, 'read_seed', forbidden)
    monkeypatch.setattr(probe, 'new_output', forbidden)
    monkeypatch.setattr(probe.native_probe, 'execute', forbidden)
    with pytest.raises(ValueError):
        probe.execute(args(tmp_path, **changes))
    assert not (tmp_path/'local').exists()


def test_pdf_excerpt_removes_hidden_outside_text_and_keeps_source_geometry(tmp_path):
    fitz = pytest.importorskip('fitz')
    source, target = tmp_path/'original.pdf', tmp_path/'excerpt.pdf'
    with fitz.open() as pdf:
        for _ in range(2):
            page = pdf.new_page(width=600, height=600)
            page.insert_text((36, 50), 'OUTSIDE777')
            page.insert_text((36, 145), 'Customer Azul REVENUE 123.45')
            page.insert_text((460, 175), 'HIDDEN888')
            page.insert_text((36, 450), 'EXCLUDED999')
        pdf.save(source)
    old_hash = sha256_file(source)
    manifest = probe.extract_table_excerpt(source, target,
        regions=((1, (30, 100, 420, 250)), (2, (30, 100, 420, 250))))
    assert sha256_file(source) == old_hash == manifest['original_sha256']
    assert manifest['full_report_acceptance'] is False
    assert manifest['all_excerpt_candidates_retained'] is True
    with fitz.open(source) as old, fitz.open(target) as new:
        for i, record in enumerate(manifest['pages']):
            text = new[i].get_text('text')
            assert '123.45' in text
            assert all(label not in text for label in ('OUTSIDE777', 'HIDDEN888', 'EXCLUDED999'))
            original = old[i].get_text('text')
            for block in record['original_blocks']:
                assert original[block['source_page_start']:block['source_page_end']] == block['text']
                assert any(tuple(row[:4]) == tuple(block['bbox']) for row in new[i].get_text('blocks'))


def test_excerpt_never_overwrites_original_or_existing_pdf(tmp_path):
    source = tmp_path/'source.pdf'
    source.write_bytes(b'read-only-fixture')
    for target in (source, tmp_path/'existing.pdf'):
        target.write_bytes(b'read-only-fixture')
        with pytest.raises(ValueError):
            probe.extract_table_excerpt(source, target)
        assert target.read_bytes() == b'read-only-fixture'


def test_customer_review_keeps_other_excerpt_claims_and_every_ledger_occurrence():
    source_proof = {'report_sha256': probe.PROSPECTUS_SHA256}
    claims = [
        {'claim_id': 'random-claim-a', 'table_context': {'dimension_kind': 'customer',
         'title_source': {'text': '表4：客户历史收入'}, 'source_table_reference': source_proof}},
        {'claim_id': 'random-claim-b', 'table_context': {'dimension_kind': 'customer',
         'title_source': {'text': '表4：客户历史收入'}}},
        {'claim_id': 'random-claim-c', 'table_context': {'title_source': {'text': '其他原文'}}},
    ]
    ledger = {'mentions': [{'id': 'occurrence-a', 'claim_id': 'random-claim-a'},
                           {'id': 'occurrence-b', 'claim_id': 'random-claim-a'},
                           {'id': 'excluded-visible', 'status': 'rule_excluded'}]}
    data = {'claims': claims, 'results': [
        {'claim_id': 'random-claim-a', 'status': 'verified'},
        {'claim_id': 'random-claim-b', 'status': 'abstained', 'abstention_code': 'X1'},
        {'claim_id': 'random-claim-c', 'status': 'abstained', 'abstention_code': 'X3'},
    ], 'coverage': ledger}
    before = deepcopy(data)
    report = probe.customer_diagnostic(data)
    assert data == before
    assert report['counts'] == {'verified': 1, 'abstained': 1}
    assert report['selected_claim_count'] == 2 and report['all_excerpt_claim_count'] == 3
    assert report['claims_removed_from_agent_input'] == 0
    assert report['all_excerpt_ledger'] == ledger
    assert report['other_excerpt_claims'][0]['result']['abstention_code'] == 'X3'
    assert not report['selected_claims'][1]['linked_to_nominated_prospectus']
    assert report['financial_accuracy'] == 'unknown_requires_independent_gold'


def test_prepare_only_does_not_access_saved_config_or_call_provider(tmp_path, monkeypatch):
    draft, corpus = tmp_path/'original.pdf', tmp_path/'official.pdf'
    draft.write_bytes(b'original')
    corpus.write_bytes(b'official')
    protected = {str(path): sha256_file(path) for path in (draft, corpus)}
    monkeypatch.setattr(probe, 'read_seed', lambda _: {
        'draft': draft, 'report': corpus, 'company': 'Invented fixture issuer',
        'report_resource': {'path': str(corpus), 'sha256': sha256_file(corpus), 'publish_date': '2023-10-16'},
        'protected_hashes': protected})
    def excerpt(_, target):
        target.write_bytes(b'new-excerpt')
        return {'original_sha256': protected[str(draft)], 'pages': []}
    monkeypatch.setattr(probe, 'extract_table_excerpt', excerpt)
    monkeypatch.setattr(probe, 'source_lock', lambda: {'test': 'source-lock'})
    monkeypatch.setattr(probe.native_probe, 'execute', lambda _: pytest.fail('prepare-only called provider flow'))
    assert probe.execute(args(tmp_path, prepare_only=True)) == 0
    receipt = json.loads((tmp_path/'local/acceptance.json').read_text(encoding='utf-8'))
    assert receipt['status'] == 'prepared_not_executed'
    assert receipt['real_provider_api'] == 'not_executed'
    assert receipt['kernel_replay']['status'] == receipt['agent_replay']['status'] == 'not_executed'
    assert receipt['full_report_acceptance'] is False
    assert receipt['request']['corpus'] == str(corpus)
    assert receipt['request']['facts'] == receipt['request']['knowledge_root'] == ''
    assert receipt['request']['corpus_additions'] == []
    assert receipt['request']['scope_declaration'] == ''
    assert receipt['historical_seed_unchanged']


@pytest.mark.parametrize('style', ['direct', 'module'])
def test_help_works_without_real_api_or_install(tmp_path, style):
    command = [sys.executable, '-X', 'utf8']
    command += ([str(probe.ROOT/'scripts/source_customer_table_acceptance.py')] if style == 'direct'
                else ['-m', 'scripts.source_customer_table_acceptance'])
    completed = subprocess.run(command+['--help'], cwd=tmp_path if style == 'direct' else probe.ROOT,
                               capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert completed.returncode == 0, completed.stderr
    assert '--prepare-only' in completed.stdout

