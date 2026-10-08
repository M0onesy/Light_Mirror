"""Explicit issuer aliases backed by official exchange identity records.

No substring/amount matching and no inference that similarly named issuers
are the same company. Disclosure cutoff still applies independently.
"""
from __future__ import annotations

ISSUERS = {
    'gfbj0839493': {
        'names': frozenset({'并行科技', '北京并行科技', '北京并行科技股份有限公司'}),
        'codes': frozenset({'839493', '920493'}),
        'sources': ('cninfo:bj_stock.json:orgId=gfbj0839493', 'bse:project=229:companyCd=839493'),
    },
}


def same_issuer(first: str, second: str) -> bool:
    if first == second:
        return True
    return any(first in row['names'] | row['codes'] and second in row['names'] | row['codes']
               for row in ISSUERS.values())
