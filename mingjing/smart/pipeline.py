"""直传任务编排：文件 → 知识库入库（去重）→ 自动识别 → 取材 → 抽取 → 核查/分析 → 报告与审计。

只需给文件即可运行；公司、期间、日期等全部从内容识别，用户填写的可选项仅作覆盖。
"""
from __future__ import annotations

import json
import hashlib
import re
import tempfile
import time
import uuid
from pathlib import Path

from ..audit import AuditLog, digest
from . import EXTRACTOR_VERSION, PARSER_VERSION, PIPELINE_VERSION
from .analysis import analyze
from .claims import build_report_model
from .detect import same_company
from .errors import TaskCancelled, ReplayMismatch
from .kb import TYPE_LABELS, KnowledgeBase
from .render import write_outputs
from .taxonomy import account_name
from .verify import ABSTAIN_LABELS, ERROR_LABELS, Verifier, summarize

FILING_TYPES = ('annual_report', 'semiannual_report', 'quarterly_report', 'prospectus', 'facts_json')


def ingest(paths: list, kb: KnowledgeBase, hints: dict | None = None, origin: str = 'upload') -> list[dict]:
    records = []
    for path in paths:
        path = Path(path)
        if path.is_dir():
            for child in sorted(path.rglob('*')):
                if child.is_file() and child.suffix.lower() in ('.pdf', '.docx', '.md', '.txt'):
                    records.append(kb.add_file(child, origin=origin))
        else:
            records.append(kb.add_file(path, origin=origin, hints=hints if len(paths) == 1 else None))
    return records


def _brief(record: dict, source: str = '') -> dict:
    keys = ('doc_id', 'name', 'doc_type', 'type_label', 'company', 'company_full', 'stock_code', 'period', 'report_date', 'broker', 'title', 'pages', 'facts', 'backend', 'status', 'error')
    return {**{k: record.get(k, '') for k in keys}, 'source': source}


def _texts(kb: KnowledgeBase, doc_ids: list[str]) -> list[dict]:
    pages = []
    for doc_id in doc_ids:
        record = kb.record(doc_id) or {}
        if record.get('doc_type') == 'facts_json':
            continue
        document = kb.document(doc_id)
        for page in document.pages:
            if page.flow.strip():
                pages.append({'name': record.get('name', ''), 'sha': doc_id, 'page': page.number, 'text': page.flow,
                              'company': record.get('company', ''), 'stock_code': record.get('stock_code', ''),
                              'period': record.get('period', ''), 'report_date': record.get('report_date', '')})
    return pages


def check_report(report_id: str, kb: KnowledgeBase, out_dir: Path, *, run_id: str, extra_ids: list[str] | None = None,
                 options: dict | None = None, progress=None, frozen_material_sources: dict | None = None) -> dict:
    options = options or {}
    started = time.time()
    out_dir.mkdir(parents=True, exist_ok=True)
    audit = AuditLog(out_dir, run_id)
    step = (lambda name, pct: progress(name, pct)) if progress else (lambda name, pct: None)
    record = kb.record(report_id)
    overrides = {k: options.get(k) for k in ('company', 'stock_code', 'report_date') if options.get(k)}
    meta = kb.meta(report_id)
    for key, value in overrides.items():
        setattr(meta, key, value)
    step('读取研报', 10)
    document = kb.document(report_id)
    audit.emit('file_access', path=record['name'], sha256=report_id, purpose='research_report', pages=len(document.pages), backend=document.backend)
    step('检索参考材料', 25)
    extra_ids = [d for d in (extra_ids or []) if d != report_id]
    given = [kb.record(d) for d in extra_ids if (kb.record(d) or {}).get('doc_type') in FILING_TYPES]
    if not (meta.company or meta.stock_code):
        pool = given
    else:
        pool = [r for r in given if same_company(r, meta) or not (r.get('company') or r.get('stock_code'))]
    found = [] if options.get('kb_lookup') is False else kb.find_materials(meta, exclude={report_id} | {r['doc_id'] for r in pool})
    materials = [_brief(r, '本次上传') for r in pool] + [_brief(r, '知识库') for r in found]
    if frozen_material_sources is not None:
        # Offline replay preserves how the original task acquired a material.
        # This private argument is never accepted by the public run API.
        for item in materials:
            item['source'] = frozen_material_sources[item['doc_id']]
    facts, diags = [], {}
    cutoff = meta.report_date or ''
    for item in materials:
        published = item.get('report_date') or ''
        if cutoff and published and published > cutoff:
            # 公理二：判定以研报日期为界——研报日期之后才公告的材料不作为当时已知依据
            item['excluded'] = f'{published}公告，晚于研报日期{cutoff}，未用于核查'
            audit.emit('file_access', path=item['name'], sha256=item['doc_id'], purpose='excluded_after_cutoff', published=published, cutoff=cutoff)
            continue
        if cutoff and not published:
            item['excluded'] = '公告日期未知，不能作为截至研报日已公开的依据'
            audit.emit('file_access', path=item['name'], sha256=item['doc_id'], purpose='excluded_unknown_date', cutoff=cutoff)
            continue
        rows, diag = kb.facts(item['doc_id'])
        item['facts'] = len(rows)
        facts.extend(rows)
        diags[item['doc_id']] = {k: diag.get(k) for k in ('facts', 'accounts', 'periods', 'mapped_rows', 'skipped_rows', 'segment_rows') if k in diag}
        audit.emit('file_access', path=item['name'], sha256=item['doc_id'], purpose='reference_material', facts=len(rows), source=item['source'])
    step('抽取研报陈述', 45)
    segments = {f.dimension_id for f in facts if f.dimension_kind != 'total'}
    model = build_report_model(document, meta, segments)
    audit.emit('tool_call', tool='build_report_model', input={'report': report_id[:12]}, output={'claims': len(model.claims), 'tables': len(model.tables), 'by_category': model.stats['by_category']})
    binder = {'mode': 'rule', 'status': 'not_configured'}
    if options.get('llm') and options['llm'].get('enabled'):
        try:
            from .llm_bind import refine_claims
            binder = refine_claims(model, document, kb, options['llm'], audit)
        except Exception as exc:                       # 模型不可用时保持确定性绑定并留痕
            if isinstance(exc, (TaskCancelled, ReplayMismatch)) or options['llm'].get('replay_only'):
                raise
            binder = {'mode': 'rule', 'status': 'fallback', 'error': f'{type(exc).__name__}: {exc}'}
            audit.emit('model_call', status='fallback', error=binder['error'])
    step('确定性核查', 70)
    eligible = [m for m in materials if not m.get('excluded')]
    texts = _texts(kb, [m['doc_id'] for m in eligible]) if options.get('text_evidence', True) else []
    doc_index = {m['doc_id'][:12]: {k: m.get(k, '') for k in ('doc_type', 'period', 'name', 'company', 'company_full', 'stock_code', 'report_date')} for m in eligible}
    verifier = Verifier(facts, model, strict=bool(options.get('strict')), texts=texts, doc_index=doc_index)
    results = verifier.verify_all()
    by_cid = {r.cid: r for r in results}
    items = []
    for claim in model.claims:
        result = by_cid[claim.cid]
        row = {**claim.to_dict(), **{k: v for k, v in result.to_dict().items() if k != 'cid'}}
        row['metric_name'] = account_name(claim.metric) if claim.metric else ''
        row['error_label'] = ERROR_LABELS.get(result.error_type, '')
        row['abstain_label'] = ABSTAIN_LABELS.get(result.abstain_code, '')
        items.append(row)
        audit.emit('judgement', claim_id=claim.cid, input={'metric': claim.metric, 'period': claim.period, 'relation': claim.relation, 'value': claim.value},
                   output={'status': result.status, 'error_type': result.error_type, 'abstain_code': result.abstain_code, 'expected': result.expected,
                           'definition': result.definition, 'formula': result.formula[:300]}, evidence=result.evidence[:6], status=result.status)
    summary = summarize(model.claims, results)
    step('生成报告', 90)
    used_docs = [report_id] + [m['doc_id'] for m in materials]
    kb.touch(used_docs, run_id, 'check')
    payload = {
        'schema': 'mingjing-smart-check.v1', 'pipeline': PIPELINE_VERSION, 'run_id': run_id, 'task': 'check',
        'report': {**_brief(record), **{k: getattr(meta, k) for k in ('company', 'stock_code', 'report_date', 'broker', 'title', 'base_year')}},
        'materials': materials, 'material_diagnostics': diags, 'facts_used': len(facts), 'summary': summary, 'items': items,
        'market': model.market, 'tables': [{'name': t.name, 'kind': t.kind, 'unit': t.unit, 'page': t.page, 'years': t.years, 'rows': len(t.rows)} for t in model.tables],
        'binder': binder, 'options': {k: v for k, v in options.items() if k != 'llm'}, 'mode': 'strict' if options.get('strict') else 'standard',
        'kb': dict(kb.session), 'versions': {'parser': PARSER_VERSION, 'extractor': EXTRACTOR_VERSION, 'pipeline': PIPELINE_VERSION},
        'seconds': round(time.time() - started, 2),
        'gaps': (gaps := build_gaps(items, materials, meta, facts)), 'advice': advice_from_gaps(gaps, materials, meta),
        'market_check': model.stats.get('market_check', {}),
    }
    payload['narrative'] = _narrative(payload, options, kb, audit)
    audit.emit('summary', output={'totals': summary['totals'], 'core': summary['core'], 'historical': summary['historical']})
    write_outputs(payload, out_dir)
    (out_dir / 'facts_used.json').write_text(json.dumps([f.to_dict() for f in facts], ensure_ascii=False), encoding='utf-8')
    audit.session('run_complete', summary=summary['totals'])
    _manifest(out_dir, run_id, 'smart-check', used_docs, kb, payload)
    step('完成', 100)
    return payload


def _narrative(payload: dict, options: dict, kb, audit) -> dict:
    from .render import template_narrative
    llm = options.get('llm') or {}
    if llm.get('enabled'):
        from .llm_bind import write_summary
        return write_summary(payload, llm, kb, audit)
    return {'text': template_narrative(payload), 'source': 'template'}


GAP_KINDS = {'missing_report': '缺整份报告', 'missing_detail': '报告中无该明细', 'definition': '定义缺口', 'external': '需外部来源',
             'mention': '原文信息不足', 'premise': '公式前提不成立', 'conflict': '来源冲突'}
_GAP_ORDER = ('missing_report', 'missing_detail', 'definition', 'external', 'mention', 'premise', 'conflict')
_NEEDS = {'Y': '年度报告', 'H1': '半年度报告', 'H2': '年度报告与半年度报告', 'Q1': '第一季度报告', 'Q2': '半年度报告与第一季度报告',
          'Q3': '第三季度报告与半年度报告', 'Q4': '年度报告与第三季度报告', '9M': '第三季度报告'}


def build_gaps(items: list[dict], materials: list, meta, facts: list | None = None) -> list[dict]:
    """把“未能核实”按缺口类型分组：缺整份报告 / 报告中无该明细 / 定义缺口 / 需外部来源 / 原文信息不足 / 公式前提 / 来源冲突。

    每组写明需要什么、可替代的材料和影响的陈述条数，便于一次补齐。
    """
    from .common import period_label
    statement_periods = {f.period for f in (facts or []) if f.source_type in ('primary_statement', 'financial_summary') and f.dimension_kind == 'total'}
    account_periods: dict[str, set] = {}
    for fact in facts or []:
        if fact.dimension_kind == 'total':
            account_periods.setdefault(fact.account, set()).add(fact.period)
    groups: dict[str, dict] = {}

    def add(key: str, kind: str, need: str, alternative: str, item: dict, what: str) -> None:
        group = groups.setdefault(key, {'kind': kind, 'kind_label': GAP_KINDS[kind], 'need': need, 'alternative': alternative,
                                        'count': 0, 'items': [], 'examples': [], 'categories': {}})
        group['count'] += 1
        group['categories'][item.get('category', '')] = group['categories'].get(item.get('category', ''), 0) + 1
        if what and what not in group['items'] and len(group['items']) < 12:
            group['items'].append(what)
        if len(group['examples']) < 3:
            group['examples'].append({'cid': item['cid'], 'page': item.get('page'), 'text': (item.get('text') or '')[:80]})

    for item in items:
        if item.get('status') != 'unverifiable':
            continue
        code = item.get('abstain_code') or 'X1'
        name = item.get('metric_name') or item.get('metric_text') or item.get('num_text', '')
        if item.get('category') in ('external', 'other_fact'):
            add('external', 'external', '行业、第三方或非报表数据',
                '需外部来源（行业协会、统计部门、第三方数据库或公司其他公告）；当前仅在已入库材料原文中做同值佐证', item, name)
            continue
        if code == 'X3':
            add('definition', 'definition', '券商/数据商自定义口径指标',
                '已登记的常见定义均不一致或缺少计算所需明细；补充券商模板的公式说明后可登记为候选定义，不判为错误', item, name)
            continue
        if code == 'X6':
            add('mention', 'mention', '原文未写明期间、主体或指标，无法定位核对对象', '启用模型语义绑定或人工确认后重新核查', item, name)
            continue
        if code == 'X2':
            add('premise', 'premise', '公式前提不成立（如基期为零或跨零）', '按原文口径人工复核', item, name)
            continue
        if code == 'X4':
            add('conflict', 'conflict', '同一事实在材料中有冲突数值或置信不足', '核对原文页后在知识库中修正识别结果', item, name)
            continue
        keys = set()
        for missing in item.get('missing') or [{}]:
            period = missing.get('period') or ''
            label = missing.get('name') or (account_name(missing['account']) if missing.get('account') else name)
            dimension = re.sub(r'(?:下形成的?|形成的?|的)$', '', (missing.get('dimension') or '').split('/')[0])
            if not dimension and item.get('segment') and period in account_periods.get(missing.get('account', ''), set()):
                dimension = re.sub(r'(?:下形成的?|形成的?|提供的?|的)$', '', item['segment'])      # 合计数已有，缺的是分项（分部/客户/模式）
            what = f"{period_label(period)}{label}" + (f"·{dimension}" if dimension else '')
            if dimension:
                key = 'detail:' + dimension
                need, alternative, kind = f"已入库报告中没有“{dimension}”的明细", '分业务、分客户、业务模式等口径通常见于招股说明书、问询回复或年报“分产品/前五名客户”章节', 'missing_detail'
            elif period and (period not in statement_periods or (missing.get('account') in account_periods and period not in account_periods[missing['account']])):
                kind_code = period[4:] or 'Y'
                need = f"{period[:4]}年{_NEEDS.get(kind_code, '年度报告')}"
                alternative = (f"{int(period[:4]) + 1}年年度报告（含上年比较数）或覆盖该年度的招股说明书" if kind_code == 'Y'
                               else '下一年同期报告（含上年同期比较数）或覆盖该期间的招股说明书')
                key, kind = 'report:' + need, 'missing_report'
            elif period:
                key = 'detail:' + label
                need, alternative, kind = f"已入库报告的报表中没有“{label}”", '该科目可能只在附注或其他章节披露，可补充相应附注页或公告', 'missing_detail'
            else:
                key, need, alternative, kind = 'mention', '原文未写明期间、主体或指标，无法定位核对对象', '启用模型语义绑定或人工确认后重新核查', 'mention'
            if key not in keys:
                keys.add(key)
                add(key, kind, need, alternative, item, what)
    return sorted(groups.values(), key=lambda g: (_GAP_ORDER.index(g['kind']), -g['count'], g['need']))


def advice_from_gaps(gaps: list[dict], materials: list, meta) -> list[str]:
    """由缺口分组生成可执行的补材建议（文字版，供摘要与报告使用）。"""
    advice = []
    name = meta.company or meta.stock_code or '该公司'
    if not [m for m in materials if not m.get('excluded')]:
        advice.append(f'知识库中尚无“{name}”在研报日期前公告的财报。上传其年度报告后重新核查，历史财务陈述即可判定。')
    reports = [g for g in gaps if g['kind'] == 'missing_report']
    details = [g for g in gaps if g['kind'] == 'missing_detail']
    if reports:
        advice.append('建议补充：' + '；'.join(f"{g['need']}（影响{g['count']}条，可替代：{g['alternative']}）" for g in reports[:6]) + '。')
    if details:
        advice.append('已入库报告中没有以下明细：' + '、'.join(g['need'].replace('已入库报告中没有', '').replace('已入库报告的报表中没有', '') + f"（{g['count']}条）" for g in details[:8])
                      + '；分业务、分客户、业务模式等口径通常见于招股说明书、问询回复或年报“分产品/前五名客户”章节。')
    definition = next((g for g in gaps if g['kind'] == 'definition'), None)
    if definition:
        advice.append(f"{definition['count']}条为券商/数据商自定义口径指标（{'、'.join(definition['items'][:5])}），与登记的常见定义不一致，记为定义缺口而非错误。")
    if reports or details:
        advice.append(f'可在巨潮资讯网或交易所官网按证券代码{(" " + meta.stock_code) if meta.stock_code else ""}检索下载，拖入工作台即自动入库并参与下一次核查。')
    return advice


def analyze_filings(doc_ids: list[str], kb: KnowledgeBase, out_dir: Path, *, run_id: str, options: dict | None = None, progress=None) -> dict:
    options = options or {}
    started = time.time()
    out_dir.mkdir(parents=True, exist_ok=True)
    audit = AuditLog(out_dir, run_id)
    step = (lambda name, pct: progress(name, pct)) if progress else (lambda name, pct: None)
    records = [kb.record(d) for d in doc_ids if kb.record(d)]
    anchor = records[0]
    if options.get('company'):
        anchor = {**anchor, 'company': options['company']}
    step('检索同公司材料', 20)
    extra = [] if options.get('kb_lookup') is False else [r for r in kb.find_materials(anchor, exclude={r['doc_id'] for r in records})]
    materials = [_brief(r, '本次上传') for r in records if same_company(r, anchor) or r is records[0]] + [_brief(r, '知识库') for r in extra]
    facts, corroborated = [], []
    step('抽取财报事实', 45)
    for item in materials:
        rows, diag = kb.facts(item['doc_id'])
        item['facts'] = len(rows)
        facts.extend(rows)
        corroborated += [dict(row, source_doc=item['doc_id'][:12]) for row in diag.get('corroborated', [])]
        audit.emit('file_access', path=item['name'], sha256=item['doc_id'], purpose='filing', facts=len(rows), source=item['source'])
    step('计算指标与信号', 70)
    company = anchor.get('company') or anchor.get('company_full') or ''
    result = analyze(facts, company, _texts(kb, [m['doc_id'] for m in materials]), corroborated=corroborated,
                     report_date=options.get('report_date', ''), policy='strict' if options.get('strict') else 'standard',
                     scope=options.get('scope', ''))
    audit.emit('tool_call', tool='analyze', input={'facts': len(facts)}, output={'metrics': len(result['metrics']), 'signals': len(result['signals']),
                                                                            'checks': len(result['checks']), 'failed_checks': result['check_summary']['failed']})
    kb.touch([m['doc_id'] for m in materials], run_id, 'analysis')
    payload = {'schema': 'mingjing-smart-analysis.v1', 'pipeline': PIPELINE_VERSION, 'run_id': run_id, 'task': 'analysis',
               'report': {**_brief(anchor), 'company': company}, 'materials': materials, 'facts_used': len(facts), 'analysis': result,
               'kb': dict(kb.session), 'seconds': round(time.time() - started, 2),
               'versions': {'parser': PARSER_VERSION, 'extractor': EXTRACTOR_VERSION, 'pipeline': PIPELINE_VERSION}}
    step('生成报告', 90)
    payload['narrative'] = _narrative(payload, options, kb, audit)
    write_outputs(payload, out_dir)
    (out_dir / 'facts_used.json').write_text(json.dumps([f.to_dict() for f in facts], ensure_ascii=False), encoding='utf-8')
    audit.session('run_complete', summary={'metrics': len(result['metrics']), 'signals': len(result['signals'])})
    _manifest(out_dir, run_id, 'smart-analysis', [m['doc_id'] for m in materials], kb, payload)
    step('完成', 100)
    return payload


def _manifest(out_dir: Path, run_id: str, mode: str, doc_ids: list[str], kb: KnowledgeBase, payload: dict) -> None:
    import platform
    import sys
    from ..evidence import sha256_file
    inputs = []
    for doc_id in doc_ids:
        record = kb.record(doc_id) or {}
        kb.validated_blob(doc_id)
        inputs.append({'name': record.get('name', ''), 'sha256': doc_id, 'bytes': record.get('size', 0), 'doc_type': record.get('doc_type', ''),
                       'metadata': dict(record)})
    caches = []
    import shutil
    for name, source in kb.cache_dependencies.items():
        target = out_dir / 'model-cache' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        caches.append({'path': target.relative_to(out_dir).as_posix(), 'sha256': sha256_file(target), 'bytes': target.stat().st_size})
    outputs = [{'path': path.name, 'sha256': sha256_file(path), 'bytes': path.stat().st_size} for path in sorted(out_dir.iterdir()) if path.is_file() and path.name != 'run_manifest.json']
    manifest = {'schema_version': 4, 'run_id': run_id, 'mode': mode, 'created_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'python': sys.version.split()[0],
                'platform': platform.platform(), 'inputs': inputs, 'outputs': outputs, 'versions': payload['versions'],
                'model': payload.get('binder', {'mode': 'rule'}), 'results_digest': digest(payload.get('summary') or payload.get('analysis', {}).get('signals', [])),
                'execution_fingerprint': execution_fingerprint(), 'model_cache': caches}
    (out_dir / 'run_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')


def _slug(record: dict, index: int) -> str:
    base = re.sub(r'[^\w\u4e00-\u9fff-]+', '_', (record.get('company') or Path(record.get('name', 'doc')).stem))[:24].strip('_') or 'doc'
    return f'{index:02d}-{base}'


def native_payload(native_results, context: dict) -> dict:
    """Project frozen host judgements into the upload UI; never re-judge here.

    Context is built by the trusted upload adapter, not accepted from HTTP.
    The native callback carries a complete smart_result under each claim ID.
    """
    from dataclasses import fields
    from .verify import Result
    model = context['model']
    allowed = {field.name for field in fields(Result)}
    projected, items = [], []
    records = native_results.get('results', []) if isinstance(native_results, dict) else native_results
    by_id = {row.get('claim_id', row.get('cid', '')): row for row in records}
    by_id.update({row['smart_result']['cid']: row for row in records if (row.get('smart_result') or {}).get('cid')})
    native_claims = native_results.get('claims', []) if isinstance(native_results, dict) else []
    native_by_cid = {c.get('table_context', {}).get('smart_cid'): c['claim_id'] for c in native_claims if c.get('table_context', {}).get('smart_cid')}
    pending_ids = set(native_results.get('pending_claim_ids', [])) if isinstance(native_results, dict) else set()
    for claim in model.claims:
        source = by_id.get(claim.cid, {})
        row = dict(source.get('smart_result') or {})
        if not row:
            row = {'cid': claim.cid, 'status': {'abstained': 'unverifiable'}.get(source.get('status'), source.get('status') or 'unverifiable'),
                   'error_type': source.get('error_type') or '', 'abstain_code': source.get('abstention_code') or 'X3',
                   'expected': source.get('expected') or '', 'formula': source.get('formula') or '',
                   'evidence': source.get('evidence') or [], 'operands': source.get('operands') or [],
                   'reason': source.get('reason') or '原生任务尚未完成该位置的绑定与判定'}
        row['cid'] = claim.cid
        result = Result(**{k: v for k, v in row.items() if k in allowed})
        projected.append(result)
        item = {**claim.to_dict(), **{k: v for k, v in result.to_dict().items() if k != 'cid'}}
        item.update(metric_name=account_name(claim.metric) if claim.metric else '',
                    error_label=ERROR_LABELS.get(result.error_type, ''), abstain_label=ABSTAIN_LABELS.get(result.abstain_code, ''),
                    rule_ids=source.get('rule_ids', []), blockers=source.get('blockers', []))
        item['execution_status'] = 'pending' if native_by_cid.get(claim.cid) in pending_ids else 'judged' if source else 'unmapped'
        items.append(item)
    summary = summarize(model.claims, projected)
    report = dict(context.get('report') or model.meta.to_dict())
    materials = context.get('materials', [])
    facts = context.get('facts', [])
    options = context.get('options', {})
    payload = {'schema': 'mingjing-smart-check.v2', 'pipeline': PIPELINE_VERSION, 'run_id': context['run_id'], 'task': 'check',
               'report': report, 'materials': materials, 'material_diagnostics': context.get('material_diagnostics', {}),
               'facts_used': context.get('facts_used', len(facts)), 'summary': summary, 'items': items,
               'market': model.market, 'market_check': model.stats.get('market_check', {}),
               'tables': [{'name': t.name, 'kind': t.kind, 'unit': t.unit, 'page': t.page, 'years': t.years, 'rows': len(t.rows)} for t in model.tables],
               'binder': context.get('binder', {}), 'options': {k: v for k, v in options.items() if k != 'llm'},
               'mode': 'strict' if options.get('strict') else 'standard', 'seconds': context.get('seconds', 0), 'kb': context.get('kb', {}),
               'versions': {'parser': PARSER_VERSION, 'extractor': EXTRACTOR_VERSION, 'pipeline': PIPELINE_VERSION},
               'native_run_id': context.get('native_run_id'), 'session_id': context.get('session_id'),
               'canonical_run_path': context.get('canonical_run_path'),
               'execution': context.get('execution', native_results.get('execution', {}) if isinstance(native_results, dict) else {})}
    coverage = native_results.get('coverage', {}) if isinstance(native_results, dict) else {}
    pending = sum(item['execution_status'] in {'pending', 'unmapped'} for item in items)
    payload['model'] = native_results.get('model', {}) if isinstance(native_results, dict) else {}
    payload['partial'] = bool(native_results.get('partial')) if isinstance(native_results, dict) else bool(pending)
    payload['coverage_ledger'] = coverage
    payload['execution_statistics'] = {'processed_fraction': coverage.get('coverage'),
        'pending_ledger_mentions': coverage.get('counts', {}).get('pending', pending),
        'pending_claims': pending, 'legal_unverifiable': max(0, summary['totals']['unverifiable'] - pending),
        'effective_coverage': summary['historical']['coverage'], 'basis': summary.get('basis', {}),
        'presumed_results': summary['presumed_results'], 'recall': coverage.get('recall', 'unknown_requires_independent_gold')}
    payload['gaps'] = build_gaps(items, materials, model.meta, facts)
    payload['advice'] = advice_from_gaps(payload['gaps'], materials, model.meta)
    from .render import template_narrative
    payload['narrative'] = {'text': template_narrative(payload), 'source': 'template'}
    return payload


def run_task(doc_ids: list[str], kb: KnowledgeBase, out_root: str | Path, *, mode: str = 'auto', options: dict | None = None,
             run_id: str | None = None, progress=None) -> dict:
    """mode: check（研报核查）/ analysis（财报分析）/ auto（按识别结果自动决定）。支持批量。"""
    run_id = run_id or f'smart-{uuid.uuid4().hex[:12]}'
    root = Path(out_root) / run_id
    root.mkdir(parents=True, exist_ok=True)
    records = [kb.record(d) for d in doc_ids if kb.record(d)]
    reports = [r for r in records if r.get('doc_type') == 'research_report']
    filings = [r for r in records if r.get('doc_type') in FILING_TYPES]
    others = [r for r in records if r not in reports and r not in filings]
    if mode == 'check':
        reports += others
    elif mode == 'analysis':
        filings += [r for r in others]
        reports = []
    elif not reports and not filings:
        reports = others
    children = []
    failures = []
    if reports:
        for index, record in enumerate(reports, 1):
            def sub(name, pct, _i=index):
                if progress:
                    progress(f'[{_i}/{len(reports)}] {record.get("company") or record["name"][:16]} · {name}', int(((_i - 1) + pct / 100) / len(reports) * 100))
            try:
                payload = check_report(record['doc_id'], kb, root / _slug(record, index), run_id=run_id,
                                       extra_ids=[r['doc_id'] for r in filings], options=options, progress=sub)
                children.append({'item_id': _slug(record, index), 'task': 'check', 'name': record['name'], 'company': payload['report']['company'],
                                 'report_date': payload['report']['report_date'], 'totals': payload['summary']['totals'], 'core': payload['summary']['core'],
                                 'historical': payload['summary']['historical'], 'financial': payload['summary']['financial'],
                                 'materials': len(payload['materials']), 'seconds': payload['seconds']})
            except Exception as exc:
                if isinstance(exc, (TaskCancelled, ReplayMismatch)):
                    raise
                failures.append({'name': record['name'], 'error': f'{type(exc).__name__}: {exc}'})
    elif filings:
        groups: list[list[dict]] = []
        for record in filings:
            group = next((g for g in groups if same_company(g[0], record)), None)
            (group.append(record) if group else groups.append([record]))
        for index, group in enumerate(groups, 1):
            def sub(name, pct, _i=index):
                if progress:
                    progress(f'[{_i}/{len(groups)}] {group[0].get("company") or group[0]["name"][:16]} · {name}', int(((_i - 1) + pct / 100) / len(groups) * 100))
            try:
                payload = analyze_filings([r['doc_id'] for r in group], kb, root / _slug(group[0], index), run_id=run_id, options=options, progress=sub)
                children.append({'item_id': _slug(group[0], index), 'task': 'analysis', 'name': group[0]['name'], 'company': payload['report']['company'],
                                 'periods': payload['analysis']['periods'], 'signals': len(payload['analysis']['signals']),
                                 'metrics': len(payload['analysis']['metrics']), 'materials': len(payload['materials']), 'seconds': payload['seconds']})
            except Exception as exc:
                if isinstance(exc, (TaskCancelled, ReplayMismatch)):
                    raise
                failures.append({'name': group[0]['name'], 'error': f'{type(exc).__name__}: {exc}'})
    batch = {'schema': 'mingjing-smart-batch.v1', 'run_id': run_id, 'mode': mode, 'task': 'check' if reports else 'analysis' if filings else 'none',
             'created_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'inputs': [_brief(r) for r in records], 'items': children, 'failures': failures,
             'kb': dict(kb.session), 'out': str(root)}
    (root / 'batch.json').write_text(json.dumps(batch, ensure_ascii=False, indent=2), encoding='utf-8')
    return batch


def execution_fingerprint() -> dict:
    """Lock executed financial code and rules, including the upload adapters."""
    import marshal
    import importlib
    modules = {}
    for name in ('common', 'claims', 'detect', 'docio', 'tables', 'taxonomy', 'verify', 'analysis', 'pipeline', 'llm_bind'):
        module = importlib.import_module(f'{__package__}.{name}')
        path = Path(module.__file__)
        raw = path.read_bytes() if path.is_file() else marshal.dumps(module.__loader__.get_code(module.__name__))
        modules[name] = hashlib.sha256(raw).hexdigest()
    from ..audit import kernel_fingerprint
    return {'modules': modules, 'kernel': kernel_fingerprint()}


def comparison_payload(payload: dict) -> dict:
    """All business fields count; only runtime counters and paths are volatile."""
    keys = ('task', 'report', 'materials', 'facts_used', 'summary', 'items', 'analysis', 'tables', 'market', 'market_check',
            'gaps', 'advice', 'mode', 'partial', 'coverage_ledger', 'execution_statistics', 'execution')
    return json.loads(json.dumps({key: payload.get(key) for key in keys if key in payload},
                                 ensure_ascii=False, default=str))


def replay(item_dir: str | Path, kb: KnowledgeBase) -> dict:
    """离线重放：用知识库中按哈希登记的同一批输入、同一选项重算，逐条比较结论。

    原运行目录只读；启用过模型绑定的任务只从知识库缓存重放模型应答，缓存缺失即失败，不联网。
    """
    item_dir = Path(item_dir)
    payload = json.loads((item_dir / 'results.json').read_text(encoding='utf-8'))
    manifest = json.loads((item_dir / 'run_manifest.json').read_text(encoding='utf-8'))
    from ..evidence import sha256_file
    integrity = []
    for row in manifest.get('outputs', []) + manifest.get('model_cache', []):
        path = (item_dir / row['path']).resolve()
        if not path.is_relative_to(item_dir.resolve()) or not path.is_file() or path.stat().st_size != row['bytes'] or sha256_file(path) != row['sha256']:
            integrity.append(row['path'])
    if integrity:
        return {'status': 'integrity_mismatch', 'issues': integrity, 'network_calls': 0}
    if manifest.get('execution_fingerprint') != execution_fingerprint():
        return {'status': 'incompatible_version', 'reason': 'financial_code_or_rules_changed', 'network_calls': 0}
    if payload.get('canonical_run_path'):
        from ..replay import replay_run
        receipt = replay_run(payload['canonical_run_path'])
        return {**receipt, 'status': 'identical' if receipt.get('status') == 'passed' else receipt.get('status'),
                'compared': len(payload.get('items', [])), 'differences': len(receipt.get('differences', [])),
                'diffs': receipt.get('differences', []), 'network_calls': 0}
    missing = [row for row in manifest.get('inputs', []) if not kb.record(row['sha256'])]
    if missing:
        return {'status': 'missing_inputs', 'missing': [row['name'] for row in missing]}
    for row in manifest.get('inputs', []):
        try:
            kb.validated_blob(row['sha256'])
        except (ValueError, OSError):
            return {'status': 'integrity_mismatch', 'issues': [row['sha256']], 'network_calls': 0}
    options = dict(payload.get('options') or {})
    options['kb_lookup'] = False
    binder = payload.get('binder') or {}
    if binder.get('mode') == 'llm':
        options['llm'] = {**binder.get('request_config', {}), 'enabled': True, 'api_key': 'replay-only', 'model': binder.get('model', ''), 'replay_only': True}
    with tempfile.TemporaryDirectory(prefix='mingjing-replay-') as temp:
        target = Path(temp) / 'replay'
        frozen_kb = KnowledgeBase(Path(temp) / 'kb')
        frozen_index = {'version': 1, 'docs': {}}
        import shutil
        for row in manifest['inputs']:
            record = row.get('metadata') or kb.record(row['sha256'])
            frozen_index['docs'][row['sha256']] = record
            source = kb.validated_blob(row['sha256'])
            destination = frozen_kb.blob_path(row['sha256'], record['ext'])
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        frozen_kb._save(frozen_index)
        for row in manifest.get('model_cache', []):
            relative = Path(row['path']).relative_to('model-cache')
            destination = frozen_kb.root / 'cache' / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(item_dir / row['path'], destination)
        if payload['task'] == 'check':
            fresh = check_report(payload['report']['doc_id'], frozen_kb, target, run_id=payload['run_id'] + '-replay',
                                 extra_ids=[m['doc_id'] for m in payload['materials']], options=options,
                                 frozen_material_sources={m['doc_id']: m['source'] for m in payload['materials']})
            old = {item['cid']: item for item in payload['items']}
            new = {item['cid']: item for item in fresh['items']}
            diffs = []
            for cid in sorted(set(old) | set(new)):
                a, b = old.get(cid, {}), new.get(cid, {})
                changed = {key: [a.get(key), b.get(key)] for key in set(a) | set(b) if a.get(key) != b.get(key)}
                if changed:
                    diffs.append({'cid': cid, 'changed': changed})
            same_summary = comparison_payload(fresh) == comparison_payload(payload)
            if not same_summary:
                from ..replay import _analysis_differences
                before = comparison_payload(payload)
                after = comparison_payload(fresh)
                before.pop('items', None)
                after.pop('items', None)
                diffs.extend(_analysis_differences(before, after, 'projection'))
            compared = len(old)
        else:
            fresh = analyze_filings([m['doc_id'] for m in payload['materials']], frozen_kb, target, run_id=payload['run_id'] + '-replay', options=options)
            diffs = [] if comparison_payload(fresh) == comparison_payload(payload) else [{'analysis': '指标、公式、证据或信号不同'}]
            same_summary = not diffs
            compared = len(payload['analysis']['metrics']) + len(payload['analysis']['signals'])
    receipt = {'schema': 'mingjing-smart-replay.v1', 'run_id': payload['run_id'], 'task': payload['task'],
               'status': 'identical' if not diffs and same_summary else 'different', 'compared': compared, 'differences': len(diffs),
               'diffs': diffs[:50], 'inputs': [row['sha256'][:12] for row in manifest.get('inputs', [])],
               'versions': {'recorded': payload.get('versions'), 'current': fresh.get('versions')}, 'replayed_at': time.strftime('%Y-%m-%d %H:%M:%S')}
    kb._journal('replayed', '', run_id=payload['run_id'], status=receipt['status'], differences=len(diffs))
    return receipt
