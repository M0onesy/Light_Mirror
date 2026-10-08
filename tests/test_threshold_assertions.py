"""Disclosure predicates prove set entailment, never an invented point value."""
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from mingjing.decimal_math import rounding_interval
from mingjing.rules import RuleError, evaluate_rule, validate_rulepack
from mingjing.thresholds import (
    RULE_ID, ThresholdAssertion, evaluate_threshold_assertion,
)
from scripts.register_threshold_rule import (
    BROKER_ID, BROKER_FORMULA, register_threshold_rule, threshold_card,
)
from scripts.rulepack_build_guard import RulepackBuildError
from scripts.extend_coverage_rules import refresh_rulepack_lock


ROOT = Path(__file__).resolve().parents[1]


def predicate(operator, amount):
    return ThresholdAssertion.from_operator(operator, Decimal(amount))


@pytest.fixture
def card():
    snapshot = ROOT / "rulepack/sources/MJ-THRESHOLD-METHOD.md"
    quote = next(line for line in snapshot.read_text("utf-8").splitlines()
                 if line.startswith("来源谓词蕴含规则："))
    return threshold_card(quote)


@pytest.mark.parametrize("source_op,source_value,claim_op,claim_value,status", [
    ("gt", ".70", "gt", ".70", "verified"),
    ("gt", ".70", "gt", ".80", "unknown"),
    ("gt", ".70", "lt", ".60", "violated"),
    ("gt", ".70", "ge", ".70", "verified"),
    ("ge", ".70", "gt", ".70", "unknown"),
    ("gt", ".70", "le", ".70", "violated"),
    ("ge", ".70", "lt", ".70", "violated"),
    ("ge", ".70", "le", ".70", "unknown"),
    ("gt", ".70", "lt", ".80", "unknown"),
    ("gt", ".70", "eq", ".71", "unknown"),
    ("gt", ".70", "eq", ".70", "violated"),
    ("lt", ".70", "le", ".70", "verified"),
    ("le", ".70", "lt", ".70", "unknown"),
    ("lt", ".70", "gt", ".70", "violated"),
    ("le", ".70", "ge", ".70", "unknown"),
    ("eq", ".70", "eq", ".70", "verified"),
    ("eq", ".70", "gt", ".70", "violated"),
    ("eq", ".70", "ge", ".70", "verified"),
    ("eq", ".75", "gt", ".70", "verified"),
    ("gt", "-.70", "ge", "-.70", "verified"),
    ("ge", "0", "gt", "0", "unknown"),
    ("gt", "0", "le", "0", "violated"),
])
def test_official_predicate_entailment_and_adjacent_counterexamples(
        card, source_op, source_value, claim_op, claim_value, status):
    source, claim = predicate(source_op, source_value), predicate(claim_op, claim_value)
    result = evaluate_threshold_assertion(source, claim, rule_card=card)
    assert result.status == status
    assert result.rule_id == RULE_ID
    assert result.professional_signoff_status == "pending"
    output = result.to_dict()
    assert output["verification_basis"] == "source_predicate_entailment"
    assert output["establishes_exact_value"] is False
    assert "expected_value" not in output and "fact" not in output


def test_strict_bound_does_not_receive_hedge_upper_or_100_percent(card):
    source = predicate("gt", ".70")
    hedge = ThresholdAssertion(Decimal(".70"), Decimal(".714"), False, True)
    assert evaluate_threshold_assertion(source, hedge, rule_card=card).status == "unknown"
    assert source.upper is None
    assert source.to_dict()["upper"] is None
    assert evaluate_threshold_assertion(source, predicate("le", "1"), rule_card=card).status == "unknown"


@pytest.mark.parametrize("source_value,claim_value,status", [
    (".70", ".70", "unknown"),
    (".75", ".70", "verified"),
    (".75", ".75", "unknown"),
    (".65", ".70", "violated"),
])
def test_rounded_point_source_requires_predicate_entailment(card, source_value, claim_value, status):
    rounded = rounding_interval(Decimal(source_value), "%", 0)
    source = ThresholdAssertion(rounded.lower, rounded.upper, rounded.lower_closed, rounded.upper_closed)
    result = evaluate_threshold_assertion(source, predicate("gt", claim_value), rule_card=card)
    assert result.status == status


@pytest.mark.parametrize("source,claim,status", [
    (ThresholdAssertion(Decimal(".7"), Decimal(".8"), True, False), predicate("ge", ".7"), "verified"),
    (ThresholdAssertion(Decimal(".7"), Decimal(".8"), True, False), predicate("gt", ".7"), "unknown"),
    (ThresholdAssertion(Decimal(".7"), Decimal(".8"), True, False), predicate("lt", ".8"), "verified"),
    (ThresholdAssertion(Decimal(".7"), Decimal(".8"), True, True), predicate("lt", ".8"), "unknown"),
    (ThresholdAssertion(Decimal(".7"), Decimal(".8"), True, False), predicate("ge", ".8"), "violated"),
    (ThresholdAssertion(), predicate("gt", ".7"), "unknown"),
    (predicate("gt", ".7"), ThresholdAssertion(), "verified"),
])
def test_bounded_and_unbounded_endpoint_edges(card, source, claim, status):
    assert evaluate_threshold_assertion(source, claim, rule_card=card).status == status


def test_serialized_assertions_and_evaluation_are_replay_stable(card):
    source, claim = predicate("gt", ".70"), predicate("gt", ".70")
    expected = evaluate_threshold_assertion(source, claim, rule_card=card).to_dict()
    assert ThresholdAssertion.from_dict(json.loads(json.dumps(source.to_dict()))) == source
    with localcontext() as context:
        context.prec = 2
        actual = evaluate_threshold_assertion(ThresholdAssertion.from_dict(source.to_dict()),
                                              ThresholdAssertion.from_dict(claim.to_dict()), rule_card=card)
    assert actual.to_dict() == expected


@pytest.mark.parametrize("kwargs", [
    {"lower": Decimal("NaN")}, {"upper": Decimal("Infinity")},
    {"lower": ".7"}, {"lower": 0.7}, {"lower_closed": True},
    {"upper_closed": True}, {"lower_closed": 1},
    {"lower": Decimal(1), "upper": Decimal(0)},
    {"lower": Decimal(1), "upper": Decimal(1)},
    {"lower": Decimal(1), "upper": Decimal(1), "lower_closed": True},
])
def test_invalid_or_empty_source_assertion_rejected(kwargs):
    with pytest.raises(ValueError):
        ThresholdAssertion(**kwargs)


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), ".7", .7, None, True])
def test_operator_constructor_rejects_non_decimal_and_nonfinite(value):
    with pytest.raises(ValueError):
        ThresholdAssertion.from_operator("gt", value)


@pytest.mark.parametrize("change", [
    {"lower": .7}, {"lower": "NaN"}, {"lower_closed": "false"},
    {"upper_closed": 0}, {"lower": "not-a-number"}, {"candidate_value": "999999"},
])
def test_replay_schema_cannot_smuggle_candidate_values(change):
    value = {**predicate("gt", ".7").to_dict(), **change}
    with pytest.raises(ValueError):
        ThresholdAssertion.from_dict(value)


def test_rule_card_required_and_free_operation_rejected(card):
    source = predicate("gt", ".7")
    with pytest.raises(TypeError):
        evaluate_threshold_assertion(source, source)
    with pytest.raises(RuleError):
        evaluate_threshold_assertion(source, source, rule_card=None)
    with pytest.raises(TypeError):
        evaluate_threshold_assertion({"candidate_value": "999999"}, source, rule_card=card)
    for field, wrong in [("id", "FIN-range"), ("formula", "lower_entails == ONE"),
                         ("operation", "model_guess"), ("inputs", {"candidate_value": "ratio"}),
                         ("constants", {"ONE": {"value": "2", "dimension": "ratio"}})]:
        forged = {**card, field: wrong}
        forged["version_hash"] = hashlib.sha256(json.dumps(
            {k: v for k, v in forged.items() if k != "version_hash"}, ensure_ascii=False,
            sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        with pytest.raises(RuleError):
            evaluate_threshold_assertion(source, source, rule_card=forged)


def _rulecopy(tmp_path):
    root = tmp_path / "rulepack"
    shutil.copytree(ROOT / "rulepack", root, ignore=shutil.ignore_patterns("__pycache__", ".rulepack-build.lock"))
    return root


def test_incremental_registration_preserves_other_cards_and_is_idempotent(tmp_path):
    root = _rulecopy(tmp_path)
    before = json.loads((root / "cards.json").read_text("utf-8"))
    before_caliber = (root / "caliber.json").read_bytes()
    before_sources = {p.name: p.read_bytes() for p in (root / "sources").iterdir()
                      if p.name != "MJ-COVERAGE-METHOD.md"}
    receipt = register_threshold_rule(root)
    after = json.loads((root / "cards.json").read_text("utf-8"))
    for card in before:
        if card["id"] != BROKER_ID:
            assert next(item for item in after if item["id"] == card["id"]) == card
    assert (root / "caliber.json").read_bytes() == before_caliber
    assert {p.name: p.read_bytes() for p in (root / "sources").iterdir()
            if p.name != "MJ-COVERAGE-METHOD.md"} == before_sources
    assert validate_rulepack(root)["status"] == "ok"
    assert receipt["professional_signoff_status"] == "pending"
    raw = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    repeated = register_threshold_rule(root)
    assert repeated["changed_files"] == []
    assert {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()} == raw


def test_registration_keeps_newer_release_and_unrelated_human_approval(tmp_path):
    root = _rulecopy(tmp_path)
    cards = json.loads((root / "cards.json").read_text("utf-8"))
    untouched = next(item for item in cards if item["id"] == "FIN-current-ratio")
    untouched.update(approved_by=["reviewer-1", "reviewer-2"], review_status="signed", version="0.10.0")
    untouched["version_hash"] = hashlib.sha256(json.dumps(
        {k: v for k, v in untouched.items() if k != "version_hash"}, ensure_ascii=False,
        sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    (root / "cards.json").write_text(json.dumps(cards, ensure_ascii=False), encoding="utf-8")
    sources = json.loads((root / "sources.json").read_text("utf-8"))
    sources["version"] = "0.10.0"
    sources.pop("version_hash", None)
    sources["version_hash"] = hashlib.sha256(json.dumps(sources, ensure_ascii=False,
        sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    (root / "sources.json").write_text(json.dumps(sources, ensure_ascii=False), encoding="utf-8")
    refresh_rulepack_lock(root)
    result = register_threshold_rule(root)
    assert result["version"] == "0.10.0"
    after = json.loads((root / "cards.json").read_text("utf-8"))
    assert next(item for item in after if item["id"] == untouched["id"]) == untouched


def test_unknown_existing_threshold_operation_fails_without_publishing(tmp_path):
    root = _rulecopy(tmp_path)
    register_threshold_rule(root)
    cards = json.loads((root / "cards.json").read_text("utf-8"))
    card = next(item for item in cards if item["id"] == RULE_ID)
    card["formula"] = "lower_entails == ONE"
    (root / "cards.json").write_text(json.dumps(cards, ensure_ascii=False), encoding="utf-8")
    raw = {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    with pytest.raises(RulepackBuildError, match="differs"):
        register_threshold_rule(root)
    assert {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()} == raw


def test_broker_composition_subtracts_all_disjoint_displayed_rows_and_missing_is_not_zero(tmp_path):
    root = _rulecopy(tmp_path)
    register_threshold_rule(root)
    card = next(c for c in json.loads((root / "cards.json").read_text("utf-8")) if c["id"] == BROKER_ID)
    assert card["formula"] == BROKER_FORMULA
    operands = {"CURRENT_ASSETS": "100", "CASH_SOURCE": "20", "AR_NOTES": "10",
                "OTHER_RECEIVABLES": "7", "PREPAYMENTS": "8", "INVENTORY": "5"}
    assert evaluate_rule(card, operands) == Decimal("50")
    assert evaluate_rule(card, {**operands, "PREPAYMENTS": None}) is None
    assert evaluate_rule(card, {key: value for key, value in operands.items() if key != "OTHER_RECEIVABLES"}) is None
    assert card["approved_by"] is None
