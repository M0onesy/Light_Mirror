"""Read-only extraction diagnostic for an already-exposed development report."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from mingjing.coverage import CoverageLedger, partition_document
from mingjing.parser import extract_facts, extract_text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--pages', type=int, nargs='*', default=[])
    args = parser.parse_args()
    state = json.loads(args.state.read_text(encoding='utf-8'))
    request = state['request']
    draft = Path(request['draft'])
    original_project = args.state.resolve().parents[2]
    corpus = Path(request['corpus'])
    if not corpus.is_absolute():
        corpus = (original_project / corpus).resolve()
    text, pages, blocks = partition_document(draft)
    ledger, claims = CoverageLedger.build(text, pages, blocks, request['company'], request['period'])
    facts = extract_facts(corpus, request['company'], request['period'])
    args.out.mkdir(parents=True, exist_ok=True)
    for name, data in [('coverage.json', ledger.to_dict()), ('claims.json', [c.to_dict() for c in claims]),
                       ('facts.json', [f.to_dict() for f in facts])]:
        (args.out/name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    for number in args.pages:
        region = pages[number-1]
        (args.out/f'page-{number:02}.txt').write_text(text[region['start']:region['end']], encoding='utf-8')
    result = {'claims': len(claims), 'facts': len(facts), 'claim_accounts': dict(Counter(c.account for c in claims)),
              'parsed_claims': sum(c.parse_status == 'parsed' for c in claims),
              'fact_accounts': dict(Counter(f.account for f in facts)),
              'coverage': {k:v for k,v in ledger.to_dict().items() if k not in {'pages','blocks','mentions'}},
              'scope': 'development_material_machine_extraction_not_independent_financial_gold'}
    (args.out/'summary.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
