"""Source-gap page viewing must not manufacture facts or trust caller paths."""
from copy import deepcopy
import hashlib
from pathlib import Path

import pytest

from mingjing.evidence import make_evidence_id, sha256_file
from mingjing.source_gap_evidence import export_source_gap_evidence

fitz = pytest.importorskip('fitz')


def source(tmp_path):
    pdf = tmp_path/'registered.pdf'
    document = fitz.open()
    page = document.new_page()
    page.insert_text((70,80), 'Customer revenue 100.00; issuer scope unresolved')
    page.insert_text((70,150), 'Total revenue 1000.00')
    document.new_page().insert_text((70,80), 'Continued source customer footnote')
    document.save(pdf)
    document.close()
    sha = sha256_file(pdf)
    with fitz.open(pdf) as document:
        blocks = [(index+1, block) for index,page in enumerate(document) for block in page.get_text('blocks')]
    evidence = [make_evidence_id(sha, number, block[4].strip(), block[:4]) for number,block in blocks]
    report = {'id':'report-'+sha[:20], 'sha256':sha, 'path':str(pdf)}
    gaps = [{'id':'sg-'+'a'*24, 'doc_sha256':sha, 'report_id':report['id'],
             'evidence':[evidence[0],evidence[2]], 'scope':'', 'eligible_for_verification':False,
             'source_path':str(tmp_path/'untrusted-path.pdf'), 'raw_value':'999999'}]
    return pdf, report, gaps, evidence


def test_registered_original_pages_render_with_source_only_metadata_and_inputs_unchanged(tmp_path):
    _,report,gaps,evidence = source(tmp_path)
    before = deepcopy((report,gaps))
    out = tmp_path/'run'
    result = export_source_gap_evidence(gaps,[report],out)
    assert set(result)=={evidence[0],evidence[2]}
    assert (report,gaps)==before
    for identifier,row in result.items():
        image = out/row['image']
        assert image.read_bytes().startswith(b'\x89PNG')
        assert sha256_file(image)==row['image_sha256']
        assert row['document_sha256']==report['sha256']
        assert row['source_path']==report['path']
        assert row['reference_status']=='source_definition_unresolved'
        assert row['eligible_as_fact'] is False and row['authorizes_judgement'] is False
        assert row['source_gap_ids']==[gaps[0]['id']]
        assert 'raw_value' not in row and 'fact_id' not in row
        assert make_evidence_id(row['document_sha256'],row['page'],row['quote_text'],row['bbox'])==identifier


def test_shared_original_block_is_rendered_once_with_all_gap_links(tmp_path):
    _,report,gaps,evidence = source(tmp_path)
    second = dict(gaps[0],id='sg-'+'b'*24,evidence=[evidence[0]])
    result = export_source_gap_evidence([*gaps,second],{report['id']:report},tmp_path/'run')
    assert result[evidence[0]]['source_gap_ids']==[gaps[0]['id'],second['id']]
    assert len(list((tmp_path/'run/evidence').glob('*.png')))==2


def test_unreferenced_text_reports_are_allowed_but_identity_key_conflicts_are_not(tmp_path):
    _,report,gaps,evidence = source(tmp_path)
    unused = {'id':'report-text','sha256':'d'*64,'path':str(tmp_path/'other.md')}
    result = export_source_gap_evidence(gaps,[report,unused],tmp_path/'run')
    assert evidence[0] in result
    with pytest.raises(ValueError,match='registry keys'):
        export_source_gap_evidence(gaps,{'report-conflicting-key':report},tmp_path/'other-run')


@pytest.mark.parametrize('mutation', ['unknown-report','other-document','invented-block','invalid-page','invalid-box',
                                      'empty-evidence','promoted-fact','path-from-gap'])
def test_original_report_and_span_guards_fail_explicitly(tmp_path,mutation):
    pdf,report,gaps,evidence = source(tmp_path)
    if mutation=='unknown-report':
        gaps[0]['report_id']='report-outside'
    elif mutation=='other-document':
        gaps[0]['doc_sha256']='c'*64
    elif mutation=='invented-block':
        gaps[0]['evidence']=[evidence[0][:-8]+'01234567']
    elif mutation=='invalid-page':
        gaps[0]['evidence']=[evidence[0].replace(':p1:',':p999:')]
    elif mutation=='invalid-box':
        gaps[0]['evidence']=[report['sha256'][:12]+':p1:0,0,99999,99999:'+evidence[0][-8:]]
    elif mutation=='empty-evidence':
        gaps[0]['evidence']=[]
    elif mutation=='promoted-fact':
        gaps[0]['eligible_as_fact']=True
    elif mutation=='path-from-gap':
        report['path']=str(tmp_path/'missing.pdf')
        gaps[0]['source_path']=str(pdf)
    with pytest.raises((ValueError,OSError)):
        export_source_gap_evidence(gaps,[report],tmp_path/'run')
    assert not list((tmp_path/'run').rglob('*.png'))


def test_changed_report_bytes_and_ambiguous_source_identities_are_rejected(tmp_path):
    pdf,report,gaps,_ = source(tmp_path)
    pdf.write_bytes(pdf.read_bytes()+b'\nchanged')
    with pytest.raises(ValueError,match='changed'):
        export_source_gap_evidence(gaps,[report],tmp_path/'run')
    other = dict(report,id='report-alias')
    gaps[0].pop('report_id')
    with pytest.raises(ValueError,match='exactly one'):
        export_source_gap_evidence(gaps,[report,other],tmp_path/'run')


def test_change_during_render_is_detected_before_any_image_is_published(tmp_path,monkeypatch):
    _,report,gaps,_ = source(tmp_path)
    monkeypatch.setattr('mingjing.source_gap_evidence.sha256_file',lambda path:'0'*64)
    with pytest.raises(ValueError,match='during'):
        export_source_gap_evidence(gaps,[report],tmp_path/'run')
    assert not list((tmp_path/'run').rglob('*.png'))


def test_symlinked_output_or_source_never_escapes_registered_roots(tmp_path):
    pdf,report,gaps,_ = source(tmp_path)
    target = tmp_path/'target'
    target.mkdir()
    link = tmp_path/'run'
    try:
        link.symlink_to(target,target_is_directory=True)
    except OSError:
        pytest.skip('Host does not grant symbolic-link creation')
    with pytest.raises(ValueError,match='links|junctions'):
        export_source_gap_evidence(gaps,[report],link)
    assert not list(target.rglob('*.png'))
    source_link = tmp_path/'source-link.pdf'
    source_link.symlink_to(pdf)
    with pytest.raises(ValueError,match='links|junctions'):
        export_source_gap_evidence(gaps,[dict(report,path=str(source_link))],tmp_path/'real-run')


def test_preexisting_target_image_link_is_rejected_before_write(tmp_path):
    _,report,gaps,evidence = source(tmp_path)
    root = tmp_path/'run/evidence'
    root.mkdir(parents=True)
    victim = tmp_path/'unrelated.txt'
    victim.write_text('unchanged')
    name = hashlib.sha256(evidence[0].encode()).hexdigest()[:20]+'.png'
    try:
        (root/name).symlink_to(victim)
    except OSError:
        pytest.skip('Host does not grant symbolic-link creation')
    with pytest.raises(ValueError,match='links|junctions'):
        export_source_gap_evidence(gaps,[report],tmp_path/'run')
    assert victim.read_text()=='unchanged'
def test_same_page_quote_does_not_change_existing_fact_authority():
    from mingjing.source_gap_evidence import merge_source_gap_evidence
    known = {'eid': {'document_sha256': 'a'*64, 'source_path': 'report.pdf',
                     'page': 1, 'image': 'evidence/page.png', 'authorizes_judgement': True}}
    quote = {'eid': {'document_sha256': 'a'*64, 'source_path': 'report.pdf', 'page': 1,
                     'source_gap_ids': ['sg-'+'b'*24], 'authorizes_judgement': False,
                     'reference_status': 'source_definition_unresolved'}}
    merge_source_gap_evidence(known, quote)
    assert known['eid']['authorizes_judgement'] is True
    assert 'reference_status' not in known['eid']
    assert known['eid']['unresolved_source_gap_ids'] == ['sg-'+'b'*24]
    quote['eid']['page'] = 2
    with pytest.raises(ValueError, match='conflicts'):
        merge_source_gap_evidence(known, quote)


def test_web_quote_response_preserves_unresolved_status_and_has_no_candidate_value():
    from mingjing.server import _source_resource_info
    resource = {'source_path': 'report.pdf', 'document_sha256': 'a'*64,
                'reference_status': 'source_definition_unresolved', 'quote_text': 'Original table',
                'quote_sha256': 'b'*64, 'report_id': 'report-locked', 'raw_value': '999999',
                'eligible_as_fact': True, 'authorizes_judgement': True}
    response = _source_resource_info(resource, 'a'*12+':p1:10,10,50,50:deadbeef')
    assert response['quote_text'] == 'Original table'
    assert response['reference_status'] == 'source_definition_unresolved'
    assert not response['authorizes_judgement'] and not response['eligible_as_fact']
    assert 'raw_value' not in response
