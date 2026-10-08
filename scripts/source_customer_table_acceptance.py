"""Live native development probe of original research-report table 4 only.

Uses the saved provider unchanged and the production Agent/replay entrypoint.
Every candidate in the fixed PDF excerpt remains in its complete ledger.
This is neither full-report acceptance nor independent financial gold.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
import hashlib
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mingjing.import_integrity import assert_application_origin
assert_application_origin(ROOT)

from mingjing.agent import write_json
from mingjing.config import validate_path
from mingjing.evidence import sha256_file
from scripts import source_agent_acceptance as native_probe
from scripts.development_receipt import source_lock
from scripts.diagnose_binding_coverage import diagnose, markdown_report, write_report
from scripts.source_acceptance_inputs import positive_integer
from scripts.source_web_acceptance import new_output


DRAFT_SHA256 = '7a70da91863feb0ce795748619f02b21a2b68ab6ec134c5520239b658e686a9d'
PROSPECTUS_SHA256 = '9f351a9852deaf4b6365dafb9c8195debee341277352bf91b69060c27c069ed1'
CUTOFF = '2024-04-22'
# Fixed before execution: caption/header and every table row, including totals,
# followed by the actual source footer. Selection never consults a verdict/value.
TABLE_REGIONS = ((13, (36.0, 610.0, 558.0, 780.0)),
                 (14, (36.0, 70.0, 558.0, 283.0)))


def validate_args(args):
    for field in ('seed_run', 'out'):
        if not isinstance(getattr(args, field), Path):
            raise ValueError(field+' must be a path')
    for field in ('model_calls', 'tool_calls', 'tokens', 'timeout'):
        positive_integer(getattr(args, field), field)
    if type(args.prepare_only) is not bool:
        raise ValueError('prepare_only must be a boolean')
    seed, out = args.seed_run.resolve(), args.out.resolve()
    if out == seed or out.is_relative_to(seed) or seed.is_relative_to(out):
        raise ValueError('Local diagnostic output must be separate from its read-only historical seed')
    return args


def _read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def _locked_path(resource, *, expected=None):
    path = validate_path(resource['path'], kind='file', must_exist=True, follow_symlinks=False)
    checksum = sha256_file(path)
    if checksum != resource['sha256'] or (expected is not None and checksum != expected):
        raise ValueError('Selected historical source bytes do not match their original lock')
    return path


def read_seed(seed):
    """Choose by immutable document identity, never by financial amounts."""
    seed = validate_path(seed, kind='dir', must_exist=True, follow_symlinks=False)
    files = [seed/name for name in ('agent_lock.json', 'results.json')]
    locked, results = (_read(path) for path in files)
    resources = locked.get('resources') or []
    drafts = [row for row in resources if row.get('id') == 'draft']
    reports = [row for row in resources if row.get('sha256') == PROSPECTUS_SHA256
               and str(row.get('id', '')).startswith('report-')]
    if len(drafts) != 1 or len(reports) != 1:
        raise ValueError('Historical seed must have one original draft and the nominated official prospectus')
    draft = _locked_path(drafts[0], expected=DRAFT_SHA256)
    report = _locked_path(reports[0], expected=PROSPECTUS_SHA256)
    published = reports[0].get('publish_date')
    try:
        valid_date = isinstance(published, str) and date.fromisoformat(published).isoformat() == published
    except ValueError:
        valid_date = False
    if (results.get('report_date') != CUTOFF or not results.get('company')
            or not valid_date or published > CUTOFF):
        raise ValueError('Historical cutoff, issuer or eligible disclosure date is missing or changed')
    protected = {str(path): sha256_file(path) for path in [*files, draft, report]}
    index = reports[0].get('index_path')
    if index:
        index_path = _locked_path({'path': index, 'sha256': reports[0]['index_sha256']})
        protected[str(index_path)] = reports[0]['index_sha256']
    return {'draft': draft, 'report': report, 'company': results['company'],
            'report_resource': reports[0], 'protected_hashes': protected}


def protected_unchanged(hashes):
    return all(Path(path).is_file() and sha256_file(path) == expected for path, expected in hashes.items())


def _text_key(text):
    return re.sub(r'\s+', '', text)


def extract_table_excerpt(source, target, *, regions=TABLE_REGIONS):
    """Physically remove out-of-sample text while preserving PDF coordinates.

    Merely rendering with a clip may leave invisible source text extractable.
    Real PDF redaction removes that text; extracted content is checked against
    the source's nominated rectangle before accepting the new diagnostic PDF.
    """
    import fitz
    source = Path(source).resolve()
    target = Path(target).resolve()
    if source == target or target.exists():
        raise ValueError('Excerpt must be a new PDF distinct from the original source')
    source_sha = sha256_file(source)
    pages = []
    with fitz.open(source) as original, fitz.open() as excerpt:
        for local_page, (source_page, coordinates) in enumerate(regions, 1):
            if type(source_page) is not int or not 1 <= source_page <= len(original):
                raise ValueError('Invalid source page in local excerpt selection')
            page = original[source_page-1]
            region = fitz.Rect(coordinates)
            if not region.is_valid or region.is_empty or not page.rect.contains(region):
                raise ValueError('Selected table rectangle is outside its original page')
            expected = page.get_text('text', clip=region)
            full_text = page.get_text('text')
            blocks, search_from = [], 0
            for block in page.get_text('blocks'):
                if len(block) < 7 or block[6] != 0:
                    continue
                found = full_text.find(block[4], search_from)
                if found >= 0:
                    search_from = found + len(block[4])
                box = fitz.Rect(block[:4])
                if region.contains(box):
                    if found < 0:
                        raise ValueError('Original block cannot be mapped to original page characters')
                    blocks.append({'bbox': list(box), 'text': block[4], 'source_page_start': found,
                                   'source_page_end': found+len(block[4])})
            excerpt.insert_pdf(original, from_page=source_page-1, to_page=source_page-1)
            copied = excerpt[-1]
            rectangle = page.rect
            excluded = [fitz.Rect(rectangle.x0, rectangle.y0, rectangle.x1, region.y0),
                        fitz.Rect(rectangle.x0, region.y1, rectangle.x1, rectangle.y1),
                        fitz.Rect(rectangle.x0, region.y0, region.x0, region.y1),
                        fitz.Rect(region.x1, region.y0, rectangle.x1, region.y1)]
            for box in excluded:
                if not box.is_empty:
                    copied.add_redact_annot(box, fill=(1, 1, 1))
            copied.apply_redactions()
            if _text_key(copied.get_text('text')) != _text_key(expected):
                raise ValueError('Redacted excerpt changed in-sample source text or retained hidden out-of-sample text')
            pages.append({'excerpt_page': local_page, 'original_page': source_page,
                          'source_region': list(region), 'source_page_text_sha256':
                          hashlib.sha256(full_text.encode('utf-8')).hexdigest(),
                          'selected_text': expected, 'original_blocks': blocks})
        excerpt.set_metadata({'title': 'MingJing original table 4 local diagnostic excerpt',
                              'subject': 'Original research pages only; not full-report acceptance',
                              'keywords': 'source-sha256='+source_sha})
        excerpt.save(target, garbage=4, deflate=True)
    if sha256_file(source) != source_sha:
        raise ValueError('Original research PDF changed during excerpt preparation')
    with fitz.open(target) as check:
        if len(check) != len(pages) or any(_text_key(check[i].get_text('text')) != _text_key(row['selected_text'])
                                         for i, row in enumerate(pages)):
            raise ValueError('Saved excerpt failed the original-source text roundtrip')
    return {'schema_version': 'original-table-excerpt.v1', 'original_pdf': str(source),
            'original_sha256': source_sha, 'excerpt_pdf': str(target),
            'excerpt_sha256': sha256_file(target), 'table_number': 4, 'pages': pages,
            'selection_basis': 'predeclared_table_geometry_not_amounts_or_verdicts',
            'full_report_acceptance': False, 'all_excerpt_candidates_retained': True}


def customer_diagnostic(data, report_sha=PROSPECTUS_SHA256):
    """Select output rows for review; never remove any production input claim."""
    claims, results = data.get('claims') or [], data.get('results') or []
    lookup = {row['claim_id']: row for row in results}
    if len(lookup) != len(results):
        raise ValueError('Duplicate financial result identity in local diagnostic')
    selected, remaining = [], []
    for claim in claims:
        context = claim.get('table_context') or {}
        caption = (context.get('title_source') or {}).get('text') or ''
        is_customer = (context.get('dimension_kind') == 'customer'
                       and bool(re.match(r'\s*表\s*4\s*[:：]', caption)))
        proof = context.get('source_table_reference') or {}
        row = {'claim_id': claim['claim_id'], 'original_claim': claim,
               'result': lookup.get(claim['claim_id']), 'source_table_reference': proof or None,
               'linked_to_nominated_prospectus': proof.get('report_sha256') == report_sha}
        (selected if is_customer else remaining).append(row)
    return {'scope': 'original_research_table4_customer_subset_diagnostic_only',
            'selected_claims': selected, 'other_excerpt_claims': remaining,
            'counts': dict(Counter((row['result'] or {}).get('status', 'pending') for row in selected)),
            'selected_claim_count': len(selected), 'all_excerpt_claim_count': len(claims),
            'claims_removed_from_agent_input': 0,
            'all_excerpt_ledger': data.get('coverage'),
            'excerpt_coverage_statistics': data.get('coverage_statistics'),
            'financial_recall': 'unknown_requires_independent_gold',
            'financial_accuracy': 'unknown_requires_independent_gold'}


def execute(args):
    validate_args(args)
    # Historical checks precede output creation and any credential resolution.
    selected = read_seed(args.seed_run)
    out = new_output(args.out)
    receipt = {'status': 'running', 'scope': 'table4_local_development_probe_only',
               'full_report_acceptance': False, 'independent_gold': False,
               'real_provider_api': 'not_executed', 'EXE_built': False,
               'model_cost': 'unknown', 'historical_seed_unchanged': None,
               'release_acceptance': 'not_measured',
               'kernel_replay': {'status': 'not_executed'},
               'agent_replay': {'status': 'not_executed'}}
    write_json(out/'acceptance.json', receipt)
    locked_source = source_lock()
    try:
        receipt['phase'] = 'prepare_original_excerpt'
        draft = out/'table4-original-pages-13-14.pdf'
        excerpt = extract_table_excerpt(selected['draft'], draft)
        write_json(out/'excerpt_manifest.json', excerpt)
        receipt['excerpt_manifest_sha256'] = sha256_file(out/'excerpt_manifest.json')
        receipt['official_corpus'] = {key: selected['report_resource'].get(key) for key in
            ('path', 'sha256', 'publish_date', 'publish_date_basis', 'source_provenance')}
        request = {'draft': str(draft), 'corpus': str(selected['report']), 'company': selected['company'],
                   'period': '', 'report_date': CUTOFF, 'mode': 'harness', 'task_type': 'check',
                   'facts': '', 'corpus_additions': [], 'knowledge_root': '', 'scope_declaration': ''}
        seed = out/'diagnostic-seed'
        seed.mkdir()
        write_json(seed/'agent_state.json', {'request': request})
        receipt['request'] = request
        if args.prepare_only:
            receipt.update(status='prepared_not_executed', phase='awaiting_live_execution')
        else:
            receipt['phase'] = 'execute_production_native_task'
            code = native_probe.execute(SimpleNamespace(
                state=seed/'agent_state.json', out=out/'run', task_type='check', expect_event=[],
                corpus=selected['report'], corpus_addition=[], knowledge_root=None, scope_declaration=None,
                model_calls=args.model_calls, tool_calls=args.tool_calls, tokens=args.tokens,
                followup_tokens=None, timeout=args.timeout))
            native_receipt = _read(out/'run/source_acceptance.json')
            usage = native_receipt.get('usage')
            actual_requests = usage.get('model_requests') if isinstance(usage, dict) else None
            receipt.update(native_exit_code=code, native_receipt=native_receipt,
                           model_run=native_receipt.get('model_run'),
                           kernel_replay=native_receipt.get('kernel_replay'),
                           agent_replay=native_receipt.get('agent_replay'),
                           real_provider_api=(actual_requests > 0 if type(actual_requests) is int else 'unknown'))
            results_path = out/'run/results.json'
            if results_path.is_file():
                data = _read(results_path)
                subset = customer_diagnostic(data)
                write_json(out/'customer_diagnostic.json', subset)
                diagnosis = diagnose(out/'run', sample_per_category=2, category_limit=8)
                write_report(diagnosis, out/'binding_diagnostic.json')
                (out/'binding_diagnostic.md').write_text(markdown_report(diagnosis), encoding='utf-8')
                chosen = subset['selected_claims']
                chosen_ids = {row['claim_id'] for row in chosen}
                binding_rows = [row for row in diagnosis['rows'] if row['claim_id'] in chosen_ids]
                receipt['checks'] = {
                    'original_excerpt_all_candidates_visible': bool(data.get('coverage', {}).get('mentions'))
                        and len(subset['selected_claims'])+len(subset['other_excerpt_claims']) == len(data['claims']),
                    'table4_customer_claims_present': bool(chosen),
                    'production_source_table_link': bool(chosen) and all(row['linked_to_nominated_prospectus'] for row in chosen),
                    'customer_double_binding_tool_submissions': bool(binding_rows) and chosen_ids == {row['claim_id'] for row in binding_rows}
                        and all(row['binding_A']['actually_submitted'] is True and row['binding_B']['actually_submitted'] is True
                                for row in binding_rows),
                    'customer_claims_decisively_judged': bool(chosen) and all((row['result'] or {}).get('status') in {'verified', 'violated'} for row in chosen),
                    'native_api_and_chain': native_receipt.get('model_run', {}).get('status') == 'success'
                        and native_receipt.get('native_financial_chain') is True and not data.get('partial', True),
                    'kernel_replay': receipt['kernel_replay'].get('status') == 'passed',
                    'native_replay': receipt['agent_replay'].get('status') == 'passed'
                        and receipt['agent_replay'].get('network_calls') == 0,
                }
                receipt['customer_summary'] = subset['counts']
                receipt['all_excerpt_summary'] = data['summary']
                receipt['status'] = ('passed_live_local_chain_quality_pending' if code == 0
                                     and all(receipt['checks'].values()) else 'needs_improvement')
            else:
                receipt.update(status='failed', reason='production_task_has_no_results')
    except BaseException as exc:
        # No credential is handled here. The delegated production entrypoint
        # persists and redacts supplier exceptions inside its own receipt.
        receipt.update(status='failed', error_type=type(exc).__name__, error=(
            'Local native probe failed; inspect its redacted production receipt.'
            if receipt.get('phase') == 'execute_production_native_task' else str(exc)))
    finally:
        receipt['historical_seed_unchanged'] = protected_unchanged(selected['protected_hashes'])
        receipt['source_code_unchanged'] = source_lock() == locked_source
        if not receipt['historical_seed_unchanged'] or not receipt['source_code_unchanged']:
            receipt['status'] = 'failed_integrity_check'
        write_json(out/'acceptance.json', receipt)
    print(json.dumps({key: receipt.get(key) for key in ('status', 'customer_summary', 'all_excerpt_summary',
                                                      'real_provider_api', 'historical_seed_unchanged')}, ensure_ascii=False))
    return 0 if receipt['status'] in {'prepared_not_executed', 'passed_live_local_chain_quality_pending'} else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed-run', type=Path, default=ROOT/'runs/source-real-stable-16-full')
    parser.add_argument('--out', type=Path, required=True, help='New diagnostic directory; old evidence is never overwritten')
    parser.add_argument('--prepare-only', action='store_true', help='Only create the provenance-checked excerpt; no API or secret access')
    parser.add_argument('--model-calls', type=int, default=512)
    parser.add_argument('--tool-calls', type=int, default=4000)
    parser.add_argument('--tokens', type=int, default=2000000)
    parser.add_argument('--timeout', type=int, default=3600)
    args = parser.parse_args(argv)
    try:
        validate_args(args)
    except ValueError as exc:
        parser.error(str(exc))
    return execute(args)


if __name__ == '__main__':
    raise SystemExit(main())
