"""Load local-page supplements only into a new, independently locked task."""
from __future__ import annotations

import json
from pathlib import Path

from .audit import digest
from .config import validate_path
from .evidence import sha256_file
from .file_io import io_path, record_path
from .facts import FactStore, fact_from_dict

PARENT_FIELDS = ('draft', 'corpus', 'company', 'period', 'report_date', 'task_type',
                 'scope_declaration', 'corpus_additions')


def project_reparse_facts(facts, supplements):
    """Human-facing source facts, distinct from a verified draft verdict."""
    from .presentation import display_standard_value, standard_unit, _metric_title
    selected = {fact_id for row in supplements for fact_id in row['added_fact_ids']}
    views = []
    for fact in facts:
        if fact.fact_id not in selected:
            continue
        record = fact.to_dict()
        unit = standard_unit(fact.account, record)
        views.append({**record, 'value_unit': unit, 'display_value': display_standard_value(fact.value, unit),
                      'account_title': _metric_title(fact.account), 'reference_status': 'source_fact'})
    return views


def _read(path):
    return json.loads(io_path(path).read_text(encoding='utf-8'))


def load_parent_context(parent_out, *, allowed_roots=None):
    """Verify frozen bytes without requiring the old run's code to be current.

    A new parser may improve an old task. It may not change the source material,
    company, disclosure cutoff or the frozen baseline that the packet refers to.
    """
    root = validate_path(parent_out, allowed_roots=allowed_roots, kind='dir',
                         must_exist=True, follow_symlinks=False)
    manifest_path = validate_path(root / 'run_manifest.json', allowed_roots=[str(root)],
                                  kind='file', must_exist=True, follow_symlinks=False)
    manifest = _read(manifest_path)
    if manifest.get('schema_version', 0) < 2 or not manifest.get('run_id'):
        raise ValueError('Reparse parent needs a complete frozen run manifest')
    outputs, inputs = {}, {}
    for group, target in (('outputs', outputs), ('inputs', inputs)):
        for row in manifest.get(group, []):
            raw = root / row['path'] if group == 'outputs' else Path(row['path'])
            path = validate_path(raw, allowed_roots=[str(root)] if group == 'outputs' else allowed_roots,
                                 kind='file', must_exist=True, follow_symlinks=False)
            key = path.relative_to(root).as_posix() if group == 'outputs' else str(path)
            if key in target or sha256_file(path) != row.get('sha256'):
                raise ValueError('Frozen parent material or output hash changed')
            target[key] = row['sha256']
    required = {'results.json', 'facts.json', 'claims.json', 'audit.jsonl'}
    if not required <= outputs.keys():
        raise ValueError('Reparse parent lacks frozen facts, claims, results or audit')
    data = _read(root / 'results.json')
    if data.get('run_id') != manifest['run_id'] or not data.get('company'):
        raise ValueError('Reparse parent company or run identity is incomplete')
    if _read(root / 'facts.json') != data.get('facts') or _read(root / 'claims.json') != data.get('claims'):
        raise ValueError('Frozen parent facts or claims disagree with its result')
    from .replay import _check_audit
    if _check_audit(root, manifest['run_id']):
        raise ValueError('Reparse parent audit chain is invalid')
    resources, resource_lock = [], ''
    lock_path = root / 'agent_lock.json'
    if io_path(lock_path).exists():
        if 'agent_lock.json' not in outputs:
            raise ValueError('Parent agent resource registry is not frozen')
        locked = _read(lock_path)
        resource_lock = locked.get('resource_lock', '')
        if (not resource_lock or resource_lock != digest({k: v for k, v in locked.items() if k != 'resource_lock'})
                or data.get('resource_lock') != resource_lock):
            raise ValueError('Parent resource lock has changed')
        for row in locked.get('resources', []):
            path = validate_path(row['path'], allowed_roots=allowed_roots, kind='file',
                                 must_exist=True, follow_symlinks=False)
            if inputs.get(str(path)) != row.get('sha256'):
                raise ValueError('Parent registry and input manifest disagree')
            resources.append(dict(row, path=str(path)))
    else:
        draft = str(record_path(io_path(data['draft']).resolve()))
        knowledge_paths = set()
        if (data.get('knowledge') or {}).get('enabled'):
            knowledge_path = root/'knowledge_lock.json'
            if inputs.get(str(record_path(io_path(knowledge_path).resolve()))) != sha256_file(knowledge_path):
                raise ValueError('Parent knowledge material scope is not frozen')
            knowledge = _read(knowledge_path)
            if knowledge.get('knowledge_lock') != data['knowledge'].get('knowledge_lock'):
                raise ValueError('Parent knowledge material lock disagrees with results')
            knowledge_paths = {str(record_path(io_path(row['path']).resolve())) for row in knowledge.get('resources', [])}
        for path, sha in inputs.items():
            rid = ('draft' if path == draft else 'kb-input-' + sha[:20] if path in knowledge_paths else 'report-' + sha[:20]
                   if Path(path).suffix.lower() in {'.pdf', '.md', '.txt'} else 'input-' + sha[:20])
            if not any(row['id'] == rid for row in resources):
                resources.append({'id': rid, 'path': path, 'sha256': sha})
    request_path = root / 'run_request.json'
    if io_path(request_path).exists():
        validate_path(request_path, allowed_roots=[str(root)], kind='file', follow_symlinks=False)
        accepted = _read(request_path)
        for key in PARENT_FIELDS:
            if key in accepted and key in data and accepted[key] != data[key]:
                # Empty inferred company/period fields do not change the actual
                # issuer locked in the resulting facts and resource registry.
                if key in {'company', 'period'} and not accepted[key]:
                    continue
                raise ValueError('Parent request disagrees with frozen material parameters')
        request_sha = sha256_file(request_path)
    else:
        request_sha = digest({key: data.get(key) for key in PARENT_FIELDS})
    generation = {'schema_version': 'reparse-source-generation.v1', 'run_id': manifest['run_id'],
                  'run_manifest_sha256': sha256_file(manifest_path), 'results_sha256': outputs['results.json'],
                  'request_sha256': request_sha, 'resource_lock': resource_lock}
    locks = [{'id': 'reparse-parent-' + digest(str(root))[:16] + '-' + name,
              'path': str(root / name), 'sha256': sha256_file(root / name)}
             for name in ('run_manifest.json', 'results.json', 'facts.json', 'claims.json',
                          'agent_lock.json', 'run_request.json') if io_path(root / name).is_file()]
    return {'data': data, 'source_resources': resources,
            'baseline_facts': [fact_from_dict(row) for row in data['facts']],
            'company': data['company'], 'as_of_date': data.get('report_date') or '',
            'source_generation': generation, 'input_locks': locks}


def apply_task_reparse_packets(request, baseline_facts, *, source_resources=None,
                              allowed_roots=None, cancel_event=None):
    """Return additive facts, provenance and input locks; never write the parent."""
    if request.get('reparse_inheritance'):
        if any(request.get(key) for key in ('reparse_packets', 'reparse_packet_paths', 'reparse_packet')):
            raise ValueError('Choose inherited supplements or newly registered packets, not both')
        return _apply_inherited_packets(request, baseline_facts, source_resources=source_resources,
                                        allowed_roots=allowed_roots, cancel_event=cancel_event)
    refs = request.get('reparse_packets') or []
    paths = request.get('reparse_packet_paths') or request.get('reparse_packet') or []
    if not refs and not paths:
        return baseline_facts, [], []
    if refs and paths:
        raise ValueError('Choose one registered reparse packet input form')
    if not isinstance(refs, list) or not isinstance(paths, list) or len(refs or paths) > 12:
        raise ValueError('At most twelve registered reparse packets can enter one new task')
    parent_out = request.get('reparse_parent_run')
    if not parent_out:
        raise ValueError('Reparse supplements require their frozen parent run')
    parent = load_parent_context(parent_out, allowed_roots=allowed_roots)
    data = parent['data']
    company = request.get('company') or parent['company']
    if company != parent['company'] or (request.get('report_date') or '') != parent['as_of_date']:
        raise ValueError('Reparse task company or cutoff differs from its parent')
    if sha256_file(request['draft']) != sha256_file(data['draft']):
        raise ValueError('Reparse supplement belongs to a different draft')
    for key in ('period', 'scope_declaration', 'task_type'):
        default = 'check' if key == 'task_type' else ''
        if (request.get(key) or default) != (data.get(key) or default):
            raise ValueError('Reparse task dimensions differ from its frozen parent')
    if digest([fact.to_dict() for fact in baseline_facts]) != digest(data['facts']):
        raise ValueError('Reparse baseline changed; create a new supplement for this material version')
    resources = source_resources if source_resources is not None else parent['source_resources']
    from .reparse import load_reparse_packet, _split_facts
    registered, provenance, locks, merged = set(), [], list(parent['input_locks']), list(baseline_facts)
    for reference in refs or [{'path': path} for path in paths]:
        if (not isinstance(reference, dict) or set(reference) - {'path', 'packet_id', 'sha256', 'source_generation'}
                or not reference.get('path')):
            raise ValueError('Invalid host reparse packet reference')
        root = request.get('_controlled_reparse_root')
        path = validate_path(reference['path'], allowed_roots=[root] if root else allowed_roots,
                             kind='file', must_exist=True, follow_symlinks=False)
        packet = _read(path)
        packet_id = packet.get('packet_id')
        if packet_id in registered:
            raise ValueError('Duplicate reparse packet input')
        registered.add(packet_id)
        actual_sha = sha256_file(path)
        if reference.get('sha256') and reference['sha256'] != actual_sha:
            raise ValueError('Registered reparse packet file changed')
        if reference.get('source_generation') and reference['source_generation'] != parent['source_generation']:
            raise ValueError('Registered reparse packet points to another parent generation')
        added = load_reparse_packet(path, source_resources=resources, baseline_facts=baseline_facts,
            company=company, as_of_date=parent['as_of_date'], expected_packet_id=reference.get('packet_id'),
            expected_source_generation=parent['source_generation'], cancel_event=cancel_event)
        accepted, conflicts = _split_facts(added, merged, packet['source'], packet['pages'])
        if conflicts:
            raise ValueError('Reparse supplements conflict with each other (X4); resolve material conflict first')
        merged = FactStore([*merged, *accepted]).facts
        provenance.append({'packet_id': packet_id, 'packet_sha256': packet['packet_sha256'],
                           'file_sha256': actual_sha, 'source_generation': parent['source_generation'],
                           'source': packet['source'], 'pages': packet['pages'],
                           'added_fact_ids': [fact.fact_id for fact in accepted]})
        locks.append({'id': 'reparse-packet-' + packet_id, 'path': str(path), 'sha256': actual_sha})
    return merged, provenance, locks


def _inheritance_groups(root: Path, *, allowed_roots=None, visited=None):
    """Resolve proof ancestry from frozen requests; model input cannot supply refs."""
    visited = set() if visited is None else visited
    root = record_path(io_path(root).resolve())
    if root in visited or len(visited) >= 32:
        raise ValueError('Supplement ancestry is cyclic or exceeds thirty-two frozen versions')
    visited.add(root)
    context = load_parent_context(root, allowed_roots=allowed_roots)
    accepted_path = validate_path(root/'run_request.json', allowed_roots=[str(root)], must_exist=True,
                                  kind='file', follow_symlinks=False)
    accepted = _read(accepted_path)
    prior = accepted.get('reparse_inheritance')
    if prior:
        if not isinstance(prior, dict) or not prior.get('source_run'):
            raise ValueError('Frozen supplement inheritance registry is invalid')
        groups, ancestors = _inheritance_groups(Path(prior['source_run']), allowed_roots=allowed_roots, visited=visited)
    else:
        refs = accepted.get('reparse_packets') or [{'path': path} for path in accepted.get('reparse_packet_paths', [])]
        if not isinstance(refs, list) or not refs or not accepted.get('reparse_parent_run'):
            raise ValueError('Frozen supplemented task lacks its registered packet origins')
        origin = load_parent_context(accepted['reparse_parent_run'], allowed_roots=allowed_roots)
        groups, ancestors = [], list(origin['input_locks'])
        for reference in refs:
            if not isinstance(reference, dict) or not reference.get('path'):
                raise ValueError('Frozen packet reference is invalid')
            path = validate_path(reference['path'], allowed_roots=allowed_roots, kind='file', must_exist=True,
                                 follow_symlinks=False)
            packet = _read(path)
            registered = next((row for row in context['source_resources']
                               if record_path(io_path(row['path']).resolve()) == path), None)
            if not registered or sha256_file(path) != registered['sha256']:
                raise ValueError('Inherited packet is not frozen in its source task input registry')
            if reference.get('sha256') and reference['sha256'] != registered['sha256']:
                raise ValueError('Inherited packet reference hash changed')
            groups.append({'path': path, 'packet_id': packet.get('packet_id'), 'origin': origin,
                           'file_sha256': registered['sha256']})
    ids = [group['packet_id'] for group in groups]
    provenance = context['data'].get('reparse_supplements') or []
    if (len(ids) != len(set(ids)) or set(ids) != {row.get('packet_id') for row in provenance}
            or len(provenance) != len(ids)):
        raise ValueError('Frozen supplement provenance disagrees with its registered ancestry')
    for group in groups:
        entry = next(row for row in provenance if row['packet_id'] == group['packet_id'])
        if (entry.get('file_sha256') != group['file_sha256']
                or entry.get('source_generation') != group['origin']['source_generation']):
            raise ValueError('Frozen supplement source generation changed')
    return groups, [*ancestors, *context['input_locks']]


def _apply_inherited_packets(request, baseline_facts, *, source_resources=None,
                             allowed_roots=None, cancel_event=None):
    from .task_versions import plan_supplement_inheritance
    from .reparse import load_reparse_packet, _split_facts
    plan = request['reparse_inheritance']
    if (not isinstance(plan, dict) or set(plan) !=
            {'schema_version', 'source_run', 'source_generation', 'rules_version_hash'}):
        raise ValueError('Invalid host supplement inheritance plan')
    current = plan_supplement_inheritance(plan['source_run'], request, allowed_roots=allowed_roots)
    if current != plan:
        raise ValueError('Frozen supplement source or rule generation changed before execution')
    groups, locks = _inheritance_groups(Path(plan['source_run']), allowed_roots=allowed_roots)
    merged, provenance = list(baseline_facts), []
    for group in groups:
        origin, path = group['origin'], group['path']
        added = load_reparse_packet(path, source_resources=source_resources or origin['source_resources'],
            baseline_facts=origin['baseline_facts'], company=origin['company'], as_of_date=origin['as_of_date'],
            expected_packet_id=group['packet_id'], expected_source_generation=origin['source_generation'],
            cancel_event=cancel_event)
        packet = _read(path)
        accepted, conflicts = _split_facts(added, merged, packet['source'], packet['pages'])
        if conflicts:
            raise ValueError('Inherited supplement conflicts with the new material baseline (X4)')
        merged = FactStore([*merged, *accepted]).facts
        retained = []
        for fact in added:
            dimensions = {key: getattr(fact, key) for key in ('dimension_kind', 'dimension_id', 'currency', 'definition_id')}
            matches = [item for item in FactStore(merged).versions(fact.company, fact.account, fact.period, fact.scope, fact.nr, **dimensions)
                       if (item.publish_date, item.version, item.value) == (fact.publish_date, fact.version, fact.value)]
            if not matches:
                raise ValueError('Revalidated supplement is missing from the new material version')
            retained.append(min(matches, key=lambda item: (item.source_doc != fact.source_doc, item.fact_id)).fact_id)
        provenance.append({'packet_id': group['packet_id'], 'packet_sha256': packet['packet_sha256'],
                           'file_sha256': group['file_sha256'], 'source_generation': origin['source_generation'],
                           'source': packet['source'], 'pages': packet['pages'], 'added_fact_ids': sorted(set(retained)),
                           'inherited_from_run': plan['source_generation']['run_id'], 'source_pages_revalidated': True,
                           'new_baseline_digest': digest([fact.to_dict() for fact in baseline_facts])})
        locks.append({'id': 'reparse-packet-'+group['packet_id'], 'path': str(path), 'sha256': group['file_sha256']})
    unique = {}
    for row in locks:
        key = str(record_path(io_path(row['path']).resolve()))
        if key in unique and unique[key]['sha256'] != row['sha256']:
            raise ValueError('Supplement ancestry contains inconsistent input hashes')
        unique.setdefault(key, row)
    return merged, provenance, list(unique.values())
