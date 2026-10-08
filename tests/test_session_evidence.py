"""Real local HTTP and PDF evidence, without any model/API call."""
import copy
import hashlib
import json
from pathlib import Path
import threading
from urllib.parse import urlencode, quote
import urllib.error
import urllib.request

import pytest

from mingjing import server
from mingjing.cli import build_parser, command_check
from mingjing.coverage import partition_document
from mingjing.evidence import make_evidence_id, sha256_file
from mingjing.facts import fact_from_dict
from mingjing.financial_tools import FinancialDispatcher
from mingjing.session_evidence import persist_source_references, SessionEvidenceError


@pytest.fixture
def citation(tmp_path, monkeypatch):
    fitz = pytest.importorskip('fitz')
    monkeypatch.setenv('MINGJING_HOME', str(tmp_path / 'profile'))
    state = server.AppState()
    corpus = tmp_path / 'corpus'
    corpus.mkdir()
    pdf = corpus / 'annual.pdf'
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 100), 'Accounting policy: prior-period adjustment disclosed.')
        page.insert_text((72, 160), 'A second paragraph with supporting context.')
        document.save(pdf)
    (corpus / 'facts.json').write_text(json.dumps([{'fact_id': 'r', 'company': '公司甲',
        'account': 'REVENUE', 'period': '2024Y', 'scope': '合并', 'value': '100',
        'publish_date': '2025-03-01', 'source_path': pdf.name}]), encoding='utf-8')
    draft = tmp_path / 'draft.md'
    draft.write_text('公司合并口径2024年营业收入100元。', encoding='utf-8')
    run = server.RunTask('citation-fixture', {'draft': str(draft), 'corpus': str(corpus)}, state.runs_root, state.config)
    args = build_parser().parse_args(['check', '--draft', str(draft), '--corpus', str(corpus),
        '--company', '公司甲', '--report-date', '2025-03-31', '--out', str(run.out), '--run-id', run.id])
    assert command_check(args) == 0
    run.status = 'completed'
    state.tasks[run.id] = run
    created = state.sessions.create(run.id)
    session = state.sessions.get(created['session_id'])
    resource = next(row for row in session['locks']['resources'] if row['path'] == str(pdf.resolve()))
    _, _, blocks = partition_document(pdf)
    blocks = [{**row, 'id': resource['id'] + ':' + row['id'], 'report_id': resource['id']} for row in blocks]
    result = json.loads((run.out / 'results.json').read_text(encoding='utf-8'))
    dispatcher = FinancialDispatcher(run.id, session['resource_lock'], [],
        [fact_from_dict(row) for row in result['facts']], blocks,
        [{'report_id': resource['id'], 'name': pdf.name, 'companies': ['公司甲'], 'sha256': resource['sha256']}],
        '2025-03-31', task_company='公司甲', source_resources=session['locks']['resources'])
    dispatcher.register_session(session['session_id'], 'question')
    envelope = {'session_id': session['session_id'], 'role': 'question', 'run_id': run.id,
                'resource_lock': session['resource_lock']}
    located = dispatcher.dispatch({**envelope, 'call_id': 'source-search', 'tool_name': 'mj_search_blocks',
                                   'params': {'query': 'Accounting policy', 'limit': 1}})[0]
    submitted = dispatcher.dispatch({**envelope, 'call_id': 'submit-quotation', 'tool_name': 'mj_submit_report',
        'params': {'template': located['slot_template'], 'slots': located['slot_ids']}})
    assert submitted['accepted']
    selected = dispatcher.submitted_slots['question']
    references = [dispatcher.source_references[slot] for slot in selected]
    visible_keys = {'slot_id', 'report_id', 'block_id', 'page', 'evidence_id', 'reference_status'}
    assert located['source_references'] == [
        {key: reference[key] for key in visible_keys} for reference in references
    ]
    assert all(set(reference) == visible_keys and 'quote_text' not in reference
               for reference in located['source_references'])
    assert all(reference['quote_text'] and reference['quote_sha256'] and reference['block_sha256']
               for reference in references)
    reference = references[0]
    text = reference['quote_text']
    message_id = 'message-0001'
    folder = state.sessions.root / session['session_id'] / message_id
    generated = persist_source_references(session, folder, references)
    session['messages'].append({'role': 'assistant', 'text': text, 'message_id': message_id,
        'source_references': generated['references'], 'source_reference_manifest_sha256': generated['manifest_sha256']})
    state.sessions.save(session)
    frozen = {path.name: path.read_bytes() for path in run.out.iterdir() if path.is_file()}
    httpd = server.MingJingServer(('127.0.0.1', 0), state)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    base = f'http://127.0.0.1:{httpd.server_port}'

    def request(**changes):
        values = {'session': session['session_id'], 'message': message_id, 'run': run.id}
        evidence_id = changes.pop('evidence_id', reference['evidence_id'])
        values.update(changes)
        with urllib.request.urlopen(base + '/api/evidence/' + quote(evidence_id, safe='') + '?' + urlencode(values), timeout=5) as response:
            raw = response.read()
            return raw if values.get('format') == 'png' else json.loads(raw)

    yield {'state': state, 'session': session, 'reference': reference, 'folder': folder,
           'pdf': pdf, 'run': run, 'request': request, 'frozen': frozen, 'generated': generated}
    httpd.shutdown()
    httpd.server_close()
    worker.join(2)


def test_pdf_quotation_json_and_png_are_message_owned_and_original_run_stays_frozen(citation):
    c = citation
    info = c['request']()
    assert info['reference_status'] == 'unverified_source_quotation'
    assert '未作数字核查' in info['title'] and info['name'] == 'annual.pdf'
    assert info['quote_text'] == c['reference']['quote_text']
    assert info['company'] == '公司甲' and info['page'] == 1
    assert info['image_available'] and '_image_path' not in info and '_image_sha256' not in info
    assert c['request'](format='png').startswith(b'\x89PNG\r\n\x1a\n')
    assert list(c['folder'].glob('*.png'))
    assert all((c['run'].out / name).read_bytes() == raw for name, raw in c['frozen'].items())


@pytest.mark.parametrize('changes,status', [
    ({'message': '../message-0001'}, 400),
    ({'message': 'message-0002'}, 404),
    ({'session': 'session-' + 'f' * 24}, 404),
    ({'run': 'other-run'}, 403),
    ({'source_path': 'C:/private.pdf'}, 400),
    ({'bbox': '1,2,3,4'}, 400),
    ({'format': 'pdf'}, 400),
    ({'evidence_id': 'not-registered'}, 404),
])
def test_http_rejects_arbitrary_context_paths_and_unregistered_evidence(citation, changes, status):
    with pytest.raises(urllib.error.HTTPError) as error:
        citation['request'](**changes)
    assert error.value.code == status


def test_other_session_cannot_read_this_message_registry(citation):
    c = citation
    other = c['state'].sessions.create(c['run'].id)
    with pytest.raises(urllib.error.HTTPError) as error:
        c['request'](session=other['session_id'])
    assert error.value.code == 404


@pytest.mark.parametrize('field,value,status', [
    ('company', '公司乙', 403), ('report_id', 'report-foreign', 404),
    ('doc_sha256', '0' * 64, 409), ('quote_sha256', '0' * 64, 409),
    ('block_sha256', '0' * 64, 409), ('evidence_id', 'forged-id', 409),
    ('page', 99, 400), ('reference_status', 'verified', 400),
    ('bbox', [0, 0, 5, 5], 409),
])
def test_host_registry_rejects_foreign_company_forged_values_and_locations(citation, field, value, status):
    reference = copy.deepcopy(citation['reference'])
    reference[field] = value
    with pytest.raises(SessionEvidenceError) as error:
        persist_source_references(citation['session'], citation['folder'], [reference])
    assert error.value.status == status


@pytest.mark.parametrize('target', ['pdf', 'registry', 'image', 'results'])
def test_http_rechecks_source_and_cache_integrity_on_every_access(citation, target):
    c = citation
    path = {'pdf': c['pdf'], 'registry': c['folder'] / 'source_references.json',
            'image': next(c['folder'].glob('*.png')), 'results': c['run'].out / 'results.json'}[target]
    path.write_bytes(path.read_bytes() + b'\nmodified')
    with pytest.raises(urllib.error.HTTPError) as error:
        c['request'](format='png')
    assert error.value.code == 409


def test_quote_text_must_come_from_original_block_not_model_fabrication(citation):
    reference = copy.deepcopy(citation['reference'])
    reference['quote_text'] = 'Fabricated policy conclusion.'
    reference['quote_sha256'] = hashlib.sha256(reference['quote_text'].encode()).hexdigest()
    reference['evidence_id'] = make_evidence_id(reference['doc_sha256'], reference['page'], reference['quote_text'], reference['bbox'])
    with pytest.raises(SessionEvidenceError, match='不属于锁定原文块'):
        persist_source_references(citation['session'], citation['folder'], [reference])


def test_unregistered_or_escaping_output_folder_is_rejected(citation, tmp_path):
    for folder in (tmp_path / 'message-0001', citation['folder'].parent / 'arbitrary-name'):
        with pytest.raises(SessionEvidenceError):
            persist_source_references(citation['session'], folder, [citation['reference']])
