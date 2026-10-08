"""Controlled annotation validation; no fixture claims independent humans."""
import copy
import json

import pytest

from bench.v1 import human_review as review
from mingjing.audit import digest
from mingjing.evidence import sha256_file


@pytest.fixture
def batch(tmp_path):
    fitz = pytest.importorskip('fitz')
    pdf = tmp_path/'source.pdf'
    with fitz.open() as doc:
        page = doc.new_page()
        for index, value in enumerate(('100.00', '200.00', '300.00')):
            page.insert_text((72, 100+index*80), 'Revenue '+value)
        doc.save(pdf)
    tasks = [{'id': f'A-{index}', 'set': 'A', 'company': 'Company',
              'source_documents': [{'path': 'source.pdf', 'sha256': sha256_file(pdf)}]} for index in range(3)]
    package = {'schema_version': review.SCHEMA, 'task_sha256': digest(tasks), 'tasks': tasks}
    tasks_file = tmp_path/'tasks.json'
    tasks_file.write_text(json.dumps(package), encoding='utf-8')

    def submission(reviewer_id):
        result = []
        for index, task in enumerate(tasks):
            value = f'{(index+1)*100}.00'
            label = {'status': 'verified', 'kind': 'original', 'notes': 'Controlled fixture only.', 'cells': [
                {'printed_value': value, 'unit': '元', 'decimals': 2,
                 'binding': {'company': 'Company', 'account': 'REVENUE', 'period': '2024Y',
                             'scope': '合并', 'relation': 'value', 'base_period': ''},
                 'source_version': '原披露', 'publish_date': '2025-03-01',
                 'evidence': [{'path': 'source.pdf', 'sha256': sha256_file(pdf), 'page': 1,
                    'bbox': [60, 80+index*80, 250, 105+index*80], 'table': 'Revenue', 'row': str(index+1),
                    'col': '2024', 'transcription': 'Revenue '+value}]}]}
            row = {'schema_version': review.SCHEMA, 'task_id': task['id'], 'task_sha256': package['task_sha256'],
                   'reviewer': {'id': reviewer_id, 'name': 'Fixture actor '+reviewer_id, 'profession': 'Test fixture',
                       'reviewer_kind': 'human', 'independent': True, 'attested': True, 'signed_at': '2026-10-07T10:00:00+08:00'}, 'label': label}
            row['signed_content_sha256'] = digest(row)
            result.append(row)
        return result
    return tmp_path, package, tasks_file, submission


def test_missing_label_cannot_pass_three_reviewer_check(batch):
    root, package, _, submission = batch
    row = submission('one')[0]
    row['label'] = None
    with pytest.raises(ValueError, match='label'):
        review.validate_review(row, package['tasks'][0], package['task_sha256'], root)


@pytest.mark.parametrize('change', ['model', 'false_independence', 'tampered', 'wrong_value', 'wrong_box'])
def test_forged_or_incomplete_annotations_rejected(batch, change):
    root, package, _, submission = batch
    row = submission('one')[0]
    if change == 'model': row['reviewer']['reviewer_kind'] = 'model'
    if change == 'false_independence': row['reviewer']['independent'] = 'true'
    if change == 'tampered': row['label']['notes'] = 'Changed after attestation'
    if change == 'wrong_value': row['label']['cells'][0]['printed_value'] = '999.00'
    if change == 'wrong_box': row['label']['cells'][0]['evidence'][0]['bbox'] = [60, 0, 250, 20]
    with pytest.raises(ValueError):
        review.validate_review(row, package['tasks'][0], package['task_sha256'], root)


def test_three_original_submissions_agreement_and_content_lock(batch):
    root, package, tasks_file, submission = batch
    files = []
    for actor in ('one', 'two', 'three'):
        path = root/f'{actor}.jsonl'
        review.write(path, submission(actor)); files.append(path)
    receipt = review.merge(tasks_file, files, root, root/'merged')
    assert receipt['agreement']['kappa'] == 1
    assert receipt['status'] == 'human_gold_ready'
    assert receipt['identity_verification'] == 'external_pending'
    assert receipt['field_agreement']['status']['kappa'] is None
    assert len(review.rows(root/'merged'/'human_gold.jsonl')) == 3
    with pytest.raises(ValueError, match='覆盖'):
        review.merge(tasks_file, files, root, root/'merged')


def test_kappa_includes_disputed_items_and_single_category_is_unknown():
    result = review.fleiss_kappa([['a','a','a'], ['b','b','b'], ['a','b','c']])
    assert result['raw_agreement'] == pytest.approx(2/3)
    assert result['kappa'] < .80 and result['items'] == 3
    assert review.fleiss_kappa([['only','only','only']])['kappa'] is None


def test_blind_prepare_does_not_expose_machine_amounts(tmp_path):
    source = tmp_path/'annotations'; source.mkdir()
    ref = {'path': 'source.pdf', 'sha256': 'a'*64}
    review.write(source/'A_candidates.jsonl', [{'id':'A-1', 'company':'Company', 'ticker':'000001',
        'period':'2024Y', 'account_candidate':'REVENUE', 'source_document':ref,
        'observed_candidate':{'value':'999999.99'}}])
    review.write(source/'P_candidates.json', [{'id':'P-1', 'company':'Company', 'ticker':'000001',
        'source_document':ref, 'before_after_candidates':[{'row_label_candidate':'Assets',
        'printed_numeric_tokens':['123456.78','876543.21'], 'row_text':'123456.78'}]}])
    result = review.prepare(source, tmp_path/'blind')
    assert result['A'] == 1 and result['P'] == 1
    contents = ''.join(path.read_text(encoding='utf-8') for path in (tmp_path/'blind').glob('*'))
    assert all(value not in contents for value in ('999999.99','123456.78','876543.21'))
