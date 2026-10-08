"""Capability discovery must recognize the actual frozen payload layout."""
from pathlib import Path

import pytest

from mingjing.server import _harness_runtime_declared


def put(root: Path, name: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'fixture; not a validated runtime')


def test_frozen_compressed_carrier_is_declared_without_extraction(tmp_path):
    for name in ('carrier_manifest.json', 'payload.json', 'finance-carrier.payload.zip'):
        put(tmp_path, 'runtime/finance-carrier/' + name)
    assert not (tmp_path / 'runtime/finance-carrier/staged').exists()
    assert _harness_runtime_declared(tmp_path) is True


@pytest.mark.parametrize('missing', ('carrier_manifest.json', 'payload.json', 'finance-carrier.payload.zip'))
def test_partial_frozen_payload_is_unavailable(tmp_path, missing):
    for name in ('carrier_manifest.json', 'payload.json', 'finance-carrier.payload.zip'):
        if name != missing:
            put(tmp_path, 'runtime/finance-carrier/' + name)
    assert _harness_runtime_declared(tmp_path) is False


@pytest.mark.parametrize('layout', (
    'runtime/finance-carrier/staged/carrier_manifest.json',
    'runtime/harness/deepseek-harness-sdk-runtime-win-x64.exe',
))
def test_development_and_legacy_presence_remain_declared(tmp_path, layout):
    assert _harness_runtime_declared(tmp_path) is False
    put(tmp_path, layout)
    assert _harness_runtime_declared(tmp_path) is True
