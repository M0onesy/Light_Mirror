"""Deterministic Chinese text for frozen financial analysis records.

This presentation layer formats kernel values and citations. It never calls a
model, changes a financial record, or supplies a missing value or threshold.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, ROUND_FLOOR, ROUND_CEILING, localcontext

from .decimal_math import DecimalInterval, unit_scale, round_display_interval


_TITLES = {
    'REVENUE': '营业收入', 'COST': '营业成本', 'TOTAL_COST': '营业总成本',
    'NP': '净利润', 'NP_PARENT': '归母净利润', 'NP_PARENT_ADJ': '扣非归母净利润',
    'CFO': '经营活动现金流量净额', 'ASSETS': '资产总额', 'LIABILITIES': '负债总额',
    'EQUITY': '所有者权益', 'EQUITY_PARENT': '归属于母公司所有者权益',
    'EPS': '基本每股收益', 'EPS_DILUTED': '稀释每股收益', 'ROE': '净资产收益率',
    'GROSS_MARGIN': '毛利率', 'NET_MARGIN': '净利率', 'DEBT_RATIO': '资产负债率',
    'CASH_CONTENT': '经营现金流含金量', 'NON_RECURRING_SHARE': '非经常性损益占比',
    'NP_PARENT_RECOMPUTED': '扣非归母净利润重算', 'NRI_TOTAL': '非经常性损益总额',
    'NRI_TAX_EFFECT': '所得税影响', 'NRI_MINORITY': '少数股东权益影响',
    'NRI_MINORITY_RAW': '少数股东权益影响披露项', 'NONRECURRING_NET': '非经常性损益净额',
    'SELLING_EXPENSE': '销售费用', 'ADMIN_EXPENSE': '管理费用', 'RD_EXPENSE': '研发费用',
    'FINANCE_EXPENSE': '财务费用', 'RECEIVABLES': '应收账款', 'INVENTORY': '存货',
    'MONETARY_FUNDS': '货币资金', 'SHORT_TERM_BORROWING': '短期借款',
    'LONG_TERM_BORROWING': '长期借款', 'SHARES': '股本',
}
_RATIOS = {'ROE', 'GROSS_MARGIN', 'NET_MARGIN', 'DEBT_RATIO', 'CASH_CONTENT', 'NON_RECURRING_SHARE'}
_PER_SHARE = {'EPS', 'EPS_DILUTED'}
_AMOUNTS = set(_TITLES) - _RATIOS - _PER_SHARE - {'SHARES'}
from .metrics import METRIC_SPECS
from .taxonomy import ACCOUNT_NAMES, AMOUNT_ACCOUNTS, SHARE_ACCOUNTS
_TITLES.update({code: title for code, title in ACCOUNT_NAMES.items() if code not in _TITLES})
_TITLES.update({code: spec.label for code, spec in METRIC_SPECS.items()})
_RATIOS.update(code for code, spec in METRIC_SPECS.items() if spec.kind == 'ratio')
_AMOUNTS.update(code for code, spec in METRIC_SPECS.items() if spec.kind == 'amount')
_AMOUNTS.update(AMOUNT_ACCOUNTS)
_MULTIPLES = {'CURRENT_RATIO', 'QUICK_RATIO', 'ASSET_TURNOVER', 'AR_TURNOVER', 'AP_TURNOVER', 'INVENTORY_TURNOVER'}
_SIGNALS = {'low_cash_content': '经营现金流含金量连续期间提示',
            'profit_cashflow_divergence': '利润与经营现金流变动方向背离提示',
            'non_recurring_share': '非经常性损益占比提示'}
_EVENTS = {'disclosure_version_change': '披露版本变化', 'accounting_policy_change': '会计政策变更',
           'accounting_estimate_change': '会计估计变更', 'prior_period_error': '前期差错更正'}


def escape_markdown(value) -> str:
    """Keep source strings as plain text, including URLs and template braces."""
    text = re.sub(r'[\r\n\t]+', ' ', str(value if value is not None else ''))
    return re.sub(r'([\\`*_{}\[\]()<>#+\-.!|~:/&=])', r'\\\1', text)


def _decimal(value) -> Decimal | None:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _plain_decimal(number: Decimal) -> str:
    text = format(number, 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def standard_unit(metric: str, record: dict | None = None) -> str:
    if metric in _MULTIPLES:
        return '倍'
    if metric in _RATIOS or metric.endswith(('_YOY', '_QOQ')):
        return '无量纲比例'
    if metric in _PER_SHARE:
        return '元/股'
    if metric == 'SHARES' or metric in SHARE_ACCOUNTS:
        return '股'
    if metric in _AMOUNTS:
        return '元'
    unit = (record or {}).get('value_unit') or (record or {}).get('unit')
    return unit if unit in {'元', '元/股', '股', '无量纲比例'} else '单位未知'


def display_standard_value(value, unit: str) -> str:
    """Exact inspection text, not a precision-qualified financial conclusion."""
    if value is None:
        return '未计算'
    number = _decimal(value)
    if number is None:
        return '数值无效；请查看冻结记录'
    standard = _plain_decimal(number)
    if unit == '无量纲比例':
        parts = number.as_tuple()
        # Decimal tuple construction moves the display decimal point without
        # invoking the active precision context or rounding a kernel value.
        percent = Decimal((parts.sign, parts.digits, parts.exponent + 2))
        return f'{_plain_decimal(percent)}%（标准值 {standard}，无量纲比例）'
    return f'{standard} {unit}'


def _display_precision(*numbers: Decimal) -> int:
    return max(80, *(len(number.as_tuple().digits) + abs(number.as_tuple().exponent) +
                     max(0, number.adjusted()) + 8 for number in numbers))


def _fixed(number: Decimal, decimals: int) -> str:
    # All product formatting owns its Decimal context and rounding policy.
    # A caller's precision=2 must never affect a financial display.
    with localcontext() as context:
        context.prec = _display_precision(number) + decimals
        rounded = number.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    return format(abs(rounded) if rounded.is_zero() else rounded, f'.{decimals}f')


def _source_display(record: dict, value: Decimal) -> dict | None:
    metric = str(record.get('metric') or record.get('account') or '')
    operands = record.get('operands') or ([record] if record.get('fact_id') else [])
    candidates = [operand for operand in operands if operand.get('account') == metric
                  and _decimal(operand.get('value')) == value]
    if len(candidates) != 1:
        return None
    source = candidates[0]
    unit, decimals = source.get('display_unit'), source.get('decimals')
    if type(decimals) is not int or not 0 <= decimals <= 12:
        return None
    if not unit or (standard_unit(metric, record) == '无量纲比例' and unit not in {'%', '％', '比例', '倍', '百分点'}):
        return None
    if standard_unit(metric, record) == '倍' and unit not in {'倍', '比例'}:
        return None
    if metric in _PER_SHARE and unit not in {'元', '元/股'}:
        return None
    try:
        scale = unit_scale(unit)
        with localcontext() as context:
            context.prec = _display_precision(value, scale) + decimals
            printed = value / scale
            rounded = printed.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
        if rounded != printed:
            return None  # Inconsistent metadata cannot establish precision.
    except (ValueError, InvalidOperation, TypeError):
        return None
    label = '元/股' if metric in _PER_SHARE and unit == '元' else '%' if unit == '％' else unit
    return {'display_value':_fixed(printed, decimals) + (label if label in {'%', '百分点'} else ' ' + label),
            'display_unit':label, 'display_precision':decimals,
            'display_precision_status':'source_disclosure',
            'display_precision_note':'按原报告披露单位及小数位展示；标准值与来源精度可在原始记录查看。'}


def _metric_interval(record: dict) -> DecimalInterval | None:
    raw = record.get('expected_interval')
    if not isinstance(raw, dict) or type(raw.get('lower_closed')) is not bool or type(raw.get('upper_closed')) is not bool:
        return None
    lower, upper = _decimal(raw.get('lower')), _decimal(raw.get('upper'))
    if lower is None or upper is None:
        return None
    try:
        return DecimalInterval(lower, upper, raw['lower_closed'], raw['upper_closed'])
    except ValueError:
        return None


def _rounded_interval(bounds: DecimalInterval, decimals: int) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    return round_display_interval(bounds, decimals)


def _formula_display_unit(record: dict, standard: str) -> tuple[str, int]:
    if standard == '倍':
        return '倍', 2
    if standard == '无量纲比例':
        return '%', 2
    precision = record.get('source_precision') or []
    if not precision:
        precision = record.get('operands') or []
    known = [row for row in precision if type(row.get('decimals')) is int and 0 <= row['decimals'] <= 12]
    units = {row.get('display_unit') for row in known}
    if len(units) == 1:
        unit = next(iter(units))
        try:
            unit_scale(unit)
            if unit not in {'%', '％', '百分点', '比例', '倍', ''}:
                return unit, min(row['decimals'] for row in known)
        except (ValueError, TypeError):
            pass
    return standard, 2


def metric_display_metadata(record: dict) -> dict:
    """Qualify display precision without changing exact kernel numerics.

    A derived value is a point only when its entire source-supported interval
    rounds to that display. Otherwise the UI receives an outward-rounded
    interval. These strings are computed by Python, never by JavaScript floats
    or reporter model prose.
    """
    metric = str(record.get('metric') or record.get('account') or '')
    standard = standard_unit(metric, record)
    unit, decimals = _formula_display_unit(record, standard)
    value = _decimal(record.get('value'))
    if record.get('value') is None or value is None:
        return {'display_value':'未计算' if record.get('value') is None else '数值无效；请查看冻结记录',
                'display_unit':unit, 'display_precision':None,
                'display_precision_status':'unavailable', 'display_precision_note':record.get('reason')}
    source_disclosure = record.get('verification_basis') == 'source_disclosure' or record.get('formula') == '原报告披露值' or record.get('fact_id')
    if source_disclosure:
        disclosed = _source_display(record, value)
        if disclosed:
            return disclosed
    bounds = None if source_disclosure else _metric_interval(record)
    if bounds is None:
        # Old records remain readable, but unsupported long decimals cannot
        # masquerade as disclosed precision. Exact raw values remain intact.
        shown = value
        if standard == '无量纲比例':
            parts = value.as_tuple()
            shown = Decimal((parts.sign, parts.digits, parts.exponent + 2))
        if max(0, -shown.as_tuple().exponent) > 2:
            text = '约' + _fixed(shown, 2) + ('%' if standard == '无量纲比例' else ' ' + standard)
        else:
            text = display_standard_value(value, standard)
        return {'display_value':text, 'display_unit':'%' if standard == '无量纲比例' else standard,
                'display_precision':None, 'display_precision_status':'source_precision_unknown',
                'display_precision_note':'来源披露精度未提供；显示位数仅用于阅读，不能视为已核实的精度。'}
    try:
        scale = unit_scale(unit)
        bounds = bounds.scaled(Decimal(1) / scale)
    except (ValueError, InvalidOperation, TypeError):
        return {'display_value':'单位依据不足；请查看原始记录', 'display_unit':standard,
                'display_precision':None, 'display_precision_status':'source_precision_unknown',
                'display_precision_note':'未登记显示单位，不能推断换算及显示精度。'}
    low, high, outward_low, outward_high = _rounded_interval(bounds, decimals)
    suffix = '%' if unit == '%' else ' ' + unit
    if low == high:
        return {'display_value':'约' + _fixed(low, decimals) + suffix,
                'display_unit':unit, 'display_precision':decimals,
                'display_precision_status':'interval_stable_rounded',
                'display_precision_note':f'由操作数披露精度传播；整个来源区间在显示小数{decimals}位时舍入一致，未增加独立核实的精确位数。'}
    return {'display_value':f'区间 [{_fixed(outward_low, decimals)}, {_fixed(outward_high, decimals)}]' + suffix,
            'display_unit':unit, 'display_precision':decimals,
            'display_precision_status':'interval_range',
            'display_interval':{'lower':_fixed(outward_low, decimals), 'upper':_fixed(outward_high, decimals),
                                'unit':unit, 'enclosure':'outward_rounded_closed'},
            'display_precision_note':'来源披露区间跨越显示舍入边界，保守展示向外取整的区间；完整端点及开闭性可在原始记录查看。'}


def display_metric_value(record: dict) -> str:
    return metric_display_metadata(record)['display_value']


def _metric_title(metric: str) -> str:
    for suffix, label in (('_YOY', '同比'), ('_QOQ', '环比')):
        if metric.endswith(suffix):
            return _TITLES.get(metric[:-len(suffix)], escape_markdown(metric[:-len(suffix)])) + label
    return _TITLES.get(metric, escape_markdown(metric) or '未命名指标')


def _period(value) -> str:
    match = re.fullmatch(r'(20\d{2})(Y|H1|H2|9M|Q[1-4])', str(value or ''))
    if not match:
        return escape_markdown(value) or '期间未提供'
    labels = {'Y': '年', 'H1': '年上半年', 'H2': '年下半年', '9M': '年前三季度',
              'Q1': '年第一季度', 'Q2': '年第二季度', 'Q3': '年第三季度', 'Q4': '年第四季度'}
    return match[1] + labels[match[2]]


def _context(record: dict) -> list[str]:
    periods = record.get('periods') or ([record['period']] if record.get('period') else [])
    return [f"公司：{escape_markdown(record.get('company')) or '未提供'}；口径：{escape_markdown(record.get('scope')) or '未提供'}",
            '期间：' + '、'.join(_period(period) for period in periods) if periods else '期间：未提供']


def _evidence(record: dict) -> list[str]:
    ids = record.get('evidence') or []
    return ['证据 ID：' + '；'.join(escape_markdown(e) for e in ids)] if ids else ['证据 ID：未提供，需要补充可定位材料']


def _operands(record: dict) -> list[str]:
    operands = record.get('operands') or []
    if not operands:
        return ['操作数：未提供']
    lines = ['操作数：']
    for row in operands:
        metric = str(row.get('account') or '')
        value = display_metric_value(row)
        description = (f"{_metric_title(metric)}；{_period(row.get('period'))}；"
                       f"{escape_markdown(row.get('scope')) or '口径未提供'}；{value}；"
                       f"事实 ID：{escape_markdown(row.get('fact_id')) or '未提供'}")
        if row.get('display_unit'):
            description += '；原披露单位：' + escape_markdown(row['display_unit'])
        lines.append('- ' + description)
        if row.get('source_doc') or row.get('publish_date'):
            lines.append('  来源：' + escape_markdown(row.get('source_doc')) +
                         '；披露日期：' + escape_markdown(row.get('publish_date')))
        if row.get('evidence'):
            lines.append('  证据 ID：' + '；'.join(escape_markdown(e) for e in row['evidence']))
    return lines


def analysis_record_text(category: str, record: dict, parameters: dict | None = None) -> str:
    """Render only fields from an immutable kernel record and its parameters."""
    parameters = parameters or {}
    metric = str(record.get('metric') or '')
    if category == 'metrics':
        lines = ['## ' + _metric_title(metric), *_context(record)]
        statuses = {'verified': '内核结果可用', 'abstained': '弃权', 'violated': '发现差异'}
        lines.append('状态：' + statuses.get(record.get('status'), escape_markdown(record.get('status')) or '未提供'))
        presentation = metric_display_metadata(record)
        lines.append('数值：' + presentation['display_value'])
        if presentation.get('display_precision_note'):
            lines.append('显示精度：' + presentation['display_precision_note'])
        if record.get('base_period'):
            lines.append('基期：' + _period(record['base_period']))
        lines.append('公式：' + (escape_markdown(record.get('formula')) or '未提供授权公式'))
        lines.extend(_operands(record))
        if record.get('disclosed_value') is not None:
            lines.append('已披露扣非值：' + (record.get('disclosed_display_value') or display_standard_value(record['disclosed_value'], '元')))
        if record.get('match_disclosure') is not None:
            lines.append('与披露值核对：' + ('在冻结容差内一致' if record['match_disclosure'] else '需要复核差异'))
    elif category == 'signals':
        kind = str(record.get('kind') or '')
        lines = ['## 复核提示：' + _SIGNALS.get(kind, escape_markdown(kind) or '未命名提示'), *_context(record),
                 '提示性质：登记规则触发的财务复核信号，不构成投资建议或舞弊判断。']
        if record.get('value') is not None:
            presentation = metric_display_metadata(dict(record, metric='NON_RECURRING_SHARE', value_unit='无量纲比例'))
            lines.append('信号数值：' + presentation['display_value'])
            if presentation.get('display_precision_note'):
                lines.append('显示精度：' + presentation['display_precision_note'])
        if record.get('threshold') is not None:
            lines.append('登记阈值：' + display_standard_value(record['threshold'], '无量纲比例'))
        if record.get('consecutive_changes') is not None:
            lines.append('连续变化次数：' + escape_markdown(record['consecutive_changes']))
        if record.get('severity'):
            lines.append('提示级别：' + {'elevated': '加强复核', 'notice': '复核提示'}.get(
                record['severity'], escape_markdown(record['severity'])))
        source = record.get('parameter_source') or parameters.get('source')
        lines.append('阈值来源：' + ('团队登记参数（用于复核提示）' if source == 'team_parameter' else escape_markdown(source) or '未提供'))
        if record.get('threshold') is None and record.get('consecutive_changes') is None:
            lines.append('触发阈值：冻结记录未提供，不能补写阈值。')
        lines.extend(_operands(record))
    elif category == 'events':
        kind = str(record.get('kind') or '')
        lines = ['## 披露复核事件：' + _EVENTS.get(kind, escape_markdown(kind) or '未命名事件'), *_context(record),
                 '事件性质：已披露材料中的复核事项；原因、金额影响及处理方式以原文和适用规则为准。']
        if record.get('versions'):
            lines.append('披露版本：' + '；'.join(escape_markdown(v) for v in record['versions']))
        for key, label in (('classification', '分类'), ('treatment', '规则处理标识'), ('publish_date', '披露日期'),
                           ('document_sha256', '文档 SHA-256'), ('source_text', '披露原文')):
            if record.get(key):
                lines.append(label + '：' + escape_markdown(record[key]))
    else:
        raise ValueError('unknown analysis presentation category')
    if record.get('abstention_code'):
        lines.append('弃权码：' + escape_markdown(record['abstention_code']))
    if record.get('reason'):
        lines.append('原因：' + escape_markdown(record['reason']))
    if record.get('rule_id'):
        lines.append('规则依据：' + escape_markdown(record['rule_id']))
    lines.extend(_evidence(record))
    return '\n\n'.join(lines)
