from __future__ import annotations

import hashlib
import re
from pathlib import Path
from .file_io import io_path


PAGE_EVIDENCE = re.compile(
    r'(?P<sha>[a-f0-9]{12}):p(?P<page>[1-9]\d*):'
    r'(?P<coords>-?\d+,-?\d+,-?\d+,-?\d+):(?P<span>[a-f0-9]{8})'
    r'(?::r(?P<row>\d+)c(?P<column>\d+))?\Z')


def fact_evidence_references(fact) -> dict[str, tuple[str, ...]]:
    """Enumerate value, table and scope provenance without promoting headers.

    A table header can be required for a financial decision even when it is
    not itself an amount-bearing Fact. Keep those authority roles explicit.
    Accept the frozen JSON form and the in-memory immutable Fact form.
    """
    result: dict[str, list[str]] = {}
    for field, role in (('evidence', 'fact_value'), ('source_table_evidence', 'source_table'),
                        ('source_scope_evidence', 'source_scope')):
        values = fact.get(field, ()) if isinstance(fact, dict) else getattr(fact, field, ())
        if not isinstance(values, (list, tuple)):
            raise ValueError('Fact evidence references must be a sequence of identities')
        for identifier in values:
            if not isinstance(identifier, str) or not identifier:
                raise ValueError('Fact evidence identity must be nonempty text')
            roles = result.setdefault(identifier, [])
            if role not in roles:
                roles.append(role)
    return {identifier: tuple(roles) for identifier, roles in result.items()}


def parse_page_evidence(identifier: str) -> dict | None:
    """Read a registered page identity; caller-supplied paths are not accepted."""
    match = PAGE_EVIDENCE.fullmatch(identifier) if isinstance(identifier, str) else None
    if match is None:
        return None
    return {'document_sha12': match['sha'], 'page': int(match['page']),
            'bbox': [int(value) for value in match['coords'].split(',')],
            'span_hash': match['span'], 'row': int(match['row']) if match['row'] else None,
            'column': int(match['column']) if match['column'] else None}


def original_pdf_evidence(page, document_sha256: str) -> dict[str, dict]:
    """Index physical blocks, lines and cells from the same original page.

    Source IDs may address a whole statement row or a separately disclosed
    cell. Requiring a whole-block match would reject legitimate cell evidence;
    an arbitrary rectangle with matching nearby text is never sufficient.
    """
    spans = []
    for block in page.get_text('blocks'):
        if len(block) >= 7 and block[6] == 0:
            spans.append((block[4].strip(), block[:4]))
    for block in page.get_text('dict').get('blocks', []):
        if block.get('type') != 0:
            continue
        for line in block.get('lines', []):
            spans.append((''.join(span['text'] for span in line['spans']).strip(), line['bbox']))
            spans.extend((span['text'].strip(), span['bbox']) for span in line['spans'])
    result = {}
    for text, bbox in spans:
        if text:
            identifier = make_evidence_id(document_sha256, page.number+1, text, bbox)
            result.setdefault(identifier, {'quote_text': text, 'bbox': list(bbox)})
    return result


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with io_path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def span_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def make_evidence_id(doc_sha: str, page: int | None, text: str, bbox=None, row=None, col=None) -> str:
    if page is None:
        base = f"{doc_sha[:12]}:text:{span_hash(text)}"
    else:
        coords = "0,0,0,0" if bbox is None else ",".join(str(int(round(v))) for v in bbox)
        base = f"{doc_sha[:12]}:p{page}:{coords}:{span_hash(text)}"
    if row is not None and col is not None:
        base += f":r{row}c{col}"
    return base


def evidence_from_span(document: str, doc_sha: str, text: str, start: int, end: int, page: int | None = None):
    snippet = text[max(0, start - 100): min(len(text), end + 100)]
    return {
        "evidence_id": make_evidence_id(doc_sha, page, snippet),
        "document": document,
        "page": page,
        "bbox": None,
        "text": snippet,
        "document_sha256": doc_sha,
    }

