"""Local acceptance preflight and receipt helpers; never invokes a provider."""
from __future__ import annotations

from pathlib import Path
import json

from mingjing.evidence import sha256_file
from mingjing.http_transport import redact_value, safe_error_message


def page_evidence_diagnostic(run: Path, rows: list[dict], resources: dict) -> dict:
    """Expose absent header/scope previews instead of one opaque failure flag.

    Source references are counted by both occurrence and unique ID. This
    validates only artifact availability/integrity, not financial truth.
    """
    from mingjing.config import validate_path
    required = [identifier for row in rows for identifier in row.get('evidence', [])]
    failures, available, integrity_known = [], 0, True
    for identifier in sorted(set(required)):
        resource = resources.get(identifier)
        if not resource:
            failures.append({'evidence_id': identifier, 'reason': 'resource_not_registered'})
            continue
        try:
            raw = resource.get('image')
            if not isinstance(raw, str) or Path(raw).is_absolute():
                raise ValueError('invalid image resource')
            image = validate_path(run/raw, allowed_roots=[str(run)], kind='file',
                                  must_exist=True, follow_symlinks=False)
            with image.open('rb') as stream:
                if stream.read(8) != b'\x89PNG\r\n\x1a\n':
                    failures.append({'evidence_id': identifier, 'reason': 'invalid_png'})
                    continue
            expected = resource.get('image_sha256')
            if expected is not None and sha256_file(image) != expected:
                failures.append({'evidence_id': identifier, 'reason': 'image_hash_mismatch'})
                continue
            integrity_known &= expected is not None
            available += 1
        except (OSError, ValueError):
            failures.append({'evidence_id': identifier, 'reason': 'image_missing_or_unsafe_path'})
    rows_have_evidence = all(bool(row.get('evidence')) for row in rows)
    return {'required_rows': len(rows), 'required_references': len(required),
            'required_unique_evidence': len(set(required)), 'available_unique_evidence': available,
            'failed_unique_evidence': len(failures), 'failures': failures,
            'all_required_rows_have_evidence': rows_have_evidence,
            'image_integrity_known': integrity_known if required and not failures else None,
            'all_available': rows_have_evidence and not failures,
            'financial_truth': 'not_assessed_by_artifact_diagnostic'}


EVENT_KINDS = ('accounting_policy_change', 'accounting_estimate_change',
               'prior_period_error', 'disclosure_version_change')


def positive_integer(value, name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f'{name} must be a positive integer')
    return value


def nonempty_text(value, name: str, *, maximum: int = 3000) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be nonempty text')
    if len(value.encode('utf-16-le')) // 2 > maximum or any(ord(c) < 32 and c not in '\n\r\t' for c in value):
        raise ValueError(f'{name} contains invalid characters or exceeds {maximum} UTF-16 characters')
    return value.strip()


def profile_hashes(root: Path) -> dict[Path, str | None]:
    return {path: sha256_file(path) if path.is_file() else None
            for path in (root/'config.json', root/'secrets.json')}


def profile_unchanged(before: dict[Path, str | None]) -> bool | None:
    if not before:
        return None
    return all((path.is_file() and sha256_file(path) == expected) if expected is not None
               else not path.exists() for path, expected in before.items())


def verify_seed_materials(run: Path, paths) -> dict:
    """Reuse historical materials without treating historical code as current.

    A seed is not a session resume. Check its frozen source bytes if recorded,
    while deliberately leaving historical kernel/runtime locks unchanged.
    """
    registered = {}
    for name, key in (('agent_lock.json', 'resources'), ('run_manifest.json', 'inputs')):
        lock = run/name
        if not lock.is_file():
            continue
        for entry in json.loads(lock.read_text(encoding='utf-8')).get(key, []):
            if entry.get('path') and entry.get('sha256'):
                source = Path(entry['path']).resolve()
                previous = registered.get(source)
                if previous is not None and previous != entry['sha256']:
                    raise ValueError('Historical seed has conflicting material hashes')
                registered[source] = entry['sha256']
    checked = []
    for raw in paths:
        if not raw:
            continue
        path = Path(raw).resolve()
        if path in registered:
            if not path.is_file() or sha256_file(path) != registered[path]:
                raise ValueError('Historical seed material bytes changed; use a newly reviewed source')
            checked.append(str(path))
    return {'status': 'passed' if checked else 'no_source_hash_registered', 'checked_files': checked,
            'current_kernel_compatibility': 'not_required_for_new_material_task'}


class CredentialReader:
    """Track resolved credentials in memory solely for output redaction."""
    def __init__(self, store):
        self.store = store
        self.values: set[str] = set()

    def get(self, reference):
        try:
            value = self.store.get(reference)
        except Exception as exc:
            # A failing credential backend may include an as-yet unresolved
            # key in its exception; its raw message cannot be safely retained.
            raise ValueError(f'Credential resolution failed ({type(exc).__name__})') from None
        if isinstance(value, str) and value:
            self.values.add(value)
        return value

    def read_configured(self, config):
        for provider in config.get('providers', {}).values():
            if provider.get('credential_ref'):
                self.get(provider['credential_ref'])


def open_credentials(factory, root):
    try:
        return CredentialReader(factory(root))
    except Exception as exc:
        raise ValueError(f'Credential store initialization failed ({type(exc).__name__})') from None


def finish_receipt(receipt: dict, out: Path, before, credentials, *, filename: str) -> dict:
    from mingjing.agent import write_json
    values = credentials.values if credentials is not None else set()
    receipt = redact_value(receipt, values)
    try:
        receipt['user_profile_unchanged'] = profile_unchanged(before)
    except Exception as exc:
        receipt.update(status='failed_security_check', profile_check_error=safe_error_message(exc, values))
    # Include the completed receipt in the scan rather than scanning only
    # artifacts created before the final error diagnosis was persisted.
    write_json(out/filename, receipt)
    files = [path for path in out.rglob('*') if path.is_file()
             and path.suffix.lower() in {'.json', '.jsonl', '.md', '.html', '.log', '.yml'}]
    encoded = [value.encode('utf-8') for value in values]
    try:
        leak = any(any(secret in path.read_bytes() for secret in encoded) for path in files)
        receipt['secret_scan'] = {'files_scanned': len(files), 'plaintext_found': leak}
        if leak or receipt.get('user_profile_unchanged') is False:
            receipt['status'] = 'failed_security_check'
    except Exception as exc:
        receipt.update(status='failed_security_check', secret_scan={
            'status': 'failed', 'error': safe_error_message(exc, values)})
    write_json(out/filename, receipt)
    return receipt
