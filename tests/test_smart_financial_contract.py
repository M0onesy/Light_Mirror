"""Financial counterexamples from the delivery audit, using in-memory inputs."""
import hashlib
import unittest
from dataclasses import replace
from decimal import Decimal, localcontext

from mingjing.models import Fact
from mingjing.smart.analysis import analyze
from mingjing.smart.claims import Claim, ReportModel, ReportTable, build_report_model
from mingjing.smart.common import IV, quantum
from mingjing.smart.detect import DocMeta
from mingjing.smart.docio import Document, Page, rows_from_layout_text
from mingjing.smart.tables import extract_filing_facts
from mingjing.smart.verify import Verifier, figure_tick_run, registered_definitions, summarize, verify_model


def fact(value, account='REVENUE', period='2023Y', **overrides):
    fields = dict(fact_id='f-' + str(value) + str(overrides), company='测试公司', account=account,
                  period=period, value=Decimal(str(value)), decimals=2, display_unit='元',
                  source_doc='a' * 12, publish_date='2024-04-20', evidence=('a' * 12 + ':p1:source',))
    fields.update(overrides)
    return Fact(**fields)


def model(text, segments=()):
    meta = DocMeta(doc_type='research_report', company='测试公司', report_date='2024-04-25', base_year=2023)
    doc = Document('/virtual/report.txt', 'report.txt', hashlib.sha256(text.encode()).hexdigest(),
                   'text', 'text', [Page(1, rows_from_layout_text(1, text), text)])
    return build_report_model(doc, meta, set(segments))


class FinancialAdmissionTests(unittest.TestCase):
    def test_company_currency_confidence_and_definition_are_admitted(self):
        m = model('2023年合并报表营业收入为1.00亿元。')
        for changes, code in [({'company': '其他公司'}, 'X1'), ({'currency': 'USD'}, 'X1'),
                              ({'parse_conf': '0.1'}, 'X4'), ({'definition_id': 'metric:foreign-definition'}, 'X1')]:
            with self.subTest(changes=changes):
                result = verify_model([fact(100000000, **changes)], m)[0]
                self.assertEqual((result.status, result.abstain_code), ('unverifiable', code))
        self.assertEqual(verify_model([fact(100000000)], m)[0].status, 'verified')

    def test_future_unknown_dates_and_conflicts_cannot_be_rescued(self):
        m = model('2023年合并报表营业收入为1.00亿元。')
        for facts, code in [([fact(100000000, publish_date='2025-04-20')], 'X1'),
                            ([fact(100000000, publish_date='')], 'X5'),
                            ([fact(100000000), fact(90000000)], 'X4')]:
            result = verify_model(facts, m)[0]
            self.assertEqual((result.status, result.abstain_code), ('unverifiable', code))

    def test_future_period_scenario_is_not_historical_disclosure(self):
        m = model('2023年合并报表营业收入为1.00亿元。')
        scenario = fact(200000000, publish_date='2023-10-16')
        self.assertEqual(verify_model([scenario], m)[0].abstain_code, 'X1')
        result = verify_model([scenario, fact(100000000)], m)[0]
        self.assertEqual(result.status, 'verified')
        self.assertEqual([operand['publish_date'] for operand in result.operands], ['2024-04-20'])
        text = '合并利润表\n项目        2023年       2022年\n营业收入    200000000.00 100000000.00\n'
        doc = Document('/virtual/scenario.txt', 'scenario.txt', 'c' * 64, 'text', 'text',
                       [Page(1, rows_from_layout_text(1, text), text)])
        facts, diag = extract_filing_facts(doc, DocMeta(company='测试公司', report_date='2023-10-16'))
        self.assertEqual({f.period for f in facts}, {'2022Y'})
        self.assertEqual(diag['excluded_future_period_cells'], 1)

    def test_rejected_older_versions_do_not_invalidate_current_source(self):
        m = model('2023年合并报表营业收入为1.00亿元。')
        for older in [[fact(90000000, publish_date='2024-04-01'), fact(80000000, publish_date='2024-04-01')],
                      [fact(90000000, publish_date='2024-04-01', parse_conf='0.1')],
                      [fact(90000000, publish_date='')]]:
            with self.subTest(older=older):
                self.assertEqual(verify_model([fact(100000000)] + older, m)[0].status, 'verified')
        current_conflict = [fact(90000000), fact(80000000), fact(100000000, publish_date='2024-04-01')]
        self.assertEqual(verify_model(current_conflict, m)[0].abstain_code, 'X4')

    def test_market_box_cap_and_share_inputs_are_not_independent_checks(self):
        m = model('当前股价(元)  10.00  总市值(亿元)  6.00  总股本(亿股)  0.60\n')
        self.assertTrue({'PRICE', 'MARKET_CAP', 'TOTAL_SHARES'} <= set(m.market))
        market_results = [r for c, r in zip(m.claims, verify_model([fact(30000000, 'SHARE_CAPITAL')], m))
                          if c.category == 'market']
        self.assertEqual(len(market_results), 3)
        self.assertTrue(all(r.status == 'skipped' for r in market_results))

    def test_earlier_version_is_explicit_and_standard_only(self):
        m = model('2023年合并报表营业收入为1.00亿元。')
        facts = [fact(100000000, publish_date='2024-04-01'), fact(120000000)]
        standard = verify_model(facts, m)[0]
        self.assertEqual(standard.status, 'verified')
        self.assertIn('version', {a['code'] for a in standard.assumptions})
        self.assertEqual(verify_model(facts, m, policy='strict')[0].status, 'unverifiable')

    def test_mother_company_uses_declared_scope(self):
        facts = [fact(100000000), fact(80000000, scope='母公司')]
        wrong = verify_model(facts, model('2023年母公司营业收入为1.00亿元。'))[0]
        correct = verify_model(facts, model('2023年母公司营业收入为0.80亿元。'))[0]
        self.assertEqual(wrong.status, 'violated')
        self.assertEqual(correct.status, 'verified')
        self.assertEqual(correct.operands[0]['scope'], '母公司')
        self.assertFalse(any(a['code'] == 'scope' for a in correct.assumptions))

    def test_parent_attribution_label_is_not_parent_statement_scope(self):
        claim = Claim(cid='c1', kind='table', metric='NP_PARENT', period='2023Y',
                      text='归属母公司净利润 100', value='100', unit='元', decimals=2)
        m = ReportModel(DocMeta(company='测试公司', report_date='2024-04-25'), [claim], [], {}, {})
        result = verify_model([fact(100, 'NP_PARENT')], m)[0]
        self.assertEqual(result.status, 'verified')
        self.assertEqual(result.operands[0]['scope'], '合并')
        self.assertIn('scope', {a['code'] for a in result.assumptions})
        self.assertEqual(verify_model([fact(100, 'NP_PARENT')], m, policy='strict')[0].status, 'unverifiable')

    def test_default_scope_is_standard_assumption_and_strict_abstention(self):
        m = model('2023年公司营业收入为1.00亿元。')
        standard = verify_model([fact(100000000)], m)[0]
        self.assertEqual(standard.status, 'verified')
        self.assertIn('scope', {a['code'] for a in standard.assumptions})
        self.assertEqual(verify_model([fact(100000000)], m, policy='strict')[0].status, 'unverifiable')

    def test_segment_abbreviation_requires_source_identity_mapping(self):
        facts = [fact(414000000, dimension_kind='business', dimension_id='超算云服务'),
                 fact(61000000, dimension_kind='business', dimension_id='超算云系统集成')]
        result = verify_model(facts, model('2023年超算云整体收入为4.14亿元。'))[0]
        self.assertEqual((result.status, result.abstain_code, result.error_type), ('unverifiable', 'X3', ''))
        result = verify_model(facts, model('2023年超算云服务收入为4.14亿元。', ('超算云服务',)))[0]
        self.assertEqual(result.status, 'verified')

    def test_registry_evidence_is_frozen_on_results(self):
        result = verify_model([fact(100000000)], model('2023年合并报表营业收入为1.00亿元。'))[0]
        registration = next(r for r in registered_definitions() if r['rule_id'] == result.rule_ids[0])
        self.assertEqual(result.definition_source_hash, registration['function_source_hash'])
        self.assertEqual(len(result.contract_hash), 64)

    def test_verification_intervals_disclose_coarser_source_precision(self):
        result = verify_model([fact(100000000, decimals=0, display_unit='亿元')],
                              model('2023年合并报表营业收入为1.00亿元。'))[0]
        self.assertEqual(result.status, 'verified')
        self.assertEqual(result.candidate_interval['lower'], '99500000.00')
        self.assertEqual(result.expected_interval['lower'], '50000000')
        self.assertFalse(result.expected_interval['upper_closed'])
        self.assertIn('A5', result.advisories)
        self.assertFalse(result.establishes_exact_value)
        self.assertEqual(result.source_precision[0]['quantum'], '100000000')
        self.assertEqual(result.operands[0]['currency'], 'CNY')
        self.assertEqual(result.operands[0]['definition_id'], '')
        self.assertIn('更细数值', result.precision_note)

    def test_subject_alias_requires_source_declared_matching_security_code(self):
        m = model('2023年合并报表营业收入为1.00亿元。')
        m.meta.stock_code = '999901'
        source = fact(100000000, company='测试示范公司')
        metadata = {source.source_doc: {'company': '测试示范公司', 'company_full': '测试示范公司股份有限公司',
                                       'stock_code': '999901'}}
        result = verify_model([source], m, doc_index=metadata)[0]
        self.assertEqual(result.status, 'verified')
        self.assertEqual(result.operands[0]['subject_evidence']['source_company'], '测试示范公司')
        metadata[source.source_doc]['stock_code'] = '999902'
        self.assertEqual(verify_model([source], m, doc_index=metadata)[0].abstain_code, 'X1')
        metadata[source.source_doc]['stock_code'] = '999901'
        metadata[source.source_doc]['company'] = '无关公司'
        metadata[source.source_doc]['company_full'] = ''
        self.assertEqual(verify_model([source], m, doc_index=metadata)[0].abstain_code, 'X1')
        self.assertEqual(verify_model([fact(100000000, company='测试公司子公司')], m)[0].abstain_code, 'X1')

    def test_threshold_result_keeps_unbounded_claim_side(self):
        result = verify_model([fact(200000000)], model('2023年合并报表营业收入超过1.00亿元。'))[0]
        self.assertEqual(result.candidate_interval, {'lower': '100000000.00', 'upper': None,
                                                    'lower_closed': False, 'upper_closed': False})
        self.assertEqual(result.verification_basis, 'source_interval_entails_threshold')
        self.assertIn('SMART:SOURCE_THRESHOLD:threshold.entailment', result.rule_ids)
        self.assertTrue(result.source_precision)


class NumericSemanticsTests(unittest.TestCase):
    def test_latest_share_capital_is_not_overridden_by_older_total_shares(self):
        facts = [fact(-114165954.11, 'NP_PARENT', period='2022Y'),
                 fact(34094450, 'TOTAL_SHARES', period='2018Y', decimals=0, display_unit='股'),
                 fact(58230000, 'SHARE_CAPITAL', period='2023Y')]
        claim = Claim(cid='c1', kind='table', metric='EPS_LATEST', period='2022Y',
                      value='-1.96', unit='元', decimals=2, category='core', scope='合并')
        m = ReportModel(DocMeta(company='测试公司', report_date='2024-04-25'), [claim], [], {}, {})
        result = verify_model(facts, m)[0]
        self.assertEqual(result.status, 'verified')
        self.assertEqual(result.operands[-1]['value'], '58230000')
        self.assertEqual(result.operands[-1]['period'], '2023Y')
        # A current share count wins over monetary capital only at the same date.
        facts.append(fact(60000000, 'TOTAL_SHARES', period='2023Y', decimals=0, display_unit='股'))
        result = verify_model(facts, m)[0]
        self.assertEqual(result.operands[-1]['account'], 'TOTAL_SHARES')
        self.assertEqual(result.operands[-1]['period'], '2023Y')

    def test_declared_share_denominator_is_the_operand(self):
        facts = [fact(120000000), fact(30000000, dimension_kind='business', dimension_id='云业务'),
                 fact(60000000, dimension_kind='business', dimension_id='企业业务')]
        for percent, expected in [(25, 'violated'), (50, 'verified')]:
            m = model(f'2023年云业务收入占企业业务收入的比例为{percent}%。', ('云业务', '企业业务'))
            result = verify_model(facts, m)[0]
            self.assertEqual(result.status, expected)
            self.assertEqual(result.operands[-1]['dimension'], '企业业务')

    def test_threshold_checks_entailment_without_finite_upper_protection(self):
        m = model('2023年合并报表营业收入超过1.00亿元。')
        self.assertEqual(verify_model([fact(99600000)], m)[0].status, 'violated')
        self.assertEqual(verify_model([fact(200000000)], m)[0].status, 'verified')
        self.assertEqual(verify_model([fact(100000000)], m)[0].status, 'unverifiable')
        for wording, operator in [('不低于', 'ge'), ('不高于', 'le'), ('低于', 'lt')]:
            self.assertEqual(model(f'2023年合并报表营业收入{wording}1.00亿元。').claims[0].threshold_operator, operator)

    def test_negative_bounds_and_inclusive_exact_endpoint(self):
        verifier = Verifier([], model(''))
        for operator, value, wanted in [('gt', '-1', 'verified'), ('gt', '-2', 'violated'),
                                        ('ge', '-2', 'verified'), ('lt', '-2', 'violated'),
                                        ('le', '-2', 'verified')]:
            claim = Claim(value='-2', threshold_operator=operator)
            self.assertEqual(verifier._threshold(claim, IV(Decimal(value))), wanted)

    def test_adjacent_disclosed_cent_values_do_not_match(self):
        result = verify_model([fact('100.01')], model('2023年合并报表营业收入为100.00元。'))[0]
        self.assertEqual(result.status, 'violated')
        self.assertFalse(IV.rounded(Decimal('-100.00'), Decimal('.01')).intersects(
                         IV.rounded(Decimal('-100.01'), Decimal('.01'))))

    def test_interval_math_ignores_ambient_decimal_precision(self):
        with localcontext() as context:
            context.prec = 3
            interval = IV.rounded(Decimal('1000'), Decimal('.01'))
            self.assertEqual(interval.to_list(), ['999.995', '1000.005'])
            self.assertEqual((interval / IV(-2)).bounds.lower_closed, False)
            self.assertEqual(quantum('bp', 4), Decimal('.00000001'))
            self.assertEqual((IV(Decimal('100000000000000000000000000000000000001')) + IV(1)).mid,
                             Decimal('100000000000000000000000000000000000002'))

    def test_cagr_uses_certified_shared_roots_and_rejects_zero_base(self):
        m = model('2022-2023年合并报表营业收入CAGR为50%。')
        self.assertEqual(verify_model([fact(150000000), fact(100000000, period='2022Y')], m)[0].status, 'verified')
        result = verify_model([fact(150000000), fact(0, period='2022Y')], m)[0]
        self.assertEqual((result.status, result.abstain_code), ('unverifiable', 'X2'))


class ProvenanceAndInternalTests(unittest.TestCase):
    def test_financial_captions_and_growth_to_amount_are_preserved(self):
        m = model('图7:期间费用率由2016年的149.36%下降至2023年的43%\n'
                  '图8:2023年归母净利润为-8050 万元(+29%)\n'
                  '2022年超算云服务为主要创收模块,营收2.67亿元,2023年进一步增长55%至4.14亿元。', ('超算云服务',))
        values = {(claim.num_text, claim.metric, claim.period, claim.relation) for claim in m.claims}
        self.assertIn(('149.36%', 'PERIOD_EXPENSE_RATIO', '2016Y', 'value'), values)
        self.assertIn(('43%', 'PERIOD_EXPENSE_RATIO', '2023Y', 'value'), values)
        self.assertIn(('-8050万元', 'NP_PARENT', '2023Y', 'value'), values)
        self.assertIn(('+29%', 'NP_PARENT', '2023Y', 'yoy'), values)
        self.assertIn(('55%', 'REVENUE', '2023Y', 'yoy'), values)

    def test_repeated_assertions_keep_every_original_occurrence(self):
        sentence = '2023年合并报表营业收入为1.00亿元。'
        m = model(sentence + '\n' + sentence)
        self.assertEqual(len(m.claims), 2)
        self.assertEqual(len({c.cid for c in m.claims}), 2)
        self.assertEqual(len({c.assertion_id for c in m.claims}), 1)
        self.assertEqual(len({c.source_paragraph_id for c in m.claims}), 2)
        self.assertTrue(all(c.occurrences == 1 for c in m.claims))
        results = verify_model([fact(100000000)], m)
        stats = summarize(m.claims, results)
        self.assertEqual((stats['totals']['total'], stats['totals']['occurrences'], stats['totals']['verified']), (2, 2, 2))
        self.assertEqual(stats['assertions']['total'], 1)
        self.assertEqual(stats['assertions']['by_status'], {'verified': 1})

    def test_distinct_citations_are_checked_separately(self):
        m = model('根据公司2023年年报，2023年合并报表营业收入为1.00亿元。\n'
                  '根据公司2022年年报，2023年合并报表营业收入为1.00亿元。')
        self.assertEqual(len(m.claims), 2)
        facts = [fact(100000000), fact(80000000, period='2022Y', source_doc='b' * 12)]
        result = verify_model(facts, m, doc_index={'a' * 12: {'doc_type': 'annual_report', 'period': '2023Y'},
                                                  'b' * 12: {'doc_type': 'annual_report', 'period': '2022Y'}})
        self.assertEqual([(r.status, r.error_type) for r in result], [('verified', ''), ('violated', 'E6')])

    def test_text_same_number_wrong_period_or_future_date_cannot_verify(self):
        m = model('2023年合并报表营业收入为1.00亿元。')
        for text, published, expected in [('2022年合并报表营业收入为1.00亿元。', '2023-04-20', 'unverifiable'),
                                          ('2023年合并报表营业收入为1.00亿元。', '2025-04-20', 'unverifiable'),
                                          ('2023年合并报表营业收入为1.00亿元。', '2024-04-20', 'verified')]:
            texts = [{'name': 'source', 'sha': 'b' * 64, 'page': 1, 'text': text, 'report_date': published,
                      'company': '测试公司', 'currency': 'CNY', 'period': '2023Y'}]
            self.assertEqual(verify_model([], m, texts=texts)[0].status, expected)

    def test_text_cache_uses_content_when_source_metadata_collides(self):
        m = model('2023年合并报表营业收入为2.00亿元。')
        shared = {'name': 'source', 'sha': '', 'page': 1, 'report_date': '2024-04-20',
                  'company': '测试公司', 'period': '2023Y'}
        texts = [{**shared, 'text': '2023年合并报表营业收入为1.00亿元。'},
                 {**shared, 'text': '2023年合并报表营业收入为2.00亿元。'}]
        self.assertEqual(verify_model([], m, texts=texts)[0].status, 'verified')

    def test_single_quarter_header_binds_quarter_not_year(self):
        """季报“本报告期（2026年7-9月）｜上年同期（2025年7-9月）”必须绑成单季。

        版面把表头拆成“本报告期 上年同期 / 项目 / (2026年7-9月) (2025年7-9月)”三行，
        月份区间若退化成年度，单季利润表数据就会被当成全年值，进而在增长率勾稽里
        顶掉年报的真实全年基期（真实案例：2026 三季报 p3，营收 276,360,399.94 是
        2025 年 7-9 月，不是 2025 年全年）。
        """
        text = ('第二节  公司基本情况\n'
                '一、  主要财务数据\n'
                '单位：元\n'
                '本报告期  上年同期  本报告期比上年\n'
                '项目\n'
                '(2026年7-9月)  (2025年7-9月)  同期增减比例%\n'
                '营业收入  600691926.83  276360399.94  117.36%\n'
                '归属于上市公司股东的净利润  36879290.17  3329384.38  1007.69%\n')
        doc = Document('/virtual/quarterly.txt', 'quarterly.txt', 'd' * 64, 'text', 'text',
                       [Page(1, rows_from_layout_text(1, text), text)])
        facts, _ = extract_filing_facts(doc, DocMeta(company='测试公司', doc_type='quarterly_report',
                                                     period='20269M', report_date='2026-11-01'))
        revenue = {f.period: f.value for f in facts if f.account == 'REVENUE' and f.source_relation == 'value'}
        self.assertEqual(revenue, {'2026Q3': Decimal('600691926.83'), '2025Q3': Decimal('276360399.94')})
        self.assertEqual({f.period: f.value for f in facts if f.account == 'NP_PARENT' and f.source_relation == 'value'},
                         {'2026Q3': Decimal('36879290.17'), '2025Q3': Decimal('3329384.38')})
        # 单季表不得再产出年度事实，否则会盖掉年报的全年值
        self.assertFalse([f for f in facts if f.account in ('REVENUE', 'NP_PARENT') and f.period.endswith('Y')])
        self.assertEqual([(f.period, f.value) for f in facts if f.account == 'REVENUE' and f.source_relation == 'yoy'],
                         [('2026Q3', Decimal('1.1736'))])

    def test_split_month_header_preserves_intervening_annual_columns(self):
        text = ('合并利润表\n'
                '                  2023年1月\n'
                '项目                            2022年度       2021年度       2020年度\n'
                '                    —6月\n'
                '营业收入          202512534.26  312776998.45  220099934.89  122603340.15\n')
        doc = Document('/virtual/wrapped.txt', 'wrapped.txt', 'e' * 64, 'text', 'text',
                       [Page(1, rows_from_layout_text(1, text), text)])
        facts, _ = extract_filing_facts(doc, DocMeta(company='测试公司', report_date='2023-10-16'))
        self.assertEqual({f.period: f.value for f in facts if f.account == 'REVENUE'},
                         {'2023H1': Decimal('202512534.26'), '2022Y': Decimal('312776998.45'),
                          '2021Y': Decimal('220099934.89'), '2020Y': Decimal('122603340.15')})
        broken = text.replace('—6月', '—7月')
        doc.pages = [Page(1, rows_from_layout_text(1, broken), broken)]
        facts, _ = extract_filing_facts(doc, DocMeta(company='测试公司', report_date='2023-10-16'))
        self.assertFalse(any(f.period == '2023H1' for f in facts))

    def test_nonrecurring_net_is_not_attributed_equity(self):
        text = ('非经常性损益明细\n项目          2023年\n'
                '归属于母公司所有者权益的非经常性损益净额    2039608.80\n')
        doc = Document('/virtual/nri.txt', 'nri.txt', 'f' * 64, 'text', 'text',
                       [Page(1, rows_from_layout_text(1, text), text)])
        facts, _ = extract_filing_facts(doc, DocMeta(company='测试公司', report_date='2024-04-20'))
        self.assertEqual([(f.account, f.value) for f in facts], [('NONRECURRING_NET', Decimal('2039608.80'))])

    def test_annual_series_is_not_overridden_by_relative_quarter_labels(self):
        m = model('2021年公司营业收入为1.00亿元。\n'
                  '2020-2022年第四季度占超算云服务全年收入的比例分别为37.56%、35.96%和31.89%。',
                  ('超算云服务',))
        shares = [c for c in m.claims if c.relation == 'share']
        self.assertEqual([(c.period, c.value) for c in shares],
                         [('2020Y', '0.3756'), ('2021Y', '0.3596'), ('2022Y', '0.3189')])
        self.assertTrue(all('占比分子为期间片段：第四季度' in c.notes for c in shares))

    def test_prediction_input_does_not_verify_itself_but_prose_can_compare(self):
        meta = DocMeta(company='测试公司', report_date='2024-04-25', base_year=2023)
        table = ReportTable('盈利预测', 'SUMMARY', '百万元', [('2025Y', True)], 1,
                            [{'metric': 'REVENUE', 'relation': 'value', 'unit': '', 'label': '营业收入',
                              'page': 1, 'cells': [('999', Decimal('999'))]}])
        base = dict(cid='c1', value='999000000', unit='百万元', decimals=0, metric='REVENUE',
                    period='2025Y', forecast=True, category='forecast')
        cell = Claim(kind='table', table='SUMMARY', row_label='营业收入', **base)
        prose = Claim(kind='prose', **{**base, 'cid': 'c2'})
        results = verify_model([], ReportModel(meta, [cell, prose], [table], {}, {'doc_sha256': 'd' * 64}))
        self.assertEqual([r.status for r in results], ['skipped', 'verified'])
        self.assertTrue(results[1].evidence)

    def test_identity_cannot_clear_historical_fact_gap(self):
        labels = [('短期借款', 'DELTA_SHORT_BORROWING', 1), ('长期借款', 'DELTA_LONG_BORROWING', 2),
                  ('普通股增加', 'DELTA_SHARE_CAPITAL', 3), ('资本公积增加', 'DELTA_CAPITAL_RESERVE', 4),
                  ('其他筹资现金流', 'RESIDUAL', 5), ('筹资活动现金流', 'CFF', 15)]
        claims = [Claim(cid=f'c{i}', kind='table', metric=metric, period='2022Y', table='CF', row_label=label,
                        value=str(value), unit='元', decimals=2, category='template' if metric == 'RESIDUAL' else 'core')
                  for i, (label, metric, value) in enumerate(labels)]
        results = verify_model([], ReportModel(DocMeta(company='测试公司'), claims, [], {}, {}))
        self.assertEqual(results[0].status, 'unverifiable')
        self.assertEqual(results[0].abstain_code, 'X1')
        self.assertEqual(results[0].internal_check, '表内小计与明细一致')
        self.assertEqual(results[4].status, 'verified')

    @staticmethod
    def bs_claims(total_liabilities: int, period: str = '2025Y'):
        """最小的资产负债表：流动/非流动负债小计、非流动负债的两条登记明细与负债合计。

        非流动负债 550 = 长期借款 421 + 租赁负债 58（未登记 metric，进不了恒等式）
        + 其他非流动负债 72，差 1 为四舍五入，即表本身是平的。
        """
        rows = [('流动负债', 'CURRENT_LIABILITIES', 1212, 'core'),
                ('非流动负债', 'NONCURRENT_LIABILITIES', 550, 'core'),
                ('长期借款', 'LONG_TERM_BORROWING', 421, 'core'),
                ('其他非流动负债', 'RESIDUAL', 72, 'template'),
                ('负债合计', 'LIABILITIES', total_liabilities, 'core')]
        return [Claim(cid=f'c{index}', kind='table', metric=metric, period=period, table='BS', row_label=label,
                      value=str(value * 1000000), num_text=str(value), unit='百万元', decimals=0, category=category)
                for index, (label, metric, value, category) in enumerate(rows)]

    def test_missing_detail_line_with_consistent_parent_total_abstains(self):
        # 小计与已列明细不符，但 负债合计 = 流动负债 + 非流动负债 自洽：
        # 缺口来自恒等式未覆盖的明细行（租赁负债），属口径缺口，不得判为计算错误。
        results = verify_model([], ReportModel(DocMeta(company='测试公司'), self.bs_claims(1762), [], {}, {}))
        residual = results[3]
        self.assertEqual((residual.status, residual.abstain_code), ('unverifiable', 'X3'))
        self.assertEqual(residual.error_type, '')
        self.assertEqual(residual.internal_check, '表内小计与明细不一致')
        self.assertIn('负债合计 = 流动负债 + 非流动负债', residual.reason)
        self.assertIn('不判定', residual.reason)
        self.assertTrue(any(item['code'] == 'identity' for item in residual.assumptions))
        self.assertEqual(residual.expected, '129000000')

    def test_missing_detail_line_with_inconsistent_parent_total_is_still_violated(self):
        # 合计 1,900 ≠ 1,212 + 550：缺口既解释不成漏列明细行，也落在残差项上，
        # 仍按表内计算不平判错——修复弃权后不能把所有勾稽都放过。
        results = verify_model([], ReportModel(DocMeta(company='测试公司'), self.bs_claims(1900), [], {}, {}))
        residual = results[3]
        self.assertEqual((residual.status, residual.error_type), ('violated', 'E5'))
        self.assertEqual(residual.abstain_code, '')
        self.assertEqual(residual.reason, '表内勾稽不平：非流动负债 ≠ 长期借款 + 其他非流动负债')

    def test_figure_tick_run_is_abstained_instead_of_flagged(self):
        """图轴刻度混进正文行时，紧邻刻度的数值归属无从确定：弃权，而不是判错。

        真实案例：研报首页把 5pt 的图轴刻度与 10.6pt 的正文按纵向重叠聚成同一版面
        行，“同比 -48% 2025/11/3 2026/3/3 2026/7/1”里的 -48% 是图内标注，却被当成
        归母净利润的同比，与同句披露的 1,007.69% 冲突而误判 E5。
        """
        contaminated = ('2026Q3 公司实  -34% -41%现营收6.01 亿元,同比增长117.36%；'
                        '归母净利润3,687.93 万元,同比  -48% 2025/11/3  2026/3/3  2026/7/1  2026/10/29增长1,007.69%')
        claim = Claim(cid='c1', kind='prose', page=1, text=contaminated, num_text='-48%', value='-0.48',
                      unit='%', decimals=2, metric='NP_PARENT', metric_text='归母净利润', relation='yoy',
                      period='2026Q3', category='core')
        result = verify_model([], ReportModel(DocMeta(company='测试公司'), [claim], [], {}, {}))[0]
        self.assertEqual((result.status, result.abstain_code, result.error_type), ('unverifiable', 'X6', ''))
        self.assertIn('图表刻度', result.reason)
        self.assertIn('2025/11/3', result.reason)
        # 同一行里不挨着刻度的数值照常核验：不能因为一行有刻度就把整行放过
        self.assertEqual(figure_tick_run(replace(claim, cid='c2', num_text='6.01', relation='value')), '')
        # 正文里被引用的单个日期不是刻度，不触发弃权
        self.assertEqual(figure_tick_run(replace(claim, text='归母净利润同比下降48%，公司于2026/3/3披露业绩快报。')), '')
        # 表格单元格（非正文行）不走这条判定
        self.assertEqual(figure_tick_run(replace(claim, kind='table')), '')

    def test_analysis_uses_same_company_and_conflict_admission(self):
        result = analyze([fact(100000000), fact(90000000), fact(150000000, company='其他公司')], '测试公司')
        self.assertFalse(any(row['metric'] == 'REVENUE' for row in result['metrics']))
        self.assertTrue(any(c['category'] == 'admission' and c['ok'] is False for c in result['checks']))
        result = analyze([fact(100000000, publish_date='2025-04-20')], '测试公司', report_date='2024-04-25')
        self.assertEqual(result['metrics'], [])


class AnalysisPrecisionTests(unittest.TestCase):
    @staticmethod
    def cell(result, metric='REVENUE', period='2023Y', section='metrics'):
        return next(row for row in result[section] if row['metric'] == metric)['cells'][period]

    def test_coarse_precision_keeps_two_period_intervals_and_nominal_display(self):
        current = fact(100000000, decimals=0, display_unit='亿元', evidence=('current:p1:amount',))
        base = fact(100000000, period='2022Y', decimals=0, display_unit='亿元', evidence=('base:p1:amount',))
        result = analyze([current, base], '测试公司')
        cell = self.cell(result)
        self.assertEqual(cell['yoy_display'], '0.0%')
        self.assertEqual(set(cell['yoy_evidence']), {'current:p1:amount', 'base:p1:amount'})
        self.assertLess(Decimal(cell['yoy_interval']['lower']), Decimal('-0.6'))
        self.assertGreaterEqual(Decimal(cell['yoy_interval']['upper']), Decimal('2'))
        self.assertFalse(cell['interval']['upper_closed'])
        self.assertEqual(cell['source_precision'][0]['quantum'], '100000000')
        self.assertEqual(cell['yoy_calculation']['base']['period'], '2022Y')
        self.assertIn('SMART:PERIOD_CHANGE:change.abs', cell['yoy_rule_ids'])
        registration = next(row for row in registered_definitions() if row['rule_id'] == cell['rule_ids'][0])
        self.assertEqual(cell['definition_source_hash'], registration['function_source_hash'])
        self.assertEqual(len(cell['contract_hash']), 64)

    def test_missing_conflicting_or_zero_crossing_base_blocks_growth(self):
        for bases, code in [([], 'X1'), ([fact(80, period='2022Y'), fact(90, period='2022Y')], 'X4'),
                            ([fact(0, period='2022Y', decimals=0)], 'X2')]:
            with self.subTest(code=code):
                result = analyze([fact(100)] + bases, '测试公司')
                self.assertNotIn('yoy', self.cell(result))
                self.assertTrue(any(block['metric'] == 'REVENUE' and block['relation'] == 'yoy'
                                    and block['abstain_code'] == code for block in result['blocks']))

    def test_strict_scope_definition_and_negative_base_are_explicit(self):
        facts = [fact(-100), fact(-200, period='2022Y')]
        standard = analyze(facts, '测试公司')
        self.assertEqual(standard['policy'], 'standard')
        self.assertEqual(standard['assumptions'][0]['code'], 'scope')
        self.assertIn('negative_base', {a['code'] for a in self.cell(standard)['yoy_calculation']['assumptions']})
        strict = analyze(facts, '测试公司', policy='strict')
        self.assertEqual(strict['metrics'], [])
        self.assertEqual(strict['checks'], [])
        self.assertTrue(all(b['abstain_code'] == 'X3' for b in strict['blocks']))
        strict = analyze(facts, '测试公司', policy='strict', scope='合并')
        self.assertNotIn('yoy', self.cell(strict))
        self.assertTrue(any(b['relation'] == 'yoy' and b['abstain_code'] == 'X3' for b in strict['blocks']))
        facts.append(fact('0.5', source_relation='yoy', display_unit='%'))
        strict = analyze(facts, '测试公司', policy='strict', scope='合并')
        self.assertIsNone(next(check for check in strict['checks'] if check['category'] == 'yoy')['ok'])
        facts = [fact(100), fact('0.3', 'GROSS_MARGIN', display_unit='%')]
        standard = analyze(facts, '测试公司')
        self.assertEqual(self.cell(standard, 'GROSS_MARGIN')['definition_id'], 'GM.disclosed')
        strict = analyze(facts, '测试公司', policy='strict', scope='合并')
        self.assertFalse(any(row['metric'] == 'GROSS_MARGIN' for row in strict['metrics']))

    def test_quarter_derivation_and_qoq_keep_both_sources(self):
        facts = [fact(100, period='2023Y'), fact(10, period='2023Q1', evidence=('q1:p1:amount',)),
                 fact(30, period='2023H1', evidence=('h1:p1:amount',))]
        result = analyze(facts, '测试公司', scope='合并')
        quarter = self.cell(result, period='2023Q2', section='quarterly')
        self.assertEqual(Decimal(quarter['value']), Decimal('20'))
        self.assertEqual(set(quarter['evidence']), {'q1:p1:amount', 'h1:p1:amount'})
        self.assertEqual(quarter['qoq_calculation']['base_period'], '2023Q1')
        self.assertIn('SMART:PERIOD_CHANGE:quarter.derived', quarter['rule_ids'])
        self.assertIn('interval', quarter['qoq_calculation']['base'])

    def test_coarse_threshold_does_not_emit_midpoint_only_signal(self):
        facts = [fact(100000000, 'ASSETS', decimals=0, display_unit='亿元'),
                 fact(70000000, 'LIABILITIES', decimals=0, display_unit='亿元')]
        result = analyze(facts, '测试公司', scope='合并')
        self.assertEqual(self.cell(result, 'DEBT_RATIO')['display'], '70.00%')
        self.assertFalse(any(signal['code'] == 'S-DEBT' for signal in result['signals']))


if __name__ == '__main__':
    unittest.main()
