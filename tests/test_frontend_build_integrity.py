"""A new UI source cannot be shipped with the preceding compiled JavaScript."""
import json

import pytest

from scripts.build_frontend import input_files, output_files, verify


@pytest.fixture
def build(tmp_path):
    source = tmp_path/'frontend'/'src'/'App.tsx'
    source.parent.mkdir(parents=True)
    source.write_text('export const App = () => "current"', encoding='utf-8')
    web = tmp_path/'web'/'harness'
    web.mkdir(parents=True)
    (web/'index.html').write_text('<script src="app.js"></script>', encoding='utf-8')
    (web/'app.js').write_text('current()', encoding='utf-8')
    record = {'kind': 'mingjing-harness-ui-build.v1', 'status': 'built',
              'inputs': input_files(tmp_path), 'outputs': output_files(tmp_path),
              'source_sha256': 'fixture-only', 'assets_sha256': 'fixture-only'}
    (web/'build_receipt.json').write_text(json.dumps(record), encoding='utf-8')
    return tmp_path


def test_changed_source_or_static_bytes_cannot_use_previous_build_receipt(build):
    assert verify(build)['status'] == 'passed_source_to_assets'
    (build/'frontend'/'src'/'App.tsx').write_text('fixed-security-check()', encoding='utf-8')
    with pytest.raises(ValueError, match='source changed'):
        verify(build)


def test_static_asset_corruption_is_detected(build):
    (build/'web'/'harness'/'app.js').write_text('old-bug()', encoding='utf-8')
    with pytest.raises(ValueError, match='assets changed'):
        verify(build)


def test_new_runtime_asset_must_be_part_of_the_same_build(build):
    (build/'web'/'harness'/'extra.js').write_text('injected()', encoding='utf-8')
    with pytest.raises(ValueError, match='assets changed'):
        verify(build)
