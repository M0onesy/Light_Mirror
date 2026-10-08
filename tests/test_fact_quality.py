from decimal import Decimal

import pytest

from mingjing.facts import FactStore, fact_from_dict
from mingjing.models import Fact
from mingjing import parser


def _pages(rows_by_page):
    pages = []
    for page, rows in enumerate(rows_by_page, 1):
        blocks = [{"bbox": [10.0, float(i * 30), 500.0, float(i * 30 + 20)], "text": row}
                  for i, row in enumerate(rows)]
        pages.append({"page": page, "text": "\n".join(rows), "blocks": blocks})
    return pages


def _extract(tmp_path, monkeypatch, pages, name="2024-04-15_2023年年度报告.pdf"):
    path = tmp_path / name
    path.write_bytes(b"fixture-pdf-hash-input")
    monkeypatch.setattr(parser, "extract_text", lambda _: pages)
    return path, parser.extract_facts(path, "公司甲", "2025Y", "2025-04-20")


def test_annual_columns_scope_and_growth_rows_are_not_guessed(tmp_path, monkeypatch):
    pages = _pages([
        ["第三节 会计数据和财务指标", "单位：万元", "2023年\n2022年\n本年比上年增减%\n2021年",
         "营业收入\n120.00\n100.00\n20.00%\n80.00",
         "营业收入增长率%\n20.00%\n25.00%\n-\n30.00%",
         "归属于上市公司股东的扣除非\n经常性损益后的净利润\n-8.00\n-10.00\n-20.00%\n-5.00"],
        ["（三）合并利润表", "单位：元", "项目\n2023年\n2022年", "其中：营业收入\n1,200,000.00\n1,000,000.00",
         "其中：营业成本\n600,000.00\n500,000.00"],
        ["（四）母公司利润表", "单位：元", "项目\n2023年\n2022年", "一、营业收入\n400,000.00\n300,000.00"],
        ["财务报表附注", "营业收入\n2023\n2022", "超算云业务\n999,999.00\n888,888.00"],
    ])
    path, facts = _extract(tmp_path, monkeypatch, pages)
    store = FactStore(facts)
    assert store.query("公司甲", "REVENUE", "2023Y").value == Decimal("1200000.00")
    assert store.query("公司甲", "REVENUE", "2022Y").value == Decimal("1000000.00")
    assert store.query("公司甲", "REVENUE", "2023Y", scope="母公司").value == Decimal("400000.00")
    assert store.query("公司甲", "NP_PARENT_ADJ", "2023Y", nr="扣非").value == Decimal("-80000.00")
    assert not any(f.value == Decimal("2023") or f.period == "2025Y" for f in facts)
    assert all(f.publish_date == "2024-04-15" for f in facts)
    evidence = {e.evidence_id for e in parser.build_evidence(path)}
    assert all(set(f.evidence) <= evidence for f in facts)


def test_quarter_columns_do_not_contaminate_annual_period(tmp_path, monkeypatch):
    pages = _pages([["2023年分季度主要财务数据", "单位：元",
                     "第一季度\n第二季度\n第三季度\n第四季度", "营业收入\n10.00\n20.00\n30.00\n40.00"]])
    _, facts = _extract(tmp_path, monkeypatch, pages)
    assert {f.period: f.value for f in facts} == {
        "2023Q1": Decimal("10.00"), "2023Q2": Decimal("20.00"),
        "2023Q3": Decimal("30.00"), "2023Q4": Decimal("40.00"),
    }
    assert FactStore(facts).query("公司甲", "REVENUE", "2023Y") is None


def test_missing_units_and_ambiguous_columns_create_no_fact(tmp_path, monkeypatch):
    pages = _pages([["合并利润表", "2023年\n2022年", "营业收入\n100.00\n80.00"],
                    ["母公司利润表", "单位：元", "2023年\n2022年", "营业收入\n100.00\n80.00\n70.00"]])
    _, facts = _extract(tmp_path, monkeypatch, pages)
    assert facts == []


def test_half_year_balance_dates_keep_previous_year_end(tmp_path, monkeypatch):
    pages = _pages([["合并资产负债表", "单位：元", "2023年6月30日\n2022年12月31日",
                    "资产总计\n100.00\n80.00"]])
    _, facts = _extract(tmp_path, monkeypatch, pages, name="2023-08-15_2023年半年度报告.pdf")
    assert {f.period: f.value for f in facts} == {"2023H1": Decimal("100.00"), "2022Y": Decimal("80.00")}


@pytest.mark.parametrize("title,expected", [("2024年一季度报告.pdf", "2024Q1"), ("2024年三季度报告.pdf", "20249M")])
def test_quarter_filename_period_never_defaults_to_annual(title, expected):
    from pathlib import Path
    assert parser._document_period(Path(title), "2099Y") == expected


def test_wrapped_row_retains_original_resolvable_evidence(tmp_path, monkeypatch):
    pages = _pages([["合并利润表", "单位：元", "2023年\n2022年"]])
    pages[0]["blocks"].extend([
        {"bbox": [10.0, 100.0, 200.0, 130.0], "text": "2.归属于母公司所有者的净利润（净亏损\n以“-”号填列）"},
        {"bbox": [220.0, 100.0, 500.0, 120.0], "text": "-80.00\n-100.00"},
    ])
    path, facts = _extract(tmp_path, monkeypatch, pages)
    assert len(facts) == 2
    original_row_ids = {parser.make_evidence_id(parser.sha256_file(path), 1, block['text'].strip(), block['bbox'])
                        for block in pages[0]['blocks'][-2:]}
    assert all(original_row_ids <= set(f.evidence) for f in facts)
    assert all(len(f.evidence) >= 3 for f in facts)  # Include the proven financial heading.
    ids = {e.evidence_id for e in parser.build_evidence(path)}
    assert all(set(f.evidence) <= ids for f in facts)


def test_conflicting_same_disclosure_abstains_independent_of_order():
    conflicting = [Fact("first", "甲", "REVENUE", "2023Y", value=Decimal("100.00"), publish_date="2024-04-01"),
                   Fact("second", "甲", "REVENUE", "2023Y", value=Decimal("80.00"), publish_date="2024-04-01")]
    for rows in (conflicting, list(reversed(conflicting))):
        store = FactStore(rows)
        assert store.query("甲", "REVENUE", "2023Y") is None
        assert "冲突" in store.query_issue("甲", "REVENUE", "2023Y")
    adjacent = FactStore([Fact("a", "甲", "REVENUE", "2023Y", value=Decimal("100.00")),
                          Fact("b", "甲", "REVENUE", "2023Y", value=Decimal("100.01"))])
    assert adjacent.query("甲", "REVENUE", "2023Y") is None


def test_rounded_duplicate_disclosures_and_cutoff():
    store = FactStore([
        Fact("summary", "甲", "REVENUE", "2023Y", value=Decimal("496000000"), display_unit="亿元", decimals=2,
             publish_date="2024-04-01", source_type="financial_summary"),
        Fact("primary", "甲", "REVENUE", "2023Y", value=Decimal("495671818.88"), decimals=2,
             publish_date="2024-04-01", source_type="primary_statement"),
        Fact("future", "甲", "REVENUE", "2023Y", value=Decimal("510000000"), decimals=2,
             publish_date="2024-05-01", version="更正后"),
    ])
    assert store.query("甲", "REVENUE", "2023Y", cutoff="2024-04-22").fact_id == "primary"
    assert store.query("甲", "REVENUE", "2023Y").fact_id == "future"


@pytest.mark.parametrize("bad_value", ["not-a-number", None, "NaN", "Infinity", "-Infinity"])
def test_invalid_imported_fact_never_becomes_zero(bad_value):
    with pytest.raises(ValueError):
        fact_from_dict({"value": bad_value})
    with pytest.raises(ValueError):
        fact_from_dict({})


def test_multi_company_directory_never_relabels_other_issuer(tmp_path, monkeypatch):
    a = tmp_path / "reports" / "920001_公司甲" / "年报"
    b = tmp_path / "reports" / "920002_公司乙" / "年报"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    reports = [a / "2024-04-15_2023年年度报告.pdf", a / "2025-04-15_2024年年度报告.pdf",
               b / "2024-04-15_2023年年度报告.pdf"]
    for path in reports:
        path.write_bytes(path.name.encode("utf-8"))
    monkeypatch.setattr(parser, "extract_text", lambda path: _pages([["合并利润表", "单位：元",
        "2024年\n2023年" if "2025-" in str(path) else "2023年\n2022年", "营业收入\n100.00\n80.00"]]))
    selected = parser.load_or_extract_facts(tmp_path / "reports", "公司甲", "2099Y", "2099-01-01")
    assert {f.company for f in selected} == {"公司甲"}
    assert {f.period for f in selected} == {"2024Y", "2023Y", "2022Y"}
    all_issuers = parser.load_or_extract_facts(tmp_path / "reports", period="2099Y")
    assert {f.company for f in all_issuers} == {"公司甲", "公司乙"}
    with pytest.raises(ValueError, match="不一致"):
        parser.load_or_extract_facts(reports[2], "公司甲", "2023Y")
    with pytest.raises(ValueError, match="不一致"):
        parser.extract_facts(reports[2], "公司甲", "2023Y")


def test_index_metadata_filters_issuer_and_checks_frozen_hash(tmp_path, monkeypatch):
    import csv
    path = tmp_path / "reports" / "920001_公司甲" / "年报" / "2024-04-15_2023年年度报告.pdf"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"pdf")
    fieldnames = ["code", "name", "category", "year", "date", "file", "size_bytes", "sha256"]
    def write_index(sha):
        with (tmp_path / "index.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerow({"code": "920001", "name": "公司甲", "category": "年报", "year": "2023",
                "date": "2024-04-15", "file": path.relative_to(tmp_path).as_posix(), "size_bytes": "3", "sha256": sha})
    monkeypatch.setattr(parser, "extract_text", lambda _: _pages([["合并利润表", "单位：元", "2023年\n2022年", "营业收入\n100.00\n80.00"]]))
    write_index(parser.sha256_file(path))
    facts = parser.load_or_extract_facts(tmp_path / "reports", "公司甲", "2099Y")
    assert len(facts) == 2
    assert {f.company for f in facts} == {"公司甲"}
    assert parser.load_or_extract_facts(tmp_path / "reports", "公司乙", "2023Y") == []
    write_index("0" * 64)
    with pytest.raises(ValueError, match="SHA-256"):
        parser.load_or_extract_facts(tmp_path / "reports", "公司甲", "2023Y")


def test_coalesced_table_block_supports_current_previous_and_registered_issuer(tmp_path, monkeypatch):
    pages = _pages([["主要会计数据和财务指标",
                    "单位：元\n盈利能力\n本期\n上年同期\n增减比例%\n"
                    "营业收入\n120.00\n100.00\n20.00%\n毛利率%\n40.00%\n30.00%\n-\n"
                    "归属于挂牌公司股东的净利润\n10.00\n8.00\n25.00%\n"
                    "归属于挂牌公司股东的扣除非经常\n性损益后的净利润\n9.00\n7.00\n28.57%"]])
    path, facts = _extract(tmp_path, monkeypatch, pages)
    store = FactStore(facts)
    assert store.query("公司甲", "REVENUE", "2023Y").value == Decimal("120.00")
    assert store.query("公司甲", "NP_PARENT", "2022Y").value == Decimal("8.00")
    assert store.query("公司甲", "NP_PARENT_ADJ", "2023Y", nr="扣非").value == Decimal("9.00")
    assert store.query("公司甲", "GROSS_MARGIN", "2023Y").value == Decimal('0.4000')
    assert store.query("公司甲", "GROSS_MARGIN", "2022Y").value == Decimal('0.3000')
    assert len(facts) == 8  # Two disclosed percent facts are now supported safely.
    evidence = {e.evidence_id for e in parser.build_evidence(path)}
    assert all(set(f.evidence) <= evidence for f in facts)


def test_short_label_continuation_and_values_across_pdf_blocks(tmp_path, monkeypatch):
    pages = _pages([["会计数据和财务指标", "单位：元", "2023年\n2022年\n2021年"]])
    pages[0]["blocks"].extend([
        {"bbox": [10.0, 100.0, 180.0, 111.0], "text": "经营活动产生的现金流量净"},
        {"bbox": [10.0, 118.0, 25.0, 129.0], "text": "额"},
        {"bbox": [220.0, 100.0, 500.0, 111.0], "text": "48.00\n60.00\n-20.00%\n50.00"},
    ])
    path, facts = _extract(tmp_path, monkeypatch, pages)
    assert len(facts) == 3
    assert FactStore(facts).query("公司甲", "CFO", "2023Y").value == Decimal("48.00")
    ids = {e.evidence_id for e in parser.build_evidence(path)}
    assert all(set(f.evidence) <= ids for f in facts)


def test_cached_source_path_is_resolved_inside_corpus(tmp_path):
    import json
    source = tmp_path / "demo_annual.pdf"
    source.write_bytes(b"pdf")
    (tmp_path / "facts.json").write_text(json.dumps([{"fact_id": "demo", "company": "甲", "account": "REVENUE",
        "period": "2023Y", "value": "100.00", "source_path": "demo_annual.pdf"}]), encoding="utf-8")
    loaded = parser.load_or_extract_facts(tmp_path, "甲")
    assert loaded[0].source_path == str(source.resolve())
    escaped = tmp_path.parent / "outside.pdf"
    escaped.write_bytes(b"external")
    (tmp_path / "facts.json").write_text(json.dumps([{"value": "100.00", "source_path": "../outside.pdf"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="越过"):
        parser.load_or_extract_facts(tmp_path)
