"""Candidate assets must remain unreviewed and separate from scoreable gold."""
from decimal import Decimal
import hashlib
import json
from pathlib import Path

from mingjing.models import Fact
from bench.v1.prepare_annotations import (a_candidates, restatement_case, _restatement_rows,
                                        _complete_annual_report_year, select_reports)
import csv
from dataclasses import replace
import pytest

ASSETS = Path(__file__).parent/'annotations_v6_corrected'


def test_corrected_candidate_assets_have_600_pending_rows_and_verified_file_hashes():
    rows = [json.loads(line) for line in (ASSETS/'A_candidates.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(rows)==600 and len({r['ticker'] for r in rows})==20
    assert len({r['account_candidate'] for r in rows})==15
    assert len({r['id'] for r in rows})==600
    for row in rows:
        assert row['annotation_status']=='pending_human'
        assert row['gold_origin']=='unreviewed_source_linked_candidate'
        assert row['gold_status'] is None and row['gold_value'] is None and not row['human_reviews']
        assert not Path(row['source_document']['path']).is_absolute()
        for evidence in row['evidence_candidates']:
            assert evidence['document_sha256']==row['source_document']['sha256']
            assert evidence['evidence_id'].startswith(evidence['document_sha256'][:12]+':p')
            assert evidence['row'] is None and evidence['col'] is None
        if row['computed_context']:
            assert row['observed_candidate'] is None and row['candidate_status']=='unknown'
            assert row['computed_context']['candidate_type']=='deterministic_formula_context_not_original_cell'
    audit=json.loads((ASSETS/'preparation_audit.json').read_text(encoding='utf-8'))
    assert audit['professional_acceptance'] is False and audit['model_requests']==0
    assert audit['D3_status']=='not_selected_not_opened_not_sealed_not_executed'
    for name, sha in audit['files'].items():
        assert hashlib.sha256((ASSETS/name).read_bytes()).hexdigest()==sha


def test_computed_candidates_never_become_original_cells_or_human_truth():
    sha='a'*64
    observations=[{'source':{'code':'test','name':'甲','year':'2023','date':'2024-04-01','relative_path':'fixture.pdf'},
                  'sha256':sha,'bytes':0,'evidence':{},'facts':[
                      Fact('r','甲','REVENUE','2023Y',value=Decimal('120'),source_doc=sha[:12],publish_date='2024-04-01'),
                      Fact('c','甲','COST','2023Y',value=Decimal('60'),source_doc=sha[:12],publish_date='2024-04-01')]}]
    rows=a_candidates(observations)
    gross=next(row for row in rows if row['account_candidate']=='GROSS_MARGIN')
    assert gross['computed_context']['value']=='0.5'
    assert gross['observed_candidate'] is None and gross['gold_value'] is None
    assert gross['candidate_status']=='unknown' and gross['annotation_status']=='pending_human'
    cash=next(row for row in rows if row['account_candidate']=='CASH_CONTENT')
    assert cash['computed_context']['value'] is None


def test_restatement_rows_exclude_prose_and_source_rows_above_the_column_header():
    assert not _restatement_rows('应确认的金额为100.00元，而财报列示金额为80.00元。')
    rows=_restatement_rows('调整重述前\n调整重述后\n在建工程\n100.00\n120.00\n无形资产\n200.00\n220.00')
    assert [r['row_label_candidate'] for r in rows]==['在建工程','无形资产']
    source={'source':{'code':'test','name':'甲','relative_path':'fixture.pdf'},'sha256':'a'*64,
            'page_texts':[{'page':1,'text':'会计数据追溯调整或重述情况\n调整重述前\n调整重述后'}],
            'evidence':{'header':{'page':1,'bbox':[0,100,500,120],'text':'调整重述前\n调整重述后'},
                        'other':{'page':1,'bbox':[0,10,500,20],'text':'非流动资产处置损益\n100.00\n120.00'}}}
    assert restatement_case(source) is None


def test_p_assets_are_source_linked_observations_without_period_or_scope_invention():
    records=json.loads((ASSETS/'P_candidates.json').read_text(encoding='utf-8'))
    assert len(records)==3 and len({r['ticker'] for r in records})==3
    assert all(r['case_status']=='source_linked_restatement_candidate' for r in records)
    for record in records:
        assert record['gold_status'] is None and not record['human_reviews']
        for row in record['before_after_candidates']:
            assert row['scope']=='unknown' and row['period_assignment']=='unknown_requires_column_review'
            assert all(token in row['evidence']['text'] for token in row['printed_numeric_tokens'])
            assert '非流动资产处置损益' not in row['row_label_candidate']


@pytest.mark.parametrize('title', ['年度报告重大差错责任追究制度','2025年年度报告摘要',
                                  '2025年年度报告更正公告','2025年年度报告信息披露重大差错责任追究制度'])
def test_annual_category_does_not_make_policy_summary_or_correction_notice_a_report(title):
    row = {'category':'年报','year':'2025','file':'reports/2025-07-16_'+title+'.pdf','title':title}
    assert _complete_annual_report_year(row) is None


@pytest.mark.parametrize('title', ['2025年年度报告', '2025年年度报告（更正后）', '2025年年度报告(修订版)'])
def test_complete_annual_caption_proves_the_indexed_fiscal_year(title):
    row = {'category':'年报','year':'2025','file':'reports/2026-03-16_'+title+'.pdf','title':title}
    assert _complete_annual_report_year(row) == '2025'
    assert _complete_annual_report_year(dict(row, year='2026')) is None


def test_selection_uses_two_actual_annual_reports_and_stays_within_exposed_issuers(tmp_path):
    rows = []
    for code, year, title, date in [('999998','2024','2024年年度报告','2025-04-01'),
                                  ('999998','2025','年度报告重大差错责任追究制度','2025-07-16'),
                                  ('999998','2025','2025年年度报告','2026-04-01'),
                                  ('999999','2024','2024年年度报告','2025-04-01'),
                                  ('999999','2025','2025年年度报告','2026-04-01')]:
        source = tmp_path/'reports'/code/(date+'_'+title+'.pdf')
        source.parent.mkdir(parents=True,exist_ok=True)
        source.write_bytes(b'original')
        rows.append({'code':code,'name':'fixture','year':year,'category':'年报','date':date,'title':title,
                     'file':source.relative_to(tmp_path).as_posix()})
    with (tmp_path/'index.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    root, selected = select_reports(tmp_path, companies=1, allowed_tickers={'999998'})
    assert len(selected) == 2 and all(row['code']=='999998' for row in selected)
    assert selected[-1]['date']=='2026-04-01'
    assert not any('责任追究制度' in row['path'] for row in selected)


def test_rejected_disclosed_ratio_does_not_generate_a_convenient_formula_context():
    sha='a'*64
    margin=Fact('m','甲','GROSS_MARGIN','2023Y',value=Decimal('.4'),source_doc=sha[:12],publish_date='2024-04-01',display_unit='%')
    source={'source':{'code':'test','name':'甲','year':'2023','date':'2024-04-01','relative_path':'fixture.pdf'},
            'sha256':sha,'bytes':0,'evidence':{},'facts':[margin,replace(margin,fact_id='conflict',value=Decimal('.5')),
                Fact('r','甲','REVENUE','2023Y',value=Decimal('100'),source_doc=sha[:12],publish_date='2024-04-01'),
                Fact('c','甲','COST','2023Y',value=Decimal('60'),source_doc=sha[:12],publish_date='2024-04-01')]}
    gross=next(row for row in a_candidates([source]) if row['account_candidate']=='GROSS_MARGIN')
    assert gross['lookup_status']=='conflict' and gross['computed_context'] is None
    assert gross['gold_status'] is None and not gross['human_reviews']


def test_previously_exposed_mvp_is_eligible_for_a_without_touching_new_holdout(tmp_path):
    rows=[]
    for year,date in [('2023','2024-04-15'),('2024','2025-04-28')]:
        title=year+'年年度报告'
        source=tmp_path/'reports'/'920493_并行科技'/'年报'/(date+'_'+title+'.pdf')
        source.parent.mkdir(parents=True,exist_ok=True); source.write_bytes(b'original')
        rows.append({'code':'920493','name':'并行科技','year':year,'category':'年报','date':date,'title':title,
                     'file':source.relative_to(tmp_path).as_posix()})
    with (tmp_path/'index.csv').open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    _,selected=select_reports(tmp_path,companies=1,allowed_tickers={'920493'})
    assert len(selected)==2 and {r['code'] for r in selected}=={'920493'}
