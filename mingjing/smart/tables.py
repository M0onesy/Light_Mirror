"""财报通用表格抽取：把定期报告的版面行解析成带证据的 Fact。

覆盖：合并/母公司三大报表、主要会计数据和财务指标（含同比列）、分季度数据、
非经常性损益、现金流量表补充资料、分行业/产品/地区收入成本毛利。
不认识的版式只产生诊断，不产生事实；缺失单元格不当作零。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from ..evidence import make_evidence_id
from ..models import Fact
from . import EXTRACTOR_VERSION
from .common import BLANK_CELL_RE, CELL_RE, compact, decimals_of, find_periods, period_end, to_decimal, unit_scale
from .detect import DocMeta
from .docio import Document, Row
from .taxonomy import ADJUSTED_ACCOUNTS, PERCENT_ACCOUNTS, PLAIN_ACCOUNTS, filing_account

_STATEMENT = re.compile(r'^(?:[(（]?[一二三四五六七八九十\d]+[)）、.．]?)?(合并及母公司|合并|母公司)?(?:公司)?(资产负债表|利润表|现金流量表|所有者权益变动表|股东权益变动表)(?:[(（]续[)）])?$')
_SECTION_TITLES = [
    ('QUARTERLY', re.compile(r'分季度(?:的)?主要(?:财务|会计)(?:数据|指标)')),
    ('NRI', re.compile(r'非经常性损益(?:项目|明细)')),
    ('CF_SUPP', re.compile(r'现金流量表补充资料|将净利润调节为经营活动现金流量')),
    ('SEGMENT', re.compile(r'分行业、?分产品|主营业务分(?:行业|产品|地区)|按(?:产品|行业|地区|区域|业务)(?:分类)?(?:分析|列示|情况)|收入构成|营业收入构成|占公司营业收入或营业利润10%以上')),
    ('SUMMARY', re.compile(r'主要会计数据和财务指标|主要会计数据|主要财务指标|主要财务数据|会计数据和财务指标摘要|近三年主要会计数据|盈利能力$|偿债能力$|营运情况$|成长情况$')),
]
_CHAPTER = re.compile(r'^第[一二三四五六七八九十]+[节章]')
_UNIT = re.compile(r'(?:金额)?单位[:为]?(?:人民币)?(元|千元|万元|百万元|亿元)')
_REL_CUR = re.compile(r'^(?:本期|本年|本报告期|本期金额|本期发生额|本年金额|本期数|本年数|本年度|本年发生额|本期发生数|报告期)$')
_REL_YTD = re.compile(r'^(?:年初至(?:本)?报告期末|年初至今|本年累计|本期累计|年初至报告期末金额)$')
_REL_SAME = re.compile(r'^(?:上年同期|去年同期|上年同期数|上年同期金额)$')
_REL_PREV = re.compile(r'^(?:上期|上年|上期金额|上期发生额|上年金额|上年度|上期数|上年数|上年发生额|上期发生数)$')
_REL_CUR_END = re.compile(r'^(?:期末|期末余额|期末数|年末余额|本报告期末|本期末|本年末|期末金额|年末数|本期期末|报告期末)$')
_REL_PREV_END = re.compile(r'^(?:期初|年初|期初余额|年初余额|上年年末|上年末|上年度末|上期末|上年年末余额|期初数|年初数|上年期末|上期期末|上年末余额)$')
_GROWTH = re.compile(r'增减|变动|同比|增长率|比上年|比上期|幅度')
_VERSION = re.compile(r'^(调整前|调整后|重述前|重述后|更正前|更正后)$')
_QUARTER = re.compile(r'^第?([一二三四])季度')
_SKIP_HEAD = re.compile(r'^(?:项目|附注|注释|指标|科目|主要会计数据|主要财务指标|类别|分类|附注号|行次|类别/项目|项目名称)$')
_NOTE_TAIL = re.compile(r'(?:[一二三四五六七八九十]+[、.．]?(?:[(（][一二三四五六七八九十\d]+[)）])?|[(（][一二三四五六七八九十\d]{1,3}[)）]|注释?\d+)$')
_NOTE_REF = re.compile(r'^[一二三四五六七八九十]+[、.．:]?[(（]?[一二三四五六七八九十\d]*[)）]?[、.．]?\d*$|^[(（][一二三四五六七八九十\d]+[)）]$|^注释?\d+$')


_PERIOD_PART = re.compile(r'^(?:20\d{2}(?:年(?:度|末|年末|\d{1,2}月(?:\d{1,2}日)?)?|[AE])|第?[一二三四]季度|本期|上期|本年|上年|期末余额|期初余额|本期金额|上期金额|本期发生额|上期发生额)$')
_GLUED = re.compile(r'^(.*[\u4e00-\u9fff][\u4e00-\u9fff)）%]*)([-(（]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d{2,})[)）]?%?)$')


@dataclass
class Tok:
    text: str
    x0: float
    x1: float
    kind: str      # num / blank / text


@dataclass
class Col:
    kind: str      # period / growth
    period: str = ''
    version: str = ''
    x0: float = 0.0
    x1: float = 0.0
    caption: str = ''


def tokenize(row: Row) -> list[Tok]:
    tokens: list[Tok] = []
    for cell in row.cells:
        parts = cell.text.split()
        if len(parts) > 1 and sum(bool(_QUARTER.match(p)) for p in parts) >= 2 and not any(CELL_RE.match(p) for p in parts):
            pass                                  # “第一季度（1-3月 第二季度（4-6月 …”被版面挤成一格：按季度拆开
        elif len(parts) > 1 and any(_PERIOD_PART.match(p) for p in parts) and not any(CELL_RE.match(p) for p in parts):
            pass                                  # “2022年 本年比上年增减(%)”是两个列标题，保持拆开
        elif len(parts) <= 1 or not any(CELL_RE.match(p) or BLANK_CELL_RE.match(p) for p in parts):
            parts, joined = [cell.text.replace(' ', '')] if not CELL_RE.match(cell.text.replace(' ', '')) else [cell.text.replace(' ', '')], True
        width = max(cell.x1 - cell.x0, 1e-6)
        total = sum(len(p) for p in parts) + max(len(parts) - 1, 0)
        cursor = 0
        expanded = []
        for part in parts:
            glued = _GLUED.match(part)
            expanded += [glued[1], glued[2]] if glued else [part]
        parts = expanded
        total = sum(len(p) for p in parts) + max(len(parts) - 1, 0)
        for part in parts:
            x0 = cell.x0 + width * cursor / max(total, 1)
            x1 = cell.x0 + width * (cursor + len(part)) / max(total, 1)
            cursor += len(part) + 1
            kind = 'num' if CELL_RE.match(part) else 'blank' if BLANK_CELL_RE.match(part) else 'text'
            tokens.append(Tok(part, x0, x1, kind))
    return tokens


def split_label(tokens: list[Tok]) -> tuple[str, list[Tok]]:
    index = 0
    while index < len(tokens) and tokens[index].kind == 'text':
        if index and _NOTE_REF.match(tokens[index].text):      # 附注引用列不属于行标签
            return ''.join(t.text for t in tokens[:index]), tokens[index + 1:]
        index += 1
    # “2.稀释每股收益”这类序号被切成数字时并回标签
    if index == 0 and len(tokens) > 1 and tokens[0].kind == 'num' and tokens[1].kind == 'text' and re.fullmatch(r'\d{1,2}\.?', tokens[0].text):
        index = 1
        while index < len(tokens) and tokens[index].kind == 'text':
            index += 1
        return ''.join(t.text for t in tokens[1:index]), tokens[index:]
    return ''.join(t.text for t in tokens[:index]), tokens[index:]


class _Periods:
    def __init__(self, doc_period: str):
        self.period = doc_period
        self.year = int(doc_period[:4]) if re.match(r'20\d{2}', doc_period or '') else None
        self.kind = (doc_period[4:] or 'Y') if self.year else 'Y'

    def cur(self): return f'{self.year}{self.kind}' if self.year else ''
    def prev(self): return f'{self.year - 1}{self.kind}' if self.year else ''
    def quarter(self):
        """季报主要数据表的“本报告期”是单季：一季报=Q1，三季报=Q3；年报、半年报与累计期一致。"""
        if not self.year:
            return ''
        return f'{self.year}Q3' if self.kind == '9M' else self.cur()
    def cur_end(self): return self.cur()
    def prev_end(self): return f'{self.year - 1}Y' if self.year else ''

    def of_token(self, text: str, section: str) -> Col | None:
        text = compact(text)
        if not text or _SKIP_HEAD.match(text):
            return None
        if _VERSION.match(text):
            return Col('version', version=text)
        if _GROWTH.search(text):
            return Col('growth', caption=text)
        opening = re.fullmatch(r'(20\d{2})年1月1日(?:余额)?', text)
        if opening:
            return Col('period', f'{int(opening[1]) - 1}Y')
        quarter = _QUARTER.match(text)
        if quarter and self.year:
            return Col('period', f"{self.year}Q{'一二三四'.index(quarter[1]) + 1}")
        if _REL_YTD.match(text):
            return Col('period', self.cur(), caption=text) if self.year else None
        if _REL_SAME.match(text):
            return Col('period', self.prev(), caption=text) if self.year else None      # 由 _header_columns 按前一列修正
        if text == '本报告期' and section not in ('IS', 'CF'):
            return Col('period', self.quarter(), caption=text) if self.year else None
        for pattern, getter in ((_REL_CUR, self.cur), (_REL_PREV, self.prev), (_REL_CUR_END, self.cur_end), (_REL_PREV_END, self.prev_end)):
            if pattern.match(text):
                value = getter()
                return Col('period', value) if value else None
        if re.fullmatch(r'20\d{2}', text):
            return Col('period', text + (self.kind if section in ('IS', 'CF') and self.kind != 'Y' and self.year == int(text) else 'Y'))
        found = find_periods(text)
        if len(found) == 1 and not found[0].is_range:
            residue = (text[:found[0].start] + text[found[0].end:]).strip('()（）:末度年全余额金发生数截至日的')
            if len(residue) <= 4:
                period = found[0].periods[0]
                # 中期报告利润表/现金流量表里的“2024年”“2023年”指同口径累计期间
                if (section in ('IS', 'CF') and self.kind != 'Y' and period.endswith('Y') and not found[0].explicit_kind
                        and self.year and int(period[:4]) in (self.year, self.year - 1)):
                    period = period[:4] + self.kind
                return Col('period', period)
        return None


def _header_columns(tokens: list[Tok], periods: _Periods, section: str) -> list[Col] | None:
    cols, residue = [], 0
    for token in tokens:
        if token.kind == 'blank':
            continue
        col = periods.of_token(token.text, section)
        if col is None:
            if token.kind == 'num':
                return None
            if not _SKIP_HEAD.match(compact(token.text)):
                residue += len(re.findall(r'[\u4e00-\u9fff]', token.text))
            continue
        col.x0, col.x1 = token.x0, token.x1
        col.caption = col.caption or compact(token.text)
        cols.append(col)
    if residue > 8 or not any(c.kind == 'period' for c in cols):
        return None
    for index, col in enumerate(cols):
        if col.kind == 'period' and _REL_SAME.match(col.caption or ''):
            previous = next((c for c in reversed(cols[:index]) if c.kind == 'period'), None)
            if previous is not None and re.match(r'20\d{2}', previous.period):
                col.period = f'{int(previous.period[:4]) - 1}{previous.period[4:]}'
    return cols


def growth_target(cols: list[Col], index: int) -> str:
    """增减列对应的期间：向左找到上一个增减列为止的同一区块，取其中年份最新的期间。

    年报“2023年｜2022年｜本年比上年增减｜2021年”→2023Y；三季报“本报告期｜增减｜年初至报告期末｜增减”→Q3、9M。
    非年度期末余额相对“上年度末/年初”的变动不是同比，返回空串。
    """
    block = []
    for col in reversed(cols[:index]):
        if col.kind == 'growth':
            break
        if col.kind == 'period' and re.match(r'20\d{2}', col.period):
            block.append(col)
    if not block:
        return ''
    latest = max(int(c.period[:4]) for c in block)
    target = next(c.period for c in reversed(block) if int(c.period[:4]) == latest)
    caption = cols[index].caption or ''
    if not target.endswith('Y') and re.search(r'年末|年度末|年初|期初', caption) and '同期' not in caption:
        return ''
    return target


def _assign(values: list[Tok], cols: list[Col], geometry: bool) -> list[tuple[Col, Tok]] | None:
    value_cols = [c for c in cols if c.kind in ('period', 'growth')]
    period_cols = [c for c in cols if c.kind == 'period']
    if len(values) == len(value_cols):
        return list(zip(value_cols, values))
    if len(values) == len(period_cols):
        return list(zip(period_cols, values))
    if values and len(values) < len(value_cols) and all(c.x1 > c.x0 for c in value_cols):
        # 缺格：按横向位置就近对齐（坐标后端用点，版面文本后端用字符列），超出容差或撞列则放弃
        limit = 60.0 if geometry else 14.0
        pairs, used = [], set()
        for token in values:
            centre = (token.x0 + token.x1) / 2
            scores = [min(abs(c.x1 - token.x1), abs((c.x0 + c.x1) / 2 - centre)) for c in value_cols]
            best = min(range(len(value_cols)), key=scores.__getitem__)
            if best in used or scores[best] > limit:
                return None
            used.add(best)
            pairs.append((value_cols[best], token))
        return pairs
    return None


def _split_growth_caption(cols: list[Col], rows: list[Row], texts: list[str], index: int, periods: '_Periods', section: str) -> list[Col]:
    """“本年比上年增/减%”被版面拆到表头上下两行时，按横向位置补回增减列。"""
    if any(c.kind == 'growth' for c in cols):
        return cols
    period_cols = [c for c in cols if c.kind == 'period']
    if len(period_cols) < 2:
        return cols
    for j in (index - 1, index + 1, index - 2):
        if not 0 <= j < len(rows) or rows[j].page != rows[index].page:
            continue
        for token in tokenize(rows[j]):
            if token.kind == 'num' or not _GROWTH.search(token.text + (texts[j] if len(texts[j]) < 12 else '')) and not re.fullmatch(r'减%?|增减%?|%', token.text):
                continue
            centre = (token.x0 + token.x1) / 2
            between = period_cols[0].x1 < centre < period_cols[-1].x0 + (period_cols[-1].x1 - period_cols[-1].x0)
            trailing = centre > period_cols[-1].x1 and _GROWTH.search(token.text)      # 增减列在最右、标题折到下一行
            if between or trailing:
                growth = Col('growth', x0=token.x0, x1=token.x1, caption=compact(texts[j]))
                return sorted(cols + [growth], key=lambda c: (c.x0 + c.x1) / 2)
    return cols


def _wrapped_header(rows: list[Row], index: int, periods: _Periods, section: str):
    """Join explicit date/month fragments across at most three header rows.

    Complete neighbouring annual columns survive an intervening row. Only
    header tokens and a matching date/range tail may be consumed.
    """
    tokens = tokenize(rows[index])
    partial = [token for token in tokens if re.fullmatch(r'20\d{2}年\d{1,2}月\d{0,2}', token.text)]
    if not partial or not all(token in partial or _SKIP_HEAD.match(compact(token.text)) for token in tokens):
        return None
    complete, consumed = [], {index}
    for following in range(index + 1, min(index + 3, len(rows))):
        if rows[following].page != rows[index].page:
            return None
        neighbours = tokenize(rows[following])
        tails = [token for token in neighbours if re.fullmatch(r'\d{0,2}日|[—–\-~至]\d{1,2}月', token.text)]
        if any(token not in tails and not _SKIP_HEAD.match(compact(token.text))
               and periods.of_token(token.text, section) is None for token in neighbours):
            return None
        complete += [token for token in neighbours if token not in tails and not _SKIP_HEAD.match(compact(token.text))]
        consumed.add(following)
        if not tails:
            continue
        if len(tails) != len(partial):
            return None
        joined = []
        for beginning, tail in zip(partial, tails):
            # Month ranges print the first month before the break: 1月 / —6月.
            text = (re.sub(r'月$', '', beginning.text) + '-' + tail.text[1:]
                    if re.fullmatch(r'[—–\-~至]\d{1,2}月', tail.text) else beginning.text + tail.text)
            if periods.of_token(text, section) is None:
                return None
            # Each tail must be below its own header column.
            limit = 60 if rows[index].geometry else 14
            if abs((beginning.x0 + beginning.x1 - tail.x0 - tail.x1) / 2) > limit:
                return None
            joined.append(Tok(text, beginning.x0, max(beginning.x1, tail.x1), 'text'))
        return sorted(joined + complete, key=lambda token: token.x0), consumed
    return None


def extract_filing_facts(doc: Document, meta: DocMeta) -> tuple[list[Fact], dict]:
    periods = _Periods(meta.period)
    company = meta.company or meta.company_full or ''
    sha12 = doc.sha256[:12]
    facts: list[Fact] = []
    seen: set[tuple] = set()
    diag = {'sections': [], 'unmapped_labels': {}, 'skipped_rows': 0, 'mapped_rows': 0, 'segment_rows': 0,
            'excluded_future_period_cells': 0}
    section = scope = table_label = ''
    unit, cols, has_note, ended = '元', [], False, False
    consumed_headers: set[int] = set()
    header_row = -9
    first_table: dict = {}
    also_in: dict = {}
    seg_kind = ''
    rows = [row for page in doc.pages for row in page.rows]
    texts = [compact(row.text) for row in rows]

    def add(account, period, value, decimals, display_unit, row, label, *, version='', relation='value',
            row_scope=None, dim_kind='total', dim_id='', source_type=None, conf='0.97'):
        nonlocal facts
        if meta.report_date and period_end(period) and period_end(period) > meta.report_date:
            diag['excluded_future_period_cells'] += 1
            return
        key = (account, period, row_scope or scope or '合并', version, relation, dim_kind, dim_id, value)
        where = table_label or section
        if key in seen and period and where and where != first_table.get(key):
            also_in.setdefault(key, set()).add(f'{where}（第{row.page}页）')       # 同一数值在另一张表再次出现：跨表互证
        if key in seen or not period:
            return
        seen.add(key)
        first_table[key] = where
        evidence = make_evidence_id(doc.sha256, row.page, row.text, row.bbox)
        kind = source_type or ('primary_statement' if section in ('BS', 'IS', 'CF') else 'financial_summary')
        facts.append(Fact(
            fact_id=f'{sha12}-{account}-{period}-p{row.page}-{len(facts) + 1}', company=company, account=account, period=period,
            scope=row_scope or scope or '合并', nr='扣非' if account in ADJUSTED_ACCOUNTS else '未扣非',
            version='原披露' + ('/' + version if version else ''), source_doc=sha12, publish_date=meta.report_date or '',
            value=value, decimals=decimals, display_unit=display_unit, evidence=(evidence,), label=label,
            source_path=doc.path, source_type=kind, table_label=table_label, column_label=period + (f'（{version}）' if version else ''),
            parse_conf=conf, extractor_version=EXTRACTOR_VERSION, version_group=sha12, dimension_kind=dim_kind,
            dimension_id=dim_id, source_relation=relation))

    for index, row in enumerate(rows):
        if index in consumed_headers:
            continue
        text = texts[index]
        if not text:
            continue
        if re.search(r'[.．…·]{4,}\d{1,4}$', text):        # 目录行
            continue
        unit_match = _UNIT.search(text)
        if unit_match and len(text) < 60:
            unit = unit_match[1]
        statement = _STATEMENT.match(text) if len(text) <= 24 else None
        if statement:
            kind = {'资产负债表': 'BS', '利润表': 'IS', '现金流量表': 'CF'}.get(statement[2], 'EQ')
            section, scope = kind, '母公司' if statement[1] == '母公司' else '合并'
            table_label, cols, has_note, ended, unit = text, [], False, False, unit if unit_match else '元'
            diag['sections'].append({'page': row.page, 'section': section, 'scope': scope, 'title': text})
            continue
        if len(text) <= 40 and not any(t.kind == 'num' for t in tokenize(row)):
            titled = None if re.search(r'[《》]|议案|决议|公告', text) else next((name for name, pattern in _SECTION_TITLES if pattern.search(text)), None)
            if titled and any((col := periods.of_token(t.text, section)) is not None and col.kind == 'period' for t in tokenize(row)):
                titled = None                     # “主要会计数据 2023年 2022年 …”是表头行而不是小节标题
            if not titled and section in ('CF_SUPP', 'NRI', 'SEGMENT', 'QUARTERLY') and re.match(r'^(?:[(（][一二三四五六七八九十百]+[)）]|[一二三四五六七八九十]+[、.．])\S', text):
                section, cols = '', []       # 下一个附注/小节开始，结束当前专项表
                continue
            if titled:
                if titled == section:          # 同一张表的小标题（如“1、将净利润调节为…”）不重置列
                    table_label = text
                else:
                    section, scope, table_label, cols, has_note, ended = titled, '合并', text, ([] if titled != 'SUMMARY' or section != 'SUMMARY' else cols), False, False
                    if titled == 'SEGMENT':
                        seg_kind = 'geography' if re.search(r'地区|区域', text) and not re.search(r'产品|行业', text) else 'business'
                    diag['sections'].append({'page': row.page, 'section': section, 'scope': scope, 'title': text})
                continue
            if _CHAPTER.match(text) and section in ('SUMMARY', 'QUARTERLY', 'NRI', 'SEGMENT', 'CF_SUPP'):
                section, cols = '', []
                continue
        if section in ('BS', 'IS', 'CF') and re.search(r'法定代表人|主管会计工作负责人|会计机构负责人', text):
            ended = True
            continue
        if not section or section == 'EQ' or ended:
            continue
        tokens = tokenize(row)
        if section == 'SEGMENT':
            if re.search(r'分地区|按地区|按区域', text) and len(text) < 30:
                seg_kind = 'geography'
            elif re.search(r'分产品|分行业|按产品|按行业', text) and len(text) < 30:
                seg_kind = 'business'
            label, rest = split_label(tokens)
            numbers = [t for t in rest if t.kind in ('num', 'blank')]
            label = re.sub(r'^(?:其中|含)[:：,，、]?', '', re.sub(r'\s+', '', label))
            if label and len(numbers) >= 3 and all(t.kind == 'num' for t in numbers[:3]) and not re.search(r'合计|小计|总计', label):
                revenue, cost, margin = (to_decimal(t.text) for t in numbers[:3])
                scale = unit_scale(unit)
                if revenue and revenue > 0 and cost is not None and margin is not None:
                    implied = (revenue - cost) / revenue * 100
                    if abs(implied - margin) <= Decimal('0.6') and periods.cur():
                        dim = seg_kind or 'business'
                        add('REVENUE', periods.cur(), revenue * scale, decimals_of(numbers[0].text), unit, row, label, dim_kind=dim, dim_id=label, source_type='segment_table', conf='0.96')
                        add('COST', periods.cur(), cost * scale, decimals_of(numbers[1].text), unit, row, label, dim_kind=dim, dim_id=label, source_type='segment_table', conf='0.96')
                        add('GROSS_MARGIN', periods.cur(), margin / 100, decimals_of(numbers[2].text), '%', row, label, dim_kind=dim, dim_id=label, source_type='segment_table', conf='0.96')
                        if len(numbers) >= 4 and numbers[3].kind == 'num':
                            add('REVENUE', periods.cur(), to_decimal(numbers[3].text) / 100, decimals_of(numbers[3].text), '%', row, label, relation='yoy', dim_kind=dim, dim_id=label, source_type='segment_table', conf='0.95')
                        diag['segment_rows'] += 1
            continue
        # —— 表头（含“2022年12月31/日”这类跨行折行日期） ——
        wrapped = _wrapped_header(rows, index, periods, section)
        if wrapped is not None:
            tokens, consumed = wrapped
            consumed_headers.update(consumed - {index})
        elif any(re.fullmatch(r'20\d{2}年\d{1,2}月\d{0,2}', t.text) for t in tokens):
            continue                          # incomplete date headers cannot establish columns
        label, rest = split_label(tokens)
        if any(compact(t.text) in ('附注', '注释', '附注号') for t in tokens):
            has_note = True
        if len(text) <= 90 and not re.search(r'[。；]', text):
            year_only = [t for t in tokens if t.kind == 'num' and re.fullmatch(r'20\d{2}', t.text)]
            header_tokens = tokens
            if year_only and len(year_only) == sum(t.kind == 'num' for t in tokens) and filing_account(label, section=section) is None:
                header_tokens = [Tok(t.text, t.x0, t.x1, 'text') if t in year_only else t for t in tokens]
            if not any(t.kind == 'num' for t in header_tokens):
                header = _header_columns(header_tokens, periods, section)
                if header:
                    versions = [c for c in header if c.kind == 'version']
                    real = [c for c in header if c.kind != 'version']
                    if real and any(c.kind == 'period' for c in real):
                        if header_row == index - 1 and cols and row.page == rows[index - 1].page:
                            merged = sorted(cols + real, key=lambda c: c.x0)
                            cols = [c for i, c in enumerate(merged) if not any(o.kind == c.kind and o.period == c.period and o.version == c.version for o in merged[:i])]
                        else:
                            cols = real
                        header_row = index
                        cols = _split_growth_caption(cols, rows, texts, index, periods, section)
                        continue
                if tokens and all(_VERSION.match(compact(t.text)) for t in tokens) and cols:
                    labels = [compact(t.text) for t in tokens]
                    prior = [i for i, c in enumerate(cols) if c.kind == 'period'][1:]
                    if len(labels) % 2 == 0 and len(labels) // 2 <= len(prior):
                        expanded, cursor = [], 0
                        targets = set(prior[:len(labels) // 2])
                        for i, c in enumerate(cols):
                            if i in targets:
                                for _ in range(2):
                                    expanded.append(Col('period', c.period, labels[cursor], tokens[cursor].x0, tokens[cursor].x1)); cursor += 1
                            else:
                                expanded.append(c)
                        cols = expanded
                    continue
        if re.search(r'业绩快报|业绩预告|境内外会计准则', text) and len(text) <= 60:
            cols = []
            if section == 'SUMMARY':
                section = ''
            continue
        header_words = [compact(t.text) for t in tokens if t.kind == 'text' and not _SKIP_HEAD.match(compact(t.text))]
        if cols and not any(t.kind == 'num' for t in tokens) and len(text) <= 60 and (
                (section not in ('BS', 'IS', 'CF') and re.match(r'^[一二三四五六七八九十]+[、.．]', text)
                 and filing_account(_NOTE_TAIL.sub('', text), section=section) is None)
                or (re.match(r'^项目', text) and len(header_words) >= 2 and not any(periods.of_token(w, section) for w in header_words))):
            cols = []
            continue
        if not cols:
            continue
        if re.search(r'[:：]$', label) and section in ('BS', 'IS', 'CF'):
            continue                             # “流动负债：”等小标题行，同行若有页码也不取数
        values = [t for t in rest if t.kind in ('num', 'blank')]
        if not values:
            continue
        # —— 行标签（处理跨行折行：括号配平、附注碎片、前后纯文本行补全） ——
        core = '' if (_NOTE_REF.match(label) or _NOTE_TAIL.fullmatch(label)) else _NOTE_TAIL.sub('', label)
        before = [_NOTE_TAIL.sub('', texts[j]) for j in range(max(0, index - 2), index) if rows[j].page == row.page and texts[j] and not re.search(r'[:：]$', texts[j])
                  and not re.search(r'20\d{2}', texts[j]) and not any(t.kind == 'num' for t in tokenize(rows[j])) and len(texts[j]) <= 40]
        after = [texts[j] for j in range(index + 1, min(len(rows), index + 3)) if rows[j].page == row.page and texts[j]
                 and not any(t.kind == 'num' for t in tokenize(rows[j])) and len(texts[j]) <= 40]
        unbalanced = lambda value: value.count('(') + value.count('（') > value.count(')') + value.count('）')
        ordered = []
        if before and (unbalanced(before[-1]) or not core or re.search(r'(?:的|非|每股|净资|现金流量净|收益率%?)$', before[-1])):
            ordered += [before[-1] + core + (after[0] if after else ''), before[-1] + core]
            if len(before) == 2:
                ordered.append(before[0] + before[1] + core)
        if core and unbalanced(core) and after:
            ordered += [core + ''.join(after[:2]), core + after[0]]
        ordered.append(core)
        if before:
            ordered += [before[-1] + core, before[-1] + core + (after[0] if after else '')]
        if after:
            ordered.append(core + after[0])
        percent_only = values and all(t.kind != 'num' or t.text.endswith('%') for t in values) and any(t.kind == 'num' for t in values)
        mapped = None
        for candidate in ordered:
            if not candidate:
                continue
            found = filing_account(candidate, section=section)
            if not found:
                continue
            code, info_ = found
            if percent_only and code not in PERCENT_ACCOUNTS and code not in PLAIN_ACCOUNTS and info_.get('unit') != '%':
                continue                         # 金额科目不会只有百分比单元格：多为跨行标签被截断
            mapped, label = found, candidate
            break
        if not mapped:
            if core and re.search(r'[\u4e00-\u9fff]', core):
                diag['unmapped_labels'][core[:30]] = diag['unmapped_labels'].get(core[:30], 0) + 1
            continue
        account, info = mapped
        if has_note and section in ('BS', 'IS', 'CF') and len(values) == len([c for c in cols if c.kind == 'period']) + 1 \
                and values[0].kind == 'num' and re.fullmatch(r'\d{1,3}(?:\.\d{1,2})?', values[0].text):
            values = values[1:]
        pairs = _assign(values, cols, row.geometry)
        if pairs is not None and not core and len(values) < len([c for c in cols if c.kind == 'period']):
            pairs = None                          # 纯数字行借用邻行标签时，缺格即放弃（多为页码或孤立数字）
        if pairs is None:
            diag['skipped_rows'] += 1
            continue
        row_unit = info.get('unit') or unit
        row_scope = info.get('scope')
        mapped_any = False
        for col, token in pairs:
            if token.kind == 'blank' and section in ('BS', 'IS', 'CF') and col.kind == 'period' and token.text in ('-', '—', '–') \
                    and account not in PERCENT_ACCOUNTS and account not in PLAIN_ACCOUNTS:
                money_unit = row_unit if row_unit in ('元', '千元', '万元', '百万元', '亿元') else unit
                add(account, col.period, Decimal(0), 2, money_unit, row, label + '（横线视为零）', version=col.version, row_scope=row_scope, conf='0.96')
                continue
            if token.kind != 'num':
                continue
            number = to_decimal(token.text)
            if number is None:
                continue
            decimals = decimals_of(token.text)
            percent_cell = token.text.endswith('%')
            if col.kind == 'growth':
                current = growth_target(cols, cols.index(col)) if col in cols else ''
                if not current:
                    continue
                if account in PERCENT_ACCOUNTS or account in PLAIN_ACCOUNTS and not percent_cell:
                    continue            # 比率行的“增减”为百分点或差值，非同比增速
                add(account, current, number / 100, decimals, '%', row, label, relation='yoy', row_scope=row_scope, conf='0.95')
                mapped_any = True
                continue
            if account in PERCENT_ACCOUNTS or percent_cell or row_unit == '%':
                add(account, col.period, number / 100, decimals, '%', row, label, version=col.version, row_scope=row_scope)
            elif account in PLAIN_ACCOUNTS:
                plain_unit = '股' if account == 'TOTAL_SHARES' else '元' if account in ('EPS', 'EPS_DILUTED', 'EPS_ADJ', 'BPS', 'CFO_PER_SHARE') else '倍'
                add(account, col.period, number, decimals, plain_unit, row, label, version=col.version, row_scope=row_scope)
            else:
                money_unit = row_unit if row_unit in ('元', '千元', '万元', '百万元', '亿元') else unit
                add(account, col.period, number * unit_scale(money_unit), decimals, money_unit, row, label, version=col.version, row_scope=row_scope)
            mapped_any = True
        diag['mapped_rows'] += bool(mapped_any)
    # 同一科目同一期间既有“横线视为零”又有实际数值时（如营业总收入项下与财务费用项下的“利息收入”），保留实际数值
    real_keys = {(f.account, f.period, f.scope, f.dimension_kind, f.dimension_id, f.source_relation) for f in facts if '横线视为零' not in (f.label or '')}
    facts = [f for f in facts if '横线视为零' not in (f.label or '') or (f.account, f.period, f.scope, f.dimension_kind, f.dimension_id, f.source_relation) not in real_keys]
    diag['corroborated'] = [{'account': k[0], 'period': k[1], 'scope': k[2], 'version': k[3], 'value': str(k[7]),
                             'tables': [first_table[k]] + sorted(v)} for k, v in also_in.items() if k[4] == 'value' and k[5] == 'total'][:400]
    diag['facts'] = len(facts)
    diag['accounts'] = len({f.account for f in facts})
    diag['periods'] = sorted({f.period for f in facts})
    diag['unmapped_labels'] = dict(sorted(diag['unmapped_labels'].items(), key=lambda kv: -kv[1])[:40])
    return facts, diag


def facts_from_json(path: str | Path, company: str = '') -> list[Fact]:
    """导入结构化事实文件（旧版 facts.json / fact cache），不校验外部来源路径。"""
    from ..facts import fact_from_dict
    payload = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    rows = payload.get('facts', payload.get('official_facts', [])) if isinstance(payload, dict) else payload
    output = []
    for item in rows if isinstance(rows, list) else []:
        if not isinstance(item, dict) or 'value' not in item:
            continue
        item = dict(item)
        item['source_path'] = ''
        if company and not item.get('company'):
            item['company'] = company
        try:
            output.append(fact_from_dict(item))
        except Exception:
            continue
    return output
