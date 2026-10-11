"""A complete, source-addressed ledger; model selection never deletes a row."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .audit import digest
from .mentions import CONTEXT_TERMS, NOISE_TERMS, VALUE_RE, extract_mentions, has_financial_context
from .models import AtomicClaim
from .parser import extract_text

TERMINAL = {"judged", "abstained", "out_of_scope", "rule_excluded"}


def partition_document(path: Path, *, cancel_event=None, progress_callback=None,
                       deadline_monotonic=None, material_profile='') -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    from .parser import _control_options, _parse_checkpoint
    control = _control_options(cancel_event, progress_callback, deadline_monotonic)
    if material_profile == 'smart':
        from .smart.ingestion import partition_smart
        return partition_smart(path, **control)
    pages = extract_text(path, **control)
    text_parts, regions, blocks = [], [], []
    offset = 0
    for page in pages:
        _parse_checkpoint(**control)
        text = page["text"]
        page_id = f"page-{digest([str(path.name), page['page'], text])[:20]}"
        regions.append({"id": page_id, "page": page["page"], "start": offset,
                        "end": offset + len(text), "characters": len(text),
                        "text_layer": bool(text.strip()),
                        "vector_lines": page.get('vector_lines', []),
                        "vector_rectangles": page.get('vector_rectangles', []),
                        "lines": [{**line, "start": offset + line['start'],
                                   "end": offset + line['end']} for line in page.get('lines', [])
                                  if line.get('start') is not None and line.get('end') is not None]})
        source_blocks = page.get("blocks") or [{"text": line, "bbox": None} for line in text.splitlines() if line.strip()]
        search_from = 0
        for index, block in enumerate(source_blocks):
            _parse_checkpoint(**control)
            snippet = block["text"]
            found = text.find(snippet, search_from)
            if found < 0:
                found = text.find(snippet)
            if found >= 0:
                search_from = found + len(snippet)
            blocks.append({"id": f"block-{digest([page_id, index, snippet])[:20]}",
                           "page_id": page_id, "page": page["page"], "text": snippet,
                           "start": offset + found if found >= 0 else None,
                           "end": offset + found + len(snippet) if found >= 0 else None,
                           "bbox": block.get("bbox"), "table_candidate": bool(re.search(r"\d.*\s+.*\d", snippet))})
        text_parts.append(text)
        offset += len(text) + 1
    return "\n".join(text_parts), regions, blocks


@dataclass
class CoverageLedger:
    pages: list[dict]
    blocks: list[dict]
    entries: list[dict] = field(default_factory=list)

    @classmethod
    def build(cls, text: str, pages: list[dict], blocks: list[dict], company: str, period: str, *, trusted_claims=None):
        claims = extract_mentions(text, company, period, preserve_occurrences=True)
        by_span = {(c.start, c.end): c for c in claims if c.claim_type != 'comparison'}
        from .table_mentions import extract_table_mentions
        table_claims = extract_table_mentions(text, pages, blocks, company, period)
        # Table headers provide authoritative units/periods. Prefer those
        # bindings over a prose heuristic for the very same occurrence.
        for table_claim in table_claims:
            overlap = next((c for c in claims if c.start == table_claim.start), None)
            if overlap:
                claims.remove(overlap)
            if table_claim.modality == 'fact':
                claims.append(table_claim)
        smart_profile = trusted_claims is not None
        if smart_profile:
            selected_starts = {c.start for c in trusted_claims} | {fragment['start'] for c in trusted_claims for fragment in c.table_context.get('source_numeric_fragments', [])}
            # The smart parser owns the executable claim set. Raw scanning
            # remains exhaustive below without adding heuristic duplicates.
            claims = list(trusted_claims)
            table_claims = [c for c in table_claims if c.start not in selected_starts] + list(trusted_claims)
        claims.sort(key=lambda c: (c.start, c.end))
        by_span = {(c.start, c.end): c for c in claims if c.claim_type != 'comparison'}
        table_by_start = {c.start: c for c in table_claims}
        continuation_by_start = {fragment['start']: c for c in trusted_claims or [] for fragment in c.table_context.get('source_numeric_fragments', [])}
        table_headers = {(header['start'], header['end']) for c in table_claims
                         for header in c.table_context.get('headers', [])}
        source_scopes = _policy_table_regions(text, pages)
        from .layout_noise import chart_layout_exclusions, chart_measure_scopes
        chart_exclusions = chart_layout_exclusions(text, pages)
        chart_scopes = chart_measure_scopes(text, pages)
        from .source_modality import prose_modality_exclusions
        prose_exclusions = prose_modality_exclusions(text, pages)
        syntax_exclusions = _source_occurrence_rules(text, pages)
        ledger = cls(list(pages), list(blocks))
        for match in VALUE_RE.finditer(text):
            start, end = match.span()
            line_start = text.rfind("\n", 0, start) + 1
            # VALUE_RE may consume trailing whitespace across a newline.
            # Classify the physical line containing the number itself, not
            # the next line (e.g. the second number in ``7 / 44``).
            line_end = text.find("\n", match.end('num'))
            line = text[line_start:line_end if line_end >= 0 else len(text)]
            claim = continuation_by_start.get(start) or by_span.get((start, end)) or next((c for c in claims if c.start == start and c.claim_type != 'comparison'), None)
            table = table_by_start.get(start)
            prose_exclusion = prose_exclusions.get(match.start('num'))
            modality_basis = None
            status, reason = "pending", "financial_table_or_unresolved"
            region = next((p for p in pages if p['start'] <= start < p['end']), {})
            block = next((b for b in blocks if b['start'] is not None and b['start'] <= start < b['end']), {})
            # Layout identifiers receive explicit source rules. In particular,
            # a number on a dotted TOC line is not a second financial assertion;
            # dates/page ratios/header years also never consume Agent budget.
            excluded = _source_identifier_reason(text, match, line_start, line, table_headers)
            syntax_rule = syntax_exclusions.get(match.start('num'))
            if not excluded and syntax_rule and syntax_rule['status']=='rule_excluded':
                excluded = syntax_rule['reason']
            chart_exclusion = chart_exclusions.get(match.start('num')) if not table else None
            if not excluded and chart_exclusion:
                excluded = chart_exclusion['reason']
            source_scope = next((scope for scope in source_scopes if scope['start'] <= start < scope['end']), None)
            if claim and claim.table_context.get('smart_cid'):
                # Smart extraction retains forecast/market checks as distinct,
                # source-addressed work; its verifier controls external truth.
                status, reason = 'pending', 'smart_source_occurrence'
                modality_basis = {'schema_version': 'smart-source-modality.v1', 'modality': claim.modality,
                                  'category': claim.table_context['smart_claim'].get('category')}
            elif excluded:
                if claim:
                    claims.remove(claim)
                    by_span.pop((claim.start, claim.end), None)
                claim = None
                status, reason = 'rule_excluded', excluded
            elif source_scope:
                if claim:
                    claims.remove(claim)
                    by_span.pop((claim.start, claim.end), None)
                claim = None
                status, reason = 'out_of_scope', source_scope['reason']
            elif syntax_rule and syntax_rule['status']=='out_of_scope':
                if claim:
                    claims.remove(claim)
                    by_span.pop((claim.start,claim.end),None)
                claim = None
                status, reason = 'out_of_scope', syntax_rule['reason']
            elif table and table.modality != 'fact':
                claim = None
                status, reason = 'out_of_scope', ('valuation_table_row' if table.modality=='opinion'
                                                  else 'forecast_table_column')
                modality_basis = {'schema_version': 'source-table-modality.v1',
                                  'modality': table.modality, 'occurrence_span': [start, end],
                                  'headers': table.table_context.get('headers', []),
                                  'period': table.period,
                                  'column_index': table.table_context.get('column_index'),
                                  'row_label': table.table_context.get('row_label'),
                                  'source_spans': table.table_context.get('source_spans', [])}
            elif prose_exclusion and not table:
                if claim:
                    claims.remove(claim)
                    by_span.pop((claim.start, claim.end), None)
                claim = None
                status, reason = 'out_of_scope', prose_exclusion['reason']
                modality_basis = prose_exclusion['basis']
            elif claim:
                reason = "deterministic_financial_mention"
            elif (any(term in line for term in NOISE_TERMS) or
                  (match.end('num') < len(text) and text[match.end('num')] in "年月日") or
                  (not match['unit'] and re.match(r'\s*\d+[.、)）]\s*', line)
                   and start == line_start + len(line)-len(line.lstrip()))):
                status, reason = "rule_excluded", "identifier_date_or_page"
            elif not has_financial_context(line):
                # Isolated numeric cells close to a financial header remain
                # visible until a deterministic table parser can bind them.
                nearby = text[max(0, line_start - 140):start]
                if not has_financial_context(nearby):
                    status, reason = "rule_excluded", "no_financial_context"
            if smart_profile and status == 'pending' and claim is None:
                reason = 'smart_source_parse_gap'
            if status == 'pending' and claim is None and not smart_profile:
                # A source candidate that deterministic parsing cannot bind
                # is still executable work. X6 can mean incomplete source
                # dimensions/structure, not necessarily a damaged number.
                # Never invent a candidate value, subject, period, unit or
                # caliber just to make the candidate available to an Agent.
                left, right = line_start, max(end, line_end if line_end >= 0 else len(text))
                if (block.get('start') is not None and block.get('end') is not None and
                        block['start'] <= start and match.end('num') <= block['end']):
                    left, right = block['start'], max(block['end'], end)
                source = text[left:right]
                claim = AtomicClaim(
                    claim_id=f'unresolved-{digest([start, end, match[0]])[:20]}',
                    text=source, start=start, end=end, account='UNKNOWN',
                    candidate_value=None, numeric_text=match[0],
                    parse_status='unresolved', binding_status='unknown',
                    source_numeric_start=start-left,
                    table_context={'source_parse_reason': 'financial_context_unresolved',
                                   'source_page': region.get('page'),
                                   'source_block_id': block.get('id'),
                                   'source_start': left, 'source_end': right},
                )
                claims.append(claim)
                reason = 'source_addressed_parse_failure'
            ledger.entries.append({"id": claim.claim_id if claim and not smart_profile else f"mention-{digest([start, end, match[0]])[:20]}",
                                   "start": start, "end": end, "page_id": region.get('id'), "page": region.get('page'),
                                   "block_id": block.get('id'), "text": match[0],
                                   "status": status, "reason": reason, "claim_id": claim.claim_id if claim else None})
            from .source_classification import classify_occurrence
            scope, classification = classify_occurrence(text, start=start, end=end, line=line,
                                                         claim=claim, status=status, reason=reason,
                                                         modality_basis=modality_basis, source_region=region)
            chart_scope = chart_scopes.get(match.start('num')) if not table else None
            if chart_scope and status not in {'rule_excluded','out_of_scope'}:
                scope, classification = chart_scope['evaluation_scope'], chart_scope['classification_basis']
            ledger.entries[-1].update(evaluation_scope=scope, classification_basis=classification)
            if claim:
                # Host-created, frozen alongside the source dimensions; an
                # Agent cannot choose an easier evaluation denominator.
                claim.table_context.update(evaluation_scope=scope, classification_basis=classification)
            if chart_exclusion and reason == chart_exclusion['reason']:
                ledger.entries[-1]['exclusion_basis'] = chart_exclusion['basis']
            if syntax_rule and reason == syntax_rule['reason']:
                ledger.entries[-1]['exclusion_basis'] = syntax_rule['basis']
            if modality_basis:
                ledger.entries[-1]['modality_basis'] = modality_basis
        # Comparison claims contain no candidate financial number and still
        # need their own source-addressed ledger row.
        for claim in claims:
            if not any(e['claim_id'] == claim.claim_id for e in ledger.entries):
                ledger.entries.append({"id": claim.claim_id, "claim_id": claim.claim_id,
                                       "start": claim.start, "end": claim.end, "text": claim.text,
                                       "status": "pending", "reason": "comparison",
                                       "evaluation_scope": claim.table_context.get('evaluation_scope', 'issuer_historical_financial'),
                                       "classification_basis": {'schema_version':'source-evaluation-scope.v1', 'rule':'source_comparison'}})
        claims.sort(key=lambda c: (c.start, c.end))
        return ledger, claims

    def complete(self, claim_id: str, result: dict) -> None:
        for entry in self.entries:
            if entry.get('claim_id') == claim_id:
                if entry.get('reason') == 'native_binding_not_submitted':
                    entry.update(result_status=result['status'], abstention_code=result.get('abstention_code'))
                    continue
                entry.update(status="abstained" if result['status'] == 'abstained' else "judged",
                             result_status=result['status'], abstention_code=result.get('abstention_code'))

    def mark_pending(self, claim_id: str, reason: str):
        for entry in self.entries:
            if entry.get('claim_id') == claim_id:
                entry.update(status='pending', reason=reason)

    def to_dict(self) -> dict:
        counts = {key: sum(e['status'] == key for e in self.entries) for key in sorted(TERMINAL | {'pending'})}
        financial = [e for e in self.entries if e['status'] not in {'rule_excluded', 'out_of_scope'}]
        return {"schema_version": 1, "pages": self.pages, "blocks": self.blocks, "mentions": self.entries,
                "counts": counts, "partial": bool(counts['pending']),
                "financial_candidates": len(financial), "processed": len(financial) - counts['pending'],
                "coverage": (len(financial) - counts['pending']) / len(financial) if financial else None,
                "recall": "unknown_requires_independent_gold"}


def _source_occurrence_rules(text: str, pages: list[dict]) -> dict[int, dict]:
    """Source-proved identifiers and nonfinancial dimensions, with lineage.

    Financial monetary/percent values are not discarded by proximity. A
    duration or technical quantity has its own explicit suffix; ordinal
    columns require both a source header and source PDF column geometry.
    Every excluded number remains visible in the occurrence ledger.
    """
    spans = []
    period = re.compile(r'(?<!\d)20\d{2}\s*(?:年\s*)?[-－–—~～至到]\s*20\d{2}\s*(?:年(?:度|末|上半年|下半年|第?[一二三四]季度)?)?'
                        r'(?!\d|\s*(?:百万元|亿元|万元|元|[%％]|股|美元|港元|欧元|USD|HKD|EUR))'
                        r'|(?<!\d)20\d{2}\s*(?:H[12]|Q[1-4]|9M|[AEY]|年(?:度|末|上半年|下半年|第?[一二三四]季度)?)', re.I)
    month_range = re.compile(r'(?<!\d)20\d{2}\s*年\s*(?:[1-9]|1[0-2])\s*[-－–—~～至到]\s*(?:[1-9]|1[0-2])\s*月(?![\u4e00-\u9fff]*(?:币|元|股))')
    technical_edition = re.compile(r'(?<![A-Za-z0-9_])HPC\s*China\s*20\d{2}(?!\d|\s*(?:百万元|亿元|万元|元|[%％]|股))', re.I)
    technical_identifier = re.compile(r'(?<![A-Za-z0-9])(?:2A(?![A-Za-z0-9])(?=\s*(?:或|实例|型号|服务器|CPU|算力))'
                                      r'|(?:A100|A800|H100|H800|RTX\s*4090)(?![A-Za-z0-9])(?=\s*(?:型号|GPU|显卡|服务器)))', re.I)
    duration = re.compile(r'(?<![\d.])\d+(?:\.\d+)?\s*(?:个\s*)?(?:年|个月|月|天|日|小时|分钟)(?![\u4e00-\u9fff]*(?:币|元|股))')
    technical = re.compile(r'(?<![\d.])\d[\d,，]*(?:\.\d+)?\s*(?:百\s*万|千\s*万|亿|万)?\s*'
                           r'(?:台(?:服务器|设备)?|颗(?:芯片|CPU)?|张(?:[^\n，。；;]{0,28}(?:GPU|显卡))|个(?:计算核心|CPU|GPU|核心)|计算核心|'
                           r'套\s*(?:存储设备|服务器|计算设备)|CPU\s*核时|GPU\s*(?:卡|小时)|[EPT]?\s*FLOPS|TFLOPS|GFLOPS|GHz|MHz|GB|TB|PB|纳米|nm)', re.I)
    formula = re.compile(r'(?:\d+(?:\.\d+)?(?:\s*\^\s*\d+)?\s*[EPT]?\s*FLOPS\s*(?:[=≈]\s*)?){2,}', re.I)
    for pattern, status, reason in [(period,'rule_excluded','declared_period'),
                                     (month_range,'rule_excluded','declared_month_range'),
                                     (technical_edition,'rule_excluded','technical_event_edition'),
                                     (technical_identifier,'rule_excluded','technical_model_identifier'),
                                     (duration,'out_of_scope','duration_quantity'),
                                     (technical,'out_of_scope','technical_quantity'),
                                     (formula,'out_of_scope','technical_unit_identity')]:
        for match in pattern.finditer(text):
            spans.append({'start':match.start(),'end':match.end(),'text':match[0],
                          'status':status,'reason':reason})
    rating_definition = re.compile(r'(?:评级\s*说明\s*[:：]\s*)?(?:买入|增持|中性|减持)\s*指\s*预计\s*相对\s*'
                                   r'(?:强于|弱于|跑赢|跑输)\s*市场表现\s*\d+(?:\.\d+)?\s*[%％]\s*(?:以上|以下|以内)?')
    for match in rating_definition.finditer(text):
        spans.append({'start': match.start(), 'end': match.end(), 'text': match[0],
                      'status': 'out_of_scope', 'reason': 'analyst_rating_definition'})
    # A numbered prose item needs a real closing enumeration marker and a
    # clause/line boundary. A monetary amount with adjacent punctuation does
    # not become an ordinal merely because it is small.
    numbered_item = re.compile(r'(?:^|[。；;\n])\s*(?P<ordinal>\d{1,3})[）)](?=\s*[A-Za-z\u4e00-\u9fff])',re.M)
    for match in numbered_item.finditer(text):
        spans.append({'start':match.start('ordinal'),'end':match.end(),'text':text[match.start('ordinal'):match.end()],
                      'status':'rule_excluded','reason':'source_numbered_prose_item'})
    valuation_caption = re.compile(r'(?m)^\s*(?:表\s*\d+\s*[:：]\s*)?可比公司\s*'
        r'(?:P\s*/?\s*[ESB]|EV\s*/\s*EBITDA)\s*(?:20\d{2}[AEY]?\s*)?'
        r'(?:为|约为|达|达到|对应)\s*[-+－−]?\d+(?:\.\d+)?\s*(?:[Xx]|倍)(?=\s*(?:[。；;\n]|$))',re.I)
    for match in valuation_caption.finditer(text):
        spans.append({'start':match.start(),'end':match.end(),'text':match[0],
                      'status':'out_of_scope','reason':'source_comparable_valuation_caption'})
    for page in pages:
        lines = [line for line in page.get('lines',[]) if line.get('bbox') and line.get('start') is not None]
        for header in lines:
            if re.sub(r'\s+','',header['text']) not in {'序号','序次'}:
                continue
            box = header['bbox']
            stop = min((line['bbox'][1] for line in lines if line['bbox'][1] > box[3]
                        and re.match(r'\s*(?:图|表)\s*\d+\s*[:：]|\s*(?:数据|资料)来源\s*[:：]',line['text'])), default=float('inf'))
            for line in lines:
                value = line['text'].strip()
                cell = line['bbox']
                center = (cell[0]+cell[2])/2
                if (re.fullmatch(r'\d{1,3}',value) and box[0]-2 <= center <= box[2]+2
                        and box[3] <= cell[1] < stop):
                    spans.append({'start':line['start'],'end':line['end'],'text':line['text'],
                                  'status':'rule_excluded','reason':'table_ordinal_column',
                                  'header':{'text':header['text'],'start':header['start'],'end':header['end'],'bbox':box},
                                  'bbox':cell,'page':page.get('page')})
    output = {}
    for match in VALUE_RE.finditer(text):
        for span in spans:
            if span['start'] <= match.start('num') and match.end('num') <= span['end']:
                if span['reason']=='declared_period' and match['unit']:
                    continue  # 2023亿元 is an amount, not a year.
                output.setdefault(match.start('num'), {'status':span['status'],'reason':span['reason'],
                    'basis':{'schema_version':'source-occurrence-rule.v1', 'source_span':span,
                             'occurrence_span':[match.start('num'),match.end()]}})
                break
    return output


def _source_identifier_reason(text, match, line_start: int, line: str,
                              table_headers: set[tuple[int, int]]) -> str | None:
    """Classify source syntax, never amounts based on their plausibility."""
    start, end = match.span('num')
    if re.search(r'(?:[.．…·]{4,}|(?:\.\s*){6,})\s*\d{1,4}\s*$', line):
        return 'table_of_contents_entry'
    if re.fullmatch(r'\s*\d{1,4}\s*/\s*\d{1,4}\s*', line):
        return 'page_number_ratio'
    footer = re.search(r'(?:请务必[^\n]*(?:信息披露|法律声明)|(?:信息披露|法律声明))\s*'
                       r'(?P<page_number>\d{1,4}\s*/\s*\d{1,4})\s*$', line)
    if footer and footer.start('page_number') <= start - line_start and end - line_start <= footer.end('page_number'):
        return 'source_footer_page_number_ratio'
    if any(left <= start and end <= right for left, right in table_headers):
        return 'financial_table_period_header'
    relative_start = start - line_start
    for security_code in re.finditer(r'(?<!\d)\d{6}\.(?:SH|SZ|BJ|HK)(?![A-Za-z0-9])|'
                                    r'(?:证券代码|股票代码)\s*[:：]\s*\d{6}(?!\d)', line, re.I):
        if security_code.start() <= relative_start and end - line_start <= security_code.end():
            return 'source_security_code'
    # Keep exact declared periods, including both halves of 2023H1/Q4,
    # out of the numeric candidate ledger. A bare amount equal to 2023 is
    # deliberately not inferred to be a year.
    for period in re.finditer(r'(?<!\d)20\d{2}\s*(?:H[12]|Q[1-4]|9M|[AEY]|年(?:度|末|半年度|上半年|下半年|前三季度)?)'
                              r'|(?<!\d)20\d{2}\s*(?:-|－|–|—|~|～|至|到)\s*20\d{2}(?:\s*年)?'
                              r'(?!\d|\s*(?:百万元|亿元|万元|元|[%％]|股|美元|港元|欧元|USD|HKD|EUR))', line, re.I):
        if period.start() <= relative_start and end-line_start <= period.end():
            # Monetary and rate units take precedence over a year-looking
            # value (e.g. 2023亿元 is a genuine candidate amount).
            if not match['unit']:
                return 'declared_period'
    for figure in re.finditer(r'(?<![\w])(?:图|表)\s*\d+(?:[-－]\d+)?\s*[:：]', line):
        if figure.start() <= relative_start < figure.end():
            return 'figure_or_table_identifier'
    section = re.match(r'\s*\d+(?:[.．]\d+)*\s*[、．.]\s*(?=[^\d\s])', line)
    if section and relative_start < section.end():
        return 'section_identifier'
    # Exact date spans only: another amount on that line stays a candidate.
    for date in re.finditer(r'(?<!\d)20\d{2}\s*(?:年\s*\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?'
                            r'|[-/.]\s*\d{1,2}\s*[-/.]\s*\d{1,2})(?!\d)', line):
        if date.start() <= relative_start and end-line_start <= date.end():
            return 'calendar_date'
    return None


def _policy_table_regions(text: str, pages: list[dict]) -> list[dict]:
    """Identify explicit policy target tables, retaining every ledger row.

    Both a policy/national-plan caption and a policy source citation must
    exist in the same table on the same page. A generic ``指标`` caption or
    an industry word near an issuer financial table is insufficient.
    """
    output = []
    for page in pages:
        page_text = text[page['start']:page['end']]
        captions = list(re.finditer(r'(?m)^\s*表\s*\d+\s*[:：]\s*([^\n]+)', page_text))
        for index, caption in enumerate(captions):
            if not re.search(r'国家规划|国家目标|政策目标|政策规划|行动计划|发展规划', caption[1]):
                continue
            stop = captions[index+1].start() if index+1 < len(captions) else len(page_text)
            segment = page_text[caption.end():stop]
            citation = re.search(r'(?m)^\s*(?:数据|资料)来源\s*[:：]\s*[^\n]*(?:行动计划|发展规划|政策|规划纲要)[^\n]*', segment)
            if not citation:
                continue
            output.append({'start': page['start']+caption.start(),
                           'end': page['start']+caption.end()+citation.end(),
                           'reason': 'policy_target_table'})
    return output


def mask_financial_numbers(text: str, *, preserve_bare_years: bool = True) -> str:
    """Hide candidates, including neighbouring table cells, but keep years."""
    explicit_periods = []
    if not preserve_bare_years:
        # Blind readers must not infer that an isolated amount equal to 20xx
        # is a year. Only explicit periods, dates and year ranges survive.
        boundary = r'(?!\d|百万元|亿元|万元|元|[%％]|倍|股)'
        month, day = r'(?:0?[1-9]|1[0-2])', r'(?:0?[1-9]|[12]\d|3[01])'
        patterns = [
            r'(?<!\d)20\d{2}\s*年(?:度|末|第?[一二三四1-4]季度|上半年|下半年|前三季度)?',
            r'(?<!\d)20\d{2}(?:A|E|Y|H[12]|Q[1-4]|[1-4]Q|9M)(?![A-Za-z0-9_])',
            rf'(?<!\d)20\d{{2}}年{month}月(?:{day}日)?' + boundary,
            rf'(?<!\d)20\d{{2}}(?:-{month}(?:-{day})?|/{month}(?:/{day})?|\.{month}(?:\.{day})?)' + boundary,
            r'(?<!\d)20\d{2}\s*(?:-|－|–|—|~|～|至|到)\s*20\d{2}(?:年)?' + boundary,
        ]
        explicit_periods = [match.span() for pattern in patterns for match in re.finditer(pattern, text, re.I)]
    def replace(match):
        raw = match[0]
        number_end = match.end('num')
        if (preserve_bare_years and re.fullmatch(r"20\d{2}", match['num']) and not match['unit'] and
                (number_end == len(text) or text[number_end] in "年YHQAE-/. \t\r\n")):
            return raw
        if not match['unit'] and any(start <= match.start('num') and number_end <= end for start, end in explicit_periods):
            return raw
        trailing = raw[len(raw.rstrip()):] if not match['unit'] else ''
        return "[数值已屏蔽]" + (match['unit'] or '') + trailing
    return VALUE_RE.sub(replace, text)
