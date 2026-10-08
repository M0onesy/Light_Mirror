"""Source-local account aliases used by the blind binding gate.

These tests exercise only canonical identity mapping.  They do not promote a
customer table to a consolidated company fact; customer/business scope still
requires independent source evidence.
"""
import json
from pathlib import Path

import pytest

from mingjing.dimension_tables import extract_dimension_tables
from mingjing.financial_tools import FinancialDispatcher
from mingjing.table_mentions import extract_table_mentions
from mingjing.scope import source_scope_dimensions
from test_binding_argument_contract import bridge, invoke


FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "financial_source_geometry_14.json").read_text(encoding="utf-8"))


def test_broker_compact_aliases_use_frozen_source_identity_and_scope():
    claims = [c for c in extract_table_mentions(
        FIXTURE["text"], FIXTURE["pages"], [], "并行科技", "2099Y"
    ) if c.account.startswith("BROKER_OTHER_")]
    assert claims
    claim = claims[0]
    scope = source_scope_dimensions(claim)["scope_header"]
    binding = {
        "company": claim.company,
        "account": "OTHER_CURRENT_ASSETS",
        "period": claim.period,
        "scope": scope,
        "relation": "value",
        "base_period": "",
    }
    dispatcher = FinancialDispatcher(
        "alias-test", "lock", [claim], [], [], [], "2099-01-01", task_company=claim.company
    )
    dispatcher.bindings["planner"][claim.claim_id] = dict(binding)
    dispatcher.bindings["verifier"][claim.claim_id] = dict(binding)
    bound = dispatcher.freeze_bindings()[0]
    assert bound.binding_status == "agreed"
    assert bound.account == "BROKER_OTHER_CURRENT_ASSETS"
    assert dispatcher.agreements[claim.claim_id]["account_aliases"]


def test_revenue_share_dimension_alias_is_source_definition_only():
    claims = [c for c in extract_dimension_tables(
        FIXTURE["text"], FIXTURE["pages"], "并行科技"
    ) if c.account == "REVENUE_SHARE"]
    assert claims
    claim = claims[0]
    canonical, reason = FinancialDispatcher._canonical_binding_account(claim, "REVENUE")
    assert canonical == "REVENUE_SHARE" and reason == "dimension_revenue_share_alias"
    # The same source row must never rewrite an ordinary customer revenue
    # claim: only the explicitly registered share definition authorizes this.
    ordinary = next(c for c in extract_dimension_tables(
        FIXTURE["text"], FIXTURE["pages"], "并行科技"
    ) if c.account == "REVENUE" and c.table_context.get("dimension_id") == claim.table_context.get("dimension_id"))
    canonical, reason = FinancialDispatcher._canonical_binding_account(ordinary, "REVENUE")
    assert canonical == "REVENUE" and reason is None


def test_dimension_account_labels_expose_registered_source_code():
    claim = next(c for c in extract_dimension_tables(
        FIXTURE["text"], FIXTURE["pages"], "并行科技"
    ) if c.account == "REVENUE_SHARE")
    labels = FinancialDispatcher.source_labels(claim)
    assert labels["REVENUE_SHARE"] == "REVENUE_SHARE"


@pytest.mark.parametrize('source_account, alias', [
    ('BROKER_OTHER_CURRENT_ASSETS', 'OTHER_CURRENT_ASSETS'),
    ('BROKER_OTHER_CURRENT_LIABILITIES', 'OTHER_CURRENT_LIABILITIES'),
    ('BROKER_OTHER_NONCURRENT_ASSETS', 'OTHER_NONCURRENT_ASSETS'),
    ('BROKER_OTHER_NONCURRENT_LIABILITIES', 'OTHER_NONCURRENT_LIABILITIES'),
    ('REVENUE_SHARE', 'REVENUE'),
])
def test_registered_source_alias_passes_the_actual_submit_guard(tmp_path, source_account, alias):
    source_claims = (extract_dimension_tables(FIXTURE['text'], FIXTURE['pages'], '并行科技')
                     if source_account == 'REVENUE_SHARE' else extract_table_mentions(
                         FIXTURE['text'], FIXTURE['pages'], [], '并行科技', '2099Y'))
    claim = next(c for c in source_claims if c.account == source_account)
    owner, claim = bridge(tmp_path, claim=claim)
    source = owner.dispatcher.source_dimensions(claim)
    submission = dict(company=claim.company, account=alias, period=source['period'],
                      scope=source['scope_header'], relation=source['relation'],
                      base_period=source['base_period'])
    for role in ('planner', 'verifier'):
        reply = invoke(owner, role, 'mj_submit_binding', {
            'mention_id': claim.claim_id, 'binding': submission}, 'submit-source-alias')
        assert reply['accepted'] is True
        assert owner.dispatcher.bindings[role][claim.claim_id]['account'] == alias
    canonical, reason = owner.dispatcher._canonical_binding_account(claim, alias)
    assert canonical == source_account and reason
    # Alias acceptance never invents a consolidated source scope for a
    # customer share row whose own table has no such declaration.
    if source_account == 'REVENUE_SHARE':
        assert source['scope_header'] == '' and submission['scope'] == ''


@pytest.mark.parametrize('source_account, unrelated', [
    ('BROKER_OTHER_CURRENT_ASSETS', 'OTHER_CURRENT_LIABILITIES'),
    ('REVENUE_SHARE', 'COST'),
    ('REVENUE', 'REVENUE_SHARE'),
])
def test_unrelated_alias_is_rejected_at_submit_without_waiting_for_freeze(tmp_path, source_account, unrelated):
    source_claims = (extract_table_mentions(FIXTURE['text'], FIXTURE['pages'], [], '并行科技', '2099Y')
                     if source_account.startswith('BROKER_') else extract_dimension_tables(
                         FIXTURE['text'], FIXTURE['pages'], '并行科技'))
    claim = next(c for c in source_claims if c.account == source_account)
    owner, claim = bridge(tmp_path, claim=claim)
    source = owner.dispatcher.source_dimensions(claim)
    submission = dict(company=claim.company, account=unrelated, period=source['period'],
                      scope=source['scope_header'], relation=source['relation'],
                      base_period=source['base_period'])
    reply = invoke(owner, 'planner', 'mj_submit_binding', {
        'mention_id': claim.claim_id, 'binding': submission}, 'reject-unrelated-alias')
    assert reply['error']['code'] == 'binding_source_conflict'
    assert reply['error']['conflicting_fields'] == ['account']
    assert reply['error']['source_dimensions']['account'] == source_account
    assert not owner.dispatcher.bindings['planner']
