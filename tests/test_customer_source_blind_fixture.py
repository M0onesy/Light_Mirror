"""Keep exposed table provenance reusable without leaking financial candidates."""
from __future__ import annotations

import json
from pathlib import Path
import re


FIXTURE = Path(__file__).parent / 'fixtures/customer_table_reference_blind_16.json'


def _nodes(value):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from _nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from _nodes(child)


def _load():
    return json.loads(FIXTURE.read_text(encoding='utf-8'))


def test_blind_source_fixture_contains_no_financial_candidates_or_results():
    data = _load()
    forbidden = {'candidate_value', 'expected_value', 'correct_value', 'amount',
                 'judgment', 'verification_status', 'value', 'facts'}
    assert data['financial_values_included'] is False
    for node in _nodes(data):
        if isinstance(node, dict):
            assert not (forbidden & node.keys())
            if 'text' in node:
                text = node['text']
                assert not re.search(r'\d+(?:\.\d+)?\s*[%％]', text)
                assert not re.search(r'\d+[.,]\d+', text)
    assert data['caption']['financial_values_withheld'] is True
    assert '[financial value withheld]' in data['caption']['text']


def test_customer_labels_are_backed_by_customer_column_spans():
    data = _load()
    lower, upper = data['continuation_evidence']['same_customer_column_x_range']
    for segment in data['segments']:
        for row in segment['rows']:
            spans = row['source_spans']
            assert spans
            assert ''.join(re.sub(r'\s+', '', s['text']) for s in spans) == row['customer']
            for span in spans:
                x0, y0, x1, y1 = span['bbox']
                assert lower <= x0 < x1 <= upper
                assert 0 <= y0 < y1
                assert span['page'] == segment['page']
                assert not re.search(r'\d', span['text'])


def test_family_citation_does_not_forge_exact_version_or_accounting_scope():
    data = _load()
    reference = data['source_reference']
    assert reference['family'] == 'prospectus'
    assert '公司招股书' in data['citation']['text']
    assert reference['exact_source_doc_sha256'] is None
    assert reference['exact_source_publish_date'] is None
    assert not any('scope' in node for node in _nodes(data) if isinstance(node, dict))
    assert reference['anonymous_customer_identity'].startswith('requires_locked_same_source_table')


def test_cross_page_table_boundaries_keep_periods_and_footer_together():
    data = _load()
    assert [s['page'] for s in data['segments']] == [13, 14, 14]
    assert [s['period'] for s in data['segments']] == ['2023H1', '2022Y', '2021Y']
    assert data['caption']['page'] == data['headers']['page'] == 13
    assert data['caption']['bbox'][3] < data['headers']['bbox'][1]
    assert data['citation']['page'] == 14
    for segment in data['segments']:
        assert [row['ordinal'] for row in segment['rows']] == [1, 2, 3, 4, 5]
        if segment['page'] == 14:
            assert all(span['bbox'][3] < data['citation']['bbox'][1]
                       for row in segment['rows'] for span in row['source_spans'])

