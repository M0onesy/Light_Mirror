"""Validate a stage EXE with saved routes and a complete local real-report sample.

The saved profile is read-only. This scoped test is not whole-report financial
acceptance, independent Windows evidence or formal release approval.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil

from scripts.native_exe_smoke import LocalAPI, run_task, redact, artifact_scan
from scripts.delivery_smoke import clean_environment, force_cleanup, launch, require, sha256, shutdown
from mingjing.config import ConfigManager, SecretStore, app_root, ensure_workspace, save_config
from mingjing.providers import resolve_route

ROOT = Path(__file__).resolve().parents[1]


def execute(exe: Path, sample: Path, out: Path) -> dict:
    require(not out.exists(), 'stage_acceptance_requires_new_output')
    out.mkdir(parents=True)
    home = out / 'isolated-profile'
    manager = ConfigManager()
    cfg = manager.get()
    route = resolve_route(cfg.to_dict())
    user_paths = [app_root() / name for name in ('config.json', 'secrets.json', 'secrets.key', 'config.v1.bak.json')]
    before = {str(p): sha256(p) if p.is_file() else None for p in user_paths}
    values = []
    secret_store = SecretStore(home)
    for role in ('default', 'planner', 'verifier', 'reporter'):
        selected = resolve_route(cfg.to_dict(), role)
        key = manager.get_secret(selected['credential_ref'])
        require(bool(key), 'configured_credential_missing')
        secret_store.set(selected['credential_ref'], key)
        if key not in values:
            values.append(key)
    cfg.workspace.data_root = str(home / 'workspace')
    cfg.workspace.output_root = str(home / 'workspace/runs')
    cfg.workspace.temp_root = str(home / 'tmp')
    cfg.logging.directory = str(home / 'logs')
    cfg.security.allowed_roots, cfg.security.allow_external_paths = [str(home / 'workspace')], True
    cfg.server.host, cfg.server.port, cfg.server.auto_open_browser = '127.0.0.1', 0, False
    cfg.server.bearer_token_required = False
    cfg.harness.enabled, cfg.harness.runtime_mode = False, 'embedded'
    cfg.harness.executable, cfg.harness.home, cfg.harness.patch_file, cfg.harness.working_dir = '', '', '', ''
    cfg.agent.enabled, cfg.agent.cross_provider_fallback = True, False
    cfg.agent.budget.max_model_calls, cfg.agent.budget.max_total_tokens = 128, 1000000
    cfg.agent.budget.max_runtime_s = 900
    ensure_workspace(cfg)
    save_config(cfg, home / 'config.json')
    request = json.loads((sample / 'agent_state.json').read_text(encoding='utf-8'))['request']
    request = {k: request[k] for k in ('draft', 'corpus', 'corpus_additions', 'company', 'period', 'report_date', 'task_type') if k in request}
    request['mode'] = 'harness'
    build = json.loads((ROOT / 'dist/MingJing-build.json').read_text(encoding='utf-8'))
    require(sha256(exe) == build['sha256'], 'build_exe_hash_mismatch')
    environment = clean_environment(out, home)
    require(not any(shutil.which(name, path=environment['PATH']) for name in ('python', 'node', 'pnpm')), 'external_tool_present')
    receipt = {'schema': 'mingjing-stage-native-exe.v1', 'status': 'failed', 'version': build['version'],
               'exe_sha256': build['sha256'], 'source_sha256': build['source_sha256'],
               'runtime_manifest_sha256': build['runtime_manifest_sha256'], 'started_at': datetime.now(timezone.utc).isoformat(),
               'scope': 'Complete historical quarter-income paragraph and figures, original research pages 30-31; not whole-report acceptance',
               'checks': {}, 'limitations': ['Developer Windows with restricted child PATH, not independent clean VM',
                                            'Human gold, D3, Kimi/OpenAI real API and formal financial thresholds not certified']}
    process = stream = api = None
    try:
        process, stream, _, record = launch(exe, home, environment, out / 'launcher.log')
        api = LocalAPI(record['url'])
        capabilities = api.json('/api/config')['capabilities']
        require(capabilities.get('pdf') is True and capabilities.get('harness') is True, 'missing_pdf_or_harness')
        receipt['checks']['capabilities'] = capabilities
        probe = api.json('/api/providers/' + route['provider'] + '/test-agent', 'POST', {'model': route['model']}, timeout=360)
        require(probe.get('status') == 'passed' and probe.get('native_runtime') and probe.get('tool_roundtrip'), 'native_tool_probe_failed')
        receipt['checks']['native_agent'] = redact(probe, values)
        print(json.dumps({'stage': 'native_tool_probe', 'status': 'passed', 'model': route['model']}), flush=True)
        state, results = run_task(api, request, 1000)
        require(results.get('model', {}).get('status') == 'success', 'model_fallback')
        require(results['summary'] == {'verified': 11, 'violated': 0, 'abstained': 0}, 'local_sample_changed_or_incomplete')
        require(not results.get('partial') and all(r.get('evidence') for r in results['results']), 'missing_numeric_evidence')
        receipt['checks']['real_api'] = {'model': results['model'], 'summary': results['summary'], 'run_id': state['id'], 'sample_sha256': sha256(Path(request['draft']))}
        replays = []
        for mode in ('kernel', 'agent'):
            proof = api.json('/api/replay', 'POST', {'run': state['out'], 'mode': mode}, timeout=1000)
            require(proof.get('status') == 'passed' and proof.get('numeric_equal') is True and proof.get('network_calls') == 0, 'replay_failed')
            if mode == 'agent':
                require(proof.get('real_harness_loop') is True, 'replay_not_native')
            replays.append({**proof, 'mode': mode, 'replay_mode': proof.get('mode')})
        receipt['checks']['replays'] = replays
        receipt['checks']['secret_scan_files'] = artifact_scan([p for p in home.rglob('*') if p.is_file()], values)
        receipt['checks']['secret_scan'] = 'passed'
        receipt['checks']['user_profile_unchanged'] = before == {str(p): sha256(p) if p.is_file() else None for p in user_paths}
        require(receipt['checks']['user_profile_unchanged'], 'user_profile_changed')
        receipt['status'] = 'passed_scoped_native_agent_and_exe'
    except Exception as exc:
        receipt['error'] = redact(f'{type(exc).__name__}: {exc}', values)
    finally:
        if process is not None and api is not None and process.poll() is None:
            try:
                shutdown(process, stream, api, home)
                process = stream = api = None
                receipt['checks']['normal_shutdown'] = 'passed'
            except Exception:
                receipt['status'], receipt['checks']['normal_shutdown'] = 'failed', 'failed'
        force_cleanup(process, stream, api)
        receipt['finished_at'] = datetime.now(timezone.utc).isoformat()
        (out / 'acceptance.json').write_text(json.dumps(redact(receipt, values), ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe', type=Path, default=ROOT / 'dist/MingJing.exe')
    parser.add_argument('--sample-run', type=Path, default=ROOT / 'runs/source-quarter-income-43-local')
    parser.add_argument('--out', type=Path, default=ROOT / 'runs/stage-native-exe')
    args = parser.parse_args()
    receipt = execute(args.exe.resolve(strict=True), args.sample_run.resolve(strict=True), args.out.resolve())
    print(json.dumps({'status': receipt['status'], 'checks': list(receipt['checks']), 'error': receipt.get('error')}))
    return 0 if receipt['status'].startswith('passed') else 1


if __name__ == '__main__':
    raise SystemExit(main())
