"""Pure Decimal set logic for quoted disclosure predicates, not point facts.

Missing endpoints represent an unbounded side. They are never materialised as
Infinity, zero, 100%, or a guessed numeric value. Identity, provenance and
publication-time checks belong to the trusted caller and precede this module.
The ordinary rounded-value/hedge compatibility policy remains independent.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json

from .rules import RuleError, evaluate_rule


RULE_ID = "SOURCE-threshold-entailment"
RULE_VERSION = "0.6.1"
RULE_FORMULA = (
    "(lower_entails == ONE and upper_entails == ONE) or "
    "source_below_claim == ONE or claim_below_source == ONE"
)
RULE_INPUTS = {
    "lower_entails": "boolean", "upper_entails": "boolean",
    "source_below_claim": "boolean", "claim_below_source": "boolean",
}


@dataclass(frozen=True)
class ThresholdAssertion:
    """A nonempty one-dimensional set; bounds use canonical base units."""

    lower: Decimal | None = None
    upper: Decimal | None = None
    lower_closed: bool = False
    upper_closed: bool = False

    def __post_init__(self):
        if type(self.lower_closed) is not bool or type(self.upper_closed) is not bool:
            raise ValueError("Threshold endpoint inclusivity must be boolean")
        for name, value, closed in (("lower", self.lower, self.lower_closed),
                                    ("upper", self.upper, self.upper_closed)):
            if value is None:
                if closed:
                    raise ValueError("An absent threshold endpoint cannot be closed")
            elif not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(f"Threshold {name} must be a finite Decimal or None")
        if self.lower is not None and self.upper is not None:
            if self.lower > self.upper or (self.lower == self.upper and not (
                    self.lower_closed and self.upper_closed)):
                raise ValueError("Threshold assertion is empty")

    @classmethod
    def from_operator(cls, operator: str, value: Decimal) -> "ThresholdAssertion":
        # The caller, never the model, has already parsed and scaled the bound.
        operators = {
            "gt": lambda: cls(lower=value),
            "ge": lambda: cls(lower=value, lower_closed=True),
            "lt": lambda: cls(upper=value),
            "le": lambda: cls(upper=value, upper_closed=True),
            "eq": lambda: cls(value, value, True, True),
        }
        if not isinstance(operator, str) or operator not in operators:
            raise ValueError("Threshold operator is not registered")
        if not isinstance(value, Decimal) or not value.is_finite():
            raise ValueError("Threshold operand must be a finite Decimal")
        return operators[operator]()

    @classmethod
    def from_dict(cls, payload: dict) -> "ThresholdAssertion":
        keys = {"lower", "upper", "lower_closed", "upper_closed"}
        if not isinstance(payload, dict) or set(payload) != keys:
            raise ValueError("Threshold assertion schema is invalid")
        def endpoint(name):
            value = payload[name]
            if value is None:
                return None
            if not isinstance(value, str):
                raise ValueError("Serialized threshold endpoints must be Decimal strings")
            try:
                return Decimal(value)
            except ArithmeticError as exc:
                raise ValueError("Serialized threshold endpoint is invalid") from exc
        return cls(endpoint("lower"), endpoint("upper"),
                   payload["lower_closed"], payload["upper_closed"])

    def to_dict(self) -> dict:
        return {
            "lower": str(self.lower) if self.lower is not None else None,
            "upper": str(self.upper) if self.upper is not None else None,
            "lower_closed": self.lower_closed, "upper_closed": self.upper_closed,
        }

    def _lower_entails(self, claim: "ThresholdAssertion") -> bool:
        if claim.lower is None:
            return True
        if self.lower is None:
            return False
        return (self.lower > claim.lower or self.lower == claim.lower
                and (not self.lower_closed or claim.lower_closed))

    def _upper_entails(self, claim: "ThresholdAssertion") -> bool:
        if claim.upper is None:
            return True
        if self.upper is None:
            return False
        return (self.upper < claim.upper or self.upper == claim.upper
                and (not self.upper_closed or claim.upper_closed))

    def subset_of(self, claim: "ThresholdAssertion") -> bool:
        _assert_typed(claim)
        return self._lower_entails(claim) and self._upper_entails(claim)

    def below(self, other: "ThresholdAssertion") -> bool:
        """True iff this entire set lies strictly below the other entire set."""
        _assert_typed(other)
        if self.upper is None or other.lower is None:
            return False
        return (self.upper < other.lower or self.upper == other.lower
                and not (self.upper_closed and other.lower_closed))

    def disjoint_from(self, other: "ThresholdAssertion") -> bool:
        _assert_typed(other)
        return self.below(other) or other.below(self)


def _assert_typed(value):
    if not isinstance(value, ThresholdAssertion):
        raise TypeError("Expected a ThresholdAssertion, never a candidate point fact")


def _validate_rule(card):
    # This is an operation allowlist, not a substitute for the caller's frozen
    # rulepack lock. A free expression cannot become executable by changing ID.
    if not isinstance(card, dict):
        raise RuleError("An explicitly frozen threshold rule card is required")
    checksum = hashlib.sha256(json.dumps(
        {k: v for k, v in card.items() if k != "version_hash"}, ensure_ascii=False,
        sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    if (card.get("id") != RULE_ID or card.get("version_hash") != checksum
            or card.get("formula") != RULE_FORMULA or card.get("inputs") != RULE_INPUTS
            or card.get("dimension") != "boolean" or card.get("positive_inputs") != []
            or card.get("constants") != {"ONE": {"value": "1", "dimension": "ratio"}}
            or card.get("operation") != "source_predicate_entailment.v1"):
        raise RuleError("Threshold rule is missing, modified, or not authorized for this operation")


@dataclass(frozen=True)
class ThresholdEvaluation:
    status: str
    relation: str
    source: ThresholdAssertion
    claim: ThresholdAssertion
    operands: dict
    rule_id: str
    rule_version: str
    rule_hash: str
    professional_signoff_status: str

    def to_dict(self) -> dict:
        return {
            "status": self.status, "relation": self.relation,
            "source_assertion": self.source.to_dict(), "claim_assertion": self.claim.to_dict(),
            "operands": dict(self.operands), "rule_id": self.rule_id,
            "rule_version": self.rule_version, "rule_hash": self.rule_hash,
            "professional_signoff_status": self.professional_signoff_status,
            "verification_basis": "source_predicate_entailment",
            "establishes_exact_value": False,
        }


def evaluate_threshold_assertion(source: ThresholdAssertion, claim: ThresholdAssertion,
                                 *, rule_card: dict) -> ThresholdEvaluation:
    """Entailment => verified; disjoint => violated; overlap => unknown.

    Merely overlapping possible values never verifies a predicate. A source
    greater than 70% does not establish either 71% or a greater-than-80% claim.
    No I/O, searches, model requests, point Facts, or identity guesses occur.
    """
    _assert_typed(source)
    _assert_typed(claim)
    _validate_rule(rule_card)
    operands = {
        "lower_entails": int(source._lower_entails(claim)),
        "upper_entails": int(source._upper_entails(claim)),
        "source_below_claim": int(source.below(claim)),
        "claim_below_source": int(claim.below(source)),
    }
    decidable = evaluate_rule(rule_card, operands)
    entails = bool(operands["lower_entails"] and operands["upper_entails"])
    disjoint = bool(operands["source_below_claim"] or operands["claim_below_source"])
    if type(decidable) is not bool or decidable != (entails or disjoint):
        raise RuleError("Threshold rule and set relation disagree")
    status, relation = (("verified", "entails") if entails else
                        ("violated", "disjoint") if disjoint else ("unknown", "overlap"))
    return ThresholdEvaluation(status, relation, source, claim, operands, RULE_ID,
                               rule_card["version"], rule_card["version_hash"],
                               "approved" if rule_card.get("approved_by") else "pending")
