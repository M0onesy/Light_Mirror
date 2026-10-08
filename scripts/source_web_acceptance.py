"""Actual loopback HTTP acceptance of the source launcher and Harness Web UI.

Uses a new application home, synthetic redistributable materials, no saved
credentials and no cloud API. It is not an EXE/clean-machine acceptance.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mingjing.audit import kernel_fingerprint
from mingjing.config import validate_path
from mingjing.evidence import sha256_file
from scripts.build_frontend import verify as verify_frontend


def isolated_environment(home, inherited=None):
    env = dict(os.environ if inherited is None else inherited)
    for key in list(env):
        if (key.upper().startswith(('MINGJING_', 'DSH_', 'HARNESS_'))
                or key.upper() in {'OPENAI_API_KEY', 'DEEPSEEK_API_KEY', 'ANTHROPIC_API_KEY',
                                   'MOONSHOT_API_KEY', 'KIMI_API_KEY', 'PYTHONPATH', 'PYTHONHOME'}):
            env.pop(key)
    env.update(MINGJING_HOME=str(home), MINGJING_NO_BROWSER='1', PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
    return env


def new_output(value):
    out = validate_path(value, kind='dir', follow_symlinks=False)
    reserved = [Path(value) for key in ('MINGJING_HOME',) if (value := os.environ.get(key))]
    if os.environ.get('LOCALAPPDATA'):
        reserved.append(Path(os.environ['LOCALAPPDATA'])/'MingJing')
    for root in reserved:
        if out.is_relative_to(root.expanduser().resolve()):
            raise ValueError('Acceptance must not use the existing application home')
    if out.exists():
        raise ValueError('Acceptance output must be new; existing evidence is never overwritten')
    out.mkdir(parents=True)
    return out


def require(condition, message):
    if not condition:
        raise ValueError(message)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        raise urllib.error.HTTPError(request.full_url, code, 'Acceptance refuses redirects', headers, fp)


class HTTPProof:
    def __init__(self, base, out):
        parsed = urllib.parse.urlparse(base)
        require(parsed.scheme == 'http' and parsed.hostname == '127.0.0.1' and parsed.port,
                'Only the actual IPv4 loopback service is accepted')
        self.base, self.out, self.token = base.rstrip('/'), Path(out), ''
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.records = []

    def request(self, method, path, payload=None, *, expected=200, authenticated=True, headers=None, timeout=15):
        require(path.startswith('/') and not path.startswith('//'), 'Only same-service paths are accepted')
        raw_request = None if payload is None else json.dumps(payload, ensure_ascii=False).encode('utf-8')
        fields = {'Content-Type': 'application/json'} if payload is not None else {}
        if authenticated and self.token:
            fields['Authorization'] = 'Bearer '+self.token
        fields.update(headers or {})
        request = urllib.request.Request(self.base+path, data=raw_request, method=method, headers=fields)
        started = time.monotonic()
        try:
            response = self.opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            status, raw, response_headers = response.status, response.read(), dict(response.headers)
        require(not self.token or self.token.encode() not in raw, 'HTTP response leaked the ephemeral bearer')
        content_type = response_headers.get('Content-Type', '')
        extension = '.json' if 'json' in content_type else '.zip' if 'zip' in content_type else '.png' if 'png' in content_type else '.bin'
        body_path = self.out/'http'/(f'{len(self.records)+1:04d}'+extension)
        body_path.parent.mkdir(parents=True, exist_ok=True)
        with body_path.open('xb') as stream:
            stream.write(raw)
        record = {'sequence': len(self.records)+1, 'method': method, 'path': path, 'status': status,
                  'authenticated': bool(authenticated and self.token), 'elapsed_ms': round((time.monotonic()-started)*1000),
                  'request_sha256': hashlib.sha256(raw_request).hexdigest() if raw_request is not None else None,
                  'response_sha256': hashlib.sha256(raw).hexdigest(), 'response_bytes': len(raw),
                  'response_file': body_path.relative_to(self.out).as_posix(), 'content_type': content_type,
                  'response_headers': {key: value for key, value in response_headers.items()
                                       if key.lower() in {'content-security-policy', 'x-content-type-options', 'cache-control', 'content-disposition'}}}
        self.records.append(record)
        with (self.out/'http.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(record, ensure_ascii=False)+'\n')
        allowed = {expected} if isinstance(expected, int) else set(expected)
        require(status in allowed, f'{method} {path} returned {status}, expected {sorted(allowed)}')
        return json.loads(raw.decode('utf-8')) if 'json' in content_type else raw


class Launcher:
    def __init__(self, out):
        self.out, self.home = Path(out), Path(out)/'profile'
        self.child, self.log, self.generation = None, None, 0

    def start(self, port=0):
        self.generation += 1
        self.log = (self.out/f'launcher-{self.generation}.log').open('xb')
        command = [sys.executable, '-m', 'mingjing.launcher', '--home', str(self.home), '--host', '127.0.0.1',
                   '--port', str(port), '--no-browser']
        kwargs = {'cwd': ROOT, 'env': isolated_environment(self.home), 'stdout': self.log, 'stderr': subprocess.STDOUT}
        if os.name == 'nt':
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
        self.child = subprocess.Popen(command, **kwargs)
        record = self.home/'workspace'/'server.json'
        deadline = time.monotonic()+15
        while time.monotonic() < deadline and self.child.poll() is None:
            if record.is_file():
                value = json.loads(record.read_text(encoding='utf-8'))
                if value.get('pid') == self.child.pid:
                    return value
            time.sleep(.05)
        self.close(force=True)
        raise ValueError('Actual source launcher did not publish a live service record')

    def close(self, force=False):
        if self.child is not None:
            if force and self.child.poll() is None:
                self.child.terminate()
            try:
                self.child.wait(timeout=6)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait(timeout=4)
            code = self.child.returncode
            self.child = None
        else:
            code = None
        if self.log is not None:
            self.log.close()
            self.log = None
        return code


def poll_task(http, run_id, deadline):
    while time.monotonic() < deadline:
        value = http.request('GET', '/api/runs/'+run_id)
        if value['status'] not in {'running', 'queued'}:
            return value
        time.sleep(.05)
    raise ValueError('Actual HTTP task did not reach a terminal state before the acceptance deadline')


def clone_existing_run(source, runs):
    source = validate_path(source, must_exist=True, kind='dir', follow_symlinks=False)
    require(not any(re.search(r'(^|[-_])d3($|[-_])', part, re.I) for part in source.parts), 'D3 is excluded from this development tool')
    data = json.loads((source/'results.json').read_text(encoding='utf-8'))
    require(data.get('run_id') == source.name and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', source.name),
            'Explicit existing run must retain its actual run identity')
    require((source/'run_manifest.json').is_file(), 'Existing run must have a frozen manifest')
    files = []
    total = 0
    for path in source.rglob('*'):
        validate_path(path, allowed_roots=[str(source)], follow_symlinks=False)
        if path.is_file():
            relative = path.relative_to(source)
            require(not any(part.lower() in {'secrets', 'credentials'} or re.search(r'(^|[-_])d3($|[-_])', part, re.I)
                            for part in relative.parts), 'Secret storage and D3 files cannot enter an optional run clone')
            total += path.stat().st_size
            require(total <= 64*1024*1024, 'Optional run clone exceeds the bounded small-evidence size')
            files.append((path, relative, sha256_file(path)))
    target = runs/source.name
    require(not target.exists(), 'Existing run clone must not overwrite a task')
    for path, relative, sha in files:
        destination = target/relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        require(sha256_file(destination) == sha and sha256_file(path) == sha, 'Existing run changed while cloning')
    return target, {'run_id': source.name, 'source': str(source), 'copy_bytes': total,
                    'source_files': [{'path': relative.as_posix(), 'sha256': sha} for _, relative, sha in files]}


def execute(args):
    out = new_output(args.out)
    launcher = Launcher(out)
    http = None
    receipt = {'schema_version': 'mingjing-source-web-acceptance.v1', 'status': 'failed',
               'created_at': datetime.now(timezone.utc).isoformat(), 'scope': 'actual_source_launcher_loopback_http',
               'materials': 'redistributable_synthetic_demo_not_financial_gold', 'production_modified': False,
               'saved_configuration_accessed': False, 'saved_credentials_accessed': False, 'cloud_api_calls': 0,
               'D3_accessed': False, 'EXE_built': False, 'browser_interaction': 'not_executed',
               'clean_windows_acceptance': 'not_executed', 'final_project_acceptance': False, 'checks': {}}
    checks = receipt['checks']
    token = secrets.token_urlsafe(32)
    try:
        initial_code = kernel_fingerprint()
        receipt['kernel_fingerprint'] = initial_code
        source_lock = {path.relative_to(ROOT).as_posix(): sha256_file(path)
                       for path in (ROOT/'mingjing').glob('*.py')}
        receipt['application_source_lock'] = source_lock
        receipt['frontend'] = verify_frontend(ROOT)
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            occupied.listen()
            busy = occupied.getsockname()[1]
            record = launcher.start(busy)
            require(record['port'] != busy, 'Launcher did not recover from a real occupied port')
            checks['occupied_port_recovery'] = True
        receipt['service'] = record
        http = HTTPProof(record['url'], out)
        health = http.request('GET', '/api/health')
        require(health.get('instance_id') == record['instance_id'] and health.get('service') == 'mingjing',
                'HTTP service identity differs from the actual launcher record')
        checks['actual_launcher_health'] = True
        index = http.request('GET', '/')
        require(index == (ROOT/'web'/'harness'/'index.html').read_bytes(), 'Served HTML is not the actual Harness UI build')
        require("script-src 'self'" in http.records[-1]['response_headers'].get('Content-Security-Policy', '')
                and http.records[-1]['response_headers'].get('X-Content-Type-Options') == 'nosniff',
                'Actual Harness UI is missing its static security headers')
        web_receipt = http.request('GET', '/build_receipt.json')
        require(web_receipt == json.loads((ROOT/'web'/'harness'/'build_receipt.json').read_text(encoding='utf-8')),
                'HTTP frontend receipt differs from the source build receipt')
        for entry in web_receipt['outputs']:
            path = entry['path'].removeprefix('web/harness/')
            raw = http.request('GET', '/'+urllib.parse.quote(path, safe='/'))
            if isinstance(raw, dict):
                raise ValueError('A frontend asset was unexpectedly returned as JSON')
            require(hashlib.sha256(raw).hexdigest() == entry['sha256'] and len(raw) == entry['bytes'],
                    'Served frontend asset differs from its build lock')
        checks['all_served_frontend_assets_locked'] = len(web_receipt['outputs'])
        http.request('GET', '/api/health', headers={'Origin': 'https://untrusted.example'}, expected=401)
        http.request('GET', '/api/health', headers={'Host': 'untrusted.example'}, expected=401)
        config = http.request('GET', '/api/config')
        require(not config['config'].get('providers') and not config['config']['llm']['enabled'], 'Isolated profile unexpectedly enabled a model')
        patch = {'revision': config['revision'], 'llm': {'enabled': False}, 'harness': {'enabled': False},
                 'agent': {'enabled': False}, 'run': {'mode': 'kernel', 'timeout_s': max(30, int(args.timeout))},
                 'security': {'allow_external_paths': False, 'allowed_roots': [str(launcher.home)], 'follow_symlinks': False},
                 'server': {'bearer_token_required': True, 'bearer_token_ref': 'acceptance_bearer'},
                 'secrets': {'acceptance_bearer': token}}
        http.token = token
        value = http.request('PATCH', '/api/config', patch, authenticated=False)
        require(value['revision'] == config['revision']+1, 'Configuration revision did not advance once')
        http.request('GET', '/api/config', authenticated=False, expected=401)
        http.request('GET', '/api/config', headers={'Authorization': 'Bearer invalid-token'}, expected=401)
        http.request('GET', '/', authenticated=False)
        http.request('PATCH', '/api/config', {'revision': config['revision'], 'run': {'company': 'stale'}}, expected=409)
        checks['host_origin_bearer_authentication'] = True
        checks['revision_conflict'] = True
        require(http.request('POST', '/api/config/validate', {})['ok'], 'Actual isolated Web configuration did not validate')
        exported = http.request('GET', '/api/config/export')
        require(exported.get('secrets') == 'omitted', 'Configuration export did not explicitly omit secrets')
        imported = http.request('POST', '/api/config/import', {'revision': value['revision'], 'config': exported['config']})
        require(imported['revision'] == value['revision']+1, 'HTTP configuration import did not use revision locking')
        checks['configuration_validate_export_import'] = True
        demo = http.request('POST', '/api/demo', {})
        request = {key: demo[key] for key in ('draft', 'corpus', 'company', 'period', 'report_date', 'mode')}
        request['task_type'] = 'check'
        draft = Path(demo['draft']).with_name('acceptance_draft.md')
        draft.write_text(Path(demo['draft']).read_text(encoding='utf-8')+'\n公司合并口径2024年研发费用0.50亿元。\n', encoding='utf-8')
        request['draft'] = str(draft)
        require(http.request('POST', '/api/config/test-path', {'path': str(draft)})['ok'], 'Actual demo input path is unusable')
        preview = http.request('POST', '/api/draft/preview', {'draft': str(draft)})
        require(preview['sha256'] == sha256_file(draft), 'HTTP original draft preview hash differs')
        submitted = http.request('POST', '/api/run', request, expected=202)
        run_id = submitted['id']
        receipt['kernel_run_id'] = run_id
        task = poll_task(http, run_id, time.monotonic()+args.timeout)
        require(task['status'] == 'completed', 'Actual Web kernel task failed or returned partial')
        results = http.request('GET', '/api/runs/'+run_id+'/results')
        require(results.get('results') and {row['status'] for row in results['results']} == {'verified', 'violated', 'abstained'},
                'Synthetic Web fixture did not exercise all three result states')
        run = launcher.home/'workspace'/'runs'/run_id
        frozen = json.loads((run/'results.json').read_text(encoding='utf-8'))
        for field in ('claims', 'facts', 'summary'):
            require(results[field] == frozen[field], 'HTTP result projection changed frozen '+field)
        projected_rows = [{key: value for key, value in row.items() if key not in {'execution_status', 'display_status'}}
                          for row in results['results']]
        require(projected_rows == frozen['results'] and all(row.get('execution_status') == 'processed'
                and row.get('display_status') == row['status'] for row in results['results']),
                'HTTP result projection changed a frozen verdict or misrepresented its execution')
        require(frozen.get('model', {}).get('status') not in {'success', 'fallback'}, 'Kernel task unexpectedly exercised a model')
        events = http.request('GET', '/api/runs/'+run_id+'/events')
        require(events.get('events') and events.get('raw_event_count', 0) > 0, 'Actual Web task emitted no progress events')
        require(run_id in {item['id'] for item in http.request('GET', '/api/runs')['runs']}, 'Completed task missing from HTTP history')
        checks['kernel_run'] = {'status': task['status'], 'summary': results['summary'], 'claims': len(results['claims']),
                                'events': events['raw_event_count'], 'model_enabled': False}
        fact = next(row for row in results['facts'] if row.get('evidence') and str(row.get('source_path', '')).lower().endswith('.pdf'))
        evidence_id = fact['evidence'][0]
        endpoint = '/api/evidence/'+urllib.parse.quote(evidence_id, safe='')+'?run='+run_id
        evidence = http.request('GET', endpoint)
        png = http.request('GET', endpoint+'&format=png')
        require(evidence.get('evidence_id') == evidence_id and evidence.get('page', 0) >= 1 and png.startswith(b'\x89PNG\r\n\x1a\n'),
                'Actual HTTP PDF page evidence is missing or invalid')
        checks['page_evidence'] = {'evidence_id': evidence_id, 'page': evidence['page'], 'png_sha256': hashlib.sha256(png).hexdigest()}
        archive_raw = http.request('GET', '/api/runs/'+run_id+'/export')
        with zipfile.ZipFile(io.BytesIO(archive_raw)) as archive:
            required = {'claims.json', 'facts.json', 'results.json', 'report.md', 'report.html', 'audit.jsonl', 'run_manifest.json'}
            require(required <= set(archive.namelist()), 'HTTP export is missing required frozen artifacts')
            for name in required:
                require(archive.read(name) == (run/name).read_bytes(), 'HTTP export changed frozen artifact '+name)
            checks['frozen_http_export'] = {'files': len(archive.namelist()), 'bytes': len(archive_raw),
                                            'sha256': hashlib.sha256(archive_raw).hexdigest()}
        replay = http.request('POST', '/api/replay', {'run': str(run), 'mode': 'kernel'}, timeout=max(15, args.timeout))
        require(replay.get('status') == 'passed' and replay.get('numeric_equal') and replay.get('network_calls') == 0,
                'HTTP kernel replay did not reproduce the frozen numeric result offline')
        checks['kernel_replay'] = {'status': replay['status'], 'numeric_equal': replay['numeric_equal'], 'network_calls': replay['network_calls']}
        large_draft = draft.with_name('cancel_acceptance.md')
        large_draft.write_text('公司合并口径2024年营业收入10.00亿元。\n'*4000, encoding='utf-8')
        cancel_request = dict(request, draft=str(large_draft))
        cancelling = http.request('POST', '/api/run', cancel_request, expected=202)
        http.request('POST', '/api/runs/'+cancelling['id']+'/cancel', {}, expected=202)
        cancelled = poll_task(http, cancelling['id'], time.monotonic()+15)
        require(cancelled['status'] == 'cancelled', 'Actual HTTP cancellation did not stop the task')
        checks['cancel_actual_task'] = {'run_id': cancelling['id'], 'status': cancelled['status']}
        if args.existing_run:
            clone, optional = clone_existing_run(args.existing_run, run.parent)
            history = http.request('GET', '/api/runs')['runs']
            require(clone.name in {item['id'] for item in history}, 'Explicit frozen clone was not discovered through Web history')
            optional['view'] = http.request('GET', '/api/runs/'+clone.name+'/results').get('model', {})
            optional['export_sha256'] = hashlib.sha256(http.request('GET', '/api/runs/'+clone.name+'/export')).hexdigest()
            optional['kernel_replay'] = http.request('POST', '/api/replay', {'run': str(clone), 'mode': 'kernel'}, timeout=max(15, args.timeout))
            optional['scope'] = 'existing_frozen_run_view_export_offline_replay_only_no_new_agent_api_execution'
            receipt['optional_existing_run'] = optional
            optional['status'] = optional['kernel_replay'].get('status', 'failed')
            require(optional['status'] == 'passed', 'Explicit existing run failed HTTP kernel replay; the original lock was not changed')
        if os.name != 'nt':
            current = http.request('GET', '/api/config')
            http.request('PATCH', '/api/config', {'revision': current['revision'], 'server': {'bearer_token_required': False}})
            checks['bearer_restart_persistence'] = 'not_executed_non_windows_dpapi'
        http.request('POST', '/api/shutdown', {}, expected=202)
        require(launcher.close() == 0 and not (launcher.home/'workspace'/'server.json').exists(), 'Actual launcher did not shut down cleanly')
        restarted = launcher.start()
        http.base = restarted['url'].rstrip('/')
        require(http.request('GET', '/api/config')['config']['run']['mode'] == 'kernel', 'Configuration did not survive a real restart')
        history = http.request('GET', '/api/runs')['runs']
        require(run_id in {item['id'] for item in history}, 'Real restarted Web service lost completed history')
        require(http.request('GET', '/api/runs/'+run_id+'/results')['results'] == results['results'], 'Restarted Web result changed')
        if os.name == 'nt':
            http.request('GET', '/api/config', authenticated=False, expected=401)
            checks['bearer_restart_persistence'] = 'passed_windows_dpapi_ephemeral_test_bearer'
        checks['real_restart_configuration_history'] = True
        http.request('POST', '/api/shutdown', {}, expected=202)
        require(launcher.close() == 0, 'Restarted launcher did not stop cleanly')
        require(kernel_fingerprint() == initial_code, 'Production kernel changed during this HTTP acceptance')
        require({path.relative_to(ROOT).as_posix(): sha256_file(path) for path in (ROOT/'mingjing').glob('*.py')} == source_lock,
                'Application source changed during this HTTP acceptance')
        require(verify_frontend(ROOT) == receipt['frontend'], 'Frontend changed during this HTTP acceptance')
        for path in launcher.home.rglob('*'):
            if path.is_file():
                require(token.encode() not in path.read_bytes(), 'Acceptance profile wrote a plaintext ephemeral bearer')
        receipt.update(status='passed', http_requests=len(http.records), finished_at=datetime.now(timezone.utc).isoformat())
    except Exception as exc:
        receipt.update(error_class=type(exc).__name__, error=str(exc).replace(token, '[REDACTED]'),
                       finished_at=datetime.now(timezone.utc).isoformat())
    finally:
        if http is not None and launcher.child is not None and launcher.child.poll() is None:
            try:
                http.request('POST', '/api/shutdown', {}, expected=202, timeout=4)
                launcher.close()
            except Exception:
                pass
        launcher.close(force=True)
        receipt['http_requests'] = len(http.records) if http is not None else 0
        receipt['script_sha256'] = sha256_file(Path(__file__))
        with (out/'acceptance.json').open('x', encoding='utf-8') as stream:
            stream.write(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n')
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True, help='New isolated acceptance evidence directory')
    parser.add_argument('--timeout', type=int, default=90)
    parser.add_argument('--existing-run', type=Path, help='Explicit small frozen run; clone without altering the original')
    args = parser.parse_args(argv)
    require(5 <= args.timeout <= 1800, 'Acceptance timeout must be between 5 and 1800 seconds')
    result = execute(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
