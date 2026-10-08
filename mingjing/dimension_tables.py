"""Source-only geometry for customer and business financial tables.

No requested period, amount matching or model output establishes a dimension.
Continuation requires adjacent pages and the same independently observed column
coordinates. Missing cells remain missing, never zero.
"""
from __future__ import annotations

import hashlib
import re
from decimal import Decimal

from .models import AtomicClaim
from .mentions import period_from_text, previous_period, unit_multiplier

_NUMBER = re.compile(r'(?P<number>[+\-－−]?\d[\d,，]*(?:\.\d+)?)\s*(?P<unit>%|％|万元|亿元|百万元|元)?\s*$')
_CAPTION = re.compile(r'^(?:表\s*\d+\s*[:：])')
_CITATION = re.compile(r'^(?:数据|资料)来源\s*[:：]')

# Registered row semantics of the external-resource disclosure. The source
# table reference must still be proven before any unknown scope is queryable.
PROCUREMENT_GRID_DEFINITION = 'source:procurement-revenue-grid'
_PROCUREMENT_ROWS = {
    '算力资源采购金额': ('PURCHASE_AMOUNT', 'purchase_mode', '外采算力资源'),
    '外采算力资源产生的超算云服务收入': ('REVENUE', 'purchase_mode', '外采算力资源'),
    '超算云服务收入': ('REVENUE', 'business', '超算云服务'),
}


def procurement_row_identity(label):
    return _PROCUREMENT_ROWS.get(_compact(label))


def _compact(value):
    return re.sub(r'\s+', '', value)


def _lines(text, page):
    output = []
    for line in page.get('lines', []):
        left, right, box = line.get('start'), line.get('end'), line.get('bbox')
        if (type(left) is not int or type(right) is not int or not box or len(box) != 4
                or left < page['start'] or right > page['end'] or text[left:right] != line.get('text')):
            continue
        if not line['text'].strip():
            continue
        output.append(line)
    return output


def _ref(line):
    return {field: line[field] for field in ('start', 'end', 'text', 'bbox')}


def _center(line):
    return (line['bbox'][0] + line['bbox'][2]) / 2


def _claim(text, company, cell, *, title, headers, labels, account, period, unit,
           dimension_kind, dimension_id, definition_id='', relation='value', additional=None):
    match = _NUMBER.fullmatch(cell['text'].strip())
    if match is None:
        return None
    left_pad = len(cell['text']) - len(cell['text'].lstrip())
    start = cell['start'] + left_pad + match.start('number')
    end = cell['start'] + left_pad + match.end()
    # Do not consume source trailing whitespace: occurrence starts are exact,
    # while VALUE_RE keeps its own original occurrence end in the ledger.
    while end > start and text[end-1].isspace():
        end -= 1
    numeric = text[start:end]
    raw = match['number'].replace(',', '').replace('，', '').replace('－', '-').replace('−', '-')
    number = Decimal(raw)
    unit = match['unit'] or unit
    source = sorted({line['start']: line for line in labels + [cell]}.values(), key=lambda line: line['start'])
    fragments, position = [], 0
    for line in source:
        if line is cell:
            position = sum(len(fragment) + 1 for fragment in fragments) + start-line['start']
        fragments.append(text[line['start']:line['end']])
    body = '\n'.join(fragments)
    row_label = ''.join(line['text'].strip() for line in labels)
    evidence = {'schema_version': 'source-dimension-table.v1', 'dimension_kind': dimension_kind,
                'dimension_id': dimension_id, 'caption': _ref(title),
                'label_spans': [_ref(line) for line in labels], 'headers': [_ref(line) for line in headers],
                'cell': _ref(cell)}
    context = {'title': title['text'], 'title_source': _ref(title), 'headers': [_ref(line) for line in headers],
               'row_label': row_label, 'source_spans': [{'start': line['start'], 'end': line['end']} for line in source],
               'period_source': 'source_header', 'modality_source': 'source_header',
               'account_hint': account, 'period_hint': period, 'scope_hint': '未知', 'unit_hint': unit,
               'relation_hint': relation, 'layout_source': 'pdf_dimension_table_columns',
               'dimension_kind': dimension_kind, 'dimension_id': dimension_id, 'currency': 'CNY',
               'definition_id': definition_id, 'dimension_source': evidence,
               'source_unit_proof': {'kind': 'source_dimension_table_header', 'unit': unit,
                                     'header_spans': [_ref(line) for line in headers]},
               'evaluation_scope': 'issuer_historical_financial'}
    if additional:
        context.update(additional)
    return AtomicClaim(claim_id=f'table-{start}-{end}', text=body, start=start, end=end,
                       claim_type='numeric' if relation == 'value' else 'calculation', company=company,
                       account=account, period=period, candidate_value=str(number*unit_multiplier(unit)),
                       unit=unit, caliber_hint=('未知', '未扣非'), relation=relation,
                       base_period=previous_period(period, relation) or None,
                       display_decimals=max(0, -number.as_tuple().exponent), numeric_text=numeric,
                       source_numeric_start=position, parse_status='parsed', binding_status='unresolved',
                       table_context=context)


def _customer_page(text, page, lines, previous):
    headers = {}
    for line in lines:
        caption = _compact(line['text'])
        if caption in {'年度', '年份', '序号', '客户名称', '客户', '占营业收入比重', '占营业收入比例'} or re.fullmatch(r'(?:营业收入|销售收入)[（(](?:万元|亿元|百万元|元)[）)]', caption):
            headers[caption] = line
    amount_header = next((line for caption, line in headers.items() if re.match(r'(?:营业收入|销售收入)[（(]', caption)), None)
    name_header = headers.get('客户名称') or headers.get('客户')
    ordinal_header, period_header = headers.get('序号'), headers.get('年度') or headers.get('年份')
    caption = next((line for line in lines if _CAPTION.match(line['text'].strip())
                    and re.search(r'客户|销售', line['text'])), None)
    carried = False
    if caption and all((amount_header, name_header, ordinal_header, period_header)):
        top = max(line['bbox'][3] for line in (amount_header, name_header, ordinal_header, period_header))
        state = {'caption': caption, 'headers': list(headers.values()), 'amount': amount_header,
                 'name': name_header, 'ordinal': ordinal_header, 'period': period_header,
                 'page': page['page'], 'previous_bottom': max((line['bbox'][3] for line in lines
                                                             if line['bbox'][0] < amount_header['bbox'][2]), default=top)}
    elif (previous and page['page'] == previous['page'] + 1 and previous['previous_bottom'] > 700
          and not caption):
        state, carried, top = dict(previous), True, 0
        # New material, unrelated prose and a table with changed columns must
        # never inherit old headers simply because it starts on the next page.
        early = [line for line in lines if line['bbox'][1] < 100]
        if not any(re.fullmatch(r'1', line['text'].strip())
                   and abs(_center(line)-_center(state['ordinal'])) <= 5 for line in early):
            return [], None
    else:
        return [], None
    stop = min((line['bbox'][1] for line in lines if line['bbox'][1] > top
                and (_CITATION.match(line['text'].strip()) or _CAPTION.match(line['text'].strip()))), default=780)
    local = [line for line in lines if top < line['bbox'][1] < stop]
    ax, nx, ox, px = (_center(state[key]) for key in ('amount', 'name', 'ordinal', 'period'))
    if not px < ox < nx < ax:
        return [], None
    amount_cells = [line for line in local if _NUMBER.fullmatch(line['text'].strip())
                    and not re.search(r'[%％]', line['text']) and abs(_center(line)-ax) < 18]
    ordinals = [line for line in local if re.fullmatch(r'[1-9]\d?', line['text'].strip())
                and abs(_center(line)-ox) < 8]
    # Continuation must reproduce column geometry, not merely row text.
    if not amount_cells or not ordinals:
        return [], None
    periods = [line for line in local if re.fullmatch(r'20\d{2}\s*(?:年(?:度)?|H[12]|Q[1-4]|9M)', line['text'].strip(), re.I)
               and abs(_center(line)-px) < 12]
    starts = sorted((line['bbox'][1] for line in ordinals if line['text'].strip() == '1'))
    if not starts:
        return [], None
    output = []
    for group_index, group_top in enumerate(starts):
        group_end = starts[group_index+1] if group_index+1 < len(starts) else stop
        group_periods = [line for line in periods if group_top-3 <= line['bbox'][1] < group_end]
        if len(group_periods) != 1:
            continue
        period_source = group_periods[0]
        period = period_from_text(period_source['text'])
        group_amounts = sorted((line for line in amount_cells if group_top-3 <= line['bbox'][1] < group_end), key=lambda line: line['bbox'][1])
        for index, amount in enumerate(group_amounts):
            y = (amount['bbox'][1]+amount['bbox'][3])/2
            before = (y + (group_amounts[index-1]['bbox'][1]+group_amounts[index-1]['bbox'][3])/2)/2 if index else group_top-8
            after = (y + (group_amounts[index+1]['bbox'][1]+group_amounts[index+1]['bbox'][3])/2)/2 if index+1 < len(group_amounts) else group_end
            labels = sorted((line for line in local if ox+10 < _center(line) < ax-25
                             and before <= (line['bbox'][1]+line['bbox'][3])/2 < after
                             and not _NUMBER.fullmatch(line['text'].strip())), key=lambda line: line['bbox'][1])
            name = _compact(''.join(line['text'] for line in labels))
            if not name:
                continue
            dimension = '前五大客户' if name == '合计' and re.search('前五大', state['caption']['text']) else name
            unit = re.search(r'[（(](万元|亿元|百万元|元)[）)]', _compact(state['amount']['text']))[1]
            context = {'source_period_span': _ref(period_source),
                       'customer_identity_requires_source_mapping': bool(re.fullmatch(r'客户[A-Za-z0-9]+', dimension)),
                       'table_continuation': {'source_page': state['page'], 'current_page': page['page'],
                                              'kind': 'adjacent_aligned_customer_columns'} if carried else None}
            ordinal = [line for line in ordinals
                       if abs((line['bbox'][1]+line['bbox'][3])/2-y) <= 4]
            if len(ordinal) == 1:
                context.update(source_row_index=int(ordinal[0]['text'].strip()),
                               source_row_index_evidence=_ref(ordinal[0]))
            references = state['headers'] + [period_source]
            claim = _claim(text, '', amount, title=state['caption'], headers=references, labels=labels,
                           account='REVENUE', period=period, unit=unit,
                           dimension_kind='customer', dimension_id=dimension, additional=context)
            if claim:
                output.append(claim)
            shares = [line for line in local if re.fullmatch(r'[-+－−]?\d+(?:\.\d+)?[%％]', line['text'].strip())
                      and abs((line['bbox'][1]+line['bbox'][3])/2-y) <= 4 and _center(line) > ax+25]
            if len(shares) == 1:
                claim = _claim(text, '', shares[0], title=state['caption'], headers=references, labels=labels,
                               account='REVENUE_SHARE', period=period, unit='%', dimension_kind='customer',
                               dimension_id=dimension, definition_id='metric:revenue-share', additional=context)
                if claim:
                    output.append(claim)
    state.update(page=page['page'], previous_bottom=max((line['bbox'][3] for line in local), default=0))
    if any(_CITATION.match(line['text'].strip()) and line['bbox'][1] >= top for line in lines):
        state = None
    return output, state


def _business_tables(text, page, lines, company):
    output = []
    for caption in [line for line in lines if _CAPTION.match(line['text'].strip())]:
        stop = min((line['bbox'][1] for line in lines if line['bbox'][1] > caption['bbox'][3]
                    and (_CITATION.match(line['text'].strip()) or _CAPTION.match(line['text'].strip()))), default=0)
        if not stop:
            continue
        local = [line for line in lines if caption['bbox'][3] < line['bbox'][1] < stop]
        years = sorted((line for line in local if re.fullmatch(r'20\d{2}\s*年(?:度)?', line['text'].strip())), key=_center)
        amounts = sorted((line for line in local if re.fullmatch(r'金额[（(](?:万元|亿元|百万元|元)[）)]', _compact(line['text']))), key=_center)
        growths = sorted((line for line in local if _compact(line['text']) in {'同比变化', '同比增长率', '同比增长'}), key=_center)
        if len(years) < 2 or len(amounts) != len(years) or len(growths) != len(years):
            continue
        if any(not _center(amounts[index]) < _center(year) < _center(growths[index]) for index, year in enumerate(years)):
            continue
        top = max(line['bbox'][3] for line in amounts + growths)
        left = min(_center(line) for line in amounts)-25
        cells = [line for line in local if line['bbox'][1] > top and _NUMBER.fullmatch(line['text'].strip())]
        row_ys = sorted({round((line['bbox'][1]+line['bbox'][3])/2, 1) for line in cells if abs(_center(line)-_center(amounts[0])) < 20})
        for row_index, y in enumerate(row_ys):
            low = (y+row_ys[row_index-1])/2 if row_index else top
            high = (y+row_ys[row_index+1])/2 if row_index+1 < len(row_ys) else stop
            labels = sorted((line for line in local if line['bbox'][2] < left+15
                             and low <= (line['bbox'][1]+line['bbox'][3])/2 < high), key=lambda line: line['bbox'][1])
            name = _compact(''.join(line['text'] for line in labels))
            grid = procurement_row_identity(name)
            if grid:
                account, dimension_kind, dimension = grid
            elif name.endswith('收入'):
                account, dimension = 'REVENUE', name[:-2]
                dimension_kind = 'business'
            elif name.endswith('采购金额'):
                account, dimension = 'PURCHASE_AMOUNT', name[:-4]
                dimension_kind = 'business'
            else:
                continue
            for index, year in enumerate(years):
                period = period_from_text(year['text'])
                unit = re.search(r'[（(](万元|亿元|百万元|元)[）)]', _compact(amounts[index]['text']))[1]
                for header, relation in ((amounts[index], 'value'), (growths[index], 'yoy')):
                    candidates = [line for line in cells if low <= (line['bbox'][1]+line['bbox'][3])/2 < high
                                  and abs(_center(line)-_center(header)) < 20]
                    if len(candidates) != 1:
                        continue
                    claim = _claim(text, company, candidates[0], title=caption, headers=[year, header], labels=labels,
                                   account=account, period=period, unit=unit if relation == 'value' else '%',
                                   dimension_kind=dimension_kind, dimension_id=dimension, relation=relation,
                                   definition_id=PROCUREMENT_GRID_DEFINITION if grid else '')
                    if claim:
                        output.append(claim)
    return output


def extract_dimension_tables(text: str, pages: list[dict], company: str) -> list[AtomicClaim]:
    output, previous = [], None
    for page in pages:
        lines = _lines(text, page)
        customer, previous = _customer_page(text, page, lines, previous)
        # The corporate subject is host-selected; a customer cell is a
        # counterparty dimension, never an issuer substitution.
        for claim in customer:
            from dataclasses import replace
            output.append(replace(claim, company=company))
        output.extend(_business_tables(text, page, lines, company))
        from .source_customer_quotation import chart_claims
        output.extend(chart_claims(text, page, lines, company))
    return output
