"""Inspect all source-backed blockers before spending another model budget.

This developer tool keeps every occurrence and makes no acceptance claim. It
uses the same ledger, fact queries, rule registry and Judge as production.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mingjing.coverage import CoverageLedger, partition_document
from mingjing.coverage_stats import PRIMARY_SCOPE, attach_coverage_statistics
from mingjing.evidence import sha256_file
from mingjing.facts import FactStore
from mingjing.judge import Judge
from mingjing.parser import load_or_extract_facts
from mingjing.source_semantics import freeze_source_dimensions


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--corpus-addition', action='append', type=Path, default=[])
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.out.exists():
        raise ValueError('Diagnostics require a new output directory; frozen runs are read-only')
    state = json.loads(args.state.read_text(encoding='utf-8'))
    request = state['request']
    draft = Path(request['draft'])
    sources = [Path(request['corpus']), *(Path(p) for p in request.get('corpus_additions', [])),
               *args.corpus_addition]
    company, period = request['company'], request['period']
    facts = []
    for source in sources:
        print(json.dumps({'phase': 'facts', 'source': str(source)}, ensure_ascii=False), flush=True)
        facts.extend(load_or_extract_facts(source, company, period))
    store = FactStore(facts)
    text, pages, blocks = partition_document(draft)
    ledger, claims = CoverageLedger.build(text, pages, blocks, company, period)
    claims = [freeze_source_dimensions(replace(c, table_context={**c.table_context, 'source_scope_required': True}))
              for c in claims]
    judge = Judge(store, request['report_date'])
    results = []
    for claim in claims:
        verdict = judge.verify(claim).to_dict()
        results.append(verdict)
        ledger.complete(claim.claim_id, verdict)
    payload = {'run_id': args.out.name, 'task_type': 'check', 'company': company, 'period': period,
               'report_date': request['report_date'], 'draft_text': text,
               'claims': [c.to_dict() for c in claims], 'facts': store.to_list(), 'results': results,
               'coverage': ledger.to_dict(), 'partial': False,
               'mode': 'deterministic_development_diagnostic',
               'draft_sha256': sha256_file(draft), 'input_sources': [str(p.resolve()) for p in sources]}
    attach_coverage_statistics(payload)
    stats = payload['coverage_statistics']
    core = [r for r in results if stats['claim_scopes'].get(r['claim_id']) == PRIMARY_SCOPE]
    summary = {'schema_version': 'financial-coverage-diagnostic.v1', 'core': stats['core'],
               'facts': len(store.facts), 'claims': len(claims),
               'all_blockers': dict(Counter((b['code']+':'+b['kind']) for r in core for b in r.get('blockers', []))),
               'first_reasons': dict(Counter(r['reason'] for r in core if r['status'] == 'abstained')),
               'scope': 'development_diagnostic_not_independent_financial_acceptance'}
    args.out.mkdir(parents=True)
    for name, data in [('results.json', payload), ('diagnostic.json', summary)]:
        (args.out/name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
