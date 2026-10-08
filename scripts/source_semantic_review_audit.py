"""Read-only development comparison of numeric parsing versus semantic review.

No providers, no gold labels, and no writes to the archived source run. Optional
fixture exports only the relevant original text pages, not the source PDF.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mingjing.coverage import CoverageLedger
from mingjing.judge import _source_scope_issue, _source_semantic_issue
from mingjing.source_semantics import semantic_review_required, source_binding_issue


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def review(run, out, fixture=None):
    run, out = run.resolve(strict=True), out.resolve()
    if out == run or run in out.parents or (fixture and (fixture.resolve() == run or run in fixture.resolve().parents)):
        raise ValueError('Read-only review outputs must be outside the archived run')
    names = ['results.json', 'claims.json', 'coverage.json', 'agent_state.json', 'run_manifest.json']
    before = {name: sha(run/name) for name in names}
    data = json.loads((run/'results.json').read_text(encoding='utf-8'))
    original = data['coverage']
    ledger, claims = CoverageLedger.build(data['draft_text'], original['pages'], original['blocks'], data['company'], data['period'])
    old_claims = {(c['start'], c['end']): c for c in data['claims']}
    old_entries = {(e['start'], e['end']): e for e in original['mentions']}
    new_entries = {(e['start'], e['end']): e for e in ledger.entries}
    reviewed = [c for c in claims if semantic_review_required(c)]
    rows = []
    for claim in reviewed:
        old = old_claims.get((claim.start, claim.end), {})
        rows.append({'original_claim_id': old.get('claim_id'), 'rebuilt_claim_id': claim.claim_id,
                     'start': claim.start, 'end': claim.end, 'account': claim.account,
                     'period': claim.period, 'candidate_value': claim.candidate_value, 'unit': claim.unit,
                     'numeric_text': claim.numeric_text, 'text': claim.text,
                     'before_parse_status': old.get('parse_status'), 'after_parse_status': claim.parse_status,
                     'expected_kernel_issue': source_binding_issue(claim, strict=True),
                     'page': next((p['page'] for p in original['pages'] if p['start'] <= claim.start < p['end']), None)})
    preflight = {c.claim_id: (_source_semantic_issue(c) or _source_scope_issue(c, strict=True, check_binding=False)
                             or source_binding_issue(c, strict=True))
                 for c in claims if c.parse_status == 'parsed' and not semantic_review_required(c)}
    new_exclusions = [{**new, 'old_status': old_entries[span]['status'], 'old_reason': old_entries[span]['reason']}
                      for span, new in new_entries.items() if span in old_entries and new['status'] in {'rule_excluded','out_of_scope'}
                      and old_entries[span]['status'] not in {'rule_excluded', 'out_of_scope'}]
    explicit = [e for e in original['mentions'] if any(unit in e['text'] for unit in ['%', '\uff05', '\u5143'])]
    rejected_explicit = [e for e in explicit if (e['start'],e['end']) in {(n['start'],n['end']) for n in new_exclusions}]
    after = {name: sha(run/name) for name in names}
    if before != after:
        raise ValueError('Archived source changed during read-only review')
    receipt = {'kind': 'development_numeric_semantic_review', 'gold_annotation': False,
               'professional_acceptance': False, 'live_model_executed': False,
               'source_run': run.name, 'archived_hashes_before': before, 'archived_hashes_after': after,
               'archived_run_unchanged': before == after,
               'draft_pdf_sha256': sha(Path(data['draft'])),
               'occurrences_before': len(old_entries), 'occurrences_after': len(new_entries),
               'occurrence_spans_unchanged': set(old_entries)==set(new_entries),
               'claims_before': len(data['claims']), 'claims_after': len(claims),
               'parse_before': dict(Counter(c['parse_status'] for c in data['claims'])),
               'parse_after': dict(Counter(c.parse_status for c in claims)),
               'semantic_review_candidates': len(reviewed), 'semantic_review_rows': rows,
               'deterministic_preflight_candidates': sum(bool(v) for v in preflight.values()),
               'native_eligible_candidates': sum(c.parse_status=='parsed' and not preflight.get(c.claim_id) for c in claims),
               'new_source_exclusions': new_exclusions,
               'new_rule_exclusions': [e for e in new_exclusions if e['status']=='rule_excluded'],
               'new_out_of_scope_exclusions': [e for e in new_exclusions if e['status']=='out_of_scope'],
               'explicit_amount_percent_occurrences': len(explicit), 'new_explicit_amount_percent_exclusions': rejected_explicit,
               'limits': ['Rebuild eligibility is not evidence of a live model submission.',
                          'Unknown financial definitions remain unverified after A/B agreement.',
                          'Full-report financial recall requires independent human gold.']}
    save(out, receipt)
    if fixture:
        pages = []
        needed = {r['page'] for r in rows}
        for page in original['pages']:
            if page['page'] not in needed:
                continue
            start, end = page['start'], page['end']
            window = dict(page, start=0, end=end-start,
                          lines=[dict(line, start=line['start']-start, end=line['end']-start) for line in page.get('lines',[])])
            blocks = [dict(b, start=b['start']-start, end=b['end']-start) for b in original['blocks'] if b.get('page')==page['page']]
            pages.append({'source_start': start, 'text': data['draft_text'][start:end], 'page': window, 'blocks': blocks})
        save(fixture, {'origin': 'development_observation_not_human_gold', 'source_run': run.name,
                       'draft_pdf_sha256': receipt['draft_pdf_sha256'], 'results_sha256': before['results.json'],
                       'company': data['company'], 'period': data['period'], 'windows': pages, 'observations': rows})
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--fixture', type=Path)
    args = parser.parse_args()
    receipt = review(args.run, args.out, args.fixture)
    print(json.dumps({key:receipt[key] for key in ['claims_before','claims_after','parse_after',
          'semantic_review_candidates','native_eligible_candidates','deterministic_preflight_candidates',
          'occurrence_spans_unchanged','archived_run_unchanged','new_explicit_amount_percent_exclusions']}, ensure_ascii=False))
