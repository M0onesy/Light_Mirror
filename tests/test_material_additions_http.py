"""The HTTP material gate exposes the same controlled append API as sessions."""
import urllib.error

import pytest

from test_server_analysis_product import local_web


@pytest.mark.parametrize('endpoint', ['/api/sessions/one/materials', '/api/agent/sessions/one/materials'])
def test_multiple_material_paths_reach_session_version_builder(local_web, tmp_path, monkeypatch, endpoint):
    state, request = local_web
    sources = [tmp_path/'2024.md', tmp_path/'2023.md']
    for source in sources:
        source.write_text('Unmodified local source material', encoding='utf-8')
    received = []
    monkeypatch.setattr(state.sessions, 'add_materials', lambda sid, payload:
        received.append((sid, payload)) or {'run_id': 'new-version', 'new_version': True})
    response = request(endpoint, {'corpus_additions': [str(source) for source in sources]}, 'POST')
    assert response['new_version'] and response['run_id'] == 'new-version'
    assert received == [('one', {'corpus_additions': [str(source.resolve()) for source in sources]})]


@pytest.mark.parametrize('additions', [[], 'not-an-array', [9], [''], ['/missing-original-material.pdf']])
def test_invalid_additions_never_reach_the_version_builder(local_web, monkeypatch, additions):
    state, request = local_web
    received = []
    monkeypatch.setattr(state.sessions, 'add_materials', lambda *args: received.append(args))
    with pytest.raises(urllib.error.HTTPError) as rejected:
        request('/api/sessions/one/materials', {'corpus_additions': additions}, 'POST')
    assert rejected.value.code == 400 and not received
