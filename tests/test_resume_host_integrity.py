"""The Web host must preserve the Agent's read-only resume transaction."""
from __future__ import annotations

import copy
import threading
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from mingjing import agent as agent_module, server
from mingjing.agent import write_json
from mingjing.audit import digest
from test_agent_lifecycle import lifecycle, new_agent, read, frozen_bytes


def interrupted_task(fixture, *, local_request=False):
    fixture.out = fixture.out.parent/'lifecycle-fixture'
    if local_request:
        local = fixture.out/'input'/'draft.md'
        local.parent.mkdir(parents=True)
        local.write_bytes(Path(fixture.request['draft']).read_bytes())
        fixture.request['draft'] = str(local)
    fixture.request['mode'] = 'harness'
    result = new_agent(fixture).run()
    assert result['model']['status'] == 'cancelled' and not (fixture.out/'run_manifest.json').exists()
    frozen_draft = Path(next(row['path'] for row in read(fixture.out/'agent_lock.json')['resources']
                             if row['id'] == 'draft'))
    # Match the real new-task host's accepted request, including its exact
    # serialization order. Successful resume must not reserialize this file.
    accepted = {key: fixture.request[key] for key in ('company', 'period', 'report_date', 'mode')}
    accepted.update(corpus_additions=[], draft=str(frozen_draft), corpus=fixture.request['corpus'])
    write_json(fixture.out/'run_request.json', accepted)
    config = SimpleNamespace(get=lambda: copy.deepcopy(fixture.config), secret=lambda _: None)
    task = server.RunTask('lifecycle-fixture', accepted, fixture.out.parent, config)
    task.status = 'cancelled'
    return task, frozen_draft


def wait(task):
    task.thread.join(5)
    assert not task.thread.is_alive()


def relock(fixture, mutate):
    lock = read(fixture.out/'agent_lock.json')
    mutate(lock)
    lock['resource_lock'] = digest({key: value for key, value in lock.items() if key != 'resource_lock'})
    write_json(fixture.out/'agent_lock.json', lock)
    state = read(fixture.out/'agent_state.json')
    state['resource_lock'] = lock['resource_lock']
    write_json(fixture.out/'agent_state.json', state)


def test_web_resume_edited_external_original_reuses_frozen_draft_without_copy(lifecycle, monkeypatch):
    task, frozen = interrupted_task(lifecycle)
    draft_bytes = frozen.read_bytes()
    request_bytes = (lifecycle.out/'run_request.json').read_bytes()
    Path(lifecycle.request['draft']).write_text('外部原稿已修改999999元', encoding='utf-8')

    def copy_forbidden(*_args, **_kwargs):
        raise AssertionError('Resume cannot copy or replace old inputs')

    monkeypatch.setattr(server.shutil, 'copy2', copy_forbidden)
    task.resume()
    wait(task)
    assert task.status == 'completed', task.error
    assert frozen.read_bytes() == draft_bytes
    assert (lifecycle.out/'run_request.json').read_bytes() == request_bytes
    assert task.request['draft'] == str(frozen)
    assert read(lifecycle.out/'results.json')['summary'] == {'verified': 2, 'violated': 0, 'abstained': 0}
    assert task.agent.resume_committed


@pytest.mark.parametrize('failure', ['parser', 'derived_facts', 'cache'])
def test_host_reconstruction_failure_preserves_every_old_byte(lifecycle, monkeypatch, failure):
    task, _ = interrupted_task(lifecycle, local_request=True)
    if failure == 'cache':
        # A malformed checkpoint cache is detected after material preparation
        # but before the restored state can be atomically published.
        (lifecycle.out/'agent_model_cache.json').write_text('{malformed', encoding='utf-8')
    before = frozen_bytes(lifecycle.out)
    native_count = len(lifecycle.boundary.instances)
    if failure == 'parser':
        def fail(*_args, **_kwargs):
            raise ValueError('Injected readonly reconstruction failure')
        monkeypatch.setattr(agent_module, 'partition_document', fail)
    elif failure == 'derived_facts':
        original = agent_module.FinancialAgent._material_facts
        def altered(self, *args):
            return [replace(fact, value=fact.value+Decimal('1')) for fact in original(self, *args)]
        monkeypatch.setattr(agent_module.FinancialAgent, '_material_facts', altered)
    task.resume()
    wait(task)
    assert task.status == 'failed'
    assert task.error and task.events[-1]['resume_preflight']
    assert frozen_bytes(lifecycle.out) == before
    assert len(lifecycle.boundary.instances) == native_count
    assert not task.agent.resume_committed


@pytest.mark.parametrize('corruption', ['run_id', 'state_resource', 'lock_resource', 'lock_body', 'state_type'])
def test_host_rejects_checkpoint_identity_before_worker_or_write(lifecycle, corruption):
    task, _ = interrupted_task(lifecycle)
    state = read(lifecycle.out/'agent_state.json')
    lock = read(lifecycle.out/'agent_lock.json')
    if corruption == 'run_id':
        state['run_id'] = 'another-task'
    elif corruption == 'state_resource':
        state['resource_lock'] = '0'*64
    elif corruption == 'lock_resource':
        state['resource_lock'] = lock['resource_lock'] = '0'*64
    elif corruption == 'lock_body':
        lock['config']['revision'] += 1
    else:
        state = []
    write_json(lifecycle.out/'agent_state.json', state)
    write_json(lifecycle.out/'agent_lock.json', lock)
    before = frozen_bytes(lifecycle.out)
    original_thread = task.thread
    with pytest.raises(ValueError, match='身份或材料锁'):
        task.resume()
    assert task.thread is original_thread and not task.thread.is_alive()
    assert frozen_bytes(lifecycle.out) == before


@pytest.mark.parametrize('corruption', ['missing', 'duplicate', 'outside_input'])
def test_frozen_draft_has_one_identity_inside_original_input(lifecycle, corruption):
    task, frozen = interrupted_task(lifecycle)
    def mutate(lock):
        draft = next(row for row in lock['resources'] if row['id'] == 'draft')
        if corruption == 'missing':
            lock['resources'].remove(draft)
        elif corruption == 'duplicate':
            lock['resources'].append(copy.deepcopy(draft))
        else:
            draft['path'] = str(Path(lifecycle.request['draft']))
    relock(lifecycle, mutate)
    before = frozen_bytes(lifecycle.out)
    with pytest.raises(ValueError):
        task.resume()
    assert frozen_bytes(lifecycle.out) == before


def test_cancel_during_resource_preflight_is_not_erased_before_start(lifecycle, monkeypatch):
    task, _ = interrupted_task(lifecycle)
    before = frozen_bytes(lifecycle.out)
    entered, release = threading.Event(), threading.Event()
    original_hash = server.sha256_file
    def held_hash(path):
        entered.set()
        assert release.wait(3)
        return original_hash(path)
    monkeypatch.setattr(server, 'sha256_file', held_hash)
    errors = []
    def resume():
        try:
            task.resume()
        except Exception as exc:
            errors.append(exc)
    caller = threading.Thread(target=resume)
    caller.start()
    assert entered.wait(3)
    task.cancelled.set()
    release.set()
    caller.join(5)
    assert not caller.is_alive() and errors and isinstance(errors[0], InterruptedError)
    assert not task.thread.is_alive() and frozen_bytes(lifecycle.out) == before


def test_cancel_during_readonly_reconstruction_preserves_checkpoint(lifecycle, monkeypatch):
    task, _ = interrupted_task(lifecycle)
    before = frozen_bytes(lifecycle.out)
    native_count = len(lifecycle.boundary.instances)
    entered, release = threading.Event(), threading.Event()
    original = agent_module.partition_document
    def held_partition(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)
    monkeypatch.setattr(agent_module, 'partition_document', held_partition)
    task.resume()
    assert entered.wait(3)
    task.cancelled.set()
    release.set()
    wait(task)
    assert task.status == 'cancelled' and task.events[-1]['resume_preflight']
    assert frozen_bytes(lifecycle.out) == before
    assert len(lifecycle.boundary.instances) == native_count


def test_cancel_at_worker_publication_preserves_old_run(lifecycle, monkeypatch):
    task, _ = interrupted_task(lifecycle)
    before = frozen_bytes(lifecycle.out)
    original_start = task.start
    def cancelled_start():
        task.cancelled.set()
        original_start()
    monkeypatch.setattr(task, 'start', cancelled_start)
    task.resume()
    wait(task)
    assert task.status == 'cancelled' and task.events[-1]['resume_preflight']
    assert frozen_bytes(lifecycle.out) == before


def test_shutdown_after_preflight_refuses_resume_publication(lifecycle, monkeypatch):
    task, _ = interrupted_task(lifecycle)
    before = frozen_bytes(lifecycle.out)
    entered, release = threading.Event(), threading.Event()
    closing = {'value': False}
    lifecycle_lock = threading.RLock()
    original_hash = server.sha256_file
    def held_hash(path):
        entered.set()
        assert release.wait(3)
        return original_hash(path)
    monkeypatch.setattr(server, 'sha256_file', held_hash)
    errors = []
    def resume():
        try:
            task.resume(lifecycle_lock=lifecycle_lock, is_closing=lambda: closing['value'])
        except Exception as exc:
            errors.append(exc)
    caller = threading.Thread(target=resume)
    caller.start()
    assert entered.wait(3)
    with lifecycle_lock:
        closing['value'] = True
    release.set()
    caller.join(5)
    assert not caller.is_alive() and errors and '正在退出' in str(errors[0])
    assert not task.thread.is_alive() and frozen_bytes(lifecycle.out) == before


def test_worker_start_failure_restores_host_state_and_old_bytes(lifecycle, monkeypatch):
    task, _ = interrupted_task(lifecycle)
    before = frozen_bytes(lifecycle.out)
    previous = task.thread, task.request, task.status, task.config_snapshot
    def failed_start():
        raise RuntimeError('Injected thread start failure')
    monkeypatch.setattr(task, 'start', failed_start)
    with pytest.raises(RuntimeError, match='thread start'):
        task.resume()
    assert (task.thread, task.request, task.status, task.config_snapshot) == previous
    assert frozen_bytes(lifecycle.out) == before


@pytest.mark.parametrize('entry', ['source_agent', 'web_host'])
def test_changed_cutoff_cannot_turn_unpublished_fact_into_verified_on_resume(lifecycle, entry):
    from mingjing.facts import FactStore, fact_from_dict
    from mingjing.judge import Judge
    from mingjing.mentions import extract_mentions
    lifecycle.request['report_date'] = '2025-02-28'
    task, frozen = interrupted_task(lifecycle)
    original = read(lifecycle.out/'results.json')
    store = FactStore([fact_from_dict(row) for row in original['facts']])
    claim = extract_mentions(frozen.read_text(encoding='utf-8'), '甲公司', '2024Y')[0]
    # This is an actual financial counterexample: the very same 100元 fact
    # is unavailable at the accepted date, and available after publication.
    earlier = Judge(store, '2025-02-28').verify(claim)
    later = Judge(store, '2025-03-31').verify(claim)
    assert earlier.status == 'abstained' and earlier.abstention_code == 'X1'
    assert later.status == 'verified'
    state = read(lifecycle.out/'agent_state.json')
    state['request']['report_date'] = '2025-03-31'
    write_json(lifecycle.out/'agent_state.json', state)
    before = frozen_bytes(lifecycle.out)
    native_count = len(lifecycle.boundary.instances)
    with pytest.raises(ValueError, match='业务参数'):
        if entry == 'source_agent':
            new_agent(lifecycle).run(resume=True)
        else:
            task.resume()
    assert frozen_bytes(lifecycle.out) == before
    assert len(lifecycle.boundary.instances) == native_count


@pytest.mark.parametrize('key,value', [('task_type', 'analysis'), ('company', '乙公司'),
                                     ('period', '2023Y'), ('scope_declaration', '母公司')])
@pytest.mark.parametrize('entry', ['source_agent', 'web_host'])
def test_changed_semantic_task_choices_fail_before_reconstruction(lifecycle, key, value, entry):
    task, _ = interrupted_task(lifecycle)
    state = read(lifecycle.out/'agent_state.json')
    state['request'][key] = value
    write_json(lifecycle.out/'agent_state.json', state)
    before = frozen_bytes(lifecycle.out)
    with pytest.raises(ValueError, match='业务参数'):
        if entry == 'source_agent':
            new_agent(lifecycle).run(resume=True)
        else:
            task.resume()
    assert frozen_bytes(lifecycle.out) == before


def test_web_resume_compares_original_accepted_request_semantics(lifecycle):
    task, _ = interrupted_task(lifecycle)
    accepted = read(lifecycle.out/'run_request.json')
    accepted['report_date'] = '2026-01-01'
    write_json(lifecycle.out/'run_request.json', accepted)
    before = frozen_bytes(lifecycle.out)
    with pytest.raises(ValueError, match='业务参数'):
        task.resume()
    assert not task.thread.is_alive() and frozen_bytes(lifecycle.out) == before


@pytest.mark.parametrize('entry', ['source_agent', 'web_host'])
def test_old_lock_without_business_semantics_stays_byte_for_byte_readonly(lifecycle, entry):
    task, _ = interrupted_task(lifecycle)
    def old_schema(lock):
        lock.pop('schema_version', None)
        lock.pop('task_semantics', None)
    relock(lifecycle, old_schema)
    before = frozen_bytes(lifecycle.out)
    with pytest.raises(ValueError, match='旧任务缺少业务参数锁'):
        if entry == 'source_agent':
            new_agent(lifecycle).run(resume=True)
        else:
            task.resume()
    assert frozen_bytes(lifecycle.out) == before


def test_new_task_locks_actual_query_policies_and_issuer_separately_from_ui_defaults(lifecycle):
    lifecycle.request['company'] = ''
    lifecycle.config['run'].update(company='另一个默认公司', report_date='2020-01-01', period='2020Y',
                                   scope='母公司', version_strategy='specified_report')
    owner = new_agent(lifecycle)
    owner.prepare()
    locked = read(lifecycle.out/'agent_lock.json')['task_semantics']
    assert locked['request']['company'] == '' and locked['execution']['company'] == '甲公司'
    assert locked['execution']['report_date'] == '2025-03-31' and locked['execution']['period'] == '2024Y'
    assert locked['execution']['analysis_scope'] == '合并'
    assert locked['query_policy']['default'] == 'latest_as_of'
    assert locked['query_policy']['explicit_reference'] == 'pinned'
    assert locked['query_policy']['comparison_period'] == 'same_report'
    assert {row['strategy'] for row in locked['query_policy']['claims']} == {'latest_as_of'}
    assert {row['claim_id'] for row in locked['query_policy']['claims']} == {claim.claim_id for claim in owner.claims}
