import json
import socket

import pytest

from mingjing.judge import Judge
from mingjing.models import VerificationResult
from scripts.acceptance_bench import DEFAULT_CASES, load_cases, run_acceptance


def test_frozen_gold_cases_do_not_contain_predictions():
    data = load_cases()
    assert {case["error_type"] for case in data["cases"]} >= {f"E{i}" for i in range(1, 10)}
    assert all("prediction" not in case and "result" not in case for case in data["cases"])
    assert data["annual_report"]["sha256"] == "595ba27f8900c41ceac19facbc2de5154b2c20bd71749b8affce1cc1f31dbeff"


def test_prefilled_predictions_are_rejected(tmp_path):
    data = load_cases()
    data["cases"][0]["prediction"] = "verified"
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="prefilled"):
        load_cases(path)


def test_metrics_come_from_actual_judge_calls(tmp_path, monkeypatch):
    calls = []

    def abstain(self, claim):
        calls.append(claim.claim_id)
        return VerificationResult(claim.claim_id, "abstained", error_type="E8", reason="test adapter")

    monkeypatch.setattr(Judge, "verify", abstain)
    report = run_acceptance(out=tmp_path, verify_source=False)
    cases = load_cases()["cases"]
    assert len(calls) == len(cases)
    assert report["prediction_counts"] == {"abstained": len(cases)}
    assert report["metrics"]["recall"] == 0
    assert report["failed_case_ids"]
    assert report["text_repair"]["trials"] == 0
    saved = [json.loads(line) for line in (tmp_path / "predictions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert all(item["prediction"] == "abstained" for item in saved)


def test_error_classification_failure_is_visible_even_when_detection_is_positive(tmp_path, monkeypatch):
    verify = Judge.verify

    def misclassify(self, claim):
        result = verify(self, claim)
        if claim.claim_id.startswith("B-E4") and result.status == "violated":
            result.error_type = "E1"
        return result

    monkeypatch.setattr(Judge, "verify", misclassify)
    report = run_acceptance(out=tmp_path, verify_source=False)
    cases = [item for item in report["records"] if item["id"].startswith("B-E4")]
    assert all(item["prediction"] == "violated" and not item["strict_match"] for item in cases)
    assert report["by_error_type"]["E4"]["recall"] == 1
    assert report["strict_by_error_type"]["E4"]["rate"] == 0


def test_no_source_verification_cannot_claim_acceptance_or_pdf_hit(tmp_path):
    report = run_acceptance(out=tmp_path, verify_source=False)
    assert report["source_verification"]["source_verified"] is False
    assert report["scoped_kernel_acceptance"] == "failed"
    assert report["pdf_evidence"]["cell"]["trials"] == 0
    assert report["full_crrv_bench_acceptance"] == "not_completed"
    assert any(item["set"] == "D" and item["status"] == "not_met" for item in report["unmet_requirements"])


def test_bad_correction_is_not_fixed_using_gold(tmp_path, monkeypatch):
    verify = Judge.verify

    def bad_correction(self, claim):
        result = verify(self, claim)
        if claim.claim_id == "B-E1-real-raw" and result.correction:
            result.correction = {
                "correct_value": "999999元", "source": ",".join(result.evidence),
                "calculation": "incorrect test correction", "basis": "test",
                "replacement_text": "并行科技2023年营业收入999999元。",
            }
        return result

    monkeypatch.setattr(Judge, "verify", bad_correction)
    run_acceptance(out=tmp_path, verify_source=False)
    repairs = json.loads((tmp_path / "repairs.json").read_text(encoding="utf-8"))
    repair = next(item for item in repairs if item["id"] == "B-E1-real-raw")
    assert repair["structured_result"]["observed"] == "999999"
    assert repair["structured_pass"] is False and repair["text_pass"] is False
    assert repair["qualified"] is False


def test_benchmark_execution_is_offline(tmp_path, monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("benchmark attempted network access")

    monkeypatch.setattr(socket, "create_connection", blocked)
    report = run_acceptance(out=tmp_path, verify_source=False)
    assert report["model_requests"] == 0 and report["model_cost"] == 0
    assert report["runtime_ms"] >= 0
