"""New CLI runs freeze source dimensions before either API or kernel use."""
import hashlib
import json
from pathlib import Path

import pytest

from mingjing.cli import build_parser, command_check
from mingjing.replay import replay_run


@pytest.mark.parametrize('source,declaration,status', [
    ('2023年公司营业收入100元。', '', 'abstained'),
    ('公司营业收入100元。', '合并', 'abstained'),
    ('2023年公司合并口径营业收入100元。', '', 'verified'),
    ('2023年公司营业收入100元。', '合并', 'verified'),
])
def test_cli_protects_source_period_and_scope_without_rewriting_earlier_runs(tmp_path, source, declaration, status):
    draft = tmp_path/'draft.md'
    draft.write_text(source, encoding='utf-8')
    corpus = tmp_path/'corpus'
    corpus.mkdir()
    (corpus/'facts.json').write_text(json.dumps([{'fact_id': 'revenue', 'company': '公司甲', 'account': 'REVENUE',
        'period': '2023Y', 'scope': '合并', 'value': '100', 'publish_date': '2024-03-01'}]), encoding='utf-8')
    output = tmp_path/'new-cli-run'
    args = build_parser().parse_args(['check', '--draft', str(draft), '--corpus', str(corpus),
        '--company', '公司甲', '--period', '2023Y', '--report-date', '2024-04-01', '--out', str(output),
        '--scope-declaration', declaration])
    assert command_check(args) == 0
    result = json.loads((output/'results.json').read_text(encoding='utf-8'))
    assert result['results'][0]['status'] == status
    if status == 'abstained':
        assert result['results'][0]['abstention_code'] == 'X3'
    claim = result['claims'][0]
    assert claim['table_context']['source_scope_required'] is True
    assert claim['candidate_value'] == '100' and claim['text'] == source.rstrip('。')
    archived = json.loads((output/'source_dimensions.json').read_text(encoding='utf-8'))
    proof = archived['dimensions'][0]
    assert proof['account'] == 'REVENUE' and proof['relation'] == 'value'
    assert proof['period'] == ('2023Y' if '2023年' in source else '')
    assert proof['scope'] == ('合并' if declaration or '合并口径' in source else '')
    assert archived['draft_sha256'] == hashlib.sha256(draft.read_bytes()).hexdigest()
    doc_hash = hashlib.sha256((output/'source_dimensions.json').read_bytes()).hexdigest()
    assert result['source_dimensions_resource']['sha256'] == doc_hash
    manifest = json.loads((output/'run_manifest.json').read_text(encoding='utf-8'))
    assert any(Path(row['path']).name == 'source_dimensions.json' and row['sha256'] == doc_hash for row in manifest['inputs'])
    assert replay_run(output)['status'] == 'passed'
    (output/'source_dimensions.json').write_text('{}', encoding='utf-8')
    assert replay_run(output)['status'] == 'failed'


def test_api_selector_runs_only_after_source_dimensions_are_frozen(tmp_path, monkeypatch):
    from mingjing import llm
    root = Path(__file__).resolve().parents[1]
    draft = root/'samples'/'demo_draft_defined.md'
    output = tmp_path/'api-fixture'
    observed = []
    def semantic_selector(text):
        frozen = json.loads((output/'source_dimensions.json').read_text(encoding='utf-8'))
        observed.extend(frozen['dimensions'])
        return [{'source': '公司合并口径2024年营业收入10.00亿元。',
                 'candidate_value': '999999999', 'period': '2099Y', 'verdict': 'verified'}], {'model': 'fixture-only'}
    monkeypatch.setattr(llm, 'decompose_with_metadata', semantic_selector)
    args = build_parser().parse_args(['check', '--draft', str(draft), '--corpus', str(root/'samples'/'corpus'),
        '--report-date', '2025-03-31', '--out', str(output), '--mode', 'agent'])
    assert command_check(args) == 0
    result = json.loads((output/'results.json').read_text(encoding='utf-8'))
    assert len(observed) == len(result['claims']) == 10
    assert result['summary'] == {'verified': 4, 'violated': 5, 'abstained': 1}
    assert result['model']['status'] == 'success' and result['model']['model'] == 'fixture-only'
    assert all(claim['candidate_value'] != '999999999' for claim in result['claims'])
    assert all(proof['period'] != '2099Y' for proof in observed)
    audit = [json.loads(row) for row in (output/'audit.jsonl').read_text(encoding='utf-8').splitlines()]
    assert next(i for i, row in enumerate(audit) if row['type'] == 'source_dimensions_locked') < next(
        i for i, row in enumerate(audit) if row['type'] == 'model_call')
