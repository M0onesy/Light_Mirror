"""Real local PDF supplements with controlled (not live) Agent boundaries."""
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from mingjing.agent import FinancialAgent
from mingjing.cli import build_parser, command_check
from mingjing.evidence import sha256_file
from mingjing.parser import extract_fact_window
from mingjing.reparse import create_reparse_packet
from mingjing.reparse_inputs import apply_task_reparse_packets, load_parent_context
from mingjing.replay import replay_run
from test_agent_lifecycle import lifecycle
from test_fact_window import pdf


def setup_version(lifecycle):
    root = lifecycle.out.parent / '920001_甲公司'
    root.mkdir()
    source = pdf(root, [['合并利润表', '单位：万元', '项目\n2024年\n2023年',
                        '营业收入\n100.00\n80.00', '资产总计\n900.00\n850.00']],
                 name='2025-03-01_2024年年度报告.pdf')
    window = extract_fact_window(source, pages=[1], company='甲公司', publish_date='2025-03-01',
                                 expected_sha256=sha256_file(source))
    # This deliberate subset is a controlled structured fixture, not an
    # authenticated complete extraction cache or independent financial gold.
    baseline = [replace(fact, source_type='structured', extractor_version='structured').to_dict()
                for fact in window['facts'] if fact.account == 'ASSETS']
    assert baseline and len(window['facts']) > len(baseline)
    for row in baseline:
        row['source_path'] = source.name
    (root / 'facts.json').write_text(json.dumps(baseline, ensure_ascii=False), encoding='utf-8')
    Path(lifecycle.request['draft']).write_text('甲公司2024年合并营业收入100万元，同比增长25%。', encoding='utf-8')
    request = dict(lifecycle.request, corpus=str(root), task_type='check', scope_declaration='')
    args = build_parser().parse_args(['check', '--draft', request['draft'], '--corpus', request['corpus'],
        '--company', request['company'], '--period', request['period'], '--report-date', request['report_date'],
        '--out', str(lifecycle.out), '--run-id', 'reparse-parent', '--mode', 'kernel'])
    assert command_check(args) == 0
    parent = load_parent_context(lifecycle.out)
    original = {path: path.read_bytes() for path in lifecycle.out.rglob('*') if path.is_file()}
    resource = next(row for row in parent['source_resources'] if row['path'] == str(source))
    packet_root = lifecycle.out.parent / 'reparse' / 'reparse-parent'
    summary = create_reparse_packet(output_root=packet_root, block={'id': 'page-1', 'report_id': resource['id'], 'page': 1},
        claim=None, source_resources=parent['source_resources'], baseline_facts=parent['baseline_facts'],
        company=parent['company'], as_of_date=parent['as_of_date'], source_generation=parent['source_generation'])
    assert summary['status'] == 'ready', summary
    path = packet_root / (summary['packet_id'] + '.json')
    ref = {'path': str(path), 'packet_id': summary['packet_id'], 'sha256': sha256_file(path),
           'source_generation': parent['source_generation']}
    return SimpleNamespace(request=request, parent=parent, packet=path, ref=ref, source=source, original=original)


def test_new_kernel_task_uses_pdf_facts_and_freezes_parent_and_packet(lifecycle):
    version = setup_version(lifecycle)
    out = lifecycle.out.parent / 'supplemented-kernel'
    request = version.request
    args = build_parser().parse_args(['check', '--draft', request['draft'], '--corpus', request['corpus'],
        '--company', request['company'], '--period', request['period'], '--report-date', request['report_date'],
        '--out', str(out), '--mode', 'kernel', '--reparse-packet', str(version.packet),
        '--reparse-parent-run', str(lifecycle.out)])
    assert command_check(args) == 0
    result = json.loads((out / 'results.json').read_text(encoding='utf-8'))
    assert result['summary'] == {'verified': 2, 'violated': 0, 'abstained': 0}
    assert len(result['reparse_supplements']) == 1
    assert replay_run(out)['status'] == 'passed'
    assert all(path.read_bytes() == raw for path, raw in version.original.items())
    version.packet.write_text('{}', encoding='utf-8')
    assert replay_run(out)['status'] == 'failed'


def test_native_host_loads_supplement_before_resource_lock_and_numeric_tools(lifecycle):
    version = setup_version(lifecycle)
    lifecycle.boundary.cancel_first_binding = False
    out = lifecycle.out.parent / 'supplemented-native'
    request = dict(version.request, reparse_packets=[version.ref], reparse_parent_run=str(lifecycle.out),
                   _controlled_reparse_root=str(version.packet.parent))
    owner = FinancialAgent(request, lifecycle.config, lambda _: pytest.fail('No real model in fixture'),
                           out, run_id='supplemented-native')
    result = owner.run()
    assert result['model']['status'] == 'success', result['model']
    assert result['summary'] == {'verified': 2, 'violated': 0, 'abstained': 0}
    assert owner.snapshot['reparse_supplements'] == result['reparse_supplements']
    assert len(owner.facts) == len(owner.dispatcher.store.facts) == 4
    assert str(version.packet) in {row['path'] for row in owner.snapshot['resources']}
    assert replay_run(out)['status'] == 'passed'
    assert all(path.read_bytes() == raw for path, raw in version.original.items())


@pytest.mark.parametrize('field,value', [('company', '乙公司'), ('report_date', '2025-04-01'),
                                       ('scope_declaration', '母公司'), ('period', '2023Y')])
def test_supplement_cannot_change_parent_business_dimensions(lifecycle, field, value):
    version = setup_version(lifecycle)
    request = dict(version.request, reparse_packets=[version.ref], reparse_parent_run=str(lifecycle.out))
    request[field] = value
    with pytest.raises(ValueError, match='differs|differ'):
        apply_task_reparse_packets(request, version.parent['baseline_facts'])
    assert all(path.read_bytes() == raw for path, raw in version.original.items())


def test_reparse_rejects_changed_parent_request_hash_and_duplicate_packet(lifecycle):
    version = setup_version(lifecycle)
    request = dict(version.request, reparse_packets=[version.ref, version.ref], reparse_parent_run=str(lifecycle.out))
    with pytest.raises(ValueError, match='Duplicate'):
        apply_task_reparse_packets(request, version.parent['baseline_facts'])
    request['reparse_packets'] = [dict(version.ref, source_generation=dict(version.ref['source_generation'], request_sha256='0'*64))]
    with pytest.raises(ValueError, match='another parent'):
        apply_task_reparse_packets(request, version.parent['baseline_facts'])


def test_parent_frozen_bytes_are_required_even_if_packet_itself_is_valid(lifecycle):
    version = setup_version(lifecycle)
    (lifecycle.out / 'facts.json').write_text('[]', encoding='utf-8')
    with pytest.raises(ValueError, match='hash changed'):
        load_parent_context(lifecycle.out)


def test_resume_missing_fact_authority_is_same_session_and_source_valid_only(lifecycle):
    owner = FinancialAgent(lifecycle.request, lifecycle.config, lambda _: None, lifecycle.out)
    owner.prepare()
    def event(sid, tool, params, output, status='success'):
        return dict(session_id=sid, role='coordinator', tool=tool, input=params, output=output, status=status)
    owner.dispatcher.trace = [
        event('A', 'mj_get_fact', {'binding_id': 'm1'}, {'fact': None}),
        event('A', 'mj_get_fact', {'binding_id': 'm2'}, {'fact': None, 'source_definition_unavailable': 'unknown scope'}),
        event('B', 'mj_get_facts', {'binding_ids': ['m3', 'm4']}, {'items': [
            {'binding_id': 'm3', 'fact': None}, {'binding_id': 'm4', 'fact': None},
            {'binding_id': 'outside', 'fact': None}]}),
        event('B', 'mj_get_fact', {'binding_id': 'm4'}, {'fact': {'fact_id': 'known'}}),
        event('B', 'mj_get_fact', {'binding_id': 'm5'}, {'fact': None}, 'denied')]
    observations = owner._native_observations()
    assert observations['A']['facts_missing'] == {'m1'}
    assert observations['B']['facts_missing'] == {'m3'}


@pytest.mark.parametrize('value,conflicted', [('1000000.00', False), ('999999.00', True)])
def test_deducted_parent_profit_alias_cannot_bypass_packet_conflict_or_dedup(lifecycle, value, conflicted):
    version = setup_version(lifecycle)
    parsed = extract_fact_window(version.source, pages=[1], company='甲公司', publish_date='2025-03-01',
                                 expected_sha256=sha256_file(version.source))
    current = replace(next(fact for fact in parsed['facts'] if fact.account=='REVENUE' and fact.period=='2024Y'),
                      account='NP_PARENT_ADJ', nr='扣非')
    cached = replace(current, fact_id='old-alias', account='NP_PARENT', value=Decimal(value), evidence=())
    from mingjing.reparse import _split_facts
    metadata = {'company': '甲公司', 'doc_sha256': sha256_file(version.source),
                'publish_date': '2025-03-01', 'as_of_date': '2025-03-31'}
    added, conflicts = _split_facts([current], [cached], metadata, [1])
    assert not added
    assert bool(conflicts) is conflicted
    if conflicts:
        assert conflicts[0]['abstention_code'] == 'X4'


@pytest.mark.parametrize('value,conflicted', [('1000000.00', False), ('999999.00', True)])
def test_equivalent_disclosure_version_labels_cannot_bypass_conflict_or_dedup(lifecycle, value, conflicted):
    version = setup_version(lifecycle)
    parsed = extract_fact_window(version.source, pages=[1], company='甲公司', publish_date='2025-03-01',
                                 expected_sha256=sha256_file(version.source))
    current = next(fact for fact in parsed['facts'] if fact.account=='REVENUE' and fact.period=='2024Y')
    cached = replace(current, fact_id='old-version-label', version='original', value=Decimal(value), evidence=())
    from mingjing.reparse import _split_facts
    metadata = {'company': '甲公司', 'doc_sha256': sha256_file(version.source),
                'publish_date': '2025-03-01', 'as_of_date': '2025-03-31'}
    added, conflicts = _split_facts([current], [cached], metadata, [1])
    assert not added and bool(conflicts) is conflicted


def test_added_fact_evidence_is_exported_even_without_a_matching_draft_claim(lifecycle):
    version = setup_version(lifecycle)
    # Use a separate original draft and parent; the first task stays frozen.
    old_bytes = dict(version.original)
    draft = lifecycle.out.parent / 'no-claims-draft.md'
    draft.write_text('只保存财报材料与证据，不提供数值陈述。', encoding='utf-8')
    version.request = dict(version.request, draft=str(draft))
    old_parent = lifecycle.out
    lifecycle.out = old_parent.parent / 'no-claims-parent'
    version = setup_version_no_claims(lifecycle, version)
    out = old_parent.parent / 'no-claims-child'
    args = build_parser().parse_args(['check', '--draft', str(draft), '--corpus', version.request['corpus'],
        '--company', version.request['company'], '--period', version.request['period'],
        '--report-date', version.request['report_date'], '--out', str(out), '--mode', 'kernel',
        '--reparse-packet', str(version.packet), '--reparse-parent-run', str(lifecycle.out)])
    assert command_check(args)==0
    result = json.loads((out/'results.json').read_text(encoding='utf-8'))
    assert result['results']==[]
    ids = set(result['reparse_supplements'][0]['added_fact_ids'])
    evidence = {identity for fact in result['facts'] if fact['fact_id'] in ids for identity in fact['evidence']}
    assert evidence and evidence <= result['evidence_resources'].keys()
    assert all((out/result['evidence_resources'][identity]['image']).is_file() for identity in evidence)
    assert replay_run(out)['status']=='passed'
    assert all(path.read_bytes()==raw for path,raw in old_bytes.items())


def setup_version_no_claims(lifecycle, version):
    request = version.request
    args = build_parser().parse_args(['check', '--draft', request['draft'], '--corpus', request['corpus'],
        '--company', request['company'], '--period', request['period'], '--report-date', request['report_date'],
        '--out', str(lifecycle.out), '--run-id', 'no-claims-parent'])
    assert command_check(args)==0
    parent = load_parent_context(lifecycle.out)
    resource = next(row for row in parent['source_resources'] if row['path']==str(version.source))
    packet_root = lifecycle.out.parent / 'reparse' / 'no-claims-parent'
    summary = create_reparse_packet(output_root=packet_root, block={'id':'page-1','report_id':resource['id'],'page':1},
        claim=None, source_resources=parent['source_resources'], baseline_facts=parent['baseline_facts'],
        company=parent['company'], as_of_date=parent['as_of_date'], source_generation=parent['source_generation'])
    return SimpleNamespace(request=request, packet=packet_root/(summary['packet_id']+'.json'))


def test_native_missing_fact_stages_original_page_and_only_frozen_child_applies_it(lifecycle):
    version = setup_version(lifecycle)
    lifecycle.boundary.cancel_first_binding = False
    parent = lifecycle.out.parent / 'native-missing-parent'
    owner = FinancialAgent(version.request, lifecycle.config, lambda _: pytest.fail('Fixture cannot call a model'),
                           parent, run_id='native-missing-parent')
    observed = []
    def after_tool(runtime, spec, name, arguments, output):
        if name!='mj_get_fact' or output.get('fact') is not None or output.get('source_definition_unavailable'):
            return
        block = next(row for row in owner.dispatcher.blocks.values() if row.get('report_id') and row['page']==1)
        result = owner._incoming('mj/tool.dispatch', {
            **{key:spec[key] for key in ('run_id','resource_lock','session_id','role')},
            'call_id':'stage-'+arguments['binding_id'], 'tool_name':'mj_request_reparse',
            'params':{'binding_id':arguments['binding_id'],'block_id':block['id']}})
        observed.append(result)
        assert result['status']=='staged' and not result['applied_to_current_task']
        assert len(owner.dispatcher.store.facts)==2
    lifecycle.boundary.after_tool = after_tool
    result = owner.run()
    lifecycle.boundary.after_tool = None
    assert result['model']['status']=='success', result['model']
    assert result['summary']=={'verified':0,'violated':0,'abstained':2}
    assert observed and len(result['facts'])==2
    manifest = json.loads((parent/'run_manifest.json').read_text(encoding='utf-8'))
    assert any(row['path'].startswith('staged-reparse/rp-') for row in manifest['outputs'])
    from mingjing.reparse import list_reparse_packets
    packets_root = parent.parent/'reparse'/'native-missing-parent'
    context = load_parent_context(parent)
    proposals = list_reparse_packets(packets_root, expected_source_generation=context['source_generation'])
    assert proposals and all(row['status']=='ready' for row in proposals)
    frozen_bytes = {path:path.read_bytes() for path in parent.rglob('*') if path.is_file()}
    packet = packets_root/(proposals[0]['packet_id']+'.json')
    child = parent.parent/'native-supplemented-child'
    request = dict(version.request, reparse_parent_run=str(parent), reparse_packet_paths=[str(packet)])
    data = FinancialAgent(request, lifecycle.config, lambda _: pytest.fail('No real API in fixture'),
                          child, run_id='native-supplemented-child').run()
    assert data['summary']=={'verified':2,'violated':0,'abstained':0}
    assert data['model']['status']=='success'
    assert replay_run(parent)['status']==replay_run(child)['status']=='passed'
    assert all(path.read_bytes()==raw for path,raw in frozen_bytes.items())
