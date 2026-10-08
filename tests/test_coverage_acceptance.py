"""Development measurements cannot turn missing work into acceptance."""
from copy import deepcopy
from decimal import Decimal
import json

import pytest

from mingjing.coverage_stats import PRIMARY_SCOPE, attach_coverage_statistics
from scripts.financial_coverage_acceptance import evaluate
from scripts.financial_coverage_acceptance import numeric_source_closure, source_occurrences_match
from scripts.financial_coverage_acceptance import operand_source_reconstruction
from test_coverage_statistics import population, record


def payload():
    coverage, claims, results = population(record('a', 'verified'))
    coverage['mentions'][0].update(start=0, end=3, text='100')
    results[0].update(expected='100', formula='disclosed value', operands=[{'fact_id': 'f'}], evidence=['ev'])
    return attach_coverage_statistics(dict(coverage=coverage, claims=claims, results=results,
                                          partial=False, draft_text='100'))


def test_shape_only_results_cannot_claim_development_professional_or_release_pass():
    measured = evaluate(payload())
    assert measured['status'] == 'needs_improvement'
    assert 'numeric_evidence_present' in measured['failed_checks']
    assert 'full_source_occurrences_present' in measured['failed_checks']
    assert measured['professional_acceptance'] is False
    assert measured['release_acceptance'] is False


def test_empty_or_incomplete_numeric_sources_do_not_pass():
    data = payload()
    data['results'][0]['evidence'] = []
    assert 'numeric_evidence_present' in evaluate(data)['failed_checks']
    data = payload()
    data['partial'] = True
    assert 'task_complete' in evaluate(data)['failed_checks']


def test_user_scope_assumption_cannot_satisfy_original_report_acceptance():
    data = payload()
    data['results'][0]['source_dimensions'] = {'scope_provenance': 'user_supplied_scope'}
    measured = evaluate(data)
    assert measured['checks']['original_scope_supported'] is False
    assert 'original_scope_supported' in measured['failed_checks']


def test_old_report_without_38_restored_occurrences_cannot_pass_new_recall_check():
    data = payload()
    old = deepcopy(data)
    old['coverage']['mentions'][0].update(status='rule_excluded', claim_id=None)
    measured = evaluate(data, old)
    assert measured['checks']['original_occurrences_preserved']
    assert measured['restored_financial_occurrences'] == 1
    assert 'previously_missed_financial_cells_restored' in measured['failed_checks']
    data['draft_text'] += 'changed'
    assert 'original_occurrences_preserved' in evaluate(data, old)['failed_checks']


def test_omitted_ledger_work_and_wrong_recorded_stats_fail_measurement():
    data = payload()
    data['coverage']['mentions'][0]['status'] = 'pending'
    measured = evaluate(data)
    assert 'recorded_statistics_match' in measured['failed_checks']
    assert 'no_pending_primary_items' in measured['failed_checks']


def test_deleting_or_reclassifying_ledger_rows_is_a_failed_integrity_receipt():
    data = payload()
    data['coverage']['mentions'].clear()
    assert evaluate(data)['failed_checks'] == ['ledger_integrity']
    data = payload()
    data['coverage']['mentions'][0]['evaluation_scope'] = 'industry_historical'
    assert evaluate(data)['failed_checks'] == ['ledger_integrity']


def test_missing_frozen_run_produces_failed_receipt_instead_of_crashing(tmp_path):
    measured = evaluate(payload(), run_root=tmp_path)
    assert measured['status'] == 'needs_improvement'
    assert not measured['checks']['frozen_numeric_replay']
    assert not measured['checks']['full_source_occurrences_present']


def test_acceptance_rebuild_accepts_serialized_draft_path(tmp_path):
    data = payload()
    draft = tmp_path / 'draft.md'
    draft.write_text(data['draft_text'], encoding='utf-8')
    data['draft'] = str(draft)
    measured = evaluate(data, run_root=tmp_path)
    assert measured['status'] == 'needs_improvement'
    assert measured['checks']['full_source_occurrences_present'] is False


def test_source_reconstruction_rejects_joint_reclassification_and_false_exclusion():
    source = [dict(start=0, end=3, text='100', status='pending', evaluation_scope=PRIMARY_SCOPE,
                   classification_basis={'rule': 'issuer_financial'})]
    saved = deepcopy(source)
    saved[0]['status'] = 'judged'
    assert source_occurrences_match(saved, source)
    saved[0]['evaluation_scope'] = 'industry_historical'
    assert not source_occurrences_match(saved, source)
    saved = deepcopy(source)
    saved[0].update(status='rule_excluded', reason='pretend_noise')
    assert not source_occurrences_match(saved, source)


def test_pdf_page_and_image_must_reconstruct_the_actual_original_source(tmp_path):
    fitz = pytest.importorskip('fitz')
    from mingjing.evidence import sha256_file, make_evidence_id
    from mingjing.models import Fact
    from mingjing.artifacts import export_evidence
    source = tmp_path/'report.pdf'
    with fitz.open() as document:
        document.new_page().insert_text((70,80), 'Revenue 100.00')
        document.save(source)
    with fitz.open(source) as document:
        block = document[0].get_text('blocks')[0]
    sha = sha256_file(source)
    identifier = make_evidence_id(sha, 1, block[4].strip(), block[:4])
    fact = Fact('f', 'Issuer', 'REVENUE', '2023Y', source_path=str(source), evidence=(identifier,))
    row = dict(expected='100', formula='disclosed value', rule_ids=['E1-value'],
               operands=[fact.to_dict()], evidence=[identifier])
    root = tmp_path/'run'; root.mkdir()
    resources = export_evidence([fact], [row], root)
    image = root/resources[identifier]['image']
    def freeze():
        manifest = dict(inputs=[dict(path=str(source), sha256=sha)], outputs=[dict(
            path=image.relative_to(root).as_posix(), sha256=sha256_file(image), bytes=image.stat().st_size)])
        (root/'run_manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    freeze()
    data = dict(facts=[fact.to_dict()], evidence_resources=resources)
    assert numeric_source_closure(data, [row], root)
    with fitz.open(source) as document:
        rectangle = document[0].rect * fitz.Matrix(1.2, 1.2)
    white = fitz.Pixmap(fitz.csRGB, rectangle.irect, False)
    white.clear_with(255); white.save(image)
    freeze()
    assert not numeric_source_closure(data, [row], root)
    false_id = identifier.replace(':p1:', ':p999:')
    row['evidence'] = [false_id]; row['operands'][0]['evidence'] = [false_id]
    data['facts'][0]['evidence'] = [false_id]
    resources[false_id] = {**resources[identifier], 'page': 999}
    assert not numeric_source_closure(data, [row], root)


def test_genuine_page_reference_does_not_authorize_a_forged_cached_amount(tmp_path):
    from mingjing.parser import extract_facts
    from mingjing.evidence import sha256_file
    fitz = pytest.importorskip('fitz')
    source = tmp_path / '2024-04-15_2023年年度报告.pdf'
    with fitz.open() as document:
        page = document.new_page()
        for i, text in enumerate(['甲公司2023年年度报告', '合并利润表', '单位：元',
                                 '2023年 2022年', '营业收入 100.00 80.00']):
            page.insert_text((50, 70 + i * 45), text, fontname='china-s', fontsize=12)
        document.save(source)
    facts = extract_facts(source, '甲公司', '', '2024-04-15')
    fact = next(fact for fact in facts if fact.account == 'REVENUE' and fact.period == '2023Y').to_dict()
    root = tmp_path / 'run'
    root.mkdir()
    (root / 'run_manifest.json').write_text(json.dumps({'inputs': [dict(
        path=str(source.resolve()), sha256=sha256_file(source))]}), encoding='utf-8')
    data = {'facts': [fact]}
    conclusion = {'operands': [deepcopy(fact)]}
    assert operand_source_reconstruction(data, [conclusion], root)
    fact['value'] = '999999'
    conclusion['operands'][0]['value'] = '999999'
    assert not operand_source_reconstruction(data, [conclusion], root)


@pytest.fixture
def complete_pdf_source_closure(tmp_path):
    """A header, a physical line, a financial cell and a scope footnote."""
    fitz = pytest.importorskip('fitz')
    from mingjing.artifacts import export_evidence
    from mingjing.evidence import make_evidence_id, sha256_file
    from mingjing.models import Fact
    source = tmp_path / 'complete_original.pdf'
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((60, 70), 'Customer revenue table\nReporting period 2023')
        page.insert_text((60, 145), 'Customer Azul')
        page.insert_text((310, 145), '12.34')
        document.new_page().insert_text((60, 90), 'Consolidated scope original footnote')
        document.save(source)
    sha = sha256_file(source)
    with fitz.open(source) as document:
        header = document[0].get_text('blocks')[0]
        lines = [line for block in document[0].get_text('dict')['blocks'] if block['type'] == 0
                 for line in block['lines']]
        line = next(line for line in lines if ''.join(span['text'] for span in line['spans']) ==
                    'Reporting period 2023')
        cell = next(span for row in lines for span in row['spans'] if span['text'] == '12.34')
        scope = document[1].get_text('blocks')[0]
    ids = {'header': make_evidence_id(sha, 1, header[4].strip(), header[:4]),
           'line': make_evidence_id(sha, 1, 'Reporting period 2023', line['bbox']),
           'cell': make_evidence_id(sha, 1, cell['text'], cell['bbox']) + ':r2c3',
           'scope': make_evidence_id(sha, 2, scope[4].strip(), scope[:4])}
    assert ids['header'] != ids['line']
    fact = Fact('real-customer-cell', 'Invented issuer', 'REVENUE', '2023Y', value=Decimal('12.34'),
                source_path=str(source), evidence=(ids['cell'],),
                source_table_evidence=(ids['header'], ids['line']), source_scope_evidence=(ids['scope'],))
    row = {'expected': '12.34', 'formula': 'disclosed value', 'rule_ids': ['E1-value'],
           'operands': [fact.to_dict()], 'evidence': list(ids.values())}
    root = tmp_path / 'run'
    resources = export_evidence([fact], [row], root, source_resources=[{'path': str(source), 'sha256': sha}])
    manifest = {'inputs': [{'path': str(source), 'sha256': sha}], 'outputs': []}
    for resource in resources.values():
        image = root / resource['image']
        manifest['outputs'].append({'path': image.relative_to(root).as_posix(),
                                   'sha256': sha256_file(image), 'bytes': image.stat().st_size})
    (root / 'run_manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return {'facts': [fact.to_dict()], 'evidence_resources': resources}, row, root, ids


@pytest.mark.parametrize('role', ['header', 'line', 'cell', 'scope'])
def test_numeric_source_closure_accepts_each_original_evidence_role(complete_pdf_source_closure, role):
    data, row, root, ids = complete_pdf_source_closure
    original = deepcopy(data)
    row['evidence'] = [ids[role]]
    assert numeric_source_closure(data, [row], root)
    # Headers and source definitions stay provenance, not amount-bearing facts.
    assert data == original and len(data['facts']) == 1


@pytest.mark.parametrize('role', ['header', 'line', 'cell', 'scope'])
@pytest.mark.parametrize('fault', ['span_hash', 'coordinates', 'page', 'quote', 'image_hash'])
def test_numeric_source_closure_rejects_nearby_forged_provenance(complete_pdf_source_closure, role, fault):
    data, row, root, ids = complete_pdf_source_closure
    identifier = ids[role]
    row['evidence'] = [identifier]
    resource = data['evidence_resources'][identifier]
    if fault == 'quote':
        resource['quote_text'] += ' invented'
    elif fault == 'image_hash':
        resource['image_sha256'] = 'f' * 64
    else:
        parts = identifier.split(':')
        if fault == 'span_hash':
            parts[3] = '0' * 8 if parts[3] != '0' * 8 else 'f' * 8
        elif fault == 'coordinates':
            parts[2] = '1,1,10,10'
        else:
            parts[1] = 'p999'
            resource['page'] = 999
        false_id = ':'.join(parts)
        for fact in (data['facts'][0], row['operands'][0]):
            for field in ('evidence', 'source_table_evidence', 'source_scope_evidence'):
                fact[field] = [false_id if value == identifier else value for value in fact[field]]
        data['evidence_resources'][false_id] = resource
        row['evidence'] = [false_id]
    assert not numeric_source_closure(data, [row], root)


def test_numeric_source_closure_requires_provenance_authority_from_actual_operand(complete_pdf_source_closure):
    data, row, root, ids = complete_pdf_source_closure
    row['evidence'] = [ids['header']]
    for fact in (data['facts'][0], row['operands'][0]):
        fact['source_table_evidence'] = []
    assert not numeric_source_closure(data, [row], root)


def test_numeric_source_closure_cannot_redirect_an_operand_to_a_second_registered_path(complete_pdf_source_closure):
    from mingjing.evidence import sha256_file
    data, row, root, ids = complete_pdf_source_closure
    row['evidence'] = [ids['scope']]
    original = data['facts'][0]['source_path']
    from pathlib import Path
    alternate = Path(original).with_name('different_registered_source.pdf')
    alternate.write_bytes(Path(original).read_bytes())
    manifest_path = root / 'run_manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['inputs'].append({'path': str(alternate), 'sha256': sha256_file(alternate)})
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    data['evidence_resources'][ids['scope']]['source_path'] = str(alternate)
    assert not numeric_source_closure(data, [row], root)
