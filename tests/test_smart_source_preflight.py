"""Only kernel-skipped frozen inputs avoid native binding work."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json

import pytest

from mingjing.agent import FinancialAgent
from mingjing.audit import AuditLog
from mingjing.audit import digest
from mingjing.config import AppConfig
from mingjing.coverage import CoverageLedger
from mingjing.financial_tools import FinancialDispatcher
from mingjing.models import AtomicClaim
from mingjing.smart.claims import Claim, ReportModel, ReportTable
from mingjing.smart.detect import DocMeta
from mingjing.smart.ingestion import SmartKernel
from test_smart_financial_contract import fact, model


def paired_owner(tmp_path):
    meta = DocMeta(company='测试公司', report_date='2024-04-25', base_year=2023)
    table = ReportTable('盈利预测', 'SUMMARY', '元', [('2025Y', True)], 1,
                        [{'metric': 'REVENUE', 'relation': 'value', 'unit': '', 'label': '营业收入',
                          'page': 1, 'cells': [('999.00', Decimal('999'))]}])
    base = dict(value='999', unit='元', decimals=2, metric='REVENUE', period='2025Y',
                forecast=True, category='forecast', scope='合并')
    cell = Claim(cid='cell', kind='table', table='SUMMARY', row_label='营业收入', **base)
    prose = Claim(cid='prose', kind='prose', **base)
    history = Claim(cid='history', value='100', unit='元', decimals=2, metric='REVENUE', period='2023Y',
                    category='core', scope='合并')
    missing_history = replace(history, cid='missing-history', period='2022Y')
    market_model = model('当前股价(元)  10.00  总市值(亿元)  6.00  总股本(亿股)  0.60\n')
    markets = [replace(c, cid='market-' + c.cid) for c in market_model.claims if c.category == 'market']
    sources = [cell, prose, history, missing_history, *markets]
    financial_model = ReportModel(meta, sources, [table], market_model.market, {'doc_sha256': 'd' * 64})
    native = []
    for i, c in enumerate(sources):
        binding = dict(company='测试公司', account=c.metric, period=c.period, scope='合并',
                       relation=c.relation, base_period=c.base_period)
        native.append(AtomicClaim('native-' + c.cid, c.num_text or c.value, i * 10, i * 10 + 3,
                      company='测试公司', account=c.metric, period=c.period, candidate_value=c.value,
                      unit=c.unit, caliber_hint=('合并',), relation=c.relation, display_decimals=c.decimals,
                      table_context={'smart_cid': c.cid, 'smart_claim': c.to_dict(), 'smart_binding': binding,
                                     'scope_assumption': False, 'validation_policy': 'standard'}))
    cfg = AppConfig().ensure_defaults().to_dict()
    cfg['agent']['budget'].update(max_requests=3, max_model_calls=3, max_subagents=1)
    owner = FinancialAgent({'material_profile': 'smart', 'validation_policy': 'standard',
                            'report_date': '2024-04-25', 'task_type': 'check'}, cfg, lambda _: '',
                           tmp_path / 'native', run_id='paired')
    owner.resource_lock = 'lock'
    owner.audit = AuditLog(owner.out, owner.run_id)
    owner.claims = native
    owner.smart_kernel = SmartKernel([fact(100)], financial_model, 'standard', {}, [],
                                     {c.claim_id: c.table_context['smart_claim'] for c in native})
    owner.dispatcher = FinancialDispatcher(owner.run_id, owner.resource_lock, native, [fact(100)], [], [],
                                            '2024-04-25', audit=owner.audit, task_company='测试公司')
    owner.dispatcher.trusted_verifier = owner.smart_kernel.verify
    owner.dispatcher.validation_policy = 'standard'
    owner.ledger = CoverageLedger([], [], [{'id': c.claim_id, 'claim_id': c.claim_id, 'text': c.numeric_text,
                                          'start': c.start, 'end': c.end, 'status': 'pending'} for c in native])
    return owner


def install_controlled_roles(owner):
    def run_role(role, ids, text, **kwargs):
        owner.native_request_count += 1
        assert owner.native_request_count <= owner.budget['max_model_calls']
        sid = role + str(owner.native_request_count)
        owner.dispatcher.register_session(sid, role, ids)
        owner.session_records.append({'session_id': sid, 'role': role, 'mention_ids': ids, 'completed': True})

        def call(tool, args):
            return owner.dispatcher.dispatch({'run_id': owner.run_id, 'resource_lock': owner.resource_lock,
                 'session_id': sid, 'role': role, 'call_id': tool, 'tool_name': tool, 'params': args})

        if role in {'planner', 'verifier'}:
            call('mj_list_mentions', {})
            call('mj_submit_bindings', {'bindings': [{'mention_id': mid,
                  'binding': owner.dispatcher.claims[mid].table_context['smart_binding']} for mid in ids]})
        else:
            call('mj_get_facts', {'binding_ids': ids})
            call('mj_get_rule', {'rule_id': 'SMART-registered-kernel'})
            call('mj_verify_bindings', {'binding_ids': ids})
        return {'finish_reason': 'completed'}

    owner.run_role = run_role


def test_forecast_self_input_and_market_skip_but_history_and_internal_prose_keep_native_work(tmp_path):
    owner = paired_owner(tmp_path)
    expected_excluded = {'native-cell', *[c.claim_id for c in owner.claims if c.account in {'PRICE', 'MARKET_CAP', 'TOTAL_SHARES'}]}
    for c in owner.claims:
        result = owner.smart_kernel.verify(c)
        assert bool(owner._smart_source_exclusion(c)) == (c.claim_id in expected_excluded)
        if c.claim_id == 'native-prose':
            assert result['status'] == 'verified' and result['basis'] == 'internal'
        if c.claim_id == 'native-missing-history':
            assert result['status'] == 'abstained' and result['smart_result']['status'] == 'unverifiable'
    install_controlled_roles(owner)
    original_ledger = deepcopy(owner.ledger.entries)
    owner.bind()
    saved = json.loads((owner.out / 'agent_state.json').read_text(encoding='utf-8'))
    assert set(saved['preflight_source_exclusions']) == expected_excluded
    assert saved['preflight_claim_ids'] == owner.preflight_claim_ids
    assert all(expected_excluded.isdisjoint(r['mention_ids']) for r in owner.session_records)
    assert owner.native_request_count == 3
    owner.calculate_kernel(dual=True)
    rows = {r['claim_id']: r for r in owner.results}
    assert len(rows) == len(original_ledger) == len(owner.ledger.entries)
    assert owner.pending_claim_ids == [] and not owner.ledger.to_dict()['partial']
    assert rows['native-history']['status'] == rows['native-prose']['status'] == 'verified'
    assert rows['native-missing-history']['smart_result']['status'] == 'unverifiable'
    for mid in expected_excluded:
        assert rows[mid]['smart_result']['status'] == 'skipped'
        assert rows[mid]['abstention_code'] == 'X6' and rows[mid]['status'] == 'abstained'
        entry = next(e for e in owner.ledger.entries if e['claim_id'] == mid)
        assert entry['status'] == 'rule_excluded' and entry['exclusion_basis']['native_verification'] is False
    assert [(e['id'], e['text'], e['start'], e['end']) for e in owner.ledger.entries] == [
        (e['id'], e['text'], e['start'], e['end']) for e in original_ledger]
    # A reconstructed checkpoint must derive identical IDs, reasons and hash
    # proofs; no previous A/B submission is needed for these same skipped inputs.
    resumed = paired_owner(tmp_path / 'reconstructed')
    resumed.preflight_claim_ids = saved['preflight_claim_ids']
    resumed.preflight_source_exclusions = saved['preflight_source_exclusions']
    assert {c.claim_id: proof for c in resumed.claims if (proof := resumed._smart_source_exclusion(c))} == saved['preflight_source_exclusions']
    install_controlled_roles(resumed)
    resumed.bind()
    resumed.calculate_kernel(dual=True)
    assert resumed.results == owner.results
    assert resumed.ledger.to_dict() == owner.ledger.to_dict()


@pytest.mark.parametrize('change', ['legacy', 'foreign_source', 'wrong_category', 'unverifiable', 'verified', 'wrong_policy', 'changed_value'])
def test_uncertain_or_non_smart_inputs_cannot_use_the_exclusion(tmp_path, monkeypatch, change):
    owner = paired_owner(tmp_path)
    claim = owner.claims[0]
    if change == 'legacy':
        owner.request['material_profile'] = 'legacy'
    elif change == 'foreign_source':
        claim = replace(claim, claim_id='foreign')
    elif change == 'wrong_category':
        source = owner.smart_kernel.claim_map[claim.claim_id]
        source['category'] = 'core'
    else:
        original = owner.smart_kernel.verify
        def altered(c):
            result = original(c)
            if change in {'verified', 'unverifiable'}:
                result['smart_result']['status'] = change
            elif change == 'wrong_policy':
                result['validation_policy'] = 'strict'
            else:
                result['observed'] = '998'
            return result
        monkeypatch.setattr(owner.smart_kernel, 'verify', altered)
        owner.dispatcher.trusted_verifier = owner.smart_kernel.verify
    assert owner._smart_source_exclusion(claim) is None


def test_tampered_checkpoint_proof_cannot_authorize_history_without_any_native_binding(tmp_path):
    owner = paired_owner(tmp_path)
    history = next(c for c in owner.claims if c.claim_id == 'native-history')
    original = owner.smart_kernel.verify(history)
    assert original['status'] == 'verified'
    owner.preflight_claim_ids = [history.claim_id]
    owner.preflight_source_exclusions = {history.claim_id: {'verdict_hash': digest(original), 'rule': 'forged'}}
    owner.claims = owner.dispatcher.freeze_bindings()
    assert all(not bindings for bindings in owner.dispatcher.bindings.values())
    assert not owner.native_verified_ids()
    owner.calculate_kernel(dual=True)
    result = next(r for r in owner.results if r['claim_id'] == history.claim_id)
    assert result['status'] == 'abstained' and result['abstention_code'] == 'X3'
    assert result['smart_result']['status'] == 'unverifiable'
    entry = next(e for e in owner.ledger.entries if e['claim_id'] == history.claim_id)
    assert entry['status'] != 'rule_excluded'
