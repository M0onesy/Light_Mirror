"""Presentation of execution coverage, independent from financial verdicts.

This module never changes frozen results. Pending Agent work is a task state,
not an additional financial verdict or a completed abstention.
"""
from __future__ import annotations

from collections import Counter


def _records(value) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _ids(value) -> set[str]:
    return {item for item in value if isinstance(item, str) and item} if isinstance(value, list) else set()


def _execution_state(payload: dict):
    coverage = payload.get('coverage') if isinstance(payload.get('coverage'), dict) else None
    mentions = _records((coverage or {}).get('mentions'))
    declared = payload.get('execution') if isinstance(payload.get('execution'), dict) else {}
    pending = _ids(payload.get('pending_claim_ids')) | _ids(declared.get('pending_claim_ids'))
    pending |= {item['claim_id'] for item in mentions if item.get('status') == 'pending' and item.get('claim_id')}
    completed = _ids(declared.get('judged_claim_ids'))
    completed |= {item['claim_id'] for item in mentions if item.get('status') in {'judged', 'abstained'} and item.get('claim_id')}
    rows = _records(payload.get('results'))
    states = {}
    for row in rows:
        cid = row.get('claim_id')
        if cid in pending:
            states[cid] = 'pending'
        elif row.get('status') not in {'verified', 'violated', 'abstained'}:
            states[cid] = 'unknown'
        elif cid in completed or coverage is None:
            states[cid] = 'processed'
        else:
            states[cid] = 'unknown'
    claims = _records(payload.get('claims'))
    claimed_ids = {item['claim_id'] for item in claims if item.get('claim_id')}
    unknown = (claimed_ids - set(states) - pending) | {cid for cid, status in states.items() if status == 'unknown'}
    return coverage, mentions, declared, pending, completed, rows, states, claims, unknown


def project_execution(payload: dict) -> dict:
    """Return truthful task statistics without exposing amounts or model prose.

    Explicit pending IDs from current runs and pending ledger rows from older
    frozen runs are both respected. The original three-state summary remains
    available in the source payload for numerical replay.
    """
    coverage, mentions, declared, pending, _completed, rows, states, claims, unknown = _execution_state(payload)
    result_counts = Counter(row.get('status') for row in rows if states.get(row.get('claim_id')) == 'processed')
    ledger_counts = Counter(item.get('status') for item in mentions)
    candidates = [item for item in mentions if item.get('status') not in {'out_of_scope', 'rule_excluded'}]
    processed_mentions = sum(item.get('status') in {'judged', 'abstained'} for item in candidates)
    pending_mentions = ledger_counts['pending'] if coverage is not None else None
    incomplete = bool(payload.get('partial') or pending or unknown or pending_mentions)
    claim_ids = {item['claim_id'] for item in claims if item.get('claim_id')} | {row.get('claim_id') for row in rows} | pending
    claim_ids.discard(None)
    native_checked = _ids(declared.get('native_verified_claim_ids'))
    declared_view = {key: sorted(_ids(declared.get(key))) for key in
                     ('native_verified_claim_ids', 'pending_claim_ids', 'judged_claim_ids') if key in declared}
    return {**declared_view, 'schema_version': 'run-execution.v1',
            'status': 'partial' if incomplete else 'completed' if coverage is not None else 'unknown_coverage',
            'partial': incomplete, 'coverage_known': coverage is not None,
            'summary': {key: result_counts[key] for key in ('verified', 'violated', 'abstained')} |
                       {'pending': len(pending), 'unknown': len(unknown)},
            'summary_basis': 'completed_claims_only', 'claim_count': len(claim_ids),
            'processed_claims': sum(result_counts.values()), 'pending_claims': len(pending),
            'kernel_result_count': len(rows), 'unknown_claims': len(unknown),
            'native_tool_checked_claims': len(native_checked) if 'native_verified_claim_ids' in declared else None,
            'financial_candidates': len(candidates) if coverage is not None else None,
            'processed_mentions': processed_mentions if coverage is not None else None,
            'pending_mentions': pending_mentions,
            'unbound_pending_mentions': sum(item.get('status') == 'pending' and not item.get('claim_id') for item in mentions)
                                       if coverage is not None else None,
            'coverage': processed_mentions / len(candidates) if candidates else None,
            'recall': 'unknown_requires_independent_gold'}


def project_result_view(payload: dict) -> dict:
    """Decorate Web results and separately list work with no financial verdict."""
    _coverage, mentions, _declared, pending, _completed, rows, states, claims, _unknown = _execution_state(payload)
    claim_map = {item['claim_id']: item for item in claims if item.get('claim_id')}
    ledger_map = {item['claim_id']: item for item in mentions if item.get('claim_id')}
    result_rows = [dict(row, execution_status=states.get(row.get('claim_id'), 'unknown'),
                        display_status=row.get('status') if states.get(row.get('claim_id')) == 'processed'
                                       else states.get(row.get('claim_id'), 'unknown')) for row in rows]
    pending_items = []
    for cid in pending:
        claim, mention = claim_map.get(cid, {}), ledger_map.get(cid, {})
        start, end = claim.get('start', mention.get('start')), claim.get('end', mention.get('end'))
        pending_items.append({'claim_id': cid, 'text': claim.get('text', mention.get('text', '')),
                              'execution_status': 'pending', 'display_status': 'pending',
                              'source_span': {'start': start, 'end': end},
                              'reason': mention.get('reason', 'native_work_not_completed')})
    pending_items.sort(key=lambda item: (item['source_span']['start'] if isinstance(item['source_span']['start'], int) else 10**15, item['claim_id']))
    view = dict(payload, view_schema='run-result-view.v1', execution=project_execution(payload),
                results=result_rows, pending_items=pending_items)
    if isinstance(payload.get('analysis'), dict):
        from .presentation import analysis_record_text, display_standard_value, standard_unit
        analysis = dict(payload['analysis'])
        analysis['metrics'] = [dict(row, display_value=display_standard_value(
            row.get('value'), standard_unit(str(row.get('metric') or ''), row)))
            for row in _records(analysis.get('metrics'))]
        for category in ('signals', 'events'):
            analysis[category] = [dict(row, display_text=analysis_record_text(
                category, row, analysis.get('parameters') or {}))
                for row in _records(analysis.get(category))]
        view['analysis'] = analysis
    return view
