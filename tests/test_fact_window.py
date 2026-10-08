"""Original-page fact proposals must fail closed without local evidence."""
import hashlib
import sys
import threading
import time
from decimal import Decimal

import fitz
import pytest

from mingjing import parser
from mingjing.evidence import make_evidence_id, sha256_file


TABLE = ['合并利润表', '单位：万元', '项目\n2023年\n2022年', '营业收入\n100.00\n80.00']


def pdf(tmp_path, pages, name='report.pdf', directory=None):
    folder = tmp_path / directory if directory else tmp_path
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    with fitz.open() as doc:
        for rows in pages:
            page = doc.new_page(width=600, height=900)
            y = 40
            for row in rows:
                page.insert_text((40, y), row, fontname='china-s', fontsize=11)
                y += len(row.splitlines()) * 16 + 18
        doc.save(path)
    return path


def window(path, pages=(1,), **kwargs):
    options = dict(company='公司甲', publish_date='2024-04-15', expected_sha256=sha256_file(path))
    options.update(kwargs)
    return parser.extract_fact_window(path, pages=pages, **options)


def issues(output):
    return {issue['code'] for issue in output['issues']}


def original_evidence(path):
    digest = sha256_file(path)
    with fitz.open(path) as doc:
        return {
            make_evidence_id(digest, number, block[4].strip(), block[:4]): (number, block[4])
            for number, page in enumerate(doc, 1)
            for block in page.get_text('blocks')
            if block[4].strip() and block[6] == 0
        }


def test_window_uses_original_pages_and_dimension_proofs_without_saving_pdf(tmp_path, monkeypatch):
    path = pdf(tmp_path, [['不应读取的第一页'], TABLE[:3], TABLE[3:], ['不应读取的第四页']])
    before = path.read_bytes()
    read_pages = []
    get_text = fitz.Page.get_text
    def tracked(page, *args, **kwargs):
        read_pages.append(page.number + 1)
        return get_text(page, *args, **kwargs)
    monkeypatch.setattr(fitz.Page, 'get_text', tracked)
    result = window(path, pages=[3, 2], report_period='2099H1')
    assert set(read_pages) == {2, 3}
    assert path.read_bytes() == before
    assert not result['issues']
    assert [(f.period, f.value, f.scope) for f in result['facts']] == [
        ('2023Y', Decimal('1000000.00'), '合并'), ('2022Y', Decimal('800000.00'), '合并')]
    with fitz.open(path) as doc:
        assert result['source_pages'] == [
            {'page': n, 'text_sha256': hashlib.sha256(doc[n-1].get_text().encode()).hexdigest(), 'text_layer': True}
            for n in (2, 3)]
    monkeypatch.setattr(fitz.Page, 'get_text', get_text)
    evidence = original_evidence(path)
    for fact in result['facts']:
        assert fact.extractor_version == 'financial-window-v1'
        assert fact.source_path == str(path.resolve())
        assert '-p3-w' in fact.fact_id and fact.source_doc == sha256_file(path)[:12]
        assert all(identity in evidence for identity in fact.evidence)
        assert {evidence[identity][0] for identity in fact.evidence} == {2, 3}
        cited = ''.join(evidence[identity][1] for identity in fact.evidence)
        assert all(text in cited for text in ['合并利润表', '单位：万元', '2023年', '2022年', '营业收入'])
    assert window(path, pages=[2, 3])['facts'] == result['facts']


@pytest.mark.parametrize('pages', [[], [0], [-1], [True], ['1'], [1, 3], [1, 1], [1, 2, 3, 4]])
def test_invalid_windows_are_rejected_before_pdf_open(tmp_path, monkeypatch, pages):
    path = pdf(tmp_path, [TABLE, TABLE, TABLE, TABLE])
    monkeypatch.setattr(fitz, 'open', lambda *a, **k: pytest.fail('invalid window opened PDF'))
    with pytest.raises(ValueError, match='页|窗口'):
        window(path, pages=pages)


def test_window_bound_is_checked_without_consuming_an_unbounded_iterable(tmp_path):
    path = pdf(tmp_path, [TABLE])
    def unbounded():
        n = 0
        while True:
            n += 1
            if n > 4:
                pytest.fail('unbounded page iterator was consumed')
            yield n
    with pytest.raises(ValueError, match='三张'):
        window(path, pages=unbounded())


def test_page_range_cannot_exceed_locked_document(tmp_path):
    path = pdf(tmp_path, [TABLE])
    with pytest.raises(ValueError, match='超出'):
        window(path, pages=[1, 2])


@pytest.mark.parametrize('digest', ['', 'abc', 'g' * 64, '0' * 64])
def test_full_hash_is_required_and_verified_before_pdf_open(tmp_path, monkeypatch, digest):
    path = pdf(tmp_path, [TABLE])
    monkeypatch.setattr(fitz, 'open', lambda *a, **k: pytest.fail('unlocked PDF opened'))
    with pytest.raises(ValueError, match='SHA-256'):
        window(path, expected_sha256=digest)


@pytest.mark.parametrize('value', ['', 'unknown', '2024-02-30', '2024-1-1', '2024-04-15T00:00:00Z', None])
def test_unknown_publication_date_cannot_be_recovered_from_filename(tmp_path, value):
    path = pdf(tmp_path, [TABLE], name='2024-04-15_2023年年度报告.pdf')
    with pytest.raises(ValueError, match='披露日期'):
        window(path, publish_date=value)


def test_trusted_disclosure_date_is_preserved_without_filename_override(tmp_path):
    path = pdf(tmp_path, [TABLE], name='2024-04-14_2023年年度报告.pdf')
    assert {f.publish_date for f in window(path)['facts']} == {'2024-04-15'}


@pytest.mark.parametrize('company', ['', '公司乙', '公司'])
def test_stock_company_directory_cannot_be_relabelled(tmp_path, company):
    path = pdf(tmp_path, [TABLE], directory='920001_公司甲')
    with pytest.raises(ValueError, match='公司|改名'):
        window(path, company=company)


@pytest.mark.parametrize('declaration', ['编制单位：公司乙', '报告主体：公司乙股份有限公司', '公司乙合并利润表'])
def test_explicit_foreign_issuer_cannot_be_relabelled_even_with_host_hint(tmp_path, declaration):
    path = pdf(tmp_path, [[declaration] + TABLE])
    with pytest.raises(ValueError, match='不一致|改名'):
        window(path)


def test_exact_issuer_corporate_suffix_is_not_a_false_conflict(tmp_path):
    path = pdf(tmp_path, [['编制单位：公司甲股份有限公司'] + TABLE])
    assert {f.company for f in window(path)['facts']} == {'公司甲'}


@pytest.mark.parametrize('rows,code', [
    (['合并利润表', '2023年\n2022年', '营业收入\n100\n80'], 'unit_unproven'),
    (['利润表', '单位：元', '2023年\n2022年', '营业收入\n100\n80'], 'scope_unproven'),
    (['合并及母公司利润表', '单位：元', '2023年\n2022年', '营业收入\n100\n80'], 'scope_unproven'),
    (['主要会计数据', '单位：元', '2023年\n2022年', '营业收入\n100\n80'], 'scope_unproven'),
    (['合并利润表', '单位：元', '本期\n上年同期', '营业收入\n100\n80'], 'period_unproven'),
    (['合并利润表', '单位：元', '2023\n2022', '营业收入\n100\n80'], 'period_unproven'),
    (['合并利润表', '单位：元', '2023年\n2023年', '营业收入\n100\n80'], 'period_unproven'),
    (['合并利润表', '单位：元', '2023年\n2022年', '营业收入\n100\n80\n60'], 'unsupported_layout'),
])
def test_missing_or_ambiguous_dimensions_do_not_create_facts(tmp_path, rows, code):
    path = pdf(tmp_path, [rows], name='2024-04-15_2023年年度报告.pdf')
    result = window(path, report_period='2023Y')
    assert result['facts'] == []
    assert code in issues(result)


def test_row_local_unit_requires_its_own_source_not_an_unrelated_block(tmp_path):
    path = pdf(tmp_path, [['单位：亿元', '合并利润表', '2023年\n2022年',
                           '营业收入（万元）\n100\n80', '营业成本\n50\n40']])
    result = window(path)
    assert {f.account for f in result['facts']} == {'REVENUE'}
    assert {f.value for f in result['facts']} == {Decimal('1000000'), Decimal('800000')}
    assert 'unit_unproven' in issues(result)


def test_scope_change_resets_units_and_columns(tmp_path):
    path = pdf(tmp_path, [TABLE, ['母公司利润表', '2023年\n2022年', '营业收入\n3\n2']])
    result = window(path, pages=[1, 2])
    assert len(result['facts']) == 2 and {f.scope for f in result['facts']} == {'合并'}
    assert 'unit_unproven' in issues(result)


def test_previous_physical_page_outside_window_cannot_donate_context(tmp_path):
    path = pdf(tmp_path, [TABLE[:3], TABLE[3:]])
    assert window(path, pages=[2], report_period='2023Y')['facts'] == []
    assert len(window(path, pages=[1, 2])['facts']) == 2


@pytest.mark.parametrize('header,periods', [
    ('2023年06月30日\n2022年12月31日', {'2023H1', '2022Y'}),
    ('2023年1-6月\n2022年1-6月', {'2023H1', '2022H1'}),
    ('2023年1-9月\n2022年1-9月', {'20239M', '20229M'}),
])
def test_explicit_interim_columns_are_not_rewritten_by_request_period(tmp_path, header, periods):
    path = pdf(tmp_path, [['合并资产负债表', '单位：元', header, '资产总计\n100\n80']])
    result = window(path, report_period='2099Y')
    assert {f.period for f in result['facts']} == periods


def test_source_report_caption_authorizes_relative_columns_and_is_cited(tmp_path):
    path = pdf(tmp_path, [['2023年半年度报告', '合并利润表', '单位：元', '本期\n上年同期', '营业收入\n100\n80']])
    result = window(path, report_period='2099Y')
    assert {f.period for f in result['facts']} == {'2023H1', '2022H1'}
    original = original_evidence(path)
    assert all(any('2023年半年度报告' in original[i][1] for i in f.evidence) for f in result['facts'])


def test_unknown_date_caption_cannot_reuse_previous_year_columns(tmp_path):
    path = pdf(tmp_path, [['合并利润表', '单位：元', '2023年\n2022年',
                           '2023年4月15日\n2022年4月15日', '营业收入\n100\n80']])
    result = window(path)
    assert not result['facts'] and 'period_unproven' in issues(result)


def test_forecast_columns_are_not_promoted_to_disclosures(tmp_path):
    path = pdf(tmp_path, [['合并利润表', '单位：元', '2023A\n2024E', '营业收入\n100\n200']])
    assert [(f.period, f.value) for f in window(path)['facts']] == [('2023Y', Decimal('100'))]


def test_eps_does_not_guess_yuan_share_from_monetary_wanyuan_header(tmp_path):
    path = pdf(tmp_path, [['合并利润表', '单位：万元', '2023年\n2022年',
                           '基本每股收益\n1.20\n1.00']])
    result = window(path)
    assert not result['facts'] and 'unit_unproven' in issues(result)


def test_blank_scan_neither_uses_image_description_nor_triggers_ocr(tmp_path, monkeypatch):
    path = pdf(tmp_path, [[]])
    monkeypatch.setattr(parser, '_pdftotext', lambda *a, **k: pytest.fail('window invoked external converter'))
    result = window(path)
    assert result['facts'] == []
    assert result['source_pages'][0]['text_layer'] is False
    assert 'no_text_layer' in issues(result)


def test_blank_page_breaks_continuation_context(tmp_path):
    path = pdf(tmp_path, [TABLE[:3], [], TABLE[3:]])
    result = window(path, pages=[1, 2, 3])
    assert not result['facts'] and 'no_text_layer' in issues(result)


def test_missing_pymupdf_is_visible_without_uncontrolled_fallback(tmp_path, monkeypatch):
    path = pdf(tmp_path, [TABLE])
    monkeypatch.setitem(sys.modules, 'fitz', None)
    monkeypatch.setattr(parser, '_pdftotext', lambda *a, **k: pytest.fail('uncontrolled fallback'))
    result = window(path)
    assert not result['facts'] and 'parser_unavailable' in issues(result)


def test_cancel_and_deadline_propagate_before_any_material_read(tmp_path):
    stopped = threading.Event()
    stopped.set()
    with pytest.raises(InterruptedError, match='cancelled'):
        parser.extract_fact_window(tmp_path/'missing.pdf', pages=[1], company='公司甲',
                                   publish_date='2024-04-15', expected_sha256='0'*64, cancel_event=stopped)
    with pytest.raises(TimeoutError, match='deadline'):
        parser.extract_fact_window(tmp_path/'missing.pdf', pages=[1], company='公司甲',
                                   publish_date='2024-04-15', expected_sha256='0'*64, deadline_monotonic=time.monotonic()-1)


def test_cancel_after_progress_does_not_read_page_or_fallback(tmp_path, monkeypatch):
    path = pdf(tmp_path, [TABLE])
    stopped = threading.Event()
    def stop(phase, **fields):
        if phase == '解析局部PDF页面':
            stopped.set()
    monkeypatch.setattr(fitz.Page, 'get_text', lambda *a, **k: pytest.fail('cancelled window read page'))
    monkeypatch.setattr(parser, '_pdftotext', lambda *a, **k: pytest.fail('cancel became fallback'))
    with pytest.raises(InterruptedError):
        window(path, cancel_event=stopped, progress_callback=stop)


def test_file_changed_during_parse_invalidates_proposal(tmp_path):
    path = pdf(tmp_path, [TABLE])
    def mutate(phase, **fields):
        if phase == '校验局部事实提案':
            path.write_bytes(path.read_bytes() + b'\nexternal-writer-change')
    with pytest.raises(ValueError, match='已变更'):
        window(path, progress_callback=mutate)


def test_local_fact_conflicts_are_visible_and_withheld_instead_of_choosing_close_amount(tmp_path):
    path = pdf(tmp_path, [TABLE + ['营业收入\n101.00\n81.00', '营业成本\n50.00\n40.00']])
    result = window(path)
    assert {f.account for f in result['facts']} == {'COST'}
    conflicts = [row for row in result['issues'] if row['code'] == 'fact_conflict']
    assert {row['period'] for row in conflicts} == {'2023Y', '2022Y'}
    assert all(row['company'] == '公司甲' and row['scope'] == '合并' and row['account'] == 'REVENUE' for row in conflicts)


def test_conflicting_cell_and_header_units_are_not_silently_selected(tmp_path):
    path = pdf(tmp_path, [['合并利润表', '单位：万元', '2023年\n2022年', '营业收入\n100元\n80元']])
    result = window(path)
    assert not result['facts'] and 'unit_conflict' in issues(result)
