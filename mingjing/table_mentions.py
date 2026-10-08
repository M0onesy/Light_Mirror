"""Source-addressed, conservative financial table cell extraction.

This module never consults a model or fills a missing financial dimension from
the requested period. Forecast columns remain observable but are out of scope.
It does not claim cell-level PDF geometry: ``start/end`` identify original text.
"""
from __future__ import annotations

import re
import hashlib
from dataclasses import replace
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .models import AtomicClaim
from .mentions import (previous_period, unit_multiplier, source_currency, net_margin_definition_matches,
                       SOURCE_REVIEW_LABELS, SOURCE_REVIEW_RATIOS, SOURCE_REVIEW_SCALARS, SOURCE_REVIEW_PER_SHARE)
from .mentions import source_metric_identity
from .metrics import RATIO_METRICS, TURNOVER_METRICS
from .taxonomy import FINANCIAL_LABELS, UNDEFINED_ACCOUNTS


_LABELS = {
    "归属于上市公司股东的扣除非经常性损益后的净利润": "NP_PARENT_ADJ",
    "归属于挂牌公司股东的扣除非经常性损益后的净利润": "NP_PARENT_ADJ",
    "归属于母公司股东的扣除非经常性损益后的净利润": "NP_PARENT_ADJ",
    "扣除非经常性损益后的净利润": "NP_PARENT_ADJ",
    "归属于母公司所有者的净利润": "NP_PARENT",
    "归属于母公司股东的净利润": "NP_PARENT",
    "归属于上市公司股东的净利润": "NP_PARENT",
    "归属于挂牌公司股东的净利润": "NP_PARENT",
    "归属母公司净利润": "NP_PARENT", "归母净利润": "NP_PARENT",
    "归属于母公司净利润": "NP_PARENT",
    "扣非净利润": "NP_PARENT_ADJ",
    "经营活动产生的现金流量净额": "CFO",
    "经营活动现金流": "CFO", "经营现金流": "CFO",
    "归属于母公司所有者权益合计": "EQUITY_PARENT",
    "归属于母公司股东的净资产": "EQUITY_PARENT",
    "归属于上市公司股东的净资产": "EQUITY_PARENT",
    "非经常性损益项目合计": "NRI_TOTAL",
    "非经常性损益总额": "NRI_TOTAL", "非经常性损益合计": "NRI_TOTAL",
    "所得税影响额": "NRI_TAX_EFFECT",
    "少数股东权益影响额": "NRI_MINORITY_RAW",
    "营业总成本": "TOTAL_COST", "主营业务成本": "MAIN_COST",
    "营业总收入": "REVENUE", "营业收入": "REVENUE", "营业成本": "COST",
    "资产总计": "ASSETS", "资产合计": "ASSETS",
    "负债总计": "LIABILITIES", "负债合计": "LIABILITIES",
    "基本每股收益": "EPS", "稀释每股收益": "EPS_DILUTED",
    "每股收益": "EPS_UNSPECIFIED",
    "加权平均净资产收益率": "ROE", "净资产收益率": "ROE_UNSPECIFIED",
    "资产负债率": "DEBT_RATIO", "毛利率": "GROSS_MARGIN",
    "净利率": "NET_MARGIN_UNSPECIFIED", "净利润": "NP",
    "净利润率": "NET_MARGIN_UNSPECIFIED", "归母净利润率": "NET_MARGIN_PARENT_UNSPECIFIED",
    "归母净利率": "NET_MARGIN_PARENT_UNSPECIFIED",
    "ROE": "ROE_UNSPECIFIED", "EPS": "EPS_UNSPECIFIED",
    "P/E": "VALUATION", "P/B": "VALUATION", "P/S": "VALUATION", "EV/EBITDA":"VALUATION",
}
_LABELS = {**FINANCIAL_LABELS, **SOURCE_REVIEW_LABELS, **_LABELS,
           '归母净利润率': 'NP_PARENT_MARGIN', '归母净利率': 'NP_PARENT_MARGIN',
           '流动比率': 'CURRENT_RATIO',
           '销售费用率': 'SELLING_EXPENSE_RATIO', '管理费用率': 'ADMIN_EXPENSE_RATIO',
           '研发费用率': 'RD_EXPENSE_RATIO', '财务费用率': 'FINANCE_EXPENSE_RATIO'}
_RATIO_ACCOUNTS = {"GROSS_MARGIN", "NET_MARGIN", "NET_MARGIN_UNSPECIFIED", "NET_MARGIN_PARENT_UNSPECIFIED",
                   "ROE", "ROE_UNSPECIFIED", "DEBT_RATIO"} | SOURCE_REVIEW_RATIOS | set(RATIO_METRICS)
_PER_SHARE_SECTION = re.compile(r'^(?:每股指标|每股数据|每股财务指标)[（(](元)[）)]$')
_FINANCIAL_SECTION = re.compile(r'^(?:成长能力|获利能力|偿债能力|营运能力|估值比率|每股指标|每股数据|每股财务指标)$')
_TITLE = re.compile(r"^(?:附[:：]?)?(?:(?:合并|母公司)?(?:资产负债表|利润表|现金流量表)|财务预测摘要|主要财务比率|主要会计数据|会计数据和财务指标|主要财务指标|指标)(?:[（(](?:百万元|亿元|万元|元|%|％)[）)])?$")
_STOP = re.compile(r"客户|可比公司|证券代码|股票代码|财务报表附注|所有者权益变动表|免责声明|分析师|评级|^数据来源[:：]|^资料来源[:：]|^图[表]?\s*\d+[:：]")
_HEADER = re.compile(r"^(?P<year>20\d{2})\s*(?P<suffix>A|E|H1|H2|Q[1-4]|9M|Y|年(?:度|末|半年度|上半年|前三季度)?)?$", re.I)
_CELL = re.compile(r"(?P<number>[+\-－−]?\d[\d,，]*(?:\.\d+)?)\s*(?P<unit>百万元|亿元|万元|元|%|％|倍)?")
_UNIT = re.compile(r"(?:单位\s*[:：]\s*|[（(]\s*)(百万元|亿元|万元|元|%|％)(?:\s*[）)]|\s*$)")


@dataclass(frozen=True)
class _Line:
    text: str
    start: int
    end: int


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _lines(text: str, start: int, end: int) -> list[_Line]:
    output, offset = [], start
    for raw in text[start:end].splitlines(keepends=True):
        value = raw.strip()
        if value:
            left = len(raw) - len(raw.lstrip())
            output.append(_Line(value, offset + left, offset + left + len(value)))
        offset += len(raw)
    return output


def _table_flows(text: str, pages: list[dict]) -> list[dict]:
    """Separate side-by-side tables only when source line geometry proves lanes.

    PyMuPDF often emits a left balance-sheet row followed by a right income
    row in a single block. Stateful period/unit parsing of that sequence leaks
    across tables. Financial table titles in distinct x lanes establish the
    boundary; source offsets and the untouched document remain authoritative.
    Missing or conflicting geometry falls back to conservative text parsing.
    """
    output = []
    for page in pages:
        geometry = page.get('lines', [])
        titles = [line for line in geometry if line.get('bbox') and
                  _TITLE.fullmatch(_compact(line['text'])) and
                  re.search(r'资产负债表|利润表|现金流量表|主要财务比率', line['text'])]
        anchors = []
        for line in sorted(titles, key=lambda item: item['bbox'][0]):
            x = float(line['bbox'][0])
            if not anchors or x - anchors[-1] > 25:
                anchors.append(x)
        if len(anchors) < 2 or len(anchors) > 3 or any(b-a < 120 for a,b in zip(anchors, anchors[1:])):
            output.append(page)
            continue
        boundaries = [anchor-2 for anchor in anchors[1:]]
        top = min(line['bbox'][1] for line in titles)
        lanes = [[] for _ in anchors]
        ambiguous = False
        for line in geometry:
            box = line.get('bbox')
            if not box or box[1] < top-2:
                continue
            compact = _compact(line['text'])
            if not compact:
                continue
            # A full-width source citation/footer cannot belong to either
            # financial lane. It is a stop, not a monetary cell.
            if re.match(r'^(?:数据|资料)来源[:：]', compact) or re.fullmatch(r'\d+/\d+', compact):
                continue
            lane = sum(box[0] >= boundary for boundary in boundaries)
            if any(box[0] < boundary < box[2]-2 for boundary in boundaries):
                # Long financial labels are not split at a guessed boundary.
                if _TITLE.fullmatch(compact) or _cell_line(compact):
                    ambiguous = True
                    break
                continue
            start, end = line.get('start'), line.get('end')
            if start is None or end is None or text[start:end].strip() != line['text'].strip():
                ambiguous = True
                break
            lanes[lane].append(_Line(line['text'].strip(), start, end))
        if ambiguous or any(not lane for lane in lanes):
            output.append(page)
        else:
            for index, lane in enumerate(lanes):
                # Read within each physical lane from top to bottom. All rows
                # still point back to exact offsets in the original sequence.
                positions = {line['start']: line['bbox'][1] for line in geometry}
                lane.sort(key=lambda item: (positions[item.start], item.start))
                output.append({**page, '_source_lines': lane, '_geometry_lane': index+1})
    return output


def _header(value: str) -> tuple[str, bool] | None:
    match = _HEADER.fullmatch(value.strip())
    if not match:
        return None
    suffix = (match['suffix'] or 'Y').upper()
    forecast = suffix == 'E'
    kind = {"A": "Y", "E": "Y", "年": "Y", "年度": "Y", "年末": "Y",
            "年半年度": "H1", "年上半年": "H1", "年前三季度": "9M"}.get(suffix, suffix)
    return match['year'] + kind, forecast


def _cell_line(value: str) -> bool:
    clean = value.strip().strip('|').strip()
    if clean in {'-', '—', '－', '--', 'N/A', '不适用'}:
        return True
    tokens = list(_CELL.finditer(clean))
    if tokens and not _CELL.sub('', clean).strip(' |\t'):
        return True
    # Retain an unsupported Chinese-number cell instead of silently dropping it.
    return bool(re.fullmatch(r"[负正零一二三四五六七八九十百千万亿点两]+(?:元|万元|亿元|%)?", clean))


def _row(value: str, previous_account: str) -> tuple[str, str, str] | None:
    compact = _compact(value).strip('|')
    compact = re.sub(r"^(?:[一二三四五六七八九十]+[、.]|\d+[.、]|其中[:：]|加[:：]|减[:：])", '', compact)
    if re.fullmatch(r"(?:YOY|同比|增长率)[（(]?[%％]?[）)]?", compact, re.I):
        return previous_account or 'UNKNOWN', '%', 'yoy'
    definitions = [(compact[start:end], account) for start,end,account in net_margin_definition_matches(compact) if start==0]
    for label, account in definitions + sorted(_LABELS.items(), key=lambda row: -len(row[0])):
        if not compact.startswith(label):
            continue
        tail = compact[len(label):]
        relation = 'value'
        if re.match(r"^(?:同比|增长率|增速)", tail):
            relation, tail = 'yoy', re.sub(r"^(?:同比)?(?:增长率|增速)?", '', tail)
        unit_match = re.search(r"[（(]([^）)]+)[）)]|([%％])$", tail)
        row_unit = ''
        if unit_match:
            annotation = unit_match[1] or unit_match[2]
            if annotation in {'百万元', '亿元', '万元', '元', '%', '％', '倍'}:
                row_unit = annotation
            elif (account.startswith('EPS') or account in SOURCE_REVIEW_PER_SHARE) and ('元' in annotation or '摊薄' in annotation):
                row_unit = '元' if '元' in annotation else ''
                # Broker "摊薄" commonly uses ending share capital, whereas
                # statutory diluted EPS uses adjusted weighted shares. An
                # annotation alone does not establish the statutory identity.
                account = 'EPS_BROKER_DILUTED_UNSPECIFIED' if account.startswith('EPS') and '摊薄' in annotation else account
            elif not re.match(r"(?:净亏损|亏损|净损失)", annotation):
                # An unsupported annotation remains a real source row. Keep
                # its header/forecast lineage as UNKNOWN instead of losing
                # all cells to the unstructured occurrence fallback.
                break
            tail = tail[:unit_match.start()] + tail[unit_match.end():]
        if tail:
            # A caption such as "资本公积增加" is a flow, not the recognised
            # balance "资本公积". It must not receive that account, but a
            # genuine caption followed by aligned cells still has source
            # periods and forecast columns. The UNKNOWN fallback below
            # preserves those dimensions without creating financial truth.
            break
        if row_unit in {'%', '％'} and account not in _RATIO_ACCOUNTS:
            relation = 'yoy'
        return account, row_unit, relation
    # Unregistered captions keep source lineage without inventing a family.
    if (0 < len(compact) < 35 and re.search(r"[\u4e00-\u9fff]", compact)
            and not re.search(r"\d|[。；：:]", compact) and not _TITLE.search(compact)):
        return 'UNKNOWN', '', 'value'
    # An English financial label outside the authorised taxonomy still has
    # real historical/forecast column lineage. Never guess EBITDA as profit
    # or ROIC as ROE merely to parse it.
    if (not _cell_line(compact) and
            re.fullmatch(r"[A-Za-z][A-Za-z _./%％()（）-]{0,33}", compact)):
        units = re.search(r"[（(]([%％])[）)]$", compact)
        return 'UNKNOWN', units[1] if units else '', 'value'
    return None


def table_metric_identity(account: str, label: str, title: str) -> tuple[str, str]:
    """Resolve the source caption in its own financial statement domain.

    A cash-flow model's finance adjustment is not its income-statement
    expense. A familiar label never authorizes that cross-statement mapping.
    Extraction and source-proof reconstruction share this exact decision.
    """
    account, definition = source_metric_identity(account, label)
    if '现金流量表' in title:
        if account in {'SHORT_TERM_BORROWING', 'LONG_TERM_BORROWING'}:
            return account + '_CHANGE_UNSPECIFIED', ''
        if account == 'EXP_FINANCE':
            return 'BROKER_CFO_FINANCE_UNSPECIFIED', ''
    return account, definition


def extract_table_mentions(text: str, pages: list[dict], blocks: list[dict],
                           company: str, period: str) -> list[AtomicClaim]:
    """Return every observed table occurrence, including unresolved/forecast cells.

    ``period`` is intentionally not used to assign table columns. A table without
    authoritative headers receives an empty period and ``parse_status=unresolved``.
    Section units are reset at each table title; a row unit never leaks downstream.
    """
    claims: list[AtomicClaim] = []
    pages = pages or [{'start': 0, 'end': len(text), 'page': 1}]
    for page in _table_flows(text, pages):
        lines = page.get('_source_lines') or _lines(text, int(page['start']), int(page['end']))
        active, table_unit, scope = False, '', '未知'
        table_unit_source = None
        per_share_unit_source = None
        table_currency = None
        columns: list[tuple[str, bool]] = []
        header_spans: list[dict] = []
        source_title = ''
        title_source = None
        summary = _summary_source(text, page)
        previous_account = ''
        previous_account_lines: list[_Line] = []
        index = 0
        while index < len(lines):
            line = lines[index]
            compact = _compact(line.text).strip('|')
            if _STOP.search(compact):
                active, table_unit, columns, previous_account = False, '', [], ''
                table_currency = None
                table_unit_source = None
                per_share_unit_source = None
                previous_account_lines = []
                header_spans, source_title = [], ''
                index += 1
                continue
            if _TITLE.search(compact):
                active, columns, previous_account = True, [], ''
                table_currency = None
                per_share_unit_source = None
                previous_account_lines = []
                header_spans, source_title = [], line.text
                title_source = {'start': line.start, 'end': line.end, 'text': text[line.start:line.end]}
                title_geometry = next((item.get('bbox') for item in page.get('lines',[]) if item.get('start')==line.start), None)
                if title_geometry:
                    title_source['bbox']=list(title_geometry)
                table_unit = (_UNIT.search(compact)[1] if _UNIT.search(compact) else '')
                table_unit_source = ({'kind':'table_unit_header','unit':table_unit,'start':line.start,
                                     'end':line.end,'text':text[line.start:line.end],
                                     'layout_lane':page.get('_geometry_lane')} if table_unit else None)
                scope = '母公司' if '母公司' in compact else '合并' if '合并' in compact else '未知'
                index += 1
                continue
            if not active:
                index += 1
                continue
            currency = source_currency(line.text)
            if currency:
                table_currency = {**currency, 'source_span':{'start':line.start,'end':line.end,'text':line.text}}
            if compact.startswith('单位') or compact.startswith('金额单位'):
                units = _UNIT.search(compact)
                table_unit = units[1] if units else ''
                table_unit_source = ({'kind':'table_unit_declaration','unit':table_unit,'start':line.start,
                                     'end':line.end,'text':text[line.start:line.end],
                                     'layout_lane':page.get('_geometry_lane')} if table_unit else None)
                per_share_unit_source = None
                index += 1
                continue
            section = _PER_SHARE_SECTION.fullmatch(compact)
            if section:
                per_share_unit_source = {'kind':'per_share_section','unit':section[1],
                                         'start':line.start,'end':line.end,'text':text[line.start:line.end],
                                         'table_title_sha256':hashlib.sha256(source_title.encode('utf-8')).hexdigest(),
                                         'layout_lane':page.get('_geometry_lane')}
                previous_account, previous_account_lines = '', []
                index += 1
                continue
            if _FINANCIAL_SECTION.fullmatch(compact):
                per_share_unit_source = None
                previous_account, previous_account_lines = '', []
                index += 1
                continue
            # Only contiguous pure year cells or an entirely year-only line are
            # headers. A title/prose year and chart x-axis are not financial data.
            header_tokens = [t for t in re.split(r"[\s|]+", line.text.strip()) if t]
            header_values = [_header(t) for t in header_tokens]
            if header_values and all(header_values):
                group = [value for value in header_values if value is not None]
                header_spans = [{'start': line.start, 'end': line.end, 'text': text[line.start:line.end]}]
                cursor = index + 1
                while cursor < len(lines) and (value := _header(lines[cursor].text)):
                    if value in group:  # A repeated side-by-side header starts a new group.
                        group = []
                        header_spans = []
                    group.append(value)
                    header_spans.append({'start': lines[cursor].start, 'end': lines[cursor].end,
                                         'text': text[lines[cursor].start:lines[cursor].end]})
                    cursor += 1
                columns = group
                previous_account, previous_account_lines = '', []
                index = cursor
                continue
            # Wrapped supported labels may occupy several source lines. Do not
            # join arbitrary section labels into a new invented account.
            matched = _row(line.text, previous_account)
            label_end = index + 1
            for width in range(2, min(4, len(lines) - index + 1)):
                joined = ''.join(item.text for item in lines[index:index+width])
                candidate = _row(joined, previous_account)
                if candidate and candidate[0] != 'UNKNOWN':
                    matched, label_end = candidate, index + width
                    break
                if _cell_line(lines[index+width-1].text):
                    break
            if not matched:
                index += 1
                continue
            account, row_unit, relation = matched
            account, metric_definition_id = table_metric_identity(account, line.text, source_title)
            if summary and source_title and '合并' not in source_title and '母公司' not in source_title:
                account = {'OTHER_CURRENT_ASSETS':'BROKER_OTHER_CURRENT_ASSETS',
                           'OTHER_CURRENT_LIABILITIES': 'BROKER_OTHER_CURRENT_LIABILITIES',
                           'OTHER_NONCURRENT_LIABILITIES': 'BROKER_OTHER_NONCURRENT_LIABILITIES',
                           'OTHER_NONCURRENT_ASSETS': 'BROKER_OTHER_NONCURRENT_ASSETS'}.get(account, account)
            inherited = bool(re.fullmatch(r"(?:YOY|同比|增长率)[（(]?[%％]?[）)]?", compact, re.I)
                             and previous_account and previous_account_lines)
            cells: list[_Line] = []
            cursor = label_end
            while cursor < len(lines) and _cell_line(lines[cursor].text):
                cells.append(lines[cursor])
                cursor += 1
            if not cells:
                index = label_end
                continue
            matches: list[tuple[_Line, re.Match | None]] = []
            for cell in cells:
                found = list(_CELL.finditer(cell.text))
                matches.extend((cell, match) for match in found) if found else matches.append((cell, None))
            aligned = bool(columns) and len(columns) == len(matches)
            label_lines = lines[index:label_end]
            row_lines = (previous_account_lines if inherited else []) + label_lines + cells
            account_lineage = None
            if inherited:
                preceding_label = '\n'.join(text[item.start:item.end] for item in previous_account_lines)
                account_lineage = {
                    'schema_version': 'source-table-account-lineage.v1',
                    'kind': 'preceding_primary_row', 'label_text': preceding_label,
                    'label_sha256': hashlib.sha256(preceding_label.encode('utf-8')).hexdigest(),
                    'label_start_in_context': 0, 'label_end_in_context': len(preceding_label),
                    'label_spans': [{'start': item.start, 'end': item.end,
                                     'text': text[item.start:item.end]} for item in previous_account_lines],
                    'row_start': line.start,
                    'table_title_sha256': hashlib.sha256(source_title.encode('utf-8')).hexdigest(),
                }
            if page.get('_geometry_lane') or inherited:
                # A contiguous source slice crosses the other physical lane.
                # Keep only proven lane lines in semantic context, with exact
                # source spans for every excerpt; numeric offsets still point
                # into the untouched complete document.
                row_offsets, fragments, length = {}, [], 0
                for item in row_lines:
                    row_offsets[item.start] = length
                    fragment = text[item.start:item.end]
                    fragments.append(fragment)
                    length += len(fragment) + 1
                row_text = '\n'.join(fragments)
                row_label = '\n'.join(text[item.start:item.end] for item in label_lines)
            else:
                row_offsets = {}
                row_text = text[line.start:cells[-1].end]
                row_label = text[line.start:lines[label_end-1].end]
            if account.startswith('EPS') or account in SOURCE_REVIEW_PER_SHARE:
                # Per-share data cannot inherit a statement's monetary scale.
                # An explicit local subsection supplies yuan without changing
                # neighbouring rows or the other physical financial lane.
                effective_unit = row_unit or (per_share_unit_source['unit'] if per_share_unit_source else '') or ('元' if table_unit=='元' else '')
                unit_proof = (per_share_unit_source or (table_unit_source if table_unit=='元' else None)) if not row_unit else None
            else:
                effective_unit = row_unit or ('倍' if account in SOURCE_REVIEW_SCALARS or account in TURNOVER_METRICS or account in {'CURRENT_RATIO','QUICK_RATIO'} else '%' if account in _RATIO_ACCOUNTS else table_unit)
                unit_proof = (None if row_unit else
                              {'kind':'source_caption_dimension','unit':'倍','start':line.start,
                               'end':lines[label_end-1].end,'text':row_label,
                               'dimension':'ratio','layout_lane':page.get('_geometry_lane')}
                              if account in SOURCE_REVIEW_SCALARS or account in TURNOVER_METRICS or account in {'CURRENT_RATIO','QUICK_RATIO'} else table_unit_source)
            nr = '扣非' if account == 'NP_PARENT_ADJ' else '未扣非'
            for column_index, (cell, match) in enumerate(matches):
                value, decimals, numeric = None, None, cell.text
                start, end = cell.start, cell.end
                cell_unit = effective_unit
                if match:
                    start, end = cell.start + match.start(), cell.start + match.end()
                    numeric = text[start:end]
                    cell_unit = match['unit'] or effective_unit
                    raw = match['number'].replace(',', '').replace('，', '').replace('－', '-').replace('−', '-')
                    try:
                        parsed = Decimal(raw)
                        if cell_unit in {'百万元', '亿元', '万元', '元', '%', '％', '倍'} and account != 'UNKNOWN':
                            value = str(parsed * unit_multiplier(cell_unit))
                        decimals = max(0, -parsed.as_tuple().exponent)
                    except InvalidOperation:
                        pass
                column, forecast = columns[column_index] if aligned else ('', False)
                parsed_ok = aligned and value is not None and account not in {'UNKNOWN', 'VALUATION'}
                semantic_review = parsed_ok and (account in UNDEFINED_ACCOUNTS or account.endswith('_UNSPECIFIED'))
                claims.append(AtomicClaim(
                    claim_id=f"table-{start}-{end}", text=row_text, start=start, end=end,
                    claim_type='calculation' if relation != 'value' else 'numeric', company=company,
                    account=account, period=column, candidate_value=value, unit=cell_unit,
                    caliber_hint=(scope, nr), relation=relation,
                    base_period=previous_period(column, relation) or None,
                    display_decimals=decimals, numeric_text=numeric,
                    source_numeric_start=(row_offsets[cell.start] + start-cell.start
                                          if row_offsets else start-line.start),
                    binding_status='single' if scope != '未知' else 'unresolved',
                    parse_status='parsed' if parsed_ok else 'unresolved',
                    modality='forecast' if forecast else 'opinion' if account == 'VALUATION' else 'fact',
                    table_context={'title': source_title, 'headers': list(header_spans),
                                   'title_source': title_source,
                                   'table_profile': 'broker_compact' if summary else 'statutory' if re.search('合并|母公司', source_title) else 'unspecified',
                                   'layout_lane': page.get('_geometry_lane'),
                                   **({'summary_source': summary} if summary else {}),
                                   **({'definition_id': 'metric:' + account.lower().replace('_', '-')} if account.startswith('BROKER_OTHER_') else {}),
                                   **({'definition_id': metric_definition_id} if metric_definition_id else {}),
                                   'row_label': row_label,
                                   'source_spans': [{'start': item.start, 'end': item.end}
                                                    for item in row_lines],
                                   'column_index': column_index + 1 if aligned else None,
                                   'period_source': 'source_header' if aligned else 'unresolved',
                                   'modality_source': 'explicit_forecast_header' if forecast else 'source_header' if aligned else 'unresolved',
                                   'account_hint': account, 'period_hint': column,
                                   'scope_hint': scope, 'unit_hint': cell_unit,
                                   'relation_hint': relation,
                                   **({'source_unit_proof':dict(unit_proof)} if unit_proof else {}),
                                   **({'numeric_parse_status':'parsed','semantic_review_required':True,
                                       'source_account_unresolved':account} if semantic_review else {}),
                                   **({'source_currency':table_currency} if table_currency else {}),
                                   **({'account_lineage': account_lineage} if account_lineage else {}),
                                   'layout_source': 'pdf_text_line_lanes' if page.get('_geometry_lane') else 'source_text_sequence'},
                ))
            if account not in _RATIO_ACCOUNTS and relation == 'value':
                previous_account = account if account != 'UNKNOWN' else ''
                previous_account_lines = list(label_lines) if previous_account else []
            index = cursor
    claims = _attach_structure_scopes(claims, text)
    claims = _attach_summary_structure_scope(claims)
    from .dimension_tables import extract_dimension_tables
    existing = {claim.start for claim in claims}
    claims.extend(claim for claim in extract_dimension_tables(text, pages, company) if claim.start not in existing)
    return sorted(claims, key=lambda claim: (claim.start, claim.end))


def _summary_source(text: str, page: dict) -> dict | None:
    """A compact broker profile needs its own bounded source heading."""
    geometry = page.get('lines', [])
    # Broker reports use two equivalent captions.  The first implementation
    # only admitted ``财务预测摘要`` and therefore left the common
    # ``财务摘要和估值指标`` table with an unknown scope, even though its
    # heading, period columns and source citation were all present on the
    # same page.  Keep the heading bounded and source backed; this does not
    # make a free standing metric a fact.
    headings = [line for line in geometry if re.fullmatch(
        r'(?:附[:：]?)?(?:财务预测摘要|财务摘要和估值指标)', _compact(line.get('text', '')))]
    citations = [line for line in geometry if re.match(r'^(?:数据|资料)来源[:：]', _compact(line.get('text', '')))]
    if len(headings) != 1 or not citations:
        return None
    heading = headings[0]
    citation = min((line for line in citations if line.get('bbox') and heading.get('bbox') and line['bbox'][1] > heading['bbox'][3]),
                   key=lambda line: line['bbox'][1], default=None)
    if citation is None:
        return None
    refs = []
    for line in (heading, citation):
        start, end = line.get('start'), line.get('end')
        if type(start) is not int or type(end) is not int or text[start:end] != line['text']:
            return None
        refs.append({'start': start, 'end': end, 'text': text[start:end], 'bbox': list(line['bbox'])})
    return {'schema_version': 'source-broker-summary.v1', 'heading': refs[0], 'citation': refs[1]}


def _attach_structure_scopes(claims: list[AtomicClaim], text: str) -> list[AtomicClaim]:
    """Parent and minority captions within one table prove consolidation.

    This never propagates to a sibling table, cash-flow table or nearby prose.
    Proofs contain original caption/header excerpts; no amount is consulted.
    """
    groups = {}
    for claim in claims:
        context = claim.table_context
        title = context.get('title_source')
        if not title or context.get('period_source') != 'source_header':
            continue
        groups.setdefault((title['start'], title['end'], context.get('layout_source')), []).append(claim)
    output = []
    proofs = {}
    for key, group in groups.items():
        title = group[0].table_context['title_source']
        if '母公司' in title['text'] or '现金流量表' in title['text']:
            continue
        labels = {}
        for claim in group:
            label = _compact(claim.table_context.get('row_label', ''))
            spans = claim.table_context.get('source_spans', [])
            for span in spans:
                excerpt = text[span['start']:span['end']]
                if _compact(excerpt) == label:
                    labels[label] = {**span, 'text': excerpt}
                    break
        pair = None
        for parent, minority, kind in (
            (r'归属(?:于)?母公司(?:所有者|股东)权益(?:合计)?', r'少数股东权益(?:合计)?', 'balance_sheet'),
            (r'归属(?:于)?母公司(?:所有者|股东)?(?:的)?净利润', r'少数股东损益', 'income_statement')):
            a = [ref for name, ref in labels.items() if re.fullmatch(parent, name)]
            b = [ref for name, ref in labels.items() if re.fullmatch(minority, name)]
            if len(a) == len(b) == 1:
                pair = (a[0], b[0], kind)
                break
        if not pair:
            continue
        end = max(claim.end for claim in group)
        proof = {'schema_version': 'source-table-structure.v1', 'scope': '合并',
                 'kind': pair[2], 'title': title, 'start': title['start'], 'end': end,
                 'parent_caption': pair[0], 'minority_caption': pair[1],
                 'header_spans': group[0].table_context.get('headers', []),
                 'layout_source': group[0].table_context.get('layout_source')}
        import json
        proof['sha256'] = hashlib.sha256(json.dumps(proof, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
        for claim in group:
            proofs[claim.claim_id] = proof
    for claim in claims:
        if claim.claim_id in proofs:
            context = dict(claim.table_context, scope_hint='合并', scope_proof=proofs[claim.claim_id])
            output.append(replace(claim, caliber_hint=('合并',) + claim.caliber_hint[1:],
                                  binding_status='single' if claim.parse_status == 'parsed' else claim.binding_status,
                                  table_context=context))
        else:
            output.append(claim)
    return output


def _attach_summary_structure_scope(claims):
    """Consolidation within one bounded, four-part financial summary entity.

    A heading, ending citation, both geometric lanes, all matching column
    schemas and two independent consolidation structures are required. This
    cannot authorize another table on the same page or plain nearby prose.
    """
    import json
    summaries={}
    for claim in claims:
        context=claim.table_context
        summary=context.get('summary_source')
        if summary and context.get('layout_source')=='pdf_text_line_lanes':
            key=json.dumps(summary,ensure_ascii=False,sort_keys=True)
            summaries.setdefault(key,[]).append(claim)
    authorized={}
    expected={'balance_sheet':'资产负债表','income_statement':'利润表',
              'cash_flow_statement':'现金流量表','financial_ratios':'主要财务比率'}
    for summary_key, members in summaries.items():
        groups={}
        for claim in members:
            title=claim.table_context.get('title_source')
            if title:
                groups.setdefault(title['start'],[]).append(claim)
        tables={}
        for group in groups.values():
            context=group[0].table_context
            title=context['title_source']
            name=_compact(title['text'])
            kinds=[kind for kind,label in expected.items() if re.fullmatch(re.escape(label)+r'(?:[（(](?:百万元|亿元|万元|元)[）)])?',name)]
            if len(kinds)!=1 or kinds[0] in tables or not title.get('bbox'):
                tables={};break
            headers=context.get('headers',[])
            signature=[]
            for header in headers:
                parsed=_header(header['text'])
                if parsed is None:
                    signature=[];break
                signature.append(list(parsed))
            if len(signature)<2 or any(c.table_context.get('headers')!=headers for c in group):
                tables={};break
            structure=next((c.table_context.get('scope_proof') for c in group if c.table_context.get('scope_proof')),None)
            tables[kinds[0]]={'kind':kinds[0],'title':title,'start':min(c.start for c in group),
                             'end':max(c.end for c in group),'lane':context.get('layout_lane'),
                             'headers':headers,'period_signature':signature,'structure':structure}
        if set(tables)!=set(expected):
            continue
        bs,ins,cf,ratios=(tables[name] for name in expected)
        if (not bs['structure'] or not ins['structure'] or
                bs['structure'].get('kind')!='balance_sheet' or ins['structure'].get('kind')!='income_statement'
                or {table['lane'] for table in tables.values()}!={1,2}
                or bs['lane']!=cf['lane'] or ins['lane']!=ratios['lane'] or bs['lane']==ins['lane']
                or any(table['period_signature']!=bs['period_signature'] for table in tables.values())
                or abs(bs['title']['bbox'][1]-ins['title']['bbox'][1])>4
                or bs['title']['bbox'][3]>=cf['title']['bbox'][1]
                or ins['title']['bbox'][3]>=ratios['title']['bbox'][1]):
            continue
        summary=json.loads(summary_key)
        heading,citation=summary['heading'],summary['citation']
        if any(not heading['end']<=table['title']['start']<table['start']<=table['end']<=citation['start']
               or not heading['bbox'][3]<table['title']['bbox'][1]<citation['bbox'][1] for table in tables.values()):
            continue
        from .scope import _source_scopes
        if any('母公司' in _source_scopes(str(c.table_context.get('title',''))+'；'+c.text)[0] for c in members):
            continue
        proof={'schema_version':'source-financial-summary-structure.v1','scope':'合并',
               'kind':'bounded_combined_financial_summary','summary':summary,
               'tables':list(tables.values()),'period_signature':bs['period_signature'],
               'professional_signoff_status':'pending','source_rule_id':'SOURCE-summary-consolidated-structure.v1'}
        proof['sha256']=hashlib.sha256(json.dumps(proof,ensure_ascii=False,sort_keys=True).encode('utf-8')).hexdigest()
        proof['entity_id']='financial-summary-'+proof['sha256'][:20]
        for claim in members:
            authorized[claim.claim_id]=proof
    output=[]
    for claim in claims:
        proof=authorized.get(claim.claim_id)
        if proof is None:
            output.append(claim);continue
        output.append(replace(claim,caliber_hint=('合并',)+claim.caliber_hint[1:],
                              binding_status='single' if claim.parse_status=='parsed' else claim.binding_status,
                              table_context={**claim.table_context,'scope_hint':'合并','scope_proof':proof}))
    return output
