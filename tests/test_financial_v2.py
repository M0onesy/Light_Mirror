from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import copy
import json
import shutil

import pytest

from mingjing.analysis import analyze_financials, non_recurring_adjustment, cash_content, extract_accounting_events
from mingjing.facts import FactStore, fact_from_dict
from mingjing.judge import Judge
from mingjing.models import AtomicClaim, Fact, SemanticBinding
from mingjing.rules import RuleError, evaluate_rule, execute_rule, load_rule_cards, rule_applicability, validate_rulepack

ROOT = Path(__file__).resolve().parents[1]


def fact(identifier='current', period='2023Y', value='120', **kwargs):
    return Fact(identifier, '甲', 'REVENUE', period, value=Decimal(value),
                publish_date=kwargs.pop('publish_date', '2024-04-01'),
                source_doc=kwargs.pop('source_doc', 'report-new'), evidence=(identifier+':e',), **kwargs)


def claim(**kwargs):
    return AtomicClaim('m1', '甲2023年营业收入120元。', 11, 16,
                       company='甲', account='REVENUE', period='2023Y',
                       candidate_value='120', unit='元', **kwargs)


def test_unknown_and_invalid_publication_cannot_pass_cutoff():
    for date in ('', '2024-02-31', 'garbage'):
        store = FactStore([fact(publish_date=date)])
        assert store.query('甲', 'REVENUE', '2023Y', cutoff='2024-04-22') is None
        result = Judge(store, '2024-04-22').verify(claim())
        assert result.abstention_code == 'X4'
    with pytest.raises(ValueError):
        FactStore([fact()]).query('甲', 'REVENUE', '2023Y', cutoff='2024-02-31')


def test_latest_same_report_and_pinned_are_distinct():
    facts = [fact('current'), fact('prior-in-new', '2022Y', '100'),
             fact('prior-in-old', '2022Y', '80', source_doc='report-old', publish_date='2023-04-01'),
             fact('later-correction', '2022Y', '90', source_doc='report-correction', publish_date='2024-04-10', version='更正后')]
    store = FactStore(facts)
    assert store.query('甲','REVENUE','2022Y',cutoff='2024-04-22').value == Decimal('90')
    assert store.query('甲','REVENUE','2022Y',cutoff='2024-04-22',strategy='same_report',source_doc='report-new').value == Decimal('100')
    assert store.query('甲','REVENUE','2022Y',cutoff='2024-04-22',strategy='pinned',source_doc='report-old').value == Decimal('80')
    assert store.query('甲','REVENUE','2022Y',strategy='same_report') is None
    with pytest.raises(ValueError):
        store.query('甲','REVENUE','2022Y',strategy='model_guessed')
    percentage = replace(claim(), candidate_value='0.2', unit='%', relation='yoy', base_period='2022Y')
    result = Judge(store,'2024-04-22').verify(percentage)
    assert result.status == 'verified'
    assert [row['fact_id'] for row in result.operands] == ['current','prior-in-new']


def test_explicit_old_source_gets_advisory_instead_of_false_version_error():
    old = fact('old',value='100',source_doc='old-report',publish_date='2024-03-01')
    new = fact('new',value='120',version='更正后')
    item = replace(claim(),candidate_value='100',declared_source='old-report')
    result = Judge(FactStore([old,new]),'2024-04-22').verify(item)
    assert result.status == 'verified' and result.advisories == ['A1']
    assert result.operands[0]['fact_id'] == 'old'
    uncited = Judge(FactStore([old,new]),'2024-04-22').verify(replace(item,declared_source=None))
    assert uncited.error_type == 'E1'
    assert uncited.diagnostic_hypotheses[0]['error_type'] == 'E9'
    assert uncited.diagnostic_hypotheses[0]['proof'] == 'numeric_interval_compatibility_only'


def test_citation_correction_replaces_bad_reference_as_well_as_number():
    from scripts.agent_benchmark import run_case
    case={'id':'citation','set':'B','draft':'甲2023年营业收入120.00元【来源:错来源】。',
          'company':'甲','period':'2023Y','report_date':'2024-04-22',
          'gold_status':'violated','gold_error_type':'E6','gold_origin':'deterministic_generated_fixture',
          'evidence_kind':'synthetic','facts':[fact().to_dict()]}
    result=run_case(case)
    assert result['strict_match'] and result['repair']['rechecked']
    assert '错来源' not in result['repair']['text'] and 'report-new' in result['repair']['text']


def test_future_version_is_not_used_for_diagnosis():
    now, future = fact(), fact('future',value='999',publish_date='2024-05-01',version='更正后')
    result = Judge(FactStore([now,future]),'2024-04-22').verify(replace(claim(),candidate_value='999'))
    assert result.error_type == 'E1' and future.evidence[0] not in result.evidence


def test_all_six_abstention_codes_are_distinct_from_advisories():
    current = fact()
    baseline = claim()
    cases = [
        (Judge(FactStore(), '2024-04-22'), baseline, 'X1'),
        (Judge(FactStore([current,fact('base','2022Y','0')]), '2024-04-22'), replace(baseline,relation='yoy',base_period='2022Y',candidate_value='1',unit='%'),'X2'),
        (Judge(FactStore([current]), '2024-04-22',require_dual_binding=True),baseline,'X3'),
        (Judge(FactStore([replace(current,parse_conf='0.2')]), '2024-04-22'),baseline,'X4'),
        (Judge(FactStore([current,fact('old',value='100',version='调整后',publish_date='2024-03-01')])),baseline,'X5'),
        (Judge(FactStore([current]), '2024-04-22'),replace(baseline,parse_status='unparsed'),'X6'),
    ]
    for judge,item,code in cases:
        result = judge.verify(item)
        assert result.status == 'abstained' and result.abstention_code == code
        assert result.diagnostics == [code]
        assert all(tag.startswith('A') for tag in result.advisories)
    explicit = replace(baseline, text='甲2023年合并营业收入120元。', caliber_hint=('合并','未扣非'), binding_status='agreed')
    assert Judge(FactStore([current]),'2024-04-22',require_dual_binding=True).verify(explicit).status == 'verified'


def test_a2_a3_a4_and_strict_caliber():
    result = Judge(FactStore([fact()]),'2024-04-22').verify(replace(claim(),text='约120元',approximate=True))
    assert 'A2' in result.advisories
    parent = replace(fact(),account='NP_PARENT')
    item = replace(claim(),account='NP_PARENT',text='2023年净利润120元')
    assert Judge(FactStore([parent]),'2024-04-22').verify(item).advisories == ['A3']
    strict = Judge(FactStore([parent]),'2024-04-22',strict_caliber=True).verify(item)
    assert strict.error_type == 'E3' and '归母净利润' in strict.correction['replacement_text']
    result = Judge(FactStore([fact(),fact('conflict',value='121')]),'2024-04-22').verify(claim())
    assert result.abstention_code == 'X4' and 'A4' in result.advisories


def test_binding_rejects_fabricated_numeric_inputs_and_bad_enums():
    data = {'mention_id':'m1','company':'甲','account':'REVENUE','period':'2023Y'}
    assert SemanticBinding.from_dict(data).agreement_key()[:3] == ('甲','REVENUE','2023Y')
    for extra in ({'candidate_value':'999'},{'formula':'x'},{'verdict':'verified'},{'note':'修正为999'},{'scope':'集团猜测'}):
        with pytest.raises(ValueError):
            SemanticBinding.from_dict(data|extra)


@pytest.mark.parametrize('confidence',['NaN','-0.1','1.1'])
def test_invalid_confidence_is_not_trusted(confidence):
    with pytest.raises(ValueError):
        fact_from_dict({'value':'1','parse_conf':confidence})


def test_non_recurring_four_operands_and_missing_not_zero():
    assert non_recurring_adjustment(Decimal('130'),Decimal('20'),Decimal('3'),Decimal('2')) == Decimal('115')
    assert non_recurring_adjustment(Decimal('130'),Decimal('20')) is None
    assert non_recurring_adjustment(Decimal('130'),Decimal('20'),Decimal('0'),Decimal('0')) == Decimal('110')
    assert cash_content(Decimal('10'),Decimal('-1')) is None


def financial_fact(account,period,value,**kwargs):
    return replace(fact(identifier=account+'-'+period,period=period,value=value,**kwargs),
                   account=account,nr='扣非' if account=='NP_PARENT_ADJ' else '未扣非')


def test_evidence_bound_analysis_recomputes_disclosed_deduction():
    facts=[financial_fact(account,'2023Y',value) for account,value in
           [('NP_PARENT','130'),('NRI_TOTAL','20'),('NRI_TAX_EFFECT','3'),('NRI_MINORITY','2'),('NP_PARENT_ADJ','115')]]
    result=analyze_financials(FactStore(facts),'甲',['2023Y'],'2024-04-22')
    row=next(item for item in result['metrics'] if item['metric']=='NP_PARENT_RECOMPUTED')
    assert row['value']=='115' and row['match_disclosure']
    assert len(row['operands'])==5 and row['evidence']
    missing=analyze_financials(FactStore(facts[:-2]+facts[-1:]),'甲',['2023Y'],'2024-04-22')
    row=next(item for item in missing['metrics'] if item['metric']=='NP_PARENT_RECOMPUTED')
    assert row['value'] is None and row['abstention_code']=='X1'


def test_ratio_operands_from_different_documents_do_not_mix():
    facts=[financial_fact('REVENUE','2023Y','100'),financial_fact('COST','2023Y','75',source_doc='different')]
    item=replace(claim(),account='GROSS_MARGIN',candidate_value='0.25',unit='%')
    assert Judge(FactStore(facts),'2024-04-22').verify(item).abstention_code=='X1'
    result=analyze_financials(FactStore(facts),'甲',['2023Y'],'2024-04-22')
    row=next(item for item in result['metrics'] if item['metric']=='GROSS_MARGIN')
    assert row['value'] is None


def test_two_changes_need_three_points_three_changes_need_four():
    facts=[]
    for year,profit,cfo in [('2020','10','5'),('2021','20','4'),('2022','30','3'),('2023','40','2')]:
        facts += [financial_fact('NP',year+'Y',profit),financial_fact('CFO',year+'Y',cfo)]
    result=analyze_financials(FactStore(facts),'甲',['2020Y','2021Y','2022Y','2023Y'],'2024-04-22')
    signals=[s for s in result['signals'] if s['kind']=='profit_cashflow_divergence']
    assert [s['consecutive_changes'] for s in signals]==[2,3]
    assert len(signals[-1]['periods'])==4 and signals[-1]['severity']=='elevated'
    assert all(s['code']=='A5' and s['evidence'] for s in signals)
    skipped=analyze_financials(FactStore(facts),'甲',['2020Y','2022Y','2023Y'],'2024-04-22')
    assert not any(s['kind']=='profit_cashflow_divergence' for s in skipped['signals'])


def test_accounting_changes_require_explicit_source_and_correct_treatment():
    common={'company':'甲','evidence_id':'doc:p1:e1','document_sha256':'a'*64,'publish_date':'2024-04-01'}
    rows=[common|{'text':'会计政策变更'},common|{'text':'本公司未发生会计政策变更。'},
          common|{'text':'本公司会计估计变更，自本期开始采用新的使用年限。'},
          common|{'text':'本公司前期差错更正，已对比较报表追溯重述。'},
          common|{'text':'本公司会计政策变更，自本期执行新准则。','publish_date':'2024-05-01'}]
    events=extract_accounting_events(rows,'甲','2024-04-22')
    assert [event['kind'] for event in events]==['accounting_estimate_change','prior_period_error']
    assert events[0]['treatment']=='prospective'
    assert all(event['evidence'] and event['code']=='A5' for event in events)


def test_thirty_rules_sixty_labels_and_pending_human_review():
    report=validate_rulepack()
    assert report['status']=='ok' and report['cards']==len(load_rule_cards()) and report['cards']>=30 and report['dictionary_entries']==60
    assert report['professional_signoff_status']=='pending'
    assert execute_rule('FIN-non-recurring',{'NP_PARENT':'130','NRI_TOTAL':'20','NRI_TAX_EFFECT':'3','NRI_MINORITY':'2'}) == Decimal('115')
    assert rule_applicability('FIN-non-recurring','2024-04-22')['professional_applicability']=='pending_or_inapplicable'


@pytest.mark.parametrize('formula',['__import__("os")','NP_PARENT+999','NP_PARENT[0]','NP_PARENT.value','NP_PARENT**2'])
def test_rule_ast_refuses_free_constants_and_executable_code(formula):
    card=copy.deepcopy(next(c for c in load_rule_cards() if c['id']=='FIN-non-recurring'))
    card['formula']=formula
    with pytest.raises(RuleError):
        evaluate_rule(card,{'NP_PARENT':'130','NRI_TOTAL':'20','NRI_TAX_EFFECT':'3','NRI_MINORITY':'2'})


def test_dimension_mismatch_quote_and_source_tampering_are_detected(tmp_path):
    card=copy.deepcopy(next(c for c in load_rule_cards() if c['id']=='FIN-cash-content'))
    card['inputs']['NP']='shares'
    with pytest.raises(RuleError):
        evaluate_rule(card,{'CFO':'10','NP':'2'})
    destination=tmp_path/'rules'
    shutil.copytree(ROOT/'rulepack',destination)
    (destination/'sources/MJ-METHOD.md').write_text('tampered',encoding='utf-8')
    report=validate_rulepack(destination)
    assert report['status']=='failed' and any('来源' in e or '引文' in e for e in report['errors'])
@pytest.mark.parametrize('threshold', ['NaN', 'Infinity', '-0.1', '1.1'])
def test_confidence_threshold_rejects_nonfinite_or_out_of_range(threshold):
    with pytest.raises(ValueError):
        Judge(FactStore([]), confidence_threshold=threshold)

def test_invalid_or_low_confidence_facts_cannot_support_error_diagnostics():
    from dataclasses import replace
    good = Fact('good', '甲', 'REVENUE', '2023Y', value=Decimal('100'), publish_date='2024-04-01')
    invalid = Fact('invalid', '甲', 'REVENUE', '2022Y', value=Decimal('80'), publish_date='2020-invalid')
    claim = AtomicClaim('c', '2023年营业收入为80元。', 11, 14, company='甲', account='REVENUE',
                        period='2023Y', candidate_value='80', unit='元', display_decimals=0)
    assert Judge(FactStore([invalid]), '2024-04-22').verify(claim).status == 'abstained'
    for alternative in (replace(invalid, period='2023Y', scope='母公司'),
                        replace(invalid, period='2023Y', version='更正前'),
                        replace(invalid, period='2023Y', publish_date='2024-03-01', parse_conf='0.5')):
        result = Judge(FactStore([good, alternative]), '2024-04-22').verify(claim)
        assert result.status == 'violated' and result.error_type == 'E1'

