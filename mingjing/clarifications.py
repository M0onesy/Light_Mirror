"""Archive host-approved scope material and describe unresolved scope groups.

No fact lookup or amount-based scope selection happens here. A clarification
creates a new conditional task; it never proves the original report's scope.
"""
from __future__ import annotations

from pathlib import Path


def request_scope_clarifications(request: dict) -> list:
    from .scope import validate_scope_clarifications, validate_scope_declaration
    specs = validate_scope_clarifications(request.get('scope_clarifications', []))
    if specs and validate_scope_declaration(request.get('scope_declaration', '')):
        raise ValueError('分组口径澄清与全篇口径声明不能同时使用；请选择明确适用的范围')
    return specs


def prepare_scope_materials(claims: list, request: dict, *, draft_sha256: str,
                            output: Path, archive) -> tuple[list, list[dict]]:
    """archive(path, resource) must atomically write/check bytes and return SHA."""
    from .scope import (apply_scope_clarification, create_scope_clarification,
                        create_grouped_scope_clarifications, scope_clarification_metadata)
    specs = request_scope_clarifications(request)
    resource = (create_grouped_scope_clarifications(claims, specs, draft_sha256=draft_sha256)
                if specs else create_scope_clarification(request.get('scope_declaration', ''),
                                                       draft_sha256=draft_sha256))
    if not resource:
        return claims, []
    name = 'scope_clarifications.json' if specs else 'clarifications.json'
    path = output / name
    checksum = archive(path, resource)
    metadata = scope_clarification_metadata(resource, checksum)
    updated = apply_scope_clarification(claims, metadata, draft_sha256=draft_sha256)
    return updated, [{**resource, 'sha256': checksum, 'path': str(path.resolve())}]


def scope_clarification_groups(claims: list, results: list[dict]) -> list[dict]:
    """Group actual unknown scope occurrences, keeping all other blockers visible."""
    from .scope import scope_table_id, source_scope_dimensions
    by_id = {row['claim_id']: row for row in results}
    groups = {}
    for claim in claims:
        row = by_id.get(claim.claim_id, {})
        if (row.get('status') != 'abstained' or row.get('display_status') == 'pending'
                or claim.modality != 'fact'):
            continue
        scope = source_scope_dimensions(claim)
        if (scope.get('scope_header') or scope.get('source_scope_conflict')
                or scope.get('source_scope_ambiguous') or claim.parse_status != 'parsed'):
            continue
        table_id = scope_table_id(claim)
        key = (table_id, claim.account)
        context = claim.table_context if isinstance(claim.table_context, dict) else {}
        proof = context.get('source_dimensions') or {}
        if isinstance(proof, dict) and proof.get('evaluation_scope') not in {None, '', 'issuer_historical_financial'}:
            continue
        title_source = context.get('title_source')
        title = (title_source.get('text') if isinstance(title_source, dict) else '') or context.get('title') or '正文'
        title = str(title)
        group = groups.setdefault(key, {'id': 'scope-group-'+str(len(groups)+1),
            'label': title.strip()+' · '+claim.account, 'account': claim.account,
            'table_id': table_id, 'periods': [], 'claim_ids': [], 'blockers': [],
            'conditional_verification': True})
        group['claim_ids'].append(claim.claim_id)
        if claim.period not in group['periods']:
            group['periods'].append(claim.period)
        for blocker in row.get('blockers', []):
            if blocker not in group['blockers']:
                group['blockers'].append(blocker)
    for group in groups.values():
        group['selector'] = {'claim_ids': list(group['claim_ids'])}
        group['occurrences'] = len(group['claim_ids'])
    return list(groups.values())
