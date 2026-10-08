"""Compact lists must preserve identity, blind isolation and resource guards."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from mingjing.audit import digest
from mingjing.binding_metadata import compact_binding_metadata
from mingjing.financial_tools import (FinancialArgumentError, FinancialDispatcher, ToolDenied,
                                      _blind_source_metadata)
from mingjing.mentions import extract_mentions
from mingjing.source_semantics import source_binding_issue
from test_quoted_source_table import linked_sample


FIELDS = ('company', 'account', 'period', 'scope', 'relation', 'base_period',
          'dimension_kind', 'dimension_id', 'currency', 'definition_id')


def dispatcher():
    claims, facts = linked_sample()
    owner = FinancialDispatcher('compact-test', 'frozen-lock', claims, facts, [], [],
                                '2024-04-22', task_company=claims[0].company)
    for role in ('planner', 'verifier'):
        owner.register_session(role, role, [claims[0].claim_id])
    return owner


def call(owner, role, tool, params, call_id):
    return owner.dispatch({'run_id': owner.run_id, 'resource_lock': owner.resource_lock,
        'session_id': role, 'role': role, 'tool_name': tool,
        'params': params, 'call_id': call_id})


def test_compact_metadata_retains_semantics_positions_source_and_host_authority():
    owner = dispatcher()
    claim = next(iter(owner.claims.values()))
    frozen = deepcopy(claim.to_dict())
    full = owner.source_dimensions(claim)
    expanded = owner.mention_context(claim)
    compact = owner.mention_context(claim, compact=True)
    view = compact['source_dimensions']
    for field in FIELDS:
        assert view[field] == expanded['source_dimensions'][field]
    for field in ('source_text_sha256', 'source_start', 'source_end', 'source_numeric_start'):
        assert view[field] == full[field]
    assert compact['issuer_context'] == expanded['issuer_context']
    assert view['company_provenance'] == full['company_provenance']
    assert view['source_metadata_sha256'] == digest(full)
    reference = view['source_table_reference']
    for field in ('sha256', 'source_doc', 'report_id', 'publish_date', 'cutoff', 'source_table_ids'):
        assert reference[field] == full['source_table_reference'][field]
    assert reference['accounting_scope'] == '未从引用推断合并或母公司'
    assert 'source_table_evidence' not in reference
    assert 'dimension_source' not in view
    assert view['source_geometry']['bbox'] == full['dimension_source']['cell']['bbox']
    assert len(json.dumps(view, ensure_ascii=False)) < len(json.dumps(full, ensure_ascii=False)) * .70
    assert claim.to_dict() == frozen


def test_compact_resource_reference_reads_full_blind_metadata_in_authorized_scope():
    owner = dispatcher()
    claim = next(iter(owner.claims.values()))
    listing = call(owner, 'planner', 'mj_list_mentions', {}, 'list')['mentions'][0]
    reference = listing['metadata_reference']
    assert reference == {'tool': 'mj_read_context', 'params': {'mention_id': claim.claim_id},
                         'source_resource_id': 'source-dimensions'}
    reading = call(owner, 'planner', reference['tool'], reference['params'], 'expand')
    assert 'dimension_source' in reading['source_dimensions']
    assert 'source_table_evidence' in reading['source_dimensions']['source_table_reference']
    assert 'metadata_reference' not in reading
    assert reading == call(owner, 'verifier', reference['tool'], reference['params'], 'expand-b')
    assert claim.numeric_text not in reading['text']
    other = list(owner.claims)[1]
    with pytest.raises(ToolDenied):
        call(owner, 'planner', 'mj_read_context', {'mention_id': other}, 'not-assigned')
    with pytest.raises(ToolDenied):
        call(owner, 'planner', 'mj_read_context', {'mention_id': '../source_dimensions.json'}, 'path')


def test_compact_projection_cannot_change_frozen_binding_or_leak_a_to_b():
    owner = dispatcher()
    claim = next(iter(owner.claims.values()))
    original = deepcopy(claim.to_dict())
    before_a = call(owner, 'planner', 'mj_list_mentions', {}, 'before-a')
    before_b = call(owner, 'verifier', 'mj_list_mentions', {}, 'before-b')
    assert before_a == before_b
    binding = {key: value for key, value in before_a['mentions'][0]['source_dimensions'].items()
               if key in FIELDS[:6]}
    call(owner, 'planner', 'mj_submit_binding', {'mention_id': claim.claim_id, 'binding': binding}, 'submit-a')
    after_b = call(owner, 'verifier', 'mj_list_mentions', {}, 'after-b')
    assert after_b == before_b
    changed = deepcopy(binding)
    changed['scope'] = '母公司'
    with pytest.raises(FinancialArgumentError) as rejected:
        call(owner, 'verifier', 'mj_submit_binding', {'mention_id': claim.claim_id, 'binding': changed}, 'wrong-b')
    repair = rejected.value.rejection()
    assert repair['error']['code'] == 'binding_source_conflict'
    assert repair['error']['conflicting_fields'] == ['scope']
    assert repair['error']['source_dimensions']['scope'] == '来源表'
    assert not any(value in json.dumps(repair) for value in ('planner', 'verifier', 'binding_A', 'binding_B',
                                                            claim.candidate_value))
    assert not owner.bindings['verifier'] and owner.bindings['planner'][claim.claim_id] == binding
    assert call(owner, 'verifier', 'mj_list_mentions', {}, 'after-rejection') == before_b
    assert call(owner, 'verifier', 'mj_submit_binding',
                {'mention_id': claim.claim_id, 'binding': binding}, 'repair-b')['accepted']
    assert owner.freeze_bindings()[0].binding_status == 'agreed'
    before_a['mentions'][0]['source_dimensions']['period'] = '2022Y'
    stored = owner.claims[claim.claim_id]
    assert stored.table_context == original['table_context']
    assert stored.period == original['period'] and stored.candidate_value == original['candidate_value']
    assert source_binding_issue(replace(claim, period='2022Y'), strict=True)[0] == 'X3'


def test_summary_blinds_neighbor_numbers_and_never_copies_financial_cell_text():
    owner = dispatcher()
    claim = next(iter(owner.claims.values()))
    dimensions = owner.source_dimensions(claim)
    dimensions['source_company_subjects'] = ['营业收入98765元与利润3.14亿元']
    dimensions['source_table_reference']['citation']['text'] += '；旁注净利率26%'
    dimensions['classification_basis'] = {'source_text': '98765元', 'candidate_value': '999999'}
    dimensions['dimension_source']['cell']['text'] = '98765元'
    dimensions['binding_A'] = {'candidate_value': '999999'}
    original = deepcopy(dimensions)
    compact = compact_binding_metadata(dimensions, redact=_blind_source_metadata)
    payload = json.dumps(compact, ensure_ascii=False)
    assert not any(number in payload for number in ('98765', '3.14', '26%', '999999'))
    assert 'candidate_value' not in payload and 'binding_A' not in payload
    assert 'source_table_evidence' not in payload
    assert dimensions == original


@pytest.mark.parametrize('subject', ['乙公司', '甲公司与乙公司', '本公司的子公司', '某未知主体'])
def test_compaction_keeps_source_conflicts_and_cannot_authorize_task_issuer(subject):
    claim = extract_mentions(subject+'2023年合并营业收入98765元。', '甲公司')[0]
    claim = replace(claim, table_context={**claim.table_context,
        'classification_basis': {'rule': 'source_financial_dimension', 'source_text': claim.text}})
    owner = FinancialDispatcher('conflict', 'lock', [claim], [], [], [],
                                '2024-04-22', task_company='甲公司')
    stored = owner.claims[claim.claim_id]
    compact = owner.mention_context(stored, compact=True)
    dimensions = compact['source_dimensions']
    assert dimensions.get('source_company_conflict') or dimensions.get('source_company_ambiguous')
    assert compact['issuer_context']['applicable'] is False
    assert compact['issuer_context']['company'] == ''
    assert '98765' not in json.dumps(compact, ensure_ascii=False)


def test_registered_numeric_issuer_authority_is_preserved_separately_from_blind_source_text():
    claim = extract_mentions('公司2023年合并营业收入98765元。', '360公司')[0]
    claim = replace(claim, table_context={**claim.table_context,
        'classification_basis': {'rule': 'source_financial_dimension', 'source_text': claim.text}})
    owner = FinancialDispatcher('issuer', 'lock', [claim], [], [], [],
                                '2024-04-22', task_company='360公司')
    view = owner.mention_context(owner.claims[claim.claim_id], compact=True)
    assert view['issuer_context']['applicable'] is True
    assert view['issuer_context']['company'] == '360公司'
    assert '98765' not in json.dumps(view, ensure_ascii=False)


def test_summary_retains_structure_authority_and_discards_repeated_tables():
    dimensions = {'company': '甲公司', 'account': 'REVENUE', 'period': '2023Y',
        'scope': '合并', 'relation': 'value', 'base_period': '',
        'scope_proof': {'schema_version': 'source-financial-summary-structure.v1',
            'scope': '合并', 'kind': 'bounded_combined_financial_summary',
            'sha256': 'a'*64, 'entity_id': 'financial-summary-'+'a'*20,
            'source_rule_id': 'SOURCE-summary-consolidated-structure.v1',
            'professional_signoff_status': 'pending',
            'summary': {'heading': {'text': '财务预测摘要'}, 'citation': {'text': '资料来源：公司年报'}},
            'tables': [{'headers': [{'text': '2023A'}]*50, 'structure': {
                'parent_caption': {'text': '归属母公司股东权益'},
                'minority_caption': {'text': '少数股东权益'}}}]}}
    frozen = deepcopy(dimensions)
    summary = compact_binding_metadata(dimensions)['scope_proof']
    assert summary['source_rule_id'] == frozen['scope_proof']['source_rule_id']
    assert summary['sha256'] == 'a'*64
    assert summary['professional_signoff_status'] == 'pending'
    assert summary['structure_labels'] == [{'parent_caption': '归属母公司股东权益',
                                           'minority_caption': '少数股东权益'}]
    assert 'tables' not in summary
    summary['structure_labels'][0]['parent_caption'] = '模型改写'
    assert dimensions == frozen


def test_small_metadata_keeps_compatible_shape_and_independent_copy():
    dimensions = {'company': '甲公司', 'period': '2023Y', 'scope_header': '',
                  'explicit': {'company': False}, 'source_company_subjects': []}
    projected = compact_binding_metadata(dimensions)
    assert projected == dimensions and 'metadata_view' not in projected
    projected['explicit']['company'] = True
    assert dimensions['explicit']['company'] is False


def test_only_strict_host_resource_identifiers_bypass_numeric_redaction():
    owner = dispatcher()
    dimensions = owner.source_dimensions(next(iter(owner.claims.values())))
    valid = compact_binding_metadata(dimensions, redact=_blind_source_metadata)
    reference = valid['source_table_reference']
    assert reference['source_doc'] == dimensions['source_table_reference']['source_doc']
    assert reference['source_table_ids'] == dimensions['source_table_reference']['source_table_ids']
    assert reference['sha256'] == dimensions['source_table_reference']['sha256']
    assert valid['metadata_view'] == 'compact-binding.v1'
    altered = deepcopy(dimensions)
    altered['source_text_sha256'] = '98765元'
    altered['source_table_reference'].update(source_doc='98765', report_id='report-98765元',
        sha256='98765', source_table_ids=['source-table-98765元'])
    rejected = compact_binding_metadata(altered, redact=_blind_source_metadata)
    assert '98765' not in json.dumps(rejected, ensure_ascii=False)


def test_compact_preserves_registered_scope_rule_and_period_currency_semantics():
    dimensions = {'company': '甲公司', 'account': 'REVENUE', 'period': '2023Y',
        'scope': '合并', 'relation': 'cagr', 'base_period': '2018Y',
        'scope_definition_id': 'NP_PARENT-consolidated.v1',
        'source_cagr_period': {'text': '2018-2023年', 'years': 5},
        'source_period_list': {'source': 'explicit_ordered_period_list', 'first_year': 2020,
            'last_year': 2022, 'period_kind': 'Q4', 'occurrence_index': 1, 'ambiguous': False,
            'occurrence_spans': [[1, 2]]*100},
        'source_currency': {'currency': 'USD', 'source': 'original_numeric_currency',
            'currency_texts': ['美元'], 'conflict': False, 'declarations': ['98765美元']},
        'scope_proof': {'source_rule_id': 'SOURCE-summary-consolidated-structure.v1',
            'professional_signoff_status': 'pending', 'sha256': 'a'*64}}
    view = compact_binding_metadata(dimensions, redact=_blind_source_metadata)
    assert view['source_cagr_period'] == dimensions['source_cagr_period']
    assert view['source_period_list']['first_year'] == 2020
    assert view['source_period_list']['occurrence_index'] == 1
    assert 'occurrence_spans' not in view['source_period_list']
    assert view['source_currency']['currency'] == 'USD'
    assert view['source_currency']['conflict'] is False
    assert '98765' not in json.dumps(view, ensure_ascii=False)
    assert view['scope_definition_id'] == 'NP_PARENT-consolidated.v1'
    assert view['scope_proof']['source_rule_id'] == 'SOURCE-summary-consolidated-structure.v1'
    assert view['scope_proof']['professional_signoff_status'] == 'pending'
