"""Source-written financial dimensions; amounts never determine identity."""
from __future__ import annotations
import re

DIMENSION_KINDS = frozenset({'total', 'business', 'customer', 'customer_group', 'purchase_mode', 'geography'})

def dimension_ids_compatible(kind: str, requested: str, available: str) -> bool:
    """Return whether two explicit source labels may refer to one dimension.

    This function has no issuer or disclosure identity, so it cannot authorise
    abbreviations, legal-suffix removal, prefixes or business aliases. A
    source-proved alias must be resolved within its frozen source table before
    querying the fact store; identical amounts never authorise a mapping.
    """
    requested = normalize_dimension_label(requested)
    available = normalize_dimension_label(available)
    return requested == available

def normalize_dimension_label(value: str) -> str:
    """Remove layout whitespace/terminal unit, preserving anonymised identities."""
    if not isinstance(value, str):
        raise ValueError('维度标签必须为字符串')
    text = re.sub(r'\s+', '', value)
    return re.sub(r'[（(](?:元|万元|亿元|百万元|%|％)[）)]$', '', text)


def anonymous_customer_label(value: str) -> bool:
    """An anonymised label has only document-local identity, never a global alias."""
    label = normalize_dimension_label(value)
    return bool(re.fullmatch(
        r'(?:客户|Customer)?(?:[A-Za-z]{1,3}|[1-9][0-9]*|[甲乙丙丁戊己庚辛壬癸]|[一二三四五六七八九十百]+)',
        label,
    ))

def validate_fact_dimensions(kind, identifier, currency, definition):
    if kind not in DIMENSION_KINDS or not isinstance(identifier, str):
        raise ValueError('未知财务维度')
    if (kind == 'total') != (identifier == ''):
        raise ValueError('总公司维度必须无分部标识；分部维度必须有原文标识')
    if not isinstance(currency, str) or not re.fullmatch(r'[A-Z]{3}', currency):
        raise ValueError('币种必须为三字母明确标识')
    if not isinstance(definition, str) or len(definition) > 200:
        raise ValueError('指标定义标识非法')

def claim_dimensions(claim) -> dict:
    context = claim.table_context or {}
    from .source_semantics import source_dimensions_from_text
    frozen = source_dimensions_from_text(claim)
    result = {key: frozen.get(key, context.get(key, default)) for key, default in
              (('dimension_kind', 'total'), ('dimension_id', ''), ('currency', 'CNY'), ('definition_id', ''))}
    # Explicit source currency outranks a parser's CNY default.
    currency = frozen.get('source_currency') or context.get('source_currency')
    if isinstance(currency, dict) and currency.get('currency'):
        if currency.get('conflict'):
            raise ValueError('原文币种声明冲突')
        result['currency'] = currency['currency']
    for key in ('dimension_kind','dimension_id','currency','definition_id'):
        if key in context and context[key] != result[key]:
            raise ValueError('原文维度与冻结维度冲突：'+key)
    validate_fact_dimensions(result['dimension_kind'], result['dimension_id'], result['currency'], result['definition_id'])
    return result
