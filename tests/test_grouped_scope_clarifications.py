"""Scoped user assumptions cannot rewrite source evidence or source-table checks."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from mingjing.audit import digest
from mingjing.facts import FactStore
from mingjing.judge import Judge
from mingjing.mentions import extract_mentions
from mingjing.scope import (apply_scope_clarification, create_grouped_scope_clarifications,
                            create_scope_clarification, scope_clarification_metadata,
                            scope_table_id, source_scope_dimensions, validate_scope_clarifications)
from mingjing.source_semantics import freeze_source_dimensions, source_dimensions_from_text
from mingjing.source_table_reference import link_quoted_customer_tables, validated_table_reference
from test_quoted_source_table import sample


DRAFT_SHA = 'a' * 64


def claims():
    return extract_mentions('甲公司2023年营业收入100万元。甲公司2023年经营现金流10万元。', '甲公司')


def freeze_group(items, specs):
    resource = create_grouped_scope_clarifications(items, specs, draft_sha256=DRAFT_SHA)
    return resource, scope_clarification_metadata(resource, digest(resource))


def test_group_only_changes_selected_occurrences_and_keeps_original_values_locations():
    original = claims()
    before = [item.to_dict() for item in original]
    resource, metadata = freeze_group(original, [{'scope': '母公司', 'selector': {'accounts': ['REVENUE']}}])
    updated = apply_scope_clarification(original, metadata, draft_sha256=DRAFT_SHA)
    assert source_scope_dimensions(updated[0])['scope_header'] == '母公司'
    assert source_scope_dimensions(updated[0])['conditional_verification'] is True
    assert source_scope_dimensions(updated[0])['scope_declaration_draft_sha256'] == DRAFT_SHA
    assert updated[1] is original[1] and source_scope_dimensions(updated[1])['scope_header'] == ''
    for field in ('claim_id', 'text', 'numeric_text', 'candidate_value', 'start', 'end', 'source_numeric_start'):
        assert getattr(updated[0], field) == getattr(original[0], field)
    assert [item.to_dict() for item in original] == before
    assert 'candidate_value' not in json.dumps(resource)
    assert resource['groups'][0]['targets'][0]['candidate_sha256'] == digest(original[0].candidate_value)


def test_selector_fields_are_intersection_and_each_array_is_union():
    original = claims()
    _, metadata = freeze_group(original, [{'scope': '合并', 'selector': {
        'claim_ids': [original[0].claim_id, original[1].claim_id], 'accounts': ['REVENUE']}}])
    updated = apply_scope_clarification(original, metadata, draft_sha256=DRAFT_SHA)
    assert source_scope_dimensions(updated[0])['scope_header'] == '合并'
    assert updated[1] is original[1]
    with pytest.raises(ValueError, match='共同匹配'):
        freeze_group(original, [{'scope': '合并', 'selector': {
            'claim_ids': [original[1].claim_id], 'accounts': ['REVENUE']}}])


@pytest.mark.parametrize('specs', [None, {}, True, ['合并'],
    [{'scope': '合并', 'selector': {}}],
    [{'scope': '', 'selector': {'accounts': ['REVENUE']}}],
    [{'scope': '合并', 'selector': {'accounts': []}}],
    [{'scope': '合并', 'selector': {'accounts': ['REVENUE', 'REVENUE']}}],
    [{'scope': '合并', 'selector': {'accounts': [1]}}],
    [{'scope': '合并', 'selector': {'accounts': 'REVENUE'}}],
    [{'scope': '合并', 'selector': {'table_ids': ['source-table-' + 'b' * 24]}}],
    [{'scope': '合并', 'selector': {'claim_ids': ['../claim']}}],
    [{'scope': '合并', 'selector': {'accounts': ['REVENUE'], 'value': '100'}}],
    [{'scope': '合并', 'selector': {'accounts': ['REVENUE']}, 'candidate_value': '100'}],
])
def test_ui_parameter_validation_rejects_empty_types_paths_and_candidate_numbers(specs):
    with pytest.raises(ValueError):
        validate_scope_clarifications(specs)


@pytest.mark.parametrize('selector', [
    {'claim_ids': ['unknown']}, {'accounts': ['MADE_UP']},
    {'table_ids': ['draft-table-' + 'b' * 24]},
])
def test_unregistered_targets_never_silently_succeed(selector):
    with pytest.raises(ValueError, match='未登记'):
        freeze_group(claims(), [{'scope': '合并', 'selector': selector}])


def test_global_declaration_empty_default_remains_compatible():
    original = claims()
    assert create_grouped_scope_clarifications(original, [], draft_sha256=DRAFT_SHA) is None
    assert apply_scope_clarification(original, None) is original
    global_resource = create_scope_clarification('合并', draft_sha256=DRAFT_SHA)
    updated = apply_scope_clarification(original, scope_clarification_metadata(global_resource, digest(global_resource)))
    assert all(source_scope_dimensions(item)['scope_header'] == '合并' for item in updated)


@pytest.mark.parametrize('mutation', ['draft', 'text', 'value', 'numeric_text', 'position', 'issuer', 'period', 'account'])
def test_resource_binding_rejects_changed_draft_or_original_occurrence(mutation):
    original = claims()
    _, metadata = freeze_group(original, [{'scope': '合并', 'selector': {'accounts': ['REVENUE']}}])
    draft_sha = DRAFT_SHA
    changed = list(original)
    if mutation == 'draft':
        draft_sha = 'c' * 64
    else:
        changes = {'text': {'text': original[0].text + '补充'},
                   'value': {'candidate_value': '999999'},
                   'numeric_text': {'numeric_text': '999'},
                   'position': {'start': original[0].start + 1},
                   'issuer': {'company': '乙公司'},
                   'period': {'period': '2022Y'},
                   'account': {'account': 'CFO'}}
        changed[0] = replace(original[0], **changes[mutation])
    with pytest.raises(ValueError, match='SHA-256|已改变'):
        apply_scope_clarification(changed, metadata, draft_sha256=draft_sha)


@pytest.mark.parametrize('field', ['scope', 'selector', 'target', 'hash', 'free_value'])
def test_tampered_archived_resource_is_rejected(field):
    _, metadata = freeze_group(claims(), [{'scope': '合并', 'selector': {'accounts': ['REVENUE']}}])
    mutated = deepcopy(metadata)
    if field == 'scope':
        mutated['groups'][0]['scope'] = '母公司'
    elif field == 'selector':
        mutated['groups'][0]['selector']['accounts'] = ['CFO']
    elif field == 'target':
        mutated['groups'][0]['targets'][0]['start'] += 1
    elif field == 'hash':
        mutated['resource_id'] = 'scope-clarification-' + 'c' * 20
    else:
        mutated['groups'][0]['targets'][0]['candidate_value'] = '100'
    with pytest.raises(ValueError, match='哈希|结构'):
        apply_scope_clarification(claims(), mutated, draft_sha256=DRAFT_SHA)


def test_original_scope_and_overlapping_opposite_groups_fail_before_mutation():
    original = claims()
    before = [item.to_dict() for item in original]
    with pytest.raises(ValueError, match='冲突'):
        freeze_group(original, [
            {'scope': '合并', 'selector': {'accounts': ['REVENUE']}},
            {'scope': '母公司', 'selector': {'claim_ids': [original[0].claim_id]}}])
    explicit = extract_mentions('2023年合并营业收入100万元。', '甲公司')
    with pytest.raises(ValueError, match='冲突'):
        freeze_group(explicit, [{'scope': '母公司', 'selector': {'accounts': ['REVENUE']}}])
    assert [item.to_dict() for item in original] == before


def test_draft_table_selector_is_local_and_amount_independent():
    _, original, _, _ = sample()
    table_id = scope_table_id(original[0])
    assert table_id and table_id == scope_table_id(original[1])
    assert scope_table_id(replace(original[0], candidate_value='999999')) == table_id
    _, metadata = freeze_group(original, [{'scope': '母公司', 'selector': {'table_ids': [table_id]}}])
    updated = apply_scope_clarification(original, metadata, draft_sha256=DRAFT_SHA)
    assert all(source_scope_dimensions(item)['scope_header'] == '母公司' for item in updated)
    context = deepcopy(original[0].table_context)
    context['title_source']['start'] += 10
    context['title_source']['end'] += 10
    assert scope_table_id(replace(original[0], table_context=context)) != table_id


@pytest.mark.parametrize('grouped', [False, True])
def test_production_apply_freeze_link_refreeze_preserves_explicit_source_table(grouped):
    text, original, facts, reports = sample()
    if grouped:
        _, metadata = freeze_group(original, [{'scope': '合并', 'selector': {'accounts': ['REVENUE']}}])
    else:
        resource = create_scope_clarification('合并', draft_sha256=DRAFT_SHA)
        metadata = scope_clarification_metadata(resource, digest(resource))
    clarified = apply_scope_clarification(original, metadata, draft_sha256=DRAFT_SHA)
    prelinked = [freeze_source_dimensions(item) for item in clarified]
    assert all(source_scope_dimensions(item)['scope_header'] == '合并' for item in prelinked)
    linked, _ = link_quoted_customer_tables(prelinked, text, facts, reports, '2024-04-22')
    assert all('source_dimensions' not in item.table_context for item in linked)
    frozen = [freeze_source_dimensions(replace(item, binding_status='agreed')) for item in linked]
    assert all(validated_table_reference(item) for item in frozen)
    assert all(source_scope_dimensions(item)['scope_header'] == '来源表' for item in frozen)
    assert all(source_dimensions_from_text(item)['scope'] == '来源表' for item in frozen)
    results = [Judge(FactStore(facts), '2024-04-22', require_dual_binding=True).verify(item) for item in frozen]
    assert [item.status for item in results] == ['verified', 'verified']
    assert all(item.verification_basis == 'explicit_disclosed_table_comparison' for item in results)
    assert all('A3' not in item.advisories for item in results)


def test_clarification_after_source_linking_also_keeps_table_transcription_identity():
    text, original, facts, reports = sample()
    linked, _ = link_quoted_customer_tables(original, text, facts, reports, '2024-04-22')
    frozen = [freeze_source_dimensions(item) for item in linked]
    _, metadata = freeze_group(frozen, [{'scope': '母公司', 'selector': {'accounts': ['REVENUE']}}])
    clarified = apply_scope_clarification(frozen, metadata, draft_sha256=DRAFT_SHA)
    assert all(source_scope_dimensions(item)['scope_header'] == '来源表' for item in clarified)
    assert all(item.caliber_hint[0] == '来源表' for item in clarified)


def test_new_version_discards_only_selected_old_proof_then_can_be_frozen_again():
    original = [freeze_source_dimensions(item) for item in claims()]
    before = deepcopy(original[0].table_context['source_dimensions'])
    _, metadata = freeze_group(original, [{'scope': '母公司', 'selector': {'accounts': ['REVENUE']}}])
    updated = apply_scope_clarification(original, metadata, draft_sha256=DRAFT_SHA)
    assert 'source_dimensions' not in updated[0].table_context
    assert original[0].table_context['source_dimensions'] == before
    assert updated[1] is original[1]
    frozen = freeze_source_dimensions(updated[0])
    assert source_dimensions_from_text(frozen)['scope'] == '母公司'
    loaded = replace(frozen, table_context=json.loads(json.dumps(frozen.table_context)))
    assert source_scope_dimensions(loaded) == source_scope_dimensions(frozen)


@pytest.mark.parametrize('mutation', ['scope', 'target', 'root', 'group_index', 'schema', 'legacy_downgrade'])
def test_per_claim_membership_certificate_tampering_blocks_scope(mutation):
    original = claims()
    _, metadata = freeze_group(original, [{'scope': '合并', 'selector': {'accounts': ['REVENUE']}}])
    updated = apply_scope_clarification(original, metadata, draft_sha256=DRAFT_SHA)[0]
    context = deepcopy(updated.table_context)
    declaration = context['scope_declaration']
    if mutation == 'scope':
        declaration['scope'] = '母公司'
    elif mutation == 'target':
        declaration['target']['account'] = 'CFO'
    elif mutation == 'root':
        declaration['resource_binding']['groups'][0]['target_hashes'][0] = 'f' * 64
    elif mutation == 'group_index':
        declaration['group_index'] = True
    elif mutation == 'schema':
        declaration['schema_version'] = 'scope-clarification-target.v3'
    else:
        declaration['schema_version'] = 'scope-clarification.v1'
    dimensions = source_scope_dimensions(replace(updated, table_context=context))
    assert dimensions['scope_header'] == '' and dimensions['scope_declaration_invalid']
    assert dimensions['scope_declaration_conflict']
