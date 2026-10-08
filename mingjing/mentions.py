from __future__ import annotations

"""Conservative numeric extraction with offsets into the untouched source.

Only dimensioned values in financial sentences are admitted. Table cells
without headers are not silently assigned the surrounding annual period.
"""
import re
from decimal import Decimal
from .models import AtomicClaim
from .taxonomy import FINANCIAL_LABELS, AMOUNT_ACCOUNTS, PER_SHARE_LABELS

NUM = r"[-+－−]?\d[\d,，]*(?:\.\d+)?"
VALUE_RE = re.compile(rf"(?P<num>{NUM})\s*(?:个(?=百分点))?(?P<unit>百万元|亿元|万元|万股|百分点|亿|万|元|%|％|倍|股)?")
YEAR_RE = re.compile(r"(?P<year>20\d{2})\s*(?:年\s*(?P<period>上半年|下半年|半年度|半年|前三季度|第四季度|四季度|第三季度|三季度|第二季度|二季度|第一季度|一季度|年度)?|(?P<code>H1|H2|Q[1-4]|9M|Y))", re.I)
ACCOUNT_LABELS = (
    ("归属于挂牌公司股东的扣除非经常性损益后的净利润", "NP_PARENT_ADJ"),
    ("归属于上市公司股东的扣除非经常性损益后的净利润", "NP_PARENT_ADJ"),
    ("归属于挂牌公司股东的净利润", "NP_PARENT"),
    ("归属于母公司所有者的净利润", "NP_PARENT"),
    ("归属于母公司股东的净利润", "NP_PARENT"),
    ("归属于上市公司股东的净利润", "NP_PARENT"),
    ("扣除非经常性损益后的净利润", "NP_PARENT_ADJ"),
    ("归母净利润", "NP_PARENT"), ("扣非净利润", "NP_PARENT_ADJ"),
    ("经营活动产生的现金流量净额", "CFO"), ("经营现金流", "CFO"),
    ("营业总收入", "REVENUE"), ("营业收入", "REVENUE"), ("营收", "REVENUE"),
    ("营业成本", "COST"), ("净利润", "NP"),
    # A broker expense ratio is not an expense amount or its growth rate.
    # Until its denominator/aggregation has an authorised rule, retain the
    # explicit source identity and abstain instead of guessing a formula.
    ("期间费用率", "EXP_PERIOD_RATIO_UNSPECIFIED"),
    ("管理费用率", "ADMIN_EXPENSE_RATIO"),
    ("销售费用率", "SELLING_EXPENSE_RATIO"),
    ("研发费用率", "RD_EXPENSE_RATIO"),
    ("财务费用率", "FINANCE_EXPENSE_RATIO"),
    ("费用率", "EXP_RATIO_UNSPECIFIED"),
    ("毛利率", "GROSS_MARGIN"), ("净利率", "NET_MARGIN_UNSPECIFIED"),
    ("净利润率", "NET_MARGIN_UNSPECIFIED"),
    ("归母净利润率", "NP_PARENT_MARGIN"),
    ("归母净利率", "NP_PARENT_MARGIN"),
    ("资产负债率", "DEBT_RATIO"), ("加权平均净资产收益率", "ROE"),
    ("加权净资产收益率", "ROE"), ("净资产收益率", "ROE_UNSPECIFIED"),
    ("ROE", "ROE_UNSPECIFIED"), ("EPS", "EPS_UNSPECIFIED"),
    ("每股收益", "EPS_UNSPECIFIED"), ("目标价", "UNKNOWN"),
)
# Source identities are deliberately separate from the authorised fact
# taxonomy. Recognising a broker caption never grants a new formula/query
# account, nor permission to treat a bridge movement as a closing balance.
SOURCE_REVIEW_LABELS = {
    'EBITDA':'EBITDA_UNSPECIFIED', 'ROIC':'ROIC_UNSPECIFIED',
    '净负债比率':'NET_DEBT_RATIO_UNSPECIFIED',
    '速动比率':'QUICK_RATIO_UNSPECIFIED',
    '总资产周转率':'TURNOVER_ASSETS_UNSPECIFIED',
    '\u5b58\u8d27\u5468\u8f6c\u7387':'TURNOVER_INVENTORY_UNSPECIFIED',
    '应收账款周转率':'TURNOVER_AR_UNSPECIFIED', '应付账款周转率':'TURNOVER_AP_UNSPECIFIED',
    '折旧摊销':'DEPRECIATION_AMORTIZATION_UNSPECIFIED',
    '投资损失':'INVESTMENT_LOSS_UNSPECIFIED', '营运资金变动':'WORKING_CAPITAL_CHANGE_UNSPECIFIED',
    '其他经营现金流':'OTHER_CFO_UNSPECIFIED', '资本支出':'CAPEX_UNSPECIFIED',
    '其他投资现金流':'OTHER_CFI_UNSPECIFIED', '其他筹资现金流':'OTHER_CFF_UNSPECIFIED',
    '普通股增加':'COMMON_SHARE_CHANGE_UNSPECIFIED', '资本公积增加':'CAPITAL_RESERVE_CHANGE_UNSPECIFIED',
    '每股经营现金流':'CFO_PER_SHARE_BROKER_UNSPECIFIED',
    '每股净资产':'EQUITY_PER_SHARE_BROKER_UNSPECIFIED',
}
SOURCE_REVIEW_SCALARS = frozenset({'CURRENT_RATIO_UNSPECIFIED','QUICK_RATIO_UNSPECIFIED',
                                  'TURNOVER_ASSETS_UNSPECIFIED','TURNOVER_INVENTORY_UNSPECIFIED',
                                  'TURNOVER_AR_UNSPECIFIED','TURNOVER_AP_UNSPECIFIED'})
SOURCE_REVIEW_RATIOS = SOURCE_REVIEW_SCALARS | frozenset({'ROIC_UNSPECIFIED','NET_DEBT_RATIO_UNSPECIFIED'})
SOURCE_REVIEW_PER_SHARE = frozenset({'CFO_PER_SHARE_BROKER_UNSPECIFIED','EQUITY_PER_SHARE_BROKER_UNSPECIFIED'})
SOURCE_REVIEW_FORMULAS = SOURCE_REVIEW_RATIOS | frozenset({'EBITDA_UNSPECIFIED'})
ACCOUNT_LABELS = tuple(sorted(dict(ACCOUNT_LABELS + (('流动比率','CURRENT_RATIO'),) + tuple(FINANCIAL_LABELS.items()) + tuple(PER_SHARE_LABELS.items()) + tuple(SOURCE_REVIEW_LABELS.items())).items(),
                              key=lambda item: -len(item[0])))
CONTEXT_TERMS = tuple(label for label, _ in ACCOUNT_LABELS) + ("收入", "利润", "现金流", "同比", "环比", "CAGR", "占比", "增长率",
                                                           "市场规模", "行业规模", "市场份额", "行业份额", "渗透率")
NOISE_TERMS = ("证券代码", "股票代码", "报告编号", "公告编号", "统一社会信用代码", "注册号", "证书编号", "备案号", "目录", "页码")
FORECAST_TERMS = ("预计", "预测", "目标价", "当前股价", "可比公司", "估值")


def account_label_pattern(label: str) -> str:
    """English financial abbreviations must be standalone source tokens."""
    escaped = re.escape(label)
    return (r'(?<![A-Za-z0-9_])(?i:'+escaped+r')(?![A-Za-z0-9_])'
            if label.isascii() and label.isalpha() else escaped)


def has_financial_context(text: str) -> bool:
    return any(re.search(account_label_pattern(term), text) for term in CONTEXT_TERMS)


def net_margin_definition_matches(text: str) -> list[tuple[int, int, str]]:
    """An explicit source numerator may refine a margin label, never its value.

    NET_MARGIN is the issued NP/REVENUE rule. A complete parent-profit definition
    uses the distinct NP_PARENT_MARGIN rule; a numerator alone leaves its
    denominator unknown even when that rule is registered.
    Only complete local definition syntax is accepted, not a nearby profit term.
    """
    numerator = r'(?P<numerator>归属于母公司所有者的净利润|归属于母公司股东的净利润|归母净利润|净利润)'
    denominator = r'(?:营业总收入|营业收入|营收)'
    label = r'(?<!归母)(?<!扣非)(?:净利润率|净利率)'
    patterns = [
        re.compile(label+r'[（(]'+numerator+r'(?:[/÷]|除以)'+denominator+r'[）)]'),
        re.compile(label+r'(?:=|＝|即|指|等于|为)'+numerator+r'(?:[/÷]|除以)'+denominator),
        re.compile(r'以'+numerator+r'为分子[、,，]?以?'+denominator+r'为分母(?:计算的?|得到的?)?'+label),
    ]
    matches = [(m.start(), m.end(), 'NET_MARGIN' if m['numerator']=='净利润' else 'NP_PARENT_MARGIN')
               for pattern in patterns for m in pattern.finditer(text)]
    # A numerator-only annotation is still one margin source label. Retain
    # its unresolved identity so an embedded "净利润" cannot steal the %
    # occurrence, but never invent its missing revenue denominator.
    partial = [
        re.compile(label+r'[（(](?:按|以)?'+numerator+r'(?:口径|为分子|计算)[）)]'),
        re.compile(label+r'(?:按|采用|使用|以)'+numerator+r'(?:口径(?:计算)?|为分子)'),
    ]
    matches.extend((m.start(),m.end(), 'NET_MARGIN_UNSPECIFIED' if m['numerator']=='净利润'
                    else 'NET_MARGIN_PARENT_UNSPECIFIED') for pattern in partial for m in pattern.finditer(text))
    return matches


def current_ratio_definition_matches(text: str) -> list[tuple[int, int, str]]:
    """Recognise a local written method before any embedded amount captions.

    A plain current-ratio label has its registered standard meaning. A source
    that explicitly defines another numerator/denominator must retain its own
    unresolved identity; a matching amount cannot select the standard method.
    """
    patterns = (
        re.compile(r'流动比率[（(](?P<method>[^）)]+)[）)]'),
        re.compile(r'流动比率(?:=|＝|即|指|等于|为|采用|按|以|使用)(?P<method>[^，,。；;\n]+)'),
        re.compile(r'(?:按|以|采用|使用)(?P<method>[^，,。；;\n]{1,80}?)(?:计算的?|定义的?|口径的?)流动比率'),
    )
    output = []
    standard = re.compile(r'流动资产(?:合计)?(?:[/÷]|除以|与)流动负债(?:合计)?'
                          r'(?:的?(?:比值|比率|比例))?(?:口径)?(?:计算)?(?:[×*]100[%％])?')
    for pattern in patterns:
        for match in pattern.finditer(text):
            method = match['method']
            if method in {'倍', '%', '％'}:
                continue
            # "=0.8" is a value assignment, not a new financial definition.
            if re.fullmatch(r'[-+－−]?\d[\d,，]*(?:\.\d+)?(?:倍|[%％])?', method):
                continue
            if not re.search(r'[\u4e00-\u9fffA-Za-z/÷×*]', method):
                continue
            method = re.split(r'(?:约为|为|达到|达)(?=[+\-－−]?\d)', method)[0]
            method = re.sub(r'(?:约为|为|达到|达)$', '', method)
            method = re.sub(r'^(?:按|以|采用|使用)', '', method)
            account = 'CURRENT_RATIO' if standard.fullmatch(method) else 'CURRENT_RATIO_UNSPECIFIED'
            output.append((match.start(), match.end(), account))
    output.extend((match.start(), match.end(), 'CURRENT_RATIO_UNSPECIFIED') for match in
                  re.finditer(r'(?:自定义|非标准|调整后|修正后)流动比率', text))
    return output


def source_metric_identity(account: str, text: str, numeric_start: int | None = None) -> tuple[str, str]:
    """A source label/method chooses a registered identity, never an amount."""
    fixed = {'CURRENT_RATIO': 'metric:current-ratio', 'NP_PARENT_MARGIN': 'metric:np-parent-margin',
             'SELLING_EXPENSE_RATIO': 'metric:selling-expense-ratio', 'ADMIN_EXPENSE_RATIO': 'metric:admin-expense-ratio',
             'RD_EXPENSE_RATIO': 'metric:rd-expense-ratio', 'FINANCE_EXPENSE_RATIO': 'metric:finance-expense-ratio'}
    labels = {'CURRENT_RATIO': r'流动比率', 'NP_PARENT_MARGIN': r'归母净利(?:润)?率|净利(?:润)?率.*归母净利润',
              'SELLING_EXPENSE_RATIO': r'销售费用率', 'ADMIN_EXPENSE_RATIO': r'管理费用率',
              'RD_EXPENSE_RATIO': r'研发费用率', 'FINANCE_EXPENSE_RATIO': r'财务费用率'}
    compact = re.sub(r'\s+', '', text)
    if account in {'CURRENT_RATIO', 'CURRENT_RATIO_UNSPECIFIED'}:
        method_text = compact
        if type(numeric_start) is int and 0 <= numeric_start <= len(text):
            before = re.sub(r'\s+', '', text[:numeric_start])
            labels_before = list(re.finditer(r'流动比率', before))
            if labels_before:
                label = labels_before[-1]
                left = max(before.rfind(boundary, 0, label.start()) for boundary in '，,。；;') + 1
                method_text = before[left:]
        methods = current_ratio_definition_matches(method_text)
        if any(code == 'CURRENT_RATIO_UNSPECIFIED' for _, _, code in methods):
            return 'CURRENT_RATIO_UNSPECIFIED', ''
        if account == 'CURRENT_RATIO_UNSPECIFIED':
            return account, ''
    if account in fixed and re.search(labels[account], compact):
        return account, fixed[account]
    if account == 'NET_MARGIN' and any(code == 'NET_MARGIN' for _,_,code in net_margin_definition_matches(compact)):
        return account, 'metric:net-margin'
    methods = (
        ('QUICK_RATIO_UNSPECIFIED', 'QUICK_RATIO', 'metric:quick-ratio-inventory', r'速动比率(?:[（(]|=|＝|即|指)(?:流动资产[-－减]存货)[）)]?[/÷除以]+流动负债'),
        # Require both the numerator and average balance in the source method.
        ('TURNOVER_ASSETS_UNSPECIFIED', 'ASSET_TURNOVER', 'metric:asset-turnover-average',
         '\u603b\u8d44\u4ea7\u5468\u8f6c\u7387.*?(?:\u8425\u4e1a\u6536\u5165|\u8425\u4e1a\u603b\u6536\u5165|\u8425\u4e1a\u6536\u5165\u989d|\u8425\u6536).*(?:/|\u00f7|\u9664\u4ee5).*\u5e73\u5747(?:\u603b)?\u8d44\u4ea7'),
        ('TURNOVER_INVENTORY_UNSPECIFIED', 'INVENTORY_TURNOVER', 'metric:inventory-turnover-average',
         '\u5b58\u8d27\u5468\u8f6c\u7387.*?(?:\u8425\u4e1a\u6210\u672c|\u4e3b\u8425\u4e1a\u52a1\u6210\u672c|\u9500\u552e\u6210\u672c).*(?:/|\u00f7|\u9664\u4ee5).*\u5e73\u5747\u5b58\u8d27'),
        ('TURNOVER_AR_UNSPECIFIED', 'AR_TURNOVER', 'metric:ar-turnover-average',
         '\u5e94\u6536\u8d26\u6b3e\u5468\u8f6c\u7387.*?(?:\u8425\u4e1a\u6536\u5165|\u8425\u4e1a\u603b\u6536\u5165|\u8425\u4e1a\u6536\u5165\u989d|\u8425\u6536).*(?:/|\u00f7|\u9664\u4ee5).*\u5e73\u5747\u5e94\u6536\u8d26\u6b3e'),
        ('TURNOVER_AP_UNSPECIFIED', 'AP_TURNOVER', 'metric:ap-turnover-average',
         '\u5e94\u4ed8\u8d26\u6b3e\u5468\u8f6c\u7387.*?(?:\u8425\u4e1a\u6210\u672c|\u4e3b\u8425\u4e1a\u52a1\u6210\u672c|\u9500\u552e\u6210\u672c).*(?:/|\u00f7|\u9664\u4ee5).*\u5e73\u5747\u5e94\u4ed8\u8d26\u6b3e'),
    )
    for old, new, definition, pattern in methods:
        if account == old and re.search(pattern, compact):
            return new, definition
    return account, ''

# Currency is separate from a decimal magnitude. The current Fact store has
# no foreign-currency or FX dimension; “亿美元” must not become RMB “亿”.
_CURRENCY_NAMES = {
    '人民币':'CNY', 'RMB':'CNY', 'CNY':'CNY', '￥':'CNY',
    '美元':'USD', 'USD':'USD', 'US$':'USD',
    '港元':'HKD', '港币':'HKD', 'HKD':'HKD', 'HK$':'HKD',
    '欧元':'EUR', 'EUR':'EUR', '€':'EUR',
    '英镑':'GBP', 'GBP':'GBP', '£':'GBP',
    '日元':'JPY', 'JPY':'JPY', '加元':'CAD', 'CAD':'CAD',
    '澳元':'AUD', 'AUD':'AUD', '瑞士法郎':'CHF', '瑞郎':'CHF', 'CHF':'CHF',
    '新加坡元':'SGD', 'SGD':'SGD', '外币':'UNKNOWN', '未知币种':'UNKNOWN', '$':'UNKNOWN', '¥':'UNKNOWN',
}
_CURRENCY_LABEL = '|'.join(re.escape(label) for label in sorted(_CURRENCY_NAMES, key=len, reverse=True))
_CURRENCY_TOKEN = re.compile(r'(?<![A-Za-z])(?:'+_CURRENCY_LABEL+r')(?![A-Za-z])', re.I)


def currency_declarations(text: str) -> list[dict]:
    """Read explicit source currency/unit declarations, never infer FX rates."""
    output = []
    for declaration in re.finditer(r'(?:币种|货币单位|记账本位币|(?:金额)?单位|Functional\s+currency|Currency|(?:Monetary|Amount)\s+unit)'
                                    r'\s*[:：为是]?\s*([^\n。；;]{0,45})', text, re.I):
        tokens = list(_CURRENCY_TOKEN.finditer(declaration[1]))
        is_currency = not re.match(r'(?:单位|金额单位|(?:Monetary|Amount)\s+unit)', declaration[0], re.I)
        if not tokens and not is_currency:
            continue  # A plain “单位：元” is not an unfamiliar currency.
        codes = {_CURRENCY_NAMES.get(token[0].upper(), _CURRENCY_NAMES.get(token[0], 'UNKNOWN')) for token in tokens}
        output.append({'currency':next(iter(codes)) if len(codes)==1 else 'UNKNOWN',
                       'text':declaration[0], 'start':declaration.start(), 'end':declaration.end(),
                       'conflict':len(codes)>1})
    return output


def source_currency(text: str, numeric_start: int | None = None) -> dict | None:
    """Locate currency governing this exact numeric occurrence in original text.

    Prefix/suffix declarations stop at the local clause. A later foreign
    amount must not invalidate an earlier RMB amount or a share-count cell.
    """
    if numeric_start is None:
        declarations = currency_declarations(text)
        if not declarations:
            return None
        codes = {row['currency'] for row in declarations}
        return {'currency':next(iter(codes)) if len(codes)==1 else 'UNKNOWN',
                'source':'explicit_currency_declaration', 'declarations':declarations,
                'conflict':len(codes)>1 or any(row.get('conflict') for row in declarations)}
    if type(numeric_start) is not int or not 0 <= numeric_start < len(text):
        return None
    number = VALUE_RE.match(text, numeric_start)
    if not number:
        return None
    before = re.split(r'[,，。；;]', text[:numeric_start])[-1]
    # Include an adjacent foreign suffix after magnitude 亿/万 or no unit.
    after = text[number.end('num'):number.end('num')+30]
    suffix = re.match(r'\s*(?:百\s*万|千\s*万|万|亿)?\s*(?:'+_CURRENCY_LABEL+r')', after, re.I)
    nearby = list(_CURRENCY_TOKEN.finditer(suffix[0])) if suffix else []
    prefix = re.search(r'(?:'+_CURRENCY_LABEL+r')\s*(?:口径|计价|计量|折算|表示|为|约|的)?\s*$', before, re.I)
    if prefix:
        nearby += list(_CURRENCY_TOKEN.finditer(prefix[0]))
    qualification = re.search(r'(?:'+_CURRENCY_LABEL+r')\s*(?:口径|计价|计量|折算|表示)[^\n，,。；;]{0,35}$', before, re.I)
    if qualification:
        nearby += list(_CURRENCY_TOKEN.finditer(qualification[0]))
    # “按美元计价，营业收入同比…” carries the explicit basis through
    # adjacent clauses until a sentence/section boundary. An amount without
    # a basis declaration does not taint an unrelated later clause.
    shared_prefix = re.split(r'[。；;]', text[:numeric_start])[-1]
    qualifiers = re.findall(r'(?:'+_CURRENCY_LABEL+r')\s*(?:口径|计价|计量|折算|表示|计算)', shared_prefix, re.I)
    for qualification_text in qualifiers:
        nearby += list(_CURRENCY_TOKEN.finditer(qualification_text))
    declarations = currency_declarations(shared_prefix)
    codes = {_CURRENCY_NAMES.get(token[0].upper(), _CURRENCY_NAMES.get(token[0], 'UNKNOWN')) for token in nearby}
    codes.update(row['currency'] for row in declarations)
    if not codes:
        return None
    return {'currency':next(iter(codes)) if len(codes)==1 else 'UNKNOWN',
            'source':'original_numeric_currency', 'source_numeric_start':numeric_start,
            'currency_texts':[token[0] for token in nearby], 'declarations':declarations,
            'conflict':len(codes)>1 or any(row.get('conflict') for row in declarations)}


def listed_periods(text: str, numeric_start: int) -> tuple[str | None, dict | None]:
    """Resolve an explicit ordered multi-period list, or declare ambiguity.

    Only “2020–2022年…分别为a、b和c” with matching counts is bound. A
    comparison range without a one-to-one list cannot donate its final year.
    Values are read only to count untouched occurrences, never match facts.
    """
    pattern = re.compile(r'(?P<first>20\d{2})\s*(?:年\s*)?[-－–—~～至到]\s*'
                         r'(?P<last>20\d{2})\s*年(?P<kind>度|上半年|下半年|第?[一二三四]季度)?'
                         r'(?P<label>[^。；;]{0,90}?)分别(?:为|达|是)?\s*')
    ranges = [match for match in pattern.finditer(text) if match.end() <= numeric_start]
    if not ranges:
        return None, None
    declared = ranges[-1]
    first, last = int(declared['first']), int(declared['last'])
    numbers, cursor = [], declared.end()
    while cursor < len(text):
        value = VALUE_RE.match(text, cursor)
        if not value:
            break
        numbers.append(value)
        cursor = value.end()
        separator = re.match(r'\s*(?:、|/|,|，|和|及|以及|与)\s*(?=[+\-－−]?\d)', text[cursor:])
        if not separator:
            break
        cursor += separator.end()
    end = numbers[-1].end() if numbers else declared.end()
    parent_numbers = None
    secondary_caption = None
    if numeric_start >= end:
        # An immediately adjoining, independently labelled ratio list may
        # reuse the same explicit ordered periods. It needs the complete
        # primary AND secondary list with equal counts; no later subject,
        # omitted cell or free percentage can inherit a year by proximity.
        caption = re.match(r'\s*[,，]\s*占比\s*分别\s*(?:为|达|是)?\s*', text[end:])
        if caption and 0 <= last-first <= 10 and len(numbers) == last-first+1:
            secondary, cursor = [], end+caption.end()
            while cursor < len(text):
                value = VALUE_RE.match(text, cursor)
                if not value:
                    break
                secondary.append(value)
                cursor = value.end()
                separator = re.match(r'\s*(?:、|/|,|，|和|及|以及|与)\s*(?=[+\-－−]?\d)', text[cursor:])
                if not separator:
                    break
                cursor += separator.end()
            second_end = secondary[-1].end() if secondary else cursor
            if (end+caption.end() <= numeric_start < second_end
                    and len(secondary) == len(numbers)
                    and secondary[-1]['unit'] in {'%', '％'}
                    and all(value['unit'] in {None, '', '%', '％'} for value in secondary)):
                parent_numbers = numbers
                secondary_caption = [end, end+caption.end()]
                numbers, end = secondary, second_end
        if parent_numbers is None and '分别' in text[end:numeric_start]:
            return '', {'source':'explicit_ordered_period_list', 'ambiguous':True,
                        'reason':'secondary_numeric_list_requires_its_own_source_period_mapping'}
        if parent_numbers is None:
            return None, None
    kind = {'上半年':'H1', '下半年':'H2', '第一季度':'Q1', '一季度':'Q1',
            '第二季度':'Q2', '二季度':'Q2', '第三季度':'Q3', '三季度':'Q3',
            '第四季度':'Q4', '四季度':'Q4'}.get(declared['kind'], 'Y')
    basis = {'source':'explicit_ordered_period_list', 'period_span':[declared.start(),declared.end()],
             'first_year':first,'last_year':last,'period_kind':kind,
             'occurrence_spans':[[match.start(),match.end()] for match in numbers]}
    if parent_numbers is not None:
        basis.update(source='explicit_secondary_ordered_period_list',
                     parent_occurrence_spans=[[match.start(),match.end()] for match in parent_numbers],
                     secondary_caption_span=secondary_caption,
                     source_context_span=[declared.start(),end])
    # A final explicit unit governs an uninterrupted “a、b及c万元” list.
    # Mixed units remain local; no unit is borrowed from a different clause.
    shared_unit = numbers[-1]['unit'] if numbers else None
    if shared_unit and all(not match['unit'] or match['unit']==shared_unit for match in numbers):
        basis.update(source_unit=shared_unit,
                     unit_source_span=[numbers[-1].start('unit'),numbers[-1].end('unit')])
    index = next((i for i,match in enumerate(numbers) if match.start()==numeric_start),None)
    if not 0 <= last-first <= 10 or len(numbers)!=last-first+1 or index is None:
        return '', dict(basis, ambiguous=True)
    return str(first+index)+kind, dict(basis, occurrence_index=index, ambiguous=False)

def _decimal(raw: str) -> Decimal:
    return Decimal(raw.replace(",", "").replace("，", "").replace("－", "-").replace("−", "-"))

def unit_multiplier(unit: str) -> Decimal:
    return Decimal({"百万元": "1000000", "亿元": "100000000", "亿": "100000000", "万元": "10000", "万": "10000", "元": "1", "%": "0.01", "％": "0.01", "百分点": "0.01", "倍": "1", "股": "1", "万股": "10000", "": "1"}.get(unit, "1"))

def period_from_text(text: str, fallback: str = "") -> str:
    matches = list(YEAR_RE.finditer(text))
    if not matches:
        return fallback
    match = matches[-1]
    suffix = match.group("code") or {"上半年": "H1", "下半年": "H2", "半年度": "H1", "半年": "H1", "前三季度": "9M", "四季度": "Q4", "第四季度": "Q4", "三季度": "Q3", "第三季度": "Q3", "二季度": "Q2", "第二季度": "Q2", "一季度": "Q1", "第一季度": "Q1"}.get(match.group("period"), "Y")
    if not match.group('period') and not match.group('code'):
        # A dated balance-sheet caption is not automatically the whole year.
        date = re.match(r'\s*(\d{1,2})月(\d{1,2})日', text[match.end():])
        if date:
            suffix = {('3', '31'): 'Q1', ('6', '30'): 'H1',
                      ('9', '30'): '9M', ('12', '31'): 'Y'}.get(
                          (str(int(date[1])), str(int(date[2]))), '')
            if not suffix:
                return ''
    return match.group("year") + suffix.upper()

def previous_period(period: str, relation: str) -> str:
    match = re.fullmatch(r"(\d{4})(Y|H1|H2|Q[1-4]|9M)", period)
    if not match or relation not in {"yoy", "qoq"}:
        return ""
    year, kind = int(match[1]), match[2]
    if relation == "yoy":
        return f"{year-1}{kind}"
    if kind.startswith("Q"):
        quarter = int(kind[1])
        return f"{year-1}Q4" if quarter == 1 else f"{year}Q{quarter-1}"
    return ""

def infer_account(text: str) -> str:
    compact = re.sub(r"\s+", "", text)
    definitions = net_margin_definition_matches(compact)
    if definitions:
        return max(definitions, key=lambda row: row[1])[2]
    for label, account in ACCOUNT_LABELS:
        if re.search(account_label_pattern(label), compact):
            return account
    return "UNKNOWN"

def _line_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    left = max(text.rfind("\n", 0, start), text.rfind("\r", 0, start)) + 1
    endings = [pos for ch in "\n\r" if (pos := text.find(ch, end)) >= 0]
    return left, min(endings) if endings else len(text)

def _sentence_bounds(text: str, start: int, end: int) -> tuple[int, int]:
    physical_left, physical_right = _line_bounds(text, start, end)
    line = text[physical_left:physical_right].strip()
    left, right = physical_left, physical_right
    # Join at most two wrapped prose lines. A fresh subject/year/title starts
    # a new statement; numeric chart axes never inherit a paragraph label.
    if not re.match(r"(?:图|表||公司|20\d{2}|\d+(?:\.\d+)*[.、](?!\d))", line):
        for _ in range(2):
            if left <= 0:
                break
            prior_end = left-1
            prior_left = text.rfind("\n", 0, prior_end)+1
            prior = text[prior_left:prior_end].strip()
            wrapped_label = prior.endswith("归") and line.startswith("母净利润")
            wrapped_business_period = bool(
                re.fullmatch(r'(?:从)?(?:产品|业务|分部)收入(?:结构)?(?:来看)?[,，]?20\d{2}年',re.sub(r'\s+','',prior))
                and re.match(r'(?:公司|本公司|发行人)?(?:并行通用超算云|并行行业云|并行AI云|超算云)',re.sub(r'\s+','',line)))
            if (len(prior) < 25 and not (wrapped_label or wrapped_business_period)) or prior.endswith(("。", "；", ":", "：")) or any(term in prior for term in NOISE_TERMS) or prior.startswith(("图", "表", "")):
                break
            left = prior_left
    sentence_start = max((text.rfind(ch, left, start) for ch in "。！？!?；;"), default=-1)
    left = max(left, sentence_start+1)
    endings = [pos for ch in "。！？!?；;" if (pos := text.find(ch, end, right)) >= 0]
    if endings:
        right = min(endings)
    while left < start and text[left].isspace():
        left += 1
    return left, max(end, right)

def _account_before(sentence: str, numeric_start: int) -> str:
    prefix = re.sub(r"\s+", "", sentence[:numeric_start])
    hits: list[tuple[int, int, str]] = []
    for label, account in ACCOUNT_LABELS:
        for match in re.finditer(account_label_pattern(label), prefix):
            hits.append((match.start(), match.end(), account))
    hits.extend(net_margin_definition_matches(prefix))
    hits.extend(current_ratio_definition_matches(prefix))
    hits = [h for h in hits if not any(other[0] <= h[0] and other[1] >= h[1] and other != h for other in hits)]
    if not hits:
        return "UNKNOWN"
    position, label_end, account = max(hits, key=lambda h: h[1])
    tail = prefix[position:]
    if len(tail) > 100:
        return "UNKNOWN"
    if account in {"REVENUE", "GROSS_MARGIN", "NET_MARGIN", "NET_MARGIN_UNSPECIFIED", "NET_MARGIN_PARENT_UNSPECIFIED"}:
        if any(term in prefix for term in ("超算云", "行业云", "AI云", "分部", "分产品", "前五大客户", "客户营收")):
            dimension = business_source_dimensions(sentence, numeric_start)
            if not dimension or dimension.get('account') != account:
                return "UNKNOWN"
        if any(term in tail for term in ("占比", "占总", "比重", "比例")):
            return "UNKNOWN"
    return account


def business_source_dimensions(text: str, numeric_start: int) -> dict:
    """Bind an explicitly named business to its own local financial measure."""
    prefix = text[:numeric_start]
    clause = re.split(r'[,，。；;]', prefix)[-1]
    names = ('并行通用超算云', '通用超算云', '并行行业云', '并行AI云', '超算云系统集成',
             '超算软件及技术服务', '超算软件与技术服务', '超算云服务', '超算云')
    compact = re.sub(r'\s+', '', clause)
    def business_hits(value):
        found = [(m.start(), m.end(), name) for name in names for m in re.finditer(re.escape(name), value)]
        return [hit for hit in found if not any(other[0] <= hit[0] and other[1] >= hit[1] and other != hit for other in found)]
    hits = business_hits(compact)
    if not hits:
        # A written business subject can govern at most two short comma
        # continuations of this same sentence. Only a revenue-subject anchor
        # and subsequent financial/growth grammar are accepted; a new issuer,
        # business, total-company measure, quotation or sentence blocks it.
        pieces = list(re.finditer(r'[^,，。；;]+', prefix))
        for offset in range(max(0,len(pieces)-3),len(pieces)-1):
            anchor = pieces[offset]
            anchor_text = re.sub(r'\s+', '', anchor[0])
            anchors = business_hits(anchor_text)
            if len(anchors)!=1 or not re.fullmatch(r'(?:为|是)(?:主要|核心)?创收(?:模块|业务)',anchor_text[anchors[0][1]:]):
                continue
            following = [re.sub(r'\s+', '', piece[0]) for piece in pieces[offset+1:]]
            if not following or len(following)>2 or len(''.join(following))>120:
                continue
            stripped=[]
            for value in following:
                clean=YEAR_RE.sub('',value)
                clean=VALUE_RE.sub('',clean)
                stripped.append(clean)
            grammar=r'(?:(?:营业收入|营收|收入)|进一步|同比|环比|增长|下降|下滑|增加|减少|提升|提高|回升|达到|达|至|为|约|近)*'
            if not all(re.fullmatch(grammar,value) for value in stripped):
                continue
            if not any(re.search(r'营业收入|营收|收入',value) for value in following):
                continue
            _,end,name=anchors[0]
            inherited={'source_text':prefix[anchor.start():], 'business_caption':name,
                       'account_caption':next(re.search(r'营业收入|营收|收入',value)[0] for value in following
                                              if re.search(r'营业收入|营收|收入',value)),
                       'subject_prefix':anchor_text[:anchors[0][0]],
                       'caption_relation':'bounded_same_subject_continuation',
                       'continuation_clauses':[piece[0] for piece in pieces[offset+1:]]}
            return {'dimension_kind':'business','dimension_id':name,'account':'REVENUE',
                    'dimension_source':{'schema_version':'source-business-caption.v1',**inherited}}
    if len(hits) != 1:
        return {}
    begin, end, name = hits[0]
    tail = compact[end:]
    labels = [(match.start(), match.end(), code) for label, code in
              (('营业收入','REVENUE'), ('营收','REVENUE'), ('收入','REVENUE'),
               ('营业成本','COST'), ('成本','COST'), ('毛利率','GROSS_MARGIN'))
              for match in re.finditer(re.escape(label), tail)]
    labels = [hit for hit in labels if not any(other[0] <= hit[0] and other[1] >= hit[1] and other != hit for other in labels)]
    if not labels:
        # A money-valued growth caption is financial data, but "service grew"
        # does not independently define revenue versus another money measure.
        # Preserve its source dimensions and parse its written number while
        # leaving the account unresolved for source-method work.
        target=VALUE_RE.match(text,numeric_start)
        growth=re.fullmatch(r'(?:整体)?(?:进一步)?(?:同比)?(?:增长|下降|下滑|增加|减少|达|达到)'
                            r'(?:[-+－−]?\d+(?:\.\d+)?[%％])?(?:至|到)?',tail)
        monetary_target=target and target['unit'] in {'元','万元','亿元','百万元'}
        monetary_following=target and target['unit'] in {'%','％'} and re.match(
            r'\s*(?:至|到)\s*[-+－−]?\d+(?:\.\d+)?\s*(?:百万元|亿元|万元|元)',text[target.end():])
        if growth and (monetary_target or monetary_following):
            return {'dimension_kind':'business','dimension_id':name,'account':'UNKNOWN',
                    'dimension_source':{'schema_version':'source-business-caption.v1',
                        'source_text':clause,'business_caption':name,'account_caption':'',
                        'subject_prefix':compact[:begin],'measure_method_unresolved':True}}
    if len(labels) != 1 or labels[0][0] > 35:
        return {}
    account = labels[0][2]
    if any(term in tail[labels[0][1]:] for term in ('占比', '占总', '比重', '比例')):
        return {}
    return {'dimension_kind': 'business', 'dimension_id': name, 'account': account,
            'dimension_source': {'schema_version': 'source-business-caption.v1',
                                  'source_text': clause, 'business_caption': name,
                                  'account_caption': tail[labels[0][0]:labels[0][1]],
                                  'subject_prefix':compact[:begin]}}


def customer_source_dimensions(text: str, numeric_start: int) -> dict:
    """Bind a source-defined customer aggregate; a bare ``占比`` is insufficient.

    This recognises only an explicit top-five customer revenue amount or a
    share with total issuer revenue as its written denominator. It does not
    infer identities for anonymised customers, or substitute customer-group
    consolidation for the issuer's financial statement basis.
    """
    clause = re.split(r'[,，。；;]', text[:numeric_start])[-1]
    compact = re.sub(r'\s+', '', clause)
    matches = list(re.finditer(r'前五大客户(?:的)?(?:营业收入|营收|收入)', compact))
    if len(matches) != 1:
        return {}
    match = matches[0]
    tail = compact[match.end():]
    target = VALUE_RE.match(text, numeric_start)
    if target is None:
        return {}
    ratio = re.fullmatch(r'占(?:公司(?:的)?|总)?(?:营业收入|营收|收入)(?:的)?(?:比例|比重|占比)(?:约|近|达|为|达到)?', tail)
    if ratio and target['unit'] in {'%', '％'}:
        account, definition = 'REVENUE_SHARE', 'metric:revenue-share'
    elif target['unit'] in {'元', '万元', '亿元', '百万元'} and re.fullmatch(r'(?:约|近|达|为|达到)?', tail):
        account, definition = 'REVENUE', ''
    else:
        return {}
    return {'dimension_kind': 'customer', 'dimension_id': '前五大客户', 'account': account,
            'definition_id': definition,
            'dimension_source': {'schema_version': 'source-customer-caption.v1',
                                 'source_text': clause, 'customer_caption': '前五大客户',
                                 'account_caption': compact[match.start():],
                                 'subject_prefix': compact[:match.start()]}}

def infer_relation(text: str, start: int, end: int, unit: str = "", account: str = "") -> str:
    prefix = text[max(0, start-22):start]
    # Growth language before another clause must not turn the next absolute
    # margin into a growth rate ("收入同比增长20%，毛利率25%") .
    prefix = re.split(r'[,，。；;]', prefix)[-1]
    if unit == '百分点':
        return 'pp_difference'
    if unit not in {"%", "％", "百分点"}:
        return "value"
    if "环比" in prefix:
        return "qoq"
    if "CAGR" in prefix or "复合" in prefix:
        return "unsupported"
    from .metrics import RATIO_METRICS
    if (account in RATIO_METRICS or account in {'GROSS_MARGIN', 'NET_MARGIN', 'NET_MARGIN_UNSPECIFIED', 'NET_MARGIN_PARENT_UNSPECIFIED',
                    'DEBT_RATIO', 'ROE', 'ROE_UNSPECIFIED'} or
            account.endswith('_RATIO_UNSPECIFIED')):
        compact_prefix = re.sub(r'\s+', '', prefix)
        if re.search(r'(?:至|到|为|达到)(?:20\d{2}年(?:的)?)?$', compact_prefix):
            return 'value'
    if re.search(r"(?:毛利率|净利率|资产负债率|净资产收益率).*(?:至|为|达到)$", prefix):
        return "value"
    if any(term in prefix for term in ("同比", "上年同期", "增长", "下降", "增速")):
        return "yoy"
    if text[start:end].startswith(("+", "-", "－", "−")) and re.search(r"[（(]\s*$", prefix):
        return "yoy"
    return "value"

def percentage_point_dimensions(text: str, numeric_start: int) -> dict:
    """Resolve a source-written rate difference, never a model's proposal.

    The rate, direction and both periods must be supported by the original
    clause. A bare ``百分点`` cannot authorize a guessed baseline. Returned
    metadata contains no candidate or true amount and is safe for binders.
    """
    target = VALUE_RE.match(text, numeric_start)
    if not target or target['unit'] != '百分点':
        return {}
    prefix = re.split(r'[。！？!?；;]', text[:numeric_start])[-1]
    compact = re.sub(r'\s+', '', prefix)
    account = _account_before(prefix, len(prefix))
    if account not in {'GROSS_MARGIN', 'NET_MARGIN', 'DEBT_RATIO', 'ROE'}:
        return {}
    direction = re.search(r'(提升|提高|上升|增加|扩大|下降|下滑|减少|降低|回落|收窄)'
                          r'(?:了|约|大约|近|达|达到|为)?$', compact)
    if not direction:
        return {}
    decrease = direction[1] in {'下降', '下滑', '减少', '降低', '回落', '收窄'}
    period_pattern = (r'20\d{2}(?:年(?:度|上半年|下半年|半年度|前三季度|第?[一二三四1-4]季度)?'
                      r'|H[12]|Q[1-4]|9M|Y)')
    comparisons = list(re.finditer(
        rf'(?P<current>{period_pattern})[^，,。；;%％]{{0,80}}?'
        rf'(?:相较于?|相比于?|对比|较|比)(?P<base>{period_pattern})', compact, re.I))
    if len(comparisons) == 1:
        current, base = (period_from_text(comparisons[0][field]) for field in ('current', 'base'))
        base_source = 'explicit_period_comparison'
    elif comparisons:
        return {}
    else:
        years = {period_from_text(match[0]) for match in re.finditer(period_pattern, compact, re.I)}
        if len(years) != 1:
            return {}
        current = next(iter(years))
        modes = {kind for word, kind in (('同比', 'yoy'), ('环比', 'qoq'),
                 ('较上年', 'yoy'), ('比上年', 'yoy'), ('较去年', 'yoy'), ('比去年', 'yoy'),
                 ('较上一季度', 'qoq'), ('比上一季度', 'qoq'), ('较上季', 'qoq'), ('比上季', 'qoq'))
                 if word in compact}
        if len(modes) != 1:
            return {}
        base = previous_period(current, next(iter(modes)))
        base_source = 'registered_relative_period'
    matching_kinds = current[4:] == base[4:] or (
        re.fullmatch(r'Q[1-4]', current[4:]) and re.fullmatch(r'Q[1-4]', base[4:]))
    if not current or not base or current == base or not matching_kinds:
        return {}
    raw = target['num']
    conflict = (_decimal(raw) < 0 and not decrease) or (raw.startswith('+') and decrease)
    return {'account': account, 'period': current, 'relation': 'pp_difference', 'base_period': base,
            'percentage_point_direction': 'decrease' if decrease else 'increase',
            'percentage_point_base_source': base_source,
            'percentage_point_direction_conflict': conflict}


def cagr_source_dimensions(text: str, numeric_start: int) -> dict:
    prefix = re.split(r'[。！？!?；;]', text[:numeric_start])[-1]
    if not re.search(r'CAGR|复合(?:年)?(?:增长率|增速)', prefix, re.I):
        return {}
    pattern = r'(?P<start>20\d{2})\s*(?:年\s*)?[-－–—~～至到]\s*(?P<end>20\d{2})\s*年'
    spans = list(re.finditer(pattern, prefix))
    if len(spans) != 1 or int(spans[0]['end']) <= int(spans[0]['start']):
        return {}
    account = _account_before(prefix, len(prefix))
    if account == 'UNKNOWN':
        return {}
    return {'account': account, 'period': spans[0]['end']+'Y', 'base_period': spans[0]['start']+'Y',
            'relation': 'cagr', 'source_cagr_period': {'text': spans[0][0], 'years': int(spans[0]['end'])-int(spans[0]['start'])}}


def source_range_occurrence(text: str, numeric_start: int) -> dict:
    units = r'百万元|亿元|万元|元|[%％]'
    pattern = rf'(?P<low>{NUM})\s*(?P<low_unit>{units})?\s*(?P<separator>[-－–—~～至到])\s*(?P<high>{NUM})\s*(?P<high_unit>{units})'
    for match in re.finditer(pattern, text):
        lower_unit, upper_unit = match['low_unit'], match['high_unit']
        if lower_unit and lower_unit != upper_unit and {lower_unit,upper_unit} != {'%','％'}:
            continue
        if numeric_start == match.start('low'):
            raw, unit = match['low'], lower_unit or upper_unit
        elif (numeric_start == match.start('high') or
              numeric_start == match.start('separator') and match['separator'] in {'-','－'} and
              match.start('high') == match.end('separator') and not match['high'].startswith(('-', '－', '−', '+'))):
            # VALUE_RE observes the hyphen connector as the high number's
            # sign. Preserve that exact raw occurrence, parse the high endpoint
            # from the independently matched range structure instead.
            raw, unit = match['high'], upper_unit
        else:
            continue
        lower = _decimal(match['low'])*unit_multiplier(lower_unit or upper_unit)
        upper = _decimal(match['high'])*unit_multiplier(upper_unit)
        if lower>upper:
            continue
        return {'unit':unit, 'number':str(_decimal(raw)),
                'amount_range':{'lower':str(lower),'upper':str(upper),'unit':unit,
                                'source_span':{'start':match.start(),'end':match.end(),'text':match[0]}}}
    return {}


def extract_mentions(text: str, company: str = "", fallback_period: str = "", *, preserve_occurrences: bool = False) -> list[AtomicClaim]:
    claims: list[AtomicClaim] = []
    seen: set[tuple] = set()
    from .source_modality import prose_modality_exclusions
    modality_exclusions = prose_modality_exclusions(text, [])
    for match in VALUE_RE.finditer(text):
        raw, unit = match["num"], match["unit"] or ""
        start, end = match.span()
        amount_range = source_range_occurrence(text, start)
        if amount_range:
            unit = amount_range['unit']
        currency = source_currency(text, start)
        ordered_period, ordered_basis = listed_periods(text, start)
        if not unit and ordered_basis and not ordered_basis.get('ambiguous'):
            unit = ordered_basis.get('source_unit') or ''
        if (not unit and not currency) or (not amount_range and start and text[start-1].isalnum() and text[start-1].isascii()):
            continue
        if end < len(text) and text[end] in "年月日" and not currency:
            continue
        line_left, line_right = _line_bounds(text, start, end)
        line = text[line_left:line_right]
        left, right = _sentence_bounds(text, start, end)
        if ordered_basis and not ordered_basis.get('ambiguous') and ordered_basis.get('source') == 'explicit_secondary_ordered_period_list':
            # Keep the complete source-proof chain in every secondary-list
            # claim, including a wrapped middle cell. Local frozen semantic
            # derivation can then revalidate the counts rather than borrowing
            # an absolute period hint from outside its immutable excerpt.
            chain_left, chain_right = ordered_basis['source_context_span']
            left, right = min(left, chain_left), max(right, chain_right)
        if not re.search(r"[\u4e00-\u9fff]", line[:start-line_left]):
            # A number at the start of a wrapped prose line may still have a
            # directly preceding financial clause. Bare chart/table numbers
            # lack this bounded Chinese financial prefix and stay unresolved.
            if (line[:start-line_left].strip() or left>=line_left or
                    not re.search(r"[\u4e00-\u9fff]",text[left:start]) or
                    not has_financial_context(re.sub(r'\s+','',text[left:start]))):
                continue
        if any(term in line for term in NOISE_TERMS) or re.search(r"[.．…]{4,}", line):
            continue
        sentence = text[left:right].strip()
        compact = re.sub(r"\s+", "", sentence)
        business = business_source_dimensions(text[left:right], start-left)
        if match.start('num') in modality_exclusions or not (has_financial_context(compact) or business):
            continue
        account = _account_before(text[left:right], start-left)
        customer = customer_source_dimensions(text[left:right], start-left)
        dimension = business or customer
        if dimension and account == 'UNKNOWN':
            account = dimension['account']
        account, definition_id = source_metric_identity(account, text[left:right], start-left)
        definition_id = customer.get('definition_id') or definition_id
        relation = infer_relation(text, start, end, unit, account)
        if relation == "unsupported":
            # Keep the source relation instead of relabelling a growth rate
            # as an amount. Judge also enforces the raw-source guard after
            # any model binding, which may replace ``relation``.
            relation = "cagr"
        if unit in {"%", "％", "百分点"} and relation == "value" and account in AMOUNT_ACCOUNTS:
            account = "UNKNOWN"
        if unit in {"万", "股", "万股"} or unit=='倍' and account not in SOURCE_REVIEW_SCALARS|{'CURRENT_RATIO'}:
            account = "UNKNOWN"
        ordered_period, ordered_basis = listed_periods(text[left:right], start-left)
        local_period = ordered_period if ordered_period is not None else period_from_text(text[left:start], fallback_period)
        pp_dimensions = percentage_point_dimensions(text[left:right], start-left) if unit == '百分点' else {}
        cagr_dimensions = cagr_source_dimensions(text[left:right], start-left) if relation == 'cagr' else {}
        if cagr_dimensions:
            local_period, account = cagr_dimensions['period'], cagr_dimensions['account']
        if pp_dimensions:
            local_period = pp_dimensions['period']
        value = _decimal(amount_range['number'] if amount_range else raw)
        if pp_dimensions.get('percentage_point_direction') == 'decrease' and not raw.startswith(('+', '-', '－', '−')):
            value = -value
        if relation in {'yoy', 'qoq'} and not raw.startswith(('+', '-', '－', '−')):
            rate_prefix = re.split(r'[,，。；;]', text[max(left, start-30):start])[-1]
            if re.search(r'(?:下降|下滑|减少|降低|下跌|回落|缩减)\s*(?:约|近|了|达|为)?\s*$', rate_prefix):
                value = -value
        candidate = str(value * unit_multiplier(unit))
        key = (account, local_period, relation, candidate, unit, sentence)
        if key in seen and not preserve_occurrences:
            continue
        seen.add(key)
        scope = "母公司" if re.search(r"母公司(?:报表|口径|营业收入|营业成本|净利润|经营现金流)", compact) else "合并"
        # Adjustment is local to this financial occurrence. An earlier
        # adjusted profit in the same sentence does not adjust revenue or
        # the following unadjusted parent profit.
        local_caliber = re.split(r'[,，。；;]', text[left:start])[-1]
        nr = "扣非" if account == "NP_PARENT_ADJ" or ("扣非" in local_caliber and "未扣非" not in local_caliber) else "未扣非"
        claims.append(AtomicClaim(
            claim_id=f"claim-{len(claims)+1:05d}", text=sentence, start=start, end=end,
            claim_type="calculation" if relation != "value" else "numeric", company=company,
            account=account, period=local_period, candidate_value=candidate, unit=unit,
            caliber_hint=(scope, nr),
            relation=relation, base_period=pp_dimensions.get('base_period') or cagr_dimensions.get('base_period') or previous_period(local_period, relation) or None,
            display_decimals=max(0, -value.as_tuple().exponent), numeric_text=text[start:end],
            source_numeric_start=start-left,
            parse_status='parsed',
            table_context={**({'source_currency':currency} if currency else {}),
                           **({'definition_id': definition_id} if definition_id else {}),
                           **({'amount_range': amount_range['amount_range']} if amount_range else {}),
                           **({'source_cagr_period': cagr_dimensions['source_cagr_period']} if cagr_dimensions else {}),
                            **({key: dimension[key] for key in ('dimension_kind','dimension_id','dimension_source')} if dimension else {}),
                           **({'source_period_list':ordered_basis} if ordered_basis else {}),
                           **({'numeric_parse_status':'parsed','semantic_review_required':True,
                               'source_account_unresolved':account} if account.endswith('_UNSPECIFIED') else {})},
        ))
    labels = "|".join(account_label_pattern(label) for label, account in ACCOUNT_LABELS if account != "UNKNOWN")
    comparative = re.compile(rf"(?P<current>20\d{{2}}\s*(?:年|Y))(?P<scope>母公司口径|合并口径)?(?P<label>{labels})\s*(?P<relation>不低于|不高于|高于|低于|大于|小于|等于)\s*(?P<base>20\d{{2}}\s*(?:年|Y))")
    for match in comparative.finditer(text):
        left, right = _sentence_bounds(text, match.start(), match.end())
        sentence = text[left:right].strip()
        if any(term in sentence for term in FORECAST_TERMS):
            continue
        account = infer_account(match['label'])
        relation = {'高于': 'gt', '大于': 'gt', '低于': 'lt', '小于': 'lt', '不低于': 'ge', '不高于': 'le', '等于': 'eq'}[match['relation']]
        claims.append(AtomicClaim(f"claim-{len(claims)+1:05d}", sentence, match.start(), match.end(), claim_type='comparison', company=company, account=account,
            period=period_from_text(match['current']), caliber_hint=('母公司' if match['scope'] == '母公司口径' else '合并', '扣非' if account == 'NP_PARENT_ADJ' else '未扣非'),
            relation=relation, base_period=period_from_text(match['base'])))
    return claims

def parse_number_claim(raw: str, relation: str = "value") -> tuple[Decimal, str]:
    match = VALUE_RE.search(raw)
    if not match:
        raise ValueError(f"无法解析数值: {raw}")
    return _decimal(match["num"]) * unit_multiplier(match["unit"] or ""), match["unit"] or ""
