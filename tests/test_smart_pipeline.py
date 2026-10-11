"""直传智能管线（mingjing.smart）回归测试。

unittest 风格，`python -m unittest tests.test_smart_pipeline` 或 pytest 均可运行；
需要 PDF 后端的用例在没有 PyMuPDF / poppler / pdfplumber 时自动跳过。
"""
from __future__ import annotations

import hashlib
import http.server
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
FIX = ROOT / 'tests' / 'fixtures'
SAMPLES = ROOT / 'samples' / 'smart'

from mingjing.models import Fact  # noqa: E402
from mingjing.smart.claims import build_report_model, extract_prose_claims  # noqa: E402
from mingjing.smart.common import IV, find_periods, norm, quantum, to_decimal  # noqa: E402
from mingjing.smart.detect import DocMeta, detect, same_company  # noqa: E402
from mingjing.smart.docio import Cell, Document, Page, Row, body_lines, load_document, rows_from_chunks, rows_from_layout_text  # noqa: E402
from mingjing.smart.kb import KnowledgeBase  # noqa: E402
from mingjing.smart.pipeline import run_task  # noqa: E402
from mingjing.smart.tables import extract_filing_facts  # noqa: E402
from mingjing.smart.verify import Verifier, summarize  # noqa: E402


def has_pdf_backend() -> bool:
    try:
        import fitz  # noqa: F401
        return True
    except Exception:
        pass
    if shutil.which('pdftotext'):
        return True
    try:
        import pdfplumber  # noqa: F401
        return True
    except Exception:
        return False


def text_doc(text: str, name: str = 'doc.txt') -> Document:
    return Document('/virtual/' + name, name, hashlib.sha256(text.encode()).hexdigest(), 'text', 'text', [Page(1, rows_from_layout_text(1, text), text)])


def fixture_doc(pages: list, sha: str) -> Document:
    return Document('/virtual/fx.pdf', 'fx.pdf', sha, 'pdf', 'fitz',
                    [Page(p['page'], rows_from_chunks(p['page'], [(l['text'], *l['bbox']) for l in p['lines']]), p.get('text', '')) for p in pages])


def fact(account: str, period: str, value, **kw) -> Fact:
    # Synthetic filing fixtures have explicit issuer and disclosure metadata.
    return Fact(fact_id=f"{account}-{period}-{kw.get('scope', '合并')}-{kw.get('dim', '')}-{kw.get('relation', 'value')}", company=kw.get('company', '测试公司'), account=account,
                period=period, scope=kw.get('scope', '合并'), value=Decimal(str(value)), decimals=kw.get('decimals', 2), display_unit=kw.get('unit', '元'),
                evidence=('a' * 12 + ':p1:0,0,0,0:deadbeef',), label=kw.get('label', account), source_type=kw.get('source_type', 'primary_statement'),
                source_doc='a' * 12, publish_date=kw.get('publish_date', '2024-04-20'), currency=kw.get('currency', 'CNY'),
                definition_id=kw.get('definition_id', ''), dimension_kind=kw.get('dim_kind', 'total'), dimension_id=kw.get('dim', ''), source_relation=kw.get('relation', 'value'))


FILING_TXT = """星河智造股份有限公司
2023年年度报告
证券代码：999001    证券简称：星河智造
披露日期：2024年3月28日
第一节 重要提示
公司董事会、监事会保证年度报告内容真实、准确、完整。审计报告为标准无保留意见。
第二节 公司简介和主要财务指标
一、主要会计数据和财务指标
主要会计数据    2023年    2022年    本年比上年增减(%)    2021年
营业收入    1,286,450,000.00    1,052,300,000.00    22.25    880,000,000.00
归属于上市公司股东的净利润    176,740,688.00    126,682,570.00    39.51    100,660,000.00
归属于上市公司股东的扣除非经常性损益的净利润    165,440,688.00    107,782,570.00    53.49    88,260,000.00
经营活动产生的现金流量净额    186,300,000.00    152,800,000.00    21.92    96,500,000.00
第十节 财务报告
合并资产负债表
单位：元
项目    附注    2023年12月31日    2022年12月31日
货币资金    七、1    456,000,000.00    381,000,000.00
资产总计    1,990,355,436.00    1,630,269,988.00
负债合计    758,710,309.00    641,779,600.00
归属于母公司所有者权益（或股东权益）合计    1,186,345,127.00    945,640,388.00
法定代表人：林晨    主管会计工作负责人：周岚    会计机构负责人：许拓
合并利润表
单位：元
项目    附注    2023年度    2022年度
一、营业总收入    1,286,450,000.00    1,052,300,000.00
其中：营业收入    七、35    1,286,450,000.00    1,052,300,000.00
其中：营业成本    七、35    842,624,750.00    707,145,600.00
五、净利润（净亏损以“－”号填列）    182,515,688.00    131,406,570.00
1.归属于母公司股东的净利润（净亏损以“-”号填列）    176,740,688.00    126,682,570.00
法定代表人：林晨    主管会计工作负责人：周岚    会计机构负责人：许拓
"""

REPORT_TXT = """证券研究报告 公司深度
星河智造（999001.SH）——盈利质量稳步提升
2024年4月25日 虚构证券研究所 分析师：陈一鸣（证书编号：S0000000000001） 投资评级：买入（维持）
投资要点
2023年公司实现营业收入12.86亿元，同比增长32.25%。
2023年公司归母净利润为1.77亿元，扣非归母净利润1.65亿元。
2023年公司毛利率为34.50%。
2022年公司营业收入为8.80亿元。
2023年经营活动现金流量净额为1.86万元。
盈利预测与风险提示：下游需求不及预期。
"""


class CommonTests(unittest.TestCase):
    def test_periods(self):
        self.assertEqual(find_periods(norm('2018-2023 年5 年营收CAGR'))[0].periods, ('2018Y', '2019Y', '2020Y', '2021Y', '2022Y', '2023Y'))
        self.assertEqual(find_periods('23Q3单季')[0].periods, ('2023Q3',))
        self.assertEqual(find_periods('1H24收入')[0].periods, ('2024H1',))
        self.assertEqual(find_periods('2023年前三季度')[0].periods, ('20239M',))
        self.assertTrue(find_periods('2024E')[0].forecast)
        self.assertEqual(find_periods('10年期国债'), [])
        self.assertEqual(find_periods('截至2023年12月31日')[0].periods, ('2023Y',))

    def test_numbers_and_intervals(self):
        self.assertEqual(to_decimal('(1,234.50)'), Decimal('-1234.50'))
        self.assertEqual(to_decimal('－80,499,535.36'), Decimal('-80499535.36'))
        claim = IV.rounded(Decimal('4.96') * Decimal('1e8'), quantum('亿元', 2))
        self.assertTrue(claim.intersects(IV(Decimal('495671818.88'))))
        self.assertFalse(claim.intersects(IV(Decimal('497000000'))))
        with self.assertRaises(ZeroDivisionError):
            IV(1) / IV(-1, 1)


class DocioTests(unittest.TestCase):
    def test_date_fragments_merge_but_columns_stay_apart(self):
        chunks = [('项目', 120, 691, 141, 702), ('2023', 317, 691, 339, 702), ('年', 345, 691, 350, 702), ('12', 356, 691, 366, 702), ('月', 372, 691, 377, 702),
                  ('31', 383, 691, 394, 702), ('日', 400, 691, 405, 702), ('2022', 438, 691, 460, 702), ('年', 466, 691, 471, 702), ('12', 477, 691, 487, 702),
                  ('月', 493, 691, 498, 702), ('31', 504, 691, 515, 702), ('日', 521, 691, 526, 702)]
        cells = [c.text for c in rows_from_chunks(1, chunks)[0].cells]
        self.assertEqual(cells, ['项目', '2023年12月31日', '2022年12月31日'])

    def test_layout_header_years_are_separate_cells(self):
        row = rows_from_layout_text(1, '            项目       附注          2023 年              2022 年')[0]
        self.assertEqual([c.text for c in row.cells], ['项目', '附注', '2023年', '2022年'])

    def test_body_lines_separates_left_sidebar(self):
        body = ['并行科技是国内领先的超算云服务和算力运营服务提供商，主要服务包括并行通', '等领域的领先技术，参与多项国家重点研发计划。我们预计公司 2024-2026 年归',
                '母净利润为-0.37/0.07/0.45 亿元，对应 EPS 分别为-0.63/0.13/0.77 元/股，对应当', '前股价的 PS 分别为 3.6/2.8/2.1 倍，看好公司算力云及行业云释放增量，首次']
        side = [('当前股价(元)', '42.80'), ('总市值(亿元)', '24.92'), ('总股本(亿股)', '0.58'), ('流通股本(亿股)', '0.25')]
        rows = [Row(1, i, [Cell(side[i][0], 2, 14), Cell(side[i][1], 30, 35), Cell(text, 40, 40 + len(text))]) for i, text in enumerate(body)]
        lines = body_lines(Page(1, rows, ''))
        self.assertEqual(lines[:4], body)
        self.assertIn('当前股价(元) 42.80', lines)


class FilingTableTests(unittest.TestCase):
    def test_real_balance_sheet_fixture(self):
        data = json.loads((FIX / 'broker_residual_p42_official_balance.json').read_text(encoding='utf-8'))
        facts, diag = extract_filing_facts(fixture_doc(data['official_pages'], data['official_sha256']),
                                           DocMeta(doc_type='annual_report', company='并行科技', period='2023Y', report_date=data['official_publish_date']))
        got = {(f.account, f.period, f.scope): f.value for f in facts if '横线视为零' not in f.label}
        expected = {(f['account'], f['period'], f['scope']): Decimal(f['value']) for f in data['official_facts']}
        self.assertEqual(len(expected), 78)
        self.assertEqual({k: got.get(k) for k in expected}, expected)
        self.assertEqual(diag['skipped_rows'], 0)

    def test_wrapped_date_header_and_note_column_fixtures(self):
        wrapped = json.loads((FIX / 'wrapped_period_columns_15.json').read_text(encoding='utf-8'))
        facts, _ = extract_filing_facts(fixture_doc(wrapped['pages'], wrapped['doc_sha256']), DocMeta(doc_type='prospectus', company='并行科技', period='2022Y'))
        self.assertEqual({f.period for f in facts}, {'2022Y', '2021Y', '2020Y'})
        cash = {f.period: f.value for f in facts if f.account == 'CASH_FUNDS' and f.scope == '合并'}
        self.assertEqual(cash['2022Y'], Decimal('153826896.55'))
        notes = json.loads((FIX / 'statement_note_column_15.json').read_text(encoding='utf-8'))
        facts, _ = extract_filing_facts(fixture_doc(notes['pages'], notes['doc_sha256']), DocMeta(doc_type='annual_report', company=notes['company'], period=notes['period']))
        revenue = {(f.period, f.scope): f.value for f in facts if f.account == 'REVENUE'}
        self.assertEqual(revenue[('2017Y', '合并')], Decimal('50704101.31'))

    def test_cash_flow_supplement_fixture(self):
        data = json.loads((FIX / 'financial_notes_source_14.json').read_text(encoding='utf-8'))
        facts, _ = extract_filing_facts(fixture_doc(data['pages'], data['document_sha256']), DocMeta(doc_type='annual_report', company='并行科技', period='2023Y'))
        dep = {f.period: f.value for f in facts if f.account == 'CF_BRIDGE_DEPRECIATION'}
        self.assertEqual(dep, {'2023Y': Decimal('83390330.82'), '2022Y': Decimal('65102569.28')})

    def test_layout_summary_split_growth_caption_and_stale_header(self):
        text = """
一、     盈利能力
                                                                              单位：元
                                                             本年比上年增
                    2023 年                  2022 年                         2021 年
                                                               减%
营业收入              495,671,818.88          312,776,998.45       58.47%    220,099,934.89
毛利率%                     25.63%                  28.26%        -                34.02%
归属于上市公司股东的净利润     -80,499,535.36          -114,165,954.11      -29.49%   -81,682,113.20
归属于上市公司股东的扣除非
                  -86,841,444.76          -115,337,769.57      -24.71%   -88,269,696.53
经常性损益后的净利润
加权平均净资产收益率%（依据                                                 -
归属于上市公司股东的净利润           -63.55%                  -65.05%                      -245.96%
计算）
四、    与业绩预告/业绩快报中披露的财务数据差异
            项目                         年度报告                   业绩快报               差异率
营业收入                                  495,671,818.88         496,677,356.68          -0.20%
"""
        facts, _ = extract_filing_facts(text_doc(text), DocMeta(doc_type='annual_report', company='X', period='2023Y'))
        value = {(f.account, f.period, f.source_relation): f.value for f in facts}
        self.assertEqual(value[('REVENUE', '2022Y', 'value')], Decimal('312776998.45'))
        self.assertEqual(value[('REVENUE', '2021Y', 'value')], Decimal('220099934.89'))
        self.assertEqual(value[('REVENUE', '2023Y', 'yoy')], Decimal('0.5847'))
        self.assertEqual(value[('NP_PARENT_ADJ', '2023Y', 'value')], Decimal('-86841444.76'))
        self.assertEqual(value[('ROE', '2023Y', 'value')], Decimal('-0.6355'))
        self.assertEqual(value[('NP_PARENT', '2022Y', 'value')], Decimal('-114165954.11'))
        self.assertNotIn(Decimal('496677356.68'), [f.value for f in facts])

    def test_main_board_header_row_is_not_a_section_title(self):
        facts, diag = extract_filing_facts(text_doc(FILING_TXT), DocMeta(doc_type='annual_report', company='星河智造', period='2023Y'))
        value = {(f.account, f.period, f.source_relation): f.value for f in facts}
        self.assertEqual(value[('NP_PARENT_ADJ', '2023Y', 'value')], Decimal('165440688.00'))
        self.assertEqual(value[('REVENUE', '2021Y', 'value')], Decimal('880000000.00'))
        self.assertEqual(value[('NP_PARENT', '2023Y', 'yoy')], Decimal('0.3951'))
        self.assertEqual(value[('COST', '2023Y', 'value')], Decimal('842624750.00'))
        self.assertEqual(diag['skipped_rows'], 0)

    def test_parent_statement_note_fragment_dash_zero_and_subheading(self):
        text = """
母公司利润表
                                                      单位：元
           项目           附注      2023 年            2022 年
一、营业收入                 十四、
                         （五）    426,412,227.84   244,383,062.07
减：营业成本                 十四、
                         （五）    354,489,163.75   217,713,190.06
  投资收益（损失以“-”号填列）                                -                  -
法定代表人：甲    主管会计工作负责人：乙    会计机构负责人：丙
合并资产负债表
            项目       附注          2023 年12 月31 日              2022 年12 月31 日
流动负债：                                80
短期借款                           183,000,000.00         27,000,000.00
"""
        facts, _ = extract_filing_facts(text_doc(text), DocMeta(doc_type='annual_report', company='X', period='2023Y'))
        value = {(f.account, f.period, f.scope): f for f in facts}
        self.assertEqual(value[('REVENUE', '2023Y', '母公司')].value, Decimal('426412227.84'))
        self.assertEqual(value[('COST', '2022Y', '母公司')].value, Decimal('217713190.06'))
        self.assertEqual(value[('INVESTMENT_INCOME', '2023Y', '母公司')].value, Decimal('0'))
        self.assertIn('横线视为零', value[('INVESTMENT_INCOME', '2023Y', '母公司')].label)
        self.assertNotIn(('CURRENT_LIABILITIES', '2023Y', '合并'), value)
        self.assertEqual(value[('SHORT_TERM_BORROWING', '2022Y', '合并')].value, Decimal('27000000.00'))


class ClaimTests(unittest.TestCase):
    META = DocMeta(doc_type='research_report', company='并行科技', base_year=2023, report_date='2024-04-21')

    def claims(self, text, segments=None):
        return extract_prose_claims(text_doc(text), self.META, segments or set())

    def test_parenthetical_growth_and_cagr(self):
        rows = self.claims('公司持续高速发展，2023年实现营收4.96亿元（+58%），2018-2023年5年营收CAGR达41%。')
        got = {(c.metric, c.relation, c.period, c.base_period, c.num_text, c.entity) for c in rows}
        self.assertIn(('REVENUE', 'value', '2023Y', '', '4.96亿元', 'company'), got)
        self.assertIn(('REVENUE', 'yoy', '2023Y', '', '+58%', 'company'), got)
        self.assertIn(('REVENUE', 'cagr', '2023Y', '2018Y', '41%', 'company'), got)

    def test_forecast_series_follow_period_range(self):
        rows = self.claims('我们预计公司2024-2026年归母净利润为-0.37/0.07/0.45亿元，对应EPS分别为-0.63/0.13/0.77元/股，对应当前股价的PS分别为3.6/2.8/2.1倍。')
        self.assertEqual([(c.metric, c.period) for c in rows if c.metric == 'NP_PARENT'], [('NP_PARENT', '2024Y'), ('NP_PARENT', '2025Y'), ('NP_PARENT', '2026Y')])
        self.assertEqual([c.period for c in rows if c.metric == 'PS'], ['2024Y', '2025Y', '2026Y'])
        self.assertTrue(all(c.forecast and c.category == 'forecast' for c in rows))

    def test_share_keeps_numerator_and_denominator(self):
        rows = self.claims('由于公司超算云服务具有一定季节性，2020-2022年第四季度占超算云服务全年收入的比例分别为37.56%、35.96%和31.89%，从而毛利率偏高。', {'超算云服务'})
        shares = [c for c in rows if c.relation == 'share']
        self.assertEqual(len(shares), 3)
        self.assertTrue(all(c.denominator == '超算云服务' for c in shares))
        self.assertEqual([c.period for c in shares], ['2020Y', '2021Y', '2022Y'])

    def test_noise_is_not_bound_to_financial_metrics(self):
        rows = self.claims('存储容量超过20PB，预计中国AI服务器市场2026年达123亿美元，参会费用在1000-3000元不等。')
        self.assertFalse([c for c in rows if c.metric in ('PB', 'PS', 'PE')])
        self.assertFalse([c for c in rows if '123' in c.num_text or '3000' in c.num_text])

    def test_segment_subject(self):
        rows = self.claims('从产品收入结构来看，2022年超算云服务为主要创收模块，营收2.67亿元，2023年进一步增长55%至4.14亿元。', {'超算云服务'})
        self.assertTrue(all(c.entity == 'segment' and c.segment == '超算云服务' for c in rows if c.metric == 'REVENUE'))
        rows = self.claims('2023年公司实现营业收入4.96亿元。')
        self.assertEqual((rows[0].entity, rows[0].segment), ('company', ''))


class VerifyTests(unittest.TestCase):
    FACTS = [fact('REVENUE', '2023Y', '495671818.88'), fact('REVENUE', '2022Y', '312776998.45'), fact('NP_PARENT', '2023Y', '-80499535.36'),
             fact('NP_PARENT', '2022Y', '-114165954.11'), fact('NP', '2023Y', '-77711823.33'), fact('NP', '2022Y', '-111625396.72'),
             fact('NP_PARENT_ADJ', '2023Y', '-86841444.76'), fact('COST', '2023Y', '368622173.98'), fact('EQUITY_PARENT', '2023Y', '336548723.81'),
             fact('EQUITY', '2023Y', '346718795.65'), fact('REVENUE', '2023Y', '414115382.02', dim_kind='business', dim='超算云服务', source_type='segment_table'),
             fact('REVENUE', '2023Y', '0.5501', dim_kind='business', dim='超算云服务', relation='yoy', unit='%', source_type='segment_table')]

    def run_text(self, text, facts=None, strict=False):
        meta = DocMeta(doc_type='research_report', company='测试公司', base_year=2023, report_date='2024-04-21')
        facts = self.FACTS if facts is None else facts
        model = build_report_model(text_doc(text), meta, {f.dimension_id for f in facts if f.dimension_kind != 'total'})
        results = {r.cid: r for r in Verifier(facts, model, strict=strict).verify_all()}
        return [(c, results[c.cid]) for c in model.claims], model

    def one(self, text, **kw):
        rows, _ = self.run_text(text, **kw)
        self.assertEqual(len(rows), 1, [c.to_dict() for c, _ in rows])
        return rows[0]

    def test_direct_value_and_growth(self):
        rows, _ = self.run_text('2023年公司营业收入4.96亿元，同比增长58.47%。')
        self.assertEqual([(c.relation, r.status) for c, r in rows], [('value', 'verified'), ('yoy', 'verified')])

    def test_negative_base_growth_uses_absolute_base(self):
        rows, _ = self.run_text('2023年归母净利润为-8050万元（+29%）。')
        growth = next(r for c, r in rows if c.relation == 'yoy')
        self.assertEqual(growth.status, 'verified')
        self.assertTrue(any('基期为负' in note for note in growth.notes))

    def test_ambiguous_term_matches_alternative_definition_with_advisory(self):
        claim, result = self.one('2023年净利润为-8050万元。')
        self.assertEqual((result.status, 'A3' in result.advisories, result.definition), ('verified', True, 'NP=NP_PARENT'))
        claim, result = self.one('2023年净利润为-8050万元。', strict=True)
        self.assertEqual((result.status, result.abstain_code), ('unverifiable', 'X3'))

    def test_error_attribution(self):
        self.assertEqual(self.one('2023年营业收入4.96万元。')[1].error_type, 'E4')
        self.assertEqual(self.one('2022年营业收入4.96亿元。')[1].error_type, 'E2')
        self.assertEqual(self.one('2023年归母净利润为-8684万元。')[1].error_type, 'E3')
        claim, strict = self.one('2022年合并报表营业收入4.96亿元。', strict=True)  # 明示口径，才能比较 strict 数值归因
        self.assertEqual((strict.status, strict.error_type), ('violated', 'E1'))
        self.assertIn('审慎模式', strict.diagnosis['detail'])
        claim, result = self.one('2023年营业收入同比增长48.47%。')
        self.assertEqual((result.status, result.error_type, result.expected_display), ('violated', 'E5', '58.47%'))
        self.assertIn('58.47%', result.correction['replacement_text'])
        claim, result = self.one('2023年营业收入5.96亿元。')
        self.assertEqual((result.status, result.error_type, result.correction['correct_value']), ('violated', 'E1', '4.96亿元'))

    def test_multi_definition_ratio_and_blocked_candidates(self):
        claim, result = self.one('2023年ROE为-22.4%。')
        self.assertEqual((result.status, result.definition), ('verified', 'ROE.total'))
        without_equity = [f for f in self.FACTS if f.account != 'EQUITY']
        claim, result = self.one('2023年ROE为-22.4%。', facts=without_equity)
        self.assertEqual((result.status, result.abstain_code), ('unverifiable', 'X3'))     # 缺材料时不能把“未命中”当“有误”

    def test_segment_prior_year_is_derived_from_disclosed_growth(self):
        claim, result = self.one('2022年超算云服务收入2.67亿元。')
        self.assertEqual((claim.segment, result.status), ('超算云服务', 'verified'))
        self.assertIn('推算', result.definition_label)

    def test_missing_material_is_a_gap_not_an_error(self):
        claim, result = self.one('2018年营业收入1.20亿元。')
        self.assertEqual((result.status, result.abstain_code), ('unverifiable', 'X1'))
        self.assertEqual(result.missing[0]['period'], '2018Y')

    def test_summary_rates(self):
        rows, model = self.run_text('2023年公司营业收入4.96亿元。2023年营业收入同比增长48.47%。2018年营业收入1.20亿元。')
        summary = summarize(model.claims, [r for _, r in rows])
        self.assertEqual((summary['totals']['verified'], summary['totals']['violated'], summary['totals']['unverifiable']), (1, 1, 1))
        self.assertAlmostEqual(summary['core']['abstention_rate'], 1 / 3, places=3)
        self.assertEqual(summary['gaps'][0]['period'], '2018Y')


class GeneralizationTests(unittest.TestCase):
    def test_quarterly_report_columns(self):
        text = """2024年第三季度报告
一、主要财务数据
单位：元
项目    本报告期    本报告期比上年同期增减变动幅度(%)    年初至报告期末    年初至报告期末比上年同期增减变动幅度(%)
营业收入    410,000,000.00    22.50    1,120,000,000.00    18.30
经营活动产生的现金流量净额    不适用    不适用    98,000,000.00    -8.20
    本报告期末    上年度末    本报告期末比上年度末增减变动幅度(%)
总资产    2,250,000,000.00    1,990,355,436.00    13.05
"""
        facts, _ = extract_filing_facts(text_doc(text), DocMeta(doc_type='quarterly_report', company='X', period='20249M'))
        got = {(f.account, f.period, f.source_relation): f.value for f in facts}
        self.assertEqual(got[('REVENUE', '2024Q3', 'value')], Decimal('410000000.00'))
        self.assertEqual(got[('REVENUE', '20249M', 'value')], Decimal('1120000000.00'))
        self.assertEqual(got[('REVENUE', '20249M', 'yoy')], Decimal('0.183'))
        self.assertEqual(got[('CFO', '20249M', 'value')], Decimal('98000000.00'))
        self.assertEqual(got[('ASSETS', '2023Y', 'value')], Decimal('1990355436.00'))
        self.assertNotIn(('ASSETS', '20249M', 'yoy'), got)                     # 相对上年度末的变动不是同比

    def test_relative_period_words_and_citation_scope(self):
        meta = DocMeta(doc_type='research_report', company='星河智造', base_year=2023, report_date='2024-08-30')
        rows = extract_prose_claims(text_doc('2024年上半年公司实现营业收入6.44亿元。盈利能力方面，上半年综合毛利率为34.80%。'
                                             '据公司2023年年度报告，2024年上半年经营活动现金流量净额为0.72亿元；截至2024年6月末，公司货币资金4.38亿元。'), meta, set())
        by = {c.num_text: c for c in rows}
        self.assertEqual((by['34.80%'].period, by['34.80%'].forecast), ('2024H1', False))
        self.assertEqual((by['0.72亿元'].cite, by['4.38亿元'].cite), ('annual_report:2023Y', ''))
        self.assertEqual(by['4.38亿元'].period, '2024H1')

    def test_bse_code_migration_is_same_company(self):
        from mingjing.smart.detect import code_aliases
        self.assertIn('920493', code_aliases('839493'))
        self.assertTrue(same_company({'stock_code': '839493', 'company': '并行科技'}, {'stock_code': '920493', 'company': ''}))
        self.assertFalse(same_company({'stock_code': '839493'}, {'stock_code': '920494'}))

    def test_docx_and_markdown_inputs(self):
        import zipfile
        ns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
        para = lambda t: f'<w:p><w:r><w:t>{t}</w:t></w:r></w:p>'
        cell = lambda t: f'<w:tc><w:p><w:r><w:t>{t}</w:t></w:r></w:p></w:tc>'
        body = (para('星河智造（999001.SH）——公司点评') + para('2024年4月25日 虚构证券研究所 投资评级：买入 分析师：甲 证书编号：S0000000000009')
                + para('2023年公司实现营业收入12.86亿元，同比增长22.25%。') + para('风险提示：需求不及预期。')
                + '<w:tbl>' + ''.join('<w:tr>' + ''.join(cell(c) for c in row) + '</w:tr>' for row in
                                      [['指标', '2022A', '2023A', '2024E'], ['营业收入(百万元)', '1,052', '1,286', '1,556'], ['同比增长率', '19.6%', '22.3%', '21.0%']]) + '</w:tbl>')
        tmp = Path(tempfile.mkdtemp(prefix='mj-docx-'))
        try:
            path = tmp / '研报.docx'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('word/document.xml', f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{ns}"><w:body>{body}</w:body></w:document>')
            doc = load_document(path)
            meta = detect(doc)
            self.assertEqual((meta.doc_type, meta.company, meta.stock_code), ('research_report', '星河智造', '999001'))
            model = build_report_model(doc, meta)
            table = [(c.metric, c.relation, c.period) for c in model.claims if c.kind == 'table']
            self.assertIn(('REVENUE', 'yoy', '2023Y'), table)
            self.assertIn(('REVENUE', 'value', '2024Y'), table)
            md = tmp / '研报.md'
            md.write_text('# 星河智造（999001.SH）点评\n\n证券研究报告 投资评级：买入 分析师：甲\n\n| 指标 | 2022A | 2023A | 2024E |\n|---|---|---|---|\n'
                          '| 营业收入(百万元) | 1,052 | 1,286 | 1,556 |\n| 归母净利润(百万元) | 127 | 177 | 181 |\n\n2023年公司实现营业收入12.86亿元。\n', encoding='utf-8')
            model = build_report_model(load_document(md), detect(load_document(md)))
            self.assertTrue(any(c.kind == 'table' and c.period == '2024Y' and c.forecast for c in model.claims))
            self.assertTrue(any(c.kind == 'prose' and c.metric == 'REVENUE' and c.period == '2023Y' for c in model.claims))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class KnowledgeBaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='mj-kb-'))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dedupe_cache_journal_and_meta_update(self):
        kb = KnowledgeBase(self.tmp / 'kb')
        first = kb.add_bytes(FILING_TXT.encode('utf-8'), '星河智造_2023年年度报告.txt')
        again = kb.add_bytes(FILING_TXT.encode('utf-8'), '重复上传.txt')
        self.assertEqual((first['duplicate'], again['duplicate'], again['uploads'], again['doc_id']), (False, True, 2, first['doc_id']))
        self.assertEqual((first['doc_type'], first['company'], first['stock_code'], first['period'], first['report_date']),
                         ('annual_report', '星河智造', '999001', '2023Y', '2024-03-28'))
        facts, _ = kb.facts(first['doc_id'])
        self.assertGreater(len(facts), 20)
        fresh = KnowledgeBase(self.tmp / 'kb')
        fresh.facts(first['doc_id'])
        self.assertEqual((fresh.session['fact_hits'], fresh.session['fact_misses']), (1, 0))
        self.assertEqual(fresh.stats()['duplicate_uploads_saved'], 1)
        previous = ''
        for event in kb.journal(500):
            body = {k: v for k, v in event.items() if k != 'hash'}
            self.assertEqual(body['prev'], previous)
            self.assertEqual(hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest(), event['hash'])
            previous = event['hash']
        self.assertIn('dedupe_hit', [e['event'] for e in kb.journal(500)])
        kb.update(first['doc_id'], {'company': '星河智造集团'})
        facts, _ = KnowledgeBase(self.tmp / 'kb').facts(first['doc_id'])
        self.assertEqual({f.company for f in facts}, {'星河智造集团'})
        self.assertTrue(same_company({'company': '星河智造', 'stock_code': ''}, {'company_full': '星河智造股份有限公司', 'company': ''}))

    def test_upload_only_run_uses_materials_from_knowledge_base(self):
        kb = KnowledgeBase(self.tmp / 'kb')
        kb.add_bytes(FILING_TXT.encode('utf-8'), '星河智造_2023年年度报告.txt')
        report = kb.add_bytes(REPORT_TXT.encode('utf-8'), '虚构证券_星河智造深度.txt')
        self.assertEqual((report['doc_type'], report['company'], report['stock_code'], report['report_date']), ('research_report', '星河智造', '999001', '2024-04-25'))
        batch = run_task([report['doc_id']], kb, self.tmp / 'runs')          # 只给研报，财报从知识库自动调取
        item = batch['items'][0]
        payload = json.loads((Path(batch['out']) / item['item_id'] / 'results.json').read_text(encoding='utf-8'))
        self.assertEqual([m['source'] for m in payload['materials']], ['知识库'])
        errors = {(i['metric'], i['relation'], i['period']): i['error_type'] for i in payload['items'] if i['status'] == 'violated'}
        self.assertEqual(errors, {('REVENUE', 'yoy', '2023Y'): 'E5', ('REVENUE', 'value', '2022Y'): 'E2', ('CFO', 'value', '2023Y'): 'E4'})
        self.assertEqual(payload['summary']['core']['abstention_rate'], 0.0)
        for name in ('report.md', 'report.html', 'corrections.csv', 'audit.jsonl', 'run_manifest.json', 'facts_used.json'):
            self.assertTrue((Path(batch['out']) / item['item_id'] / name).is_file(), name)
        strict = run_task([report['doc_id']], kb, self.tmp / 'runs', options={'strict': True})
        strict_item = strict['items'][0]
        strict_payload = json.loads((Path(strict['out']) / strict_item['item_id'] / 'results.json').read_text(encoding='utf-8'))
        self.assertEqual(strict_item['totals']['violated'], 0)
        self.assertTrue(all(i['status'] == 'unverifiable' and i['abstain_code'] == 'X3'
                            for i in strict_payload['items'] if i['category'] == 'core'))
        self.assertTrue(any(a['code'] == 'scope' for i in strict_payload['items'] for a in i['assumptions']))
        analysis = run_task([d['doc_id'] for d in kb.list() if d['doc_type'] == 'annual_report'], kb, self.tmp / 'runs', mode='analysis')
        self.assertEqual(analysis['task'], 'analysis')
        self.assertGreater(analysis['items'][0]['metrics'], 5)


@unittest.skipUnless(has_pdf_backend() and (SAMPLES / 'seeded_errors.json').is_file(), '需要 PDF 后端与 samples/smart 样例')
class SampleEndToEndTests(unittest.TestCase):
    def test_seeded_errors_are_all_found_without_false_positives(self):
        tmp = Path(tempfile.mkdtemp(prefix='mj-e2e-'))
        try:
            kb = KnowledgeBase(tmp / 'kb')
            ids = [kb.add_file(path)['doc_id'] for path in sorted(SAMPLES.glob('*.pdf'))]
            batch = run_task(ids, kb, tmp / 'runs')                                     # 自动模式：两份研报各自核查
            self.assertEqual(batch['failures'], [])
            seeded = {r['report']: r['errors'] for r in json.loads((SAMPLES / 'seeded_errors.json').read_text(encoding='utf-8'))['reports']}
            self.assertEqual(len(batch['items']), len(seeded))
            kinds, payloads = set(), {}
            for item in batch['items']:
                payload = json.loads((Path(batch['out']) / item['item_id'] / 'results.json').read_text(encoding='utf-8'))
                payloads[payload['report']['name']] = payload
                errors = seeded[payload['report']['name']]
                violated = [i for i in payload['items'] if i['status'] == 'violated']
                self.assertEqual(len(violated), len(errors), payload['report']['name'])        # 无额外误报
                for error in errors:
                    number = error['wrong'].replace('万元', '').rstrip('%')
                    hit = [i for i in violated if number in i['num_text'] and (not error.get('relation') or i['relation'] == error['relation'])]
                    self.assertTrue(hit, error)
                    self.assertEqual(hit[0]['error_type'], error['error_type'], error)
                kinds |= {i['error_type'] for i in violated}
                self.assertEqual(payload['summary']['core']['abstention_rate'], 0.0)
            self.assertEqual(kinds, {'E1', 'E2', 'E3', 'E4', 'E5', 'E6', 'E7'})
            self.assertEqual(sum(len(e) for e in seeded.values()), 23)                       # 两家公司三份研报共23处预埋错误
            third = payloads['镜湖证券_镜湖科技首次覆盖_2025-04-28.pdf']
            self.assertEqual(third['market_check']['status'], 'consistent')                 # 行情框按版面行读取，股价×总股本≈总市值
            p8 = next(i for i in third['items'] if i['status'] == 'violated' and '1.90' in i['num_text'])
            self.assertEqual((p8['error_type'], p8['diagnosis']['type']), ('E6', 'E6'))       # 正文与自身预测表不一致：引用错误
            first = payloads['虚构证券_2024-04-25_星河智造深度报告.pdf']
            late = {m['period'] for m in first['materials'] if m.get('excluded')}
            self.assertEqual(late, {'2024Q1', '2024H1'})                                # 研报日期截断：之后公告的季报与半年报不得使用
            second = payloads['示例证券_2024-08-30_星河智造2024年中报点评.pdf']
            q2 = next(i for i in second['items'] if i['relation'] == 'yoy' and i['period'] == '2024Q2')
            self.assertEqual((q2['status'], q2['modality']), ('verified', 'fact'))          # 单季由半年报减一季报推导
            self.assertIn('相减', q2['definition_label'])
            second_run = run_task(ids, KnowledgeBase(tmp / 'kb'), tmp / 'runs')          # 第二次全部走缓存，结论不变
            for item in second_run['items']:
                again = json.loads((Path(second_run['out']) / item['item_id'] / 'results.json').read_text(encoding='utf-8'))
                self.assertEqual((again['kb']['parse_misses'], again['kb']['fact_misses']), (0, 0))
                self.assertEqual(again['summary']['totals'], payloads[again['report']['name']]['summary']['totals'])
            from mingjing.smart.pipeline import replay
            for item in batch['items']:
                receipt = replay(Path(batch['out']) / item['item_id'], KnowledgeBase(tmp / 'kb'))
                self.assertEqual((receipt['status'], receipt['differences']), ('identical', 0))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class _MockLLM(http.server.BaseHTTPRequestHandler):
    calls = 0
    metric = 'REVENUE'

    def log_message(self, *args):
        return

    summary = '本次核查已核实{{verified}}条，发现{{violated}}处错误（以E5为主），{{unverifiable}}条未能核实。'

    def do_POST(self):
        type(self).calls += 1
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        content = json.loads(body['messages'][1]['content'])
        if 'items' in content:
            answers = [{'id': i['id'], 'metric': type(self).metric, 'period': i['candidates']['periods'][0], 'relation': 'value', 'entity': 'company', 'segment': '', 'forecast': False} for i in content['items']]
            text = '```json\n' + json.dumps(answers) + '\n```'
        else:
            text = type(self).summary
        raw = json.dumps({'model': 'mock', 'choices': [{'finish_reason': 'stop', 'message': {'content': text}}],
                          'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class LlmBindTests(unittest.TestCase):
    def test_enum_only_binding_is_validated_cached_and_audited(self):
        from mingjing.smart.llm_bind import refine_claims
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _MockLLM)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        tmp = Path(tempfile.mkdtemp(prefix='mj-llm-'))
        try:
            cfg = {'base_url': f'http://127.0.0.1:{server.server_port}/v1', 'api_key': 'sk-test-secret', 'model': 'mock-model'}
            meta = DocMeta(doc_type='research_report', company='测试公司', base_year=2023)
            events = []
            audit = type('Audit', (), {'emit': lambda self, kind, **kw: events.append((kind, kw))})()
            kb = KnowledgeBase(tmp / 'kb')
            _MockLLM.calls, _MockLLM.metric = 0, 'REVENUE'
            _MockLLM.summary = '本次核查已核实{{verified}}条，发现{{violated}}处错误（以E5为主），{{unverifiable}}条未能核实。'
            model = build_report_model(text_doc('全年规模达到4.96亿元，表现亮眼。'), meta)
            self.assertEqual(model.claims[0].metric, '')
            summary = refine_claims(model, None, kb, cfg, audit)
            claim = model.claims[0]
            self.assertEqual((summary['status'], summary['refined'], summary['requests'], _MockLLM.calls), ('success', 1, 1, 1))
            self.assertEqual((claim.metric, claim.binder, claim.category, claim.value), ('REVENUE', 'llm', 'core', '496000000.00'))
            self.assertTrue(all('sk-test-secret' not in json.dumps(kw, ensure_ascii=False) for _, kw in events))
            again = build_report_model(text_doc('全年规模达到4.96亿元，表现亮眼。'), meta)
            cached = refine_claims(again, None, kb, cfg, audit)
            self.assertEqual((cached['requests'], cached['cache_hits'], _MockLLM.calls), (0, 1, 1))       # 命中缓存，不再请求
            _MockLLM.metric = 'DROP_TABLE'
            bad = build_report_model(text_doc('全年规模达到5.10亿元，表现亮眼。'), meta)
            rejected = refine_claims(bad, None, kb, cfg, audit)
            self.assertEqual((rejected['rejected'], bad.claims[0].metric, bad.claims[0].binder), (1, '', 'rule'))
            self.assertEqual(refine_claims(bad, None, kb, {'base_url': '', 'api_key': '', 'model': ''}, audit)['status'], 'not_configured')
            # 摘要：模型只写文字，数字由宿主按槽位填入；出现槽位以外的数字即回退到模板
            from mingjing.smart.llm_bind import write_summary
            payload = {'task': 'check', 'report': {'company': '测试公司'}, 'materials': [], 'advice': [],
                       'summary': {'totals': {'total': 10, 'occurrences': 12, 'verified': 7, 'violated': 2, 'unverifiable': 1, 'skipped': 0},
                                   'error_types': {'E5': 2}, 'core': {'abstention_rate': 0.1, 'total': 10, 'unverifiable': 1}, 'financial': {'coverage': 0.9}}}
            good = write_summary(payload, {**cfg, 'enabled': True}, kb, audit)
            self.assertEqual((good['source'], good['text']), ('llm', '本次核查已核实7条，发现2处错误（以E5为主），1条未能核实。'))
            _MockLLM.summary = '本次核查已核实{{verified}}条，另有3处疑似错误。'
            bad_summary = write_summary(payload, {**cfg, 'enabled': True, 'model': 'mock-2'}, kb, audit)
            self.assertEqual((bad_summary['source'], bad_summary.get('rejected')), ('template', True))
            self.assertIn('已核实7处', bad_summary['text'])
        finally:
            server.shutdown()
            server.server_close()
            shutil.rmtree(tmp, ignore_errors=True)


class WebServiceTests(unittest.TestCase):
    def setUp(self):
        from mingjing.smart.web import make_server
        self.tmp = Path(tempfile.mkdtemp(prefix='mj-web-'))
        self.server = make_server('127.0.0.1', 0, str(self.tmp))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, method, path, data=None, raw=False):
        request = urllib.request.Request(self.base + path, data=data, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read()
                return response.status, body if raw else json.loads(body)
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read() or b'{}')

    def test_upload_run_results_and_knowledge_base_management(self):
        from urllib.parse import quote
        status, page = self.call('GET', '/', raw=True)
        self.assertEqual(status, 200)
        self.assertIn('上传即核查', page.decode('utf-8'))
        _, filing = self.call('POST', '/api/kb/upload?name=' + quote('星河智造_2023年年度报告.txt'), FILING_TXT.encode('utf-8'))
        _, report = self.call('POST', '/api/kb/upload?name=' + quote('虚构证券_星河智造深度.txt'), REPORT_TXT.encode('utf-8'))
        _, dup = self.call('POST', '/api/kb/upload?name=' + quote('再传一次.txt'), REPORT_TXT.encode('utf-8'))
        self.assertEqual((filing['doc_type'], report['doc_type'], dup['duplicate']), ('annual_report', 'research_report', True))
        self.assertEqual(self.call('POST', '/api/kb/upload?name=evil.exe', b'MZ')[0], 400)
        status, run = self.call('POST', '/api/smart/run', json.dumps({'doc_ids': [report['doc_id']], 'mode': 'auto', 'options': {}}).encode())
        self.assertEqual(status, 202)
        deadline = time.time() + 60
        while time.time() < deadline:
            _, state = self.call('GET', '/api/smart/runs/' + run['run_id'])
            if state['status'] in ('completed', 'failed', 'cancelled'):
                break
            time.sleep(0.2)
        self.assertEqual(state['status'], 'completed', state)
        item = state['batch']['items'][0]
        _, results = self.call('GET', f"/api/smart/runs/{run['run_id']}/items/{quote(item['item_id'])}/results.json")
        self.assertEqual(results['summary']['totals']['violated'], 3)
        status, html = self.call('GET', f"/api/smart/runs/{run['run_id']}/items/{quote(item['item_id'])}/report.html", raw=True)
        self.assertIn('明镜研报核查', html.decode('utf-8'))
        status, archive = self.call('GET', f"/api/smart/runs/{run['run_id']}/export", raw=True)
        self.assertEqual(archive[:2], b'PK')
        self.assertEqual(self.call('GET', '/api/smart/runs/smart-000000000000')[0], 404)
        self.assertEqual(self.call('GET', f"/api/smart/runs/{run['run_id']}/items/..%2F..%2Fkb/results.json")[0], 400)
        _, listing = self.call('GET', '/api/kb')
        self.assertEqual((listing['stats']['documents'], listing['stats']['duplicate_uploads_saved']), (2, 1))
        _, patched = self.call('PATCH', '/api/kb/' + filing['doc_id'], json.dumps({'company': '星河智造'}).encode())
        self.assertEqual(patched['company'], '星河智造')
        self.assertEqual(self.call('GET', '/api/kb/evidence/not-an-id.png')[0], 400)
        status, removed = self.call('DELETE', '/api/kb/' + report['doc_id'])
        self.assertEqual(status, 400)
        self.assertIn('error', removed)
        self.assertEqual(self.call('GET', '/api/kb')[1]['stats']['documents'], 2)
        self.assertEqual(self.call('POST', '/api/smart/run', json.dumps({'doc_ids': [], 'mode': 'auto'}).encode())[0], 400)
        if has_pdf_backend() and (SAMPLES / 'seeded_errors.json').is_file():
            status, demo = self.call('POST', '/api/smart/demo', b'{}')
            self.assertEqual(status, 202)
            deadline = time.time() + 120
            while time.time() < deadline:
                _, state = self.call('GET', '/api/smart/runs/' + demo['run_id'])
                if state['status'] in ('completed', 'failed', 'cancelled'):
                    break
                time.sleep(0.3)
            self.assertEqual((state['status'], len(state['batch']['items'])), ('completed', 3))      # 一键演示：三份研报
            self.assertEqual(sorted(i['totals']['violated'] for i in state['batch']['items']), [6, 8, 9])      # 23处预埋错误逐份检出


class MainServerIntegrationTests(unittest.TestCase):
    def test_new_home_page_classic_ui_and_smart_routes_on_main_server(self):
        from mingjing import server
        tmp = Path(tempfile.mkdtemp(prefix='mj-main-'))
        old = os.environ.get('MINGJING_HOME')
        os.environ['MINGJING_HOME'] = str(tmp / 'profile')
        try:
            state = server.AppState()
            httpd = server.MingJingServer(('127.0.0.1', 0), state)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{httpd.server_port}'

            def get(path):
                with urllib.request.urlopen(base + path, timeout=20) as response:
                    return response.status, response.read().decode('utf-8', errors='replace')
            self.assertIn('上传即核查', get('/')[1])
            if (server.STATIC_ROOT / 'harness' / 'index.html').is_file():
                self.assertIn('<div id="root">', get('/classic')[1])
            self.assertEqual(json.loads(get('/api/kb')[1])['stats']['documents'], 0)
            self.assertEqual(json.loads(get('/api/health')[1])['status'], 'ok')
            self.assertEqual(json.loads(get('/api/runs')[1]).get('runs', []), [])
            request = urllib.request.Request(base + '/api/kb/upload?name=a.txt', data=FILING_TXT.encode('utf-8'), method='POST')
            with urllib.request.urlopen(request, timeout=30) as response:
                self.assertEqual(json.loads(response.read())['doc_type'], 'annual_report')
            httpd.shutdown()
            httpd.server_close()
            thread.join(3)
        finally:
            if old is None:
                os.environ.pop('MINGJING_HOME', None)
            else:
                os.environ['MINGJING_HOME'] = old
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)


class FusionTests(VerifyTests):
    """融合版新增：行情框版式、计划书表5-6错误代码、推定披露、补材分组、勾稽检查、留痕校验、接口与命令行。"""

    def test_market_box_layouts_and_cross_check(self):
        from mingjing.smart.claims import market_consistency, parse_market_box
        cases = {'同行并排': '当前股价(元)  18.60  总市值(亿元)  78.12  总股本(亿股)  4.20\n',
                 '标签数值分列': '当前股价(元)          15.20\n总股本(亿股)          4.00\n总市值(亿元)          60.80\n',
                 '上下两行': '当前股价(元)   总市值(亿元)   总股本(亿股)\n18.60   78.12   4.20\n'}
        for name, text in cases.items():
            market = parse_market_box(text_doc(text))
            self.assertEqual(set(market) >= {'PRICE', 'MARKET_CAP', 'TOTAL_SHARES'}, True, name)
            self.assertEqual(market_consistency(market)['status'], 'consistent', name)
        # 阅读序文本把“总市值(亿元)”和股价拆在相邻两行：不得跨行拼接
        trap = Document('/virtual/t.pdf', 't.pdf', 'b' * 64, 'pdf', 'poppler', [Page(1, [], '总市值(亿元)\n15.20\n当前股价(元) 15.20\n')])
        market = parse_market_box(trap)
        self.assertNotIn('MARKET_CAP', market)
        self.assertEqual(market['PRICE']['value'], '15.20')
        bad = parse_market_box(text_doc('当前股价(元)  10.00  总市值(亿元)  60.00  总股本(亿股)  4.00\n'))
        self.assertEqual(market_consistency(bad)['status'], 'inconsistent')

    def test_direction_reversal_is_e7_and_presumptions_are_disclosed(self):
        rows, model = self.run_text('2023年公司营业收入4.96亿元，同比下降58.47%。')
        value = next(r for c, r in rows if c.relation == 'value')
        growth = next(r for c, r in rows if c.relation == 'yoy')
        self.assertEqual((value.status, growth.status, growth.error_type), ('verified', 'violated', 'E7'))
        self.assertIn('scope', {a['code'] for a in value.assumptions})                    # 未写口径→按合并报表核对并写明
        summary = summarize(model.claims, [r for _, r in rows])
        self.assertTrue(any(a['code'] == 'scope' and a['count'] >= 1 for a in summary['assumptions']))
        claim, result = self.one('2023年合并报表营业收入4.96亿元。')
        self.assertNotIn('scope', {a['code'] for a in result.assumptions})
        claim, result = self.one('2023年归母净利润为-8050万元（+29%）。') if False else self.run_text('2023年归母净利润为-8050万元（+29%）。')[0][1]
        self.assertIn('negative_base', {a['code'] for a in result.assumptions})

    def test_gaps_are_grouped_by_what_to_supplement(self):
        from mingjing.smart.pipeline import advice_from_gaps, build_gaps
        items = [{'cid': 'c1', 'status': 'unverifiable', 'abstain_code': 'X1', 'category': 'core', 'metric_name': '营业收入', 'page': 3, 'text': '2020年营业收入8亿元',
                  'missing': [{'account': 'REVENUE', 'name': '营业收入', 'period': '2020Y'}]},
                 {'cid': 'c2', 'status': 'unverifiable', 'abstain_code': 'X1', 'category': 'core', 'metric_name': '营业收入', 'page': 4, 'text': '2016年营业收入1亿元',
                  'missing': [{'account': 'REVENUE', 'name': '营业收入', 'period': '2016Y'}]},
                 {'cid': 'c3', 'status': 'unverifiable', 'abstain_code': 'X1', 'category': 'core', 'metric_name': '营业收入', 'page': 5, 'text': '共建模式收入占比',
                  'missing': [{'account': 'REVENUE', 'period': '2023Y', 'dimension': '共建模式下形成/比'}]},
                 {'cid': 'c4', 'status': 'unverifiable', 'abstain_code': 'X3', 'category': 'open_metric', 'metric_name': 'EBITDA', 'page': 9, 'text': 'EBITDA'},
                 {'cid': 'c5', 'status': 'unverifiable', 'abstain_code': 'X1', 'category': 'external', 'metric_name': '', 'num_text': '620亿元', 'page': 2, 'text': '市场规模620亿元'},
                 {'cid': 'c6', 'status': 'verified', 'abstain_code': '', 'category': 'core'}]
        facts = [fact('REVENUE', '2023Y', 1), fact('REVENUE', '2022Y', 1)]
        meta = DocMeta(company='星河智造', stock_code='999001')
        gaps = build_gaps(items, [{'name': 'a.pdf'}], meta, facts)
        self.assertEqual([g['kind'] for g in gaps], ['missing_report', 'missing_report', 'missing_detail', 'definition', 'external'])
        self.assertEqual({g['need'] for g in gaps if g['kind'] == 'missing_report'}, {'2020年年度报告', '2016年年度报告'})
        self.assertIn('2021年年度报告（含上年比较数）', next(g for g in gaps if g['need'] == '2020年年度报告')['alternative'])
        self.assertIn('“共建模式”', next(g for g in gaps if g['kind'] == 'missing_detail')['need'])
        advice = advice_from_gaps(gaps, [{'name': 'a.pdf'}], meta)
        self.assertTrue(any('2020年年度报告' in a and '影响1条' in a for a in advice))
        self.assertTrue(any('999001' in a for a in advice))

    def test_analysis_consistency_checks_and_highlights(self):
        from mingjing.smart.analysis import analyze
        facts = [fact('ASSETS', '2023Y', 100), fact('LIABILITIES', '2023Y', 40), fact('EQUITY', '2023Y', 60),
                 fact('ASSETS', '2022Y', 90), fact('LIABILITIES', '2022Y', 40), fact('EQUITY', '2022Y', 60),
                 fact('REVENUE', '2023Y', 100), fact('REVENUE', '2022Y', 80), fact('REVENUE', '2023Y', '0.25', unit='%', relation='yoy', source_type='financial_summary')]
        facts += [fact('REVENUE', f'2023Q{q}', v, source_type='financial_summary') for q, v in zip(range(1, 5), (10, 20, 30, 40))]
        result = analyze(facts, '测试公司')
        verdict = {(c['category'], c['period']): c['ok'] for c in result['checks']}
        self.assertEqual(verdict[('identity', '2023Y')], True)
        self.assertEqual(verdict[('identity', '2022Y')], False)                           # 90 ≠ 40 + 60
        self.assertEqual((verdict[('yoy', '2023Y')], verdict[('quarter', '2023Y')]), (True, True))
        self.assertEqual(result['check_summary']['failed'], 1)
        self.assertTrue(any(s['code'] == 'S-CHECK' for s in result['signals']))
        self.assertTrue(any('勾稽检查' in line and '1项不一致' in line for line in result['highlights']))

    def test_journal_hash_chain_detects_tampering(self):
        tmp = Path(tempfile.mkdtemp(prefix='mj-journal-'))
        try:
            kb = KnowledgeBase(tmp)
            kb.add_bytes('2023年营业收入4.96亿元。'.encode('utf-8'), 'a.md')
            kb.add_bytes('2023年营业收入4.96亿元。'.encode('utf-8'), 'b.md')                   # 去重命中也留痕
            receipt = kb.verify_journal()
            self.assertEqual((receipt['ok'], receipt['entries'] >= 3), (True, True))
            lines = kb.journal_path.read_text(encoding='utf-8').splitlines()
            target = next(i for i, line in enumerate(lines) if '"event": "dedupe_hit"' in line)
            lines[target] = lines[target].replace('"name": "b.md"', '"name": "forged.md"')
            kb.journal_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
            broken = kb.verify_journal()
            self.assertEqual((broken['ok'], broken['broken_at']), (False, target + 1))      # 被改动的那一条断链
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    @unittest.skipUnless(has_pdf_backend() and (SAMPLES / 'seeded_errors.json').is_file(), '需要 PDF 后端与 samples/smart 样例')
    def test_settings_status_page_and_replay_endpoints(self):
        from mingjing.smart.web import make_server
        tmp = Path(tempfile.mkdtemp(prefix='mj-web2-'))
        server = make_server('127.0.0.1', 0, str(tmp))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{server.server_port}'

        def call(path, data=None, method=None, raw=False):
            body = None if data is None else (data if isinstance(data, bytes) else json.dumps(data).encode())
            req = urllib.request.Request(base + path, data=body, method=method or ('POST' if body is not None else 'GET'),
                                         headers={'Content-Type': 'application/octet-stream' if isinstance(data, bytes) else 'application/json'})
            with urllib.request.urlopen(req, timeout=60) as res:
                payload = res.read()
                return payload if raw else json.loads(payload)
        try:
            settings = call('/api/smart/settings')
            self.assertEqual((settings['writable'], settings['persistent'], settings['effective']['configured']), (True, True, False))
            saved = call('/api/smart/settings', {'llm': {'enabled': True, 'base_url': 'http://127.0.0.1:9/v1', 'model': 'mock-model'}, 'api_key': 'sk-test'})
            self.assertEqual((saved['llm']['has_key'], saved['effective']['configured'], saved['effective']['model']), (True, True, 'mock-model'))
            self.assertNotIn('sk-test', json.dumps(saved))                                 # 密钥不回显
            with self.assertRaises(urllib.error.HTTPError) as bad:
                call('/api/smart/settings', {'llm': {'base_url': 'ftp://x'}})
            self.assertEqual(bad.exception.code, 400)
            status = call('/api/smart/status')
            self.assertTrue(status['pipeline'].startswith('smart-'))
            ids = []
            for name in ('星河智造_2023年年度报告.pdf', '虚构证券_2024-04-25_星河智造深度报告.pdf'):
                ids.append(call('/api/kb/upload?name=' + urllib.parse.quote(name), (SAMPLES / name).read_bytes())['doc_id'])
            if shutil.which('pdftoppm') or 'fitz' in status['pdf_backends']:
                png = call(f'/api/kb/{ids[1]}/page/1.png?bbox=50,60,300,90', raw=True)
                self.assertEqual(png[:8], b'\x89PNG\r\n\x1a\n')
            run = call('/api/smart/run', {'doc_ids': ids, 'mode': 'auto', 'options': {'kb_lookup': False}})
            for _ in range(240):
                record = call('/api/smart/runs/' + run['run_id'])
                if record['status'] not in ('queued', 'running'):
                    break
                time.sleep(0.25)
            self.assertEqual(record['status'], 'completed', record)
            item = record['batch']['items'][0]['item_id']
            receipt = call(f"/api/smart/runs/{run['run_id']}/items/{urllib.parse.quote(item)}/replay", {})
            self.assertEqual((receipt['status'], receipt['differences']), ('identical', 0))
            journal = call('/api/kb/journal/verify')
            self.assertTrue(journal['ok'])
        finally:
            server.shutdown()
            server.server_close()
            shutil.rmtree(tmp, ignore_errors=True)

    def test_cli_knowledge_base_list_remove_verify(self):
        import subprocess
        tmp = Path(tempfile.mkdtemp(prefix='mj-cli-kb-'))
        try:
            source = tmp / '研报.md'
            source.write_text('证券研究报告\n星河智造（999001.SH）2024年4月25日 投资评级：买入\n2023年公司营业收入12.86亿元。\n', encoding='utf-8')
            run = lambda *args: subprocess.run([sys.executable, '-m', 'mingjing.cli', 'smart', 'kb', '--kb', str(tmp / 'kb'), *args],
                                               cwd=str(ROOT), capture_output=True, text=True, encoding='utf-8', timeout=120)
            self.assertEqual(run(str(source)).returncode, 0)
            listed = run('--list')
            self.assertEqual(listed.returncode, 0, listed.stderr)
            prefix = listed.stdout.strip().split('\t')[0]
            self.assertEqual(len(prefix), 12)
            verified = run('--verify')
            self.assertEqual((verified.returncode, json.loads(verified.stdout)['ok']), (0, True))
            removed = run('--remove', prefix)
            self.assertEqual(removed.returncode, 0, removed.stderr)
            self.assertEqual(run('--list').stdout.strip(), '')
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
