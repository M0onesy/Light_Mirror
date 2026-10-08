"""Unresolved source tables stay inspectable and never become financial facts."""
from copy import deepcopy
import json

import pytest

from mingjing.source_gaps import load_source_gaps, source_gap_summary


def catalogue(tmp_path, **changes):
    row = {'id': 'sg-'+'a'*24, 'company': '甲公司', 'doc_sha256': 'b'*64,
           'account': 'REVENUE', 'period': '2022Y', 'dimension_kind': 'customer',
           'dimension_id': '客户B', 'currency': 'CNY', 'gap_kind': 'scope_missing',
           'scope': '合并', 'reason': '原文未明确发行人合并/母公司口径', 'table_label': '主要客户收入',
           'raw_value': '999999', 'source_context': '原表待澄清',
           'evidence': ['b'*12+':p3:1,2,3,4:deadbeef']}
    row.update(changes)
    (tmp_path/'facts.json').write_text(json.dumps({'facts': [], 'source_gaps': [row]}), encoding='utf-8')
    resources = [{'id': 'report-'+'b'*20, 'sha256': 'b'*64, 'publish_date': '2023-10-16'}]
    return resources, row


def test_unresolved_amount_or_supplied_scope_is_not_a_fact_authority(tmp_path):
    resources, original = catalogue(tmp_path)
    rows = load_source_gaps([tmp_path], resources, '甲公司', '2024-04-22')
    assert rows[0]['scope'] == '' and rows[0]['eligible_for_verification'] is False
    assert 'raw_value' not in rows[0] and 'value' not in rows[0]
    assert rows[0]['public_at_cutoff'] is True
    assert rows[0]['report_id'] == resources[0]['id']
    assert source_gap_summary(rows)['eligible_for_verification'] is False


@pytest.mark.parametrize('published', ['', '2024-04-23'])
def test_unknown_or_late_disclosure_cannot_supply_cutoff_evidence(tmp_path, published):
    resources, _ = catalogue(tmp_path)
    resources[0]['publish_date'] = published
    rows = load_source_gaps([tmp_path], resources, '甲公司', '2024-04-22')
    assert not rows[0]['public_at_cutoff']


@pytest.mark.parametrize('change', [dict(id='../../escape'), dict(doc_sha256='c'*64),
                                  dict(evidence=['c'*12+':p3:1,2,3,4:deadbeef'])])
def test_unregistered_or_cross_document_gap_is_rejected(tmp_path, change):
    resources, _ = catalogue(tmp_path, **change)
    with pytest.raises(ValueError):
        load_source_gaps([tmp_path], resources, '甲公司', '2024-04-22')


def test_duplicate_catalogues_deduplicate_but_conflicting_id_is_rejected(tmp_path):
    resources, row = catalogue(tmp_path)
    assert len(load_source_gaps([tmp_path, tmp_path], resources, '甲公司', '2024-04-22')) == 1
    other = tmp_path/'other'
    other.mkdir()
    catalogue(other, reason='不同来源含义')
    with pytest.raises(ValueError, match='Conflicting'):
        load_source_gaps([tmp_path, other], resources, '甲公司', '2024-04-22')


def test_other_issuer_catalogue_is_not_relabelled_to_current_company(tmp_path):
    resources, _ = catalogue(tmp_path, company='乙公司')
    assert not load_source_gaps([tmp_path], resources, '甲公司', '2024-04-22')
