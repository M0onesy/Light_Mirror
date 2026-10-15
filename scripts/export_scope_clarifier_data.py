"""Export scope-clarification data from a MingJing run into the interactive tool.

Reads ``runs/<run>/results.json`` and produces a compact, self-contained data
snapshot (``scope_clarifier_data.json``) that the single-page HTML deliverable
inlines into a ``<script>`` tag. No third-party dependencies are required.

Usage::

    py -3.12 scripts/export_scope_clarifier_data.py \
        --results runs/full-off/results.json \
        --out docs/deliverables/scope-clarifier/scope_clarifier_data.json

The output preserves the *verbatim* group metadata (labels, accounts, periods,
blockers) so the UI never fabricates data. Only the fields the UI needs are
kept; ``selector.claim_ids`` is retained because it is the exact selector the
CLI's ``--scope-clarifications-json`` expects.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


REQUIRED_GROUP_FIELDS = ('id', 'label', 'account', 'periods', 'claim_ids',
                         'blockers', 'selector', 'occurrences')


def _reason_text(blockers: list) -> str:
    """Return the most specific human-readable棄權 reason from a blocker list.

    Prefers the ``source_scope`` blocker (the one that names the missing report
    caliber); falls back to the first blocker that carries a ``reason`` string.
    """
    preferred = ''
    fallback = ''
    for blocker in blockers if isinstance(blockers, list) else []:
        if not isinstance(blocker, dict):
            continue
        reason = blocker.get('reason')
        if not isinstance(reason, str) or not reason:
            continue
        if blocker.get('kind') == 'source_scope':
            preferred = preferred or reason
        fallback = fallback or reason
    return preferred or fallback


def _clean_claim_ids(values) -> list:
    """Return a de-duplicated, order-preserving list of non-empty string ids."""
    seen, out = set(), []
    for value in values if isinstance(values, list) else []:
        if isinstance(value, str) and value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _claim_hint_index(results: dict) -> dict:
    """Map claim_id -> ordered, de-duplicated ``caliber_hint`` list.

    ``caliber_hint`` is a *parsing clue* carried on each claim (e.g. the
    original text says "合并"). It is deliberately surfaced to the UI only as a
    read-only hint so the user can make an informed scope choice; it must never
    be used to auto-fill a declaration (see FINANCIAL_SOURCE_POLICY section 13).
    """
    index: dict = {}
    for claim in results.get('claims', []) or []:
        if not isinstance(claim, dict):
            continue
        claim_id = claim.get('claim_id')
        if not isinstance(claim_id, str) or not claim_id:
            continue
        index[claim_id] = _clean_claim_ids(claim.get('caliber_hint'))
    return index


def _merge_hints(claim_ids: list, hint_index: dict) -> list:
    """Union the caliber hints across a group's claims, order-preserving."""
    merged: list = []
    for claim_id in claim_ids:
        for hint in hint_index.get(claim_id, []):
            if hint not in merged:
                merged.append(hint)
    return merged


def build_snapshot(results: dict, *, source_path: str) -> dict:
    """Build the compact snapshot consumed by the interactive HTML tool."""
    raw_groups = results.get('scope_clarification_groups')
    if not isinstance(raw_groups, list):
        raise ValueError('results.json 缺少 scope_clarification_groups 字段')

    groups = []
    hint_index = _claim_hint_index(results)
    for raw in raw_groups:
        if not isinstance(raw, dict):
            continue
        missing = [field for field in REQUIRED_GROUP_FIELDS if field not in raw]
        if missing:
            raise ValueError('分组缺少必要字段: %s' % ', '.join(missing))
        selector = raw.get('selector') if isinstance(raw.get('selector'), dict) else {}
        claim_ids = _clean_claim_ids(raw.get('claim_ids')) or _clean_claim_ids(selector.get('claim_ids'))
        periods = [str(p) for p in raw.get('periods', []) if isinstance(p, (str, int, float))]
        groups.append({
            'id': str(raw['id']),
            'label': str(raw['label']),
            'account': str(raw['account']),
            'table_id': str(raw.get('table_id', '') or ''),
            'periods': periods,
            'claim_ids': claim_ids,
            'occurrences': int(raw.get('occurrences', len(claim_ids))),
            'blockers': [b for b in raw.get('blockers', []) if isinstance(b, dict)],
            'reason': _reason_text(raw.get('blockers')),
            'caliber_hint': _merge_hints(claim_ids, hint_index),
            'conditional_verification': bool(raw.get('conditional_verification', True)),
            'selector': {'claim_ids': claim_ids},
        })

    summary = results.get('summary') if isinstance(results.get('summary'), dict) else {}
    all_claim_ids = set()
    for group in groups:
        all_claim_ids.update(group['claim_ids'])

    abstained = int(summary.get('abstained', 0) or 0)
    # The number of abstentions attributable to scope is the distinct set of
    # claim ids across all abstained scope groups.
    scope_abstained = len(all_claim_ids)

    snapshot = {
        'schema_version': 'scope-clarifier-data.v1',
        'source': {
            'path': source_path,
            'run_id': str(results.get('run_id', '')),
            'draft': str(results.get('draft', '')),
            'corpus': str(results.get('corpus', '')),
            'report_date': str(results.get('report_date', '')),
            'draft_sha256': hashlib.sha256(
                str(results.get('draft_text', '')).encode('utf-8')).hexdigest(),
            'exported_at': str(results.get('report_date', '')),
        },
        'summary': {
            'verified': int(summary.get('verified', 0) or 0),
            'violated': int(summary.get('violated', 0) or 0),
            'abstained': abstained,
        },
        'totals': {
            'claims_total': len(results.get('claims', []) or []),
            'scope_abstained': scope_abstained,
            'groups': len(groups),
        },
        'groups': groups,
    }
    return snapshot


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='导出明镜口径澄清交互工具所需的数据快照')
    parser.add_argument('--results', required=True, help='runs/<run>/results.json 路径')
    parser.add_argument('--out', required=True, help='输出 JSON 路径')
    args = parser.parse_args(argv)

    results_path = Path(args.results)
    with results_path.open('r', encoding='utf-8') as handle:
        results = json.load(handle)

    snapshot = build_snapshot(results, source_path=str(results_path).replace('\\', '/'))
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open('w', encoding='utf-8') as handle:
        json.dump(snapshot, handle, ensure_ascii=False, indent=2)

    print(json.dumps({
        'out': str(out_path),
        'groups': snapshot['totals']['groups'],
        'scope_abstained': snapshot['totals']['scope_abstained'],
        'summary': snapshot['summary'],
    }, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
