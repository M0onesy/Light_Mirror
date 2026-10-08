"""Fallback fact indexes must obey the selected issuer before calculation."""
import json
from argparse import Namespace
from decimal import Decimal

import pytest

from mingjing.cli import command_check
from mingjing.models import Fact


def inputs(tmp_path, company):
    draft = tmp_path/'draft.md'
    draft.write_text('甲公司2024年合并营业收入100元。', encoding='utf-8')
    corpus = tmp_path/'empty'
    corpus.mkdir()
    facts = [Fact('a', '甲公司', 'REVENUE', '2024Y', value=Decimal('100'), publish_date='2025-03-01'),
             Fact('b', '乙公司', 'REVENUE', '2024Y', value=Decimal('999'), publish_date='2025-03-01')]
    index = tmp_path/'index.json'
    index.write_text(json.dumps([fact.to_dict() for fact in facts], ensure_ascii=False), encoding='utf-8')
    return Namespace(draft=str(draft), corpus=str(corpus), facts=str(index), out=str(tmp_path/'run'),
                     report_date='2025-03-31', company=company, period='2024Y', mode='kernel', run_id='company-boundary',
                     task_type='analysis')


def test_fallback_index_keeps_only_selected_company_in_facts_and_analysis(tmp_path):
    args = inputs(tmp_path, '甲公司')
    assert command_check(args) == 0
    data = json.loads((tmp_path/'run/results.json').read_text(encoding='utf-8'))
    assert {fact['company'] for fact in data['facts']} == {'甲公司'}
    assert data['summary']['verified'] == 1
    assert all(row['company'] == '甲公司' for row in data['analysis']['metrics'])


def test_unselected_mixed_issuer_index_is_rejected_before_any_judgement(tmp_path):
    args = inputs(tmp_path, '')
    with pytest.raises(ValueError, match='材料包含多家公司'):
        command_check(args)
    assert not (tmp_path/'run/results.json').exists()
