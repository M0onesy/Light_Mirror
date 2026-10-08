"""Original research table 13 is a quotation of a registered disclosure grid.

These tests assert source selection, independent binding and deterministic
comparison. They never use draft values to select a financial definition.
"""
from dataclasses import replace
from decimal import Decimal
import itertools
import json
from pathlib import Path

import pytest

from mingjing.dimension_tables import extract_dimension_tables
from mingjing.facts import FactStore
from mingjing.financial_notes import _extract_procurement_source_facts
from mingjing.financial_tools import FinancialDispatcher, ToolDenied
from mingjing.judge import Judge
from mingjing.source_semantics import freeze_source_dimensions
from mingjing.source_table_reference import (link_quoted_tables,
    validated_table_reference, query_quoted_table)


FIXTURES = Path(__file__).parent/'fixtures'
DRAFT = json.loads((FIXTURES/'quoted_operating_report_p30.json').read_text(encoding='utf-8'))
SOURCE = next(item for item in json.loads((FIXTURES/'source_gap_tables_14.json').read_text(encoding='utf-8'))
              if item['doc_sha256'].startswith('31e53f84d52e'))
CUTOFF = '2024-04-22'


def sample():
    claims = extract_dimension_tables(DRAFT['text'], DRAFT['pages'], DRAFT['company'])
    facts = _extract_procurement_source_facts(SOURCE['pages'], path=Path(SOURCE['filename']),
        company=SOURCE['company'], effective_period='2022Y', doc_sha=SOURCE['doc_sha256'],
        disclosure_date=SOURCE['publish_date'], disclosure_version='原披露')
    reports = [dict(name=SOURCE['filename'], companies=[SOURCE['company']],
        sha256=SOURCE['doc_sha256'], report_id='report-'+SOURCE['doc_sha256'][:20],
        publish_date=SOURCE['publish_date'])]
    return claims, facts, reports


def link(claims=None, facts=None, reports=None, text=None):
    original, source, registry = sample()
    linked, diagnostics = link_quoted_tables(original if claims is None else claims,
        DRAFT['text'] if text is None else text, source if facts is None else facts,
        registry if reports is None else reports, CUTOFF)
    return linked, diagnostics


def agreed(claim):
    return freeze_source_dimensions(replace(claim, binding_status='agreed'))


def changed_draft_cell(old, new):
    """Change one original cell before parsing, retaining its source geometry."""
    text = DRAFT['text'].replace(old, new)
    lines, cursor = [], 0
    for line in DRAFT['pages'][0]['lines']:
        raw = line['text'].replace(old, new)
        start = text.find(raw, cursor)
        assert start >= 0
        end = start+len(raw)
        lines.append({**line, 'text':raw, 'start':start, 'end':end})
        cursor = end
    pages = [{**DRAFT['pages'][0], 'text':text, 'end':len(text), 'lines':lines}]
    return text, extract_dimension_tables(text, pages, DRAFT['company'])


def test_all_18_original_occurrences_resolve_by_geometry_and_explicit_disclosure():
    original, facts, _ = sample()
    linked, diagnostics = link()
    assert len(original) == len(linked) == len(facts) == 18
    assert {(claim.start, claim.end) for claim in linked} == {(claim.start, claim.end) for claim in original}
    assert not diagnostics
    assert all(fact.scope == '未知' for fact in facts)
    assert all(fact.source_row_index == 0 for fact in facts)
    judge = Judge(FactStore(facts), CUTOFF, require_dual_binding=True)
    results = [judge.verify(agreed(claim)) for claim in linked]
    assert [result.status for result in results] == ['verified']*18
    assert all(result.verification_basis == 'explicit_disclosed_table_comparison' for result in results)
    assert all(result.operands[0]['source_relation'] == claim.relation
               for claim, result in zip(linked, results))
    assert all(result.evidence and all(':p132:' in evidence for evidence in result.evidence)
               for result in results)
    for claim in linked:
        proof = validated_table_reference(claim)
        assert proof['report_sha256'] == SOURCE['doc_sha256']
        assert proof['source_table_ids'] == ['source-table-c0289d284ec7ba23b0f55e04']
        assert proof['source_row_label'] == claim.table_context['row_label']
        assert proof['relation'] == claim.relation


def test_original_caption_conflict_remains_visible_even_when_every_numeric_quotation_matches():
    linked, _ = link()
    _, facts, _ = sample()
    for claim in linked:
        result = Judge(FactStore(facts), CUTOFF, require_dual_binding=True).verify(agreed(claim))
        assert result.status == 'verified' and result.advisories == ['A4']
        assert any('共建' in basis and '对外采购' in basis for basis in result.basis)
        assert '未推断' in result.precision_note


@pytest.mark.parametrize('subject', ['海光信息公司','乙公司','并行科技与海光信息公司',
    '公司与乙公司','并行科技公司的子公司','360公司'])
def test_explicit_other_issuer_in_title_cannot_be_erased_by_registered_business_rows(subject):
    text, original = changed_draft_cell('公司通过', subject+'通过')
    linked, _ = link(claims=original, text=text)
    _, facts, _ = sample()
    results = [Judge(FactStore(facts), CUTOFF, require_dual_binding=True).verify(agreed(c)) for c in linked]
    assert all(result.status == 'abstained' and result.abstention_code == 'X3' for result in results)
    assert all(not result.operands for result in results)


def test_direct_quoted_query_cannot_bypass_different_issuer_in_title():
    linked, _ = link()
    _, facts, _ = sample()
    from mingjing.source_table_reference import _digest
    target = linked[0]
    caption = {**target.table_context['title_source']}
    caption['text'] = caption['text'].replace('公司通过', '乙公司通过')
    caption['end'] = caption['start']+len(caption['text'])
    proof = {**target.table_context['source_table_reference'], 'caption':caption}
    proof['sha256'] = _digest({key:value for key,value in proof.items() if key!='sha256'})
    changed = replace(target, table_context={**target.table_context,
        'title':caption['text'], 'title_source':caption, 'source_table_reference':proof})
    assert validated_table_reference(changed) is None
    assert query_quoted_table(FactStore(facts),changed,CUTOFF).status=='source_required'


@pytest.mark.parametrize('subject', ['公司','并行科技','北京并行科技股份有限公司'])
def test_exact_issuer_alias_in_title_preserves_all_18_source_cells(subject):
    text, original = changed_draft_cell('公司通过', subject+'通过')
    linked, _ = link(claims=original, text=text)
    _, facts, _ = sample()
    results = [Judge(FactStore(facts), CUTOFF, require_dual_binding=True).verify(agreed(c)) for c in linked]
    assert all(result.status == 'verified' for result in results)


@pytest.mark.parametrize('relation,delta', [('value', Decimal('1000000')), ('yoy', Decimal('.01'))])
def test_wrong_amount_and_wrong_disclosed_yoy_are_e1_transfer_errors(relation, delta):
    linked, _ = link()
    _, facts, _ = sample()
    claim = next(claim for claim in linked if claim.relation == relation)
    claim = replace(agreed(claim), candidate_value=str(Decimal(claim.candidate_value)+delta))
    result = Judge(FactStore(facts), CUTOFF, require_dual_binding=True).verify(claim)
    assert result.status == 'violated' and result.error_type == 'E1'
    assert result.verification_basis == 'explicit_disclosed_table_comparison'
    assert result.correction and len(result.correction) >= 5
    assert result.operands[0]['source_relation'] == relation
    assert '披露表' in result.formula and result.advisories == ['A4']


@pytest.mark.parametrize('original,wrong', [('3131.12', '9999.99'), ('-15.57%', '-25.57%')])
def test_wrong_original_cell_is_detected_after_source_extraction_and_binding(original, wrong):
    text, claims = changed_draft_cell(original, wrong)
    linked, _ = link(claims=claims, text=text)
    _, facts, _ = sample()
    target = next(claim for claim in linked if claim.numeric_text == wrong)
    result = Judge(FactStore(facts), CUTOFF, require_dual_binding=True).verify(agreed(target))
    assert result.status == 'violated' and result.error_type == 'E1'
    assert result.observed == target.candidate_value


@pytest.mark.parametrize('citation', [
    '数据来源：互联网', '数据来源：公司年报', '数据来源：公司第一轮问询函回复',
])
def test_unfamiliar_or_different_original_citation_cannot_borrow_the_second_inquiry_table(citation):
    text, claims = changed_draft_cell('数据来源：公司第二轮问询函回复、开源证券研究所', citation)
    linked, _ = link(claims=claims, text=text)
    assert len(linked) == 18
    assert not any(validated_table_reference(claim) for claim in linked)


def test_2020_yoy_is_verified_as_disclosed_transfer_without_fabricated_2019_operands():
    linked, _ = link()
    _, facts, _ = sample()
    assert not any(fact.period == '2019Y' for fact in facts)
    selected = [claim for claim in linked if claim.period == '2020Y' and claim.relation == 'yoy']
    assert len(selected) == 3
    for claim in selected:
        result = Judge(FactStore(facts), CUTOFF, require_dual_binding=True).verify(agreed(claim))
        assert result.status == 'verified' and len(result.operands) == 1
        assert result.operands[0]['period'] == '2020Y'
        assert any('未宣称' in basis and '独立重算' in basis for basis in result.basis)
        assert result.rule_ids == ['E1-value']


@pytest.mark.parametrize('field,value', [
    ('name', '并行科技关于第一轮问询回复.pdf'),
    ('publish_date', ''), ('publish_date', '2024-04-23'),
    ('companies', ['陌生公司']), ('sha256', 'a'*64),
])
def test_missing_wrong_issuer_unknown_date_or_future_document_cannot_authorize_source(field, value):
    _, _, reports = sample()
    reports[0] = {**reports[0], field: value}
    claims, _ = link(reports=reports)
    assert not any(validated_table_reference(claim) for claim in claims)


@pytest.mark.parametrize('field,value', [
    ('dimension_id', '共建算力资源'), ('dimension_kind', 'customer'),
    ('definition_id', 'metric:revenue-share'), ('row_label', '共建超算云服务收入'),
])
def test_unregistered_or_tampered_dimensions_cannot_reuse_a_valid_source_proof(field, value):
    linked, _ = link()
    _, facts, _ = sample()
    original = agreed(linked[0])
    tampered = replace(original, table_context={**original.table_context, field: value})
    assert validated_table_reference(tampered) is None
    assert Judge(FactStore(facts), CUTOFF, require_dual_binding=True).verify(tampered).status == 'abstained'


def test_source_selection_uses_exact_rows_and_latest_date_not_matching_values():
    original, facts, reports = sample()
    # A newer eligible disclosure with different amounts remains the selected
    # source, so a matching older document cannot rescue the draft quotation.
    newer = [replace(fact, fact_id=fact.fact_id+'-new', source_doc='d'*12,
                     publish_date='2023-04-05', value=fact.value+Decimal('999')) for fact in facts]
    reports += [{**reports[0], 'sha256':'d'*64, 'report_id':'report-'+'d'*20, 'publish_date':'2023-04-05'}]
    linked, _ = link(claims=original, facts=[*facts,*newer], reports=reports)
    assert all(validated_table_reference(claim)['source_doc'] == 'd'*12 for claim in linked)
    assert Judge(FactStore([*facts,*newer]), CUTOFF, require_dual_binding=True).verify(agreed(linked[0])).status == 'violated'
    changed, _ = link(claims=[replace(claim, candidate_value='999999999') for claim in original],
                      facts=[replace(fact, value=Decimal('-123456789')) for fact in facts])
    assert all(validated_table_reference(claim)['source_doc'] == SOURCE['doc_sha256'][:12] for claim in changed)


def test_two_eligible_documents_with_same_disclosure_date_are_explicitly_ambiguous():
    _, facts, reports = sample()
    competing = [replace(fact, fact_id=fact.fact_id+'-other', source_doc='d'*12) for fact in facts]
    reports += [{**reports[0], 'sha256':'d'*64, 'report_id':'report-'+'d'*20}]
    claims, diagnostics = link(facts=[*facts,*competing], reports=reports)
    assert not any(validated_table_reference(claim) for claim in claims)
    assert diagnostics == [{'kind':'ambiguous_quoted_operating_table',
                           'caption_start':claims[0].table_context['title_source']['start']}]


def test_conflicting_source_cell_abstains_instead_of_recomputing_from_matching_amounts():
    linked, _ = link()
    _, facts, _ = sample()
    target = next(claim for claim in linked if claim.relation == 'yoy' and claim.period == '2021Y')
    matching = next(fact for fact in facts if fact.account == target.account
                    and fact.period == target.period and fact.source_relation == target.relation)
    conflict = replace(matching, fact_id='conflict', value=Decimal('.99'))
    result = Judge(FactStore([*facts, conflict]), CUTOFF, require_dual_binding=True).verify(agreed(target))
    assert result.status == 'abstained' and result.abstention_code == 'X4'
    assert not result.operands


def test_reporter_compact_operand_keeps_reported_yoy_relation_and_dimension():
    _, facts, _ = sample()
    growth = next(fact for fact in facts if fact.account == 'REVENUE' and fact.source_relation == 'yoy')
    compact = FinancialDispatcher.compact_operand(growth.to_dict())
    assert compact['source_relation'] == 'yoy'
    assert compact['unit'] == compact['value_unit'] == '无量纲比例'
    assert compact['display_unit'] == '%'
    assert compact['dimension_kind'] == growth.dimension_kind


def test_missing_one_disclosed_yoy_does_not_use_amount_or_other_table_fallback():
    linked, _ = link()
    _, facts, _ = sample()
    target = next(claim for claim in linked if claim.account == 'PURCHASE_AMOUNT'
                  and claim.period == '2020Y' and claim.relation == 'yoy')
    kept = [fact for fact in facts if not (fact.account == target.account
            and fact.period == target.period and fact.source_relation == target.relation)]
    unrelated = replace(next(fact for fact in facts if fact.account == target.account
        and fact.period == target.period and fact.source_relation == target.relation),
        fact_id='uncited-table', source_table_id='source-table-'+'e'*24)
    result = Judge(FactStore([*kept, unrelated]), CUTOFF, require_dual_binding=True).verify(agreed(target))
    assert result.status == 'abstained' and result.abstention_code == 'X1'
    assert query_quoted_table(FactStore(kept), agreed(target), CUTOFF).status == 'missing'


def test_independent_repeated_source_tables_all_must_have_the_target_cell_and_agree():
    _, facts, _ = sample()
    repeated = [replace(fact, fact_id=fact.fact_id+'-repeated', source_table_id='source-table-'+'e'*24)
                for fact in facts]
    linked, _ = link(facts=[*facts,*repeated])
    target = next(claim for claim in linked if claim.account == 'PURCHASE_AMOUNT'
                  and claim.period == '2020Y' and claim.relation == 'yoy')
    assert len(validated_table_reference(target)['source_table_ids']) == 2
    judge = Judge(FactStore([*facts,*repeated]), CUTOFF, require_dual_binding=True)
    assert judge.verify(agreed(target)).status == 'verified'
    partial = [fact for fact in repeated if not (fact.account == target.account
               and fact.period == target.period and fact.source_relation == target.relation)]
    result = Judge(FactStore([*facts,*partial]), CUTOFF, require_dual_binding=True).verify(agreed(target))
    assert result.status == 'abstained' and result.abstention_code == 'X1'


def _dispatcher():
    claims, _ = link()
    _, facts, reports = sample()
    dispatcher = FinancialDispatcher('test-operating', 'lock', claims, facts, [], reports,
                                    CUTOFF, task_company=DRAFT['company'])
    counter = itertools.count()
    def call(role, tool, params):
        return dispatcher.dispatch(dict(session_id=role, role=role, run_id='test-operating',
            resource_lock='lock', tool_name=tool, params=params, call_id=f'call-{next(counter)}'))
    return dispatcher, claims, call


def test_independent_ab_bindings_and_financial_tools_verify_all_18_source_cells():
    dispatcher, claims, call = _dispatcher()
    for role in ('planner', 'verifier'):
        dispatcher.register_session(role, role, [claim.claim_id for claim in claims])
        for claim in claims:
            context = call(role, 'mj_read_context', {'mention_id':claim.claim_id})
            assert context['source_dimensions']['scope'] == '来源表'
            assert 'candidate_value' not in context
            assert claim.numeric_text.strip() not in context['text']
            binding = dict(company=claim.company, account=claim.account, period=claim.period,
                scope='来源表', relation=claim.relation, base_period=claim.base_period or '')
            assert call(role, 'mj_submit_binding', {'mention_id':claim.claim_id, 'binding':binding})['accepted']
    assert all(claim.binding_status == 'agreed' for claim in dispatcher.freeze_bindings())
    dispatcher.register_session('coordinator', 'coordinator', [claim.claim_id for claim in claims])
    for claim in claims:
        fact = call('coordinator', 'mj_get_fact', {'binding_id':claim.claim_id})
        assert fact['query_outcome']['status'] == 'found'
        assert fact['required_rule_ids'] == ['E1-value']
        assert fact['fact']['source_relation'] == claim.relation
        call('coordinator', 'mj_get_rule', {'rule_id':'E1-value'})
        result = call('coordinator', 'mj_verify', {'binding_id':claim.claim_id})
        assert result['status'] == 'verified'
        assert result['verification_basis'] == 'explicit_disclosed_table_comparison'
    assert len([event for event in dispatcher.trace if event['tool']=='mj_verify']) == 18


def test_missing_or_disagreeing_binder_cannot_promote_source_cells():
    dispatcher, claims, call = _dispatcher()
    target = claims[0]
    for role in ('planner', 'verifier'):
        dispatcher.register_session(role, role, [target.claim_id])
    binding = dict(company=target.company, account=target.account, period=target.period,
                   scope='来源表', relation=target.relation, base_period=target.base_period or '')
    call('planner', 'mj_submit_binding', {'mention_id':target.claim_id, 'binding':binding})
    dispatcher.freeze_bindings()
    assert dispatcher.claims[target.claim_id].binding_status == 'disagreed'
    from mingjing.financial_tools import FinancialArgumentError
    with pytest.raises(FinancialArgumentError) as caught:
        call('verifier', 'mj_submit_binding', {'mention_id':target.claim_id,
                                           'binding':{**binding,'account':'REVENUE'}})
    assert caught.value.rejection()['error']['code'] == 'binding_source_conflict'
    assert not dispatcher.bindings['verifier']
    dispatcher.freeze_bindings()
    assert dispatcher.claims[target.claim_id].binding_status == 'disagreed'
    assert Judge(dispatcher.store, CUTOFF, require_dual_binding=True).verify(
        dispatcher.claims[target.claim_id]).abstention_code == 'X3'


def test_binders_cannot_read_fact_values_or_submit_candidate_amounts():
    dispatcher, claims, call = _dispatcher()
    target = claims[0]
    dispatcher.register_session('planner', 'planner', [target.claim_id])
    with pytest.raises(ToolDenied, match='role cannot call'):
        call('planner', 'mj_get_fact', {'binding_id':target.claim_id})
    binding = dict(company=target.company, account=target.account, period=target.period,
                   scope='来源表', relation=target.relation, base_period=target.base_period or '')
    with pytest.raises((ValueError, ToolDenied)):
        call('planner', 'mj_submit_binding', {'mention_id':target.claim_id,
                                            'binding':{**binding,'candidate_value':'999999'}})
    assert not dispatcher.bindings['planner']
