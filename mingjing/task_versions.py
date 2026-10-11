"""Shared admission rules for new versions of frozen material tasks."""
from __future__ import annotations


def has_supplements(accepted: dict, results: dict) -> bool:
    return bool(accepted.get('reparse_packets') or accepted.get('reparse_packet_ids')
                or accepted.get('reparse_packet_paths') or accepted.get('reparse_inheritance')
                or results.get('reparse_supplements'))


def assert_plain_version_allowed(accepted: dict, results: dict) -> None:
    """Never silently omit an already applied, independently locked supplement.

    A registered packet proves one parent baseline and material generation.
    Ordinary material/scope edits cannot carry that proof to another baseline.
    The caller must use the original parent's controlled supplement workflow
    or explicitly start a fresh task with its complete source materials.
    """
    if not isinstance(accepted, dict) or not isinstance(results, dict):
        raise ValueError('原任务的材料参数或冻结结果损坏，请选择材料新建任务')
    if has_supplements(accepted, results):
        raise ValueError('该任务已应用补证材料，普通材料更新不会继承补证包。'
                         '请从原父任务选择补证包创建新版本；材料或口径改变时请新建任务，旧补证包不自动适用')


def material_version_request(accepted: dict, results: dict, *, inherit_supplements: bool = False) -> dict:
    """Build a plain child from frozen material coordinates, not mutable draft.

    Transport-only fields, paths of internal supplement packets, and prior
    run IDs never cross the public launch boundary.
    """
    if not inherit_supplements:
        assert_plain_version_allowed(accepted, results)
    fields = {'draft', 'corpus', 'corpus_additions', 'facts', 'company', 'period',
              'report_date', 'mode', 'task_type', 'scope_declaration', 'scope_clarifications', 'knowledge_root',
              'material_profile', 'validation_policy', 'smart_options', 'smart_draft_metadata'}
    request = {key: value for key, value in accepted.items() if key in fields}
    request['draft'] = results['draft']
    for key in ('corpus', 'corpus_additions', 'company', 'period', 'report_date',
                'task_type', 'scope_declaration', 'scope_clarifications', 'knowledge_root',
                'material_profile', 'validation_policy', 'smart_options', 'smart_draft_metadata'):
        if key in results:
            request[key] = results[key]
    return request


def plan_supplement_inheritance(source_run, request: dict, *, allowed_roots=None) -> dict:
    """Issue a host-only plan; execution reopens and revalidates every PDF page."""
    from pathlib import Path
    import json
    from .evidence import sha256_file
    from .reparse_inputs import load_parent_context
    from .rules import rulepack_root
    root = Path(source_run).resolve()
    try:
        parent = load_parent_context(root, allowed_roots=allowed_roots)
        data = parent['data']
        if not data.get('reparse_supplements'):
            raise ValueError('冻结父版本没有已应用补证来源')
        for key in ('company', 'period', 'report_date', 'task_type'):
            default = 'check' if key == 'task_type' else ''
            if (request.get(key) or default) != (data.get(key) or default):
                raise ValueError('公司、期间、披露截止日或任务类型改变，补证材料失效')
        if not request.get('draft') or sha256_file(request['draft']) != sha256_file(data['draft']):
            raise ValueError('草稿原文改变，补证材料失效')
        original_sources = parent['source_resources']
        selected_paths = {str(Path(value).resolve()) for value in
                          [request.get('corpus'), *(request.get('corpus_additions') or []), request.get('facts')] if value}
        old_sources = {str(Path(value).resolve()) for value in
                       [data.get('corpus'), *(data.get('corpus_additions') or [])] if value}
        # Material messages append. A rerun that replaces/removes a selected
        # corpus cannot silently retain evidence from its old material scope.
        if not old_sources <= selected_paths:
            raise ValueError('原语料或追加材料被移除，补证材料失效')
        manifest = json.loads((root/'run_manifest.json').read_text(encoding='utf-8'))
        current_rules = json.loads((rulepack_root()/'sources.json').read_text(encoding='utf-8'))['version_hash']
        if manifest.get('rules_version_hash') != current_rules:
            raise ValueError('规则版本改变，补证材料需重新生成')
        if not any(row['id'].startswith('report-') for row in original_sources):
            raise ValueError('补证父版本缺少原PDF资源登记')
        return {'schema_version': 'reparse-inheritance.v1', 'source_run': str(root),
                'source_generation': parent['source_generation'], 'rules_version_hash': current_rules}
    except (KeyError, OSError, ValueError) as exc:
        raise ValueError('补证材料不能继承：'+str(exc)) from exc
