"""Trusted ingestion and native judgement gates; no providers or native process."""
import json
import threading
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from mingjing.agent import FinancialAgent, task_request_semantics
from mingjing.config import AppConfig
from mingjing.financial_tools import FinancialDispatcher, FinancialRequirementError
from mingjing.models import Fact
from mingjing.smart.ingestion import SmartKernel, ingest_facts, partition_smart, smart_claims
from mingjing.smart.kb import KnowledgeBase
from mingjing.smart.native import SmartRunAdapter


def fixture_materials(tmp_path):
    kb = KnowledgeBase(tmp_path / 'kb')
    filing = kb.add_bytes(('甲公司2023年年度报告\n合并利润表\n单位：元\n'
                           '项目  2023年度  2022年度\n营业收入  100.00  90.00\n'
                           '营业成本  60.00  50.00\n净利润  20.00  18.00\n').encode(), 'annual.txt',
                          hints={'company': '甲公司', 'period': '2023Y', 'report_date': '2024-04-01', 'doc_type': 'annual_report'})
    report = kb.add_bytes('甲公司证券研究报告\n2023年公司营业收入为100.00元。\n'.encode(), 'research.txt',
                         hints={'company': '甲公司', 'period': '2023Y', 'report_date': '2024-05-01', 'doc_type': 'research_report'})
    adapter = SmartRunAdapter(None, kb, tmp_path / 'runs')
    corpus, draft_root = tmp_path / 'selected', tmp_path / 'draft'
    adapter.freeze([filing], corpus)
    adapter.freeze([report], draft_root)
    return kb, corpus, draft_root / (report['doc_id'] + '.txt'), report


def test_uploaded_index_freezes_explicit_period_and_detected_metadata(tmp_path):
    _, corpus, _, _ = fixture_materials(tmp_path)
    facts, evidence = ingest_facts(corpus, '甲公司', '2023Y')
    assert any(f.account == 'REVENUE' and f.value == Decimal('100') for f in facts)
    assert all(f.source_table_id and f.evidence and f.publish_date == '2024-04-01' for f in facts)
    assert evidence and all(e['document_sha256'] for e in evidence)
    blob = next(corpus.glob('*.txt'))
    blob.write_text('changed', encoding='utf-8')
    with pytest.raises(ValueError, match='SHA-256|字节|大小'):
        ingest_facts(corpus, '甲公司', '2023Y')


def test_host_standard_strategy_and_native_callback_share_exact_result(tmp_path):
    kb, corpus, draft, report = fixture_materials(tmp_path)
    facts, _ = ingest_facts(corpus, '甲公司', '2023Y')
    text, pages, blocks = partition_smart(draft)
    metadata = {k: v for k, v in report.items() if k in __import__('mingjing.smart.detect', fromlist=['DocMeta']).DocMeta().to_dict()}
    model, claims = smart_claims(draft, text, pages, blocks, '甲公司', '2023Y', facts, 'standard', metadata=metadata)
    assert len(claims) == 1
    claim = claims[0]
    kernel = SmartKernel(facts, model, 'standard', {}, [], {claim.claim_id: claim.table_context['smart_claim']})
    result = kernel.verify(claim)
    assert result['status'] == 'verified'
    assert result['smart_result']['expected'] and result['rule_ids']
    strict = SmartKernel(facts, model, 'strict', {}, [], kernel.claim_map).verify(claim)
    assert strict['status'] == 'abstained'
    dispatcher = FinancialDispatcher('native-check', 'lock', claims, facts, blocks, [], '2024-05-01', task_company='甲公司')
    dispatcher.trusted_verifier = kernel.verify
    dispatcher.validation_policy = 'standard'
    assert dispatcher._required_verification_rules(claim) == ['SMART-registered-kernel']
    dimensions = dispatcher.source_dimensions(claim)
    assert dimensions['account'] == 'REVENUE' and dimensions['scope_assumption']
    for role in ('planner', 'verifier'):
        dispatcher.bindings[role][claim.claim_id] = dict(claim.table_context['smart_binding'])
    assert dispatcher.freeze_bindings()[0].binding_status == 'agreed'
    dispatcher.register_session('coordinator', 'coordinator', [claim.claim_id])
    def call(tool, args, call_id):
        return dispatcher.dispatch({'run_id': dispatcher.run_id, 'resource_lock': dispatcher.resource_lock,
                                    'session_id': 'coordinator', 'role': 'coordinator', 'call_id': call_id,
                                    'tool_name': tool, 'params': args})
    with pytest.raises(FinancialRequirementError):
        call('mj_verify', {'binding_id': claim.claim_id}, 'before-read')
    batch = call('mj_get_facts', {'binding_ids': [claim.claim_id]}, 'facts')
    assert batch['items'][0]['required_rule_ids'] == ['SMART-registered-kernel']
    assert batch['items'][0]['fact']['account'] == 'REVENUE'
    call('mj_get_rule', {'rule_id': 'SMART-registered-kernel'}, 'rule')
    model_verdict = call('mj_verify', {'binding_id': claim.claim_id}, 'verify')
    full_verdict = kernel.verify(dispatcher.claims[claim.claim_id])
    assert dispatcher.trace[-1]['output'] == full_verdict
    assert 'smart_result' not in model_verdict
    assert model_verdict['status'] == full_verdict['status']
    assert model_verdict['expected'] == full_verdict['expected']
    assert model_verdict['assumptions'] == full_verdict['smart_result']['assumptions']
    dispatcher.bindings['verifier'][claim.claim_id]['period'] = '2022Y'
    assert dispatcher.freeze_bindings()[0].binding_status == 'disagreed'


def test_smart_yoy_empty_base_and_segment_keep_frozen_host_semantics(tmp_path):
    from dataclasses import replace
    _, corpus, draft, report = fixture_materials(tmp_path)
    facts, _ = ingest_facts(corpus, '甲公司', '2023Y')
    text, pages, blocks = partition_smart(draft)
    model, claims = smart_claims(draft, text, pages, blocks, '甲公司', '2023Y', facts, 'standard')
    original = claims[0]
    binding = {**original.table_context['smart_binding'], 'relation': 'yoy', 'base_period': ''}
    context = {**original.table_context, 'smart_binding': binding,
               'smart_claim': {**original.table_context['smart_claim'], 'entity': 'segment', 'segment': '超算云服务'},
               'smart_dimensions': {'dimension_kind': 'business', 'dimension_id': '超算云服务'}}
    claim = replace(original, relation='yoy', base_period=None, table_context=context)
    dispatcher = FinancialDispatcher('smart-yoy', 'lock', [claim], facts, blocks, [], '2024-05-01', task_company='甲公司')
    dispatcher.trusted_verifier = lambda _: None
    for role in ('planner', 'verifier'):
        dispatcher.bindings[role][claim.claim_id] = dict(binding)
    assert dispatcher.freeze_bindings()[0].binding_status == 'agreed'
    view = dispatcher.mention_context(claim, compact=True)
    assert view['issuer_context']['applicable']
    assert view['source_dimensions']['scope_assumption'] is True
    assert view['source_dimensions']['smart_segment'] == '超算云服务'
    assert view['source_dimensions']['dimension_kind'] == 'business'
    dispatcher.store.facts.append(replace(facts[0], fact_id='segment-revenue', account='REVENUE', value=Decimal('123'),
                                          dimension_kind='business', dimension_id='超算云服务'))
    # Rebuild the index after introducing the explicit adjacent segment fact.
    from mingjing.facts import FactStore
    dispatcher.store = FactStore(dispatcher.store.facts)
    dispatcher.register_session('coordinator', 'coordinator', [claim.claim_id])
    queried = dispatcher.dispatch({'run_id': dispatcher.run_id, 'resource_lock': dispatcher.resource_lock,
                                   'session_id': 'coordinator', 'role': 'coordinator', 'call_id': 'segment-query',
                                   'tool_name': 'mj_get_fact', 'params': {'binding_id': claim.claim_id}})
    assert queried['query_dimensions']['dimension_id'] == '超算云服务'
    assert queried['fact']['value'] == '123'
    dispatcher.bindings['verifier'][claim.claim_id]['base_period'] = '2022Y'
    assert dispatcher.freeze_bindings()[0].binding_status == 'disagreed'


def test_smart_model_owns_claims_while_raw_coverage_remains_complete(tmp_path):
    from mingjing.coverage import CoverageLedger
    from mingjing.mentions import VALUE_RE
    _, corpus, draft, report = fixture_materials(tmp_path)
    draft.write_text('甲公司证券研究报告\n2023年公司营业收入为100.00元。\n999\n图1:营业收入\n2022 2023\n', encoding='utf-8')
    facts, _ = ingest_facts(corpus, '甲公司', '2023Y')
    text, pages, blocks = partition_smart(draft)
    model, trusted = smart_claims(draft, text, pages, blocks, '甲公司', '2023Y', facts, 'standard', metadata=report)
    ledger, claims = CoverageLedger.build(text, pages, blocks, '甲公司', '2023Y', trusted_claims=trusted)
    assert len(claims) == len(model.claims) == len(trusted)
    assert {c.claim_id for c in claims} == {c.claim_id for c in trusted}
    assert len(ledger.entries) == len(list(VALUE_RE.finditer(text)))
    assert any(e['status'] in {'rule_excluded', 'out_of_scope'} for e in ledger.entries)
    assert all(e['claim_id'] in {c.claim_id for c in trusted} for e in ledger.entries if e['claim_id'])


def test_selected_metadata_cannot_rename_explicit_source_issuer(tmp_path):
    kb = KnowledgeBase(tmp_path / 'kb')
    other = kb.add_bytes('乙公司2023年年度报告\n合并利润表\n单位：元\n项目  2023年度\n营业收入  100\n'.encode(),
                         'other.txt', hints={'company': '甲公司', 'period': '2023Y', 'report_date': '2024-04-01', 'doc_type': 'annual_report'})
    SmartRunAdapter(None, kb, tmp_path / 'runs').freeze([other], tmp_path / 'selected')
    with pytest.raises(ValueError, match='原文发行人.*冲突'):
        ingest_facts(tmp_path / 'selected', '甲公司', '2023Y')


def test_original_security_code_proves_issuer_alias_without_accepting_neighbor_code(tmp_path):
    from mingjing.smart.detect import detect
    from mingjing.smart.docio import load_document
    kb = KnowledgeBase(tmp_path / 'kb')
    subject = kb.add_bytes('星河智造（999001）\n证券研究报告\n2023年营业收入100元。'.encode(), 'research.txt')
    subject_meta = detect(load_document(kb.blob_path(subject['doc_id'], subject['ext'])))
    assert subject_meta.company == '星河智造' and subject_meta.stock_code == '999001'
    adapter = SmartRunAdapter(None, kb, tmp_path / 'runs')
    for code in ('999001', '999002'):
        filing = kb.add_bytes((f'星河示范科技股份有限公司2023年年度报告\n证券代码：{code}\n'
                              '合并利润表\n单位：元\n项目  2023年度\n营业收入  100\n').encode(), 'annual-' + code + '.txt',
                hints={'company': '星河示范科技', 'stock_code': '999001', 'period': '2023Y', 'report_date': '2024-04-01', 'doc_type': 'annual_report'})
        corpus = tmp_path / code
        adapter.freeze([filing], corpus)
        if code == '999001':
            facts, evidence = ingest_facts(corpus, '星河智造', '2023Y', task_subject=subject_meta)
            assert facts and all(f.company == '星河智造' for f in facts)
            assert any(e.get('issuer_resolution', {}).get('basis') == 'source_declared_security_code' for e in evidence)
        else:
            with pytest.raises(ValueError, match='原文发行人.*冲突'):
                ingest_facts(corpus, '星河智造', '2023Y', task_subject=subject_meta)


def test_provider_protocol_credentials_and_rejection_survive_restart(tmp_path):
    from mingjing.smart.web import make_server
    server = make_server('127.0.0.1', 0, str(tmp_path / 'home'))
    try:
        service = server.smart
        saved = service.save_settings({'llm': {'enabled': True, 'base_url': 'http://127.0.0.1:9/v1',
                                               'model': 'offline-test', 'protocol': 'openai-responses'}, 'api_key': 'test-secret'})
        assert saved['llm']['protocol'] == 'openai-responses'
        assert saved['llm']['chat_path'] == '/responses'
        assert saved['effective']['configured'] and saved['llm']['has_key']
        assert 'test-secret' not in json.dumps(saved)
        before = server.state.config.get()
        ref = before['providers']['smart']['credential_ref']
        for patch in ({'enabled': 'false'}, {'enabled': 1}, {'base_url': 'https://user:pass@example.invalid/v1'}):
            with pytest.raises(ValueError):
                service.save_settings({'llm': patch, 'api_key': 'must-not-be-stored'})
            assert server.state.config.get() == before
            assert service.secret(ref) == 'test-secret'
    finally:
        server.server_close()
    restored = make_server('127.0.0.1', 0, str(tmp_path / 'home'))
    try:
        assert restored.smart.settings()['llm'] == saved['llm']
        assert restored.smart.settings()['effective']['configured']
        for options in ({'use_llm': 'false'}, {'strict': 1}, {'company': []}, {'report_date': '2024-02-30'},
                        {'scope': '全集团'}, {'scope': True}, {'scope': None}):
            with pytest.raises(ValueError):
                restored.smart.start([], options=options)
    finally:
        restored.server_close()


def test_queued_batch_freezes_configuration_and_supplement_publishes_new_batch(tmp_path, monkeypatch):
    from mingjing.smart.web import make_server
    server = make_server('127.0.0.1', 0, str(tmp_path / 'home'))
    try:
        service = server.smart
        service.save_settings({'llm': {'enabled': True, 'base_url': 'http://127.0.0.1:9/v1', 'model': 'before-queue'}})
        filing = service.kb.add_bytes(('甲公司2023年年度报告\n合并利润表\n单位：元\n项目  2023年度  2022年度\n'
                                       '营业收入  100  90\n').encode(), 'annual.txt',
                                      hints={'company': '甲公司', 'period': '2023Y', 'report_date': '2024-04-01', 'doc_type': 'annual_report'})
        reports = [service.kb.add_bytes(f'甲公司证券研究报告\n2023年营业收入为{number}元。\n'.encode(), f'research-{number}.txt',
                                       hints={'company': '甲公司', 'period': '2023Y', 'report_date': '2024-05-01', 'doc_type': 'research_report'})
                   for number in (100, 101)]
        entered, release = threading.Event(), threading.Event()
        original = service.native.run_batch
        def delayed(*args, **kwargs):
            entered.set()
            assert release.wait(10)
            return original(*args, **kwargs)
        monkeypatch.setattr(service.native, 'run_batch', delayed)
        started = service.start([filing['doc_id'], *(r['doc_id'] for r in reports)], options={'kb_lookup': False})
        assert entered.wait(10)
        service.save_settings({'llm': {'model': 'after-queue'}})
        release.set()
        record = service.runs[started['run_id']]
        record['_thread'].join(30)
        assert record['status'] == 'completed', record
        assert len(record['children']) == 2
        assert all(server.state.get_task(c['native_run_id']).config_snapshot['agent']['routing']['default']['model'] == 'before-queue'
                   for c in record['children'])
        cfg = json.loads((service.runs_root / record['run_id'] / 'config_snapshot.json').read_text(encoding='utf-8'))
        assert cfg['revision'] == record['config_revision']
        from mingjing.smart.docio import sha256_path
        assert sha256_path(service.runs_root / record['run_id'] / 'config_snapshot.json') == record['config_sha256']
        supplemented = service.native.add_materials(record, record['children'][0]['item_id'], {'scope_declaration': '合并'})
        new_record = service.runs[supplemented['batch_id']]
        new_record['_thread'].join(30)
        assert new_record['status'] == 'completed', new_record
        assert new_record['parent_batch_id'] == record['run_id']
        assert supplemented['run_id'] != record['children'][0]['native_run_id']
        assert (service.runs_root / supplemented['batch_id'] / new_record['children'][0]['item_id'] / 'results.json').is_file()
        assert server.state.get_task(supplemented['run_id']).config_snapshot['agent']['routing']['default']['model'] == 'after-queue'
    finally:
        server.server_close()


def test_prepare_locks_new_parser_model_and_policy_without_model_call(tmp_path):
    _, corpus, draft, report = fixture_materials(tmp_path)
    cfg = AppConfig().ensure_defaults().to_dict()
    cfg['workspace']['knowledge_root'] = ''
    request = {'draft': str(draft), 'corpus': str(corpus), 'company': '甲公司', 'period': '2023Y',
               'report_date': '2024-05-01', 'task_type': 'check', 'mode': 'harness', 'material_profile': 'smart',
               'validation_policy': 'standard', 'smart_options': {'use_llm': False},
               'smart_draft_metadata': {k: v for k, v in report.items() if k in __import__('mingjing.smart.detect', fromlist=['DocMeta']).DocMeta().to_dict()}}
    owner = FinancialAgent(request, cfg, lambda _: '', tmp_path / 'native', run_id='native-check')
    owner.prepare()
    assert any(c.table_context.get('smart_cid') for c in owner.claims)
    assert all(not c.table_context['source_scope_required'] for c in owner.claims)
    assert owner.snapshot['task_semantics']['request']['validation_policy'] == 'standard'
    assert {'smart-report-model', 'smart-kernel-context', 'smart-evidence'} <= {r['id'] for r in owner.snapshot['resources']}
    frozen = json.loads((owner.out / 'agent_lock.json').read_text(encoding='utf-8'))
    second = FinancialAgent(request, cfg, lambda _: '', owner.out, run_id=owner.run_id)
    second.prepare(locked=frozen)
    assert second.snapshot == frozen
    assert second.smart_kernel.verify(second.claims[0])['status'] == 'verified'


def test_batch_cancel_reaches_registered_native_children():
    task = SimpleNamespace(cancelled=threading.Event())
    adapter = SmartRunAdapter(SimpleNamespace(get_task=lambda _: task), None, '.')
    record = {'children': [{'native_run_id': 'native-check'}]}
    adapter.cancel(record)
    assert record['_cancel'].is_set() and task.cancelled.is_set()


def test_smart_policy_is_frozen_and_rejects_undocumented_variants():
    assert task_request_semantics({'material_profile': 'smart'})['validation_policy'] == 'standard'
    with pytest.raises(ValueError, match='策略'):
        task_request_semantics({'material_profile': 'smart', 'validation_policy': 'guess'})


def test_smart_analysis_scope_projects_same_kernel_into_native_tools_and_slots(tmp_path):
    from mingjing.facts import fact_from_dict
    from mingjing.smart.analysis import analyze
    from mingjing.smart.native_analysis import project_analysis
    from mingjing.smart.web import make_server
    server = make_server('127.0.0.1', 0, str(tmp_path / 'home'))
    try:
        service = server.smart
        filing = service.kb.add_bytes(('甲公司2023年年度报告\n合并利润表\n单位：元\n'
                    '项目  2023年度  2022年度\n营业收入  100.00  90.00\n'
                    '营业成本  60.00  50.00\n净利润  20.00  18.00\n').encode(), 'annual.txt',
                    hints={'company': '甲公司', 'period': '2023Y', 'report_date': '2024-04-01', 'doc_type': 'annual_report'})
        for strict, scope in ((True, ''), (True, '合并'), (False, '')):
            start = service.start([filing['doc_id']], 'analysis', {'strict': strict, 'scope': scope, 'kb_lookup': False})
            record = service.runs[start['run_id']]
            record['_thread'].join(30)
            assert record['status'] == 'completed', record
            task = server.state.get_task(record['children'][0]['native_run_id'])
            assert task.request['scope_declaration'] == scope
            payload = json.loads((task.out / 'results.json').read_text(encoding='utf-8'))
            expected = project_analysis(payload['smart_analysis'])
            assert payload['analysis'] == expected
            verified = [r for r in expected['metrics'] if r['status'] == 'verified']
            if strict and not scope:
                assert not verified
                assert expected['metrics'] and all(r['abstention_code'] == 'X3' and 'value' not in r for r in expected['metrics'])
            else:
                revenue = next(r for r in verified if r['metric'] == 'REVENUE' and r['period'] == '2023Y')
                cell = payload['smart_analysis']['metrics'][0]['cells']['2023Y']
                for key in ('value', 'formula', 'operands', 'evidence', 'source_precision', 'rule_ids'):
                    assert revenue[key] == cell[key]
                assert revenue['expected_interval'] == cell['interval']
            facts = [fact_from_dict(row) for row in json.loads((task.out / 'facts.json').read_text(encoding='utf-8'))]
            dispatcher = FinancialDispatcher('analysis', 'lock', [], facts, [], [], '2024-04-01', task_company='甲公司')
            dispatcher.smart_analysis = lambda: analyze(facts, '甲公司', policy='strict' if strict else 'standard', scope=scope)
            dispatcher.trusted_verifier = lambda _: None
            dispatcher.validation_policy = 'strict' if strict else 'standard'
            dispatcher.register_session('coordinator', 'coordinator', [])
            def call(name, params):
                return dispatcher.dispatch({'run_id': 'analysis', 'resource_lock': 'lock', 'session_id': 'coordinator',
                    'role': 'coordinator', 'call_id': name, 'tool_name': name, 'params': params})
            call('mj_list_reports', {})
            call('mj_get_rule', {'rule_id': 'SMART-registered-kernel'})
            result = call('mj_analyze', {'analysis_id': 'financial_summary', 'metric': 'REVENUE', 'period': '2023Y'})
            assert result['analysis'][0]['record']['status'] == ('abstained' if strict and not scope else 'verified')
            if strict and not scope:
                assert 'value' not in result['analysis'][0]['record']
                assert '未计算' in next(iter(result['slots'].values()))
    finally:
        server.server_close()


def test_smart_declared_scope_only_clarifies_missing_source_scope(tmp_path):
    kb, corpus, draft, report = fixture_materials(tmp_path)
    cfg = AppConfig().ensure_defaults().to_dict()
    cfg['workspace']['knowledge_root'] = ''
    request = {'draft': str(draft), 'corpus': str(corpus), 'company': '甲公司', 'period': '2023Y',
               'report_date': '2024-05-01', 'task_type': 'check', 'material_profile': 'smart',
               'validation_policy': 'strict', 'scope_declaration': '合并', 'smart_options': {'use_llm': False}}
    agent = FinancialAgent(request, cfg, lambda _: '', tmp_path / 'native', run_id='scope-check')
    agent.prepare()
    assert agent.claims[0].table_context['scope_assumption'] is False
    assert agent.smart_kernel.verify(agent.claims[0])['status'] == 'verified'
    conflicting = kb.add_bytes('甲公司证券研究报告\n2023年公司母公司营业收入为100.00元。\n'.encode(), 'conflict.txt',
                    hints={'company': '甲公司', 'period': '2023Y', 'report_date': '2024-05-01', 'doc_type': 'research_report'})
    SmartRunAdapter(None, kb, tmp_path / 'runs').freeze([conflicting], tmp_path / 'conflict-draft')
    request['draft'] = str(tmp_path / 'conflict-draft' / (conflicting['doc_id'] + '.txt'))
    with pytest.raises(ValueError, match='口径.*冲突'):
        FinancialAgent(request, cfg, lambda _: '', tmp_path / 'conflict', run_id='conflict').prepare()


def test_smart_batch_schema_and_host_cap_are_sixteen_without_expanding_legacy(tmp_path):
    from mingjing.financial_tools import tool_definitions
    from mingjing.financial_tools import FinancialArgumentError
    from dataclasses import replace
    _, corpus, draft, _ = fixture_materials(tmp_path)
    facts, _ = ingest_facts(corpus, '甲公司', '2023Y')
    text, pages, blocks = partition_smart(draft)
    _, claims = smart_claims(draft, text, pages, blocks, '甲公司', '2023Y', facts, 'standard')
    claims = [replace(claims[0], claim_id=f'c-{index}') for index in range(17)]
    dispatcher = FinancialDispatcher('batch', 'lock', claims, facts, blocks, [], '2024-05-01', task_company='甲公司')
    dispatcher.batch_size = 16
    dispatcher.register_session('planner', 'planner', [c.claim_id for c in claims[:16]])
    bindings = [{'mention_id': c.claim_id, 'binding': c.table_context['smart_binding']} for c in claims]
    def call(values):
        return dispatcher.dispatch({'run_id': 'batch', 'resource_lock': 'lock', 'session_id': 'planner',
            'role': 'planner', 'call_id': 'bind-' + str(len(values)), 'tool_name': 'mj_submit_bindings', 'params': {'bindings': values}})
    assert len(call(bindings[:16])['binding_ids']) == 16
    with pytest.raises(FinancialArgumentError, match='registered batch cap'):
        call(bindings)
    for size in (8, 16):
        schema = next(row['inputSchema'] for row in tool_definitions('planner', size) if row['name'] == 'mj_submit_bindings')
        assert schema['properties']['bindings']['maxItems'] == size
    cfg = AppConfig().ensure_defaults().to_dict()
    assert FinancialAgent({'material_profile': 'smart'}, cfg, lambda _: '', tmp_path / 'smart').binding_group_size == 16
    assert FinancialAgent({}, cfg, lambda _: '', tmp_path / 'legacy').binding_group_size == 8


def test_physical_pdf_lines_keep_duplicate_ticks_at_separate_source_occurrences(monkeypatch):
    from mingjing.smart.docio import Cell, Document, Page, Row
    import mingjing.smart.ingestion as ingestion
    rows = [Row(1, 0, [Cell('100%', 10, 30), Cell('100%', 100, 120)], 10, 20, True),
            Row(1, 1, [Cell('20%', 10, 30), Cell('0%', 100, 120)], 30, 40, True),
            Row(1, 2, [Cell('图1:毛利率', 10, 120)], 50, 60, True)]
    physical = [{'text': '100%', 'bbox': [10, 10, 30, 20]}, {'text': '100%', 'bbox': [100, 10, 120, 20]},
                {'text': '20%', 'bbox': [10, 30, 30, 40]}, {'text': '0%', 'bbox': [100, 30, 120, 40]},
                {'text': '图1:毛利率 ', 'bbox': [10, 50, 120, 60]}]
    document = Document('fixture.pdf', 'fixture.pdf', 'a' * 64, 'pdf', 'fitz', [Page(1, rows, geometry_lines=physical)])
    monkeypatch.setattr(ingestion, 'load_document', lambda _: document)
    text, pages, _ = partition_smart(Path('fixture.pdf'))
    lines = pages[0]['lines']
    assert [line['text'] for line in lines] == ['100%', '100%', '20%', '0%', '图1:毛利率']
    assert len({line['start'] for line in lines}) == len(lines)
    assert all(text[line['start']:line['end']] == line['text'] for line in lines)
    assert lines[3]['start'] > lines[2]['end']


def test_explicit_ticker_suffix_excludes_only_the_security_identifier(tmp_path):
    from mingjing.coverage import CoverageLedger
    path = tmp_path / 'report.txt'
    path.write_text('星河智造(999001.SH)——2024年中报点评\n2024年营业收入999001元。', encoding='utf-8')
    text, pages, blocks = partition_smart(path)
    ledger, claims = CoverageLedger.build(text, pages, blocks, '星河智造', '2024H1', trusted_claims=[])
    identifiers = [row for row in ledger.entries if row.get('reason') == 'source_security_code']
    assert len(identifiers) == 1 and identifiers[0]['status'] == 'rule_excluded'
    assert any(row['status'] == 'pending' and '999001元' in row['text'] for row in ledger.entries)
    assert not claims


def test_pdf_claim_occurrences_keep_direct_and_native_judgement_identical(tmp_path):
    pytest.importorskip('fitz')
    from mingjing.smart.claims import build_report_model
    from mingjing.smart.verify import verify_model
    from mingjing.smart.detect import same_company
    from mingjing.smart.docio import load_document
    samples = Path(__file__).resolve().parents[1] / 'samples' / 'smart'
    kb = KnowledgeBase(tmp_path / 'kb')
    records = [kb.add_file(p) for p in sorted(samples.glob('*.pdf'))]
    report = next((r for r in records if r.get('doc_type') == 'research_report' and r.get('report_date') == '2024-04-25'), None)
    if report is None:
        pytest.skip('fictional PDF materials are not installed')
    filings = [r for r in records if r.get('doc_type') in {'annual_report', 'quarterly_report', 'semiannual_report'} and same_company(r, report)]
    adapter = SmartRunAdapter(None, kb, tmp_path / 'runs')
    adapter.freeze(filings, tmp_path / 'selected')
    adapter.freeze([report], tmp_path / 'draft')
    source = tmp_path / 'draft' / (report['doc_id'] + '.pdf')
    facts, _ = ingest_facts(tmp_path / 'selected', report['company'], '')
    text, pages, blocks = partition_smart(source)
    model, claims = smart_claims(source, text, pages, blocks, report['company'], '', facts, 'standard', metadata=kb.meta(report['doc_id']).to_dict())
    assert len(claims) == len(model.claims), [(c.cid, c.num_text) for c in model.claims if c.cid not in {a.table_context['smart_cid'] for a in claims}]
    assert any(c.table_context.get('source_numeric_fragments') for c in claims)
    index = {r['doc_id'][:12]: {**kb.meta(r['doc_id']).to_dict(), 'name': r['name']} for r in filings}
    kernel = SmartKernel(facts, model, 'standard', index, [], {c.claim_id: c.table_context['smart_claim'] for c in claims})
    direct = {r.cid: r.to_dict() for r in verify_model(facts, model, doc_index=index)}
    for claim in claims:
        native = kernel.verify(claim)['smart_result']
        for field in ('status', 'error_type', 'abstain_code', 'expected', 'definition', 'rule_ids'):
            assert native[field] == direct[native['cid']][field], (native['cid'], field, native, direct[native['cid']])
