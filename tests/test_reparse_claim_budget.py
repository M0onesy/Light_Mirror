"""A missing mention cannot reset local-PDF attempt budgets by changing blocks."""
from pathlib import Path

import pytest

from mingjing.agent import write_json
from mingjing.cli import build_parser, command_check
from mingjing.coverage import partition_document
from mingjing.reparse import freeze_staged_reparse_packets, resolve_reparse_packet
from mingjing.reparse_inputs import load_parent_context

from test_agent_guards import call
from test_agent_lifecycle import frozen_bytes, lifecycle, new_agent, read
from test_reparse_packets import dispatcher, source


def test_moving_to_other_blocks_or_sessions_cannot_bypass_two_attempt_claim_limit(source):
    d=dispatcher(source)
    d.reparse_source_generation=None
    mid=source['claim'].claim_id
    call(d,'coordinator','mj_get_fact',{'binding_id':mid},'facts')
    call(d,'coordinator','mj_get_rule',{'rule_id':'E1-value'},'rule')
    verdict=call(d,'coordinator','mj_verify_bindings',{'binding_ids':[mid]},'verify')['items'][0]
    assert verdict['status']=='abstained' and verdict['abstention_code']=='X1'
    _,_,blocks=partition_document(Path(source['source_resources'][0]['path']))
    rid=source['block']['report_id']
    anchors=[]
    for block in blocks:
        if block['page']==3:
            original=dict(block,id=rid+':'+block['id'],report_id=rid)
            d.blocks[original['id']]=original
            anchors.append(original['id'])
    assert len(anchors)>=3
    first=call(d,'coordinator','mj_request_reparse',{'block_id':anchors[0],'binding_id':mid},'first')
    assert first['status']=='staged' and first['claim_attempt']==1
    repeated=call(d,'coordinator','mj_request_reparse',{'block_id':anchors[0],'binding_id':mid},'repeat')
    assert repeated['reason']=='no_new_information' and d.reparse_claim_attempts[mid]==1
    second=call(d,'coordinator','mj_request_reparse',{'block_id':anchors[1],'binding_id':mid},'second')
    assert second['status']=='staged' and second['claim_attempt']==2 and second['remaining_claim_attempts']==0
    third=call(d,'coordinator','mj_request_reparse',{'block_id':anchors[2],'binding_id':mid},'third')
    assert third['status']=='stopped' and third['reason']=='retry_budget_exhausted'
    d.register_session('other','coordinator',[mid])
    identity=d.sessions['other']
    identity['facts_missing']={mid}
    cross=d.dispatch({'run_id':'run','resource_lock':'lock','session_id':'other','role':'coordinator',
        'call_id':'other','tool_name':'mj_request_reparse','params':{'block_id':anchors[2],'binding_id':mid}})
    assert cross['reason']=='retry_budget_exhausted'
    assert d.store.to_list()==[row.to_dict() for row in source['baseline_facts']]


def test_claim_attempt_counter_survives_fresh_owner_checkpoint_resume(lifecycle):
    original=new_agent(lifecycle)
    data=original.run()
    assert data['model']['status']=='cancelled'
    state=read(lifecycle.out/'agent_state.json')
    mid=next(iter(original.dispatcher.claims))
    # Simulate a durable checkpoint after two host attempts, then construct a
    # different owner. This is lifecycle validation, not a native/API score.
    state['reparse_claim_attempts']={mid:2}
    write_json(lifecycle.out/'agent_state.json',state)
    resumed=new_agent(lifecycle)
    assert not hasattr(resumed, 'dispatcher')
    result=resumed.run(resume=True)
    assert result['model']['status']=='success'
    assert resumed.dispatcher.reparse_claim_attempts=={mid:2}
    assert read(lifecycle.out/'agent_state.json')['reparse_claim_attempts']=={mid:2}


@pytest.mark.parametrize('bad', [True, -1, 3, 1.0, '1'])
def test_invalid_claim_counters_cannot_publish_a_resumed_checkpoint(lifecycle, bad):
    owner = new_agent(lifecycle)
    owner.run()
    state = read(lifecycle.out/'agent_state.json')
    state['reparse_claim_attempts'] = {next(iter(owner.dispatcher.claims)): bad}
    write_json(lifecycle.out/'agent_state.json', state)
    before = frozen_bytes(lifecycle.out)
    with pytest.raises(ValueError, match='Invalid reparse budget'):
        new_agent(lifecycle).run(resume=True)
    assert frozen_bytes(lifecycle.out) == before


def test_recorded_successful_reparse_cannot_have_its_claim_counter_lowered(source):
    d = dispatcher(source)
    mid, block_id = source['claim'].claim_id, source['block']['id']
    call(d, 'coordinator', 'mj_get_fact', {'binding_id': mid}, 'lookup')
    call(d, 'coordinator', 'mj_request_reparse', {'block_id': block_id, 'binding_id': mid}, 'parse')
    d.restore_reparse_budgets({block_id: 1}, {mid: 1})
    with pytest.raises(ValueError, match='reset consumed'):
        d.restore_reparse_budgets({block_id: 1}, {mid: 0})
    with pytest.raises(ValueError, match='Invalid reparse budget'):
        d.restore_reparse_budgets({block_id: 1}, {'foreign-claim': 1})
    assert d.reparse_claim_attempts == {mid: 1}


def test_legal_abstention_stages_evidence_freezes_parent_and_verifies_only_in_child(source):
    folder = Path(source['source_resources'][0]['path']).parent
    draft, facts = folder/'draft.md', folder/'facts.json'
    draft.write_text(source['claim'].text, encoding='utf-8')
    write_json(facts, [fact.to_dict() for fact in source['baseline_facts']])
    out = folder.parent/'parent'
    args = dict(source, output_root=out/'staged-reparse', source_generation=None)
    d = dispatcher(args)
    mid = source['claim'].claim_id
    call(d, 'coordinator', 'mj_get_fact', {'binding_id': mid}, 'lookup')
    call(d, 'coordinator', 'mj_get_rule', {'rule_id': 'E1-value'}, 'rule')
    before = d.store.to_list()
    verdict = call(d, 'coordinator', 'mj_verify', {'binding_id': mid}, 'verify')
    assert verdict['status'] == 'abstained' and verdict['abstention_code'] == 'X1'
    proposal = call(d, 'coordinator', 'mj_request_reparse',
                    {'block_id': source['block']['id'], 'binding_id': mid}, 'reparse')
    assert proposal['status'] == 'staged' and d.store.to_list() == before
    common = ['--draft', str(draft), '--corpus', str(folder), '--company', source['company'],
              '--period', '2024Y', '--report-date', source['as_of_date']]
    parser = build_parser()
    assert command_check(parser.parse_args(['check', *common, '--out', str(out), '--run-id', 'parent'])) == 0
    parent = read(out/'results.json')
    assert parent['results'][0]['status'] == 'abstained' and parent['results'][0]['abstention_code'] == 'X1'
    frozen = frozen_bytes(out)
    export = out.parent/'reparse'/'parent'
    promoted = freeze_staged_reparse_packets(out, export)
    assert promoted[0]['status'] == 'ready' and promoted[0]['stage_id'] == proposal['stage_id']
    packet = resolve_reparse_packet(export, promoted[0]['packet_id'])
    context = load_parent_context(out)
    child_common = ['--draft', context['data']['draft'], *common[2:]]
    child = out.parent/'child'
    assert command_check(parser.parse_args(['check', *child_common, '--out', str(child), '--run-id', 'child',
        '--reparse-parent-run', str(out), '--reparse-packet', str(packet)])) == 0
    result = read(child/'results.json')
    assert result['results'][0]['status'] == 'verified'
    assert result['results'][0]['evidence'] and all(':p3:' in eid for eid in result['results'][0]['evidence'])
    assert result['reparse_supplements'][0]['packet_id'] == promoted[0]['packet_id']
    assert frozen_bytes(out) == frozen and d.store.to_list() == before
