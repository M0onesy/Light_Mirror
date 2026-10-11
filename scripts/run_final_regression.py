"""Prepare or execute the one final repository pytest run; no automatic rerun."""
from __future__ import annotations

import argparse
import ast
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / 'evidence' / 'regression' / dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def inspection() -> dict:
    """Static collection review only; never import test modules or call pytest."""
    import configparser

    config = configparser.ConfigParser()
    config.read(ROOT / 'pytest.ini', encoding='utf-8')
    testpaths = config['pytest'].get('testpaths', '').split()
    if testpaths != ['tests']:
        raise ValueError('Final regression requires pytest.ini testpaths = tests')
    files = sorted((ROOT / 'tests').rglob('test_*.py'))
    escaped = [str(path) for path in files if ROOT / 'tests' not in path.resolve().parents]
    hooks = [str(path.relative_to(ROOT)) for path in (ROOT / 'tests').rglob('conftest.py')]
    if (ROOT / 'conftest.py').exists():
        hooks.insert(0, 'conftest.py')
    functions = 0
    for path in files:
        tree = ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
        functions += sum(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                         and node.name.startswith('test_') for node in ast.walk(tree))
    return {'mode': 'static_only_no_pytest_import_or_execution', 'testpaths': testpaths,
            'root_config': str(ROOT / 'pytest.ini'), 'test_files': len(files),
            'test_function_definitions': functions,
            'definition_count_is_not_collected_count': True,
            'files_outside_tests': escaped, 'conftest_files': hooks,
            'vendor_and_old_projects_are_outside_testpaths': True,
            'test_file_sha256': {str(path.relative_to(ROOT)): sha(path) for path in files},
            'environment_pytest_addopts_present': bool(os.environ.get('PYTEST_ADDOPTS'))}


def junit_details(path: Path) -> dict:
    if not path.exists():
        return {'present': False, 'counts': {}, 'failure_groups': []}
    root = ET.parse(path).getroot()
    suites = list(root.iter('testsuite'))
    counts = {name: sum(int(suite.get(name, '0')) for suite in suites)
              for name in ('tests', 'errors', 'failures', 'skipped')}
    failures = []
    groups: dict[tuple[str, str], list[str]] = {}
    for case in root.iter('testcase'):
        selector = case.get('classname', '').replace('.', '/') + '::' + case.get('name', '')
        for kind in ('failure', 'error'):
            detail = case.find(kind)
            if detail is None:
                continue
            message = detail.get('message', '')
            # Keep concrete messages; final root-cause interpretation follows
            # inspection of full.log rather than an automatic repair or rerun.
            signature = re.sub(r'0x[0-9a-fA-F]+', '0xADDRESS', message.splitlines()[0] if message else kind)
            failures.append({'selector': selector, 'kind': kind, 'message': message,
                             'traceback': detail.text or ''})
            groups.setdefault((kind, signature), []).append(selector)
    return {'present': True, 'counts': counts, 'failures': failures,
            'failure_groups': [{'kind': key[0], 'message_signature': key[1],
                                'count': len(selectors), 'selectors': selectors}
                               for key, selectors in groups.items()],
            'failure_grouping': 'message fingerprints; root causes require log review'}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', type=Path, default=DEFAULT_OUT)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--prepare', action='store_true')
    action.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    static = inspection()
    if static['files_outside_tests']:
        raise ValueError('Test paths resolve outside the current tests directory')
    command = [sys.executable, '-X', 'utf8', '-B', '-m', 'pytest', '-q',
               '--junitxml', str(out / 'junit.xml')]
    prepared = {'schema': 'mingjing.final-regression-preparation.v1',
                'recorded_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
                'cwd': str(ROOT), 'command': command, 'log_path': str(out / 'full.log'),
                'execution_authorization': 'wait for parent compact freeze and small04 pass notification',
                'test_execution_started': False, 'inspection': static,
                'executor_sha256': sha(Path(__file__))}
    if args.prepare:
        path = out / 'preparation.json'
        if path.exists():
            raise FileExistsError('Preparation receipt already exists; preserve it')
        write(path, prepared)
        print(json.dumps({'prepared': str(path), 'pytest_started': False,
                          'test_files': static['test_files'],
                          'test_function_definitions': static['test_function_definitions']}, ensure_ascii=False))
        return 0
    outputs = [out / name for name in ('full.log', 'junit.xml', 'receipt.json')]
    if any(path.exists() for path in outputs):
        raise FileExistsError('Final run outputs already exist; no automatic full rerun is permitted')
    started_utc = dt.datetime.now(dt.timezone.utc).isoformat()
    started = time.perf_counter()
    with (out / 'full.log').open('wb') as log:
        process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        write(out / 'running.json', {'schema': 'mingjing.final-regression-running.v1',
                                    'pid': process.pid, 'started_utc': started_utc,
                                    'command': command, 'cwd': str(ROOT), 'log_path': str(out / 'full.log')})
        code = process.wait()
    elapsed = time.perf_counter() - started
    details = junit_details(out / 'junit.xml')
    receipt = {'schema': 'mingjing.final-regression-receipt.v1', 'started_utc': started_utc,
               'finished_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
               'cwd': str(ROOT), 'command': command, 'exit_code': code,
               'duration_seconds': elapsed, 'pytest_collected_test_count': details['counts'].get('tests'),
               'inspection': static, 'junit': details,
               'artifacts': {path.name: {'bytes': path.stat().st_size, 'sha256': sha(path)}
                             for path in outputs[:2] if path.exists()},
               'executor_sha256': sha(Path(__file__)),
               'automatic_rerun': False, 'automatic_repair': False}
    write(out / 'receipt.json', receipt)
    print(json.dumps({'receipt': str(out / 'receipt.json'), 'exit_code': code,
                      'counts': details['counts'], 'duration_seconds': elapsed,
                      'failure_groups': details['failure_groups']}, ensure_ascii=False))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
