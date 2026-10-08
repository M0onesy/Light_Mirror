"""Source-known numbers stay semantic work; exclusions require source proofs."""
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path

import pytest

from mingjing.coverage import CoverageLedger
from mingjing.facts import FactStore
from mingjing.financial_tools import BIND_ACCOUNTS, FinancialDispatcher
from mingjing.judge import Judge
from mingjing.mentions import SOURCE_REVIEW_LABELS, SOURCE_REVIEW_FORMULAS, SOURCE_REVIEW_SCALARS, SOURCE_REVIEW_PER_SHARE, extract_mentions, VALUE_RE
from mingjing.models import Fact, AtomicClaim
from mingjing.source_semantics import freeze_source_dimensions, semantic_review_required, source_dimensions_from_text
from mingjing.table_mentions import extract_table_mentions
from test_agent_lifecycle import lifecycle, new_agent


@pytest.mark.parametrize('label,code', list(SOURCE_REVIEW_LABELS.items()))
def test_known_broker_labels_preserve_number_period_unit_without_extending_canonical_tools(label,code,monkeypatch):
    unit = '%' if code=='ROIC_UNSPECIFIED' or code=='NET_DEBT_RATIO_UNSPECIFIED' else '倍' if code in SOURCE_REVIEW_SCALARS else '元' if code in SOURCE_REVIEW_PER_SHARE else '百万元'
    annotation = '(最新摊薄/元)' if code in SOURCE_REVIEW_PER_SHARE else '('+unit+')'
    source = '合并利润表(百万元)\n2022A\n2023A\n2024E\n'+label+annotation+'\n1.20\n2.40\n3.60\n'
    claims = extract_table_mentions(source,[{'start':0,'end':len(source),'page':1}],[],'甲公司','2099Y')
    assert len(claims)==3 and all(c.account==code for c in claims)
    assert [c.period for c in claims]==['2022Y','2023Y','2024Y']
    assert [c.modality for c in claims]==['fact','fact','forecast']
    assert all(c.parse_status=='parsed' and semantic_review_required(c) for c in claims)
    assert code not in BIND_ACCOUNTS  # Source recognition grants no query privilege.
    assert all(source[c.start:c.end]==c.numeric_text for c in claims)
    store=FactStore([Fact('matching','甲公司','NP','2023Y',value=Decimal(claims[1].candidate_value),publish_date='2024-04-01')])
    def denied(*args,**kwargs): raise AssertionError('Undefined source cannot read matching truth')
    for method in ('query','query_outcome','versions'): monkeypatch.setattr(store,method,denied)
    original=freeze_source_dimensions(claims[1])
    result=Judge(store,'2024-04-22',require_dual_binding=True).verify(replace(original,account='NP',binding_status='agreed'))
    assert result.status=='abstained' and result.abstention_code==('X2' if code in SOURCE_REVIEW_FORMULAS else 'X3')
    assert result.expected is None and not result.operands and not result.evidence


@pytest.mark.parametrize('label,code',[('流动比率','CURRENT_RATIO'),('总资产周转率','TURNOVER_ASSETS_UNSPECIFIED')])
def test_source_ratio_caption_supplies_only_dimensionless_unit_and_no_formula(label,code):
    source='主要财务比率\n2022A\n2023A\n'+label+'\n0.4\n0.5\n'
    claims=extract_table_mentions(source,[],[],'甲公司','2099Y')
    assert [c.candidate_value for c in claims]==['0.4','0.5'] and all(c.unit=='倍' for c in claims)
    for c in claims:
        p=c.table_context['source_unit_proof']
        assert source[p['start']:p['end']]==label and p['dimension']=='ratio'
        assert c.relation=='value' and c.account==code


def test_unknown_unregistered_label_or_missing_table_unit_is_not_guessed():
    for caption in ('未登记桥接项目','EBITDA'):
        source='利润表\n2023A\n'+caption+'\n1.20\n'
        claims=extract_table_mentions(source,[],[],'甲公司','2023Y')
        assert len(claims)==1 and claims[0].parse_status=='unresolved' and claims[0].candidate_value is None
        assert Judge(FactStore([]),'2024-04-22').verify(claims[0]).abstention_code=='X6'
    source='利润表(万元)\n2023A\n每股经营现金流(最新摊薄)\n1.20\n'
    claims=extract_table_mentions(source,[],[],'甲公司','2023Y')
    assert len(claims)==1 and claims[0].candidate_value is None and claims[0].unit==''


def test_cash_flow_loan_movements_never_read_matching_closing_loan_balances(monkeypatch):
    source='合并现金流量表(百万元)\n2023A\n短期借款\n1.20\n长期借款\n2.40\n'
    claims=extract_table_mentions(source,[],[],'甲公司','2023Y')
    assert [c.account for c in claims]==['SHORT_TERM_BORROWING_CHANGE_UNSPECIFIED','LONG_TERM_BORROWING_CHANGE_UNSPECIFIED']
    assert [c.candidate_value for c in claims]==['1200000.00','2400000.00']
    for c in claims:
        proof=c.table_context['source_unit_proof']
        assert source[proof['start']:proof['end']]==proof['text']=='合并现金流量表(百万元)'
        canonical=c.account.replace('_CHANGE_UNSPECIFIED','')
        store=FactStore([Fact('loan','甲公司',canonical,'2023Y',value=Decimal(c.candidate_value),publish_date='2024-04-01')])
        def denied(*args,**kwargs):raise AssertionError('Flow cannot query closing stock')
        monkeypatch.setattr(store,'query_outcome',denied)
        result=Judge(store,'2024-04-22').verify(replace(freeze_source_dimensions(c),account=canonical))
        assert result.abstention_code=='X3' and not result.operands


@pytest.mark.parametrize('source', ['公司以HPC China\n2023为例。','HPC China2023举办。'])
def test_technical_edition_ascii_boundary_works_after_chinese_and_wraps(source):
    source+='甲公司2023年合并营业收入2023元，毛利率20%。'
    ledger,claims=CoverageLedger.build(source,[{'start':0,'end':len(source),'page':1}],[],'甲公司','2023Y')
    edition=[r for r in ledger.entries if r['reason']=='technical_event_edition']
    assert len(edition)==1 and edition[0]['status']=='rule_excluded' and edition[0]['exclusion_basis']
    assert {c.numeric_text for c in claims}=={'2023元','20%'}


@pytest.mark.parametrize('source',['HPC China2023元营业收入。','MYHPC China2023为例。'])
def test_edition_rule_cannot_discard_financial_unit_or_identifier_substring(source):
    ledger,_=CoverageLedger.build(source,[{'start':0,'end':len(source),'page':1}],[],'甲公司','2023Y')
    assert not any(r['reason']=='technical_event_edition' for r in ledger.entries)


def test_inline_numbered_prose_proof_keeps_neighbor_financial_occurrences():
    source='业务带来增长。1）人工智能计算业务。甲公司2023年合并营业收入1元，毛利率20%。'
    ledger,claims=CoverageLedger.build(source,[{'start':0,'end':len(source),'page':1}],[],'甲公司','2023Y')
    rows=[e for e in ledger.entries if e['reason']=='source_numbered_prose_item']
    assert len(rows)==1 and rows[0]['status']=='rule_excluded'
    proof=rows[0]['exclusion_basis']['source_span']
    assert source[proof['start']:proof['end']]=='1）'
    assert {c.numeric_text for c in claims}=={'1元','20%'}
    source='甲公司2023年合并营业收入（1）元，毛利率20%。'
    ledger,_=CoverageLedger.build(source,[{'start':0,'end':len(source),'page':1}],[],'甲公司','2023Y')
    assert not any(e['reason']=='source_numbered_prose_item' for e in ledger.entries)


def quarter_axis(*,coords=True,legend=True,baseline=True,table=False):
    rows=[('图42：共建模式收入占比提高，2022Q4达到75%',20,20),('数据来源：披露文件',20,230)]
    for index,value in enumerate([100,80,60,40,20,0]):
        rows.append((str(value)+'%',30,40+index*14))
        if table:rows.append((['营业收入','毛利率','净利率','资产负债率','净利润率','ROE'][index],75,40+index*14))
    if legend:
        rows.extend([('外购模式收入占比',90,160),('共建模式收入占比',190,160),('总体毛利率',290,160)])
    if baseline:
        # Genuine category labels sit one point below the lowest tick's
        # source box, not in the separate multi-series legend below it.
        rows.extend([('第一季度',90,120),('第二季度',190,120),('第三季度',290,120)])
    source,lines='',[]
    for label,x,y in rows:
        start=len(source);source+=label+'\n'
        lines.append({'text':label,'start':start,'end':start+len(label),'bbox':[x,y,x+len(label)*5,y+9]})
    return source,[{'start':0,'end':len(source),'page':1,**({'lines':lines} if coords else {})}]


@pytest.mark.parametrize('legend',[True,False])
def test_quarter_percentage_axis_with_actual_baseline_excludes_only_ticks_not_caption_value(legend):
    source,pages=quarter_axis(legend=legend)
    ledger,_=CoverageLedger.build(source,pages,[],'甲公司','2023Y')
    ticks=[r for r in ledger.entries if r['reason']=='chart_axis_tick']
    assert len(ticks)==6 and all(r['exclusion_basis']['schema_version']=='pdf-chart-layout.v1' for r in ticks)
    assert any(r['text']=='75%' and r['reason']!='chart_axis_tick' for r in ledger.entries)
    for row in ticks:
        proof=row['exclusion_basis'];span=proof['occurrence_span']
        assert source[span[0]:span[1]].isdigit()
        assert proof['baseline_kind']=='categories' and proof['baseline_distance']==1
        assert len(proof['category_source_spans'])==3 and proof['source_boundary']
        assert [source[left:right] for left,right in proof['category_source_spans']]==['第一季度','第二季度','第三季度']


@pytest.mark.parametrize('options',[{'coords':False},{'baseline':False},{'table':True}])
def test_missing_chart_geometry_baseline_or_financial_table_rows_do_not_form_axis_proof(options):
    source,pages=quarter_axis(**options)
    ledger,_=CoverageLedger.build(source,pages,[],'甲公司','2023Y')
    assert not any(r['reason']=='chart_axis_tick' for r in ledger.entries)
    assert len([r for r in ledger.entries if r['text'].strip() in {'0%','20%','40%','60%','80%','100%'}])==6


def test_quarter_title_and_series_legend_cannot_replace_missing_horizontal_baseline():
    source,pages=quarter_axis(baseline=False)
    ledger,_=CoverageLedger.build(source,pages,[],'甲公司','2023Y')
    expected={'0%','20%','40%','60%','80%','100%'}
    ticks=[row for row in ledger.entries if row['text'].strip() in expected]
    assert {row['text'].strip() for row in ticks}==expected
    assert all(row['status']=='pending' and not row.get('exclusion_basis') for row in ticks)
    assert all(source[row['start']:row['end']]==row['text'] for row in ticks)


def test_valuation_caption_and_ebitda_table_are_outside_scope_with_original_occurrences():
    source='表18：可比公司PS 2024E为3.7X\n主要财务比率\n2022A\n2023A\nEV/EBITDA\n-51.8\n247.2\n'
    ledger,claims=CoverageLedger.build(source,[{'start':0,'end':len(source),'page':1}],[],'甲公司','2023Y')
    assert not claims
    observed=[r for r in ledger.entries if r['text'].strip() in {'3.7','-51.8','247.2'}]
    assert len(observed)==3 and all(r['status']=='out_of_scope' for r in observed)
    assert observed[0]['reason']=='source_comparable_valuation_caption' and observed[0]['exclusion_basis']
    assert all(r['reason']=='valuation_table_row' and r['modality_basis']['modality']=='opinion' for r in observed[1:])
    source='甲公司2023年合并营业收入3.7亿元。'
    ledger,claims=CoverageLedger.build(source,[{'start':0,'end':len(source),'page':1}],[],'甲公司','2023Y')
    assert len(claims)==1 and not any(r['reason']=='source_comparable_valuation_caption' for r in ledger.entries)


def test_percent_connector_is_not_invented_as_a_company_and_real_later_company_is_kept():
    for source in ('78.53%和81.27%','53%和乙公司2023年合并营业收入81.27元。'):
        if '乙公司' in source:
            claim=replace(extract_mentions(source,'甲公司','2023Y')[-1],table_context={'source_scope_required':True})
        else:
            match=list(VALUE_RE.finditer(source))[-1]
            claim=AtomicClaim('raw-percent-list',source,match.start(),match.end(),company='甲公司',
                              account='UNKNOWN',numeric_text=match[0],source_numeric_start=match.start(),
                              table_context={'source_scope_required':True})
        proof=source_dimensions_from_text(claim)
        assert '53%和' not in proof['source_company_subjects']
        if '乙公司' in source:
            assert proof['source_company_conflict'] and proof['source_company_subjects']==['乙公司']
            result=Judge(FactStore([Fact('wrong','甲公司','REVENUE','2023Y',value=Decimal('81.27'),publish_date='2024-04-01')]),'2024-04-22').verify(claim)
            assert result.status=='abstained' and not result.operands
        else:
            assert proof['company']=='甲公司' and proof['source_company_subjects']==[]
            assert not proof.get('source_company_conflict') and not proof['company_source_explicit']


def test_new_unit_metadata_is_blind_and_does_not_mutate_frozen_original():
    source='合并现金流量表(百万元)\n2023A\n营运资金变动\n98765.43\n其他经营现金流\n12345.67\n'
    claims=extract_table_mentions(source,[],[],'甲公司','2023Y')
    dispatcher=FinancialDispatcher('run','lock',claims,[],[],[],'2024-04-22')
    for role in ('planner','verifier'):
        dispatcher.register_session(role,role)
        for c in dispatcher.claims.values():
            original=json.dumps(c.to_dict(),ensure_ascii=False,sort_keys=True)
            for compact in (True,False):
                payload=json.dumps(dispatcher.mention_context(c,blind=True,compact=compact),ensure_ascii=False)
                assert '98765.43' not in payload and '12345.67' not in payload
            assert json.dumps(c.to_dict(),ensure_ascii=False,sort_keys=True)==original


def test_x6_describes_unproven_source_dimensions_without_calling_valid_digits_corrupt():
    source='利润表\n2023A\nEBITDA\n1.20\n'
    claim=extract_table_mentions(source,[],[],'甲公司','2023Y')[0]
    result=Judge(FactStore([]),'2024-04-22').verify(claim)
    assert result.abstention_code=='X6' and '科目、期间或单位维度不足' in result.reason


def test_actual_p42_historical_known_numbers_are_reviewed_and_valuation_is_scoped_out():
    observations=json.loads((Path(__file__).parent/'fixtures/numeric_semantic_review_03.json').read_text(encoding='utf8'))
    window=next(w for w in observations['windows'] if w['page']['page']==42)
    ledger,claims=CoverageLedger.build(window['text'],[window['page']],window['blocks'],observations['company'],observations['period'])
    assert not any(c.table_context.get('row_label','').strip()=='EV/EBITDA' for c in claims)
    supported=[c for c in claims if c.account in set(SOURCE_REVIEW_LABELS.values()) or '_BORROWING_CHANGE_' in c.account]
    assert len(supported)==40 and all(c.parse_status=='parsed' and semantic_review_required(c) for c in supported)
    authorized=[c for c in claims if c.account=='CURRENT_RATIO']
    assert len(authorized)==2 and all(c.table_context['definition_id']=='metric:current-ratio' for c in authorized)
    assert len([e for e in ledger.entries if e['reason']=='valuation_table_row'])>=2
    assert all(window['text'][c.start:c.end]==c.numeric_text for c in claims)


def test_new_source_families_actually_submit_unknown_in_both_isolated_sessions(lifecycle):
    """Controlled runtime exercises the real host workflow, never a live API."""
    source=('合并利润表(百万元)\n2023A\nEBITDA\n9.00\n'
            '合并现金流量表(百万元)\n2023A\n营运资金变动\n13.00\n'
            '主要财务比率\n2023A\n速动比率\n0.80\n'
            '每股指标（元）\n每股经营现金流(最新摊薄)\n1.05\n')
    _,claims=CoverageLedger.build(source,[{'start':0,'end':len(source),'page':1}],[],'甲公司','2023Y')
    assert len(claims)==4 and all(semantic_review_required(c) for c in claims)
    owner=new_agent(lifecycle);owner.prepare()
    owner.claims=[freeze_source_dimensions(c) for c in claims]
    owner.dispatcher.claims={c.claim_id:c for c in owner.claims}
    owner.ledger=CoverageLedger([],[],[{'id':c.claim_id,'claim_id':c.claim_id,'start':c.start,'end':c.end,
                                      'text':c.numeric_text,'status':'pending','reason':'original_financial_mention'} for c in claims])
    lifecycle.boundary.cancel_first_binding=False
    lifecycle.boundary.batch_coordinator=True
    lifecycle.boundary.binding_transform=lambda b:{**b,'account':'UNKNOWN'}
    owner.start_runtime()
    try:
        owner.bind();owner.calculate();owner.report();result=owner.finish('success')
    finally:owner.runtime.close()
    assert result['model']['semantic_review_candidates']==result['model']['semantic_review_submitted']==4
    assert len(owner.dispatcher.bindings['planner'])==len(owner.dispatcher.bindings['verifier'])==4
    assert not owner.preflight_claim_ids and not result['pending_claim_ids']
    assert {r['abstention_code'] for r in result['results']}=={'X2','X3'}
    assert all(r['status']=='abstained' and not r['operands'] for r in result['results'])
    from mingjing.replay import replay_run
    assert replay_run(lifecycle.out)['status']=='passed'
