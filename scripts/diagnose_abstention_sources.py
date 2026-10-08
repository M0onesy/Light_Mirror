"""Read-only diagnosis of abstentions in a frozen financial run.

This report never edits the run, facts, source gaps, or rule pack.  It records
which abstentions could be revisited from already frozen material and which
ones require a new source, an explicitly signed rule, or a semantic parser
change.  In particular, a matching amount is never treated as proof of
period, scope, or definition.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
from pathlib import Path
from typing import Any


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _claim_index(claims: list[dict]) -> dict[str, dict]:
    return {str(item.get("claim_id")): item for item in claims}


def _reason_group(result: dict) -> str:
    reason = str(result.get("reason") or "")
    if "合并或母公司" in reason:
        return "scope_unresolved"
    if "分业务" in reason or "分部" in reason or "产品或客户" in reason:
        return "segment_dimension_unresolved"
    if "定义" in reason or "授权输入" in reason or "费用率" in reason:
        return "definition_or_formula_unresolved"
    if "负基数" in reason or "零基数" in reason:
        return "negative_or_zero_base"
    if "CAGR" in reason:
        return "cagr_periods_unresolved"
    if "占比" in reason:
        return "ratio_denominator_unresolved"
    if "同币种" in reason or "汇率" in reason:
        return "currency_source_missing"
    if "结构" in reason or "维度不足" in reason:
        return "source_parse_incomplete"
    if "事实" in reason:
        return "fact_missing"
    return "other"


def _action(result: dict, claim: dict) -> tuple[str, str, str]:
    """Return action, confidence, and the evidence gate still required."""
    code = result.get("abstention_code") or "unknown"
    account = (result.get("source_dimensions") or {}).get("account", "")
    scope = (result.get("source_dimensions") or {}).get("evaluation_scope", "")
    text = str(claim.get("text") or "")
    claim_id = str(result.get("claim_id"))

    # The draft itself labels these as a contract/procurement announcement,
    # not a financial-statement expense.  This can leave the primary ledger
    # without an abstention, while retaining the occurrence in the operating
    # ledger for review.
    if claim_id == "unresolved-551968cb503129780199":
        return (
            "reclassify_to_issuer_operating",
            "high",
            "研报原文明确为2023年11月供应商采购公告；不能绑定为年度费用或财报科目，需在覆盖账本保留运营信息。",
        )
    if code == "X6" and scope == "issuer_operating":
        return (
            "retain_operating_ledger_reparse",
            "medium",
            "原文已有运营信息/图表分类和单位，但缺业务期间或完整图表列；完成结构重解析后仍不得转为财务报表事实。",
        )
    if code == "X1" and account == "NONOPERATING_INCOME" and (result.get("source_dimensions") or {}).get("period") == "2022Y":
        return (
            "conditional_derive_after_rule_signoff",
            "medium",
            "同报告合并利润总额、营业利润、营业外支出均有证据；仅可按登记公式派生0，不能声称年报原文披露了0。",
        )
    if code == "X2" and "负基数" in str(result.get("reason") or ""):
        return (
            "retain_abstention",
            "high",
            "冻结规则对负/零基数默认同比弃权；除非来源明示亏损收窄算法，不得改为普通同比。",
        )
    if code in {"X1", "X6"} and scope == "industry_historical":
        return (
            "retain_missing_external_source",
            "high",
            "属于IDC/行业历史材料，冻结 corpus 没有同币种原始来源；不能用发行人财报补齐。",
        )
    if "客户" in text or "客户" in account or account in {"REVENUE_SHARE", "REVENUE"} and "占" in text:
        return (
            "retain_source_scope_or_definition_block",
            "high",
            "客户/业务分组需要原表分母、维度和发行人财务 scope；客户归集脚注不证明合并/母公司口径。",
        )
    if code == "X3" and "口径" in str(result.get("reason") or ""):
        return (
            "retain_scope_abstention",
            "high",
            "研究报告原文没有唯一合并/母公司声明；不能从金额巧合或同页结构传播口径。",
        )
    return (
        "retain_abstention",
        "high",
        "冻结材料未同时提供完整事实维度、授权定义和可审查证据。",
    )


def diagnose(results_path: Path, acceptance_path: Path, out: Path) -> Path:
    data = json.loads(results_path.read_text(encoding="utf-8"))
    acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
    claims = _claim_index(data.get("claims", []))
    abstained = [item for item in data.get("results", []) if item.get("status") == "abstained"]
    records: list[dict[str, Any]] = []
    for item in abstained:
        claim = claims.get(str(item.get("claim_id")), {})
        dims = item.get("source_dimensions") or {}
        action, confidence, gate = _action(item, claim)
        records.append({
            "claim_id": item.get("claim_id"),
            "abstention_code": item.get("abstention_code"),
            "reason_group": _reason_group(item),
            "reason": item.get("reason"),
            "action": action,
            "action_confidence": confidence,
            "remaining_gate": gate,
            "evaluation_scope": dims.get("evaluation_scope", ""),
            "account": dims.get("account", ""),
            "period": dims.get("period", ""),
            "scope": dims.get("scope", ""),
            "dimension_kind": dims.get("dimension_kind", ""),
            "source_text_sha256": dims.get("source_text_sha256", ""),
            "source_span": item.get("source_span") or claim.get("source_span"),
            "claim_text": claim.get("text", item.get("claim_text", "")),
            "blocker_codes": sorted({str(block.get("code")) for block in item.get("blockers", []) if block.get("code")}),
        })

    def counts(key: str) -> dict[str, int]:
        return dict(collections.Counter(str(item.get(key) or "unknown") for item in records))

    by_source = collections.defaultdict(lambda: {
        "count": 0, "codes": collections.Counter(), "accounts": collections.Counter(),
        "claim_ids": [], "evaluation_scopes": collections.Counter(),
        "periods": collections.Counter(), "dimension_kinds": collections.Counter(),
        "source_previews": [],
    })
    for item in records:
        key = item["source_text_sha256"] or "no_source_text_hash"
        bucket = by_source[key]
        bucket["count"] += 1
        bucket["codes"][item["abstention_code"] or "unknown"] += 1
        bucket["accounts"][item["account"] or "unknown"] += 1
        bucket["evaluation_scopes"][item["evaluation_scope"] or "unknown"] += 1
        bucket["periods"][item["period"] or "unknown"] += 1
        bucket["dimension_kinds"][item["dimension_kind"] or "unknown"] += 1
        bucket["claim_ids"].append(item["claim_id"])
        preview = " ".join(str(item.get("claim_text") or "").split())[:180]
        if preview and preview not in bucket["source_previews"]:
            bucket["source_previews"].append(preview)
    source_groups = {}
    for key, bucket in by_source.items():
        source_groups[key] = {
            "count": bucket["count"],
            "codes": dict(bucket["codes"]),
            "accounts": dict(bucket["accounts"]),
            "evaluation_scopes": dict(bucket["evaluation_scopes"]),
            "periods": dict(bucket["periods"]),
            "dimension_kinds": dict(bucket["dimension_kinds"]),
            "source_previews": bucket["source_previews"][:5],
            "claim_ids": bucket["claim_ids"],
        }

    facts = data.get("facts", [])
    def fact_values(account: str, period: str, scope: str = "合并") -> list[dict]:
        # Use one coherent, latest-as-of report for the conditional bridge;
        # mixing equivalent values from different disclosure versions would
        # hide a version conflict.
        selected = [
            f for f in facts
            if f.get("account") == account and f.get("period") == period
            and f.get("scope") == scope and f.get("source_doc") == "595ba27f8900"
        ]
        return selected[:1]

    derivation = {
        "claim_id": "table-51147-51148",
        "account": "NONOPERATING_INCOME",
        "period": "2022Y",
        "status": "conditional_not_applied",
        "formula": "PROFIT_TOTAL - OPERATING_PROFIT + NONOPERATING_EXPENSE",
        "operands": {
            "PROFIT_TOTAL": fact_values("PROFIT_TOTAL", "2022Y"),
            "OPERATING_PROFIT": fact_values("OPERATING_PROFIT", "2022Y"),
            "NONOPERATING_EXPENSE": fact_values("NONOPERATING_EXPENSE", "2022Y"),
        },
        "computed_value": "0",
        "gate": "签发独立派生规则并在结果中标记derived_from_explicit_operands；不能把原文横线改写成披露事实。",
    }

    report = {
        "schema_version": "abstention-source-diagnosis.v1",
        "status": "diagnostic_only_no_frozen_run_modified",
        "inputs": {
            "results": str(results_path), "results_sha256": sha(results_path),
            "financial_coverage_acceptance": str(acceptance_path), "acceptance_sha256": sha(acceptance_path),
            "run_id": data.get("run_id"), "acceptance_status": acceptance.get("status"),
        },
        "totals": {
            "abstained_results": len(records),
            "by_code": counts("abstention_code"),
            "by_reason_group": counts("reason_group"),
            "by_action": counts("action"),
            "by_evaluation_scope": counts("evaluation_scope"),
            "by_account": counts("account"),
        },
        "source_groups": source_groups,
        "conditional_derivations": [derivation],
        "records": records,
        "source_gap_summary": {
            "count": len(data.get("source_gaps", [])),
            "by_dimension_kind": dict(collections.Counter(g.get("dimension_kind", "") for g in data.get("source_gaps", []))),
            "eligible_as_fact_false": sum(
                g.get("eligible_as_fact") is False or g.get("eligible_for_verification") is False
                for g in data.get("source_gaps", [])
            ),
            "policy": "客户/业务/采购来源缺口保留为缺口，不升级为Fact；发行人scope未知。",
        },
        "safe_resolution_count": sum(item["action"] == "conditional_derive_after_rule_signoff" for item in records),
        "safe_resolution_statement": "本报告不直接解除任何弃权；仅一条营业外收入可在独立派生规则签发后条件性重算，其余核心弃权没有足够冻结证据可安全解除。",
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "diagnosis.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# Stable-16 来源弃权诊断",
        "",
        f"输入运行：`{data.get('run_id')}`；结果 SHA-256：`{sha(results_path)}`。",
        "本报告只读分析，不修改 stable-16 运行、事实、来源缺口或规则锁。",
        "",
        "## 统计",
        "",
        f"- 弃权结果：{len(records)}；按代码：{json.dumps(report['totals']['by_code'], ensure_ascii=False)}。",
        f"- 按原因：{json.dumps(report['totals']['by_reason_group'], ensure_ascii=False)}。",
        f"- 来源缺口：{report['source_gap_summary']['count']}（全部 eligible_as_fact=false：{report['source_gap_summary']['eligible_as_fact_false']}）。",
        "",
        "## 可安全推进的项目",
        "",
        "- `table-51147-51148`（2022Y 营业外收入）有同一合并报告的利润总额、营业利润和营业外支出证据，按 `PROFIT_TOTAL - OPERATING_PROFIT + NONOPERATING_EXPENSE` 得 0。当前仅登记为条件性派生候选，需先签发规则并保留 derived 标记。",
        "- `unresolved-551968cb503129780199` 的 687 万元在原文明确属于 2023 年 11 月 AI 服务器采购公告；可从主财务事实账本重分类到公司运营信息，不能作为年度费用核验。",
        "- 其余运营图表和 HPC 会议费用应留在运营账本；重解析可完善单位/图表列，但不升级为财报 Fact。",
        "",
        "## 必须保留的弃权",
        "",
        "客户/业务表共有 228 条来源缺口（customer 160、customer_group 32、purchase_mode 24、business 12）。招股书客户归集脚注没有证明发行人合并/母公司财务范围，不能通过金额相等、同页或模型一致解除；客户类型 7.93%/10.10%/17.59%/24.25% 还需要固定分母为超算云服务收入的来源定义。",
        "",
        "X1 的公允价值变动收益、投资净收益、2023 应付票据/长期借款和留存收益均不能把原文横线或空白写成零；X1 的美元行业数字缺少冻结的 IDC 原始来源，不能用人民币财报替换。",
        "",
        "X2 的负基数同比、未登记的费用率/ROIC/周转率/速动比率/净负债率等需要已签发定义、分母、期间和公式；当前不因数值可重算而解除。X3 的口径、定义和主体绑定缺证；X6 的图表/客户/行业文本需要完整解析或外部来源。",
        "",
        "## 结论",
        "",
        "稳定材料最多支持一条条件性派生候选和若干运营账本重分类，不能安全把核心公司历史财务弃权压到个位数。若要继续降低弃权，必须新增规则签发、补充原始来源或由真人确认定义；不得修改 stable-16 结果来包装通过。",
    ]
    (out / "diagnosis.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--acceptance", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    options = parser.parse_args()
    diagnose(options.results, options.acceptance, options.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
