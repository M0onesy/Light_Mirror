"""Exercise the finance-only entry against existing Harness Node dependencies.

The probe never deploys packages or builds an executable. A Node import hook
denies packages outside the proposed finance closure while redirecting only the
entry's dsh import to the existing carrier. This is entry/import evidence, not
proof of a physically complete standalone carrier or clean-machine acceptance.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from scripts.plan_finance_runtime import DEFAULT_UPSTREAM, ROOT, create_plan, digest


def write_resolver(path: Path, plan: dict, upstream: Path, trace: Path) -> None:
    names = sorted({entry["name"] for entry in plan["candidate"]["packages"]})
    bootstrap = ROOT / "runtime/finance-carrier/bootstrap.mjs"
    anchor = upstream / "python/sdk-runtime/src/deepseek_harness_runtime/runtime/node/package.json"
    config = json.dumps({"packages": names, "bootstrap": bootstrap.as_uri(), "anchor": anchor.as_uri(),
                         "trace": str(trace.resolve())}, ensure_ascii=False)
    source = """import { registerHooks, builtinModules } from 'node:module';
import { appendFileSync } from 'node:fs';
import { isAbsolute } from 'node:path';
import { fileURLToPath } from 'node:url';
const config = CONFIG;
const allowed = new Set(config.packages);
const builtins = new Set(builtinModules.map(name => name.replace(/^node:/, '')));
function packageName(specifier) {
  if (isAbsolute(specifier) || specifier.startsWith('.') || specifier.startsWith('/') || specifier.startsWith('file:') || specifier.startsWith('#')) return null;
  if (specifier.startsWith('node:') || builtins.has(specifier)) return null;
  return specifier.startsWith('@') ? specifier.split('/').slice(0,2).join('/') : specifier.split('/')[0];
}
registerHooks({ resolve(specifier, context, nextResolve) {
  const name = packageName(specifier);
  if (name && !allowed.has(name)) throw new Error('FINANCE_CLOSURE_DENIED: ' + name);
  const redirected = context.parentURL === config.bootstrap && specifier === '@deepseek-ai/dsh/lib/bin.js';
  const result = nextResolve(specifier, redirected ? { ...context, parentURL: config.anchor } : context);
  const relative = result.url.startsWith('file:') ? fileURLToPath(result.url).replaceAll('\\\\', '/').split('/node_modules/').at(-1) : null;
  const resolvedPackage = relative && relative !== fileURLToPath(result.url).replaceAll('\\\\', '/') ? packageName(relative) : null;
  if (resolvedPackage && !allowed.has(resolvedPackage)) throw new Error('FINANCE_CLOSURE_DENIED_RESOLVED: ' + resolvedPackage);
  if (name || resolvedPackage) appendFileSync(config.trace, JSON.stringify({package:name ?? resolvedPackage,specifier,url:result.url}) + '\\n');
  return result;
} });
""".replace("CONFIG", config)
    path.write_text(source, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "runs/finance-carrier-probe")
    parser.add_argument("--upstream", type=Path, default=DEFAULT_UPSTREAM)
    parser.add_argument("--protocol", choices=("deepseek", "openai-completions"), default="deepseek")
    args = parser.parse_args()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    if (args.out / "home-original").exists():
        raise ValueError("Use a new probe output directory; persisted session receipts are kept unchanged")
    upstream = args.upstream.resolve()
    plan = create_plan(upstream)
    if plan["candidate"]["unresolved"]:
        raise ValueError("Cannot probe a closure with unresolved dependencies")
    node = shutil.which("node")
    if not node:
        raise ValueError("Development Node is not available")
    resolver = args.out / "development-resolver.mjs"
    trace = args.out / "module-imports.jsonl"
    write_resolver(resolver, plan, upstream, trace)
    bootstrap = ROOT / "runtime/finance-carrier/bootstrap.mjs"
    from mingjing.harness_client import HarnessRuntime
    from scripts import build_harness_smoke

    class FinanceCarrierProbe(HarnessRuntime):
        def _launch(self) -> list[str]:
            return [node, "--import", resolver.as_uri(), str(bootstrap)]

        def start(self) -> dict:
            try:
                return super().start()
            except Exception:
                # This probe uses only the local keyless fixture. Persist its
                # startup diagnostic so missing dynamic imports can be fixed.
                (args.out / "keyless-startup-stderr.txt").write_text("\n".join(self._stderr) + "\n", encoding="utf-8")
                raise

    # Reuse the existing black-box fixture: three dependent financial tools,
    # native session events, cached offline replay, cross-process resume/cancel.
    # No modification to HarnessRuntime or the product's launch selection.
    build_harness_smoke.HarnessRuntime = FinanceCarrierProbe
    original_argv = sys.argv
    sys.argv = ["finance-carrier-probe", "--out", str(args.out), "--protocol", args.protocol]
    try:
        build_harness_smoke.main()
    except Exception as exc:
        (args.out / "acceptance.json").write_text(json.dumps({
            "status": "failed_finance_entry_probe", "error_class": type(exc).__name__,
            "error": str(exc), "physical_candidate_deployment": "not_executed", "sea_build": "not_executed",
            "real_provider_api": "not_executed", "clean_windows": "not_executed",
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        raise
    finally:
        sys.argv = original_argv
        build_harness_smoke.HarnessRuntime = HarnessRuntime
    receipt_path = args.out / "acceptance.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    imports = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    receipt.update({"status": "passed_finance_entry_existing_node_carrier",
                    "evidence_kind": "keyless_fixture_original_harness_loop_import_guard",
                    "physical_candidate_deployment": "not_executed", "sea_build": "not_executed",
                    "clean_windows": "not_executed", "real_provider_api": "not_executed",
                    "source_sha256": plan["source_sha256"],
                    "finance_bootstrap_sha256": digest(bootstrap), "resolver_sha256": digest(resolver),
                    "import_trace_sha256": digest(trace), "candidate_packages": plan["candidate"]["package_count"],
                    "observed_packages": sorted({entry["package"] for entry in imports}),
                    "runtime_launch": {"kind": "development_finance_entry_with_import_guard", "node": node,
                                       "bootstrap": str(bootstrap), "existing_carrier": str(upstream)}})
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": receipt["status"], "observed_packages": len(receipt["observed_packages"]),
                      "live_tools": receipt["live_tools"], "cached_replay_tools": receipt["cached_replay_tools"],
                      "cross_process_resume": receipt["cross_process_resume"], "out": str(args.out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
