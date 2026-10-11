"""Host-owned smart parsing of frozen bytes, never browser-supplied facts."""
from __future__ import annotations

import json
import re
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from ..audit import digest
from ..evidence import make_evidence_id
from ..models import AtomicClaim, Evidence
from ..parser import _indexed_files, _parse_checkpoint
from .claims import build_report_model
from .common import unit_scale
from .detect import DocMeta, detect, same_company, short_name
from .docio import load_document, sha256_path
from .tables import extract_filing_facts, facts_from_json

MANIFEST = 'smart-materials.json'


def report_model_from_dict(raw):
    """Restore typed deterministic inputs after an immutable JSON archive."""
    from .claims import Claim, ReportModel, ReportTable
    tables = []
    for table in raw['tables']:
        rows = [{**row, 'cells': [(cell[0], Decimal(str(cell[1])) if cell[1] is not None else None)
                                  for cell in row['cells']]} for row in table['rows']]
        tables.append(ReportTable(**{**table, 'rows': rows}))
    return ReportModel(DocMeta(**raw['meta']), [Claim(**c) for c in raw['claims']], tables, raw['market'], raw['stats'])


def material_manifest(source: Path) -> dict:
    root = source if source.is_dir() else source.parent
    path = root / MANIFEST
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema') != 'mingjing-smart-materials.v2':
        raise ValueError('智能材料清单版本无效')
    return data


def source_meta(source: Path, *, company='', period='') -> DocMeta:
    manifest = material_manifest(source)
    item = next((r for r in manifest.get('documents', []) if r['file'] == source.name), None)
    if item:
        if sha256_path(source) != item['sha256'] or source.stat().st_size != item['size_bytes']:
            raise ValueError('冻结上传材料的字节已改变')
        raw = item.get('metadata', {})
        return DocMeta(**{k: raw[k] for k in DocMeta().to_dict() if k in raw})
    doc = load_document(source)
    return detect(doc, hints={'company': company, 'period': period})


def ingest_facts(source: Path, company: str, period: str, *, task_subject=None, **control) -> tuple[list, list[dict]]:
    """Parse the selected allowlist directly; smart KB caches have no authority."""
    # The frozen allowlist is selected by the upload host. Issuer admission
    # happens after reading original names and security codes below; a legacy
    # name-only index filter cannot erase sources before that proof is read.
    indexed = _indexed_files(source, '', **control)
    selected = indexed if indexed is not None else [(source, company, period, '')] if source.is_file() else []
    facts, evidence = [], []
    for path, issuer, selected_period, published in selected:
        _parse_checkpoint(**control, phase='新版确定性表格建库', document=path.name)
        meta = source_meta(path, company=issuer or company, period=selected_period or period)
        meta.period = selected_period or meta.period or period
        meta.report_date = published or meta.report_date
        if path.suffix.lower() == '.json':
            # Explicit uploaded structured source stays subject to canonical cache admission.
            from ..parser import load_or_extract_facts
            extracted = load_or_extract_facts(path, company=company, period=meta.period, **control)
            facts.extend(extracted)
            continue
        doc = load_document(path)
        # Selection metadata cannot rename an issuer explicitly printed in
        # the frozen source heading.
        detected = detect(doc)
        cover = '\n'.join(p.flow for p in doc.pages[:2])
        title = re.search(r'(?m)^\s*([\u4e00-\u9fffA-Z*（）()]{2,40})\s*20\d{2}\s*年\s*(?:年度|半年度|第[一二三四]季度)\s*报告', cover)
        source_issuer = title[1] if title else detected.company
        selected_issuer = company or issuer or meta.company
        subject = task_subject.to_dict() if hasattr(task_subject, 'to_dict') else task_subject or {}
        subject_names = {subject.get('company'), subject.get('company_full')}
        code_proof = bool(selected_issuer in subject_names and subject.get('stock_code') and detected.stock_code
                          and re.fullmatch(r'\d{6}', subject['stock_code'])
                          and re.fullmatch(r'\d{6}', detected.stock_code)
                          # Search aliases are not original issuer proof. A
                          # new/old code bridge requires a separately frozen
                          # mapping; none is registered by this ingestion API.
                          and subject['stock_code'] == detected.stock_code)
        source_names = {source_issuer, detected.company, detected.company_full,
                        short_name(detected.company_full or source_issuer)}
        if source_issuer and selected_issuer and selected_issuer not in source_names:
            if not code_proof:
                raise ValueError('原文发行人与所选任务公司冲突: ' + source_issuer + ' / ' + selected_issuer)
        meta.company = selected_issuer or source_issuer
        _parse_checkpoint(**control)
        extracted, _ = extract_filing_facts(doc, meta)
        rows = [row for page in doc.pages for row in page.rows]
        by_evidence = {make_evidence_id(doc.sha256, row.page, row.text, row.bbox): row for row in rows}
        for fact in extracted:
            row = next((by_evidence[e] for e in fact.evidence if e in by_evidence), None)
            page_rows = [r for r in rows if row and r.page == row.page and r.index <= row.index]
            scope_rows = [r for r in page_rows if re.search(r'合并(?:资产负债表|利润表|现金流量表)|母公司(?:资产负债表|利润表|现金流量表)', r.text)]
            scope_proof = tuple(make_evidence_id(doc.sha256, r.page, r.text, r.bbox) for r in scope_rows[-1:])
            table_rows = [r for r in page_rows if fact.table_label and fact.table_label.replace(' ', '') in r.text.replace(' ', '')]
            table_proof = tuple(make_evidence_id(doc.sha256, r.page, r.text, r.bbox) for r in table_rows[-1:]) or fact.evidence
            currency = 'CNY'
            units = ' '.join(r.text for r in page_rows[-30:])
            for token, code in [('美元', 'USD'), ('港元', 'HKD'), ('欧元', 'EUR')]:
                if re.search(r'(?:币种|货币|单位)[：: ]*' + token, units):
                    currency = code
            fact = replace(fact, currency=currency, source_table_id='smart-table-' + digest([doc.sha256, fact.table_label, fact.table_label and row.page if row else 0])[:24],
                           source_table_evidence=table_proof, source_scope_evidence=scope_proof,
                           source_row_index=min(10000, row.index + 1) if row else 0)
            facts.append(fact)
        for row in rows:
            identifier = make_evidence_id(doc.sha256, row.page, row.text, row.bbox)
            proof = Evidence(identifier, path.name, row.page, tuple(row.bbox) if row.bbox else None,
                             row.text, row.index, document_sha256=doc.sha256).to_dict()
            if code_proof:
                proof['issuer_resolution'] = {'source_company': source_issuer, 'resolved_company': selected_issuer,
                    'source_stock_code': detected.stock_code, 'subject_stock_code': subject['stock_code'],
                    'basis': 'source_declared_security_code'}
            evidence.append(proof)
    return facts, evidence


def partition_smart(path: Path, **control):
    doc = load_document(path)
    parts, pages, blocks, offset = [], [], [], 0
    for page in doc.pages:
        _parse_checkpoint(**control)
        # The deterministic row view keeps table cell order and coordinates together.
        text = '\n'.join(row.text for row in page.rows) or page.flow
        page_id = 'page-' + digest([doc.sha256, page.number])[:20]
        start = offset
        lines = []
        cell_spans = {}
        for row in page.rows:
            snippet = row.text
            left = offset + text.find(snippet, max(0, (lines[-1]['end'] - offset) if lines else 0))
            if left < offset:
                continue
            block = {'id': 'block-' + digest([doc.sha256, page.number, row.index, snippet])[:20],
                     'page_id': page_id, 'page': page.number, 'text': snippet, 'start': left,
                     'end': left + len(snippet), 'bbox': row.bbox, 'table_candidate': len(row.cells) > 1,
                     'row_index': row.index}
            blocks.append(block)
            position = 0
            cell_spans[block['id']] = []
            for cell in row.cells:
                cell_spans[block['id']].append({'start': position, 'end': position + len(cell.text),
                                              'x0': cell.x0, 'x1': cell.x1})
                position += len(cell.text) + 2
            lines.append({'text': snippet, 'start': left, 'end': left + len(snippet), 'bbox': row.bbox})
        if page.geometry_lines:
            from .docio import tidy
            physical, consumed = [], set()
            for source in page.geometry_lines:
                snippet = tidy(source['text']).strip()
                box = source['bbox']
                for block in blocks:
                    if block['page'] != page.number or not block.get('bbox'):
                        continue
                    row_box = block['bbox']
                    if box[0] < row_box[0] - 1 or box[2] > row_box[2] + 1 or box[1] < row_box[1] - 2 or box[3] > row_box[3] + 2:
                        continue
                    positions = []
                    token = bool(re.fullmatch(r'[+−-]?\d[\d,]*(?:\.\d+)?[%％]?', snippet))
                    pattern = (r'(?<![\d.,])' + re.escape(snippet) + r'(?![\d.,])') if token else re.escape(snippet)
                    for match in re.finditer(pattern, block['text']):
                        left, right = match.span()
                        if block['start'] + left in consumed:
                            continue
                        cells = [cell for cell in cell_spans[block['id']] if cell['start'] < right and cell['end'] > left]
                        if cells and min(c['x0'] for c in cells) - 2 <= box[0] and box[2] <= max(c['x1'] for c in cells) + 2:
                            positions.append(left)
                    if len(positions) != 1:
                        continue
                    left = positions[0]
                    absolute = block['start'] + left
                    physical.append({'text': snippet, 'start': absolute, 'end': absolute + len(snippet), 'bbox': box})
                    consumed.add(absolute)
                    break
            if physical:
                lines = physical
        pages.append({'id': page_id, 'page': page.number, 'start': start, 'end': start + len(text),
                      'characters': len(text), 'text_layer': bool(text.strip()), 'lines': lines,
                      'vector_lines': page.vector_lines, 'vector_rectangles': page.vector_rectangles})
        parts.append(text)
        offset += len(text) + 1
    return '\n'.join(parts), pages, blocks


def smart_claims(path, text, pages, blocks, company, period, facts, policy, metadata=None):
    """Map source-addressed smart claims; leave unmatched occurrences in the ledger."""
    doc = load_document(path)
    meta = (DocMeta(**{k: v for k, v in metadata.items() if k in DocMeta().to_dict()}) if metadata else
            source_meta(path, company=company, period=period))
    meta.company = company or meta.company
    segments = {f.dimension_id for f in facts if f.dimension_kind != 'total' and f.dimension_id}
    model = build_report_model(doc, meta, segments)
    mapped, used, unmapped = [], set(), []
    for item in model.claims:
        number = re.search(r'[+−-]?\d[\d,，]*(?:\.\d+)?', item.num_text)
        if number is None:
            unmapped.append(item.cid)
            continue
        raw_number = number[0]
        candidates = []
        for block in blocks:
            if block.get('page') != item.page or block.get('start') is None:
                continue
            for match in re.finditer(r'(?<![\d.,，])' + re.escape(raw_number) + r'(?![\d.,，])', block['text']):
                start = block['start'] + match.start()
                if start in used:
                    continue
                score = sum(token in block['text'].replace(' ', '') for token in (item.row_label, item.metric_text) if token)
                if item.bbox and block.get('bbox') == item.bbox:
                    score += 3
                candidates.append((score, start, match.end() - match.start(), block))
        if not candidates:
            # A PDF may wrap inside a decimal token. Match only intervening
            # source whitespace and retain every original fragment/offset.
            page = next((p for p in pages if p['page'] == item.page), None)
            if page:
                segment = text[page['start']:page['end']]
                pattern = r'(?<![\d.,，])' + r'\s*'.join(re.escape(char) for char in raw_number) + r'(?![\d.,，])'
                for match in re.finditer(pattern, segment):
                    left = page['start'] + match.start()
                    right = page['start'] + match.end()
                    if left in used:
                        continue
                    selected = [b for b in blocks if b.get('start') is not None and b['start'] < right and b['end'] > left]
                    if len(selected) < 2:
                        continue
                    block = {'id': 'wrapped-' + digest([doc.sha256, left, right])[:20], 'page': item.page,
                             'start': selected[0]['start'], 'end': selected[-1]['end'],
                             'text': text[selected[0]['start']:selected[-1]['end']],
                             'source_numeric_fragments': [{'start': max(left, b['start']), 'end': min(right, b['end']),
                                                           'text': text[max(left, b['start']):min(right, b['end'])]} for b in selected]}
                    score = sum(token in block['text'].replace(' ', '') for token in (item.row_label, item.metric_text) if token)
                    candidates.append((score, left, right - left, block))
            if not candidates:
                unmapped.append(item.cid)
                continue
        _, start, length, block = max(candidates, key=lambda x: (x[0], -x[1]))
        used.add(start)
        cid = 'smart-' + digest([doc.sha256, start, item.cid])[:20]
        relation = 'pp_difference' if item.relation == 'pp' else item.relation
        candidate = str(Decimal(item.value) / unit_scale(item.unit))
        scope = getattr(item, 'scope', '') or ('母公司' if '母公司' in item.text else '合并')
        binding = {'company': company, 'account': item.metric or 'UNKNOWN', 'period': item.period,
                   'scope': scope, 'relation': relation, 'base_period': item.base_period or ''}
        context = {'smart_cid': item.cid, 'smart_claim': item.to_dict(), 'smart_binding': binding,
                   'account_hint': item.metric, 'row_label': item.row_label or item.metric_text,
                   'source_page': item.page, 'source_block_id': block['id'], 'source_start': block['start'],
                   'source_end': block['end'], 'validation_policy': policy, 'source_scope_required': policy == 'strict',
                   'smart_dimensions': {'currency': getattr(item, 'currency', 'CNY'), 'definition_id': getattr(item, 'definition_id', ''),
                                        'dimension_kind': 'business' if item.segment else 'total', 'dimension_id': item.segment},
                   'scope_assumption': not bool(getattr(item, 'scope', '')), 'parser': doc.parser_version}
        if block.get('source_numeric_fragments'):
            context.update(source_numeric_fragments=block['source_numeric_fragments'], normalization='source_whitespace_only_numeric_join')
        mapped.append(AtomicClaim(cid, block['text'], start, start + length, company=company,
                                  account=item.metric or 'UNKNOWN', period=item.period, candidate_value=candidate,
                                  unit=item.unit, caliber_hint=(scope,), relation=relation, base_period=item.base_period or None,
                                  display_decimals=item.decimals, numeric_text=text[start:start + length],
                                  modality=item.modality, parse_status='parsed' if item.metric else 'unresolved',
                                  table_context=context, source_numeric_start=start - block['start']))
    if unmapped:
        raise ValueError('智能声明无法定位到冻结原文 occurrence: ' + ', '.join(unmapped))
    return model, mapped


class SmartKernel:
    """One trusted callback used by native tools and final host judgement."""
    def __init__(self, facts, model, policy, reports, texts, claim_map):
        self.facts, self.model, self.policy = facts, model, policy
        self.reports, self.texts, self.claim_map = reports, texts, claim_map

    def verify(self, claim):
        from .claims import Claim
        from .verify import verify_model
        source = self.claim_map.get(claim.claim_id)
        if not source:
            return None
        item = Claim(**{k: v for k, v in source.items() if k in Claim.__dataclass_fields__})
        item.metric, item.period, item.relation = claim.account, claim.period, ('pp' if claim.relation == 'pp_difference' else claim.relation)
        item.base_period = claim.base_period or ''
        # A/B agreement on an inferred default cannot turn it into an explicit
        # source declaration or make the strict policy silently pass.
        item.scope = ('' if claim.table_context.get('scope_assumption') else
                      claim.caliber_hint[0] if claim.caliber_hint else getattr(item, 'scope', ''))
        # A/B binds semantics only. The number remains the host's source occurrence.
        model = replace(self.model, claims=[item])
        result = verify_model(self.facts, model, policy=self.policy, texts=self.texts, doc_index=self.reports)[0].to_dict()
        status = {'unverifiable': 'abstained', 'skipped': 'abstained'}.get(result['status'], result['status'])
        canonical = {'claim_id': claim.claim_id, 'status': status, 'error_type': result.get('error_type') or None,
                'observed': source['value'], 'expected': result.get('expected'), 'display_value': result.get('expected_display'),
                'formula': result.get('formula'), 'evidence': result.get('evidence', []), 'basis': result.get('basis'),
                'reason': result.get('reason'), 'correction': result.get('correction'), 'operands': result.get('operands', []),
                'abstention_code': result.get('abstain_code') or ('X6' if result['status'] == 'skipped' else None),
                'advisories': result.get('advisories', []), 'rule_ids': result.get('rule_ids') or ['SMART-registered-kernel'],
                'verification_basis': result.get('basis'), 'source_dimensions': claim.table_context.get('smart_binding'),
                'smart_result': result, 'validation_policy': self.policy}
        for key in ('candidate_interval', 'expected_interval', 'candidate_precision', 'source_precision',
                    'precision_note', 'verification_basis', 'establishes_exact_value', 'rules'):
            canonical[key] = result.get(key)
        canonical['claim_interval'] = result.get('candidate_interval')
        return canonical
