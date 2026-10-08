"""Parsing failures are executable X6 records, never invisible pending work."""
from mingjing.coverage import CoverageLedger, partition_document
from mingjing.facts import FactStore
from mingjing.judge import Judge


def build(tmp_path, source):
    draft = tmp_path / 'draft.md'
    draft.write_text(source, encoding='utf-8')
    text, pages, blocks = partition_document(draft)
    ledger, claims = CoverageLedger.build(text, pages, blocks, '甲公司', '2023Y')
    return text, ledger, claims


def test_unbound_numeric_cells_have_source_positions_and_kernel_x6(tmp_path):
    text, ledger, claims = build(tmp_path, '营业收入\n100 80 20%\n')
    failures = [c for c in claims if c.claim_id.startswith('unresolved-')]
    assert len(failures) == 3
    assert not any(e['status'] == 'pending' and not e['claim_id'] for e in ledger.entries)
    for claim in failures:
        assert claim.parse_status == 'unresolved'
        assert claim.account == 'UNKNOWN'
        assert claim.company == claim.period == claim.unit == ''
        assert claim.candidate_value is None and claim.caliber_hint == ()
        assert claim.numeric_text == text[claim.start:claim.end]
        offset = claim.source_numeric_start
        assert offset is not None
        assert claim.text[offset:offset + len(claim.numeric_text)] == claim.numeric_text
        result = Judge(FactStore([]), require_dual_binding=True).verify(claim)
        assert result.status == 'abstained' and result.abstention_code == 'X6'
        ledger.complete(claim.claim_id, result.to_dict())
    assert ledger.to_dict()['counts']['abstained'] == 3
    assert not ledger.to_dict()['partial']


def test_integer_chart_cells_near_financial_heading_are_not_assumed_pages(tmp_path):
    _, ledger, claims = build(tmp_path, '营业收入（万元）\n100\n80\n20\n')
    assert len(claims) == 3
    assert all(c.parse_status == 'unresolved' for c in claims)
    assert all(e['reason'] == 'source_addressed_parse_failure' for e in ledger.entries)


def test_trailing_newline_cannot_hide_page_ratio_and_toc(tmp_path):
    text, ledger, claims = build(tmp_path, '营业收入\n7 / 44 \n甲公司介绍\n'
                                           '表5： 营业收入 ................................ 15 \n')
    assert not claims
    ratios = [e for e in ledger.entries if e['reason'] == 'page_number_ratio']
    assert len(ratios) == 2
    assert text[ratios[0]['start']:ratios[0]['end']].strip() == '7'
    assert text[ratios[1]['start']:ratios[1]['end']].strip() == '44'
    assert any(e['reason'] == 'table_of_contents_entry' for e in ledger.entries)
    assert not ledger.to_dict()['partial']


def test_explicit_period_parts_are_excluded_without_deleting_same_line_amount(tmp_path):
    _, ledger, claims = build(tmp_path, '甲公司2023H1合并营业收入100元。\n'
                                        '2018-2023年营业收入CAGR为41%。\n'
                                        '合并营业收入2023亿元。')
    assert not any(c.numeric_text.strip() in {'2018', '-2023', '2023', '1'} for c in claims)
    assert any(c.numeric_text == '100元' for c in claims)
    assert any(c.numeric_text == '2023亿元' for c in claims)
    assert any(c.numeric_text == '41%' for c in claims)
    assert any(e['reason'] == 'declared_period' for e in ledger.entries)


def test_policy_target_table_keeps_source_rows_but_spends_no_binding_work(tmp_path):
    source = ('表9：国家规划加快数字基础设施建设\n指标\n2023年\n2024年\n2025年\n'
              '智能算力占比(%)\n25\n30\n35\n'
              '数据来源：《算力基础设施高质量发展行动计划》\n'
              '甲公司2023年合并营业收入100元。')
    _, ledger, claims = build(tmp_path, source)
    targets = [e for e in ledger.entries if e['text'].strip() in {'25', '30', '35'}]
    assert len(targets) == 3
    assert all(e['status'] == 'out_of_scope' and e['reason'] == 'policy_target_table' for e in targets)
    assert all(e['claim_id'] is None for e in targets)
    assert len(claims) == 1 and claims[0].numeric_text == '100元'


def test_generic_indicator_table_and_policy_words_do_not_hide_issuer_finances(tmp_path):
    _, ledger, claims = build(tmp_path, '表1：公司主要财务指标\n指标\n2023年\n'
                                        '营业收入（元）\n100\n'
                                        '资料来源：公司年报\n'
                                        '公司受益于国家规划，2023年合并营业收入200元。')
    assert not any(e['reason'] == 'policy_target_table' for e in ledger.entries)
    assert any(c.numeric_text.strip() == '100' for c in claims)
    assert any(c.numeric_text == '200元' for c in claims)


def test_policy_caption_alone_is_insufficient_to_mark_target_out_of_scope(tmp_path):
    _, ledger, claims = build(tmp_path, '表1：国家规划投资与公司营业收入\n'
                                        '100 80\n资料来源：公司年报\n')
    assert not any(e['reason'] == 'policy_target_table' for e in ledger.entries)
    assert len(claims) == 2 and all(c.parse_status == 'unresolved' for c in claims)
