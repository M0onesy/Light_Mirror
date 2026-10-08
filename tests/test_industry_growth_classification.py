"""Local industry lineage must preserve occurrences and its source boundaries."""
from types import SimpleNamespace

import pytest

from mingjing.coverage import CoverageLedger
from mingjing.source_classification import classify_occurrence


def classify(text, number, *, source_region=None, reason="deterministic_financial_mention",
             status="pending", modality_basis=None, table_context=None):
    start = text.index(number)
    end = start + len(number)
    clause_start = max(text.rfind("，", 0, start), text.rfind(",", 0, start)) + 1
    claim = SimpleNamespace(
        company="甲科技", account="UNKNOWN", text=text[clause_start:],
        source_numeric_start=start - clause_start, table_context=table_context or {},
    )
    return classify_occurrence(
        text, start=start, end=end, line=text[clause_start:], claim=claim,
        status=status, reason=reason, modality_basis=modality_basis,
        source_region=source_region,
    )


@pytest.mark.parametrize("clause", [
    "CAGR为52.4%", "CAGR 为52.4%", "cagr 为52.4%", "年均增长率为52.4%",
    "年复合增长率为52.4%", "复合增长率达52.4%", "增长率约为52.4%",
    "同比增长52.4%", "同比增长率为52.4%", "增速为52.4%",
    "其同比增长52.4%", "该市场2022年同比增长52.4%", "2022年同比下降52.4%",
])
def test_market_growth_continuation_has_source_receipt(clause):
    text = "2022年超算云市场规模达33.2亿元，" + clause + "。"
    region = {"start": 0, "end": len(text), "page": 32}
    scope, basis = classify(text, "52.4%", source_region=region)
    assert scope == "industry_historical"
    assert basis["rule"] == "source_industry_growth_continuation"
    proof = basis["industry_lineage"]
    assert proof["kind"] == "same_sentence_industry_growth"
    assert proof["page_bound"] == region
    assert proof["page_boundary_basis"] == "host_source_region"
    assert proof["occurrence_span"] == [text.index("52.4%"), text.index("52.4%") + 5]
    for key in ("subject", "continuation"):
        span = proof[key]
        assert text[span["start"]:span["end"]] == span["text"]


def test_one_page_pdf_line_wrap_keeps_written_market_subject():
    text = "据IDC数据，2022年超算云市场\n规模达33.2亿元，\nCAGR 为52.4%。"
    scope, basis = classify(text, "52.4%", source_region={"start": 0, "end": len(text), "page": 32})
    assert scope == "industry_historical"
    assert "市场\n规模" in basis["industry_lineage"]["subject"]["text"]


@pytest.mark.parametrize("boundary", ["。", "；", ";", "！", "?", "\n\n", "\f",
                                           "\n资料来源：IDC\n", "\n数据来源：IDC\n", "\n来源：IDC\n",
                                           "\n图12：其他指标\n", "\n表12：其他指标\n"])
def test_growth_does_not_cross_source_or_sentence_boundary(boundary):
    text = "2022年超算云市场规模达33.2亿元" + boundary + "CAGR为52.4%。"
    scope, basis = classify(text, "52.4%", source_region={"start": 0, "end": len(text), "page": 32})
    assert scope == "issuer_historical_financial"
    assert "industry_lineage" not in basis


@pytest.mark.parametrize("new_subject", [
    "公司2022年同比增长", "甲科技同比增长", "客户同比增长", "企业用户同比增长",
    "个人客户同比增长", "公司营收100万元，同比增长", "营业收入100万元，同比增长",
    "销售收入100万元，同比增长", "利润100万元，同比增长", "现金流100万元，同比增长",
    "供应商收入100万元，同比增长", "微软增长率为", "采购金额100万元，同比增长",
])
def test_market_growth_cannot_transfer_to_issuer_customer_or_financial_measure(new_subject):
    text = "2022年超算云市场规模达33.2亿元，" + new_subject + "52.4%。"
    scope, basis = classify(text, "52.4%", source_region={"start": 0, "end": len(text), "page": 32})
    assert scope == "issuer_historical_financial"
    assert "industry_lineage" not in basis


@pytest.mark.parametrize("target", ["CAGR为52.4%", "同比增长52.4%", "占整体市场的52.4%"])
def test_real_page_boundary_blocks_even_a_single_newline(target):
    first = "2022年超算云市场规模达33.2亿元，"
    text = first + "\n" + target + "。"
    region = {"start": len(first) + 1, "end": len(text), "page": 33}
    scope, basis = classify(text, "52.4%", source_region=region)
    assert scope == "issuer_historical_financial"
    assert "industry_lineage" not in basis


def test_legacy_share_lineage_is_retained_with_page_proof():
    text = "据IDC数据，2021年国内专用于人工\n智能的公有云服务市场规模达到44.1亿元人民币，占整体人工智能软件市场的13.4%。"
    region = {"start": 0, "end": len(text), "page": 32}
    scope, basis = classify(text, "13.4%", source_region=region)
    assert scope == "industry_historical"
    assert basis["rule"] == "source_industry_ratio_continuation"
    assert basis["industry_lineage"]["page_bound"] == region


@pytest.mark.parametrize("region", [{"start": -1, "end": 100}, {"start": "0", "end": 100},
                                       {"start": 0, "end": True}, {"start": 0, "end": 1}, [0, 100]])
def test_invalid_page_region_cannot_authorize_lineage(region):
    text = "2022年超算云市场规模达33.2亿元，CAGR为52.4%。"
    scope, basis = classify(text, "52.4%", source_region=region)
    assert scope == "issuer_historical_financial"
    assert "industry_lineage" not in basis


@pytest.mark.parametrize("status,reason,modality,expected", [
    ("rule_excluded", "identifier_date_or_page", None, "rule_excluded"),
    ("out_of_scope", "forecast_prose_without_period", {"modality": "forecast"}, "forecast"),
    ("out_of_scope", "valuation_prose_lineage", {"modality": "hypothesis"}, "hypothesis"),
])
def test_existing_modality_and_exclusion_precedence(status, reason, modality, expected):
    text = "2022年超算云市场规模达33.2亿元，CAGR为52.4%。"
    scope, basis = classify(text, "52.4%", reason=reason, status=status, modality_basis=modality)
    assert scope == expected
    assert "industry_lineage" not in basis


def test_explicit_company_financial_dimension_cannot_be_reclassified():
    text = "2022年超算云市场规模达33.2亿元，CAGR为52.4%。"
    scope, _ = classify(text, "52.4%", table_context={"dimension_kind": "business"})
    assert scope == "issuer_historical_financial"


def test_legacy_call_states_that_physical_page_boundary_is_unavailable():
    text = "2022年超算云市场规模达33.2亿元，CAGR为52.4%。"
    scope, basis = classify(text, "52.4%")
    assert scope == "industry_historical"
    assert basis["industry_lineage"]["page_boundary_basis"] == "not_available_sentence_and_source_boundaries_only"


def test_full_ledger_preserves_market_size_and_cagr_occurrences_and_results():
    text = "3.1、空间：2022年超算云市场规模达33.2亿元，CAGR为52.4%。"
    pages = [{"id": "page32", "page": 32, "start": 0, "end": len(text)}]
    ledger, claims = CoverageLedger.build(text, pages, [], "甲科技", "2023Y")
    targets = [row for row in ledger.entries if row["text"].strip() in {"33.2亿元", "52.4%"}]
    assert len(targets) == 2
    assert all(row["evaluation_scope"] == "industry_historical" for row in targets)
    assert all(row["status"] == "pending" and row["claim_id"] for row in targets)
    claim_ids = {claim.claim_id for claim in claims}
    assert all(row["claim_id"] in claim_ids for row in targets)
    identities = [(row["id"], row["start"], row["end"], row["text"]) for row in ledger.entries]
    for row in targets:
        ledger.complete(row["claim_id"], {"status": "abstained", "abstention_code": "X1"})
    assert [(row["id"], row["start"], row["end"], row["text"]) for row in ledger.entries] == identities
    assert all(row["result_status"] == "abstained" and row["abstention_code"] == "X1" for row in targets)


def test_ledger_uses_actual_pages_for_market_share_and_growth():
    first = "2022年超算云市场规模达33.2亿元，"
    second = "CAGR为52.4%，占比为13.4%。"
    text = first + "\n" + second
    pages = [
        {"id": "page32", "page": 32, "start": 0, "end": len(first)},
        {"id": "page33", "page": 33, "start": len(first) + 1, "end": len(text)},
    ]
    ledger, _ = CoverageLedger.build(text, pages, [], "甲科技", "2023Y")
    targets = [row for row in ledger.entries if row["text"].strip() in {"52.4%", "13.4%"}]
    assert len(targets) == 2
    assert all(row["evaluation_scope"] == "issuer_historical_financial" and row["claim_id"] for row in targets)
