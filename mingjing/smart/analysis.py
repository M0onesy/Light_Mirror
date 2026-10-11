"""财报分析：指标表（含同比/环比）+ 异常信号（非经常性损益、口径变化、利润与现金流背离等）。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from .common import IV, best_money_unit, fmt_amount, period_label, previous_quarter, quantum, shift_year
from .taxonomy import account_name
from .verify import (Ctx, FactBase, Missing, _operand, definitions_for, fact_iv, point_change,
                     registered_definitions, relative_change)

AMOUNTS = ['REVENUE', 'COST', 'OPERATING_PROFIT', 'PROFIT_TOTAL', 'NP', 'NP_PARENT', 'NP_PARENT_ADJ', 'CFO', 'ASSETS', 'LIABILITIES',
           'EQUITY_PARENT', 'AR', 'INVENTORY', 'CASH_FUNDS', 'EXP_SELLING', 'EXP_ADMIN', 'EXP_RD', 'EXP_FINANCE']
RATIOS = ['GROSS_MARGIN', 'NET_MARGIN', 'NP_PARENT_MARGIN', 'ROE', 'DEBT_RATIO', 'CURRENT_RATIO', 'SELLING_RATIO', 'ADMIN_RATIO', 'RD_RATIO',
          'FINANCE_RATIO', 'PERIOD_EXPENSE_RATIO', 'EPS', 'BPS']
EXTRA = {'CASH_CONTENT': '净现比（经营现金流/净利润）', 'NRI_SHARE': '非经常性损益占归母净利润'}


@dataclass
class Evaluation:
    metric: str
    period: str
    definition_id: str
    label: str
    interval: IV
    point: Decimal
    used: list
    derived_notes: list = field(default_factory=list)
    assumptions: list = field(default_factory=list)


class PointCtx(Ctx):
    """Run the same registered formula on source centres for display only."""

    def __call__(self, account, off=0, *, total=False):
        return IV(super().__call__(account, off, total=total).mid)

    def _direct(self, account):
        return IV(super()._direct(account).mid)


def _interval(interval: IV) -> dict:
    return {'lower': str(interval.lo), 'upper': str(interval.hi),
            'lower_closed': interval.lower_closed, 'upper_closed': interval.upper_closed}


def _unique_facts(used: list) -> list:
    return list({fact.fact_id: fact for fact in used}.values())


def _registration(metric: str, definition_id: str) -> dict:
    return next(row for row in registered_definitions()
                if row['account'] == metric and row['definition_id'] == definition_id)


def _operands(used: list) -> list:
    return [{**_operand(fact), 'currency': fact.currency, 'definition_id': fact.definition_id,
             'nr': fact.nr,
             'parse_conf': str(fact.parse_conf), 'interval': _interval(fact_iv(fact)),
             'quantum': str(quantum(fact.display_unit, fact.decimals))} for fact in _unique_facts(used)]


def _evidence(used: list) -> list:
    return list(dict.fromkeys(e for fact in used for e in fact.evidence))


def _cell(value: Evaluation) -> dict:
    registration = _registration(value.metric, value.definition_id)
    registrations = [registration]
    if value.derived_notes:
        registrations.append(_registration('PERIOD_CHANGE', 'quarter.derived'))
    operands = _operands(value.used)
    return {'value': str(value.point), 'display': _show(value.metric, value.point),
            'period': value.period, 'scope': value.used[0].scope if value.used else '',
            'definition': value.label, 'definition_id': value.definition_id, 'formula': registration['formula'],
            'operands': operands, 'evidence': _evidence(value.used), 'interval': _interval(value.interval),
            'source_precision': [{key: operand[key] for key in ('fact_id', 'unit', 'decimals', 'quantum', 'interval')}
                                 for operand in operands],
            'rule_ids': [row['rule_id'] for row in registrations], 'rules': registrations,
            'definition_source_hash': registration['function_source_hash'],
            'contract_hash': registration['contract_hash'], 'execution_entry': 'mingjing.smart.analysis.analyze',
            'derived_notes': list(value.derived_notes), 'assumptions': list(value.assumptions),
            'value_role': 'source_centre_for_display', 'decision_basis': 'full_interval'}


def _admitted_rows(fb, *args, **kwargs):
    try:
        return fb.get(*args, **kwargs)
    except Missing:
        return []


def _evaluate(fb: FactBase, metric: str, period: str, *, scope: str, policy: str,
              definition_id: str = '') -> Evaluation:
    candidates = definitions_for(metric)
    if definition_id:
        candidates = [candidate for candidate in candidates if candidate[0] == definition_id]
    if metric in AMOUNTS or policy == 'strict':
        candidates = candidates[:1]          # 金额科目只取本义，不用“净利润≈归母净利润”这类备选口径填表
    errors = []
    for position, (def_id, label, definition) in enumerate(candidates):
        ctx = Ctx([fb], period, scope=scope)
        try:
            interval = definition(ctx)
            assumptions = []
            if position:
                assumptions.append({'code': 'definition', 'text': '首选定义材料不足，使用登记的备选定义：' + label})
            if any('横线视为零' in (fact.label or '') for fact in ctx.used):
                if policy == 'strict':
                    raise Missing(metric, period, '审慎模式不将横线列报推定为零', 'X3')
                assumptions.append({'code': 'dash_zero', 'text': '来源横线按零列报'})
            point = definition(PointCtx([fb], period, scope=scope)).mid
            return Evaluation(metric, period, def_id, label, interval, point, _unique_facts(ctx.used), ctx.derived_notes, assumptions)
        except Missing as exc:
            if exc.code in ('X4', 'X5'):
                raise
            errors.append(exc)
        except ZeroDivisionError as exc:
            errors.append(Missing(metric, period, str(exc), 'X2'))
    raise next((error for code in ('X4', 'X5', 'X2', 'X3', 'X1') for error in errors if error.code == code),
               Missing(metric, period, '无可执行的登记定义', 'X3'))


def _comparison(current: Evaluation, base: Evaluation, relation: str, policy: str) -> dict:
    if current.definition_id != base.definition_id:
        raise Missing(current.metric, current.period, '两期采用不同指标定义，无法直接比较', 'X3')
    if relation != 'pp' and base.interval.lo <= 0 <= base.interval.hi:
        raise ZeroDivisionError('基数为零或跨零')
    if relation != 'pp' and policy == 'strict' and base.interval.lo < 0:
        raise Missing(current.metric, base.period, '负基期增速缺少明示授权方法；审慎模式不默认绝对值规则', 'X3')
    interval = point_change(current.interval, base.interval) if relation == 'pp' else relative_change(current.interval, base.interval)
    point = (point_change(IV(current.point), IV(base.point)) if relation == 'pp'
             else relative_change(IV(current.point), IV(base.point))).mid
    registration = _registration('PERIOD_CHANGE', 'change.points' if relation == 'pp' else 'change.abs')
    current_cell, base_cell = _cell(current), _cell(base)
    assumptions = list({(item['code'], item['text']): item for item in current.assumptions + base.assumptions}.values())
    if relation != 'pp' and base.interval.hi < 0:
        assumptions += [{'code': 'negative_base', 'text': '负基期按 (本期-基期)/|基期| 计算'}]
    return {'value': str(point), 'display': fmt_amount(point, '百分点' if relation == 'pp' else '%', 2 if relation == 'pp' else 1),
            'relation': relation, 'period': current.period, 'base_period': base.period,
            'definition_id': registration['definition_id'], 'formula': registration['formula'], 'interval': _interval(interval),
            'current': current_cell, 'base': base_cell, 'operands': _operands(current.used + base.used),
            'source_precision': current_cell['source_precision'] + base_cell['source_precision'],
            'evidence': _evidence(current.used + base.used),
            'rule_ids': list(dict.fromkeys([registration['rule_id']] + current_cell['rule_ids'] + base_cell['rule_ids'])),
            'rules': list({row['rule_id']: row for row in [registration] + current_cell['rules'] + base_cell['rules']}.values()),
            'definition_source_hash': registration['function_source_hash'], 'contract_hash': registration['contract_hash'],
            'assumptions': assumptions}


def _attach_comparison(cell: dict, calculation: dict) -> None:
    relation = calculation['relation']
    cell[relation], cell[relation + '_display'] = calculation['value'], calculation['display']
    cell[relation + '_calculation'] = calculation
    for key in ('interval', 'evidence', 'operands', 'rule_ids', 'formula', 'source_precision'):
        cell[relation + '_' + key] = calculation[key]
    if any(assumption['code'] == 'negative_base' for assumption in calculation['assumptions']):
        cell[relation + '_note'] = '基期为负，按 (本期-基期)/|基期|'


def _show(metric: str, value: Decimal) -> str:
    if metric in ('EPS', 'BPS'):
        return fmt_amount(value, '元', 2)
    if metric in ('CURRENT_RATIO', 'CASH_CONTENT'):
        return fmt_amount(value, '倍', 2)
    if metric in RATIOS or metric in EXTRA:
        return fmt_amount(value, '%', 2)
    return fmt_amount(value, '亿元' if abs(value) >= Decimal('1e8') else '万元', 2)


def analyze(facts: list, company: str, texts: list | None = None, corroborated: list | None = None,
            report_date: str = '', *, policy: str = 'standard', scope: str = '') -> dict:
    if policy not in ('standard', 'strict'):
        raise ValueError('分析策略须为 standard 或 strict')
    if scope not in ('', '合并', '母公司'):
        raise ValueError('分析口径须明确为合并或母公司')
    fb = FactBase(facts, report_date, company=company)
    facts = [f for f in fb.facts if not report_date or f.publish_date and f.publish_date <= report_date]
    selected_scope = scope or ('合并' if policy == 'standard' else '')
    periods = sorted({f.period for f in facts if f.dimension_kind == 'total' and f.source_relation == 'value'
                      and (not selected_scope or f.scope == selected_scope)
                      and f.account in ('REVENUE', 'TOTAL_REVENUE', 'NP_PARENT', 'ASSETS')})
    annual = [p for p in periods if p.endswith('Y')]
    table, signals, blocks = [], [], []
    assumptions = [{'code': 'scope', 'text': '未指定分析口径，按合并报表分析'}] if not scope and policy == 'standard' else []
    cache = {}

    def evaluate(metric, period, definition_id=''):
        key = (metric, period, definition_id)
        if key not in cache:
            try:
                if not selected_scope:
                    raise Missing(metric, period, '审慎分析需要明确指定合并或母公司口径', 'X3')
                cache[key] = _evaluate(fb, metric, period, scope=selected_scope, policy=policy, definition_id=definition_id)
                cache[key].assumptions = assumptions + cache[key].assumptions
            except Missing as exc:
                cache[key] = exc
        if isinstance(cache[key], Missing):
            raise cache[key]
        return cache[key]

    def block(metric, period, relation, exc, base_period=''):
        record = {'metric': metric, 'period': period, 'relation': relation, 'base_period': base_period,
                  'abstain_code': exc.code, 'reason': exc.why or '缺少可用事实',
                  'missing': {'account': exc.account, 'period': exc.period}, 'policy': policy, 'scope': selected_scope}
        if record not in blocks:
            blocks.append(record)

    def compare(item, current, base_period, relation):
        try:
            # Both periods must use the same registered method.
            base = evaluate(current.metric, base_period, current.definition_id)
            _attach_comparison(item, _comparison(current, base, relation, policy))
        except Missing as exc:
            block(current.metric, current.period, relation, exc, base_period)
        except ZeroDivisionError as exc:
            block(current.metric, current.period, relation, Missing(current.metric, base_period, str(exc), 'X2'), base_period)

    for metric in AMOUNTS + RATIOS + list(EXTRA):
        row = {'metric': metric, 'name': EXTRA[metric] if metric in EXTRA else account_name(metric), 'kind': 'amount' if metric in AMOUNTS else 'ratio', 'cells': {}}
        for period in periods:
            try:
                current = evaluate(metric, period)
            except Missing as exc:
                block(metric, period, 'value', exc)
                continue
            cell = _cell(current)
            base_period = shift_year(period, -1)
            if metric in AMOUNTS:
                compare(cell, current, base_period, 'yoy')
            elif metric not in ('EPS', 'BPS', 'CURRENT_RATIO', 'CASH_CONTENT'):
                compare(cell, current, base_period, 'pp')
            row['cells'][period] = cell
        if row['cells']:
            table.append(row)
    by_metric = {row['metric']: row for row in table}

    def cell(metric, period):
        return by_metric.get(metric, {}).get('cells', {}).get(period)

    def bounds(item, relation=''):
        value = item[relation + '_interval' if relation else 'interval']
        return IV(value['lower'], value['upper'], value['lower_closed'], value['upper_closed'])

    def add(code, title, severity, period, detail, metrics, nature='事实+规则推断'):
        evidence = list(dict.fromkeys(e for m in metrics for relation in ('', 'yoy_', 'pp_')
                                     for e in (cell(m, period) or {}).get(relation + 'evidence', [])))
        signals.append({'code': code, 'title': title, 'severity': severity, 'period': period, 'detail': detail, 'evidence': evidence, 'nature': nature,
                        'metrics': {m: (cell(m, period) or {}).get('display') for m in metrics}})
    for period in periods:
        label = period_label(period)
        nri, npp, cfo, np_ = cell('NRI_SHARE', period), cell('NP_PARENT', period), cell('CFO', period), cell('NP', period)
        if nri and bounds(nri).abs().lo >= Decimal('0.3'):
            add('S-NRI', '非经常性损益占比高', 'high' if bounds(nri).abs().lo >= Decimal('0.5') else 'medium', period,
                f'{label}非经常性损益占归母净利润 {nri["display"]}，扣非前后利润差异显著，盈利质量需结合非经常性项目明细判断。', ['NP_PARENT', 'NP_PARENT_ADJ', 'NRI_SHARE'])
        adj = cell('NP_PARENT_ADJ', period)
        if npp and adj and (bounds(npp).lo > 0 and bounds(adj).hi < 0 or bounds(npp).hi < 0 and bounds(adj).lo > 0):
            add('S-NRI-SIGN', '扣非前后盈亏方向相反', 'high', period, f'{label}归母净利润 {npp["display"]}，扣非后 {adj["display"]}，盈亏由非经常性损益决定。', ['NP_PARENT', 'NP_PARENT_ADJ'])
        if cfo and np_:
            c, n = bounds(cfo), bounds(np_)
            if n.hi < 0 and c.lo > 0:
                add('S-LOSS-CASH', '亏损但经营现金流为正（利润与现金流背离）', 'medium', period,
                    f'{label}净利润 {np_["display"]}，经营活动现金流量净额 {cfo["display"]}，方向相反；常见原因是折旧摊销等非付现成本或预收款增加，需结合现金流量表补充资料判断。', ['NP', 'CFO'])
            if n.lo > 0 and c.hi < 0:
                add('S-CASH-NEG', '盈利但经营现金流为负', 'high', period, f'{label}净利润 {np_["display"]}，经营活动现金流量净额 {cfo["display"]}。', ['NP', 'CFO'])
            elif n.lo > 0 and cell('CASH_CONTENT', period) and bounds(cell('CASH_CONTENT', period)).hi < Decimal('0.5'):
                add('S-CASH-LOW', '净现比偏低', 'medium', period, f'{label}经营现金流/净利润 = {cell("CASH_CONTENT", period)["display"]}，利润的现金含量不足 0.5。', ['NP', 'CFO', 'CASH_CONTENT'])
            if np_.get('yoy_interval') and cfo.get('yoy_interval') and bounds(np_, 'yoy').lo > Decimal('0.1') and bounds(cfo, 'yoy').hi < Decimal('-0.1'):
                add('S-DIVERGE', '利润与现金流背离', 'high', period, f'{label}净利润同比 {np_["yoy_display"]}，经营现金流同比 {cfo["yoy_display"]}，方向相反。', ['NP', 'CFO'])
        revenue = cell('REVENUE', period)
        for metric, title in (('AR', '应收账款增速高于营收'), ('INVENTORY', '存货增速高于营收')):
            item = cell(metric, period)
            if revenue and item and revenue.get('yoy_interval') and item.get('yoy_interval') and (bounds(item, 'yoy') - bounds(revenue, 'yoy')).lo > Decimal('0.2'):
                add('S-' + metric, title, 'medium', period, f'{label}{account_name(metric)}同比 {item["yoy_display"]}，营业收入同比 {revenue["yoy_display"]}，差距超过 20 个百分点。', [metric, 'REVENUE'])
        margin = cell('GROSS_MARGIN', period)
        if margin and margin.get('pp_interval') and bounds(margin, 'pp').abs().lo >= Decimal('0.05'):
            add('S-GM', '毛利率大幅波动', 'medium', period, f'{label}毛利率 {margin["display"]}，同比变动 {margin["pp_display"]}。', ['GROSS_MARGIN'])
        debt = cell('DEBT_RATIO', period)
        if debt and (bounds(debt).lo >= Decimal('0.7') or debt.get('pp_interval') and bounds(debt, 'pp').lo >= Decimal('0.1')):
            add('S-DEBT', '资产负债率偏高或快速上升', 'medium', period, f'{label}资产负债率 {debt["display"]}' + (f'，同比 {debt["pp_display"]}' if debt.get('pp') else '') + '。', ['DEBT_RATIO'])
    losses = [p for p in annual if (cell('NP_PARENT', p) and bounds(cell('NP_PARENT', p)).hi < 0)]
    if len(losses) >= 2 and any(shift_year(p, -1) in losses for p in losses):
        latest = max(losses)
        add('S-LOSS', '连续亏损', 'medium', latest, f'{"、".join(period_label(p) for p in losses)}归母净利润均为负，最近一期 {cell("NP_PARENT", latest)["display"]}。', ['NP_PARENT'], nature='事实')
    restated = sorted({(f.account, f.period) for f in facts if '调整后' in f.version})
    if restated:
        names = '、'.join(f'{period_label(p)}{account_name(a)}' for a, p in restated[:6])
        signals.append({'code': 'S-RESTATE', 'title': '比较期数据经追溯调整', 'severity': 'medium', 'period': restated[0][1], 'nature': '事实',
                        'detail': f'报告对 {names} 等披露了“调整前/调整后”两套数据，同比口径以调整后为准；引用旧版数字需注明。', 'evidence': [], 'metrics': {}})
    for page in texts or []:
        for keyword, title in (('会计政策变更', '会计政策变更'), ('会计估计变更', '会计估计变更'), ('前期差错更正', '前期会计差错更正')):
            position = page['text'].find(keyword)
            if position >= 0 and not any(s['code'] == 'S-POLICY' and s['title'] == title for s in signals):
                window = page['text'][position:position + 160].replace('\n', '')
                if '不适用' in window[:40] or '未发生' in window[:60] or '□适用' in window[:30]:
                    continue
                tail = window[len(keyword):len(keyword) + 40]
                if len(re.findall(r'[-—–\d,.\s]', tail)) > 0.6 * max(len(tail), 1) or not re.search(r'[\u4e00-\u9fff]{4,}', tail):
                    continue                      # 报表行标题（如权益变动表的“前期差错更正 - -”）不是披露事项
                signals.append({'code': 'S-POLICY', 'title': title, 'severity': 'low', 'period': '', 'nature': '事实（原文披露）',
                                'detail': f'{page["name"]} 第{page["page"]}页披露：{window[:110]}…', 'evidence': [f"{page['sha'][:12]}:p{page['page']}:text"], 'metrics': {}})
    years = sorted({int(f.period[:4]) for f in facts if f.period[4:] in ('Q1', 'Q2', 'Q3', 'Q4', 'H1', '9M')})
    quarterly = []
    for metric in ('REVENUE', 'NP_PARENT', 'NP_PARENT_ADJ', 'CFO'):
        cells = {}
        for year in years:
            for q in ('Q1', 'Q2', 'Q3', 'Q4'):
                period = f'{year}{q}'
                try:
                    current = evaluate(metric, period)
                except Missing as exc:
                    block(metric, period, 'value', exc)
                    continue
                item = _cell(current)
                if current.derived_notes:
                    item['derived'] = '；'.join(current.derived_notes)
                    item['display'] += '*'
                compare(item, current, previous_quarter(period), 'qoq')
                compare(item, current, f'{year - 1}{q}', 'yoy')
                cells[period] = item
        if cells:
            quarterly.append({'metric': metric, 'name': account_name(metric), 'cells': cells})
    check_facts = [fact for fact in facts if fact.scope == selected_scope]
    check_fb = FactBase(check_facts, report_date, company=company)
    checks = consistency_checks(check_fb, check_facts, corroborated or [], policy=policy) if selected_scope else []
    check_summary = {'total': len(checks), 'passed': sum(c['ok'] is True for c in checks), 'failed': sum(c['ok'] is False for c in checks),
                     'undetermined': sum(c['ok'] is None for c in checks),
                     'by_category': {k: sum(c['category'] == k for c in checks) for k in dict.fromkeys(c['category'] for c in checks)}}
    for check in checks:
        if check['ok'] is False:
            signals.append({'code': 'S-CHECK', 'title': '报告内部勾稽不一致', 'severity': 'high', 'period': check['period'], 'nature': '事实（勾稽计算）',
                            'detail': f"{check['name']}：{check['detail']}", 'evidence': check['evidence'][:4], 'metrics': {}})
    return {'company': company, 'policy': policy, 'scope': selected_scope, 'report_date': report_date,
            'assumptions': assumptions, 'blocks': blocks, 'periods': periods, 'annual_periods': annual,
            'metrics': table, 'quarterly': quarterly, 'signals': signals,
            'checks': checks, 'check_summary': check_summary, 'highlights': highlights(by_metric, periods, checks, signals),
            'facts': len(facts), 'note': '指标由披露事实按登记公式计算（带*的单季数由累计数相减得到）；勾稽检查按披露精度做区间比较；'
                                         '信号为规则触发的提示，属推断，不构成舞弊或投资结论。'}


def _money(value: Decimal) -> str:
    unit, decimals = best_money_unit(value)
    return fmt_amount(value, unit, decimals)


# (名称, 合计科目, ((科目, 符号), …), 适用口径, 可缺省为0的科目)
IDENTITIES = [
    ('资产总计 = 负债合计 + 所有者权益合计', 'ASSETS', (('LIABILITIES', 1), ('EQUITY', 1)), ('合并', '母公司'), ()),
    ('负债和所有者权益总计 = 资产总计', 'LIABILITIES_EQUITY', (('ASSETS', 1),), ('合并', '母公司'), ()),
    ('资产总计 = 流动资产合计 + 非流动资产合计', 'ASSETS', (('CURRENT_ASSETS', 1), ('NONCURRENT_ASSETS', 1)), ('合并', '母公司'), ()),
    ('负债合计 = 流动负债合计 + 非流动负债合计', 'LIABILITIES', (('CURRENT_LIABILITIES', 1), ('NONCURRENT_LIABILITIES', 1)), ('合并', '母公司'), ()),
    ('所有者权益合计 = 归母所有者权益 + 少数股东权益', 'EQUITY', (('EQUITY_PARENT', 1), ('EQUITY_MINORITY', 1)), ('合并',), ('EQUITY_MINORITY',)),
    ('净利润 = 归母净利润 + 少数股东损益', 'NP', (('NP_PARENT', 1), ('NP_MINORITY', 1)), ('合并',), ('NP_MINORITY',)),
    ('净利润 = 利润总额 − 所得税费用', 'NP', (('PROFIT_TOTAL', 1), ('INCOME_TAX_EXPENSE', -1)), ('合并', '母公司'), ()),
    ('利润总额 = 营业利润 + 营业外收入 − 营业外支出', 'PROFIT_TOTAL', (('OPERATING_PROFIT', 1), ('NONOPERATING_INCOME', 1), ('NONOPERATING_EXPENSE', -1)),
     ('合并', '母公司'), ('NONOPERATING_INCOME', 'NONOPERATING_EXPENSE')),
    ('现金及现金等价物净增加额 = 经营 + 投资 + 筹资活动现金流量净额 + 汇率变动影响', 'CASH_EQUIVALENTS_CHANGE',
     (('CFO', 1), ('CFI', 1), ('CFF', 1), ('FX_CASH_EFFECT', 1)), ('合并', '母公司'), ('FX_CASH_EFFECT',)),
    ('期末现金及现金等价物余额 = 期初余额 + 净增加额', 'CASH_EQUIVALENTS', (('CASH_EQUIVALENTS_OPENING', 1), ('CASH_EQUIVALENTS_CHANGE', 1)), ('合并', '母公司'), ()),
    ('非经常性损益净额 = 归母净利润 − 扣非归母净利润', 'NONRECURRING_NET', (('NP_PARENT', 1), ('NP_PARENT_ADJ', -1)), ('合并',), ()),
]
CROSS_ACCOUNTS = ('REVENUE', 'TOTAL_REVENUE', 'NP', 'NP_PARENT', 'NP_PARENT_ADJ', 'CFO', 'ASSETS', 'LIABILITIES', 'EQUITY', 'EQUITY_PARENT',
                  'EPS', 'EPS_DILUTED', 'CASH_FUNDS')


def consistency_checks(fb: FactBase, facts: list, corroborated: list, *, policy: str = 'standard') -> list[dict]:
    """报告内部勾稽检查（选题二“财务勾稽关系”）：报表恒等式、披露同比与重算、四季度之和等于全年、
    跨表一致、分部合计、披露比率与重算。全部按披露精度做 Decimal 区间比较；ok=None 表示材料不足无法判定。"""
    checks: list[dict] = []

    def first(account, period, scope='合并'):
        try:
            rows = fb.get(account, period, scope)
        except Missing:
            return None
        return rows[0] if rows and not (policy == 'strict' and '横线视为零' in (rows[0].label or '')) else None

    def record(category, name, period, scope, ok, detail, used, *, computed=None, disclosed=None, formula='', assumptions=()):
        checks.append({'category': category, 'name': name, 'period': period, 'scope': scope, 'ok': ok, 'detail': detail,
                       'evidence': _evidence(used), 'operands': _operands(used), 'formula': formula or name,
                       'computed_interval': _interval(computed) if computed is not None else None,
                       'disclosed_interval': _interval(disclosed) if disclosed is not None else None,
                       'policy': policy, 'assumptions': list(assumptions)})

    periods_by_scope: dict[str, set] = {}
    for fact in facts:
        if fact.dimension_kind == 'total' and fact.source_relation == 'value':
            periods_by_scope.setdefault(fact.scope, set()).add(fact.period)
    for account, period, scope, dimension_kind, relation in fb.index:
        if dimension_kind != 'total':
            continue
        try:
            fb.get(account, period, scope, relation=relation)
        except Missing as exc:
            used = fb.index[(account, period, scope, dimension_kind, relation)]
            record('admission', f'{account_name(account)}事实准入', period, scope,
                   False if exc.code == 'X4' else None, exc.why, used)
    # 1. 报表恒等式
    for name, total_account, parts, scopes, optional in IDENTITIES:
        for scope in scopes:
            for period in sorted(periods_by_scope.get(scope, ())):
                total = first(total_account, period, scope)
                if total is None:
                    continue
                acc, used, assumed, complete = IV(0), [total], [], True
                for account, sign in parts:
                    fact = first(account, period, scope)
                    if fact is None:
                        if account in optional:
                            assumed.append(account_name(account))
                            continue
                        complete = False
                        break
                    used.append(fact)
                    acc = acc + fact_iv(fact) * sign
                if not complete or len(used) < 2:
                    continue
                ok = fact_iv(total).intersects(acc)
                note = f"（{'、'.join(assumed)}未单列，按0计）" if assumed else ''
                if assumed and (not ok or policy == 'strict'):
                    ok = None                         # 缺省项按0计后不平：多为该项未被抽取，不下“不一致”结论
                if assumed and policy == 'strict':
                    note = '（审慎模式：未披露的明细不按零推定）'
                record('identity', name, period, scope, ok, f'{account_name(total_account)} {_money(fact_iv(total).mid)}；右侧合计 {_money(acc.mid)}{note}', used,
                       computed=acc, disclosed=fact_iv(total))
    # 2. 披露同比 vs 按金额重算
    for (account, period, scope, dim_kind, relation), rows in sorted(fb.index.items()):
        if relation != 'yoy' or dim_kind != 'total' or scope != '合并' or not rows:
            continue
        admitted = _admitted_rows(fb, account, period, scope, relation='yoy')
        if not admitted:
            continue
        disclosed = admitted[0]
        current, bases = first(account, period), _admitted_rows(fb, account, shift_year(period, -1))
        if current is None or not bases:
            continue
        candidates = []
        blocked_bases = []
        for base in bases:
            base_iv = fact_iv(base)
            if base_iv.lo <= 0 <= base_iv.hi:
                blocked_bases.append('基期区间为零或跨零')
                continue
            if policy == 'strict' and base_iv.hi < 0:
                blocked_bases.append('审慎模式：负基期增速缺少明示授权方法')
                continue
            candidates.append((relative_change(fact_iv(current), base_iv), base, 'absolute'))
            if base_iv.hi < 0:
                candidates.append(((fact_iv(current) - base_iv) / base_iv, base, 'signed'))
        if not candidates:
            record('yoy', f'{account_name(account)}同比（披露值与按金额重算）', period, scope, None,
                   '；'.join(dict.fromkeys(blocked_bases)) or '缺少可执行的增长率方法', [disclosed, current] + bases,
                   disclosed=fact_iv(disclosed))
            continue
        target = fact_iv(disclosed)
        hit = next((item for item in candidates if item[0].intersects(target)), None)
        shown = hit or candidates[0]
        record('yoy', f'{account_name(account)}同比（披露值与按金额重算）', period, scope, hit is not None,
               f'披露 {fmt_amount(target.mid, "%", 2)}，重算 {fmt_amount(shown[0].mid, "%", 2)}' + ('（基期为负，按带符号基期相除与披露一致）' if hit and shown[2] == 'signed' else ''),
               [disclosed, current, shown[1]], computed=shown[0], disclosed=target,
               formula='(本期-基期)/基期' if shown[2] == 'signed' else '(本期-基期)/|基期|',
               assumptions=[{'code': 'negative_base', 'text': '负基期采用' + ('带符号基期' if shown[2] == 'signed' else '绝对值基期') + '规则'}]
               if shown[1].value < 0 else [])
    # 3. 四个季度之和 = 全年
    years = sorted({int(f.period[:4]) for f in facts if f.period[4:] in ('Q1', 'Q2', 'Q3', 'Q4')})
    for account in ('REVENUE', 'NP_PARENT', 'NP_PARENT_ADJ', 'CFO'):
        for year in years:
            quarters = [first(account, f'{year}Q{q}') for q in range(1, 5)]
            annual = first(account, f'{year}Y')
            if annual is None or not all(quarters):
                continue
            total = IV(0)
            for fact in quarters:
                total = total + fact_iv(fact)
            record('quarter', f'四个季度{account_name(account)}之和 = 全年', f'{year}Y', '合并', fact_iv(annual).intersects(total),
                   f'季度合计 {_money(total.mid)}，全年 {_money(fact_iv(annual).mid)}', [annual] + quarters)
    # 4. 跨表一致：同一数值在主要会计数据表与报表中重复出现（抽取时互证），或同一文档同一版本出现不同数值
    seen = set()
    for row in corroborated:
        if row['account'] not in CROSS_ACCOUNTS or row['scope'] != '合并':
            continue
        key = (row['account'], row['period'], row.get('source_doc', ''))
        if key in seen:
            continue
        supporting = [f for f in _admitted_rows(fb, row['account'], row['period']) if str(f.value) == row['value']]
        if not supporting:
            continue
        seen.add(key)
        record('cross', f"{account_name(row['account'])}在多张表中一致", row['period'], row['scope'], True,
               '同一数值见于：' + '、'.join(row['tables'][:3]), supporting[:1])
    for account in CROSS_ACCOUNTS:
        for (acc, period, scope, dim_kind, relation), rows in fb.index.items():
            if acc != account or scope != '合并' or dim_kind != 'total' or relation != 'value':
                continue
            groups: dict = {}
            for fact in rows:
                if '横线视为零' in (fact.label or ''):
                    continue
                if Decimal(str(fact.parse_conf)) < Decimal('.95') or fb.report_date and (not fact.publish_date or fact.publish_date > fb.report_date):
                    continue
                groups.setdefault((fact.source_doc, fact.version), []).append(fact)
            for (_doc, _version), members in groups.items():
                values = {fact.value for fact in members}
                if len(values) < 2:
                    continue
                consistent = all(fact_iv(members[0]).intersects(fact_iv(other)) for other in members[1:])
                record('cross', f'{account_name(account)}在报告不同位置的数值一致', period, scope, consistent,
                       '；'.join(f'{fact.table_label or fact.source_type} {_money(fact.value)}' for fact in members[:3]), members[:3])
    # 5. 分部合计 = 营业收入（分部表可能只列主营业务，未覆盖时记为“无法判定”而非不一致）
    segment_groups: dict = {}
    for fact in facts:
        if fact.account == 'REVENUE' and fact.dimension_kind != 'total' and fact.source_relation == 'value':
            try:
                if fact not in fb.get(fact.account, fact.period, fact.scope, (fact.dimension_kind, fact.dimension_id)):
                    continue
            except Missing:
                continue
            segment_groups.setdefault((fact.source_doc, fact.period, fact.dimension_kind, fact.table_label), []).append(fact)
    for (_doc, period, kind, table_label), members in sorted(segment_groups.items(), key=lambda kv: str(kv[0])):
        if len(members) < 2:
            continue
        total = IV(0)
        for fact in members:
            total = total + fact_iv(fact)
        whole = first('REVENUE', period) or first('MAIN_REVENUE', period)
        if whole is None:
            continue
        main = first('MAIN_REVENUE', period)
        ok = fact_iv(whole).intersects(total) or (main is not None and fact_iv(main).intersects(total))
        record('segment', f"分{'地区' if kind == 'geography' else '业务/产品'}收入合计 = 营业收入", period, '合并', True if ok else None,
               f'分部合计 {_money(total.mid)}，营业收入 {_money(fact_iv(whole).mid)}' + ('' if ok else '（分部表可能只列示主营业务或部分分部）'), [whole] + members[:3])
    # 6. 披露比率与按金额重算
    for metric, name in (('DEBT_RATIO', '资产负债率'), ('GROSS_MARGIN', '毛利率')):
        for (acc, period, scope, dim_kind, relation), rows in fb.index.items():
            if acc != metric or scope != '合并' or dim_kind != 'total' or relation != 'value' or not rows:
                continue
            try:
                admitted = fb.get(metric, period, scope)
                if not admitted:
                    continue
                disclosed = admitted[0]
                computed = _evaluate(fb, metric, period, scope=scope, policy=policy,
                                     definition_id=definitions_for(metric)[0][0])
            except Missing:
                continue
            record('ratio', f'披露{name}与按报表金额重算', period, scope, fact_iv(disclosed).intersects(computed.interval),
                   f'披露 {fmt_amount(disclosed.value, "%", 2)}，重算 {fmt_amount(computed.interval.mid, "%", 2)}',
                   [disclosed] + computed.used, computed=computed.interval, disclosed=fact_iv(disclosed), formula=computed.label)
    order = {'identity': 0, 'yoy': 1, 'quarter': 2, 'cross': 3, 'segment': 4, 'ratio': 5}
    checks.sort(key=lambda c: (c['ok'] is not False, order.get(c['category'], 9), c['period'], c['scope'], c['name']))
    return checks


def highlights(by_metric: dict, periods: list, checks: list, signals: list) -> list[str]:
    """要点：只用结构化结果拼句，数字全部来自指标表与勾稽检查。"""
    if not periods:
        return []
    annual = [p for p in periods if p.endswith('Y')]
    latest = max(annual) if annual else max(periods)
    label = period_label(latest)

    def cell(metric):
        return (by_metric.get(metric) or {}).get('cells', {}).get(latest)
    lines = []
    for metric, name in (('REVENUE', '营业收入'), ('NP_PARENT', '归母净利润'), ('NP_PARENT_ADJ', '扣非归母净利润'), ('CFO', '经营活动现金流量净额')):
        item = cell(metric)
        if item:
            lines.append(f"{label}{name}{item['display']}" + (f"，同比{item['yoy_display']}" if item.get('yoy_display') else '')
                         + ('（基期为负，按|基期|计算）' if item.get('yoy_note') else '') + '。')
    margin = cell('GROSS_MARGIN')
    if margin:
        lines.append(f"毛利率{margin['display']}" + (f"，同比变动{margin['pp_display']}" if margin.get('pp_display') else '') + '。')
    failed = [c for c in checks if c['ok'] is False]
    lines.append(f'执行{len(checks)}项报告内部勾稽检查，{len(failed)}项不一致' + ('：' + '；'.join(c['name'] for c in failed[:3]) if failed else '') + '。')
    titles = list(dict.fromkeys(s['title'] for s in signals if s.get('code') != 'S-CHECK'))
    if titles:
        lines.append('需关注：' + '；'.join(titles[:5]) + '。')
    return lines
