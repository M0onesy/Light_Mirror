"""Read-only exposed financial source review; no model or network request."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mingjing.smart.analysis import analyze
from mingjing.smart.claims import build_report_model
from mingjing.smart.detect import DocMeta
from mingjing.smart.docio import load_document
from mingjing.smart.tables import extract_filing_facts
from mingjing.smart.verify import summarize, verify_model


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def scoped_counts(claims, results):
    """Acceptance views retain unresolved company claims irrespective of label."""
    def count(selected):
        items = [(claim, result) for claim, result in zip(claims, results) if selected(claim)]
        assertion_results = {}
        for claim, result in items:
            assertion_results.setdefault(claim.assertion_id or claim.cid, set()).add(result.status)
        statuses = Counter(result.status for _, result in items)
        judged = statuses['verified'] + statuses['violated']
        eligible = len(items) - statuses['skipped']
        return {'occurrences': len(items), 'statuses': dict(statuses), 'judged': judged,
                'eligible': eligible, 'abstention_rate': statuses['unverifiable'] / eligible if eligible else None,
                'assertions': len(assertion_results),
                'assertion_statuses': dict(Counter(next(iter(values)) if len(values) == 1 else 'mixed'
                                                  for values in assertion_results.values())),
                'unresolved_cids': [claim.cid for claim, result in items if result.status == 'unverifiable']}
    historical = lambda claim: not claim.forecast and claim.modality != 'inference'
    company_history = lambda claim: historical(claim) and claim.entity in ('company', 'segment') \
                                    and claim.category not in ('market', 'template')
    return {'core': count(lambda c: c.category == 'core'),
            'historical_financial': count(lambda c: historical(c) and c.category in ('core', 'open_metric')),
            'company_historical_acceptance': count(company_history),
            'company_business_operating_and_unbound': count(lambda c: company_history(c)
                                                           and c.category not in ('core', 'open_metric')),
            'industry_external': count(lambda c: c.entity == 'external'),
            'forecast': count(lambda c: c.forecast or c.modality == 'inference'),
            'market_inputs': count(lambda c: c.category == 'market'),
            'template': count(lambda c: c.category == 'template')}


def execute(args):
    out = args.out.resolve()
    if ROOT not in out.parents or out.exists():
        raise ValueError('Use a new output directory inside the editable project')
    out.mkdir(parents=True)
    records = list(csv.DictReader(args.material_index.open(encoding='utf-8-sig', newline='')))
    latest = {}
    for row in sorted(records, key=lambda row: row['date']):
        if row['date'] > args.cutoff or not ('年报' in row['category'] or '招股书' in row['category']):
            continue
        year = re.search(r'(20\d{2})年年度报告', row['title'])
        latest[year[1] if year else 'prospectus'] = row
    facts, texts, doc_index, sources = [], [], {}, []
    for key, row in latest.items():
        source = args.material_index.parent / row['file']
        if sha(source) != row['sha256']:
            raise ValueError('Official source differs from acquisition manifest')
        doc = load_document(source)
        meta = DocMeta(company=args.company, stock_code=row['code'], report_date=row['date'],
                       period=key + 'Y' if key != 'prospectus' else '',
                       doc_type='annual_report' if key != 'prospectus' else 'prospectus')
        extracted, diagnostics = extract_filing_facts(doc, meta)
        facts.extend(extracted)
        doc_index[doc.sha256[:12]] = {**asdict(meta), 'name': source.name}
        texts.extend({'name': source.name, 'sha': doc.sha256, 'page': page.number, 'text': page.flow,
                      'company': args.company, 'stock_code': row['code'], 'report_date': row['date']}
                     for page in doc.pages)
        sources.append({'path': str(source), 'sha256': doc.sha256, 'date': row['date'], 'url': row['url'],
                        'pages': len(doc.pages), 'facts': len(extracted), 'diagnostics': diagnostics})
        print(json.dumps({'source': key, 'pages': len(doc.pages), 'facts': len(extracted)}, ensure_ascii=False), flush=True)
    doc = load_document(args.report)
    meta = DocMeta(company=args.company, stock_code=args.stock_code, report_date=args.cutoff,
                   doc_type='research_report', base_year=args.base_year)
    model = build_report_model(doc, meta, {fact.dimension_id for fact in facts if fact.dimension_kind != 'total'})
    write(out / 'fresh_facts.json', [fact.to_dict() for fact in facts])
    write(out / 'report_model.json', asdict(model))
    receipt = {'schema': 'mingjing.finance-direct-source-review.v1',
               'review_status': 'exposed_developer_review_not_professional_gold',
               'source_profile': 'fresh_parse_original_pdfs', 'api_calls': 0,
               'report': {'path': str(args.report), 'sha256': doc.sha256, 'pages': len(doc.pages)},
               'cutoff': args.cutoff, 'sources': sources, 'cases': []}
    receipt['source_sha256'] = {str(path.relative_to(ROOT)): sha(path) for path in
                                [Path(__file__), *[ROOT / 'mingjing' / 'smart' / name for name in
                                                   ('common.py', 'claims.py', 'tables.py', 'taxonomy.py', 'verify.py', 'analysis.py')]]}
    for policy in ('standard', 'strict'):
        results = verify_model(facts, model, policy=policy, texts=texts, doc_index=doc_index)
        items = [{**claim.to_dict(), **result.to_dict()} for claim, result in zip(model.claims, results)]
        result_file = out / (policy + '_check.json')
        summary = summarize(model.claims, results)
        write(result_file, {'summary': summary, 'items': items})
        analysis = analyze(facts, args.company, texts=texts, report_date=args.cutoff, policy=policy, scope='合并')
        write(out / (policy + '_analysis.json'), analysis)
        suspect = [item['cid'] for item in items if item['status'] == 'verified' and not item['forecast']
                   and item['entity'] in ('company', 'segment') and item['category'] not in ('template', 'market')
                   and (item['basis'] == 'identity' or any(operand.get('source_doc') == 'report' for operand in item['operands']))]
        case = {'policy': policy, 'summary': summary, 'historical_self_proof': suspect,
                'scope_counts': scoped_counts(model.claims, results),
                'statuses': dict(Counter(result.status for result in results)),
                'abstentions': dict(Counter(result.abstain_code for result in results if result.status == 'unverifiable')),
                'analysis': {'metrics': len(analysis['metrics']), 'cells': sum(len(row['cells']) for row in analysis['metrics']),
                             'checks': analysis['check_summary'], 'blocks': dict(Counter(block['abstain_code'] for block in analysis['blocks']))},
                'result_path': str(result_file), 'result_sha256': sha(result_file)}
        receipt['cases'].append(case)
        write(out / 'receipt.json', receipt)
        print(json.dumps({'policy': policy, 'core': summary['core'], 'statuses': case['statuses'],
                          'abstentions': case['abstentions'], 'self_proof': suspect, 'analysis': case['analysis']}, ensure_ascii=False), flush=True)
    receipt['source_unchanged'] = all(sha(Path(source['path'])) == source['sha256'] for source in sources)
    receipt['report_unchanged'] = sha(args.report) == doc.sha256
    write(out / 'receipt.json', receipt)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--material-index', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--company', default='并行科技')
    parser.add_argument('--stock-code', default='839493')
    parser.add_argument('--cutoff', default='2024-04-22')
    parser.add_argument('--base-year', type=int, default=2023)
    execute(parser.parse_args())
