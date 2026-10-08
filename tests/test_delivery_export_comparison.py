"""A Web view may decorate statuses; exports must preserve signed source bytes."""
import hashlib
import io
import json
from pathlib import Path
import zipfile

import pytest

from scripts.delivery_smoke import financial_rows, verify_export, SmokeFailure


def exported_fixture(tmp_path):
    row = {'claim_id': 'claim-1', 'status': 'verified', 'actual_value': '10.00',
           'expected_value': '10.00', 'formula': None, 'evidence': ['doc:p1:cell']}
    frozen = {'results': [row]}
    resources = {'claims.json': b'[]', 'facts.json': b'[]', 'audit.jsonl': b'{}\n',
                 'results.json': (json.dumps(frozen, ensure_ascii=False, indent=2) + '\n').encode()}
    manifest = {'outputs': [{'path': name, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                            for name, data in resources.items()]}
    resources['run_manifest.json'] = json.dumps(manifest).encode()
    for name, data in resources.items():
        (tmp_path / name).write_bytes(data)
    displayed = {'view_schema': 'run-result-view.v1',
                 'results': [dict(row, execution_status='processed', display_status='verified')]}
    return resources, displayed


def archive(resources):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w') as result:
        for name, raw in resources.items():
            result.writestr(name, raw)
    return zipfile.ZipFile(io.BytesIO(data.getvalue()))


def test_web_status_projection_does_not_change_financial_equivalence(tmp_path):
    resources, displayed = exported_fixture(tmp_path)
    with archive(resources) as output:
        proof = verify_export(output, displayed, tmp_path)
    assert proof['financial_fields_equal'] is True
    assert proof['frozen_bytes_equal'] is True
    assert proof['frozen_outputs_verified'] == 4


@pytest.mark.parametrize('field,new_value', [('expected_value', '999999'), ('status', 'violated'),
                                          ('evidence', ['forged-source']), ('formula', 'free formula')])
def test_any_financial_field_change_is_rejected(tmp_path, field, new_value):
    resources, displayed = exported_fixture(tmp_path)
    displayed['results'][0][field] = new_value
    with archive(resources) as output, pytest.raises(SmokeFailure, match='financial result fields'):
        verify_export(output, displayed, tmp_path)


def test_reformatting_json_without_numeric_change_still_breaks_frozen_bytes(tmp_path):
    resources, displayed = exported_fixture(tmp_path)
    resources['results.json'] = json.dumps(json.loads(resources['results.json'])).encode()
    with archive(resources) as output, pytest.raises(SmokeFailure, match='frozen results JSON bytes'):
        verify_export(output, displayed, tmp_path)


def test_exported_facts_must_match_frozen_manifest_hash(tmp_path):
    resources, displayed = exported_fixture(tmp_path)
    resources['facts.json'] = b'["tampered fact"]'
    with archive(resources) as output, pytest.raises(SmokeFailure, match='frozen output: facts.json'):
        verify_export(output, displayed, tmp_path)


def test_unknown_payload_schema_cannot_hide_added_fields():
    payload = {'results': [{'status': 'verified', 'display_status': 'violated'}]}
    assert financial_rows(payload) == payload['results']
