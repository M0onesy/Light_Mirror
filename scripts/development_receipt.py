"""Collect actual development evidence; never issue a professional release pass.

Receipts remain linked by their original bytes. This does not rerun, relabel,
rewrite old locks, select a holdout, or substitute tests for independent gold.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

from scripts.build_frontend import verify


ROOT = Path(__file__).resolve().parents[1]


def _digest(value):
    from mingjing.audit import digest
    return digest(value)


def _path(path, root=None):
    """Resolve only nominated files; never inspect a D3 directory or follow an escape."""
    base = (root or ROOT).resolve()
    path = Path(path)
    if not path.is_absolute():
        path = base/path
    if any(re.search(r'(^|[-_])d3($|[-_])', part, re.I) for part in path.parts):
        raise ValueError('D3 is outside development evidence collection')
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(base) or not resolved.is_file():
        raise ValueError('Evidence file outside its approved root')
    relative = path.absolute().relative_to(base)
    cursor = base
    for part in relative.parts:
        cursor = cursor/part
        if cursor.is_symlink() or getattr(cursor, 'is_junction', lambda: False)():
            raise ValueError('Evidence symlinks and junctions are not accepted')
    return resolved


def _sha(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            result.update(chunk)
    return result.hexdigest()


def _locked_file(path, expected, root=None):
    path = _path(path, root)
    if not isinstance(expected, dict) or not re.fullmatch(r'[0-9a-f]{64}', str(expected.get('sha256', ''))):
        raise ValueError('Missing or invalid evidence SHA-256')
    if type(expected.get('bytes')) is not int or expected['bytes'] < 0:
        raise ValueError('Missing or invalid evidence size')
    if path.stat().st_size != expected['bytes'] or _sha(path) != expected['sha256']:
        raise ValueError('Frozen evidence hash or size changed: ' + path.name)
    return path


def _outputs(manifest, root, required):
    root = root.resolve()
    entries = manifest.get('outputs')
    if not isinstance(entries, list) or not entries:
        raise ValueError('Missing frozen output inventory')
    seen = set()
    for entry in entries:
        raw = entry.get('path', '') if isinstance(entry, dict) else ''
        if not isinstance(raw, str) or Path(raw).is_absolute() or raw in seen:
            raise ValueError('Invalid or duplicate frozen output path')
        path = _locked_file(root/raw, entry, root)
        if path.relative_to(root.resolve()).as_posix() != raw:
            raise ValueError('Noncanonical frozen output path')
        seen.add(raw)
    if not required <= seen:
        raise ValueError('Required output is absent from frozen inventory')
    return len(entries)


def _read(path):
    return json.loads(_path(path).read_text(encoding='utf-8'))


def _audit(root, run_id):
    previous, events = '', []
    for line in _path(root/'audit.jsonl').read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        saved = event.pop('event_hash', None)
        if event.get('run_id') != run_id or event.get('prev_event_hash') != previous or _digest(event) != saved:
            raise ValueError('Frozen audit chain changed')
        previous = saved
        events.append(event)
    if not events:
        raise ValueError('Frozen audit contains no events')
    return events


def _remote_route(config, role=None):
    routing = config.get('agent', {}).get('routing', {})
    route = routing.get(role) or routing.get('default') or {}
    provider = config.get('providers', {}).get(route.get('provider'), {})
    address = urlparse(provider.get('base_url') or '')
    host = (address.hostname or '').lower()
    if not route.get('model') or not route.get('provider') or address.scheme not in {'http', 'https'}:
        raise ValueError('Missing frozen model route')
    if not host or host in {'localhost', '::1', '0.0.0.0'} or host.startswith('127.') or host.endswith('.invalid'):
        raise ValueError('A local/mock/invalid provider route is not live API evidence')
    return {key: route[key] for key in ('provider', 'model')}


def _replay(row, *, agent=False):
    good = (isinstance(row, dict) and row.get('status') == 'passed' and row.get('numeric_equal') is True
            and type(row.get('network_calls')) is int and row['network_calls'] == 0)
    if agent:
        good = good and row.get('real_harness_loop') is True
    else:
        good = good and row.get('integrity_ok', True) is True
    return bool(good)


def _run(root, receipt, *, runtime_lock=None, task_type=None, expected_run_id=None):
    root = root.resolve()
    manifest, record = read_receipt(root/'run_manifest.json')
    lock = _read(root/'agent_lock.json')
    data = _read(root/'results.json')
    count = _outputs(manifest, root, {'results.json', 'claims.json', 'facts.json', 'audit.jsonl',
                                     'agent_lock.json', 'agent_state.json', 'agent_model_cache.json', 'bindings.json'})
    inputs = manifest.get('inputs')
    if not isinstance(inputs, list) or not inputs:
        raise ValueError('Run has no frozen input inventory')
    for entry in inputs:
        _locked_file(Path(entry['path']), entry, ROOT.parent)
    run_id = data.get('run_id')
    if expected_run_id and run_id != expected_run_id:
        raise ValueError('Run belongs to another acceptance series')
    if not run_id or manifest.get('run_id') != run_id or manifest.get('native_agent') is not True:
        raise ValueError('Run is not a frozen native Agent task')
    if manifest.get('resource_lock') != lock.get('resource_lock') or lock.get('resource_lock') != data.get('resource_lock'):
        raise ValueError('Run resource lock changed')
    if _digest({key: value for key, value in lock.items() if key != 'resource_lock'}) != lock['resource_lock']:
        raise ValueError('Run original resource/configuration lock digest changed')
    if lock.get('revision') != lock.get('config', {}).get('revision') or manifest.get('agent_config_revision') != lock.get('revision'):
        raise ValueError('Run original configuration revision changed')
    if lock.get('kernel') != manifest.get('kernel_fingerprint') or not lock.get('agent', {}).get('modules'):
        raise ValueError('Run original kernel/Agent lock is missing or inconsistent')
    if runtime_lock is not None and lock.get('agent', {}).get('runtime') != runtime_lock:
        raise ValueError('Run runtime differs from the nominated runtime acceptance')
    if task_type and data.get('task_type') != task_type:
        raise ValueError('Run task type differs from receipt')
    for key, data_key in [('model_run', 'model'), ('summary', 'summary'), ('partial', 'partial')]:
        if key == 'partial' and key not in receipt:
            continue  # The existing Web receipt omits this field; use frozen output below.
        if receipt.get(key) != data.get(data_key):
            raise ValueError('Receipt ' + key + ' differs from frozen run output')
    for key in ('claims', 'facts'):
        if isinstance(receipt.get(key), int) and receipt[key] != len(data.get(key, [])):
            raise ValueError('Receipt ' + key + ' count differs from frozen run')
        if _read(root/(key+'.json')) != data.get(key):
            raise ValueError('Frozen ' + key + ' differs between output formats')
    route = _remote_route(lock['config'])
    if receipt.get('provider') and receipt['provider'] != route['provider']:
        raise ValueError('Receipt provider differs from frozen configuration')
    if receipt.get('model') and receipt['model'] != route['model']:
        raise ValueError('Receipt model differs from frozen configuration')
    events = _audit(root, run_id)
    descriptions = [event.get('output', {}) for event in events if event.get('type') == 'runtime_describe']
    if not any(row.get('native_loop') is True and row.get('profile') == 'mingjing'
               and row.get('shell') is False and row.get('code_mode') is False for row in descriptions):
        raise ValueError('No original guarded native Harness runtime description')
    calls = [event for event in events if event.get('type') == 'model_request']
    if (not calls or any(row.get('replay_only') is not False or row.get('cache_hit') is not False for row in calls)
            or len(calls) != data.get('model', {}).get('request_count')):
        raise ValueError('Live model requests are absent, cached, or inconsistent')
    for event in events:
        if event.get('type') == 'model_call' and event.get('model') != _remote_route(lock['config'], event.get('role'))['model']:
            raise ValueError('Model call differs from frozen role routing')
    cache = _read(root/'agent_model_cache.json')
    if not isinstance(cache, dict) or not all(row.get('request_hash') in cache for row in calls):
        raise ValueError('Live model requests have no frozen response cache')
    return {'status': 'passed', 'manifest': record, 'outputs_verified': count, 'inputs_verified': len(inputs),
        'audit_events_verified': len(events), 'live_logged_model_requests': len(calls), 'run_id': run_id,
        'model_route': route, 'kernel': lock['kernel'], 'agent_modules': lock['agent']['modules'],
        'runtime_lock': lock['agent']['runtime'], 'config_revision': lock.get('revision'),
        'config_sha256': _digest(lock['config']), 'resource_lock': data['resource_lock'],
        'pending_claim_ids': data.get('pending_claim_ids', 'unknown'),
        'partial': data.get('partial', 'unknown'),
        'binding_submissions': data.get('model', {}).get('binding_submissions', 'unknown')}


def _attempt(function):
    try:
        return function()
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError) as exc:
        return {'status': 'failed', 'reason': type(exc).__name__ + ': ' + str(exc)}


def _runtime(receipt):
    from mingjing.harness_client import verify_finance_carrier
    lock = receipt.get('runtime_lock')
    if not isinstance(lock, dict) or lock.get('lock_schema_version') != 2:
        raise ValueError('Missing nominated runtime lock')
    fixture = receipt.get('native_fixture', {})
    linked, linked_record = read_receipt(ROOT/fixture['receipt'])
    if linked_record['sha256'] != fixture.get('sha256') or linked.get('runtime_lock') != lock:
        raise ValueError('Linked native runtime receipt hash or lock changed')
    if (linked.get('status') != 'passed_staged_finance_runtime_functional_fixture'
            or linked.get('real_provider_api') != 'not_executed'
            or linked.get('describe', {}).get('native_loop') is not True
            or type(linked.get('replay_network_requests')) is not int or linked['replay_network_requests'] != 0):
        raise ValueError('Runtime fixture failed or was relabeled as live provider acceptance')
    linked_root = (ROOT/fixture['receipt']).parent
    for name, field in [('resolved-modules.jsonl', 'import_trace_sha256'), ('carrier-import-guard.mjs', 'import_guard_sha256')]:
        if _sha(_path(linked_root/name)) != linked.get(field):
            raise ValueError('Runtime import guard/trace hash changed')
    for entry in receipt.get('frozen_source_inputs', []):
        _locked_file(ROOT/entry['path'], entry)
    if not receipt.get('frozen_source_inputs') or not receipt.get('tests') or any(
            type(row.get('exit_code')) is not int or row['exit_code'] != 0
            or type(row.get('passed')) is not int or row['passed'] <= 0 for row in receipt['tests']):
        raise ValueError('Runtime source proof or tests are missing/failed')
    carrier = verify_finance_carrier(ROOT/'runtime/finance-carrier/staged')
    launch = lock.get('launch', {})
    entries = {row['path']: row for row in carrier['files']}
    if (carrier['manifest_sha256'] != lock.get('manifest_sha256')
            or carrier['manifest_sha256'] != launch.get('dependency_lock_sha256')
            or carrier.get('plugin_sha256') != lock.get('plugin_sha256')
            or carrier.get('source_sha256') != lock.get('source_archive_sha256')
            or carrier.get('source_snapshot_sha256') != lock.get('source_snapshot_sha256')
            or carrier.get('harness_version') != lock.get('harness_version')
            or entries[carrier['node']]['sha256'] != launch.get('node_sha256')
            or entries[carrier['bootstrap']]['sha256'] != launch.get('entry_sha256')):
        raise ValueError('Actual carrier resources differ from nominated runtime lock')
    return {'status': 'passed', 'linked_fixture': linked_record, 'carrier_manifest_sha256': carrier['manifest_sha256'],
            'carrier_files_verified': len(carrier['files']), 'evidence_kind': 'original_harness_loop_keyless_fixture',
            'real_provider_api': 'not_executed', 'runtime_lock': lock}


def _followup(root, receipt, parent_root, parent_validation):
    root, parent_root = root.resolve(), parent_root.resolve()
    sid = receipt.get('session_id')
    if not isinstance(sid, str) or not re.fullmatch(r'session-[a-zA-Z0-9-]+', sid):
        raise ValueError('Missing followup session ID')
    state = _read(root/'sessions'/(sid+'.json'))
    parent_manifest = _read(parent_root/'run_manifest.json')
    parent_results = _read(parent_root/'results.json')
    if state.get('run_id') != parent_results['run_id'] or receipt.get('source_run') != parent_results['run_id']:
        raise ValueError('Followup parent run differs from receipt')
    messages = [row for row in state.get('messages', []) if row.get('role') == 'assistant']
    replays = receipt.get('message_agent_replays', [])
    if not messages or len(messages) != len(replays):
        raise ValueError('Followup messages/replay evidence are missing')
    records, request_count, tool_count = [], 0, 0
    for message, replay in zip(messages, replays):
        mid = message.get('message_id')
        if not isinstance(mid, str) or not re.fullmatch(r'message-\d+', mid):
            raise ValueError('Invalid followup message ID')
        message_root = root/'sessions'/sid/mid
        manifest, record = read_receipt(message_root/'message_manifest.json')
        if record['sha256'] != message.get('agent_message_manifest_sha256'):
            raise ValueError('Followup message manifest hash changed')
        _outputs(manifest, message_root, {'message_lock.json', 'message_output.json', 'agent_model_cache.json', 'audit.jsonl'})
        lock, output = _read(message_root/'message_lock.json'), _read(message_root/'message_output.json')
        for key, expected in [('session_id', sid), ('message_id', mid), ('run_id', parent_results['run_id']),
                              ('resource_lock', state.get('resource_lock'))]:
            if manifest.get(key) != expected or lock.get(key) != expected:
                raise ValueError('Followup message identity or resource lock changed')
        if (lock.get('parent_manifest') != parent_manifest or lock.get('parent_results_sha256') != _sha(_path(parent_root/'results.json'))
                or Path(lock.get('parent_run_path', '')).resolve() != parent_root.resolve()
                or lock.get('config') != state.get('config') or output.get('answer') != message.get('text')
                or lock.get('kernel') != parent_manifest.get('kernel_fingerprint')
                or lock.get('agent', {}).get('runtime') != parent_validation.get('runtime_lock')
                or lock.get('agent', {}).get('modules') != parent_validation.get('agent_modules')):
            raise ValueError('Followup frozen parent, configuration, runtime or answer lock changed')
        route = _remote_route(lock['config'])
        if receipt.get('route') != route:
            raise ValueError('Followup receipt route differs from frozen configuration')
        events = _audit(message_root, parent_results['run_id'])
        calls = [row for row in events if row.get('type') == 'model_request']
        if not calls or any(row.get('replay_only') is not False or row.get('cache_hit') is not False for row in calls):
            raise ValueError('Followup has no live original model requests')
        cache = _read(message_root/'agent_model_cache.json')
        if not isinstance(cache, dict) or not all(row.get('request_hash') in cache for row in calls):
            raise ValueError('Followup model requests have no frozen response cache')
        request_count += len(calls)
        tool_count += sum(row.get('type') == 'tool_call' for row in events)
        if (replay.get('status') != 'passed' or replay.get('real_harness_loop') is not True
                or type(replay.get('network_calls')) is not int or replay['network_calls'] != 0
                or replay.get('output_sha256') != _digest(output)):
            raise ValueError('Followup replay failed or output digest differs')
        records.append(record)
    usage = receipt.get('usage', {})
    if (type(usage.get('model_requests')) is not int or usage['model_requests'] != request_count
            or type(usage.get('tool_calls')) is not int or usage['tool_calls'] != tool_count):
        raise ValueError('Followup receipt request/tool counts differ from original audit')
    return {'status': 'passed', 'messages_verified': records, 'session_id': sid,
            'source_run': parent_results['run_id'], 'live_logged_model_requests': request_count,
            'tool_calls_verified': tool_count, 'usage': receipt.get('usage', 'unknown')}


def _generated(root, metrics):
    audit, record = read_receipt(root/'measurement_audit.json')
    for name, field in [('metrics.json', 'metrics_sha256'), ('generated_cases.jsonl', 'input_sha256'),
                        ('predictions.jsonl', 'predictions_sha256')]:
        if _sha(_path(root/name)) != audit.get(field):
            raise ValueError('Generated measurement file hash changed')
    cases = [json.loads(line) for line in _path(root/'generated_cases.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    predictions = [json.loads(line) for line in _path(root/'predictions.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    if (audit.get('professional_acceptance') is not False or metrics.get('status') != 'generated_pipeline_measured_not_professional_acceptance'
            or metrics.get('suite_sha256') != _digest(cases) or metrics.get('records_sha256') != _digest(predictions)
            or type(metrics.get('total')) is not int or metrics['total'] <= 0 or len(cases) != len(predictions) or metrics['total'] != len(cases)):
        raise ValueError('Generated mechanism provenance, count or digest is invalid')
    if any(row.get('set') == 'D3' for row in cases):
        raise ValueError('D3 cannot enter development mechanism evidence')
    return {'status': 'passed', 'measurement_audit': record, 'scope': 'generated_mechanism_not_financial_gold'}


def _regression(path):
    path = _path(path)
    raw = path.read_bytes()
    tree = ET.fromstring(raw)
    cases = list(tree.iter('testcase'))
    result = {key: sum(case.find(tag) is not None for case in cases)
        for key, tag in [('failures', 'failure'), ('errors', 'error'), ('skipped', 'skipped')]}
    result.update(total=len(cases), passed=sum(not any(case.find(tag) is not None
        for tag in ('failure', 'error', 'skipped')) for case in cases), declared_counts_consistent=True)
    for suite in tree.iter('testsuite'):
        local = list(suite.iter('testcase'))
        for field, tag in [('tests', None), ('failures', 'failure'), ('errors', 'error'), ('skipped', 'skipped')]:
            actual = len(local) if tag is None else sum(case.find(tag) is not None for case in local)
            if field in suite.attrib and (not suite.attrib[field].isdigit() or int(suite.attrib[field]) != actual):
                result['declared_counts_consistent'] = False
    return result, {'path': path.relative_to(ROOT).as_posix(), 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}


def _prior_failures(series):
    match = re.fullmatch(r'(stable)-(\d+)', series)
    if not match or int(match[2]) < 2:
        return []
    previous = f'{match[1]}-{int(match[2])-1:02d}'
    path = ROOT/f'runs/source-native-reparse-{previous}/acceptance.json'
    if not path.is_file():
        return []
    row, record = read_receipt(path)
    if row.get('status') not in {'needs_improvement', 'failed', 'partial'}:
        return []
    return [{'series': previous, 'task': 'native_autonomous_supplement', 'evidence': record,
             'status': row.get('status'), 'child': row.get('child', {}),
             'numeric_value_from_original_pdf': row.get('numeric_value_from_original_pdf', 'unknown'),
             'page_evidence_available': row.get('page_evidence_available', 'unknown')}]


def source_lock():
    paths = set()
    def source_files(base):
        for directory, dirs, files in os.walk(base, followlinks=False):
            here = Path(directory)
            dirs[:] = [name for name in dirs if name != '__pycache__'
                and not re.search(r'(^|[-_])d3($|[-_])', name, re.I)
                and not (here/name).is_symlink()
                and not getattr(here/name, 'is_junction', lambda: False)()]
            for name in files:
                path = here/name
                if not re.search(r'(^|[-_])d3($|[-_])', path.stem, re.I) and not path.is_symlink():
                    yield path
    for directory in ('mingjing', 'scripts', 'tests', 'bench/v1', 'rulepack', 'runtime/finance-plugin', 'samples'):
        base = ROOT/directory
        if not base.is_dir():
            continue
        # Bench lock covers implementation files only. Do not even enumerate
        # annotation/holdout subdirectories to freeze development source.
        candidates = base.iterdir() if directory == 'bench/v1' else source_files(base)
        suffixes = {'.py', '.json', '.mjs', '.md', '.lock'}
        if directory == 'samples':
            suffixes = {'.md', '.txt', '.json', '.jsonl', '.pdf', '.csv', '.tsv', '.yaml', '.yml'}
        paths.update(path for path in candidates if path.is_file()
            and '__pycache__' not in path.parts and path.suffix in suffixes
            and (directory != 'bench/v1' or path.parent == ROOT/'bench/v1'))
    paths.update(source_files(ROOT/'frontend'/'src'))
    paths.update(ROOT/name for name in ('app_entry.py', 'pyproject.toml', 'requirements-build.lock',
        'runtime/harness/manifest.json', 'runtime/finance-carrier/staged/carrier_manifest.json',
        'web/harness/build_receipt.json'))
    return {path.relative_to(ROOT).as_posix(): _sha(_path(path))
        for path in sorted(paths) if path.is_file()}


def read_receipt(path):
    path = _path(path)
    raw = path.read_bytes()
    return json.loads(raw), {'path': path.relative_to(ROOT).as_posix(),
        'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}


def collect(args):
    sources = {
        'report': ROOT/f'runs/source-real-{args.series}/source_acceptance.json',
        'analysis': ROOT/f'runs/source-analysis-{args.series}/source_acceptance.json',
        'numeric_message': ROOT/f'runs/source-followup-{args.series}/acceptance.json',
        'knowledge_message': ROOT/f'runs/source-knowledge-followup-{args.series}/acceptance.json',
        'native_reparse': ROOT/f'runs/source-native-reparse-{args.series}/acceptance.json',
        'native_web': ROOT/f'runs/source-native-web-{args.series}/acceptance.json',
        'source_web': args.web_receipt,
        'runtime': args.runtime_receipt,
        'generated_metrics': ROOT/f'runs/generated-{args.series}/metrics.json',
    }
    records, evidence, validation = {}, {}, {}
    for name, path in sources.items():
        try:
            records[name], evidence[name] = read_receipt(path)
        except FileNotFoundError:
            records[name] = {'status': 'not_executed', 'reason': 'receipt_missing'}
            evidence[name] = {'path': str(Path(path).relative_to(ROOT)), 'status': 'missing', 'sha256': 'unknown', 'bytes': 'unknown'}
        except (OSError, ValueError, TypeError) as exc:
            records[name] = {'status': 'failed', 'reason': type(exc).__name__ + ': ' + str(exc)}
            evidence[name] = {'path': str(path), 'status': 'invalid', 'sha256': 'unknown', 'bytes': 'unknown'}
    try:
        regression, evidence['regression'] = _regression(args.regression)
    except (OSError, ValueError, ET.ParseError) as exc:
        regression = {'status': 'not_executed_or_invalid', 'reason': str(exc),
                      'total': 'unknown', 'passed': 'unknown', 'failures': 'unknown', 'errors': 'unknown', 'skipped': 'unknown'}
    code_lock = _attempt(lambda: read_receipt(args.code_lock)[0])
    if code_lock.get('status') != 'failed':
        evidence['code_lock'] = read_receipt(args.code_lock)[1]
    validation['runtime'] = _attempt(lambda: _runtime(records['runtime']))
    runtime_lock = records['runtime'].get('runtime_lock')
    for name in ('report', 'analysis'):
        validation[name] = _attempt(lambda name=name: _run(Path(sources[name]).parent, records[name],
            runtime_lock=runtime_lock, task_type='analysis' if name == 'analysis' else 'check',
            expected_run_id='source-real-'+Path(sources[name]).parent.name))
    for phase in ('parent', 'child'):
        validation['native_reparse_'+phase] = _attempt(lambda phase=phase: _run(
            Path(sources['native_reparse']).parent/('native-'+phase), records['native_reparse'].get(phase, {}),
            runtime_lock=runtime_lock, task_type='check', expected_run_id=Path(sources['native_reparse']).parent.name+'-native-'+phase))
    def native_web_run():
        root = Path(records['native_web']['out']).resolve()
        if not root.is_relative_to(Path(sources['native_web']).parent.resolve()):
            raise ValueError('Native Web run outside its own acceptance workspace')
        verified = _run(root, records['native_web'], runtime_lock=runtime_lock, task_type='check')
        if verified['run_id'] != records['native_web'].get('run_id'):
            raise ValueError('Native Web receipt run identity changed')
        return verified
    validation['native_web'] = _attempt(native_web_run)
    for name in ('numeric_message', 'knowledge_message'):
        validation[name] = _attempt(lambda name=name: _followup(Path(sources[name]).parent, records[name],
                                      Path(sources['analysis']).parent, validation['analysis']))
    validation['generated_metrics'] = _attempt(lambda: _generated(Path(sources['generated_metrics']).parent, records['generated_metrics']))
    frontend = _attempt(lambda: verify(ROOT))
    reference = validation['report']
    native_rows = [validation[name] for name in ('report', 'analysis', 'native_web', 'native_reparse_parent', 'native_reparse_child')]
    coherent_native_locks = bool(reference.get('agent_modules')) and all(
        row.get('status') == 'passed' and row.get('agent_modules') == reference['agent_modules']
        and row.get('kernel') == records['source_web'].get('kernel_fingerprint') for row in native_rows)
    source_web_lock = records['source_web'].get('application_source_lock')
    coherent_source_web = isinstance(source_web_lock, dict) and bool(source_web_lock) and all(
        code_lock.get(path) == sha for path, sha in source_web_lock.items())
    live = {}
    for name in ('report', 'analysis'):
        row = records[name]
        live[name] = {key: row.get(key) for key in ('status', 'model', 'provider', 'summary',
            'claims', 'facts', 'partial', 'elapsed_s', 'abstention_codes', 'financial_analysis')}
        live[name]['model_run'] = {key: row.get('model_run', {}).get(key) for key in
            ('status', 'request_count', 'tokens', 'cost', 'mapped_claims', 'source_candidates',
             'source_parsed_claims', 'semantic_review_candidates', 'semantic_review_submitted', 'binding_submissions')}
        live[name]['replays'] = {key: row.get(key, {}).get('status')
            for key in ('kernel_replay', 'agent_replay')}
    checks = {
        'source_unchanged_since_regression_started': code_lock == source_lock(),
        'coherent_regression': type(regression['passed']) is int and regression['passed'] > 0
            and regression['errors'] == 0 and regression['failures'] == 0 and regression.get('declared_counts_consistent') is True,
        'frontend_source_and_assets': frontend.get('status') == 'passed_source_to_assets',
        'frozen_run_and_message_integrity': all(row.get('status') == 'passed' for name, row in validation.items()
                                               if name != 'runtime'),
        'coherent_original_native_locks': bool(coherent_native_locks),
        'source_web': records['source_web'].get('status') == 'passed' and bool(coherent_source_web),
        'real_native_Web_API': records['native_web'].get('status') == 'passed_technical_native_Web_API'
            and records['native_web'].get('user_profile_unchanged') is True
            and records['native_web'].get('secret_scan', {}).get('plaintext_found') is False
            and records['native_web'].get('real_provider_api') is True
            and records['native_web'].get('model_run', {}).get('status') == 'success'
            and validation['native_web'].get('partial') is False
            and _replay(records['native_web'].get('kernel_replay'))
            and _replay(records['native_web'].get('agent_replay'), agent=True),
        'real_native_report_and_analysis': all(records[name].get('status') == 'technical_flow_passed_quality_pending'
            and records[name].get('mode') == 'native_harness_source'
            and records[name].get('model_run', {}).get('status') == 'success'
            and records[name].get('model_run', {}).get('mode') == 'harness'
            and records[name].get('partial') is False for name in ('report', 'analysis')),
        'kernel_and_native_replays': all(_replay(records[name].get(key), agent=key == 'agent_replay')
            for name in ('report', 'analysis') for key in ('kernel_replay', 'agent_replay')),
        'live_numeric_evidence_complete': all(type(records[name].get('numeric_conclusion_count')) is int
            and records[name]['numeric_conclusion_count'] > 0 and all(records[name].get(key) is True for key in
                ('numeric_evidence_complete', 'numeric_page_evidence_available', 'numeric_company_scope_consistent',
                 'expected_events_present', 'native_financial_chain')) for name in ('report', 'analysis')),
        'numeric_and_source_followups': all(records[name].get('status') == 'technical_followup_passed_quality_pending'
            and records[name].get('real_provider_api') is True
            and records[name].get('message_replay_passed') is True and records[name].get('answer_present') is True
            and records[name].get('user_profile_unchanged') is True for name in ('numeric_message', 'knowledge_message')),
        'numeric_followup_evidence': all(records['numeric_message'].get(key) is True for key in
            ('numeric_company_scope_consistent', 'numeric_evidence_complete', 'requested_evidence_in_answer')),
        'exact_knowledge_definition': records['knowledge_message'].get('exact_definition_subject_present') is True
            and records['knowledge_message'].get('substantive_source_content_present') is True
            and all(records['knowledge_message'].get(key) is True for key in
                ('source_quote_labeled', 'source_provenance_complete', 'source_evidence_verified_against_original',
                 'knowledge_reference_guarded', 'knowledge_original_locations_available')),
        'user_profile_preserved': all(records[name].get('user_profile_unchanged') is True for name in ('report', 'analysis')),
        'no_plaintext_keys_in_live_artifacts': all(records[name].get('secret_scan', {}).get('plaintext_found') is False
            for name in ('report', 'analysis', 'numeric_message', 'knowledge_message')),
        'runtime_integrity': records['runtime'].get('status') == 'passed_scoped_Windows_change_time_integrity_fix'
            and validation['runtime'].get('status') == 'passed',
        'native_autonomous_supplement': records['native_reparse'].get('status') == 'technical_native_workflow_passed_quality_pending'
            and all(records['native_reparse'].get(name, {}).get('model_run', {}).get('status') == 'success'
                and records['native_reparse'].get(name, {}).get('partial') is False for name in ('parent', 'child'))
            and all(_replay(records['native_reparse'].get(name, {}).get(key), agent=key == 'agent_replay')
                for name in ('parent', 'child') for key in ('kernel_replay', 'agent_replay'))
            and all(records['native_reparse'].get(key) is True for key in
                ('parent_unchanged', 'numeric_value_from_original_pdf', 'page_evidence_available',
                 'actual_native_staged_loop', 'source_unchanged', 'user_profile_unchanged'))
            and records['native_reparse'].get('plaintext_secret_found') is False
            and type(records['native_reparse'].get('child', {}).get('summary', {}).get('verified')) is int
            and records['native_reparse']['child']['summary']['verified'] > 0,
        'generated_mechanism': validation['generated_metrics'].get('status') == 'passed'
            and type(records['generated_metrics'].get('false_accepts')) is int and records['generated_metrics']['false_accepts'] == 0
            and type(records['generated_metrics'].get('strict_match', {}).get('rate')) in {int, float}
            and records['generated_metrics']['strict_match']['rate'] == 1,
    }
    result = {'schema_version': 'mingjing-development-evidence.v2',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'status': 'development_checks_passed_final_acceptance_pending' if all(checks.values()) else 'needs_improvement',
        'professional_acceptance': False, 'release_acceptance': False, 'series': args.series,
        'checks': checks, 'failed_checks': [name for name, passed in checks.items() if not passed],
        'receipt_statuses': {name: row.get('status', 'unknown') for name, row in records.items()},
        'validation': validation, 'regression': regression, 'frontend': frontend, 'live': live,
        'generated_mechanism': {key: records['generated_metrics'].get(key)
            for key in ('total', 'strict_match', 'false_accepts')},
        'evidence': evidence,
        'native_autonomous_supplement': records['native_reparse'],
        'followups': {name: {key: records[name].get(key, 'unknown') for key in
            ('status', 'usage', 'within_budget', 'answer_present', 'message_replay_passed',
             'numeric_evidence_complete', 'source_evidence_verified_against_original')}
            for name in ('numeric_message', 'knowledge_message')},
        'previous_failed_attempts': _prior_failures(args.series),
        'native_Web': {key: records['native_web'].get(key) for key in
            ('status', 'http_requests', 'provider', 'model', 'summary', 'selected_original_pdf_page',
             'user_profile_unchanged', 'kernel_replay', 'agent_replay')},
        'unexecuted_release_prerequisites': ['three_independent_human_reviews', 'professional_rule_signoff',
            'external_20_item_review', 'independent_full_report_recall', 'sealed_fresh_D3',
            'Kimi_and_GPT_real_API_without_credentials', 'final_EXE_build_after_quality_pass', 'independent_clean_Windows'],
        'quality_metrics_without_independent_gold': 'unknown',
        'notes': ['Generated fixtures are mechanism checks, not independent financial accuracy.',
            'A settled coverage ledger is not verified financial coverage or measured mention recall.',
            'Historical receipts retain their original runtime/configuration locks; no lock is replaced.',
            'Hashes verify consistency with nominated frozen artifacts, not independent witnessing of providers or human review.']}
    if (not args.out.resolve().is_relative_to(ROOT.resolve()) or any(
            re.search(r'(^|[-_])d3($|[-_])', part, re.I) for part in args.out.parts)):
        raise ValueError('Development summary output must remain within the current project')
    args.out.mkdir(parents=True, exist_ok=False)
    (args.out/'acceptance.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--freeze-code', type=Path, help='Write a new source lock before starting regression')
    parser.add_argument('--series')
    parser.add_argument('--regression', type=Path)
    parser.add_argument('--web-receipt', type=Path)
    parser.add_argument('--runtime-receipt', type=Path)
    parser.add_argument('--code-lock', type=Path)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    if args.freeze_code:
        if (not args.freeze_code.resolve().is_relative_to(ROOT.resolve()) or any(
                re.search(r'(^|[-_])d3($|[-_])', part, re.I) for part in args.freeze_code.parts)):
            parser.error('Source lock must remain in the development project, outside D3')
        args.freeze_code.parent.mkdir(parents=True, exist_ok=True)
        with args.freeze_code.open('x', encoding='utf-8') as stream:
            json.dump(source_lock(), stream, ensure_ascii=False, indent=2)
        print(json.dumps({'status': 'development_source_locked', 'path': str(args.freeze_code)}))
        return 0
    if any(getattr(args, key) is None for key in ('series', 'regression', 'web_receipt', 'runtime_receipt', 'code_lock', 'out')):
        parser.error('Collection requires series, regression, web-receipt, runtime-receipt, code-lock and out')
    if not args.series or any(char not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for char in args.series):
        parser.error('Series must use lower-case letters, digits and hyphens')
    result = collect(args)
    print(json.dumps({'status': result['status'], 'checks': result['checks'],
        'regression': result['regression'], 'release_acceptance': False}, ensure_ascii=False))
    return 0 if all(result['checks'].values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
