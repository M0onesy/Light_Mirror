"""Acceptance utility integrity plus actual source-launcher HTTP integration.

The end-to-end fixture uses synthetic materials and real processes/sockets;
it does not claim financial gold, a browser click test or clean-machine EXE.
"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import source_web_acceptance as acceptance


def test_child_environment_isolated_without_mutating_the_parent_mapping(tmp_path):
    inherited = {'PATH': 'retained', 'LOCALAPPDATA': 'retained-user-root', 'MINGJING_HOME': 'old-profile',
                 'MINGJING_LLM_API_KEY': 'private', 'DSH_HOME': 'old-harness', 'OPENAI_API_KEY': 'private',
                 'MOONSHOT_API_KEY': 'private', 'PYTHONPATH': 'old-checkout', 'HARNESS_TELEMETRY': 'enabled'}
    original = dict(inherited)
    child = acceptance.isolated_environment(tmp_path/'new', inherited)
    assert inherited == original
    assert child['PATH'] == 'retained' and child['LOCALAPPDATA'] == 'retained-user-root'
    assert child['MINGJING_HOME'] == str(tmp_path/'new') and child['MINGJING_NO_BROWSER'] == '1'
    assert all(key not in child for key in ('MINGJING_LLM_API_KEY', 'DSH_HOME', 'OPENAI_API_KEY',
                                          'MOONSHOT_API_KEY', 'PYTHONPATH', 'HARNESS_TELEMETRY'))


def test_output_cannot_replace_existing_evidence_or_enter_saved_profile(tmp_path, monkeypatch):
    old = tmp_path/'old-profile'
    old.mkdir()
    marker = old/'config.json'
    marker.write_text('existing-config', encoding='utf-8')
    monkeypatch.setenv('MINGJING_HOME', str(old))
    with pytest.raises(ValueError, match='existing application home'):
        acceptance.new_output(old/'nested-new-output')
    other = tmp_path/'existing-receipt'
    other.mkdir()
    with pytest.raises(ValueError, match='never overwritten'):
        acceptance.new_output(other)
    assert marker.read_text(encoding='utf-8') == 'existing-config'
    assert not (old/'nested-new-output').exists()


@pytest.mark.parametrize('base', ['https://127.0.0.1:123', 'http://external.example:123',
                                 'http://127.0.0.1/', 'http://localhost:123'])
def test_http_proof_never_targets_an_external_or_unbound_service(tmp_path, base):
    with pytest.raises(ValueError, match='actual IPv4 loopback'):
        acceptance.HTTPProof(base, tmp_path)


def test_optional_holdout_run_rejected_before_opening_its_results(tmp_path):
    holdout = tmp_path/'D3'
    holdout.mkdir()
    with pytest.raises(ValueError, match='D3'):
        acceptance.clone_existing_run(holdout, tmp_path/'clone')


def test_optional_frozen_clone_is_bounded_read_only_and_keeps_original_identity(tmp_path):
    source = tmp_path/'actual-run'
    source.mkdir()
    original = {'run_id': source.name, 'model': {'status': 'success'}, 'results': []}
    (source/'results.json').write_text(json.dumps(original), encoding='utf-8')
    (source/'run_manifest.json').write_text('{}', encoding='utf-8')
    target, proof = acceptance.clone_existing_run(source, tmp_path/'isolated-runs')
    assert target.name == source.name and proof['run_id'] == source.name
    assert (target/'results.json').read_bytes() == (source/'results.json').read_bytes()
    assert json.loads((source/'results.json').read_text(encoding='utf-8')) == original
    with pytest.raises(ValueError, match='overwrite'):
        acceptance.clone_existing_run(source, target.parent)
    private = source/'secrets'
    private.mkdir()
    (private/'token').write_text('do-not-copy', encoding='utf-8')
    with pytest.raises(ValueError, match='Secret storage'):
        acceptance.clone_existing_run(source, tmp_path/'second-clone')
    assert not (tmp_path/'second-clone'/source.name).exists()


def test_actual_source_launcher_web_task_evidence_export_replay_cancel_and_restart(tmp_path):
    out = tmp_path/'actual-web'
    result = acceptance.execute(SimpleNamespace(out=out, timeout=60, existing_run=None))
    assert result['status'] == 'passed', result.get('error')
    assert result['scope'] == 'actual_source_launcher_loopback_http'
    assert result['cloud_api_calls'] == 0 and not result['saved_credentials_accessed']
    assert not result['EXE_built'] and not result['D3_accessed'] and not result['final_project_acceptance']
    assert result['browser_interaction'] == result['clean_windows_acceptance'] == 'not_executed'
    checks = result['checks']
    assert checks['kernel_run']['status'] == 'completed' and checks['kernel_run']['claims'] == 11
    assert all(checks['kernel_run']['summary'][status] for status in ('verified', 'violated', 'abstained'))
    assert checks['all_served_frontend_assets_locked'] > 0
    assert checks['host_origin_bearer_authentication'] and checks['revision_conflict']
    assert checks['kernel_replay']['numeric_equal'] and checks['kernel_replay']['network_calls'] == 0
    assert checks['cancel_actual_task']['status'] == 'cancelled' and checks['real_restart_configuration_history']
    records = [json.loads(line) for line in (out/'http.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(records) == result['http_requests']
    assert any(row['path'].endswith('/export') and row['content_type'] == 'application/zip' for row in records)
    assert any(row['status'] == 401 and row['path'] == '/api/config' for row in records)
    assert any(row['status'] == 409 and row['method'] == 'PATCH' for row in records)
    assert all('Authorization' not in row and 'secrets' not in row for row in records)
    assert not (out/'profile'/'workspace'/'server.json').exists()
    assert json.loads((out/'acceptance.json').read_text(encoding='utf-8'))['status'] == 'passed'
