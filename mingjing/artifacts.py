"""Render offline value and source-definition evidence from frozen PDF bytes."""
from __future__ import annotations

from collections import defaultdict
import hashlib
import os
from pathlib import Path

from .config import validate_path
from .evidence import fact_evidence_references, original_pdf_evidence, parse_page_evidence, sha256_file
from .file_io import io_path


def _registered_hashes(resources):
    result = {}
    for resource in resources or []:
        if not isinstance(resource, dict) or not resource.get('path') or not resource.get('sha256'):
            continue
        path = str(Path(resource['path']).resolve())
        previous = result.get(path)
        if previous and previous != resource['sha256']:
            raise ValueError('Frozen source registry has conflicting document hashes')
        result[path] = resource['sha256']
    return result


def _pdf_snapshot(path, expected, prefix):
    maximum = 256 * 1024 * 1024
    with io_path(path).open('rb') as stream:
        if os.fstat(stream.fileno()).st_size > maximum:
            raise ValueError('Evidence PDF exceeds the bounded preview size')
        raw = stream.read(maximum+1)
    if len(raw) > maximum:
        raise ValueError('Evidence PDF exceeds the bounded preview size')
    checksum = hashlib.sha256(raw).hexdigest()
    if not checksum.startswith(prefix) or expected and checksum != expected:
        raise ValueError('Evidence PDF changed; document SHA-256 does not match its frozen source')
    return raw, checksum


def export_evidence(facts, results, out, *, source_resources=None):
    """Render every requested value/table/scope reference, once per identity.

    Headers remain source-definition references, not fabricated financial
    facts. Original block text and coordinates must reproduce the identifier.
    A supplied frozen registry also enforces complete document SHA-256 values.
    Changed/invented registered PDF evidence fails before publishing images.
    Text-only references retain their normal non-PDF capability semantics.
    """
    wanted = {identifier for result in results for identifier in result.get('evidence', [])}
    if not wanted:
        return {}
    lookup, roles = defaultdict(list), defaultdict(set)
    for fact in facts:
        for identifier, kinds in fact_evidence_references(fact).items():
            if identifier in wanted:
                lookup[identifier].append(fact)
                roles[identifier].update(kinds)
    try:
        import fitz
    except ImportError:
        return {}
    output = validate_path(out, kind='dir', follow_symlinks=False)
    output.mkdir(parents=True, exist_ok=True)
    root = validate_path(output/'evidence', allowed_roots=[str(output)], kind='dir', follow_symlinks=False)
    root.mkdir(exist_ok=True)
    registered = _registered_hashes(source_resources)
    grouped = defaultdict(list)
    for identifier in sorted(wanted):
        parsed = parse_page_evidence(identifier)
        if parsed is None:
            continue
        paths = set()
        for fact in lookup[identifier]:
            if fact.source_path and Path(fact.source_path).suffix.lower() == '.pdf':
                original = Path(fact.source_path)
                checked = validate_path(original, allowed_roots=[str(original.parent)], kind='file',
                                        must_exist=True, follow_symlinks=False)
                paths.add(str(checked))
        paths = sorted(paths)
        if not paths:
            continue
        source = paths[0]
        if source_resources is not None and source not in registered:
            raise ValueError('Requested evidence source is outside the frozen resource registry')
        grouped[source].append((identifier, parsed))
    staged, resources = [], {}
    for filename, requests in grouped.items():
        original = Path(filename)
        path = validate_path(original, allowed_roots=[str(original.parent)], kind='file',
                             must_exist=True, follow_symlinks=False)
        source_bytes, checksum = _pdf_snapshot(path, registered.get(filename), requests[0][1]['document_sha12'])
        with fitz.open(stream=source_bytes, filetype='pdf') as document:
            if document.needs_pass:
                raise ValueError('Frozen evidence PDF requires a password')
            page_sources = {}
            for identifier, parsed in requests:
                number = parsed['page']
                if parsed['document_sha12'] != checksum[:12] or number > len(document):
                    raise ValueError('Evidence identity does not belong to its frozen PDF page')
                page = document[number-1]
                if number not in page_sources:
                    page_sources[number] = original_pdf_evidence(page, checksum)
                # Row/column suffixes label table cells; they do not change
                # the underlying source block used to prove the identity.
                base = identifier.split(':r', 1)[0]
                original_span = page_sources[number].get(base)
                if original_span is None:
                    raise ValueError('Evidence hash or coordinates do not match an original PDF block, line or cell')
                rectangle = fitz.Rect(original_span['bbox'])
                if rectangle.is_empty or not rectangle.is_valid or not page.rect.contains(rectangle):
                    raise ValueError('Evidence rectangle is outside its original PDF page')
                annotation = page.add_rect_annot(rectangle)
                annotation.update()
                try:
                    raw = page.get_pixmap(matrix=fitz.Matrix(1.2, 1.2), alpha=False).tobytes('png')
                finally:
                    page.delete_annot(annotation)
                name = hashlib.sha256(identifier.encode('utf-8')).hexdigest()[:20]+'.png'
                resources[identifier] = {
                    'evidence_id': identifier, 'image': 'evidence/'+name,
                    'image_sha256': hashlib.sha256(raw).hexdigest(), 'image_bytes': len(raw),
                    'page': number, 'bbox': original_span['bbox'], 'document_sha256': checksum,
                    'source_path': str(path), 'quote_text': original_span['quote_text'],
                    'quote_sha256': hashlib.sha256(original_span['quote_text'].encode('utf-8')).hexdigest(),
                    'reference_kinds': sorted(roles[identifier]), 'image_available': True}
                staged.append((root/name, raw))
        # The rendered bytes belong to the hash-checked immutable snapshot.
        # Refuse to publish them as current evidence if the path changed.
        validate_path(original, allowed_roots=[str(original.parent)], kind='file', must_exist=True, follow_symlinks=False)
        if sha256_file(path) != checksum:
            raise ValueError('Evidence PDF changed during artifact generation')
    from .source_gap_evidence import _atomic_image
    for target, raw in staged:
        _atomic_image(target, raw, root)
    return resources
