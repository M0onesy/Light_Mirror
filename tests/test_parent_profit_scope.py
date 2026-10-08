"""Professional source definitions, not a blanket consolidated default."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from mingjing.judge import _source_scope_issue
from mingjing.mentions import extract_mentions
from mingjing.scope import (apply_scope_clarification, create_scope_clarification,
                            scope_clarification_metadata, source_scope_dimensions)
from mingjing.source_semantics import (freeze_source_dimensions, source_binding_issue,
                                      source_dimensions_from_text)
from mingjing.table_mentions import extract_table_mentions


def parse_table(text):
    return extract_table_mentions(text, [], [], '甲公司', '')


@pytest.mark.parametrize('label', ['归母净利润', '归属母公司净利润', '归属于母公司净利润',
                                  '归属于母公司所有者的净利润', '归属于母公司股东的净利润',
                                  '归属于上市公司股东的净利润', '归属于挂牌公司股东的净利润'])
def test_parent_profit_label_defines_consolidated_scope_before_model_binding(label):
    claim = extract_mentions(f'甲公司2023年{label}100万元。', '甲公司')[0]
    frozen = freeze_source_dimensions(claim)
    proof = source_dimensions_from_text(frozen)
    assert proof['account'] == 'NP_PARENT' and proof['scope'] == '合并'
    assert proof['scope_provenance'] == 'source_account_definition'
    assert proof['scope_definition_basis']['clause_id'] == 'CAS33-三十六'
    assert proof['scope_definition_basis']['professional_signoff_status'] == 'pending'
    assert _source_scope_issue(frozen, strict=True, check_binding=False) is None
    assert source_binding_issue(frozen, strict=True) is None
    assert not set(proof) & {'value', 'candidate_value', 'expected', 'verdict'}
    forged = replace(frozen, account='REVENUE', binding_status='agreed')
    assert source_binding_issue(forged, strict=True)[0] == 'X3'


def test_definition_applies_to_exact_target_and_does_not_propagate_to_revenue_or_plain_profit():
    for text in ['2023年营业收入100万元，归母净利润10万元。',
                 '2023年归母净利润10万元，营业收入100万元。',
                 '2023年归母净利润10万元，净利润20万元。']:
        claims = extract_mentions(text, '甲公司')
        assert len(claims) == 2
        for claim in claims:
            dimensions = source_scope_dimensions(claim)
            assert dimensions['scope_header'] == ('合并' if claim.account == 'NP_PARENT' else '')
    ordinary = extract_mentions('2023年营业收入100万元。', '甲公司')[0]
    forged = replace(ordinary, account='NP_PARENT', caliber_hint=('合并', '未扣非'))
    assert source_scope_dimensions(forged)['scope_header'] == ''


@pytest.mark.parametrize('text', ['2023年母公司口径归母净利润100万元。',
                                  '2023年合并及母公司报表归母净利润100万元。',
                                  '2023年归母净利润100万元，本段不按合并口径。'])
def test_explicit_parent_entity_negation_and_mixed_scope_conflict_with_definition(text):
    claim = extract_mentions(text, '甲公司')[0]
    dimensions = source_scope_dimensions(claim)
    assert dimensions['scope_header'] == '' and dimensions['source_scope_conflict']
    assert _source_scope_issue(claim, strict=True, check_binding=False)[0] == 'X3'
    resource = create_scope_clarification('合并', draft_sha256='a'*64)
    with pytest.raises(ValueError, match='冲突'):
        apply_scope_clarification([claim], scope_clarification_metadata(resource, 'b'*64))


def test_parent_entity_profit_remains_a_different_account_and_scope():
    claim = extract_mentions('甲公司2023年母公司净利润100万元。', '甲公司')[0]
    proof = source_dimensions_from_text(freeze_source_dimensions(claim))
    assert proof['account'] == 'NP' and proof['scope'] == '母公司'
    assert 'scope_definition_id' not in proof


def test_independent_yoy_has_verified_prior_label_spans_and_no_prior_amounts():
    text = '指标\n2022A 2023A\n归母净利润(万元)\n100\n120\nYOY(%)\n10\n20\n'
    claims = parse_table(text)
    assert len(claims) == 4
    growth = claims[-1]
    lineage = growth.table_context['account_lineage']
    assert lineage['label_text'] == '归母净利润(万元)'
    assert lineage['label_sha256'] == hashlib.sha256(lineage['label_text'].encode('utf-8')).hexdigest()
    for span in lineage['label_spans']:
        assert text[span['start']:span['end']] == span['text']
    assert growth.text.startswith('归母净利润(万元)\nYOY(%)\n')
    assert '100' not in growth.text and '120' not in growth.text
    assert growth.text[growth.source_numeric_start:growth.source_numeric_start+len(growth.numeric_text)] == growth.numeric_text
    proof = source_dimensions_from_text(freeze_source_dimensions(growth))
    assert proof['scope'] == '合并' and proof['scope_definition_source'] == 'validated_preceding_table_label'
    assert _source_scope_issue(growth, strict=True, check_binding=False) is None
    assert source_binding_issue(growth, strict=True) is None


@pytest.mark.parametrize('mutation', ['missing', 'hash', 'span', 'context', 'title'])
def test_tampered_or_missing_yoy_lineage_cannot_use_account_hint(mutation):
    growth = parse_table('指标\n2022A 2023A\n归母净利润(万元)\n100\n120\nYOY(%)\n10\n20\n')[-1]
    context = deepcopy(growth.table_context)
    if mutation == 'missing':
        context.pop('account_lineage')
    elif mutation == 'hash':
        context['account_lineage']['label_sha256'] = 'a'*64
    elif mutation == 'span':
        context['account_lineage']['label_spans'][0]['start'] += 1
    elif mutation == 'title':
        context['title'] = '另一表格'
    else:
        growth = replace(growth, text=growth.text.replace('归母净利润', '其他净利润'))
    forged = replace(growth, table_context=context)
    assert source_scope_dimensions(forged)['scope_header'] == ''
    assert _source_scope_issue(forged, strict=True, check_binding=False)[0] == 'X3'


def test_plain_yoy_account_hint_cannot_invent_parent_profit_definition():
    claim = parse_table('指标\n2022A 2023A\n营业收入(万元)\n100\n120\nYOY(%)\n10\n20\n')[-1]
    forged = replace(claim, account='NP_PARENT', table_context={**claim.table_context, 'account_hint': 'NP_PARENT'})
    assert source_scope_dimensions(forged)['scope_header'] == ''
    assert _source_scope_issue(forged, strict=True, check_binding=False)[0] == 'X3'


def test_a_new_table_or_unknown_primary_row_cannot_inherit_parent_profit_scope():
    for text in ['指标\n2022A 2023A\n归母净利润(万元)\n100\n120\n'
                 '主要财务比率\n2022A 2023A\nYOY(%)\n10\n20\n',
                 '指标\n2022A 2023A\n归母净利润(万元)\n100\n120\n'
                 '2021A 2022A\nYOY(%)\n10\n20\n',
                 '指标\n2022A 2023A\n归母净利润(万元)\n100\n120\n'
                 '未知指标\n1\n2\nYOY(%)\n10\n20\n']:
        growth = parse_table(text)[-1]
        assert source_scope_dimensions(growth)['scope_header'] == ''
        assert 'account_lineage' not in growth.table_context


def test_parent_profit_in_parent_entity_table_is_a_source_conflict():
    claims = parse_table('母公司利润表(万元)\n2022A 2023A\n归母净利润\n100\n120\nYOY(%)\n10\n20\n')
    assert all(source_scope_dimensions(c)['scope_header'] == '' for c in claims)
    assert all(source_scope_dimensions(c)['source_scope_conflict'] for c in claims)


def test_frozen_definition_and_validated_yoy_lineage_survive_json_replay():
    from mingjing.replay import _claim_from_dict
    original = parse_table('指标\n2022A 2023A\n归母净利润(万元)\n100\n120\nYOY(%)\n10\n20\n')[-1]
    frozen = freeze_source_dimensions(original)
    loaded = _claim_from_dict(json.loads(json.dumps(frozen.to_dict(), ensure_ascii=False)))
    assert source_dimensions_from_text(loaded) == source_dimensions_from_text(frozen)
    assert source_scope_dimensions(loaded) == source_scope_dimensions(frozen)
    assert loaded.candidate_value == frozen.candidate_value and loaded.numeric_text == frozen.numeric_text
    assert _source_scope_issue(loaded, strict=True, check_binding=False) is None


def test_registered_definition_quotes_match_local_standard_and_cards_remain_unsigned():
    root = Path(__file__).resolve().parents[1]/'rulepack'
    clauses = json.loads((root/'source_clauses.json').read_text(encoding='utf-8'))
    clause = next(v for v in clauses if v['id'] == 'CAS33-三十六')
    assert clause['quote'] in (root/'sources/CAS33.md').read_text(encoding='utf-8')
    assert hashlib.sha256(clause['quote'].encode('utf-8')).hexdigest() == clause['quote_sha256']
    cards = json.loads((root/'caliber.json').read_text(encoding='utf-8'))
    for card in cards:
        if card['id'] in {'CAL-014', 'CAL-015', 'CAL-016', 'CAL-017', 'CAL-018'}:
            ref = card['basis_refs'][0]
            assert ref['clause_id'] == clause['id'] and ref['quote'] == clause['quote']
            assert ref['quote_sha256'] == clause['quote_sha256']
            assert card['approved_by'] is None and card['review_status'] == 'pending_human_signoff'
