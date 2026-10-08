"""Regression corpus promoted from the first failed D evaluation.

These five issuers are DEVELOPMENT data now, never a fresh holdout score.
The original failed metrics and original manual gold file remain unchanged.
"""
import json
from pathlib import Path

import pytest

from mingjing.facts import FactStore
from mingjing.parser import build_evidence, extract_facts


def test_original_five_issuer_development_regression():
    gold_path = Path(__file__).resolve().parents[1] / "bench" / "D.jsonl"
    if not gold_path.exists():
        pytest.skip("Original development gold is not installed")
    gold = [json.loads(line) for line in gold_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if any(not Path(row["source_path"]).is_file() for row in gold):
        pytest.skip("External original PDF development corpus is not installed")
    stores = {}
    evidence_ids = {}
    for row in gold:
        source = row["source_path"]
        if source not in stores:
            stores[source] = FactStore(extract_facts(source, row["company"], "2023Y", row["publish_date"]))
            evidence_ids[source] = {e.evidence_id for e in build_evidence(source)}
        fact = stores[source].query(row["company"], row["account"], row["period"], row["scope"], row["nr"], row["publish_date"])
        assert fact is not None, row["id"]
        assert str(fact.value) == row["value"], row["id"]
        assert set(fact.evidence) <= evidence_ids[source], row["id"]
