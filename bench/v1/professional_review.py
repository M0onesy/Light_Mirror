"""Frozen rule signoff and external PDF spot checks awaiting real professionals.

Content hashes establish integrity only. This tool never authenticates human
identity or modifies the production rule package's approval fields.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re

from bench.v1 import human_review
from mingjing.audit import digest
from mingjing.config import validate_path
from mingjing.evidence import sha256_file


SCHEMA = 'mingjing-professional-review.v1'
IDENTITY_SCHEMA = 'mingjing-external-identity-attestation.v1'
RULE_CHECKS = ('basis', 'formula', 'dimensions', 'applicable_caliber', 'positive_example',
               'negative_example', 'boundary_example', 'source_version', 'conflicts')


def _rule_digest(value):
    # Preserve the existing production rulepack's compact canonical format;
    # human-review task/content locks use audit.digest's separate schema.
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'缺少{name}')
    return value


def _identity_text(value, name):
    _text(value, name)
    if re.search(r'\b(?:placeholder|fixture|unknown|pending|test|model|agent|AI)\b|待填|待签|占位|虚构|测试|机器人|模型|代理', value, re.I):
        raise ValueError(f'{name}不能使用占位或代理身份')
    return value


def _sha(value, name):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError(f'{name}不是SHA-256')
    return value


def _time(value, name):
    instant = datetime.fromisoformat(_text(value, name).replace('Z', '+00:00'))
    if instant.tzinfo is None or instant > datetime.now(timezone.utc):
        raise ValueError(f'{name}必须带时区且不能在未来')
    return value


def _person(value):
    if not isinstance(value, dict):
        raise ValueError('缺少真人审阅者身份')
    for field in ('id', 'name', 'profession'):
        _identity_text(value.get(field), '真人'+field)
    _time(value.get('signed_at'), '真人签认时间')
    if (value.get('reviewer_kind') != 'human' or value.get('independent') is not True
            or value.get('attested') is not True):
        raise ValueError('必须由独立真人本人签认，代理不能代替')


def _person_template():
    return {'id': None, 'name': None, 'profession': None, 'reviewer_kind': 'human',
            'independent': False, 'attested': False, 'signed_at': None}


def rule_lock(rulepack):
    root = validate_path(rulepack, must_exist=True, kind='dir', follow_symlinks=False)
    path = root/'rulepack.lock'
    lock = json.loads(path.read_text(encoding='utf-8'))
    files = lock.get('files')
    if not isinstance(files, dict) or not files or lock.get('sha256') != _rule_digest(files):
        raise ValueError('规则包清单哈希不符')
    for relative, expected in files.items():
        part = PurePosixPath(relative)
        if (part.is_absolute() or part.as_posix() != relative or not part.parts
                or any(p in {'.', '..'} or ':' in p or '\\' in p for p in part.parts)):
            raise ValueError('规则包路径非法')
        source = validate_path(root/relative, allowed_roots=[str(root)], must_exist=True,
                               kind='file', follow_symlinks=False)
        if sha256_file(source) != _sha(expected, '规则文件哈希'):
            raise ValueError('规则文件已变动，必须建立新签发批次')
    cards = json.loads((root/'cards.json').read_text(encoding='utf-8'))
    if not isinstance(cards, list) or len(cards) < 30:
        raise ValueError('规则签发至少需要30张完整规则卡')
    ids = [card.get('id') for card in cards]
    if any(not isinstance(value, str) or not value for value in ids) or len(ids) != len(set(ids)):
        raise ValueError('规则卡ID缺失或重复')
    for card in cards:
        if card.get('version_hash') != _rule_digest({k:v for k,v in card.items() if k != 'version_hash'}):
            raise ValueError('规则卡版本哈希不符')
        if not all(key in card for key in ('basis', 'basis_refs', 'formula', 'dimension', 'applicable_caliber', 'examples')):
            raise ValueError('规则卡的依据、公式、量纲、口径和例证不完整')
        if not {'positive', 'negative', 'boundary'} <= set(card['examples']):
            raise ValueError('规则卡必须包含正例、反例和边界例')
    return {'version': lock['version'], 'rulepack_sha256': lock['sha256'],
            'rulepack_lock_file_sha256': sha256_file(path), 'files': files}, cards


def _create_package(out, kind, tasks, source_lock):
    output = Path(out)
    if output.exists():
        raise ValueError('签认模板与旧记录不能覆盖；请选择新的目录')
    package = {'schema_version': SCHEMA, 'kind': kind, 'created_at': datetime.now(timezone.utc).isoformat(),
               'tasks': tasks, 'source_lock': source_lock, 'status': 'awaiting_external_human_review'}
    package['task_sha256'] = digest({'kind': kind, 'tasks': tasks, 'source_lock': source_lock})
    human_review.write(output/'tasks.json', package)
    return output, package


def _identities_template(out, scope):
    human_review.write(out/'identity-confirmations.json', {'schema_version': IDENTITY_SCHEMA,
        'authentication_status': 'external_pending', 'reviewers': [{'reviewer_id': None,
        'reviewer_name': None, 'profession': None, 'scope': scope,
        'identity_confirmed': False, 'qualification_confirmed': False, 'independence_confirmed': False,
        'verified_by': {'id': None, 'name': None}, 'verified_at': None,
        'evidence_reference': None, 'evidence_sha256': None, 'submission_sha256': None}]})


def prepare_rules(rulepack, out):
    locked, cards = rule_lock(rulepack)
    target = Path(out).absolute()
    if target.is_relative_to(Path(rulepack).absolute()):
        raise ValueError('签发模板不能写入生产规则包')
    tasks = [{'id': card['id'], 'card_version_hash': card['version_hash'], 'rule_card': card} for card in cards]
    output, package = _create_package(out, 'rule_signoff', tasks, locked)
    human_review.write(output/'professional-review.jsonl', [{'schema_version': SCHEMA,
        'task_id': task['id'], 'task_sha256': package['task_sha256'], 'reviewer': _person_template(),
        'review': {'decision': None, 'checks': {name: None for name in RULE_CHECKS}, 'notes': None},
        'signed_content_sha256': None} for task in tasks])
    _identities_template(output, 'rule_signoff')
    return {'status': 'awaiting_external_human_review', 'rule_cards': len(tasks),
            'task_sha256': package['task_sha256'], 'professional_acceptance': False}


def _blind_tasks(source):
    package = json.loads(Path(source).read_text(encoding='utf-8'))
    tasks = package.get('tasks')
    if (package.get('schema_version') != human_review.SCHEMA or not isinstance(tasks, list)
            or package.get('task_sha256') != digest(tasks)):
        raise ValueError('原真人盲标任务锁不符')
    if any(task.get('set') not in {'A', 'P'} for task in tasks):
        raise ValueError('外部抽检只接收已登记A/P开发材料，不接触D3')
    ids = [task.get('id') for task in tasks]
    if len(tasks) < 20 or any(not isinstance(value, str) or not value for value in ids) or len(ids) != len(set(ids)):
        raise ValueError('外部抽检至少需要20个不重复原任务')
    for task in tasks:
        if not task.get('company') or not isinstance(task.get('source_documents'), list) or not task['source_documents']:
            raise ValueError('抽检任务缺少公司及原PDF')
        for doc in task['source_documents']:
            if not isinstance(doc.get('path'), str) or not doc['path'].lower().endswith('.pdf'):
                raise ValueError('外部抽检必须查阅原PDF')
            _sha(doc.get('sha256'), '原PDF哈希')
    keys = [_sample_key(task) for task in tasks]
    if len(keys) != len(set(keys)):
        raise ValueError('不同ID指向同一材料与核查对象，属于重复抽检')
    return package


def _sample_key(task):
    return digest({'set':task.get('set'), 'company':task.get('company'), 'target':task.get('target', {}),
                   'source_document_hashes':sorted(doc.get('sha256') for doc in task.get('source_documents', []))})


def prepare_external(tasks_file, out, seed='mingjing-external-check-v1'):
    source = _blind_tasks(tasks_file)
    _text(seed, '抽检种子')
    # Rank the whole locked registry, without consulting machine results.
    selected = sorted(source['tasks'], key=lambda task: digest({'seed': seed,
        'source_task_sha256': source['task_sha256'], 'task_id': task['id']}))[:20]
    tasks = [{key:task[key] for key in ('id', 'set', 'company', 'source_documents')}
             | {'target': task.get('target', {})} for task in selected]
    locked = {'source_tasks_file_sha256': sha256_file(tasks_file), 'source_task_sha256': source['task_sha256'],
              'source_task_count': len(source['tasks']), 'sampling_seed': seed,
              'sampling_method': 'sha256_rank_all_locked_A_P_tasks_without_predictions',
              'scope': 'development_materials_not_D3'}
    output, package = _create_package(out, 'external_20_item_check', tasks, locked)
    human_review.write(output/'external-review.jsonl', [{'schema_version': SCHEMA,
        'task_id': task['id'], 'task_sha256': package['task_sha256'], 'reviewer': _person_template(),
        'label': None, 'signed_content_sha256': None} for task in tasks])
    _identities_template(output, 'external_20_item_check')
    return {'status': 'awaiting_external_human_review', 'external_items': len(tasks),
            'task_sha256': package['task_sha256'], 'professional_acceptance': False}


def _load_package(tasks_file, kind):
    package = json.loads(Path(tasks_file).read_text(encoding='utf-8'))
    if (package.get('schema_version') != SCHEMA or package.get('kind') != kind
            or package.get('task_sha256') != digest({key:package.get(key) for key in ('kind', 'tasks', 'source_lock')})):
        raise ValueError('专业审阅任务清单哈希不符')
    tasks = package.get('tasks')
    if not isinstance(tasks, list) or len(tasks) < (30 if kind == 'rule_signoff' else 20):
        raise ValueError('专业审阅规模不足30张规则卡或20条外部抽检')
    if kind != 'rule_signoff' and (len(tasks) != 20 or any(task.get('set') not in {'A', 'P'} for task in tasks)):
        raise ValueError('外部抽检必须是20条A/P材料')
    if kind != 'rule_signoff' and len({_sample_key(task) for task in tasks}) != len(tasks):
        raise ValueError('不能用不同ID重复抽检同一对象')
    if len({task['id'] for task in tasks}) != len(tasks):
        raise ValueError('重复规则卡或重复抽检任务')
    return package


def _reviews(package, submission, *, rulepack=None, corpus=None, signed=True):
    submitted = human_review.rows(submission)
    tasks = {task['id']: task for task in package['tasks']}
    if (not isinstance(submitted, list) or len(submitted) != len(tasks)
            or {row.get('task_id') for row in submitted} != set(tasks)):
        raise ValueError('必须完成全部不重复规则卡或20条抽检；不能减少样本')
    if package['kind'] == 'rule_signoff':
        locked, cards = rule_lock(rulepack)
        expected = [{'id': card['id'], 'card_version_hash': card['version_hash'], 'rule_card': card} for card in cards]
        if locked != package['source_lock'] or expected != package['tasks']:
            raise ValueError('规则包哈希已变动，旧签认不能沿用')
    persons = []
    for row in submitted:
        if row.get('schema_version') != SCHEMA or row.get('task_sha256') != package['task_sha256']:
            raise ValueError('真人签认与冻结任务不一致')
        _person(row.get('reviewer'))
        persons.append(row['reviewer'])
        if package['kind'] == 'rule_signoff':
            review = row.get('review')
            if not isinstance(review, dict) or review.get('decision') not in {'approved', 'changes_required', 'rejected'}:
                raise ValueError('规则卡缺少真人签发决定')
            checks = review.get('checks')
            if not isinstance(checks, dict) or set(checks) != set(RULE_CHECKS) or any(value not in {'passed', 'failed'} for value in checks.values()):
                raise ValueError('规则卡九项专业审阅不能为空')
            _text(review.get('notes'), '专业审阅说明')
            if review['decision'] == 'approved' and any(value != 'passed' for value in checks.values()):
                raise ValueError('未通过的规则卡不能签发approved')
        else:
            human_review.validate_label(row.get('label'), tasks[row['task_id']], corpus)
        content_hash = digest({key:value for key,value in row.items() if key != 'signed_content_sha256'})
        if signed and row.get('signed_content_sha256') != content_hash:
            raise ValueError('签认内容哈希不符；变更后须真人重新签认')
        if not signed:
            row['signed_content_sha256'] = content_hash
    if any(person != persons[0] for person in persons):
        raise ValueError('一份完整提交只能对应同一独立真人')
    return submitted, persons[0]


def sign(tasks_file, submission, out, *, rulepack=None, corpus=None):
    kind = 'rule_signoff' if rulepack is not None else 'external_20_item_check'
    package = _load_package(tasks_file, kind)
    submitted, _ = _reviews(package, submission, rulepack=rulepack, corpus=corpus, signed=False)
    human_review.write(out, submitted)
    return {'status': 'content_attested_identity_requires_external_verification', 'rows': len(submitted),
            'identity_verification': 'external_pending', 'professional_acceptance': False}


def _identity_attestation(path, person, submission, scope):
    if path is None:
        return 'external_pending'
    record = json.loads(Path(path).read_text(encoding='utf-8'))
    entries = record.get('reviewers')
    if record.get('schema_version') != IDENTITY_SCHEMA or not isinstance(entries, list) or not entries:
        raise ValueError('外部真人核验记录为空')
    ids = [entry.get('reviewer_id') for entry in entries]
    if len(ids) != len(set(ids)):
        raise ValueError('真人核验记录重复')
    match = next((entry for entry in entries if entry.get('reviewer_id') == person['id']), None)
    if not match:
        raise ValueError('提交身份未登记，不能接受未核验或伪造身份')
    if (match.get('scope') != scope or match.get('reviewer_name') != person['name']
            or match.get('profession') != person['profession'] or match.get('submission_sha256') != sha256_file(submission)):
        raise ValueError('外部核验必须绑定真人身份、专业资格与该份原始签认文件')
    if any(match.get(key) is not True for key in ('identity_confirmed', 'qualification_confirmed', 'independence_confirmed')):
        raise ValueError('真人身份、资格及独立性尚未由外部核验')
    verifier = match.get('verified_by')
    if not isinstance(verifier, dict):
        raise ValueError('外部身份核验者为空')
    _identity_text(verifier.get('id'), '外部核验者ID'); _identity_text(verifier.get('name'), '外部核验者姓名')
    if verifier['id'] == person['id'] or verifier['name'] == person['name']:
        raise ValueError('审阅者不能自己核验自己的独立性')
    _time(match.get('verified_at'), '外部身份核验时间')
    _text(match.get('evidence_reference'), '外部身份和资格核验凭据')
    _sha(match.get('evidence_sha256'), '外部凭据哈希')
    # An untrusted JSON declaration cannot authenticate the actual people.
    return 'external_attestation_recorded_authentication_requires_user_verification'


def validate(tasks_file, submission, out, *, rulepack=None, corpus=None, gold=None, identities=None):
    kind = 'rule_signoff' if rulepack is not None else 'external_20_item_check'
    package = _load_package(tasks_file, kind)
    submitted, person = _reviews(package, submission, rulepack=rulepack, corpus=corpus)
    identity = _identity_attestation(identities, person, submission, kind)
    result = {'schema_version': SCHEMA, 'task_sha256': package['task_sha256'], 'kind': kind,
              'rows': len(submitted), 'reviewer_id': person['id'], 'submission_sha256': sha256_file(submission),
              'identity_verification': identity, 'professional_acceptance': False,
              'status': 'complete_content_identity_external_pending'}
    if identities is not None:
        result['identity_attestation_file_sha256'] = sha256_file(identities)
    if rulepack is not None:
        result['approved_count'] = sum(row['review']['decision'] == 'approved' for row in submitted)
        result['rulepack_lock'] = package['source_lock']
        if result['approved_count'] != len(submitted):
            result['status'] = 'rule_cards_require_professional_correction'
    elif gold is None:
        result['reference_gold'] = 'pending_independent_human_gold'
        result['external_agreement'] = None
        result['status'] = 'external_check_reference_gold_pending'
    else:
        records = human_review.rows(gold)
        if not isinstance(records, list) or len({row['id'] for row in records}) != len(records):
            raise ValueError('外部抽检的原人工金标重复或非法')
        mapping = {row['id']:row for row in records}
        matched, disagreements = 0, []
        for row in submitted:
            reference = mapping.get(row['task_id'])
            if (not reference or reference.get('gold_origin') != 'independent_human_original_pdf'
                    or reference.get('task_sha256') != package['source_lock']['source_task_sha256']):
                raise ValueError('抽检必须使用同批冻结任务的独立真人原PDF金标')
            task = next(task for task in package['tasks'] if task['id'] == row['task_id'])
            originals = reference.get('human_reviews')
            if not isinstance(originals, list) or len(originals) != 3:
                raise ValueError('抽检金标缺少三份原始真人评级')
            ids = []
            for original in originals:
                human_review.validate_review(original, task, reference['task_sha256'], corpus)
                _person(original['reviewer']); ids.append(original['reviewer']['id'])
            original_names = [original['reviewer']['name'] for original in originals]
            if len(set(ids)) != 3 or len(set(original_names)) != 3 or person['id'] in ids or person['name'] in original_names:
                raise ValueError('外部抽检者必须独立于三名原始标注者')
            keys = [human_review.label_key(original['label']) for original in originals]
            if any(key != keys[0] for key in keys) or human_review.label_key(reference['gold_label']) != keys[0]:
                raise ValueError('原始金标有未解决分歧或已被修改')
            if human_review.label_key(row['label']) == keys[0]:
                matched += 1
            else:
                disagreements.append(row['task_id'])
        result.update(reference_gold_sha256=sha256_file(gold), external_agreement={'matched':matched, 'total':20},
                      disputed_task_ids=disagreements)
        if disagreements:
            result['status'] = 'external_check_requires_human_correction'
    human_review.write(out, result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare-rules'); p.add_argument('--rulepack', type=Path, required=True); p.add_argument('--out', type=Path, required=True)
    p = sub.add_parser('prepare-external'); p.add_argument('--tasks', type=Path, required=True); p.add_argument('--out', type=Path, required=True); p.add_argument('--seed', default='mingjing-external-check-v1')
    for command in ('sign-rules', 'validate-rules', 'sign-external', 'validate-external'):
        p = sub.add_parser(command); p.add_argument('--tasks', type=Path, required=True); p.add_argument('--submission', type=Path, required=True); p.add_argument('--out', type=Path, required=True)
        if command.endswith('rules'): p.add_argument('--rulepack', type=Path, required=True)
        else: p.add_argument('--corpus', type=Path, required=True)
        if command.startswith('validate'):
            p.add_argument('--identity-confirmations', type=Path)
            if command.endswith('external'): p.add_argument('--gold', type=Path)
    args = parser.parse_args(argv)
    if args.command == 'prepare-rules': result = prepare_rules(args.rulepack, args.out)
    elif args.command == 'prepare-external': result = prepare_external(args.tasks, args.out, args.seed)
    else:
        options = {'rulepack': getattr(args, 'rulepack', None), 'corpus': getattr(args, 'corpus', None)}
        if args.command.startswith('sign'): result = sign(args.tasks, args.submission, args.out, **options)
        else: result = validate(args.tasks, args.submission, args.out, gold=getattr(args, 'gold', None), identities=args.identity_confirmations, **options)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
