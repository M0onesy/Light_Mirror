"""研报陈述抽取与分级：正文数字提及 + 预测/财务摘要表 + 首页行情框。

每条陈述带：指标、关系、期间、主体、单位精度、置信度与分级（core/forecast/external/…）。
抽取是确定性的；可选的模型绑定只能改写枚举字段（指标/期间/关系/主体），不能改数值。
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from decimal import Decimal

from .common import (IV, PERCENT_UNITS, MONEY_UNITS, compact, decimals_of, find_periods, norm, quantum, relative_periods, to_decimal, unit_scale)
from .detect import DocMeta
from .docio import Document, Row, body_lines
from .tables import Tok, tokenize
from .taxonomy import ALIAS_MAP, ALIAS_RE, metric_from_label

RATIO_METRICS = {'GROSS_MARGIN', 'NET_MARGIN', 'NP_PARENT_MARGIN', 'OPERATING_MARGIN', 'ROE', 'ROE_WEIGHTED', 'ROA', 'ROIC',
                 'DEBT_RATIO', 'NET_DEBT_RATIO', 'SELLING_RATIO', 'ADMIN_RATIO', 'RD_RATIO', 'FINANCE_RATIO', 'PERIOD_EXPENSE_RATIO'}
PLAIN_METRICS = {'CASH_CONTENT', 'EPS', 'EPS_BASIC', 'EPS_DILUTED', 'EPS_LATEST', 'BPS', 'CFO_PER_SHARE', 'CURRENT_RATIO', 'QUICK_RATIO',
                 'ASSET_TURNOVER', 'AR_TURNOVER', 'AP_TURNOVER', 'INVENTORY_TURNOVER', 'PE', 'PB', 'PS', 'EV_EBITDA'}
VALUATION_METRICS = {'PE', 'PB', 'PS', 'EV_EBITDA'}
MARKET_METRICS = {'MARKET_CAP', 'PRICE', 'TARGET_PRICE'}
OPEN_METRICS = {'EBITDA', 'EBIT', 'ROIC', 'NET_DEBT_RATIO', 'EV_EBITDA', 'DA', 'TEMPLATE_ITEM', 'LONG_TERM_INVEST',
                'QUICK_RATIO', 'ASSET_TURNOVER', 'AR_TURNOVER', 'AP_TURNOVER', 'INVENTORY_TURNOVER', 'IMPAIRMENT', 'CAPEX'}
_NUM = r'\d+(?:,\d{3})*(?:\.\d+)?'
MENTION_RE = re.compile(
    r'(?P<sign>[+\-]?)(?P<num>' + _NUM + r')(?P<series>(?:\s*/\s*[+\-]?' + _NUM + r')+)?\s*'
    r'(?P<unit>亿元|万元|百万元|千元|元/股|亿股|万股|个百分点|百分点|个pct|pcts|pct|pp|bp|BP|亿|万|元|%|倍|x(?![A-Za-z])|X(?![A-Za-z]))?')
_COUNT_UNIT = re.compile(r'\s*(?:万|亿|千|百|余)?(?:台|家|人|名|项|个|套|颗|张|座|条|款|次|笔|户|核时|卡时|小时|天|年|月|日|GW|MW|KW|kW|吨|万吨|辆|件|平方米|亩|%?的)')
_EXTERNAL = re.compile(r'市场规模|行业规模|市场空间|市场容量|全球|全国|我国|国内市场|行业(?:整体|平均|规模|增速)|渗透率|出货量|装机量|市占率|市场份额|份额|CR\d|同业|可比公司|竞争对手|产业规模|算力规模|用户规模|网民')
_FORECAST = re.compile(r'预计|预测|有望|将达|将实现|预期|目标|指引|E(?![A-Za-z])|展望|假设|测算')
_GROWTH_WORD = re.compile(r'(同比|同增|同减|yoy|YoY|YOY|较上年同期|较去年同期|比上年同期|较上年|比上年|较去年|环比|qoq|QoQ|增长|下降|增加|减少|提升|下滑|上升|回落|增速|增幅|降幅|扩大|收窄|提高|降低)')
_DOWN = re.compile(r'下降|减少|下滑|回落|降幅|降低|同减|减亏|缩减|下跌')
_HEDGE = [('approx', re.compile(r'(?:大约|约为|约|大致|左右)\s*$')), ('above', re.compile(r'(?:超过|超|逾|突破|高于|不低于|多于|以上)\s*$')),
          ('below', re.compile(r'(?:不足|不到|低于|少于|不高于|小于)\s*$')), ('near', re.compile(r'(?:接近|将近|近)\s*$'))]
_GENERIC_NOUN = re.compile(r'公司|合并|报表|口径|销售|经营|累计|营业|利息|投资|其他|补贴|实现|单季|季度|同期|当期|全年|年度|整体|合计|总|净|上年|去年|本期|主营|业务$|预计|分别|对应|带动|促进|推动|导致|使得|以及|的$|为$|和$|与$|及$|元$|其中|来自|贡献')
_CITE = re.compile(r'(?:(?P<pre>据|根据|援引|引自|来自|参考|按照|依据)\s*(?:公司)?\s*《?\s*(?P<y1>20\d{2})\s*年\s*(?P<k1>年度报告|年报|半年度报告|半年报|中期报告|中报|第一季度报告|一季度报告|一季报|第三季度报告|三季度报告|三季报)》?)'
                   r'|(?:《?\s*(?P<y2>20\d{2})\s*年\s*(?P<k2>年度报告|年报|半年度报告|半年报|中期报告|中报|第一季度报告|一季度报告|一季报|第三季度报告|三季度报告|三季报)》?\s*(?:中|内)?\s*(?:显示|披露|所示|称|数据显示))')
_CITE_KIND = {'年度报告': ('annual_report', 'Y'), '年报': ('annual_report', 'Y'), '半年度报告': ('semiannual_report', 'H1'), '半年报': ('semiannual_report', 'H1'),
              '中期报告': ('semiannual_report', 'H1'), '中报': ('semiannual_report', 'H1'), '第一季度报告': ('quarterly_report', 'Q1'),
              '一季度报告': ('quarterly_report', 'Q1'), '一季报': ('quarterly_report', 'Q1'), '第三季度报告': ('quarterly_report', '9M'),
              '三季度报告': ('quarterly_report', '9M'), '三季报': ('quarterly_report', '9M')}
_LIST_TAIL = re.compile(r'(?:[+\-]?\d+(?:,\d{3})*(?:\.\d+)?\s*(?:%|亿元|万元|元|倍|个百分点|百分点)?\s*[、,，和及与]\s*)+$')
_BOX_LINE = re.compile(r'^(?:日期|当前股价|收盘价|一年最高最低|总市值|流通市值|总股本|流通股本|近\d个月换手率|52周|投资评级|目标价|所属行业|上次评级)')
_FOREIGN = re.compile(r'\s*(?:美元|美金|港元|港币|欧元|日元|英镑|USD|HKD|EUR)')
_GENERIC_SUBJECT = re.compile(r'^(?:公司|本公司|全年|实现|同比|整体|总|主营|营业|合计|其中|报告期|上半年|下半年|季度|年度|单季|单季度|累计|对应|我们|预计|分别|以及|同期|当期|期内|年|的|和|与|及|为)+$')


@dataclass
class Claim:
    cid: str = ''
    kind: str = 'prose'            # prose / table / box
    page: int = 0
    text: str = ''
    num_text: str = ''
    value: str = '0'               # 基本单位（元/比例/纯数）下的 Decimal 字符串
    unit: str = ''
    decimals: int = 0
    metric: str = ''
    metric_text: str = ''
    cite: str = ''                 # 句中明示的出处，如 annual_report:2023Y
    cite_text: str = ''
    modality: str = 'fact'         # fact 事实 / inference 推论（预测、估算）/ opinion 观点（目标价等）
    relation: str = 'value'        # value / yoy / qoq / cagr / share / pp
    period: str = ''
    base_period: str = ''
    entity: str = 'company'        # company / segment / external / market
    segment: str = ''
    denominator: str = ''          # 占比分母（空=公司总口径）
    category: str = 'core'         # core / open_metric / forecast / external / operational / market / template / other_fact / unbound
    forecast: bool = False
    hedge: str = ''
    confidence: float = 1.0
    notes: list = field(default_factory=list)
    table: str = ''
    row_label: str = ''
    span: tuple = (0, 0)
    occurrences: int = 1
    pages: list = field(default_factory=list)
    binder: str = 'rule'
    bbox: list | None = None       # 原文位置（坐标后端可用时为所在版面行），用于研报原页高亮
    scope: str = ''                # 空表示未声明；标准模式可推定，审慎模式阻断
    currency: str = 'CNY'
    definition_id: str = ''
    threshold_operator: str = ''   # gt/ge/lt/le；独立于同比、占比等计算关系
    assertion_id: str = ''         # 相同断言分组；每一原文出现位置仍有独立 cid
    source_paragraph_id: int = -1

    def to_dict(self) -> dict:
        data = asdict(self)
        data['span'] = list(self.span)
        return data

    def decimal(self) -> Decimal:
        return Decimal(self.value)

    def interval(self) -> IV:
        scale_unit = self.unit if self.unit else ''
        return IV.rounded(self.decimal(), quantum(scale_unit, self.decimals))


@dataclass
class ReportTable:
    name: str
    kind: str                      # BS / IS / CF / RATIO / SUMMARY
    unit: str
    years: list                    # [(period, forecast)]
    page: int
    rows: list = field(default_factory=list)   # dict(label, metric, relation, unit, cells=[(text, Decimal|None)], row_text)
    scope: str = ''


@dataclass
class ReportModel:
    meta: DocMeta
    claims: list
    tables: list
    market: dict                   # PRICE / MARKET_CAP / TOTAL_SHARES → (Decimal, unit, decimals, page, text)
    stats: dict


_YEAR_TOKEN = re.compile(r'^(20\d{2})(?:年|年度)?([AE])?$', re.I)


def _table_kind(title: str) -> str:
    title = compact(title)
    for key, kind in (('资产负债表', 'BS'), ('利润表', 'IS'), ('损益表', 'IS'), ('现金流量表', 'CF'), ('财务比率', 'RATIO'),
                      ('主要指标', 'RATIO'), ('比率', 'RATIO'), ('估值', 'SUMMARY'), ('财务摘要', 'SUMMARY'), ('盈利预测', 'SUMMARY'),
                      ('指标', 'SUMMARY'), ('会计年度', 'SUMMARY'), ('单位', 'SUMMARY')):
        if key in title:
            return kind
    return 'SUMMARY'


def _unit_in(text: str) -> str:
    found = re.search(r'[(（:]?(?:单位[:：]?)?(?:人民币)?(百万元|亿元|万元|千元|元)[)）]?', compact(text))
    return found[1] if found else ''


def _segments(tokens: list[Tok]) -> list[tuple[list[Tok], list[Tok]]]:
    """把一行切成若干（标签 tokens, 数值/年份 tokens）段，支持左右并排的两张表。"""
    output, label, run = [], [], []
    for token in tokens:
        is_value = token.kind in ('num', 'blank') or bool(_YEAR_TOKEN.match(token.text))
        if is_value:
            run.append(token)
        else:
            if run:
                output.append((label, run))
                label, run = [], []
            label.append(token)
    if label or run:
        output.append((label, run))
    return output


def parse_report_tables(doc: Document, base_year: int | None) -> list[ReportTable]:
    tables: list[ReportTable] = []
    for page in doc.pages:
        groups: list[dict] = []          # 当前页活动表：x0, table
        last_metric: dict[int, str] = {}
        page_unit = ''
        page_scope = ''
        for row in page.rows:
            text = compact(row.text)
            if not text:
                continue
            if re.search(r'母公司(?:资产负债表|利润表|现金流量表|报表)', text):
                page_scope = '母公司'
            elif re.search(r'合并(?:资产负债表|利润表|现金流量表|报表)', text):
                page_scope = '合并'
            if re.search(r'[.．…·]{4,}\d{1,4}$', text):
                continue
            unit_here = re.search(r'单位[:：]?(?:人民币)?(百万元|亿元|万元|千元|元)', text)
            if unit_here:
                page_unit = unit_here[1]
            if re.match(r'^(?:数据|资料)来源', text):
                groups = []
                continue
            for label_tokens, run in _segments(tokenize(row)):
                label = ''.join(t.text for t in label_tokens)
                x0 = label_tokens[0].x0 if label_tokens else (run[0].x0 if run else 0.0)
                years = [_YEAR_TOKEN.match(t.text) for t in run]
                if len(run) >= 3 and all(years):
                    cols = []
                    for match in years:
                        year = int(match[1]); flag = (match[2] or '').upper()
                        forecast = flag == 'E' or (not flag and base_year is not None and year > base_year)
                        cols.append((f'{year}Y', forecast))
                    table = ReportTable(label or '财务摘要', _table_kind(label or '财务摘要'), _unit_in(label) or page_unit, cols, page.number)
                    table.scope = page_scope
                    table._x0 = x0; table._cols_x = [(t.x0, t.x1) for t in run]
                    near = next((g for g in groups if abs(g['x0'] - x0) <= (25 if row.geometry else 12)), None)
                    if near:
                        near['table'] = table
                    else:
                        groups.append({'x0': x0, 'table': table})
                        groups.sort(key=lambda g: g['x0'])
                    tables.append(table)
                    continue
                if not groups or not label or not run:
                    continue
                group = min(groups, key=lambda g: abs(g['x0'] - x0)) if len(groups) > 1 else groups[0]
                table = group['table']
                numbers = [t for t in run if t.kind in ('num', 'blank')]
                if len(numbers) != len(table.years):
                    if row.geometry and 0 < len(numbers) < len(table.years):
                        aligned = [None] * len(table.years)
                        for token in numbers:
                            best = min(range(len(table.years)), key=lambda i: abs(table._cols_x[i][1] - token.x1))
                            aligned[best] = token
                        numbers = aligned
                    else:
                        continue
                mapped = metric_from_label(label, table.kind) or metric_from_label(label, 'RATIO' if table.kind == 'SUMMARY' else table.kind)
                metric, relation, unit = mapped if mapped else ('', 'value', '')
                key = id(table)
                if metric == '__PREV__':
                    metric = last_metric.get(key, '')
                elif metric and relation == 'value':
                    last_metric[key] = metric
                table.rows.append({'label': label, 'metric': metric, 'relation': relation, 'unit': unit, 'row_text': row.text, 'page': page.number, 'bbox': row.bbox,
                                   'cells': [(t.text, to_decimal(t.text) if t is not None and t.kind == 'num' else None) if t is not None else ('', None) for t in numbers]})
    return [table for table in tables if table.rows]


_BOX_LABEL = re.compile(r'^(?P<label>当前股价|最新收盘价|最新股价|收盘价|股价|现价|总市值|流通市值|总股本)'
                        r'(?:[(（](?P<unit>[^()（）]{1,5})[)）])?[:：]?(?P<rest>.*)$')
_BOX_VALUE = re.compile(r'^(?P<num>\d+(?:,\d{3})*(?:\.\d+)?)(?P<unit>亿元|百万元|万元|元|亿股|百万股|万股)?$')
_BOX_KEY_OF = {'当前股价': 'PRICE', '最新收盘价': 'PRICE', '最新股价': 'PRICE', '收盘价': 'PRICE', '股价': 'PRICE', '现价': 'PRICE',
               '总市值': 'MARKET_CAP', '流通市值': 'FLOAT_CAP', '总股本': 'TOTAL_SHARES'}
_BOX_UNITS = {'PRICE': ('元',), 'MARKET_CAP': ('亿元', '百万元', '万元'), 'FLOAT_CAP': ('亿元', '百万元', '万元'),
              'TOTAL_SHARES': ('亿股', '百万股', '万股')}
_BOX_FLOW = re.compile(r'(当前股价|最新收盘价|最新股价|收盘价|总市值|流通市值|总股本)(?:[(（]([^()（）]{1,5})[)）])?[:：]?'
                       r'(\d+(?:,\d{3})*(?:\.\d+)?)(亿元|百万元|万元|元|亿股|百万股|万股)?')


def _box_item(key: str, number: str, unit: str, page: int, text: str, source: str, bbox=None) -> dict | None:
    unit = (unit or '').replace('人民币', '')
    unit = unit if unit in _BOX_UNITS[key] else _BOX_UNITS[key][0]
    value = to_decimal(number)
    if value is None or value <= 0:
        return None
    return {'value': str(value * unit_scale(unit)), 'unit': unit, 'decimals': decimals_of(number), 'page': page,
            'text': text, 'source': source, 'bbox': bbox}


def parse_market_box(doc: Document) -> dict:
    """首页行情框：按版面行读取“标签—数值”对，支持同一行并排、标签与数值分列、上下两行对齐三种版式。

    阅读序文本会把侧栏标签与数值拆到不同行（“总市值(亿元)”后面紧跟的可能是股价），
    因此只有版面行读不到时，才在阅读序文本的同一行内匹配，绝不跨行拼接。
    """
    market: dict = {}
    for page in doc.pages[:2]:
        pending: list = []
        for row in page.rows:
            cells = [compact(cell.text) for cell in row.cells]
            labels, numbers = [], []
            index = 0
            while index < len(cells):
                found = _BOX_LABEL.match(cells[index])
                if not found:
                    value = _BOX_VALUE.match(cells[index])
                    if value:
                        numbers.append((value, row.cells[index]))
                    index += 1
                    continue
                key, unit, rest = _BOX_KEY_OF[found['label']], found['unit'] or '', found['rest']
                value = _BOX_VALUE.match(rest) if rest else None
                if value is None and not rest and index + 1 < len(cells) and not _BOX_LABEL.match(cells[index + 1]):
                    value = _BOX_VALUE.match(cells[index + 1])
                    if value:
                        index += 1
                if value is not None:
                    shown = f"{found['label']}{'(' + unit + ')' if unit else ''} {value['num']}{value['unit'] or ''}"
                    item = _box_item(key, value['num'], value['unit'] or unit, page.number, shown, 'layout_row', row.bbox)
                    if item and key not in market:
                        market[key] = item
                elif not rest:
                    labels.append((key, unit, found['label'], row.cells[index]))
                index += 1
            if labels and not numbers:
                pending = labels                     # 竖排行情表：本行只有标签，数值在下一行
                continue
            if pending and numbers and not labels and len(numbers) == len(pending):
                for (key, unit, label, _), (value, _cell) in zip(pending, numbers):
                    item = _box_item(key, value['num'], value['unit'] or unit, page.number,
                                     f"{label}{'(' + unit + ')' if unit else ''} {value['num']}", 'layout_column', row.bbox)
                    if item and key not in market:
                        market[key] = item
            pending = []
    if not all(key in market for key in ('PRICE', 'MARKET_CAP', 'TOTAL_SHARES')):
        for page in doc.pages[:2]:
            for line in norm(page.flow).splitlines():
                for found in _BOX_FLOW.finditer(compact(line)):
                    key = _BOX_KEY_OF[found[1]]
                    if key not in market:
                        item = _box_item(key, found[3], found[4] or found[2] or '', page.number, found[0], 'flow_line')
                        if item:
                            market[key] = item
    return market


def market_consistency(market: dict, tolerance: str = '0.03') -> dict:
    """首页行情的交叉校验：股价×总股本≈总市值（容差覆盖取价日与股本口径的小幅差异）。"""
    keys = ('PRICE', 'TOTAL_SHARES', 'MARKET_CAP')
    if not all(key in market for key in keys):
        return {'status': 'incomplete', 'detail': '首页行情缺少股价、总股本或总市值，估值倍数改用研报表内数据勾稽'}
    price, shares, cap = (IV.rounded(Decimal(market[k]['value']), quantum(market[k]['unit'], market[k]['decimals'])) for k in keys)
    implied = price * shares
    consistent = implied.widen(Decimal(tolerance)).intersects(cap)
    gap = (cap.mid - implied.mid) / implied.mid if implied.mid else Decimal(0)
    return {'status': 'consistent' if consistent else 'inconsistent', 'implied_market_cap': str(implied.mid),
            'market_cap': market['MARKET_CAP']['value'], 'gap': f'{gap * 100:.2f}%',
            'detail': ('股价×总股本与总市值一致' if consistent else f'股价×总股本与首页总市值相差 {gap * 100:.2f}%，超出 {Decimal(tolerance) * 100}% 容差')}


# ───────────────────────── 正文 ─────────────────────────
def _clean_lines(doc: Document) -> list[tuple[int, str]]:
    per_page = [[line.strip() for line in body_lines(page)] for page in doc.pages]
    frequency = Counter(line for lines in per_page for line in set(re.sub(r'\d+', '#', l) for l in lines if l))
    limit = max(3, int(len(doc.pages) * 0.35))
    output: list[tuple[int, str]] = []
    total = len(doc.pages)
    for index, (page, lines) in enumerate(zip(doc.pages, per_page)):
        joined = compact(''.join(lines))
        if index >= total - 4 and re.search(r'法律声明|免责声明|分析师承诺|评级说明|特别声明|分析师声明', joined[:400]):
            break
        dotted = sum(bool(re.search(r'[.．…·]{6,}\s*\d{1,4}$', line)) for line in lines)
        if dotted >= 5:
            continue
        skip_numbers = False
        for line in lines:
            if not line:
                output.append((page.number, ''))
                continue
            if frequency[re.sub(r'\d+', '#', line)] >= limit and len(line) < 60:
                continue
            if re.search(r'[.．…·]{6,}\s*\d{1,4}$', line) or len(line) <= 2:
                continue
            if _BOX_LINE.match(compact(line)) or re.search(r'@|证书编号|执业证书|分析师[)）]|联系人', line):
                skip_numbers = True
                continue
            if not re.search(r'[\u4e00-\u9fff]', line) and not re.search(r'[A-Za-z]{3,}', line):
                continue                       # 纯数字/符号行：图表数据标签、行情框数值
            skip_numbers = False
            output.append((page.number, line))
    return output


def _width(line: str) -> int:
    return sum(2 if ord(ch) > 0x2E7F else 1 for ch in line)


def _sentences(lines: list[tuple[int, str]]) -> list[tuple[int, int, str]]:
    paragraphs: list[tuple[int, str]] = []
    buffer, buffer_page, previous_full = '', 0, True
    caption = re.compile(r'^(?:图表?\s*\d+|表\s*\d+)\s*[:：]')
    heading = re.compile(r'^(?:\d+(?:\.\d+)*[、.]\s*\D|[一二三四五六七八九十]+[、.]|[(（][一二三四五六七八九十\d]+[)）]|[●■◆▶➢•])')
    widths: dict[int, list] = {}
    for page, line in lines:
        if len(re.findall(r'[\u4e00-\u9fff]', line)) >= 8:
            widths.setdefault(page, []).append(_width(line))
    page_width = {page: sorted(values)[int(len(values) * 0.75)] if values else 1 for page, values in widths.items()}
    for page, line in lines:
        if not line:
            if buffer:
                paragraphs.append((buffer_page, buffer)); buffer = ''
            previous_full = True
            continue
        numeric_tokens = len(re.findall(_NUM, line))
        cjk = len(re.findall(r'[\u4e00-\u9fff]', line))
        if numeric_tokens >= 4 and cjk <= numeric_tokens * 3 and not re.search(r'[，。；,]', line) and not caption.match(line):
            if buffer:
                paragraphs.append((buffer_page, buffer)); buffer = ''
            continue
        standalone = bool(caption.match(line) or heading.match(line) or re.match(r'^(?:数据|资料)来源', line))
        ended = bool(re.search(r'[。！？!?]$', buffer)) or (not previous_full and not re.search(r'[，、：:,；;（(]$', buffer))
        if buffer and (standalone or ended):
            paragraphs.append((buffer_page, buffer)); buffer = ''
        previous_full = _width(line) >= 0.85 * page_width.get(page, 1)
        if standalone:
            paragraphs.append((page, line))
            previous_full = True
            continue
        if not buffer:
            buffer_page = page
        if buffer and re.search(r'\d\.?$', buffer) and re.match(r'^[\d.]+(?:\s*/\s*[+\-]?[\d.]+)*\s*(?:%|亿|万|元|个百分点|百分点|倍|pct)', line):
            joiner = ''                         # 数字被版面折行截断：“39.5 / 1%”“0 / .43元”
        else:
            joiner = '' if (buffer and re.search(r'[\u4e00-\u9fff，、（(]$', buffer)) or re.match(r'[\u4e00-\u9fff，。、）)%]', line) else ' '
        buffer = (buffer + joiner + line) if buffer else line
    if buffer:
        paragraphs.append((buffer_page, buffer))
    output = []
    for number, (page, paragraph) in enumerate(paragraphs):
        if re.match(r'^(?:数据来源|资料来源)', paragraph):
            continue
        paragraph = re.sub(r'^(?:图表?\s*\d+|表\s*\d+)\s*[:：]?\s*', '', paragraph)
        paragraph = re.sub(r'^\d+(?:\.\d+)*[、.]\s*', '', paragraph)
        for sentence in re.split(r'(?<=[。！？!?])', paragraph):
            sentence = sentence.strip()
            if len(sentence) >= 6 and re.search(r'\d', sentence):
                output.append((page, number, sentence))
    return output


def _relation(window: str, tail: str, unit: str, metric: str, signed: bool) -> tuple[str, float]:
    percent = unit in PERCENT_UNITS or unit in ('个百分点', '个pct', 'pcts', 'BP')
    if not percent:
        return 'value', 1.0
    if unit in ('个百分点', '百分点', '个pct', 'pcts', 'pct', 'pp', 'bp', 'BP'):
        return 'pp', 1.0
    if re.search(r'CAGR|复合增(?:速|长)|年均复合|复合年均', window + tail[:12], re.I):
        return 'cagr', 1.0
    near = window[-14:]
    if re.search(r'环比|qoq', near, re.I):
        return 'qoq', 1.0
    if re.search(r'占比|比重|占(?:公司|总|营业|营收|收入|全部|比)|占.{1,18}收入|比例', window[-30:]) and metric not in RATIO_METRICS:
        return 'share', 0.9
    if metric in RATIO_METRICS:
        if re.search(r'(?:同比|较上年|较去年)[^，,;；]{0,6}(?:提升|下降|增加|减少|上升|回落|提高|降低)\s*$', near) and not re.search(r'至|到|为|达', near[-3:]):
            return 'pp', 0.7
        return 'value', 1.0
    if re.search(r'同比|同增|同减|yoy|较上年|较去年|比上年', near, re.I):
        return 'yoy', 1.0
    if _GROWTH_WORD.search(near[-8:]):
        return 'yoy', 0.85
    if signed:
        return 'yoy', 0.8
    return 'yoy', 0.5


def extract_prose_claims(doc: Document, meta: DocMeta, segments: set[str] | None = None) -> list[Claim]:
    segments = {s for s in (segments or set()) if len(s) >= 2}
    base_year = meta.base_year
    claims: list[Claim] = []
    last_period_in_paragraph, last_paragraph = '', -1
    recent_year = None
    for page, paragraph_id, sentence in _sentences(_clean_lines(doc)):
        if paragraph_id != last_paragraph:
            last_period_in_paragraph, last_paragraph = '', paragraph_id
        text = norm(sentence)
        periods = find_periods(text)
        periods = sorted(periods + relative_periods(text, periods, recent_year), key=lambda m: m.start)
        explicit_years = [int(m.periods[-1][:4]) for m in periods if not m.forecast and re.match(r'20\d{2}', m.periods[-1])]
        if explicit_years:
            recent_year = explicit_years[-1]
        aliases = [(m.start(), m.end(), ALIAS_MAP[m[0]], m[0]) for m in ALIAS_RE.finditer(text)]
        aliases = [a for a in aliases if not any(p.start <= a[0] < p.end for p in periods)]
        mentions = []
        for match in MENTION_RE.finditer(text):
            start, end = match.start(), match.end()
            if any(p.start < match.end('num') and match.start('num') < p.end for p in periods):
                continue
            before = text[max(0, start - 1):start]
            after = text[match.end('num'):match.end('num') + 4]
            if before and (before.isalpha() and before.isascii() or before in '.#_'):
                continue
            if re.match(r'\.(?:SH|SZ|BJ|HK)', after, re.I) or re.match(r'\s*(?:月|日|年(?!化)|号|届|期(?!间)|季|楼|条|款|位|名(?!义)|代)', after) and not match['unit']:
                continue
            if not match['unit'] and re.match(r'^\s*[、.]\s*\D', text[match.end('num'):match.end('num') + 3]) and start < 4:
                continue
            if _FOREIGN.match(text[match.end():match.end() + 5]):
                continue                      # 外币金额不与人民币事实比较
            range_tail = re.match(r'\s*[-~至到]\s*(?=\d)', text[match.end():])
            next_number = MENTION_RE.match(text, match.end() + range_tail.end()) if range_tail else None
            same_unit_range = bool(range_tail and next_number and
                                   (not match['unit'] or not next_number['unit'] or match['unit'] == next_number['unit']))
            if re.search(r'\d\s*[-~至到]\s*$', text[max(0, start - 3):start + (1 if match['sign'] else 0)]) or same_unit_range:
                continue                      # “1000-3000元”区间表述不作单值陈述
            mentions.append(match)
        if not mentions:
            if periods:
                last_period_in_paragraph = periods[-1].periods[-1]
            continue
        sentence_forecast = bool(_FORECAST.search(text))
        citation_matches = list(_CITE.finditer(text))
        cite_match = citation_matches[0] if citation_matches else None
        cite, cite_text = '', ''
        if cite_match:
            year, kind = (cite_match['y1'], cite_match['k1']) if cite_match['y1'] else (cite_match['y2'], cite_match['k2'])
            doc_type, suffix = _CITE_KIND[kind]
            cite, cite_text = f'{doc_type}:{year}{suffix}', f'{year}年{kind}'
        previous: Claim | None = None
        for match in mentions:
            start = match.start()
            eligible_cites = [m for m in citation_matches if _cited(m, match, text)]
            cite_match = eligible_cites[-1] if eligible_cites else None
            cite, cite_text = '', ''
            if cite_match:
                year, kind = (cite_match['y1'], cite_match['k1']) if cite_match['y1'] else (cite_match['y2'], cite_match['k2'])
                doc_type, suffix = _CITE_KIND[kind]
                cite, cite_text = f'{doc_type}:{year}{suffix}', f'{year}年{kind}'
            unit = (match['unit'] or '')
            unit = {'个百分点': '百分点', '个pct': 'pct', 'pcts': 'pct', 'BP': 'bp', 'X': 'x'}.get(unit, unit)
            clause_start = max(text.rfind(ch, 0, start) for ch in '，,；;：:。') + 1
            window = text[max(0, start - 40):start]
            tail = text[match.end():match.end() + 16]
            prior_aliases = [a for a in aliases if a[1] <= start]
            alias = prior_aliases[-1] if prior_aliases else None
            notes: list[str] = []
            confidence = 1.0
            # 数字后置指标：“实现4.96亿元的营业收入”
            if (alias is None or alias[1] < clause_start) and unit:
                post = next((a for a in aliases if a[0] >= match.end() and a[0] - match.end() <= 4 and re.fullmatch(r'\s*的?\s*', text[match.end():a[0]])), None)
                if post:
                    alias = post
            if alias is not None and alias[1] < clause_start:
                between = text[alias[1]:start]
                if len(between) > 60 or len(re.findall(r'[，,；;]', between)) > 3:
                    alias = None
                else:
                    confidence = min(confidence, 0.8); notes.append('指标承接上一分句')
            metric = alias[2] if alias else ''
            metric_text = alias[3] if alias else ''
            if metric in VALUATION_METRICS and metric_text.isascii() and (match['unit'] or '') not in ('倍', 'x', 'X'):
                alias, metric, metric_text = None, '', ''
            if not unit:
                if metric in PLAIN_METRICS and alias and start - alias[1] <= 12:
                    unit = '元' if metric in ('EPS', 'EPS_BASIC', 'EPS_DILUTED', 'EPS_LATEST', 'BPS', 'CFO_PER_SHARE') else '倍'
                    if metric in VALUATION_METRICS:
                        unit = '倍'
                elif _COUNT_UNIT.match(text[match.end('num'):match.end('num') + 6]):
                    continue                      # 台/家/人等经营技术量由文本佐证通道处理，不进入财务陈述
                else:
                    continue
            # 序列：“-0.37/0.07/0.45亿元” → 展开为多条
            raw_numbers = [match['sign'] + match['num']] + ([part.strip() for part in match['series'].split('/') if part.strip()] if match['series'] else [])
            signed = bool(match['sign']) or bool(re.search(r'[(（]\s*$', window)) and bool(match['sign'])
            relation, rel_conf = _relation(text[max(alias[1] if alias else clause_start, start - 40):start] if alias else window, tail, unit, metric, bool(match['sign']))
            confidence = min(confidence, rel_conf)
            if unit == '倍' and metric not in PLAIN_METRICS:
                growth = re.search(r'(?:增长|提升|扩大|翻)\s*$', window[-4:])
                metric = metric if metric in VALUATION_METRICS else ''
                if growth:
                    continue
            # 括号内的同比：“4.96亿元（+58%）”沿用前一条的指标与期间
            parenthetical = bool(re.search(r'[(（]\s*(?:同比|yoy|YoY)?\s*$', window[-8:])) and unit == '%'
            # 期间
            prior_periods = [p for p in periods if p.end <= start]
            period_mention = prior_periods[-1] if prior_periods else None
            inferred = False
            if period_mention is None:
                later = [p for p in periods if p.start >= match.end() and p.start - match.end() <= 6]
                period_mention = later[0] if later else None
            series_periods = None
            if period_mention is not None and period_mention.is_range:
                if relation == 'cagr':
                    period, base = period_mention.periods[-1], period_mention.periods[0]
                elif len(raw_numbers) == len(period_mention.periods):
                    series_periods = list(period_mention.periods); period, base = '', ''
                elif len(raw_numbers) == 1 and '分别' in text[period_mention.end:start]:
                    siblings = [m for m in mentions if m.start() >= period_mention.end and (m['unit'] or '') in (match['unit'] or '', '')]
                    order = [m.start() for m in siblings]
                    if len(siblings) == len(period_mention.periods) and start in order:
                        period, base = period_mention.periods[order.index(start)], ''
                    else:
                        period, base = period_mention.periods[-1], ''; confidence = min(confidence, 0.6); notes.append('区间期间取末年')
                else:
                    period, base = period_mention.periods[-1], ''; confidence = min(confidence, 0.6); notes.append('区间期间取末年')
            elif period_mention is not None:
                period, base = period_mention.periods[0], ''
            elif parenthetical and previous is not None:
                period, base = previous.period, ''
            elif last_period_in_paragraph:
                period, base, inferred = last_period_in_paragraph, '', True
            elif base_year:
                period, base, inferred = f'{base_year}Y', '', True
            else:
                period, base = '', ''
            if inferred:
                confidence = min(confidence, 0.55); notes.append('期间由上下文推定')
            if parenthetical and previous is not None and previous.metric:
                metric, metric_text = previous.metric, previous.metric_text
                relation = 'pp' if metric in RATIO_METRICS else ('qoq' if re.search(r'环比', window[-6:]) else 'yoy')
                confidence = max(confidence, 0.9)
            forecast_flag = (period_mention.forecast if period_mention else False)
            # 主体
            subject_zone = text[clause_start:alias[0]] if alias and alias[0] >= clause_start else text[max(0, clause_start - 30):start]
            chain = text[max(0, (alias[0] if alias else start) - 26):(alias[0] if alias else start)]
            entity, segment = 'company', ''
            denominator_name = ''
            numerator_period_part = False
            chain = _strip_periods(chain)
            if _EXTERNAL.search(text[max(0, clause_start - 20):start]) and not re.search(r'公司(?:实现|的)?(?:营业收入|营收|归母)', text[clause_start:start]):
                entity = 'external'
            elif metric in ('REVENUE', 'GROSS_MARGIN', 'COST', 'GROSS_PROFIT', ''):
                seg = next((s for s in sorted(segments, key=len, reverse=True) if s in chain), '')
                if not seg and alias:
                    found = re.search(r'([\u4e00-\u9fffA-Za-z+]{2,12}?(?:业务|产品|板块|服务|系列|分部))(?:的)?(?:整体)?$', chain.rstrip(' ，,的'))
                    if found and not _GENERIC_SUBJECT.match(found[1]) and not re.search(r'实现|达到|同比|增长|公司|其中$', found[1]):
                        seg = found[1]
                if not seg and alias and metric_text in ('收入', '营收', '毛利率', '毛利', '营业收入', '营业成本'):
                    head_text = _strip_periods(text[max(0, alias[0] - 14):alias[0]])
                    glued = None if re.search(r'(?:实现|达到|完成|录得|取得|贡献|带来|获得|为|达|共|累计|合计|总计|全年|同比)$', head_text) else \
                        re.search(r'([\u4e00-\u9fffA-Za-z+]{2,12})$', head_text)
                    name = re.sub(r'^(?:公司|本公司|集团|其中|而|且|但)+', '', glued[1]) if glued else ''
                    name = re.sub(r'(?:的|整体|总体|合计|业务的)+$', '', name)
                    if len(name) >= 2 and not _GENERIC_NOUN.search(name) and not re.search(r'持续|高速|快速|稳定|大幅|显著|稳步|逐步|进一步|主要|核心', name):
                        seg = name
                customer = re.search(r'前(?:五|5|十|10)(?:大|名)客户|第一大客户', text[clause_start:start]) or (
                    re.search(r'前(?:五|5|十|10)(?:大|名)客户|第一大客户', text[:clause_start])
                    if re.search(r'^[，,]?\s*(?:相比|较|对比|而|同比)?\s*$', _strip_periods(text[clause_start:start]).split('的')[0][:6]) else None)
                if customer:
                    seg = customer[0]
                if not seg and segments:
                    # 分句以指标词开头且无主语时，承接本句此前出现的业务主体
                    head = _strip_periods(text[clause_start:alias[0]] if alias and alias[0] >= clause_start else text[clause_start:start])
                    earlier = [s for s in sorted(segments, key=len, reverse=True) if s in text[:clause_start]]
                    if earlier and not re.search(r'公司|整体|合计|总', head) and len(re.sub(r'[\s进一步实现同比增长达到至为约]', '', head)) <= 2:
                        seg = max(earlier, key=lambda name: text[:clause_start].rfind(name))
                        confidence = min(confidence, 0.7); notes.append('业务主体承接上文')
                if seg and not metric and unit in MONEY_UNITS + ('%',) and re.search(r'增长|下降|达|至|为|实现', text[clause_start:start] + window[-6:]):
                    metric, metric_text = 'REVENUE', '收入(推定)'
                    relation = 'yoy' if unit == '%' else 'value'
                    confidence = min(confidence, 0.6); notes.append('指标由业务主体推定为收入')
                if relation == 'share':
                    before_number = _LIST_TAIL.sub('', _strip_periods(text[clause_start:start]))
                    den = re.search(r'占(?P<den>[\u4e00-\u9fffA-Za-z+]{0,18}?)(?:的)?(?:比例|比重|占比)?(?:分别|已|约|达|超过|高达)*(?:为|达|约|超过)?\s*$', before_number)
                    if den:
                        name = re.sub(r'(?:全年|当年|当期|年度)?(?:营业收入|营收|收入|销售额|总额)$', '', re.sub(r'^(?:公司|总体|整体|全部|总|本公司)+', '', den['den']))
                        if name and not re.fullmatch(r'(?:公司|总|全部|整体|营业|主营业务|主营|比)?', name):
                            denominator_name = name
                        else:
                            denominator_name = ''
                    else:
                        denominator_name = ''
                    numerator = re.search(r'([\u4e00-\u9fffA-Za-z+]{2,14}?)(?:的)?(?:营业收入|营收|收入|销售额|毛利)?(?:合计)?(?:占|在)(?:公司|总|全部|整体)?[\u4e00-\u9fff]{0,10}(?:比例|比重|占比)?(?:分别)?(?:为|达|约)?\s*$',
                                          before_number if re.search(r'占', text[clause_start:start]) else _LIST_TAIL.sub('', _strip_periods(text[:start]))[-40:])
                    if numerator:
                        candidate = re.sub(r'^(?:公司|本公司|其中|而|由于|以|的)+', '', numerator[1])
                        if re.fullmatch(r'第?[一二三四1-4]季度|[上下]半年|单季度?|全年', candidate):
                            notes.append('占比分子为期间片段：' + candidate); confidence = min(confidence, 0.6)
                            seg = seg or ''
                            numerator_period_part = True
                        elif candidate and candidate != seg and not _GENERIC_NOUN.search(candidate) and not (segments and candidate in segments):
                            seg = candidate if candidate not in ('前五大客户', '前五名客户') else seg or candidate
                            notes.append('占比分子：' + candidate); confidence = min(confidence, 0.8)
                        elif candidate and segments and candidate in segments:
                            seg = candidate
                if relation == 'share' and denominator_name:
                    notes.append('占比分母：' + denominator_name)
                if seg and metric:
                    entity, segment = 'segment', seg
            if relation == 'cagr' and not base and period_mention is None:
                span_years = re.search(r'([一二三四五六七八九十两\d]+)年(?:期间)?(?:的)?(?:营收|收入|利润|净利润)?(?:年均)?(?:复合|CAGR)', text[max(0, start - 30):start + 4], re.I)
                digits = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
                if span_years and base_year:
                    years = digits.get(span_years[1]) or (int(span_years[1]) if span_years[1].isdigit() else 0)
                    if 1 <= years <= 15:
                        period, base = f'{base_year}Y', f'{base_year - years}Y'
                        confidence = min(confidence, 0.6); notes.append(f'CAGR 区间按“{span_years[1]}年”推定')
            for offset, raw in enumerate(raw_numbers):
                number = to_decimal(raw)
                if number is None:
                    continue
                this_period = series_periods[offset] if series_periods else period
                this_forecast = forecast_flag or _future(this_period, meta.report_date, base_year) or (sentence_forecast and inferred)
                value = number * unit_scale(unit)
                if relation in ('yoy', 'qoq', 'pp') and not raw.startswith(('+', '-')) and _DOWN.search(window[-8:]) and not re.search(r'至|到', window[-2:]):
                    value = -value
                hedge = next((name for name, pattern in _HEDGE if pattern.search(window[-5:])), '')
                if not hedge and re.match(r'\s*(?:左右|上下)', tail):
                    hedge = 'approx'
                if not hedge and re.match(r'\s*(?:以上|以下)', tail):
                    hedge = 'above' if '以上' in tail[:4] else 'below'
                threshold_operator = ''
                if hedge in ('above', 'below'):
                    bound_prefix = window[-8:]
                    if re.search(r'不低于\s*$', bound_prefix) or re.match(r'\s*以上', tail):
                        threshold_operator = 'ge'
                    elif re.search(r'不高于\s*$', bound_prefix) or re.match(r'\s*以下', tail):
                        threshold_operator = 'le'
                    else:
                        threshold_operator = 'gt' if hedge == 'above' else 'lt'
                scopes = list(re.finditer(r'母公司|合并(?:报表|口径)?', text[:start]))
                declared_scope = ('母公司' if scopes[-1][0] == '母公司' else '合并') if scopes else ''
                base_period = base
                if relation == 'share' and (denominator_name or numerator_period_part):
                    entity = 'segment' if (segment or numerator_period_part) else entity
                claim = Claim(kind='prose', page=page, text=sentence, num_text=raw + (match['unit'] or ''), value=str(value), unit=unit, denominator=denominator_name,
                              cite=cite if _cited(cite_match, match, text) else '', cite_text=cite_text if _cited(cite_match, match, text) else '',
                              decimals=decimals_of(raw), metric=metric, metric_text=metric_text, relation=relation, period=this_period,
                              base_period=base_period, entity=entity, segment=segment, forecast=this_forecast, hedge=hedge,
                              confidence=round(confidence, 2), notes=list(notes), span=(match.start(), match.end()),
                              scope=declared_scope, threshold_operator=threshold_operator, source_paragraph_id=paragraph_id)
                claim.category = _category(claim)
                claims.append(claim)
                previous = claim
        if periods:
            last_period_in_paragraph = periods[-1].periods[-1]
    return claims


def _strip_periods(text: str) -> str:
    for mention in reversed(find_periods(text)):
        text = text[:mention.start] + text[mention.end:]
    return text


_PERIOD_END = {'Y': '12-31', 'H1': '06-30', 'H2': '12-31', 'Q1': '03-31', 'Q2': '06-30', 'Q3': '09-30', 'Q4': '12-31', '9M': '09-30'}


def _future(period: str, report_date: str, base_year: int | None) -> bool:
    """期间截止日晚于研报日期才是预测；无研报日期时退回“年份晚于最近实际年度”。"""
    if not period or not re.match(r'20\d{2}', period):
        return False
    if report_date and period[4:] in _PERIOD_END:
        return f'{period[:4]}-{_PERIOD_END[period[4:]]}' > report_date
    return base_year is not None and int(period[:4]) > base_year


def _cited(cite_match, match, text: str) -> bool:
    """出处只覆盖其后、同一分句组内的数字（遇到分号即止）。"""
    return bool(cite_match) and cite_match.start() < match.start() and not re.search(r'[；;]', text[cite_match.end():match.start()])


def modality(claim: Claim) -> str:
    """事实、推论与观点的区分：目标价与评级类为观点；预测期数值与基于预测的估值为推论；其余为事实性陈述。"""
    if claim.metric == 'TARGET_PRICE' or re.search(r'目标价|评级', claim.text[max(0, claim.span[0] - 12):claim.span[0]] if claim.kind == 'prose' else ''):
        return 'opinion'
    if claim.forecast or claim.category == 'forecast':
        return 'inference'
    return 'fact'


def _category(claim: Claim) -> str:
    if claim.entity == 'external':
        return 'external'
    if claim.metric in MARKET_METRICS:
        return 'market'
    if not claim.metric:
        return 'other_fact'
    if claim.forecast:
        return 'forecast'
    if claim.metric in ('RESIDUAL', 'TEMPLATE_ITEM'):
        return 'template'
    if claim.metric in OPEN_METRICS:
        return 'open_metric'
    return 'core'


def table_claims(tables: list[ReportTable]) -> list[Claim]:
    claims = []
    for table in tables:
        for row in table.rows:
            metric, relation = row['metric'], row['relation']
            unit = row['unit'] or ''
            for (period, forecast), (raw, number) in zip(table.years, row['cells']):
                if number is None:
                    continue
                cell_unit = unit
                if not cell_unit:
                    if relation != 'value' or metric in RATIO_METRICS:
                        cell_unit = '%'
                    elif metric in PLAIN_METRICS or metric in ('EPS_LATEST',):
                        cell_unit = '倍' if metric in VALUATION_METRICS or metric in ('CURRENT_RATIO', 'QUICK_RATIO', 'ASSET_TURNOVER', 'AR_TURNOVER', 'AP_TURNOVER', 'INVENTORY_TURNOVER') else '元'
                    else:
                        cell_unit = table.unit or '百万元'
                if raw.endswith('%'):
                    cell_unit = '%'
                claim = Claim(kind='table', page=row['page'], text=row['row_text'], num_text=raw, value=str(number * unit_scale(cell_unit)),
                              unit=cell_unit, decimals=decimals_of(raw), metric=metric, metric_text=row['label'], relation=relation,
                              period=period, forecast=forecast, table=table.kind, row_label=row['label'], confidence=1.0 if metric else 0.0,
                              bbox=row.get('bbox'), scope=table.scope)
                claim.category = _category(claim) if metric else 'template'
                claims.append(claim)
    return claims


def build_report_model(doc: Document, meta: DocMeta, segments: set[str] | None = None) -> ReportModel:
    tables = [t for t in parse_report_tables(doc, meta.base_year)
              if sum(bool(r['metric']) for r in t.rows) >= max(2, 0.4 * len(t.rows))]
    market = parse_market_box(doc)
    body = norm(' '.join(line for _, line in _clean_lines(doc)))
    learned = Counter(m[1] for m in re.finditer(r'([\u4e00-\u9fffA-Za-z]{2,8}(?:业务|服务|产品|板块))(?:的)?(?:营业收入|营收|收入|毛利率|营业成本)', body))
    segments = set(segments or set()) | {name for name, count in learned.items()
                                         if count >= 2 and not _GENERIC_SUBJECT.match(name) and not re.search(r'公司|主营|实现|年|其他业务$|同比', name)}
    claims = table_claims(tables) + extract_prose_claims(doc, meta, segments)
    for key, item in market.items():
        if key == 'FLOAT_CAP':
            continue
        claims.append(Claim(kind='box', page=item['page'], text=item['text'], num_text=item['text'], value=item['value'], unit=item['unit'],
                            decimals=item['decimals'], metric=key, metric_text=key, period=f'{meta.base_year}Y' if meta.base_year else '',
                            category='market', entity='market', bbox=item.get('bbox')))
    # 相同断言只共享分组，不删除摘要、正文或图题中的原文出现位置。
    assertion_ids: dict[tuple, str] = {}
    for claim in claims:
        key = (claim.kind == 'table', claim.metric, claim.relation, claim.period, claim.base_period, claim.value, claim.unit, claim.segment,
               claim.entity, claim.table, claim.row_label, claim.cite, claim.cite_text, claim.scope, claim.currency,
               claim.definition_id, claim.denominator, claim.forecast, claim.hedge, claim.threshold_operator) if claim.metric else (id(claim),)
        if key not in assertion_ids:
            assertion_ids[key] = f'a{len(assertion_ids) + 1:04d}'
        claim.assertion_id = assertion_ids[key]
        claim.occurrences = 1
        claim.pages = [claim.page]
    ordered = claims
    for index, claim in enumerate(ordered, 1):
        claim.cid = f'c{index:04d}'
        claim.modality = modality(claim)
    _locate(doc, ordered)
    stats = dict(Counter(claim.category for claim in ordered))
    return ReportModel(meta, ordered, tables, market, {'by_category': stats, 'tables': len(tables), 'prose': sum(c.kind == 'prose' for c in ordered),
                                                       'occurrences': len(ordered), 'assertions': len(assertion_ids),
                                                       'market_check': market_consistency(market), 'doc_sha256': doc.sha256})


def _locate(doc: Document, claims: list) -> None:
    """为正文陈述找到所在版面行（仅坐标后端有框），供界面在研报原页上高亮。"""
    pages = {page.number: page for page in doc.pages}
    for claim in claims:
        if claim.bbox or claim.kind != 'prose' or claim.page not in pages:
            continue
        needle = compact(claim.num_text)
        context = compact(norm(claim.text)[max(0, claim.span[0] - 6):claim.span[0]])
        best = None
        for row in pages[claim.page].rows:
            if row.bbox is None:
                continue
            text = compact(row.text)
            if needle and needle in text:
                score = 2 if context and context[-3:] in text else 1
                if best is None or score > best[0]:
                    best = (score, row.bbox)
        if best:
            claim.bbox = [round(v, 1) for v in best[1]]
