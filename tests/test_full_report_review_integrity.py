"""Synthetic review records test mechanics, never real human acceptance."""
import copy
import json

import pytest

from bench.v1.human_review import rows, write
from mingjing.audit import digest
from mingjing.evidence import sha256_file
from scripts import full_report_review as review


@pytest.fixture
def batch(tmp_path):
    corpus = tmp_path/'corpus'
    corpus.mkdir()
    draft = corpus/'research.md'
    draft.write_text('2024年营业收入100元。\n2024年营业收入100元。\n2025E营业收入200元。\n页码1。\n', encoding='utf-8')
    report = corpus/'annual.txt'
    report.write_text('财报原文，独立判断用，不导出机器事实。', encoding='utf-8')
    task = {'id': 'full-report', 'set': 'development', 'company': '甲公司', 'period': '2024Y',
            'report_date': '2025-03-31', 'scope_declaration': '',
            'draft': {'path': draft.name, 'sha256': sha256_file(draft)},
            'source_documents': [{'path': report.name, 'sha256': sha256_file(report)}]}
    plan = tmp_path/'plan.json'
    write(plan, {'schema_version': review.PLAN_SCHEMA, 'tasks': [task]})
    out = tmp_path/'blind'
    review.prepare(plan, corpus, out)
    package, indexes = review.load_package(out/'tasks.json', corpus)
    index = indexes[task['id']]

    def submission(actor):
        result = rows(out/'reviewer-1.jsonl')
        entry = result[0]
        entry['reviewer'] = {'id': actor, 'name': 'Synthetic actor '+actor, 'profession': 'Controlled test fixture',
            'reviewer_kind': 'human', 'independent': True, 'attested': True, 'signed_at': '2026-10-07T10:00:00+08:00'}
        entry['page_reviews'] = [dict(page, status='reviewed', notes='Synthetic full-page inspection.') for page in entry['page_reviews']]
        entry['completeness_attested'] = True
        entry['occurrences'] = []
        # These declared source lexemes are test-only original human records,
        # independently addressed; no model/fact/coverage candidates are used.
        marks = [('2024年', 'non_financial_numeric'), ('100元', 'financial_in_scope'),
                 ('2024年', 'non_financial_numeric'), ('100元', 'financial_in_scope'),
                 ('2025E', 'non_financial_numeric'), ('200元', 'financial_out_of_scope'),
                 ('1', 'non_financial_numeric')]
        cursor = 0
        for text, category in marks:
            start = index['text'].index(text, cursor)
            end = start+len(text)
            cursor = end
            block = next(b for b in index['blocks'] if b['start'] is not None and b['start'] <= start < end <= b['end'])
            financial = category != 'non_financial_numeric'
            entry['occurrences'].append({'location': {'page_id': block['page_id'], 'block_id': block['id'],
                'start': start, 'end': end, 'text': text}, 'category': category, 'form': 'prose',
                'binding': dict(company='甲公司', account='REVENUE', period='2024Y', scope='合并', relation='value', base_period='') if financial else None,
                'unit': '元' if financial else None, 'currency': 'CNY' if financial else None,
                'scope_reason': '预测陈述' if category == 'financial_out_of_scope' else None,
                'notes': 'Synthetic original-span inspection only.'})
        return result

    def signed(actor, mutate=None):
        items = submission(actor)
        if mutate:
            mutate(items[0])
        path = tmp_path/(actor+'.jsonl')
        write(path, items)
        target = tmp_path/(actor+'-signed.jsonl')
        review.sign(out/'tasks.json', path, corpus, target)
        return target

    return dict(root=tmp_path, corpus=corpus, draft=draft, report=report, task=task, plan=plan,
                out=out, package=package, index=index, submission=submission, signed=signed)


def test_blind_package_preserves_all_original_text_but_contains_no_machine_candidates(batch):
    root = batch['out']
    index = batch['index']
    assert index['text'] == batch['draft'].read_text(encoding='utf-8')
    assert '100元' in index['text'] and '200元' in index['text']
    assert all('table_candidate' not in block for block in index['blocks'])
    template = rows(root/'reviewer-1.jsonl')[0]
    assert template['occurrences'] is None and not template['completeness_attested']
    assert template['execution_task_sha256'] == digest(review.execution_task_identity(batch['task']))
    with pytest.raises(ValueError):
        review.sign(root/'tasks.json', root/'reviewer-1.jsonl', batch['corpus'], batch['root']/'fake-signed.jsonl')
    assert not (batch['root']/'fake-signed.jsonl').exists()


@pytest.mark.parametrize('extra', ['observed_candidate', 'candidate_value', 'facts', 'coverage', 'predictions', 'gold_label'])
def test_machine_fields_cannot_enter_an_independent_full_report_plan(batch, extra):
    plan = json.loads(batch['plan'].read_text(encoding='utf-8'))
    plan['tasks'][0][extra] = '999999'
    path = batch['root']/'machine-plan.json'
    write(path, plan)
    with pytest.raises(ValueError, match='original execution inputs'):
        review.prepare(path, batch['corpus'], batch['root']/'rejected')
    assert not (batch['root']/'rejected').exists()


def test_d3_and_overwritten_packages_are_rejected_before_material_access(batch):
    task = dict(batch['task'], set='D3')
    with pytest.raises(ValueError, match='D3'):
        review._task(task, batch['corpus'])
    with pytest.raises(ValueError, match='overwrite'):
        review.prepare(batch['plan'], batch['corpus'], batch['out'])


@pytest.mark.parametrize('damage', ['raw_bytes', 'index', 'forged_index', 'task', 'execution_lock'])
def test_material_indices_and_task_locks_cannot_be_forged(batch, damage):
    tasks_file = batch['out']/'tasks.json'
    package = json.loads(tasks_file.read_text(encoding='utf-8'))
    task = package['tasks'][0]
    if damage == 'raw_bytes':
        batch['draft'].write_text('Changed source amounts.', encoding='utf-8')
    elif damage in {'index', 'forged_index'}:
        path = batch['out']/task['draft_index']['path']
        index = json.loads(path.read_text(encoding='utf-8'))
        index['text'] = index['text'].replace('100元', '999元')
        path.write_text(json.dumps(index), encoding='utf-8')
        if damage == 'forged_index':
            task['draft_index']['content_sha256'] = digest(index)
    elif damage == 'task':
        task['report_date'] = '2026-03-31'
    else:
        task['execution_task_sha256'] = '0'*64
    if damage != 'raw_bytes':
        if damage == 'forged_index':
            package['task_sha256'] = digest(package['tasks'])
            package['package_sha256'] = digest({k: v for k, v in package.items() if k != 'package_sha256'})
        tasks_file.write_text(json.dumps(package), encoding='utf-8')
    with pytest.raises(ValueError):
        review.load_package(tasks_file, batch['corpus'])


@pytest.mark.parametrize('damage', ['model', 'no_attestation', 'wrong_date_lock', 'missing_page', 'wrong_block',
                                  'wrong_transcription', 'invalid_span', 'duplicate', 'missing_binding'])
def test_partial_forged_or_model_occurrences_cannot_be_signed(batch, damage):
    entry = batch['submission']('actor')[0]
    if damage == 'model': entry['reviewer']['reviewer_kind'] = 'model'
    elif damage == 'no_attestation': entry['completeness_attested'] = False
    elif damage == 'wrong_date_lock': entry['execution_task_sha256'] = 'f'*64
    elif damage == 'missing_page': entry['page_reviews'] = []
    elif damage == 'wrong_block': entry['occurrences'][0]['location']['block_id'] = 'invented'
    elif damage == 'wrong_transcription': entry['occurrences'][1]['location']['text'] = '999元'
    elif damage == 'invalid_span': entry['occurrences'][1]['location']['start'] = True
    elif damage == 'duplicate': entry['occurrences'].append(copy.deepcopy(entry['occurrences'][1]))
    else: entry['occurrences'][1]['binding'].pop('base_period')
    with pytest.raises(ValueError):
        review.validate_review(entry, batch['package']['tasks'][0], batch['package'], batch['index'], require_signature=False)


def test_three_unanimous_original_reviews_keep_duplicate_text_occurrences_and_external_identity_pending(batch):
    signed = [batch['signed'](actor) for actor in ('one', 'two', 'three')]
    output = batch['root']/'consensus'
    report = review.merge(batch['out']/'tasks.json', signed, batch['corpus'], output)
    assert report['classification_agreement']['kappa'] == 1
    assert report['classification_agreement']['items'] == 7
    assert report['discovery_agreement']['kappa'] is None
    assert report['status'] == 'content_consensus_identity_pending'
    assert not report['professional_acceptance'] and report['identity_verification'] == 'external_pending'
    assert report['full_report_recall'] is None
    gold = rows(output/'content_consensus.jsonl')[0]
    duplicates = [o for o in gold['occurrences'] if o['location']['text'] == '100元']
    assert len(duplicates) == 2 and duplicates[0]['location']['start'] != duplicates[1]['location']['start']
    originals = json.loads((output/'original_reviews.json').read_text(encoding='utf-8'))
    assert len(originals['submissions']) == 3 and all(len(items[0]['occurrences']) == 7 for items in originals['submissions'])
    with pytest.raises(ValueError, match='overwrite'):
        review.merge(batch['out']/'tasks.json', signed, batch['corpus'], output)


def test_missing_original_mark_is_in_agreement_denominator_and_not_agent_adjudicated(batch):
    signed = [batch['signed']('one'), batch['signed']('two'),
              batch['signed']('three', lambda entry: entry['occurrences'].pop(1))]
    output = batch['root']/'disputed'
    report = review.merge(batch['out']/'tasks.json', signed, batch['corpus'], output)
    assert report['classification_agreement']['items'] == 7
    assert report['discovery_agreement']['raw_agreement'] < 1
    assert report['disputed_occurrences'][0]['original_ratings'][-1] == 'not_marked'
    assert report['status'] == 'pending_human_dispute_resolution'
    assert not (output/'content_consensus.jsonl').exists()
    assert not report['professional_acceptance']


def test_original_signature_tampering_duplicate_reviewers_and_unreadable_pages_remain_pending(batch):
    one, two, three = [batch['signed'](actor) for actor in ('one', 'two', 'three')]
    with pytest.raises(ValueError, match='distinct'):
        review.merge(batch['out']/'tasks.json', [one, two, two], batch['corpus'], batch['root']/'same-human')
    changed = rows(three)
    changed[0]['occurrences'][0]['notes'] = 'Edited after signature.'
    three.write_text('\n'.join(json.dumps(r) for r in changed), encoding='utf-8')
    with pytest.raises(ValueError, match='signed content changed'):
        review.merge(batch['out']/'tasks.json', [one, two, three], batch['corpus'], batch['root']/'edited')
    other = batch['signed']('four', lambda entry: entry['page_reviews'][0].update(status='unreadable'))
    report = review.merge(batch['out']/'tasks.json', [one, two, other], batch['corpus'], batch['root']/'unreadable')
    assert report['status'] == 'pending_unreadable_pages' and report['unreadable_pages']
    assert not (batch['root']/'unreadable'/'content_consensus.jsonl').exists()


def test_single_category_or_empty_original_ratings_do_not_fake_kappa_or_gold(batch):
    def only_financial(entry):
        entry['occurrences'] = [o for o in entry['occurrences'] if o['category'] == 'financial_in_scope']
    paths = [batch['signed'](actor, only_financial) for actor in ('only-one', 'only-two', 'only-three')]
    report = review.merge(batch['out']/'tasks.json', paths, batch['corpus'], batch['root']/'single-category')
    assert report['status'] == 'pending_non_estimable_agreement'
    assert report['classification_agreement']['kappa'] is None
    assert not (batch['root']/'single-category'/'content_consensus.jsonl').exists()
    paths = [batch['signed'](actor, lambda entry: entry.update(occurrences=[])) for actor in ('empty-one', 'empty-two', 'empty-three')]
    report = review.merge(batch['out']/'tasks.json', paths, batch['corpus'], batch['root']/'empty')
    assert report['classification_agreement']['status'] == 'no_original_items'
    assert not (batch['root']/'empty'/'content_consensus.jsonl').exists()


@pytest.mark.parametrize('field,value', [('period', 2024), ('set', ['P']), ('scope_declaration', [])])
def test_malformed_task_dimensions_are_explicit_validation_errors(batch, field, value):
    with pytest.raises(ValueError):
        review._task(dict(batch['task'], **{field: value}), batch['corpus'])


@pytest.mark.parametrize('target', ['plan', 'package', 'page', 'occurrence'])
def test_malformed_review_structures_fail_closed_without_type_errors(batch, target):
    if target == 'plan':
        path = batch['root']/'malformed-plan.json'
        write(path, ['invalid'])
        with pytest.raises(ValueError):
            review.prepare(path, batch['corpus'], batch['root']/'invalid-plan')
    elif target == 'package':
        path = batch['root']/'malformed-package.json'
        write(path, ['invalid'])
        with pytest.raises(ValueError):
            review.load_package(path, batch['corpus'])
    else:
        entry = batch['submission']('malformed')[0]
        if target == 'page':
            entry['page_reviews'][0]['page_id'] = []
        else:
            entry['occurrences'][0]['category'] = []
        with pytest.raises(ValueError):
            review.validate_review(entry, batch['package']['tasks'][0], batch['package'], batch['index'], require_signature=False)


def test_pdf_geometry_is_original_and_reviewed_scan_page_cannot_fake_complete_gold(batch):
    fitz = pytest.importorskip('fitz')
    draft = batch['corpus']/'mixed-original.pdf'
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), '2024 REVENUE 100 CNY')
        page = document.new_page()
        raster = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 16, 16), False)
        raster.clear_with(127)
        page.insert_image(fitz.Rect(72, 72, 120, 120), stream=raster.tobytes('png'))
        document.save(draft)
    task = dict(batch['task'], draft={'path': draft.name, 'sha256': sha256_file(draft)})
    plan = batch['root']/'pdf-plan.json'
    write(plan, {'schema_version': review.PLAN_SCHEMA, 'tasks': [task]})
    out = batch['root']/'pdf-blind'
    prepared = review.prepare(plan, batch['corpus'], out)
    package, indexes = review.load_package(out/'tasks.json', batch['corpus'])
    index = indexes[task['id']]
    assert prepared['pages'] == 2 and index['pages'][0]['text_layer']
    assert not index['pages'][1]['text_layer']
    block = index['blocks'][0]
    assert block['bbox'] and index['text'][block['start']:block['end']] == block['text']
    assert block['page_id'] == index['pages'][0]['id']
    assert 'table_candidate' not in block
    submitted = []
    for actor in ('pdf-one', 'pdf-two', 'pdf-three'):
        entry = rows(out/'reviewer-1.jsonl')[0]
        entry['reviewer'] = batch['submission'](actor)[0]['reviewer']
        entry['page_reviews'] = [dict(row, status='reviewed', notes='Synthetic inspection including raster page.')
                                 for row in entry['page_reviews']]
        entry['completeness_attested'] = True
        entry['occurrences'] = []
        for number, category in [('2024', 'non_financial_numeric'), ('100', 'financial_in_scope')]:
            start = index['text'].index(number)
            financial = category == 'financial_in_scope'
            entry['occurrences'].append({'location': {'page_id': block['page_id'], 'block_id': block['id'],
                'start': start, 'end': start+len(number), 'text': number}, 'category': category, 'form': 'prose',
                'binding': dict(company='fixture', account='REVENUE', period='2024Y', scope='', relation='value', base_period='') if financial else None,
                'unit': '元' if financial else None, 'currency': 'CNY' if financial else None,
                'scope_reason': None, 'notes': 'Synthetic original PDF character-span test.'})
        path = batch['root']/(actor+'.jsonl')
        signed = batch['root']/(actor+'-signed.jsonl')
        write(path, [entry])
        review.sign(out/'tasks.json', path, batch['corpus'], signed)
        submitted.append(signed)
    report = review.merge(out/'tasks.json', submitted, batch['corpus'], batch['root']/'pdf-merged')
    assert report['classification_agreement']['kappa'] == 1
    assert report['status'] == 'pending_unindexed_source_pages'
    assert report['unindexed_source_pages'] == [{'task_id': task['id'], 'page_id': index['pages'][1]['id'],
                                               'page': 2, 'reason': 'no_original_text_layer'}]
    assert not (batch['root']/'pdf-merged'/'content_consensus.jsonl').exists()
    assert report['full_report_recall'] is None and not report['professional_acceptance']
