"""Frozen follow-up messages replayed through the original offline AgentLoop."""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import threading
from pathlib import Path

from .agent import orchestration_fingerprint, write_json
from .audit import digest, kernel_fingerprint
from .config import configured_temp_root, validate_path
from .evidence import sha256_file
from .replay import _check_audit

SCHEMA = 'financial-message-replay.v1'
MESSAGE_ID = re.compile(r'message-[0-9]{4,12}')


def capture_native_prefix(home: Path, out: Path, resume: bool) -> dict:
    """Copy only session JSONL and descriptors, never the runtime or credentials."""
    prefix = out/'native-prefix'
    files = []
    if resume:
        for dirname in ('sessions', 'mingjing-session-descriptors'):
            source = home/dirname
            if not source.exists():
                continue
            validate_path(source, allowed_roots=[str(home)], kind='dir', follow_symlinks=False)
            for path in sorted(source.rglob('*')):
                validate_path(path, allowed_roots=[str(source)], follow_symlinks=False)
                if not path.is_file():
                    continue
                relative = path.relative_to(home)
                target = prefix/relative
                target.parent.mkdir(parents=True, exist_ok=True)
                before = sha256_file(path)
                shutil.copy2(path, target)
                if sha256_file(target) != before or sha256_file(path) != before:
                    raise ValueError('Native session prefix changed during capture')
                files.append({'path': relative.as_posix(), 'sha256': before, 'bytes': target.stat().st_size})
    return {'resume': bool(resume), 'files': files}


def write_message_lock(out: Path, state: dict, agent, opening: dict, prompt: str,
                       prefix: dict, previous_slots: list[str]) -> None:
    lock = {'schema_version': SCHEMA, 'session_id': state['session_id'],
            'message_id': out.name, 'run_id': state['run_id'], 'resource_lock': state['resource_lock'],
            'config': agent.config, 'kernel': kernel_fingerprint(), 'agent': orchestration_fingerprint(agent.config),
            'parent_run_path': state['run_path'], 'parent_manifest': state['locks']['manifest'],
            'parent_results_sha256': sha256_file(Path(state['run_path'])/'results.json'),
            'resources': state['locks'].get('resources', []), 'native_spec': opening,
            'knowledge': state['locks'].get('knowledge'),
            'prompt': prompt, 'native_prefix': prefix, 'previous_revealed_slots': previous_slots}
    write_json(out/'message_lock.json', lock)


def _logical_trace(trace: list[dict]) -> list[dict]:
    return [{key: event.get(key) for key in ('role', 'tool', 'input', 'output', 'status')} for event in trace]


def _output(agent, answer: str) -> dict:
    sid = next(iter(agent.dispatcher.sessions))
    slots = agent.dispatcher.submitted_slots.get('question', [])
    return {'answer': answer, 'submitted_slots': slots,
            'source_references': [agent.dispatcher.source_references[slot] for slot in slots
                                  if slot in agent.dispatcher.source_references],
            'diagnostics': [agent.dispatcher.diagnostic_slots[slot] for slot in slots
                            if slot in agent.dispatcher.diagnostic_slots],
            'revealed_slots': sorted(agent.dispatcher.sessions[sid]['revealed_slots']),
            'tool_trace': _logical_trace(agent.dispatcher.trace)}


def freeze_message(out: Path, state: dict, agent, answer: str) -> str:
    """Seal a successful guarded turn after its runtime and host tools settle."""
    agent.audit.emit('message_complete', session_id=state['session_id'], message_id=out.name,
                     output={'answer_sha256': digest(answer), 'tool_calls': len(agent.dispatcher.trace)})
    agent.save_state('frozen_message')
    write_json(out/'message_output.json', _output(agent, answer))
    outputs = []
    for path in sorted(out.rglob('*')):
        if path.is_file() and path.name != 'message_manifest.json':
            validate_path(path, allowed_roots=[str(out)], kind='file', follow_symlinks=False)
            outputs.append({'path': path.relative_to(out).as_posix(), 'sha256': sha256_file(path), 'bytes': path.stat().st_size})
    manifest = {'schema_version': SCHEMA, 'run_id': state['run_id'], 'session_id': state['session_id'],
                'message_id': out.name, 'resource_lock': state['resource_lock'], 'outputs': outputs}
    write_json(out/'message_manifest.json', manifest)
    return sha256_file(out/'message_manifest.json')


def replay_message(sessions, sid: str, message_id: str) -> dict:
    result = {'status': 'failed', 'mode': 'message_agent_replay', 'session_id': sid,
              'message_id': message_id, 'network_calls': 0, 'real_harness_loop': False}
    if not isinstance(message_id, str) or not MESSAGE_ID.fullmatch(message_id):
        return {**result, 'reason': 'invalid_message_id'}
    state = sessions.get(sid)
    message = next((row for row in state.get('messages', []) if row.get('role') == 'assistant'
                    and row.get('message_id') == message_id), None)
    if not message or not message.get('agent_message_manifest_sha256'):
        return {**result, 'reason': 'missing_frozen_message_cache'}
    root = validate_path(sessions.root/sid/message_id, allowed_roots=[str(sessions.root)],
                         must_exist=True, kind='dir', follow_symlinks=False)
    read = lambda name: json.loads((root/name).read_text(encoding='utf-8'))
    try:
        sessions.check_locks(state)
        manifest_path = validate_path(root/'message_manifest.json', allowed_roots=[str(root)],
                                      must_exist=True, kind='file', follow_symlinks=False)
        if sha256_file(manifest_path) != message['agent_message_manifest_sha256']:
            raise ValueError('Message manifest hash changed')
        manifest = read('message_manifest.json')
        if any(manifest.get(key) != expected for key, expected in
               (('schema_version', SCHEMA), ('session_id', sid), ('message_id', message_id),
                ('run_id', state['run_id']), ('resource_lock', state['resource_lock']))):
            raise ValueError('Message manifest identity changed')
        seen = set()
        for entry in manifest.get('outputs', []):
            path = validate_path(root/entry['path'], allowed_roots=[str(root)], must_exist=True,
                                 kind='file', follow_symlinks=False)
            relative = path.relative_to(root).as_posix()
            if relative in seen or relative != entry['path'] or sha256_file(path) != entry['sha256']:
                raise ValueError('Frozen message output hash changed')
            seen.add(relative)
        required = {'message_lock.json', 'message_output.json', 'agent_model_cache.json', 'audit.jsonl'}
        if not required <= seen:
            raise ValueError('Frozen message lacks native cache or guarded output')
        issues = _check_audit(root, state['run_id'])
        if issues:
            raise ValueError('Frozen message audit chain is invalid')
        lock, original, cache = read('message_lock.json'), read('message_output.json'), read('agent_model_cache.json')
        if (lock.get('session_id') != sid or lock.get('message_id') != message_id
                or lock.get('run_id') != state['run_id'] or lock.get('resource_lock') != state['resource_lock']
                or lock.get('kernel') != kernel_fingerprint()
                or lock.get('agent') != orchestration_fingerprint(lock['config'])
                or lock.get('config') != state['config'] or original['answer'] != message['text']):
            raise ValueError('Message runtime, configuration, or output lock changed')
        parent = validate_path(lock['parent_run_path'], allowed_roots=[state['run_path']],
                               must_exist=True, kind='dir', follow_symlinks=False)
        if parent != Path(state['run_path']).resolve() or sha256_file(parent/'results.json') != lock['parent_results_sha256']:
            raise ValueError('Frozen message parent results changed')
        if not cache:
            return {**result, 'reason': 'empty_model_cache'}
        frozen_state = dict(state, config=lock['config'], locks=dict(state['locks'], resources=lock['resources']),
                            revealed_slots=lock['previous_revealed_slots'])
        data = json.loads((parent/'results.json').read_text(encoding='utf-8'))
        from .harness_client import HarnessRuntime
        with tempfile.TemporaryDirectory(prefix='mingjing-message-replay-', dir=configured_temp_root(lock['config'])) as temporary:
            replay_root = Path(temporary)
            out, home = replay_root/'output', replay_root/'harness'
            out.mkdir()
            for entry in lock['native_prefix']['files']:
                if 'native-prefix/'+entry['path'] not in seen:
                    raise ValueError('Native history prefix is absent from the frozen message manifest')
                source = validate_path(root/'native-prefix'/entry['path'], allowed_roots=[str(root/'native-prefix')],
                                       must_exist=True, kind='file', follow_symlinks=False)
                if sha256_file(source) != entry['sha256']:
                    raise ValueError('Native history prefix hash changed')
                target = validate_path(home/entry['path'], allowed_roots=[str(home)], follow_symlinks=False)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            opening = dict(lock['native_spec'], replay_only=True)
            if lock['native_prefix']['resume']:
                descriptor = home/'mingjing-session-descriptors'/f'{sid}.json'
                if not descriptor.is_file():
                    raise ValueError('Native replay requires the pre-turn session descriptor')
                # The change is confined to a temporary replay home. The
                # original persisted session and descriptors stay immutable.
                stored_spec = json.loads(descriptor.read_text(encoding='utf-8'))
                stored_spec['replay_only'] = True
                write_json(descriptor, stored_spec)
                # Reuse the normalized, ordered native descriptor (including
                # runtime defaults/schema normalization) for strict resume.
                opening = stored_spec
            agent = sessions._prepare_message_agent(frozen_state, out, data, cancel_event=threading.Event(), replay_cache=cache)
            agent.runtime = HarnessRuntime(home, agent._incoming, config=lock['config'],
                                           expected_runtime_lock=lock['agent']['runtime'])
            try:
                agent.runtime.start()
                if lock['native_prefix']['resume']:
                    agent.runtime.resume(**opening)
                else:
                    agent.runtime.open_session(**opening)
                completion = agent.runtime.prompt(sid, lock['prompt'], timeout_s=agent.budget.get('max_runtime_s', 180),
                                                   cancel_event=agent.cancelled)
                if agent.replay_cache_misses:
                    raise RuntimeError('REPLAY_CACHE_MISS: frozen message cache does not match the native model request')
                if completion.get('finish_reason') != 'completed':
                    raise RuntimeError('Offline native message incomplete: ' + str(completion.get('finish_reason')))
                answer = agent.dispatcher.templates.get('question')
                if not answer:
                    raise RuntimeError('Offline native message did not submit a guarded answer')
                replayed = _output(agent, answer)
                differences = [key for key in original if original[key] != replayed.get(key)]
                result.update(status='passed' if not differences else 'failed', real_harness_loop=True,
                              answer_equal='answer' not in differences, references_equal='source_references' not in differences,
                              tool_trace_equal='tool_trace' not in differences, differences=differences,
                              cached_model_requests=agent.native_request_count, tool_calls=len(agent.dispatcher.trace),
                              output_sha256=digest(replayed))
            except Exception as exc:
                result.update(reason=type(exc).__name__, details=agent.redact(str(exc)),
                              cached_model_requests=agent.native_request_count)
            finally:
                if agent.runtime:
                    agent.runtime.close()
    except Exception as exc:
        result.update(reason=type(exc).__name__, details=str(exc))
    return result
