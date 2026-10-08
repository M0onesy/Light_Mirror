"""Conservative modality lineage from original prose, never model numbers.

PDF line wrapping is not a semantic boundary. Explicit forecast predicates and
periods may govern several wrapped lines, but stop at a sentence, paragraph,
page, table or a new historical clause. Every excluded occurrence retains its
exact source rule; uncertain prose remains executable unresolved work.
"""
from __future__ import annotations

import re

from .mentions import CONTEXT_TERMS, VALUE_RE


_PREDICATE = re.compile(r"预\s*(?:计|测|估)|\b(?:we\s+)?(?:forecast|estimate|expect)\b", re.I)
_ASSUMPTION = re.compile(r'假设|假定|按照(?=[^。；;]{0,180}(?:定价[^。；;]{0,60}计算|来预测))')
_VALUATION = re.compile(r"目标价|当前股价(?:为|约为|达到|对应)|估值(?:水平|倍数|为|达)|\btarget\s+price\b", re.I)
_PERIOD = re.compile(r"(?<!\d)20\d{2}(?:\s*(?:[-－–—~～]|至|到|to|through)\s*20\d{2}"
                     r"\s*(?:年|[EY]\b)?|\s*(?:年(?:度|上半年|下半年|第?[一二三四1-4]季度)?|[EY]\b))", re.I)
_EN_PERIOD = re.compile(r"(?<![\w.])20\d{2}(?:\s*(?:[-–—]|to|through)\s*20\d{2})?(?!\w|\.\d|\s*(?:元|万元|亿元))", re.I)
_BOUNDARY = re.compile(r"[。！？!?；;]|\n[ \t\r]*\n|\f|\.(?!\d)(?=\s|$)")
_STRUCTURE = re.compile(r"(?m)^[ \t]*(?:(?:图|表)\s*\d+\s*[:：]|(?:数据|资料)来源\s*[:：]"
                        r"|(?:合并|母公司)?(?:资产负债表|利润表|现金流量表)|主要财务比率|财务预测摘要)")
_CLAUSE = re.compile(r"[,，]\s*|\n\s*")
_NEW_PERIOD = re.compile(r"(?:(?:而|但|但是|此外|另外|同时|其中|公司|该公司|本公司|发行人)\s*)*"
                        r"(?P<period>20\d{2}\s*年(?:度|上半年|下半年|第?[一二三四1-4]季度)?)")
_ACTUAL = re.compile(r"(?:(?:而|但|但是|公司|该公司|本公司|发行人)\s*)*"
                    r"(?:20\d{2}\s*年\s*)?(?:实际(?:实现)?|已实现|已披露|已公布|历史(?:数据|金额))")
_COMPARATOR = re.compile(r"(?:较|比|相比(?:于)?|相较(?:于)?|对比|从|由)\s*"
                        r"(?P<period>20\d{2}\s*年(?:度|上半年|下半年|第?[一二三四1-4]季度)?)")
_EN_FINANCE = re.compile(r"\b(?:revenue|earnings|net\s+(?:profit|income)|EPS|EBITDA|cash\s+flow)\b", re.I)
_EN_ACTUAL = re.compile(r"(?:but\s+|while\s+)?(?:actual(?:ly)?|reported|historical)\b", re.I)
_INDUSTRY_MEASURE = re.compile(r'(?:市场|行业)(?:规模|份额|渗透率|增速|增长率)')
_FORECAST_SUBJECT = re.compile('|'.join(re.escape(term) for term in sorted(CONTEXT_TERMS, key=len, reverse=True))
                               + r'|(?:市场|行业)(?:规模|份额|渗透率|增速|增长率)')


def _preceding_forecast_subject(text: str, start: int, page_start: int) -> dict | None:
    """Accept a directly preceding measure caption, never a remote topic.

    A title such as ``市场规模预计2025年达到...`` puts its financial
    subject before the verb. Permit only whitespace and a small set of
    source-written linking words after that caption; earlier paragraph/table
    labels or historical figures cannot silently donate a forecast subject.
    """
    left = max(page_start, start-80)
    prefix = text[left:start]
    boundary = list(_BOUNDARY.finditer(prefix))
    if boundary:
        left += boundary[-1].end()
        prefix = text[left:start]
    matches = list(_FORECAST_SUBJECT.finditer(prefix))
    if not matches:
        return None
    subject = matches[-1]
    if not re.fullmatch(r'\s*(?:(?:将|将会|有望|或|则|的)\s*)*', prefix[subject.end():]):
        return None
    return {'start': left+subject.start(), 'end': left+subject.end(), 'text': subject[0],
            'kind': 'directly_preceding_measure_caption'}


def _years(value: str) -> set[int]:
    years = [int(v) for v in re.findall(r"20\d{2}", value)]
    if len(years) == 2 and 0 <= years[1] - years[0] <= 10:
        return set(range(years[0], years[1] + 1))
    return set(years)


def _bound(text: str, start: int, page_end: int) -> tuple[int, str]:
    # A missing punctuation mark cannot govern an unbounded PDF page.
    end, kind = min(page_end, start + 900), 'page_end' if page_end <= start + 900 else 'length_limit'
    for pattern, name in [(_BOUNDARY, 'sentence_or_paragraph'), (_STRUCTURE, 'source_structure')]:
        match = pattern.search(text, start, end)
        if match and match.start() < end:
            end, kind = match.start(), name
    line_end = start
    for _ in range(16):
        line_end = text.find('\n', line_end + 1, end)
        if line_end < 0:
            break
    else:
        end, kind = line_end, 'line_limit'
    return end, kind


def _historical_boundary(text: str, start: int, end: int, forecast_years: set[int]) -> tuple[int, str | None]:
    for clause in _CLAUSE.finditer(text, start, end):
        tail = text[clause.end():end]
        compact = re.sub(r'\s+', '', tail[:100])
        new_period = _NEW_PERIOD.match(tail)
        period_range = re.match(r'(?:(?:而|但|公司|该公司|本公司|发行人)\s*)*20\d{2}\s*(?:年\s*)?[-－–—~～至到]\s*20\d{2}\s*年?',tail)
        actual = _ACTUAL.match(tail) or _EN_ACTUAL.match(tail)
        # "较2023年增长20%" is a forecast comparison, not a new historical
        # subject. Only an explicit new clause or actual disclosure resets it.
        if actual or (new_period and not (_years(new_period['period']) & forecast_years)
                      and not (period_range and _years(period_range[0]) & forecast_years)):
            return clause.start(), 'explicit_historical_clause'
        if (new_period and re.search(r'实际|已实现|已披露|已公布|录得', compact[:65])):
            return clause.start(), 'explicit_historical_clause'
    return end, None


def _historical_operands(text: str, start: int, end: int, forecast_years: set[int]) -> list[dict]:
    """Keep explicitly stated historical baseline amounts in mixed prose.

    A comparison year alone is insufficient: "较2023年增长20%" keeps its
    forecast rate. A dimensioned historical amount before the growth predicate
    is protected without assigning it an account, scope or true value.
    """
    output = []
    for comparator in _COMPARATOR.finditer(text, start, end):
        if _years(comparator['period']) & forecast_years:
            continue
        stop = min(end, comparator.end() + 100)
        delimiter = re.search(r'[,，。；;]', text[comparator.end():stop])
        if delimiter:
            stop = comparator.end() + delimiter.start()
        amount = VALUE_RE.search(text, comparator.end(), stop)
        if not amount or not amount['unit']:
            continue
        prefix = re.sub(r'\s+', '', text[comparator.end():amount.start('num')])
        if re.search(r'增长|下降|下滑|同比|环比|增速|增长率|提升|提高|增加|减少', prefix):
            continue
        if (not prefix.strip('的为达达到：:()（）') or
                any(term in prefix for term in CONTEXT_TERMS) or _EN_FINANCE.search(prefix)):
            output.append({'start': amount.start('num'), 'end': amount.end(),
                           'text': text[amount.start('num'):amount.end()],
                           'period': {'start': comparator.start('period'), 'end': comparator.end('period'),
                                      'text': comparator['period']}})
    return output


def _undated_forecast_occurrences(text: str, predicate, page, end: int,
                                  preceding_subject: dict | None) -> dict[int, dict]:
    """A written forecast can omit its period without becoming historical.

    Admit only the same directly governed financial clause. A following
    comma, semicolon, sentence, source heading or actual/historical marker
    stops this rule. It classifies original occurrences; no date, amount or
    financial definition is inferred for execution.
    """
    tail = text[predicate.end():end]
    stop = re.search(r'[,，]|实际(?:实现)?|已实现|已披露|已公布|历史(?:数据|金额)', tail)
    if stop:
        end = predicate.end() + stop.start()
    numbers = list(VALUE_RE.finditer(text, predicate.end(), end))
    if not numbers:
        return {}
    first = numbers[0]
    prefix = text[predicate.end():first.start('num')]
    compact = re.sub(r'\s+', '', prefix)
    measure = _FORECAST_SUBJECT.search(compact) or _EN_FINANCE.search(prefix)
    if not measure and preceding_subject is None:
        return {}
    # Naked quantities/identifier years are not predictive financial values.
    # Every accepted target must itself carry a monetary or ratio unit.
    eligible = [number for number in numbers if number['unit'] in {
        '元', '万元', '亿元', '百万元', '亿', '%', '％', '百分点'}]
    if not eligible or eligible[0].start('num') != first.start('num'):
        return {}
    basis = {'schema_version': 'source-prose-modality.v1', 'modality': 'forecast',
             'page': page.get('page'),
             'predicate': {'start': predicate.start(), 'end': predicate.end(), 'text': predicate[0]},
             'source_span': {'start': predicate.start(), 'end': end,
                             'text': text[predicate.start():end]},
             'declared_periods': [], 'period_status': 'source_unspecified',
             'boundary': {'start': end, 'kind': 'direct_undated_forecast_clause'},
             'protected_historical_operands': []}
    if preceding_subject:
        basis['measure_subject'] = preceding_subject
    elif measure:
        basis['measure_subject'] = {'text': measure[0], 'kind': 'following_measure_caption'}
    return {number.start('num'): {'reason': 'forecast_prose_without_period',
            'basis': {**basis, 'occurrence_span': [number.start('num'), number.end()]}}
            for number in eligible}


def prose_modality_exclusions(text: str, pages: list[dict]) -> dict[int, dict]:
    """Map exact numeric starts to supported forecast/valuation source rules."""
    output = {}
    pages = pages or [{'start': 0, 'end': len(text), 'page': 1}]
    for page in pages:
        page_start, page_end = int(page['start']), int(page['end'])
        predicates = [(m, 'forecast') for m in _PREDICATE.finditer(text, page_start, page_end)]
        predicates += [(m, 'valuation') for m in _VALUATION.finditer(text, page_start, page_end)]
        predicates += [(m, 'hypothesis') for m in _ASSUMPTION.finditer(text, page_start, page_end)]
        for predicate, modality in sorted(predicates, key=lambda item: item[0].start()):
            prior = re.sub(r'\s+', '', text[max(page_start, predicate.start()-12):predicate.start()])
            if re.search(r'(?:不|未|没有|并非|不是|不能|无法)$', prior):
                continue
            if modality == 'forecast' and re.match(r'\s*负债', text[predicate.end():page_end]):
                continue  # "预计负债" is an accounting item, not a forecast verb.
            if modality == 'forecast' and re.match(r'\s*(?:模型|方法|误差|准确率|能力|技术)', text[predicate.end():page_end]):
                continue  # A forecast model's historical inputs are not its predictions.
            end, boundary_kind = _bound(text, predicate.end(), page_end)
            prefix_end = min(end, predicate.end() + 100)
            period_pattern = _EN_PERIOD if predicate[0].isascii() else _PERIOD
            declared = list(period_pattern.finditer(text, predicate.end(), prefix_end))
            source_prefix = text[predicate.end():prefix_end]
            prefix = re.sub(r'\s+', '', source_prefix)
            preceding_subject = _preceding_forecast_subject(text, predicate.start(), page_start)
            following_measure = (any(term in prefix for term in CONTEXT_TERMS)
                                 or _EN_FINANCE.search(source_prefix) or _INDUSTRY_MEASURE.search(prefix))
            first_numeric = VALUE_RE.search(text, predicate.end(), prefix_end)
            # A date in a later historical clause does not govern the earlier
            # undated forecast target ("预计营收120万元，2023年实际...").
            earlier_undated_target = (first_numeric and declared and not predicate[0].isascii()
                                      and first_numeric.start('num') < declared[0].start())
            if modality == 'forecast' and (not declared or earlier_undated_target):
                for numeric_start, rule in _undated_forecast_occurrences(
                        text, predicate, page, end, preceding_subject).items():
                    output.setdefault(numeric_start, rule)
                continue
            if modality == 'forecast' and (not declared or not (following_measure or preceding_subject)):
                continue
            if modality == 'hypothesis' and not (following_measure or preceding_subject):
                continue
            if modality == 'forecast' and preceding_subject and not following_measure:
                # An unrelated future noun/operational count cannot inherit
                # the preceding financial caption. Require its direct
                # dimensioned continuation after the declared target period.
                continuation = text[declared[0].end():end]
                amount = VALUE_RE.search(continuation)
                linking = re.sub(r'\s+', '', continuation[:amount.start()]) if amount else ''
                if (not amount or amount['unit'] not in {'元', '万元', '亿元', '百万元', '亿', '%', '％', '百分点'}
                        or not re.fullmatch(r'(?:(?:将|会|可|有望|达到|达|为|约|大约|超过|超|'
                                            r'增长至|增长到|增长|上升至|提升至|在|到|至))*', linking)):
                    continue
            if (modality == 'forecast' and not predicate[0].isascii() and first_numeric and
                    first_numeric.start('num') < declared[0].start()):
                continue  # A later historical clause cannot supply the forecast period.
            # English bare-year declarations are accepted only after explicit
            # forecast verbs; no bare amount is ever inferred to be a year.
            forecast_years = _years(declared[0][0]) if declared and modality == 'forecast' else set()
            end, historical_kind = _historical_boundary(text, predicate.end(), end, forecast_years)
            boundary_kind = historical_kind or boundary_kind
            protected = _historical_operands(text, predicate.end(), end, forecast_years)
            declared = [m for m in declared if m.end() <= end]
            basis = {
                'schema_version': 'source-prose-modality.v1', 'modality': modality,
                'page': page.get('page'),
                'predicate': {'start': predicate.start(), 'end': predicate.end(), 'text': predicate[0]},
                'source_span': {'start': predicate.start(), 'end': end,
                                'text': text[predicate.start():end]},
                'declared_periods': [{'start': m.start(), 'end': m.end(), 'text': m[0]} for m in declared],
                'boundary': {'start': end, 'kind': boundary_kind},
                'protected_historical_operands': protected,
            }
            if preceding_subject:
                basis['measure_subject'] = preceding_subject
            for amount in VALUE_RE.finditer(text, predicate.end(), end):
                numeric_start, numeric_end = amount.span('num')
                if any(p['start'] <= numeric_start < p['end'] for p in protected):
                    continue
                if any(m.start() <= numeric_start and numeric_end <= m.end() for m in declared):
                    continue  # Periods retain the ledger's identifier rule.
                output.setdefault(numeric_start, {
                    'reason': 'forecast_prose_lineage' if modality == 'forecast' else 'hypothesis_prose_lineage' if modality == 'hypothesis' else 'valuation_prose_lineage',
                    'basis': {**basis, 'occurrence_span': [numeric_start, amount.end()]},
                })
    return output
