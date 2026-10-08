from decimal import Decimal

import pytest

from mingjing.coverage import CoverageLedger, mask_financial_numbers, partition_document
from mingjing.financial_tools import FinancialArgumentError, FinancialDispatcher, ToolDenied
from mingjing.mentions import extract_mentions
from mingjing.models import Fact


def fixture():
    claims = extract_mentions('甲公司2024年营业收入100元，同比增长25%。', '甲公司')
    facts = [Fact('f1', '甲公司', 'REVENUE', '2024Y', value=Decimal('100'), publish_date='2025-03-01')]
    d = FinancialDispatcher('run', 'lock', claims, facts, [], [], '2025-03-31')
    for role in ('planner', 'verifier', 'reporter'):
        d.register_session(role, role)
    return d, claims


def call(d, role, name, params, call_id='c1'):
    return d.dispatch(dict(session_id=role, role=role, run_id='run', resource_lock='lock',
                           call_id=role + ':' + call_id, tool_name=name, params=params))


def test_roles_are_real_identities_and_amounts_cannot_enter_tools():
    d, claims = fixture()
    with pytest.raises(ToolDenied):
        d.dispatch(dict(session_id='planner', role='coordinator', run_id='run', resource_lock='lock',
                        call_id='x', tool_name='mj_verify', params={'binding_id': claims[0].claim_id}))
    with pytest.raises(Exception):
        call(d, 'planner', 'mj_fact_dimensions', {'mention_id': claims[0].claim_id, 'candidate_value': '999999'})
    with pytest.raises(ToolDenied):
        call(d, 'planner', 'mj_get_verdicts', {})
    result = call(d, 'verifier', 'mj_read_context', {'mention_id': claims[0].claim_id})
    assert '100' not in result['text'] and '25%' not in result['text'] and '2024' in result['text']


def test_independent_bindings_and_gate():
    d, claims = fixture()
    mid = claims[0].claim_id
    a = dict(company='甲公司', account='REVENUE', period='2024Y', scope='合并', relation='value', base_period='')
    call(d, 'planner', 'mj_submit_binding', {'mention_id': mid, 'binding': a})
    context = call(d, 'verifier', 'mj_fact_dimensions', {'mention_id': mid})
    assert 'binding' not in str(context) and '100' not in str(context)
    b = dict(a, period='2023Y')
    with pytest.raises(FinancialArgumentError) as caught:
        call(d, 'verifier', 'mj_submit_binding', {'mention_id': mid, 'binding': b}, 'c2')
    rejection = caught.value.rejection()
    assert rejection['error']['code'] == 'binding_source_conflict'
    assert rejection['error']['conflicting_fields'] == ['period']
    assert '100' not in str(rejection) and 'planner' not in str(rejection)
    assert not d.bindings['verifier']
    # The same independent session may decline an uncertain dimension;
    # neither rejection nor A's choice is silently converted into B's choice.
    call(d, 'verifier', 'mj_submit_binding', {
        'mention_id': mid, 'binding': dict(a, period='')}, 'c3')
    bound = d.freeze_bindings()
    assert bound[0].binding_status == 'disagreed'
    assert d.agreements[mid]['differences'] == ['period']


def test_batch_financial_tools_keep_host_values_and_require_dependent_reads():
    d, claims = fixture()
    from dataclasses import replace
    explicit = extract_mentions('甲公司2024年合并营业收入100元，同比增长25%。', '甲公司')[0]
    d.claims[claims[0].claim_id] = replace(explicit, claim_id=claims[0].claim_id)
    mid = claims[0].claim_id
    binding = dict(company='甲公司', account='REVENUE', period='2024Y', scope='合并', relation='value', base_period='')
    for role in ('planner', 'verifier'):
        call(d, role, 'mj_submit_binding', {'mention_id': mid, 'binding': binding}, 'bind')
    d.freeze_bindings()
    d.register_session('coordinator', 'coordinator', [mid])
    with pytest.raises(ToolDenied, match='read facts'):
        call(d, 'coordinator', 'mj_verify_bindings', {'binding_ids': [mid]}, 'before-read')
    with pytest.raises(Exception):
        call(d, 'coordinator', 'mj_get_facts', {'binding_ids': [mid], 'candidate_value':'999999'}, 'forged')
    output = call(d, 'coordinator', 'mj_get_facts', {'binding_ids': [mid]}, 'facts')
    assert output['items'][0]['fact']['value'] == '100'
    call(d, 'coordinator', 'mj_get_rule', {'rule_id':'E1'}, 'rule')
    result = call(d, 'coordinator', 'mj_verify_bindings', {'binding_ids':[mid]}, 'verify')['items'][0]
    assert result['binding_id'] == mid and result['status'] == 'verified'
    assert result['observed'] == result['expected'] == '100'
    with pytest.raises(ToolDenied):
        call(d, 'verifier', 'mj_get_facts', {'binding_ids':[mid]}, 'leak')
    with pytest.raises(ToolDenied, match='scope'):
        call(d, 'coordinator', 'mj_get_facts', {'binding_ids':['foreign']}, 'foreign')


def test_analysis_native_dependencies_and_reporter_read_only_slots(monkeypatch):
    d, _ = fixture()
    d.register_session('coordinator', 'coordinator', [])
    params = {'analysis_id':'financial_summary', 'period':'2024Y', 'metric':'REVENUE'}
    with pytest.raises(ToolDenied, match='read locked reports'):
        call(d, 'coordinator', 'mj_analyze', params, 'no-reads')
    call(d, 'coordinator', 'mj_list_reports', {}, 'reports')
    call(d, 'coordinator', 'mj_get_rule', {'rule_id':'E1'}, 'non-analysis-rule')
    with pytest.raises(ToolDenied, match='authorized financial rule'):
        call(d, 'coordinator', 'mj_analyze', params, 'wrong-rule')
    call(d, 'coordinator', 'mj_get_rule', {'rule_id':'FIN-gross-margin'}, 'rule')
    output = call(d, 'coordinator', 'mj_analyze', params, 'analysis')
    assert output['analysis'][0]['record']['value'] == '100'
    monkeypatch.setattr('mingjing.analysis.analyze_financials', lambda *a, **k: (_ for _ in ()).throw(AssertionError('reporter must not compute')))
    frozen = call(d, 'reporter', 'mj_analyze', params, 'read-frozen')
    assert frozen == output
    accepted = call(d, 'reporter', 'mj_submit_report', {'template':frozen['slot_template'], 'slots':frozen['slot_ids']}, 'report')
    assert accepted['accepted']


def test_resource_and_report_slots_are_enforced():
    d, claims = fixture()
    with pytest.raises(ToolDenied):
        call(d, 'planner', 'mj_read_context', {'mention_id': 'another-company'})
    d.freeze_results([dict(claim_id=claims[0].claim_id, status='verified', evidence=['e'])])
    slot = claims[0].claim_id + '.result'
    call(d, 'reporter', 'mj_get_verdicts', {}, 'read')
    accepted = call(d, 'reporter', 'mj_submit_report', {'template': '{{' + slot + '}}', 'slots': [slot]})
    assert accepted['accepted']
    with pytest.raises(ToolDenied):
        call(d, 'reporter', 'mj_submit_report', {'template': '利润999999元', 'slots': []}, 'c2')
    with pytest.raises(ToolDenied):
        call(d, 'reporter', 'mj_submit_report', {'template': '{{unknown}}', 'slots': ['unknown']}, 'c3')


def test_duplicate_occurrences_and_unprocessed_tables_stay_visible(tmp_path):
    p = tmp_path / 'draft.md'
    p.write_text('甲公司2024年营业收入100元。\n甲公司2024年营业收入100元。\n营业收入\n100 80 20%', encoding='utf-8')
    text, pages, blocks = partition_document(p)
    ledger, claims = CoverageLedger.build(text, pages, blocks, '甲公司', '2024Y')
    assert len([c for c in claims if c.candidate_value == '100']) == 2
    assert ledger.to_dict()['partial']
    assert len(ledger.entries) >= len(claims)
    assert mask_financial_numbers('2024年归母净利润1.23亿元，同比增长20%') == '2024年归母净利润[数值已屏蔽]亿元，同比增长[数值已屏蔽]%'


def test_numbered_claims_do_not_leave_fake_pending_work(tmp_path):
    p = tmp_path/'draft.md'
    p.write_text('1. 甲公司2024年合并营业收入100元。\n2. 甲公司2024年合并营业收入100元。', encoding='utf-8')
    text, pages, blocks = partition_document(p)
    ledger, claims = CoverageLedger.build(text, pages, blocks, '甲公司', '2024Y')
    for c in claims:
        ledger.complete(c.claim_id, dict(status='verified'))
    assert not ledger.to_dict()['partial']
    assert mask_financial_numbers('2023A 2024E\n123\n456') == '2023A 2024E\n[数值已屏蔽]\n[数值已屏蔽]'


def test_call_ids_are_scoped_to_session_and_analysis_answers_use_host_slots():
    d, claims = fixture()
    d.register_session('question', 'question')
    for role in ('planner', 'verifier'):
        d.dispatch(dict(session_id=role, role=role, run_id='run', resource_lock='lock',
                        call_id='shared', tool_name='mj_list_mentions', params={}))
    analysis = call(d, 'question', 'mj_analyze', {'analysis_id': 'financial_summary'})
    assert analysis['slots'] and all(key.startswith('analysis.') for key in analysis['slots'])
    key = next(iter(analysis['slots']))
    reply = call(d, 'question', 'mj_submit_report', {'template': '{{'+key+'}}', 'slots': [key]}, 'answer')
    assert reply['accepted']


def test_batch_bindings_are_atomic_and_do_not_bypass_identity_guards():
    d, claims = fixture()
    a = dict(company='甲公司', account='REVENUE', period='2024Y', scope='合并', relation='value', base_period='')
    batch = [{'mention_id': claims[0].claim_id, 'binding': a}, {'mention_id': claims[1].claim_id, 'binding': dict(a, company='乙公司')}]
    with pytest.raises(ToolDenied):
        call(d, 'planner', 'mj_submit_bindings', {'bindings': batch})
    assert not d.bindings['planner']
    batch[1]['binding'] = dict(a, relation='yoy', base_period='2023Y')
    assert call(d, 'planner', 'mj_submit_bindings', {'bindings': batch}, 'batch2')['accepted']
    assert len(d.bindings['planner']) == 2


def test_verdict_pages_only_return_page_slots_with_resource_scope():
    import json
    from dataclasses import replace
    d, claims = fixture()
    expanded = [replace(claims[0], claim_id=f'c-{i}') for i in range(100)]
    d.claims = {c.claim_id: c for c in expanded}
    d.register_session('question', 'question', list(d.claims))
    d.freeze_results([dict(claim_id=c.claim_id, status='verified', expected='100', evidence=['e']) for c in expanded])
    page = call(d, 'question', 'mj_get_verdicts', {'query': '营业收入'})
    assert page['total_matched'] == 100 and page['next_cursor'] == 5
    assert page['returned'] == 5 and not page['complete']
    assert len(json.dumps(page, ensure_ascii=False).encode('utf-8')) <= 16384
    assert {key.split('.')[0] for key in page['slots']} == {f'c-{i}' for i in range(5)}
    d.sessions['question']['mentions'] = {'c-9'}
    scoped = call(d, 'question', 'mj_get_verdicts', {}, 'scope')
    assert scoped['total_matched'] == 1 and scoped['verdicts'][0]['claim_id'] == 'c-9'
    with pytest.raises(ToolDenied):
        call(d, 'question', 'mj_get_verdicts', {'claim_id': 'c-8'}, 'denied')


def test_analysis_filter_returns_only_requested_metric_and_host_slot():
    d, claims = fixture()
    d.register_session('question', 'question')
    page = call(d, 'question', 'mj_analyze', {'analysis_id': 'financial_summary', 'period': '2024Y', 'metric': 'GROSS_MARGIN'})
    assert page['total_matched'] == 1 and page['returned'] == 1 and len(page['slots']) == 1
    assert page['analysis'][0]['record']['metric'] == 'GROSS_MARGIN'
    assert page['analysis'][0]['record']['period'] == '2024Y'
    with pytest.raises(Exception):
        call(d, 'question', 'mj_analyze', {'analysis_id': 'financial_summary', 'formula': '999999'}, 'denied')


def test_report_slots_require_actual_session_reads_and_mention_scope():
    d, claims = fixture()
    d.freeze_results([dict(claim_id=c.claim_id, status='verified', evidence=['e']) for c in claims])
    slot = claims[0].claim_id+'.detail'
    with pytest.raises(ToolDenied):
        call(d, 'reporter', 'mj_submit_report', {'template': '{{'+slot+'}}', 'slots': [slot]}, 'unread')
    call(d, 'reporter', 'mj_get_verdicts', {'claim_id': claims[0].claim_id}, 'read')
    d.sessions['reporter']['mentions'] = {claims[1].claim_id}
    with pytest.raises(ToolDenied):
        call(d, 'reporter', 'mj_submit_report', {'template': '{{'+slot+'}}', 'slots': [slot]}, 'outside')
    d.register_session('empty', 'question', [])
    assert d.sessions['empty']['mentions'] == set()


def test_blind_target_uses_exact_occurrence_and_preserves_source_definition():
    d, _ = fixture()
    source = '2023年超算云服务毛利率27%，公司整体毛利率27%。'
    a, b = extract_mentions(source, '甲公司', '2023Y', preserve_occurrences=True)
    assert d.binding_context(a).index('[待绑定数值]') < d.binding_context(a).index('公司整体')
    assert d.binding_context(b).index('[待绑定数值]') > d.binding_context(b).index('公司整体')
    assert d.mention_context(a)['source_dimensions']['dimension_kind'] == 'business'
    assert d.mention_context(a)['source_dimensions']['dimension_id'] == '超算云服务'
    assert 'source_definition' not in d.mention_context(a)
    assert d.mention_context(b)['source_dimensions']['dimension_kind'] == 'total'
    assert 'source_definition' not in d.mention_context(b)
    unsupported = extract_mentions('2023年产品A毛利率27%，公司整体毛利率27%。',
                                   '甲公司', '2023Y', preserve_occurrences=True)[0]
    assert d.mention_context(unsupported)['source_definition']['supported'] is False


def test_compact_operands_distinguish_normalized_values_from_source_units():
    row = FinancialDispatcher.compact_operand(dict(account='REVENUE', value='120000000', display_unit='百万元', evidence=['p1']))
    assert row['value_unit'] == row['unit'] == '元'
    assert row['display_unit'] == '百万元' and row['value'] == '120000000'
    assert FinancialDispatcher.compact_operand(dict(account='GROSS_MARGIN', value='0.25', display_unit='%'))['value_unit'] == '无量纲比例'
    assert FinancialDispatcher.compact_operand(dict(account='EPS', value='0.03', display_unit='元'))['value_unit'] == '元/股'


def _table_metadata_fixture():
    from dataclasses import replace
    source = '甲公司2023年营业收入3.2百万元。'
    claim = extract_mentions(source, '甲公司', '2023Y')[0]
    metadata = {
        'title': '合并利润表（毛利率26%）',
        'row_label': '营业收入：3.2百万元',
        'headers': [{'text': '2023A | 2024E | 毛利率26% | 相邻列3.2百万元',
                     'start': 2023, 'end': 2024, 'bbox': [3.2, 26.0, 100, 200]}],
        'neighbour_cells': {'texts': ['3.2', '3.2百万元', '26%', '3.2'],
                            'periods': ('2023A', '2024E')},
        'column_index': 1, 'period_source': 'source_header', 'account_hint': 'REVENUE',
        'period_hint': '2023Y', 'scope_hint': '合并', 'unit_hint': '百万元',
    }
    claim = replace(claim, claim_id='table-metadata', table_context=metadata)
    dispatcher = FinancialDispatcher('run', 'lock', [claim], [], [], [], '2024-04-22')
    for role in ('planner', 'verifier', 'question'):
        dispatcher.register_session(role, role)
    return dispatcher, claim


def test_both_blind_roles_mask_source_table_metadata_through_public_tools():
    import copy
    dispatcher, claim = _table_metadata_fixture()
    original = copy.deepcopy(claim.table_context)
    frozen_original = copy.deepcopy(dispatcher.claims[claim.claim_id].table_context)
    observed = {}
    for role in ('planner', 'verifier'):
        listed = call(dispatcher, role, 'mj_list_mentions', {}, 'list')['mentions'][0]
        read = call(dispatcher, role, 'mj_read_context', {'mention_id': claim.claim_id}, 'read')
        observed[role] = listed
        for response in (listed, read):
            metadata = response['table_context']
            assert metadata['title'] == '合并利润表（毛利率[数值已屏蔽]%）'
            assert metadata['row_label'] == '营业收入：[数值已屏蔽]百万元'
            assert metadata['headers'][0]['text'] == '2023A | 2024E | 毛利率[数值已屏蔽]% | 相邻列[数值已屏蔽]百万元'
            assert '3.2' not in response['text']
            assert read['source_dimensions']['period_header'] == '2023Y'
        assert read['table_context']['neighbour_cells']['texts'] == [
            '[数值已屏蔽]', '[数值已屏蔽]百万元', '[数值已屏蔽]%', '[数值已屏蔽]']
        assert 'neighbour_cells' not in listed['table_context']
        assert set(listed['table_context']['headers'][0]) == {'text'}
        assert listed['source_dimensions'] == read['source_dimensions']
    assert observed['planner'] == observed['verifier']
    assert claim.table_context == original
    assert dispatcher.claims[claim.claim_id].table_context == frozen_original


def test_blind_table_metadata_keeps_dimensions_and_does_not_alias_frozen_source():
    import copy
    dispatcher, claim = _table_metadata_fixture()
    original = copy.deepcopy(claim.table_context)
    metadata = call(dispatcher, 'planner', 'mj_read_context', {'mention_id': claim.claim_id})['table_context']
    assert metadata['headers'][0]['start'] == 2023
    assert metadata['headers'][0]['end'] == 2024
    assert metadata['headers'][0]['bbox'] == [3.2, 26.0, 100, 200]
    assert metadata['neighbour_cells']['periods'] == ('2023A', '2024E')
    for field in ('column_index', 'period_source', 'account_hint', 'period_hint', 'scope_hint', 'unit_hint'):
        assert metadata[field] == original[field]
    metadata['headers'][0]['text'] = 'changed model-facing copy'
    metadata['headers'][0]['bbox'][0] = -1
    metadata['neighbour_cells']['texts'][0] = 'changed nested copy'
    assert claim.table_context == original


def test_nonblind_question_reads_original_table_metadata_without_redaction():
    import copy
    dispatcher, claim = _table_metadata_fixture()
    unfrozen = copy.deepcopy(claim.table_context)
    original = copy.deepcopy(dispatcher.claims[claim.claim_id].table_context)
    response = call(dispatcher, 'question', 'mj_read_context', {'mention_id': claim.claim_id})
    assert response['text'] == claim.text
    assert response['table_context'] == original
    assert dispatcher.mention_context(dispatcher.claims[claim.claim_id], blind=False)['table_context'] == original
    assert claim.table_context == unfrozen


def test_confirmed_table_cells_equal_to_years_are_hidden_but_headers_remain():
    from mingjing.table_mentions import extract_table_mentions
    source = '合并利润表(元)\n2023A\n2024E\n营业收入\n100\n2023\n'
    pages = [{'start': 0, 'end': len(source), 'page': 1}]
    claims = extract_table_mentions(source, pages, [], '甲公司', '2023Y')
    assert len(claims) == 2 and claims[1].numeric_text == '2023'
    original = [claim.to_dict() for claim in claims]
    dispatcher = FinancialDispatcher('run', 'lock', claims, [], [], [], '2024-04-22')
    for role in ('planner', 'verifier', 'question'):
        dispatcher.register_session(role, role)
    contexts = {}
    for role in ('planner', 'verifier'):
        contexts[role] = call(dispatcher, role, 'mj_list_mentions', {}, 'list')['mentions']
        for index, claim in enumerate(claims):
            context = contexts[role][index]
            read = call(dispatcher, role, 'mj_read_context', {'mention_id': claim.claim_id}, f'read-{index}')
            assert '100' not in context['text'] and '2023' not in context['text']
            assert context['text'].count('[待绑定数值]') == 1
            assert context['text'].count('[数值已屏蔽]') == 1
            assert read['text'] == context['text']
            assert [header['text'] for header in context['table_context']['headers']] == ['2023A', '2024E']
            assert context['table_context']['period_hint'] == claim.period
    assert contexts['planner'] == contexts['verifier']
    read = call(dispatcher, 'question', 'mj_read_context', {'mention_id': claims[0].claim_id})
    assert '100' in read['text'] and '2023' in read['text']
    assert [claim.to_dict() for claim in claims] == original


def test_blind_block_read_and_search_hide_ambiguous_year_amounts_for_both_roles():
    rows = '合并利润表(元)\n2023A\n2024E\n营业收入\n100\n2023\n'
    blocks = [{'id': 'full-row', 'text': rows, 'page': 1},
              {'id': 'isolated-cell', 'text': '2023', 'page': 1}]
    dispatcher = FinancialDispatcher('run', 'lock', [], [], blocks, [], '2024-04-22')
    for role in ('planner', 'verifier', 'question'):
        dispatcher.register_session(role, role)
    outputs = {}
    for role in ('planner', 'verifier'):
        search = call(dispatcher, role, 'mj_search_blocks', {'query': '2023'}, 'search')
        outputs[role] = search
        assert search[0]['text'] == '合并利润表(元)\n2023A\n2024E\n营业收入\n[数值已屏蔽]\n[数值已屏蔽]\n'
        assert search[1]['text'] == '[数值已屏蔽]'
        for hit in search:
            read = call(dispatcher, role, 'mj_read_context', {'mention_id': hit['block_id']}, hit['block_id'])
            assert read['text'] == hit['text']
    assert outputs['planner'] == outputs['verifier']
    for block in blocks:
        read = call(dispatcher, 'question', 'mj_read_context', {'mention_id': block['id']}, block['id'])
        assert read['text'] == block['text']
    assert blocks[0]['text'] == rows and blocks[1]['text'] == '2023'


def test_conservative_blind_mask_keeps_explicit_periods_dates_and_year_ranges():
    source = '2023-04-22 2023年12月31日 2023Q1 2024H1 2023A 2024E 2018-2023 裸值2023 2024 营收2023元'
    masked = mask_financial_numbers(source, preserve_bare_years=False)
    assert masked == ('2023-04-22 2023年12月31日 2023Q1 2024H1 2023A 2024E 2018-2023 '
                      '裸值[数值已屏蔽] [数值已屏蔽] 营收[数值已屏蔽]元')
    assert mask_financial_numbers('2023 2024') == '2023 2024'  # Existing callers keep their old behavior.


def test_only_recognized_period_headers_keep_bare_years_in_blind_metadata():
    from dataclasses import replace
    dispatcher, claim = _table_metadata_fixture()
    metadata = dict(claim.table_context, title='合并利润表金额2023', row_label='营业收入金额2023',
                    headers=[{'text': '2023 | 2024', 'start': 5, 'end': 16},
                             {'text': '营业收入金额2023', 'start': 17, 'end': 29}])
    dispatcher.claims[claim.claim_id] = replace(claim, table_context=metadata)
    original = dispatcher.claims[claim.claim_id].to_dict()
    for role in ('planner', 'verifier'):
        context = call(dispatcher, role, 'mj_read_context', {'mention_id': claim.claim_id})['table_context']
        assert context['headers'][0]['text'] == '2023 | 2024'
        assert context['headers'][1]['text'] == '营业收入金额[数值已屏蔽]'
        assert context['title'] == '合并利润表金额[数值已屏蔽]'
        assert context['row_label'] == '营业收入金额[数值已屏蔽]'
    assert dispatcher.claims[claim.claim_id].to_dict() == original


def test_binding_tools_use_source_vocabulary_without_defaulting_scope():
    from dataclasses import replace
    from mingjing.mentions import extract_mentions
    claims = extract_mentions('2023年公司扣非净利润为1.20亿元。', '甲公司', '2023Y')
    claim = replace(claims[0], caliber_hint=('合并', '扣非'))
    dispatcher = FinancialDispatcher('run', 'lock', [claim], [], [], [], '2024-04-22')
    for role in ('planner', 'verifier'):
        dispatcher.register_session(role, role)
        listed = call(dispatcher, role, 'mj_list_mentions', {}, 'list')
        item = listed['mentions'][0]
        assert item['account_labels'] == {'扣非净利润': 'NP_PARENT_ADJ'}
        assert item['source_dimensions']['scope_header'] == ''
        assert 'accounts' not in listed
        dimensions = call(dispatcher, role, 'mj_fact_dimensions', {'mention_id': claim.claim_id}, 'dims')
        assert dimensions['accounts'] == ['NP_PARENT_ADJ']
        assert '1.20' not in str(item) and '120000000' not in str(dimensions)


def test_compact_binding_list_does_not_duplicate_large_source_coordinates():
    import json
    dispatcher, claim = _table_metadata_fixture()
    claim = dispatcher.claims[claim.claim_id]
    claim.table_context['source_spans'] = [{'start': n, 'end': n+5, 'bbox': [1, 2, 3, 4]}
                                           for n in range(500)]
    listed = call(dispatcher, 'planner', 'mj_list_mentions', {}, 'list')['mentions'][0]
    expanded = call(dispatcher, 'planner', 'mj_read_context', {'mention_id': claim.claim_id}, 'read')
    assert len(json.dumps(listed)) < len(json.dumps(expanded)) / 5
    assert len(expanded['table_context']['source_spans']) == 500
    assert listed['table_context']['period_hint'] == expanded['table_context']['period_hint']


@pytest.mark.parametrize('source,scope', [
    ('2023年归属于母公司股东的净利润为100元', '合并'),
    ('2023年归属母公司净利润为100元', '合并'),
    ('2023年母公司净利润为100元', '母公司'),
    ('2023年合并营业收入为100元', '合并'),
    ('2023年合并及母公司报表营业收入为100元', ''),
    ('2023年母公司及合并报表营业收入为100元', ''),
    ('2023年未按合并口径披露营业收入100元', ''),
    ('2023年不是母公司口径营业收入100元', ''),
])
def test_scope_evidence_does_not_confuse_parent_attribution_with_parent_statement(source, scope):
    claims = extract_mentions(source, '甲公司', '2023Y')
    assert FinancialDispatcher.source_dimensions(claims[0])['scope_header'] == scope


def test_two_binders_cannot_invent_a_source_scope_by_agreeing():
    d, claims = fixture()
    mid = claims[0].claim_id
    binding = dict(company='甲公司', account='REVENUE', period='2024Y', scope='合并', relation='value', base_period='')
    for role in ('planner','verifier'):
        call(d, role, 'mj_submit_binding', {'mention_id':mid,'binding':binding})
    d.freeze_bindings()
    assert d.agreements[mid]['differences'] == []
    assert d.agreements[mid]['scope_source_proven'] is False
    assert d.agreements[mid]['agreed'] is False
