"""Isolated offline replay adapter; saved resource identities never change.

Only the explicit CLI worker enables this adapter. It presents immutable old
paths to the original replay/tool code, while file IO uses the verified map.
No original record is edited and no mapping is installed in the Web service.
"""
from __future__ import annotations

import argparse
import copy
from functools import lru_cache
import importlib
import json
import os
from pathlib import Path as BasePath
import subprocess
import sys
import tempfile
import types

from .relocation import RelocationError, RelocationMap


def mapped_path_type(mapping, *, exclusions=(), read_mounts=()):
    concrete = type(BasePath())
    def underneath(path, root):
        left, right = path.parts, root.parts
        return len(left) >= len(right) and all(os.path.normcase(a) == os.path.normcase(b) for a, b in zip(left, right))
    logical_path = lru_cache(maxsize=32768)(mapping.logical)
    def excluded(path):
        return (any(underneath(path, root) for root in exclusions)
                and not any(underneath(path, root) for root in read_mounts))
    mounts = {old: new for old, new, _ in mapping.entries if old in read_mounts}
    def physical_path(path, *, strict=False):
        for old, new in mounts.items():
            if underneath(path, old):
                # Only host-created, fully verified carrier mounts qualify.
                # The original runtime still lstat/checks every resource.
                target = new.joinpath(*path.parts[len(old.parts):])
                if strict and not target.exists():
                    raise RelocationError('Verified carrier resource is missing')
                return target
        return path if excluded(path) else mapping.resolve(path, must_exist=strict)

    class ReplayPath(concrete):
        def __init__(self, *args):
            text = str(BasePath(*args))
            if text.startswith('\\\\?\\'):
                text = text[4:]
            value = BasePath(text)
            super().__init__(str(value if excluded(value) else logical_path(value)))

        def __fspath__(self):
            logical = BasePath(super().__str__())
            text = str(physical_path(logical))
            return '\\\\?\\'+text if os.name == 'nt' and len(text) >= 240 else text

        def resolve(self, strict=False):
            logical = BasePath(super().__str__())
            physical = physical_path(logical, strict=strict)
            from .relocation import _io
            physical = _io(physical).resolve(strict=strict)
            if any(underneath(logical, root) for root in read_mounts):
                return self.with_segments(str(logical))
            return type(self)(physical)

        def absolute(self):
            return type(self)(BasePath(os.path.abspath(os.fspath(self))))

    return ReplayPath


def _install(mapping, output, temporary, *, root_logical, read_mounts=(), include_agent=True):
    """Installed only in this short-lived worker, after argument validation."""
    from .agent import orchestration_fingerprint
    from .audit import kernel_fingerprint
    before = {'kernel': kernel_fingerprint(), 'agent': orchestration_fingerprint() if include_agent else None}
    Path = mapped_path_type(mapping, exclusions=(output, temporary), read_mounts=read_mounts)
    # Import first: normal import machinery always uses physical source paths.
    for name in ('replay', 'agent_replay', 'message_replay', 'sessions', 'smart.ingestion', 'smart.docio', 'smart.detect'):
        importlib.import_module('mingjing.'+name)
    for name, module in list(sys.modules.items()):
        if (name.startswith('mingjing.') and name not in {'mingjing.relocation', 'mingjing.relocation_replay'}
                and isinstance(module, types.ModuleType) and vars(module).get('Path') is BasePath):
            module.Path = Path

    original_fitz = None
    try:
        import fitz
        original_fitz = fitz.open
        def pdf_open(filename=None, *args, **kwargs):
            if isinstance(filename, (str, os.PathLike)):
                filename = str(mapping.resolve(mapping.logical(BasePath(os.fspath(filename))), must_exist=True))
            return original_fitz(filename, *args, **kwargs)
        fitz.open = pdf_open
    except ImportError:
        pass

    original_popen = subprocess.Popen
    def process_path(value):
        path = BasePath(str(value))
        if path.is_relative_to(output):
            return str(path)
        return str(mapping.resolve(path))
    def popen(args, *positional, **kwargs):
        if isinstance(args, (list, tuple)):
            args = [process_path(item) if isinstance(item, (str, os.PathLike))
                    and BasePath(str(item)).is_absolute() else item for item in args]
        if kwargs.get('cwd') is not None:
            kwargs['cwd'] = process_path(kwargs['cwd'])
        return original_popen(args, *positional, **kwargs)
    subprocess.Popen = popen

    from . import agent_replay, replay, message_replay
    agent_replay.configured_temp_root = lambda _config: temporary
    message_replay.configured_temp_root = lambda _config: temporary
    original_atomic = replay.atomic_write_text
    def replay_write(path, value, **kwargs):
        path = BasePath(str(path))
        if path.parent == root_logical and path.name == 'replay.json':
            path = output / path.name
        return original_atomic(path, value, **kwargs)
    replay.atomic_write_text = replay_write

    real_open = Path.open
    def open_file(self, mode='r', *args, **kwargs):
        logical = BasePath(str(self))
        if logical.parent == root_logical and logical.name in {'replay_history.jsonl', 'agent_replay.json'}:
            return (output/logical.name).open(mode, *args, **kwargs)
        return real_open(self, mode, *args, **kwargs)
    Path.open = open_file
    # Scope filesystem writes to fresh outputs and an owned temporary home.
    # The hook also proves Python-side code did not attempt network fallback.
    allowed = [output.resolve(), temporary.resolve()]
    network = {'attempts': 0}
    def permitted(value):
        return _write_permitted(value, allowed)
    def audit(event, args):
        if event in {'socket.connect', 'socket.connect_ex', 'socket.getaddrinfo'}:
            network['attempts'] += 1
            raise PermissionError('Relocation replay forbids networking')
        if event == 'open':
            mode, flags = args[1], args[2]
            writing = (isinstance(mode, str) and any(token in mode for token in 'wax+')) or isinstance(flags, int) and flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND)
            if writing and not permitted(args[0]):
                raise PermissionError('Relocation replay cannot write outside its new output and temporary directories: '+str(args[0]))
        if event in {'os.remove', 'os.rmdir', 'os.mkdir'} and not permitted(args[0]):
            raise PermissionError('Relocation replay cannot mutate archived resources')
        if event in {'os.rename', 'os.replace'} and (not permitted(args[0]) or not permitted(args[1])):
            raise PermissionError('Relocation replay cannot replace archived resources')
    sys.addaudithook(audit)
    after = {'kernel': kernel_fingerprint(), 'agent': orchestration_fingerprint() if include_agent else None}
    if before != after:
        raise RelocationError('Relocation IO adapter changed financial or orchestration fingerprints')
    return Path, before, network


def _write_permitted(value, allowed):
    if isinstance(value, int):
        return True
    text = os.fsdecode(value)
    if text.startswith('\\\\?\\'):
        text = text[4:]
    candidate = BasePath(text)
    if '..' in candidate.parts:
        return False
    from .relocation import _unlinked, _io
    try:
        _unlinked(candidate.absolute())
        candidate = _io(candidate.absolute()).resolve(strict=False)
        raw = str(candidate)
        candidate = BasePath(raw[4:] if raw.startswith('\\\\?\\') else raw)
    except (OSError, RelocationError):
        return False
    return any(candidate.is_relative_to(root) for root in allowed)


def _verify_inputs(mapping, root):
    manifest = json.loads((root/'run_manifest.json').read_text(encoding='utf-8'))
    for entry in manifest.get('inputs', []):
        mapping.resolve(entry['path'], must_exist=True, sha256=entry['sha256'], size=entry.get('bytes'))
    return manifest


class _FrozenSessions:
    """Read-only facade: no persistent session constructor or state repair."""
    def __init__(self, state, root):
        self.state = state
        self.root = root
        self.config = state['config']

    def get(self, sid):
        if sid != self.state['session_id']:
            raise ValueError('Frozen session ID mismatch')
        return copy.deepcopy(self.state)

    def check_locks(self, state):
        from .sessions import FinancialSessions
        return FinancialSessions.check_locks(self, state)

    def _prepare_message_agent(self, *args, **kwargs):
        from .sessions import FinancialSessions
        self.last_agent = FinancialSessions._prepare_message_agent(self, *args, **kwargs)
        return self.last_agent


def run_worker(*, mode, run, relocation_map, out, session_file=None, message_id=None):
    mapping = RelocationMap.load(BasePath(relocation_map).absolute())
    from .relocation import _unlinked
    def supplied(value):
        candidate = BasePath(value)
        if '..' in candidate.parts:
            raise RelocationError('Replay arguments cannot contain parent components')
        candidate = candidate.absolute()
        _unlinked(candidate)
        return candidate
    requested = supplied(run)
    root = requested.resolve(strict=True) if requested.exists() else mapping.resolve(requested, must_exist=True)
    if (not root.is_dir() or not any(root.is_relative_to(mapping.workspace/name) for name in ('Light_Mirror', '存档'))):
        raise RelocationError('Replay run must be an existing directory')
    _verify_inputs(mapping, root)
    output = supplied(out).resolve(strict=False)
    if output.exists() or not output.is_relative_to(mapping.workspace/'Light_Mirror'/'evidence'/'relocation'):
        raise RelocationError('Replay requires a new output below Light_Mirror/evidence/relocation')
    _unlinked(output)
    output.mkdir(parents=True)
    diagnostics = []
    logical_root = mapping.logical(root)
    result = {'status': 'failed', 'mode': 'relocation_'+mode, 'map_sha256': mapping.map_sha256}
    # A short owned Temp root prevents Windows native DLL and profile paths
    # exceeding MAX_PATH under a user's deeply nested evidence directory.
    with tempfile.TemporaryDirectory(prefix='mjrel-') as temp:
        temporary = BasePath(temp)
        os.environ['MINGJING_HOME'] = str(temporary/'private-profile')
        # Borrow the already hash-checked source carrier read-only. Native
        # cache/profile/session writes still use this owned temporary home.
        # This avoids needlessly re-extracting 9,500 dependencies per replay.
        read_mounts = ()
        if mode in {'agent', 'message'}:
            from . import harness_client
            carrier = harness_client._finance_carrier(harness_client._source_resource_root(), {})
            if carrier is None:
                raise RelocationError('The locked carrier is unavailable')
            harness_client.verify_finance_carrier(carrier)
            manifest, _ = harness_client._read_finance_manifest(carrier)
            mounted = temporary/'private-profile'/'runtime-cache'/'finance'/manifest['manifest_sha256']
            # The private mount is not a persisted map or a filesystem junction.
            # Add it in memory only after the public map and carrier are validated.
            mapping.entries.insert(0, (mounted, carrier, 'directory'))
            mapping.entries.sort(key=lambda row: len(row[0].parts), reverse=True)
            read_mounts = (mounted,)
        Path, fingerprint, network = _install(mapping, output, temporary, root_logical=logical_root, read_mounts=read_mounts, include_agent=mode != 'kernel')
        if mode == 'kernel':
            from .replay import replay_run
            result = replay_run(Path(root))
        elif mode == 'agent':
            from . import agent_replay
            from .facts import fact_from_dict
            from .replay import _check_integrity
            original_lock = json.loads((root/'run_manifest.json').read_text(encoding='utf-8'))
            if _check_integrity(Path(root), original_lock):
                raise RelocationError('Frozen replay inputs or outputs differ from their original hashes')
            frozen_facts = [fact_from_dict(row) for row in json.loads((root/'facts.json').read_text(encoding='utf-8'))]
            OriginalAgent = agent_replay.FinancialAgent
            def frozen_agent(*args, **kwargs):
                agent = OriginalAgent(*args, **kwargs)
                def material_facts(source, company, period):
                    base = source if source.is_dir() else source.parent
                    return [fact for fact in frozen_facts if (not company or fact.company == company)
                            and fact.source_path and BasePath(fact.source_path).is_relative_to(BasePath(str(base)))]
                # The saved parser output is itself a hash-locked artifact.
                # Keep real source reads, semantic preparation, tools, model
                # request hashes and all verdict computations unchanged.
                agent._material_facts = material_facts
                return agent
            agent_replay.FinancialAgent = frozen_agent
            result = agent_replay.replay_agent(Path(root))
            result['frozen_parse_cache'] = True
        elif mode == 'message':
            if not session_file or not message_id:
                raise RelocationError('Message replay requires a frozen session file and message ID')
            requested_session = supplied(session_file)
            source = requested_session.resolve(strict=True) if requested_session.exists() else mapping.resolve(requested_session, must_exist=True)
            if not any(source.is_relative_to(mapping.workspace/name) for name in ('Light_Mirror', '存档')):
                raise RelocationError('Frozen session is outside approved relocated roots')
            state = json.loads(source.read_text(encoding='utf-8'))
            from .message_replay import replay_message
            frozen_sessions = _FrozenSessions(state, Path(source).parent)
            result = replay_message(frozen_sessions, state['session_id'], message_id)
            if result.get('status') != 'passed' and getattr(frozen_sessions, 'last_agent', None):
                agent = frozen_sessions.last_agent
                if agent.runtime:
                    # Failure reasons are retained without private reasoning,
                    # model responses, transport frames or credential payloads.
                    for frames in agent.runtime._notifications.values():
                        for frame in frames:
                            event = frame.get('params', {}).get('event', {})
                            if event.get('type') == 'turn/end':
                                diagnostics.append({'turn_end': agent.redact(json.dumps(event.get('data', {}).get('reason', {})))})
        else:
            raise RelocationError('Unsupported relocation replay mode')
        result['relocation'] = {'schema': 'mingjing.readonly-relocation-replay.v1',
                                'map_sha256': mapping.map_sha256, 'original_records_modified': False,
                                'fingerprints_unchanged': True, 'network_attempts': network['attempts'],
                                'logical_run': str(logical_root), 'physical_run': str(root)}
        if result.get('status') != 'passed':
            result['native_diagnostics'] = diagnostics
        (output/'relocation-replay.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def replay_relocated(*, mode, run, relocation_map, out, session_file=None, message_id=None):
    """Public optional adapter runs in a disposable interpreter, never in host."""
    if BasePath(out).exists() or '..' in BasePath(out).parts:
        raise RelocationError('Replay output must be a new canonical location')
    command = [sys.executable, '-X', 'utf8', '-B', '-m', 'mingjing.relocation_replay',
               '--mode', mode, '--run', str(run), '--relocation-map', str(relocation_map), '--out', str(out)]
    if session_file:
        command += ['--session-file', str(session_file)]
    if message_id:
        command += ['--message-id', message_id]
    child = subprocess.run(command, cwd=BasePath(__file__).resolve().parents[1], capture_output=True, text=True, encoding='utf-8')
    path = BasePath(out)/'relocation-replay.json'
    if path.is_file():
        return json.loads(path.read_text(encoding='utf-8'))
    return {'status': 'failed', 'mode': 'relocation_'+mode, 'reason': 'relocation_worker_failed',
            'details': child.stderr[-6000:], 'network_calls': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('kernel', 'agent', 'message'), required=True)
    parser.add_argument('--run', required=True)
    parser.add_argument('--relocation-map', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--session-file')
    parser.add_argument('--message-id')
    result = run_worker(**vars(parser.parse_args()))
    print(json.dumps({key: result.get(key) for key in ('status', 'mode', 'reason', 'network_calls', 'relocation')}, ensure_ascii=False))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
