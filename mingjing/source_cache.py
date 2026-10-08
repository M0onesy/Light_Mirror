"""Admit machine-extracted facts only under the actual extractor source lock.

This is a new-task admission policy. Historical replay consumes its already
frozen facts separately and must not rewrite old caches to satisfy this lock.
"""
from __future__ import annotations

import re
from pathlib import Path


CACHE_SCHEMA = 'mingjing-source-cache.v3'
EXTRACTOR_MODULES = (
    'parser.py', 'financial_notes.py', 'source_business_mode.py', 'source_customer_quotation.py', 'source_product_financial.py', 'source_quarter_quotation.py', 'issuer_identity.py', 'taxonomy.py',
    'mentions.py', 'dimensions.py', 'evidence.py', 'models.py', 'facts.py',
    'decimal_math.py', 'file_io.py', 'source_gap_catalog.py',
)
_MACHINE_TYPES = {'primary_statement', 'financial_summary', 'financial_note'}


def is_machine_fact(row: dict, *, declared_cache: bool = False) -> bool:
    """A PDF source alone does not turn a manually structured fact into a cache."""
    return (row.get('source_type') in _MACHINE_TYPES
            or str(row.get('extractor_version') or '').startswith(('financial-', 'pdf-'))
            or declared_cache and row.get('source_type') != 'structured')


def extractor_source_modules(**control) -> dict[str, str]:
    from .parser import _file_hash, _parse_checkpoint
    root = Path(__file__).resolve().parent
    hashes = {}
    for name in EXTRACTOR_MODULES:
        _parse_checkpoint(**control)
        source = root / name
        if not source.is_file():
            raise ValueError('当前运行载体缺少可核验的提取器源码；请依据冻结索引重解析')
        hashes[name] = _file_hash(source, **control)
    return hashes


def cache_admission(payload, rows, **control) -> dict:
    declared = isinstance(payload, dict) and 'source_fact_cache' in payload
    declaration = payload.get('source_fact_cache') if isinstance(payload, dict) else None
    if not declared and not any(is_machine_fact(row) for row in rows):
        return {'status': 'structured', 'reason': 'manually_structured_facts'}
    if not isinstance(declaration, dict):
        return {'status': 'reparse', 'reason': 'missing_extractor_lock'}
    if declaration.get('schema_version') != CACHE_SCHEMA:
        return {'status': 'reparse', 'reason': 'legacy_extractor_lock'}
    expected = declaration.get('modules')
    if not isinstance(expected, dict) or not set(EXTRACTOR_MODULES) <= set(expected):
        return {'status': 'reparse', 'reason': 'incomplete_extractor_lock'}
    if any(not isinstance(name, str) or not re.fullmatch(r'[a-z_][a-z_0-9]*\.py', name)
           or not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value)
           for name, value in expected.items()):
        return {'status': 'reparse', 'reason': 'invalid_extractor_lock'}
    try:
        actual = extractor_source_modules(**control)
    except ValueError:
        return {'status': 'reparse', 'reason': 'extractor_source_unavailable'}
    root = Path(__file__).resolve().parent
    from .parser import _file_hash, _parse_checkpoint
    for name, value in expected.items():
        _parse_checkpoint(**control)
        source = root / name
        current = actual.get(name)
        if current is None:
            if not source.is_file():
                return {'status': 'reparse', 'reason': 'extractor_source_unavailable', 'module': name}
            current = _file_hash(source, **control)
        if current != value:
            return {'status': 'reparse', 'reason': 'extractor_source_changed', 'module': name}
    return {'status': 'accepted', 'reason': 'extractor_source_lock_matches'}
