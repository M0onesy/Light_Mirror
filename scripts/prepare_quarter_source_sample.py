"""Prepare a complete predeclared quarterly paragraph/figure development sample.

Selection uses original geometry, not candidate amounts or eventual verdicts.
All financial occurrences in the selected regions remain in the task ledger.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mingjing.evidence import sha256_file
from scripts.source_customer_table_acceptance import extract_table_excerpt, protected_unchanged

DRAFT = '7a70da91863feb0ce795748619f02b21a2b68ab6ec134c5520239b658e686a9d'
SOURCE = '31e53f84d52e48fbc5889fdcfbb2588adf45528af750f211649295ed7f5c2d04'
FIRST_INQUIRY = 'ed4bf57ba31ee1d7e87c40052903ecfbd0d12791af1b82ac6ad3b28e673dc6dd'
# Keep both complete adjacent figures and the intervening paragraph. Removing
# that paragraph changes the text-derived figure extent and loses the axis
# proof; the six earlier business-mode occurrences are retained and counted
# as repeated validation, rather than being cropped to improve the score.
REGIONS = ((30, (170, 589, 568, 703)), (31, (170, 70, 568, 591)))


def prepare(seed: Path, out: Path) -> dict:
    seed, out = seed.resolve(strict=True), out.resolve()
    if out.exists() or out == seed or out.is_relative_to(seed) or seed.is_relative_to(out):
        raise ValueError('Sample output must be new and separate from the historical seed')
    lock, result = seed / 'agent_lock.json', seed / 'results.json'
    data = json.loads(lock.read_text(encoding='utf-8'))
    baseline = json.loads(result.read_text(encoding='utf-8'))
    drafts = [r for r in data['resources'] if r.get('id') == 'draft' and r.get('sha256') == DRAFT]
    sources = [r for r in data['resources'] if r.get('sha256') in {SOURCE, FIRST_INQUIRY} and r.get('id', '').startswith('report-')]
    if len(drafts) != 1 or len(sources) != 2 or baseline.get('report_date') != '2024-04-22':
        raise ValueError('Original immutable source identities or cutoff are unavailable')
    report = next(r for r in sources if r['sha256'] == SOURCE)
    addition = next(r for r in sources if r['sha256'] == FIRST_INQUIRY)
    if any(not r.get('publish_date') or r['publish_date'] > '2024-04-22' for r in sources):
        raise ValueError('Future or undated evidence cannot enter the historical sample')
    protected = {str(lock): sha256_file(lock), str(result): sha256_file(result)}
    for resource in [drafts[0], *sources]:
        path = Path(resource['path'])
        if not path.is_file() or sha256_file(path) != resource['sha256']:
            raise ValueError('Original source bytes differ from their historical lock')
        protected[str(path)] = resource['sha256']
    out.mkdir(parents=True)
    target = out / 'quarter-original-pages-30-31.pdf'
    excerpt = extract_table_excerpt(drafts[0]['path'], target, regions=REGIONS)
    excerpt.update(sample_kind='complete_original_quarter_income_paragraph_and_figure',
                   source_financial_recall='not_measured_requires_independent_gold')
    (out / 'excerpt_manifest.json').write_text(json.dumps(excerpt, ensure_ascii=False, indent=2), encoding='utf-8')
    request = {'draft': str(target), 'corpus': report['path'], 'corpus_additions': [addition['path']],
               'company': baseline['company'], 'period': '', 'report_date': '2024-04-22',
               'mode': 'harness', 'task_type': 'check', 'facts': '', 'knowledge_root': '',
               'scope_declaration': ''}
    state = out / 'diagnostic-seed'
    state.mkdir()
    (state / 'agent_state.json').write_text(json.dumps({'request': request}, ensure_ascii=False, indent=2), encoding='utf-8')
    receipt = {'schema_version': 'quarter-source-local-sample.v1', 'status': 'prepared_not_executed',
               'full_report_acceptance': False, 'real_provider_api': 'not_executed',
               'request': request, 'protected_hashes': protected,
               'historical_seed_unchanged': protected_unchanged(protected),
               'source_resources': sources,
               'sample_manifest_sha256': sha256_file(out / 'excerpt_manifest.json'),
               'selection_basis': 'complete_original_paragraph_and_adjacent_figure_geometry_not_candidate_values',
               'all_excerpt_candidates_retained': True}
    if not receipt['historical_seed_unchanged']:
        raise ValueError('Historical resources changed during sample preparation')
    (out / 'preparation.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed-run', type=Path, default=ROOT / 'runs/source-real-stable-16-full')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    record = prepare(args.seed_run, args.out)
    print(json.dumps({k: record[k] for k in ('status', 'historical_seed_unchanged')}, ensure_ascii=False))
