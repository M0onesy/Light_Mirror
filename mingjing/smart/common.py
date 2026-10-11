"""数值、单位、期间与区间的公共工具（无外部依赖）。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, localcontext

from ..decimal_math import DecimalInterval, _precision

_TRANS = {ord(a): b for a, b in zip('０１２３４５６７８９％－−–﹣＋．，：（）／～', '0123456789%----+.,:()/~')}
_TRANS.update({0x200b: None, 0xfeff: None, 0x00a0: ' ', 0x3000: ' ', 0x2002: ' ', 0x2003: ' ', 0x2009: ' '})


def norm(text: str) -> str:
    """一对一字符归一化：全角数字/符号转半角，长度不保证相等（仅删除零宽字符）。"""
    return (text or '').translate(_TRANS)


def compact(text: str) -> str:
    return re.sub(r'\s+', '', norm(text))


UNIT_SCALE = {
    '元': '1', '千元': '1000', '万元': '10000', '百万元': '1000000', '千万元': '10000000', '亿元': '100000000',
    '千': '1000', '万': '10000', '百万': '1000000', '亿': '100000000', '元/股': '1', '股': '1', '万股': '10000',
    '亿股': '100000000', '百万股': '1000000', '%': '0.01', '百分点': '0.01', 'pct': '0.01', 'pp': '0.01', 'bp': '0.0001',
    '倍': '1', 'x': '1', '次': '1', '': '1',
}
MONEY_UNITS = ('元', '千元', '万元', '百万元', '千万元', '亿元', '万', '亿')
PERCENT_UNITS = ('%', '百分点', 'pct', 'pp', 'bp')


def unit_scale(unit: str) -> Decimal:
    return Decimal(UNIT_SCALE.get((unit or '').lower() if (unit or '').lower() in ('pct', 'pp', 'bp', 'x') else (unit or ''), '1'))


NUM_BODY = r'\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?'
CELL_RE = re.compile(r'^[（(]?[+\-]?(?:' + NUM_BODY + r')[)）]?%?$')
BLANK_CELL_RE = re.compile(r'^[-—–/]{1,3}$|^不适用$|^N/?A$', re.I)


def to_decimal(token: str) -> Decimal | None:
    """解析表格/正文数字：千分位、括号负数、前置符号、尾随百分号。"""
    raw = norm(token).strip().replace(' ', '')
    if not raw:
        return None
    negative = raw.startswith('(') and raw.endswith(')')
    raw = raw.strip('()').rstrip('%').replace(',', '')
    if raw.startswith('+'):
        raw = raw[1:]
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    if not value.is_finite():
        return None
    return -value if negative else value


def decimals_of(token: str) -> int:
    raw = norm(token).strip().strip('()').rstrip('%').replace(',', '')
    return len(raw.partition('.')[2])


# ───────────────────────── 期间 ─────────────────────────
KIND_LABEL = {'Y': '年', 'H1': '年上半年', 'H2': '年下半年', 'Q1': '年第一季度', 'Q2': '年第二季度',
              'Q3': '年第三季度', 'Q4': '年第四季度', '9M': '年前三季度'}


def period_label(period: str) -> str:
    return period[:4] + KIND_LABEL.get(period[4:], period[4:]) if period else ''


def shift_year(period: str, years: int) -> str:
    return f'{int(period[:4]) + years}{period[4:]}' if re.match(r'\d{4}', period or '') else ''


def previous_quarter(period: str) -> str:
    match = re.fullmatch(r'(\d{4})Q([1-4])', period or '')
    if not match:
        return ''
    year, quarter = int(match[1]), int(match[2])
    return f'{year - 1}Q4' if quarter == 1 else f'{year}Q{quarter - 1}'


def period_end(period: str) -> str:
    """End of an actual accounting period; absent means an unsupported kind."""
    match = re.fullmatch(r'(20\d{2})(Y|H1|H2|Q1|Q2|Q3|Q4|9M)', period or '')
    if not match:
        return ''
    end = {'Y': '12-31', 'H1': '06-30', 'H2': '12-31', 'Q1': '03-31', 'Q2': '06-30',
           'Q3': '09-30', 'Q4': '12-31', '9M': '09-30'}[match[2]]
    return match[1] + '-' + end


_CN_KIND = {'上半年': 'H1', '半年度': 'H1', '半年': 'H1', '中期': 'H1', '中报': 'H1', '下半年': 'H2',
            '前三季度': '9M', '三季报': '9M', '1-9月': '9M', '1-6月': 'H1', '1-3月': 'Q1', '1-12月': 'Y',
            '一季度': 'Q1', '第一季度': 'Q1', '一季报': 'Q1', '二季度': 'Q2', '第二季度': 'Q2', '三季度': 'Q3',
            '第三季度': 'Q3', '四季度': 'Q4', '第四季度': 'Q4', '全年': 'Y', '年度': 'Y', '年报': 'Y', '年末': 'Y', '末': 'Y', '年底': 'Y'}
_KIND_ALT = '|'.join(sorted(map(re.escape, _CN_KIND), key=len, reverse=True))
PERIOD_RE = re.compile(
    r'(?P<range>(?<!\d)(?P<y1>20\d{2})\s*年?\s*[-~至到]\s*(?P<y2>20\d{2})\s*(?:年|[AE])?)'
    r'|(?P<short_range>(?<!\d)(?P<s1>[12]\d)\s*[-~]\s*(?P<s2>[12]\d)\s*年)'
    r'|(?P<yq>(?<!\d)(?P<yq_y>20\d{2}|[12]\d)\s*年?\s*(?P<yq_k>Q[1-4]|H[12]|9M))'
    r'|(?P<qy>(?<![\dA-Za-z])(?P<qy_k>[1-4]Q|[12]H)\s*(?P<qy_y>20\d{2}|[12]\d)(?!\d))'
    r'|(?P<full>(?<!\d)(?P<f_y>20\d{2})\s*年\s*(?P<f_k>' + _KIND_ALT + r'))'
    r'|(?P<date>(?<!\d)(?P<d_y>20\d{2})\s*年\s*(?P<d_m>\d{1,2})\s*月\s*(?:(?P<d_d>\d{1,2})\s*日|(?P<d_end>末|底))?)'
    r'|(?P<ae>(?<!\d)(?P<ae_y>20\d{2})\s*(?P<ae_f>[AE])(?![A-Za-z]))'
    r'|(?P<year>(?<!\d)(?P<y_y>20\d{2})\s*年(?!\s*\d{1,2}\s*月)(?:度)?)'
    r'|(?P<short>(?<!\d)(?P<sh_y>[12]\d)\s*年\s*(?P<sh_k>' + _KIND_ALT + r')?)'
    r'|(?P<bare>(?<![\d.,])(?P<b_y>20[0-4]\d)(?![\d.,%年]))', re.I)


@dataclass(frozen=True)
class PeriodMention:
    start: int
    end: int
    periods: tuple[str, ...]      # 单期一个；区间为逐年列表
    forecast: bool = False
    is_range: bool = False
    explicit_kind: bool = True
    text: str = ''


def _year(value: str) -> int:
    number = int(value)
    return number if number > 100 else 2000 + number


def find_periods(text: str, *, allow_bare: bool = False) -> list[PeriodMention]:
    """在半角归一化后的文本中定位期间表达。返回位置与期间编码。"""
    output: list[PeriodMention] = []
    for match in PERIOD_RE.finditer(text):
        kind = match.lastgroup
        groups = match.groupdict()
        if groups.get('range'):
            y1, y2 = int(groups['y1']), int(groups['y2'])
            if 0 < y2 - y1 <= 12:
                output.append(PeriodMention(match.start(), match.end(), tuple(f'{y}Y' for y in range(y1, y2 + 1)),
                                            is_range=True, text=match[0], forecast=match[0].rstrip().upper().endswith('E')))
        elif groups.get('short_range'):
            y1, y2 = _year(groups['s1']), _year(groups['s2'])
            if 0 < y2 - y1 <= 12 and 2010 <= y1 <= 2039:
                output.append(PeriodMention(match.start(), match.end(), tuple(f'{y}Y' for y in range(y1, y2 + 1)), is_range=True, text=match[0]))
        elif groups.get('yq'):
            year = _year(groups['yq_y'])
            if 2005 <= year <= 2040:
                output.append(PeriodMention(match.start(), match.end(), (f"{year}{groups['yq_k'].upper()}",), text=match[0]))
        elif groups.get('qy'):
            token = groups['qy_k'].upper()
            code = ('Q' + token[0]) if token.endswith('Q') else ('H' + token[0])
            year = _year(groups['qy_y'])
            if 2005 <= year <= 2040:
                output.append(PeriodMention(match.start(), match.end(), (f'{year}{code}',), text=match[0]))
        elif groups.get('full'):
            output.append(PeriodMention(match.start(), match.end(), (groups['f_y'] + _CN_KIND[groups['f_k']],), text=match[0]))
        elif groups.get('date'):
            month, day = int(groups['d_m']), groups.get('d_d')
            if groups.get('d_end'):
                day = {12: '31', 6: '30', 3: '31', 9: '30'}.get(month)
            code = {(12, '31'): 'Y', (6, '30'): 'H1', (3, '31'): 'Q1', (9, '30'): '9M'}.get((month, day))
            if code:
                output.append(PeriodMention(match.start(), match.end(), (groups['d_y'] + code,), text=match[0]))
        elif groups.get('ae'):
            output.append(PeriodMention(match.start(), match.end(), (groups['ae_y'] + 'Y',), forecast=groups['ae_f'].upper() == 'E', text=match[0]))
        elif groups.get('year'):
            output.append(PeriodMention(match.start(), match.end(), (groups['y_y'] + 'Y',), explicit_kind='度' in match[0], text=match[0]))
        elif groups.get('short'):
            year = _year(groups['sh_y'])
            if 2015 <= year <= 2035 and (groups.get('sh_k') or True):
                code = _CN_KIND.get(groups.get('sh_k') or '', 'Y')
                # “10年期国债”“5年”等时长不是期间：两位年份后须为期间词或直接接指标词
                tail = text[match.end():match.end() + 1]
                if groups.get('sh_k') or tail not in ('期', '来', '内', '前', '后', '以', '间', '的'):
                    output.append(PeriodMention(match.start(), match.end(), (f'{year}{code}',), explicit_kind=bool(groups.get('sh_k')), text=match[0]))
        elif groups.get('bare') and allow_bare:
            output.append(PeriodMention(match.start(), match.end(), (groups['b_y'] + 'Y',), explicit_kind=False, text=match[0]))
    return output


# ───────────────────────── 闭区间运算 ─────────────────────────
class IV:
    """Compatibility adapter for the shared Decimal interval contract."""
    __slots__ = ('bounds',)

    def __init__(self, lo, hi=None, lower_closed=True, upper_closed=True):
        if isinstance(lo, DecimalInterval):
            self.bounds = lo
            return
        lo = Decimal(str(lo)) if not isinstance(lo, Decimal) else lo
        hi = lo if hi is None else (Decimal(str(hi)) if not isinstance(hi, Decimal) else hi)
        if lo > hi:
            lo, hi, lower_closed, upper_closed = hi, lo, upper_closed, lower_closed
        self.bounds = DecimalInterval(lo, hi, lower_closed, upper_closed)

    @property
    def lo(self): return self.bounds.lower

    @property
    def hi(self): return self.bounds.upper

    @property
    def lower_closed(self): return self.bounds.lower_closed

    @property
    def upper_closed(self): return self.bounds.upper_closed

    @classmethod
    def rounded(cls, value: Decimal, quantum: Decimal) -> 'IV':
        with localcontext() as ctx:
            ctx.prec = _precision(value, quantum)
            half = quantum / 2
            return cls(value - half, value + half, True, False)

    def _other(self, other): return other if isinstance(other, IV) else IV(other)
    def __add__(self, other): return IV(self.bounds.add(self._other(other).bounds))
    def __sub__(self, other): return IV(self.bounds.subtract(self._other(other).bounds))
    def __mul__(self, other): return IV(self.bounds.multiply(self._other(other).bounds))
    __radd__ = __add__
    __rmul__ = __mul__

    def __rsub__(self, other): return IV(other) - self
    def __neg__(self): return IV(self.bounds.scaled(Decimal(-1)))

    def __truediv__(self, other):
        other = other if isinstance(other, IV) else IV(other)
        if other.lo <= 0 <= other.hi:
            raise ZeroDivisionError('分母区间包含零')
        if other.hi < 0:
            return IV(self.bounds.scaled(Decimal(-1)).divide_positive(other.bounds.scaled(Decimal(-1))))
        return IV(self.bounds.divide_positive(other.bounds))

    def __rtruediv__(self, other): return IV(other) / self

    def abs(self) -> 'IV':
        return IV(self.bounds.absolute())

    def widen(self, relative: Decimal = Decimal(0), absolute: Decimal = Decimal(0)) -> 'IV':
        with localcontext() as ctx:
            ctx.prec = _precision(self.lo, self.hi, relative, absolute)
            span = max(abs(self.lo), abs(self.hi)) * relative + absolute
        return IV(self.bounds.expanded(span))

    def intersects(self, other: 'IV') -> bool:
        return self.bounds.intersects(other.bounds)

    @property
    def mid(self) -> Decimal:
        with localcontext() as ctx:
            ctx.prec = _precision(self.lo, self.hi)
            return (self.lo + self.hi) / 2

    def to_list(self) -> list[str]:
        return [format(self.lo, 'f'), format(self.hi, 'f')]


def quantum(unit: str, decimals: int) -> Decimal:
    if type(decimals) is not int or decimals < 0:
        raise ValueError('披露小数位数须为非负整数')
    with localcontext() as ctx:
        ctx.prec = max(64, decimals + 16)
        return unit_scale(unit) * (Decimal(10) ** -decimals)


def fmt_amount(value: Decimal, unit: str = '元', decimals: int = 2) -> str:
    from decimal import ROUND_HALF_UP
    with localcontext() as ctx:
        ctx.prec = _precision(value, unit_scale(unit)) + decimals
        scaled = (value / unit_scale(unit)).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    if scaled == 0:
        scaled = abs(scaled)
    text = f'{scaled:,.{decimals}f}' if abs(scaled) >= 10000 else f'{scaled:.{decimals}f}'
    return text + ('' if unit in ('x',) else unit)


def best_money_unit(value: Decimal) -> tuple[str, int]:
    magnitude = abs(value)
    if magnitude >= Decimal('1e8'):
        return '亿元', 2
    if magnitude >= Decimal('1e4'):
        return '万元', 2
    return '元', 2


_RELATIVE = re.compile(r'(?<![\d年Qq])(?P<word>上半年|下半年|前三季度|第一季度|第二季度|第三季度|第四季度|一季度|二季度|三季度|四季度|全年|年末|年底)(?!度)')
_RELATIVE_CODE = {'上半年': 'H1', '下半年': 'H2', '前三季度': '9M', '第一季度': 'Q1', '第二季度': 'Q2', '第三季度': 'Q3', '第四季度': 'Q4',
                  '一季度': 'Q1', '二季度': 'Q2', '三季度': 'Q3', '四季度': 'Q4', '全年': 'Y', '年末': 'Y', '年底': 'Y'}


def relative_periods(text: str, explicit: list[PeriodMention], fallback_year: int | None) -> list[PeriodMention]:
    """“上半年”“二季度”等不带年份的期间词：年份取本句此前最近的显式期间，否则取上文最近年份。"""
    output = []
    for match in _RELATIVE.finditer(text):
        if any(m.start <= match.start() < m.end for m in explicit):
            continue
        preceding = [m for m in explicit if m.end <= match.start()]
        if preceding and preceding[-1].is_range:
            continue              # 多年序列中的“第四季度/全年”不能绑定上文的单一年份
        before = [m for m in preceding if not m.is_range]
        year = int(before[-1].periods[0][:4]) if before else fallback_year
        if not year:
            continue
        output.append(PeriodMention(match.start(), match.end(), (f'{year}{_RELATIVE_CODE[match["word"]]}',), text=match[0]))
    return output
