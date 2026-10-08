from __future__ import annotations

"""Evidence-bound financial analysis; model values never enter this module."""
from decimal import Decimal
from typing import Iterable
import re

from .compute import yoy
from .facts import FactStore, _date_key
from .decimal_math import fact_interval, DecimalInterval, resolution
from .mentions import previous_period
from .rules import execute_rule, execute_rule_interval, intervals_compatible
from .presentation import metric_display_metadata
from .metrics import METRIC_SPECS, RATIO_METRICS, TURNOVER_METRICS


def gross_margin(revenue: Decimal, cost: Decimal) -> Decimal | None:
    return execute_rule('FIN-gross-margin', {'REVENUE': revenue, 'COST': cost})


def net_margin(net_profit: Decimal, revenue: Decimal) -> Decimal | None:
    return execute_rule('FIN-net-margin', {'NP': net_profit, 'REVENUE': revenue})


def debt_ratio(total_liabilities: Decimal, total_assets: Decimal) -> Decimal | None:
    return execute_rule('FIN-debt-ratio', {'LIABILITIES': total_liabilities, 'ASSETS': total_assets})


def cash_content(operating_cash_flow: Decimal, net_profit: Decimal) -> Decimal | None:
    return execute_rule('FIN-cash-content', {'CFO': operating_cash_flow, 'NP': net_profit})


def non_recurring_adjustment(net_profit: Decimal, non_recurring_profit: Decimal,
                            tax_effect: Decimal | None = None,
                            minority_effect: Decimal | None = None) -> Decimal | None:
    """NP_PARENT-(NRI_TOTAL-NRI_TAX_EFFECT-NRI_MINORITY).

    An explicitly disclosed zero is valid; an absent component is not zero.
    The former ambiguous two/three-argument estimate no longer yields a value.
    """
    if tax_effect is None or minority_effect is None:
        return None
    return execute_rule('FIN-non-recurring', {'NP_PARENT': net_profit, 'NRI_TOTAL': non_recurring_profit,
                                             'NRI_TAX_EFFECT': tax_effect, 'NRI_MINORITY': minority_effect})


def change_signal(current: Decimal, prior: Decimal) -> dict[str, str | None]:
    change = yoy(current, prior)
    return {"direction": "up" if current > prior else "down" if current < prior else "flat", "rate": str(change) if change is not None else None}


def _period_key(period: str) -> tuple:
    return (period[:4], {"Q1": 1, "H1": 2, "Q2": 2, "Q3": 3, "9M": 3, "Q4": 4, "Y": 4}.get(period[4:], 0), period[4:])


def _adjacent(first: str, second: str) -> bool:
    if first[:4].isdigit() and second[:4].isdigit() and first[4:].startswith('Q') and second[4:].startswith('Q'):
        return int(second[:4])*4+int(second[5:]) == int(first[:4])*4+int(first[5:])+1
    return (first[4:] == second[4:] and first[:4].isdigit() and second[:4].isdigit()
            and int(second[:4]) == int(first[:4]) + 1)


def analyze_financials(store: FactStore, company: str, periods: Iterable[str], report_date: str | None,
                      scope: str = "合并", *, confidence_threshold: str = "0.95",
                      cash_threshold: str = "0.5", non_recurring_threshold: str = "0.30",
                      disclosures: Iterable[dict] = ()) -> dict:
    """Produce metrics/signals solely from registered, dated source facts.

    Team thresholds describe review signals, not regulatory or investment
    conclusions. Every computed value carries formula, operands and evidence.
    """
    cash_limit, nr_limit = Decimal(cash_threshold), Decimal(non_recurring_threshold)
    if not all(x.is_finite() and 0 <= x <= 1 for x in (cash_limit, nr_limit)):
        raise ValueError("分析阈值须为零至一的有限小数")
    from .financial_derivations import supported_quarter_periods
    requested_periods = set(periods)
    derived_periods = supported_quarter_periods(store, company, requested_periods, scope)
    periods = sorted(requested_periods | derived_periods, key=_period_key)
    metrics, signals, events = [], [], []
    rows: dict[str, dict] = {}
    lookup_issues: dict[tuple, tuple[str, str]] = {}
    lookup_outcomes = {}
    direct = ("REVENUE", "COST", "NP", "NP_PARENT", "NP_PARENT_ADJ", "CFO", "ASSETS", "LIABILITIES", "EQUITY_PARENT", "EPS", "EPS_DILUTED", "ROE")
    def lookup(account, period, source=None):
        key = (account, period, source)
        if not report_date:
            lookup_issues[key] = ('X5', '缺少回溯披露日期，不能采用未截断的财报事实')
            return None
        nr = "扣非" if account == "NP_PARENT_ADJ" else "未扣非"
        outcome = store.query_outcome(company, account, period, scope, nr,
                           cutoff=report_date, strategy="same_report" if source else "latest_as_of",
                           source_doc=source, min_confidence=confidence_threshold)
        lookup_outcomes[key] = outcome
        if outcome.fact is None:
            lookup_issues[key] = (outcome.abstention_code, outcome.reason)
        return outcome.fact
    def missing_problem(requests):
        issues = [lookup_issues[request] for request in requests if request in lookup_issues]
        return next((issue for issue in issues if issue[0] in {'X5', 'X4'}),
                    issues[0] if issues else ('X1', '缺少同一报告的必要操作数'))
    def source_problem(fact):
        if fact and not (fact.source_doc or fact.source_path):
            return ('X4', '操作数缺少报告来源标识，不能确认同一披露版本')
        return None
    def calculated(rule_id, mapped_facts):
        if not all(mapped_facts.values()):
            return None, None
        interval = execute_rule_interval(rule_id, {key: fact_interval(fact) for key, fact in mapped_facts.items()})
        if interval is None:
            return None, None
        return execute_rule(rule_id, {key: fact.value for key, fact in mapped_facts.items()}), interval
    def record(period, metric, value, operands, formula, code=None, reason=None, *, interval=None, rule_id=None):
        ratio_metric = metric.endswith('_YOY') or metric in RATIO_METRICS|{'ROE', 'NON_RECURRING_SHARE'}
        item = {"period": period, "metric": metric, "status": "verified" if value is not None else "abstained",
                "value": str(value) if value is not None else None, "formula": formula,
                "unit": '倍' if metric in TURNOVER_METRICS|{'CURRENT_RATIO','QUICK_RATIO'} else '比例' if ratio_metric else '元/股' if metric in {'EPS', 'EPS_DILUTED'} else '元',
                "operands": [f.to_dict() for f in operands if f],
                "evidence": list(dict.fromkeys(e for f in operands if f for e in f.evidence)),
                "abstention_code": ('X5' if not report_date else code) if value is None else None,
                "reason": '缺少回溯披露日期，不能采用未截断的财报事实' if not report_date else reason,
                "company": company, "scope": scope,
                "expected_interval": interval.to_dict() if interval is not None else None,
                "source_precision": [{"fact_id": f.fact_id, "display_unit": f.display_unit,
                                      "decimals": f.decimals, "resolution": str(resolution(f.display_unit, f.decimals))}
                                     for f in operands if f],
                "verification_basis": 'source_disclosure' if formula == '原报告披露值' else 'authorized_formula_interval_propagation',
                "precision_note": '数值以原披露精度为依据；授权公式区间由操作数区间传播，不增加独立核实的精确位数' if value is not None else None,
                "rule_id": rule_id}
        item.update(metric_display_metadata(item))
        metrics.append(item)
        return item
    for period in periods:
        facts = {metric: lookup(metric, period) for metric in direct}
        rows[period] = facts
        for metric, fact in facts.items():
            if fact is None and (metric == 'EPS' or (metric in {'REVENUE', 'COST', 'NP', 'NP_PARENT', 'NP_PARENT_ADJ', 'CFO'}
                                                     and period[4:] in {'Q2', 'Q3', 'Q4'})):
                from .financial_derivations import derive_metric
                derived = derive_metric(store, company, metric, period, scope,
                                        '扣非' if metric == 'NP_PARENT_ADJ' else '未扣非', report_date,
                                        direct_outcome=lookup_outcomes.get((metric, period, None)),
                                        min_confidence=confidence_threshold, basic_eps_definition=metric == 'EPS')
                item = record(period, metric, derived.value, derived.operands, derived.formula or '无适用授权派生公式',
                              derived.abstention_code, derived.reason, interval=derived.interval,
                              rule_id=derived.rule.get('id'))
                item['rule_metadata'] = derived.rule
                item['calculation_steps'] = derived.calculation_steps
                continue
            code, reason = missing_problem([(metric, period, None)]) if fact is None else (None, None)
            record(period, metric, fact.value if fact else None, [fact], "原报告披露值",
                   code, reason, interval=fact_interval(fact) if fact else None,
                   rule_id='FIN-ROE' if metric == 'ROE' else 'E1-value')
        # Annual/half-year/single-quarter comparisons use the same period
        # kind in the prior year, and the current disclosure's comparative
        # numbers. An older unadjusted report cannot replace a missing base.
        for account in ('REVENUE', 'NP_PARENT', 'CFO'):
            current = facts[account]
            base_period = previous_period(period, 'yoy')
            document = (current.source_doc or current.source_path) if current else None
            base = lookup(account, base_period, document) if document and base_period else None
            value, interval = calculated('E5-yoy', {'current': current, 'base': base})
            if not current or not base:
                code, reason = source_problem(current) or missing_problem(
                    [(account, period, None), (account, base_period, document)])
            else:
                code, reason = 'X2', '同比基数为零或负值，登记公式不适用'
            item = record(period, account + '_YOY', value, [current, base], '(current-base)/base',
                          code, None if value is not None else reason, interval=interval, rule_id='E5-yoy')
            item.update(base_period=base_period, rule_id='E5-yoy')
        formulae = (("GROSS_MARGIN", ("REVENUE", "COST"), 'FIN-gross-margin', "(REVENUE-COST)/REVENUE"),
                    ("NET_MARGIN", ("NP", "REVENUE"), 'FIN-net-margin', "NP/REVENUE"),
                    ("DEBT_RATIO", ("LIABILITIES", "ASSETS"), 'FIN-debt-ratio', "LIABILITIES/ASSETS"),
                    ("CASH_CONTENT", ("CFO", "NP"), 'FIN-cash-content', "CFO/NP"))
        for metric, names, rule_id, formula in formulae:
            disclosed = lookup(metric, period)
            if disclosed:
                item = record(period, metric, disclosed.value, [disclosed], '原报告披露值',
                              interval=fact_interval(disclosed), rule_id='E1-value')
                if metric == 'CASH_CONTENT':
                    rows[period]['cash_record'] = item
                continue
            direct_issue = lookup_issues.get((metric, period, None))
            if direct_issue and direct_issue[0] in {'X4', 'X5'}:
                record(period, metric, None, [], formula, *direct_issue, rule_id=rule_id)
                continue
            first = facts[names[0]]
            document = (first.source_doc or first.source_path) if first else None
            operands = [first] + [lookup(name, period, document) if document else None for name in names[1:]]
            value, interval = calculated(rule_id, dict(zip(names, operands)))
            code, reason = ('X2', '登记公式要求分母为正') if all(operands) else (
                source_problem(first) or missing_problem(
                    [(names[0], period, None)] + [(name, period, document) for name in names[1:]]))
            item = record(period, metric, value, operands, formula, code, None if value is not None else reason,
                          interval=interval, rule_id=rule_id)
            if metric == "CASH_CONTENT":
                rows[period]["cash_record"] = item
        # Extended cards explicitly state their analytical method. This does
        # not resolve an ambiguous broker claim; only this analysis report
        # adopts and displays the registered definition.
        from .financial_derivations import derive_metric
        for spec in METRIC_SPECS.values():
            if not spec.analysis or spec.account in {'GROSS_MARGIN','NET_MARGIN','DEBT_RATIO','CASH_CONTENT'}:
                continue
            definition=spec.definition_id or ('metric:'+spec.account.lower().replace('_','-')) if spec.definition_required else ''
            direct_metric=store.query_outcome(company,spec.account,period,scope,cutoff=report_date,
                          min_confidence=confidence_threshold,definition_id=definition) if report_date else None
            if direct_metric and direct_metric.fact:
                fact=direct_metric.fact
                record(period,spec.account,fact.value,[fact],'原报告披露值',interval=fact_interval(fact),rule_id='E1-value')
                continue
            derived=derive_metric(store,company,spec.account,period,scope,'未扣非',report_date,
                        definition_id=definition,
                        min_confidence=confidence_threshold)
            item=record(period,spec.account,derived.value,derived.operands,derived.formula or spec.rule_id,
                          derived.abstention_code,derived.reason,interval=derived.interval,rule_id=spec.rule_id)
            item.update(rule_metadata=derived.rule,calculation_steps=derived.calculation_steps,definition_id=spec.definition_id,
                        definition_label=spec.label,blockers=derived.blockers)
        anchor = facts["NP_PARENT"]
        source = (anchor.source_doc or anchor.source_path) if anchor else None
        components = [anchor] + [lookup(name, period, source) if source else None for name in ("NRI_TOTAL", "NRI_TAX_EFFECT", "NRI_MINORITY")]
        adjusted, adjusted_interval = calculated('FIN-non-recurring', dict(zip(
            ('NP_PARENT', 'NRI_TOTAL', 'NRI_TAX_EFFECT', 'NRI_MINORITY'), components)))
        code, reason = (None, None) if adjusted is not None else (source_problem(anchor) or missing_problem(
            [('NP_PARENT', period, None)] + [(name, period, source) for name in
                                           ('NRI_TOTAL', 'NRI_TAX_EFFECT', 'NRI_MINORITY')]))
        nr_record = record(period, "NP_PARENT_RECOMPUTED", adjusted, components,
                           "NP_PARENT-(NRI_TOTAL-NRI_TAX_EFFECT-NRI_MINORITY)", code,
                           None if adjusted is not None else reason + '；扣非重算缺项不能当零',
                           interval=adjusted_interval, rule_id='FIN-non-recurring')
        disclosed = lookup('NP_PARENT_ADJ', period, source) if source else None
        if adjusted is not None and disclosed:
            nr_record["disclosed_value"] = str(disclosed.value)
            disclosed_display = metric_display_metadata({'metric':'NP_PARENT_ADJ', 'value':str(disclosed.value),
                'verification_basis':'source_disclosure', 'operands':[disclosed.to_dict()]})
            nr_record['disclosed_display_value'] = disclosed_display['display_value']
            nr_record['disclosed_display_precision_note'] = disclosed_display['display_precision_note']
            nr_record['disclosed_interval'] = fact_interval(disclosed).to_dict()
            nr_record["match_disclosure"] = intervals_compatible(adjusted_interval, fact_interval(disclosed))
            nr_record['disclosure_check_status'] = 'compatible' if nr_record['match_disclosure'] else 'conflict'
            nr_record["operands"].append(disclosed.to_dict())
            nr_record["evidence"] = list(dict.fromkeys(nr_record["evidence"] + list(disclosed.evidence)))
        elif adjusted is not None:
            nr_record['disclosure_check_status'] = 'abstained'
            nr_record['disclosure_abstention_code'], nr_record['disclosure_reason'] = missing_problem([('NP_PARENT_ADJ', period, source)])
        if anchor and adjusted is not None and fact_interval(anchor).lower > 0:
            share = execute_rule('FIN-non-recurring-share', {'NP_PARENT': anchor.value, 'NP_PARENT_ADJ': adjusted})
            share_interval = execute_rule_interval('FIN-non-recurring-share', {'NP_PARENT': fact_interval(anchor), 'NP_PARENT_ADJ': adjusted_interval})
            record(period, "NON_RECURRING_SHARE", share, components, "(NP_PARENT-NP_PARENT_RECOMPUTED)/NP_PARENT",
                   interval=share_interval, rule_id='FIN-non-recurring-share')
            if (share_interval.absolute().lower > nr_limit and execute_rule('FIN-high-nri-share',
                {'NP_PARENT': anchor.value, 'NP_PARENT_ADJ': adjusted, 'threshold': nr_limit}) is True):
                signals.append({"code": "A5", "kind": "non_recurring_share", "periods": [period], "value": str(share),
                                "threshold": str(nr_limit), "parameter_source": "team_parameter",
                                "expected_interval": share_interval.to_dict(),
                                "evidence": nr_record["evidence"], "rule_id": "FIN-high-nri-share",
                                "metric_rule_id": "FIN-non-recurring-share"})
        versions = store.versions(company, "REVENUE", period, scope)
        visible = [f for f in versions if report_date and _date_key(f.publish_date)[0] and f.publish_date <= report_date
                   and Decimal(f.parse_conf) >= Decimal(confidence_threshold)]
        if any(f.version not in {"原披露", "original"} for f in visible):
            events.append({"kind": "disclosure_version_change", "period": period,
                           "classification": "cause_requires_note_review", "reason": "版本变化本身不能区分政策、估计或差错原因",
                           "versions": [f.version for f in visible], "evidence": list(dict.fromkeys(e for f in visible for e in f.evidence))})
    low_cash, diverged = [], []
    for index, period in enumerate(periods):
        row = rows[period]
        cash_row = row.get("cash_record") or {}
        cash_bounds = cash_row.get('expected_interval') or {}
        under_threshold = (cash_bounds and (Decimal(cash_bounds['upper']) < cash_limit
                           or Decimal(cash_bounds['upper']) == cash_limit and not cash_bounds['upper_closed']))
        if cash_row.get("value") is not None and under_threshold:
            if low_cash and not _adjacent(low_cash[-1], period):
                low_cash = []
            low_cash.append(period)
        else:
            low_cash = []
        if len(low_cash) == 2:
            signals.append({"code": "A5", "kind": "low_cash_content", "periods": low_cash.copy(), "threshold": str(cash_limit),
                            "parameter_source": "team_parameter", "rule_id": "FIN-low-cash-content",
                            "evidence": list(dict.fromkeys(e for p in low_cash for e in rows[p]["cash_record"]["evidence"]))})
        if index == 0:
            continue
        previous = periods[index-1]
        operands = [rows[p].get(metric) for p in (previous, period) for metric in ("NP", "CFO")]
        changes = [fact_interval(operands[i+2]).subtract(fact_interval(operands[i])) for i in range(2)] if all(operands) else []
        opposite = bool(changes and ((changes[0].lower > 0 and changes[1].upper < 0)
                                     or (changes[1].lower > 0 and changes[0].upper < 0))
                        and execute_rule('FIN-divergence', {'NP_BASE': operands[0].value, 'CFO_BASE': operands[1].value,
                            'NP_CURRENT': operands[2].value, 'CFO_CURRENT': operands[3].value}) is True)
        if opposite and _adjacent(previous, period):
            diverged.append(period)
        else:
            diverged = []
        if len(diverged) in {2, 3}:
            relevant = [periods[periods.index(diverged[0])-1], *diverged]
            signals.append({"code": "A5", "kind": "profit_cashflow_divergence", "periods": relevant,
                            "consecutive_changes": len(diverged), "severity": "elevated" if len(diverged) >= 3 else "notice",
                            "rule_id": "FIN-divergence", "parameter_source": "team_parameter",
                            "evidence": list(dict.fromkeys(e for p in relevant for metric in ("NP", "CFO") for e in (rows[p][metric].evidence if rows[p].get(metric) else ())))})
    if report_date:
        events.extend(extract_accounting_events(disclosures, company, report_date))
    for signal in signals:
        if signal.get('value') is not None:
            signal.update(metric_display_metadata(dict(signal, metric='NON_RECURRING_SHARE', value_unit='无量纲比例')))
    return {"schema_version": "financial-analysis.v2", "company": company, "periods": periods, "report_date": report_date,
            "requested_periods": sorted(requested_periods, key=_period_key),
            "derived_quarter_periods": sorted(derived_periods - requested_periods, key=_period_key),
            "scope": scope, "metrics": metrics, "signals": signals, "events": events,
            "parameters": {"cash_threshold": str(cash_limit), "non_recurring_threshold": str(nr_limit),
                           "confidence_threshold": str(confidence_threshold), "source": "team_parameter"}}


def extract_accounting_events(disclosures: Iterable[dict], company: str, report_date: str) -> list[dict]:
    """Recognize explicit disclosure passages, never infer change from a title.

    Records must come from the frozen corpus with document hash and evidence.
    This identifies review events, not the correctness of an accounting policy.
    """
    from datetime import date
    cutoff = date.fromisoformat(report_date)
    events=[]
    identities={}
    rules=(('accounting_policy_change','会计政策变更','CHANGE-policy','retrospective_subject_to_disclosed_exceptions'),
           ('accounting_estimate_change','会计估计变更','CHANGE-estimate','prospective'),
           ('prior_period_error','前期差错更正','CHANGE-error','restatement_subject_to_disclosed_exceptions'))
    for disclosure in disclosures:
        if disclosure.get('company') != company or not disclosure.get('evidence_id') or not disclosure.get('document_sha256'):
            continue
        try:
            if date.fromisoformat(disclosure.get('publish_date','')) > cutoff:
                continue
        except (ValueError, TypeError):
            continue
        text=str(disclosure.get('text',''))
        compact=re.sub(r'\s+','',text)
        for kind,label,rule_id,treatment in rules:
            if label not in compact:
                continue
            # "不适用初始确认豁免" is a positive accounting-policy change,
            # while "未发生会计政策变更" denies the event itself. Negation
            # belongs to the requested topic, never the whole PDF section.
            denied = (re.search(r'(?:未发生|未进行|不存在|没有|无重大)(?:重要|重大|本期|报告期|的)*' + re.escape(label), compact) or
                      re.search(re.escape(label) + r'(?:事项|情况)?[：:]?(?:未发生|未进行|没有|□适用√不适用|不适用(?=[。；]|$))', compact) or
                      re.search(re.escape(label) + r'[^。；]{0,65}(?:未发生|未进行|不存在|没有)(?:重大|重要|的)*变更', compact) or
                      re.search(re.escape(label.removesuffix('变更')) + r'(?:本期|报告期)?未发生(?:重大|重要)?变更', compact))
            if denied:
                continue
            # An unrelated production-plan action after a heading cannot
            # establish a financial event. Require a company/topic assertion
            # or an explicit issuer action with a substantive accounting cue.
            local_assertion = re.search(r'(?:本公司|公司)[^。；]{0,50}' + re.escape(label), compact)
            action = re.search(r'(?:本公司|公司)[^。；]{0,120}(?:执行|采用|更正|调整)|追溯(?:调整|重述)', compact)
            body = compact.replace(label, '').replace(label.removesuffix('变更'), '')
            accounting_cue = re.search(r'准则|递延所得税|所得税|会计处理|比较报表|折旧|使用年限|使用寿命|坏账|摊销', body)
            if not local_assertion and not (action and accounting_cue):
                continue
            identifiers = list(dict.fromkeys(disclosure.get('evidence') or [disclosure['evidence_id']]))
            identity = disclosure.get('event_identity')
            if identity and (identity, kind) in identities:
                existing = identities[(identity, kind)]
                existing['evidence'] = list(dict.fromkeys(existing['evidence'] + identifiers))
                continue
            item = {'kind':kind,'company':company,'rule_id':rule_id,'code':'A5',
                           'treatment':treatment,'status':'disclosure_event_requires_professional_review',
                           'reason':'明示披露触发复核，不由标题推断金额影响或舞弊',
                           'evidence':identifiers,'document_sha256':disclosure['document_sha256'],
                           'publish_date':disclosure['publish_date'],'source_text':text}
            if disclosure.get('period'):
                item['period'] = disclosure['period']
            if identity:
                item['event_identity'] = identity
                identities[(identity, kind)] = item
            events.append(item)
    return events

