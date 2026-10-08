"""Independent full-report occurrence review; no machine candidate selection.

This development tool freezes original material and every source page/block,
prepares three empty submissions, and measures all original human ratings.
Content attestations never prove identity or professional qualifications.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path, PurePosixPath
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bench.v1.human_review import _required_text, fleiss_kappa, rows, write
from mingjing.audit import digest
from mingjing.config import validate_path
from mingjing.coverage import partition_document
from mingjing.evidence import sha256_file
from scripts.experiment_runner import execution_task_identity

SCHEMA = 'mingjing-full-report-review.v1'
PLAN_SCHEMA = 'mingjing-full-report-review-plan.v1'
TASK_FIELDS = frozenset({'id', 'set', 'company', 'period', 'report_date', 'draft',
                        'source_documents', 'scope_declaration'})
BINDING_FIELDS = ('company', 'account', 'period', 'scope', 'relation', 'base_period')
CATEGORIES = {'financial_in_scope', 'financial_out_of_scope', 'non_financial_numeric'}
FORMS = {'prose', 'table', 'chart', 'header', 'other'}
REVIEW_FIELDS = {'schema_version', 'task_id', 'task_sha256', 'execution_task_sha256',
                 'reviewer', 'page_reviews', 'occurrences', 'completeness_attested', 'signed_content_sha256'}
LOCATION_FIELDS = {'page_id', 'block_id', 'start', 'end', 'text'}
OCCURRENCE_FIELDS = {'location', 'category', 'form', 'binding', 'unit', 'currency', 'scope_reason', 'notes'}
_NUMERIC = re.compile(r'[+\-−－]?(?:\d[\d,，]*(?:\.\d+)?|[零〇一二三四五六七八九十百千万亿两半]+)'
    r'(?:\s*(?:百万元|亿元|万元|万股|亿股|元/股|美元|人民币|元|百分点|基点|[%％]|倍|股|亿|万|千|年|月|日|[AE]))?')


def _json(path):
    source = validate_path(path, must_exist=True, kind='file', follow_symlinks=False)
    if source.stat().st_size > 64*1024*1024:
        raise ValueError('Review JSON exceeds the bounded material size')
    return json.loads(source.read_text(encoding='utf-8-sig'))


def _relative(value):
    path = PurePosixPath(value) if isinstance(value, str) else None
    if (path is None or not path.parts or path.is_absolute() or path.as_posix() != value
            or any(part in {'.', '..'} or ':' in part or '\\' in part for part in path.parts)):
        raise ValueError('Review paths must be canonical root-relative paths')
    return path


def _source(reference, corpus):
    if not isinstance(reference, dict) or set(reference) != {'path', 'sha256'}:
        raise ValueError('Original material needs exactly path and SHA-256')
    _relative(reference['path'])
    root = validate_path(corpus, must_exist=True, kind='dir', follow_symlinks=False)
    source = validate_path(root/reference['path'], allowed_roots=[str(root)], must_exist=True,
                           kind='file', follow_symlinks=False)
    if (source.suffix.lower() not in {'.pdf', '.md', '.txt'} or source.stat().st_size > 64*1024*1024
            or not re.fullmatch(r'[a-f0-9]{64}', str(reference['sha256'])) or sha256_file(source) != reference['sha256']):
        raise ValueError('Original material format, size or byte hash differs')
    return source


def _task(raw, corpus):
    if not isinstance(raw, dict) or set(raw)-TASK_FIELDS or not {'id', 'company', 'report_date', 'draft', 'source_documents'} <= raw.keys():
        raise ValueError('Blind tasks accept original execution inputs only, never candidates, facts or predictions')
    if not isinstance(raw['id'], str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,100}', raw['id']):
        raise ValueError('Task ID is missing or unsafe')
    if not isinstance(raw.get('set', 'development'), str) or raw.get('set', 'development') not in {'development', 'P', 'A', 'B', 'C', 'X'}:
        raise ValueError('D3 and unregistered cohorts cannot enter this development tool')
    _required_text(raw['company'], 'company')
    period = raw.get('period', '')
    if not isinstance(period, str) or period and not re.fullmatch(r'20\d{2}(?:Y|H[12]|Q[1-4]|9M)', period):
        raise ValueError('Task period is invalid')
    if not isinstance(raw['report_date'], str) or date.fromisoformat(raw['report_date']).isoformat() != raw['report_date']:
        raise ValueError('Task disclosure cutoff must be an explicit ISO date')
    if not isinstance(raw.get('scope_declaration', ''), str) or raw.get('scope_declaration', '') not in {'', '合并', '母公司'}:
        raise ValueError('Task scope declaration is invalid')
    draft = _source(raw['draft'], corpus)
    documents = raw['source_documents']
    if not isinstance(documents, list) or not 1 <= len(documents) <= 50:
        raise ValueError('Task must lock one to fifty original financial documents')
    seen = set()
    for ref in documents:
        _source(ref, corpus)
        if ref['path'] in seen:
            raise ValueError('Original source document is duplicated')
        seen.add(ref['path'])
    return dict(raw), draft


def _index(reference, source):
    text, pages, blocks = partition_document(source)
    # Geometry and original text are useful to independent reviewers. The
    # parser's table_candidate hint and all financial labels are excluded.
    keep = ('id', 'page_id', 'page', 'start', 'end', 'text', 'bbox')
    return {'source_document': reference, 'text': text, 'text_sha256': digest(text),
            'pages': [{k: row[k] for k in ('id', 'page', 'start', 'end', 'characters', 'text_layer')} for row in pages],
            'blocks': [{k: row.get(k) for k in keep} for row in blocks],
            'character_basis': 'python_unicode_codepoints_of_frozen_extracted_text',
            'amount_origin': 'original_source_text_only_not_machine_candidates'}


def _extractor_lock():
    return {name: sha256_file(ROOT/'mingjing'/name) for name in ('parser.py', 'coverage.py')}


def prepare(plan_file, corpus, out):
    plan = _json(plan_file)
    if not isinstance(plan, dict) or set(plan) != {'schema_version', 'tasks'} or plan['schema_version'] != PLAN_SCHEMA or not isinstance(plan['tasks'], list) or not 1 <= len(plan['tasks']) <= 100:
        raise ValueError('Explicit development review plan is required')
    destination = validate_path(out, kind='dir', follow_symlinks=False)
    if destination.exists():
        raise ValueError('Cannot overwrite an independent review package')
    tasks, indexes, ids = [], {}, set()
    for raw in plan['tasks']:
        task, source = _task(raw, corpus)
        if task['id'] in ids:
            raise ValueError('Review task IDs are duplicated')
        ids.add(task['id'])
        sha = task['draft']['sha256']
        if sha not in indexes:
            indexes[sha] = _index(task['draft'], source)
        if sha256_file(source) != sha:
            raise ValueError('Original report changed during indexing')
        tasks.append(dict(task, execution_task_sha256=digest(execution_task_identity(task)),
                          draft_index={'path': 'indexes/'+sha+'.json', 'content_sha256': digest(indexes[sha])}))
    package = {'schema_version': SCHEMA, 'tasks': tasks, 'task_sha256': digest(tasks),
               'extractor_lock': _extractor_lock(), 'created_at': datetime.now(timezone.utc).isoformat(),
               'origin': 'original_full_reports_without_machine_occurrence_candidates', 'human_review': 'pending'}
    package['package_sha256'] = digest(package)
    for sha, index in indexes.items():
        write(destination/'indexes'/(sha+'.json'), index)
    write(destination/'tasks.json', package)
    for reviewer in range(1, 4):
        write(destination/f'reviewer-{reviewer}.jsonl', [
            {'schema_version': SCHEMA, 'task_id': task['id'], 'task_sha256': package['task_sha256'],
             'execution_task_sha256': task['execution_task_sha256'],
             'reviewer': {'id': None, 'name': None, 'profession': None, 'reviewer_kind': 'human',
                          'independent': False, 'attested': False, 'signed_at': None},
             'page_reviews': [{'page_id': page['id'], 'status': None, 'notes': None}
                              for page in indexes[task['draft']['sha256']]['pages']],
             'occurrences': None, 'completeness_attested': False, 'signed_content_sha256': None} for task in tasks])
    instructions = ('三名真人独立阅读整份原研报及原财报，不查看机器候选、绑定、金额或结果。原文金额必须保留。\n'
        'indexes 仅是原文页面/块/字符索引，不生成数字候选；独立标注每一个数字提及，包括金融、范围外金融与非金融数字。\n'
        '每个数字 occurrence 填 location(page_id/block_id/start/end/text)、category、form、binding六字段、unit、currency、scope_reason、notes。\n'
        '数字字符区间是原索引 text 的 Python Unicode codepoint [start,end)，包括源写单位，不含尾空白；重复数字分别登记。\n'
        'financial_in_scope 包括口径不明/缺材料等可弃权的金融提及；financial_out_of_scope 包括预测/其他公司等，并说明原因。\n'
        'non_financial_numeric 的 binding/unit/currency 填null。金融未知维度填空字符串，不能用事实库金额反推。\n'
        'form 选prose/table/chart/header/other；每页填reviewed或unreadable及说明，未读页不能签成全文完整。\n'
        'reviewer本人填写身份、专业背景、独立性、带时区签认时间及completeness_attested，sign冻结原始提交。\n'
        'merge从三份完整原始提交的地址并集计算存在/分类/绑定一致率及Fleiss kappa；未标项保留为not_marked。\n'
        'Agent不裁决分歧、不删漏项。未签认、分歧、未读页面或单类别kappa不可估计均不会生成可评分金标。\n'
        '内容哈希及human声明不证明真人身份；独立资格核验、30规则签发、20外抽检及D3属于另行验收。\n')
    (destination/'README.md').write_text('# 全文研报独立盲标\n\n'+instructions, encoding='utf-8')
    return {'status': 'pending_human_review', 'tasks': len(tasks), 'pages': sum(len(i['pages']) for i in indexes.values()),
            'task_sha256': package['task_sha256'], 'full_report_recall': None, 'professional_acceptance': False}


def load_package(tasks_file, corpus):
    package = _json(tasks_file)
    if (not isinstance(package, dict) or not isinstance(package.get('tasks'), list)
            or not 1 <= len(package['tasks']) <= 100 or any(not isinstance(task, dict) for task in package['tasks'])):
        raise ValueError('A locked full-report review package is required')
    locked = {key: val for key, val in package.items() if key != 'package_sha256'}
    if (package.get('schema_version') != SCHEMA or package.get('package_sha256') != digest(locked)
            or package.get('task_sha256') != digest(package.get('tasks')) or package.get('extractor_lock') != _extractor_lock()):
        raise ValueError('Review package, tasks or source extractor lock changed')
    base = Path(tasks_file).resolve().parent
    indexes, seen = {}, set()
    for task in package['tasks']:
        raw = {key: val for key, val in task.items() if key in TASK_FIELDS}
        _, source = _task(raw, corpus)
        if (set(task)-TASK_FIELDS != {'execution_task_sha256', 'draft_index'} or task['id'] in seen
                or task['execution_task_sha256'] != digest(execution_task_identity(raw))):
            raise ValueError('Full report execution task lock changed')
        seen.add(task['id'])
        ref = task['draft_index']
        if not isinstance(ref, dict) or set(ref) != {'path', 'content_sha256'}:
            raise ValueError('Original text index lock is missing')
        _relative(ref['path'])
        path = validate_path(base/ref['path'], allowed_roots=[str(base)], must_exist=True, kind='file', follow_symlinks=False)
        index = _json(path)
        if digest(index) != ref['content_sha256'] or index.get('source_document', {}).get('sha256') != task['draft']['sha256']:
            raise ValueError('Original text index differs from its document lock')
        original = _source(index['source_document'], corpus)
        if _index(index['source_document'], original) != index:
            raise ValueError('Frozen text/geometry must come from the original bytes, not a forged index')
        indexes[task['id']] = index
    return package, indexes


def _reviewer(identity):
    if not isinstance(identity, dict) or set(identity) != {'id', 'name', 'profession', 'reviewer_kind', 'independent', 'attested', 'signed_at'}:
        raise ValueError('Complete original human identity declaration is required')
    for field in ('id', 'name', 'profession', 'signed_at'):
        _required_text(identity.get(field), field)
    if identity['reviewer_kind'] != 'human' or identity['independent'] is not True or identity['attested'] is not True:
        raise ValueError('An independent human must attest the content; an agent cannot substitute')
    if datetime.fromisoformat(identity['signed_at'].replace('Z', '+00:00')).tzinfo is None:
        raise ValueError('Human attestation time needs an explicit timezone')


def _location(location, index):
    if not isinstance(location, dict) or set(location) != LOCATION_FIELDS:
        raise ValueError('Every occurrence needs original page, block and exact character span')
    start, end = location['start'], location['end']
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(index['text']):
        raise ValueError('Original occurrence character span is invalid')
    value = index['text'][start:end]
    if location['text'] != value or not _NUMERIC.fullmatch(value):
        raise ValueError('Occurrence transcription must be the exact original numeric span')
    block = next((row for row in index['blocks'] if row['id'] == location['block_id']), None)
    page = next((row for row in index['pages'] if row['id'] == location['page_id']), None)
    if (not block or not page or block['page_id'] != page['id'] or block['start'] is None
            or not block['start'] <= start < end <= block['end'] or not page['start'] <= start < end <= page['end']):
        raise ValueError('Occurrence page/block does not contain its original character span')
    return start, end


def _occurrence(item, index):
    if (not isinstance(item, dict) or set(item) != OCCURRENCE_FIELDS
            or not isinstance(item['category'], str) or item['category'] not in CATEGORIES
            or not isinstance(item['form'], str) or item['form'] not in FORMS):
        raise ValueError('Occurrence schema, financial category or source form is invalid')
    key = _location(item['location'], index)
    _required_text(item['notes'], '独立原文标注说明')
    if item['category'] == 'non_financial_numeric':
        if any(item[k] is not None for k in ('binding', 'unit', 'currency')):
            raise ValueError('Non-financial occurrences cannot assert financial binding or units')
    else:
        binding = item['binding']
        if not isinstance(binding, dict) or set(binding) != set(BINDING_FIELDS) or any(not isinstance(v, str) for v in binding.values()):
            raise ValueError('Financial occurrences require all six binding fields; unknown is an empty string')
        if binding['scope'] not in {'', '合并', '母公司'} or binding['relation'] not in {'', 'value', 'yoy', 'qoq', 'pp_diff', 'single_quarter', 'comparison', 'unsupported'}:
            raise ValueError('Financial occurrence scope or relation is invalid')
        if any(not isinstance(item[k], str) or len(item[k]) > 100 for k in ('unit', 'currency')):
            raise ValueError('Human source units and currency must be explicitly supplied or unknown')
    if item['category'] == 'financial_out_of_scope':
        _required_text(item['scope_reason'], '金融范围外依据')
    elif item['scope_reason'] is not None and item['scope_reason'] != '':
        raise ValueError('Scope reason applies only to out-of-scope financial occurrences')
    return key


def validate_review(review, task, package, index, *, require_signature=True):
    if (not isinstance(review, dict) or set(review) != REVIEW_FIELDS or review.get('schema_version') != SCHEMA
            or review.get('task_id') != task['id'] or review.get('task_sha256') != package['task_sha256']
            or review.get('execution_task_sha256') != task['execution_task_sha256']):
        raise ValueError('Review does not sign the identical full-report execution task')
    _reviewer(review['reviewer'])
    pages = review['page_reviews']
    expected = {page['id'] for page in index['pages']}
    if (not isinstance(pages, list) or len(pages) != len(expected)
            or any(not isinstance(row, dict) or not isinstance(row.get('page_id'), str) for row in pages)
            or {row['page_id'] for row in pages} != expected):
        raise ValueError('The original full report needs an explicit review record for every page')
    for page in pages:
        if set(page) != {'page_id', 'status', 'notes'} or not isinstance(page['status'], str) or page['status'] not in {'reviewed', 'unreadable'}:
            raise ValueError('Every original page must be reviewed or explicitly unreadable')
        _required_text(page['notes'], '逐页独立审阅说明')
    if review['completeness_attested'] is not True:
        raise ValueError('The human must explicitly attest independent full-report enumeration')
    occurrences = review['occurrences']
    if not isinstance(occurrences, list) or len(occurrences) > 100000:
        raise ValueError('Independent occurrences must be supplied, not an empty template')
    seen = set()
    for occurrence in occurrences:
        key = _occurrence(occurrence, index)
        if key in seen:
            raise ValueError('Duplicate source occurrence cannot pad the independent gold')
        seen.add(key)
    content = {key: val for key, val in review.items() if key != 'signed_content_sha256'}
    if require_signature and review['signed_content_sha256'] != digest(content):
        raise ValueError('Human signed content changed; the original reviewer must attest again')
    return digest(content)


def _submission(path, package, indexes, *, require_signature=True):
    submitted = rows(path)
    tasks = {t['id']: t for t in package['tasks']}
    if (not isinstance(submitted, list) or len(submitted) != len(tasks)
            or any(not isinstance(r, dict) or not isinstance(r.get('task_id'), str) for r in submitted)
            or {r.get('task_id') for r in submitted} != set(tasks)):
        raise ValueError('Each original reviewer must submit the whole identical batch')
    identities, mapping = set(), {}
    for review in submitted:
        task_id = review['task_id']
        validate_review(review, tasks[task_id], package, indexes[task_id], require_signature=require_signature)
        identity = dict(review['reviewer'])
        identity.pop('signed_at')
        identities.add(digest(identity))
        mapping[task_id] = review
    if len(identities) != 1:
        raise ValueError('A submission must belong to one consistently declared original human')
    return submitted, mapping


def sign(tasks_file, submission, corpus, out):
    package, indexes = load_package(tasks_file, corpus)
    submitted, _ = _submission(submission, package, indexes, require_signature=False)
    for review in submitted:
        review['signed_content_sha256'] = digest({k: v for k, v in review.items() if k != 'signed_content_sha256'})
    write(out, submitted)
    return {'status': 'content_attested_identity_requires_external_verification', 'tasks': len(submitted),
            'professional_acceptance': False, 'identity_verification': 'external_pending'}


def _semantic(item):
    return {key: item[key] for key in ('category', 'form', 'binding', 'unit', 'currency', 'scope_reason')}


def merge(tasks_file, submissions, corpus, out):
    package, indexes = load_package(tasks_file, corpus)
    if len(submissions) != 3:
        raise ValueError('Exactly three complete original human submissions are required')
    originals, mappings, identities = [], [], []
    for path in submissions:
        submitted, mapping = _submission(path, package, indexes)
        originals.append(submitted)
        mappings.append(mapping)
        identities.append(submitted[0]['reviewer']['id'])
    if len(set(identities)) != 3:
        raise ValueError('Three distinct independent human reviewers are required')
    discovery, classification, exact, fields, disputes, unanimous = [], [], [], {f: [] for f in BINDING_FIELDS}, [], []
    unreadable, unindexed = [], []
    for task in package['tasks']:
        index = indexes[task['id']]
        for page in index['pages']:
            if not page['text_layer']:
                unindexed.append({'task_id': task['id'], 'page_id': page['id'], 'page': page['page'],
                                  'reason': 'no_original_text_layer'})
            elif any(block['start'] is None or block['end'] is None for block in index['blocks']
                     if block['page_id'] == page['id']):
                unindexed.append({'task_id': task['id'], 'page_id': page['id'], 'page': page['page'],
                                  'reason': 'unaddressed_original_source_block'})
        reviews = [mapping[task['id']] for mapping in mappings]
        for number, review in enumerate(reviews):
            unreadable.extend({'task_id': task['id'], 'reviewer_id': identities[number], 'page_id': page['page_id']}
                              for page in review['page_reviews'] if page['status'] == 'unreadable')
        by_span = [{(item['location']['start'], item['location']['end']): item for item in review['occurrences']} for review in reviews]
        union = sorted(set().union(*(mapping.keys() for mapping in by_span)))
        accepted = []
        for span in union:
            items = [mapping.get(span) for mapping in by_span]
            discovery.append(['marked' if item else 'not_marked' for item in items])
            classification.append([item['category'] if item else 'not_marked' for item in items])
            signatures = [digest(_semantic(item)) if item else 'not_marked' for item in items]
            exact.append(signatures)
            for field in BINDING_FIELDS:
                fields[field].append([item['binding'][field] if item and item['binding'] else '__not_financial__' if item else '__not_marked__' for item in items])
            if len(set(signatures)) != 1:
                disputes.append({'task_id': task['id'], 'start': span[0], 'end': span[1],
                                 'original_ratings': [_semantic(item) if item else 'not_marked' for item in items]})
            else:
                accepted.append(items[0])
        unanimous.append({**{key: task[key] for key in TASK_FIELDS if key in task},
                          'execution_task_sha256': task['execution_task_sha256'], 'draft_index': task['draft_index'],
                          'occurrences': accepted, 'human_reviews': reviews, 'task_sha256': package['task_sha256'],
                          'gold_origin': 'independent_human_full_report_occurrences'})
    agreement = fleiss_kappa(classification) if classification else {'status': 'no_original_items', 'kappa': None, 'raw_agreement': None, 'items': 0}
    status = ('pending_unindexed_source_pages' if unindexed else 'pending_unreadable_pages' if unreadable
              else 'pending_human_dispute_resolution' if disputes
              else 'pending_non_estimable_agreement' if agreement['kappa'] is None
              else 'reannotate_entire_batch' if agreement['kappa'] < .80 else 'content_consensus_identity_pending')
    destination = validate_path(out, kind='dir', follow_symlinks=False)
    if destination.exists():
        raise ValueError('Cannot overwrite original human ratings or agreement')
    report = {'schema_version': SCHEMA, 'task_sha256': package['task_sha256'], 'reviewers': identities,
              'status': status, 'agreement_uses_all_original_occurrences': True, 'classification_agreement': agreement,
              'discovery_agreement': fleiss_kappa(discovery) if discovery else None,
              'exact_semantic_agreement': fleiss_kappa(exact) if exact else None,
              'binding_field_agreement': {f: fleiss_kappa(rating) if rating else None for f, rating in fields.items()},
              'disputed_occurrences': disputes, 'unreadable_pages': unreadable, 'unindexed_source_pages': unindexed,
              'professional_acceptance': False,
              'identity_verification': 'external_pending', 'full_report_recall': None,
              'submission_files': [{'path': str(path), 'sha256': sha256_file(path)} for path in submissions]}
    write(destination/'original_reviews.json', {'schema_version': SCHEMA, 'submissions': originals,
                                               'task_sha256': package['task_sha256'], 'files': report['submission_files']})
    write(destination/'agreement.json', report)
    if status == 'content_consensus_identity_pending':
        write(destination/'content_consensus.jsonl', unanimous)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--corpus', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    for command in ('sign', 'merge', 'validate'):
        p = sub.add_parser(command)
        p.add_argument('--tasks', type=Path, required=True)
        p.add_argument('--corpus', type=Path, required=True)
        p.add_argument('--submission', type=Path, required=True, **({'nargs': 3} if command == 'merge' else {}))
        if command != 'validate':
            p.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == 'prepare':
        result = prepare(args.plan, args.corpus, args.out)
    elif args.command == 'sign':
        result = sign(args.tasks, args.submission, args.corpus, args.out)
    elif args.command == 'merge':
        result = merge(args.tasks, args.submission, args.corpus, args.out)
    else:
        package, indexes = load_package(args.tasks, args.corpus)
        submitted, _ = _submission(args.submission, package, indexes)
        result = {'status': 'content_valid_identity_pending', 'tasks': len(submitted), 'professional_acceptance': False}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
