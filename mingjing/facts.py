from __future__ import annotations

"""Fact-store loading and deterministic version selection."""

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from datetime import date

from .models import Fact, FactQueryOutcome
from .decimal_math import fact_interval, resolution


def _text(value: object, default: str = "") -> str:
    return default if value is None else str(value)


def _evidence_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if item)
    return ()


def fact_from_dict(data: dict) -> Fact:
    if "value" not in data:
        raise ValueError("事实缺少 value；不能将缺失值转换为零")
    try:
        value = Decimal(str(data["value"]))
    except (InvalidOperation, ValueError):
        raise ValueError("事实 value 不是有效的 Decimal 数值") from None
    if not value.is_finite():
        raise ValueError("事实 value 必须为有限数值")
    try:
        decimals = max(0, int(data.get("decimals", 2)))
    except (TypeError, ValueError):
        decimals = 2
    evidence = _evidence_tuple(data.get("evidence", ()))
    return Fact(
        fact_id=_text(data.get("fact_id")), company=_text(data.get("company")),
        account=_text(data.get("account")), period=_text(data.get("period")),
        scope=_text(data.get("scope"), "合并"), nr=_text(data.get("nr"), "未扣非"),
        version=_text(data.get("version"), "原披露"), source_doc=_text(data.get("source_doc")),
        publish_date=_text(data.get("publish_date")), value=value, decimals=decimals,
        display_unit=_text(data.get("display_unit"), "元"),
        evidence=evidence, label=_text(data.get("label")),
        source_path=_text(data.get("source_path")),
        source_type=_text(data.get("source_type"), "structured"),
        table_label=_text(data.get("table_label")),
        column_label=_text(data.get("column_label")),
        parse_conf=_text(data.get("parse_conf"), "1"),
        extractor_version=_text(data.get("extractor_version"), "legacy-structured"),
        version_group=_text(data.get("version_group")),
        dimension_kind=_text(data.get("dimension_kind"), "total"),
        dimension_id=_text(data.get("dimension_id")),
        currency=_text(data.get("currency"), "CNY"),
        definition_id=_text(data.get("definition_id")),
        source_table_id=_text(data.get("source_table_id")),
        source_table_evidence=_evidence_tuple(data.get("source_table_evidence", ())),
        source_scope_evidence=_evidence_tuple(data.get("source_scope_evidence", ())),
        source_row_index=data.get('source_row_index', 0),
        source_relation=_text(data.get('source_relation'), 'value'),
    )


def _dedup_key(fact: Fact) -> tuple:
    return (fact.company, fact.account, fact.period, fact.scope, fact.nr, fact.value,
            tuple(fact.evidence), fact.source_doc, fact.publish_date, fact.version,
            fact.dimension_kind, fact.dimension_id, fact.currency, fact.definition_id,
            fact.source_table_id, fact.source_row_index, fact.source_relation)


def _version_rank(version: str) -> int:
    value = (version or "").lower()
    if any(token in value for token in ("调整前", "重述前", "更正前", "before_adjust", "before_restat")):
        return 10
    if any(token in value for token in ("更正", "重述", "重编", "修订", "restat", "correct", "revis")):
        return 40
    if any(token in value for token in ("调整", "adjust", "updated", "更新")):
        return 30
    if any(token in value for token in ("最终", "final")):
        return 20
    if any(token in value for token in ("原披露", "原始", "original", "initial")):
        return 10
    return 0


def _date_key(value: str) -> tuple[int, str]:
    raw = (value or "").strip()
    try:
        valid = date.fromisoformat(raw).isoformat() == raw
    except ValueError:
        valid = False
    return (1 if valid else 0, raw)


class FactStore:
    def __init__(self, facts: list[Fact] | None = None):
        self.facts: list[Fact] = []
        for fact in facts or []:
            self.add(fact)

    @classmethod
    def from_json(cls, path: str | Path) -> "FactStore":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload = payload.get("facts", [])
        if not isinstance(payload, list):
            return cls()
        return cls([fact_from_dict(item) for item in payload if isinstance(item, dict)])

    @classmethod
    def from_corpus(cls, corpus: str | Path) -> "FactStore":
        # New tasks share the extractor admission policy. from_json remains
        # the frozen-snapshot reader used by historical replay.
        from .parser import load_or_extract_facts
        return cls(load_or_extract_facts(corpus))

    def add(self, fact: Fact) -> None:
        key = _dedup_key(fact)
        if any(_dedup_key(existing) == key for existing in self.facts):
            return
        self.facts.append(fact)

    def _matches(self, company: str, account: str, period: str, scope: str, nr: str, *, dimension_kind='total', dimension_id='', currency='CNY', definition_id='', source_relation='value') -> list[Fact]:
        from .dimensions import validate_fact_dimensions, dimension_ids_compatible
        validate_fact_dimensions(dimension_kind, dimension_id, currency, definition_id)
        def canonical(key, qualifier):
            return 'NP_PARENT_ADJ' if key in {'NP_PARENT', 'NP_PARENT_ADJ'} and qualifier == '扣非' else key
        if source_relation not in {'value', 'yoy'}:
            raise ValueError('未知来源单元格关系')
        return [fact for fact in self.facts if fact.company == company
                and fact.source_relation == source_relation
                and canonical(fact.account, fact.nr) == canonical(account, nr)
                and fact.period == period and fact.scope == scope and fact.nr == nr
                and fact.currency == currency and fact.definition_id == definition_id
                and fact.dimension_kind == dimension_kind
                and (dimension_ids_compatible(dimension_kind, dimension_id, fact.dimension_id)
                     if dimension_kind != 'total' else fact.dimension_id == dimension_id)]

    def query_outcome(self, company: str, account: str, period: str, scope: str = "合并", nr: str = "未扣非", cutoff: str | None = None, strategy: str = "latest_as_of", source_doc: str | None = None, min_confidence: str = "0.95", *, dimension_kind='total', dimension_id='', currency='CNY', definition_id='', source_relation='value') -> FactQueryOutcome:
        strategy = {"latest_disclosure": "latest_as_of", "specified_report": "pinned"}.get(strategy, strategy)
        if strategy not in {"latest_as_of", "same_report", "pinned"}:
            raise ValueError("未知取数策略")
        confidence = Decimal(str(min_confidence))
        if not confidence.is_finite() or not 0 <= confidence <= 1:
            raise ValueError("事实置信度阈值须为零至一")
        if cutoff and not _date_key(cutoff)[0]:
            raise ValueError("研报日期须为有效 ISO 日期")
        matches = self._matches(company, account, period, scope, nr, dimension_kind=dimension_kind, dimension_id=dimension_id, currency=currency, definition_id=definition_id, source_relation=source_relation)
        if strategy in {"same_report", "pinned"}:
            if not source_doc:
                return FactQueryOutcome("source_required", reason="指定报告或同报告比较策略缺少来源文件编号")
            reference = source_doc.removeprefix("evidence:")
            matches = [fact for fact in matches if reference in {fact.source_doc, fact.source_path} or reference in fact.evidence]
        if cutoff:
            visible = [fact for fact in matches if _date_key(fact.publish_date)[0] and fact.publish_date <= cutoff]
            if not visible and any(not _date_key(fact.publish_date)[0] for fact in matches):
                return FactQueryOutcome("unknown_date", reason="来源公告日期缺失或无效，无法证明截至研报日已披露")
            if not visible and matches:
                return FactQueryOutcome("future_only", reason="截止报告日期缺少匹配公司、科目、期间及口径的事实")
            matches = visible
        if not matches:
            return FactQueryOutcome("missing", reason="截止报告日期缺少匹配公司、科目、期间及口径的事实")
        # Anonymous customer letters are local to a disclosure. Even a corpus
        # containing a single report cannot prove that an unanchored draft
        # customer label refers to that report's customer. Only a declared,
        # source-bound strategy may select that identity.
        from .dimensions import anonymous_customer_label
        if (dimension_kind == "customer" and anonymous_customer_label(dimension_id)
                and strategy == "latest_as_of"):
            multiple_sources = len({fact.source_doc or fact.source_path for fact in matches}) > 1
            return FactQueryOutcome(
                "ambiguous_source" if multiple_sources else "source_required",
                reason="匿名客户标识仅在原报告内有效；须以 pinned 或 same_report 策略指定有原文引用依据的来源",
                evidence=tuple(sorted({identifier for fact in matches for identifier in fact.evidence})),
                candidate_fact_ids=tuple(sorted(fact.fact_id for fact in matches)),
            )
        # Disclosure time and version are the only mechanisms for selecting
        # different truths. Conflicting values within that version are not
        # resolved by insertion order or a conveniently small fact id.
        newest_date = max(_date_key(f.publish_date) for f in matches)
        dated = [f for f in matches if _date_key(f.publish_date) == newest_date]
        newest_version = max(_version_rank(f.version) for f in dated)
        versioned = [f for f in dated if _version_rank(f.version) == newest_version]
        evidence = tuple(sorted({identifier for fact in versioned for identifier in fact.evidence}))
        identifiers = tuple(sorted(fact.fact_id for fact in versioned))
        def rejected(status, reason):
            return FactQueryOutcome(status, reason=reason, evidence=evidence, candidate_fact_ids=identifiers)
        if strategy == "pinned" and len({f.source_doc or f.source_path for f in versioned}) > 1:
            return rejected("ambiguous_source", "指定报告不唯一，无法确认真值")
        if any(Decimal(f.parse_conf) < confidence for f in versioned):
            return rejected("low_confidence", "事实解析置信度不足，不能进入判定")
        # Values rounded in different display units are compatible only when
        # their disclosed intervals overlap. A disagreement means abstention.
        try:
            resolutions = [resolution(f.display_unit, f.decimals) for f in versioned]
            intervals = [fact_interval(fact) for fact in versioned]
        except ValueError as exc:
            return rejected("invalid_interval", str(exc))
        for index, fact in enumerate(versioned):
            if any(resolutions[index] == resolutions[other] and fact.value != versioned[other].value
                   for other in range(index)):
                return rejected("conflict", "同一披露版本存在冲突事实，无法确认真值")
        intersection = intervals[0]
        for interval in intervals[1:]:
            intersection = intersection.intersection(interval)
            if intersection is None:
                return rejected("conflict", "同一披露版本存在冲突事实，无法确认真值")
        ranks = {"primary_statement": 3, "financial_summary": 2, "structured": 2}
        selected = min(versioned, key=lambda f: (-ranks.get(f.source_type, 0),
                                                resolution(f.display_unit, f.decimals),
                                                tuple(f.evidence), f.source_doc, f.fact_id))
        return FactQueryOutcome("found", fact=selected, evidence=evidence, candidate_fact_ids=identifiers)

    def query(self, company: str, account: str, period: str, scope: str = "合并", nr: str = "未扣非", cutoff: str | None = None, strategy: str = "latest_as_of", source_doc: str | None = None, min_confidence: str = "0.95", *, dimension_kind='total', dimension_id='', currency='CNY', definition_id='') -> Fact | None:
        return self.query_outcome(company, account, period, scope, nr, cutoff,
                                  strategy, source_doc, min_confidence, dimension_kind=dimension_kind, dimension_id=dimension_id, currency=currency, definition_id=definition_id).fact

    def versions(self, company: str, account: str, period: str, scope: str = "合并", nr: str = "未扣非", *, dimension_kind='total', dimension_id='', currency='CNY', definition_id='') -> list[Fact]:
        matches = self._matches(company, account, period, scope, nr, dimension_kind=dimension_kind, dimension_id=dimension_id, currency=currency, definition_id=definition_id)
        return sorted(matches, key=lambda f: (_date_key(f.publish_date), _version_rank(f.version), f.fact_id), reverse=True)

    def query_issue(self, company: str, account: str, period: str, scope: str = "合并", nr: str = "未扣非", cutoff: str | None = None, strategy: str = "latest_as_of", source_doc: str | None = None, min_confidence: str = "0.95", *, dimension_kind='total', dimension_id='', currency='CNY', definition_id='') -> str | None:
        """Explain an unresolved lookup without returning a guessed value."""
        return self.query_outcome(company, account, period, scope, nr, cutoff,
                                  strategy, source_doc, min_confidence, dimension_kind=dimension_kind, dimension_id=dimension_id, currency=currency, definition_id=definition_id).reason

    def to_list(self) -> list[dict]:
        return [fact.to_dict() for fact in self.facts]


def load_index_facts(corpus_root: str | Path) -> FactStore:
    """Load user-supplied facts; never infer values from an index alone."""
    return FactStore.from_corpus(corpus_root)
