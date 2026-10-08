"""Evaluation scope from source semantics; uncertainty stays in the denominator."""
from __future__ import annotations

import re


def _page_bounds(text: str, start: int, end: int, source_region) -> tuple[int, int] | None:
    if source_region:
        if (not isinstance(source_region, dict) or type(source_region.get('start')) is not int
                or type(source_region.get('end')) is not int
                or not 0 <= source_region['start'] <= start < end <= source_region['end'] <= len(text)):
            return None
        return source_region['start'], source_region['end']
    return 0, len(text)


def _local_subject_start(text: str, start: int, page_start: int) -> int:
    left = max(page_start, start-900)
    boundaries = list(re.finditer(r'[。；;！？!?]|\n\s*\n|\f', text[left:start]))
    if boundaries:
        left += boundaries[-1].end()
    # A source footer or a new caption cannot authorize subject inheritance
    # from the paragraph before it, even when a parser excerpt spans both.
    headings = list(re.finditer(
        r'(?m)^\s*(?:(?:图|表)\s*\d+\s*[:：、]|(?:(?:数据|资料)?来源)\s*[:：])',
        text[left:start]))
    if headings:
        left += headings[-1].end()
    return left


def _industry_ratio_lineage(text: str, start: int, end: int, claim, source_region=None) -> dict | None:
    """Carry a written industry-market subject into its local ratio clause.

    The numeric parser's excerpt may begin after a PDF line wrap or comma.
    Re-read the unchanged document's bounded sentence, and stop at any new
    issuer/counterparty subject. Neither a value match nor a remote heading
    can authorize this auxiliary classification.
    """
    if not re.search(r'[%％]', text[start:end]):
        return None
    bounds = _page_bounds(text, start, end, source_region)
    if bounds is None:
        return None
    page_start, page_end = bounds
    left = _local_subject_start(text, start, page_start)
    prefix = text[left:start]
    market = list(re.finditer(r'(?:全球|中国|国内|我国)[^，,。；;]{0,80}?市场|'
                             r'(?:市场|行业)\s*(?:规模|总规模|容量|份额|渗透率|增速|增长率)', prefix))
    if not market:
        return None
    subject = market[-1]
    continuation = prefix[subject.end():]
    target_clause = re.split(r'[,，]', continuation)[-1]
    suffix = re.split(r'[，,。；;！？!?]|\n\s*\n|\f', text[end:min(page_end,end+60)])[0]
    ratio = bool(re.search(r'占|比例|比重|份额|市占率', target_clause+suffix))
    # Admit an omitted market subject only to an explicitly written local
    # growth predicate. A new named subject, income measure or vague number
    # cannot borrow the preceding market's classification.
    growth = re.fullmatch(
        r'\s*(?:(?:其|该市场|该行业)\s*)?'
        r'(?:20\d{2}\s*(?:年(?:度)?)?\s*)?'
        r'(?:CAGR|(?:年均|年复合|复合|平均)?增长率|增速|'
        r'(?:同比|环比)(?:增长率|增速|增长|增加|下降|下滑|减少)?|'
        r'(?:增长|增加|下降|下滑|减少)(?:率)?)'
        r'\s*(?:(?:为|达|达到|约为|约|了|至|到|近|大约)\s*)*', target_clause, re.I)
    if not ratio and not growth:
        return None
    selected = claim.company if claim else ''
    if (re.search(r'(?:本公司|该公司|公司|发行人|集团|客户|企业用户|个人客户)', continuation)
            or selected and selected in continuation
            or growth and re.search(r'营收|营业收入|销售收入|利润|毛利|资产|现金流|销量|采购|供应商', continuation)):
        return None
    return {'kind': 'same_sentence_industry_growth' if growth and not ratio else 'same_sentence_industry_ratio',
            'subject': {'start': left+subject.start(), 'end': left+subject.end(), 'text': subject[0]},
            'continuation': {'start': left+subject.end(), 'end': start, 'text': continuation},
            'occurrence_span': [start, end],
            'page_bound': {'start':page_start,'end':page_end,'page':source_region.get('page') if source_region else None},
            'page_boundary_basis': 'host_source_region' if source_region else 'not_available_sentence_and_source_boundaries_only'}


def classify_occurrence(text: str, *, start: int, end: int, line: str, claim=None,
                        status: str, reason: str, modality_basis=None, source_region=None) -> tuple[str, dict]:
    """Return scope and a reviewable rule, without reading a fact or verdict."""
    context = claim.table_context if claim and isinstance(claim.table_context, dict) else {}
    source = {'schema_version': 'source-evaluation-scope.v1', 'occurrence_span': [start, end],
              'rule': 'unresolved_financial_retained', 'source_text': line}
    if status == 'rule_excluded':
        source.update(rule=reason)
        return 'rule_excluded', source
    if (modality_basis and modality_basis.get('modality') == 'forecast') or reason.startswith('forecast_'):
        source.update(rule=reason, modality_basis=modality_basis)
        return 'forecast', source
    if (modality_basis and modality_basis.get('modality') == 'hypothesis') or reason.startswith('hypothesis_'):
        source.update(rule=reason, modality_basis=modality_basis)
        return 'hypothesis', source
    if reason in {'valuation_table_row', 'valuation_prose_lineage', 'source_comparable_valuation_caption', 'policy_target_table'}:
        source.update(rule=reason, modality_basis=modality_basis)
        return 'hypothesis', source
    if context.get('dimension_kind') in {'customer', 'customer_group', 'business', 'purchase_mode', 'geography'}:
        source.update(rule='source_financial_dimension', dimension_source=context.get('dimension_source'))
        return 'issuer_historical_financial', source
    # Use the target clause only; a remote industry paragraph cannot remove
    # issuer figures later in a financial table or same sentence.
    bounds = _page_bounds(text, start, end, source_region)
    if bounds is None:
        return 'issuer_historical_financial', source
    page_start, page_end = bounds
    left = _local_subject_start(text, start, page_start)
    prefix = re.split(r'[，,]', text[left:start])[-1]
    compact = re.sub(r'\s+', '', prefix)
    # Raw fallback blocks can cover a whole paragraph. Limit subject evidence
    # to the target sentence; decimal points remain part of financial values.
    sentence_suffix = re.split(r'[，,。；;！？!?]|\n\s*\n|\f', text[start:page_end])[0]
    target_sentence = re.sub(r'\s+', '', prefix+sentence_suffix)
    industry = re.search(r'(?:市场|行业)(?:规模|总规模|容量|份额|渗透率|增速|增长率)|(?:全球|中国|国内|我国)(?:[^，。；;]{0,30})(?:市场|行业)', compact)
    explicit_issuer_finance = bool(claim and claim.account not in {'','UNKNOWN'} and
                                  (re.search(r'本公司|该公司|发行人|公司',compact) or claim.company and claim.company in compact))
    if not industry and not explicit_issuer_finance:
        industry = re.search(r'(?:全球|中国|国内|我国)[^。；;]{0,50}(?:智能算力|算力规模|数据中心|AI服务器|人工智能服务器)|市占率', target_sentence)
    if not industry and not re.search(r'本公司|该公司|发行人|公司', target_sentence):
        industry = re.search(r'智能算力规模|智能算力同比|数据中心用于推理', target_sentence)
    if industry:
        source.update(rule='explicit_industry_measure', source_text=prefix)
        return 'industry_historical', source
    industry_lineage = _industry_ratio_lineage(text, start, end, claim, source_region)
    if industry_lineage:
        source.update(rule='source_industry_growth_continuation' if industry_lineage['kind']=='same_sentence_industry_growth'
                      else 'source_industry_ratio_continuation', industry_lineage=industry_lineage)
        return 'industry_historical', source
    if reason in {'duration_quantity', 'technical_quantity', 'technical_unit_identity'}:
        source.update(rule=reason)
        return 'issuer_operating', source
    # A measured data label within an explicitly unit-labelled operating
    # chart is still an occurrence, with its own status; never a page-axis
    # exclusion and never a corporate finance assertion by proximity.
    if context.get('source_operating_measure'):
        source.update(rule='source_operating_measure', measure=context['source_operating_measure'])
        return 'issuer_operating', source
    if re.search(r'参会费用|参会注册|参会者|票价|门票|报名时间|算力(?:资源|服务)?能力|CPU核时|GPU卡时|计算核心|计算能力', compact):
        source.update(rule='explicit_operating_measure', source_text=prefix)
        return 'issuer_operating', source
    dimensions = context.get('source_dimensions', {})
    if dimensions.get('source_company_conflict'):
        source.update(rule='explicit_other_issuer_subject', subjects=dimensions.get('source_company_subjects'))
        return 'external_entity', source
    # A missing definition, unit or scope never authorizes reclassification.
    return 'issuer_historical_financial', source
