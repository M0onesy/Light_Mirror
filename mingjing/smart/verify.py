"""核查引擎：事实库查询 → 候选定义逐一计算 → 披露区间比较 → 错误归因 → 修改建议。

判定口径（标准模式）
- 法定科目与封闭定义指标：首选定义命中=核实；仅备选定义命中=核实并提示口径（A3）；
  全部不命中=有误，并按 单位/期间/口径/版本/计算 顺序做归因（E4/E2/E3/E9/E5，否则 E1）。
- 开放定义指标（EBITDA、ROIC 等数据商口径）：命中=核实；不命中=定义缺口（不判错）。
- 预测与估值：用研报自身表格与首页行情做内部勾稽（文表一致、公式一致）。
- 缺材料：输出缺口清单（缺哪家公司哪个期间哪个科目），不强行二态。
- 审慎模式（strict=True）：备选定义命中不算核实、口径推定一律弃权，对应旧内核的保守策略。
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field, replace
from decimal import Decimal, localcontext
from datetime import date
import hashlib
import inspect
from functools import lru_cache
from pathlib import Path

from ..models import Fact
from ..facts import FactStore
from ..thresholds import ThresholdAssertion
from ..root_math import cagr_interval
from .claims import (Claim, MARKET_METRICS, OPEN_METRICS, PLAIN_METRICS, RATIO_METRICS, ReportModel, VALUATION_METRICS)
from .common import (IV, MONEY_UNITS, PERCENT_UNITS, compact, find_periods, fmt_amount, norm, period_end, period_label, previous_quarter,
                     quantum, shift_year, to_decimal, unit_scale)
from .taxonomy import account_name
from .detect import code_aliases, same_company, short_name

# 错误分类与计划书表5-6一致：E5 授权计算不相容（含表内计算不平）；E6 引用错误（含正文与研报自身表格不一致）；
# E7 逻辑比较错误（增减方向、排序或比较关系说反）。
ERROR_LABELS = {'E1': '数值错误', 'E2': '期间错误', 'E3': '口径错误', 'E4': '单位错误', 'E5': '计算错误', 'E6': '引用错误',
                'E7': '逻辑比较错误', 'E8': '材料不可验证', 'E9': '披露版本错配'}
# 推定与假设：标准模式“按通行惯例推定并披露”，每条结论写明用了哪些推定，汇总中分别计数
ASSUMPTION_LABELS = {'scope': '默认合并口径', 'period': '期间按上下文推定', 'subject': '主体承接上文', 'metric': '指标由主体推定',
                     'definition': '多义指标按备选定义', 'derived': '推算数（单季/分部上年）', 'negative_base': '负基数按绝对值',
                     'dash_zero': '“-”按零列报', 'hedge': '限定词放宽区间', 'internal': '预测/估值改核内部勾稽',
                     'drift': '估值取价日差异', 'text': '原文佐证（未经重算）', 'identity': '模板项按表内勾稽', 'version': '早期披露版本'}
ABSTAIN_LABELS = {'X1': '缺少可用事实', 'X2': '公式前提不成立', 'X3': '指标/口径定义缺口', 'X4': '来源冲突或置信不足',
                  'X5': '缺回溯日期', 'X6': '主体或维度待确认'}
SIBLINGS = {
    'NP': ['NP_PARENT', 'NP_PARENT_ADJ', 'PROFIT_TOTAL', 'OPERATING_PROFIT'], 'NP_PARENT': ['NP', 'NP_PARENT_ADJ', 'PROFIT_TOTAL'],
    'NP_PARENT_ADJ': ['NP_PARENT', 'NP'], 'REVENUE': ['TOTAL_REVENUE', 'MAIN_REVENUE', 'COST'], 'COST': ['TOTAL_COST', 'REVENUE'],
    'EQUITY_PARENT': ['EQUITY', 'ASSETS'], 'EQUITY': ['EQUITY_PARENT'], 'ASSETS': ['CURRENT_ASSETS', 'EQUITY', 'LIABILITIES'],
    'LIABILITIES': ['CURRENT_LIABILITIES', 'ASSETS'], 'CFO': ['CFI', 'CFF', 'CASH_EQUIVALENTS_CHANGE', 'NP'],
    'EPS': ['EPS_DILUTED', 'EPS_ADJ'], 'OPERATING_PROFIT': ['PROFIT_TOTAL', 'NP'], 'PROFIT_TOTAL': ['OPERATING_PROFIT', 'NP'],
    'CASH_FUNDS': ['CASH_EQUIVALENTS'], 'ROE': ['ROE_ADJ'], 'EXP_RD': ['RD_INVESTMENT'],
}


class Missing(Exception):
    def __init__(self, account: str, period: str, why: str = '', code: str = 'X1'):
        super().__init__(f'{account}@{period}')
        self.account, self.period, self.why = account, period, why
        self.code = code


# ───────────────────────── 事实库 ─────────────────────────
_TYPE_RANK = {'primary_statement': 4, 'financial_summary': 3, 'segment_table': 3, 'structured': 3, 'note': 2, 'report_table': 1}


def fact_iv(fact: Fact) -> IV:
    unit = fact.display_unit if fact.display_unit not in ('％',) else '%'
    return IV.rounded(fact.value, quantum(unit, fact.decimals))


class FactBase:
    def __init__(self, facts: list[Fact], report_date: str = '', label: str = 'disclosed', *, company: str = '', currency: str = 'CNY',
                 source_metadata: dict | None = None, subject=None):
        companies = {fact.company for fact in facts if fact.company}
        self.company = company or (next(iter(companies)) if len(companies) == 1 else '')
        self.currency = currency
        self.subject_evidence = {}
        admitted = []
        for fact in facts:
            metadata = (source_metadata or {}).get(fact.source_doc, {})
            declared_names = {name for name in (metadata.get('company'), metadata.get('company_full'),
                                                short_name(metadata.get('company_full', ''))) if name}
            source_code = metadata.get('stock_code', '')
            subject_code = subject.get('stock_code', '') if isinstance(subject, dict) else getattr(subject, 'stock_code', '')
            if fact.company != self.company and fact.company in declared_names and source_code and subject_code \
                    and re.fullmatch(r'\d{6}', source_code) and re.fullmatch(r'\d{6}', subject_code) \
                    and code_aliases(source_code) & code_aliases(subject_code):
                self.subject_evidence[fact.fact_id] = {'source_company': fact.company, 'resolved_company': self.company,
                                                       'source_stock_code': source_code, 'subject_stock_code': subject_code,
                                                       'source_doc': fact.source_doc, 'basis': 'source_declared_security_code'}
                fact = replace(fact, company=self.company)
            admitted.append(fact)
        self.facts = [replace(f, company=self.company) if f.company != self.company else f for f in admitted
                      if self.company and (f.company == self.company or short_name(f.company) == short_name(self.company))
                      and f.currency == currency]
        self.report_date = report_date
        self.label = label
        self.future_period_facts = [fact for fact in self.facts if label == 'disclosed' and fact.publish_date
                                   and period_end(fact.period) and period_end(fact.period) > fact.publish_date]
        self.facts = [fact for fact in self.facts if fact not in self.future_period_facts]
        self.store = FactStore(self.facts)
        self.index: dict[tuple, list[Fact]] = defaultdict(list)
        for fact in self.facts:
            self.index[(fact.account, fact.period, fact.scope, fact.dimension_kind, fact.source_relation)].append(fact)
        self.dims: dict[str, set[str]] = defaultdict(set)
        for fact in self.facts:
            if fact.dimension_kind != 'total':
                self.dims[fact.dimension_kind].add(fact.dimension_id)

    def _rank(self, fact: Fact) -> tuple:
        visible = 1 if (not self.report_date or not fact.publish_date or fact.publish_date <= self.report_date) else 0
        version = 2 if '调整后' in fact.version or '更正' in fact.version else 0 if '调整前' in fact.version else 1
        return (visible, fact.publish_date or '', version, _TYPE_RANK.get(fact.source_type, 2), -len(fact.fact_id))

    def get(self, account: str, period: str, scope: str = '合并', dim: tuple = ('total', ''), relation: str = 'value', definition_id: str = '') -> list[Fact]:
        rows = self.index.get((account, period, scope, dim[0], relation), [])
        if dim[0] != 'total':
            rows = [fact for fact in rows if fact.dimension_id == dim[1]]
        rows = [f for f in rows if f.definition_id == definition_id]
        if not rows:
            return []
        qualifiers = {f.nr for f in rows}
        if len(qualifiers) > 1:
            raise Missing(account, period, '同一科目的扣非身份不一致', 'X4')
        nr = next(iter(qualifiers))
        def admitted(store):
            outcome = store.query_outcome(self.company, account, period, scope, nr,
                cutoff=self.report_date or None, min_confidence='0.95', dimension_kind=dim[0],
                dimension_id=dim[1], currency=self.currency, definition_id=definition_id, source_relation=relation)
            if outcome.status not in ('found', 'missing'):
                code = 'X5' if outcome.status == 'unknown_date' else 'X4' if outcome.status in ('conflict', 'low_confidence', 'invalid_interval', 'ambiguous_source') else 'X1'
                raise Missing(account, period, outcome.reason, code)
            return outcome.fact
        primary = admitted(self.store)
        if primary is None:
            return []
        distinct = [primary]
        # Earlier versions are independently admitted; a conflicting current
        # version cannot be bypassed by a convenient older value.
        groups = sorted({(f.publish_date, f.version) for f in rows}, reverse=True)
        for published, version in groups:
            if published == primary.publish_date and version == primary.version:
                continue
            if self.report_date and (not published or published > self.report_date):
                continue
            if published == primary.publish_date and self._rank(next(f for f in rows if f.publish_date == published and f.version == version)) >= self._rank(primary):
                continue
            try:
                older = admitted(FactStore([f for f in rows if f.publish_date == published and f.version == version]))
            except Missing:
                # A rejected old version is unavailable; it cannot invalidate
                # the independently admitted current version or rescue it.
                continue
            if older and not any(fact_iv(older).intersects(fact_iv(existing)) for existing in distinct):
                distinct.append(older)
        return distinct

    def periods(self, account: str, scope: str = '合并') -> list[str]:
        return sorted({key[1] for key in self.index if key[0] == account and key[2] == scope and key[3] == 'total' and key[4] == 'value'})

    def match_dim(self, name: str) -> tuple | None:
        from ..dimensions import normalize_dimension_label
        name = normalize_dimension_label(name)
        matches = [(kind, label) for kind, labels in self.dims.items() for label in labels
                   if name and normalize_dimension_label(label) == name]
        return matches[0] if len(matches) == 1 else None

    def dimension_alias_candidates(self, name: str) -> list[str]:
        """Nearby names explain a missing source mapping; they cannot select operands."""
        name = compact(name)
        return sorted({label for labels in self.dims.values() for label in labels
                       if len(name) >= 2 and (name in compact(label) or compact(label) in name)})


_FALLBACK = {'REVENUE': ['TOTAL_REVENUE'], 'TOTAL_REVENUE': ['REVENUE'], 'ASSETS': ['LIABILITIES_EQUITY'], 'TOTAL_SHARES': [],
             'LIABILITIES_EQUITY': ['ASSETS'], 'CASH_FUNDS': [], 'INCOME_TAX_EXPENSE': [], 'EXP_SELLING': []}


class Ctx:
    """单条陈述的取数上下文：记录用到的事实、缺失项与是否动用了研报内部数据。"""

    def __init__(self, bases: list[FactBase], period: str, *, scope: str = '合并', dim: tuple = ('total', ''), market: dict | None = None,
                 share_bases: list[FactBase] | None = None, pick: int = 0, definition_id: str = '', root_metric: str = ''):
        self.bases, self.period, self.scope, self.dim = bases, period, scope, dim
        self.market = market or {}
        self.share_bases = share_bases or bases
        self.used: list[Fact] = []
        self.internal = False
        self.conflict = False
        self.depth = 0
        self.pick = pick
        self.definition_id, self.root_metric = definition_id, root_metric
        self.derived_notes: list[str] = []

    def _lookup(self, account: str, period: str, dim: tuple | None = None):
        for base in self.bases:
            for name in [account] + _FALLBACK.get(account, []):
                rows = base.get(name, period, self.scope, dim or self.dim,
                                definition_id=self.definition_id if account == self.root_metric else '')
                if rows:
                    if len(rows) > 1 and rows[0].source_doc == rows[1].source_doc and rows[0].version == rows[1].version:
                        self.conflict = True
                    fact = rows[min(self.pick, len(rows) - 1)] if self.pick else rows[0]
                    if base.label != 'disclosed':
                        self.internal = True
                    return fact
        return None

    def __call__(self, account: str, off: int = 0, *, total: bool = False) -> IV:
        period = shift_year(self.period, off) if off else self.period
        fact = self._lookup(account, period, ('total', '') if total else None)
        if fact is not None:
            self.used.append(fact)
            return fact_iv(fact)
        derived = self._derive_quarter(account, period) if (self.dim[0] == 'total' or total) else None
        if derived is not None:
            return derived
        if self.dim[0] != 'total' and not total and account in ('REVENUE', 'COST'):
            later = shift_year(period, 1)
            current = self._lookup(account, later)
            growth = None
            for base in self.bases:
                rows = base.get(account, later, self.scope, self.dim, 'yoy')
                if rows:
                    growth = rows[0]
                    break
            if current is not None and growth is not None:
                self.used += [current, growth]
                self.derived_notes.append(f'{period_label(period)}{self.dim[1]}{account_name(account)}由{period_label(later)}金额与披露同比增速推算')
                return fact_iv(current) / (IV(1) + fact_iv(growth))
        definitions = DEFS.get(account)
        if definitions and self.depth < 3 and (self.dim[0] == 'total' or total):
            self.depth += 1
            try:
                saved, self.period = self.period, period
                errors = []
                for def_id, _, fn in definitions:
                    if '=' in def_id:
                        continue                 # “净利润≈归母净利润”这类用语备选只在陈述层面比较，公式内部不替换
                    try:
                        return fn(self)
                    except Missing as exc:
                        errors.append(exc)
                raise errors[0] if errors else Missing(account, period)
            finally:
                self.period = saved
                self.depth -= 1
        raise Missing(account, period)

    _QUARTER_RULES = {
        'Q4': [('Y', '9M', -1)], 'Q3': [('9M', 'H1', -1)], 'Q2': [('H1', 'Q1', -1)],
        'H1': [('Q1', 'Q2', 1)], '9M': [('H1', 'Q3', 1)],
    }

    def _derive_quarter(self, account: str, period: str):
        """流量科目的单季/累计互推：Q4=全年-前三季度，Q3=前三季度-上半年，Q2=上半年-一季度，H1=Q1+Q2，9M=H1+Q3。"""
        from .taxonomy import FLOW_ACCOUNTS
        kind, year = period[4:], period[:4]
        if account not in FLOW_ACCOUNTS or kind not in self._QUARTER_RULES or self.depth > 2:
            return None
        for left, right, sign in self._QUARTER_RULES[kind]:
            a = self._lookup(account, year + left)
            b = self._lookup(account, year + right)
            if a is None or b is None:
                continue
            self.used += [a, b]
            self.derived_notes.append(f'{period_label(period)}{account_name(account)}由{period_label(year + left)}与{period_label(year + right)}{"相减" if sign < 0 else "相加"}得到')
            return fact_iv(a) + fact_iv(b) * sign
        return None

    def avg(self, account: str) -> IV:
        return (self(account) + self(account, -1)) / 2

    def delta(self, account: str) -> IV:
        return self(account) - self(account, -1)

    def shares(self) -> IV:
        """截至研报日最新披露股数；同期间优先总股本，再取股本。"""
        for base in self.share_bases:
            candidates = [(period_end(period), account == 'TOTAL_SHARES', account, period)
                          for account in ('TOTAL_SHARES', 'SHARE_CAPITAL')
                          for period in base.periods(account, self.scope) if period_end(period)]
            if candidates:
                _, _, account, period = max(candidates)
                rows = base.get(account, period, self.scope)
                if rows:
                    fact = rows[0]
                    if base.label != 'disclosed':
                        self.internal = True
                    self.used.append(fact)
                    return fact_iv(fact)
            if base.label == 'disclosed' and 'TOTAL_SHARES' in self.market:
                break
        if 'TOTAL_SHARES' in self.market:
            self.internal = True
            return self.market['TOTAL_SHARES']
        raise Missing('TOTAL_SHARES', self.period)

    def mkt(self, key: str) -> IV:
        if key not in self.market:
            raise Missing(key, self.period, '研报未给出行情数据')
        self.internal = True
        return self.market[key]


def _da(c: Ctx) -> IV:
    total = c('CF_BRIDGE_DEPRECIATION') + c('CF_BRIDGE_INTANGIBLE_AMORTIZATION') + c('CF_BRIDGE_PREPAID_AMORTIZATION')
    try:
        total = total + c('CF_BRIDGE_ROU_DEPRECIATION')
    except Missing:
        pass
    return total


def _da_ex_rou(c: Ctx) -> IV:
    return c('CF_BRIDGE_DEPRECIATION') + c('CF_BRIDGE_INTANGIBLE_AMORTIZATION') + c('CF_BRIDGE_PREPAID_AMORTIZATION')


def _debt(c: Ctx, lease: bool) -> IV:
    total = IV(0)
    for account in ('SHORT_TERM_BORROWING', 'LONG_TERM_BORROWING', 'CURRENT_PORTION_NONCURRENT_LIABILITIES', 'BONDS_PAYABLE') + (('LEASE_LIABILITIES',) if lease else ()):
        try:
            total = total + c(account)
        except Missing:
            if account in ('SHORT_TERM_BORROWING',):
                raise
    return total


# 指标 → [(定义ID, 说明, 计算函数)]；第一项为首选定义
DEFS: dict[str, list[tuple]] = {
    'NP': [('NP', '净利润（含少数股东损益）', lambda c: c('NP')), ('NP=NP_PARENT', '归属于母公司股东的净利润', lambda c: c('NP_PARENT'))],
    'GROSS_PROFIT': [('GP', '营业收入-营业成本', lambda c: c('REVENUE') - c('COST'))],
    'GROSS_MARGIN': [('GM', '(营业收入-营业成本)/营业收入', lambda c: (c('REVENUE') - c('COST')) / c('REVENUE')),
                     ('GM.disclosed', '报告披露的毛利率', lambda c: c._direct('GROSS_MARGIN'))],
    'NET_MARGIN': [('NM', '净利润/营业收入', lambda c: c('NP') / c('REVENUE')), ('NM.parent', '归母净利润/营业收入', lambda c: c('NP_PARENT') / c('REVENUE'))],
    'NP_PARENT_MARGIN': [('NPM', '归母净利润/营业收入', lambda c: c('NP_PARENT') / c('REVENUE'))],
    'OPERATING_MARGIN': [('OM', '营业利润/营业收入', lambda c: c('OPERATING_PROFIT') / c('REVENUE'))],
    'ROE': [('ROE.weighted', '加权平均净资产收益率（报告披露）', lambda c: c._direct('ROE')),
            ('ROE.diluted', '归母净利润/期末归母净资产（摊薄）', lambda c: c('NP_PARENT') / c('EQUITY_PARENT')),
            ('ROE.average', '归母净利润/期初期末平均归母净资产', lambda c: c('NP_PARENT') / c.avg('EQUITY_PARENT')),
            ('ROE.total', '净利润/期末所有者权益', lambda c: c('NP') / c('EQUITY'))],
    'ROE_WEIGHTED': [('ROE.weighted', '加权平均净资产收益率（报告披露）', lambda c: c._direct('ROE'))],
    'ROA': [('ROA.avg', '净利润/平均总资产', lambda c: c('NP') / c.avg('ASSETS')), ('ROA.end', '净利润/期末总资产', lambda c: c('NP') / c('ASSETS'))],
    'DEBT_RATIO': [('DR', '负债合计/资产总计', lambda c: c('LIABILITIES') / c('ASSETS')), ('DR.disclosed', '报告披露的资产负债率', lambda c: c._direct('DEBT_RATIO'))],
    'CURRENT_RATIO': [('CR', '流动资产/流动负债', lambda c: c('CURRENT_ASSETS') / c('CURRENT_LIABILITIES')), ('CR.disclosed', '报告披露的流动比率', lambda c: c._direct('CURRENT_RATIO'))],
    'QUICK_RATIO': [('QR', '(流动资产-存货)/流动负债', lambda c: (c('CURRENT_ASSETS') - c('INVENTORY')) / c('CURRENT_LIABILITIES')),
                    ('QR.prepay', '(流动资产-存货-预付款项)/流动负债', lambda c: (c('CURRENT_ASSETS') - c('INVENTORY') - c('PREPAYMENTS')) / c('CURRENT_LIABILITIES')),
                    ('QR.liquid', '(现金+应收票据及应收账款+其他应收款)/流动负债', lambda c: (c('CASH') + c('AR_NOTES') + c('OTHER_RECEIVABLES')) / c('CURRENT_LIABILITIES'))],
    'SELLING_RATIO': [('SR', '销售费用/营业收入', lambda c: c('EXP_SELLING') / c('REVENUE'))],
    'ADMIN_RATIO': [('AR', '管理费用/营业收入', lambda c: c('EXP_ADMIN') / c('REVENUE'))],
    'RD_RATIO': [('RR', '研发费用/营业收入', lambda c: c('EXP_RD') / c('REVENUE')), ('RR.invest', '研发投入/营业收入', lambda c: c('RD_INVESTMENT') / c('REVENUE'))],
    'FINANCE_RATIO': [('FR', '财务费用/营业收入', lambda c: c('EXP_FINANCE') / c('REVENUE'))],
    'PERIOD_EXPENSE_RATIO': [('PER4', '(销售+管理+研发+财务费用)/营业收入', lambda c: (c('EXP_SELLING') + c('EXP_ADMIN') + c('EXP_RD') + c('EXP_FINANCE')) / c('REVENUE')),
                             ('PER3', '(销售+管理+财务费用)/营业收入', lambda c: (c('EXP_SELLING') + c('EXP_ADMIN') + c('EXP_FINANCE')) / c('REVENUE'))],
    'EPS': [('EPS.basic', '基本每股收益（报告披露）', lambda c: c._direct('EPS')), ('EPS.diluted', '稀释每股收益（报告披露）', lambda c: c._direct('EPS_DILUTED')),
            ('EPS.latest', '归母净利润/最新总股本（最新摊薄）', lambda c: c('NP_PARENT') / c.shares())],
    'EPS_BASIC': [('EPS.basic', '基本每股收益（报告披露）', lambda c: c._direct('EPS'))],
    'EPS_LATEST': [('EPS.latest', '归母净利润/最新总股本（最新摊薄）', lambda c: c('NP_PARENT') / c.shares()), ('EPS.basic', '基本每股收益（报告披露）', lambda c: c._direct('EPS'))],
    'BPS': [('BPS', '归母净资产/最新总股本', lambda c: c('EQUITY_PARENT') / c.shares()), ('BPS.disclosed', '报告披露的每股净资产', lambda c: c._direct('BPS'))],
    'CFO_PER_SHARE': [('CFOPS', '经营活动现金流量净额/最新总股本', lambda c: c('CFO') / c.shares())],
    'ASSET_TURNOVER': [('AT.avg', '营业收入/平均总资产', lambda c: c('REVENUE') / c.avg('ASSETS')), ('AT.end', '营业收入/期末总资产', lambda c: c('REVENUE') / c('ASSETS'))],
    'AR_TURNOVER': [('ART.avg', '营业收入/平均应收账款', lambda c: c('REVENUE') / c.avg('AR')),
                    ('ART.notes', '营业收入/平均应收票据及应收账款', lambda c: c('REVENUE') / c.avg('AR_NOTES')),
                    ('ART.end', '营业收入/期末应收账款', lambda c: c('REVENUE') / c('AR')),
                    ('ART.endnotes', '营业收入/期末应收票据及应收账款', lambda c: c('REVENUE') / c('AR_NOTES')),
                    ('ART.disclosed', '报告披露的应收账款周转率', lambda c: c._direct('AR_TURNOVER'))],
    'AP_TURNOVER': [('APT.avg', '营业成本/平均应付账款', lambda c: c('COST') / c.avg('AP')),
                    ('APT.notes', '营业成本/平均应付票据及应付账款', lambda c: c('COST') / c.avg('AP_NOTES')),
                    ('APT.end', '营业成本/期末应付账款', lambda c: c('COST') / c('AP')),
                    ('APT.endnotes', '营业成本/期末应付票据及应付账款', lambda c: c('COST') / c('AP_NOTES'))],
    'INVENTORY_TURNOVER': [('IT.avg', '营业成本/平均存货', lambda c: c('COST') / c.avg('INVENTORY')), ('IT.end', '营业成本/期末存货', lambda c: c('COST') / c('INVENTORY')),
                           ('IT.disclosed', '报告披露的存货周转率', lambda c: c._direct('INVENTORY_TURNOVER'))],
    'AR_NOTES': [('ARN', '应收票据+应收账款', lambda c: c('AR') + c('NOTES_RECEIVABLE')),
                 ('ARN.financing', '应收票据+应收账款+应收款项融资', lambda c: c('AR') + c('NOTES_RECEIVABLE') + c('RECEIVABLES_FINANCING')),
                 ('ARN.ar', '应收账款', lambda c: c('AR'))],
    'AP_NOTES': [('APN', '应付票据+应付账款', lambda c: c('AP') + c('NOTES_PAYABLE')), ('APN.ap', '应付账款', lambda c: c('AP'))],
    'CASH': [('CASH', '货币资金', lambda c: c('CASH_FUNDS')), ('CASH.trading', '货币资金+交易性金融资产', lambda c: c('CASH_FUNDS') + c('TRADING_FIN_ASSETS')),
             ('CASH.eq', '期末现金及现金等价物余额', lambda c: c('CASH_EQUIVALENTS'))],
    'RETAINED': [('RE', '盈余公积+未分配利润', lambda c: c('SURPLUS_RESERVE') + c('RETAINED_UNDISTRIBUTED_PROFIT')),
                 ('RE.residual', '归母权益-股本-资本公积', lambda c: c('EQUITY_PARENT') - c('SHARE_CAPITAL') - c('CAPITAL_RESERVE')),
                 ('RE.undistributed', '未分配利润', lambda c: c('RETAINED_UNDISTRIBUTED_PROFIT'))],
    'LONG_TERM_INVEST': [('LTI', '长期股权投资', lambda c: c('LONG_TERM_EQUITY_INVESTMENTS'))],
    'IMPAIRMENT': [('IMP', '资产减值损失', lambda c: c('ASSET_IMPAIRMENT')), ('IMP.all', '资产减值损失+信用减值损失', lambda c: c('ASSET_IMPAIRMENT') + c('CREDIT_IMPAIRMENT')),
                   ('IMP.neg', '-(资产减值损失+信用减值损失)', lambda c: -(c('ASSET_IMPAIRMENT') + c('CREDIT_IMPAIRMENT'))), ('IMP.negone', '-资产减值损失', lambda c: -c('ASSET_IMPAIRMENT'))],
    'NRI': [('NRI', '归母净利润-扣非归母净利润', lambda c: c('NP_PARENT') - c('NP_PARENT_ADJ')), ('NRI.disclosed', '非经常性损益净额（报告披露）', lambda c: c._direct('NONRECURRING_NET'))],
    'CAPEX': [('CAPEX', '购建固定资产、无形资产和其他长期资产支付的现金', lambda c: c('CF_CAPEX'))],
    'DA': [('DA.all', '固定资产折旧+使用权资产折旧+无形资产摊销+长期待摊费用摊销', _da),
           ('DA.ex_rou', '固定资产折旧+无形资产摊销+长期待摊费用摊销（不含使用权资产折旧）', _da_ex_rou)],
    'EBITDA': [('EBITDA.pt', '利润总额+财务费用+折旧摊销', lambda c: c('PROFIT_TOTAL') + c('EXP_FINANCE') + _da(c)),
               ('EBITDA.op', '营业利润+财务费用+折旧摊销', lambda c: c('OPERATING_PROFIT') + c('EXP_FINANCE') + _da(c)),
               ('EBITDA.op_da', '营业利润+折旧摊销（不含使用权资产折旧）', lambda c: c('OPERATING_PROFIT') + _da_ex_rou(c)),
               ('EBITDA.pt_da', '利润总额+折旧摊销（不含使用权资产折旧）', lambda c: c('PROFIT_TOTAL') + _da_ex_rou(c)),
               ('EBITDA.op_fin_ex', '营业利润+财务费用+折旧摊销（不含使用权资产折旧）', lambda c: c('OPERATING_PROFIT') + c('EXP_FINANCE') + _da_ex_rou(c))],
    'NET_DEBT_RATIO': [('ND.lease', '(短期借款+长期借款+一年内到期的非流动负债+租赁负债-货币资金)/所有者权益合计',
                        lambda c: (_debt(c, lease=True) - c('CASH_FUNDS')) / c('EQUITY')),
                       ('ND.plain', '(短期借款+长期借款+一年内到期的非流动负债-货币资金)/所有者权益合计',
                        lambda c: (_debt(c, lease=False) - c('CASH_FUNDS')) / c('EQUITY')),
                       ('ND.parent', '(短期借款+长期借款+一年内到期的非流动负债+租赁负债-货币资金)/归母权益',
                        lambda c: (_debt(c, lease=True) - c('CASH_FUNDS')) / c('EQUITY_PARENT'))],
    'EBIT': [('EBIT', '利润总额+财务费用', lambda c: c('PROFIT_TOTAL') + c('EXP_FINANCE'))],
    'DELTA_SHORT_BORROWING': [('dSTB', '短期借款期末-期初', lambda c: c.delta('SHORT_TERM_BORROWING'))],
    'DELTA_LONG_BORROWING': [('dLTB', '长期借款期末-期初', lambda c: c.delta('LONG_TERM_BORROWING'))],
    'DELTA_SHARE_CAPITAL': [('dSC', '股本期末-期初', lambda c: c.delta('SHARE_CAPITAL'))],
    'DELTA_CAPITAL_RESERVE': [('dCR', '资本公积期末-期初', lambda c: c.delta('CAPITAL_RESERVE'))],
    'EQUITY': [('EQ', '所有者权益合计', lambda c: c._direct('EQUITY')), ('EQ.sum', '归母权益+少数股东权益', lambda c: c('EQUITY_PARENT') + c('EQUITY_MINORITY'))],
    'TOTAL_SHARES': [('SH', '总股本', lambda c: c._direct('TOTAL_SHARES')), ('SH.capital', '股本（每股面值1元）', lambda c: c('SHARE_CAPITAL'))],
    'PE': [('PE.cap', '总市值/归母净利润', lambda c: c.mkt('MARKET_CAP') / c('NP_PARENT')), ('PE.price', '股价/每股收益', lambda c: c.mkt('PRICE') / c('EPS_LATEST'))],
    'PB': [('PB.cap', '总市值/归母净资产', lambda c: c.mkt('MARKET_CAP') / c('EQUITY_PARENT'))],
    'PS': [('PS.cap', '总市值/营业收入', lambda c: c.mkt('MARKET_CAP') / c('REVENUE'))],
    'CASH_CONTENT': [('CC', '经营活动现金流量净额/净利润', lambda c: c('CFO') / c('NP')), ('CC.parent', '经营活动现金流量净额/归母净利润', lambda c: c('CFO') / c('NP_PARENT'))],
    'NRI_SHARE': [('NRI.share', '(归母净利润-扣非归母净利润)/|归母净利润|', lambda c: (c('NP_PARENT') - c('NP_PARENT_ADJ')) / c('NP_PARENT').abs())],
    'MARKET_CAP': [('MC', '股价×总股本', lambda c: c.mkt('PRICE') * c.shares())],
}
CLOSED_OVERRIDE = {'DELTA_SHORT_BORROWING', 'DELTA_LONG_BORROWING', 'DELTA_SHARE_CAPITAL', 'DELTA_CAPITAL_RESERVE', 'RETAINED', 'CASH'}


def _direct(self: Ctx, account: str) -> IV:
    period = self.period
    fact = self._lookup(account, period)
    if fact is None:
        raise Missing(account, period)
    self.used.append(fact)
    return fact_iv(fact)


Ctx._direct = _direct


def definitions_for(metric: str) -> list[tuple]:
    return DEFS.get(metric) or [(metric, account_name(metric), lambda c, m=metric: c._direct(m) if False else c(m))]


def relative_change(current: IV, base: IV) -> IV:
    """Registered change convention, preserving disclosed precision."""
    if base.lo <= 0 <= base.hi:
        raise ZeroDivisionError('基数为零或跨零')
    return (current - base) / base.abs()


def point_change(current: IV, base: IV) -> IV:
    return current - base


@lru_cache(maxsize=1)
def registered_definitions() -> list[dict]:
    """Read-only registry metadata; executable formulas remain in one place."""
    from .taxonomy import ALIAS_MAP, FILING_LABELS
    root = Path(__file__).resolve().parents[1]
    contract = hashlib.sha256(b''.join((root / name).read_bytes() for name in
        ('smart/verify.py', 'smart/analysis.py', 'smart/common.py', 'facts.py', 'decimal_math.py', 'thresholds.py', 'root_math.py'))).hexdigest()
    accounts = set(DEFS) | set(ALIAS_MAP.values()) | set(FILING_LABELS.values())
    rows = []
    for account in sorted(accounts - {'', '__PREV__', 'RESIDUAL', 'TEMPLATE_ITEM'}):
        for definition_id, label, function in definitions_for(account):
            source = inspect.getsource(function)
            rows.append({'account': account, 'definition_id': definition_id, 'label': label, 'formula': label,
                         'rule_id': f'SMART:{account}:{definition_id}',
                         'dependencies': sorted(set(re.findall(r"c\(['\"]([A-Z_]+)['\"]", source))),
                         'function_source_hash': hashlib.sha256(source.encode('utf-8')).hexdigest(),
                         'contract_hash': contract, 'execution_entry': 'mingjing.smart.verify.verify_model'})
    for definition_id, label, function in (
            ('change.abs', '(本期-基期)/|基期|', relative_change),
            ('change.points', '本期比率-基期比率', point_change),
            ('quarter.derived', '按登记的单季/累计期间相加减', Ctx._derive_quarter)):
        source = inspect.getsource(function)
        rows.append({'account': 'PERIOD_CHANGE', 'definition_id': definition_id, 'label': label, 'formula': label,
                     'rule_id': f'SMART:PERIOD_CHANGE:{definition_id}', 'dependencies': ['current', 'base'],
                     'function_source_hash': hashlib.sha256(source.encode('utf-8')).hexdigest(), 'contract_hash': contract,
                     'execution_entry': f'mingjing.smart.verify.{function.__qualname__}'})
    for account, definition_id, label, function in (
            ('SOURCE_QUOTATION', 'disclosed.yoy', '按明确同比列及披露精度读取', fact_iv),
            ('PERIOD_CHANGE', 'change.cagr', '按登记认证算法计算CAGR', _cagr),
            ('SOURCE_THRESHOLD', 'threshold.entailment', '来源区间整体蕴含阈值或与阈值互斥', Verifier._threshold),
            ('TEMPLATE_IDENTITY', 'identity.template', '模板残差项按独立小计及其他明细反推', Verifier._identity_checks)):
        source = inspect.getsource(function)
        rows.append({'account': account, 'definition_id': definition_id, 'label': label, 'formula': label,
                     'rule_id': f'SMART:{account}:{definition_id}', 'dependencies': [],
                     'function_source_hash': hashlib.sha256(source.encode('utf-8')).hexdigest(), 'contract_hash': contract,
                     'execution_entry': f'mingjing.smart.verify.{function.__qualname__}'})
    return rows


def _is_open(metric: str) -> bool:
    return metric in OPEN_METRICS or metric in CLOSED_OVERRIDE or metric in VALUATION_METRICS


@dataclass
class Result:
    cid: str
    status: str = 'unverifiable'        # verified / violated / unverifiable / skipped
    basis: str = ''                     # disclosed / derived / internal / text / identity
    error_type: str = ''
    abstain_code: str = ''
    reason: str = ''
    expected: str = ''
    expected_display: str = ''
    definition: str = ''
    definition_label: str = ''
    formula: str = ''
    operands: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    advisories: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    candidates: list = field(default_factory=list)
    missing: list = field(default_factory=list)
    correction: dict = field(default_factory=dict)
    diagnosis: dict = field(default_factory=dict)
    internal_check: str = ''
    confidence: float = 1.0
    assumptions: list = field(default_factory=list)
    rule_ids: list = field(default_factory=list)
    definition_source_hash: str = ''
    contract_hash: str = ''
    rules: list = field(default_factory=list)
    candidate_interval: dict = field(default_factory=dict)
    expected_interval: dict = field(default_factory=dict)
    candidate_precision: dict = field(default_factory=dict)
    source_precision: list = field(default_factory=list)
    verification_basis: str = ''
    precision_note: str = ''
    establishes_exact_value: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def assume(result: 'Result', code: str, text: str) -> None:
    """登记一条推定（同码同文只记一次）。"""
    if not any(a['code'] == code and a['text'] == text for a in result.assumptions):
        result.assumptions.append({'code': code, 'label': ASSUMPTION_LABELS.get(code, code), 'text': text})


def _operand(fact: Fact) -> dict:
    return {'fact_id': fact.fact_id, 'account': fact.account, 'name': account_name(fact.account), 'period': fact.period, 'scope': fact.scope,
            'value': str(fact.value), 'unit': fact.display_unit, 'decimals': fact.decimals, 'version': fact.version,
            'source_type': fact.source_type, 'table': fact.table_label, 'label': fact.label, 'evidence': list(fact.evidence),
            'source_doc': fact.source_doc, 'publish_date': fact.publish_date, 'currency': fact.currency,
            'definition_id': fact.definition_id, 'nr': fact.nr, 'parse_conf': str(fact.parse_conf),
            'interval': _interval_dict(fact_iv(fact)), 'quantum': str(quantum(fact.display_unit, fact.decimals)),
            'dimension': fact.dimension_id if fact.dimension_kind != 'total' else '', 'relation': fact.source_relation}


def _interval_dict(interval: IV) -> dict:
    return {'lower': str(interval.lo), 'upper': str(interval.hi),
            'lower_closed': interval.lower_closed, 'upper_closed': interval.upper_closed}


def _cagr(end: IV, start: IV, years: int) -> IV:
    if start.lo <= 0 or end.lo <= 0 or years <= 0:
        raise ZeroDivisionError('CAGR 首尾值须为正')
    result = cagr_interval(end.bounds, start.bounds, IV(years).bounds)
    if result is None:
        raise ZeroDivisionError('CAGR 前提不成立或超出已登记算法范围')
    return IV(result)


def _display(value: Decimal, claim: Claim) -> str:
    unit = claim.unit or ''
    if unit in PERCENT_UNITS:
        return fmt_amount(value, unit, max(claim.decimals, 1 if unit == '%' else 2))
    return fmt_amount(value, unit, claim.decimals if claim.decimals else (2 if unit in ('元', '倍', 'x', '') and abs(value) < 100 else 0))


class Verifier:
    def __init__(self, facts: list[Fact], model: ReportModel, *, strict: bool = False, texts: list | None = None, hedge: str = '0.02',
                 drift: str = '0.03', doc_index: dict | None = None):
        self.all_facts = list(facts)
        self.doc_index = doc_index or {}            # source_doc(sha12) → {doc_type, period, name}
        self._cited: dict[str, 'Verifier'] = {}
        self.model = model
        self.meta = model.meta
        self.strict = strict
        self.fb = FactBase([f for f in facts if not (strict and '横线视为零' in (f.label or ''))], model.meta.report_date or '', 'disclosed',
                           company=model.meta.company, source_metadata=self.doc_index, subject=model.meta)
        self.tb = FactBase(self._table_facts(), '', 'report_table', company=model.meta.company)
        self.market = {key: IV.rounded(Decimal(item['value']), quantum(item['unit'], item['decimals'])) for key, item in model.market.items()}
        self.texts = texts or []
        self.hedge = Decimal(hedge)
        self.drift = Decimal(drift)
        self._text_numbers: dict[int, list] = {}
        self._text_claim_cache: dict[tuple, list] = {}

    # —— 研报自身表格 → 内部事实库（仅用于内部勾稽） ——
    def _table_facts(self) -> list[Fact]:
        facts, seen = [], set()
        company = self.model.meta.company or ''
        for table in self.model.tables:
            for row in table.rows:
                metric = row['metric']
                if not metric or row['relation'] != 'value' or metric in ('RESIDUAL', 'TEMPLATE_ITEM'):
                    continue
                unit = row['unit'] or ('%' if metric in RATIO_METRICS else '元' if metric in ('EPS', 'EPS_LATEST', 'BPS', 'CFO_PER_SHARE') else
                                       '倍' if metric in PLAIN_METRICS else table.unit or '百万元')
                for (period, _), (raw, number) in zip(table.years, row['cells']):
                    cell_identity = (table.name, table.page, row['label'], metric, period, table.scope)
                    if number is None or cell_identity in seen:
                        continue
                    seen.add(cell_identity)
                    value = number * unit_scale(unit)
                    facts.append(Fact(f'report-{metric}-{period}-{len(facts)}', company, metric, period, value=value,
                                      decimals=len(norm(raw).rstrip('%').replace(',', '').partition('.')[2]), display_unit=unit,
                                      source_type='report_table', table_label=table.name, label=row['label'], source_doc='report',
                                      scope=table.scope or '合并',
                                      evidence=(f"{self.model.stats['doc_sha256'][:12]}:p{row.get('page', table.page)}:table",) if self.model.stats.get('doc_sha256') else ()))
        return facts

    # —— 主入口 ——
    def verify_all(self) -> list[Result]:
        results = [self.verify(claim) for claim in self.model.claims]
        self._identity_checks(results)
        for index, claim in enumerate(self.model.claims):
            if claim.cite and self.doc_index:
                results[index] = self._check_citation(claim, results[index])
            self._precision(claim, results[index])
        return results

    def _doc_label(self, sha12: str) -> str:
        info = self.doc_index.get(sha12) or {}
        names = {'annual_report': '年度报告', 'semiannual_report': '半年度报告', 'quarterly_report': '季度报告', 'prospectus': '招股说明书'}
        period = info.get('period', '')
        kind = info.get('doc_type', '')
        if kind == 'quarterly_report':
            title = {'Q1': '第一季度报告', '9M': '第三季度报告'}.get(period[4:], '季度报告')
        else:
            title = names.get(kind, info.get('name', ''))
        return f'《{period[:4]}年{title}》' if period else f'《{info.get("name", sha12)}》'

    def _check_citation(self, claim: Claim, result: 'Result') -> 'Result':
        """句中明示出处时，只用所引报告的事实复核；数据确实来自其他报告时判 E6 引用错误。"""
        doc_type, period = claim.cite.split(':', 1)
        cited = {sha for sha, info in self.doc_index.items() if info.get('doc_type') == doc_type and info.get('period') == period}
        if not cited:
            result.notes.append(f'所引《{claim.cite_text}》未入库，已按全部材料核查')
            return result
        if claim.cite not in self._cited:
            restricted = [f for f in self.all_facts if f.source_doc in cited]
            self._cited[claim.cite] = Verifier(restricted, self.model, strict=self.strict,
                                            doc_index={sha: self.doc_index[sha] for sha in cited})
        inside = self._cited[claim.cite].verify(claim)
        if inside.status == 'verified':
            if result.status != 'verified':
                inside.notes.append(f'与所引《{claim.cite_text}》一致；该数据此后有更新披露，最新值为 {result.expected_display or "—"}')
                inside.advisories.append('A1')
                return inside
            inside.notes.append(f'与所引《{claim.cite_text}》一致')
            return inside
        if inside.abstain_code in ('X4', 'X5'):
            inside.notes.append(f'所引《{claim.cite_text}》未通过事实准入；不以其他报告解除此缺口')
            return inside
        if result.status == 'verified':
            sources = sorted({o['source_doc'] for o in result.operands if o.get('source_doc') not in cited})
            right = '、'.join(self._doc_label(sha) for sha in sources) or '其他已入库报告'
            reason = (f'所引《{claim.cite_text}》中没有这一数据' if inside.abstain_code in ('X1', 'X3') or not inside.expected
                      else f'所引《{claim.cite_text}》的对应值为 {inside.expected_display}')
            fixed = Result(claim.cid, status='violated', basis='disclosed', error_type='E6', confidence=claim.confidence)
            for key in ('expected', 'expected_display', 'definition', 'definition_label', 'formula', 'operands', 'evidence', 'candidates',
                        'rule_ids', 'rules', 'definition_source_hash', 'contract_hash', 'expected_interval', 'source_precision'):
                setattr(fixed, key, getattr(result, key))
            fixed.diagnosis = {'type': 'E6', 'detail': f'{reason}；数值与{right}一致，属引用出处错误'}
            text = norm(claim.text)
            replacement = text.replace(claim.cite_text, right.strip('《》'), 1) if claim.cite_text in text else text
            fixed.correction = {'correct_value': claim.num_text, 'replacement_text': replacement, 'calculation': result.formula,
                                'source': '、'.join(result.evidence[:4]), 'basis': f'E6 引用错误；数值本身与{right}一致'}
            fixed.reason = f'原文称“据{claim.cite_text}”，{reason}；应改为引用{right}'
            return fixed
        result.notes.append(f'按所引《{claim.cite_text}》复核同样不相容')
        return result

    def _claim_iv(self, claim: Claim) -> IV:
        value = claim.decimal()
        iv = claim.interval()
        if claim.hedge == 'approx':
            iv = iv.widen(self.hedge)
        elif claim.hedge == 'near':
            iv = IV(value - abs(value) * Decimal('0.10'), iv.hi) if value >= 0 else IV(iv.lo, value + abs(value) * Decimal('0.10'))
        return iv

    @staticmethod
    def _threshold(claim: Claim, expected: IV) -> str:
        operator = claim.threshold_operator or ('gt' if claim.hedge == 'above' else 'lt' if claim.hedge == 'below' else '')
        if not operator:
            return ''
        source = ThresholdAssertion(expected.lo, expected.hi, expected.lower_closed, expected.upper_closed)
        assertion = ThresholdAssertion.from_operator(operator, claim.decimal())
        return 'verified' if source.subset_of(assertion) else 'violated' if source.disjoint_from(assertion) else 'unverifiable'

    def _compatible(self, claim: Claim, expected: IV, observed: IV) -> bool:
        verdict = self._threshold(claim, expected)
        return verdict == 'verified' if verdict else expected.intersects(observed)

    def _bases(self, claim: Claim) -> tuple[list[FactBase], str]:
        if claim.forecast:
            return [self.tb, self.fb], 'forecast'
        return [self.fb], 'history'

    def _compute(self, claim: Claim, fn, bases, period: str, dim, *, scope=None, pick=0):
        scope = scope or claim.scope or '合并'
        ctx = Ctx(bases, period, scope=scope, dim=dim, market=self.market, share_bases=[self.fb, self.tb], pick=pick,
                  definition_id=claim.definition_id, root_metric=claim.metric)
        relation = claim.relation
        if relation == 'value':
            return fn(ctx), ctx
        if relation in ('yoy', 'qoq'):
            current = fn(ctx)
            base_period = claim.base_period or (shift_year(period, -1) if relation == 'yoy' else previous_quarter(period))
            if not base_period:
                raise Missing(claim.metric, period, '环比需要单季度期间')
            base_ctx = Ctx(bases if not claim.forecast else [self.tb, self.fb], base_period, scope=scope, dim=dim, market=self.market, share_bases=[self.fb, self.tb], pick=pick,
                           definition_id=claim.definition_id, root_metric=claim.metric)
            if claim.forecast and int(base_period[:4]) <= (self.meta.base_year or 0):
                base_ctx.bases = [self.fb, self.tb]
            base = fn(base_ctx)
            ctx.used += base_ctx.used
            ctx.internal = ctx.internal or base_ctx.internal
            return relative_change(current, base), ctx
        if relation == 'pp':
            current = fn(ctx)
            base_ctx = Ctx(bases, claim.base_period or shift_year(period, -1), scope=scope, dim=dim, market=self.market, share_bases=[self.fb, self.tb],
                           definition_id=claim.definition_id, root_metric=claim.metric)
            base = fn(base_ctx)
            ctx.used += base_ctx.used
            return point_change(current, base), ctx
        if relation == 'cagr':
            if not claim.base_period:
                raise Missing(claim.metric, period, 'CAGR 缺少明确起止期间')
            base_ctx = Ctx(bases, claim.base_period, scope=scope, dim=dim, market=self.market, share_bases=[self.fb, self.tb],
                           definition_id=claim.definition_id, root_metric=claim.metric)
            end, start = fn(ctx), fn(base_ctx)
            ctx.used += base_ctx.used
            return _cagr(end, start, int(period[:4]) - int(claim.base_period[:4])), ctx
        if relation == 'share':
            if dim[0] == 'total':
                raise Missing(claim.metric, period, '占比缺少明确的分子主体（分部/客户/地区）')
            part = fn(ctx)
            denominator_dim = self.fb.match_dim(claim.denominator) if claim.denominator else ('total', '')
            if denominator_dim is None:
                raise Missing(claim.metric, period, '缺少声明占比分母的明细')
            total = Ctx(bases, period, scope=scope, dim=denominator_dim, market=self.market)
            whole = total(claim.metric if claim.metric not in ('GROSS_MARGIN',) else 'REVENUE')
            ctx.used += total.used
            return part / whole, ctx
        raise Missing(claim.metric, period, '关系未登记')

    def verify(self, claim: Claim) -> Result:
        try:
            result = self._verify(claim)
        except Missing as exc:
            result = Result(claim.cid, abstain_code=exc.code, reason=exc.why or f'缺少{account_name(exc.account)}{period_label(exc.period)}',
                            missing=[{'account': exc.account, 'period': exc.period, 'why': exc.why}], confidence=claim.confidence)
        if self.strict and result.status in ('verified', 'violated'):
            blocked = {'scope', 'period', 'subject', 'metric', 'definition', 'dash_zero', 'hedge', 'drift', 'text', 'version'}
            assumptions = [a for a in result.assumptions if a['code'] in blocked]
            if assumptions:
                result.status, result.abstain_code, result.error_type = 'unverifiable', 'X3', ''
                result.reason = '审慎模式不采用推定：' + '、'.join(a['label'] for a in assumptions)
                result.correction = {}
        self._precision(claim, result)
        return result

    def _precision(self, claim: Claim, result: Result) -> None:
        observed = self._claim_iv(claim)
        result.candidate_interval = (ThresholdAssertion.from_operator(claim.threshold_operator, claim.decimal()).to_dict()
                                     if claim.threshold_operator else _interval_dict(observed))
        result.candidate_precision = {'unit': claim.unit, 'decimals': claim.decimals,
                                      'quantum': str(quantum(claim.unit, claim.decimals)),
                                      'rounded_interval': _interval_dict(claim.interval()), 'hedge': claim.hedge}
        if result.operands:
            result.source_precision = [{key: operand.get(key) for key in
                                        ('fact_id', 'claim_id', 'unit', 'decimals', 'quantum', 'interval', 'currency',
                                         'definition_id', 'publish_date', 'source_doc', 'evidence', 'subject_evidence')}
                                       for operand in result.operands]
        if result.status == 'skipped':
            result.verification_basis, result.precision_note = 'not_independently_checked', '输入或模板项未单独核实'
        elif result.status == 'unverifiable':
            result.verification_basis = 'pending'
            result.precision_note = ('来源精度区间跨越阈值，比较关系无法确定' if claim.threshold_operator and result.expected_interval
                                     and result.abstain_code == 'X6' and '阈值' in result.reason
                                     else '现有来源或方法不足，未形成数值核实结论')
        elif result.basis == 'text':
            result.verification_basis, result.precision_note = 'typed_source_quotation_match', '同身份原文佐证；未经公式重算，不证明更细数值'
        elif claim.threshold_operator:
            result.verification_basis = 'source_interval_entails_threshold' if result.status == 'verified' else 'source_interval_disjoint_threshold'
            result.precision_note = '依据来源完整精度区间判断阈值关系；不证明阈值对应的等值陈述'
        else:
            result.verification_basis = ('citation_mismatch' if result.error_type == 'E6' else result.basis
                                         + ('_interval_compatible' if result.status == 'verified' else '_interval_disjoint'))
            result.precision_note = ('引用来源或内部引用不一致；数值精度结论见两侧区间' if result.error_type == 'E6' else
                                    '按双方披露精度区间相容；不独立证明超出来源精度的更细数值' if result.status == 'verified'
                                    else '按双方披露精度区间不相容')
            if result.status == 'verified' and result.expected_interval:
                expected = result.expected_interval
                expected_iv = IV(expected['lower'], expected['upper'], expected['lower_closed'], expected['upper_closed'])
                if (IV(expected_iv.hi) - IV(expected_iv.lo)).mid > (IV(observed.hi) - IV(observed.lo)).mid and 'A5' not in result.advisories:
                    result.advisories.append('A5')
                    result.notes.append('来源区间宽于原文数值精度；仅确认披露精度相容，未独立核实更细数值')

    def _verify(self, claim: Claim) -> Result:
        result = Result(claim.cid, confidence=claim.confidence)
        category = claim.category
        if claim.currency != self.fb.currency:
            result.abstain_code, result.reason = 'X6', '陈述币种与事实库币种不一致'
            return result
        if category == 'template' and not claim.metric:
            result.status, result.reason = 'skipped', '券商模板行，未登记为可核查指标'
            return result
        if category in ('external', 'other_fact') or not claim.metric:
            return self._text_evidence(claim, result, '行业/第三方或非报表数据' if category == 'external' else '非标准报表项')
        if claim.metric in ('RESIDUAL', 'TEMPLATE_ITEM'):
            result.status, result.reason = 'skipped', '模板残差项，由表内勾稽检查'
            return result
        if category == 'market' or claim.metric == 'PRICE':
            result.status, result.reason = 'skipped', '行情数据取自研报自述，作为估值勾稽的输入，不单独判定'
            return result
        if claim.metric == 'TARGET_PRICE':
            result.status, result.reason = 'skipped', '目标价属于观点，不作事实判定'
            return result
        if not claim.period:
            result.abstain_code, result.reason = 'X6', '未能确定陈述对应的期间'
            return result
        if claim.relation == 'pp' and claim.metric not in RATIO_METRICS and claim.unit in ('百分点', 'pct', 'pp', 'bp'):
            result.abstain_code, result.reason = 'X6', '百分点变化未绑定到比率指标'
            return self._text_evidence(claim, result, result.reason) if not claim.forecast else result
        if claim.relation == 'share' and (claim.denominator or any(n.startswith('占比分子为期间片段') for n in claim.notes)):
            numerator = self.fb.match_dim(claim.segment) if claim.segment else None
            denominator = self.fb.match_dim(claim.denominator) if claim.denominator else None
            part_label = next((n.split('：', 1)[1] for n in claim.notes if n.startswith('占比分子')), claim.segment or '分子')
            if not (numerator and (denominator or not claim.denominator)):
                result.abstain_code = 'X1'
                result.reason = f'占比口径为“{part_label}”占“{claim.denominator or "公司营业收入"}”，已入库材料缺少对应明细（如季度分业务收入或业务模式收入）'
                result.missing = [{'account': claim.metric, 'period': claim.period, 'dimension': f'{part_label}/{claim.denominator or "总收入"}'}]
                return self._text_evidence(claim, result, result.reason)
        dim = ('total', '')
        if claim.entity == 'segment':
            matched = self.fb.match_dim(claim.segment)
            if matched is None:
                result.abstain_code, result.reason = 'X1', f'缺少“{claim.segment}”的分部/明细披露数据'
                candidates = self.fb.dimension_alias_candidates(claim.segment)
                if candidates:
                    result.abstain_code = 'X3'
                    result.reason = f'原文维度“{claim.segment}”与来源标签不一致，须用来源定义确认其与“{"、".join(candidates[:4])}”的关系'
                result.missing = [{'account': claim.metric, 'period': claim.period, 'dimension': claim.segment}]
                return self._text_evidence(claim, result, result.reason)
            dim = matched
        bases, mode = self._bases(claim)
        claim_iv = self._claim_iv(claim)
        definitions = definitions_for(claim.metric)
        # 披露的同比列作为第一候选
        computed = []
        if claim.relation == 'yoy' and dim[0] == 'total' or claim.relation == 'yoy' and dim[0] != 'total':
            disclosed = self.fb.get(claim.metric, claim.period, claim.scope or '合并', dim, 'yoy', claim.definition_id)
            if disclosed and not claim.forecast:
                computed.append(('disclosed.yoy', '报告披露的同比增减', fact_iv(disclosed[0]), [disclosed[0]], False))
        missing: list[Missing] = []
        derived_by_def: dict = {}
        blocked: list[str] = []          # 因缺材料而无法计算的实质性候选定义
        for def_id, label, fn in definitions:
            try:
                expected, ctx = self._compute(claim, fn, bases, claim.period, dim)
                derived_by_def[def_id] = list(dict.fromkeys(ctx.derived_notes))
                computed.append((def_id, label + ('（' + '；'.join(dict.fromkeys(ctx.derived_notes)) + '）' if ctx.derived_notes else ''), expected, ctx.used, ctx.internal))
            except Missing as exc:
                missing.append(exc)
                if not def_id.endswith(('.disclosed', '.weighted', '.basic', '.diluted')):
                    blocked.append(f'{label}（缺{period_label(exc.period)}{account_name(exc.account)}）')
            except ZeroDivisionError as exc:
                result.notes.append(f'{label}：{exc}')
        if claim.metric in VALUATION_METRICS and computed:
            return self._valuation(claim, result, claim_iv, computed)
        admission = next((exc for exc in missing if exc.code != 'X1'), None)
        if admission:
            result.abstain_code, result.reason = admission.code, admission.why
            result.missing = [{'account': admission.account, 'period': admission.period, 'why': admission.why}]
            return result
        hit = next((item for item in computed if self._compatible(claim, item[2], claim_iv)), None)
        # 历史期：其他披露版本（如调整前/原披露）命中 → 核实并提示版本
        if hit is None and mode == 'history' and computed:
            for def_id, label, fn in definitions[:1]:
                for pick in (1, 2):
                    try:
                        alt, ctx = self._compute(claim, fn, bases, claim.period, dim, pick=pick)
                    except (Missing, ZeroDivisionError):
                        break
                    if self._compatible(claim, alt, claim_iv) and not alt.intersects(computed[0][2]):
                        hit = (def_id, label + '（其他披露版本）', alt, ctx.used, ctx.internal)
                        result.advisories.append('A1')
                        result.notes.append('与较早或调整前的披露版本一致，最新披露值为 ' + _display(computed[0][2].mid, claim))
                        break
        result.candidates = [{'definition': d, 'label': l, 'expected': _display(iv.mid, claim),
                              'expected_interval': _interval_dict(iv), 'operands': [_operand(fact) for fact in used],
                              'matched': self._compatible(claim, iv, claim_iv)} for d, l, iv, used, _ in computed]
        if hit is not None:
            def_id, label, expected, used, internal = hit
            primary = computed[0]
            alt_only = hit is not primary and 'A1' not in result.advisories and not def_id.endswith('.disclosed') and def_id != 'disclosed.yoy' \
                and not primary[0] == 'disclosed.yoy'
            if alt_only and self.strict:
                result.abstain_code, result.reason = 'X3', f'仅在备选定义“{label}”下相容；审慎模式不作口径推定'
                self._fill(result, claim, def_id, label, expected, used, as_reference=True)
                return result
            result.status = 'verified'
            result.basis = 'internal' if internal else ('disclosed' if len(used) == 1 and claim.relation == 'value' else 'derived')
            if any('横线视为零' in (fact.label or '') for fact in used):
                result.notes.append('财报对应栏目列示为“-”，按报表列报惯例视为零')
                result.advisories.append('A3')
            self._fill(result, claim, def_id, label, expected, used)
            if claim.kind == 'table' and claim.relation == 'value' and internal and len(used) == 1 \
                    and used[0].source_doc == 'report' and used[0].account == claim.metric and used[0].period == claim.period:
                result.status, result.reason = 'skipped', '预测表原始输入单元格；自身数值不构成独立内部勾稽'
                result.internal_check = '输入单元格，等待独立公式或其他位置比对'
                return result
            if alt_only and not _is_open(claim.metric):
                result.advisories.append('A3')
                result.notes.append(f'按“{label}”口径相容；首选口径“{primary[1]}”对应 {_display(primary[2].mid, claim)}，建议在文中注明口径')
            elif alt_only:
                result.notes.append(f'命中定义：{label}')
            if claim.hedge:
                result.advisories.append('A2')
            if claim.relation in ('yoy', 'qoq') and any(Decimal(o['value']) < 0 for o in result.operands[-1:]):
                result.notes.append('基期为负值，增速按 (本期-基期)/|基期| 计算')
            if mode == 'forecast':
                result.notes.append('预测数据：与研报自身预测表及公式勾稽一致')
            self._presume(claim, result, mode, derived_by_def.get(def_id, []), alt_label=label if alt_only else '')
            return result
        if computed:
            primary = computed[0]
            def_id, label, expected, used, internal = primary
            if self._threshold(claim, expected):
                self._fill(result, claim, def_id, label, expected, used)
                verdict = self._threshold(claim, expected)
                result.status, result.basis = verdict, 'internal' if internal else 'disclosed'
                if verdict == 'unverifiable':
                    result.abstain_code, result.reason = 'X6', '披露精度区间跨越声明阈值，无法确定严格比较关系'
                else:
                    result.error_type, result.reason = 'E7', '披露值不满足原文声明的阈值关系'
                    result.diagnosis = {'type': 'E7', 'detail': result.reason}
                    self._correct(claim, result, expected)
                self._presume(claim, result, mode)
                return result
            if blocked and mode == 'history':
                result.abstain_code = 'X3'
                result.reason = f'已可计算的定义不相容（{label}={_display(expected.mid, claim)}），但仍有候选定义因缺材料无法计算：' + '；'.join(blocked[:3])
                result.missing = [{'account': exc.account, 'name': account_name(exc.account), 'period': exc.period, 'why': exc.why} for exc in missing][:6]
                self._fill(result, claim, def_id, label, expected, used, as_reference=True)
                return result
            if _is_open(claim.metric) and claim.metric not in CLOSED_OVERRIDE:
                result.abstain_code = 'X3'
                result.reason = f'{account_name(claim.metric)}为数据商/券商自定义口径，已登记的 {len(computed)} 种常见定义均不相容（最接近：{label}={_display(expected.mid, claim)}）'
                self._fill(result, claim, def_id, label, expected, used, as_reference=True)
                return result
            if claim.kind == 'prose' and claim.confidence < 0.75:
                rescued = self._rescue(claim, result, claim_iv, definitions, bases, dim)
                if rescued:
                    return result
                result.abstain_code = 'X6'
                result.reason = '绑定置信度不足（' + '；'.join(claim.notes or ['指标/期间/主体由上下文推定']) + '），与候选事实不相容，需人工或模型确认后再判'
                self._fill(result, claim, def_id, label, expected, used, as_reference=True)
                return result
            if claim.kind == 'prose' and claim.entity == 'company' and claim.relation == 'value' and claim.unit in MONEY_UNITS \
                    and claim.metric in ('REVENUE', 'COST', 'GROSS_PROFIT') and expected.mid > 0 and Decimal(0) < claim.decimal() < expected.mid * Decimal('0.6') \
                    and re.search(r'其中|业务|产品|模式|板块|分部|为例|来自|客户|地区|海外|国内|境外|境内|渠道|品类|系列|项目|子公司', norm(claim.text)[:claim.span[0]][-40:]) \
                    and not self._unit_or_period_hit(claim, claim_iv, definitions, bases, dim):
                result.abstain_code, result.reason = 'X6', '数值显著小于公司合计且语境含分部/业务线索，疑为分部口径，需确认主体'
                self._fill(result, claim, def_id, label, expected, used, as_reference=True)
                return self._text_evidence(claim, result, result.reason)
            result.status = 'violated'
            result.basis = 'internal' if internal else 'disclosed'
            self._fill(result, claim, def_id, label, expected, used)
            self._diagnose(claim, result, claim_iv, definitions, bases, dim, expected)
            if self.strict and result.error_type in ('E2', 'E3'):
                # 审慎模式：期间/口径错配须有表头、声明或版本证据，仅凭数值巧合不归因
                hint = result.diagnosis.get('detail', '')
                result.error_type = 'E1'
                result.diagnosis = {'type': 'E1', 'detail': '与披露值不相容；' + hint.replace('疑为', '数值上接近') + '（审慎模式下数值巧合不作为期间/口径错配证据）'}
            if result.error_type == 'E5' and claim.relation == 'value' and any(d.endswith(('.disclosed', '.weighted', '.basic')) for d, *_ in computed):
                result.error_type = 'E1'          # 该比率在财报中有直接披露值，属数值不符而非计算口径问题
                result.diagnosis = {'type': 'E1', 'detail': '与财报直接披露的数值及按公式重算的结果均不相容'}
            if result.basis == 'internal' and result.error_type in ('E1', 'E5', 'E7'):
                self._internal_attribution(claim, result, expected)
            self._presume(claim, result, mode, derived_by_def.get(def_id, []))
            self._correct(claim, result, expected)
            return result
        # 无任何定义可计算 → 材料缺口
        result.missing = [{'account': exc.account, 'name': account_name(exc.account), 'period': exc.period, 'why': exc.why} for exc in missing][:8]
        if not missing and result.notes:
            result.abstain_code, result.reason = 'X2', '；'.join(result.notes)
            return result
        if mode == 'forecast':
            result.status, result.reason = 'skipped', '预测/观点类数据，研报内无可勾稽的对应项'
            return result
        if _is_open(claim.metric) and claim.metric not in CLOSED_OVERRIDE and not any(self.fb.periods(m.account) for m in missing):
            result.abstain_code, result.reason = 'X3', f'{account_name(claim.metric)}需要的明细科目未在已入库材料中披露'
        else:
            result.abstain_code = 'X1'
            needs = sorted({f"{period_label(item['period'])}{item['name']}" for item in result.missing})
            result.reason = '缺少材料：' + '、'.join(needs[:4]) + ('等' if len(needs) > 4 else '')
        internal = self._internal_only(claim, claim_iv, definitions, dim)
        if internal:
            result.internal_check = internal
        return self._text_evidence(claim, result, result.reason)

    # —— 辅助 ——
    def _presume(self, claim: Claim, result: Result, mode: str, derived=(), alt_label: str = '') -> None:
        """把本条结论用到的推定逐项写明（标准模式的“推定并披露”）。"""
        text = norm(claim.text)
        if mode == 'history' and claim.entity in ('company', 'segment') and result.basis in ('disclosed', 'derived') \
                and not claim.scope:
            assume(result, 'scope', '原文未写明会计口径，按合并报表核对（通行口径）')
        if '期间由上下文推定' in claim.notes:
            assume(result, 'period', f'原句未写明期间，按上下文推定为{period_label(claim.period)}')
        if '业务主体承接上文' in claim.notes:
            assume(result, 'subject', f'原句未写明主体，按上文承接为“{claim.segment}”')
        if '指标由业务主体推定为收入' in claim.notes:
            assume(result, 'metric', '原句未写明指标，按业务主体推定为收入')
        if alt_label:
            assume(result, 'definition', f'原文未写明定义，按“{alt_label}”核实')
        for note in derived:
            assume(result, 'derived', note)
        if any('横线视为零' in (o.get('label') or '') for o in result.operands):
            assume(result, 'dash_zero', '财报对应栏目列示为“-”，按报表列报惯例视为零')
        if claim.relation in ('yoy', 'qoq') and any(Decimal(o['value']) < 0 for o in result.operands[-1:]):
            assume(result, 'negative_base', '基期为负，增速按 (本期−基期)/|基期| 计算')
        if claim.hedge and not (claim.threshold_operator or claim.hedge in ('above', 'below')):
            assume(result, 'hedge', '原文含“约/超过/近”等限定词，按放宽后的区间比较')
        if result.basis == 'internal':
            assume(result, 'internal', '预测与估值无法与实际披露核对，改为核对研报自身预测表与首页行情')
        if 'A1' in result.advisories:
            assume(result, 'version', '与较早或调整前的披露版本一致，最新披露值另有更新')

    def _internal_attribution(self, claim: Claim, result: Result, expected: IV) -> None:
        """研报内部勾稽不一致的归因（计划书表5-6）：方向说反为E7；正文数值与自身表格不一致为E6；表内或公式计算不平为E5。"""
        observed = claim.interval()
        if claim.relation in ('yoy', 'qoq', 'pp') and claim.decimal() != 0 and expected is not None and (-observed).intersects(expected):
            code, detail = 'E7', '增减方向与研报自身数据相反（逻辑比较错误）'
        elif claim.kind == 'prose' and claim.relation == 'value' and len(result.operands) == 1 \
                and result.operands[0].get('source_type') == 'report_table':
            table = result.operands[0].get('table') or '预测表'
            code, detail = 'E6', f'正文数值与研报自身“{table}”中的同一数据不一致（文表不一致，出处与数值不符）'
        else:
            code, detail = 'E5', '按研报自身表格数据与登记公式重算不相容（表内/公式计算不平）'
        result.error_type = code
        result.diagnosis = {'type': code, 'detail': detail}

    def _fill(self, result: Result, claim: Claim, def_id, label, expected: IV, used, as_reference=False):
        result.definition, result.definition_label = def_id, label
        registration = next((row for row in registered_definitions() if row['account'] == claim.metric and row['definition_id'] == def_id), None)
        if def_id == 'disclosed.yoy':
            registration = next(row for row in registered_definitions() if row['account'] == 'SOURCE_QUOTATION' and row['definition_id'] == def_id)
        if registration:
            result.rule_ids = [registration['rule_id']]
            result.rules = [registration]
            result.definition_source_hash = registration['function_source_hash']
            result.contract_hash = registration['contract_hash']
        relation_id = {'yoy': 'change.abs', 'qoq': 'change.abs', 'pp': 'change.points', 'cagr': 'change.cagr'}.get(claim.relation)
        if relation_id and def_id != 'disclosed.yoy':
            related = next(row for row in registered_definitions() if row['account'] == 'PERIOD_CHANGE' and row['definition_id'] == relation_id)
            result.rule_ids.append(related['rule_id'])
            result.rules.append(related)
        if claim.threshold_operator:
            threshold = next(row for row in registered_definitions() if row['account'] == 'SOURCE_THRESHOLD')
            result.rule_ids.append(threshold['rule_id'])
            result.rules.append(threshold)
        result.expected = str(expected.mid)
        result.expected_interval = _interval_dict(expected)
        result.expected_display = _display(expected.mid, claim)
        seen, operands = set(), []
        for fact in used:
            if fact.fact_id not in seen:
                seen.add(fact.fact_id)
                operand = _operand(fact)
                if fact.fact_id in self.fb.subject_evidence:
                    operand['subject_evidence'] = self.fb.subject_evidence[fact.fact_id]
                operands.append(operand)
        result.operands = operands
        result.evidence = [e for o in operands for e in o['evidence']]
        relation = {'value': '', 'yoy': '同比', 'qoq': '环比', 'cagr': 'CAGR', 'pp': '百分点变化', 'share': '占比'}[claim.relation]
        parts = '；'.join(f"{o['name']}{'[' + o['dimension'] + ']' if o['dimension'] else ''}({o['period']})={fmt_amount(Decimal(o['value']), o['unit'], o['decimals']) if o['unit'] not in ('%',) else fmt_amount(Decimal(o['value']), '%', o['decimals'])}" for o in operands[:6])
        result.formula = f'{label}{relation and "·" + relation}：{parts}' if parts else label
        if as_reference:
            result.notes.append(f'参考：{label}{relation}为 {result.expected_display}')

    def _unit_or_period_hit(self, claim, claim_iv, definitions, bases, dim) -> bool:
        probe = Result(claim.cid)
        self._diagnose(claim, probe, claim_iv, definitions, bases, dim, None)
        return probe.error_type in ('E4', 'E2')

    def _rescue(self, claim, result, claim_iv, definitions, bases, dim) -> bool:
        """低置信绑定且首选期间不相容时：若同一指标在相邻期间精确相容，则按该期间核实并提示。"""
        year = int(claim.period[:4])
        for delta in (-1, 1, -2):
            period = f'{year + delta}{claim.period[4:]}'
            for def_id, label, fn in definitions:
                try:
                    probe = Claim(**{**claim.__dict__, 'base_period': ''})
                    expected, ctx = self._compute(probe, fn, bases, period, dim)
                except (Missing, ZeroDivisionError):
                    continue
                if expected.intersects(claim.interval()) and '期间由上下文推定' in claim.notes:
                    result.status, result.basis = 'verified', 'derived' if claim.relation != 'value' else 'disclosed'
                    self._fill(result, claim, def_id, label, expected, ctx.used)
                    result.notes.append(f'原句未写明期间，数值与{period_label(period)}披露一致，按该期间核实')
                    result.advisories.append('A3')
                    assume(result, 'period', f'原句未写明期间，数值与{period_label(period)}披露一致，按该期间核实')
                    return True
        return False

    def _diagnose(self, claim, result, claim_iv, definitions, bases, dim, expected):
        observed = claim.interval()
        fn = definitions[0][2]
        # E4 单位：10 的整数次幂
        if claim.relation == 'value' and expected is not None and expected.mid != 0 and claim.decimal() != 0:
            for power in (1, 2, 3, 4, 5, 6, 8, -1, -2, -3, -4, -5, -6, -8):
                factor = Decimal(10) ** power
                if (observed * factor).intersects(expected):
                    result.error_type = 'E4'
                    result.diagnosis = {'type': 'E4', 'detail': f'原文数值乘以 10^{power} 后与披露值相容，疑为单位或数量级错误'}
                    return
        # E2 期间：同一指标的其他期间
        year = int(claim.period[:4])
        kind = claim.period[4:]
        probe = Claim(**{**claim.__dict__, 'base_period': ''})
        for period in [f'{year - 1}{kind}', f'{year + 1}{kind}', f'{year - 2}{kind}'] + [f'{year}{k}' for k in ('Y', 'H1', '9M', 'Q1', 'Q4', 'Q2', 'Q3') if k != kind]:
            try:
                other, ctx = self._compute(probe, fn, [self.fb], period, dim)
            except (Missing, ZeroDivisionError):
                continue
            if other.intersects(observed):
                result.error_type = 'E2'
                result.diagnosis = {'type': 'E2', 'detail': f'原文数值与{period_label(period)}的{definitions[0][1]}相容，疑为期间错配',
                                    'evidence': [e for f in ctx.used for e in f.evidence]}
                return
        # E3 口径：兄弟科目、母公司口径、扣非口径
        for sibling in SIBLINGS.get(claim.metric, []):
            for _, label, sib_fn in definitions_for(sibling)[:1]:
                try:
                    other, ctx = self._compute(claim, sib_fn, [self.fb], claim.period, dim)
                except (Missing, ZeroDivisionError):
                    continue
                if other.intersects(observed):
                    result.error_type = 'E3'
                    result.diagnosis = {'type': 'E3', 'detail': f'原文数值与同期“{account_name(sibling)}”相容，疑为口径/科目混用',
                                        'evidence': [e for f in ctx.used for e in f.evidence]}
                    return
        try:
            other, ctx = self._compute(claim, fn, [self.fb], claim.period, dim, scope='母公司')
            if other.intersects(observed):
                result.error_type = 'E3'
                result.diagnosis = {'type': 'E3', 'detail': '原文数值与母公司报表口径相容，而研报语境应为合并口径', 'evidence': [e for f in ctx.used for e in f.evidence]}
                return
        except (Missing, ZeroDivisionError):
            pass
        if expected is not None and claim.relation in ('yoy', 'qoq', 'pp') and claim.decimal() != 0 and (-observed).intersects(expected):
            result.error_type = 'E7'
            result.diagnosis = {'type': 'E7', 'detail': '数值大小相容但增减方向相反（逻辑比较错误）'}
            return
        if claim.relation != 'value' or len(result.operands) > 1:
            result.error_type = 'E5'
            detail = '按披露数据重算的结果与原文不相容'
            if claim.relation == 'yoy':
                try:
                    qoq_claim = Claim(**{**claim.__dict__, 'relation': 'qoq'})
                    other, _ = self._compute(qoq_claim, fn, [self.fb], claim.period, dim)
                    if other.intersects(observed):
                        detail = '数值与环比口径相容，疑将环比写成同比'
                except (Missing, ZeroDivisionError):
                    pass
            result.diagnosis = {'type': 'E5', 'detail': detail}
            return
        result.error_type = 'E1'
        result.diagnosis = {'type': 'E1', 'detail': '与披露值不相容，未发现单位、期间或口径层面的解释'}

    def _correct(self, claim: Claim, result: Result, expected: IV):
        value = expected.mid
        display = _display(value, claim)
        text = claim.text
        replacement = ''
        if claim.kind == 'prose' and claim.span[1] > claim.span[0]:
            normalized = norm(text)
            start, end = claim.span
            segment = normalized[start:end]
            shown = display
            if claim.threshold_operator or claim.hedge in ('above', 'below'):
                prefix = re.sub(r'(?:不低于|不高于|超过|高于|低于|不足|不到|少于|多于|突破|逾|超)\s*$', '为', normalized[:start])
                suffix = re.sub(r'^\s*(?:以上|以下)', '', normalized[end:])
                replacement = prefix + shown + suffix
            elif claim.relation in ('yoy', 'qoq', 'pp') and not segment.strip().startswith(('+', '-')):
                shown = _display(abs(value), claim)
                prefix = normalized[:start]
                word = re.search(r'(增长|上升|增加|提升|提高|扩大|下降|下滑|减少|降低|回落|收窄)(\s*(?:约|近|了|达|为)?\s*)$', prefix)
                if word:
                    want_down = value < 0
                    is_down = word[1] in ('下降', '下滑', '减少', '降低', '回落', '收窄')
                    if want_down != is_down:
                        prefix = prefix[:word.start(1)] + ('下降' if want_down else '增长') + prefix[word.end(1):]
                replacement = prefix + shown + normalized[end:]
            else:
                if segment.strip().startswith('+') and value >= 0:
                    shown = '+' + shown
                replacement = normalized[:start] + shown + normalized[end:]
        elif claim.kind == 'table':
            year = claim.period[:4] + ('E' if claim.forecast else 'A') if claim.period.endswith('Y') else period_label(claim.period)
            table = {'IS': '利润表', 'BS': '资产负债表', 'CF': '现金流量表', 'RATIO': '主要财务比率', 'SUMMARY': '财务摘要'}.get(claim.table, '表格')
            replacement = f'{table}“{claim.row_label}”行 {year} 列应为 {display}'
        else:
            subject = (self.meta.company or '公司') + period_label(claim.period) + (claim.segment or '') + account_name(claim.metric)
            relation = {'value': '为', 'yoy': '同比变动', 'qoq': '环比变动', 'cagr': '复合增速为', 'pp': '变动', 'share': '占比为'}[claim.relation]
            replacement = f'{subject}{relation}{display}'
        result.correction = {'correct_value': display, 'replacement_text': replacement, 'calculation': result.formula,
                             'source': '、'.join(result.evidence[:4]), 'basis': f"{result.error_type} {ERROR_LABELS.get(result.error_type, '')}；定义：{result.definition_label}"}
        if not result.reason:
            result.reason = f"原文 {claim.num_text}，按{result.definition_label}应为 {display}；{result.diagnosis.get('detail', '')}"

    def _valuation(self, claim, result, claim_iv, computed):
        """估值倍数：允许股价基准日造成的小幅漂移，漂移内核实并提示。"""
        if self._threshold(claim, computed[0][2]):
            chosen = next((item for item in computed if self._threshold(claim, item[2]) == 'verified'), computed[0])
            def_id, label, expected, used, _ = chosen
            verdict = self._threshold(claim, expected)
            result.status, result.basis = verdict, 'internal'
            self._fill(result, claim, def_id, label, expected, used)
            assume(result, 'internal', '估值阈值依据研报行情与对应盈利核对')
            if verdict == 'unverifiable':
                result.abstain_code, result.reason = 'X6', '估值披露精度跨越阈值，无法确定比较关系'
            elif verdict == 'violated':
                result.error_type, result.reason = 'E7', '估值不满足声明阈值'
                result.diagnosis = {'type': 'E7', 'detail': result.reason}
                self._correct(claim, result, expected)
            return result
        exact = next((item for item in computed if item[2].intersects(claim_iv)), None)
        chosen = exact
        if chosen is None:
            chosen = next((item for item in computed if item[2].widen(self.drift).intersects(claim_iv)), None)
            if chosen is not None:
                result.advisories.append('A4')
                assume(result, 'drift', f'估值倍数与首页行情计算值相差在 {self.drift * 100}% 以内，视为取价日不同')
                implied = ''
                if 'MARKET_CAP' in self.market and chosen[2].mid != 0:
                    ratio = Decimal(claim.value) / chosen[2].mid
                    implied = f'，隐含总市值约 {fmt_amount(self.market["MARKET_CAP"].mid * ratio, "亿元", 2)}'
                result.notes.append(f'与首页行情计算值 {_display(chosen[2].mid, claim)} 相差在 {self.drift * 100}% 内{implied}，疑为取价日不同')
        if chosen is None and claim.kind == 'prose' and claim.confidence < 0.75:
            def_id, label, expected, used, _ = computed[0]
            result.abstain_code = 'X6'
            result.reason = '绑定置信度不足（' + '；'.join(claim.notes or ['期间由上下文推定']) + '），估值倍数与候选计算不相容，需确认对应期间'
            self._fill(result, claim, def_id, label, expected, used, as_reference=True)
            return result
        if chosen is None:
            def_id, label, expected, used, _ = computed[0]
            result.status, result.basis, result.error_type = 'violated', 'internal', 'E5'
            self._fill(result, claim, def_id, label, expected, used)
            result.diagnosis = {'type': 'E5', 'detail': '估值倍数与研报自述股价/市值及对应盈利不相容'}
            assume(result, 'internal', '估值倍数无法与实际披露核对，改为核对首页行情与研报自身盈利预测')
            self._correct(claim, result, expected)
            return result
        def_id, label, expected, used, _ = chosen
        result.status, result.basis = 'verified', 'internal'
        self._fill(result, claim, def_id, label, expected, used)
        result.notes.append('估值倍数为内部勾稽：市值/股价取自研报首页，盈利取自' + ('研报预测表' if claim.forecast else '披露财报'))
        assume(result, 'internal', '估值倍数无法与实际披露核对，改为核对首页行情与' + ('研报自身盈利预测' if claim.forecast else '披露财报'))
        return result

    def _internal_only(self, claim, claim_iv, definitions, dim) -> str:
        if dim[0] != 'total':
            return ''
        for def_id, label, fn in definitions:
            try:
                expected, _ = self._compute(claim, fn, [self.tb], claim.period, dim)
            except (Missing, ZeroDivisionError):
                continue
            return '与研报自身表格一致' if expected.intersects(claim_iv) else f'与研报自身表格不一致（表内为 {_display(expected.mid, claim)}）'
        return ''

    # —— 表内勾稽：小计 = 明细之和；残差项随之确认 ——
    def _identity_checks(self, results: list[Result]):
        by_cid = {r.cid: r for r in results}
        claims = [c for c in self.model.claims if c.kind == 'table']
        index = defaultdict(dict)
        for claim in claims:
            index[(claim.table, claim.row_label)][claim.period] = claim
        blocks = {'BS': [('流动资产', ['现金', '应收票据及应收账款', '其他应收款', '预付账款', '存货', '其他流动资产']),
                         ('非流动资产', ['长期投资', '固定资产', '无形资产', '其他非流动资产']),
                         ('流动负债', ['短期借款', '应付票据及应付账款', '其他流动负债']),
                         ('非流动负债', ['长期借款', '其他非流动负债'])],
                  'CF': [('经营活动现金流', ['净利润', '折旧摊销', '财务费用', '投资损失', '营运资金变动', '其他经营现金流']),
                         ('投资活动现金流', ['-资本支出', '长期投资', '其他投资现金流']),
                         ('筹资活动现金流', ['短期借款', '长期借款', '普通股增加', '资本公积增加', '其他筹资现金流'])]}
        for table, groups in blocks.items():
            for total_label, parts in groups:
                totals = index.get((table, total_label))
                if not totals:
                    continue
                for period, total_claim in totals.items():
                    acc, members, ok = IV(0), [], True
                    for part in parts:
                        sign = -1 if part.startswith('-') else 1
                        member = index.get((table, part.lstrip('-')), {}).get(period)
                        if member is None:
                            ok = False
                            break
                        members.append(member)
                        acc = acc + member.interval() * sign
                    if not ok:
                        continue
                    consistent = acc.intersects(total_claim.interval())
                    for member in members:
                        r = by_cid.get(member.cid)
                        if r is None or r.status in ('verified', 'violated'):
                            continue
                        r.internal_check = '表内小计与明细一致' if consistent else '表内小计与明细不一致'
                        if member.metric in ('RESIDUAL', 'TEMPLATE_ITEM'):
                            registration = next(row for row in registered_definitions() if row['account'] == 'TEMPLATE_IDENTITY')
                            r.rule_ids, r.rules = [registration['rule_id']], [registration]
                            r.definition_source_hash, r.contract_hash = registration['function_source_hash'], registration['contract_hash']
                            r.definition, r.formula = 'identity.template', f'表内勾稽：{total_label} = ' + ' + '.join(parts)
                            rest = IV(0)
                            for part, other in zip(parts, members):
                                if other.cid != member.cid:
                                    rest = rest + other.interval() * (-1 if part.startswith('-') else 1)
                            sign = -1 if parts[members.index(member)].startswith('-') else 1
                            member_expected = (total_claim.interval() - rest) * sign
                            r.expected, r.expected_display = str(member_expected.mid), _display(member_expected.mid, member)
                            r.expected_interval = _interval_dict(member_expected)
                            r.operands = [{'claim_id': c.cid, 'account': c.metric, 'period': c.period,
                                           'scope': c.scope or '合并', 'value': c.value, 'unit': c.unit, 'decimals': c.decimals,
                                           'quantum': str(quantum(c.unit, c.decimals)), 'interval': _interval_dict(c.interval()),
                                           'currency': c.currency, 'definition_id': c.definition_id,
                                           'source_doc': 'report', 'publish_date': self.meta.report_date,
                                           'evidence': [f"{self.model.stats.get('doc_sha256', '')[:12]}:p{c.page}:table"]
                                           if self.model.stats.get('doc_sha256') else []}
                                          for c in [total_claim, *members] if c.cid != member.cid]
                            if consistent:
                                r.status, r.basis, r.abstain_code = 'verified', 'identity', ''
                                r.reason = ''
                                r.definition_label = f'表内勾稽：{total_label} = ' + ' + '.join(parts)
                                sha = self.model.stats.get('doc_sha256', '')
                                r.evidence = [f'{sha[:12]}:p{c.page}:table' for c in [total_claim, *members]] if sha else []
                                r.notes.append('券商模板聚合/残差项无法定同名科目，按“小计=明细之和”在表内勾稽一致')
                                assume(r, 'identity', '券商模板聚合/残差项没有法定同名科目，按“小计=明细之和”在表内勾稽')
                            elif member.metric in ('RESIDUAL',):
                                r.status, r.basis, r.error_type = 'violated', 'identity', 'E5'
                                r.reason = f'表内勾稽不平：{total_label} ≠ ' + ' + '.join(parts)
                                r.diagnosis = {'type': 'E5', 'detail': r.reason}

    # —— 文本佐证：在已入库材料原文里找“同一数值 + 语境关键词” ——
    def _text_evidence(self, claim: Claim, result: Result, reason: str) -> Result:
        if not result.reason:
            result.reason = reason
        if not result.abstain_code:
            result.abstain_code = 'X1'
        if not self.texts or claim.forecast or not claim.metric or not claim.period:
            return result
        # A lexical coincidence is a retrieval hint. Reparse the located text
        # with the current extractor and require the same typed identity.
        from .claims import extract_prose_claims
        from .detect import DocMeta
        from .docio import Document, Page, rows_from_layout_text
        for page in self.texts:
            published = page.get('report_date') or page.get('publish_date') or ''
            try:
                if date.fromisoformat(published).isoformat() != published:
                    continue
            except (ValueError, TypeError):
                continue
            if self.meta.report_date and (not published or published > self.meta.report_date):
                continue
            if not page.get('company') or not same_company(page, self.meta) or page.get('currency', 'CNY') != claim.currency:
                continue
            text = norm(page.get('text', ''))
            source_meta = DocMeta(company=self.meta.company, base_year=int(page['period'][:4]) if re.match(r'20\d{2}', page.get('period', '')) else None,
                                  report_date=published)
            document = Document('/virtual/source.txt', page.get('name', ''), page.get('sha', ''), 'text', 'text',
                                [Page(page.get('page', 1), rows_from_layout_text(page.get('page', 1), text), text)])
            segments = tuple(sorted({claim.segment, claim.denominator} - {''}))
            cache_key = (page.get('sha', ''), page.get('page', 1), published, source_meta.base_year, segments,
                         hashlib.sha256(text.encode('utf-8')).hexdigest())
            if cache_key not in self._text_claim_cache:
                self._text_claim_cache[cache_key] = extract_prose_claims(document, source_meta, set(segments))
            candidates = self._text_claim_cache[cache_key]
            for source in candidates:
                if source.forecast or '期间由上下文推定' in source.notes or not source.period:
                    continue
                identity = lambda c: (c.metric, c.period, c.base_period, c.relation, c.entity, c.segment,
                                      c.denominator, c.scope or '合并', c.currency, c.definition_id, c.threshold_operator)
                if identity(source) != identity(claim) or source.hedge != claim.hedge or not source.interval().intersects(claim.interval()):
                    continue
                if not claim.metric:
                    # Unregistered quantities need an independently typed
                    # metric; they cannot be proved by generic two-word hits.
                    continue
                result.status, result.basis, result.abstain_code = 'verified', 'text', ''
                result.expected_interval = _interval_dict(source.interval())
                result.source_precision = [{'source_doc': page.get('sha', '')[:12], 'publish_date': published,
                                            'unit': source.unit, 'decimals': source.decimals,
                                            'quantum': str(quantum(source.unit, source.decimals)),
                                            'interval': _interval_dict(source.interval()), 'currency': source.currency,
                                            'definition_id': source.definition_id,
                                            'evidence': [f"{page.get('sha', '')[:12]}:p{page.get('page', 1)}:text"]}]
                result.reason = ''
                result.definition_label = '同期间、主体与关系的原文佐证'
                result.formula = f"{page.get('name', '')} 第{page.get('page', 1)}页：{source.text}"
                result.evidence = [f"{page.get('sha', '')[:12]}:p{page.get('page', 1)}:text"] if page.get('sha') else []
                assume(result, 'text', '原文数值及期间、主体、口径、关系一致，未经公式重算')
                if not claim.scope:
                    assume(result, 'scope', '原文未声明会计口径，按合并口径比对原文')
                return result
        return result


def verify_model(facts: list[Fact], model: ReportModel, *, policy: str = 'standard', texts: list | None = None,
                 doc_index: dict | None = None) -> list[Result]:
    """Host-owned verification entry shared by upload and native workflows."""
    if policy not in ('standard', 'strict'):
        raise ValueError('核验策略须为 standard 或 strict')
    return Verifier(facts, model, strict=policy == 'strict', texts=texts, doc_index=doc_index).verify_all()


def summarize(claims: list[Claim], results: list[Result]) -> dict:
    by_cid = {r.cid: r for r in results}
    assertions: dict[str, list[Claim]] = defaultdict(list)
    for claim in claims:
        assertions[claim.assertion_id or claim.cid].append(claim)
    assertion_categories = Counter(rows[0].category for rows in assertions.values())
    assertion_statuses = Counter()
    for rows in assertions.values():
        statuses = {by_cid[claim.cid].status for claim in rows}
        assertion_statuses[next(iter(statuses)) if len(statuses) == 1 else 'mixed'] += 1
    groups: dict[str, dict] = {}
    labels = {'core': '公司历史财务（法定与封闭定义）', 'open_metric': '公司历史财务（开放定义指标）', 'forecast': '预测与估值（内部勾稽）',
              'template': '券商模板聚合项（表内勾稽）', 'market': '行情数据（内部勾稽）', 'external': '行业/第三方数据（文本佐证）',
              'other_fact': '其他事实（文本佐证）'}
    for claim in claims:
        result = by_cid[claim.cid]
        group = groups.setdefault(claim.category, {'label': labels.get(claim.category, claim.category), 'total': 0, 'verified': 0, 'violated': 0,
                                                   'unverifiable': 0, 'skipped': 0, 'occurrences': 0})
        group['total'] += 1
        group['occurrences'] += claim.occurrences
        group[result.status] += 1
    def rate(keys):
        total = sum(groups.get(k, {}).get('total', 0) - groups.get(k, {}).get('skipped', 0) for k in keys)
        unv = sum(groups.get(k, {}).get('unverifiable', 0) for k in keys)
        judged = sum(groups.get(k, {}).get('verified', 0) + groups.get(k, {}).get('violated', 0) for k in keys)
        return {'total': total, 'judged': judged, 'unverifiable': unv, 'abstention_rate': round(unv / total, 4) if total else None,
                'coverage': round(judged / total, 4) if total else None}
    totals = {key: sum(g[key] for g in groups.values()) for key in ('total', 'verified', 'violated', 'unverifiable', 'skipped', 'occurrences')}
    abstain = defaultdict(int)
    errors = defaultdict(int)
    basis = defaultdict(int)
    gaps = defaultdict(int)
    for result in results:
        if result.status == 'unverifiable':
            abstain[result.abstain_code or 'X1'] += 1
            for item in (result.missing if (result.abstain_code or 'X1') == 'X1' else []):
                gaps[(item.get('period', ''), item.get('name') or item.get('account', ''), item.get('dimension', ''))] += 1
        elif result.status == 'violated':
            errors[result.error_type or 'E1'] += 1
        if result.status in ('verified', 'violated'):
            basis[result.basis or 'disclosed'] += 1
    modality = defaultdict(lambda: {'total': 0, 'verified': 0, 'violated': 0, 'unverifiable': 0, 'skipped': 0})
    for claim in claims:
        modality[getattr(claim, 'modality', 'fact')]['total'] += 1
        modality[getattr(claim, 'modality', 'fact')][by_cid[claim.cid].status] += 1
    gap_list = [{'period': p, 'item': n, 'dimension': d, 'claims': count} for (p, n, d), count in sorted(gaps.items(), key=lambda kv: -kv[1])][:30]
    presumed = defaultdict(int)
    for result in results:
        if result.status in ('verified', 'violated'):
            for code in {a['code'] for a in result.assumptions}:
                presumed[code] += 1
    assumptions = [{'code': code, 'label': ASSUMPTION_LABELS.get(code, code), 'count': count}
                   for code, count in sorted(presumed.items(), key=lambda kv: -kv[1])]
    return {'groups': groups, 'totals': totals, 'core': rate(['core']), 'historical': rate(['core', 'open_metric']),
            'financial': rate(['core', 'open_metric', 'forecast', 'template', 'market']), 'all': rate(list(groups)),
            'abstain_codes': dict(abstain), 'error_types': dict(errors), 'basis': dict(basis), 'gaps': gap_list, 'assumptions': assumptions,
            'presumed_results': sum(1 for r in results if r.status in ('verified', 'violated') and r.assumptions),
            'assertions': {'total': len(assertions), 'by_category': dict(assertion_categories), 'by_status': dict(assertion_statuses)},
            'modality': {k: dict(v) for k, v in modality.items()}}
