"""Value, source-table and scope references need equally real PDF previews."""
from copy import deepcopy
from dataclasses import replace
import hashlib
from pathlib import Path

import pytest

from mingjing.artifacts import export_evidence
from mingjing.evidence import fact_evidence_references, make_evidence_id, sha256_file
from mingjing.models import Fact
from mingjing.server import _evidence_info, _source_resource_info
from scripts.source_acceptance_inputs import page_evidence_diagnostic


@pytest.fixture
def original_pdf(tmp_path):
    fitz = pytest.importorskip('fitz')
    pdf = tmp_path/'original.pdf'
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((60, 70), 'Customer table header Revenue Share')
        page.insert_text((60, 130), 'Customer Azul')
        page.insert_text((310, 130), '12.34')
        page.insert_text((440, 130), '5.67%')
        doc.new_page().insert_text((60, 90), 'Consolidated scope original footnote')
        doc.save(pdf)
    sha = sha256_file(pdf)
    with fitz.open(pdf) as doc:
        header = doc[0].get_text('blocks')[0]
        scope = doc[1].get_text('blocks')[0]
        cell = next(span for block in doc[0].get_text('dict')['blocks'] if block['type'] == 0
                    for line in block['lines'] for span in line['spans'] if span['text'] == '12.34')
    value_id = make_evidence_id(sha, 1, cell['text'], cell['bbox'])
    header_id = make_evidence_id(sha, 1, header[4].strip(), header[:4])
    scope_id = make_evidence_id(sha, 2, scope[4].strip(), scope[:4])
    fact = Fact('fixture-financial-cell', 'Invented issuer', 'REVENUE', '2023Y', source_doc=sha[:12],
                source_path=str(pdf), evidence=(value_id,), source_table_evidence=(header_id,),
                source_scope_evidence=(scope_id,))
    resource = {'id': 'report-'+sha[:20], 'path': str(pdf), 'sha256': sha}
    return pdf, fact, resource, [value_id, header_id, scope_id]


def test_value_cell_table_header_scope_and_suffix_render_without_changing_facts(original_pdf, tmp_path):
    pdf, fact, resource, ids = original_pdf
    original = deepcopy((fact.to_dict(), resource))
    cell_suffix = ids[0]+':r2c3'
    with_suffix = replace(fact, evidence=(*fact.evidence, cell_suffix))
    out = tmp_path/'new-run'
    rows = [{'evidence': [*ids, cell_suffix]}]
    rendered = export_evidence([with_suffix], rows, out, source_resources=[resource])
    assert set(rendered) == set(rows[0]['evidence'])
    assert (fact.to_dict(), resource) == original
    assert rendered[ids[0]]['reference_kinds'] == ['fact_value']
    assert rendered[ids[1]]['reference_kinds'] == ['source_table']
    assert rendered[ids[2]]['reference_kinds'] == ['source_scope']
    for identifier, result in rendered.items():
        image = out/result['image']
        assert image.read_bytes().startswith(b'\x89PNG')
        assert sha256_file(image) == result['image_sha256']
        assert result['document_sha256'] == resource['sha256']
        assert result['source_path'] == str(pdf)
        assert result['quote_text'] and not any(key in result for key in ('value', 'candidate_value', 'verdict'))
    diagnosis = page_evidence_diagnostic(out, rows, rendered)
    assert diagnosis['all_available'] and diagnosis['image_integrity_known']
    assert diagnosis['required_unique_evidence'] == 4


@pytest.mark.parametrize('mutation', ['invented-hash', 'wrong-coordinate', 'wrong-document', 'wrong-page'])
def test_registered_source_header_cannot_render_a_plausible_unrelated_rectangle(original_pdf, tmp_path, mutation):
    _, fact, resource, ids = original_pdf
    identifier = ids[1]
    if mutation == 'invented-hash':
        identifier = identifier[:-8]+'0'*8
    elif mutation == 'wrong-coordinate':
        parts = identifier.split(':')
        parts[2] = '1,1,10,10'
        identifier = ':'.join(parts)
    elif mutation == 'wrong-document':
        identifier = 'f'*12+identifier[12:]
    else:
        identifier = identifier.replace(':p1:', ':p999:')
    corrupted = replace(fact, source_table_evidence=(identifier,))
    out = tmp_path/'new-run'
    with pytest.raises(ValueError, match='Evidence'):
        export_evidence([corrupted], [{'evidence': [identifier]}], out, source_resources=[resource])
    assert not list(out.rglob('*.png'))


def test_complete_source_hash_and_registry_membership_are_enforced(original_pdf, tmp_path):
    _, fact, resource, ids = original_pdf
    altered = dict(resource, sha256=resource['sha256'][:12]+'0'*52)
    with pytest.raises(ValueError, match='SHA-256'):
        export_evidence([fact], [{'evidence': ids}], tmp_path/'hash-mismatch', source_resources=[altered])
    with pytest.raises(ValueError, match='outside the frozen'):
        export_evidence([fact], [{'evidence': ids}], tmp_path/'unregistered', source_resources=[])


def test_rendering_uses_original_byte_snapshot_and_refuses_a_later_path_change(original_pdf, tmp_path, monkeypatch):
    fitz = pytest.importorskip('fitz')
    pdf, fact, resource, ids = original_pdf
    original_open = fitz.open
    observed = []
    def replace_after_snapshot(*args, **kwargs):
        pdf.write_bytes(pdf.read_bytes()+b'\nchanged-after-snapshot')
        document = original_open(*args, **kwargs)
        observed.append(document[0].get_text('text'))
        return document
    monkeypatch.setattr(fitz, 'open', replace_after_snapshot)
    out = tmp_path/'new-run'
    with pytest.raises(ValueError, match='changed during'):
        export_evidence([fact], [{'evidence': ids}], out, source_resources=[resource])
    assert observed and 'Customer Azul' in observed[0]
    assert not list(out.rglob('*.png'))


def test_legacy_text_references_keep_non_pdf_capability(tmp_path):
    fact = Fact('text-fact', 'Issuer', 'REVENUE', '2023Y', evidence=('text-source-reference',))
    assert export_evidence([fact], [{'evidence': list(fact.evidence)}], tmp_path/'new-run') == {}


def test_web_metadata_recognizes_header_and_scope_without_promoting_them_to_values(original_pdf):
    _, fact, resource, ids = original_pdf
    payload = fact.to_dict()
    before = deepcopy(payload)
    assert fact_evidence_references(payload) == fact_evidence_references(fact)
    assert _evidence_info(payload, ids[1])['reference_kinds'] == ['source_table']
    assert _evidence_info(payload, ids[2])['reference_kinds'] == ['source_scope']
    view = _source_resource_info({'source_path': fact.source_path,
        'document_sha256': resource['sha256'], 'reference_kinds': ['source_scope'],
        'quote_text': 'Original source footnote'}, ids[2])
    assert view['reference_kinds'] == ['source_scope'] and view['document_sha256'] == resource['sha256']
    assert view['quote_text'] == 'Original source footnote'
    assert payload == before


def test_receipt_lists_missing_header_reference_and_bad_image_hash_instead_of_opaque_failure(original_pdf, tmp_path):
    _, fact, resource, ids = original_pdf
    out = tmp_path/'new-run'
    rows = [{'evidence': [*ids, ids[1]]}]
    resources = export_evidence([fact], rows, out, source_resources=[resource])
    missing = dict(resources)
    missing.pop(ids[1])
    diagnostic = page_evidence_diagnostic(out, rows, missing)
    assert diagnostic['required_references'] == 4 and diagnostic['required_unique_evidence'] == 3
    assert diagnostic['failures'] == [{'evidence_id': ids[1], 'reason': 'resource_not_registered'}]
    assert not diagnostic['all_available']
    image = out/resources[ids[2]]['image']
    image.write_bytes(image.read_bytes()+b'\nmodified')
    diagnostic = page_evidence_diagnostic(out, rows, resources)
    assert diagnostic['failures'] == [{'evidence_id': ids[2], 'reason': 'image_hash_mismatch'}]
    assert not diagnostic['all_available']


def test_evidence_symlink_cannot_change_the_original_source_root(original_pdf, tmp_path):
    pdf, fact, resource, ids = original_pdf
    link = tmp_path/'alias.pdf'
    try:
        link.symlink_to(pdf)
    except OSError:
        pytest.skip('Host does not permit symlink creation')
    fact = replace(fact, source_path=str(link))
    with pytest.raises(ValueError, match='links|junctions'):
        export_evidence([fact], [{'evidence': ids}], tmp_path/'new-run', source_resources=[resource])
