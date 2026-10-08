"""Source retrieval returns actionable, bounded, blind and scoped observations."""
import copy
import json

import pytest

from mingjing.financial_tools import ToolDenied, tool_definitions
from test_agent_guards import call
from test_question_source_slots import source_dispatcher


def task(tmp_path, text):
    dispatcher, report, blocks = source_dispatcher(tmp_path, text)
    for role in ('planner', 'verifier', 'coordinator'):
        dispatcher.register_session(role, role)
    return dispatcher, report, blocks


def test_coordinator_context_blocks_reads_prior_units_and_following_rows_verbatim(tmp_path):
    text = '合并利润表\n金额单位：元\n2024年 2023年\n营业收入 100.00 80.00\n净利润 20.00 15.00\n下一节说明。'
    d, _, blocks = task(tmp_path, text)
    row = next(block for block in blocks if block['text'].startswith('营业收入'))
    before = copy.deepcopy(d.store.to_list()), copy.deepcopy(d.results)
    output = call(d, 'coordinator', 'mj_read_context', {'mention_id': row['id'], 'context_blocks': 4}, 'read')
    context = '\n'.join(item['text'] for item in output['related_contexts'])
    assert '金额单位：元' in context and '2024年 2023年' in context and '净利润 20.00 15.00' in context
    assert output['text'] == row['text'] and output['requested_context_blocks'] == 4
    assert output['context_scope'] == 'same_document_and_page'
    assert len(output['source_references']) == len(blocks)
    assert all(reference['reference_status'] == 'unverified_source_quotation' for reference in output['source_references'])
    assert (d.store.to_list(), d.results) == before


def test_explicit_zero_reads_only_the_anchor(tmp_path):
    d, _, blocks = task(tmp_path, '标题\n本公司披露营业收入100万元。\n下一行原文20万元。')
    output = call(d, 'coordinator', 'mj_read_context', {'mention_id': blocks[1]['id'], 'context_blocks': 0}, 'read')
    assert output['related_contexts'] == [] and len(output['source_references']) == 1


def test_blank_PDF_blocks_do_not_consume_context_budget_or_become_false_missing_material(tmp_path):
    d, _, blocks = task(tmp_path, '单位：元\n2024年 2023年\n营业收入100元。\n下一节正文说明。')
    row = blocks[2]
    for index in range(8):
        d.blocks[f'blank-{index}'] = {'id': f'blank-{index}', 'page': row['page'], 'report_id': row['report_id'],
                                    'start': row['start'] - (index + 1) / 10, 'text': ' \n '}
    output = call(d, 'coordinator', 'mj_read_context', {'mention_id': row['id'], 'context_blocks': 2}, 'read')
    assert '单位：元' in '\n'.join(item['text'] for item in output['related_contexts'])
    assert all(isinstance(item['text'], str) and item['text'].strip() for item in output['related_contexts'])
    assert output['context_omissions'] == []


@pytest.mark.parametrize('role', ['planner', 'verifier'])
def test_blind_context_masks_neighbor_financial_values_and_keeps_explicit_period_labels(tmp_path, role):
    d, _, blocks = task(tmp_path, '单位：万元\n2024A 2023A\n营业收入987654.00万元\n净利润123456.00万元\n')
    row = next(block for block in blocks if '营业收入' in block['text'])
    output = call(d, role, 'mj_read_context', {'mention_id': row['id'], 'context_blocks': 2}, 'read')
    rendered = json.dumps(output, ensure_ascii=False)
    assert '987654' not in rendered and '123456' not in rendered and '2024A' in rendered
    assert '[数值已屏蔽]' in output['text'] and not output.get('source_references')
    assert not d.source_references


def test_context_never_crosses_source_or_page_boundaries(tmp_path):
    d, _, blocks = task(tmp_path, '单位：元\n营业收入100元。\n净利润20元。')
    original = blocks[1]
    d.blocks['other-document'] = {'id': 'other-document', 'text': '秘密987654元', 'page': 1,
        'report_id': 'foreign', 'start': original['start'] - 1, 'end': original['start']}
    d.blocks['other-page'] = {'id': 'other-page', 'text': '秘密123456元', 'page': 2,
        'report_id': original['report_id'], 'start': original['start'] - 1, 'end': original['start']}
    output = call(d, 'coordinator', 'mj_read_context', {'mention_id': original['id'], 'context_blocks': 16}, 'read')
    assert '秘密' not in json.dumps(output, ensure_ascii=False)
    assert {item['block_id'] for item in output['related_contexts']} == {blocks[0]['id'], blocks[2]['id']}


def test_multiword_AND_matches_one_block_without_requiring_adjacency(tmp_path):
    d, _, blocks = task(tmp_path, '合并利润表采用人民币为金额单位，营业收入见本行。\n仅合并利润表标题\n仅营业收入行100元。')
    output = call(d, 'coordinator', 'mj_search_blocks', {'query': '合并利润表 营业收入', 'context_blocks': 0}, 'and')
    assert len(output) == 1 and output[0]['block_id'] == blocks[0]['id']
    assert output[0]['match_policy'] == 'AND_same_original_block' and output[0]['total_matches'] == 1


def test_AND_cannot_join_title_and_numeric_rows_to_invent_a_match(tmp_path):
    d, _, _ = task(tmp_path, '合并利润表\n单位：元\n营业收入100元。')
    output = call(d, 'coordinator', 'mj_search_blocks', {'query': '合并利润表 营业收入'}, 'and')[0]
    assert output['found'] is False and output['reason'] == 'source_search_no_match'
    assert output['total_matches'] == 0 and output['next_cursor'] is None
    assert output['search_status'] == 'no_match' and output['material_availability'] == 'not_inferred'
    assert '不证明材料不存在' in output['instruction'] and '前后上下文' in output['instruction']


def test_pdf_whitespace_normalization_preserves_original_quotation(tmp_path):
    text = '本公司披露营业 收入，并说明了确认方式。'
    d, _, blocks = task(tmp_path, text)
    output = call(d, 'coordinator', 'mj_search_blocks', {'query': '营业收入', 'context_blocks': 0}, 'search')[0]
    assert output['text'] == blocks[0]['text'] and '营业 收入' in output['text']


def test_question_no_match_does_not_claim_registered_document_is_missing(tmp_path):
    d, _, _ = task(tmp_path, '合并利润表\n单位：元\n营业收入100元。')
    output = call(d, 'question', 'mj_search_blocks', {'query': '合并利润表 营业收入'}, 'and')[0]
    assert output['search_status'] == 'no_match' and output['material_availability'] == 'not_inferred'
    assert '不能据此判断锁定材料缺失' in d.slots[output['slot_ids'][0]]


def test_coordinator_pagination_reaches_late_source_without_duplicate_or_silent_truncation(tmp_path):
    text = '\n'.join(f'本公司营业收入原文第{number}行。' for number in range(15))
    d, _, blocks = task(tmp_path, text)
    first = call(d, 'coordinator', 'mj_search_blocks', {'query': '营业收入', 'limit': 10, 'context_blocks': 0}, 'first')
    assert len(first) == 10 and first[0]['total_matches'] == 15 and first[0]['next_cursor'] == 10
    second = call(d, 'coordinator', 'mj_search_blocks', {'query': '营业收入', 'limit': 10, 'cursor': 10, 'context_blocks': 0}, 'second')
    assert len(second) == 5 and second[0]['next_cursor'] is None
    assert {row['block_id'] for row in first + second} == {block['id'] for block in blocks}
    exhausted = call(d, 'coordinator', 'mj_search_blocks', {'query': '营业收入', 'cursor': 15}, 'exhausted')[0]
    assert exhausted['reason'] == 'source_search_cursor_exhausted' and exhausted['total_matches'] == 15


def test_equivalent_repeat_stops_coordinator_but_new_session_is_not_exhausted(tmp_path):
    d, _, _ = task(tmp_path, '本公司营业收入确认政策适用合并利润表。')
    first = call(d, 'coordinator', 'mj_search_blocks', {'query': '营业收入 合并利润表', 'context_blocks': 0}, 'first')
    repeat = call(d, 'coordinator', 'mj_search_blocks', {'query': '合并利润表  营业收入', 'context_blocks': 0}, 'repeat')
    assert first[0].get('block_id') and repeat[0]['stopped'] and repeat[0]['reason'] == 'no_new_information'
    d.register_session('second-coordinator', 'coordinator')
    new = d.dispatch({'session_id': 'second-coordinator', 'role': 'coordinator', 'run_id': 'run',
        'resource_lock': 'lock', 'call_id': 'new', 'tool_name': 'mj_search_blocks',
        'params': {'query': '营业收入 合并利润表', 'context_blocks': 0}})
    assert new[0]['block_id'] == first[0]['block_id'] and not new[0].get('stopped')


def test_alternative_query_returning_same_verbatim_source_stops_without_new_information(tmp_path):
    d, _, _ = task(tmp_path, '本公司营业收入确认政策适用合并利润表。')
    first = call(d, 'coordinator', 'mj_search_blocks', {'query': '营业收入', 'context_blocks': 0}, 'first')
    second = call(d, 'coordinator', 'mj_search_blocks', {'query': '合并利润表', 'context_blocks': 0}, 'second')
    assert first[0].get('block_id') and second[0]['stopped'] and second[0]['reason'] == 'no_new_information'


@pytest.mark.parametrize('date', ['', '2025-04-01', 'bad-date'])
def test_cutoff_and_unknown_publication_hide_source_for_coordinator_and_blind_roles(tmp_path, date):
    d, _, blocks = task(tmp_path, '本公司营业收入秘密数值987654元。')
    d.source_resources[blocks[0]['report_id']]['publish_date'] = date
    for role in ('coordinator', 'planner', 'verifier'):
        searched = call(d, role, 'mj_search_blocks', {'query': '营业收入'}, 'search')[0]
        direct = call(d, role, 'mj_read_context', {'mention_id': blocks[0]['id'], 'context_blocks': 4}, 'direct')
        assert searched['found'] is False and '秘密' not in json.dumps([searched, direct], ensure_ascii=False)
        assert not direct.get('source_references')


@pytest.mark.parametrize('params', [{'query': '收入', 'cursor': -1}, {'query': '收入', 'cursor': True},
                                   {'query': '收入', 'context_blocks': 3}, {'query': '收入', 'limit': False}])
def test_pagination_and_context_parameter_types_are_enforced(tmp_path, params):
    d, _, _ = task(tmp_path, '收入100元。')
    with pytest.raises(Exception):
        call(d, 'coordinator', 'mj_search_blocks', params, 'invalid')


def test_same_role_search_history_is_restorable_without_dynamic_identity_fields(tmp_path):
    d, _, _ = task(tmp_path, '本公司营业收入说明。')
    call(d, 'coordinator', 'mj_search_blocks', {'query': '营业收入'}, 'search')
    restored = {tuple(row) for row in json.loads(json.dumps(sorted(list(row) for row in d.searches)))}
    assert restored == d.searches and all(len(row) == 2 for row in restored)
    d.searches = restored
    assert call(d, 'coordinator', 'mj_search_blocks', {'query': '营业收入'}, 'again')[0]['stopped']


def test_public_native_tool_descriptions_disclose_query_context_and_pagination_contract():
    tools = {tool['name']: tool for tool in tool_definitions('coordinator')}
    search = tools['mj_search_blocks']
    assert 'AND' in search['description'] and '不证明材料缺失' in search['description']
    properties = search['inputSchema']['properties']
    assert 'next_cursor' in properties['cursor']['description']
    assert '同文档同页前后' in properties['context_blocks']['description']
    assert '同一个原文块' in properties['query']['description']
