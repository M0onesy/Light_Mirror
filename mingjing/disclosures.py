"""Prepare dated, source-addressed accounting disclosures for the pure kernel.

This build layer reads only issuer documents selected by the trusted host. A
section title is a navigation anchor; it is never itself a positive event.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .audit import digest
from .evidence import make_evidence_id, sha256_file


_TOPICS = ('会计政策变更', '会计估计变更', '前期差错更正')
_NEXT_SECTION = re.compile(r'^(?:[（(][一二三四五六七八九十]+[）)]|[一二三四五六七八九十]+、|\d+[、．.])')


def prepare_accounting_disclosures(blocks, *, company, document_sha256, publish_date, period='', source_path=''):
    """Keep small accounting sections with exact, independently cited blocks.

    Only the immediately following page may continue a section. A new topic,
    numbered section closes it; topic-specific negative disclosures remain
    visible to the kernel's event gate. No report
    amounts or model labels are used to infer an event or its financial impact.
    """
    if not company or not re.fullmatch(r'[a-f0-9]{64}', document_sha256 or ''):
        return []
    from datetime import date
    try:
        date.fromisoformat(publish_date)
    except (ValueError, TypeError):
        return []
    rows = []
    for index, block in enumerate(blocks):
        text = re.sub(r'\s+', '', str(block.get('text', '')))
        label = next((topic for topic in _TOPICS if topic in text), None)
        # Tables and cash/equity reconciliation rows are not narrative anchors.
        if not label or len(text) > 100 or '内容和原因' in text or text.startswith(('加：', '减：')):
            continue
        selected = [block]
        size = len(block['text'])
        first_page = block.get('page')
        for following in blocks[index + 1:index + 65]:
            if (type(first_page) is not int or type(following.get('page')) is not int or
                    not first_page <= following['page'] <= first_page + 1):
                break
            compact = re.sub(r'\s+', '', str(following.get('text', '')))
            if not compact or compact.isdigit():
                continue
            if any(topic in compact and topic != label for topic in _TOPICS):
                break
            if '内容和原因' in compact or '受影响的报表项目' in compact:
                break
            # Subparagraphs such as (1) explain the current policy; a new
            # standalone numbered heading ends it, except a policy citation.
            new_heading = _NEXT_SECTION.match(compact) and len(compact) < 75
            if new_heading and not any(word in compact for word in ('执行', '采用', '解释', '准则', '会计处理')):
                break
            if size + len(following['text']) > 8000:
                break
            selected.append(following)
            size += len(following['text'])
        # An event identifies the disclosed action, not the impact table.
        # Preserve short wrapped lines too; dropping them changes a sentence.
        source_text = '\n'.join(row['text'] for row in selected)
        proofs = []
        for row in selected:
            text = row['text']
            proofs.append({'evidence_id': make_evidence_id(document_sha256, row['page'], text, row.get('bbox')),
                           'document_sha256': document_sha256, 'source_path': source_path,
                           'page': row['page'], 'bbox': row.get('bbox'), 'block_id': row['id'],
                           'text': text, 'text_sha256': hashlib.sha256(text.encode('utf-8')).hexdigest()})
        authority = re.search(r'《([^》]+)》', source_text)
        rows.append({'company': company, 'publish_date': publish_date, 'period': period,
                     'document_sha256': document_sha256, 'text': source_text,
                     'topic': label, 'evidence_id': proofs[0]['evidence_id'],
                     'evidence': [row['evidence_id'] for row in proofs], 'source_evidence': proofs,
                     'event_identity': digest([company, document_sha256, label,
                                              authority[1] if authority else digest(source_text)]),
                     'provenance': 'dated_locked_issuer_section'})
    return rows


def disclosures_from_facts(facts, *, control=None):
    """CLI preparation for the finite documents already referenced by facts."""
    from .coverage import partition_document
    sources = {}
    for fact in facts:
        if fact.source_path and Path(fact.source_path).suffix.lower() in {'.pdf', '.md', '.txt'}:
            sources.setdefault((fact.company, str(Path(fact.source_path).resolve())), []).append(fact)
    disclosures = []
    for (company, filename), rows in sorted(sources.items()):
        dates = {fact.publish_date for fact in rows if fact.publish_date}
        if len(dates) != 1:
            continue
        path = Path(filename)
        if not path.is_file():
            continue
        _, _, blocks = partition_document(path, **(control or {}))
        periods = sorted({fact.period for fact in rows if fact.period})
        disclosures.extend(prepare_accounting_disclosures(blocks, company=company,
            document_sha256=sha256_file(path), publish_date=next(iter(dates)),
            period=periods[-1] if periods else '', source_path=str(path)))
    return disclosures


def export_disclosure_evidence(disclosures, out):
    """Render disclosure citations without manufacturing numeric Fact objects."""
    try:
        import fitz
    except ImportError:
        return {}
    root = Path(out) / 'evidence'
    root.mkdir(exist_ok=True)
    resources = {}
    for disclosure in disclosures:
        for proof in disclosure.get('source_evidence', []):
            identifier = proof['evidence_id']
            if identifier in resources or not proof.get('source_path') or not proof.get('bbox'):
                continue
            path = Path(proof['source_path'])
            if path.suffix.lower() != '.pdf' or not path.is_file() or sha256_file(path) != proof['document_sha256']:
                continue
            if (hashlib.sha256(proof['text'].encode('utf-8')).hexdigest() != proof['text_sha256'] or
                    make_evidence_id(proof['document_sha256'], proof['page'], proof['text'], proof['bbox']) != identifier):
                continue
            try:
                with fitz.open(path) as document:
                    page = document[proof['page'] - 1]
                    rect = fitz.Rect(proof['bbox'])
                    actual = any(row[4] == proof['text'] and all(abs(a - b) < .01 for a, b in zip(row[:4], proof['bbox']))
                                 for row in page.get_text('blocks'))
                    if not actual or rect.is_empty or not page.rect.contains(rect):
                        continue
                    page.add_rect_annot(rect).update()
                    filename = hashlib.sha256(identifier.encode('utf-8')).hexdigest()[:20] + '.png'
                    page.get_pixmap(matrix=fitz.Matrix(1.2, 1.2), alpha=False).save(root / filename)
                    resources[identifier] = {'image': 'evidence/' + filename, 'page': proof['page'],
                        'document_sha256': proof['document_sha256'], 'source_path': str(path)}
            except (RuntimeError, ValueError, IndexError):
                continue
    return resources
