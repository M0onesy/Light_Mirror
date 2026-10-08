"""Diagnostic reads must expose missing work without altering old evidence."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import uuid

import pytest

from scripts.diagnose_binding_coverage import diagnose, main, markdown_report, write_report


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


def frozen_run(tmp_path):
    run = tmp_path / 'frozen'
    run.mkdir()
    claim_ids = [uuid.uuid4().hex for _ in range(4)]
    source = dict(company='Issuer', account='REVENUE', period='2023Y', scope='consolidated',
                  relation='value', base_period='', dimension_kind='total', dimension_id='',
                  currency='CNY', definition_id='', explicit=dict(company=False, account=True, period=True))
    claims, results, mentions = [], [], []
    for n, cid in enumerate(claim_ids):
        dims = deepcopy(source)
        if n == 1:
            dims.update(account='UNKNOWN', period='', scope='', relation='')
        if n == 2:
            dims.update(account='NET_MARGIN_UNSPECIFIED')
        claims.append(dict(claim_id=cid, start=n * 10, end=n * 10 + 3, text=f'context {n}',
                           unit='CNY', parse_status='parsed', binding_status='agreed',
                           table_context={'evaluation_scope': 'issuer_historical_financial',
                                          'source_dimensions': dims, 'headers': [{'text': 'historical column'}]}))
        blockers = [] if n == 0 else [dict(code='X3', kind='source_binding', reason='source dimensions unresolved'),
                                      dict(code='X1', kind='operand', account='COST', period='2023Y',
                                           lookup_status='missing', evidence=['operand-page'])]
        results.append(dict(claim_id=cid, status='verified' if n == 0 else 'abstained',
                            abstention_code=None if n == 0 else 'X3', source_dimensions=dims,
                            blockers=blockers, evidence=[f'fact-page-{n}']))
        mentions.append(dict(id=uuid.uuid4().hex, claim_id=cid, start=n * 10, end=n * 10 + 3,
                             text=f'occurrence {n}', evaluation_scope='issuer_historical_financial',
                             page_id='page', block_id='block', status='judged'))
    # Same assertion occurs twice; actual acceptance must retain both positions.
    mentions.append({**mentions[0], 'id': uuid.uuid4().hex, 'start': 40, 'end': 43})
    claims[-1]['table_context']['evaluation_scope'] = 'industry_historical'
    mentions[-2]['evaluation_scope'] = 'industry_historical'
    results[-1]['source_dimensions']['evaluation_scope'] = 'industry_historical'
    payload = dict(run_id='immutable-run', mode='harness', draft='original.pdf', claims=claims, results=results,
                   model={'status': 'success', 'mode': 'harness', 'tokens': 500, 'cost': 'unknown'},
                   coverage={'mentions': mentions, 'pages': [{'id': 'page', 'page': 7}],
                             'blocks': [{'id': 'block', 'page': 7, 'bbox': [1, 2, 3, 4]}]})
    bindings = {'A': {}, 'B': {}, 'agreement': {}}
    trace = []
    for role, key in [('planner', 'A'), ('verifier', 'B')]:
        for n, cid in enumerate(claim_ids[:3]):
            binding = {field: source.get(field) for field in ('company', 'account', 'period', 'scope', 'relation', 'base_period')}
            if key == 'B' and n == 1:
                binding['scope'] = 'parent'
            bindings[key][cid] = binding
            bindings['agreement'][cid] = dict(agreed=n == 0)
            trace.append(dict(role=role, tool='mj_submit_bindings', call_id=f'{role}-{n}',
                              session_id=f'{role}-session', input={'bindings': [{'mention_id': cid, 'binding': binding}]},
                              output={'accepted': n != 2, 'binding_ids': [cid] if n != 2 else []},
                              status='success' if n != 2 else 'rejected'))
    _write(run / 'results.json', payload)
    _write(run / 'bindings.json', bindings)
    _write(run / 'tool_trace.json', trace)
    events = [dict(type='model_call', status='success', role='planner'),
              dict(type='model_request', cache_hit=False, replay_only=False),
              dict(type='model_response_cache')]
    (run / 'audit.jsonl').write_text('\n'.join(json.dumps(e) for e in events), encoding='utf-8')
    return run, claim_ids


def hashes(run):
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in run.iterdir()}


def test_runtime_success_is_separate_from_actual_binding_coverage(tmp_path):
    run, ids = frozen_run(tmp_path)
    before = hashes(run)
    report = diagnose(run)
    runtime, coverage = report['model_runtime'], report['binding_coverage']
    assert runtime['all_observed_prompts_succeeded'] is True
    assert runtime['non_cached_non_replay_request_count'] == 1
    assert coverage['primary_occurrences'] == 4 and coverage['unique_primary_claims'] == 3
    assert coverage['both_binding_records_occurrences'] == 4
    assert coverage['both_actual_submissions_occurrences'] == 3
    assert coverage['both_actual_submissions_rate'] == 0.75
    assert coverage['recorded_agreed_occurrences'] == 2
    assert coverage['result_status_counts'] == {'verified': 2, 'abstained': 2}
    rejected = next(row for row in report['rows'] if row['claim_id'] == ids[2])
    assert rejected['binding_A']['record_present'] is True
    assert rejected['binding_A']['tool_attempted'] is True
    assert rejected['binding_A']['actually_submitted'] is False
    assert hashes(run) == before


def test_source_gaps_keep_all_blockers_and_implicit_issuer_context(tmp_path):
    run, ids = frozen_run(tmp_path)
    report = diagnose(run)
    by_id = {row['claim_id']: row for row in report['rows']}
    good = by_id[ids[0]]
    assert 'company' not in good['known_source_gaps']['missing_dimensions']
    assert 'company' in good['known_source_gaps']['source_fields_not_explicit']
    unknown = by_id[ids[1]]
    assert set(unknown['known_source_gaps']['missing_dimensions']) == {'account', 'period', 'scope', 'relation'}
    assert unknown['binding_field_differences'] == ['scope']
    assert unknown['first_blocker']['kind'] == 'source_binding'
    assert unknown['other_recorded_blockers'][0]['lookup_status'] == 'missing'
    assert unknown['known_source_gaps']['recorded_fact_or_operand_blockers'][0]['account'] == 'COST'
    assert by_id[ids[2]]['known_source_gaps']['undefined_source_method'] is True
    assert unknown['source_location']['page'] == 7
    assert unknown['source_location']['bbox'] == [1, 2, 3, 4]
    assert unknown['evidence_ids'] == ['fact-page-1']


def test_records_without_trace_do_not_claim_actual_tool_submission(tmp_path):
    run, ids = frozen_run(tmp_path)
    (run / 'tool_trace.json').unlink()
    (run / 'audit.jsonl').unlink()
    report = diagnose(run)
    assert report['model_runtime']['all_observed_prompts_succeeded'] is None
    assert report['binding_coverage']['both_actual_submissions_occurrences'] is None
    assert all(row['binding_A']['actually_submitted'] is None for row in report['rows'])


def test_single_tool_binding_identity_requires_host_accepted_id(tmp_path):
    run, ids = frozen_run(tmp_path)
    trace = [dict(role='planner', tool='mj_submit_binding', call_id='singular',
                  status='success', input={'mention_id': ids[0]},
                  output={'accepted': True, 'binding_id': f'planner:{ids[0]}'})]
    _write(run / 'tool_trace.json', trace)
    report = diagnose(run)
    assert report['rows'][0]['binding_A']['actually_submitted'] is True
    assert report['rows'][0]['binding_B']['actually_submitted'] is False
    trace[0]['output']['binding_id'] = 'another-identity'
    _write(run / 'tool_trace.json', trace)
    assert diagnose(run)['rows'][0]['binding_A']['actually_submitted'] is False


def test_high_frequency_samples_are_bounded_and_deterministic(tmp_path):
    run, ids = frozen_run(tmp_path)
    report = diagnose(run, sample_per_category=1, category_limit=1)
    assert len(report['samples']) == 1
    assert len(report['samples'][0]['samples']) == 1
    assert report['samples'][0]['samples'][0]['source_location']['page'] == 7
    assert report == diagnose(run, sample_per_category=1, category_limit=1)
    assert report['sample_policy']['no_amount_matching'] is True
    assert report['diagnostic_only'] is True


@pytest.mark.parametrize('field', ['claims', 'results', 'mentions'])
def test_duplicate_frozen_id_is_rejected(tmp_path, field):
    run, ids = frozen_run(tmp_path)
    payload = json.loads((run / 'results.json').read_text())
    rows = payload['coverage']['mentions'] if field == 'mentions' else payload[field]
    rows.append(deepcopy(rows[0]))
    _write(run / 'results.json', payload)
    with pytest.raises(ValueError, match='duplicate'):
        diagnose(run)


def test_output_cannot_change_frozen_run(tmp_path):
    run, ids = frozen_run(tmp_path)
    report = diagnose(run)
    before = hashes(run)
    for path in [run / 'diagnosis.json', run / 'results.json']:
        with pytest.raises(ValueError, match='outside'):
            write_report(report, path)
    target = tmp_path / 'new-diagnosis' / 'review.json'
    write_report(report, target)
    assert json.loads(target.read_text(encoding='utf-8')) == report
    assert hashes(run) == before


def test_cli_writes_separate_small_sample_receipt_without_kernel_execution(tmp_path, capsys):
    run, ids = frozen_run(tmp_path)
    target = tmp_path / 'diagnosis.json'
    before = hashes(run)
    assert main(['--run', str(run), '--out', str(target), '--sample-per-category', '1', '--category-limit', '2']) == 0
    output = json.loads(capsys.readouterr().out)
    assert output['run_id'] == 'immutable-run'
    assert target.exists() and hashes(run) == before


def test_absent_ledger_is_not_inferred_from_verdict_or_model(tmp_path):
    run, ids = frozen_run(tmp_path)
    payload = json.loads((run / 'results.json').read_text())
    payload['coverage'] = {}
    _write(run / 'results.json', payload)
    with pytest.raises(ValueError, match='occurrence ledger'):
        diagnose(run)


def test_readable_report_uses_the_same_recorded_counts_and_input_hashes(tmp_path):
    run, ids = frozen_run(tmp_path)
    report = diagnose(run, sample_per_category=1, category_limit=1)
    text = markdown_report(report)
    assert '| 主范围核实 | 2 |' in text
    assert '| 主范围弃权 | 2 |' in text
    assert '| A/B 工具提交覆盖率 | 75.00% |' in text
    assert report['input_hashes']['results.json'] in text
    assert ids[1] in text or ids[2] in text
    assert 'unknown' in text


def test_markdown_cli_rejects_frozen_destination(tmp_path):
    run, ids = frozen_run(tmp_path)
    before = hashes(run)
    with pytest.raises(ValueError, match='outside'):
        main(['--run', str(run), '--out', str(tmp_path / 'diagnostic.json'),
              '--markdown', str(run / 'immutable.md')])
    assert hashes(run) == before
