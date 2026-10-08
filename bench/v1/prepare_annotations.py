"""Prepare source-linked A/P review candidates; never generate human gold."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import csv
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mingjing.evidence import make_evidence_id, sha256_file
from mingjing.facts import FactStore
from mingjing.parser import extract_facts, extract_text
from mingjing.analysis import gross_margin, net_margin, debt_ratio, cash_content
from mingjing.decimal_math import fact_interval
from mingjing.rules import execute_rule_interval

METRICS = [
    ('REVENUE', '营业收入'), ('COST', '营业成本'), ('NP', '净利润'),
    ('NP_PARENT', '归母净利润'), ('NP_PARENT_ADJ', '扣非归母净利润'),
    ('CFO', '经营活动现金流量净额'), ('ASSETS', '资产总计'), ('LIABILITIES', '负债合计'),
    ('EQUITY_PARENT', '归母权益'), ('EPS', '基本每股收益'), ('ROE', '加权平均净资产收益率'),
    ('GROSS_MARGIN', '毛利率'), ('NET_MARGIN', '净利率'), ('DEBT_RATIO', '资产负债率'),
    ('CASH_CONTENT', '经营现金流含金量'),
]
FORMULAS = {
    'GROSS_MARGIN': (('REVENUE', 'COST'), gross_margin, '(REVENUE-COST)/REVENUE'),
    'NET_MARGIN': (('NP', 'REVENUE'), net_margin, 'NP/REVENUE'),
    'DEBT_RATIO': (('LIABILITIES', 'ASSETS'), debt_ratio, 'LIABILITIES/ASSETS'),
    'CASH_CONTENT': (('CFO', 'NP'), cash_content, 'CFO/NP'),
}


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _jsonl(path, rows):
    with path.open('w', encoding='utf-8') as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')


def previous_exposures():
    # D/D2 source documents are deliberately not opened in this preparation.
    # Only their already-published issuer metadata excludes reserved cohorts.
    # Development/MVP issuers are eligible for A/P. Only already consumed
    # holdout cohorts are excluded here; all inspected A/P issuers remain
    # excluded from future D3 by the output exposure registry.
    used = set()
    for name in ('holdout_seal_manifest.json', 'holdout2_seal_manifest.json'):
        path = ROOT/'bench'/name
        if path.exists():
            used.update(str(r['ticker']) for r in json.loads(path.read_text(encoding='utf-8'))['reports'])
    return used


def _complete_annual_report_year(row):
    """A complete dated annual-report caption, not a substring document match."""
    if row.get('category') != '年报':
        return None
    filename = Path(row.get('file', '')).stem
    filename = re.sub(r'^20\d{2}-\d{2}-\d{2}_', '', filename)
    allowed = re.compile(r'^(20\d{2})年(?:年度|年年度)报告(?:[（(](?:更正(?:后|版)?|修订(?:后|版)?|更新(?:后|版)?|修订稿)[）)])?$')
    match = allowed.fullmatch(filename)
    title = re.sub(r'\s+', '', row.get('title', '')).removesuffix('.pdf')
    if not match or row.get('year') != match[1]:
        return None
    if title and not allowed.fullmatch(title):
        return None
    return match[1]


def select_reports(corpus, companies=20, *, allowed_tickers=None, allow_incomplete=False):
    root = Path(corpus).resolve()
    index = root/'index.csv'
    if not index.is_file():
        raise ValueError('需要现有语料 index.csv，不能靠PDF内容猜公司身份')
    grouped = defaultdict(lambda: defaultdict(list))
    reserved = previous_exposures()
    with index.open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            if (_complete_annual_report_year(row) and row.get('code') not in reserved
                    and (allowed_tickers is None or row.get('code') in allowed_tickers)):
                grouped[row['code']][row['year']].append(row)
    selected = []
    for ticker, years in sorted(grouped.items()):
        if len(years) < 2:
            continue
        for year in sorted(years)[-2:]:
            # Prefer original annual disclosure when the index also has later
            # corrections; version review remains a separate annotation field.
            versions = sorted(years[year], key=lambda row: (row.get('date', ''), row['file']))
            row = next((r for r in versions if not any(w in r.get('title', '') for w in ('更正', '修订'))), versions[0])
            source = (root/row['file']).resolve()
            if not source.is_relative_to(root) or not source.is_file():
                raise ValueError('索引文件越界或缺失')
            if row.get('size_bytes') and source.stat().st_size != int(row['size_bytes']):
                raise ValueError('原PDF大小与索引不符')
            selected.append({**row, 'path': str(source), 'relative_path': source.relative_to(root).as_posix()})
        if len(selected) == companies*2:
            break
    if len(selected) != companies*2 and not allow_incomplete:
        raise ValueError('不足二十家独立公司各两年材料；不复制其他公司或期间填数')
    return root, selected


def inspect_source(row):
    path = Path(row['path'])
    sha = sha256_file(path)
    if row.get('sha256') and row['sha256'].lower() != sha:
        raise ValueError('原PDF哈希与语料索引不符')
    pages = extract_text(path)
    evidence = {}
    for page in pages:
        for block in page.get('blocks') or [{'text': page['text'], 'bbox': None}]:
            snippet = block['text'].strip()
            if not snippet:
                continue
            identifier = make_evidence_id(sha, page['page'], snippet, block.get('bbox'))
            evidence[identifier] = {'evidence_id': identifier, 'document_sha256': sha,
                                    'source_path': row['relative_path'], 'page': page['page'],
                                    'bbox': block.get('bbox'), 'text': snippet,
                                    'row': None, 'col': None,
                                    'location_status': 'source_block_candidate_not_human_confirmed_cell'}
    facts = extract_facts(path, row['name'], row['year']+'Y', row.get('date', ''))
    return {'source': {key: row.get(key) for key in ('code', 'name', 'year', 'date', 'relative_path')},
            'sha256': sha, 'bytes': path.stat().st_size, 'pages': len(pages),
            'facts': facts, 'evidence': evidence, 'page_texts': pages}


def _context(observation, account):
    labels = {'ROE': ['加权平均净资产收益率', '净资产收益率'],
              'DEBT_RATIO': ['资产负债率'], 'CASH_CONTENT': ['经营活动产生的现金流量净额', '净利润'],
              'NET_MARGIN': ['净利率', '净利润'], 'EQUITY_PARENT': ['归属于', '净资产']}.get(account, [])
    if not labels:
        labels = [dict(METRICS).get(account, account)]
    return [e for e in observation['evidence'].values() if any(t in e['text'] for t in labels)][:6]


def a_candidates(observations):
    records = []
    for observation in observations:
        src = observation['source']
        period = src['year']+'Y'
        store = FactStore(observation['facts'])
        sha = observation['sha256']
        def lookup(account):
            return store.query_outcome(src['name'], account, period, '合并',
                               '扣非' if account == 'NP_PARENT_ADJ' else '未扣非',
                               cutoff=src['date'], strategy='same_report', source_doc=sha[:12])
        for account, label in METRICS:
            outcome = lookup(account)
            fact = outcome.fact
            source_ids = list(fact.evidence) if fact else []
            computed = None
            if not fact and outcome.status == 'missing' and account in FORMULAS:
                names, calculate, formula = FORMULAS[account]
                operand_outcomes = [lookup(name) for name in names]
                operands = [result.fact for result in operand_outcomes]
                rule_id = {'GROSS_MARGIN':'FIN-gross-margin','NET_MARGIN':'FIN-net-margin',
                           'DEBT_RATIO':'FIN-debt-ratio','CASH_CONTENT':'FIN-cash-content'}[account]
                interval = execute_rule_interval(rule_id, {name: fact_interval(fact) for name, fact in zip(names, operands)}) if all(operands) else None
                value = calculate(*(f.value for f in operands)) if interval is not None else None
                computed = {'candidate_type': 'deterministic_formula_context_not_original_cell',
                            'value': str(value) if value is not None else None, 'formula': formula,
                            'rule_id': rule_id, 'expected_interval': interval.to_dict() if interval is not None else None,
                            'operands': [f.to_dict() for f in operands if f],
                            'status': 'computed_review_candidate' if value is not None else 'unknown',
                            'reason': None if value is not None else 'missing_operand_or_nonpositive_denominator'}
                source_ids = list(dict.fromkeys(e for f in operands if f for e in f.evidence))
            context = [observation['evidence'][eid] for eid in source_ids if eid in observation['evidence']]
            records.append({
                'id': f"A-{src['code']}-{period}-{account}", 'set': 'A', 'annotation_kind': 'financial_metric_source_cell',
                'annotation_status': 'pending_human', 'gold_origin': 'unreviewed_source_linked_candidate',
                'gold_value': None, 'gold_status': None, 'human_reviews': [],
                'company': src['name'], 'ticker': src['code'], 'period': period,
                'account_candidate': account, 'label': label, 'scope_candidate': '合并',
                'nr_candidate': '扣非' if account == 'NP_PARENT_ADJ' else '未扣非',
                'disclosure_date_candidate': src['date'],
                'source_document': {'path': src['relative_path'], 'sha256': sha, 'bytes': observation['bytes']},
                'candidate_status': 'source_cell_candidate' if fact else 'unknown',
                'observed_candidate': fact.to_dict() if fact else None,
                'computed_context': computed,
                'evidence_candidates': context or _context(observation, account),
                'unknown_reason': None if fact else outcome.reason or 'missing_or_unsupported_original_cell',
                'lookup_status': outcome.status,
                'review_requirements': ['independent_original_pdf_reading', 'exact_unit_period_scope_version',
                                        'cell_box_and_row_column_confirmation', 'three_human_reviewers'],
            })
    return records


def _restatement_rows(text):
    """Separate printed table cells from prose numbers and coalesced PDF rows."""
    output, labels, tokens, row_lines = [], [], [], []
    cell = re.compile(r'^[+\-－−]?\d[\d,，]*\.\d+$')
    def finish():
        if tokens and labels:
            output.append({'printed_numeric_tokens':list(tokens), 'row_text':'\n'.join(row_lines),
                           'row_label_candidate':''.join(labels)})
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if cell.fullmatch(line):
            tokens.append(line); row_lines.append(line)
        elif line in {'-', '—', '－'}:
            row_lines.append(line)
        else:
            if tokens:
                finish(); labels, tokens, row_lines = [], [], []
            if not any(w in line for w in ('调整重述前', '调整重述后')):
                labels.append(line); row_lines.append(line)
    finish()
    return output


def restatement_case(observation):
    # A heading alone is insufficient. Require explicit before/after columns
    # and at least one physical row with differing printed numeric pairs.
    for page in observation['page_texts']:
        compact = re.sub(r'\s+', '', page['text'])
        if not ('会计数据追溯调整或重述情况' in compact and '调整重述前' in compact and '调整重述后' in compact):
            continue
        if '□会计政策变更□会计差错更正□其他原因√不适用' in compact:
            continue
        candidates = []
        evidence = [e for e in observation['evidence'].values() if e['page'] == page['page']]
        column_headers = [e for e in evidence if '调整重述前' in e['text'] and '调整重述后' in e['text']]
        header_top = min((e['bbox'][1] for e in column_headers if e.get('bbox')), default=None)
        for block in evidence:
            if header_top is not None and block.get('bbox') and block['bbox'][1] < header_top - 2:
                continue
            for row in _restatement_rows(block['text']):
                numbers = row['printed_numeric_tokens']
                if len(numbers) not in {2,4} or not re.search(r'[\u4e00-\u9fff]',row['row_label_candidate']):
                    continue
                values = [Decimal(n.replace(',', '').replace('，', '').replace('－', '-').replace('−', '-')) for n in numbers]
                if not any(values[i] != values[i+1] for i in range(0, len(values), 2)):
                    continue
                candidates.append({**row,'evidence': block,
                                   'status': 'before_after_row_candidate_pending_human',
                                   'period_assignment': 'unknown_requires_column_review', 'scope': 'unknown'})
        if candidates:
            src = observation['source']
            headings = [e for e in evidence if any(t in re.sub(r'\s+', '', e['text']) for t in
                        ('会计数据追溯调整或重述情况', '调整重述前', '会计政策变更', '会计差错更正', '上年期末'))]
            return {'id': f"P-{src['code']}-restatement", 'set': 'P', 'ticker': src['code'], 'company': src['name'],
                    'annotation_status': 'pending_human', 'gold_origin': 'unreviewed_source_linked_candidate',
                    'human_reviews': [], 'gold_status': None,
                    'case_status': 'source_linked_restatement_candidate',
                    'source_document': {'path': src['relative_path'], 'sha256': observation['sha256']},
                    'source_page': page['page'], 'source_context': headings,
                    'before_after_candidates': candidates,
                    'accounting_cause': 'only_explicit_checkbox_passage_requires_human_confirmation',
                    'note': 'Raw printed pairs are observations, not parsed or professionally verified restatement facts'}
    return None


def validate_assets(records, p_cases):
    companies = {r['ticker'] for r in records}
    assert len({r['id'] for r in records}) == len(records)
    assert all(sum(r['ticker'] == ticker for r in records) == 30 for ticker in companies)
    assert all(r['annotation_status'] == 'pending_human' and r['gold_value'] is None and
               r['gold_status'] is None and not r['human_reviews'] for r in records)
    assert len(p_cases) <= 3 and all(r['annotation_status'] == 'pending_human' and not r['human_reviews'] for r in p_cases)
    return {'A_rows': len(records), 'A_companies': len(companies), 'A_years_per_company': 2, 'A_metrics': 15,
            'P_cases': len(p_cases), 'target_size_met': len(records) == 600 and len(companies) == 20 and len(p_cases) == 3,
            'gold_ready': False, 'human_review_status': 'pending'}


def prepare(corpus, out, workers=2, *, reuse_exposure=None):
    out = Path(out)
    if out.exists():
        raise ValueError('禁止覆盖既有候选资产，选择新的版本目录')
    allowed = None
    if reuse_exposure:
        allowed = set(json.loads(Path(reuse_exposure).read_text(encoding='utf-8'))['exposed_tickers'])
    root, selected = select_reports(corpus, allowed_tickers=allowed, allow_incomplete=True)
    # The known development issuer is eligible for A/P, never D3.
    known = root/'reports/920493_并行科技/年报/2024-04-15_2023年年度报告.pdf'
    extra = [{'code':'920493','name':'并行科技','year':'2023','date':'2024-04-15',
              'path':str(known),'relative_path':known.relative_to(root).as_posix()}] if known.is_file() else []
    observations = {}
    unique_sources = selected + [row for row in extra if row['relative_path'] not in
                                {source['relative_path'] for source in selected}]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        tasks = {pool.submit(inspect_source,row):row for row in unique_sources}
        for task in as_completed(tasks):
            row = tasks[task]
            observation = task.result()
            observations[row['relative_path']] = observation
            print(f"source {len(observations)}/{len(tasks)}: {row['code']} {row['year']} ({len(observation['facts'])} machine facts)", flush=True)
    ordered = [observations[row['relative_path']] for row in selected]
    records = a_candidates(ordered)
    p_cases, p_used = [], set()
    for row in extra+selected:
        if row['code'] in p_used:
            continue
        candidate = restatement_case(observations[row['relative_path']])
        if candidate:
            p_cases.append(candidate); p_used.add(row['code'])
        if len(p_cases) == 3:
            break
    validation = validate_assets(records,p_cases)
    out.mkdir(parents=True)
    _jsonl(out/'A_candidates.jsonl',records)
    _write(out/'P_candidates.json',p_cases)
    source_manifest = [{'ticker':o['source']['code'],'company':o['source']['name'], 'year':o['source']['year'],
                        'path':o['source']['relative_path'],'sha256':o['sha256'],'bytes':o['bytes'],
                        'pages':o['pages'],'machine_fact_count':len(o['facts'])} for o in observations.values()]
    _write(out/'source_manifest.json',sorted(source_manifest,key=lambda s:(s['ticker'],s['year'])))
    registry = {'purpose':'exclude_all_inspected_issuers_from_future_unseen_holdouts',
                'D3_status':'not_selected_not_opened_not_sealed_not_executed',
                'exposed_tickers':sorted({row['code'] for row in selected+extra}),
                'sources':source_manifest}
    _write(out/'development_exposure_registry.json',registry)
    templates = [{'id':r['id'],'ticker':r['ticker'],'company':r['company'],'period_to_review':r['period'],
                  'metric_to_review':r['account_candidate'],'source_document':r['source_document'],
                  'reviewer_id':None,'reviewer_kind':'human','attested':False,
                  'canonical_value':None,'unit':None,'scope':None,'nr':None,'period':None,
                  'source_version':None,'page':None,'bbox':None,'row':None,'col':None,'notes':None}
                 for r in records]
    _jsonl(out/'blind_human_review_template.jsonl',templates)
    summary = {**validation,'created_at':datetime.now(timezone.utc).isoformat(),
               'A_candidate_status_counts':dict(Counter(r['candidate_status'] for r in records)),
               'A_computed_context_count':sum(r['computed_context'] is not None for r in records),
               'A_lookup_status_counts':dict(Counter(r['lookup_status'] for r in records)),
               'P_status_counts':dict(Counter(r['case_status'] for r in p_cases)),
               'sources':len(observations),'reserved_D_D2_sources_opened':0,
               'D3_status':'not_selected_not_opened_not_sealed_not_executed',
               'model_requests':0,'professional_acceptance':False,
               'preparation_revision':'complete-annual-reports.financial-0.5.0',
               'source_selection_policy':'complete_fiscal_year_annual_report_caption_only; summaries_and_policies_excluded',
               'development_issuer_policy':'previously_exposed_MVP_issuer_920493_is_eligible_for_A_and_P; consumed_D_D2_issuers_remain_excluded',
               'reused_development_exposure_sha256':sha256_file(reuse_exposure) if reuse_exposure else None,
               'unmet_requirements':([] if len(records) == 600 else ['A_requires_20_existing_development_issuers_with_two_complete_annual_reports']) +
                                    ([] if len(p_cases) == 3 else ['P_requires_three_source_linked_restatement_issuers']),
               'preparation_tool_sha256':sha256_file(Path(__file__)),
               'extractor_source_sha256':{name:sha256_file(ROOT/'mingjing'/name) for name in
                   ('parser.py','models.py','facts.py','analysis.py','decimal_math.py','rules.py','evidence.py')},
               'rulepack_lock_sha256':sha256_file(ROOT/'rulepack'/'rulepack.lock'),
               'files':{p.name:sha256_file(p) for p in sorted(out.iterdir()) if p.is_file()}}
    _write(out/'preparation_audit.json',summary)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus',type=Path,default=ROOT.parent/'年报和半年报')
    parser.add_argument('--out',type=Path,default=Path(__file__).parent/'annotations')
    parser.add_argument('--workers',type=int,default=2)
    parser.add_argument('--reuse-exposure',type=Path,help='Restrict repair to already inspected issuers; never select fresh holdout companies')
    args=parser.parse_args(argv)
    if args.workers not in {1,2,3,4}: parser.error('workers须一至四')
    result=prepare(args.corpus,args.out,args.workers,reuse_exposure=args.reuse_exposure)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
