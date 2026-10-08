"""Add the threshold operation and repair one known broker composition card.

This is an incremental migration, not a base rulepack rebuild. All unrelated
cards, source declarations, clauses, signatures and raw source files survive.
The previous changed card and file hashes are returned in the migration
receipt; existing run locks are never read or modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile


PROJECT = Path(__file__).resolve().parents[1]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

from mingjing.thresholds import RULE_FORMULA, RULE_ID, RULE_INPUTS, RULE_VERSION
from mingjing.rules import validate_rulepack
from scripts.extend_coverage_rules import refresh_rulepack_lock
from scripts.rulepack_build_guard import RulepackBuildError, exclusive_rulepack_build


SOURCE_ID = "MJ-THRESHOLD-METHOD"
CLAUSE_ID = "SOURCE-threshold-entailment-method.v1"
BROKER_ID = "FIN-broker-other-current-assets"
BROKER_CLAUSE_ID = "FIN-broker-other-current-assets-components.v2"
BROKER_FORMULA = "CURRENT_ASSETS-CASH_SOURCE-AR_NOTES-OTHER_RECEIVABLES-PREPAYMENTS-INVENTORY"
BROKER_QUOTE = (
    "券商流动资产残项仅在原简表明确列示现金、应收票据及应收账款、其他应收款、预付账款、存货与其他流动资产的互斥组成时，"
    "按流动资产合计依次减去前五项计算；所有项目须绑定同报告、公司、期间、币种和列版本的原文定义及证据，缺项不默认零。"
)


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _freeze(card):
    card.pop("version_hash", None)
    card["version_hash"] = _digest(card)
    return card


def threshold_card(quote):
    ref = {"source_id": SOURCE_ID, "clause_id": CLAUSE_ID, "quote": quote,
           "quote_sha256": hashlib.sha256(quote.encode("utf-8")).hexdigest()}
    positive = {"lower_entails": "1", "upper_entails": "1",
                "source_below_claim": "0", "claim_below_source": "0"}
    overlap = {**positive, "lower_entails": "0"}
    disjoint = {**overlap, "claim_below_source": "1"}
    return _freeze({
        "id": RULE_ID, "error_type": "E7", "name": "来源引述阈值谓词蕴含",
        "formula": RULE_FORMULA, "inputs": dict(RULE_INPUTS),
        "constants": {"ONE": {"value": "1", "dimension": "ratio"}},
        "dimension": "boolean", "positive_inputs": [], "basis": quote,
        "basis_refs": [ref], "tier": "T3", "version": RULE_VERSION,
        "applicable_caliber": "可信来源引述；发行人、期间、分子分母、单位、版本和截止日先行验证；不作为点事实",
        "conflict_key": RULE_ID, "implementation": "deterministic_kernel",
        "operation": "source_predicate_entailment.v1",
        "review_status": "engineering_validated", "drafted_by": "register_threshold_rule.py",
        "approved_by": None,
        "limitations": "只证明来源谓词蕴含/矛盾，不证明精确比例；专业签发待完成；不替换h=2%模糊词政策",
        "examples": {
            "positive": {"operands": positive, "expected": True},
            "negative": {"operands": overlap, "expected": False},
            "boundary": {"operands": disjoint, "expected": True},
        },
    })


def _version(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise RulepackBuildError("Incremental registration requires valid release declarations")
    return tuple(map(int, value.split(".")))


def _write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _add_unique(items, item, kind):
    matches = [existing for existing in items if existing.get("id") == item["id"]]
    if len(matches) > 1:
        raise RulepackBuildError(f"Duplicate {kind} identity")
    if matches and matches[0] != item:
        raise RulepackBuildError(f"Existing {kind} is different; refusing an implicit replacement")
    if not matches:
        items.append(item)


def register_threshold_rule(root=PROJECT / "rulepack"):
    root = Path(root).resolve()
    with exclusive_rulepack_build(root):
        cards = json.loads((root / "cards.json").read_text(encoding="utf-8"))
        sources = json.loads((root / "sources.json").read_text(encoding="utf-8"))
        clauses = json.loads((root / "source_clauses.json").read_text(encoding="utf-8"))
        old_lock = json.loads((root / "rulepack.lock").read_text(encoding="utf-8"))
        if not isinstance(cards, list) or not cards or len({c["id"] for c in cards}) != len(cards):
            raise RulepackBuildError("Rule cards are missing or have duplicate identities")
        # Keep an existing newer release, rather than downgrade it to our patch.
        versions = [sources.get("version"), old_lock.get("version"), RULE_VERSION,
                    *[card.get("version") for card in cards]]
        release = max(versions, key=_version)
        before_cards = json.loads(json.dumps(cards))
        before_hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                         for name in ("cards.json", "sources.json", "source_clauses.json", "rulepack.lock")}
        method_path = root / "sources/MJ-THRESHOLD-METHOD.md"
        # The dedicated method snapshot is versioned source, not generated by a
        # prompt or silently copied over an already signed custom snapshot.
        if not method_path.is_file():
            raise RulepackBuildError("The threshold methodology snapshot is missing")
        method = method_path.read_text(encoding="utf-8")
        quote = next((line for line in method.splitlines() if line.startswith("来源谓词蕴含规则：")), None)
        if not quote:
            raise RulepackBuildError("The threshold methodology clause is missing")
        addition = threshold_card(quote)
        existing = next((card for card in cards if card["id"] == RULE_ID), None)
        if existing:
            # Preserve professional review data if the exact operation remains
            # unchanged. A signed/different operation is never overwritten.
            review = {"version_hash", "review_status", "approved_by"}
            if ({k: v for k, v in existing.items() if k not in review}
                    != {k: v for k, v in addition.items() if k not in review}):
                raise RulepackBuildError("Existing threshold operation differs; explicit migration required")
        else:
            cards.append(addition)
        source = {"id": SOURCE_ID, "kind": "methodology", "name": "来源引述阈值断言工程方法v1",
                  "snapshot": "sources/MJ-THRESHOLD-METHOD.md",
                  "sha256": hashlib.sha256(method_path.read_bytes()).hexdigest(),
                  "source_url": None, "effective_at": None,
                  "validation": "engineering_validated_human_review_pending"}
        _add_unique(sources["sources"], source, "methodology")
        _add_unique(clauses, {"id": CLAUSE_ID, "source_id": SOURCE_ID, "quote": quote,
                             "quote_sha256": hashlib.sha256(quote.encode("utf-8")).hexdigest(),
                             "normalization": "UTF8_LF"}, "clause")

        broker = next((card for card in cards if card["id"] == BROKER_ID), None)
        if broker is None:
            raise RulepackBuildError("The specific broker composition card is missing")
        old_broker = json.loads(json.dumps(broker))
        coverage_path = root / "sources/MJ-COVERAGE-METHOD.md"
        coverage_raw = coverage_path.read_bytes()
        coverage_text = coverage_raw.decode("utf-8")
        coverage_new = coverage_raw
        if BROKER_QUOTE not in coverage_text.splitlines():
            coverage_new = coverage_raw + ("\n" + BROKER_QUOTE + "\n").encode("utf-8")
        broker_inputs = {item: "currency" for item in (
            "CURRENT_ASSETS", "CASH_SOURCE", "AR_NOTES", "OTHER_RECEIVABLES", "PREPAYMENTS", "INVENTORY")}
        if broker.get("formula") != BROKER_FORMULA:
            if broker.get("approved_by"):
                raise RulepackBuildError("A signed broker card cannot be replaced by an engineering migration")
            if broker.get("formula") != "CURRENT_ASSETS-CASH_SOURCE-AR_NOTES-INVENTORY":
                raise RulepackBuildError("Broker card changed since the reviewed failure; explicit migration required")
            operands = {"CURRENT_ASSETS": "100", "CASH_SOURCE": "20", "AR_NOTES": "10",
                        "OTHER_RECEIVABLES": "7", "PREPAYMENTS": "8", "INVENTORY": "5"}
            broker.update(formula=BROKER_FORMULA, inputs=broker_inputs,
                          version=RULE_VERSION, drafted_by="register_threshold_rule.py",
                          basis=BROKER_QUOTE,
                          basis_refs=[{"source_id": "MJ-COVERAGE-METHOD", "clause_id": BROKER_CLAUSE_ID,
                                       "quote": BROKER_QUOTE, "quote_sha256": hashlib.sha256(BROKER_QUOTE.encode("utf-8")).hexdigest()}],
                          applicable_caliber="原简表明示六项互斥流动资产组成；同公司、期间、币种、报告和列版本；原文现金定义与每个操作数均有证据；缺项不默认零",
                          review_status="engineering_validated", approved_by=None,
                          limitations="不得用法定同名科目或相等金额推断简表组成；专业签发待完成",
                          examples={"positive": {"operands": operands, "expected": "50"},
                                    "negative": {"operands": operands, "expected": "65", "expect_match": False},
                                    "boundary": {"operands": {**operands, "PREPAYMENTS": None}, "expected": None}})
            _freeze(broker)
        elif broker.get("inputs") != broker_inputs or broker.get("basis") != BROKER_QUOTE:
            raise RulepackBuildError("Existing corrected broker card differs; refusing implicit mutation")
        _add_unique(clauses, {"id": BROKER_CLAUSE_ID, "source_id": "MJ-COVERAGE-METHOD", "quote": BROKER_QUOTE,
                             "quote_sha256": hashlib.sha256(BROKER_QUOTE.encode("utf-8")).hexdigest(),
                             "normalization": "UTF8_LF"}, "clause")
        coverage_source = next(item for item in sources["sources"] if item["id"] == "MJ-COVERAGE-METHOD")
        coverage_source["sha256"] = hashlib.sha256(coverage_new).hexdigest()
        sources["version"] = release
        sources["threshold_assertion_revision"] = "source-predicate-entailment.v1"
        sources.pop("version_hash", None)
        sources["version_hash"] = _digest(sources)

        preserved = [card["id"] for card in before_cards if card["id"] != BROKER_ID]
        for old in before_cards:
            if old["id"] != BROKER_ID and old != next(card for card in cards if card["id"] == old["id"]):
                raise RulepackBuildError("Incremental migration changed an unrelated card")

        # Verify a complete staging tree before the first published mutation.
        # The release lock is published last: an interrupted migration fails
        # the existing resource-lock validation rather than appearing valid.
        with tempfile.TemporaryDirectory(prefix=".threshold-rule-", dir=root.parent) as temporary:
            stage = Path(temporary) / "rulepack"
            shutil.copytree(root, stage, ignore=shutil.ignore_patterns("__pycache__", ".rulepack-build.lock"))
            (stage / "sources/MJ-COVERAGE-METHOD.md").write_bytes(coverage_new)
            for name, payload in (("cards.json", cards), ("sources.json", sources), ("source_clauses.json", clauses)):
                _write_json(stage / name, payload)
            staged_lock = refresh_rulepack_lock(stage)
            validation = validate_rulepack(stage)
            if validation["status"] != "ok":
                raise RulepackBuildError("Staged incremental rulepack failed validation: " + "; ".join(validation["errors"]))
            changed = []
            for name in ("sources/MJ-COVERAGE-METHOD.md", "cards.json", "sources.json", "source_clauses.json", "rulepack.lock"):
                content = (stage / name).read_bytes()
                if content == (root / name).read_bytes():
                    continue
                pending = root / (name + ".threshold.tmp")
                pending.write_bytes(content)
                pending.replace(root / name)
                changed.append(name)
        return {"status": "ok", "version": release, "changed_files": changed,
                "rule_id": RULE_ID, "rule_hash": next(c for c in cards if c["id"] == RULE_ID)["version_hash"],
                "rulepack_sha256": staged_lock["sha256"], "previous_file_hashes": before_hashes,
                "previous_broker_card": old_broker, "preserved_unrelated_cards": preserved,
                "professional_signoff_status": "pending", "validation": validation}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=PROJECT / "rulepack")
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    receipt = register_threshold_rule(args.root)
    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        _write_json(args.receipt, receipt)
    print(json.dumps({key: receipt[key] for key in ("status", "version", "changed_files", "rule_id", "professional_signoff_status")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
