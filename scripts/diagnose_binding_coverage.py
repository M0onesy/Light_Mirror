"""Read frozen Agent evidence and select small, source-addressed review samples.

No current kernel/rule code is executed, no API is called, and no old run is
changed. A successful model prompt and a successful financial binding are
reported as different measurements. This is a diagnostic, never acceptance.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile


PRIMARY_SCOPE = 'issuer_historical_financial'
BINDING_FIELDS = ('company', 'account', 'period', 'scope', 'relation', 'base_period')
ROLE_KEYS = {'planner': 'A', 'verifier': 'B'}
GUARD_KINDS = frozenset({'source_dimensions', 'source_parse', 'source_scope',
                         'source_binding', 'source_semantics', 'definition', 'currency'})
FACT_KINDS = frozenset({'fact_lookup', 'operand', 'operand_source'})


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _unique(items: list[dict]) -> list[dict]:
    seen, result = set(), []
    for item in items:
        key = json.dumps(item, sort_keys=True, ensure_ascii=False)
        if key not in seen:
            result.append(deepcopy(item))
            seen.add(key)
    return result


def _indexed(rows: list[dict], key: str, label: str) -> dict:
    result = {}
    for row in rows:
        value = row.get(key)
        if not value or value in result:
            raise ValueError(f'{label} contains a missing or duplicate {key}')
        result[value] = row
    return result


class FrozenReader:
    def __init__(self, run: Path):
        self.run = run.resolve(strict=True)
        self.hashes: dict[str, str] = {}

    def read(self, name: str, *, optional=False, lines=False):
        path = self.run / name
        if optional and not path.exists():
            return None
        content = path.read_bytes()
        self.hashes[name] = hashlib.sha256(content).hexdigest()
        text = content.decode('utf-8-sig')
        return [json.loads(line) for line in text.splitlines() if line.strip()] if lines else json.loads(text)

    def ensure_unchanged(self):
        for name, expected in self.hashes.items():
            if _sha(self.run / name) != expected:
                raise ValueError(f'Frozen input changed during diagnosis: {name}')


def _tool_submissions(trace):
    evidence = {key: defaultdict(list) for key in ROLE_KEYS.values()}
    for item in trace or []:
        role = ROLE_KEYS.get(item.get('role'))
        if role is None or item.get('tool') not in {'mj_submit_binding', 'mj_submit_bindings'}:
            continue
        body = item.get('input') or {}
        submissions = body.get('bindings') if isinstance(body.get('bindings'), list) else [body]
        output = item.get('output') if isinstance(item.get('output'), dict) else {}
        accepted_ids = set(output.get('binding_ids') or [])
        for submission in submissions:
            if not isinstance(submission, dict):
                continue
            cid = submission.get('mention_id') or submission.get('binding_id')
            if not cid:
                continue
            # An attempted tool call is not a successful submission. Both the
            # host status and accepted ID must prove that it was admitted.
            single_id = output.get('binding_id')
            accepted = (item.get('status') == 'success' and output.get('accepted') is True
                        and (cid in accepted_ids or single_id in {cid, f"{item.get('role')}:{cid}"}))
            evidence[role][cid].append({
                'call_id': item.get('call_id'), 'session_id': item.get('session_id'),
                'tool': item.get('tool'), 'tool_status': item.get('status'), 'accepted': accepted,
                'input_hash': item.get('input_hash'),
            })
    return evidence


def _binding(cid, key, stored, submissions, trace_present):
    record = (stored.get(key) or {}).get(cid)
    events = submissions[key].get(cid, [])
    return {
        'record_present': isinstance(record, dict),
        'binding': deepcopy(record) if isinstance(record, dict) else None,
        'tool_attempted': bool(events) if trace_present else None,
        'actually_submitted': any(item['accepted'] for item in events) if trace_present else None,
        'submission_evidence': deepcopy(events),
    }


def _source_gaps(claim, source, blockers):
    """Describe only absence already visible in the frozen source snapshot."""
    missing = [field for field in ('company', 'account', 'period', 'scope', 'relation')
               if not source.get(field) or source.get(field) in {'UNKNOWN', 'unknown', '未知'}]
    if source.get('relation') in {'yoy', 'qoq', 'cagr', 'pp_difference'} and not source.get('base_period'):
        missing.append('base_period')
    if not claim.get('unit') and not (claim.get('table_context') or {}).get('unit_hint'):
        missing.append('unit')
    if source.get('dimension_kind') in {'business', 'customer', 'product', 'segment'} and not source.get('dimension_id'):
        missing.append('dimension_id')
    method_missing = (str(source.get('account') or '').endswith('_UNSPECIFIED')
                      and not source.get('definition_id'))
    # False explicit flags mean the source did not explicitly write a field;
    # they do not prove it is absent (trusted issuer context can be implicit).
    not_explicit = [field for field, flag in (source.get('explicit') or {}).items() if flag is False]
    return {
        'missing_dimensions': missing,
        'source_fields_not_explicit': not_explicit,
        'undefined_source_method': bool(method_missing),
        'recorded_method_blockers': [deepcopy(item) for item in blockers
                                     if item.get('kind') in {'definition', 'formula_precondition'}],
        'recorded_fact_or_operand_blockers': [deepcopy(item) for item in blockers
                                            if item.get('kind') in FACT_KINDS],
    }


def _model_summary(payload, audit, audit_present):
    model = payload.get('model') or {}
    calls = [item for item in audit or [] if item.get('type') == 'model_call']
    requests = [item for item in audit or [] if item.get('type') == 'model_request']
    responses = [item for item in audit or [] if item.get('type') == 'model_response_cache']
    statuses = Counter(str(item.get('status') or 'unknown') for item in calls)
    real = [item for item in requests if item.get('cache_hit') is False and item.get('replay_only') is False]
    return {
        'recorded_model_status': model.get('status'),
        'recorded_mode': model.get('mode') or payload.get('mode'),
        'audit_available': audit_present,
        'prompt_status_counts': dict(sorted(statuses.items())),
        'observed_prompt_count': len(calls) if audit_present else None,
        'all_observed_prompts_succeeded': all(item.get('status') == 'success' for item in calls) if calls else None,
        'model_request_count': len(requests) if audit_present else None,
        'non_cached_non_replay_request_count': len(real) if audit_present else None,
        'cached_response_count': len(responses) if audit_present else None,
        'roles': dict(Counter(str(item.get('role') or 'unknown') for item in calls)),
        'recorded_tokens': model.get('tokens', 'unknown'),
        'recorded_cost': model.get('cost', 'unknown'),
        'interpretation': 'Model prompt success is runtime evidence; it is not financial correctness or binding coverage.',
    }


def diagnose(run: Path, *, sample_per_category=2, category_limit=8) -> dict:
    if not 1 <= sample_per_category <= 20 or not 1 <= category_limit <= 50:
        raise ValueError('Sample count must be 1..20 and category count must be 1..50')
    reader = FrozenReader(run)
    payload = reader.read('results.json')
    if not isinstance(payload, dict):
        raise ValueError('Frozen results.json must be a structured run object')
    claims = payload.get('claims') if 'claims' in payload else reader.read('claims.json')
    coverage = payload.get('coverage') if 'coverage' in payload else reader.read('coverage.json')
    if not isinstance(coverage, dict) or not isinstance(coverage.get('mentions'), list):
        raise ValueError('A frozen occurrence ledger is required; scope cannot be guessed from model verdicts')
    claim_map = _indexed(claims or [], 'claim_id', 'claims')
    result_map = _indexed(payload.get('results') or [], 'claim_id', 'results')
    mentions = coverage['mentions']
    _indexed(mentions, 'id', 'occurrence ledger')
    if set(result_map) - set(claim_map):
        raise ValueError('Frozen result has no matching claim')
    pages = {item['id']: item for item in coverage.get('pages') or []}
    blocks = {item['id']: item for item in coverage.get('blocks') or []}
    stored = reader.read('bindings.json', optional=True) or {}
    trace = reader.read('tool_trace.json', optional=True)
    audit = reader.read('audit.jsonl', optional=True, lines=True)
    submissions = _tool_submissions(trace)
    rows = []
    for mention in mentions:
        cid = mention.get('claim_id')
        claim = claim_map.get(cid) or {}
        context = claim.get('table_context') or {}
        result = result_map.get(cid) or {}
        source = result.get('source_dimensions') or context.get('source_dimensions') or {}
        evaluation_scope = mention.get('evaluation_scope') or context.get('evaluation_scope') or source.get('evaluation_scope')
        if evaluation_scope != PRIMARY_SCOPE:
            continue
        blockers = _unique(result.get('blockers') or [])
        first = blockers[0] if blockers else None
        A = _binding(cid, 'A', stored, submissions, trace is not None)
        B = _binding(cid, 'B', stored, submissions, trace is not None)
        differences = ([field for field in BINDING_FIELDS if A['binding'].get(field) != B['binding'].get(field)]
                       if A['record_present'] and B['record_present'] else None)
        agreement = deepcopy((stored.get('agreement') or {}).get(cid))
        page = pages.get(mention.get('page_id'), {})
        block = blocks.get(mention.get('block_id'), {})
        guard_blocks = [item for item in blockers if item.get('kind') in GUARD_KINDS]
        row = {
            'occurrence_id': mention['id'], 'claim_id': cid, 'evaluation_scope': PRIMARY_SCOPE,
            'occurrence': {'start': mention.get('start'), 'end': mention.get('end'), 'text': mention.get('text')},
            'original_context': claim.get('text') or result.get('claim_text') or block.get('text'),
            'source_location': {'draft': payload.get('draft'), 'page_id': mention.get('page_id'),
                                'page': page.get('page') or block.get('page'),
                                'block_id': mention.get('block_id'), 'bbox': deepcopy(block.get('bbox'))},
            'claim_present': bool(claim), 'result_present': bool(result),
            'parse_status': claim.get('parse_status', 'not_recorded'),
            'binding_status': claim.get('binding_status', 'not_recorded'),
            'source_snapshot': deepcopy(source),
            'source_guard': {'snapshot_present': bool(source),
                             'state': 'recorded_blocked' if guard_blocks else 'no_recorded_block' if source else 'not_recorded',
                             'blockers': deepcopy(guard_blocks),
                             'note': 'Frozen guard evidence only; current source parser or rules are not re-executed.'},
            'binding_A': A, 'binding_B': B, 'binding_field_differences': differences,
            'recorded_agreement': agreement,
            'result_status': result.get('status', 'pending'), 'abstention_code': result.get('abstention_code'),
            'first_blocker': deepcopy(first), 'primary_reason': result.get('reason'),
            'all_recorded_blockers': blockers,
            'other_recorded_blockers': deepcopy(blockers[1:]),
            'known_source_gaps': _source_gaps(claim, source, blockers),
            'evidence_ids': list(dict.fromkeys(result.get('evidence') or [])),
            'source_header_references': deepcopy(context.get('headers') or []),
        }
        rows.append(row)
    rows.sort(key=lambda row: (row['occurrence']['start'] if isinstance(row['occurrence']['start'], int) else -1,
                               row['occurrence_id']))
    primary_claims = {row['claim_id'] for row in rows if row['claim_id']}
    unique_rows = {row['claim_id']: row for row in rows if row['claim_id']}
    both_records = sum(row['binding_A']['record_present'] and row['binding_B']['record_present'] for row in rows)
    both_submitted = sum(row['binding_A']['actually_submitted'] is True and row['binding_B']['actually_submitted'] is True for row in rows)
    grouped = defaultdict(list)
    for row in rows:
        if row['result_status'] not in {'abstained', 'pending'}:
            continue
        first = row['first_blocker'] or {}
        key = (row['abstention_code'] or 'unresolved', first.get('kind') or 'not_recorded',
               row['source_snapshot'].get('account') or 'unresolved_source_account')
        grouped[key].append(row)
    groups = sorted(grouped.items(), key=lambda entry: (-len(entry[1]), entry[0]))
    samples = [{
        'category': {'abstention_code': key[0], 'first_blocker_kind': key[1], 'source_account': key[2]},
        'occurrence_count': len(group), 'samples': deepcopy(group[:sample_per_category]),
    } for key, group in groups[:category_limit]]
    total = len(rows)
    report = {
        'schema_version': 'binding-coverage-diagnosis.v1', 'run_id': payload.get('run_id'),
        'run_root': str(reader.run), 'diagnostic_only': True,
        'model_runtime': _model_summary(payload, audit, audit is not None),
        'binding_coverage': {
            'primary_occurrences': total, 'unique_primary_claims': len(primary_claims),
            'both_binding_records_occurrences': both_records,
            'both_actual_submissions_occurrences': both_submitted if trace is not None else None,
            'both_actual_submissions_rate': both_submitted / total if total and trace is not None else None,
            'both_actual_submissions_unique_claims': sum(
                row['binding_A']['actually_submitted'] is True and row['binding_B']['actually_submitted'] is True
                for row in unique_rows.values()) if trace is not None else None,
            'raw_field_equal_occurrences': sum(row['binding_field_differences'] == [] for row in rows),
            'recorded_agreed_occurrences': sum((row['recorded_agreement'] or {}).get('agreed') is True for row in rows),
            'result_status_counts': dict(Counter(row['result_status'] for row in rows)),
            'source_guard_state_counts': dict(Counter(row['source_guard']['state'] for row in rows)),
            'missing_claim_occurrences': sum(not row['claim_present'] for row in rows),
            'missing_result_occurrences': sum(not row['result_present'] for row in rows),
        },
        'sample_policy': {'sample_per_category': sample_per_category, 'category_limit': category_limit,
                          'categories_available': len(groups), 'sort': 'frequency descending; category and original span ascending',
                          'no_amount_matching': True, 'no_verdict_rewrite': True},
        'samples': samples, 'rows': rows,
        'input_hashes': deepcopy(reader.hashes),
        'limitations': [
            'No claim IDs, numeric amounts or source formulas are hardcoded.',
            'Binding records without admitted tool-call evidence do not prove actual Agent submission.',
            'No-recorded-block is not a new claim that an old source passed every current guard.',
            'Missing dimensions and recorded blockers may overlap; their counts are not additive.',
            'This script does not assess gold-label recall, financial accuracy or final release acceptance.',
        ],
    }
    reader.ensure_unchanged()
    return report


def write_report(report: dict, out: Path):
    target = out.resolve()
    frozen = Path(report['run_root']).resolve()
    if target == frozen or target.is_relative_to(frozen):
        raise ValueError('Diagnostic output must be outside the frozen run')
    target.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(report, ensure_ascii=False, indent=2).encode('utf-8')
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=target.name + '.', suffix='.tmp', delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(data)
    try:
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def markdown_report(report: dict) -> str:
    """Render the same frozen measurements without inventing review verdicts."""
    model, coverage = report['model_runtime'], report['binding_coverage']
    counts = coverage['result_status_counts']
    rate = coverage['both_actual_submissions_rate']
    def value(item):
        return '未记录' if item is None else str(item)
    def cell(item):
        return value(item).replace('|', '\\|').replace('\n', '<br>')
    lines = ['# 冻结任务小样本绑定诊断', '',
             f"任务：`{report.get('run_id')}`", '', f"来源目录：`{report['run_root']}`", '',
             '该报告只读取冻结记录，不重新调用 API、内核或规则，也不修改原任务。模型运行成功与金融核查质量分别呈现。', '',
             '| 指标 | 冻结记录测量 |', '|---|---:|',
             f"| 主范围原文出现位置 | {coverage['primary_occurrences']} |",
             f"| 主范围核实 | {counts.get('verified', 0)} |",
             f"| 主范围违背 | {counts.get('violated', 0)} |",
             f"| 主范围弃权 | {counts.get('abstained', 0)} |",
             f"| 主范围待处理 | {counts.get('pending', 0)} |",
             f"| A/B 均有绑定记录 | {coverage['both_binding_records_occurrences']} |",
             f"| A/B 均经工具真实提交 | {value(coverage['both_actual_submissions_occurrences'])} |",
             f"| A/B 工具提交覆盖率 | {'未记录' if rate is None else format(rate * 100, '.2f') + '%'} |",
             f"| A/B 六字段原始一致 | {coverage['raw_field_equal_occurrences']} |",
             f"| 来源守卫认可的已记录一致绑定 | {coverage['recorded_agreed_occurrences']} |", '',
             '“六字段原始一致”只比较双方保存的字段；“认可一致绑定”还受冻结任务的来源守卫影响。未通过守卫不能用双方一致代替证据。', '',
             '| 模型运行证据 | 测量 |', '|---|---|',
             f"| 记录模型状态 | {cell(model['recorded_model_status'])} |",
             f"| prompt 状态计数 | {cell(json.dumps(model['prompt_status_counts'], ensure_ascii=False))} |",
             f"| 非缓存且非重放的实际请求 | {value(model['non_cached_non_replay_request_count'])} |",
             f"| 缓存响应条数 | {value(model['cached_response_count'])} |",
             f"| 已记录 Token | {value(model['recorded_tokens'])} |",
             f"| 已记录费用 | {value(model['recorded_cost'])} |", '',
             'API 成功不是金融验收通过；费用 unknown 保留未知，不计为零。', '',
             '## 高频类别小样本', '',
             f"每类最多 {report['sample_policy']['sample_per_category']} 条，最多 {report['sample_policy']['category_limit']} 类；按频率及原文位置选择，保留全部条目在 JSON 中。", '']
    for index, group in enumerate(report['samples'], 1):
        category = group['category']
        lines += [f"### {index}. {category['abstention_code']} / {category['first_blocker_kind']} / {category['source_account']}", '',
                  f"该类原文出现 {group['occurrence_count']} 次。", '']
        for row in group['samples']:
            loc, occurrence = row['source_location'], row['occurrence']
            lines += [f"- 条目 `{row.get('claim_id')}`；原文第 {value(loc.get('page'))} 页，字符 `{occurrence.get('start')}:{occurrence.get('end')}`。",
                      f"- 原文：{cell(row['original_context'])}",
                      f"- 解析：{row['parse_status']}；来源守卫：{row['source_guard']['state']}。",
                      f"- A/B 真实提交：{value(row['binding_A']['actually_submitted'])} / {value(row['binding_B']['actually_submitted'])}；六字段分歧：{cell(row['binding_field_differences'])}。",
                      f"- 首阻断：{cell(row.get('first_blocker'))}",
                      f"- 可确定缺维度：{cell(row['known_source_gaps']['missing_dimensions'])}；定义未明：{row['known_source_gaps']['undefined_source_method']}。",
                      f"- 全部冻结阻断：{cell(row['all_recorded_blockers'])}",
                      f"- 证据 ID：{cell(row['evidence_ids'])}", '']
    lines += ['## 数字来源与限制', '', '| 冻结文件 | SHA-256 |', '|---|---|']
    lines += [f'| {cell(name)} | `{hash_value}` |' for name, hash_value in sorted(report['input_hashes'].items())]
    lines += ['', 'JSON 中记录每个主范围出现位置的来源快照、真实工具提交证据、字段分歧、原文、页码、坐标与证据 ID。该诊断不评估真人金标召回、金融准确率或正式发布闸门。', '']
    return '\n'.join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--markdown', type=Path, help='Optional readable report outside the frozen run')
    parser.add_argument('--sample-per-category', type=int, default=2)
    parser.add_argument('--category-limit', type=int, default=8)
    args = parser.parse_args(argv)
    report = diagnose(args.run, sample_per_category=args.sample_per_category, category_limit=args.category_limit)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out = args.out or Path('runs/diagnostics') / f'{args.run.name}-{stamp}.json'
    write_report(report, out)
    if args.markdown:
        target = args.markdown.resolve()
        if target == Path(report['run_root']) or target.is_relative_to(Path(report['run_root'])):
            raise ValueError('Markdown output must be outside the frozen run')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(markdown_report(report), encoding='utf-8')
    print(json.dumps({'run_id': report['run_id'], 'out': str(out), 'model_runtime': report['model_runtime'],
                      'binding_coverage': report['binding_coverage']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
