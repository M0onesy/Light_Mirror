"""Read a frozen development run and diagnose source coverage, without APIs.

This is not gold annotation, financial acceptance or a benchmark score. It
does not change the frozen run and never matches facts by candidate amounts.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mingjing.coverage import CoverageLedger, _policy_table_regions


def read(path: Path):
    return json.loads(path.read_text(encoding='utf-8'))


def save(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def source_category(claim: dict, scopes: list[dict]) -> str:
    """Review aids only; these categories never decide a financial verdict."""
    if any(s['start'] <= claim['start'] < s['end'] for s in scopes):
        return 'explicit_policy_target_table'
    source = re.sub(r'\s+', '', claim.get('text', ''))
    if re.search(r'CAGR|复合', source, re.I):
        return 'compound_growth_requires_registered_formula'
    if re.search(r'美元|欧元|港元|英镑', source):
        return 'currency_or_external_subject_needs_binding'
    if re.search(r'超算云|行业云|AI云|分部|分产品|客户|采购算力|CPU核时|共建模式', source):
        return 'segment_customer_or_operational_dimension'
    if re.search(r'市场份额|市占率|全球.*市场|中国.*算力|智能算力', source):
        return 'industry_subject_needs_explicit_scope'
    if claim.get('account', '').endswith('_UNSPECIFIED'):
        return 'unspecified_financial_definition'
    if re.search(r'费用率', source):
        return 'expense_ratio_requires_registered_definition'
    return 'unclassified_requires_source_review'


def review(run: Path, out: Path) -> dict:
    run = run.resolve(strict=True)
    out = out.resolve()
    if out == run or run in out.parents:
        raise ValueError('审查输出必须位于冻结运行目录之外')
    original = read(run / 'coverage.json')
    claims = read(run / 'claims.json')
    facts = read(run / 'facts.json')
    state = read(run / 'agent_state.json')
    results = read(run / 'results.json')
    text = results['draft_text']
    pages = {p['id']: p for p in original['pages']}
    blocks = {b['id']: b for b in original['blocks']}
    by_span = {(e['start'], e['end']): e for e in original['mentions']}
    verdicts = {v['claim_id']: v for v in results.get('results', [])}
    scopes = _policy_table_regions(text, original['pages'])
    ledger, rebuilt_claims = CoverageLedger.build(
        text, original['pages'], original['blocks'], state['request']['company'], state['request'].get('period', ''))
    rebuilt_entries = {(e['start'], e['end']): e for e in ledger.entries}
    pending = []
    for entry in original['mentions']:
        if entry['status'] != 'pending' or entry.get('claim_id'):
            continue
        page = pages.get(entry.get('page_id'), {})
        block = blocks.get(entry.get('block_id'), {})
        left = text.rfind('\n', 0, entry['start']) + 1
        right = text.find('\n', entry['start'])
        if right < 0:
            right = len(text)
        current = rebuilt_entries.get((entry['start'], entry['end']), {})
        pending.append({'id': entry['id'], 'page': page.get('page'),
                        'start': entry['start'], 'end': entry['end'], 'numeric_text': entry['text'],
                        'source_line': text[left:right], 'source_block': block.get('text', ''),
                        'rebuilt_status': current.get('status'), 'rebuilt_reason': current.get('reason'),
                        'rebuilt_claim_id': current.get('claim_id')})
    roles = state.get('bindings', {})
    scope_rows = []
    for claim in claims:
        mid = claim['claim_id']
        a = roles.get('planner', {}).get(mid)
        b = roles.get('verifier', {}).get(mid)
        if a is None and b is None:
            continue
        scope_rows.append({'claim_id': mid, 'source_scope_hint': claim.get('caliber_hint'),
                           'planner_scope': a.get('scope') if a else None,
                           'verifier_scope': b.get('scope') if b else None,
                           'agreement': state.get('agreements', {}).get(mid),
                           'status': verdicts.get(mid, {}).get('status'),
                           'abstention_code': verdicts.get(mid, {}).get('abstention_code')})
    dimensions = {(f['company'], f['account'], f['period'], f['scope']) for f in facts}
    absent = []
    for claim in claims:
        if not claim.get('period') or claim.get('account') == 'UNKNOWN':
            continue
        scope_hint = next((s for s in claim.get('caliber_hint', []) if s in {'合并', '母公司'}), '')
        periods = [claim['period']] + ([claim['base_period']] if claim.get('base_period') else [])
        missing = [period for period in periods if
                   (claim.get('company'), claim['account'], period, scope_hint) not in dimensions]
        if missing:
            absent.append({'claim_id': claim['claim_id'], 'company': claim.get('company'),
                           'account': claim['account'], 'scope_hint': scope_hint,
                           'missing_periods': missing,
                           'note': 'dimension inventory only; unknown scope is not defaulted'})
    unknowns = [{**c, 'review_category': source_category(c, scopes)} for c in claims if c['account'] == 'UNKNOWN']
    parsed_before = {(c['start'], c['end']) for c in claims if c['parse_status'] == 'parsed'}
    parsed_after = {(c.start, c.end) for c in rebuilt_claims if c.parse_status == 'parsed'}
    summary = {
        'kind': 'development_source_semantics_review',
        'gold_annotation': False, 'acceptance_pass': None,
        'source_run': run.name,
        'source_hashes': {name: hashlib.sha256((run / name).read_bytes()).hexdigest()
                          for name in ['claims.json', 'facts.json', 'coverage.json', 'agent_state.json', 'results.json']},
        'frozen_results_summary': results.get('summary'),
        'frozen_model': {key: results.get('model', {}).get(key) for key in
                         ['status', 'semantic_claims', 'mapped_claims', 'request_count', 'prompt_count', 'tokens', 'cost']},
        'original_claims': len(claims),
        'original_parse_status': dict(Counter(c['parse_status'] for c in claims)),
        'original_unknown_accounts': len(unknowns),
        'original_unknown_review_categories': dict(Counter(c['review_category'] for c in unknowns)),
        'original_pending_without_claim': len(pending),
        'original_pending_pages': dict(Counter(str(pages.get(e.get('page_id'), {}).get('page'))
                                             for e in original['mentions'] if e['status'] == 'pending' and not e.get('claim_id'))),
        'pending_rebuild_transitions': dict(Counter(f"{e['rebuilt_status']}:{e['rebuilt_reason']}" for e in pending)),
        'rebuilt_claims': len(rebuilt_claims),
        'rebuilt_parse_status': dict(Counter(c.parse_status for c in rebuilt_claims)),
        'rebuilt_orphan_pending': sum(e['status'] == 'pending' and not e.get('claim_id') for e in ledger.entries),
        'parsed_occurrences_retained': len(parsed_before & parsed_after),
        'parsed_occurrences_before': len(parsed_before),
        'verified_with_missing_or_empty_role_scope': [e['claim_id'] for e in scope_rows
                                                     if e['status'] == 'verified' and
                                                     (not e['planner_scope'] or not e['verifier_scope'])],
        'fact_periods': sorted({f['period'] for f in facts}),
        'fact_companies': sorted({f['company'] for f in facts}),
        'claim_dimension_inventory_absences': len(absent),
        'limitations': ['All candidate occurrences remain source-addressed in the rebuilt ledger.',
                        'A parse failure is X6, not verified and not newly inferred financial information.',
                        'Dimension inventory does not prove fact absence; scope, disclosure dates and evidence still require checking.',
                        'This review does not measure financial recall, correctness, gold accuracy or model quality.'],
    }
    out.mkdir(parents=True, exist_ok=True)
    save(out / 'summary.json', summary)
    save(out / 'pending_source_review.json', pending)
    save(out / 'unknown_account_review.json', unknowns)
    save(out / 'role_scope_review.json', scope_rows)
    save(out / 'dimension_inventory_absences.json', absent)
    save(out / 'coverage_rebuild.json', ledger.to_dict())
    (out / 'README.md').write_text(
        '# 开发材料源语义审查\n\n'
        f'读取冻结运行 `{run.name}`，未调用模型、未更改原运行。\n\n'
        '这些计数只描述源定位、解析与维度库存，不是人工金标、召回率、准确率或验收成绩。'
        '新账本中的未解析提及保留原文字符定位，由确定性内核输出 X6；不能视为成功核查。\n', encoding='utf-8')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    summary = review(args.run, args.out)
    sys.stdout.reconfigure(encoding='utf-8')
    print(json.dumps({key: summary[key] for key in ['source_run', 'original_pending_without_claim',
                     'rebuilt_claims', 'rebuilt_orphan_pending', 'parsed_occurrences_retained',
                     'verified_with_missing_or_empty_role_scope']}, ensure_ascii=False))
