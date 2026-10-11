"""The upload path must replay its own financial kernel, including assumptions."""
import json
from dataclasses import asdict, replace

import pytest

from mingjing.agent import FinancialAgent
from mingjing.audit import digest, finalize_manifest
from mingjing.config import AppConfig
from mingjing.replay import replay_run
from mingjing.source_cache import extractor_source_modules
from test_smart_native_bridge import fixture_materials
from test_smart_source_preflight import paired_owner, install_controlled_roles


def test_smart_canonical_replay_preserves_every_financial_field(tmp_path):
    _, corpus, draft, report = fixture_materials(tmp_path)
    cfg = AppConfig().ensure_defaults().to_dict()
    cfg['workspace']['knowledge_root'] = ''
    request = {'draft': str(draft), 'corpus': str(corpus), 'company': '甲公司', 'period': '2023Y',
               'report_date': '2024-05-01', 'task_type': 'check', 'mode': 'harness',
               'material_profile': 'smart', 'validation_policy': 'standard', 'smart_options': {'use_llm': False},
               'smart_draft_metadata': {k: v for k, v in report.items() if k in __import__('mingjing.smart.detect', fromlist=['DocMeta']).DocMeta().to_dict()}}
    agent = FinancialAgent(request, cfg, lambda _: '', tmp_path / 'native', run_id='replay-smart')
    result = agent.run()
    assert result['summary']['verified'] == 1
    assert result['results'][0]['smart_result']['assumptions']
    receipt = replay_run(agent.out)
    assert receipt['status'] == 'passed', receipt
    assert receipt['differences'] == [] and receipt['network_calls'] == 0
    context_path = agent.out / 'smart_kernel_context.json'
    context = json.loads(context_path.read_text(encoding='utf-8'))
    context['policy'] = 'strict'
    context_path.write_text(json.dumps(context), encoding='utf-8')
    rejected = replay_run(agent.out)
    assert rejected['status'] == 'failed' and not rejected['integrity_ok']
    assert any(i['kind'] in {'hash_mismatch', 'smart_policy_mismatch'} for i in rejected['integrity_issues'])


def frozen_preflight_run(tmp_path, model_status='partial', mutate=None):
    owner = paired_owner(tmp_path)
    install_controlled_roles(owner)
    owner.bind()
    owner.calculate_kernel(dual=True)
    kernel = owner.smart_kernel
    lock = {'smart_extractor': extractor_source_modules()}
    lock['resource_lock'] = digest(lock)
    payload = {'run_id': owner.run_id, 'material_profile': 'smart', 'validation_policy': kernel.policy,
               'report_date': owner.request['report_date'], 'model': {'status': model_status},
               'resource_lock': lock['resource_lock'], 'claims': [c.to_dict() for c in owner.claims],
               'facts': owner.dispatcher.store.to_list(), 'results': owner.results,
               'summary': {status: sum(row['status'] == status for row in owner.results)
                           for status in ('verified', 'violated', 'abstained')},
               'pending_claim_ids': [], 'coverage': owner.ledger.to_dict(),
               'execution': {'native_verified_claim_ids': sorted(owner.native_verified_ids()),
                             'preflight_claim_ids': list(owner.preflight_claim_ids),
                             'preflight_source_exclusions': dict(owner.preflight_source_exclusions),
                             'pending_claim_ids': [], 'judged_claim_ids': [r['claim_id'] for r in owner.results]}}
    if mutate:
        mutate(owner, payload)
    model = asdict(kernel.model)
    records = {'claims.json': payload['claims'], 'facts.json': payload['facts'], 'results.json': payload,
               'agent_lock.json': lock, 'smart_report_model.json': model,
               'smart_kernel_context.json': {'policy': kernel.policy, 'doc_index': kernel.reports, 'texts': kernel.texts}}
    for name, value in records.items():
        (owner.out/name).write_text(json.dumps(value, ensure_ascii=False, default=str), encoding='utf-8')
    finalize_manifest(owner.out, owner.run_id,
                      [str(owner.out/'smart_report_model.json'), str(owner.out/'smart_kernel_context.json')], 'agent')
    return owner, payload


@pytest.mark.parametrize('model_status', ['success', 'partial', 'cancelled'])
def test_kernel_replay_recomputes_forecast_and_market_preflight_proofs(tmp_path, monkeypatch, model_status):
    import socket
    owner, original = frozen_preflight_run(tmp_path, model_status)
    monkeypatch.setattr(socket, 'create_connection', lambda *_args, **_kwargs: pytest.fail('Replay used a network connection'))
    receipt = replay_run(owner.out)
    assert receipt['status'] == 'passed' and receipt['integrity_ok']
    assert receipt['differences'] == [] and receipt['network_calls'] == 0
    assert receipt['result_digest'] == digest(original['results'])
    excluded = original['execution']['preflight_source_exclusions']
    assert {proof['category'] for proof in excluded.values()} == {'forecast', 'market'}
    assert len(excluded) == 4
    assert all(row['status'] == 'abstained' and row['smart_result']['status'] == 'skipped'
               for row in original['results'] if row['claim_id'] in excluded)


@pytest.mark.parametrize('tamper', ['missing_proof', 'missing_preflight_id', 'verdict_hash', 'source_claim_hash', 'validation_policy'])
def test_kernel_replay_rejects_missing_or_forged_preflight_proof(tmp_path, tamper):
    def mutate(owner, payload):
        execution = payload['execution']
        mid = 'native-cell'
        if tamper == 'missing_proof':
            execution['preflight_source_exclusions'].pop(mid)
        elif tamper == 'missing_preflight_id':
            execution['preflight_claim_ids'].remove(mid)
        else:
            execution['preflight_source_exclusions'][mid] = {
                **execution['preflight_source_exclusions'][mid], tamper: 'forged'}
    owner, original = frozen_preflight_run(tmp_path, mutate=mutate)
    receipt = replay_run(owner.out)
    assert receipt['status'] == 'failed' and receipt['integrity_ok']
    assert not receipt['numeric_equal']
    assert any(row.get('claim_id') == 'native-cell' and row.get('field') == 'abstention_code'
               and row.get('replayed') == 'X3' for row in receipt['differences'])
    assert next(row for row in original['results'] if row['claim_id'] == 'native-cell')['abstention_code'] == 'X6'


@pytest.mark.parametrize('mid', ['native-history', 'native-prose'])
def test_kernel_replay_cannot_exempt_verified_history_or_checkable_forecast(tmp_path, mid):
    def mutate(owner, payload):
        claim = next(c for c in owner.claims if c.claim_id == mid)
        fresh = owner.smart_kernel.verify(claim)
        assert fresh['status'] == 'verified'
        source = claim.table_context['smart_claim']
        execution = payload['execution']
        execution['native_verified_claim_ids'].remove(mid)
        execution['preflight_claim_ids'].append(mid)
        proof = dict(execution['preflight_source_exclusions']['native-cell'])
        proof.update(smart_cid=source['cid'], category=source['category'], source_claim_hash=digest(source),
                     verdict_hash=digest(fresh), status='skipped')
        execution['preflight_source_exclusions'][mid] = proof
        payload['claims'] = [replace(c, binding_status='ambiguous').to_dict() if c.claim_id == mid else c.to_dict()
                             for c in owner.claims]
    owner, _ = frozen_preflight_run(tmp_path, mutate=mutate)
    receipt = replay_run(owner.out)
    assert receipt['status'] == 'failed' and receipt['integrity_ok']
    assert any(row.get('claim_id') == mid and row.get('field') == 'status'
               and row.get('replayed') == 'abstained' for row in receipt['differences'])
