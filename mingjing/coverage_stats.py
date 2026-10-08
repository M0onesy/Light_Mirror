"""Separate settled work from source-backed financial judgement coverage.

Classification is supplied by the source ledger, never by a model verdict.
Unknown financial candidates remain in the primary population. Repetition is
reported separately and never reduces the occurrence-based acceptance count.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter

PRIMARY_SCOPE = 'issuer_historical_financial'
SCOPE_LABELS = {
    PRIMARY_SCOPE: '公司历史财务', 'issuer_operating': '公司运营信息',
    'industry_historical': '行业历史事实', 'external_entity': '其他主体',
    'forecast': '预测', 'hypothesis': '假设与推论', 'opinion': '观点',
    'rule_excluded': '规则排除', 'unclassified_out_of_scope': '其他范围外',
}
STATUSES = ('verified', 'violated', 'abstained', 'pending', 'out_of_scope', 'rule_excluded')


def _conditional_scope(claim: dict | None, result: dict | None) -> bool:
    """Use the occurrence's actual scope proof, never a task-wide declaration.

    A result can legitimately retain explicit source-table scope even when a
    task has a general user assumption. Prefer that final provenance over the
    pre-binding claim. Older results may lack it, so their frozen source proof
    remains a compatible fallback; a loose declaration is not such a proof.
    """
    context = (claim or {}).get('table_context') or {}
    candidates = ((result or {}).get('source_dimensions'), context.get('source_dimensions'))
    for dimensions in candidates:
        if not isinstance(dimensions, dict):
            continue
        scope_provenance = dimensions.get('scope_provenance')
        if isinstance(scope_provenance, str) and scope_provenance:
            return scope_provenance == 'user_supplied_scope'
        provenance = dimensions.get('provenance')
        if provenance == 'user_supplied_scope':
            return True
        if dimensions.get('conditional_verification') is True:
            return True
        if isinstance(provenance, str) and provenance in {
                'explicit_disclosed_table_reference', 'source_table_consolidated_structure', 'source_account_definition'}:
            return False
    return False


def _scope(entry: dict, claim: dict | None) -> str:
    context = (claim or {}).get('table_context') or {}
    value = entry.get('evaluation_scope') or context.get('evaluation_scope')
    if value in SCOPE_LABELS:
        return value
    if entry.get('status') == 'rule_excluded':
        return 'rule_excluded'
    if entry.get('status') == 'out_of_scope':
        modality = (entry.get('modality_basis') or {}).get('modality')
        if modality == 'forecast' or 'forecast' in entry.get('reason', ''):
            return 'forecast'
        if modality in {'inference', 'hypothesis'}:
            return 'hypothesis'
        if modality == 'opinion' or 'valuation' in entry.get('reason', ''):
            return 'opinion'
        return 'unclassified_out_of_scope'
    return PRIMARY_SCOPE


def _assertion_key(claim: dict | None, entry: dict) -> str:
    if not claim or claim.get('parse_status') != 'parsed' or not claim.get('account') or claim.get('account') == 'UNKNOWN':
        # Undefined dimensions cannot establish that two numbers mean the
        # same thing. Keep their source-addressed occurrences independent.
        return 'occurrence:' + str(entry['id'])
    context = claim.get('table_context') or {}
    dims = context.get('source_dimensions') or context
    key = [claim.get(field) for field in (
        'company', 'account', 'period', 'caliber_hint', 'relation', 'base_period',
        'candidate_value', 'unit', 'display_decimals', 'approximate', 'declared_source')]
    key += [dims.get(field) for field in ('dimension_kind', 'dimension_id', 'currency', 'definition_id')]
    return hashlib.sha256(json.dumps(key, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def coverage_statistics(coverage: dict | None, claims: list[dict], results: list[dict]) -> dict:
    coverage = coverage or {}
    claim_map = {claim['claim_id']: claim for claim in claims}
    result_map = {row['claim_id']: row for row in results}
    if len(claim_map) != len(claims) or len(result_map) != len(results):
        raise ValueError('覆盖统计不接受重复的陈述或判定编号')
    entries = coverage.get('mentions') or []
    if len({entry['id'] for entry in entries}) != len(entries):
        raise ValueError('覆盖统计不接受重复的原文出现编号')
    linked = {entry.get('claim_id') for entry in entries if entry.get('claim_id')}
    if set(claim_map) - linked or linked - set(claim_map):
        raise ValueError('陈述与原文账本映射不完整，不能计算有效判定覆盖率')
    if set(result_map) - set(claim_map):
        raise ValueError('判定缺少对应的冻结陈述')
    scopes: dict[str, list[tuple[dict, dict | None, str, bool]]] = {}
    claim_scopes = {}
    for entry in entries:
        claim = claim_map.get(entry.get('claim_id'))
        result = result_map.get(entry.get('claim_id'))
        context = (claim or {}).get('table_context') or {}
        claim_scope = context.get('evaluation_scope') or (context.get('source_dimensions') or {}).get('evaluation_scope')
        if entry.get('evaluation_scope') and claim_scope and entry['evaluation_scope'] != claim_scope:
            raise ValueError('原文账本与冻结陈述的核查范围冲突')
        ledger_status = entry.get('status', 'pending')
        if ledger_status in {'pending', 'out_of_scope', 'rule_excluded'}:
            status = ledger_status
        else:
            status = (result or {}).get('status') or entry.get('result_status')
            if status not in {'verified', 'violated', 'abstained'}:
                status = 'pending'
        scope = _scope(entry, claim)
        if scope == PRIMARY_SCOPE and status in {'out_of_scope', 'rule_excluded'}:
            status = 'pending'
        if claim:
            if claim['claim_id'] in claim_scopes and claim_scopes[claim['claim_id']] != scope:
                raise ValueError('同一陈述的原文出现位置具有冲突的核查范围')
            claim_scopes[claim['claim_id']] = scope
        scopes.setdefault(scope, []).append((entry, claim, status, _conditional_scope(claim, result)))
    groups = {}
    for scope, rows in scopes.items():
        counts = Counter(status for _, _, status, _ in rows)
        conditional = Counter(status for _, _, status, is_conditional in rows if is_conditional)
        population = sum(counts[status] for status in ('verified', 'violated', 'abstained', 'pending'))
        completed = population - counts['pending']
        judged = counts['verified'] + counts['violated']
        conditional_judged = conditional['verified'] + conditional['violated']
        source_supported_judged = judged - conditional_judged
        keys = {_assertion_key(claim, entry) for entry, claim, _, _ in rows}
        groups[scope] = {
            'label': SCOPE_LABELS[scope], 'occurrences': len(rows),
            'unique_assertions': len(keys), 'financial_candidates': population,
            'counts': {status: counts[status] for status in STATUSES},
            'conditional_counts': {status: conditional[status] for status in STATUSES},
            'processed': completed, 'judged': judged,
            'conditional_judged': conditional_judged,
            'source_supported_judged': source_supported_judged,
            'processing_completion_rate': completed / population if population else None,
            'judgement_coverage': judged / population if population else None,
            'source_supported_judgement_coverage': source_supported_judged / population if population else None,
            'abstention_rate': counts['abstained'] / population if population else None,
            'pending': counts['pending'],
        }
    core = groups.get(PRIMARY_SCOPE) or {
        'label': SCOPE_LABELS[PRIMARY_SCOPE], 'occurrences': 0, 'unique_assertions': 0,
        'financial_candidates': 0, 'counts': dict.fromkeys(STATUSES, 0), 'processed': 0,
        'conditional_counts': dict.fromkeys(STATUSES, 0), 'conditional_judged': 0, 'source_supported_judged': 0,
        'judged': 0, 'processing_completion_rate': None, 'judgement_coverage': None,
        'source_supported_judgement_coverage': None,
        'abstention_rate': None, 'pending': 0,
    }
    fact_dimensions = set()
    for entry, claim, status, _ in scopes.get(PRIMARY_SCOPE, []):
        if status != 'verified' or not claim:
            continue
        context = claim.get('table_context') or {}
        dims = context.get('source_dimensions') or context
        fact_dimensions.add(json.dumps([
            claim.get('company'), claim.get('account'), claim.get('period'), claim.get('caliber_hint'),
            claim.get('relation') or 'value', claim.get('base_period') or '',
            dims.get('dimension_kind'), dims.get('dimension_id'), dims.get('currency'), dims.get('definition_id'),
        ], ensure_ascii=False, sort_keys=True))
    return {
        'schema_version': 'financial-coverage.v3', 'primary_scope': PRIMARY_SCOPE,
        'policy': '主范围按全部公司历史财务出现位置计算；未确定金融候选保留在主范围；辅助类别独立列示。',
        'occurrences': len(entries), 'groups': groups, 'core': core, 'claim_scopes': claim_scopes,
        'verified_fact_dimensions': len(fact_dimensions),
        'abstention_target': 9,
        'abstention_target_met': bool(core['financial_candidates'] and not core['pending'] and core['counts']['abstained'] <= 9),
        'unconditional_unresolved': core['counts']['abstained'] + core['conditional_judged'],
        'unconditional_abstention_target_met': bool(core['financial_candidates'] and not core['pending']
            and core['counts']['abstained'] + core['conditional_judged'] <= 9),
        'professional_acceptance': 'unknown_requires_independent_gold',
        'legacy_coverage_meaning': 'processing_completion_rate_including_abstention',
    }


def attach_coverage_statistics(payload: dict) -> dict:
    """Attach a single authoritative record shared by JSON, HTML and Web."""
    payload['coverage_statistics'] = coverage_statistics(
        payload.get('coverage'), payload.get('claims') or [], payload.get('results') or [])
    return payload
