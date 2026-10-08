"""Real local PDF fact patches are additive and belong to a new task."""
import copy
import json
import threading
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from jsonschema.exceptions import ValidationError

from mingjing.audit import digest
from mingjing.coverage import partition_document
from mingjing.evidence import sha256_file
from mingjing.financial_tools import FinancialDispatcher, ToolDenied, tool_definitions
from mingjing.mentions import extract_mentions
from mingjing.models import Fact
from mingjing.reparse import create_reparse_packet, list_reparse_packets, load_reparse_packet, resolve_reparse_packet
from test_agent_guards import call


@pytest.fixture
def source(tmp_path):
    fitz = pytest.importorskip('fitz')
    folder = tmp_path / '600000_甲公司'
    folder.mkdir()
    pdf = folder / '2025-03-01_2024年年度报告.pdf'
    with fitz.open() as document:
        for number in range(1, 6):
            page = document.new_page()
            if number == 3:
                lines = ['甲公司2024年年度报告', '合并利润表', '金额单位：元', '2024年 2023年',
                         '营业收入 100.00 80.00', '净利润 20.00 15.00']
                for index, text in enumerate(lines):
                    page.insert_text((50, 70 + index * 45), text, fontname='china-s', fontsize=12)
            else:
                page.insert_text((50, 80), f'original page {number}', fontsize=12)
        document.save(pdf)
    doc_sha = sha256_file(pdf)
    rid = 'report-' + doc_sha[:20]
    _, _, blocks = partition_document(pdf)
    block = next(item for item in blocks if item['page'] == 3)
    block = dict(block, id=rid + ':' + block['id'], report_id=rid)
    baseline = [Fact('cache-np', '甲公司', 'NP', '2024Y', value=Decimal('20.00'),
                     publish_date='2025-03-01', source_doc=doc_sha[:12], source_path=str(pdf))]
    claim = extract_mentions('甲公司2024年合并营业收入100元。', '甲公司')[0]
    generation = {'schema_version': 'reparse-source-generation.v1', 'run_id': 'run',
                  'run_manifest_sha256': 'a' * 64, 'results_sha256': 'b' * 64,
                  'request_sha256': 'c' * 64, 'resource_lock': 'd' * 64}
    return {'output_root': tmp_path / 'controlled-reparse' / 'run', 'block': block, 'claim': claim,
            'source_resources': [{'id': rid, 'path': str(pdf), 'sha256': doc_sha}],
            'baseline_facts': baseline, 'company': '甲公司', 'as_of_date': '2025-03-31',
            'source_generation': generation}


def load(source, summary, **overrides):
    args = {key: source[key] for key in ('source_resources', 'baseline_facts', 'company', 'as_of_date')}
    args.update(expected_packet_id=summary['packet_id'], expected_source_generation=source['source_generation'])
    args.update(overrides)
    return load_reparse_packet(resolve_reparse_packet(source['output_root'], summary['packet_id']), **args)


def test_real_pdf_window_adds_missing_fact_without_changing_source_baseline_or_candidate(source):
    path = Path(source['source_resources'][0]['path'])
    before = path.read_bytes(), [fact.to_dict() for fact in source['baseline_facts']], source['claim'].to_dict()
    summary = create_reparse_packet(**source)
    assert summary['status'] == 'ready' and summary['new_facts_count'] > 0 and summary['conflicts'] == 0
    assert summary['proof']['pages'] == [2, 3, 4] and summary['requires_new_task_version']
    assert not summary['applied_to_current_task'] and 'source_path' not in json.dumps(summary)
    assert 'new_facts' not in summary
    facts = load(source, summary)
    revenue = next(fact for fact in facts if fact.account == 'REVENUE' and fact.period == '2024Y')
    assert revenue.value == Decimal('100.00') and revenue.parse_conf == '0.99'
    assert revenue.source_doc == sha256_file(path)[:12] and all(':p3:' in item for item in revenue.evidence)
    assert (path.read_bytes(), [fact.to_dict() for fact in source['baseline_facts']], source['claim'].to_dict()) == before
    assert list_reparse_packets(source['output_root'], expected_source_generation=source['source_generation']) == [summary]
    audit = source['output_root'] / 'audit' / summary['packet_id'] / 'audit.jsonl'
    assert json.loads(audit.read_text(encoding='utf-8').splitlines()[0])['type'] == 'reparse_packet_published'


def test_repeated_export_is_idempotent_and_no_information_packet_is_not_applicable(source):
    first = create_reparse_packet(**source)
    before = resolve_reparse_packet(source['output_root'], first['packet_id']).read_bytes()
    assert create_reparse_packet(**source) == first
    assert resolve_reparse_packet(source['output_root'], first['packet_id']).read_bytes() == before
    all_facts = load(source, first)
    args = dict(source, baseline_facts=[*source['baseline_facts'], *all_facts])
    empty = create_reparse_packet(**args)
    assert empty['status'] == 'no_new_information' and empty['new_facts_count'] == 0
    with pytest.raises(ValueError, match='Only a ready'):
        load(args, empty)


def test_conflicting_cached_same_version_value_is_not_replaced(source):
    baseline = [replace(source['baseline_facts'][0], value=Decimal('999'))]
    args = dict(source, baseline_facts=baseline)
    summary = create_reparse_packet(**args)
    assert summary['status'] == 'conflicts' and summary['conflicts'] > 0
    packet = json.loads(resolve_reparse_packet(source['output_root'], summary['packet_id']).read_text(encoding='utf-8'))
    assert all(row['abstention_code'] == 'X4' for row in packet['conflicts'])
    assert baseline[0].value == Decimal('999')
    with pytest.raises(ValueError, match='Only a ready'):
        load(args, summary)


def test_more_window_evidence_does_not_count_same_disclosure_value_as_a_new_fact(source):
    initial = create_reparse_packet(**source)
    facts = load(source, initial)
    baseline = [*source['baseline_facts'], *[replace(fact, evidence=(), fact_id='cache-' + fact.fact_id,
                                                    extractor_version='cached') for fact in facts]]
    args = dict(source, baseline_facts=baseline)
    summary = create_reparse_packet(**args)
    assert summary['status'] == 'no_new_information' and summary['new_facts_count'] == 0


def test_conflicting_original_pdf_rows_block_the_whole_packet_as_x4(source):
    import fitz
    pdf = Path(source['source_resources'][0]['path'])
    with fitz.open(pdf) as document:
        document[2].insert_text((50, 430), '净利润 999.00 15.00', fontname='china-s', fontsize=12)
        document.saveIncr()
    sha = sha256_file(pdf)
    rid = 'report-' + sha[:20]
    args = dict(source, block=dict(source['block'], id=rid + ':source-block', report_id=rid),
                source_resources=[dict(source['source_resources'][0], id=rid, sha256=sha)],
                baseline_facts=[replace(source['baseline_facts'][0], source_doc=sha[:12])])
    summary = create_reparse_packet(**args)
    assert summary['status'] == 'conflicts' and summary['conflicts'] > 0
    with pytest.raises(ValueError, match='Only a ready'):
        load(args, summary)


@pytest.mark.parametrize('change', ['cutoff', 'baseline', 'generation', 'source', 'packet', 'parser', 'kernel'])
def test_loader_rejects_changed_locks_and_tampering(source, monkeypatch, change):
    summary = create_reparse_packet(**source)
    overrides = {}
    if change == 'cutoff':
        overrides['as_of_date'] = '2025-04-01'
    elif change == 'baseline':
        overrides['baseline_facts'] = [replace(source['baseline_facts'][0], value=Decimal('21'))]
    elif change == 'generation':
        overrides['expected_source_generation'] = dict(source['source_generation'], results_sha256='e' * 64)
    elif change == 'source':
        Path(source['source_resources'][0]['path']).write_bytes(b'not the locked PDF')
    elif change == 'packet':
        path = resolve_reparse_packet(source['output_root'], summary['packet_id'])
        packet = json.loads(path.read_text(encoding='utf-8'))
        packet['new_facts'][0]['value'] = '999999'
        path.write_text(json.dumps(packet), encoding='utf-8')
    elif change == 'parser':
        monkeypatch.setattr('mingjing.reparse._parser_lock', lambda: 'f' * 64)
    elif change == 'kernel':
        monkeypatch.setattr('mingjing.reparse.kernel_fingerprint', lambda: {'changed': 'f' * 64})
    with pytest.raises(ValueError):
        load(source, summary, **overrides)


def test_even_rehashed_forged_values_are_rejected_by_source_page_reconstruction(source):
    summary = create_reparse_packet(**source)
    path = resolve_reparse_packet(source['output_root'], summary['packet_id'])
    packet = json.loads(path.read_text(encoding='utf-8'))
    packet['new_facts'][0]['value'] = '999999'
    body = {key: value for key, value in packet.items() if key not in {'packet_id', 'packet_sha256'}}
    packet['packet_sha256'] = digest(body)
    packet['packet_id'] = 'rp-' + packet['packet_sha256']
    forged = source['output_root'] / (packet['packet_id'] + '.json')
    forged.write_text(json.dumps(packet), encoding='utf-8')
    with pytest.raises(ValueError, match='deterministic source-page reconstruction'):
        load(source, packet)


@pytest.mark.parametrize('change', ['company', 'unknown_date', 'future', 'draft', 'page', 'cancel'])
def test_invalid_source_or_cancellation_does_not_publish_a_half_packet(source, change):
    args = copy.copy(source)
    if change == 'company':
        args['company'] = '乙公司'
    elif change == 'unknown_date':
        original = Path(source['source_resources'][0]['path'])
        renamed = original.with_name('2024年年度报告.pdf')
        original.rename(renamed)
        args['source_resources'] = [dict(source['source_resources'][0], path=str(renamed))]
        args['baseline_facts'] = [replace(source['baseline_facts'][0], publish_date='', source_path=str(renamed))]
    elif change == 'future':
        args['as_of_date'] = '2025-02-28'
    elif change == 'draft':
        args['block'] = dict(source['block'], report_id='draft')
    elif change == 'page':
        args['block'] = dict(source['block'], page=999)
    elif change == 'cancel':
        event = threading.Event()
        event.set()
        args['cancel_event'] = event
    with pytest.raises((ValueError, InterruptedError)):
        create_reparse_packet(**args)
    assert not source['output_root'].exists()


def test_cancellation_arriving_after_window_parse_leaves_no_packet_or_temp_file(source, monkeypatch):
    from mingjing import parser
    original = parser.extract_fact_window
    event = threading.Event()

    def cancel_after_read(*args, **kwargs):
        result = original(*args, **kwargs)
        event.set()
        return result

    monkeypatch.setattr(parser, 'extract_fact_window', cancel_after_read)
    with pytest.raises(InterruptedError):
        create_reparse_packet(**source, cancel_event=event)
    assert not source['output_root'].exists()


def test_generation_missing_hash_cannot_be_promoted_to_a_frozen_parent_packet(source):
    generation = dict(source['source_generation'])
    generation.pop('results_sha256')
    with pytest.raises(ValueError, match='complete frozen-parent fields'):
        create_reparse_packet(**dict(source, source_generation=generation))
    assert not source['output_root'].exists()


def test_no_registered_packet_id_can_escape_controlled_output_root(source):
    create_reparse_packet(**source)
    for identifier in ('../outside', '..\\outside', 'rp-' + 'f' * 64 + '/nested'):
        with pytest.raises(ValueError, match='registered reparse packet ID'):
            resolve_reparse_packet(source['output_root'], identifier)


def dispatcher(source):
    d = FinancialDispatcher('run', 'lock', [source['claim']], source['baseline_facts'], [source['block']],
        [{'report_id': source['block']['report_id'], 'companies': ['甲公司'], 'periods': ['2024Y']}],
        source['as_of_date'], task_company='甲公司', source_resources=source['source_resources'],
        reparse_root=source['output_root'], reparse_source_generation=source['source_generation'])
    for role in ('planner', 'verifier'):
        d.register_session(role, role)
        binding = dict(company='甲公司', account=source['claim'].account, period='2024Y', scope='合并', relation='value', base_period='')
        call(d, role, 'mj_submit_binding', {'mention_id': source['claim'].claim_id, 'binding': binding}, 'bind')
    d.freeze_bindings()
    d.register_session('coordinator', 'coordinator', [source['claim'].claim_id])
    return d


def test_native_reparse_is_post_binding_post_missing_read_and_never_changes_live_store(source):
    d = dispatcher(source)
    args = {'block_id': source['block']['id'], 'binding_id': source['claim'].claim_id}
    before = d.store.to_list(), d.claims[source['claim'].claim_id].to_dict()
    assert 'mj_request_reparse' not in {row['name'] for row in tool_definitions('planner')}
    with pytest.raises(ToolDenied, match='role cannot'):
        call(d, 'planner', 'mj_request_reparse', args, 'blind')
    with pytest.raises(ToolDenied, match='read a missing fact'):
        call(d, 'coordinator', 'mj_request_reparse', args, 'before-read')
    assert call(d, 'coordinator', 'mj_get_fact', {'binding_id': source['claim'].claim_id}, 'missing')['fact'] is None
    with pytest.raises(ValidationError):
        call(d, 'coordinator', 'mj_request_reparse', {**args, 'candidate_value': '999999'}, 'injection')
    result = call(d, 'coordinator', 'mj_request_reparse', args, 'reparse')
    assert result['status'] == 'ready' and result['requires_new_task_version']
    assert (d.store.to_list(), d.claims[source['claim'].claim_id].to_dict()) == before
    assert call(d, 'coordinator', 'mj_get_fact', {'binding_id': source['claim'].claim_id}, 'still-missing')['fact'] is None


def test_native_active_run_without_frozen_parent_reads_pdf_into_staging_only(source):
    d = dispatcher(source)
    d.reparse_source_generation = None
    call(d, 'coordinator', 'mj_get_fact', {'binding_id': source['claim'].claim_id}, 'missing')
    result = call(d, 'coordinator', 'mj_request_reparse',
                  {'block_id': source['block']['id'], 'binding_id': source['claim'].claim_id}, 'reparse')
    assert result['status'] == 'staged' and result['staged']
    assert result['reason'] == 'requires_frozen_parent_generation'
    assert result['source_generation'] == {} and result['stage_id'].startswith('stage-')
    assert result['proof']['source_pages'] and result['proof']['pages'] == [2, 3, 4]
    assert 'new_facts' not in result and 'source_path' not in json.dumps(result)
    assert d.store.to_list() == [fact.to_dict() for fact in source['baseline_facts']]
    with pytest.raises(ValueError, match='staged packets require freezing'):
        load(source, result, expected_source_generation=None)


def test_native_reparse_without_controlled_root_remains_unavailable(source):
    d = dispatcher(source)
    d.reparse_source_generation = None
    d.reparse_root = None
    call(d, 'coordinator', 'mj_get_fact', {'binding_id': source['claim'].claim_id}, 'missing')
    result = call(d, 'coordinator', 'mj_request_reparse',
                  {'block_id': source['block']['id'], 'binding_id': source['claim'].claim_id}, 'reparse')
    assert result['status'] == 'unavailable' and result['reason'] == 'controlled_reparse_output_root_not_configured'
    assert not source['output_root'].exists()


def test_native_repeated_no_information_stops_without_rewriting_baseline(source):
    initial = create_reparse_packet(**source)
    claim = extract_mentions('甲公司合并口径2024年经营活动产生的现金流量净额为900元。', '甲公司')[0]
    complete = dict(source, claim=claim, baseline_facts=[*source['baseline_facts'], *load(source, initial)])
    d = dispatcher(complete)
    assert d.agreements[claim.claim_id]['agreed'], d.agreements[claim.claim_id]
    # A binding for a genuinely unsupported metric can ask for local evidence;
    # the PDF contains only facts already present in this frozen baseline.
    mid = claim.claim_id
    call(d, 'coordinator', 'mj_get_fact', {'binding_id': mid}, 'missing')
    args = {'block_id': source['block']['id'], 'binding_id': mid}
    assert call(d, 'coordinator', 'mj_request_reparse', args, 'first')['status'] == 'no_new_information'
    second = call(d, 'coordinator', 'mj_request_reparse', args, 'second')
    assert second['status']=='stopped' and second['reason']=='no_new_information'
    assert call(d, 'coordinator', 'mj_request_reparse', args, 'third')['reason'] == 'no_new_information'
    assert d.reparse_claim_attempts[mid]==1
