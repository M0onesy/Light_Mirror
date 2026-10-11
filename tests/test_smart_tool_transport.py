"""Smart model responses stay small while host financial evidence stays whole."""
from copy import deepcopy
import json

import pytest

from mingjing.financial_tools import FinancialDispatcher
from mingjing.smart.ingestion import SmartKernel, ingest_facts, partition_smart, smart_claims
from test_smart_native_bridge import fixture_materials


def dispatcher_fixture(tmp_path, *, policy='standard'):
    _, corpus, draft, _ = fixture_materials(tmp_path)
    facts, _ = ingest_facts(corpus, '甲公司', '2023Y')
    text, pages, blocks = partition_smart(draft)
    model, claims = smart_claims(draft, text, pages, blocks, '甲公司', '2023Y', facts, policy)
    kernel = SmartKernel(facts, model, policy, {}, [],
                         {c.claim_id: c.table_context['smart_claim'] for c in claims})
    d = FinancialDispatcher('transport', 'lock', claims, facts, blocks, [], '2024-05-01', task_company='甲公司')
    d.trusted_verifier = kernel.verify
    d.validation_policy = policy
    for role in ('planner', 'verifier'):
        d.bindings[role][claims[0].claim_id] = dict(claims[0].table_context['smart_binding'])
    d.freeze_bindings()
    d.register_session('coordinator', 'coordinator', [claims[0].claim_id])

    def call(tool, params, call_id):
        return d.dispatch({'run_id': d.run_id, 'resource_lock': d.resource_lock,
                           'session_id': 'coordinator', 'role': 'coordinator', 'call_id': call_id,
                           'tool_name': tool, 'params': params})

    mid = claims[0].claim_id
    call('mj_get_facts', {'binding_ids': [mid]}, 'facts')
    call('mj_get_rule', {'rule_id': 'SMART-registered-kernel'}, 'rule')
    return d, kernel, mid, call


@pytest.mark.parametrize('tool', ['mj_verify', 'mj_verify_bindings'])
def test_dispatch_logs_full_smart_result_but_returns_a_separate_projection(tmp_path, tool):
    d, kernel, mid, call = dispatcher_fixture(tmp_path)
    full = kernel.verify(d.claims[mid])
    # A deliberately large candidate carries genuine-looking values and text:
    # shrinking the response must not remove it from the audited host output.
    full['smart_result']['candidates'].append({
        'definition': 'alternative', 'operands': deepcopy(full['operands']),
        'source_context': '原页上下文' * 20000,
    })
    full['operands'][0]['source_context'] = '原页上下文' * 20000
    d.trusted_verifier = lambda claim: full
    args = {'binding_ids': [mid]} if tool == 'mj_verify_bindings' else {'binding_id': mid}
    response = call(tool, args, 'verify')
    projected = response['items'][0] if tool == 'mj_verify_bindings' else response
    logged = d.trace[-1]['output']['items'][0] if tool == 'mj_verify_bindings' else d.trace[-1]['output']
    assert logged['smart_result'] == full['smart_result']
    assert logged['operands'] == full['operands']
    assert 'smart_result' not in projected and 'candidates' not in projected
    assert 'source_context' not in projected['operands'][0]
    assert projected['assumptions'] == full['smart_result']['assumptions']
    for key in ('status', 'error_type', 'abstention_code', 'reason', 'observed', 'expected',
                'formula', 'evidence', 'rule_ids', 'rules', 'source_dimensions', 'candidate_interval',
                'claim_interval', 'expected_interval', 'candidate_precision', 'verification_basis',
                'precision_note', 'establishes_exact_value'):
        assert projected[key] == full[key]
    for original, compact in zip(full['operands'], projected['operands']):
        for key in ('fact_id', 'account', 'period', 'scope', 'nr', 'value', 'unit', 'relation',
                    'decimals', 'quantum', 'interval', 'definition_id', 'currency', 'evidence'):
            assert compact[key] == original[key]
    assert len(json.dumps(response, ensure_ascii=False)) < len(json.dumps(d.trace[-1]['output'], ensure_ascii=False)) / 10
    projected['operands'][0]['evidence'].clear()
    projected['assumptions'].clear()
    assert logged['operands'][0]['evidence'] and logged['smart_result']['assumptions']


def test_projection_preserves_complete_blocking_diagnostics_and_source_precision():
    interval = {'lower': '0.1', 'upper': '0.2', 'lower_closed': True, 'upper_closed': False}
    diagnostics = {'blockers': [{'code': 'source_version_conflict', 'chain': [{'evidence': ['doc:p8:version']}]}]}
    row = {'claim_id': 'claim', 'status': 'abstained', 'abstention_code': 'X3',
           'reason': '来源版本冲突', 'operands': [], 'candidate_interval': interval,
           'expected_interval': {}, 'source_precision': [{'unit': '%', 'decimals': 2,
               'quantum': '0.0001', 'interval': interval, 'evidence': ['doc:p8:text'], 'fact_id': None}],
           'smart_result': {'cid': 'smart', 'status': 'unverifiable', 'abstain_code': 'X3',
               'assumptions': [{'code': 'version', 'text': '不可混用版本'}],
               'missing': [{'account': 'REVENUE', 'period': '2023Y', 'why': '版本冲突', **diagnostics}],
               'diagnosis': diagnostics, 'blockers': diagnostics['blockers'],
               'notes': ['补齐相同版本来源'], 'internal_check': '不能取得有效基期',
               'candidates': [{'source_context': '全文' * 2000}]}}
    full = deepcopy(row)
    view = FinancialDispatcher.model_verification_output('mj_verify', row)
    for key in ('assumptions', 'missing', 'diagnosis', 'blockers', 'notes', 'internal_check'):
        assert view[key] == row['smart_result'][key]
    assert view['status'] == 'abstained' and view['abstention_code'] == 'X3'
    assert view['source_precision'][0]['interval'] == interval
    assert view['source_precision'][0]['unit'] == '%'
    assert row == full


def test_smart_yoy_operand_unit_is_not_inferred_from_its_amount_account():
    operand = {'account': 'REVENUE', 'value': '0.25', 'unit': '%', 'relation': 'yoy',
               'decimals': 2, 'quantum': '0.0001', 'interval': {'lower': '0.24995', 'upper': '0.25005'},
               'evidence': ['doc:p8:yoy']}
    row = {'status': 'verified', 'operands': [operand], 'smart_result': {}, 'source_precision': []}
    view = FinancialDispatcher.model_verification_output('mj_verify', row)
    assert view['operands'][0] == operand


def test_legacy_and_other_tool_outputs_remain_identical():
    legacy = {'claim_id': 'native', 'status': 'verified', 'operands': [{'source_quote': '100元'}]}
    assert FinancialDispatcher.model_verification_output('mj_verify', legacy) is legacy
    batch = {'items': [legacy]}
    assert FinancialDispatcher.model_verification_output('mj_verify_bindings', batch) == batch
    other = {'smart_result': {'candidates': ['full']}}
    assert FinancialDispatcher.model_verification_output('mj_get_facts', other) is other
