"""Financial source regressions: independent columns and defined note operands."""
from __future__ import annotations
import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest
from mingjing.parser import _extract_table_facts, extract_facts
from mingjing.financial_notes import extract_note_facts
from mingjing.facts import FactStore
from mingjing.evidence import make_evidence_id

SOURCE = json.loads((Path(__file__).parent/'fixtures'/'financial_notes_source_14.json').read_text(encoding='utf-8'))


def notes(pages=None):
    return extract_note_facts(pages or SOURCE['pages'], path=Path('2024-04-15_2023年年度报告.pdf'),
        company='并行科技', effective_period='2023Y', doc_sha=SOURCE['document_sha256'],
        disclosure_date='2024-04-15', disclosure_version='原披露')


def test_original_segment_cells_do_not_pollute_company_revenue():
    store = FactStore(notes())
    total = store.query('并行科技', 'REVENUE', '2023Y', cutoff='2024-04-22')
    segment = store.query('并行科技', 'REVENUE', '2023Y', cutoff='2024-04-22',
                          dimension_kind='business', dimension_id='超算云业务')
    assert total.value == Decimal('495671818.88')
    assert segment.value == Decimal('414115382.02')
    previous = store.query('并行科技', 'COST', '2022Y', cutoff='2024-04-22',
                          dimension_kind='business', dimension_id='超算云系统集成')
    assert previous.value == Decimal('15671185.77')
    assert not store.query('并行科技', 'REVENUE', '2023Y', cutoff='2024-04-22',
                           dimension_kind='business', dimension_id='不存在的业务')


def test_basic_and_diluted_share_operands_keep_distinct_identities():
    store = FactStore(notes())
    for account in ('WEIGHTED_SHARES', 'WEIGHTED_SHARES_DILUTED'):
        value = store.query('并行科技', account, '2023Y', cutoff='2024-04-22')
        assert value.value == Decimal('48396667') and value.display_unit == '股'
    profit = store.query('并行科技', 'NP_PARENT_ORDINARY', '2023Y', cutoff='2024-04-22')
    assert profit.value == Decimal('-80499535.36')


def test_local_thousand_unit_and_foreign_currency_do_not_escape_into_next_note_table():
    pages=copy.deepcopy(SOURCE['pages'])
    segment=next(page for page in pages if page['page']==161)
    # A local note-table override has source geometry and stays with that table.
    y=next(block['bbox'][3] for block in segment['blocks'] if '营业收入和营业成本' in block['text'])+1
    segment['blocks'].append({'text':'单位：万元 币种：美元','bbox':[0,y,120,y+.5]})
    start=len(segment['text']); segment['text']+='单位：万元 币种：美元\n'
    segment['lines'].append({'text':'单位：万元 币种：美元','bbox':[0,y,120,y+.5],
                             'start':start,'end':start+len('单位：万元 币种：美元')})
    result=extract_note_facts(pages,path=Path('2024-04-15_2023年年度报告.pdf'),company='并行科技',
        effective_period='2023Y',doc_sha=SOURCE['document_sha256'],disclosure_date='2024-04-15',disclosure_version='原披露')
    segment_values=[fact for fact in result if fact.dimension_kind=='business' and fact.scope=='合并']
    assert segment_values and {fact.currency for fact in segment_values}=={'USD'}
    assert {fact.display_unit for fact in segment_values}=={'万元'}
    store=FactStore(result)
    assert store.query('并行科技','NP_PARENT_ORDINARY','2023Y',cutoff='2024-04-22').value==Decimal('-80499535.36')
    assert store.query('并行科技','CF_BRIDGE_DEPRECIATION','2023Y',cutoff='2024-04-22').value==Decimal('83390330.82')


def test_blank_bridge_cell_never_becomes_zero_or_drops_other_year():
    store = FactStore(notes())
    assert store.query('并行科技', 'CF_BRIDGE_DTL_CHANGE', '2023Y', cutoff='2024-04-22') is None
    previous = store.query('并行科技', 'CF_BRIDGE_DTL_CHANGE', '2022Y', cutoff='2024-04-22')
    assert previous.value == Decimal('-30305.00')
    depreciation = store.query('并行科技', 'CF_BRIDGE_DEPRECIATION', '2023Y', cutoff='2024-04-22')
    assert depreciation.value == Decimal('83390330.82')


def test_note_scope_and_common_unit_have_original_evidence():
    evidence_ids = {make_evidence_id(SOURCE['document_sha256'], p['page'], b['text'].strip(), b['bbox'])
                    for p in SOURCE['pages'] for b in p['blocks'] if b['text'].strip()}
    for fact in notes():
        assert set(fact.evidence) <= evidence_ids
        assert any(':p132:' in eid for eid in fact.evidence)
        assert any(':p99:' in eid for eid in fact.evidence)


def test_local_window_cannot_borrow_missing_note_scope_or_unit():
    # Same parser, but a local window lacks earlier declarations. It must
    # request evidence rather than inherit full-document/task defaults.
    assert notes([p for p in SOURCE['pages'] if p['page'] in {161,165,168}]) == []
    assert notes([p for p in SOURCE['pages'] if p['page'] != 132]) == []
    only_shares = notes([p for p in SOURCE['pages'] if p['page'] != 99])
    assert only_shares and all('WEIGHTED_SHARES' in fact.account for fact in only_shares)


def test_note_comparison_subject_header_blocks_all_note_modes():
    changed = copy.deepcopy(SOURCE['pages'])
    for page in changed:
        for block in page['blocks']:
            if '合并财务报表项目注释' in block['text']:
                block['text'] = block['text'].replace('合并财务报表项目注释', '可比公司甲合并财务报表项目注释')
    assert notes(changed) == []


@pytest.mark.parametrize('row,expected', [
    ('营业收入\t100.00\t—', [('2023Y', '100.00')]),
    ('营业收入\t—\t80.00', [('2022Y', '80.00')]),
    ('营业收入\t100.00\t-\t70.00', []),
])
def test_independent_mixed_columns_without_making_blanks_zero(tmp_path, row, expected):
    path = tmp_path/'2024-04-15_2023年年度报告.md'
    path.write_text('合并利润表\n单位：元\n2023年 2022年\n'+row, encoding='utf-8')
    facts = extract_facts(path, '公司甲', '2023Y')
    assert [(f.period, str(f.value)) for f in facts] == expected


def test_named_other_company_statement_does_not_use_issuer_directory(tmp_path):
    path = tmp_path/'839493_并行科技'/'report.md'
    path.parent.mkdir()
    path.write_text('可比公司甲合并利润表\n单位：元\n2023年 2022年\n营业收入 100 80', encoding='utf-8')
    assert extract_facts(path, '并行科技', '2023Y') == []
