"""Upload/batch adapter for the application's native run registry and lifecycle."""
from __future__ import annotations

import csv
import json
import shutil
import threading
import time
from pathlib import Path

from .detect import DocMeta, same_company
from .docio import sha256_path
from .errors import TaskCancelled
from .ingestion import MANIFEST

FILING_TYPES = {'annual_report', 'semiannual_report', 'quarterly_report', 'prospectus', 'facts_json'}


class SmartRunAdapter:
    def __init__(self, state, kb, runs_root):
        self.state, self.kb, self.runs_root = state, kb, Path(runs_root)

    def freeze(self, records, root):
        """Immutable selected originals plus metadata; no material fact array crosses HTTP."""
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        documents, rows = [], []
        for record in records:
            sha = record['doc_id']
            source = self.kb.blob_path(sha, record['ext'])
            if source.is_symlink() or not source.is_file() or sha256_path(source) != sha or source.stat().st_size != record['size']:
                raise ValueError('知识库原件与上传哈希或大小不一致')
            target = root / (sha + record['ext'])
            if not target.is_file():
                shutil.copy2(source, target)
            if sha256_path(target) != sha:
                raise ValueError('冻结上传材料复制校验失败')
            meta = {key: record.get(key, getattr(DocMeta(), key)) for key in DocMeta().to_dict()}
            documents.append({'file': target.name, 'sha256': sha, 'size_bytes': target.stat().st_size,
                              'name': record['name'], 'metadata': meta, 'user_meta': record.get('user_meta', {}),
                              'metadata_provenance': 'deterministic_detection_with_logged_user_corrections'})
            rows.append({'file': target.name, 'name': meta['company'], 'code': meta['stock_code'], 'period': meta['period'],
                         'date': meta['report_date'], 'sha256': sha, 'size_bytes': target.stat().st_size})
        (root / MANIFEST).write_text(json.dumps({'schema': 'mingjing-smart-materials.v2', 'documents': documents}, ensure_ascii=False, indent=2), encoding='utf-8')
        with (root / 'index.csv').open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=['file', 'name', 'code', 'period', 'date', 'sha256', 'size_bytes'])
            writer.writeheader()
            writer.writerows(rows)
        return documents

    def cancel(self, record):
        record.setdefault('_cancel', threading.Event()).set()
        for child in record.get('children', []):
            task = self.state.get_task(child['native_run_id'])
            if task:
                task.cancelled.set()

    def resume(self, record):
        if self.state.sessions.closing:
            raise ValueError('服务正在退出')
        children = record.get('children') or []
        if not children:
            raise ValueError('该历史批次没有原生检查点，请创建新任务')
        resumed = []
        for child in children:
            task = self.state.get_task(child['native_run_id'])
            if task and task.summary().get('can_resume'):
                task.resume(lifecycle_lock=self.state.lock, is_closing=lambda: self.state.sessions.closing)
                resumed.append(task.id)
        if not resumed:
            raise ValueError('没有可恢复的原生检查点；冻结结果可创建新版本')
        record['_cancel'] = threading.Event()
        return resumed

    def add_materials(self, record, item_id, payload):
        import uuid
        child = next((c for c in record.get('children', []) if c['item_id'] == item_id), None)
        if child is None:
            raise KeyError(item_id)
        identifiers = payload.get('doc_ids') or []
        if not isinstance(identifiers, list) or any(not isinstance(d, str) or self.kb.record(d) is None for d in identifiers):
            raise ValueError('补证材料必须是已入库ID数组')
        if not identifiers and not payload.get('scope_declaration'):
            raise ValueError('至少选择补证材料或填写口径声明')
        session_id = child.get('session_id')
        if not session_id:
            session = self.state.sessions.create(child['native_run_id'])
            session_id = session.get('session_id', session.get('id', ''))
        material = {}
        if identifiers:
            target = self.runs_root / record['run_id'] / item_id / ('supplement-' + uuid.uuid4().hex[:12])
            self.freeze([self.kb.record(d) for d in identifiers], target)
            material['corpus_additions'] = [str(target)]
        if 'scope_declaration' in payload:
            material['scope_declaration'] = payload['scope_declaration']
        result = self.state.sessions.add_materials(session_id, material)
        self.kb.touch(identifiers, result['run_id'], 'material_supplement')
        # A material supplement is a new native version with its own upload
        # history entry. The frozen parent remains visible and unchanged.
        batch_id = 'smart-' + uuid.uuid4().hex[:12]
        service = self.state.smart
        new_child = {**child, 'native_run_id': result['run_id'], 'session_id': '', 'status': 'queued',
                     'canonical_run_path': str(self.state.get_task(result['run_id']).out),
                     'material_doc_ids': list(dict.fromkeys(child['material_doc_ids'] + identifiers))}
        new_record = {'run_id': batch_id, 'status': 'running', 'mode': record['mode'],
                      'docs': list(dict.fromkeys(record['docs'] + identifiers)), 'names': record.get('names', []),
                      'progress': 0, 'phase': '补证新版本核查中', 'started_at': time.time(), 'options': dict(record['options']),
                      'parent_batch_id': record['run_id'], 'parent_run_id': child['native_run_id'],
                      'children': [new_child], '_cancel': threading.Event()}
        (self.runs_root / batch_id).mkdir(parents=True, exist_ok=True)
        with service.lock:
            service.runs[batch_id] = new_record
            service._persist()
        worker = threading.Thread(target=service._resume_work, args=(new_record,), daemon=True, name='mingjing-smart-supplement')
        new_record['_thread'] = worker
        worker.start()
        result.update(batch_id=batch_id, smart_run_id=batch_id)
        return result

    def run_batch(self, doc_ids, mode, options, run_id, record, progress):
        from .pipeline import _brief, _slug, native_payload, _manifest
        from .render import write_outputs
        config_path = self.runs_root / run_id / 'config_snapshot.json'
        if record.get('_config_snapshot') is not None:
            frozen_config = record['_config_snapshot']
        elif config_path.is_file() and sha256_path(config_path) == record.get('config_sha256'):
            frozen_config = json.loads(config_path.read_text(encoding='utf-8'))
        else:
            raise ValueError('上传批次配置快照缺失或已改变')
        selected = [self.kb.record(d) for d in doc_ids]
        reports = [r for r in selected if r['doc_type'] == 'research_report']
        filings = [r for r in selected if r['doc_type'] in FILING_TYPES]
        other = [r for r in selected if r not in reports and r not in filings]
        if mode == 'check':
            reports += other
        elif mode == 'analysis':
            filings += other
            reports = []
        elif not reports and not filings:
            reports = other
        plans = []
        if reports:
            for anchor in reports:
                given = [r for r in filings if same_company(r, anchor) or not r.get('company')]
                plans.append(('check', anchor, given))
        else:
            groups = []
            for filing in filings:
                group = next((g for g in groups if same_company(g[0], filing)), None)
                if group is None:
                    groups.append([filing])
                else:
                    group.append(filing)
            plans = [('analysis', group[0], group) for group in groups]
        root = self.runs_root / run_id
        root.mkdir(parents=True, exist_ok=True)
        children, failures = [], []
        record.setdefault('children', [])
        for index, (task_type, anchor, given) in enumerate(plans, 1):
            if record['_cancel'].is_set():
                raise TaskCancelled('用户取消了任务')
            anchor = {**anchor, **{k: options[k] for k in ('company', 'stock_code', 'report_date') if options.get(k)}}
            extra = [] if options.get('kb_lookup') is False else self.kb.find_materials(anchor, exclude={anchor['doc_id']} | {r['doc_id'] for r in given})
            materials = list({r['doc_id']: r for r in given + list(extra)}.values())
            item_id = _slug(anchor, index)
            item_root = root / item_id
            source_root = item_root / 'materials'
            self.freeze(materials, source_root)
            request = {'run_id': f'{run_id}-{index:02d}', 'corpus': str(source_root), 'mode': 'harness',
                       'task_type': task_type, 'company': anchor.get('company') or anchor.get('company_full') or '',
                       'period': anchor.get('period') or '', 'report_date': anchor.get('report_date') or '',
                       'scope_declaration': options.get('scope', ''),
                       'material_profile': 'smart', 'validation_policy': 'strict' if options.get('strict') else 'standard',
                       'smart_options': {k: options[k] for k in ('text_evidence', 'use_llm') if k in options},
                       'parent_batch_id': run_id}
            if task_type == 'check':
                draft_root = item_root / 'draft'
                self.freeze([anchor], draft_root)
                request['draft'] = str(draft_root / (anchor['doc_id'] + anchor['ext']))
                request['smart_draft_metadata'] = {k: anchor.get(k, getattr(DocMeta(), k)) for k in DocMeta().to_dict()}
            task = self.state.launch(request, config_snapshot=frozen_config)
            child = {'item_id': item_id, 'native_run_id': task.id, 'task': task_type,
                     'name': anchor['name'], 'company': request['company'], 'canonical_run_path': str(task.out),
                     'anchor_doc_id': anchor['doc_id'], 'material_doc_ids': [r['doc_id'] for r in materials]}
            record['children'].append(child)
            self.kb.touch([r['doc_id'] for r in materials] + [anchor['doc_id']], task.id, task_type)
            started = time.monotonic()
            while task.thread.is_alive():
                if record['_cancel'].is_set():
                    task.cancelled.set()
                event = next((e for e in reversed(task.events) if e.get('event') == 'phase'), {})
                progress(f'[{index}/{len(plans)}] {anchor.get("company") or anchor["name"]} · {event.get("name", "原生任务")}',
                         int(((index - 1) + event.get('percent', 5) / 100) / max(1, len(plans)) * 100))
                task.thread.join(.2)
            if task.cancelled.is_set() or record['_cancel'].is_set():
                raise TaskCancelled('原生子任务已取消；检查点保留，可恢复')
            if not (task.out / 'results.json').is_file():
                failures.append({'name': anchor['name'], 'error': task.error or '原生任务未生成结果', 'native_run_id': task.id})
                continue
            native = json.loads((task.out / 'results.json').read_text(encoding='utf-8'))
            context = {'run_id': run_id, 'report': _brief(anchor), 'materials': [_brief(r, '冻结上传材料') for r in materials],
                       'model': getattr(getattr(task, 'agent', None), 'smart_model', None), 'options': options,
                       'facts': getattr(getattr(task, 'agent', None), 'facts', []),
                       'binder': {'source': 'native_harness', 'status': native.get('model', {}).get('status')},
                       'seconds': round(time.monotonic() - started, 2), 'kb': dict(self.kb.session),
                       'native_run_id': task.id, 'canonical_run_path': str(task.out)}
            payload = native_payload(native, context)
            if task_type == 'analysis' and native.get('smart_analysis') is not None:
                payload.update(task='analysis', schema='mingjing-smart-analysis.v2', analysis=native['smart_analysis'])
            session_id = ''
            try:
                session = self.state.sessions.create(task.id)
                session_id = session.get('session_id', session.get('id', ''))
            except (ValueError, KeyError):
                pass
            payload.update(native_run_id=task.id, canonical_run_path=str(task.out), session_id=session_id)
            child.update(session_id=session_id, status=task.status, model=native.get('model', {}))
            if task_type == 'check':
                child.update({k: payload['summary'][k] for k in ('totals', 'core', 'historical', 'financial')})
            else:
                analysis = payload.get('analysis') or {}
                child.update(periods=analysis.get('periods', []), metrics=len(analysis.get('metrics', [])), signals=len(analysis.get('signals', [])))
            child.update(materials=len(materials), seconds=context['seconds'])
            write_outputs(payload, item_root)
            _manifest(item_root, run_id, task_type, [r['doc_id'] for r in materials] + [anchor['doc_id']], self.kb, payload)
            children.append(child)
        batch = {'schema': 'mingjing-smart-batch.v2', 'run_id': run_id, 'mode': mode,
                 'task': plans[0][0] if plans else 'none', 'items': children, 'failures': failures,
                 'inputs': [_brief(r) for r in selected], 'kb': dict(self.kb.session), 'out': str(root)}
        (root / 'batch.json').write_text(json.dumps(batch, ensure_ascii=False, indent=2), encoding='utf-8')
        return batch

    def refresh_record(self, record, progress):
        """Resume only native children; rebuild presentation after their workers finish."""
        from .pipeline import _brief, native_payload, _manifest
        from .render import write_outputs
        from .claims import ReportModel, ReportTable, Claim
        from .detect import DocMeta
        children, failures = [], []
        for child in record.get('children', []):
            task = self.state.get_task(child['native_run_id'])
            if task is None:
                failures.append({'name': child['name'], 'error': '原生任务未登记'})
                continue
            while task.thread.is_alive():
                if record['_cancel'].is_set():
                    task.cancelled.set()
                    raise TaskCancelled('用户取消了恢复')
                progress('原生检查点恢复中', 50)
                task.thread.join(.2)
            if task.status == 'cancelled':
                raise TaskCancelled('原生任务已取消')
            if not (task.out / 'results.json').is_file():
                failures.append({'name': child['name'], 'error': task.error})
                continue
            raw = json.loads((task.out / 'smart_report_model.json').read_text(encoding='utf-8'))
            from .ingestion import report_model_from_dict
            model = report_model_from_dict(raw)
            native = json.loads((task.out / 'results.json').read_text(encoding='utf-8'))
            anchor = self.kb.record(child['anchor_doc_id'])
            materials = [self.kb.record(d) for d in child['material_doc_ids']]
            context = {'run_id': record['run_id'], 'model': model, 'report': _brief(anchor),
                       'materials': [_brief(r, '冻结上传材料') for r in materials], 'options': record['options'],
                       'facts': getattr(getattr(task, 'agent', None), 'facts', []), 'kb': dict(self.kb.session),
                       'binder': {'source': 'native_harness', 'status': native.get('model', {}).get('status')},
                       'native_run_id': task.id, 'canonical_run_path': str(task.out), 'execution': native.get('execution', {})}
            payload = native_payload(native, context)
            session_id = child.get('session_id', '')
            if (task.out / 'run_manifest.json').is_file() and not session_id:
                session = self.state.sessions.create(task.id)
                session_id = session.get('session_id', session.get('id', ''))
            payload.update(session_id=session_id)
            child.update(status=task.status, session_id=session_id)
            if child['task'] == 'analysis':
                payload.update(task='analysis', schema='mingjing-smart-analysis.v2', analysis=native.get('smart_analysis') or {})
            else:
                child.update({k: payload['summary'][k] for k in ('totals', 'core', 'historical', 'financial')})
            item_root = self.runs_root / record['run_id'] / child['item_id']
            write_outputs(payload, item_root)
            _manifest(item_root, record['run_id'], child['task'], child['material_doc_ids'] + [child['anchor_doc_id']], self.kb, payload)
            children.append(child)
        batch = {'schema': 'mingjing-smart-batch.v2', 'run_id': record['run_id'], 'mode': record['mode'],
                 'task': children[0]['task'] if children else 'none', 'items': children, 'failures': failures,
                 'out': str(self.runs_root / record['run_id']), 'kb': dict(self.kb.session)}
        (self.runs_root / record['run_id'] / 'batch.json').write_text(json.dumps(batch, ensure_ascii=False, indent=2), encoding='utf-8')
        return batch
