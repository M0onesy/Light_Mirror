"""Explicit EPS units must override neither their dimension nor the source."""
from decimal import Decimal
import json
from pathlib import Path

import pytest

from mingjing.evidence import sha256_file
from mingjing.parser import extract_facts, extract_fact_window, _growth_column_proofs, _extract_table_facts


def pdf(tmp_path, row, header='2024年 2023年'):
    import fitz
    path = tmp_path/'2024年年度报告.pdf'
    with fitz.open() as document:
        page = document.new_page()
        for i, text in enumerate(['合并利润表', '单位：元', header, row]):
            page.insert_text((20, 40+35*i), text, fontname='china-s', fontsize=11)
        document.save(path)
    return path


@pytest.mark.parametrize('label', ['基本每股收益', '稀释每股收益'])
@pytest.mark.parametrize('tail', [
    '（万元） 1.20 1.10', '（亿元） 1.20 1.10',
    '（万元/股） 1.20 1.10', '（百万元/股） 1.20 1.10',
    '（元） 1.20万元 1.10万元', '（元/股） 1.20亿元 1.10亿元',
    '（元） 1.20 万元 1.10 万元', '（元） 1.20\n万元\n1.10\n万元',
    '（元） 1.20元 1.10万元', '（股） 1.20 1.10',
    '（%） 1.20 1.10', ' 1.20% 1.10%',
    '（元） 1.20 1.10 万元', '（元） 1.20 1.10\n亿元',
    '（元） 1.20 1.10 股', '（元） 1.20 1.10 %',
    '（元） 1.20 1.10 万股', '（元） 1.20 1.10 ％',
])
def test_explicit_unsupported_eps_unit_cannot_produce_yuan_per_share_facts(tmp_path, label, tail):
    path = pdf(tmp_path, label+tail)
    normal = extract_facts(path, '甲公司', '2024Y', '2025-04-01')
    window = extract_fact_window(path, pages=[1], company='甲公司', publish_date='2025-04-01',
                                 expected_sha256=sha256_file(path))
    assert not any(f.account in {'EPS','EPS_DILUTED'} for f in normal)
    assert not any(f.account in {'EPS','EPS_DILUTED'} for f in window['facts'])


@pytest.mark.parametrize('label,account', [('基本每股收益','EPS'), ('稀释每股收益','EPS_DILUTED')])
@pytest.mark.parametrize('unit', ['元', '元/股'])
def test_supported_explicit_eps_units_keep_original_values_and_pdf_evidence(tmp_path, label, account, unit):
    path = pdf(tmp_path, label+'（'+unit+'） 1.20 1.10')
    for facts in (extract_facts(path, '甲公司', '2024Y', '2025-04-01'),
                  extract_fact_window(path, pages=[1], company='甲公司', publish_date='2025-04-01',
                                      expected_sha256=sha256_file(path))['facts']):
        fact = next(f for f in facts if f.account == account and f.period == '2024Y')
        assert fact.value == Decimal('1.20')
        assert all(e.startswith(sha256_file(path)[:12]+':p1:') for e in fact.evidence)


@pytest.mark.parametrize('label,account', [('基本每股收益','EPS'), ('稀释每股收益','EPS_DILUTED')])
@pytest.mark.parametrize('cell_unit', ['元', '元/股', ' 元', ' 元/股', ' 元 / 股'])
def test_supported_eps_cell_units_preserve_values_without_inheriting_statement_scale(tmp_path, label, account, cell_unit):
    path = pdf(tmp_path, label+' 1.20'+cell_unit+' 1.10'+cell_unit)
    for facts in (extract_facts(path, '甲公司', '2024Y', '2025-04-01'),
                  extract_fact_window(path, pages=[1], company='甲公司', publish_date='2025-04-01',
                                      expected_sha256=sha256_file(path))['facts']):
        fact = next(f for f in facts if f.account == account and f.period == '2024Y')
        assert fact.value == Decimal('1.20') and fact.display_unit == '元'
        assert fact.evidence


@pytest.mark.parametrize('label,account', [('基本每股收益','EPS'), ('稀释每股收益','EPS_DILUTED')])
def test_only_final_eps_cell_prints_yuan_unit_without_compact_number_ambiguity(tmp_path, label, account):
    path = pdf(tmp_path, label+' 1.20 1.10 元 / 股')
    for facts in (extract_facts(path, '甲公司', '2024Y', '2025-04-01'),
                  extract_fact_window(path, pages=[1], company='甲公司', publish_date='2025-04-01',
                                      expected_sha256=sha256_file(path))['facts']):
        assert [(f.period, f.value) for f in facts if f.account == account] == [
            ('2024Y', Decimal('1.20')), ('2023Y', Decimal('1.10'))]


@pytest.mark.parametrize('label', ['基本每股收益','稀释每股收益'])
@pytest.mark.parametrize('qualifier', ['扣非后', '扣除非经常性损益', '最新摊薄'])
def test_other_eps_definitions_are_not_silently_promoted_to_unadjusted_statutory_eps(tmp_path, label, qualifier):
    path = pdf(tmp_path, label+'（'+qualifier+'）（元） 1.20 1.10')
    for facts in (extract_facts(path, '甲公司', '2024Y', '2025-04-01'),
                  extract_fact_window(path, pages=[1], company='甲公司', publish_date='2025-04-01',
                                      expected_sha256=sha256_file(path))['facts']):
        assert not any(f.account in {'EPS','EPS_DILUTED'} for f in facts)


@pytest.mark.parametrize('label,account', [('基本每股收益','EPS'), ('稀释每股收益','EPS_DILUTED')])
@pytest.mark.parametrize('percent', ['-31.97%', '-31.97 %', '-31.97\n％'])
def test_explicit_growth_column_is_not_an_eps_unit_and_never_becomes_an_eps_value(tmp_path, label, account, percent):
    path = pdf(tmp_path, label+' -1.66 -2.44 '+percent+' -2.12',
               header='2024年 2023年 本年比上年增减% 2022年')
    for facts in (extract_facts(path, '甲公司', '2024Y', '2025-04-01'),
                  extract_fact_window(path, pages=[1], company='甲公司', publish_date='2025-04-01',
                                      expected_sha256=sha256_file(path))['facts']):
        assert [(f.period, f.value) for f in facts if f.account == account] == [
            ('2024Y', Decimal('-1.66')), ('2023Y', Decimal('-2.44')), ('2022Y', Decimal('-2.12'))]


def test_an_extra_percentage_cannot_be_inferred_to_be_a_growth_column(tmp_path):
    path = pdf(tmp_path, '基本每股收益（元） 1.20 20% 1.10')
    assert not any(f.account == 'EPS' for f in extract_facts(path, '甲公司', '2024Y', '2025-04-01'))


def test_wrapped_centered_growth_caption_proof_keeps_both_original_blocks():
    first = {'text': '2024年\n本年比上年增', 'bbox': [20, 10, 150, 22], 'geometry_lines': [
        {'text':'2024年','bbox':[20,18,60,28]},
        {'text':'本年比上年增','bbox':[100,10,160,20]}]}
    second = {'text':'减%\n2023年', 'bbox':[120,26,250,36], 'geometry_lines':[
        {'text':'减%','bbox':[120,26,140,36]},
        {'text':'2023年','bbox':[200,18,240,28]}]}
    proofs = _growth_column_proofs([first,second],first)
    assert [p['text'] for p in proofs] == ['本年比上年增','减%']
    assert [p['evidence_text'] for p in proofs] == [first['text'],second['text']]
    shifted = {**second, 'geometry_lines':[{'text':'减%','bbox':[220,26,240,36]}]}
    assert not _growth_column_proofs([first,shifted],first)


def test_exposed_original_annual_eps_row_survives_wrapped_growth_column(tmp_path):
    original = json.loads((Path(__file__).parent/'fixtures/eps_growth_source_13.json').read_text(encoding='utf-8'))
    # Original text/geometry regression; this is not independent human gold.
    facts = _extract_table_facts(original['pages'], path=tmp_path/'2023年年度报告.pdf',
        company=original['company'], effective_period='2023Y', doc_sha=original['doc_sha256'],
        disclosure_date='2024-04-15', disclosure_version='原披露')
    eps = [f for f in facts if f.account == 'EPS']
    assert [(f.period, f.value, f.display_unit) for f in eps] == [
        ('2023Y',Decimal('-1.66'),'元'), ('2022Y',Decimal('-2.44'),'元'), ('2021Y',Decimal('-2.12'),'元')]
    assert all('595ba27f8900:p8:62,373,537,385:db00adaf' in f.evidence for f in eps)
    # The amount row alone does not establish the period or table identity.
    # Preserve the original header/scope evidence alongside every source cell.
    assert all(len(f.evidence) >= 3 for f in eps)
