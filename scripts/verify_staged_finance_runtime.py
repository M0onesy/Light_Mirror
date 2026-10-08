"""Verify the deployed finance carrier with its own Node and bounded imports.

This runs a local keyless provider fixture. It neither copies packages nor
builds an EXE, and cannot certify a clean Windows installation or vendor API.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    from mingjing.harness_client import HarnessRuntime, runtime_fingerprint, _native_cache_root
    from scripts import build_harness_smoke
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT/'runs/staged-finance-import-isolation')
    parser.add_argument('--protocol', choices=('deepseek', 'openai-completions'), default='deepseek')
    args = parser.parse_args()
    output = args.out.resolve()
    if output.exists():
        raise ValueError('Choose a new output directory; prior sessions and receipts stay unchanged')
    output.mkdir(parents=True)
    fingerprint = runtime_fingerprint()
    launch = fingerprint['launch']
    if launch.get('deployment') != 'finance-carrier':
        raise ValueError('This diagnostic requires the actual staged finance carrier')
    probe = HarnessRuntime(output/'unused-probe-home', lambda *_: {})
    try:
        carrier = probe._assets()
    finally:
        probe.close()
    trace = output/'resolved-modules.jsonl'
    guard = output/'carrier-import-guard.mjs'
    manifest = json.loads((carrier/'carrier_manifest.json').read_text(encoding='utf-8'))
    native_hashes = [entry['sha256'] for entry in manifest['files'] if entry['path'].endswith('.node')]
    native_cache = _native_cache_root({})
    config = json.dumps({'carrier': str(carrier), 'home': str(output), 'trace': str(trace),
                         'native_cache': str(native_cache), 'native_hashes': native_hashes}, ensure_ascii=False)
    guard.write_text("""import { registerHooks } from 'node:module';
import { appendFileSync, readFileSync, realpathSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { relative, isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';
const config = CONFIG;
const roots = [config.carrier, config.home].map(value => realpathSync(value));
function within(path, root) {
  const tail = relative(root, path);
  return tail === '' || (!isAbsolute(tail) && !/^\\.\\.(?:[\\\\/]|$)/u.test(tail));
}
registerHooks({resolve(specifier, context, nextResolve) {
  const result = nextResolve(specifier, context);
  if (result.url.startsWith('file:')) {
    const path = realpathSync(fileURLToPath(result.url));
    let materializedNative = false;
    if (!roots.some(root => within(path, root))) {
      // The upstream Windows native loader intentionally materializes a
      // short-path binary copy and itself checks it against its source.
      // Independently enforce the compiled closure hash for that exact
      // cache directory; never allow cached JavaScript or an unknown binary.
      materializedNative = within(path, config.native_cache) && path.endsWith('.node')
        && config.native_hashes.includes(createHash('sha256').update(readFileSync(path)).digest('hex'));
      if (!materializedNative) throw Error('FINANCE_IMPORT_ESCAPED_LOCKED_CARRIER');
    }
    appendFileSync(config.trace, JSON.stringify({specifier, url:result.url, resolved_path:path, materialized_native:materializedNative})+'\\n');
  } else if (!result.url.startsWith('node:')) {
    throw Error('FINANCE_IMPORT_NONLOCAL_PROTOCOL');
  }
  return result;
}});
""".replace('CONFIG', config), encoding='utf-8')

    class IsolatedCarrier(HarnessRuntime):
        def _launch(self):
            command = super()._launch()
            if command != [str(carrier/launch['node_resource']), str(carrier/launch['entry_resource'])]:
                raise ValueError('The diagnostic must use its locked standalone Node and entry')
            return [command[0], '--import', guard.as_uri(), command[1]]

    build_harness_smoke.HarnessRuntime = IsolatedCarrier
    original_argv, original_path = sys.argv, os.environ.get('PATH')
    os.environ['PATH'] = str(Path(os.environ.get('SYSTEMROOT', 'C:/Windows'))/'System32')
    sys.argv = ['staged-finance-runtime', '--out', str(output), '--protocol', args.protocol]
    try:
        build_harness_smoke.main()
    finally:
        sys.argv = original_argv
        build_harness_smoke.HarnessRuntime = HarnessRuntime
        if original_path is None:
            os.environ.pop('PATH', None)
        else:
            os.environ['PATH'] = original_path
    records = [json.loads(line) for line in trace.read_text(encoding='utf-8').splitlines()]
    receipt = json.loads((output/'acceptance.json').read_text(encoding='utf-8'))
    receipt.update(status='passed_staged_finance_runtime_functional_fixture',
                   runtime_lock=fingerprint, actual_carrier_bytes=launch['actual_bytes'],
                   dependency_lock_sha256=launch['dependency_lock_sha256'],
                   standalone_node=True, system_node_path_removed=True,
                   upstream_node_modules_allowed=False, resolved_file_imports=len(records),
                   materialized_native_imports=sum(row['materialized_native'] for row in records),
                   runtime_lock_schema_version=2,
                   native_cache_policy='mingjing_owned_generation_only_native_files_matching_locked_carrier_sha256',
                   import_guard_sha256=sha(guard), import_trace_sha256=sha(trace),
                   evidence_kind='original_harness_loop_keyless_fixture_with_import_guard',
                   mingjing_exe_built=False, clean_windows_acceptance='not_executed',
                   real_provider_api='not_executed')
    (output/'acceptance.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'status': receipt['status'], 'actual_carrier_bytes': launch['actual_bytes'],
                      'resolved_file_imports': len(records), 'live_tools': receipt['live_tools'],
                      'cross_process_resume': receipt['cross_process_resume'],
                      'replay_network_requests': receipt['replay_network_requests']}, ensure_ascii=False))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
