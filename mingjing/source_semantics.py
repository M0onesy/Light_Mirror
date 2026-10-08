"""Freeze source dimensions before model binding, without reading fact values.

Models may propose semantics, but cannot relabel an explicitly named source
account, period, relation or basis to make an unrelated number pass. Numeric
values are used only to locate their untouched original character span.
"""
from __future__ import annotations

from dataclasses import replace
from copy import deepcopy
import hashlib
import re

from .mentions import (ACCOUNT_LABELS, VALUE_RE, YEAR_RE, _account_before, infer_relation, period_from_text,
                       previous_period, listed_periods, source_currency, account_label_pattern, infer_account,
                       net_margin_definition_matches, SOURCE_REVIEW_FORMULAS, SOURCE_REVIEW_PER_SHARE,
                       percentage_point_dimensions)
from .mentions import (source_metric_identity, cagr_source_dimensions, business_source_dimensions,
                       customer_source_dimensions, source_range_occurrence)
from .scope import source_scope_dimensions


SCHEMA = 'source-dimensions.v1'
FIELDS = ('company', 'account', 'period', 'scope', 'relation', 'base_period')
_TABLE_GROWTH_ROW = re.compile(r'(?:YOY|同比|增长率)[（(]?[%％]?[）)]?', re.I)
_TABLE_LAYOUT_TITLE = re.compile(
    r'(?:附[:：]?)?(?:(?:合并|母公司)?(?:资产负债表|利润表|现金流量表)|财务预测摘要|'
    r'主要财务比率|主要会计数据|会计数据和财务指标|主要财务指标|指标)'
    r'(?:[（(](?:百万元|亿元|万元|元|%|％)[）)])?')


def _text_hash(claim) -> str:
    return hashlib.sha256(claim.text.encode('utf-8')).hexdigest()


def _position(claim) -> int | None:
    position = claim.source_numeric_start
    if (type(position) is int and 0 <= position <= len(claim.text) and claim.numeric_text
            and claim.text[position:position+len(claim.numeric_text)] == claim.numeric_text):
        return position
    if position is not None:
        return None
    if claim.numeric_text:
        hits = [match.start() for match in re.finditer(re.escape(claim.numeric_text), claim.text)]
        if len(hits) == 1:
            return hits[0]
    else:
        # Earlier programmatic claims can omit the raw span. Only a unique
        # source-written, dimensioned number may be located; never compare
        # the candidate amount to choose among several source occurrences.
        hits = [match.start() for match in VALUE_RE.finditer(claim.text) if match['unit']]
        if len(hits) == 1:
            return hits[0]
    return None


def _numeric_dimensions(claim) -> dict:
    position = _position(claim)
    if position is None:
        return {}
    account = _account_before(claim.text, position)
    business = business_source_dimensions(claim.text, position)
    customer = customer_source_dimensions(claim.text, position)
    dimension = business or customer
    if dimension and account == 'UNKNOWN':
        account = dimension['account']
    account, definition_id = source_metric_identity(account, claim.text, position)
    definition_id = customer.get('definition_id') or definition_id
    target = VALUE_RE.match(claim.text, position)
    ordered_period, ordered_basis = listed_periods(claim.text, position)
    source_unit = target['unit'] if target else None
    if not source_unit:
        source_unit = source_range_occurrence(claim.text, position).get('unit')
    if not source_unit and ordered_basis and not ordered_basis.get('ambiguous'):
        source_unit = ordered_basis.get('source_unit')
    if not target or not source_unit:
        return {}
    if source_unit == '百分点':
        pp = percentage_point_dimensions(claim.text, position)
        return pp or {'account': account if account != 'UNKNOWN' else '', 'period': '', 'relation': '',
                     'unsupported_source_relation': 'unresolved_percentage_point_difference'}
    relation = infer_relation(claim.text, position, target.end(), source_unit, account)
    if relation == 'unsupported':
        relation = 'cagr'
    if relation == 'cagr':
        cagr = cagr_source_dimensions(claim.text, position)
        if cagr:
            return cagr
    prefix = claim.text[:position]
    local_prefix = re.split(r'[,，。；;]', prefix)[-1]
    compact = re.sub(r'\s+', '', local_prefix)
    period = period_from_text(local_prefix)
    if not period:
        # A shared year is usable only when the preceding context declares
        # one unique period. Two comparison years cannot silently donate
        # their final/baseline year to a later unqualified metric.
        shared = {period_from_text(match[0]) for match in YEAR_RE.finditer(prefix)}
        shared.discard('')
        period = next(iter(shared)) if len(shared) == 1 else ''
    if ordered_period is not None:
        period = ordered_period
    dimensions = {'account': account if account != 'UNKNOWN' else '', 'period': period, 'relation': relation}
    if definition_id:
        dimensions['definition_id'] = definition_id
    if ordered_basis:
        dimensions['source_period_list'] = ordered_basis
    # Equality is not a substitute for an explicit numerical inequality or
    # negation. The current kernel has no authorised number-threshold rule.
    comparator = re.search(r'(?:不是|并非|不等于|不为|不低于|不高于|不少于|不多于|不超过|至少|至多|'
                           r'超过|低于|小于|高于|大于|不足|不到)(?:约|大约|近|达到)?$', compact)
    if comparator:
        if comparator[0] in {'超过', '不足', '不到'}:
            dimensions['hedge_kind'] = comparator[0]
            dimensions['hedge_rule_basis'] = 'proposal_table_5_5'
        else:
            dimensions.update(relation='', unsupported_source_relation='numeric_comparison_or_negation')
            return dimensions
    # Current and baseline are declared in the source, in that order. A
    # last-year heuristic would bind "2024年较2023年增长" to the wrong year.
    period_pattern = (r'20\d{2}\s*(?:年\s*(?:度|上半年|下半年|半年度|前三季度|第?[一二三四]季度)?'
                      r'|H[12]|Q[1-4]|9M|Y)')
    comparison = re.search(rf'(?P<current>{period_pattern})(?:[^\n，,。；;%％]{{0,50}}?)'
                           rf'(?:相较于?|相比于?|环比|比|较)\s*(?P<base>{period_pattern})', local_prefix)
    if comparison:
        current, base = period_from_text(comparison['current']), period_from_text(comparison['base'])
        dimensions.update(period=current, base_period=base, period_comparison_explicit=True)
        if relation in {'yoy', 'qoq'} and base != previous_period(current, relation):
            dimensions.update(relation='', unsupported_source_relation='unregistered_period_comparison')
    return dimensions


def _table_title_subject(source: dict, selected: str) -> dict | None:
    """A table's named issuer precedes its business/counterparty rows.

    Only subject grammar is inspected. A purchaser named after 公司向/从 is
    a counterparty, not a replacement issuer. No amount or approximate name
    participates in this decision.
    """
    caption = source.get('title_source')
    raw = caption['text'] if isinstance(caption, dict) and _source_reference(caption) else source.get('title', '')
    title = re.sub(r'\s+', '', str(raw))
    title = re.sub(r'^(?:表|图)\d+[:：]', '', title)
    title = re.sub(r'^(?:(?:20\d{2})(?:[-—–至](?:20\d{2}))?(?:年(?:度)?|H[12]|Q[1-4]|Y)?[：:、,，]?)+', '', title)
    # Plain financial captions have no separately declared entity subject.
    match = re.match(r'(?P<subject>.+?)(?:通过|向|从|对|实现|披露|主要客户|前五(?:大|名)?客户|'
                     r'客户|营业收入|营收|销售|采购|毛利率|财务|收入|利润|费用|现金流|资产|负债|ROE|EPS)', title)
    if not match:
        return None
    subject = re.sub(r'20\d{2}(?:年(?:度)?|H[12]|Q[1-4]|Y)?$', '', match['subject'])
    pronouns = {'公司','本公司','该公司','发行人','本发行人','集团','本集团','企业','本企业'}
    if subject in pronouns or not subject:
        return None
    from .issuer_identity import same_issuer
    if selected and (same_issuer(subject, selected) or subject == selected+'公司'):
        return {'company':selected, 'company_provenance':'explicit_source_table_issuer',
                'source_company_subjects':[subject], 'company_source_explicit':True}
    names = [value for value in re.split(r'与|及|和|、|/|&', subject) if value]
    if len(names) > 1:
        return {'company':'', 'company_provenance':'source_subject_ambiguous',
                'source_company_subjects':names, 'company_source_explicit':True,
                'source_company_ambiguous':True}
    if (re.search(r'(?:公司|集团|股份|科技|信息|银行|证券|企业)$', subject)
            or match[0].endswith(('通过','实现','披露'))):
        return {'company':'', 'company_provenance':'source_subject_conflict_or_unknown',
                'source_company_subjects':[subject], 'company_source_explicit':True,
                'source_company_conflict':True}
    return None


def _company_subject(claim, *, table: bool) -> dict:
    """Respect an explicit local subject; issuer selection is not source text.

    This deliberately does not attempt general Chinese NER. An omitted
    subject or a plain company pronoun can use the host-locked issuer; an
    explicit different/parallel/unknown noun immediately before the account
    cannot be silently rewritten to that issuer.
    """
    selected = claim.company
    position = _position(claim)
    prefix = claim.text[:position] if position is not None else claim.text
    validated_business_subject = False
    if not table and position is not None:
        customer = customer_source_dimensions(claim.text, position)
        if (customer and customer.get('dimension_id') == claim.table_context.get('dimension_id')
                and customer.get('dimension_source') == claim.table_context.get('dimension_source')):
            # Counterparty-caption words are not issuer names. The independently
            # parsed prefix still preserves an explicitly different company.
            prefix = customer['dimension_source']['subject_prefix']
        business = business_source_dimensions(claim.text, position)
        if (business and business.get('dimension_id') == claim.table_context.get('dimension_id')
                and business.get('dimension_source') == claim.table_context.get('dimension_source')):
            # Remove the proven business caption from subject grammar only;
            # "乙公司超算云服务收入" still retains the different issuer.
            prefix = business['dimension_source'].get('subject_prefix', prefix.replace(business['dimension_id'], ''))
            validated_business_subject = True
    if table:
        source = claim.table_context
        title_subject = _table_title_subject(source, selected)
        if title_subject:
            return title_subject
        dimension = source.get('dimension_source')
        if isinstance(dimension, dict) and dimension.get('schema_version') == 'source-quarter-quotation.v1':
            from .source_quarter_quotation import narrative_identity
            from .source_table_reference import validated_table_reference
            observed = narrative_identity(claim)
            if observed and validated_table_reference(claim):
                return {'company': selected, 'company_provenance': 'host_locked_issuer_context',
                        'source_company_subjects': [], 'company_source_explicit': False,
                        'quotation_company_basis': 'validated_quarter_quotation_and_source_table_reference'}
        if isinstance(dimension, dict) and dimension.get('schema_version') == 'source-product-financial-narrative.v1':
            from .source_product_financial import narrative_identity
            from .source_table_reference import validated_table_reference
            observed = narrative_identity(claim)
            if observed and validated_table_reference(claim):
                return {'company': selected, 'company_provenance': 'host_locked_issuer_context',
                        'source_company_subjects': [], 'company_source_explicit': False,
                        'quotation_company_basis': 'validated_product_financial_quotation_and_source_table_reference'}
        if isinstance(dimension, dict) and dimension.get('schema_version') == 'source-customer-quotation.v1':
            from .source_customer_quotation import identity
            from .source_table_reference import validated_table_reference
            observed = identity(claim)
            if observed and validated_table_reference(claim):
                # The complete narrative or the chart's caption/legend/year
                # proves a counterparty classification, not another issuer.
                # Its independently validated source caption still controls
                # the issuer; no customer name or amount replaces that issuer.
                return {'company': selected, 'company_provenance': 'host_locked_issuer_context',
                        'source_company_subjects': [], 'company_source_explicit': False,
                        'quotation_company_basis': 'validated_customer_quotation_and_source_table_reference'}
        if isinstance(dimension, dict) and dimension.get('schema_version') == 'source-business-mode-narrative.v1':
            from .source_business_mode import narrative_identity, narrative_subject
            from .source_table_reference import validated_table_reference
            identity = narrative_identity(claim)
            subject = narrative_subject(claim.text, selected)
            if identity and subject and validated_table_reference(claim):
                return {'company':selected, 'company_provenance':'host_locked_issuer_context' if subject['subject']=='公司' else 'explicit_source_subject',
                        'source_company_subjects':[] if subject['subject']=='公司' else [subject['subject']],
                        'company_source_explicit':subject['subject']!='公司',
                        'quotation_company_basis':'validated_business_mode_narrative_and_source_table_reference'}
        from .dimension_tables import procurement_row_identity, PROCUREMENT_GRID_DEFINITION
        grid = procurement_row_identity(source.get('row_label', ''))
        registered_purchase_row = bool(grid and grid[1] == 'purchase_mode'
            and source.get('definition_id') == PROCUREMENT_GRID_DEFINITION
            and source.get('dimension_kind') == grid[1] and source.get('dimension_id') == grid[2]
            and source.get('account_hint') == grid[0])
        if (isinstance(dimension, dict) and dimension.get('schema_version') == 'source-dimension-table.v1'
                and (dimension.get('dimension_kind') in {'customer', 'business'}
                     or registered_purchase_row and dimension.get('dimension_kind') == 'purchase_mode')
                and dimension.get('dimension_id') == source.get('dimension_id')
                and dimension.get('cell', {}).get('start', -1) <= claim.start < dimension.get('cell', {}).get('end', -1)
                and source.get('layout_source') == 'pdf_dimension_table_columns'):
            return {'company': selected, 'company_provenance': 'host_locked_issuer_with_counterparty_or_business',
                    'source_company_subjects': [], 'company_source_explicit': False}
        row_label = re.sub(r'\s+', '', str(source.get('row_label', '')))
        title = re.sub(r'\s+', '', str(source.get('title', '')))
        if (_TABLE_GROWTH_ROW.fullmatch(row_label) and _TABLE_LAYOUT_TITLE.fullmatch(title)
                and source.get('relation_hint') == 'yoy'
                and source.get('account_hint') not in {None, '', 'UNKNOWN'}):
            # A standalone YOY row inherits only its deterministic preceding
            # account, not a company name from earlier numeric cells in the
            # same row. The exact layout title contains no issuer subject;
            # the selected issuer remains host context, never source-explicit.
            return {'company': selected, 'company_provenance': 'host_locked_issuer_context',
                    'source_company_subjects': [], 'company_source_explicit': False}
        # Year/account columns are already source addressed. Only a source
        # title can introduce an issuer subject before a standalone row.
        prefix = str(claim.table_context.get('title', '')) + '；' + prefix
    local = re.split(r'[,，。；;：:]', prefix)[-1]
    compact = re.sub(r'\s+', '', local)
    matches = [(match.start(), match.end()) for label, _ in ACCOUNT_LABELS
               for match in re.finditer(account_label_pattern(label), compact)]
    # This source caption lacks a registered business identity and therefore
    # cannot authorize REVENUE. It still ends the corporate subject: do not
    # turn "甲公司主营业务收入" into an invented different company merely
    # because the account is deliberately UNKNOWN. The segment guard remains
    # independent and continues to reject a company-total substitution.
    matches.extend((match.start(), match.end())
                   for match in re.finditer(r'主营业务收入', compact))
    matches.extend((start,end) for start,end,_ in net_margin_definition_matches(compact))
    matches = [span for span in matches if not any(other[0] <= span[0] and other[1] >= span[1]
                                                 and other != span for other in matches)]
    if matches:
        before = compact[:max(matches, key=lambda span: span[1])[0]]
    else:
        before = compact
    before = re.sub(r'(?<!\d)20\d{2}\s*(?:年\s*)?[-－–—~～至到]\s*20\d{2}\s*年'
                    r'(?:度|上半年|下半年|第?[一二三四]季度)?', '', before)
    before = YEAR_RE.sub('', before)
    if position is not None and claim.relation == 'cagr':
        cagr = cagr_source_dimensions(claim.text, position)
        if (cagr and re.fullmatch(r'\d+年', before)
                and int(before[:-1]) == cagr['source_cagr_period']['years']):
            # An explicit duration agreeing with the written endpoints is a
            # time dimension, not a corporate subject ("2018-2023年5年营收").
            before = ''
    if re.search(r'(?:本公司|公司|本集团|集团|该公司)(?:的)?(?:母公司|子公司|关联公司|联营公司)', before):
        return {'company': '', 'company_provenance': 'source_subject_ambiguous',
                'source_company_subjects': [before], 'company_source_explicit': False,
                'source_company_ambiguous': True}
    before = re.sub(r'(?<!归属)(?<!属于)(?:合并|母公司)(?:财务报表|报表|口径)?', '', before)
    before = re.sub(r'[（(](?:经审计|未经审计|调整后|调整前)[）)]', '', before)
    before = before.strip('“”"\'（）()[]【】●•-— ')
    if validated_business_subject:
        # Only an independently proven business caption allows its written
        # "core/main business" qualifier to be treated as grammar. Preserve
        # any preceding issuer name or parallel-company construction.
        before = re.sub(r'(?:核心|主要)业务$', '', before)
    # Wrapped percent lists can precede a later genuine named subject.
    # Remove only complete numeric-percent fragments plus their connector;
    # the residual "乙公司" must still be treated as another issuer.
    before = re.sub(r'^(?:[-+－−]?\d[\d,，]*(?:\.\d+)?[%％](?:和|及|与|、|以及))+', '', before)
    before = re.sub(r'^(?:\d+(?:[.．]\d+)*[.．、)）]|[（(]\d+[）)]|[一二三四五六七八九十]+[、.．])', '', before)
    # Registered layout/grammar words convey no independent company name.
    for _ in range(3):
        before = re.sub(r'^(?:图\d+|表\d+|据披露|披露显示|报告显示|数据显示|业绩方面|整体来看|'
                        r'报告期内|报告期|本年度|其中|截至|期末|期初|本年|今年|去年)', '', before)
        before = re.sub(r'(?:实现|录得|达到|的|整体|总体|总|合计|为|由|同比|环比|增长|下降|相比于?|相较于?|比|较)$', '', before)
    pronouns = {'', '公司', '本公司', '该公司', '企业', '本企业', '该企业', '集团', '本集团',
                '发行人', '本发行人', '上市公司', '挂牌公司', '目标公司'}
    if before in pronouns or table and before in {'合并利润表', '母公司利润表', '利润表', '资产负债表', '现金流量表'}:
        return {'company': selected, 'company_provenance': 'host_locked_issuer_context',
                'source_company_subjects': [], 'company_source_explicit': False}
    # Exact issuer aliases take priority: a connector in a registered issuer
    # name (e.g. 和而泰) must not split that name into imaginary companies.
    if selected and before in {selected, selected + '公司'}:
        return {'company': selected, 'company_provenance': 'explicit_source_subject',
                'source_company_subjects': [before], 'company_source_explicit': True}
    # A previous number plus a conjunction is not a new corporate subject
    # ("78.53%和81.27%" in a wrapped percentage list). Do not preserve
    # fragments such as "53%和" as invented issuer names. Account/period,
    # ratio and segment source guards still apply independently.
    if re.fullmatch(r'(?:[-+－−]?\d[\d,，]*(?:\.\d+)?[%％]\s*(?:和|及|与|、|以及)?\s*)+', before):
        return {'company': selected, 'company_provenance':'host_locked_issuer_context',
                'source_company_subjects':[], 'company_source_explicit':False,
                'source_subject_noncorporate_fragment':True}
    parts = [part for part in re.split(r'与|及|和|、|/|&', before) if part]
    if len(parts) > 1:
        return {'company': '', 'company_provenance': 'source_subject_ambiguous',
                'source_company_subjects': parts, 'company_source_explicit': False,
                'source_company_ambiguous': True}
    corporate_suffix = re.search(r'(?:股份有限公司|有限责任公司|有限公司|集团|公司|股份|科技|信息|银行|证券)$', before)
    if selected and selected in before and corporate_suffix:
        return {'company': selected, 'company_provenance': 'explicit_source_subject',
                'source_company_subjects': [before], 'company_source_explicit': True}
    # A separate local name is observable even without a company suffix.
    # It is not assigned an invented canonical issuer ID. Any unresolved
    # subject is blocked; the user can supply a clearer source material.
    return {'company': '', 'company_provenance': 'source_subject_conflict_or_unknown',
            'source_company_subjects': [before], 'company_source_explicit': False,
            'source_company_conflict': True}


def _derive(claim) -> dict:
    """Only source text/table headers and host identity, never rebound fields."""
    source = claim.table_context if isinstance(claim.table_context, dict) else {}
    scope = source_scope_dimensions(claim)
    table = source.get('period_source') == 'source_header'
    proof = {'schema_version': SCHEMA, 'company': '',
             'account': '', 'period': '', 'scope': scope['scope_header'],
             'relation': '', 'base_period': '', 'source_text_sha256': _text_hash(claim),
             'source_start': claim.start, 'source_end': claim.end,
             'source_numeric_start': claim.source_numeric_start,
             'provenance': 'source_text_deterministic', 'scope_provenance': scope['provenance']}
    from .source_quarter_quotation import validated_threshold_reference
    threshold = validated_threshold_reference(claim)
    if threshold:
        # The complete original paragraph and independently cited official
        # assertion fix every semantic field. The lower-bound amount is
        # deliberately absent from the blind binding metadata.
        proof.update(company=claim.company,account='REVENUE_SHARE',period=threshold['period'],
            relation='gt',dimension_kind='business',dimension_id=threshold['dimension_id'],
            definition_id='source:annual-cloud-business-mode-revenue-share',currency='CNY',
            company_provenance='host_locked_issuer_context',company_source_explicit=False,
            source_company_subjects=[],source_threshold_reference=deepcopy(threshold),
            evaluation_scope='issuer_historical_financial',
            provenance='deterministic_source_threshold_quotation')
        proof['explicit'] = {field:bool(proof[field]) for field in FIELDS}
        proof['explicit']['company'] = False
        return proof
    proof.update(_validated_financial_identity(claim, source))
    if scope.get('scope_proof'):
        proof['scope_proof'] = deepcopy(scope['scope_proof'])
    if scope.get('source_table_reference'):
        proof['source_table_reference'] = deepcopy(scope['source_table_reference'])
    if source.get('evaluation_scope'):
        proof['evaluation_scope'] = source['evaluation_scope']
        proof['classification_basis'] = deepcopy(source.get('classification_basis', {}))
    if source.get('source_method_proposal'):
        from .source_method import validated_source_method
        proposal = validated_source_method(claim)
        if proposal:
            proof['source_method_proposal'] = proposal
    proof.update(_company_subject(claim, table=table))
    position = _position(claim)
    currency = (source_currency(claim.text, position) if position is not None else source_currency(claim.text)) or source.get('source_currency')
    if currency:
        proof['source_currency'] = currency
        proof['currency'] = currency['currency']
    proof.update({key: scope[key] for key in ('scope_definition_id', 'scope_definition_label',
                                            'scope_definition_basis', 'scope_definition_source') if key in scope})
    if scope.get('scope_declaration_resource_id'):
        proof.update(scope_declaration_resource_id=scope['scope_declaration_resource_id'],
                     scope_declaration_sha256=scope['scope_declaration_sha256'])
        for key in ('conditional_verification', 'scope_declaration_draft_sha256',
                    'scope_declaration_target_sha256'):
            if key in scope:
                proof[key] = scope[key]
    if source.get('period_source') == 'source_header':
        # These typed fields are produced by the deterministic table parser
        # before a model can submit its six strings. Preserve its undefined
        # captions too: EPS(摊薄/元) must never become statutory diluted EPS.
        source_account = _source_table_account(claim, source, proof)
        proof.update(account=source_account,
                     period=str(source.get('period_hint') or ''),
                     relation=str(source.get('relation_hint') or ''),
                     provenance='deterministic_table_headers')
    elif not claim.numeric_text:
        labels = '|'.join(account_label_pattern(label) for label, code in ACCOUNT_LABELS if code != 'UNKNOWN')
        comparison = re.compile(
            rf'(?P<current>20\d{{2}}\s*(?:年|Y))(?P<scope>母公司口径|合并口径)?'
            rf'(?P<label>{labels})\s*(?P<relation>不低于|不高于|高于|低于|大于|小于|等于)'
            r'\s*(?P<base>20\d{2}\s*(?:年|Y))')
        matches = list(comparison.finditer(claim.text))
        if len(matches) == 1:
            match = matches[0]
            relation = {'高于': 'gt', '大于': 'gt', '低于': 'lt', '小于': 'lt',
                        '不低于': 'ge', '不高于': 'le', '等于': 'eq'}[match['relation']]
            proof.update(account=infer_account(match['label']),
                         period=period_from_text(match['current']), relation=relation,
                         base_period=period_from_text(match['base']))
        elif not matches:
            proof.update(_numeric_dimensions(claim))
    else:
        # A physical numeric cell without a trusted header cannot inherit
        # an amount/rate dimension from the caller's current claim fields.
        proof.update(_numeric_dimensions(claim))
    if proof['relation'] in {'yoy', 'qoq'} and proof['period'] and not proof.get('period_comparison_explicit'):
        proof['base_period'] = previous_period(proof['period'], proof['relation'])
    diagnostic = source_definition_diagnostic(claim)
    if diagnostic:
        proof['source_definition_diagnostic'] = diagnostic
    proof['explicit'] = {field: bool(proof[field]) for field in FIELDS}
    proof['explicit']['company'] = proof['company_source_explicit']
    return proof


def _source_table_account(claim, source, identity):
    """Validate a caption instead of authorizing an arbitrary typed hint."""
    if identity.get('dimension_source'):
        kind = identity['dimension_source'].get('schema_version')
        if kind == 'source-quarter-quotation.v1':
            from .source_quarter_quotation import narrative_identity
            observed = narrative_identity(claim)
            return observed['account'] if observed else ''
        if kind == 'source-product-financial-narrative.v1':
            from .source_product_financial import narrative_identity
            observed = narrative_identity(claim)
            return observed['account'] if observed else ''
        if kind == 'source-customer-quotation.v1':
            from .source_customer_quotation import identity as customer_identity
            observed = customer_identity(claim)
            return observed['account'] if observed else ''
        if kind == 'source-business-mode-narrative.v1':
            from .source_business_mode import narrative_identity
            observed = narrative_identity(claim)
            return observed['account'] if observed else ''
        if kind == 'source-dimension-table.v1':
            headers = identity['dimension_source'].get('headers', [])
            raw = [re.sub(r'\s+','',ref['text']) for ref in headers]
            if identity.get('definition_id') == 'metric:revenue-share':
                return 'REVENUE_SHARE'
            label = re.sub(r'\s+','',str(source.get('row_label','')))
            if identity['dimension_kind']=='customer' and any(re.match(r'(?:营业收入|销售收入)[（(]',item) for item in raw):
                return 'REVENUE'
            if identity['dimension_kind'] in {'business', 'purchase_mode'}:
                return 'REVENUE' if label.endswith('收入') else 'PURCHASE_AMOUNT' if label.endswith('采购金额') else ''
    from .table_mentions import _row, table_metric_identity
    caption = str(source.get('row_label') or '')
    if _TABLE_GROWTH_ROW.fullmatch(re.sub(r'\s+','',caption)):
        from .scope import _validated_account_lineage
        caption = _validated_account_lineage(claim, source) or ''
    parsed = _row(caption, '')
    if not parsed:
        return ''
    account, _, _ = parsed
    account, _ = table_metric_identity(account, caption, str(source.get('title', '')))
    if (identity.get('definition_id','').startswith('metric:broker-other-') and
            source.get('account_hint') == 'BROKER_'+account):
        account = 'BROKER_'+account
    return account


def _frozen(claim) -> dict | None:
    source = claim.table_context if isinstance(claim.table_context, dict) else {}
    proof = source.get('source_dimensions')
    if proof is None:
        return None
    if (not isinstance(proof, dict) or proof.get('schema_version') != SCHEMA or
            proof.get('source_text_sha256') != _text_hash(claim) or
            proof.get('source_start') != claim.start or proof.get('source_end') != claim.end or
            proof.get('source_numeric_start') != claim.source_numeric_start or
            any(not isinstance(proof.get(field), str) for field in FIELDS)):
        raise ValueError('冻结源维度与原文字符定位或版本不一致')
    for field in ('dimension_kind', 'dimension_id', 'currency', 'definition_id'):
        if field in proof and field in source and proof[field] != source[field]:
            raise ValueError('冻结金融维度与上下文不一致，不能改写来源定义')
    return proof


def _source_reference(ref):
    return (isinstance(ref, dict) and type(ref.get('start')) is int and type(ref.get('end')) is int
            and isinstance(ref.get('text'), str) and ref['end']-ref['start']==len(ref['text'])
            and ref['start'] >= 0 and ref['end']>ref['start'])


def _validated_financial_identity(claim, source):
    result = {'dimension_kind':'total', 'dimension_id':'', 'currency':'CNY', 'definition_id':''}
    dimension = source.get('dimension_source')
    if isinstance(dimension, dict):
        if dimension.get('schema_version') == 'source-quarter-quotation.v1':
            from .source_quarter_quotation import narrative_identity
            observed = narrative_identity(claim)
            if observed:
                result.update(dimension_kind=observed['dimension_kind'],
                    dimension_id=observed['dimension_id'], definition_id=observed['definition_id'],
                    dimension_source=deepcopy(dimension))
        elif dimension.get('schema_version') == 'source-product-financial-narrative.v1':
            from .source_product_financial import narrative_identity
            observed = narrative_identity(claim)
            if observed:
                result.update(dimension_kind=observed['dimension_kind'],
                    dimension_id=observed['dimension_id'], definition_id=observed['definition_id'],
                    dimension_source=deepcopy(dimension))
        elif dimension.get('schema_version') == 'source-customer-quotation.v1':
            from .source_customer_quotation import identity
            observed = identity(claim)
            if observed:
                result.update(dimension_kind=observed['dimension_kind'],
                    dimension_id=observed['dimension_id'], definition_id=observed['definition_id'],
                    dimension_source=deepcopy(dimension))
        elif dimension.get('schema_version') == 'source-business-mode-narrative.v1':
            from .source_business_mode import narrative_identity
            observed = narrative_identity(claim)
            if observed:
                result.update(dimension_kind=observed['dimension_kind'],
                    dimension_id=observed['dimension_id'], definition_id=observed['definition_id'],
                    dimension_source=deepcopy(dimension))
        elif dimension.get('schema_version') == 'source-business-caption.v1':
            position = _position(claim)
            observed = business_source_dimensions(claim.text, position) if position is not None else {}
            if (observed.get('dimension_source') == dimension and observed.get('dimension_kind') == source.get('dimension_kind')
                    and observed.get('dimension_id') == source.get('dimension_id')):
                result.update(dimension_kind='business', dimension_id=observed['dimension_id'], dimension_source=deepcopy(dimension))
        elif dimension.get('schema_version') == 'source-customer-caption.v1':
            position = _position(claim)
            observed = customer_source_dimensions(claim.text, position) if position is not None else {}
            if (observed.get('dimension_source') == dimension and observed.get('dimension_kind') == source.get('dimension_kind')
                    and observed.get('dimension_id') == source.get('dimension_id')):
                result.update(dimension_kind='customer', dimension_id=observed['dimension_id'],
                              dimension_source=deepcopy(dimension), definition_id=observed['definition_id'])
        elif dimension.get('schema_version') == 'source-dimension-table.v1':
            caption, cell, labels, headers = (dimension.get(key) for key in ('caption','cell','label_spans','headers'))
            body_labels = source.get('row_label')
            valid = (all(_source_reference(ref) for ref in (caption,cell)) and isinstance(labels,list) and labels
                     and isinstance(headers,list) and headers and all(_source_reference(ref) for ref in labels+headers)
                     and dimension.get('dimension_kind') in {'customer','business','purchase_mode'}
                     and dimension.get('dimension_kind') == source.get('dimension_kind')
                     and dimension.get('dimension_id') == source.get('dimension_id')
                     and cell['start'] <= claim.start < claim.end <= cell['end']
                     and ''.join(ref['text'].strip() for ref in labels) == body_labels
                     and all(ref['text'] in claim.text for ref in labels)
                     and source.get('layout_source') == 'pdf_dimension_table_columns')
            if valid:
                name = re.sub(r'\s+', '', body_labels)
                expected = name
                from .dimension_tables import procurement_row_identity, PROCUREMENT_GRID_DEFINITION
                grid = procurement_row_identity(name)
                if (grid and source.get('definition_id') == PROCUREMENT_GRID_DEFINITION
                        and grid[1] == dimension['dimension_kind']):
                    expected = grid[2]
                elif dimension['dimension_kind']=='business':
                    expected = name[:-2] if name.endswith('收入') else name[:-4] if name.endswith('采购金额') else ''
                elif name=='合计' and '前五大' in caption['text']:
                    expected = '前五大客户'
                if expected and expected == dimension['dimension_id']:
                    result.update(dimension_kind=dimension['dimension_kind'], dimension_id=expected, dimension_source=deepcopy(dimension))
                    if grid and source.get('definition_id') == PROCUREMENT_GRID_DEFINITION:
                        result['definition_id'] = PROCUREMENT_GRID_DEFINITION
                    if (source.get('account_hint')=='REVENUE_SHARE' and source.get('definition_id')=='metric:revenue-share'
                            and any(re.search('占营业收入(?:比重|比例)',ref['text']) for ref in headers)):
                        result['definition_id']='metric:revenue-share'
    account = str(source.get('account_hint') or '') if source.get('period_source')=='source_header' else ''
    label = str(source.get('row_label') or '') if account else claim.text
    if not account:
        position = _position(claim)
        account = _account_before(claim.text, position) if position is not None else ''
    identified, definition = source_metric_identity(account, label)
    if identified == account and definition and definition == source.get('definition_id'):
        result['definition_id']=definition
    # Compact residuals require the original bounded broker-summary heading,
    # source citation and row caption; a freely supplied ID is never enough.
    broker = {'BROKER_OTHER_CURRENT_ASSETS':('其他流动资产','资产负债表'),
              'BROKER_OTHER_CURRENT_LIABILITIES':('其他流动负债','资产负债表'),
              'BROKER_OTHER_NONCURRENT_LIABILITIES':('其他非流动负债','资产负债表'),
              'BROKER_OTHER_NONCURRENT_ASSETS':('其他非流动资产','资产负债表')}
    if account in broker:
        summary = source.get('summary_source', {})
        heading, citation = summary.get('heading'), summary.get('citation')
        expected_definition = 'metric:'+account.lower().replace('_','-')
        if (_source_reference(heading) and _source_reference(citation)
                and summary.get('schema_version')=='source-broker-summary.v1'
                and re.fullmatch(r'(?:附[:：]?)?(?:财务预测摘要|财务摘要和估值指标)',re.sub(r'\s+','',heading['text']))
                and heading['end']<=claim.start<claim.end<=citation['start']
                and source.get('table_profile')=='broker_compact'
                and broker[account][1] in str(source.get('title',''))
                and re.sub(r'\s+','',str(source.get('row_label',''))) == broker[account][0]
                and source.get('definition_id')==expected_definition):
            result['definition_id']=expected_definition
    currency = source.get('source_currency')
    if isinstance(currency,dict) and isinstance(currency.get('currency'),str):
        result['currency']=currency['currency']
    return result


def freeze_source_dimensions(claim):
    """Return a replaced claim; call on host source claims before model use.

    Existing frozen dimensions survive resume/replay and a changed semantic
    binding. Recomputing them from the rebound fields would erase the guard.
    """
    if _frozen(claim) is not None:
        return claim
    return replace(claim, table_context={**claim.table_context, 'source_dimensions': _derive(claim)})


def source_dimensions_from_text(claim) -> dict:
    """Return a fresh, amount-free view of authoritative source dimensions."""
    proof = _frozen(claim) or _derive(claim)
    return deepcopy(proof)


def semantic_review_required(claim) -> bool:
    """Only host-parsed numbers with an explicitly unresolved source label."""
    source = claim.table_context if isinstance(claim.table_context, dict) else {}
    return (claim.parse_status == 'parsed' and source.get('numeric_parse_status') == 'parsed'
            and source.get('semantic_review_required') is True
            and str(source.get('source_account_unresolved') or '').endswith('_UNSPECIFIED'))


def source_definition_diagnostic(claim) -> dict | None:
    """Explain registered alternatives without granting a source definition.

    This amount-free metadata belongs to new source snapshots. It neither
    adds fields to old frozen locks nor lets a canonical model binding erase
    the parser's original unresolved account.
    """
    if not semantic_review_required(claim):
        return None
    account = claim.table_context['source_account_unresolved']
    methods = {
        'CURRENT_RATIO_UNSPECIFIED': ('CURRENT_RATIO', '原文明示非标准、自定义或未确认的流动比率方法；不能套用标准流动资产/流动负债定义',
                                      ['原文明示方法与标准定义的一致性']),
        'QUICK_RATIO_UNSPECIFIED': ('QUICK_RATIO', '原文尚未明确速动资产组成；不能假定仅扣存货或依匹配比率选择扣项',
                                    ['速动资产扣项及流动负债分母']),
        'TURNOVER_ASSETS_UNSPECIFIED': ('ASSET_TURNOVER', '原文尚未明确收入分子及平均/期末资产分母',
                                        ['收入分子范围', '平均或期末总资产分母']),
        'TURNOVER_INVENTORY_UNSPECIFIED': ('INVENTORY_TURNOVER', '原文尚未明确成本分子及平均/期末存货分母',
                                           ['成本分子范围', '平均或期末存货分母']),
        'TURNOVER_AR_UNSPECIFIED': ('AR_TURNOVER', '原文尚未明确收入分子、应收范围及平均/期末余额分母',
                                    ['收入分子范围', '应收账款或票据范围', '平均或期末应收余额分母']),
        'TURNOVER_AP_UNSPECIFIED': ('AP_TURNOVER', '原文尚未明确成本或采购分子、应付范围及平均/期末余额分母',
                                    ['成本或采购分子范围', '应付账款或票据范围', '平均或期末应付余额分母']),
        'EXP_PERIOD_RATIO_UNSPECIFIED': ('PERIOD_EXPENSE_RATIO', '原文尚未明确费用聚合范围及收入分母，不能假定采用登记的四费用定义',
                                         ['费用聚合范围', '收入分母范围']),
    }
    if account not in methods:
        return None
    canonical, gap, required = methods[account]
    from .metrics import METRIC_SPECS
    spec = METRIC_SPECS.get(canonical)
    if spec is None:
        return None
    code = 'X3' if account == 'CURRENT_RATIO_UNSPECIFIED' else 'X2'
    reason = (f'已有登记公式 {spec.rule_id}，但登记公式不能证明原文采用该方法；'+gap+
              '；保留来源定义缺口，不按相近金额或双模型一致授权')
    return {'schema_version': 'source-definition-diagnostic.v1', 'code': code,
            'kind': 'source_method_unresolved', 'source_account': account,
            'canonical_account': canonical, 'registered_formula_available': True,
            'registered_rule_id': spec.rule_id, 'registered_definition_id': spec.definition_id,
            'source_definition_status': 'not_proved_by_source', 'required_source': required,
            'reason': reason}


def unresolved_source_definition_issue(claim) -> tuple[str, str] | None:
    """Agreement is not evidence for a broader/undefined original label."""
    if not semantic_review_required(claim):
        return None
    # This source identifier was saved by the numeric parser before binding.
    # It is independent of a model's canonical account and nearby fact value.
    account = claim.table_context['source_account_unresolved']
    diagnostic = source_definition_diagnostic(claim)
    if diagnostic:
        return diagnostic['code'], diagnostic['reason']
    if account in {'EPS_BROKER_DILUTED_UNSPECIFIED', 'CFO_PER_SHARE_BROKER_UNSPECIFIED', 'EQUITY_PER_SHARE_BROKER_UNSPECIFIED'}:
        from .source_method import validated_source_method
        proposal = validated_source_method(claim)
        if proposal and proposal.get('method_kind') == 'latest_total_share':
            return 'X2', ('原文明示最新总股本摊薄方法及股数快照日 '+proposal['snapshot_date']+
                          '；尚缺该独立券商公式的登记和覆盖快照日的官方普通股数证据，不能使用年末股本、加权平均股数或研报候选股数替代')
    if account == 'NET_MARGIN_PARENT_UNSPECIFIED':
        return 'X3', '原文明示归母净利润分子，但未完整明示分母；已有归母净利率登记公式不能证明原文采用该定义，不能换用NP净利率或按相近金额选择分母'
    if account.startswith('EXP_') and account.endswith('_RATIO_UNSPECIFIED'):
        return 'X2', '数值、单位已解析，但原文费用率缺少已授权的分母、费用聚合定义与公式；不能作为费用金额或普通同比核查'
    if account in SOURCE_REVIEW_FORMULAS:
        return 'X2', '数值与源量纲已解析，但该原文指标尚无授权输入、金融定义与公式；不能借用其他科目、比率或相近金额核查'
    if account in SOURCE_REVIEW_PER_SHARE:
        return 'X3', '数值与每股元单位已解析，但原文每股现金流或净资产未明确股数、分子聚合与基本/摊薄定义；不能借用EPS或期末股本事实'
    meanings = {
        'ROE_UNSPECIFIED':'原文仅称ROE/净资产收益率，未明确加权或其他净资产定义',
        'NET_MARGIN_UNSPECIFIED':'原文净利率/净利润率未明确分子与营业收入分母的完整定义',
        'EPS_UNSPECIFIED':'原文每股收益/EPS未明确基本、法定稀释或其他股数口径',
        'EPS_BROKER_DILUTED_UNSPECIFIED':'原文券商摊薄EPS未明确法定稀释或其他股数口径',
        'ASSET_CASH_UNSPECIFIED':'原文现金未明确货币资金、现金等价物或其他现金定义',
        'INVESTMENTS_UNSPECIFIED':'原文长期投资未明确包含的法定投资科目与聚合定义',
        'BROKER_CFO_FINANCE_UNSPECIFIED':'原文现金流量表的财务费用是券商模型调整项，未明确其组成；不能直接使用损益财务费用或法定现金流补充资料财务费用',
    }
    return 'X3', '数值、单位已解析，但语义定义未确定：'+meanings.get(account,'原文科目缺少可用的明确金融定义')+'；不能依相近事实金额或双模型一致授权映射'


def source_binding_issue(claim, *, strict=False) -> tuple[str, str] | None:
    """Reject semantic substitutions against a locked/explicit source proof."""
    source = claim.table_context if isinstance(claim.table_context, dict) else {}
    if not strict and not source.get('source_scope_required') and 'source_dimensions' not in source:
        # Earlier frozen CLI runs retain their recorded compatibility policy.
        return None
    semantic_issue = unresolved_source_definition_issue(claim)
    if semantic_issue:
        return semantic_issue
    try:
        proof = source_dimensions_from_text(claim)
    except ValueError:
        return 'X3', '冻结源维度与原文字符定位不一致，不能采用模型语义绑定'
    if proof.get('source_company_conflict') or proof.get('source_company_ambiguous'):
        return 'X3', '原文明示其他公司、并列公司或主体无法确定；不能将其归入当前任务公司的财务事实'
    if proof.get('percentage_point_direction_conflict'):
        return 'X3', '原文百分点的升降方向与明示正负号冲突；不能由模型改写方向'
    for field in ('company', 'account', 'period', 'relation', 'base_period'):
        expected = proof[field]
        observed = getattr(claim, field, '') or ''
        if expected and expected != observed:
            return 'X3', f'语义绑定与冻结原文的{field}定义冲突，不能换用其他科目、期间或关系的数字'
    for field in ('account', 'period', 'relation'):
        if not proof[field] or proof[field] == 'UNKNOWN':
            return 'X3', '原文未唯一明确科目、期间或关系；需要补充源材料，不能从可用事实反推绑定'
    from .source_quarter_quotation import validated_threshold_reference
    if proof['relation'] not in {'value', 'cagr'} and not proof['base_period'] and not validated_threshold_reference(claim):
        return 'X3', '原文没有明确可用基期，不能由模型猜测比较或增长率基期'
    return None
