import json
from pathlib import Path

import pytest

from scripts.holdout_acceptance import ROOT, evaluate_holdout, seal_holdout


def test_sealed_holdout_and_next_cohort_are_disjoint(tmp_path):
    corpus = tmp_path / "corpus"
    for ticker in range(920000, 920010):
        path = corpus / f"{ticker}_公司" / "年报" / "2024-04-01_2023年年度报告.pdf"
        path.parent.mkdir(parents=True)
        path.write_bytes(f"PDF-fixture-{ticker}".encode())
    first = seal_holdout(corpus, tmp_path / "D-seal.json", 0)
    second = seal_holdout(corpus, tmp_path / "D2-seal.json", 5)
    assert len(first["reports"]) == len(second["reports"]) == 5
    assert not ({item["ticker"] for item in first["reports"]} & {item["ticker"] for item in second["reports"]})
    assert first["source_fingerprint"] == second["source_fingerprint"]
    with pytest.raises(ValueError, match="禁止覆盖"):
        seal_holdout(corpus, tmp_path / "D-seal.json", 0)


def test_existing_holdout_metrics_cannot_be_overwritten(tmp_path):
    out = tmp_path / "results"
    out.mkdir()
    metric = out / "holdout_metrics.json"
    metric.write_text("original sealed run", encoding="utf-8")
    with pytest.raises(ValueError, match="禁止覆盖"):
        evaluate_holdout(tmp_path / "unused-seal", tmp_path / "unused-gold", out)
    assert metric.read_text(encoding="utf-8") == "original sealed run"


def test_first_holdout_gold_has_original_pdf_provenance_and_no_predictions():
    rows = [json.loads(line) for line in (ROOT / "bench" / "D.jsonl").read_text(encoding="utf-8").splitlines()]
    sealed = json.loads((ROOT / "bench" / "holdout_seal_manifest.json").read_text(encoding="utf-8"))
    reports = {item["ticker"]: item for item in sealed["reports"]}
    assert len(reports) == 5 and len(rows) == 30
    assert "920493" not in reports
    for row in rows:
        assert "prediction" not in row
        assert row["gold_source"] == "manual_original_pdf"
        assert row["document_sha256"] == reports[row["ticker"]]["sha256"]
        assert row["reviewed_at"] >= sealed["sealed_at"]
        assert len(row["bbox"]) == 4 and row["cell_text"]
        assert not row["independent_second_review"]


def test_second_holdout_is_a_new_sealed_company_cohort():
    first = json.loads((ROOT / "bench" / "holdout_seal_manifest.json").read_text(encoding="utf-8"))
    second = json.loads((ROOT / "bench" / "holdout2_seal_manifest.json").read_text(encoding="utf-8"))
    rows = [json.loads(line) for line in (ROOT / "bench" / "D2.jsonl").read_text(encoding="utf-8").splitlines()]
    assert not ({item["ticker"] for item in first["reports"]} & {item["ticker"] for item in second["reports"]})
    assert len(rows) == 30
    assert all(row["reviewed_at"] >= second["sealed_at"] and "prediction" not in row for row in rows)
    assert all(row["gold_source"] == "manual_original_pdf" for row in rows)
