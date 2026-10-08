"""Real source sections reach analysis, page evidence and offline replay."""
import json
from pathlib import Path

import pytest

from mingjing.analysis import analyze_financials, extract_accounting_events
from mingjing.cli import build_parser, command_check
from mingjing.coverage import partition_document
from mingjing.disclosures import prepare_accounting_disclosures
from mingjing.evidence import sha256_file
from mingjing.facts import FactStore
from mingjing.replay import replay_run


def section(tmp_path):
    source = tmp_path / 'annual.md'
    source.write_text('1、重要会计政策变更\n'
        '执行《企业会计准则解释第16号》关于单项交易的递延所得税不适用初始确认豁免的会计处理。\n'
        '本公司自2023年1月1日起执行该规定，按照已公布的企业会计准则进行追溯调整。\n'
        '2、重要会计估计变更\n本报告期公司主要会计估计未发生变更。', encoding='utf-8')
    _, _, blocks = partition_document(source)
    return source, blocks


def disclosures(source, blocks, **change):
    metadata = dict(company='甲公司', document_sha256=sha256_file(source),
                    publish_date='2024-04-15', period='2023Y', source_path=str(source))
    return prepare_accounting_disclosures(blocks, **(metadata | change))


def test_policy_exemption_negation_is_not_a_negative_event_and_estimate_is_not_inherited(tmp_path):
    source, blocks = section(tmp_path)
    prepared = disclosures(source, blocks)
    analysis = analyze_financials(FactStore([]), '甲公司', ['2023Y'], '2024-04-22', disclosures=prepared)
    events = analysis['events']
    assert len(events) == 1 and events[0]['kind'] == 'accounting_policy_change'
    assert '不适用初始确认豁免' in events[0]['source_text']
    assert '会计估计' not in events[0]['source_text']
    assert events[0]['status'] == 'disclosure_event_requires_professional_review'
    assert events[0]['period'] == '2023Y' and events[0]['evidence']
    assert all(row['status'] == 'abstained' for row in analysis['metrics'])


@pytest.mark.parametrize('text', ['重要会计政策变更', '本公司未发生会计政策变更。',
    '本公司会计政策变更：不适用。', '本公司会计政策未发生变更。',
    '重要会计政策变更：本公司本期未发生重大变更。', '本公司未发生重要的会计政策变更。'])
def test_heading_and_denied_changes_are_not_positive_events(text):
    row = dict(text=text, company='甲公司', document_sha256='a' * 64,
               publish_date='2024-04-15', evidence_id='citation')
    assert not extract_accounting_events([row], '甲公司', '2024-04-22')


def test_anonymous_company_and_unrelated_production_action_are_not_accounting_events(tmp_path):
    row = dict(text='本公司会计政策变更，自本期执行新准则。', company=None,
               document_sha256='a' * 64, publish_date='2024-04-15', evidence_id='citation')
    assert not extract_accounting_events([row], '甲公司', '2024-04-22')
    source = tmp_path / 'negative.md'
    source.write_text('1、重要会计政策变更\n□适用 √不适用\n'
        '本公司自本年起执行正常生产计划，按照相关规定调整具体生产安排。', encoding='utf-8')
    _, _, blocks = partition_document(source)
    prepared = disclosures(source, blocks)
    assert '不适用' in prepared[0]['text']
    assert not extract_accounting_events(prepared, '甲公司', '2024-04-22')


def test_exemption_not_applicable_after_event_label_remains_a_positive_event():
    row = dict(text='本公司会计政策变更：不适用初始确认豁免的规定，自2023年1月1日起执行。',
               company='甲公司', document_sha256='a' * 64, publish_date='2024-04-15', evidence_id='citation')
    assert len(extract_accounting_events([row], '甲公司', '2024-04-22')) == 1


@pytest.mark.parametrize('publish_date', [None, 123, '', 'unknown'])
def test_invalid_disclosure_date_is_skipped(publish_date):
    row = dict(text='本公司会计政策变更，自本期执行新准则。', company='甲公司',
               document_sha256='a' * 64, publish_date=publish_date, evidence_id='citation')
    assert not extract_accounting_events([row], '甲公司', '2024-04-22')


def test_unknown_future_and_other_company_disclosures_cannot_enter_dated_analysis(tmp_path):
    source, blocks = section(tmp_path)
    assert not disclosures(source, blocks, publish_date='')
    for rows in (disclosures(source, blocks, publish_date='2024-05-01'),
                 disclosures(source, blocks, company='乙公司')):
        assert not analyze_financials(FactStore([]), '甲公司', ['2023Y'], '2024-04-22', disclosures=rows)['events']
    assert not analyze_financials(FactStore([]), '甲公司', ['2023Y'], None,
                                 disclosures=disclosures(source, blocks))['events']


def test_repeated_same_authority_merges_citations_without_creating_a_second_event(tmp_path):
    source, blocks = section(tmp_path)
    rows = disclosures(source, blocks)
    repeated = dict(rows[0], evidence_id='additional-citation', evidence=['additional-citation'])
    events = extract_accounting_events([*rows, repeated], '甲公司', '2024-04-22')
    assert len(events) == 1 and events[0]['evidence'][-1] == 'additional-citation'


def test_kernel_cli_pdf_policy_section_exports_page_evidence_and_replays(tmp_path):
    fitz = pytest.importorskip('fitz')
    corpus = tmp_path / 'corpus'
    corpus.mkdir()
    pdf = corpus / 'annual.pdf'
    with fitz.open() as document:
        page = document.new_page()
        for index, text in enumerate(['1、重要会计政策变更',
            '执行《企业会计准则解释第16号》单项交易的递延所得税不适用初始确认豁免。',
            '本公司自2023年1月1日起执行该规定，按照企业会计准则进行追溯调整。',
            '2、重要会计估计变更', '本报告期公司主要会计估计未发生变更。']):
            page.insert_text((50, 70 + index * 40), text, fontname='china-s', fontsize=10)
        document.save(pdf)
    before = sha256_file(pdf)
    (corpus / 'facts.json').write_text(json.dumps([dict(fact_id='revenue', company='甲公司',
        account='REVENUE', period='2023Y', value='100', scope='合并',
        source_path='annual.pdf', publish_date='2024-04-15')], ensure_ascii=False), encoding='utf-8')
    intent = tmp_path / 'intent.md'
    intent.write_text('分析锁定财报材料。', encoding='utf-8')
    out = tmp_path / 'run'
    args = build_parser().parse_args(['check', '--draft', str(intent), '--corpus', str(corpus),
        '--company', '甲公司', '--report-date', '2024-04-22', '--task-type', 'analysis', '--out', str(out)])
    assert command_check(args) == 0
    data = json.loads((out / 'results.json').read_text(encoding='utf-8'))
    events = data['analysis']['events']
    assert len(events) == 1 and events[0]['kind'] == 'accounting_policy_change'
    assert data['accounting_disclosures'] and events[0]['evidence']
    assert all(identifier in data['evidence_resources'] for identifier in events[0]['evidence'])
    assert all((out / data['evidence_resources'][identifier]['image']).is_file() for identifier in events[0]['evidence'])
    assert replay_run(out)['status'] == 'passed'
    assert sha256_file(pdf) == before
