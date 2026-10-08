"""Source-backed identities for bounded broker financial-summary methods.

Only captions, original geometry and stated market dates identify a method.
No amount, fact lookup, candidate match, model result or A/B choice is read.
These identities are distinct from statutory EPS and cash-flow-note formulas.
Numeric execution still requires a registered rule and independent facts.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date
import hashlib
import json
import re


SCHEMA = 'source-broker-method.v1'
_LATEST = {
    '每股收益(最新摊薄)': ('BROKER_EPS_LATEST', 'NP_PARENT', 'metric:broker-eps-latest'),
    '每股经营现金流(最新摊薄)': ('BROKER_CFO_PER_SHARE_LATEST', 'CFO', 'metric:broker-cfo-per-share-latest'),
    '每股净资产(最新摊薄)': ('BROKER_EQUITY_PER_SHARE_LATEST', 'EQUITY_PARENT', 'metric:broker-equity-per-share-latest'),
}
_CFO_COMPONENTS = {
    '财务费用': ('BROKER_CFO_FINANCE', 'metric:broker-cfo-finance'),
    '投资损失': ('CF_BRIDGE_INVESTMENT_LOSS', 'metric:cashflow-investment-loss'),
}
_CFO_ROWS = ('经营活动现金流', '净利润', '折旧摊销', '财务费用', '投资损失', '营运资金变动', '其他经营现金流')
_CF_TOTALS = ('经营活动现金流', '投资活动现金流', '筹资活动现金流', '现金净增加额')


def _compact(text):
    return re.sub(r'\s+', '', str(text)).replace('（', '(').replace('）', ')')


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def _ref_ok(ref, text=None):
    return (isinstance(ref, dict) and type(ref.get('start')) is int and type(ref.get('end')) is int
            and 0 <= ref['start'] < ref['end'] and isinstance(ref.get('text'), str)
            and ref['end']-ref['start'] == len(ref['text'])
            and (text is None or text[ref['start']:ref['end']] == ref['text']))


def _box_ok(ref):
    b = ref.get('bbox') if isinstance(ref, dict) else None
    return (isinstance(b, (list, tuple)) and len(b) == 4
            and all(type(v) in {int, float} for v in b) and b[0] < b[2] and b[1] < b[3])


def _ref(line, page, text):
    ref = {key: deepcopy(line[key]) for key in ('start', 'end', 'text', 'bbox') if key in line}
    ref['page'] = page['page']
    return ref if _ref_ok(ref, text) and _box_ok(ref) else None


def _page_for(claim, pages):
    found = [p for p in pages if p['start'] <= claim.start < claim.end <= p['end']]
    return found[0] if len(found) == 1 else None


def _label_ref(claim, page, text):
    source = claim.table_context
    caption = _compact(source.get('row_label'))
    spans = [span for span in source.get('source_spans', ())
             if type(span.get('start')) is int and type(span.get('end')) is int
             and _compact(text[span['start']:span['end']]) == caption]
    refs = [_ref(line, page, text) for span in spans for line in page.get('lines', ())
            if span['start'] == line.get('start') and span['end'] == line.get('end')]
    refs = [r for r in refs if r]
    return refs[0] if len(refs) == 1 else None


def _table_group(claim, claims):
    source = claim.table_context
    title = source.get('title_source')
    return [c for c in claims if c.table_context.get('title_source') == title
            and c.table_context.get('layout_lane') == source.get('layout_lane')
            and c.table_context.get('summary_source') == source.get('summary_source')]


def _label_rows(group, page, text):
    rows = {}
    for claim in group:
        ref = _label_ref(claim, page, text)
        if ref:
            rows.setdefault(_compact(ref['text']), {})[ref['start']] = ref
    return {label: next(iter(refs.values())) for label, refs in rows.items() if len(refs) == 1}


def _snapshot(company, text, pages, report_date):
    """Issuer's explicitly dated total-share field, never the floating shares.

    The numeric cell is referenced by a hash/location only. It remains a claim
    requiring independent official verification, not a new trusted fact.
    """
    if not report_date:
        return None
    try:
        cutoff = date.fromisoformat(report_date)
    except (ValueError, TypeError):
        return None
    snapshots = []
    for page in pages:
        lines = [(_ref(l, page, text)) for l in page.get('lines', ())]
        lines = [l for l in lines if l]
        # A stock-code heading identifies the selected issuer on this page.
        issuer = [l for l in lines if re.fullmatch(
            re.escape(company) + r'\([A-Z0-9.]+\)', _compact(l['text']))]
        if len(issuer) != 1:
            continue
        headings = [l for l in lines if re.fullmatch(r'总股本\((?:亿股|万股|股)\)', _compact(l['text']))]
        for caption in headings:
            dates = [l for l in lines if _compact(l['text']) == '日期'
                     and abs(l['bbox'][0]-caption['bbox'][0]) <= 18
                     and l['bbox'][3] < caption['bbox'][1]]
            if len(dates) != 1:
                continue
            date_caption = dates[0]
            date_values = [l for l in lines if re.fullmatch(r'20\d{2}/\d{1,2}/\d{1,2}', _compact(l['text']))
                           and abs(l['bbox'][1]-date_caption['bbox'][1]) <= 3
                           and l['bbox'][0] >= date_caption['bbox'][2]]
            values = [l for l in lines if re.fullmatch(r'\d+(?:\.\d+)?', _compact(l['text']))
                      and abs(l['bbox'][1]-caption['bbox'][1]) <= 3
                      and l['bbox'][0] >= caption['bbox'][2]]
            if len(date_values) != 1 or len(values) != 1:
                continue
            pieces = _compact(date_values[0]['text']).split('/')
            try:
                snapshot_date = date(*map(int, pieces))
            except ValueError:
                continue
            if snapshot_date > cutoff:
                continue
            value = values[0]
            snapshots.append({'date': snapshot_date.isoformat(), 'date_caption': date_caption,
                              'date_value': date_values[0], 'total_shares_caption': caption,
                              'issuer_caption': issuer[0],
                              'total_shares_cell': {key: value[key] for key in ('start', 'end', 'bbox', 'page')},
                              'total_shares_cell_sha256': hashlib.sha256(value['text'].encode('utf-8')).hexdigest(),
                              'unit': re.search(r'\((亿股|万股|股)\)', _compact(caption['text']))[1],
                              'fact_policy': 'independent_official_total_ordinary_shares_at_snapshot_date',
                              'use_candidate_total_shares_as_fact': False})
    return snapshots[0] if len(snapshots) == 1 else None


def _summary_ok(claim, text):
    source = claim.table_context
    summary = source.get('summary_source', {})
    heading, citation = summary.get('heading'), summary.get('citation')
    title = source.get('title_source')
    headers = source.get('headers')
    return (source.get('table_profile') == 'broker_compact'
            and source.get('layout_source') == 'pdf_text_line_lanes'
            and summary.get('schema_version') == 'source-broker-summary.v1'
            and _ref_ok(heading, text) and _box_ok(heading)
            and _ref_ok(citation, text) and _box_ok(citation)
            and _ref_ok(title, text) and _box_ok(title)
            and _compact(source.get('title')) == _compact(title['text'])
            and re.fullmatch(r'(?:附[:：]?)?(?:财务预测摘要|财务摘要和估值指标)', _compact(heading['text']))
            and re.match(r'^(?:数据|资料)来源[:：]', _compact(citation['text']))
            and heading['end'] <= title['start'] <= claim.start < claim.end <= citation['start']
            and isinstance(headers, list) and headers and all(_ref_ok(h, text) for h in headers)
            and source.get('period_source') == 'source_header')


def derive_source_method(claim, claims, text, pages, report_date=None):
    """Return a host-owned, amount-free method proof or ``None``."""
    if not _summary_ok(claim, text):
        return None
    caption = _compact(claim.table_context.get('row_label'))
    if caption not in _LATEST and caption not in _CFO_COMPONENTS:
        return None
    page = _page_for(claim, pages)
    if page is None:
        return None
    source = claim.table_context
    row = _label_ref(claim, page, text)
    if row is None:
        return None
    caption = _compact(row['text'])
    title = source['title_source']
    rows = _label_rows(_table_group(claim, claims), page, text)
    payload = {'schema_version': SCHEMA, 'method_authorization': 'proposal_only',
               'source_text_sha256': hashlib.sha256(claim.text.encode()).hexdigest(),
               'source_start': claim.start, 'source_end': claim.end,
               'source_numeric_start': claim.source_numeric_start,
               'source_original_account': source.get('source_account_unresolved') or claim.account,
               'source_row': row, 'source_title': deepcopy(title),
               'source_summary': deepcopy(source['summary_source']),
               'source_headers': deepcopy(source['headers']),
               'source_layout_lane': source['layout_lane'],
               'source_document_text_sha256': hashlib.sha256(text.encode()).hexdigest(),
               'professional_signoff_status': 'pending', 'uses_amount_matching': False,
               'source_numeric_value_is_fact': False}
    if caption in _LATEST and _compact(title['text']) == '主要财务比率':
        sections = [_ref(l, page, text) for l in page.get('lines', ())
                    if _compact(l.get('text')) == '每股指标(元)' and l.get('bbox')
                    and abs(l['bbox'][0]-title['bbox'][0]) < 20 and l['bbox'][3] < row['bbox'][1]]
        sections = [r for r in sections if r]
        if len(sections) != 1 or not set(_LATEST).issubset(rows):
            return None
        section = sections[0]
        # All three labelled latest-share rows must stay in this source
        # subsection. A later financial section resets its method authority.
        stops = [l for l in page.get('lines', ()) if l.get('bbox')
                 and abs(l['bbox'][0]-title['bbox'][0]) < 20
                 and _compact(l.get('text')) in {'成长能力','获利能力','偿债能力','营运能力','估值比率'}
                 and l['bbox'][1] > section['bbox'][3]]
        bottom = min((l['bbox'][1] for l in stops), default=float('inf'))
        if any(not section['bbox'][3] < rows[label]['bbox'][1] < bottom for label in _LATEST):
            return None
        anchor = _snapshot(claim.company, text, pages, report_date)
        if anchor is None:
            return None
        account, numerator, definition = _LATEST[caption]
        # Match the declared numerator identity in the same bounded summary;
        # its candidate amount never selects the row or source definition.
        numerator_candidates = [c for c in claims
            if c.account == numerator and c.table_context.get('summary_source') == source['summary_source']
            and c.table_context.get('relation_hint') == 'value'
            and (numerator != 'CFO' or '现金流量表' in c.table_context.get('title', ''))
            and (numerator != 'NP_PARENT' or '利润表' in c.table_context.get('title', ''))
            and (numerator != 'EQUITY_PARENT' or '资产负债表' in c.table_context.get('title', ''))]
        refs = {_label_ref(c, page, text)['start']: _label_ref(c, page, text)
                for c in numerator_candidates if _label_ref(c, page, text)}
        if len(refs) != 1:
            return None
        payload.update(account=account, definition_id=definition, method_kind='latest_total_share',
                       numerator_account=numerator, numerator_source=next(iter(refs.values())),
                       denominator_account='END_SHARES', denominator_anchor=anchor,
                       snapshot_date=anchor['date'],
                       source_section=section,
                       latest_share_rows=[rows[label] for label in _LATEST],
                       statutory_equivalence=False)
    elif caption in _CFO_COMPONENTS and re.fullmatch(r'现金流量表\((?:元|万元|百万元|亿元)\)', _compact(title['text'])):
        if not set(_CFO_ROWS + _CF_TOTALS).issubset(rows):
            return None
        ordered = [rows[label] for label in _CFO_ROWS]
        if not all(a['bbox'][1] < b['bbox'][1] for a, b in zip(ordered, ordered[1:])):
            return None
        if not ordered[-1]['bbox'][3] < rows['投资活动现金流']['bbox'][1]:
            return None
        if any(abs(ref['bbox'][0]-title['bbox'][0]) > 20 for ref in ordered):
            return None
        account, definition = _CFO_COMPONENTS[caption]
        payload.update(account=account, definition_id=definition,
                       method_kind='broker_indirect_cashflow_component',
                       indirect_cfo_rows=ordered, next_group=rows['投资活动现金流'],
                       source_group_total='CFO', statutory_equivalence=False,
                       requires_registered_component_rule=True)
    else:
        return None
    payload['sha256'] = _hash(payload)
    return payload


def validated_source_method(claim):
    """Recheck the archived proof without reading a fact or candidate value."""
    source = claim.table_context if isinstance(claim.table_context, dict) else {}
    proof = source.get('source_method_proposal')
    if not isinstance(proof, dict) or proof.get('schema_version') != SCHEMA:
        return None
    payload = {k: v for k, v in proof.items() if k != 'sha256'}
    if (proof.get('sha256') != _hash(payload)
            or proof.get('source_text_sha256') != hashlib.sha256(claim.text.encode()).hexdigest()
            or proof.get('source_start') != claim.start or proof.get('source_end') != claim.end
            or proof.get('source_numeric_start') != claim.source_numeric_start
            or proof.get('source_title') != source.get('title_source')
            or _compact(source.get('title')) != _compact(proof.get('source_title', {}).get('text'))
            or proof.get('source_summary') != source.get('summary_source')
            or proof.get('source_headers') != source.get('headers')
            or proof.get('source_layout_lane') != source.get('layout_lane')
            or source.get('layout_source') != 'pdf_text_line_lanes'
            or source.get('table_profile') != 'broker_compact'
            or not _ref_ok(proof.get('source_row')) or not _box_ok(proof.get('source_row'))
            or _compact(proof['source_row']['text']) != _compact(source.get('row_label'))
            or _compact(source.get('row_label')) not in _compact(claim.text)
            or proof.get('uses_amount_matching') is not False
            or proof.get('source_numeric_value_is_fact') is not False
            or proof.get('method_authorization') != 'proposal_only'
            or proof.get('statutory_equivalence') is not False):
        return None
    caption = _compact(proof['source_row']['text'])
    if proof.get('method_kind') == 'latest_total_share':
        spec = _LATEST.get(caption)
        anchor = proof.get('denominator_anchor', {})
        try:
            date.fromisoformat(anchor.get('date'))
        except (ValueError, TypeError):
            return None
        if (not spec or (proof.get('account'), proof.get('numerator_account'), proof.get('definition_id')) != spec
                or proof.get('denominator_account') != 'END_SHARES'
                or proof.get('snapshot_date') != anchor.get('date')
                or anchor.get('fact_policy') != 'independent_official_total_ordinary_shares_at_snapshot_date'
                or anchor.get('use_candidate_total_shares_as_fact') is not False
                or not _ref_ok(proof.get('source_section'))
                or _compact(proof['source_section']['text']) != '每股指标(元)'
                or not _ref_ok(proof.get('numerator_source'))
                or not all(_ref_ok(anchor.get(key)) for key in ('date_caption','date_value','total_shares_caption','issuer_caption'))
                or _compact(anchor['total_shares_caption']['text']) not in {'总股本(股)','总股本(万股)','总股本(亿股)'}
                or [_compact(r.get('text')) for r in proof.get('latest_share_rows', ())] != list(_LATEST)):
            return None
    elif proof.get('method_kind') == 'broker_indirect_cashflow_component':
        spec = _CFO_COMPONENTS.get(caption)
        if (not spec or (proof.get('account'), proof.get('definition_id')) != spec
                or [_compact(r.get('text')) for r in proof.get('indirect_cfo_rows', ())] != list(_CFO_ROWS)
                or not all(_ref_ok(r) and _box_ok(r) for r in proof['indirect_cfo_rows'])
                or not _ref_ok(proof.get('next_group'))
                or _compact(proof['next_group']['text']) != '投资活动现金流'
                or proof.get('requires_registered_component_rule') is not True):
            return None
    else:
        return None
    return deepcopy(proof)


def attach_source_methods(claims, text, pages, report_date=None):
    """Archive proposals before freezing; do not authorize/relabel a claim.

    The execution registry and snapshot share evidence are separate gates.
    Until those gates are integrated, original X3/X2 behavior is preserved.
    """
    output = []
    for claim in claims:
        if 'source_dimensions' in claim.table_context:
            output.append(claim)
            continue
        proof = derive_source_method(claim, claims, text, pages, report_date)
        if proof is None:
            output.append(claim)
            continue
        source = {**claim.table_context, 'source_method_proposal': proof}
        output.append(replace(claim, table_context=source))
    return output
