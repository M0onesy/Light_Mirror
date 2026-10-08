"""Real saved-provider API through the Web task endpoint, in an isolated home.

Selects a previously exposed original-report excerpt for a technical vertical
slice. It is not a recall sample, independent gold or clean Windows acceptance.
The saved configuration and DPAPI store are read-only. No keys are copied.
"""
from __future__ import annotations

import argparse
import sys
import copy
import json
import os
from pathlib import Path
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mingjing.import_integrity import assert_application_origin
assert_application_origin(ROOT)

from mingjing.audit import kernel_fingerprint
from mingjing.config import SecretStore, app_root, load_config, save_config, AppConfig
from mingjing.evidence import sha256_file
from mingjing.http_transport import redact_value, safe_error_message
from mingjing.providers import resolve_route
from scripts.source_web_acceptance import HTTPProof, new_output, require
from scripts.source_acceptance_inputs import (open_credentials, finish_receipt, positive_integer,
                                             profile_hashes, profile_unchanged, verify_seed_materials)


def execute(args):
    validate_args(args)
    out = new_output(args.out)
    before, credentials = {}, None
    receipt = {'scope': 'exposed_original_excerpt_real_Web_native_API_technical_only', 'status': 'running',
        'real_provider_api': 'not_executed', 'kernel_replay': {'status': 'not_executed'},
        'agent_replay': {'status': 'not_executed'}, 'final_acceptance': False,
        'http_requests': 'unknown', 'user_profile_unchanged': None, 'clean_windows': 'not_executed'}
    context = {'out': out, 'receipt': receipt, 'before': before, 'credentials': credentials}
    try:
        return _execute(args, context)
    except Exception as exc:
        credentials = context['credentials']
        receipt = context['receipt']
        receipt.update(status='failed', error_class=type(exc).__name__,
            error=safe_error_message(exc, credentials.values if credentials is not None else set()),
            script_sha256=sha256_file(Path(__file__)))
        return finish_receipt(receipt, out, context['before'], credentials, filename='acceptance.json')


def validate_args(args):
    positive_integer(args.timeout, 'timeout')
    require(30 <= args.timeout <= 1800, 'Timeout must be 30 to 1800 seconds')
    if not isinstance(args.source_run, Path) or not isinstance(args.out, Path):
        raise ValueError('source_run and out must be paths')
    return args


def _execute(args, context):
    out = context['out']
    context['receipt']['phase'] = 'read_source_materials'
    source = args.source_run.resolve(strict=True)
    require(source.is_relative_to(ROOT/'runs') and not any('d3' in part.lower() for part in source.parts),
        'Only already-exposed development results are accepted')
    original = json.loads((source/'results.json').read_text(encoding='utf-8'))
    state_snapshot = json.loads((source/'agent_state.json').read_text(encoding='utf-8'))
    text = original['draft_text']
    row = next(item for item in original['results'] if item['status'] == 'verified' and item.get('source_span'))
    span = row['source_span']
    # Table claims have numeric-cell spans and a normalized row description.
    # Preserve the actual PDF page, including its row/column/unit context;
    # do not manufacture a sentence from the machine's claim description.
    page = next(item for item in original['coverage']['pages']
        if item['start'] <= span['start'] < item['end'])
    source_pdf = Path(state_snapshot['request']['draft']).resolve(strict=True)
    require(source_pdf.suffix.lower() == '.pdf', 'This vertical slice requires an original PDF report')
    seed_integrity = verify_seed_materials(source, [source_pdf, state_snapshot['request'].get('corpus')])
    context['receipt']['seed_material_integrity'] = seed_integrity
    saved_home = app_root()
    saved = [saved_home/'config.json', saved_home/'secrets.json']
    before = {str(path): sha256_file(path) for path in saved if path.is_file()}
    context['before'] = profile_hashes(saved_home)
    context['receipt']['phase'] = 'read_saved_configuration'
    config = load_config(saved_home/'config.json').to_dict()
    route = resolve_route(config)
    context['receipt']['phase'] = 'initialize_credential_store'
    credentials = open_credentials(SecretStore, saved_home)
    store = credentials.store
    context['credentials'] = credentials
    context['receipt']['phase'] = 'resolve_credentials'
    secret = credentials.get(route['credential_ref'])
    require(bool(secret), 'Saved selected provider has no readable credential')
    home = out/'profile'
    home.mkdir()
    draft = out/'original_page.pdf'
    import fitz
    with fitz.open(source_pdf) as full, fitz.open() as selected:
        selected.insert_pdf(full, from_page=page['page']-1, to_page=page['page']-1)
        selected.save(draft)
    config = copy.deepcopy(config)
    config['workspace'] = {key: '' for key in config['workspace']}
    config['workspace'].update(data_root=str(home/'workspace'), output_root=str(home/'workspace'/'runs'),
        temp_root=str(home/'workspace'/'tmp'))
    config['logging']['directory'] = str(home/'logs')
    config['agent']['enabled'] = True
    config['agent']['cross_provider_fallback'] = False
    config['agent']['budget'].update(max_total_tokens=1000000, max_model_calls=128, max_tool_calls=1000,
        max_runtime_s=args.timeout)
    config['server'].update(host='127.0.0.1', port=0, auto_open_browser=False, bearer_token_required=False)
    config['harness'].update(runtime_mode='embedded', enabled=False, executable='', home='',
        patch_file='', working_dir='')
    config['run']['timeout_s'] = args.timeout
    request = {key: state_snapshot['request'][key] for key in ('company', 'period', 'report_date', 'corpus')}
    request.update(draft=str(draft), mode='harness', task_type='check')
    require(Path(request['corpus']).is_absolute(), 'Frozen source must identify an absolute financial corpus')
    code_before = kernel_fingerprint()
    previous_home = os.environ.get('MINGJING_HOME')
    server = thread = app = http = None
    receipt = {'scope': 'exposed_original_excerpt_real_Web_native_API_technical_only', 'status': 'failed',
        'seed_material_integrity': seed_integrity,
        'source_run': original['run_id'], 'original_text_span': span, 'draft_sha256': sha256_file(draft),
        'source_pdf_sha256': sha256_file(source_pdf), 'selected_original_pdf_page': page['page'],
        'provider': route['provider'], 'model': route['model'], 'real_provider_api': True,
        'independent_gold': False, 'clean_windows': 'not_executed', 'final_acceptance': False,
        'kernel_replay': {'status': 'not_executed'}, 'agent_replay': {'status': 'not_executed'}}
    context['receipt'] = receipt
    try:
        os.environ['MINGJING_HOME'] = str(home)
        save_config(AppConfig.from_dict(config), home/'config.json')
        from mingjing import server as host
        app = host.AppState()
        # Private host credential broker reads the existing DPAPI store. Its
        # response never enters config, HTTP, tool results or these receipts.
        app.config._manager.secret_store = store
        server = host.MingJingServer(('127.0.0.1', 0), app)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        http = HTTPProof(f'http://127.0.0.1:{server.server_port}', out)
        public = http.request('GET', '/api/config')
        require(public['capabilities']['pdf'] is True, 'Web PDF capability unavailable')
        task = http.request('POST', '/api/run', request, expected=202)
        receipt.update(run_id=task['id'], out=task['out'], task_status=task['status'])
        deadline = time.monotonic()+args.timeout+60
        while time.monotonic() < deadline:
            task = http.request('GET', '/api/runs/'+task['id'])
            receipt['task_status'] = task['status']
            if task['status'] not in {'queued', 'running'}:
                break
            time.sleep(.5)
        require(task['status'] == 'completed', 'Actual native Web task did not complete')
        result = http.request('GET', '/api/runs/'+task['id']+'/results')
        receipt.update(model_run=result.get('model', {}), summary=result.get('summary', {}),
                       partial=result.get('partial', 'unknown'))
        require(result.get('model', {}).get('status') == 'success' and result.get('mode') == 'harness',
            'Web task fell back instead of using the selected real provider')
        require(result['summary']['verified'] > 0 and result['model']['binding_submissions']['planner'] > 0
            and result['model']['binding_submissions']['verifier'] > 0, 'Real A/B numeric vertical slice incomplete')
        events = http.request('GET', '/api/runs/'+task['id']+'/events')
        require(any(event.get('event') == 'tool' for event in events['events']), 'Web omitted native tool activity')
        for mode in ('kernel', 'agent'):
            proof = http.request('POST', '/api/replay', {'run': task['out'], 'mode': mode}, timeout=120)
            receipt[mode+'_replay'] = {key: proof.get(key) for key in ('status', 'numeric_equal', 'network_calls', 'real_harness_loop')}
            require(proof.get('status') == 'passed' and proof.get('network_calls') == 0,
                'Web cached replay failed or contacted a provider')
        receipt['status'] = 'passed_technical_native_Web_API'
    except Exception as exc:
        receipt.update(error_class=type(exc).__name__, error=safe_error_message(exc, [secret]))
    finally:
        if app is not None:
            for task in list(app.tasks.values()):
                task.cancelled.set()
                if task.thread:
                    task.thread.join(15)
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(5)
        if previous_home is None:
            os.environ.pop('MINGJING_HOME', None)
        else:
            os.environ['MINGJING_HOME'] = previous_home
        receipt['user_profile_unchanged'] = profile_unchanged(context['before'])
        receipt['http_requests'] = len(http.records) if http is not None else 'unknown'
        receipt['kernel_unchanged'] = kernel_fingerprint() == code_before
        receipt['script_sha256'] = sha256_file(Path(__file__))
        receipt = redact_value(receipt, credentials.values)
        context['receipt'] = receipt
        (out/'acceptance.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        files = [path for path in out.rglob('*') if path.is_file()]
        receipt['secret_scan'] = {'files_scanned': len(files),
            'plaintext_found': any(secret.encode('utf-8') in path.read_bytes() for path in files)}
        if not receipt['user_profile_unchanged'] or not receipt['kernel_unchanged'] or receipt['secret_scan']['plaintext_found']:
            receipt['status'] = 'failed'
        (out/'acceptance.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-run', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=300)
    args = parser.parse_args(argv)
    try:
        validate_args(args)
    except ValueError as exc:
        parser.error(str(exc))
    result = execute(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'passed_technical_native_Web_API' else 1


if __name__ == '__main__':
    raise SystemExit(main())
