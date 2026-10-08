"""Release receipts cannot convert legacy API or unexecuted gates into acceptance."""
import hashlib
import importlib.util
import json
import runpy
from types import SimpleNamespace
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    spec = importlib.util.spec_from_file_location("mingjing_test_" + name, ROOT / "scripts" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def finance_fixture(root):
    from test_finance_carrier_integrity import make_carrier
    carrier = root/'runtime/finance-carrier/staged'
    manifest = make_carrier(carrier)
    snapshot = root/'runtime/harness/source_snapshot.json'
    snapshot.parent.mkdir(parents=True)
    provenance = {'upstream_version': '0.1.6-alpha.2', 'source_sha256': 'a'*64, 'git_commit': None,
                  'pnpm_lock_sha256': 'b'*64, 'files': [{'path': 'python/sdk/client.py', 'sha256': 'c'*64, 'bytes': 1}]}
    snapshot.write_text(json.dumps(provenance), encoding='utf-8')
    manifest.update(harness_version=provenance['upstream_version'], source_sha256=provenance['source_sha256'],
                    git_commit=None, source_snapshot_sha256=hashlib.sha256(snapshot.read_bytes()).hexdigest(), node_version='fixture')
    (carrier/'carrier_manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    return carrier, manifest


def candidate_receipts(tmp_path, version="1.0.0rc1"):
    exe = tmp_path / "MingJing.exe"
    exe.write_bytes(b"fixture-receipt-only-no-real-executable")
    digest = hashlib.sha256(exe.read_bytes()).hexdigest()
    replay = {"status": "passed", "recomputed": True, "integrity_ok": True, "numeric_equal": True, "network_calls": 0}
    receipt = {"version": version, "exe_sha256": digest, "source_sha256": "frozen-source",
               "status": "passed_scoped_native_agent_and_exe", "profile_isolated": True,
               "clean_path_no_python_node": True, "checks": {
                   "secret_scan": "passed", "user_profile_unchanged": True,
                   "capabilities": {"pdf": True, "harness": True},
                   "real_api": {"model": {"status": "success"}},
                   "native_agent": {"status": "passed", "native_runtime": True, "tool_roundtrip": True},
                   "replays": [replay] * 3}}
    build = {"version": version, "sha256": digest, "source_sha256": "frozen-source", "bytes": exe.stat().st_size}
    acceptance, compiled = tmp_path / "acceptance.json", tmp_path / "build.json"
    acceptance.write_text(json.dumps(receipt), encoding="utf-8")
    compiled.write_text(json.dumps(build), encoding="utf-8")
    return exe, acceptance, compiled, receipt


def test_candidate_requires_exact_native_binary_and_source_epoch(tmp_path):
    module = load_script("package_delivery")
    exe, acceptance, compiled, receipt = candidate_receipts(tmp_path)
    assert module.verify_receipts(exe, acceptance, compiled, "1.0.0rc1")[1]
    receipt["source_sha256"] = "different-source"
    acceptance.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(SystemExit, match="source"):
        module.verify_receipts(exe, acceptance, compiled, "1.0.0rc1")


def test_legacy_prompt_receipt_cannot_approve_native_candidate(tmp_path):
    module = load_script("package_delivery")
    exe, acceptance, compiled, receipt = candidate_receipts(tmp_path)
    receipt["status"] = "passed_scoped_real_api_and_exe"
    acceptance.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(SystemExit, match="legacy"):
        module.verify_receipts(exe, acceptance, compiled, "1.0.0rc1")


def test_unexecuted_gold_and_clean_vm_block_formal_release(tmp_path):
    module = load_script("package_delivery")
    exe, acceptance, compiled, receipt = candidate_receipts(tmp_path, "1.0.0")
    with pytest.raises(SystemExit, match="G0"):
        module.verify_receipts(exe, acceptance, compiled, "1.0.0")
    receipt["release_gates"] = {gate: "passed" for gate in ("G0", "G1", "G4", "G5", "G6", "G7")}
    acceptance.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(SystemExit, match="human"):
        module.verify_receipts(exe, acceptance, compiled, "1.0.0")


def test_runtime_manifest_includes_required_sidecar_and_detects_tampering(tmp_path):
    module = load_script("build_receipt")
    carrier, manifest = finance_fixture(tmp_path)
    _, paths = module.runtime_assets(tmp_path)
    assert len(paths) == len(manifest['files'])+2
    assert any("node_modules" in path.parts for path in paths)
    assert not any('office' in path.parts for path in paths)
    (carrier / "finance-plugin/index.mjs").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="differs"):
        module.runtime_assets(tmp_path)


def test_packaging_rejects_application_source_changed_after_acceptance(tmp_path, monkeypatch):
    import sys
    delivery = load_script("package_delivery")
    builder = load_script("build_receipt")
    monkeypatch.setitem(sys.modules, "build_receipt", builder)
    finance_fixture(tmp_path)
    source = tmp_path / "mingjing/providers.py"
    source.parent.mkdir()
    source.write_text("# accepted source\n", encoding="utf-8")
    for name in ("LICENSE", "MingJing.spec", "app_entry.py", "__init__.py", "pyproject.toml", "requirements-build.lock", "requirements-build.json", "scripts/build_receipt.py", "scripts/build_exe.ps1"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")
    _, assets = builder.runtime_assets(tmp_path)
    inputs = builder.build_inputs(tmp_path, assets)
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"inputs": inputs, "source_sha256": builder.canonical_hash(inputs)}), encoding="utf-8")
    delivery.verify_current_build_inputs(tmp_path, receipt)
    source.write_text("# changed after build\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="Current application source"):
        delivery.verify_current_build_inputs(tmp_path, receipt)


def test_payload_build_is_cached_and_source_and_distribution_are_both_locked(tmp_path, monkeypatch):
    module = load_script('build_receipt')
    finance_fixture(tmp_path)
    prepared = module.prepare_finance_payload(tmp_path)
    assert {path.name for path in prepared} == {'carrier_manifest.json', 'finance-carrier.payload.zip', 'payload.json'}
    original = [(path, path.stat().st_mtime_ns, path.read_bytes()) for path in prepared]
    assert module.prepare_finance_payload(tmp_path) == prepared
    assert all(path.stat().st_mtime_ns == stamp and path.read_bytes() == raw for path, stamp, raw in original)
    _, assets = module.runtime_assets(tmp_path)
    assert all(path in assets for path in prepared)
    assert any(path.name == 'native.node' for path in assets)
    descriptor = prepared[1]
    assert descriptor.name == 'payload.json'
    data = json.loads(descriptor.read_text(encoding='utf-8'))
    data['sha256'] = '0'*64
    descriptor.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError, match='distribution receipt'):
        module.finance_payload_assets(tmp_path)


def test_provenance_and_npm_resolution_fail_build_before_payload_creation(tmp_path):
    module = load_script('build_receipt')
    carrier, manifest = finance_fixture(tmp_path)
    source = tmp_path/'runtime/harness/source_snapshot.json'
    source.write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='provenance'):
        module.prepare_finance_payload(tmp_path)
    assert not (tmp_path/'runtime/finance-carrier/bundles').exists()


def test_legacy_full_sdk_manifest_cannot_replace_the_locked_finance_carrier(tmp_path):
    module = load_script('build_receipt')
    old = tmp_path/'runtime/harness/manifest.json'
    old.parent.mkdir(parents=True)
    old.write_text('{"format_version":2,"executable":"runtime.exe","files":[]}', encoding='utf-8')
    before = old.read_bytes()
    with pytest.raises(ValueError, match='Finance runtime asset'):
        module.runtime_assets(tmp_path)
    assert old.read_bytes() == before


def test_spec_embeds_three_carrier_files_and_verifies_frontend_before_build(tmp_path, monkeypatch):
    import PyInstaller.utils.hooks as hooks
    import scripts.build_frontend as frontend
    builder = load_script('build_receipt')
    finance_fixture(tmp_path)
    prepared = builder.prepare_finance_payload(tmp_path)
    calls = []
    monkeypatch.setattr(frontend, 'verify', lambda root: calls.append(('frontend', root)))
    monkeypatch.setattr(hooks, 'collect_data_files', lambda *_: [])
    monkeypatch.setattr(hooks, 'copy_metadata', lambda *_args, **_kwargs: [])
    monkeypatch.setattr(hooks, 'collect_submodules', lambda *_args, **_kwargs: [])
    monkeypatch.setenv('MINGJING_BUILD_MODE', 'onefile')
    def analyze(*_args, **kwargs):
        assert _args[0] == [str(tmp_path/'scripts/launch_exe.py')]
        calls.append(('datas', kwargs['datas']))
        graph = SimpleNamespace(find_node=lambda name: SimpleNamespace(filename=str(tmp_path/'mingjing/__init__.py')))
        return SimpleNamespace(pure=[], scripts=[], binaries=[], datas=kwargs['datas'], graph=graph)
    runpy.run_path(str(ROOT/'MingJing.spec'), init_globals={'SPEC':str(tmp_path/'MingJing.spec'),
                   'Analysis':analyze, 'PYZ':lambda *_: [], 'EXE':lambda *_args, **_kwargs: None})
    assert calls[0] == ('frontend', tmp_path)
    datas = calls[1][1]
    carrier_files = [Path(source) for source, target in datas if target == 'runtime/finance-carrier']
    assert set(carrier_files) == set(prepared)
    assert not any('node_modules' in Path(source).parts for source, _ in datas)
    assert (str(tmp_path/'runtime/harness/source_snapshot.json'), 'runtime/harness') in datas


def test_frozen_entry_does_not_infer_historical_sibling_package_root():
    from PyInstaller.utils.misc import get_path_to_toplevel_modules
    # The former root app_entry.py infers mainProj because of its __init__.py
    # shim. The build entry must start search inside scripts then this checkout.
    entry = ROOT / 'scripts/launch_exe.py'
    assert Path(get_path_to_toplevel_modules(str(entry))).resolve() == ROOT / 'scripts'
