"""Narrow source-method guards; exposed source data is not human gold."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path

import pytest

from mingjing.coverage import CoverageLedger
from mingjing.facts import FactStore, fact_from_dict
from mingjing.judge import Judge
from mingjing.mentions import extract_mentions, source_metric_identity
from mingjing.models import Fact
from mingjing.source_semantics import (freeze_source_dimensions, semantic_review_required,
    source_binding_issue, source_definition_diagnostic, source_dimensions_from_text,
    unresolved_source_definition_issue)
from mingjing.table_mentions import table_metric_identity

FIXTURES = Path(__file__).parent / "fixtures"
GEOMETRY = json.loads((FIXTURES / "financial_source_geometry_14.json").read_text(encoding="utf-8"))
OPERANDS = json.loads((FIXTURES / "current_ratio_source_operands.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("text", [
    "流动比率", "流动比率(倍)", "流动比率（%）", "流动比率为0.8倍", "流动比率=80.5%",
    "流动比率（流动资产/流动负债）", "流动比率=流动资产合计/流动负债合计",
    "流动比率指流动资产除以流动负债", "流动比率=流动资产/流动负债为0.8倍",
    "流动比率为流动资产与流动负债的比值", "按流动资产/流动负债计算的流动比率",
    "流动比率（流动资产/流动负债×100%）",
])
def test_plain_or_explicit_standard_current_ratio_keeps_canonical_definition(text):
    assert source_metric_identity("CURRENT_RATIO", text) == ("CURRENT_RATIO", "metric:current-ratio")
    assert table_metric_identity("CURRENT_RATIO", text, "主要财务比率") == ("CURRENT_RATIO", "metric:current-ratio")


@pytest.mark.parametrize("text", [
    "流动比率=现金/总负债", "流动比率（现金/总负债）", "流动比率=流动资产/总负债",
    "流动比率=流动资产减存货/流动负债", "流动比率以平均流动资产为分子",
    "按现金/总负债计算的流动比率", "流动比率（流动资产/流动负债但不含受限资金）",
    "流动比率采用现金/总负债计算", "自定义流动比率", "调整后流动比率",
])
def test_explicit_custom_method_never_gets_standard_identity_or_definition(text):
    assert source_metric_identity("CURRENT_RATIO", text) == ("CURRENT_RATIO_UNSPECIFIED", "")
    assert table_metric_identity("CURRENT_RATIO", text, "主要财务比率") == ("CURRENT_RATIO_UNSPECIFIED", "")


@pytest.mark.parametrize("method", ["流动比率=现金/总负债", "流动比率（现金/总负债）", "按现金/总负债计算的流动比率"])
def test_custom_definition_retains_numeric_occurrence_and_rejects_matching_facts_before_lookup(method, monkeypatch):
    text = "甲公司2023年合并口径" + method + "为0.8倍。"
    claims = extract_mentions(text, "甲公司")
    assert len(claims) == 1
    claim = freeze_source_dimensions(claims[0])
    assert claim.account == "CURRENT_RATIO_UNSPECIFIED" and semantic_review_required(claim)
    assert claim.numeric_text == "0.8倍" and claim.candidate_value == "0.8"
    assert text[claim.start:claim.end] == claim.numeric_text
    assert source_dimensions_from_text(claim)["account"] == "CURRENT_RATIO_UNSPECIFIED"
    issue = unresolved_source_definition_issue(claim)
    assert issue[0] == "X3" and "已有登记公式 FIN-current-ratio" in issue[1]
    store = FactStore([
        Fact("matching", "甲公司", "CURRENT_RATIO", "2023Y", value=Decimal("0.8"), display_unit="倍", publish_date="2024-04-01"),
        Fact("assets", "甲公司", "CURRENT_ASSETS", "2023Y", value=Decimal("80"), publish_date="2024-04-01"),
        Fact("liabilities", "甲公司", "CURRENT_LIABILITIES", "2023Y", value=Decimal("100"), publish_date="2024-04-01"),
    ])
    monkeypatch.setattr(store, "query_outcome", lambda *a, **k: pytest.fail("custom source method queried standard facts"))
    monkeypatch.setattr(store, "versions", lambda *a, **k: pytest.fail("custom source method inspected matching values"))
    result = Judge(store, "2024-04-22", require_dual_binding=True).verify(
        replace(claim, account="CURRENT_RATIO", binding_status="agreed"))
    assert result.status == "abstained" and result.abstention_code == "X3"
    assert result.expected is None and not result.operands and not result.correction
    assert result.source_dimensions["source_definition_diagnostic"]["source_account"] == "CURRENT_RATIO_UNSPECIFIED"


def real_current_ratios():
    ledger, claims = CoverageLedger.build(GEOMETRY["text"], GEOMETRY["pages"], [], OPERANDS["company"], "2023Y")
    return ledger, [freeze_source_dimensions(replace(claim, binding_status="agreed"))
                    for claim in claims if claim.account == "CURRENT_RATIO"]


def test_two_actual_historical_cells_keep_consolidated_structure_and_authorized_formula():
    assert GEOMETRY["source_doc_sha256"] == OPERANDS["draft_sha256"]
    assert OPERANDS["origin"] == "exposed_development_source_not_independent_gold"
    _, claims = real_current_ratios()
    assert len(claims) == 2 and [claim.period for claim in claims] == ["2022Y", "2023Y"]
    assert [claim.candidate_value for claim in claims] == ["0.6", "0.8"]
    store = FactStore([fact_from_dict(fact) for fact in OPERANDS["facts"]])
    for claim in claims:
        proof = source_dimensions_from_text(claim)
        assert proof["scope"] == "合并" and proof["definition_id"] == "metric:current-ratio"
        assert proof["scope_proof"]["kind"] == "bounded_combined_financial_summary"
        assert "source_definition_diagnostic" not in proof and not semantic_review_required(claim)
        result = Judge(store, OPERANDS["report_date"], require_dual_binding=True).verify(claim)
        assert result.status == "verified" and result.rule_ids == ["FIN-current-ratio"]
        assert result.formula == "CURRENT_ASSETS/CURRENT_LIABILITIES"
        assert {fact["account"] for fact in result.operands} == {"CURRENT_ASSETS", "CURRENT_LIABILITIES"}
        assert result.evidence


@pytest.mark.parametrize("year,value,unit,expected", [(2022, "0.6", "倍", "verified"),
                                                      (2023, "0.8", "倍", "verified"),
                                                      (2022, "57", "%", "verified"),
                                                      (2023, "80.5", "%", "verified"),
                                                      (2022, "0.6", "%", "violated"),
                                                      (2023, "1.8", "倍", "violated")])
def test_current_ratio_wrong_values_and_percent_display_do_not_change_formula(year, value, unit, expected):
    text = OPERANDS["company"] + str(year) + "年合并口径流动比率为" + value + unit + "。"
    claim = freeze_source_dimensions(replace(extract_mentions(text, OPERANDS["company"])[0], binding_status="agreed"))
    assert claim.account == "CURRENT_RATIO"
    store = FactStore([fact_from_dict(fact) for fact in OPERANDS["facts"]])
    result = Judge(store, OPERANDS["report_date"], require_dual_binding=True).verify(claim)
    assert result.status == expected and result.rule_ids == ["FIN-current-ratio"]
    assert result.formula == "CURRENT_ASSETS/CURRENT_LIABILITIES"


def test_scope_is_still_required_when_financial_summary_structure_is_missing():
    text = "主要财务比率\n2022A\n2023A\n流动比率\n0.6\n0.8\n"
    _, claims = CoverageLedger.build(text, [{"start": 0, "end": len(text), "page": 42}], [], OPERANDS["company"], "2023Y")
    assert len(claims) == 2 and all(claim.account == "CURRENT_RATIO" for claim in claims)
    store = FactStore([fact_from_dict(fact) for fact in OPERANDS["facts"]])
    for raw in claims:
        claim = freeze_source_dimensions(replace(raw, binding_status="agreed", caliber_hint=("合并", "未扣非")))
        assert source_dimensions_from_text(claim)["scope"] == ""
        result = Judge(store, OPERANDS["report_date"], require_dual_binding=True).verify(claim)
        assert result.status == "abstained" and result.abstention_code == "X3" and not result.operands


def test_same_sentence_custom_method_cannot_change_a_later_standard_occurrence():
    text = "甲公司2023年合并口径流动比率=现金/总负债为0.1倍，流动比率为0.8倍，营业收入100元。"
    claims = extract_mentions(text, "甲公司", preserve_occurrences=True)
    assert [claim.account for claim in claims] == ["CURRENT_RATIO_UNSPECIFIED", "CURRENT_RATIO", "REVENUE"]
    assert [claim.numeric_text for claim in claims] == ["0.1倍", "0.8倍", "100元"]
    assert source_dimensions_from_text(freeze_source_dimensions(claims[0]))["account"] == "CURRENT_RATIO_UNSPECIFIED"
    standard = source_dimensions_from_text(freeze_source_dimensions(claims[1]))
    assert standard["account"] == "CURRENT_RATIO" and standard["definition_id"] == "metric:current-ratio"


@pytest.mark.parametrize("caption", ["流动比率（现金/总负债）", "流动比率=现金/总负债"])
def test_custom_table_method_preserves_cells_without_admitting_standard_ratio(caption):
    text = "主要财务比率\n2022A\n2023A\n" + caption + "\n0.6\n0.8\n"
    ledger, claims = CoverageLedger.build(text, [{"start": 0, "end": len(text), "page": 42}], [], OPERANDS["company"], "2023Y")
    targets = [row for row in ledger.entries if row["text"].strip() in {"0.6", "0.8"}]
    assert len(targets) == 2 and all(row["status"] == "pending" and row["claim_id"] for row in targets)
    by_id = {claim.claim_id: claim for claim in claims}
    assert all(by_id[row["claim_id"]].account != "CURRENT_RATIO" for row in targets)
    assert all(not by_id[row["claim_id"]].table_context.get("definition_id") for row in targets)


@pytest.mark.parametrize("label,account,canonical,rule", [
    ("速动比率", "QUICK_RATIO_UNSPECIFIED", "QUICK_RATIO", "FIN-quick-ratio"),
    ("总资产周转率", "TURNOVER_ASSETS_UNSPECIFIED", "ASSET_TURNOVER", "FIN-asset-turnover"),
    ("存货周转率", "TURNOVER_INVENTORY_UNSPECIFIED", "INVENTORY_TURNOVER", "FIN-inventory-turnover"),
    ("应收账款周转率", "TURNOVER_AR_UNSPECIFIED", "AR_TURNOVER", "FIN-ar-turnover"),
    ("应付账款周转率", "TURNOVER_AP_UNSPECIFIED", "AP_TURNOVER", "FIN-ap-turnover"),
    ("期间费用率", "EXP_PERIOD_RATIO_UNSPECIFIED", "PERIOD_EXPENSE_RATIO", "FIN-period-expense-ratio"),
])
def test_missing_source_method_names_existing_registered_formula_but_does_not_unlock_it(label, account, canonical, rule):
    unit = "%" if account == "EXP_PERIOD_RATIO_UNSPECIFIED" else "倍"
    text = "甲公司2023年合并口径" + label + "为0.8" + unit + "。"
    claim = freeze_source_dimensions(extract_mentions(text, "甲公司")[0])
    assert claim.account == account and semantic_review_required(claim)
    diagnostic = source_definition_diagnostic(claim)
    assert diagnostic["registered_formula_available"] is True
    assert diagnostic["registered_rule_id"] == rule and diagnostic["canonical_account"] == canonical
    assert diagnostic["source_definition_status"] == "not_proved_by_source"
    assert diagnostic["required_source"]
    assert "已有登记公式" in diagnostic["reason"] and "尚无授权" not in diagnostic["reason"]
    assert unresolved_source_definition_issue(claim) == ("X2", diagnostic["reason"])
    proof = source_dimensions_from_text(claim)
    assert proof["source_definition_diagnostic"] == diagnostic and proof["definition_id"] == ""
    result = Judge(FactStore([]), "2024-04-22").verify(claim)
    assert result.status == "abstained" and result.abstention_code == "X2"
    assert result.expected is None and not result.operands
    rebound = replace(claim, account=canonical, binding_status="agreed")
    assert source_definition_diagnostic(rebound) == diagnostic
    assert source_binding_issue(rebound, strict=True) == ("X2", diagnostic["reason"])


def test_new_diagnostic_is_not_injected_into_a_legacy_frozen_source_lock():
    raw = extract_mentions("甲公司2023年合并口径速动比率为0.8倍。", "甲公司")[0]
    new = freeze_source_dimensions(raw)
    legacy_proof = deepcopy(new.table_context["source_dimensions"])
    legacy_proof.pop("source_definition_diagnostic")
    legacy = replace(raw, table_context={**raw.table_context, "source_dimensions": legacy_proof})
    snapshot = json.dumps(legacy.table_context, ensure_ascii=False, sort_keys=True)
    assert freeze_source_dimensions(legacy) is legacy
    assert "source_definition_diagnostic" not in source_dimensions_from_text(legacy)
    assert json.dumps(legacy.table_context, ensure_ascii=False, sort_keys=True) == snapshot
    assert source_definition_diagnostic(legacy)["registered_rule_id"] == "FIN-quick-ratio"


def test_unregistered_compound_indicator_does_not_claim_a_registered_alternative():
    claim = freeze_source_dimensions(extract_mentions("甲公司2023年合并口径ROIC为10%。", "甲公司")[0])
    assert source_definition_diagnostic(claim) is None
    code, reason = unresolved_source_definition_issue(claim)
    assert code == "X2" and "尚无授权" in reason
