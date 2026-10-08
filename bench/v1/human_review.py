"""Blind PDF annotation, human attestation and original-rating agreement.

This tool prepares work for real reviewers. It never supplies missing labels,
arbitrates disagreements, or treats a content hash as proof of human identity.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re

from mingjing.audit import digest
from mingjing.config import validate_path
from mingjing.evidence import sha256_file


SCHEMA = 'crrv-human-review.v1'
BINDING_FIELDS = ('company', 'account', 'period', 'scope', 'relation', 'base_period')
UNITS = {'元': Decimal(1), '万元': Decimal(10000), '亿元': Decimal(100000000),
         '%': Decimal('.01'), '百分点': Decimal('.01'), '元/股': Decimal(1), '股': Decimal(1)}


def rows(path):
    path = Path(path)
    raw = path.read_text(encoding='utf-8-sig')
    return [json.loads(line) for line in raw.splitlines() if line.strip()] if path.suffix == '.jsonl' else json.loads(raw)


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        if path.suffix == '.jsonl':
            for row in value:
                stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        else:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2)+'\n')


def prepare(annotations, out):
    """Only task dimensions and source addresses enter the reviewer package."""
    source, out = Path(annotations), Path(out)
    if out.exists():
        raise ValueError('不能覆盖盲标包，选择新的版本目录')
    tasks = []
    for row in rows(source/'A_candidates.jsonl'):
        tasks.append({'id': row['id'], 'set': 'A', 'company': row['company'], 'ticker': row['ticker'],
                      'target': {'account': row['account_candidate'], 'period': row['period']},
                      'source_documents': [row['source_document']]})
    for row in rows(source/'P_candidates.json'):
        if not row.get('source_document') or not row.get('before_after_candidates'):
            raise ValueError('P重述材料不完整，不能把缺失案例包装成盲标任务')
        for index, cell in enumerate(row['before_after_candidates']):
            # No printed_numeric_tokens, row text, proposed period, box or model
            # fact is exposed. The reviewer determines both columns from PDF.
            tasks.append({'id': row['id']+f'-row-{index+1}', 'set': 'P', 'company': row['company'],
                          'ticker': row['ticker'], 'target': {'row_label': cell['row_label_candidate'],
                            'review': '独立确认调整前和调整后各列的期间、口径、版本及单元格'},
                          'source_documents': [row['source_document']]})
    ids = [row['id'] for row in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError('盲标任务ID重复')
    package = {'schema_version': SCHEMA, 'tasks': tasks,
               'origin': 'blind_tasks_without_machine_values', 'created_at': datetime.now(timezone.utc).isoformat()}
    package['task_sha256'] = digest(tasks)
    write(out/'tasks.json', package)
    for index in range(1, 4):
        write(out/f'reviewer-{index}.jsonl', [{'schema_version': SCHEMA, 'task_id': task['id'],
             'task_sha256': package['task_sha256'], 'reviewer': {'id': None, 'name': None, 'profession': None,
             'reviewer_kind': 'human', 'independent': False, 'attested': False, 'signed_at': None},
             'label': None, 'signed_content_sha256': None} for task in tasks])
    instructions = [
        '三名真人分别阅读原PDF，彼此不查看标注，也不查看机器候选金额。',
        'label填写status/error_type/abstention_code、kind、cells、notes；不能留空。',
        '每个cell填写printed_value/unit/decimals/binding六字段/source_version/publish_date/evidence。',
        'evidence填写path/sha256/page/bbox/table/row/col/transcription；bbox必须覆盖原单元格。',
        'P任务至少两项cell，source_version包含调整前和调整后；期间由真人确认。',
        '原值kind=original；推导kind=derived并填写rule_id与完整operands证据；缺材料kind=unavailable。',
        '人工本人填写身份、专业背景、独立性和签认时间后，使用 sign 命令冻结内容。',
        'sign仅检测完整性与内容哈希，不能证明身份；最终验收仍需用户核验三名真人资格。',
        'merge计算未删除评级的一致率和Fleiss kappa；低于0.80整批重标，不由Agent裁决分歧。']
    with (out/'README.md').open('x', encoding='utf-8') as stream:
        stream.write('# 三名真人独立盲标\n\n'+'\n'.join(f'{i+1}. {text}' for i, text in enumerate(instructions))+'\n')
    return {'tasks': len(tasks), 'A': sum(t['set']=='A' for t in tasks), 'P': sum(t['set']=='P' for t in tasks),
            'human_review': 'pending', 'task_sha256': package['task_sha256']}


def _required_text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'人工签认缺少{field}')
    return value


def _normalized(text):
    return re.sub(r'\s+', '', text).replace('，', ',').replace('−', '-').replace('－', '-')


def validate_evidence(reference, documents, corpus):
    import fitz
    if not isinstance(reference, dict):
        raise ValueError('原PDF定位不能为空')
    pair = (reference.get('path'), reference.get('sha256'))
    if pair not in documents:
        raise ValueError('标注引用未登记文档')
    source = validate_path(Path(corpus)/reference['path'], allowed_roots=[str(Path(corpus).resolve())],
                           must_exist=True, kind='file', follow_symlinks=False)
    if source.suffix.lower() != '.pdf' or sha256_file(source) != reference['sha256']:
        raise ValueError('原PDF哈希或类型不符')
    page, bbox = reference.get('page'), reference.get('bbox')
    if type(page) is not int or page < 1 or not isinstance(bbox, list) or len(bbox) != 4:
        raise ValueError('页码/单元格坐标不完整')
    if any(type(n) not in {int, float} or not Decimal(str(n)).is_finite() for n in bbox):
        raise ValueError('非法单元格坐标')
    for key in ('table', 'row', 'col', 'transcription'):
        _required_text(reference.get(key), key)
    with fitz.open(source) as pdf:
        if page > len(pdf):
            raise ValueError('页码超出PDF')
        rectangle = fitz.Rect(bbox)
        if rectangle.is_empty or not pdf[page-1].rect.contains(rectangle):
            raise ValueError('单元格坐标超出原页')
        actual = pdf[page-1].get_text('text', clip=rectangle)
        if _normalized(reference['transcription']) not in _normalized(actual):
            raise ValueError('人工摘录与原页坐标文本不符')
    return reference


def validate_label(label, task, corpus):
    if not isinstance(label, dict) or label.get('status') not in {'verified', 'violated', 'abstained', 'out_of_scope'}:
        raise ValueError('人工label与三态/范围标签不能为空')
    if label.get('kind') not in {'original', 'derived', 'unavailable', 'out_of_scope'}:
        raise ValueError('人工事实类型未登记')
    if label['status'] == 'violated' and label.get('error_type') not in {f'E{i}' for i in range(1, 10)}:
        raise ValueError('违反标签缺少E1-E9')
    if label['status'] == 'abstained' and label.get('abstention_code') not in {f'X{i}' for i in range(1, 7)}:
        raise ValueError('弃权标签缺少X1-X6')
    _required_text(label.get('notes'), '原PDF审阅说明')
    documents = {(r['path'], r['sha256']) for r in task['source_documents']}
    cells = label.get('cells')
    if label['kind'] in {'unavailable', 'out_of_scope'}:
        if cells:
            raise ValueError('缺材料/范围外标注不能同时声称金额真值')
        return label
    if not isinstance(cells, list) or not cells:
        raise ValueError('人工金额及六字段绑定缺失')
    for cell in cells:
        _required_text(cell.get('printed_value'), '原披露数值')
        try:
            value = Decimal(cell['printed_value'].replace(',', ''))
        except InvalidOperation as exc:
            raise ValueError('原披露数值不能解析') from exc
        if not value.is_finite() or cell.get('unit') not in UNITS or type(cell.get('decimals')) is not int or not 0 <= cell['decimals'] <= 12:
            raise ValueError('人工数值/单位/精度非法')
        if value.as_tuple().exponent != -cell['decimals']:
            raise ValueError('精度必须与原披露小数位一致')
        binding = cell.get('binding')
        if not isinstance(binding, dict) or set(binding) != set(BINDING_FIELDS):
            raise ValueError('必须完整确认六字段绑定')
        for field in BINDING_FIELDS[:-1]:
            _required_text(binding.get(field), field)
        if binding['company'] != task['company'] or binding['scope'] not in {'合并', '母公司'} or binding['relation'] not in {'value', 'yoy', 'qoq', 'pp_diff', 'single_quarter'}:
            raise ValueError('人工绑定公司/口径/关系非法')
        if not isinstance(binding['base_period'], str):
            raise ValueError('基期必须填写；直接值使用空字符串')
        _required_text(cell.get('source_version'), '披露版本')
        datetime.strptime(_required_text(cell.get('publish_date'), '披露日期'), '%Y-%m-%d')
        evidence = cell.get('evidence')
        if not isinstance(evidence, list) or not evidence:
            raise ValueError('金额必须绑定原PDF单元格')
        for ref in evidence:
            validate_evidence(ref, documents, corpus)
        if label['kind'] == 'original' and not any(_normalized(cell['printed_value']) in _normalized(ref['transcription']) for ref in evidence):
            raise ValueError('原披露金额不在人工摘录内')
        if label['kind'] == 'derived':
            _required_text(cell.get('rule_id'), '授权推导规则')
            operands = cell.get('operands')
            if not isinstance(operands, list) or len(operands) < 2:
                raise ValueError('推导值缺少操作数')
            for operand in operands:
                _required_text(operand.get('printed_value'), '操作数')
                if operand.get('unit') not in UNITS or not operand.get('evidence'):
                    raise ValueError('操作数单位及来源不完整')
                for ref in operand['evidence']:
                    validate_evidence(ref, documents, corpus)
    if task['set'] == 'P' and not ({'调整前', '调整后'} <= {cell['source_version'] for cell in cells}):
        raise ValueError('P重述标注需分别确认调整前/后单元格')
    return label


def validate_review(review, task, task_hash, corpus, *, require_signature=True):
    if review.get('schema_version') != SCHEMA or review.get('task_id') != task['id'] or review.get('task_sha256') != task_hash:
        raise ValueError('人工任务锁不一致')
    reviewer = review.get('reviewer')
    if not isinstance(reviewer, dict):
        raise ValueError('缺少真人审阅者身份')
    for field in ('id', 'name', 'profession', 'signed_at'):
        _required_text(reviewer.get(field), field)
    timestamp = datetime.fromisoformat(reviewer['signed_at'].replace('Z', '+00:00'))
    if timestamp.tzinfo is None:
        raise ValueError('签认时间需带时区')
    if reviewer.get('reviewer_kind') != 'human' or reviewer.get('independent') is not True or reviewer.get('attested') is not True:
        raise ValueError('必须由独立真人明确签认；模型不能代替')
    validate_label(review.get('label'), task, corpus)
    content = {key: value for key, value in review.items() if key != 'signed_content_sha256'}
    if require_signature and review.get('signed_content_sha256') != digest(content):
        raise ValueError('签认内容哈希不符；修改后须真人重新签认')
    return digest(content)


def sign(tasks_file, submission, corpus, out):
    package = json.loads(Path(tasks_file).read_text(encoding='utf-8'))
    tasks = {t['id']: t for t in package['tasks']}
    submitted = rows(submission)
    if len(submitted) != len(tasks) or {r['task_id'] for r in submitted} != set(tasks):
        raise ValueError('整批任务必须完成后签认')
    identities = set()
    for review in submitted:
        identities.add(review['reviewer']['id'])
        review['signed_content_sha256'] = validate_review(review, tasks[review['task_id']], package['task_sha256'], corpus, require_signature=False)
    if len(identities) != 1:
        raise ValueError('每份提交只能由同一独立真人签认')
    write(out, submitted)
    return {'status': 'content_attested_identity_requires_external_verification', 'rows': len(submitted)}


def fleiss_kappa(ratings):
    """Compute from all original ratings, including later disputed items."""
    if not ratings or any(len(row) != 3 for row in ratings):
        raise ValueError('Fleiss kappa需每项三份原始评级')
    counts = [Counter(row) for row in ratings]
    agreement = sum(sum(n*(n-1) for n in row.values())/6 for row in counts)/len(counts)
    totals = Counter(value for row in ratings for value in row)
    expected = sum((n/(3*len(ratings)))**2 for n in totals.values())
    if expected == 1:
        return {'status': 'undefined_single_category', 'kappa': None, 'raw_agreement': agreement, 'items': len(ratings)}
    return {'status': 'measured', 'kappa': (agreement-expected)/(1-expected), 'raw_agreement': agreement, 'items': len(ratings)}


def label_key(label):
    """Compare financial assertions; valid cell boxes/notes may differ."""
    cells = []
    for cell in label.get('cells') or []:
        cells.append({'value': str(Decimal(cell['printed_value'].replace(',', ''))*UNITS[cell['unit']]),
                      'unit': cell['unit'], 'decimals': cell['decimals'], 'binding': cell['binding'],
                      'source_version': cell['source_version'], 'publish_date': cell['publish_date'],
                      'rule_id': cell.get('rule_id'), 'locations': sorted(
                          [(r['path'], r['sha256'], r['page'], r['table'], r['row'], r['col']) for r in cell['evidence']])})
    return {'status': label['status'], 'kind': label['kind'], 'error_type': label.get('error_type'),
            'abstention_code': label.get('abstention_code'), 'cells': sorted(cells, key=digest)}


def merge(tasks_file, submissions, corpus, out):
    package = json.loads(Path(tasks_file).read_text(encoding='utf-8'))
    tasks = package['tasks']
    if package.get('task_sha256') != digest(tasks) or len(submissions) != 3:
        raise ValueError('任务清单损坏或未提供三份独立原始提交')
    originals, reviewer_ids = [], []
    for path in submissions:
        submission = rows(path)
        mapping = {r['task_id']: r for r in submission}
        if len(submission) != len(tasks) or len(mapping) != len(tasks) or set(mapping) != {t['id'] for t in tasks}:
            raise ValueError('三名审阅者必须完整提交相同批次')
        ids = set()
        for task in tasks:
            review = mapping[task['id']]
            validate_review(review, task, package['task_sha256'], corpus)
            ids.add(review['reviewer']['id'])
        if len(ids) != 1:
            raise ValueError('同一提交混入其他审阅者')
        reviewer_ids.append(next(iter(ids)))
        originals.append(mapping)
    if len(set(reviewer_ids)) != 3:
        raise ValueError('需要三名不同独立真人，不可重复提交同一审阅者')
    ratings = [[digest(label_key(mapping[t['id']]['label'])) for mapping in originals] for t in tasks]
    measured = fleiss_kappa(ratings)
    disputes = [task['id'] for task, rating in zip(tasks, ratings) if len(set(rating)) != 1]
    passed = measured['kappa'] is not None and measured['kappa'] >= .80
    output = Path(out)
    if output.exists():
        raise ValueError('不能覆盖原始一致率/人工金标记录')
    write(output/'original_reviews.json', {'submissions': [rows(path) for path in submissions],
                                           'files': [{'path': str(path), 'sha256': sha256_file(path)} for path in submissions]})
    report = {'schema_version': SCHEMA, 'task_sha256': package['task_sha256'], 'reviewers': reviewer_ids,
              'agreement': measured, 'disputed_task_ids': disputes, 'agreement_uses_all_original_items': True,
              'field_agreement': {field: fleiss_kappa([[digest(mapping[t['id']]['label'].get(field))
                  for mapping in originals] for t in tasks]) for field in ('status', 'kind', 'error_type', 'abstention_code')},
              'status': 'reannotate_entire_batch' if not passed else 'resolve_disputes_by_humans' if disputes else 'human_gold_ready',
              'identity_verification': 'external_pending', 'professional_rule_signoff': 'pending', 'external_20_item_check': 'pending'}
    write(output/'agreement.json', report)
    if passed and not disputes:
        gold = [{**task, 'gold_origin': 'independent_human_original_pdf', 'evidence_kind': 'original_pdf',
                 'gold_status': originals[0][task['id']]['label']['status'],
                 'gold_error_type': originals[0][task['id']]['label'].get('error_type'),
                 'gold_abstention_code': originals[0][task['id']]['label'].get('abstention_code'),
                 'gold_label': originals[0][task['id']]['label'],
                 'human_reviews': [mapping[task['id']] for mapping in originals], 'task_sha256': package['task_sha256']}
                for task in tasks]
        write(output/'human_gold.jsonl', gold)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare'); p.add_argument('--annotations', type=Path, required=True); p.add_argument('--out', type=Path, required=True)
    for command in ('sign', 'merge'):
        p = sub.add_parser(command); p.add_argument('--tasks', type=Path, required=True); p.add_argument('--corpus', type=Path, required=True); p.add_argument('--out', type=Path, required=True)
        p.add_argument('--submission', type=Path, required=True, **({'nargs': 3} if command == 'merge' else {}))
    args = parser.parse_args(argv)
    result = prepare(args.annotations, args.out) if args.command == 'prepare' else sign(args.tasks, args.submission, args.corpus, args.out) if args.command == 'sign' else merge(args.tasks, args.submission, args.corpus, args.out)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
