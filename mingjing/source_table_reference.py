"""Source-addressed verification of a quoted disclosure table.

An unlabelled customer table is not necessarily a consolidated statement.
Its quotations can still be checked against the explicitly cited, frozen
disclosure. Identity is resolved with citation, issuer, period and row labels;
neither draft amounts nor fact values participate in source selection.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date
import hashlib
import json
import re


TABLE_SCOPE = '来源表'
SCHEMA = 'source-table-reference.v1'


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def _date(value):
    try:
        return isinstance(value, str) and date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _ref_valid(ref, text=None):
    if (not isinstance(ref, dict) or type(ref.get('start')) is not int
            or type(ref.get('end')) is not int or not isinstance(ref.get('text'), str)
            or not 0 <= ref['start'] < ref['end'] or ref['end']-ref['start'] != len(ref['text'])):
        return False
    return text is None or text[ref['start']:ref['end']] == ref['text']


def _citation_kind(raw):
    if (not isinstance(raw, str)
            or not re.fullmatch(r'(?:资料|数据)来源\s*[:：].+', raw)
            or re.search(r'模拟|假设|示例|非官方|非公司|不引用|未引用|并非|不是|不来自', raw)):
        return ''
    if re.search(r'公司招股(?:说明)?书', raw):
        return 'prospectus'
    if re.search(r'公司第(?:二|2)轮问询(?:函)?回复', raw):
        return 'second_inquiry'
    if re.search(r'公司第(?:一|1)轮问询(?:函)?回复', raw):
        return 'first_inquiry'
    return ''


def _citation_report_matches(raw, report):
    # A filename cannot establish the document's actual internal variant.
    # The source-cover proof is available on newly parsed table facts;
    # explicit variants without such a proof stay unresolved.
    if any(q != report.get('source_document_variant') for q in re.findall(r'注册稿|申报稿', raw)):
        return False
    dates = [f'{int(y):04}-{int(m):02}-{int(d):02}' for y, m, d in
             re.findall(r'(20\d{2})[-年](\d{1,2})[-月](\d{1,2})日?', raw)]
    if dates and any(value != report['publish_date'] for value in dates):
        return False
    # A version qualifier without a resolvable dated source is not permission
    # to substitute the newest disclosure.
    if re.search(r'原版|旧版|调整前|更正前', raw) and not dates:
        return False
    return True


def _row_index(claim, text):
    context=claim.table_context
    ref=context.get('source_row_index_evidence')
    number=context.get('source_row_index')
    cell=context.get('dimension_source',{}).get('cell',{})
    if (type(number) is int and 1 <= number <= 5 and _ref_valid(ref,text)
            and ref['text'].strip()==str(number) and ref.get('bbox') and cell.get('bbox')
            and abs(sum(ref['bbox'][1::2])-sum(cell['bbox'][1::2])) <= 8):
        return number
    return 0


_CUSTOMER_NAME_MAPPINGS = {
    # Registered transcription mappings, scoped to the frozen registration
    # prospectus and its two independently parsed customer-sales tables.
    # No name similarity, suffix stripping or amount matching is permitted.
    '9f351a9852deaf4b6365dafb9c8195debee341277352bf91b69060c27c069ed1': {
        'company': '并行科技',
        'tables': {'source-table-cc560059afb2881d4eb4f99e',
                   'source-table-ea4e24129a107574342070fb'},
        'names': {
            ('中科院力学研究所', '中国科学院力学研究所'),
            ('三台县梓丰现代农业', '三台县梓丰现代农业发展有限公司'),
        },
    },
}


def _quoted_abbreviation(requested, available, *, company='', report_sha256='',
                         source_table_ids=(), row_index=0):
    """Only exact labels or explicitly registered, source-local mappings."""
    if requested==available:
        return True
    entry = _CUSTOMER_NAME_MAPPINGS.get(report_sha256, {})
    return bool(type(row_index) is int and 1 <= row_index <= 5
                and company == entry.get('company') and source_table_ids
                and set(source_table_ids) <= entry.get('tables', set())
                and (requested, available) in entry.get('names', set()))


def _allowed_reference(proof, context, claim):
    if proof.get('quotation_kind') in {'quarter_revenue_share_narrative', 'business_mode_quarter_share_caption'}:
        from .source_quarter_quotation import allowed_reference
        return allowed_reference(proof, context, claim)
    if proof.get('quotation_kind') in {'product_financial_narrative', 'product_financial_caption'}:
        from .source_product_financial import allowed_reference
        return allowed_reference(proof, context, claim)
    kind = _citation_kind((proof.get('citation') or {}).get('text'))
    if kind == 'prospectus':
        if proof.get('quotation_kind') in {'customer_concentration_narrative','customer_type_narrative','customer_type_chart'}:
            from .source_customer_quotation import identity
            observed=identity(claim)
            return bool(observed and proof.get('quotation_kind')==observed['quotation_kind']
                and proof.get('source_definition_id',context.get('definition_id'))==observed['definition_id']
                and proof.get('dimension_kind')==observed['dimension_kind']==context.get('dimension_kind')
                and proof.get('dimension_id')==observed['dimension_id']==context.get('dimension_id')
                and proof.get('account')==observed['account']==claim.account
                and proof.get('period')==observed['period']==claim.period
                and proof.get('relation')==claim.relation=='value')
        return (proof.get('dimension_kind') == context.get('dimension_kind') == 'customer'
                and proof.get('account') in {'REVENUE', 'REVENUE_SHARE'}
                and proof.get('relation') == claim.relation == 'value')
    if kind == 'second_inquiry':
        from .dimension_tables import procurement_row_identity, PROCUREMENT_GRID_DEFINITION
        row = procurement_row_identity(context.get('row_label', ''))
        return bool(row and row == (proof.get('account'), proof.get('dimension_kind'), proof.get('dimension_id'))
            and proof.get('dimension_kind') == context.get('dimension_kind')
            and proof.get('source_row_label') == context.get('row_label')
            and proof.get('source_definition_id') == context.get('definition_id') == PROCUREMENT_GRID_DEFINITION
            and proof.get('source_dimension_id') == proof.get('dimension_id')
            and proof.get('relation') == claim.relation == context.get('relation_hint')
            and claim.relation in {'value', 'yoy'})
    if kind == 'first_inquiry':
        from .source_business_mode import DEFINITION, narrative_identity
        identity=narrative_identity(claim)
        return bool(identity and proof.get('quotation_kind')=='business_mode_narrative'
            and proof.get('source_definition_id')==context.get('definition_id')==DEFINITION
            and proof.get('dimension_kind')==context.get('dimension_kind')=='business'
            and proof.get('source_dimension_id')==proof.get('dimension_id')==identity['dimension_id']
            and proof.get('account')==identity['account'] and proof.get('period')==identity['period']
            and proof.get('relation')==claim.relation=='value')
    return False


def _narrative_reference_bounds(proof, context, claim):
    if proof.get('quotation_kind')!='business_mode_narrative':
        return False
    from .source_business_mode import narrative_identity, narrative_caption
    identity=narrative_identity(claim)
    narrative=proof.get('narrative')
    return bool(identity and narrative_caption(proof['caption']['text'],claim.company,identity['business'])
        and _ref_valid(narrative) and narrative['text']==claim.text
        and narrative==context.get('dimension_source',{}).get('narrative')
        and narrative['start']<=claim.start<claim.end<=narrative['end']<=proof['caption']['start']
        and proof['caption']['end']<=proof['citation']['start'])


def _table_issuer_matches(context, company):
    from .source_semantics import _table_title_subject
    subject = _table_title_subject(context, company)
    return (subject is None or subject.get('company') == company
            and not subject.get('source_company_conflict') and not subject.get('source_company_ambiguous'))


def validated_table_reference(claim):
    """Validate the frozen host proof, without authorizing financial formulas."""
    context = claim.table_context
    proof = context.get('source_table_reference')
    if not isinstance(proof, dict):
        return None
    frozen=context.get('source_dimensions')
    if isinstance(frozen,dict) and frozen.get('source_table_reference') != proof:
        return None
    payload = {key: value for key, value in proof.items() if key != 'sha256'}
    if (proof.get('schema_version') != SCHEMA or _digest(payload) != proof.get('sha256')
            or proof.get('source_text_sha256') != hashlib.sha256(claim.text.encode('utf-8')).hexdigest()
            or proof.get('claim_start') != claim.start or proof.get('claim_end') != claim.end
            or proof.get('company') != claim.company or proof.get('account') != claim.account
            or proof.get('period') != claim.period
            or proof.get('source_doc') != claim.declared_source
            or claim.query_strategy not in {'pinned', 'same_report'}
            or not _table_issuer_matches(context, claim.company)
            or not _allowed_reference(proof, context, claim)
            or proof.get('dimension_id') != context.get('dimension_id')
            or not isinstance(proof.get('source_dimension_id'),str) or not proof['source_dimension_id']
            or proof.get('period') != context.get('period_hint')
            or proof.get('account') != context.get('account_hint')
            or not re.fullmatch(r'[a-f0-9]{64}', str(proof.get('report_sha256', '')))
            or proof.get('source_doc') != proof['report_sha256'][:12]
            or not re.fullmatch(r'source-table-[a-f0-9]{24}', str(proof.get('source_table_id', '')))
            or not isinstance(proof.get('source_table_ids'), list) or not proof['source_table_ids']
            or proof['source_table_id'] not in proof['source_table_ids']
            or any(not re.fullmatch(r'source-table-[a-f0-9]{24}', str(identifier))
                   for identifier in proof['source_table_ids'])
            or not _date(proof.get('publish_date')) or not _date(proof.get('cutoff'))
            or proof['publish_date'] > proof['cutoff']
            or not _ref_valid(proof.get('citation')) or not _ref_valid(proof.get('caption'))
            or proof['caption'] != context.get('title_source')
            or not ((proof['caption']['end'] <= claim.start < claim.end <= proof['citation']['start'])
                    or _narrative_reference_bounds(proof, context, claim)
                    or _customer_quotation_bounds(proof, context, claim)
                    or _product_quotation_bounds(proof, context, claim)
                    or _quarter_quotation_bounds(proof, context, claim)
                    or (proof.get('caption_summary') is True
                        and context.get('caption_summary') is True
                        and proof['caption']['start'] <= claim.start < claim.end <= proof['caption']['end']
                        and proof['dimension_id']=='前五大客户' and proof['account']=='REVENUE_SHARE'))
            or not isinstance(proof.get('source_table_evidence'), list)
            or not proof['source_table_evidence']):
        return None
    if proof['source_dimension_id'] != proof['dimension_id']:
        row=context.get('source_row_index_evidence')
        if (not _ref_valid(row) or row != proof.get('row_index_evidence')
                or not _quoted_abbreviation(proof['dimension_id'],proof['source_dimension_id'],
                    company=proof['company'], report_sha256=proof['report_sha256'],
                    source_table_ids=proof['source_table_ids'], row_index=proof.get('source_row_index'))
                or proof.get('source_row_index') != context.get('source_row_index')
                or row['text'].strip()!=str(proof.get('source_row_index'))):
            return None
    return dict(proof)


def _customer_quotation_bounds(proof,context,claim):
    from .source_customer_quotation import reference_bounds
    return reference_bounds(proof,context,claim)


def _product_quotation_bounds(proof, context, claim):
    from .source_product_financial import validate_reference_bounds
    return validate_reference_bounds(proof, context, claim)


def _quarter_quotation_bounds(proof, context, claim):
    from .source_quarter_quotation import reference_bounds
    return reference_bounds(proof, context, claim)


def query_quoted_table(store, claim, cutoff, min_confidence='0.95'):
    """Query exactly the admitted table; missing evidence never falls back."""
    from .models import FactQueryOutcome
    from .facts import FactStore
    from .dimensions import claim_dimensions
    proof = validated_table_reference(claim)
    if not proof or cutoff != proof['cutoff'] or claim.declared_source != proof['source_doc']:
        return FactQueryOutcome('source_required', reason='引用表身份或截止日与冻结来源不一致')
    rows = [fact for fact in store.facts if fact.company == proof['company']
            and fact.source_doc == proof['source_doc']
            and fact.source_table_id in proof['source_table_ids']
            and fact.period == claim.period and fact.account == claim.account
            and fact.source_relation == proof['relation']
            and (not proof.get('source_row_index') or fact.source_row_index == proof['source_row_index'])
            and fact.publish_date == proof['publish_date']]
    from .dimensions import claim_dimensions
    dimensions = claim_dimensions(claim)
    dimensions['dimension_id'] = proof['source_dimension_id']
    rows = [fact for fact in rows if fact.dimension_kind == dimensions['dimension_kind']
            and fact.dimension_id == dimensions['dimension_id']
            and fact.definition_id == dimensions['definition_id']
            and fact.currency == dimensions['currency']]
    scopes = {fact.scope for fact in rows}
    if not rows:
        return FactQueryOutcome('missing', reason='所引冻结表缺少此期间、指标的披露单元格')
    if any(not any(fact.source_table_id == identifier for fact in rows)
           for identifier in proof['source_table_ids']):
        return FactQueryOutcome('missing', reason='独立重复披露表缺少对应单元格，不能仅凭其余表认定转述一致',
                                evidence=tuple(e for fact in rows for e in fact.evidence))
    if len(scopes) != 1:
        return FactQueryOutcome('conflict', reason='同一来源表存在互相冲突的报表范围标识',
                                evidence=tuple(e for fact in rows for e in fact.evidence))
    if len({fact.version for fact in rows}) > 1:
        return FactQueryOutcome('conflict', reason='所引表存在多版本披露列，原文未确定版本列；不能按版本优先级丢弃冲突',
                                evidence=tuple(e for fact in rows for e in fact.evidence))
    return FactStore(rows).query_outcome(claim.company, claim.account, claim.period,
        next(iter(scopes)), '未扣非', cutoff=cutoff, strategy='pinned',
        source_doc=proof['source_doc'], min_confidence=min_confidence,
        source_relation=proof['relation'], **dimensions)


def _declared_report_matches(reference, report):
    """Honor an existing source pin; never silently replace it by latest."""
    reference = str(reference or '').removeprefix('evidence:')
    if not reference:
        return False
    sha = report['sha256']
    return (reference in {sha, sha[:12], report.get('report_id'),
                         report.get('path'), report.get('name')}
            or reference.startswith(sha[:12] + ':p'))


def _map_customer_label(label, row_index, selected, report, company):
    rosters = [item[3] for item in selected]
    rows = [row for item in selected for row in item[4]]
    if all(label in roster for roster in rosters):
        if row_index and any({row.dimension_id for row in item[4]
                              if row.source_row_index == row_index} != {label}
                             for item in selected):
            return ''
        return label
    if not row_index:
        return ''
    source_labels = {row.dimension_id for row in rows if row.source_row_index == row_index}
    if len(source_labels) != 1 or any(not any(row.source_row_index == row_index
                                            for row in item[4]) for item in selected):
        return ''
    available = next(iter(source_labels))
    return available if _quoted_abbreviation(label, available, company=company,
        report_sha256=report['sha256'], source_table_ids=[item[2] for item in selected],
        row_index=row_index) else ''


def link_quoted_customer_tables(claims, text, facts, reports, cutoff):
    """Resolve explicit prospectus citations against a frozen document registry.

    At least two distinct exact, named customers establish the multi-period
    table family. Anonymous labels additionally require the complete period
    require the complete source cohort, so merely finding customer H in one
    document cannot establish identity. Different tables never merge. The
    latest eligible disclosure is selected by date, never closeness of values.
    """
    from .dimensions import normalize_dimension_label, anonymous_customer_label
    if not _date(cutoff):
        return list(claims), []
    groups = {}
    for claim in claims:
        context = claim.table_context
        caption = context.get('title_source')
        if (context.get('layout_source') == 'pdf_dimension_table_columns'
                and context.get('dimension_kind') == 'customer'
                and context.get('account_hint') in {'REVENUE', 'REVENUE_SHARE'}
                and _table_issuer_matches(context, claim.company)
                and claim.relation == 'value' and _ref_valid(caption, text)):
            groups.setdefault(caption['start'], []).append(claim)
    # A financial figure in this table's own caption can inherit this table's
    # explicit percentage denominator. The header and raw caption together
    # establish meaning; a neighbouring table or a bare percentage cannot.
    from .dimension_tables import _claim
    from .mentions import period_from_text
    caption_claims = {}
    for group in list(groups.values()):
        caption=group[0].table_context['title_source']
        headers=group[0].table_context['dimension_source']['headers']
        if not any(re.fullmatch(r'占营业收入(?:比例|比重)', re.sub(r'\s+','',header['text'])) for header in headers):
            continue
        period=period_from_text(caption['text'])
        label_match=re.search(r'前五大客户',caption['text'])
        if not period or not label_match:
            continue
        for candidate in claims:
            if (not caption['start'] <= candidate.start < candidate.end <= caption['end']
                    or candidate.unit not in {'%','％'}
                    or not re.search(r'前五大客户[^%％\n]{0,30}占比',candidate.text)):
                continue
            label={'start':caption['start']+label_match.start(),'end':caption['start']+label_match.end(),
                   'text':label_match[0],'bbox':caption['bbox']}
            cell={'start':candidate.start,'end':candidate.end,'text':text[candidate.start:candidate.end],
                  'bbox':caption['bbox']}
            parsed=_claim(text,candidate.company,cell,title=caption,headers=headers,labels=[label],
                account='REVENUE_SHARE',period=period,unit='%',dimension_kind='customer',
                dimension_id='前五大客户',definition_id='metric:revenue-share',additional={'caption_summary':True})
            if parsed and parsed.start==candidate.start and parsed.end==candidate.end:
                parsed=replace(parsed,claim_id=candidate.claim_id)
                caption_claims[candidate.claim_id]=parsed
                group.append(parsed)
    claims=[caption_claims.get(claim.claim_id,claim) for claim in claims]
    report_registry = {}
    for report in reports:
        if (not re.search(r'招股(?:说明)?书', report.get('name', ''))
                or not _date(report.get('publish_date')) or report['publish_date'] > cutoff
                or not re.fullmatch(r'[a-f0-9]{64}', report.get('sha256', ''))):
            continue
        report_registry[report['sha256'][:12]] = report
    replacements, diagnostics = {}, []
    for group in groups.values():
        caption = group[0].table_context['title_source']
        # Stop at the first new caption or citation. A different table's
        # source note must not lend its provenance to this table.
        boundaries = list(re.finditer(r'(?m)^\s*(?:表\s*\d+\s*[:：]|(?:资料|数据)来源\s*[:：]).*$', text[caption['end']:]))
        if not boundaries:
            continue
        boundary = boundaries[0]
        raw = boundary.group(0).strip()
        left = caption['end'] + boundary.start() + len(boundary.group(0))-len(boundary.group(0).lstrip())
        citation = {'start': left, 'end': left+len(raw), 'text': raw}
        if (_citation_kind(raw) != 'prospectus'
                or max(claim.end for claim in group) > left):
            continue
        periods = {claim.period for claim in group}
        all_named={(claim.period,normalize_dimension_label(claim.table_context['dimension_id'])) for claim in group
                   if claim.table_context['dimension_id']!='前五大客户'
                   and not anonymous_customer_label(claim.table_context['dimension_id'])}
        chosen = {}
        for period in periods:
            local = [claim for claim in group if claim.period == period]
            pins = {claim.declared_source for claim in local if claim.declared_source}
            if len(pins) > 1 or any(claim.query_strategy in {'pinned', 'same_report'}
                                   and not claim.declared_source for claim in local):
                diagnostics.append({'kind': 'unresolved_source_pin', 'caption_start': caption['start'], 'period': period})
                continue
            candidates = {}
            for fact in facts:
                report = report_registry.get(fact.source_doc)
                if (not report or fact.company != local[0].company or fact.period != period
                        or fact.dimension_kind != 'customer' or fact.account != 'REVENUE'
                        or fact.source_relation != 'value'
                        or not fact.source_table_id or not fact.source_table_evidence
                        or fact.publish_date != report['publish_date']
                        or fact.company not in (report.get('companies') or [report.get('company')])):
                    continue
                if pins and not _declared_report_matches(next(iter(pins)), report):
                    continue
                # A qualified original citation is a source constraint too.
                if not _citation_report_matches(raw, report):
                    continue
                candidates.setdefault((fact.source_doc, fact.source_table_id), []).append(fact)
            eligible = []
            for (document, table), rows in candidates.items():
                roster = {normalize_dimension_label(fact.dimension_id) for fact in rows}
                all_rows=[fact for fact in facts if fact.source_doc==document
                          and fact.source_table_id==table and fact.account=='REVENUE'
                          and fact.source_relation=='value'
                          and fact.dimension_kind=='customer' and fact.currency=='CNY'
                          and fact.publish_date==report_registry[document]['publish_date']
                          and fact.company==local[0].company]
                common=all_named & {(row.period,normalize_dimension_label(row.dimension_id)) for row in all_rows}
                if len({name for _, name in common}) >= 2:
                    eligible.append((report_registry[document]['publish_date'], document, table, roster, rows))
            if not eligible:
                continue
            latest = max(item[0] for item in eligible)
            eligible = [item for item in eligible if item[0] == latest]
            if len({item[1] for item in eligible}) != 1:
                diagnostics.append({'kind': 'ambiguous_quoted_table', 'caption_start': caption['start'], 'period': period})
                continue
            # Repeated disclosure tables within the same cited document stay
            # independent. Query all of them and let conflicts block X4; never
            # choose whichever happens to resemble the draft amount.
            chosen[period] = eligible
        for claim in group:
            # Table-family identity and column-method identity are distinct.
            # A sales-share column lacking an explicit revenue denominator
            # must not be admitted as a duplicate REVENUE_SHARE source just
            # because its amount rows belong to the same customer family.
            selected = [item for item in chosen.get(claim.period, [])
                if any(fact.source_doc == item[1] and fact.source_table_id == item[2]
                       and fact.period == claim.period and fact.company == claim.company
                       and fact.account == claim.account and fact.source_relation == 'value'
                       and fact.definition_id == claim.table_context.get('definition_id', '')
                       for fact in facts)]
            if not selected:
                continue
            published, document = selected[0][:2]
            table_ids = sorted(item[2] for item in selected)
            rows = [row for item in selected for row in item[4]]
            rosters = [item[3] for item in selected]
            label = normalize_dimension_label(claim.table_context['dimension_id'])
            row_index=_row_index(claim,text)
            report = report_registry[document]
            mapped = _map_customer_label(label, row_index, selected, report, claim.company)
            if not mapped:
                continue
            local_names = {normalize_dimension_label(c.table_context['dimension_id'])
                           for c in group if c.period == claim.period}
            if anonymous_customer_label(label):
                # Printed row numbers supplement the complete roster; they
                # must not bypass it for an anonymous source-local identity.
                mapped_roster = {_map_customer_label(normalize_dimension_label(c.table_context['dimension_id']),
                    _row_index(c, text), selected, report, c.company) for c in group
                    if c.period == claim.period and c.table_context['dimension_id'] != '前五大客户'}
                if ('' in mapped_roster or any(mapped_roster != roster-{'前五大客户'} for roster in rosters)):
                    continue
            proof = {'schema_version': SCHEMA, 'company': claim.company,
                'claim_start': claim.start, 'claim_end': claim.end,
                'source_text_sha256': hashlib.sha256(claim.text.encode('utf-8')).hexdigest(),
                'caption': caption, 'citation': citation, 'period': claim.period,
                'account': claim.account, 'relation': 'value', 'dimension_kind': 'customer',
                'dimension_id': claim.table_context['dimension_id'], 'source_doc': document,
                'source_dimension_id':mapped, 'source_row_index':row_index,
                'row_index_evidence':claim.table_context.get('source_row_index_evidence'),
                'dimension_mapping_basis': 'exact_source_label' if mapped==label else 'registered_report_table_local_name_mapping_with_original_ordinal',
                'report_sha256': report['sha256'], 'report_id': report['report_id'],
                'publish_date': published, 'cutoff': cutoff, 'source_table_id': table_ids[0],
                'source_table_ids': table_ids,
                'source_table_evidence': sorted({e for row in rows for e in row.source_table_evidence}),
                'source_selection': 'explicit_prospectus_citation_latest_as_of_exact_customer_cohort',
                'accounting_scope': '未从引用推断合并或母公司'}
            if claim.table_context.get('caption_summary'):
                proof['caption_summary']=True
            proof['sha256'] = _digest(proof)
            context = {key: value for key, value in claim.table_context.items() if key != 'source_dimensions'}
            context['source_table_reference'] = proof
            linked = replace(claim, table_context=context, declared_source=document,
                             query_strategy='pinned', caliber_hint=(TABLE_SCOPE, '未扣非'))
            if validated_table_reference(linked):
                replacements[claim.claim_id] = linked
    return [replacements.get(claim.claim_id, claim) for claim in claims], diagnostics


def _link_quoted_operating_tables(claims, text, facts, reports, cutoff):
    """Check source-defined procurement/revenue cells, including reported YOY.

    This path compares an explicitly cited disclosure column. It does not
    invent a financial-statement scope, or assert independent YOY calculation.
    """
    from .dimension_tables import procurement_row_identity, PROCUREMENT_GRID_DEFINITION
    if not _date(cutoff):
        return list(claims), []
    registry = {report['sha256'][:12]: report for report in reports
        if re.fullmatch(r'[a-f0-9]{64}', str(report.get('sha256', '')))
        and _date(report.get('publish_date')) and report['publish_date'] <= cutoff
        and re.search(r'第(?:二|2)轮.*问询.*回复', report.get('name', ''))}
    groups = {}
    for claim in claims:
        context = claim.table_context
        title = context.get('title_source')
        row = procurement_row_identity(context.get('row_label', ''))
        if (context.get('layout_source') == 'pdf_dimension_table_columns'
                and row and context.get('definition_id') == PROCUREMENT_GRID_DEFINITION
                and _table_issuer_matches(context, claim.company)
                and _ref_valid(title, text) and claim.relation in {'value', 'yoy'}):
            groups.setdefault(title['start'], []).append(claim)
    replacements, diagnostics = {}, []
    for group in groups.values():
        caption = group[0].table_context['title_source']
        boundaries = list(re.finditer(r'(?m)^\s*(?:表\s*\d+\s*[:：]|(?:资料|数据)来源\s*[:：]).*$', text[caption['end']:]))
        if not boundaries:
            continue
        boundary = boundaries[0]
        raw = boundary.group(0).strip()
        left = caption['end']+boundary.start()+len(boundary.group(0))-len(boundary.group(0).lstrip())
        citation = {'start':left, 'end':left+len(raw), 'text':raw}
        if _citation_kind(raw) != 'second_inquiry' or max(c.end for c in group) > left:
            continue
        labels = {c.table_context['row_label'] for c in group}
        if (len(labels) < 2 or not any('外采' in label or '采购' in label for label in labels)):
            continue
        pins = {c.declared_source for c in group if c.declared_source}
        if len(pins) > 1 or any(c.query_strategy in {'pinned', 'same_report'} and not c.declared_source for c in group):
            continue
        candidates = {}
        for fact in facts:
            report = registry.get(fact.source_doc)
            if (not report or fact.company != group[0].company or not fact.source_table_id
                    or not fact.source_table_evidence or fact.publish_date != report['publish_date']
                    or fact.definition_id != PROCUREMENT_GRID_DEFINITION
                    or fact.source_type != 'financial_source_table'
                    or fact.company not in (report.get('companies') or [report.get('company')])
                    or not _citation_report_matches(raw, report)
                    or pins and not _declared_report_matches(next(iter(pins)), report)):
                continue
            candidates.setdefault((fact.source_doc, fact.source_table_id), []).append(fact)
        eligible = [(registry[doc]['publish_date'],doc,table,rows) for (doc,table),rows in candidates.items()
                    if labels <= {fact.label for fact in rows}]
        if not eligible:
            continue
        latest = max(item[0] for item in eligible)
        eligible = [item for item in eligible if item[0] == latest]
        if len({item[1] for item in eligible}) != 1:
            diagnostics.append({'kind':'ambiguous_quoted_operating_table', 'caption_start':caption['start']})
            continue
        document = eligible[0][1]; report = registry[document]
        table_ids = sorted(item[2] for item in eligible)
        rows = [fact for item in eligible for fact in item[3]]
        table_evidence = sorted({e for fact in rows for e in fact.source_table_evidence})
        source_labels = sorted({fact.table_label for fact in rows})
        for claim in group:
            context = claim.table_context
            row = procurement_row_identity(context['row_label'])
            proof = {'schema_version':SCHEMA, 'company':claim.company, 'claim_start':claim.start,
                'claim_end':claim.end, 'source_text_sha256':hashlib.sha256(claim.text.encode('utf-8')).hexdigest(),
                'caption':caption, 'citation':citation, 'period':claim.period, 'account':claim.account,
                'relation':claim.relation, 'dimension_kind':row[1], 'dimension_id':row[2],
                'source_dimension_id':row[2], 'source_row_index':0, 'source_row_label':context['row_label'],
                'source_definition_id':PROCUREMENT_GRID_DEFINITION,
                'source_doc':document, 'report_sha256':report['sha256'], 'report_id':report['report_id'],
                'publish_date':report['publish_date'], 'cutoff':cutoff, 'source_table_id':table_ids[0],
                'source_table_ids':table_ids, 'source_table_evidence':table_evidence,
                'source_table_labels':source_labels,
                'source_selection':'explicit_second_inquiry_citation_registered_row_period_and_column',
                'accounting_scope':'未从引用推断合并或母公司',
                'dimension_mapping_basis':'registered_full_original_row_label',
                'caption_semantics_conflict':bool('共建' in caption['text'] and any('对外采购' in label for label in source_labels))}
            proof['sha256'] = _digest(proof)
            fresh = {key:value for key,value in context.items() if key != 'source_dimensions'}
            fresh['source_table_reference'] = proof
            linked = replace(claim, table_context=fresh, declared_source=document, query_strategy='pinned',
                             caliber_hint=(TABLE_SCOPE,'未扣非'))
            if validated_table_reference(linked):
                replacements[claim.claim_id] = linked
    return [replacements.get(c.claim_id,c) for c in claims], diagnostics


def link_quoted_tables(claims, text, facts, reports, cutoff, *, pages=None):
    linked, diagnostics = link_quoted_customer_tables(claims, text, facts, reports, cutoff)
    linked, operating = _link_quoted_operating_tables(linked, text, facts, reports, cutoff)
    linked, business = _link_business_mode_narratives(linked, text, facts, reports, cutoff)
    from .source_customer_quotation import link as link_customer_quotations
    linked, customer = link_customer_quotations(linked,text,facts,reports,cutoff)
    from .source_product_financial import link as link_product_cells
    linked, products = link_product_cells(linked, text, facts, reports, cutoff, pages=pages)
    from .source_quarter_quotation import link as link_quarter_shares
    linked, quarters = link_quarter_shares(linked, text, facts, reports, cutoff, pages=pages)
    return linked, [*diagnostics, *operating, *business, *customer, *products, *quarters]


def _link_business_mode_narratives(claims, text, facts, reports, cutoff):
    """Link a complete local quotation paragraph to its own following figure.

    The figure's first-inquiry citation is bounded by the next caption/source;
    it cannot be borrowed from a nearby procurement or other-business figure.
    Percentages are explicitly source-defined quotations, with the unprinted
    denominator disclosed as a limitation in the frozen reference.
    """
    from .source_business_mode import DEFINITION, NARRATIVE_SCHEMA, narrative_series, narrative_subject, narrative_caption
    from .mentions import unit_multiplier
    from decimal import Decimal
    if not _date(cutoff):
        return list(claims),[]
    registry={report['sha256'][:12]:report for report in reports
        if re.fullmatch(r'[a-f0-9]{64}',str(report.get('sha256','')))
        and _date(report.get('publish_date')) and report['publish_date']<=cutoff
        and re.search(r'第(?:一|1)轮.*问询.*回复',report.get('name',''))}
    replacements,diagnostics={},[]
    for series,cells in narrative_series(text):
        # Only the immediately preceding paragraph and the immediately
        # following figure belong to this typed quoted unit.
        previous=list(re.finditer(r'(?m)^\s*(?:资料|数据)来源\s*[:：].*$',text[:series.start()]))
        if not previous:continue
        paragraph_start=previous[-1].end()
        while paragraph_start<len(text) and text[paragraph_start].isspace():paragraph_start+=1
        boundaries=list(re.finditer(r'(?m)^\s*(?:图\s*\d+\s*[:：]|表\s*\d+\s*[:：]|(?:资料|数据)来源\s*[:：]).*$',text[series.end():]))
        if not boundaries:continue
        boundary=boundaries[0]
        caption_text=boundary.group(0).strip()
        caption_start=series.end()+boundary.start()+len(boundary.group(0))-len(boundary.group(0).lstrip())
        caption={'start':caption_start,'end':caption_start+len(caption_text),'text':caption_text}
        if (not re.match(r'图\s*\d+\s*[:：]',caption_text)
                or cells[0]['business'] not in re.sub(r'\s+','',caption_text)
                or '共建模式' not in re.sub(r'\s+','',caption_text)):
            continue
        following=list(re.finditer(r'(?m)^\s*(?:图\s*\d+\s*[:：]|表\s*\d+\s*[:：]|(?:资料|数据)来源\s*[:：]).*$',text[caption['end']:]))
        if not following:continue
        boundary=following[0];raw=boundary.group(0).strip()
        left=caption['end']+boundary.start()+len(boundary.group(0))-len(boundary.group(0).lstrip())
        citation={'start':left,'end':left+len(raw),'text':raw}
        if _citation_kind(raw)!='first_inquiry':continue
        narrative_end=caption['start']
        while narrative_end>paragraph_start and text[narrative_end-1].isspace():narrative_end-=1
        narrative={'start':paragraph_start,'end':narrative_end,'text':text[paragraph_start:narrative_end]}
        local=[claim for claim in claims if any(claim.start==cell['start'] and claim.end==cell['end'] for cell in cells)]
        if len(local)!=6 or len({claim.company for claim in local})!=1:continue
        company=local[0].company
        if (not narrative_subject(narrative['text'], company)
                or not narrative_caption(caption_text, company, cells[0]['business'])
                or not _table_issuer_matches({'title_source':caption},company)
                or re.search(r'模拟|假设|预计|预测|并非|不是|未引用|不引用',narrative['text'])):
            continue
        pins={claim.declared_source for claim in local if claim.declared_source}
        if len(pins)>1 or any(claim.query_strategy in {'pinned','same_report'} and not claim.declared_source for claim in local):continue
        candidates={}
        for fact in facts:
            report=registry.get(fact.source_doc)
            if (not report or fact.company!=company or fact.definition_id!=DEFINITION
                    or fact.source_type!='financial_source_table' or not fact.source_table_id
                    or not fact.source_table_evidence or fact.publish_date!=report['publish_date']
                    or company not in (report.get('companies') or [report.get('company')])
                    or not _citation_report_matches(raw,report)
                    or pins and not _declared_report_matches(next(iter(pins)),report)):
                continue
            candidates.setdefault((fact.source_doc,fact.source_table_id),[]).append(fact)
        periods={cell['period'] for cell in cells}
        dimension=cells[0]['dimension_id']
        eligible=[(registry[doc]['publish_date'],doc,table,rows) for (doc,table),rows in candidates.items()
            if periods<={row.period for row in rows if row.dimension_id==dimension}
            and len({row.dimension_id.split('/')[0] for row in rows})>=2]
        if not eligible:continue
        latest=max(item[0] for item in eligible)
        eligible=[item for item in eligible if item[0]==latest]
        if len({item[1] for item in eligible})!=1:
            diagnostics.append({'kind':'ambiguous_quoted_business_mode_table','caption_start':caption['start']})
            continue
        document=eligible[0][1];report=registry[document]
        table_ids=sorted(item[2] for item in eligible)
        evidence=sorted({e for item in eligible for row in item[3] for e in row.source_table_evidence})
        for claim in local:
            cell=next(cell for cell in cells if cell['start']==claim.start and cell['end']==claim.end)
            dimension_proof={'schema_version':NARRATIVE_SCHEMA,'narrative':narrative,
                **{key:cell[key] for key in ('business','mode','dimension_id','period','account')}}
            context={key:value for key,value in claim.table_context.items() if key!='source_dimensions'}
            context.update(title=caption_text,title_source=caption,period_source='source_header',
                account_hint=cell['account'],period_hint=cell['period'],scope_hint='未知',relation_hint='value',
                unit_hint=cell['unit'],dimension_kind='business',dimension_id=cell['dimension_id'],
                definition_id=DEFINITION,dimension_source=dimension_proof,
                layout_source='source_business_mode_narrative',row_label=cell['business']+'/'+cell['mode']+'收入')
            proof={'schema_version':SCHEMA,'company':company,'claim_start':claim.start,'claim_end':claim.end,
                'source_text_sha256':hashlib.sha256(narrative['text'].encode('utf-8')).hexdigest(),
                'caption':caption,'citation':citation,'narrative':narrative,'quotation_kind':'business_mode_narrative',
                'period':cell['period'],'account':cell['account'],'relation':'value','dimension_kind':'business',
                'dimension_id':dimension,'source_dimension_id':dimension,'source_row_index':0,
                'source_definition_id':DEFINITION,'source_doc':document,'report_sha256':report['sha256'],
                'report_id':report['report_id'],'publish_date':report['publish_date'],'cutoff':cutoff,
                'source_table_id':table_ids[0],'source_table_ids':table_ids,'source_table_evidence':evidence,
                'source_selection':'explicit_first_inquiry_citation_business_mode_period_and_disclosed_column',
                'dimension_mapping_basis':'registered_business_and_procurement_mode_grammar',
                'accounting_scope':'未从引用推断合并或母公司',
                'source_share_denominator':'所引表内该业务线的总收入',
                'draft_share_denominator_status':'not_explicit_source_defined_quotation_only'}
            proof['sha256']=_digest(proof)
            context['source_table_reference']=proof
            numeric=text[claim.start:claim.end]
            parsed=re.match(r'[+\-－−]?\d[\d,，]*(?:\.\d+)?',numeric)
            value=Decimal(parsed[0].replace(',','').replace('，','').replace('－','-').replace('−','-'))
            linked=replace(claim,text=narrative['text'],source_numeric_start=claim.start-narrative['start'],
                numeric_text=numeric,company=company,account=cell['account'],period=cell['period'],relation='value',
                base_period=None,candidate_value=str(value*unit_multiplier(cell['unit'])),unit=cell['unit'],
                display_decimals=max(0,-value.as_tuple().exponent),parse_status='parsed',binding_status='unresolved',
                table_context=context,declared_source=document,query_strategy='pinned',caliber_hint=(TABLE_SCOPE,'未扣非'))
            if validated_table_reference(linked):replacements[claim.claim_id]=linked
    return [replacements.get(claim.claim_id,claim) for claim in claims],diagnostics
