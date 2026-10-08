"""Render registered, unresolved source quotations without creating Facts.

Only host-locked report resources supply paths. Every evidence identifier is
reconstructed from an actual PDF text block, so a caller cannot nominate an
arbitrary page rectangle or invented quotation. Images remain source views;
they do not authorise financial scopes, values, formulae or verdicts.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
from pathlib import Path
import re

from .config import validate_path
from .evidence import make_evidence_id, sha256_file


_SHA = re.compile(r'[a-f0-9]{64}\Z')
_GAP = re.compile(r'sg-[a-f0-9]{24}\Z')
_EVIDENCE = re.compile(
    r'(?P<sha>[a-f0-9]{12}):p(?P<page>[1-9]\d{0,6}):'
    r'(?P<coords>-?\d+,-?\d+,-?\d+,-?\d+):(?P<span>[a-f0-9]{8})\Z')


def merge_source_gap_evidence(registry, quotations):
    """Add quotation views without changing an existing Fact's authority."""
    for identifier, quote in quotations.items():
        if identifier not in registry:
            registry[identifier] = quote
            continue
        original = registry[identifier]
        if (original.get('document_sha256') != quote['document_sha256']
                or original.get('page') != quote['page']
                or Path(original.get('source_path', '')).resolve() != Path(quote['source_path']).resolve()):
            raise ValueError('Source quotation conflicts with existing evidence identity')
        # A PDF block may support a known total and an unresolved dimension.
        # Its fact and quotation uses keep their distinct authority semantics.
        original['unresolved_source_gap_ids'] = sorted(set(original.get('unresolved_source_gap_ids', []))
                                                       | set(quote['source_gap_ids']))
    return registry


def _registered_reports(resources):
    if isinstance(resources, dict):
        if any(not isinstance(value, dict) or value.get('id', key) != key for key,value in resources.items()):
            raise ValueError('Source report registry keys must match their resource identities')
        resources = [{'id': key, **value} for key, value in resources.items()]
    if not isinstance(resources, (list, tuple)):
        raise ValueError('Source evidence requires a frozen report resource registry')
    reports = {}
    for resource in resources:
        if not isinstance(resource, dict):
            raise ValueError('Frozen source resource must be an object')
        identifier = resource.get('id')
        if not isinstance(identifier, str) or not identifier.startswith('report-'):
            continue
        if identifier in reports:
            raise ValueError('Source report identities must be unique')
        sha = resource.get('sha256')
        if not isinstance(sha, str) or not _SHA.fullmatch(sha):
            raise ValueError('Source report requires its complete frozen SHA-256')
        filename = resource.get('path')
        if not isinstance(filename, (str, Path)) or not str(filename):
            raise ValueError('Source report requires a host-registered path')
        path = Path(filename)
        reports[identifier] = {'id': identifier, 'sha256': sha, 'path': path}
    return reports


def _requested_evidence(gaps, reports):
    if not isinstance(gaps, (list, tuple)):
        raise ValueError('Source gap evidence requires structured source records')
    wanted = {}
    gap_ids = set()
    for gap in gaps:
        if not isinstance(gap, dict) or not _GAP.fullmatch(str(gap.get('id', ''))):
            raise ValueError('Source quotation requires a registered source-gap identity')
        if gap['id'] in gap_ids:
            raise ValueError('Source-gap identities must be unique')
        gap_ids.add(gap['id'])
        if gap.get('eligible_as_fact') is True or gap.get('eligible_for_verification') is True:
            raise ValueError('Unresolved source quotation cannot be promoted to a Fact')
        sha = gap.get('doc_sha256')
        if not isinstance(sha, str) or not _SHA.fullmatch(sha):
            raise ValueError('Source quotation requires its complete document hash')
        matches = [report for report in reports.values() if report['sha256'] == sha
                   and (not gap.get('report_id') or report['id'] == gap['report_id'])]
        if len(matches) != 1:
            raise ValueError('Source quotation must identify exactly one frozen report')
        report = matches[0]
        if report['path'].suffix.lower() != '.pdf':
            raise ValueError('Source gap page evidence requires a registered PDF')
        identifiers = gap.get('evidence')
        if not isinstance(identifiers, list) or not identifiers:
            raise ValueError('Source quotation requires original page evidence')
        for identifier in identifiers:
            match = _EVIDENCE.fullmatch(identifier) if isinstance(identifier, str) else None
            if match is None or match['sha'] != sha[:12]:
                raise ValueError('Source evidence identity does not belong to its report')
            existing = wanted.get(identifier)
            if existing and (existing['report']['id'] != report['id'] or existing['report']['sha256'] != sha):
                raise ValueError('Source evidence identity is ambiguous across frozen reports')
            if existing:
                existing['source_gap_ids'].append(gap['id'])
            else:
                wanted[identifier] = {'report': report, 'page': int(match['page']),
                                      'source_gap_ids': [gap['id']]}
    return wanted


def _atomic_image(path, raw, root):
    # Both paths are checked before every write; existing links cannot redirect
    # an otherwise safe hash-derived filename outside the owned evidence root.
    validate_path(root, allowed_roots=[str(root.parent)], must_exist=True, kind='dir', follow_symlinks=False)
    target = validate_path(path, allowed_roots=[str(root)], kind='file', follow_symlinks=False)
    temporary = validate_path(path.with_suffix('.png.tmp'), allowed_roots=[str(root)],
                              kind='file', follow_symlinks=False)
    try:
        temporary.write_bytes(raw)
        validate_path(target, allowed_roots=[str(root)], kind='file', follow_symlinks=False)
        temporary.replace(target)
    finally:
        if temporary.exists():
            validate_path(temporary, allowed_roots=[str(root)], kind='file', follow_symlinks=False)
            temporary.unlink()


def export_source_gap_evidence(gaps, resources, out):
    """Return page-image metadata for unresolved, host-registered sources.

    Invalid identities, changed documents, invented block hashes, escaped
    paths and invalid pages fail explicitly. Without the optional PDF runtime,
    valid requests return an empty capability result, as other evidence exports
    do. Source records and the report registry are never modified.
    """
    reports = _registered_reports(resources)
    wanted = _requested_evidence(gaps, reports)
    if not wanted:
        return {}
    output = validate_path(out, kind='dir', follow_symlinks=False)
    output.mkdir(parents=True, exist_ok=True)
    root = validate_path(output/'evidence', allowed_roots=[str(output)], kind='dir', follow_symlinks=False)
    root.mkdir(exist_ok=True)
    try:
        import fitz
    except ImportError:
        return {}
    grouped = defaultdict(list)
    for identifier, request in wanted.items():
        grouped[request['report']['id']].append((identifier, request))
    result = {}
    for report_id, requests in grouped.items():
        report = reports[report_id]
        original = report['path']
        path = validate_path(original, allowed_roots=[str(original.parent)], must_exist=True,
                             kind='file', follow_symlinks=False)
        source_bytes = path.read_bytes()
        if hashlib.sha256(source_bytes).hexdigest() != report['sha256']:
            raise ValueError('Source report changed; evidence export refused')
        staged = []
        try:
            with fitz.open(stream=source_bytes, filetype='pdf') as document:
                if document.needs_pass:
                    raise ValueError('Source report cannot be read without a password')
                for identifier, request in requests:
                    number = request['page']
                    if number > len(document):
                        raise ValueError('Source evidence page is outside its frozen PDF')
                    page = document[number-1]
                    blocks = [block for block in page.get_text('blocks') if block[6] == 0 and
                              make_evidence_id(report['sha256'], number, block[4].strip(), block[:4]) == identifier]
                    if len(blocks) != 1:
                        raise ValueError('Source evidence hash or coordinates do not match one original PDF block')
                    block = blocks[0]
                    rect = fitz.Rect(block[:4])
                    if rect.is_empty or not rect.is_valid or not page.rect.contains(rect):
                        raise ValueError('Source evidence rectangle is outside its original page')
                    annotation = page.add_rect_annot(rect)
                    annotation.update()
                    try:
                        raw = page.get_pixmap(matrix=fitz.Matrix(1.2, 1.2), alpha=False).tobytes('png')
                    finally:
                        page.delete_annot(annotation)
                    name = hashlib.sha256(identifier.encode('utf-8')).hexdigest()[:20]+'.png'
                    quote = block[4].strip()
                    metadata = {'evidence_id': identifier, 'image': 'evidence/'+name,
                                'image_sha256': hashlib.sha256(raw).hexdigest(), 'image_bytes': len(raw),
                                'page': number, 'bbox': list(block[:4]),
                                'document_sha256': report['sha256'], 'doc_sha256': report['sha256'],
                                'source_path': str(path), 'report_id': report_id,
                                'source_gap_ids': sorted(set(request['source_gap_ids'])),
                                'quote_text': quote, 'quote_sha256': hashlib.sha256(quote.encode('utf-8')).hexdigest(),
                                'reference_status': 'source_definition_unresolved', 'image_available': True,
                                'eligible_as_fact': False, 'authorizes_judgement': False,
                                'title': f'{path.name} · 第{number}页 · 原文定义待确认，未作数字核实'}
                    staged.append((name, raw, identifier, metadata))
        except (RuntimeError, IndexError) as exc:
            raise ValueError('Source report cannot produce its registered page evidence') from exc
        # The byte snapshot was independently hash-checked. Check the live path
        # again before publishing images, including its link/junction ancestry.
        validate_path(original, allowed_roots=[str(original.parent)], must_exist=True,
                      kind='file', follow_symlinks=False)
        if sha256_file(path) != report['sha256']:
            raise ValueError('Source report changed during evidence export')
        for name, raw, identifier, metadata in staged:
            _atomic_image(root/name, raw, root)
            result[identifier] = metadata
    return result
