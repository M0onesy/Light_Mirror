"""Full original lower-bound occurrence, independent binding and real evidence.

These exposed source fixtures are development regressions, not human gold.
"""
from copy import deepcopy
from dataclasses import replace
import hashlib

import pytest

from test_quoted_quarter_shares import DATA, CUTOFF, facts, claims, registry, rewritten
from mingjing.facts import FactStore
from mingjing.financial_tools import FinancialDispatcher, FinancialRequirementError
from mingjing.audit import kernel_fingerprint
from mingjing.judge import Judge
from mingjing.source_quarter_quotation import (extract_source_gaps, attach_threshold_references,
                                               validated_threshold_reference)
from mingjing.source_table_reference import link_quoted_tables
from mingjing.source_semantics import freeze_source_dimensions, source_binding_issue
from mingjing.source_gaps import load_source_gaps


def threshold_claim(pages=None, source=None):
    source = source or DATA['source']
    text, regions, ledger, raw = claims(pages)
    items, _ = link_quoted_tables(raw,text,facts(source),registry(),CUTOFF,pages=regions)
    gaps = extract_source_gaps(source['pages'],path=source['filename'],company=DATA['company'],
                              doc_sha=source['sha256'],disclosure_date=source['publish_date'])
    linked = attach_threshold_references(items,text,regions,registry(),CUTOFF,gaps)
    assert [(c.claim_id,c.start,c.end) for c in linked] == [(c.claim_id,c.start,c.end) for c in raw]
    selected = [c for c in linked if validated_threshold_reference(c)]
    assert len(selected) == 1
    return freeze_source_dimensions(selected[0]), ledger


def test_authoritative_strict_bound_never_uses_forged_point_candidate():
    claim, _ = threshold_claim()
    result = Judge(FactStore(facts()),CUTOFF,require_dual_binding=True).verify(
        replace(claim,binding_status='agreed',candidate_value='999999'))
    assert result.status == 'verified'
    assert result.rule_ids == ['SOURCE-threshold-entailment']
    assert result.verification_basis == 'source_threshold_assertion_entailment'
    assert result.expected_interval['lower'] == '0.70'
    assert result.expected_interval['upper'] is None
    assert result.expected_interval['lower_closed'] is False
    assert result.operands[0]['eligible_as_point_fact'] is False
    assert 'value' not in result.operands[0]
    assert result.evidence and any(':p182:' in e for e in result.evidence)


def test_stronger_original_assertion_is_unknown_instead_of_inventing_actual_ratio():
    claim, _ = threshold_claim(rewritten(DATA['draft_pages'],'70%','80%'))
    assert claim.numeric_text == '80%'
    result = Judge(FactStore(facts()),CUTOFF).verify(replace(claim,binding_status='single'))
    assert result.status == 'abstained' and result.abstention_code == 'X4'
    assert result.expected_interval['lower'] == '0.70'


def test_changed_official_bound_changes_result_without_value_based_source_selection():
    source = deepcopy(DATA['source'])
    source['pages'] = rewritten(source['pages'],'已超过70%','已超过60%')
    claim, _ = threshold_claim(source=source)
    result = Judge(FactStore(facts(source)),CUTOFF).verify(replace(claim,binding_status='single'))
    assert result.status == 'abstained' and result.abstention_code == 'X4'


def test_pending_or_disagreed_binding_cannot_be_bypassed_by_official_bound():
    claim, _ = threshold_claim()
    for status in ('pending','disagreed','unresolved'):
        result = Judge(FactStore(facts()),CUTOFF,require_dual_binding=True).verify(
            replace(claim,binding_status=status))
        assert result.status == 'abstained' and result.abstention_code == 'X3'


@pytest.mark.parametrize('cutoff',[None,'2023-04-03','2024-04-23'])
def test_frozen_assertion_cannot_be_reused_as_a_different_as_of_task(cutoff):
    claim, _ = threshold_claim()
    result=Judge(FactStore(facts()),cutoff).verify(replace(claim,binding_status='single'))
    assert result.status=='abstained' and result.abstention_code=='X5'


def test_numeric_local_position_tampering_revokes_threshold_reference():
    claim, _ = threshold_claim()
    assert validated_threshold_reference(claim)
    assert validated_threshold_reference(replace(claim,source_numeric_start=0)) is None
    assert validated_threshold_reference(replace(claim,text=claim.text+'另一段')) is None


def test_pinned_reference_and_formula_interpreter_are_frozen_authorities():
    claim, _ = threshold_claim()
    assert validated_threshold_reference(replace(claim,declared_source='another-source')) is None
    assert validated_threshold_reference(replace(claim,query_strategy='latest_as_of')) is None
    assert 'module:thresholds' in kernel_fingerprint() and 'module:rules' in kernel_fingerprint()
    text, regions, _, raw=claims()
    source=DATA['source']
    gaps=extract_source_gaps(source['pages'],path=source['filename'],company=DATA['company'],
        doc_sha=source['sha256'],disclosure_date=source['publish_date'])
    for declaration in ('another-source',None):
        pinned=[replace(c,query_strategy='pinned',declared_source=declaration) if c.numeric_text=='70%' else c for c in raw]
        linked,_=link_quoted_tables(pinned,text,facts(),registry(),CUTOFF,pages=regions)
        attached=attach_threshold_references(linked,text,regions,registry(),CUTOFF,gaps)
        assert not any(validated_threshold_reference(c) for c in attached)


def test_dual_binding_and_tool_reads_use_registered_assertion_rule_not_e1():
    claim, _ = threshold_claim()
    dispatcher = FinancialDispatcher('r','lock',[claim],facts(),[],registry(),CUTOFF,
                                     task_company=DATA['company'])
    binding = {'company':claim.company,'account':claim.account,'period':claim.period,
               'scope':'来源表','relation':'gt','base_period':''}
    for role in ('planner','verifier'):
        dispatcher.register_session(role,role)
        mid, accepted = dispatcher.validated_binding(dispatcher.sessions[role],role,
                                                      {'mention_id':claim.claim_id,'binding':binding})
        dispatcher.bindings[role][mid] = accepted
    bound = dispatcher.freeze_bindings()[0]
    assert bound.binding_status == 'agreed' and source_binding_issue(bound,strict=True) is None
    dispatcher.register_session('c','coordinator')
    identity = dispatcher.sessions['c']
    operand = dispatcher._execute('coordinator',identity,'mj_get_fact',{'binding_id':bound.claim_id})
    assert operand['fact'] is None and operand['source_assertion']['eligible_as_point_fact'] is False
    assert operand['required_rule_ids'] == ['SOURCE-threshold-entailment']
    identity['rules_read'] = {'E1-value'}
    with pytest.raises(FinancialRequirementError) as rejected:
        dispatcher._execute('coordinator',identity,'mj_verify',{'binding_id':bound.claim_id})
    assert rejected.value.rejection()['required_rule_ids'] == ['SOURCE-threshold-entailment']
    dispatcher._execute('coordinator',identity,'mj_get_rule',{'rule_id':'SOURCE-threshold-entailment'})
    result = dispatcher._execute('coordinator',identity,'mj_verify',{'binding_id':bound.claim_id})
    assert result['status'] == 'verified'


def test_selected_pdf_provides_registered_diagnostic_without_fact_cache(tmp_path,monkeypatch):
    source = tmp_path/'official.pdf'
    source.write_bytes(b'Frozen official source test bytes')
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    row = {'id':'report-'+sha[:20],'path':str(source),'sha256':sha,
           'publish_date':DATA['source']['publish_date']}
    monkeypatch.setattr('mingjing.parser.extract_text',lambda *a,**kw:DATA['source']['pages'])
    loaded = load_source_gaps([source],[row],DATA['company'],CUTOFF)
    lower = [r for r in loaded if r['gap_kind']=='source-threshold-disclosure']
    assert len(lower) == 1 and lower[0]['doc_sha256'] == sha
    assert lower[0]['public_at_cutoff'] and 'raw_value' not in lower[0]
    source.write_bytes(b'Changed input')
    with pytest.raises(ValueError,match='frozen hash'):
        load_source_gaps([source],[row],DATA['company'],CUTOFF)


def test_inert_cached_quote_cannot_be_promoted_using_another_documents_hash(tmp_path):
    import json
    source=DATA['source']
    gaps=extract_source_gaps(source['pages'],path=source['filename'],company=DATA['company'],
        doc_sha=source['sha256'],disclosure_date=source['publish_date'])
    forged=deepcopy(gaps[0])
    forged['source_context']['source_quote']['text']=forged['source_context']['source_quote']['text'].replace('70%','99%')
    (tmp_path/'facts.json').write_text(json.dumps({'source_gaps':[forged]}),encoding='utf8')
    rows=load_source_gaps([tmp_path],[{'id':source['id'],'sha256':source['sha256'],
                           'publish_date':source['publish_date']}],DATA['company'],CUTOFF)
    assert rows==[]
