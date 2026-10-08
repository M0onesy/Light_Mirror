"""Actual financial derivations with adverse disclosure and definition cases."""
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from mingjing.analysis import analyze_financials
from mingjing.decimal_math import fact_interval
from mingjing.evidence import make_evidence_id, sha256_file
from mingjing.facts import FactStore
from mingjing.financial_derivations import derive_metric
from mingjing.judge import Judge
from mingjing.mentions import extract_mentions
from mingjing.models import Fact, FactQueryOutcome
from mingjing.parser import extract_facts, extract_fact_window


def fact(fid, account, period, value, **kwargs):
    values = dict(source_doc='a'*12, source_path='report.pdf', publish_date='2025-04-01',
                  evidence=(make_evidence_id('a'*64, 2, fid),), label=account,
                  scope='合并', nr='未扣非', decimals=2, display_unit='元')
    values.update(kwargs)
    return Fact(fid, '甲公司', account, period, value=Decimal(value), **values)


def quarter_facts(quarter='Q2', account='REVENUE'):
    periods = {'Q2': ('H1', 'Q1'), 'Q3': ('9M', 'H1'), 'Q4': ('Y', '9M')}[quarter]
    return [fact('cumulative', account, '2024'+periods[0], '120'),
            fact('prior', account, '2024'+periods[1], '90')]


def eps_facts():
    return [fact('ordinary-profit', 'NP_PARENT_ORDINARY', '2024Y', '100', label='归属于母公司普通股股东的净利润'),
            fact('weighted', 'WEIGHTED_SHARES', '2024Y', '50',
                 label='发行在外普通股的加权平均数', display_unit='股')]


def derive(facts, account='REVENUE', period='2024Q2', **options):
    return derive_metric(FactStore(facts), '甲公司', account, period, '合并', '未扣非',
                         options.pop('report_date', '2025-04-30'), **options)


@pytest.mark.parametrize('quarter', ['Q2', 'Q3', 'Q4'])
@pytest.mark.parametrize('account', ['REVENUE', 'COST', 'NP', 'NP_PARENT', 'CFO'])
def test_quarter_calculates_same_disclosure_flow_and_propagates_half_open_intervals(quarter, account):
    facts = quarter_facts(quarter, account)
    original = [row.to_dict() for row in facts]
    out = derive(facts, account, '2024'+quarter)
    assert out.value == Decimal('30')
    assert out.interval == fact_interval(facts[0]).subtract(fact_interval(facts[1]))
    assert out.rule['id'] == 'FIN-single-quarter' and out.rule['version_hash']
    assert out.rule['dimension'] == 'currency'
    assert out.evidence == [identifier for row in facts for identifier in row.evidence]
    assert [row.to_dict() for row in facts] == original and out.operands == facts


def test_quarter_zero_and_negative_net_flow_are_valid_subtractions():
    facts = quarter_facts(account='CFO')
    facts[0] = replace(facts[0], value=Decimal('80'))
    assert derive(facts, 'CFO').value == Decimal('-10')
    facts[0] = replace(facts[0], value=Decimal('90'))
    assert derive(facts, 'CFO').value == 0


@pytest.mark.parametrize('account', ['ASSETS', 'LIABILITIES', 'ROE', 'EPS_DILUTED', 'SHARES'])
def test_stock_ratio_dilution_or_share_count_cannot_be_subtracted_as_quarter_flows(account):
    result = derive(quarter_facts(account=account), account)
    assert result.value is None and result.abstention_code == 'X2'


@pytest.mark.parametrize('change', [{'version': '更正后'}, {'publish_date': '2025-04-02'},
                                   {'version_group': 'after-column'}, {'parse_conf': '0.7'},
                                   {'publish_date': ''}])
def test_quarter_conflicts_uncertain_dates_and_different_column_versions_do_not_get_bypassed(change):
    facts = quarter_facts()
    facts[1] = replace(facts[1], **change)
    result = derive(facts)
    assert result.value is None and result.abstention_code == 'X4'


@pytest.mark.parametrize('change', [{'source_doc': 'b'*12, 'source_path': 'other.pdf'},
                                   {'scope': '母公司'}, {'nr': '扣非'}, {'company': '乙公司'},
                                   {'period': '2023Q1'}, {'publish_date': '2025-05-01'}])
def test_quarter_never_patches_missing_operand_from_another_business_dimension(change):
    facts = quarter_facts()
    facts[1] = replace(facts[1], **change)
    result = derive(facts)
    assert result.value is None and result.abstention_code == 'X1'


@pytest.mark.parametrize('account,period,factory', [('REVENUE','2024Q2',quarter_facts), ('EPS','2024Y',eps_facts)])
@pytest.mark.parametrize('direct_problem', ['conflict', 'low_confidence', 'unknown_date'])
def test_conflicting_or_uncertain_disclosed_value_cannot_be_replaced_with_correct_formula(account, period, factory, direct_problem):
    facts = factory()
    direct = fact('bad-direct', account, period, '999', display_unit='元' if account != 'EPS' else '元/股')
    if direct_problem == 'conflict':
        facts += [direct, replace(direct, fact_id='bad-direct-2', value=Decimal('888'))]
    else:
        facts.append(replace(direct, **({'parse_conf':'0.7'} if direct_problem == 'low_confidence' else {'publish_date':''})))
    result = derive(facts, account, period, basic_eps_definition=True)
    assert result.value is None and result.abstention_code == 'X4'
    analysis = analyze_financials(FactStore(facts), '甲公司', [period], '2025-04-30')
    row = next(item for item in analysis['metrics'] if item['metric'] == account)
    assert row['status'] == 'abstained' and row['abstention_code'] == 'X4'


def test_authorised_basic_eps_has_shared_value_intervals_and_repair_evidence():
    facts = eps_facts()
    result = derive(facts, 'EPS', '2024Y', basic_eps_definition=True)
    assert result.value == Decimal('2') and result.rule['id'] == 'FIN-EPS'
    assert result.interval == fact_interval(facts[0]).divide_positive(fact_interval(facts[1]))
    for amount, expected_status in [('2.00', 'verified'), ('3.00', 'violated')]:
        claim = extract_mentions('甲公司2024年合并基本每股收益'+amount+'元。', '甲公司')[0]
        verdict = Judge(FactStore(facts), '2025-04-30').verify(claim)
        assert verdict.status == expected_status and verdict.rule_ids == ['FIN-EPS']
        assert verdict.formula == 'NP_PARENT/WEIGHTED_SHARES' and verdict.operands == [f.to_dict() for f in facts]
        assert verdict.expected_interval == result.interval.to_dict()
        assert verdict.evidence == result.evidence and any(result.rule['version_hash'] in item for item in verdict.basis)
        if expected_status == 'violated':
            assert verdict.error_type == 'E5' and verdict.correction and 'replacement_text' in verdict.correction
    row = next(row for row in analyze_financials(FactStore(facts), '甲公司', ['2024Y'], '2025-04-30')['metrics'] if row['metric']=='EPS')
    assert row['value'] == '2' and row['expected_interval'] == result.interval.to_dict()
    assert row['rule_metadata']['version_hash'] == result.rule['version_hash'] and row['operands'] == [f.to_dict() for f in facts]


@pytest.mark.parametrize('change', [dict(account='SHARES', label='期末发行在外普通股股数'),
    dict(label='发行在外普通股加权平均数（稀释后）'), dict(label='普通股股数'),
    dict(display_unit='元'), dict(value=Decimal('0')), dict(value=Decimal('-50'))])
def test_basic_eps_rejects_end_of_period_diluted_or_nonpositive_denominators(change):
    facts = eps_facts()
    facts[1] = replace(facts[1], **change)
    result = derive(facts, 'EPS', '2024Y', basic_eps_definition=True)
    assert result.value is None and result.abstention_code in {'X1', 'X2'}


def test_preferred_dividends_or_perpetual_profit_cannot_be_silently_set_to_zero():
    facts = eps_facts()
    # 100元 total attributable profit includes 20元 payable to preferred
    # holders. Ordinary EPS is 80/50=1.6, not the tempting 100/50=2.0.
    facts[0] = replace(facts[0], account='NP_PARENT', label='归属于母公司股东的净利润')
    facts.append(fact('preferred', 'PREFERRED_DIVIDENDS', '2024Y', '20', label='优先股股息'))
    result = derive(facts, 'EPS', '2024Y', basic_eps_definition=True)
    assert result.value is None and result.abstention_code == 'X2' and '默认零' in result.reason
    facts[0] = replace(facts[0], account='NP_PARENT_ORDINARY', value=Decimal('80'), label='归属于母公司普通股股东的净利润')
    result = derive(facts, 'EPS', '2024Y', basic_eps_definition=True)
    assert result.value == Decimal('1.6')


def test_bare_eps_and_missing_cutoff_do_not_grant_a_derived_financial_definition():
    assert derive(eps_facts(), 'EPS', '2024Y').abstention_code == 'X2'
    assert derive(eps_facts(), 'EPS', '2024Y', report_date=None, basic_eps_definition=True).abstention_code == 'X5'
    assert derive(quarter_facts(), report_date=None).abstention_code == 'X5'


@pytest.mark.parametrize('quarter', ['Q2', 'Q3', 'Q4'])
def test_quarter_judge_and_analysis_share_authorised_rule_without_inserting_fake_facts(quarter):
    facts = quarter_facts(quarter)
    store = FactStore(facts)
    label = {'Q2':'第二季度', 'Q3':'第三季度', 'Q4':'第四季度'}[quarter]
    claim = extract_mentions('甲公司2024年'+label+'合并营业收入30元。', '甲公司')[0]
    verdict = Judge(store, '2025-04-30').verify(claim)
    assert verdict.status == 'verified' and verdict.expected == '30' and verdict.rule_ids == ['FIN-single-quarter']
    row = next(row for row in analyze_financials(store, '甲公司', ['2024'+quarter], '2025-04-30')['metrics'] if row['metric']=='REVENUE')
    assert row['value']=='30' and row['formula']==verdict.formula and row['expected_interval']==verdict.expected_interval
    assert row['evidence']==verdict.evidence and store.facts==facts


@pytest.mark.parametrize('unit,value,expected', [('股','50','50'), ('万股','50','500000'), ('亿股','0.50','50000000')])
def test_pdf_weighted_shares_have_explicit_local_unit_and_real_page_evidence(tmp_path, unit, value, expected):
    fitz = pytest.importorskip('fitz')
    path = tmp_path/'2024年年度报告.pdf'
    document = fitz.open()
    page = document.new_page()
    text = ('合并利润表\n单位：万元\n2024年 2023年\n'
            '归属于母公司普通股股东的净利润 100.00 80.00\n'
            '发行在外普通股的加权平均数（'+unit+'） '+value+' '+value+'\n')
    for index, line in enumerate(text.splitlines()):
        page.insert_text((20, 40+index*35), line, fontname='china-s', fontsize=11)
    document.save(path)
    document.close()
    facts = extract_facts(path, '甲公司', '2024Y', '2025-04-01')
    weighted = next(row for row in facts if row.account=='WEIGHTED_SHARES' and row.period=='2024Y')
    assert weighted.value==Decimal(expected) and weighted.display_unit==unit
    assert all(identifier.startswith(sha256_file(path)[:12]+':p1:') for identifier in weighted.evidence)
    assert next(row for row in facts if row.account=='NP_PARENT_ORDINARY').value == Decimal('1000000.00')
    derived = derive(facts, 'EPS', '2024Y', basic_eps_definition=True)
    assert derived.value == Decimal('1000000')/Decimal(expected)


def test_weighted_shares_never_inherit_monetary_statement_unit(tmp_path):
    path = tmp_path/'report.md'
    path.write_text('合并利润表\n单位：万元\n2024年 2023年\n发行在外普通股的加权平均数 50 50\n', encoding='utf-8')
    assert not any(row.account=='WEIGHTED_SHARES' for row in extract_facts(path, '甲公司', '2024Y', '2025-04-01'))


def test_analysis_exposes_quarter_from_same_report_cumulative_pair_without_inventing_period_fact():
    facts = quarter_facts()
    store = FactStore(facts)
    report = analyze_financials(store, '甲公司', ['2024Q1', '2024H1'], '2025-04-30')
    assert report['requested_periods'] == ['2024Q1', '2024H1'] and report['derived_quarter_periods'] == ['2024Q2']
    quarter = next(row for row in report['metrics'] if row['metric']=='REVENUE' and row['period']=='2024Q2')
    assert quarter['value']=='30' and quarter['rule_id']=='FIN-single-quarter'
    assert not any(fact.period=='2024Q2' for fact in store.facts)


@pytest.mark.parametrize('row', ['营业收入（股） 50 50', '基本每股收益（元） 50股 50股',
                              '发行在外普通股的加权平均数（元） 50股 50股'])
def test_share_unit_support_does_not_allow_monetary_or_per_share_misclassification(tmp_path, row):
    path = tmp_path/'report.md'
    path.write_text('合并利润表\n单位：元\n2024年 2023年\n'+row+'\n', encoding='utf-8')
    assert extract_facts(path, '甲公司', '2024Y', '2025-04-01') == []


@pytest.mark.parametrize('problem', ['conflict', 'unknown_date', 'low_confidence', 'found'])
def test_unrelated_supplied_missing_cannot_override_actual_direct_lookup(problem):
    direct = fact('direct', 'REVENUE', '2024Q2', '999')
    facts = quarter_facts()
    if problem == 'conflict':
        facts += [direct, replace(direct, fact_id='direct-conflict', value=Decimal('888'))]
    else:
        changes = {'unknown_date': {'publish_date':''}, 'low_confidence': {'parse_conf':'0.7'}, 'found': {}}[problem]
        facts.append(replace(direct, **changes))
    result = derive(facts, direct_outcome=FactQueryOutcome('missing'))
    assert result.value is None and result.abstention_code == ('X2' if problem == 'found' else 'X4')


def test_ordinary_profit_and_total_parent_profit_remain_distinct_accounts_and_source_guards():
    from mingjing.source_semantics import freeze_source_dimensions, source_binding_issue, source_dimensions_from_text
    from mingjing.financial_tools import BIND_ACCOUNTS
    from mingjing.taxonomy import ACCOUNT_NAMES
    facts = eps_facts()
    facts[0] = replace(facts[0], value=Decimal('80'))
    facts.append(fact('total-parent', 'NP_PARENT', '2024Y', '100', label='归属于母公司股东的净利润'))
    store = FactStore(facts)
    report = analyze_financials(store, '甲公司', ['2024Y'], '2025-04-30')
    assert next(row for row in report['metrics'] if row['metric']=='NP_PARENT')['value'] == '100'
    eps = next(row for row in report['metrics'] if row['metric']=='EPS')
    assert eps['value']=='1.6' and eps['rule_metadata']['input_accounts'] == {
        'NP_PARENT':'NP_PARENT_ORDINARY', 'WEIGHTED_SHARES':'WEIGHTED_SHARES'}
    assert 'NP_PARENT_ORDINARY' in BIND_ACCOUNTS and '普通股股东' in ACCOUNT_NAMES['NP_PARENT_ORDINARY']
    claims = extract_mentions('甲公司2024年合并口径归属于母公司普通股股东的净利润80元。', '甲公司')
    claim = freeze_source_dimensions(claims[0])
    assert source_dimensions_from_text(claim)['account']=='NP_PARENT_ORDINARY'
    assert Judge(store, '2025-04-30').verify(claim).status == 'verified'
    assert source_binding_issue(replace(claim, account='NP_PARENT'), strict=True)[0] == 'X3'


@pytest.mark.parametrize('problem', ['conflict', 'unknown_date', 'low_confidence'])
def test_independent_ordinary_profit_failure_cannot_fall_through_to_legacy_parent_index(problem):
    facts = eps_facts()
    legacy = replace(facts[0], fact_id='legacy', account='NP_PARENT')
    ordinary = facts[0]
    if problem=='conflict':
        facts.append(replace(ordinary, fact_id='ordinary-conflict', value=Decimal('90')))
    else:
        facts[0] = replace(ordinary, **({'publish_date':''} if problem=='unknown_date' else {'parse_conf':'0.7'}))
    facts.append(legacy)
    out = derive(facts, 'EPS', '2024Y', basic_eps_definition=True)
    assert out.value is None and out.abstention_code=='X4'


def test_legacy_numerator_requires_the_explicit_ordinary_label():
    facts = eps_facts()
    facts[0] = replace(facts[0], account='NP_PARENT')
    assert derive(facts, 'EPS', '2024Y', basic_eps_definition=True).value == Decimal('2')
    facts[0] = replace(facts[0], label='归属于母公司股东的净利润')
    assert derive(facts, 'EPS', '2024Y', basic_eps_definition=True).abstention_code == 'X2'


@pytest.mark.parametrize('weighted_row,accepted', [
    ('发行在外普通股的加权平均数（万股） 50.00 40.00', True),
    ('发行在外普通股的加权平均数 50.00 40.00', False),
    ('发行在外普通股的加权平均数（元） 50股 40股', False),
])
def test_strict_pdf_window_shares_use_the_same_local_dimension_rules(tmp_path, weighted_row, accepted):
    fitz = pytest.importorskip('fitz')
    path = tmp_path/'report.pdf'
    with fitz.open() as document:
        page = document.new_page()
        for i, line in enumerate(['合并利润表', '单位：万元', '2024年 2023年',
                                 '归属于母公司普通股股东的净利润 80.00 70.00', weighted_row]):
            page.insert_text((20, 40+i*35), line, fontname='china-s', fontsize=11)
        document.save(path)
    window = extract_fact_window(path, pages=[1], company='甲公司', publish_date='2025-04-01',
                                 expected_sha256=sha256_file(path))
    weighted = [row for row in window['facts'] if row.account=='WEIGHTED_SHARES']
    assert bool(weighted)==accepted
    if accepted:
        assert weighted[0].value==Decimal('500000') and weighted[0].display_unit=='万股'
        assert derive(window['facts'], 'EPS', '2024Y', basic_eps_definition=True).value==Decimal('1.6')
        assert all(row.account!='NP_PARENT' for row in window['facts'])


@pytest.mark.parametrize('task_type', ['check', 'analysis'])
def test_cli_derived_values_and_added_quarter_catalog_replay_without_network(tmp_path, monkeypatch, task_type):
    import json
    import socket
    from mingjing.cli import build_parser, command_check
    from mingjing.replay import replay_run
    corpus = tmp_path/'corpus'
    corpus.mkdir()
    report = corpus/'report.md'
    report.write_text('已冻结的核查来源', encoding='utf-8')
    facts = quarter_facts()+eps_facts()
    facts = [replace(row, source_path=str(report), source_doc=sha256_file(report)[:12]) for row in facts]
    (corpus/'facts.json').write_text(json.dumps([row.to_dict() for row in facts], ensure_ascii=False), encoding='utf-8')
    draft = tmp_path/'draft.md'
    draft.write_text('甲公司2024年第二季度合并口径营业收入30元。甲公司2024年合并口径基本每股收益2.00元。', encoding='utf-8')
    out = tmp_path/'run'
    args = build_parser().parse_args(['check', '--draft', str(draft), '--corpus', str(corpus), '--company', '甲公司',
                                    '--report-date', '2025-04-30', '--task-type', task_type, '--out', str(out)])
    assert command_check(args)==0
    payload = json.loads((out/'results.json').read_text(encoding='utf-8'))
    assert payload['summary']=={'verified':2,'violated':0,'abstained':0}
    assert {row['rule_ids'][0] for row in payload['results']}=={'FIN-single-quarter','FIN-EPS'}
    if task_type=='analysis':
        assert payload['analysis']['derived_quarter_periods']==['2024Q2']
        assert any(row['metric']=='REVENUE' and row['period']=='2024Q2' and row['value']=='30'
                   for row in payload['analysis']['metrics'])
    monkeypatch.setattr(socket, 'create_connection', lambda *a, **k: pytest.fail('replay used network'))
    replay = replay_run(out)
    assert replay['status']=='passed' and replay['numeric_equal'], replay


def test_actual_derivation_code_and_constants_are_part_of_kernel_and_agent_locks(monkeypatch):
    from mingjing import financial_derivations as module
    from mingjing.audit import kernel_fingerprint
    from mingjing.agent import orchestration_fingerprint
    kernel = kernel_fingerprint()
    agent = orchestration_fingerprint()
    assert 'module:financial_derivations' in kernel
    assert 'financial_derivations' in agent['modules']
    monkeypatch.setattr(module, 'FLOW_ACCOUNTS', module.FLOW_ACCOUNTS | {'ALTERED'})
    assert kernel_fingerprint()['module:financial_derivations']!=kernel['module:financial_derivations']


def test_real_pdf_cumulative_columns_preserve_same_generation_for_single_quarter(tmp_path):
    fitz = pytest.importorskip('fitz')
    path = tmp_path/'report.pdf'
    with fitz.open() as document:
        page = document.new_page()
        for i, line in enumerate(['合并利润表', '单位：元', '2024年上半年 2024年第一季度',
                                 '营业收入 120.00 90.00']):
            page.insert_text((20, 40+i*35), line, fontname='china-s', fontsize=11)
        document.save(path)
    normal = extract_facts(path, '甲公司', '2024H1', '2025-04-01')
    window = extract_fact_window(path, pages=[1], company='甲公司', publish_date='2025-04-01',
                                 expected_sha256=sha256_file(path))
    for rows in (normal, window['facts']):
        assert {row.period for row in rows}=={'2024H1','2024Q1'}
        assert {row.version_group for row in rows}=={sha256_file(path)[:12]}
        assert derive(rows).value==Decimal('30')


def _nonoperating_bridge_facts():
    """Three statement rows required by the registered income bridge."""
    return [
        fact('profit-total', 'PROFIT_TOTAL', '2023Y', '100'),
        fact('operating-profit', 'OPERATING_PROFIT', '2023Y', '80'),
        fact('nonoperating-expense', 'NONOPERATING_EXPENSE', '2023Y', '5'),
    ]


def test_nonoperating_income_bridge_uses_authorised_rule_and_preserves_operands():
    """The fallback is deterministic and retains every source operand."""
    from mingjing.metrics import metric_spec
    from mingjing.financial_derivations import derive_registered_metric

    anchor = fact('anchor', 'REVENUE', '2023Y', '1')
    rows = _nonoperating_bridge_facts()
    result = derive_registered_metric(FactStore(rows), anchor.company,
                                      'NONOPERATING_INCOME', '2023Y', anchor.scope,
                                      anchor.nr, '2025-04-30')

    spec = metric_spec('NONOPERATING_INCOME')
    assert spec is not None
    assert spec.rule_id == 'FIN-nonoperating-income-bridge'
    assert spec.definition_required is False
    assert result.value == Decimal('25')
    assert result.abstention_code is None
    assert result.rule['id'] == spec.rule_id
    assert result.formula == 'PROFIT_TOTAL-OPERATING_PROFIT+NONOPERATING_EXPENSE'
    assert {row.account for row in result.operands} == {
        'PROFIT_TOTAL', 'OPERATING_PROFIT', 'NONOPERATING_EXPENSE'}
    assert len(result.calculation_steps) == 1
    step = result.calculation_steps[0]
    assert step['rule_id'] == spec.rule_id
    assert set(step['inputs']) == {'PROFIT_TOTAL', 'OPERATING_PROFIT', 'NONOPERATING_EXPENSE'}
    assert all(row.evidence for row in result.operands)
    assert result.evidence == list(dict.fromkeys(
        evidence for row in result.operands for evidence in row.evidence))


def test_nonoperating_income_direct_disclosure_wins_over_bridge():
    """A raw statement line is never overwritten by a coincident bridge."""
    from mingjing.financial_derivations import derive_registered_metric

    anchor = fact('anchor', 'REVENUE', '2023Y', '1')
    raw = fact('raw-income', 'NONOPERATING_INCOME', '2023Y', '26')
    rows = [raw, *_nonoperating_bridge_facts()]
    result = derive_registered_metric(FactStore(rows), anchor.company,
                                      'NONOPERATING_INCOME', '2023Y', anchor.scope,
                                      anchor.nr, '2025-04-30')
    # The caller must consume the direct FactStore result through the E1 path;
    # derive_registered_metric refuses to replace a disclosed line.
    assert result.value is None
    assert result.abstention_code == 'X2'
    assert result.operands == []
    assert result.evidence == list(raw.evidence)
    outcome = FactStore(rows).query_outcome(anchor.company, 'NONOPERATING_INCOME',
                                            '2023Y', anchor.scope, anchor.nr,
                                            cutoff='2025-04-30')
    assert outcome.status == 'found' and outcome.fact == raw


def test_nonoperating_income_bridge_never_defaults_missing_input_to_zero():
    from mingjing.financial_derivations import derive_registered_metric

    anchor = fact('anchor', 'REVENUE', '2023Y', '1')
    rows = _nonoperating_bridge_facts()[:-1]
    result = derive_registered_metric(FactStore(rows), anchor.company,
                                      'NONOPERATING_INCOME', '2023Y', anchor.scope,
                                      anchor.nr, '2025-04-30')
    assert result.value is None
    assert result.abstention_code == 'X1'
    assert any(item.get('account') == 'NONOPERATING_EXPENSE'
               and item.get('code') == 'X1' for item in result.blockers)


def test_nonoperating_income_bridge_rejects_cross_report_operands():
    from mingjing.financial_derivations import derive_registered_metric

    anchor = fact('anchor', 'REVENUE', '2023Y', '1')
    rows = _nonoperating_bridge_facts()
    rows[-1] = replace(rows[-1], source_doc='b' * 12, source_path='restated.pdf')
    result = derive_registered_metric(FactStore(rows), anchor.company,
                                      'NONOPERATING_INCOME', '2023Y', anchor.scope,
                                      anchor.nr, '2025-04-30')
    assert result.value is None
    assert result.abstention_code == 'X4'
    assert result.reason


def test_nonoperating_income_bridge_rejects_conflicting_input_facts():
    from mingjing.financial_derivations import derive_registered_metric

    anchor = fact('anchor', 'REVENUE', '2023Y', '1')
    rows = _nonoperating_bridge_facts()
    rows.append(replace(rows[0], fact_id='profit-total-conflict', value=Decimal('101')))
    result = derive_registered_metric(FactStore(rows), anchor.company,
                                      'NONOPERATING_INCOME', '2023Y', anchor.scope,
                                      anchor.nr, '2025-04-30')
    assert result.value is None
    assert result.abstention_code == 'X4'
    assert {row.account for row in result.operands} == {
        'OPERATING_PROFIT', 'NONOPERATING_EXPENSE'}
    assert any(item.get('account') == 'PROFIT_TOTAL'
               and item.get('code') == 'X4' for item in result.blockers)


def test_nonoperating_income_metric_gate_exposes_registered_rule_to_agent_tools():
    from mingjing.financial_tools import FinancialDispatcher
    from mingjing.models import AtomicClaim

    anchor = fact('anchor', 'REVENUE', '2023Y', '1')
    claim = AtomicClaim('nonoperating-claim', '营业外收入', 0, 5,
                        company=anchor.company, account='NONOPERATING_INCOME',
                        period='2023Y', candidate_value='25', unit=anchor.display_unit,
                        caliber_hint=(anchor.scope,))
    dispatcher = FinancialDispatcher('run', 'lock', [claim],
                                     _nonoperating_bridge_facts(), [], [], '2025-04-30')
    assert dispatcher._required_verification_rules(claim) == [
        'FIN-nonoperating-income-bridge']


@pytest.mark.parametrize(
    'account,definition,text,expected',
    [
        ('TURNOVER_ASSETS_UNSPECIFIED', 'metric:asset-turnover-average',
         '\u603b\u8d44\u4ea7\u5468\u8f6c\u7387=\u8425\u4e1a\u6536\u5165/\u5e73\u5747\u603b\u8d44\u4ea7',
         'ASSET_TURNOVER'),
        ('TURNOVER_INVENTORY_UNSPECIFIED', 'metric:inventory-turnover-average',
         '\u5b58\u8d27\u5468\u8f6c\u7387=\u8425\u4e1a\u6210\u672c/\u5e73\u5747\u5b58\u8d27',
         'INVENTORY_TURNOVER'),
        ('TURNOVER_AR_UNSPECIFIED', 'metric:ar-turnover-average',
         '\u5e94\u6536\u8d26\u6b3e\u5468\u8f6c\u7387=\u8425\u4e1a\u6536\u5165/\u5e73\u5747\u5e94\u6536\u8d26\u6b3e',
         'AR_TURNOVER'),
        ('TURNOVER_AP_UNSPECIFIED', 'metric:ap-turnover-average',
         '\u5e94\u4ed8\u8d26\u6b3e\u5468\u8f6c\u7387=\u8425\u4e1a\u6210\u672c/\u5e73\u5747\u5e94\u4ed8\u8d26\u6b3e',
         'AP_TURNOVER'),
    ])
def test_turnover_source_identity_requires_numerator_and_average_denominator(
        account, definition, text, expected):
    from mingjing.mentions import source_metric_identity

    assert source_metric_identity(account, text) == (expected, definition)
    wrong = text.replace('\u8425\u4e1a\u6536\u5165', '\u51c0\u5229\u6da6').replace(
        '\u8425\u4e1a\u6210\u672c', '\u51c0\u5229\u6da6')
    assert source_metric_identity(account, wrong) == (account, '')


@pytest.mark.parametrize(
    'account,definition,period_end,period_start,numerator',
    [('INVENTORY_TURNOVER', 'metric:inventory-turnover-average', '2023Y', '2022Y', 'COST'),
     ('AR_TURNOVER', 'metric:ar-turnover-average', '2023Y', '2022Y', 'REVENUE'),
     ('AP_TURNOVER', 'metric:ap-turnover-average', '2023Y', '2022Y', 'COST')])
def test_registered_average_balance_turnover_formulas_use_prior_year_source(
        account, definition, period_end, period_start, numerator):
    from mingjing.financial_derivations import derive_registered_metric

    anchor = fact('anchor', 'REVENUE', period_end, '1')
    balance = {'INVENTORY_TURNOVER': 'INVENTORY',
               'AR_TURNOVER': 'AR', 'AP_TURNOVER': 'AP'}[account]
    rows = [fact('num', numerator, period_end, '200'),
            fact('end', balance, period_end, '100'),
            fact('start', balance, period_start, '100')]
    result = derive_registered_metric(FactStore(rows), anchor.company, account,
                                      period_end, anchor.scope, anchor.nr,
                                      '2025-04-30', definition_id=definition)
    assert result.value == Decimal('2')
    assert result.rule['id'] == {'INVENTORY_TURNOVER': 'FIN-inventory-turnover',
                                 'AR_TURNOVER': 'FIN-ar-turnover',
                                 'AP_TURNOVER': 'FIN-ap-turnover'}[account]
    assert result.calculation_steps[0]['inputs'][balance + '_START']['value'] == '100'
