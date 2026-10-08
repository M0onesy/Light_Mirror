"""Exact source definition ranking and answer scope, no independent gold/API."""
from pathlib import Path

import pytest

from mingjing.financial_tools import FinancialDispatcher, ToolDenied
from mingjing.knowledge import KnowledgeRegistry, KnowledgeTools, definition_subject_matches
from test_knowledge_materials import HEADER


TERM = '追溯调整法'
DEFINITION = '追溯调整法，是指对某项交易或事项变更会计政策，视同该项交易或事项初次发生时即采用变更后的会计政策，并以此对财务报表相关项目进行调整的方法。'
APPLICATION = '对合同组采用追溯调整法不切实可行的，企业可以采用修正追溯调整法。修正追溯调整法，是指以获得接近追溯调整法结果为目标的简化方法。'


def registry(tmp_path, *, with_definition=True):
    root, cache = tmp_path/'kb', tmp_path/'cache'
    root.mkdir()
    (root/'insurance.md').write_text(HEADER+APPLICATION*5, encoding='utf-8')
    if with_definition:
        (root/'policy.md').write_text(HEADER+'会计政策变更应当追溯调整。'+DEFINITION, encoding='utf-8')
    return KnowledgeRegistry(root,cache).scan()


@pytest.mark.parametrize('text,term', [
    (DEFINITION, TERM), ('会计政策变更按本准则处理。'+DEFINITION, TERM),
    (DEFINITION.replace('调整法','调\n整法'), TERM),
    ('术语：追溯调整法定义为依法进行的调整。', TERM),
    ('追溯调整法指的是列报方法。', TERM),
    ('EPS，是指每股收益。', 'EPS'),
])
def test_complete_source_subject_is_definition(text, term):
    assert definition_subject_matches(text,term)


@pytest.mark.parametrize('text', [
    APPLICATION, '修正'+DEFINITION, '修正\n'+DEFINITION,
    '未来适用法，是指不使用追溯调整法的方法。',
    '企业可以采用追溯调整法处理。', '会计政策变更必须采用'+TERM+'，是指相关要求。',
])
def test_modified_or_incidental_term_is_not_base_definition(text):
    assert not definition_subject_matches(text,TERM)


def test_exact_definition_outranks_long_high_frequency_insurance_application(tmp_path):
    store=registry(tmp_path)
    found=store.search(TERM,as_of_date='2024-04-22',limit=1)
    assert found['hits'][0]['text'].endswith(DEFINITION)
    assert found['total_matches']==2 and found['next_cursor']==1
    assert not found['definition_request']


@pytest.mark.parametrize('query,intent', [(TERM+' 定义','search'),(TERM,'definition')])
def test_definition_query_returns_only_exact_term_subject(tmp_path,query,intent):
    found=registry(tmp_path).search(query,as_of_date='2024-04-22',intent=intent)
    assert found['definition_request'] and found['definition_terms']==[TERM]
    assert found['total_matches']==1
    assert found['hits'][0]['text'].endswith(DEFINITION)
    assert found['no_match_reason'] is None


def test_space_separated_terms_match_and_without_concatenation(tmp_path):
    found=registry(tmp_path).search('会计政策变更 追溯调整',as_of_date='2024-04-22')
    assert found['total_matches']==1 and found['query_terms']==['会计政策变更','追溯调整']
    assert found['hits'][0]['text'].endswith(DEFINITION)


def test_unknown_chinese_segmentation_is_not_guessed(tmp_path):
    found=registry(tmp_path).search('追溯调整法定义',as_of_date='2024-04-22')
    assert found['query_terms']==['追溯调整法定义'] and not found['definition_request'] and not found['hits']


def test_definition_missing_is_explicit_and_does_not_fall_back_to_application(tmp_path):
    found=registry(tmp_path,with_definition=False).search(TERM,as_of_date='2024-04-22',intent='definition')
    assert found['hits']==[] and found['total_matches']==0 and found['no_match_reason']=='no_definition_match'


@pytest.mark.parametrize('publish', ['','2025-01-01'])
def test_definition_does_not_override_disclosure_guard(tmp_path,publish):
    store=registry(tmp_path)
    source=store.root/'policy.md'
    original=source.read_text(encoding='utf-8')
    source.write_text(original.replace('- 发布：2024-01-01','- 发布：'+publish),encoding='utf-8')
    store=KnowledgeRegistry(store.root,store.cache_root).scan()
    found=store.search(TERM,as_of_date='2024-04-22',intent='definition')
    assert not found['hits'] and found['unavailable_document_count']==1


def dispatcher(store):
    d=FinancialDispatcher('run','lock',[],[],[],[],'2024-04-22',task_company='甲公司',knowledge_registry=store)
    d.register_session('q','question')
    return d


def call(d,name,params=None,sid='q',role='question'):
    return d.dispatch(dict(session_id=sid,role=role,run_id='run',resource_lock='lock',
                           call_id='call-'+str(len(d.calls)),tool_name=name,params=params or {}))


def test_definition_submit_rejects_modified_term_then_accepts_exact_source(tmp_path):
    store=registry(tmp_path); d=dispatcher(store)
    definition=call(d,'mj_kb_search',{'query':TERM,'intent':'definition'})
    insurance_block=next(block for block in store.blocks.values() if APPLICATION in block['text'])
    incidental=call(d,'mj_kb_read',{'block_id':insurance_block['block_id']})
    with pytest.raises(ToolDenied,match='exact subject definition'):
        call(d,'mj_submit_report',{'template':incidental['slot_template'],'slots':incidental['slot_ids']})
    assert call(d,'mj_submit_report',{'template':definition['slot_template'],'slots':definition['slot_ids']})['accepted']
    assert DEFINITION in d.templates['question'] and 'insurance.md' not in d.templates['question']


def test_missing_definition_can_finish_with_guarded_diagnostic_and_is_per_turn(tmp_path):
    d=dispatcher(registry(tmp_path,with_definition=False))
    missing=call(d,'mj_kb_search',{'query':TERM,'intent':'definition'})
    assert missing['reason']=='no_definition_match' and '完整定义' in d.slots[missing['slot_ids'][0]]
    assert call(d,'mj_submit_report',{'template':missing['slot_template'],'slots':missing['slot_ids']})['accepted']
    d.register_session('next','question')
    application=call(d,'mj_kb_search',{'query':TERM},sid='next')
    assert 'definition_terms' not in d.sessions['next']
    assert call(d,'mj_submit_report',{'template':application['slot_template'],'slots':application['slot_ids']},sid='next')['accepted']


def test_blind_roles_keep_opaque_definition_ids_and_mask_values(tmp_path):
    store=registry(tmp_path)
    source=store.root/'policy.md'
    source.write_text(source.read_text(encoding='utf-8')+'金额100万元未核查。',encoding='utf-8')
    store=KnowledgeRegistry(store.root,store.cache_root).scan()
    tools=KnowledgeTools(store,run_id='run',resource_lock='lock',as_of_date='2024-04-22')
    tools.register_session('a','planner')
    result=tools.dispatch(dict(session_id='a',role='planner',run_id='run',resource_lock='lock',
                               knowledge_lock=store.resource_lock,call_id='c',tool_name='mj_kb_search',
                               params={'query':TERM,'intent':'definition'}))
    assert result['hits'][0]['block_id'] in store.blocks
    assert result['hits'][0]['evidence_id']==store.blocks[result['hits'][0]['block_id']]['evidence_id']
    assert '100万元' not in str(result) and not result['hits'][0]['authorizes_judgement']
