"""Conservative source proofs for PDF chart ticks and period labels.

No model, fact store or requested period participates. A financial-looking
number is never excluded just because its PDF block contains only numbers.
The first version requires a bounded figure and mutually supporting axes.
"""
from __future__ import annotations

from decimal import Decimal
import math
import re

from .taxonomy import FINANCIAL_LABELS


_FIGURE = re.compile(r'^\s*图\s*\d+(?:[-－]\d+)?\s*[:：]')
_SOURCE = re.compile(r'^\s*(?:数据|资料)来源\s*[:：]')
_TABLE = re.compile(r'^\s*(?:表\s*\d+\s*[:：]|(?:合并|母公司)?(?:资产负债表|利润表|现金流量表)|财务预测摘要)')
_TICK = re.compile(r'\s*(?P<num>[+\-－−]?\d[\d,，]*(?:\.\d+)?)\s*(?P<unit>[%％])?\s*')
_YEAR = re.compile(r'(?<!\d)20\d{2}(?!\d)')
_FORECAST_YEAR_CELL = re.compile(r'(?P<year>20\d{2})(?:[AEY]|H[12]|Q[1-4])\s*',re.I)
_CATEGORY = re.compile(r'[A-Za-z\u4e00-\u9fff][A-Za-z\u4e00-\u9fff ()\uff08\uff09/&+\-\u00b7]{0,29}')


def _source_line(text, line):
    start, end, box = line.get('start'), line.get('end'), line.get('bbox')
    if (type(start) is not int or type(end) is not int or start < 0 or end > len(text)
            or start >= end or not isinstance(box, (list, tuple)) or len(box) != 4
            or text[start:end].strip() != str(line.get('text', '')).strip()):
        return None
    left = len(text[start:end]) - len(text[start:end].lstrip())
    try:
        box = [float(x) for x in box]
    except (TypeError, ValueError, OverflowError):
        return None
    if (not all(math.isfinite(x) for x in box) or box[0] >= box[2] or box[1] >= box[3]
            or not text[start:end].strip()):
        return None
    return {'start': start + left, 'end': end, 'text': text[start:end].strip(),
            'bbox': box}


def _reference(line):
    result = {key: line[key] for key in ('start', 'end', 'text', 'bbox')}
    if line.get('source_bbox'):
        result['bbox'] = line['source_bbox']
        result['estimated_subspan_bbox'] = line['bbox']
        result['subspan_geometry'] = 'estimated_from_equal_width_year_glyphs'
    return result


def _inside(line, region):
    box = line['bbox']
    return (region['left'] - 2 <= box[0] and box[2] <= region['right'] + 2
            and region['top'] <= box[1] and box[3] <= region['bottom'])


def _regions(lines):
    figures = [line for line in lines if _FIGURE.match(line['text'])]
    sources = [line for line in lines if _SOURCE.match(line['text'])]
    right_edge = max((line['bbox'][2] for line in lines), default=0)
    output = []
    for caption in figures:
        same_row = sorted([line for line in figures if abs(line['bbox'][1] - caption['bbox'][1]) <= 4],
                          key=lambda line: line['bbox'][0])
        index = same_row.index(caption)
        left = caption['bbox'][0] - 2
        right = same_row[index + 1]['bbox'][0] - 8 if index + 1 < len(same_row) else right_edge + 2
        if right - left < 80:
            continue
        boundaries = [line for line in sources if line['bbox'][1] > caption['bbox'][3]
                      and left <= line['bbox'][0] < right]
        if not boundaries:
            continue
        boundary = min(boundaries, key=lambda line: line['bbox'][1])
        # A caption cannot borrow a citation from a later figure or table.
        if any(line is not caption and caption['bbox'][3] < line['bbox'][1] < boundary['bbox'][1]
               and left <= line['bbox'][0] < right and (_FIGURE.match(line['text']) or _TABLE.match(line['text']))
               for line in lines):
            continue
        if boundary['bbox'][1] - caption['bbox'][3] > 350:
            continue
        # Text can stop just before a drawn axis endpoint. This allowance is
        # only consulted after an original connected perpendicular axis has
        # matched every extremal tick; it does not enlarge candidate scanning.
        # A neighboring caption or table remains an absolute boundary.
        right_limit = right if index + 1 < len(same_row) else right + min(12, (right-left)*.05)
        neighbours = [line['bbox'][0]-8 for line in lines if line is not caption
                      and (_FIGURE.match(line['text']) or _TABLE.match(line['text']))
                      and caption['bbox'][3] <= line['bbox'][3]
                      and line['bbox'][1] < boundary['bbox'][1]
                      and line['bbox'][0] > right-2]
        if neighbours:
            right_limit = min(right_limit, *neighbours)
        output.append({'caption': caption, 'boundary': boundary, 'left': left,
                       'right': right, 'top': caption['bbox'][3], 'bottom': boundary['bbox'][1],
                       'axis_right_limit': right_limit, 'axis_extent_source_lines': lines})
    return output


def _cells(lines):
    ticks, years = [], []
    for line in lines:
        raw, box = line['text'], line['bbox']
        period = _FORECAST_YEAR_CELL.fullmatch(raw)
        if period:
            years.append({**line,'number_start':line['start']+period.start('year'),
                          'number_end':line['start']+period.end('year'),'year':int(period['year'])})
            continue
        match = _TICK.fullmatch(raw)
        if match:
            token = match['num']
            # 2000 may be an amount tick in a 0, 2000, 4000 axis. Keep its
            # period possibility until geometry proves a mixed numeric axis;
            # an axis consisting only of year-like labels is never accepted.
            period_candidate = bool(not match['unit'] and re.fullmatch(r'20\d{2}', token))
            if period_candidate:
                years.append({**line, 'number_start': line['start'] + match.start('num'),
                              'number_end': line['start'] + match.end('num'), 'year': int(token)})
            ticks.append({**line, 'number_start': line['start'] + match.start('num'),
                          'number_end': line['start'] + match.end('num'),
                          'value': Decimal(token.replace(',', '').replace('，', '').replace('－', '-').replace('−', '-')),
                          'unit': '%' if match['unit'] else '', 'period_candidate': period_candidate})
            continue
        matches = list(_YEAR.finditer(raw))
        if len(matches) < 3 or _YEAR.sub('', raw).strip():
            continue
        # Original offsets remain exact. The year glyphs have the same width;
        # this approximate sub-box is used only to establish row geometry.
        for match in matches:
            width = box[2] - box[0]
            years.append({**line, 'number_start': line['start'] + match.start(),
                          'number_end': line['start'] + match.end(), 'year': int(match[0]),
                          'source_bbox': box,
                          'bbox': [box[0] + width * match.start() / len(raw), box[1],
                                   box[0] + width * match.end() / len(raw), box[3]]})
    return ticks, years


def _year_rows(years):
    rows = []
    for year in sorted(years, key=lambda item: (item['bbox'][1], item['bbox'][0])):
        row = next((row for row in rows if abs(row[0]['bbox'][1] - year['bbox'][1]) <= 2), None)
        if row is None:
            rows.append([year])
        else:
            row.append(year)
    accepted = []
    for row in rows:
        row.sort(key=lambda item: item['bbox'][0])
        if len(row) < 3 or row[-1]['bbox'][2] - row[0]['bbox'][0] < 40:
            continue
        years_values = [year['year'] for year in row]
        steps = [years_values[i] - years_values[i - 1] for i in range(1, len(row))]
        xs = [((row[i]['bbox'][0] + row[i]['bbox'][2]) -
               (row[i - 1]['bbox'][0] + row[i - 1]['bbox'][2])) / 2 for i in range(1, len(row))]
        if steps[0] > 0 and len(set(steps)) == 1 and min(xs) > 0 and max(xs) / min(xs) <= 1.35:
            accepted.append(row)
    return accepted


def _axis_groups(ticks):
    accepted = []
    for edge in (0, 2):  # Source figures may align tick labels left or right.
        groups = []
        for tick in sorted(ticks, key=lambda item: item['bbox'][edge]):
            group = next((group for group in groups if group[0]['unit'] == tick['unit']
                          and abs(group[0]['bbox'][edge] - tick['bbox'][edge]) <= 0.7), None)
            if group is None:
                groups.append([tick])
            else:
                group.append(tick)
        for group in groups:
            group.sort(key=lambda item: item['bbox'][1])
            segments, segment = [], []
            for tick in group:
                if segment and tick['bbox'][1] - segment[-1]['bbox'][1] > 45:
                    segments.append(segment)
                    segment = []
                segment.append(tick)
            if segment:
                segments.append(segment)
            for segment in segments:
                if len(segment) < 4 or all(tick.get('period_candidate') for tick in segment):
                    continue
                values = [segment[i]['value'] - segment[i - 1]['value'] for i in range(1, len(segment))]
                positions = [segment[i]['bbox'][1] - segment[i - 1]['bbox'][1] for i in range(1, len(segment))]
                if (values[0] != 0 and len(set(values)) == 1 and min(positions) >= 5
                        and max(positions) - min(positions) <= 1.5):
                    accepted.append((segment, edge, values[0], sum(positions) / len(positions)))
    return accepted


def _category_rows(lines):
    """Source-written labels in a tight, nonoverlapping horizontal row.

    No category is converted into a period, company or financial account.
    Dates, digits, prose, source citations and financial-table headings cannot
    furnish the categorical-axis proof. A series legend farther below a plot
    also fails the separate bottom-baseline geometry check.
    """
    rows = []
    for line in sorted(lines, key=lambda item: (item['bbox'][1], item['bbox'][0])):
        if not _CATEGORY.fullmatch(line['text']) or _TABLE.match(line['text']):
            continue
        if line['bbox'][3] - line['bbox'][1] > 18:
            continue
        row = next((row for row in rows if abs(row[0]['bbox'][1] - line['bbox'][1]) <= 2), None)
        if row is None:
            rows.append([line])
        else:
            row.append(line)
    accepted = []
    for row in rows:
        row.sort(key=lambda item: item['bbox'][0])
        if len(row) < 3 or row[-1]['bbox'][2] - row[0]['bbox'][0] < 40:
            continue
        if any(row[i - 1]['bbox'][2] >= row[i]['bbox'][0] for i in range(1, len(row))):
            continue
        centres = [(line['bbox'][0] + line['bbox'][2]) / 2 for line in row]
        steps = [centres[i] - centres[i - 1] for i in range(1, len(row))]
        if min(steps) > 0 and max(steps) / min(steps) <= 1.35:
            accepted.append(row)
    return accepted


def _compatible_baselines(axis, edge, year_rows, category_rows):
    lo, hi = axis[0]['bbox'][1], axis[-1]['bbox'][3]
    x = sum(tick['bbox'][edge] for tick in axis) / len(axis)
    compatible = []
    for row in year_rows:
        y = sum(year['bbox'][1] for year in row) / len(row)
        tolerance = max(18, max(year['bbox'][3] - year['bbox'][1] for year in row) * 2)
        if (min(abs(y - lo), abs(y - hi)) <= tolerance
                and (x <= row[0]['bbox'][0] + 3 or x >= row[-1]['bbox'][2] - 3)):
            compatible.append(('years', row, min(abs(y - lo), abs(y - hi))))
    for row in category_rows:
        # This is an actual adjacent baseline, not a global enlargement of
        # the year tolerance. A legend two text rows below the axis, or one
        # inside the data area, cannot establish category coordinates.
        y = min(label['bbox'][1] for label in row)
        height = max(label['bbox'][3] - label['bbox'][1] for label in row)
        if (hi - 2 <= y <= hi + max(8, height * 1.2)
                and (x <= row[0]['bbox'][0] + 3 or x >= row[-1]['bbox'][2] - 3)):
            compatible.append(('categories', row, abs(y - hi)))
    return compatible


def chart_layout_exclusions(text: str, pages: list[dict]) -> dict[int, dict]:
    """Map numeric source offsets to exact, reviewable exclusion proofs.

    Missing geometry/citation or ambiguous financial rows return no exclusions.
    Year headers at the top of an ordinary or forecast financial table cannot
    establish an axis. Figure values with irregular magnitude or interior
    coordinates remain candidates, even when their blocks contain only digits.
    """
    proofs = {}
    for page in pages:
        lines = [source for line in page.get('lines', []) if (source := _source_line(text, line))
                 and (page.get('start') is None or source['start'] >= page['start'])
                 and (page.get('end') is None or source['end'] <= page['end'])]
        for region in _regions(lines):
            local = [line for line in lines if _inside(line, region)]
            ticks, years = _cells(local)
            rows = _year_rows(years)
            categories = _category_rows(local)
            for axis, edge, value_step, coordinate_step in _axis_groups(ticks):
                lo, hi = axis[0]['bbox'][1], axis[-1]['bbox'][3]
                # In a labelled data table, numeric values are beside financial
                # rows. Chart legends below/above the plot do not meet this.
                if any(re.sub(r'\s+', '', line['text']) in FINANCIAL_LABELS
                       and lo - 2 <= line['bbox'][1] <= hi + 2 for line in local):
                    continue
                compatible = _compatible_baselines(axis, edge, rows, categories)
                vectors = _vector_axis_proof(page.get('vector_lines', []), axis, region)
                if not compatible and not vectors:
                    continue
                kind, row, distance = min(compatible, key=lambda item: item[2]) if compatible else ('source_vector_axes', [], 0)
                basis = {'schema_version': 'pdf-chart-layout.v1', 'page': page.get('page'),
                         'figure': _reference(region['caption']), 'source_boundary': _reference(region['boundary']),
                         'figure_region': [region['left'], region['top'], region['right'], region['bottom']],
                         'axis_alignment_edge': 'left' if edge == 0 else 'right',
                         'value_step': str(value_step), 'coordinate_step': coordinate_step,
                         'tick_source_spans': [[tick['number_start'], tick['number_end']] for tick in axis],
                         'period_source_spans': [[year['number_start'], year['number_end']] for year in row] if kind == 'years' else [],
                         'category_source_spans': [[label['start'], label['end']] for label in row] if kind == 'categories' else [],
                         'baseline_kind': kind, 'baseline_distance': distance,
                         'baseline_source_lines': [_reference(label) for label in row],
                         'numeric_axis_period_candidate_spans': [[tick['number_start'], tick['number_end']]
                                                                 for tick in axis if tick.get('period_candidate')]}
                if vectors:
                    basis['vector_axes'] = vectors
                    extension = vectors.get('figure_region_extension')
                    if extension:
                        basis['figure_region'] = extension['completed_region']
                        basis['figure_region_extension'] = extension
                items = [(tick, 'chart_axis_tick') for tick in axis]
                if kind == 'years':
                    items.extend((year, 'chart_period_label') for year in row)
                for item, reason in items:
                    previous = proofs.get(item['number_start'])
                    if previous and previous['basis'].get('vector_axes') and not vectors:
                        continue
                    proofs[item['number_start']] = {'reason': reason, 'basis': {**basis,
                        'occurrence_span': [item['number_start'], item['number_end']],
                        'source_line': _reference(item)}}
    return proofs


def chart_measure_scopes(text: str, pages: list[dict]) -> dict[int, dict]:
    """Classify actual chart data by an explicit caption, keeping all data."""
    from .mentions import VALUE_RE
    result = {}
    for page in pages:
        lines = [source for line in page.get('lines',[]) if (source := _source_line(text,line))]
        for region in _regions(lines):
            caption = re.sub(r'\s+','',region['caption']['text'])
            scope = None
            if re.search(r'单位[:：]?(?:万)?(?:CPU)?(?:核小时|核时|卡时|服务器|计算核心)|[（(](?:万个|万核小时|万核时|卡时)[）)]',caption):
                scope = 'issuer_operating'
            elif re.search(r'市场规模|市场渗透率|市场份额|(?:全球|中国|国内|我国).*(?:智能算力|算力规模|AI服务器|计算加速场景)',caption):
                scope = 'industry_historical'
            if not scope:
                continue
            for line in lines:
                if not _inside(line,region):
                    continue
                for match in VALUE_RE.finditer(line['text']):
                    start = line['start']+match.start('num')
                    result[start] = {'evaluation_scope':scope,
                                     'classification_basis':{'schema_version':'source-evaluation-scope.v1',
                                      'rule':'explicit_chart_measure_caption','caption':_reference(region['caption']),
                                      'source_boundary':_reference(region['boundary']),
                                      'figure_region':[region['left'],region['top'],region['right'],region['bottom']],
                                      'occurrence_span':[start,line['start']+match.end()], 'source_line':_reference(line)}}
    return result


def _vector_axis_proof(lines, ticks, region):
    """A true perpendicular PDF path can prove an unlabeled chart baseline.

    Both extremal tick centres must coincide with the vertical path ends. This
    excludes bar sides, arbitrary equal-spaced numbers, isolated table borders
    and a path borrowed from another figure. No vector path is synthesized.
    """
    if len(ticks) < 4:
        return None
    centres = sorted((tick['bbox'][1]+tick['bbox'][3])/2 for tick in ticks)
    lo, hi = centres[0], centres[-1]
    tick_left = min(tick['bbox'][0] for tick in ticks)
    tick_right = max(tick['bbox'][2] for tick in ticks)
    normalized = []
    for line in lines:
        if not isinstance(line, (list, tuple)) or len(line) != 4:
            continue
        if not all(type(value) in {int, float} for value in line):
            continue
        try:
            a, b, c, d = [float(value) for value in line]
        except (TypeError, ValueError, OverflowError):
            continue
        if not all(math.isfinite(value) for value in (a, b, c, d)):
            continue
        if not (region['left']-2 <= min(a,c) <= max(a,c) <= region.get('axis_right_limit', region['right'])+2
                and region['top'] <= min(b,d) <= max(b,d) <= region['bottom']):
            continue
        normalized.append([a,b,c,d])
    candidates = []
    for vertical in normalized:
        x0, y0, x1, y1 = vertical
        bottom, top = max(y0,y1), min(y0,y1)
        if (not region['left']-2 <= x0 <= region['right']+2
                or abs(x0-x1) > 0.1 or abs(top-lo) > 1.5 or abs(bottom-hi) > 1.5
                or not (0 <= x0-tick_right <= 18 or 0 <= tick_left-x0 <= 18)):
            continue
        for horizontal in normalized:
            a,b,c,d = horizontal
            if (abs(b-d) <= 0.1 and abs(b-bottom) <= 0.2 and abs(c-a) >= 80
                    and (abs(a-x0) <= 0.2 or abs(c-x0) <= 0.2)):
                proof = {'vertical': vertical, 'horizontal': horizontal,
                         'kind': 'source_pdf_perpendicular_axes', 'extremal_tick_centres': [lo,hi]}
                endpoint = max(a,c)
                if endpoint > region['right']+2:
                    allowance = min(12,(region['right']-region['left'])*.05)
                    if endpoint > min(region.get('axis_right_limit',region['right']),region['right']+allowance):
                        continue
                    # Reject any written content in the newly proved strip,
                    # even if it is not a recognized financial row. A path
                    # cannot carry the chart boundary into a neighboring table.
                    if any(source['bbox'][0] < endpoint and source['bbox'][2] > region['right']+2
                           and source['bbox'][1] < region['bottom'] and source['bbox'][3] > region['top']
                           for source in region.get('axis_extent_source_lines', [])):
                        continue
                    original = [region['left'],region['top'],region['right'],region['bottom']]
                    proof['figure_region_extension'] = {
                        'basis': 'original_connected_axis_endpoint_after_complete_tick_match',
                        'original_region': original,
                        'completed_region': [region['left'],region['top'],endpoint,region['bottom']],
                        'horizontal_extension': endpoint-region['right'],
                        'maximum_extension': min(12,(region['right']-region['left'])*.05),
                        'adjacent_caption_or_table_limit': region.get('axis_right_limit',region['right']),
                        'new_source_text_intersections': []}
                candidates.append(proof)
    return candidates[0] if candidates else None
