import json
import socket
import importlib
import shutil
import sys
import subprocess
from pathlib import Path
from decimal import Decimal, localcontext

import pytest

from mingjing.audit import AuditLog, digest, finalize_manifest, kernel_fingerprint, model_manifest, _stable_code_data
from mingjing.facts import FactStore
from mingjing.judge import Judge
from mingjing.models import AtomicClaim, Fact
from mingjing.replay import replay_run
from mingjing.report import write_reports


@pytest.fixture
def frozen_run(tmp_path):
    draft = tmp_path / "原始研报.txt"
    draft.write_text("示例公司2023年营业收入120元，同比增长20%。", encoding="utf-8")
    report = tmp_path / "原始年报.txt"
    report.write_text("2023年120元，2022年100元", encoding="utf-8")
    run = tmp_path / "run"
    run.mkdir()
    facts = [
        Fact("fact-2023", "示例公司", "REVENUE", "2023Y", value=Decimal("120"), source_path=str(report), evidence=("evidence-page-1",), publish_date="2024-04-01"),
        Fact("fact-2022", "示例公司", "REVENUE", "2022Y", value=Decimal("100"), source_path=str(report), evidence=("evidence-page-2",), publish_date="2024-04-01"),
    ]
    text = draft.read_text(encoding="utf-8")
    claims = [
        AtomicClaim("claim-1", text, 0, len(text), company="示例公司", account="REVENUE", period="2023Y", candidate_value="120", unit="元"),
        AtomicClaim("claim-2", text, 0, len(text), company="示例公司", account="REVENUE", period="2023Y", candidate_value="0.2", unit="%", relation="yoy", base_period="2022Y"),
    ]
    judge = Judge(FactStore(facts), "2024-04-22")
    results = []
    for claim in claims:
        result = judge.verify(claim).to_dict()
        result.update(claim_text=claim.text, source_span={"start": claim.start, "end": claim.end})
        results.append(result)
    summary = {key: sum(item["status"] == key for item in results) for key in ("verified", "violated", "abstained")}
    payload = {"run_id": "test-run", "draft": str(draft), "company": "示例公司", "report_date": "2024-04-22", "summary": summary, "claims": [claim.to_dict() for claim in claims], "facts": [fact.to_dict() for fact in facts], "results": results}
    for name, value in (("claims", payload["claims"]), ("facts", payload["facts"]), ("results", payload)):
        (run / f"{name}.json").write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    audit = AuditLog(run, "test-run")
    audit.emit("model_call", status="success", model="configured-model", actual_model="provider-model", semantic_claims=1, mapped_claims=2, chunks=1)
    audit.emit("judgement", input=payload["claims"], output=results)
    write_reports(payload, run)
    audit.session("run_complete", summary=summary)
    finalize_manifest(run, "test-run", [str(draft), str(report), str(report)], "agent", {"summary": summary})
    return run, draft


def test_three_replays_execute_judge_without_network(frozen_run, monkeypatch):
    run, _ = frozen_run
    calls = []
    actual_verify = Judge.verify

    def observe(frame, event, _arg):
        if event == 'call' and frame.f_code is actual_verify.__code__:
            calls.append(frame.f_locals['claim'].claim_id)

    def blocked_network(*args, **kwargs):
        raise AssertionError("offline replay attempted a connection")

    monkeypatch.setattr(socket, "create_connection", blocked_network)
    # Observe calls without replacing frozen executable code; runtime
    # fingerprinting should reject a monkeypatched kernel even in a test.
    previous = sys.getprofile()
    try:
        sys.setprofile(observe)
        outputs = [replay_run(run) for _ in range(3)]
    finally:
        sys.setprofile(previous)
    assert len(calls) == 6
    assert all(item["status"] == "passed" and item["recomputed"] for item in outputs)
    assert len({item["result_digest"] for item in outputs}) == 1
    assert len((run / "replay_history.jsonl").read_text(encoding="utf-8").splitlines()) == 3


def test_input_tampering_fails_even_if_numbers_match(frozen_run):
    run, draft = frozen_run
    draft.write_text("changed", encoding="utf-8")
    output = replay_run(run)
    assert output["status"] == "failed"
    assert output["numeric_equal"]
    assert any(item["kind"] == "hash_mismatch" and item["group"] == "inputs" for item in output["integrity_issues"])


def test_changed_claim_is_recomputed_and_detected(frozen_run):
    run, _ = frozen_run
    path = run / "claims.json"
    claims = json.loads(path.read_text(encoding="utf-8"))
    claims[0]["candidate_value"] = "999999"
    path.write_text(json.dumps(claims, ensure_ascii=False), encoding="utf-8")
    output = replay_run(run)
    assert output["status"] == "failed" and not output["numeric_equal"]
    assert any(item.get("field") == "observed" and item.get("replayed") == "999999" for item in output["differences"])
    assert any(item["kind"] == "claims_payload_mismatch" for item in output["integrity_issues"])


def test_manifest_tracks_actual_model_and_deduplicates_inputs(frozen_run):
    run, _ = frozen_run
    lock = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
    assert len(lock["inputs"]) == 2
    assert lock["model"]["mode"] == "agent"
    assert lock["model"]["request_model"] == "configured-model"
    assert lock["model"]["actual_model"] == "provider-model"
    from mingjing.rules import rulepack_root
    metadata = json.loads((rulepack_root() / "sources.json").read_text(encoding="utf-8"))
    assert lock["rules_version"] == metadata["version"]
    assert lock["rules_version_hash"] == metadata["version_hash"]
    assert {entry["path"] for entry in lock["outputs"]} >= {"report.html", "report.md", "results.json", "audit.jsonl"}


def test_successful_api_with_no_mapped_claims_is_kernel(tmp_path):
    audit = AuditLog(tmp_path, "unmapped")
    audit.emit("model_call", status="success", model="example", semantic_claims=1, mapped_claims=0)
    assert model_manifest(tmp_path, "agent")["mode"] == "kernel"


def test_invalid_frozen_fact_never_silently_becomes_zero(frozen_run):
    run, _ = frozen_run
    path = run / "facts.json"
    facts = json.loads(path.read_text(encoding="utf-8"))
    facts[0]["value"] = "invalid"
    path.write_text(json.dumps(facts, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="非法数值"):
        replay_run(run)


def test_original_decimal_context_is_restored_for_replay(frozen_run):
    run, _ = frozen_run
    with localcontext() as context:
        context.prec = 8
        assert replay_run(run)["status"] == "passed"
        assert context.prec == 8


def test_audit_chain_tampering_is_detected(frozen_run):
    run, _ = frozen_run
    path = run / "audit.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    event = json.loads(lines[0])
    event["semantic_claims"] = 999
    lines[0] = json.dumps(event, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    output = replay_run(run)
    assert output["status"] == "failed" and output["numeric_equal"]
    assert any(item["kind"] == "audit_chain_mismatch" for item in output["integrity_issues"])


def test_legacy_unlocked_run_cannot_claim_passing_replay(frozen_run):
    run, _ = frozen_run
    path = run / "run_manifest.json"
    lock = json.loads(path.read_text(encoding="utf-8"))
    lock.pop("outputs")
    lock["schema_version"] = 1
    path.write_text(json.dumps(lock, ensure_ascii=False), encoding="utf-8")
    output = replay_run(run)
    assert output["recomputed"] and output["numeric_equal"]
    assert output["status"] == "failed"
    assert any(item["kind"] == "legacy_manifest" for item in output["integrity_issues"])


def _simulate_frozen_without_sources(tmp_path, monkeypatch):
    import mingjing.audit as audit_module
    source_root = Path(audit_module.__file__).resolve().parents[1]
    bundle = tmp_path / "bundle"
    shutil.copytree(source_root / "rulepack", bundle / "rulepack")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    for name in ("models", "compute", "facts", "judge", "analysis"):
        module = importlib.import_module(f"mingjing.{name}")
        monkeypatch.setattr(module, "__file__", str(bundle / "mingjing" / f"{name}.py"))
    return bundle


def test_frozen_fingerprint_is_stable_after_kernel_warmup_and_dataclass_repr(tmp_path, monkeypatch):
    _simulate_frozen_without_sources(tmp_path, monkeypatch)
    before = kernel_fingerprint()
    facts = FactStore([
        Fact("current", "A", "REVENUE", "2023Y", value=Decimal("120000000"), evidence=("e-current",)),
        Fact("base", "A", "REVENUE", "2022Y", value=Decimal("100000000"), evidence=("e-base",)),
    ])
    judge = Judge(facts)
    claim = AtomicClaim("c", "A2023年营业收入1.20亿元。", 0, 20, company="A", account="REVENUE", period="2023Y", candidate_value="120000000", unit="亿元", display_decimals=2)
    for _ in range(100):
        assert judge.verify(claim).status == "verified"
        repr(facts.facts)
        facts.to_list()
    after = kernel_fingerprint()
    assert before == after
    assert "rule:cards.json" in before and "rule:sources.json" in before


def test_stable_code_identity_ignores_extraction_path_and_line_numbers():
    def computation(value):
        return value + 1

    code = computation.__code__
    relocated = code.replace(co_filename="C:/temporary/_MEI99999/module.py", co_firstlineno=999)
    assert digest(_stable_code_data(code)) == digest(_stable_code_data(relocated))
    changed = code.replace(co_consts=(None, 2))
    assert digest(_stable_code_data(code)) != digest(_stable_code_data(changed))


def test_frozen_rulepack_validator_loads_bundle_root(tmp_path, monkeypatch):
    from mingjing.rules import rulepack_root, validate_rulepack
    bundle = _simulate_frozen_without_sources(tmp_path, monkeypatch)
    assert rulepack_root() == bundle / "rulepack"
    assert validate_rulepack()["status"] == "ok"
    assert validate_rulepack()["cards"] > 0


def test_frozen_three_replays_recompute_without_transient_kernel_changes(frozen_run, tmp_path, monkeypatch):
    run, draft = frozen_run
    _simulate_frozen_without_sources(tmp_path, monkeypatch)
    data = json.loads((run / "results.json").read_text(encoding="utf-8"))
    source_report = data["facts"][0]["source_path"]
    finalize_manifest(run, data["run_id"], [str(draft), source_report], "agent", {"summary": data["summary"]})
    outputs = [replay_run(run) for _ in range(3)]
    assert all(item["status"] == "passed" and item["numeric_equal"] and item["integrity_ok"] and item["recomputed"] for item in outputs)
    assert len({item["result_digest"] for item in outputs}) == 1


def test_frozen_code_fingerprint_is_stable_across_fresh_python_processes():
    script = """
import importlib, json
from decimal import Decimal
from mingjing.audit import kernel_fingerprint
from mingjing.models import Fact, AtomicClaim
from mingjing.facts import FactStore
from mingjing.judge import Judge
for name in ('models', 'compute', 'facts', 'judge', 'analysis'):
    importlib.import_module('mingjing.' + name).__file__ = '/unavailable-frozen-sources/' + name + '.py'
facts = FactStore([Fact('f', 'A', 'REVENUE', '2023Y', value=Decimal('120'), evidence=('e',))])
claim = AtomicClaim('c', 'A2023 income120', 0, 14, company='A', account='REVENUE', period='2023Y', candidate_value='120', unit='yuan')
before = kernel_fingerprint()
for _ in range(64):
    Judge(facts).verify(claim)
    repr(facts.facts)
assert before == kernel_fingerprint()
print(json.dumps(before, sort_keys=True))
"""
    root = Path(__file__).resolve().parents[1]
    outputs = [subprocess.check_output([sys.executable, "-c", script], cwd=root, text=True, encoding="utf-8").strip() for _ in range(3)]
    assert len(set(outputs)) == 1


def test_frozen_changed_authorized_kernel_constant_is_detected(tmp_path, monkeypatch):
    import mingjing.judge as judge_module
    _simulate_frozen_without_sources(tmp_path, monkeypatch)
    before = kernel_fingerprint()
    monkeypatch.setattr(judge_module, "RATIO_ACCOUNTS", {*judge_module.RATIO_ACCOUNTS, "ALTERED_RULE"})
    after = kernel_fingerprint()
    assert before["module:judge"] != after["module:judge"]


def _freeze_partial_execution(run):
    path = run / 'results.json'
    payload = json.loads(path.read_text(encoding='utf-8'))
    pending = payload['results'].pop()['claim_id']
    judged = [row['claim_id'] for row in payload['results']]
    payload['mode'] = 'harness'
    payload['pending_claim_ids'] = [pending]
    payload['execution'] = {'pending_claim_ids': [pending], 'judged_claim_ids': judged,
                            'native_verified_claim_ids': judged, 'preflight_claim_ids': []}
    payload['summary'] = {status: sum(row['status'] == status for row in payload['results'])
                          for status in ('verified', 'violated', 'abstained')}
    payload['coverage'] = {'mentions': [{'claim_id': pending, 'status': 'pending',
                                        'reason': 'native_verification_not_executed'}]}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    _refreeze_execution(run, payload)
    return payload


def _refreeze_execution(run, payload):
    (run / 'results.json').write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    report = payload['facts'][0]['source_path']
    finalize_manifest(run, payload['run_id'], [payload['draft'], report], 'harness', {'partial': True})


def test_partial_kernel_replay_keeps_pending_outside_judgement_denominator(frozen_run):
    run, _ = frozen_run
    original = _freeze_partial_execution(run)
    output = replay_run(run)
    assert output['status'] == 'passed', output
    assert output['claims_frozen'] == 2 and output['claims_recomputed'] == 1 and output['pending_claims'] == 1
    assert output['summary'] == original['summary'] and sum(output['summary'].values()) == 1


@pytest.mark.parametrize('tamper', ['duplicate_pending', 'unknown_pending', 'ledger_status', 'ledger_reason',
                                   'omit_unpartitioned', 'pending_native', 'preflight_native', 'metadata_type'])
def test_partial_replay_rejects_inconsistent_execution_metadata_even_with_fresh_artifact_hashes(frozen_run, tamper):
    run, _ = frozen_run
    payload = _freeze_partial_execution(run)
    pending = payload['pending_claim_ids'][0]
    if tamper == 'duplicate_pending':
        payload['pending_claim_ids'].append(pending)
    elif tamper == 'unknown_pending':
        payload['pending_claim_ids'].append('not-a-frozen-claim')
    elif tamper == 'ledger_status':
        payload['coverage']['mentions'][0]['status'] = 'abstained'
    elif tamper == 'ledger_reason':
        payload['coverage']['mentions'][0]['reason'] = 'source_unparseable'
    elif tamper == 'omit_unpartitioned':
        payload['results'] = []
    elif tamper == 'pending_native':
        payload['execution']['native_verified_claim_ids'].append(pending)
    elif tamper == 'preflight_native':
        payload['execution']['preflight_claim_ids'] = payload['execution']['native_verified_claim_ids'][:]
    elif tamper == 'metadata_type':
        payload['execution'] = None
    _refreeze_execution(run, payload)
    output = replay_run(run)
    assert output['status'] == 'failed' and output['integrity_ok'] is False
    assert any(issue['kind'].startswith(('invalid_pending', 'pending_claim', 'frozen_execution'))
               for issue in output['integrity_issues'])
