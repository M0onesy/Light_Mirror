"""Unit checks for the scope-clarifier snapshot exporter.

Covers the ``caliber_hint`` join added so the interactive tool can *display*
the original-text scope clue without ever auto-filling a declaration.
"""
import importlib.util
import json
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'export_scope_clarifier_data.py'
spec = importlib.util.spec_from_file_location('export_scope_clarifier_data', MODULE_PATH)
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


def _results(groups, claims):
    return {
        'run_id': 'run-test',
        'draft_text': 'x',
        'claims': claims,
        'scope_clarification_groups': groups,
        'summary': {'verified': 0, 'violated': 0, 'abstained': 3},
    }


def _group(group_id, claim_ids, account='REVENUE'):
    return {
        'id': group_id, 'label': '正文 · ' + account, 'account': account,
        'table_id': '', 'periods': ['2023Y'], 'claim_ids': claim_ids,
        'blockers': [{'code': 'X3', 'kind': 'source_scope', 'reason': '口径未声明'}],
        'conditional_verification': True,
        'selector': {'claim_ids': claim_ids},
        'occurrences': len(claim_ids),
    }


def test_caliber_hint_is_merged_across_group_claims():
    groups = [_group('g1', ['c1', 'c2'])]
    claims = [
        {'claim_id': 'c1', 'caliber_hint': ['合并']},
        {'claim_id': 'c2', 'caliber_hint': ['未扣非', '合并']},
    ]
    snap = exporter.build_snapshot(_results(groups, claims), source_path='r.json')
    assert snap['groups'][0]['caliber_hint'] == ['合并', '未扣非']


def test_missing_hint_yields_empty_list():
    groups = [_group('g1', ['c1'])]
    claims = [{'claim_id': 'c1'}]
    snap = exporter.build_snapshot(_results(groups, claims), source_path='r.json')
    assert snap['groups'][0]['caliber_hint'] == []


def test_missing_required_group_field_raises():
    group = _group('g1', ['c1'])
    del group['blockers']
    try:
        exporter.build_snapshot(_results([group], []), source_path='r.json')
    except ValueError as error:
        assert 'blockers' in str(error)
    else:
        raise AssertionError('expected ValueError for missing field')


def test_snapshot_round_trips_as_json():
    groups = [_group('g1', ['c1'])]
    claims = [{'claim_id': 'c1', 'caliber_hint': ['合并']}]
    snap = exporter.build_snapshot(_results(groups, claims), source_path='r.json')
    assert json.loads(json.dumps(snap, ensure_ascii=False)) == snap
