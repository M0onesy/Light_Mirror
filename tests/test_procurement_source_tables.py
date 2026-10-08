"""Exact source geometry for the disclosed external procurement/revenue grid."""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import re

import pytest

from mingjing.evidence import make_evidence_id
from mingjing.financial_notes import extract_note_facts, _extract_procurement_source_facts
from mingjing.source_gap_catalog import extract_source_gaps


SOURCE = next(item for item in json.loads(
    (Path(__file__).parent/'fixtures/source_gap_tables_14.json').read_text(encoding='utf-8'))
    if item['doc_sha256'].startswith('31e53f84d52e'))


def _facts(source=None, *, full=False):
    source = SOURCE if source is None else source
    extractor = extract_note_facts if full else _extract_procurement_source_facts
    return extractor(source['pages'], path=Path(source['filename']), company=source['company'],
        effective_period='2022Y', doc_sha=source['doc_sha256'],
        disclosure_date=source['publish_date'], disclosure_version='原披露')


def _replace(source, old, new):
    for page in source['pages']:
        page['text'] = page['text'].replace(old, new)
        for line in page['lines']:
            line['text'] = line['text'].replace(old, new)
        for block in page['blocks']:
            block['text'] = block['text'].replace(old, new)


def test_all_18_disclosed_cells_keep_distinct_measure_relation_and_source_identity():
    facts = _facts()
    assert len(facts) == 18 and len({fact.fact_id for fact in facts}) == 18
    assert {(fact.account, fact.dimension_kind, fact.dimension_id) for fact in facts} == {
        ('PURCHASE_AMOUNT', 'purchase_mode', '外采算力资源'),
        ('REVENUE', 'purchase_mode', '外采算力资源'),
        ('REVENUE', 'business', '超算云服务')}
    assert {(fact.period, fact.source_relation) for fact in facts} == {
        (period, relation) for period in ('2020Y', '2021Y', '2022Y') for relation in ('value', 'yoy')}
    assert all(fact.scope == '未知' and fact.source_row_index == 0 and not fact.source_scope_evidence
               for fact in facts)
    assert all(fact.definition_id == 'source:procurement-revenue-grid' for fact in facts)
    assert len({fact.source_table_id for fact in facts}) == 1
    assert all(fact.source_table_id.startswith('source-table-') for fact in facts)
    assert all(fact.display_unit == ('%' if fact.source_relation == 'yoy' else '万元') for fact in facts)
    lookup = {(fact.account, fact.dimension_kind, fact.period, fact.source_relation): fact for fact in facts}
    assert lookup['PURCHASE_AMOUNT', 'purchase_mode', '2020Y', 'value'].value == Decimal('31311200.00')
    assert lookup['PURCHASE_AMOUNT', 'purchase_mode', '2020Y', 'yoy'].value == Decimal('-.1557')
    assert lookup['REVENUE', 'purchase_mode', '2021Y', 'yoy'].value == Decimal('-.1056')
    assert lookup['REVENUE', 'business', '2022Y', 'value'].value == Decimal('267149800.00')


def test_full_note_parser_and_local_grid_parser_produce_identical_source_facts():
    assert [fact.to_dict() for fact in _facts(full=True)] == [fact.to_dict() for fact in _facts()]


def test_evidence_is_original_and_table_caption_excludes_following_paragraph():
    page = SOURCE['pages'][0]
    original = {make_evidence_id(SOURCE['doc_sha256'], page['page'], block['text'].strip(), block['bbox']): block['text']
                for block in page['blocks']}
    for fact in _facts():
        assert set(fact.evidence) <= original.keys()
        assert set(fact.source_table_evidence) <= original.keys()
        assert fact.evidence[0] not in fact.source_table_evidence
        assert fact.label in re.sub(r'\s+', '', ''.join(original[eid] for eid in fact.evidence))
        assert fact.table_label == '报告期内，发行人对外采购的CPU 及GPU 算力资源与超算云服务收入增长的匹配关系如下：'
        assert '每年' not in fact.table_label
        assert not any('每年对外采购' in original[eid] for eid in fact.source_table_evidence)


def test_catalog_keeps_legacy_measure_with_registered_canonical_identifier():
    cells = extract_source_gaps(SOURCE['pages'], path=SOURCE['filename'], company=SOURCE['company'],
        doc_sha=SOURCE['doc_sha256'], disclosure_date=SOURCE['publish_date'])
    assert len(cells) == 18
    purchases = [cell for cell in cells if cell['account'] == 'PROCUREMENT_AMOUNT']
    assert len(purchases) == 6 and all(cell['registered_account'] == 'PURCHASE_AMOUNT' for cell in purchases)
    assert all(cell['eligible_as_fact'] is False for cell in cells)
    assert all(cell['source_row_index'] == 0 for cell in cells)
    assert {cell['source_table_id'] for cell in cells} == {fact.source_table_id for fact in _facts()}


@pytest.mark.parametrize('old,new,remaining', [
    ('4,956.05', '—', 17),
    ('52.04%', '52.04', 17),
    ('-10.56%', '—', 17),
])
def test_missing_or_wrong_dimension_cells_do_not_drop_other_columns(old, new, remaining):
    source = deepcopy(SOURCE)
    _replace(source, old, new)
    facts = _facts(source)
    assert len(facts) == remaining
    if old == '4,956.05':
        assert any(f.account == 'PURCHASE_AMOUNT' and f.period == '2022Y' and f.source_relation == 'yoy'
                   for f in facts)
        assert not any(f.account == 'PURCHASE_AMOUNT' and f.period == '2022Y' and f.source_relation == 'value'
                       for f in facts)
    assert any(f.account == 'REVENUE' and f.dimension_kind == 'business' and f.period == '2022Y'
               and f.source_relation == 'value' for f in facts)


def test_each_period_amount_requires_its_own_explicit_unit():
    source = deepcopy(SOURCE)
    page = source['pages'][0]
    selected = next(line for line in page['lines'] if line['text'].strip() == '（万元）')
    selected['text'] = '（亿元） '
    containing = next(block for block in page['blocks'] if block['bbox'][0] <= selected['bbox'][0]
                      and block['bbox'][1] <= selected['bbox'][1] and block['bbox'][2] >= selected['bbox'][2]
                      and block['bbox'][3] >= selected['bbox'][3])
    containing['text'] = containing['text'].replace('（万元）', '（亿元）')
    facts = _facts(source)
    assert len(facts) == 15
    assert not any(f.period == '2022Y' and f.source_relation == 'value' for f in facts)
    assert sum(f.period == '2022Y' and f.source_relation == 'yoy' for f in facts) == 3


@pytest.mark.parametrize('old,new', [
    ('报告期内，发行人对外采购', '报告期内，可比公司甲对外采购'),
    ('长的匹配关系如下：', '长的匹配关系预测如下：'),
    ('2021 年度', '2022 年度'),
    ('外采算力资源产生', '共建算力资源产生'),
])
def test_unproven_subject_forecast_period_or_purchase_mode_does_not_unlock_grid(old, new):
    source = deepcopy(SOURCE)
    _replace(source, old, new)
    assert _facts(source) == []


def test_amount_outside_table_column_geometry_is_not_accepted():
    source = deepcopy(SOURCE)
    page = source['pages'][0]
    cell = next(line for line in page['lines'] if line['text'].strip() == '4,956.05')
    cell['bbox'] = [800, *cell['bbox'][1:2], 840, cell['bbox'][3]]
    facts = _facts(source)
    assert len(facts) == 17
    assert not any(f.account == 'PURCHASE_AMOUNT' and f.period == '2022Y' and f.source_relation == 'value'
                   for f in facts)


def test_adjacent_unlabelled_table_and_page_narrative_cannot_add_source_facts():
    source = deepcopy(SOURCE)
    page = source['pages'][0]
    additions = [('算力资源采购金额 ', [90, 500, 179, 511]),
                 ('999,999.00 ', [192, 500, 232, 512]),
                 ('99.99% ', [252, 500, 287, 512]),
                 ('2023 年度 ', [212, 480, 260, 492])]
    for text, bbox in additions:
        page['lines'].append({'text': text, 'bbox': bbox, 'start': len(page['text']), 'end': len(page['text'])+len(text)})
        page['blocks'].append({'text': text+'\n', 'bbox': bbox})
        page['text'] += text+'\n'
    assert [fact.to_dict() for fact in _facts(source)] == [fact.to_dict() for fact in _facts()]


def test_duplicate_source_cell_blocks_only_that_measure_and_period():
    source = deepcopy(SOURCE)
    page = source['pages'][0]
    cell = deepcopy(next(line for line in page['lines'] if line['text'].strip() == '4,956.05'))
    cell['text'] = '99.00 '
    page['lines'].append(cell)
    page['blocks'].append({'text': cell['text']+'\n', 'bbox': cell['bbox']})
    facts = _facts(source)
    assert len(facts) == 17
    assert not any(f.account == 'PURCHASE_AMOUNT' and f.period == '2022Y' and f.source_relation == 'value'
                   for f in facts)
    assert sum(f.account == 'PURCHASE_AMOUNT' and f.period == '2022Y' and f.source_relation == 'yoy'
               for f in facts) == 1
