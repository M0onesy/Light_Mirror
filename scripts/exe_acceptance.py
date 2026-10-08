"""Exercise the frozen product via HTTP with the user's encrypted LLM profile.

The isolated profile stays in LOCALAPPDATA; credentials never enter artifacts.
"""
import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mingjing.config import ConfigManager, SecretStore, save_config, ensure_workspace

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--exe', default='dist-acceptance/MingJing.exe')
    parser.add_argument('--out', default='runs/exe-acceptance')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    artifact = (root/args.out).resolve()
    artifact.mkdir(parents=True, exist_ok=True)
    profile = Path(os.environ['LOCALAPPDATA'])/'MingJing-Acceptance-0.3.0'
    user = ConfigManager()
    cfg = user.get()
    user_config_hash = hashlib.sha256(user.path.read_bytes()).hexdigest()
    token = user.get_secret(cfg.llm.api_key_ref)
    if not cfg.llm.enabled or not token:
        raise SystemExit('The saved profile must have an enabled, readable API configuration.')
    cfg.workspace.data_root = str(profile/'workspace')
    cfg.workspace.output_root = str(profile/'workspace/runs')
    cfg.workspace.temp_root = str(profile/'tmp')
    cfg.logging.directory = str(profile/'logs')
    cfg.security.allowed_roots = [str(profile/'workspace')]
    cfg.security.allow_external_paths = True
    cfg.server.host, cfg.server.port, cfg.server.auto_open_browser = '127.0.0.1', 0, False
    ensure_workspace(cfg)
    save_config(cfg, profile/'config.json')
    secrets = SecretStore(profile)
    secrets.set(cfg.llm.api_key_ref, token)
    if cfg.server.bearer_token_required:
        secrets.set(cfg.server.bearer_token_ref, user.get_secret(cfg.server.bearer_token_ref))
    exe = (root/args.exe).resolve()
    environment = os.environ.copy()
    environment['PATH'] = str(Path(os.environ['SYSTEMROOT'])/'System32')
    environment.pop('MINGJING_HOME', None)
    environment['MINGJING_NO_BROWSER'] = '1'
    for key in list(environment):
        if key.startswith('MINGJING_LLM_'):
            environment.pop(key, None)
    log = (artifact/'launcher.log').open('wb')
    process = subprocess.Popen([str(exe),'--home',str(profile),'--no-browser'], env=environment,
        stdout=log, stderr=subprocess.STDOUT, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    base = None
    receipt = {'product':'MingJing','version':'0.3.0','exe_sha256':hashlib.sha256(exe.read_bytes()).hexdigest(),
               'clean_path_no_python_node':True, 'profile_isolated':True, 'checks':{}, 'real_report_scope':'One 44-page Parallel Technology research report; annual-report-backed financial claims.'}
    def request(path, data=None, binary=False):
        headers = {'Content-Type':'application/json'}
        if cfg.server.bearer_token_required:
            headers['Authorization'] = 'Bearer '+str(user.get_secret(cfg.server.bearer_token_ref))
        body = None if data is None else json.dumps(data, ensure_ascii=False).encode('utf-8')
        with urllib.request.urlopen(urllib.request.Request(base+path, data=body, headers=headers), timeout=90) as response:
            raw = response.read()
        return raw if binary else json.loads(raw.decode('utf-8'))
    def wait_task(task):
        deadline = time.monotonic()+180
        while time.monotonic()<deadline:
            status = request('/api/runs/'+task['id'])
            if status['status'] in {'completed','failed','cancelled'}:
                if status['status'] != 'completed':
                    raise RuntimeError(str(status.get('error') or status['status']))
                return status
            time.sleep(.5)
        raise TimeoutError('Frozen task did not finish within acceptance deadline.')
    try:
        server_file = profile/'workspace/server.json'
        for attempt in range(60):
            if process.poll() is not None:
                raise RuntimeError('Frozen executable exited during startup.')
            if server_file.is_file():
                item = json.loads(server_file.read_text(encoding='utf-8'))
                base = item['url'].rstrip('/')
                try:
                    health = request('/api/health')
                    if health.get('service') == 'mingjing' and health.get('version') == '0.3.0':
                        break
                except OSError:
                    pass
            time.sleep(.5)
        else:
            raise RuntimeError('Frozen health check unavailable.')
        receipt['checks']['health'] = health
        capabilities = request('/api/config')['capabilities']
        receipt['checks']['capabilities'] = capabilities
        assert capabilities['pdf'] is True
        demo = request('/api/demo', {})
        # The demo endpoint supplies an explicit request payload for the UI.
        demo_request = demo.get('request',demo)
        task = request('/api/run', {key:demo_request[key] for key in ('draft','corpus','company','period','report_date') if key in demo_request} | {'mode':'kernel'})
        status = wait_task(task)
        receipt['checks']['demo'] = {'run_id':task['id'],'summary':status['summary']}
        assert status['summary'] == {'verified':4,'violated':5,'abstained':1}
        project = root.parent
        draft = next(project.rglob('*AP202404221630960822.pdf'))
        corpus = next(p for p in project.rglob('2024-04-15_2023\u5e74\u5e74\u5ea6\u62a5\u544a.pdf') if any(part.startswith('920493_') for part in p.parts))
        task = request('/api/run', {'draft':str(draft),'corpus':str(corpus),'company':'\u5e76\u884c\u79d1\u6280', 'period':'2023Y','report_date':'2024-04-22','mode':'agent'})
        status = wait_task(task)
        result = request('/api/runs/'+task['id']+'/results')
        manifest = json.loads((Path(status['out'])/'run_manifest.json').read_text(encoding='utf-8'))
        assert result['company'] == '\u5e76\u884c\u79d1\u6280'
        assert manifest['model']['status'] == 'success' and manifest['model']['semantic_claims'] > 0
        assert result['summary']['verified'] >= 3
        assert result['summary']['violated'] == 0
        assert any(c['relation']=='yoy' for c in result['claims'])
        assert all(r['evidence'] and r['operands'] for r in result['results'] if r['status'] != 'abstained')
        receipt['checks']['real_api'] = {'run_id':task['id'],'out':status['out'],'summary':result['summary'],'model':manifest['model']}
        evidence = next(e for r in result['results'] if r['status']=='verified' for e in r['evidence'])
        image = request('/api/evidence/'+urllib.parse.quote(evidence,safe='')+'?run='+task['id']+'&format=png',binary=True)
        assert image.startswith(b'\x89PNG\r\n\x1a\n')
        (artifact/'real-evidence.png').write_bytes(image)
        receipt['checks']['evidence'] = {'id':evidence,'bytes':len(image),'png':True}
        replays = [request('/api/replay', {'run':status['out']}) for _ in range(3)]
        assert all(r['status']=='passed' and r['recomputed'] and r['integrity_ok'] and r['numeric_equal'] for r in replays)
        assert len({r['result_digest'] for r in replays}) == 1
        receipt['checks']['replays'] = replays
        exported = request('/api/runs/'+task['id']+'/export',binary=True)
        with zipfile.ZipFile(io.BytesIO(exported)) as archive:
            assert not any('secrets' in name or 'config.json' in name for name in archive.namelist())
            assert all(token.encode('utf-8') not in archive.read(name) for name in archive.namelist())
        (artifact/'real-run-export.zip').write_bytes(exported)
        receipt['checks']['secret_scan'] = 'passed'
        assert hashlib.sha256(user.path.read_bytes()).hexdigest() == user_config_hash
        receipt['checks']['user_profile_unchanged'] = True
        receipt['status'] = 'passed_scoped_real_api_and_exe'
        (artifact/'acceptance.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'status':receipt['status'],'run_id':task['id'],'summary':result['summary'],'model_status':manifest['model']['status'],'receipt':str(artifact/'acceptance.json')},ensure_ascii=True))
    finally:
        if base:
            try:
                request('/api/shutdown', {})
            except OSError:
                pass
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=8)
        log.close()
        # Keep run records, remove acceptance-only credential references.
        secrets.delete(cfg.llm.api_key_ref)
        if cfg.server.bearer_token_required:
            secrets.delete(cfg.server.bearer_token_ref)

if __name__=='__main__':
    raise SystemExit(main())
