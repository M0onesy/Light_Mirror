"""Controlled integrity fixtures; no test attests a real person's credentials."""
import copy
import json
from pathlib import Path
import shutil

import pytest

from bench.v1 import human_review
from bench.v1 import professional_review as review
from mingjing.audit import digest
from mingjing.evidence import sha256_file


ROOT = Path(__file__).resolve().parents[1]


def actor(identifier='professional-01', name='审核人甲'):
    return {'id':identifier, 'name':name, 'profession':'注册会计师', 'reviewer_kind':'human',
            'independent':True, 'attested':True, 'signed_at':'2026-01-01T00:00:00+08:00'}


@pytest.fixture
def rule_batch(tmp_path):
    baseline = tmp_path/'rulepack'
    shutil.copytree(ROOT/'rulepack', baseline, ignore=shutil.ignore_patterns('__pycache__'))
    output = tmp_path/'rule-review'
    result = review.prepare_rules(baseline, output)
    rows = human_review.rows(output/'professional-review.jsonl')
    for row in rows:
        row['reviewer'] = actor()
        row['review'] = {'decision':'approved', 'checks':{key:'passed' for key in review.RULE_CHECKS},
                         'notes':'已逐项阅读原规则、引文、公式、口径与三个边界分类。'}
    raw = tmp_path/'completed-rules.jsonl'
    human_review.write(raw, rows)
    return tmp_path, baseline, output, result, raw


def test_rule_templates_are_pending_and_do_not_rewrite_production_approval(tmp_path):
    before = {path.name:path.read_bytes() for path in (ROOT/'rulepack').glob('*.json')}
    target = tmp_path/'pending'
    result = review.prepare_rules(ROOT/'rulepack', target)
    expected_cards = len(json.loads((ROOT/'rulepack/cards.json').read_text(encoding='utf-8')))
    assert result['rule_cards'] == expected_cards >= 30 and result['professional_acceptance'] is False
    submitted = human_review.rows(target/'professional-review.jsonl')
    assert all(row['review']['decision'] is None and row['reviewer']['id'] is None for row in submitted)
    with pytest.raises(ValueError, match='真人'):
        review.sign(target/'tasks.json', target/'professional-review.jsonl', tmp_path/'signed.jsonl', rulepack=ROOT/'rulepack')
    assert before == {path.name:path.read_bytes() for path in (ROOT/'rulepack').glob('*.json')}
    with pytest.raises(ValueError, match='覆盖'):
        review.prepare_rules(ROOT/'rulepack', target)


def test_signed_rule_content_is_not_authenticated_human_identity(rule_batch):
    root, baseline, output, _, raw = rule_batch
    signed = root/'signed-rules.jsonl'
    assert review.sign(output/'tasks.json', raw, signed, rulepack=baseline)['professional_acceptance'] is False
    report = review.validate(output/'tasks.json', signed, root/'rule-report.json', rulepack=baseline)
    expected_cards = len(json.loads((baseline/'cards.json').read_text(encoding='utf-8')))
    assert report['approved_count'] == expected_cards >= 30
    assert report['identity_verification'] == 'external_pending' and report['professional_acceptance'] is False
    assert all(card['approved_by'] is None for card in json.loads((baseline/'cards.json').read_text(encoding='utf-8')))


@pytest.mark.parametrize('change', ['model','placeholder','no_independence','empty_check','inconsistent_approved','duplicate','short','content_tamper','rule_tamper'])
def test_incomplete_forged_tampered_and_duplicate_rule_submissions_fail(rule_batch, change):
    root, baseline, output, _, raw = rule_batch
    signed = root/'signed-rules.jsonl'
    review.sign(output/'tasks.json', raw, signed, rulepack=baseline)
    rows = human_review.rows(signed)
    if change == 'model': rows[0]['reviewer']['reviewer_kind'] = 'model'
    elif change == 'placeholder': rows[0]['reviewer']['name'] = 'AI reviewer'
    elif change == 'no_independence': rows[0]['reviewer']['independent'] = 'true'
    elif change == 'empty_check': rows[0]['review']['checks']['formula'] = None
    elif change == 'inconsistent_approved': rows[0]['review']['checks']['dimensions'] = 'failed'
    elif change == 'duplicate': rows[-1] = copy.deepcopy(rows[0])
    elif change == 'short': rows.pop()
    elif change == 'content_tamper': rows[0]['review']['notes'] = '签认后修改的说明'
    else: (baseline/'sources/CAS28.md').write_text('changed source clause', encoding='utf-8')
    altered = root/'altered-rules.jsonl'; human_review.write(altered, rows)
    with pytest.raises(ValueError):
        review.validate(output/'tasks.json', altered, root/'should-not-pass.json', rulepack=baseline)
    assert not (root/'should-not-pass.json').exists()


def confirmations(root, person, submission, scope):
    path = root/'identity-record.json'
    human_review.write(path, {'schema_version':review.IDENTITY_SCHEMA, 'reviewers':[{
        'reviewer_id':person['id'], 'reviewer_name':person['name'], 'profession':person['profession'],
        'scope':scope, 'identity_confirmed':True, 'qualification_confirmed':True, 'independence_confirmed':True,
        'verified_by':{'id':'verifier-01','name':'外部核验人'}, 'verified_at':'2026-01-02T00:00:00+08:00',
        'evidence_reference':'机构回访登记册第十二项', 'evidence_sha256':'a'*64,
        'submission_sha256':sha256_file(submission)}]})
    return path


@pytest.mark.parametrize('change', ['unregistered','self_verified','unqualified','wrong_submission','unchanged'])
def test_identity_declaration_is_bound_to_submission_but_never_authenticates_itself(rule_batch, change):
    root, baseline, output, _, raw = rule_batch
    signed = root/'signed-rules.jsonl'; review.sign(output/'tasks.json', raw, signed, rulepack=baseline)
    registry = confirmations(root, actor(), signed, 'rule_signoff')
    record = json.loads(registry.read_text(encoding='utf-8'))
    entry = record['reviewers'][0]
    if change == 'unregistered': entry['reviewer_id'] = 'another-01'
    elif change == 'self_verified': entry['verified_by']['id'] = actor()['id']
    elif change == 'unqualified': entry['qualification_confirmed'] = False
    elif change == 'wrong_submission': entry['submission_sha256'] = 'b'*64
    registry.write_text(json.dumps(record), encoding='utf-8')
    if change != 'unchanged':
        with pytest.raises(ValueError):
            review.validate(output/'tasks.json', signed, root/'report.json', rulepack=baseline, identities=registry)
    else:
        result = review.validate(output/'tasks.json', signed, root/'report.json', rulepack=baseline, identities=registry)
        assert result['identity_verification'] == 'external_attestation_recorded_authentication_requires_user_verification'
        assert result['professional_acceptance'] is False


@pytest.fixture
def external_batch(tmp_path):
    fitz = pytest.importorskip('fitz')
    pdf = tmp_path/'source.pdf'
    with fitz.open() as doc:
        page = doc.new_page()
        for index in range(20): page.insert_text((72,75+index*20), f'{2024-index} Revenue {(index+1)*100}.00')
        doc.save(pdf)
    document = {'path':'source.pdf', 'sha256':sha256_file(pdf)}
    tasks = [{'id':f'A-{index+1:02}', 'set':'A', 'company':'Company', 'target':{'account':'REVENUE','period':f'{2024-index}Y'},
              'source_documents':[document]} for index in range(20)]
    source = tmp_path/'blind-tasks.json'
    package = {'schema_version':human_review.SCHEMA,'tasks':tasks,'task_sha256':digest(tasks)}
    human_review.write(source, package)
    output = tmp_path/'external-pending'; review.prepare_external(source, output)
    rows = human_review.rows(output/'external-review.jsonl')
    for row in rows:
        index = int(row['task_id'].split('-')[-1])-1
        value = f'{(index+1)*100}.00'
        row.update(reviewer=actor('external-01','外部审核人'), label={'status':'verified','kind':'original',
            'notes':'独立阅读原PDF单元格及其期间、单位和口径。', 'cells':[{'printed_value':value,'unit':'元','decimals':2,
            'binding':{'company':'Company','account':'REVENUE','period':f'{2024-index}Y','scope':'合并','relation':'value','base_period':''},
            'source_version':'原披露','publish_date':'2025-03-01','evidence':[{'path':'source.pdf','sha256':document['sha256'],
            'page':1,'bbox':[50,58+index*20,300,80+index*20],'table':'Revenue','row':str(index+1),'col':'2024',
            'transcription':'Revenue '+value}]}]})
    raw = tmp_path/'completed-external.jsonl'; human_review.write(raw, rows)
    gold = []
    for row in rows:
        original = []
        for number, name in enumerate(('原审核人甲','原审核人乙','原审核人丙')):
            original_row = {'schema_version':human_review.SCHEMA,'task_id':row['task_id'],'task_sha256':package['task_sha256'],
                            'reviewer':actor(f'original-{number+1}', name),'label':copy.deepcopy(row['label'])}
            original_row['signed_content_sha256'] = digest(original_row)
            original.append(original_row)
        gold.append({'id':row['task_id'],'gold_origin':'independent_human_original_pdf','task_sha256':package['task_sha256'],
                     'gold_label':copy.deepcopy(row['label']),'human_reviews':original})
    gold_path = tmp_path/'gold.jsonl'; human_review.write(gold_path, gold)
    return tmp_path, source, output, raw, gold_path


def test_external_twenty_checks_use_original_pdf_and_remain_pending_identity(external_batch):
    root, source, output, raw, gold = external_batch
    signed = root/'signed-external.jsonl'; review.sign(output/'tasks.json', raw, signed, corpus=root)
    result = review.validate(output/'tasks.json', signed, root/'external-report.json', corpus=root, gold=gold)
    assert result['external_agreement'] == {'matched':20,'total':20}
    assert result['professional_acceptance'] is False and result['identity_verification'] == 'external_pending'
    # Deterministic blind sampling is insensitive to input list order once its
    # exact source lock and seed are frozen. It never consults the gold values.
    second = root/'second-external'; review.prepare_external(source, second)
    assert json.loads((second/'tasks.json').read_text(encoding='utf-8'))['task_sha256'] == json.loads((output/'tasks.json').read_text(encoding='utf-8'))['task_sha256']


@pytest.mark.parametrize('change', ['nineteen','duplicate','pdf_tamper','gold_not_human','original_actor','changed_gold'])
def test_external_sample_count_duplicates_source_hash_and_independence_fail(external_batch, change):
    root, _, output, raw, gold = external_batch
    signed = root/'signed-external.jsonl'; review.sign(output/'tasks.json', raw, signed, corpus=root)
    rows = human_review.rows(signed)
    if change == 'nineteen': rows.pop()
    elif change == 'duplicate': rows[-1] = copy.deepcopy(rows[0])
    elif change == 'pdf_tamper': (root/'source.pdf').write_bytes(b'changed PDF')
    elif change in {'gold_not_human','changed_gold'}:
        records = human_review.rows(gold)
        if change == 'gold_not_human': records[0]['gold_origin'] = 'machine_candidate'
        else: records[0]['gold_label']['cells'][0]['printed_value'] = '999999.00'
        gold.write_text('\n'.join(json.dumps(record) for record in records)+'\n', encoding='utf-8')
    else:
        for row in rows:
            row['reviewer'] = actor('original-1','原审核人甲')
            row['signed_content_sha256'] = digest({key:value for key,value in row.items() if key != 'signed_content_sha256'})
    altered = root/'altered-external.jsonl'; human_review.write(altered, rows)
    with pytest.raises(ValueError): review.validate(output/'tasks.json', altered, root/'failed.json', corpus=root, gold=gold)
    assert not (root/'failed.json').exists()


def test_d3_and_fewer_than_twenty_source_tasks_cannot_be_prepared(external_batch):
    root, source, _, _, _ = external_batch
    package = json.loads(source.read_text(encoding='utf-8'))
    package['tasks'][0]['set'] = 'D3'; package['task_sha256'] = digest(package['tasks'])
    source.write_text(json.dumps(package), encoding='utf-8')
    with pytest.raises(ValueError, match='D3'): review.prepare_external(source, root/'d3')
    package['tasks'][0]['set'] = 'A'; package['tasks'].pop(); package['task_sha256'] = digest(package['tasks'])
    source.write_text(json.dumps(package), encoding='utf-8')
    with pytest.raises(ValueError, match='20'): review.prepare_external(source, root/'short')


def test_alias_task_ids_cannot_fake_twenty_distinct_external_checks(external_batch):
    root, source, _, _, _ = external_batch
    package = json.loads(source.read_text(encoding='utf-8'))
    package['tasks'][1]['target'] = copy.deepcopy(package['tasks'][0]['target'])
    package['task_sha256'] = digest(package['tasks'])
    source.write_text(json.dumps(package), encoding='utf-8')
    with pytest.raises(ValueError, match='重复抽检'):
        review.prepare_external(source, root/'aliases')
