"""Measure source coverage without replacing independent professional review."""
from __future__ import annotations

import argparse
import hashlib
from decimal import Decimal, InvalidOperation
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mingjing.coverage_stats import PRIMARY_SCOPE, coverage_statistics
from mingjing.evidence import sha256_file


def source_occurrences_match(actual, rebuilt):
    """Rebuild categories and their proofs, not only numeric coordinates."""
    key = lambda row: (row['start'], row['end'])
    entries = {key(row): row for row in actual}
    if len(entries) != len(actual) or len(actual) != len(rebuilt):
        return False
    for original in rebuilt:
        saved = entries.get(key(original))
        if saved is None or any(saved.get(field) != original.get(field) for field in (
                'text', 'evaluation_scope', 'classification_basis', 'modality_basis', 'exclusion_basis')):
            return False
        if original['status'] in {'out_of_scope', 'rule_excluded'}:
            if saved['status'] != original['status'] or saved.get('reason') != original.get('reason'):
                return False
        elif saved['status'] in {'out_of_scope', 'rule_excluded'}:
            return False
    return True


def numeric_source_closure(data, conclusions, run_root=None):
    """References must close over frozen facts and actual registered PDF pages."""
    from mingjing.evidence import fact_evidence_references, original_pdf_evidence, parse_page_evidence
    facts = {row['fact_id']: row for row in data.get('facts', [])}
    registry = data.get('evidence_resources') or {}
    if not conclusions or run_root is None or not registry or not facts:
        return False
    root = Path(run_root).resolve()
    manifest = json.loads((root/'run_manifest.json').read_text(encoding='utf-8'))
    inputs = {str(Path(row['path']).resolve()): row['sha256'] for row in manifest.get('inputs', [])}
    images = {row['path']: row for row in manifest.get('outputs', [])}
    hashes = {}
    references = {}
    for row in conclusions:
        if not row.get('evidence') or not row.get('operands') or not row.get('formula') or not row.get('rule_ids'):
            return False
        try:
            if not Decimal(str(row['expected'])).is_finite():
                return False
        except (KeyError, InvalidOperation, ValueError):
            return False
        authorised_evidence = {}
        for operand in row['operands']:
            original = facts.get(operand.get('fact_id'))
            if not original or any(original.get(key) != value for key, value in operand.items()):
                return False
            source = Path(original.get('source_path') or '')
            key = str(source.resolve())
            if source.suffix.lower() != '.pdf' or key not in inputs or not source.is_file():
                return False
            if key not in hashes:
                hashes[key] = sha256_file(source)
            if hashes[key] != inputs[key]:
                return False
            for identifier in fact_evidence_references(original):
                authorised_evidence.setdefault(identifier, set()).add(key)
        for identifier in row['evidence']:
            if identifier not in authorised_evidence or identifier not in registry:
                return False
            resource = registry[identifier]
            parsed = parse_page_evidence(identifier)
            source = str(Path(resource.get('source_path') or '').resolve())
            if (not parsed or source not in authorised_evidence[identifier] or source not in inputs
                    or resource.get('document_sha256') != inputs[source]
                    or not inputs[source].startswith(parsed['document_sha12']) or resource.get('page') != parsed['page']):
                return False
            image = root / str(resource.get('image') or '')
            if not image.resolve().is_relative_to(root) or not image.is_file():
                return False
            output = images.get(image.relative_to(root).as_posix())
            image_hash = sha256_file(image)
            if (not output or output.get('bytes') != image.stat().st_size or output.get('sha256') != image_hash
                    or resource.get('image_sha256', image_hash) != image_hash):
                return False
            references.setdefault(source, {})[identifier] = (parsed, image, resource)
    import fitz
    for source, rows in references.items():
        path = Path(source)
        if path.stat().st_size > 256 * 1024 * 1024:
            return False
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != inputs[source]:
            return False
        with fitz.open(stream=raw, filetype='pdf') as document:
            page_references = {}
            renderings = {}
            for identifier, (parsed, image, resource) in rows.items():
                page_number = parsed['page']
                if not 1 <= page_number <= len(document):
                    return False
                page = document[page_number-1]
                if page_number not in page_references:
                    page_references[page_number] = original_pdf_evidence(page, inputs[source])
                original = page_references[page_number].get(identifier.split(':r', 1)[0])
                if original is None:
                    return False
                candidates = [parsed['bbox'], original['bbox']]
                if ('bbox' in resource and resource['bbox'] not in candidates
                        or 'quote_text' in resource and resource['quote_text'] != original['quote_text']
                        or 'quote_sha256' in resource and resource['quote_sha256'] !=
                        hashlib.sha256(original['quote_text'].encode('utf-8')).hexdigest()):
                    return False
                try:
                    pixels = fitz.Pixmap(image.read_bytes())
                except (RuntimeError, ValueError):
                    return False
                rectangle = page.rect * fitz.Matrix(1.2, 1.2)
                if (pixels.width, pixels.height) != (rectangle.irect.width, rectangle.irect.height):
                    return False
                if identifier not in renderings:
                    expected = set()
                    for bounds in candidates:
                        annotation = page.add_rect_annot(fitz.Rect(bounds))
                        annotation.update()
                        try:
                            rendered = page.get_pixmap(matrix=fitz.Matrix(1.2, 1.2), alpha=False)
                            expected.add(hashlib.sha256(rendered.samples).digest())
                        finally:
                            page.delete_annot(annotation)
                    renderings[identifier] = expected
                if pixels.n != 3 or hashlib.sha256(pixels.samples).digest() not in renderings[identifier]:
                    return False
    return True


def operand_source_reconstruction(data, conclusions, run_root=None):
    """Re-extract used financial cells; a valid page link cannot prove an amount.

    This is a development consistency check, not human semantic annotation.
    A cached amount pointing at a genuine unrelated block must still fail.
    """
    if run_root is None or not conclusions:
        return False
    from mingjing.parser import extract_facts
    from mingjing.source_cache import is_machine_fact
    root = Path(run_root).resolve()
    manifest = json.loads((root / 'run_manifest.json').read_text(encoding='utf-8'))
    inputs = {str(Path(row['path']).resolve()): row['sha256'] for row in manifest.get('inputs', [])}
    originals = {row['fact_id']: row for row in data.get('facts', [])}
    parsed = {}
    identity_fields = ('company', 'account', 'period', 'scope', 'nr', 'version', 'value',
                       'decimals', 'display_unit', 'dimension_kind', 'dimension_id', 'currency', 'definition_id')
    for conclusion in conclusions:
        for operand in conclusion.get('operands') or []:
            original = originals.get(operand.get('fact_id'))
            if not original or not is_machine_fact(original):
                return False
            source = Path(original.get('source_path') or '').resolve()
            source_key = str(source)
            if source.suffix.lower() != '.pdf' or inputs.get(source_key) != sha256_file(source):
                return False
            # Disclosure metadata is already locked and cross-checked by the
            # task. Do not give the extractor a candidate fiscal period.
            key = (source_key, original['company'], original.get('publish_date') or '')
            if key not in parsed:
                parsed[key] = [fact.to_dict() for fact in extract_facts(source, key[1], '', key[2])]
            matching = [fact for fact in parsed[key] if all(
                fact.get(field) == original.get(field) for field in identity_fields)]
            if not any(set(original.get('evidence') or []) <= set(fact.get('evidence') or []) for fact in matching):
                return False
    return True


def evaluate(data: dict, seed: dict | None = None, run_root=None) -> dict:
    try:
        stats = coverage_statistics(data.get('coverage'), data.get('claims') or [], data.get('results') or [])
    except (ValueError, KeyError, TypeError) as exc:
        return {'schema_version': 'financial-coverage-acceptance.v2', 'status': 'needs_improvement',
                'run_id': data.get('run_id'), 'checks': {'ledger_integrity': False},
                'failed_checks': ['ledger_integrity'], 'error': str(exc), 'measurements': None,
                'professional_acceptance': False, 'release_acceptance': False}
    recorded = data.get('coverage_statistics')
    scopes = stats['claim_scopes']
    conclusions = [row for row in data.get('results', []) if row['status'] in {'verified', 'violated'}
                   and scopes.get(row['claim_id']) == PRIMARY_SCOPE]
    try:
        numeric_evidence = numeric_source_closure(data, conclusions, run_root)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, ImportError):
        numeric_evidence = False
    try:
        reconstructed_operands = operand_source_reconstruction(data, conclusions, run_root)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, ImportError):
        reconstructed_operands = False
    replay = {'status': 'not_executed'}
    occurrence_consistent = False
    if run_root is not None:
        from mingjing.replay import replay_run
        try:
            replay = replay_run(run_root)
            if replay.get('integrity_ok'):
                from mingjing.coverage import CoverageLedger, partition_document
                text, pages, blocks = partition_document(Path(data['draft']))
                rebuilt, _ = CoverageLedger.build(text, pages, blocks, data.get('company') or '', data.get('period') or '')
                occurrence_consistent = text == data.get('draft_text') and source_occurrences_match(
                    (data.get('coverage') or {}).get('mentions') or [], rebuilt.entries)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            replay = {'status': 'failed', 'reason': 'Frozen run or source occurrence integrity could not be verified'}
    entries = (data.get('coverage') or {}).get('mentions') or []
    preservation = None
    restored = None
    if seed is not None:
        old_entries = (seed.get('coverage') or {}).get('mentions') or []
        old_starts = {row['start'] for row in old_entries}
        by_start = {row['start']: row for row in entries}
        preservation = data.get('draft_text') == seed.get('draft_text') and old_starts.issubset(by_start)
        restored = sum(row.get('status') == 'rule_excluded' and
            by_start.get(row['start'], {}).get('claim_id') is not None and
            by_start.get(row['start'], {}).get('evaluation_scope') == PRIMARY_SCOPE for row in old_entries)
    checks = {
        'recorded_statistics_match': recorded == stats,
        'primary_abstention_at_most_nine': stats['abstention_target_met'],
        'no_pending_primary_items': stats['core']['pending'] == 0,
        'task_complete': not data.get('partial', False),
        'numeric_evidence_present': numeric_evidence,
        'operand_source_reconstruction': reconstructed_operands,
        'original_scope_supported': not any((row.get('source_dimensions') or {}).get('scope_provenance') ==
            'user_supplied_scope' for row in conclusions),
        'frozen_numeric_replay': replay.get('status') == 'passed' and replay.get('numeric_equal') is True,
        'full_source_occurrences_present': occurrence_consistent,
        'baseline_present': seed is not None,
    }
    if seed is not None:
        checks.update(original_occurrences_preserved=preservation,
                      previously_missed_financial_cells_restored=restored >= 38)
    passed = all(checks.values())
    return {
        'schema_version': 'financial-coverage-acceptance.v2',
        'status': 'development_financial_target_passed_professional_pending' if passed else 'needs_improvement',
        'run_id': data.get('run_id'), 'checks': checks,
        'failed_checks': [key for key, value in checks.items() if not value],
        'measurements': stats, 'restored_financial_occurrences': restored,
        'kernel_replay': replay,
        'professional_acceptance': False, 'release_acceptance': False,
        'independent_gold_metrics': 'not_measured_by_this_script',
        'limitations': ['范围分类为可审计程序输出，不能冒充真人金标。',
                        '用户补充口径支持条件核查，不能冒充原文口径已获证实。',
                        '本脚本不证明准确率、全文召回率或独立Windows发布验收。'],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--seed', type=Path)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args(argv)
    source = args.run / 'results.json'
    seed_path = args.seed / 'results.json' if args.seed else None
    receipt = evaluate(json.loads(source.read_text(encoding='utf-8')),
                       json.loads(seed_path.read_text(encoding='utf-8')) if seed_path else None, args.run)
    receipt['results_sha256'] = sha256_file(source)
    if seed_path:
        receipt['seed_results_sha256'] = sha256_file(seed_path)
    destination = args.out or args.run / 'financial_coverage_acceptance.json'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': receipt['status'], 'core': (receipt.get('measurements') or {}).get('core'),
                      'failed_checks': receipt['failed_checks']}, ensure_ascii=False))
    return 0 if not receipt['failed_checks'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
