"""A supplemental value is independent of adjacent parts and currencies."""
from dataclasses import replace
from decimal import Decimal

from mingjing.reparse import _split_facts
from test_reparse_packets import source


def proposal(source):
    return replace(source['baseline_facts'][0], fact_id='part-new', account='REVENUE',
                   value=Decimal('100'), dimension_kind='business', dimension_id='超算云服务',
                   evidence=(source['source_resources'][0]['sha256'][:12]+':p3:0,0,1,1:abc',))


def metadata(source):
    return dict(company=source['company'], doc_sha256=source['source_resources'][0]['sha256'],
                publish_date='2025-03-01', as_of_date=source['as_of_date'])


def test_total_other_part_and_currency_do_not_conflict_with_new_part(source):
    new = proposal(source)
    previous = [replace(new, fact_id='total', dimension_kind='total', dimension_id='', value=Decimal('500')),
                replace(new, fact_id='other', dimension_id='超算云系统集成', value=Decimal('200')),
                replace(new, fact_id='usd', currency='USD', value=Decimal('30'))]
    accepted, conflicts = _split_facts([new], previous, metadata(source), [3])
    assert accepted == [new] and not conflicts
    assert all(old.fact_id != new.fact_id for old in previous)


def test_same_part_conflict_cannot_be_hidden_by_a_matching_company_total(source):
    new = proposal(source)
    previous = [replace(new, fact_id='old-part', value=Decimal('99')),
                replace(new, fact_id='total', dimension_kind='total', dimension_id='')]
    accepted, conflicts = _split_facts([new], previous, metadata(source), [3])
    assert not accepted and len(conflicts) == 1
    assert conflicts[0]['existing_fact_ids'] == ['old-part']
    assert conflicts[0]['abstention_code'] == 'X4'
