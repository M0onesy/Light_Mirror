"""Observable original-PDF extraction QA, never independent human gold."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mingjing.coverage import partition_document
from mingjing.evidence import sha256_file
from mingjing.facts import FactStore
from mingjing.judge import Judge
from mingjing.parser import build_evidence, extract_facts
from mingjing.table_mentions import extract_table_mentions


def run_qa(draft: Path, annual: Path, out: Path):
    if out.exists():
        raise ValueError('禁止覆盖已有真实材料观察记录')
    out.mkdir(parents=True)
    text, pages, blocks = partition_document(draft)
    claims = extract_table_mentions(text, pages, blocks, '并行科技', '2023Y')
    facts = extract_facts(annual, '并行科技', '2023Y')
    store = FactStore(facts)
    judge = Judge(store, '2024-04-22')
    observed = [c for c in claims if c.modality == 'fact']
    outputs = [judge.verify(c).to_dict() for c in observed]
    # This diagnostic does not attest the scope. It explicitly separates
    # extraction/rounding/calculation mechanics from the missing semantic gate.
    subset = [c for c in observed if c.parse_status == 'parsed' and c.account in
              {'REVENUE', 'COST', 'NP', 'NP_PARENT', 'CFO', 'ASSETS', 'LIABILITIES',
               'GROSS_MARGIN', 'DEBT_RATIO'}]
    diagnostic = [judge.verify(replace(c, caliber_hint=('合并', c.caliber_hint[1]),
                                      binding_status='single')).to_dict() for c in subset]
    evidence = {e.evidence_id: e.to_dict() for e in build_evidence(annual)}
    linked = {eid: evidence[eid] for r in diagnostic for eid in r['evidence'] if eid in evidence}
    metrics = {
        'schema_version': 'original-pdf-observation.v1',
        'status': 'observable_extraction_diagnostics_not_professional_acceptance',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'draft': {'path': str(draft.resolve()), 'sha256': sha256_file(draft), 'pages': len(pages)},
        'annual': {'path': str(annual.resolve()), 'sha256': sha256_file(annual), 'facts': len(facts)},
        'table_occurrences': len(claims),
        'historical_occurrences': len(observed),
        'forecast_occurrences': sum(c.modality == 'forecast' for c in claims),
        'valuation_out_of_scope': sum(c.modality == 'opinion' for c in claims),
        'numeric_original_span_matches': {'matched': sum(text[c.start:c.end] == c.numeric_text for c in claims), 'total': len(claims)},
        'parsed_historical': sum(c.parse_status == 'parsed' for c in observed),
        'raw_historical_statuses': dict(Counter(r['status'] for r in outputs)),
        'raw_historical_abstention_codes': dict(Counter(r['abstention_code'] for r in outputs)),
        'diagnostic_only': {'binding': 'explicit_temporary_consolidated_scope_assumption_not_independent_gold',
                            'occurrences': len(subset),
                            'statuses': dict(Counter(r['status'] for r in diagnostic)),
                            'abstention_codes': dict(Counter(r['abstention_code'] for r in diagnostic)),
                            'linked_original_pdf_evidence': len(linked),
                            'evidence_pages': sorted({e['page'] for e in linked.values()})},
        'recall': 'unknown_without_independent_gold',
        'semantic_binding_accuracy': 'not_measured_no_model_or_human_binding_used',
        'professional_review': 'pending', 'model_requests': 0,
        'limitations': ['Known table headers only; layout coverage is incomplete',
                        'Scope must be bound independently; generic ROE, net margin and diluted EPS remain unresolved',
                        'No independent human gold, no held-out accuracy, no cell-level geometry measurement'],
    }
    payloads = {'claims.json': [c.to_dict() for c in claims], 'facts.json': [f.to_dict() for f in facts],
                'results.json': outputs, 'diagnostic_results.json': diagnostic,
                'linked_evidence.json': list(linked.values()), 'qa.json': metrics}
    for name, payload in payloads.items():
        (out / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (out / 'audit.json').write_text(json.dumps({'kind': 'original_pdf_observation',
        'professional_acceptance': False, 'files': {name: hashlib.sha256((out/name).read_bytes()).hexdigest()
                                                   for name in payloads}}, indent=2) + '\n', encoding='utf-8')
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--draft', type=Path)
    parser.add_argument('--annual', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    draft = args.draft or next(ROOT.parent.rglob('*AP202404221630960822.pdf'))
    annual = args.annual or next(ROOT.parent.rglob('920493_*/年报/2024-04-15_2023年年度报告.pdf'))
    result = run_qa(draft, annual, args.out)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
