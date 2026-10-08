"""Host-owned local PDF reparsing, published for a separate task version.

Packets never update an active FactStore. Their financial values can only
come from the deterministic window parser over a hash-locked source PDF.
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import date
from pathlib import Path

from .audit import _module_code_data, digest, kernel_fingerprint
from .evidence import sha256_file
from .facts import FactStore, _version_rank
from .models import AtomicClaim, Fact
from .file_io import atomic_write_text, io_path

SCHEMA = 'mingjing-reparse-packet.v1'
PACKET_ID = re.compile(r'^rp-[a-f0-9]{64}$')
GENERATION_FIELDS = {'schema_version', 'run_id', 'run_manifest_sha256', 'results_sha256', 'resource_lock', 'request_sha256'}
_PUBLISH_LOCK = threading.RLock()


def _checkpoint(cancel_event=None, deadline_monotonic=None):
    from .parser import _parse_checkpoint
    _parse_checkpoint(cancel_event=cancel_event, deadline_monotonic=deadline_monotonic)


def _generation(value):
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) != GENERATION_FIELDS:
        raise ValueError('Reparse source generation requires its complete frozen-parent fields')
    if any(not isinstance(item, str) or len(item) > 512 for item in value.values()):
        raise ValueError('Invalid reparse source generation')
    if value['schema_version'] != 'reparse-source-generation.v1' or not value['run_id']:
        raise ValueError('Invalid reparse source generation identity')
    for key in ('run_manifest_sha256', 'results_sha256', 'request_sha256'):
        if not re.fullmatch(r'[a-f0-9]{64}', value[key]):
            raise ValueError('Invalid frozen-parent generation hash')
    if value['resource_lock'] and not re.fullmatch(r'[a-f0-9]{64}', value['resource_lock']):
        raise ValueError('Invalid frozen-parent resource lock')
    return dict(value)


def _iso_date(value, label):
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError(f'{label} must be a known valid disclosure date') from None
    return value


def _baseline_digest(facts):
    if any(not isinstance(fact, Fact) for fact in facts):
        raise ValueError('Reparse baseline must contain host Fact objects')
    return digest([fact.to_dict() for fact in facts])


def _parser_lock():
    from . import parser, financial_notes, dimensions, issuer_identity
    # Window parsing now executes these helpers as part of its semantic
    # extraction. Changing one invalidates the old proof just like changing
    # the main parser; its source bytes must never silently be reinterpreted.
    return digest({module.__name__: digest(_module_code_data(module))
                   for module in (parser, financial_notes, dimensions, issuer_identity)})


def _resource(source_resources, report_id):
    if not isinstance(report_id, str) or not report_id.startswith('report-'):
        raise ValueError('Only a registered issuer report can be reparsed')
    matches = [row for row in source_resources if row.get('id') == report_id]
    if len(matches) != 1:
        raise ValueError('Reparse report is not uniquely registered')
    resource = matches[0]
    expected = resource.get('sha256', '')
    if not isinstance(expected, str) or not re.fullmatch(r'[a-f0-9]{64}', expected):
        raise ValueError('Reparse report requires its complete source hash')
    from .config import validate_path
    path = validate_path(resource.get('path', ''), kind='file', must_exist=True, follow_symlinks=False)
    if path.suffix.lower() != '.pdf':
        raise ValueError('Local fact reparse only accepts a locked PDF')
    if not report_id.startswith('report-' + expected[:20]):
        raise ValueError('Registered report ID and source hash disagree')
    return resource, path, expected


def _source_metadata(resource, path, document_sha256, baseline_facts, company, as_of_date):
    from .parser import _company_from_path, _company_matches, _document_period
    if not isinstance(company, str) or not company.strip():
        raise ValueError('Reparse requires a trusted task company')
    cutoff = _iso_date(as_of_date, 'as_of_date')
    source_facts = [fact for fact in baseline_facts if
                    (fact.source_path and Path(fact.source_path).resolve() == path.resolve()) or
                    fact.source_doc in {document_sha256, document_sha256[:12]}]
    identified = _company_from_path(path)
    if identified and not _company_matches(company, identified[1], identified[0]):
        raise ValueError('Reparse source belongs to a different company')
    declared_companies = {fact.company for fact in source_facts if fact.company}
    if resource.get('company'):
        declared_companies.add(resource['company'])
    declared_companies.update(resource.get('companies') or [])
    if declared_companies and declared_companies != {company}:
        raise ValueError('Reparse source company conflicts with its frozen metadata')
    if not identified and not declared_companies:
        raise ValueError('Reparse source issuer cannot be established from frozen metadata')
    dates = {fact.publish_date for fact in source_facts if fact.publish_date}
    if resource.get('publish_date'):
        dates.add(resource['publish_date'])
    filename_date = re.match(r'(20\d{2}-\d{2}-\d{2})', path.name)
    if filename_date:
        dates.add(filename_date[1])
    if len(dates) != 1:
        raise ValueError('Reparse disclosure date is unknown or internally inconsistent')
    published = _iso_date(next(iter(dates)), 'publish_date')
    if published > cutoff:
        raise ValueError('Reparse source was disclosed after the task cutoff')
    # Document-derived metadata is a hint only; the parser must independently
    # prove actual column periods inside the selected physical pages.
    period = _document_period(path, str(resource.get('period') or ''))
    return {'company': company, 'publish_date': published, 'report_period': period,
            'as_of_date': cutoff, 'report_id': resource['id'], 'doc_sha256': document_sha256}


def _split_facts(parsed_facts, baseline_facts, metadata, selected_pages):
    existing = FactStore(baseline_facts)
    accepted, conflicts = [], []
    page_set = set(selected_pages)
    for fact in parsed_facts:
        if not isinstance(fact, Fact) or fact.company != metadata['company']:
            raise ValueError('Window parser returned an untrusted company or fact type')
        if fact.source_doc != metadata['doc_sha256'][:12] or fact.publish_date != metadata['publish_date']:
            raise ValueError('Window parser returned mismatched source metadata')
        if not fact.evidence or any(not re.match(r'^' + metadata['doc_sha256'][:12] + r':p(\d+):', ev)
                                    for ev in fact.evidence):
            raise ValueError('Window fact has no original page evidence')
        if any(int(re.match(r'^[a-f0-9]+:p(\d+):', ev)[1]) not in page_set for ev in fact.evidence):
            raise ValueError('Window evidence escapes the selected physical pages')
        # Use the store's registered account aliases as well as its dimensions.
        # In particular NP_PARENT + 扣非 and NP_PARENT_ADJ + 扣非 refer to one
        # financial quantity; spelling cannot bypass the conflict gate.
        dimensions = {key: getattr(fact, key) for key in ('dimension_kind', 'dimension_id', 'currency', 'definition_id')}
        same_version = [old for old in existing.versions(fact.company, fact.account, fact.period, fact.scope, fact.nr, **dimensions)
                        if (old.publish_date, _version_rank(old.version)) == (fact.publish_date, _version_rank(fact.version))]
        if any(FactStore([old, fact]).query(fact.company, fact.account, fact.period, fact.scope, fact.nr,
                                          cutoff=metadata['as_of_date'], **dimensions) is None for old in same_version):
            conflicts.append({'fact': fact.to_dict(), 'existing_fact_ids': [old.fact_id for old in same_version],
                              'abstention_code': 'X4', 'reason': 'same_disclosure_facts_conflict'})
            continue
        # More header evidence for an already frozen financial value is not a
        # newly disclosed fact and must not masquerade as missing-value repair.
        if any(old.value == fact.value for old in same_version):
            continue
        before = len(existing.facts)
        existing.add(fact)
        if len(existing.facts) != before:
            accepted.append(fact)
    return accepted, conflicts


def _body(packet):
    return {key: value for key, value in packet.items() if key not in {'packet_id', 'packet_sha256'}}


def _validate_packet_hash(packet):
    if not isinstance(packet, dict) or packet.get('schema_version') != SCHEMA:
        raise ValueError('Unsupported reparse packet schema')
    actual = digest(_body(packet))
    if packet.get('packet_sha256') != actual or packet.get('packet_id') != 'rp-' + actual:
        raise ValueError('Reparse packet hash or registered ID mismatch')
    if 'staged' in packet and packet['staged'] is not True:
        raise ValueError('Invalid reparse staging marker')
    if packet.get('staged') and packet.get('source_generation') != {}:
        raise ValueError('A staged packet cannot declare a frozen-parent generation')
    if packet.get('status') == 'staged' and not packet.get('staged'):
        raise ValueError('A staged status requires the host staging marker')


def _summary(packet):
    _validate_packet_hash(packet)
    summary = {'packet_id': packet['packet_id'], 'packet_sha256': packet['packet_sha256'],
            'status': packet['status'], 'reason': packet.get('reason'),
            'new_facts_count': len(packet['new_facts']), 'conflicts': len(packet['conflicts']),
            'requires_new_task_version': True, 'applied_to_current_task': False,
            'source_generation': packet['source_generation'],
            'proof': {**packet['source'], 'pages': packet['pages'], 'source_pages': packet['source_pages'],
                      'baseline_facts_digest': packet['baseline_facts_digest'],
                      'parser_fingerprint': packet['parser_fingerprint'],
                      'kernel_fingerprint_sha256': digest(packet['kernel_fingerprint'])}}
    if packet.get('staged'):
        summary.update(staged=True, stage_id='stage-' + packet['packet_sha256'],
                       requires_frozen_parent_generation=True)
    return summary


def _publish_packet(root, packet, cancel_event, deadline_monotonic):
    from .audit import AuditLog
    from .config import validate_path
    with _PUBLISH_LOCK:
        root = validate_path(root, kind='dir', must_exist=False, follow_symlinks=False)
        io_path(root).mkdir(parents=True, exist_ok=True)
        root = validate_path(root, kind='dir', must_exist=True, follow_symlinks=False)
        destination = root / (packet['packet_id'] + '.json')
        reused = io_path(destination).exists()
        if reused:
            previous = json.loads(io_path(resolve_reparse_packet(root, packet['packet_id'])).read_text(encoding='utf-8'))
            if previous != packet:
                raise ValueError('Idempotent reparse packet has been modified')
        else:
            atomic_write_text(destination, json.dumps(packet, ensure_ascii=False, indent=2),
                              before_replace=lambda: _checkpoint(cancel_event, deadline_monotonic))
        AuditLog(root / 'audit' / packet['packet_id'], packet['source_generation'].get('run_id') or
                 'reparse-' + packet['source']['doc_sha256'][:12]).emit(
                     'reparse_packet_reused' if reused else 'reparse_packet_published',
                     input={'report_id': packet['source']['report_id'], 'pages': packet['pages'],
                            'binding_id': packet['anchor']['binding_id']}, output=_summary(packet),
                     packet_id=packet['packet_id'], packet_sha256=packet['packet_sha256'])
        return _summary(packet)


def resolve_reparse_packet(output_root: str | Path, packet_id: str) -> Path:
    if not isinstance(packet_id, str) or not PACKET_ID.fullmatch(packet_id):
        raise ValueError('Invalid registered reparse packet ID')
    from .config import validate_path
    root = validate_path(output_root, kind='dir', must_exist=True, follow_symlinks=False)
    return validate_path(root / (packet_id + '.json'), allowed_roots=[str(root)], kind='file',
                         must_exist=True, follow_symlinks=False)


def list_reparse_packets(output_root: str | Path, *, expected_source_generation: dict | None = None) -> list[dict]:
    root = Path(output_root)
    if not io_path(root).exists():
        return []
    results = []
    for path in sorted(io_path(root).glob('rp-*.json')):
        located = resolve_reparse_packet(root, path.stem)
        packet = json.loads(io_path(located).read_text(encoding='utf-8'))
        if expected_source_generation is not None and packet['source_generation'] != _generation(expected_source_generation):
            raise ValueError('Reparse packet belongs to another parent-task generation')
        results.append(_summary(packet))
    return results


def create_reparse_packet(*, output_root: str | Path, block: dict, claim: AtomicClaim | None,
                          source_resources: list[dict], baseline_facts: list[Fact], company: str,
                          as_of_date: str, source_generation: dict | None = None,
                          cancel_event=None, deadline_monotonic=None, staged: bool = False) -> dict:
    """Read at most three source pages; atomically publish a host-only packet."""
    from .parser import extract_fact_window
    _checkpoint(cancel_event, deadline_monotonic)
    baseline_sha = _baseline_digest(baseline_facts)
    generation = _generation(source_generation)
    if type(staged) is not bool or (staged and generation):
        raise ValueError('Staging requires an explicit host flag and no frozen-parent generation')
    if not staged and not generation:
        raise ValueError('A ready reparse packet requires its complete frozen-parent generation')
    report_id = block.get('source_resource_id') or block.get('report_id')
    resource, path, expected = _resource(source_resources, report_id)
    if claim is not None and (not isinstance(claim, AtomicClaim) or claim.company != company):
        raise ValueError('Reparse binding is outside the task company')
    if block.get('company') and block['company'] != company:
        raise ValueError('Reparse block is outside the task company')
    page = block.get('page')
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError('Reparse requires a registered physical page')
    metadata = _source_metadata(resource, path, expected, baseline_facts, company, as_of_date)
    if sha256_file(path) != expected:
        raise ValueError('Reparse source PDF changed')
    import fitz
    with fitz.open(io_path(path)) as document:
        if page > len(document):
            raise ValueError('Reparse page is outside the source PDF')
        pages = list(range(max(1, page - 1), min(len(document), page + 1) + 1))
    parsed = extract_fact_window(path, pages=pages, company=company, report_period=metadata['report_period'],
                                 publish_date=metadata['publish_date'], expected_sha256=expected,
                                 cancel_event=cancel_event, deadline_monotonic=deadline_monotonic)
    _checkpoint(cancel_event, deadline_monotonic)
    if sha256_file(path) != expected:
        raise ValueError('Reparse source PDF changed while reading')
    if [row.get('page') for row in parsed['source_pages']] != pages:
        raise ValueError('Window parser did not prove all selected source pages')
    accepted, conflicts = _split_facts(parsed['facts'], baseline_facts, metadata, pages)
    conflicts.extend({'abstention_code': 'X4', 'reason': 'source_window_facts_conflict',
                      'source_issue': issue} for issue in parsed.get('issues', []) if issue.get('code') == 'fact_conflict')
    status = 'conflicts' if conflicts else ('staged' if staged else 'ready') if accepted else 'no_new_information'
    packet = {'schema_version': SCHEMA, 'source': metadata, 'source_generation': generation,
              'anchor': {'block_id': block.get('id', ''), 'page': page,
                         'binding_id': claim.claim_id if claim else None},
              'pages': pages, 'source_pages': parsed['source_pages'],
              'parser_fingerprint': _parser_lock(), 'kernel_fingerprint': kernel_fingerprint(),
              'baseline_facts_digest': baseline_sha, 'new_facts': [fact.to_dict() for fact in accepted],
              'conflicts': conflicts, 'issues': parsed.get('issues', []), 'status': status,
              'reason': 'same_disclosure_facts_conflict' if conflicts else
                        ('requires_frozen_parent_generation' if staged else None) if accepted else 'no_new_deterministic_facts'}
    if staged:
        packet['staged'] = True
    packet['packet_sha256'] = digest(packet)
    packet['packet_id'] = 'rp-' + packet['packet_sha256']
    _checkpoint(cancel_event, deadline_monotonic)
    return _publish_packet(Path(output_root), packet, cancel_event, deadline_monotonic)


def _reconstruct_packet(packet, *, source_resources, baseline_facts, company, as_of_date,
                        cancel_event=None):
    """Prove packet contents against original pages without publishing anything."""
    from .parser import extract_fact_window
    _checkpoint(cancel_event)
    _validate_packet_hash(packet)
    if packet['baseline_facts_digest'] != _baseline_digest(baseline_facts):
        raise ValueError('Reparse baseline facts changed')
    if packet['parser_fingerprint'] != _parser_lock() or packet['kernel_fingerprint'] != kernel_fingerprint():
        raise ValueError('Reparse parser, kernel or rule locks changed')
    resource, source, expected = _resource(source_resources, packet['source']['report_id'])
    metadata = _source_metadata(resource, source, expected, baseline_facts, company, as_of_date)
    if metadata != packet['source']:
        raise ValueError('Reparse source company, disclosure or cutoff changed')
    if sha256_file(source) != expected:
        raise ValueError('Reparse source PDF changed')
    anchor = packet['anchor']['page']
    import fitz
    with fitz.open(source) as document:
        if isinstance(anchor, bool) or not isinstance(anchor, int) or not 1 <= anchor <= len(document):
            raise ValueError('Invalid reparse anchor page')
        pages = list(range(max(1, anchor - 1), min(len(document), anchor + 1) + 1))
    if pages != packet['pages']:
        raise ValueError('Reparse window is not the registered three-page range')
    parsed = extract_fact_window(source, pages=pages, company=company, report_period=metadata['report_period'],
                                 publish_date=metadata['publish_date'], expected_sha256=expected,
                                 cancel_event=cancel_event)
    accepted, conflicts = _split_facts(parsed['facts'], baseline_facts, metadata, pages)
    conflicts.extend({'abstention_code': 'X4', 'reason': 'source_window_facts_conflict',
                      'source_issue': issue} for issue in parsed.get('issues', []) if issue.get('code') == 'fact_conflict')
    status = 'conflicts' if conflicts else ('staged' if packet.get('staged') else 'ready') if accepted else 'no_new_information'
    if (parsed['source_pages'] != packet['source_pages'] or parsed.get('issues', []) != packet['issues']
            or conflicts != packet['conflicts'] or status != packet['status']
            or [fact.to_dict() for fact in accepted] != packet['new_facts']):
        raise ValueError('Reparse packet differs from deterministic source-page reconstruction')
    _checkpoint(cancel_event)
    if sha256_file(source) != expected:
        raise ValueError('Reparse source PDF changed while loading')
    return accepted


def load_reparse_packet(packet_path: str | Path, *, source_resources: list[dict], baseline_facts: list[Fact],
                        company: str, as_of_date: str, expected_packet_id: str | None = None,
                        expected_source_generation: dict | None = None, cancel_event=None) -> list[Fact]:
    """Re-execute the locked window before returning additive, conflict-free facts."""
    from .config import validate_path
    _checkpoint(cancel_event)
    path = validate_path(packet_path, kind='file', must_exist=True, follow_symlinks=False)
    packet = json.loads(io_path(path).read_text(encoding='utf-8'))
    _validate_packet_hash(packet)
    if path.name != packet['packet_id'] + '.json' or (expected_packet_id and expected_packet_id != packet['packet_id']):
        raise ValueError('Reparse packet is not the registered parent-task packet')
    if packet.get('staged') or packet['status'] != 'ready' or packet['conflicts'] or not packet['new_facts']:
        raise ValueError('Only a ready nonconflicting fact packet can enter a new task; staged packets require freezing')
    generation = _generation(packet.get('source_generation'))
    if not generation:
        raise ValueError('A ready reparse packet requires its complete frozen-parent generation')
    if expected_source_generation is not None and generation != _generation(expected_source_generation):
        raise ValueError('Reparse packet parent-task generation changed')
    return _reconstruct_packet(packet, source_resources=source_resources, baseline_facts=baseline_facts,
                               company=company, as_of_date=as_of_date, cancel_event=cancel_event)


def freeze_staged_reparse_packets(parent_out: str | Path, output_root: str | Path, *,
                                 cancel_event=None) -> list[dict]:
    """Re-read frozen staged pages and export separate, generation-bound packets.

    The parent directory remains read-only. Its manifest must include every
    staged packet; adding a packet after freezing never creates authority.
    """
    from .config import validate_path
    from .reparse_inputs import load_parent_context
    from .replay import _claim_from_dict
    from .audit import AuditLog
    _checkpoint(cancel_event)
    root = validate_path(parent_out, kind='dir', must_exist=True, follow_symlinks=False)
    destination = validate_path(output_root, kind='dir', follow_symlinks=False)
    if destination.is_relative_to(root):
        raise ValueError('Frozen reparse exports must stay outside the parent task directory')
    context = load_parent_context(root)
    staging = root / 'staged-reparse'
    if not io_path(staging).exists():
        return []
    staging = validate_path(staging, allowed_roots=[str(root)], kind='dir', must_exist=True, follow_symlinks=False)
    manifest_path = root / 'run_manifest.json'
    manifest = json.loads(io_path(manifest_path).read_text(encoding='utf-8'))
    registered = {row['path']: row['sha256'] for row in manifest['outputs']}
    packets = []
    for path in sorted(io_path(staging).glob('rp-*.json')):
        located = resolve_reparse_packet(staging, path.stem)
        relative = located.relative_to(root).as_posix()
        if registered.get(relative) != sha256_file(located):
            raise ValueError('Staged reparse packet is not part of the frozen parent outputs')
        packet = json.loads(io_path(located).read_text(encoding='utf-8'))
        _validate_packet_hash(packet)
        if not packet.get('staged') or located.name != packet['packet_id'] + '.json':
            raise ValueError('Only host staged packets can be frozen from this task')
        _reconstruct_packet(packet, source_resources=context['source_resources'],
                            baseline_facts=context['baseline_facts'], company=context['company'],
                            as_of_date=context['as_of_date'], cancel_event=cancel_event)
        identifier = packet['anchor']['binding_id']
        claim = None
        if identifier is not None:
            matches = [row for row in context['data']['claims'] if row.get('claim_id') == identifier]
            if len(matches) != 1:
                raise ValueError('Staged binding is not part of its frozen parent task')
            claim = _claim_from_dict(matches[0])
            if claim.company != context['company']:
                raise ValueError('Staged binding belongs to a different task company')
        packets.append((packet, claim))
    # Validate the entire staged set before exporting any new packet.
    results = []
    for packet, claim in packets:
        _checkpoint(cancel_event)
        anchor = packet['anchor']
        summary = create_reparse_packet(output_root=destination,
            block={'id': anchor['block_id'], 'page': anchor['page'], 'report_id': packet['source']['report_id']},
            claim=claim, source_resources=context['source_resources'], baseline_facts=context['baseline_facts'],
            company=context['company'], as_of_date=context['as_of_date'],
            source_generation=context['source_generation'], cancel_event=cancel_event)
        result = dict(summary, stage_id='stage-' + packet['packet_sha256'],
                      parent_run_id=context['source_generation']['run_id'])
        AuditLog(destination / 'audit' / summary['packet_id'], result['parent_run_id']).emit(
            'staged_reparse_frozen', input={'stage_id': result['stage_id'], 'packet_id': packet['packet_id']},
            output=result)
        results.append(result)
    return results


# Explicit export name for host integrations; both names share one guard.
export_reparse_packet = create_reparse_packet
