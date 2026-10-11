"""直传模式 Web 接口（挂载在 mingjing.server，也可独立运行：python -m mingjing.smart.web）。

GET    /api/kb                      知识库列表与统计
GET    /api/kb/journal              入库/去重/引用留痕
POST   /api/kb/upload?name=<文件名>  原始字节上传（application/octet-stream），自动识别并去重入库
PATCH  /api/kb/<doc_id>             修正公司/期间/日期/类型/标签（可选项）
DELETE /api/kb/<doc_id>             从知识库移除
GET    /api/kb/<doc_id>/file        下载原件
GET    /api/kb/evidence/<证据ID>.png 原文页图（坐标可用时高亮）
GET    /api/smart/llm               模型绑定是否已配置
POST   /api/smart/run               {doc_ids, mode: auto|check|analysis, options}
POST   /api/smart/demo              用随包虚构样例（两家公司四份年报、季报与半年报、三份研报）一键演示
GET    /api/smart/status            管线版本、PDF 后端、知识库与模型状态
GET    /api/smart/settings          模型（可选）配置；POST 保存（密钥只进入安全存储，不回显）
POST   /api/smart/runs/<run_id>/items/<item>/replay   离线重放该条结果并逐条比较
GET    /api/kb/journal/verify       留痕日志哈希链校验
GET    /api/kb/<doc_id>/page/<n>.png[?bbox=x0,y0,x1,y1]   原文页图（坐标可用时高亮）
GET    /api/smart/runs              任务历史
GET    /api/smart/runs/<run_id>     进度与批量摘要
POST   /api/smart/runs/<run_id>/cancel
GET    /api/smart/runs/<run_id>/items/<item>/<results.json|report.html|report.md|corrections.csv>
GET    /api/smart/runs/<run_id>/export   整个任务目录打包下载
"""
from __future__ import annotations

import io
import json
import mimetypes
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, unquote

from . import PIPELINE_VERSION
from .docio import available_backends
from .kb import KnowledgeBase
from .errors import TaskCancelled as Cancelled

_PROJECT = Path(__file__).resolve().parents[2]
_BUNDLE = Path(getattr(sys, '_MEIPASS', _PROJECT))          # 与 mingjing.server.STATIC_ROOT 的定位规则一致
STATIC = next((c for c in (_BUNDLE / 'web' / 'smart', _PROJECT / 'web' / 'smart') if c.exists()), _PROJECT / 'web' / 'smart')
_RUN_ID = re.compile(r'smart-[0-9a-f]{12}')
_DOC_ID = re.compile(r'[0-9a-f]{64}')


def _send(handler, status: int, body: bytes, content_type: str, extra: dict | None = None) -> None:
    handler.send_response(status)
    handler.send_header('Content-Type', content_type)
    handler.send_header('Content-Length', str(len(body)))
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('X-Content-Type-Options', 'nosniff')
    for key, value in (extra or {}).items():
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(body)


def _json(handler, status: int, payload) -> None:
    _send(handler, status, json.dumps(payload, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')


def _read(handler, limit: int) -> bytes:
    length = handler.headers.get('Content-Length', '0')
    if not re.fullmatch(r'\d{1,12}', length or ''):
        raise ValueError('需要 Content-Length')
    size = int(length)
    if size > limit:
        raise ValueError(f'文件超过上限 {limit // (1024 * 1024)} MB')
    chunks, remaining = [], size
    while remaining:
        block = handler.rfile.read(min(remaining, 1 << 20))
        if not block:
            raise ValueError('上传内容不完整')
        chunks.append(block)
        remaining -= len(block)
    return b''.join(chunks)


def render_page(pdf: Path, page: int, bbox: list | None, cache_dir: Path) -> bytes:
    """渲染原文页：PyMuPDF（可高亮）→ pdftoppm → 失败抛错。结果按页与框缓存。"""
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = f'p{page}-' + ('-'.join(str(int(v)) for v in bbox) if bbox else 'full') + '.png'
    target = cache_dir / key
    if target.is_file():
        return target.read_bytes()
    raw = None
    try:
        import fitz  # type: ignore
        with fitz.open(str(pdf)) as document:
            sheet = document[page - 1]
            if bbox and any(bbox):
                sheet.draw_rect(fitz.Rect(*bbox), color=(0.85, 0.15, 0.1), width=1.6)
            raw = sheet.get_pixmap(dpi=120).tobytes('png')
    except Exception:
        raw = None
    if raw is None and shutil.which('pdftoppm'):
        base = cache_dir / f'tmp-{uuid.uuid4().hex}'
        subprocess.run(['pdftoppm', '-f', str(page), '-l', str(page), '-r', '110', '-png', '-singlefile', str(pdf), str(base)],
                       capture_output=True, timeout=60, check=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        produced = base.with_suffix('.png')
        raw = produced.read_bytes()
        produced.unlink(missing_ok=True)
        if bbox and any(bbox):
            try:
                from PIL import Image, ImageDraw
                image = Image.open(io.BytesIO(raw)).convert('RGB')
                scale = 110 / 72
                ImageDraw.Draw(image).rectangle([v * scale for v in bbox], outline=(217, 38, 26), width=3)
                buffer = io.BytesIO()
                image.save(buffer, 'PNG')
                raw = buffer.getvalue()
            except Exception:
                pass
    if raw is None:
        raise RuntimeError('当前环境没有可用的 PDF 渲染器（需要 PyMuPDF 或 poppler）')
    target.write_bytes(raw)
    return raw


class MemorySettings:
    """轻量模式（python -m mingjing.smart.web）的进程内设置：不写盘，密钥只保存在本进程内存中。"""
    persistent = False

    def __init__(self):
        self.data, self.secrets = {'llm': {}}, {}

    def get(self) -> dict:
        return self.data

    def update(self, patch: dict) -> dict:
        for key, value in patch.items():
            if isinstance(value, dict):
                self.data.setdefault(key, {}).update(value)
            else:
                self.data[key] = value
        return self.data

    def secret(self, ref: str) -> str:
        return self.secrets.get(ref, '')

    def set_secret(self, ref: str, value: str) -> None:
        self.secrets[ref] = value


class SmartService:
    def __init__(self, kb_root: str | Path, runs_root: str | Path, *, config=None, secret=None, store=None, max_upload_mb: int = 200, host=None):
        if store is None and config is None:
            store = MemorySettings()
            config, secret = store.get, store.secret
        self.store = store
        self.kb = KnowledgeBase(kb_root)
        self.runs_root = Path(runs_root)
        self.runs_root.mkdir(parents=True, exist_ok=True)
        self.config = config or (lambda: {})
        self.secret = secret or (lambda ref: '')
        self.limit = max_upload_mb * 1024 * 1024
        self.host = host
        from .native import SmartRunAdapter
        self.native = SmartRunAdapter(host, self.kb, self.runs_root) if host is not None else None
        self.lock = threading.RLock()
        self.runs: dict[str, dict] = {}
        self.history_path = self.runs_root / 'smart-runs.json'
        if self.history_path.is_file():
            try:
                for row in json.loads(self.history_path.read_text(encoding='utf-8')):
                    if isinstance(row, dict) and _RUN_ID.fullmatch(str(row.get('run_id', ''))):
                        if row.get('status') in ('queued', 'running'):
                            row['status'] = 'interrupted'
                        self.runs[row['run_id']] = row
            except ValueError:
                pass

    # —— 任务 ——
    def _persist(self) -> None:
        rows = sorted(self.runs.values(), key=lambda r: r.get('started_at', 0), reverse=True)[:300]
        public = [{k: v for k, v in row.items() if not k.startswith('_')} for row in rows]
        temp = self.history_path.with_suffix('.tmp')
        temp.write_text(json.dumps(public, ensure_ascii=False, indent=1), encoding='utf-8')
        temp.replace(self.history_path)

    def llm_config(self) -> dict:
        try:
            from .llm_bind import config_from_app
            return config_from_app(self.config() or {}, self.secret)
        except Exception:
            return {'enabled': False}

    def settings(self) -> dict:
        current = self.config() or {}
        legacy = current.get('llm') or {}
        route = (current.get('agent', {}).get('routing') or {}).get('default') or {}
        provider_id = route.get('provider', '')
        provider = (current.get('providers') or {}).get(provider_id) or {}
        displayed = provider or legacy
        effective = self.llm_config()
        ref = provider.get('credential_ref') if provider else legacy.get('api_key_ref') or 'llm_api_key'
        try:
            has_key = bool(self.secret(ref))
        except Exception:
            has_key = False
        return {'llm': {'enabled': bool(displayed.get('enabled')), 'base_url': displayed.get('base_url', ''), 'model': route.get('model') if provider else legacy.get('model', ''),
                        'provider_id': provider_id, 'protocol': displayed.get('protocol') or 'openai-completions',
                        'material_access': provider.get('material_access', True),
                        'chat_path': displayed.get('chat_path') or '/chat/completions', 'has_key': has_key},
                'effective': {'configured': bool(effective.get('enabled')), 'model': effective.get('model', ''), 'source': effective.get('source', ''),
                              'provider_id': provider_id, 'protocol': displayed.get('protocol') or 'openai-completions',
                              'reason': effective.get('reason', '')},
                'routes': current.get('agent', {}).get('routing', {}),
                'writable': self.store is not None, 'persistent': bool(getattr(self.store, 'persistent', True))}

    def save_settings(self, payload: dict) -> dict:
        if self.store is None:
            raise PermissionError('当前服务未提供配置存储，请在高级界面设置模型')
        if not isinstance(payload, dict) or set(payload) - {'llm', 'api_key'}:
            raise ValueError('模型配置只接受 llm 与 api_key 字段')
        llm = payload.get('llm', {})
        if not isinstance(llm, dict):
            raise ValueError('llm 必须为配置对象')
        patch = {}
        for key in ('enabled', 'base_url', 'model', 'chat_path', 'protocol'):
            if key in llm:
                if key == 'enabled' and type(llm[key]) is not bool:
                    raise ValueError('enabled 必须为布尔值')
                if key != 'enabled' and not isinstance(llm[key], str):
                    raise ValueError(key + ' 必须为字符串')
                patch[key] = llm[key] if key == 'enabled' else llm[key].strip()
        if patch.get('base_url') and not re.match(r'https?://[^\s]+$', patch['base_url']):
            raise ValueError('接口地址须以 http:// 或 https:// 开头')
        if patch.get('chat_path') and not patch['chat_path'].startswith('/'):
            raise ValueError('对话路径须以 / 开头')
        from ..providers import PROTOCOLS, ProviderConfig, ModelConfig
        from dataclasses import asdict
        if patch.get('protocol') and patch['protocol'] not in PROTOCOLS:
            raise ValueError('不支持的供应商协议')
        current = self.config() or {}
        route = (current.get('agent', {}).get('routing') or {}).get('default') or {}
        provider_id = route.get('provider') or 'smart'
        provider = dict((current.get('providers') or {}).get(provider_id) or asdict(ProviderConfig()))
        default_ref = 'provider_' + re.sub(r'[^A-Za-z0-9_]', '_', provider_id)
        provider.setdefault('credential_ref', default_ref)
        if not provider['credential_ref']:
            provider['credential_ref'] = default_ref
        for field in ('enabled', 'base_url', 'chat_path', 'protocol'):
            if field in patch:
                provider[field] = patch[field]
        if 'protocol' in patch and 'chat_path' not in patch:
            provider['chat_path'] = {'openai-responses': '/responses', 'anthropic-messages': '/messages'}.get(patch['protocol'], '/chat/completions')
        model_id = patch.get('model') or route.get('model') or (current.get('llm') or {}).get('model') or ''
        if not model_id and patch.get('enabled'):
            raise ValueError('启用模型须填写登记模型名称')
        models = list(provider.get('models') or [])
        if model_id and not any(m.get('id') == model_id for m in models):
            models.append(asdict(ModelConfig(id=model_id)))
        provider['models'] = models
        key = payload.get('api_key')
        if key is not None and not isinstance(key, str):
            raise ValueError('api_key 必须为字符串')
        config_patch = {'providers': {provider_id: provider},
                        'agent': {'enabled': bool(provider.get('enabled')), 'runtime': 'harness',
                                  'routing': {'default': {'provider': provider_id, 'model': model_id}}}}
        # Validate the complete routed configuration before changing either
        # configuration or credential storage.
        from copy import deepcopy
        from ..config import AppConfig, validate_config
        from ..server import _deep_merge
        candidate = deepcopy(current)
        _deep_merge(candidate, config_patch)
        issues = validate_config(AppConfig.from_dict(candidate).ensure_defaults())
        if issues:
            raise ValueError('; '.join(issues))
        if isinstance(key, str) and key.strip():
            self.store.set_secret(provider['credential_ref'], key.strip())
        if patch:
            self.store.update(config_patch)
        return self.settings()

    def status(self) -> dict:
        renderer = 'fitz' if 'fitz' in available_backends() else ('pdftoppm' if shutil.which('pdftoppm') else '')
        return {'service': 'mingjing-smart', 'pipeline': PIPELINE_VERSION, 'pdf_backends': available_backends(), 'page_renderer': renderer,
                'kb': {'documents': len(self.kb.list()), 'root': str(self.kb.root)}, 'llm': self.settings()['effective'],
                'runs': len(self.runs)}

    def start(self, doc_ids: list[str], mode: str = 'auto', options: dict | None = None) -> dict:
        if self.native is None:
            raise ValueError('上传任务须由明镜原生宿主启动，请使用python -m mingjing.server')
        if self.host.sessions.closing:
            raise ValueError('服务正在退出，不能启动任务')
        if options is not None and not isinstance(options, dict):
            raise ValueError('options 必须为对象')
        options = dict(options or {})
        allowed_options = {'company', 'stock_code', 'report_date', 'kb_lookup', 'strict', 'use_llm', 'text_evidence', 'scope'}
        if set(options) - allowed_options:
            raise ValueError('上传任务只接受材料选择和登记策略参数')
        for field in ('kb_lookup', 'strict', 'use_llm', 'text_evidence'):
            if field in options and type(options[field]) is not bool:
                raise ValueError(field + ' 必须为布尔值')
        for field in ('company', 'stock_code', 'report_date'):
            if field in options and not isinstance(options[field], str):
                raise ValueError(field + ' 必须为字符串')
        from ..scope import validate_scope_declaration
        validate_scope_declaration(options.get('scope', ''))
        if options.get('report_date'):
            from datetime import date
            try:
                if date.fromisoformat(options['report_date']).isoformat() != options['report_date']:
                    raise ValueError()
            except ValueError:
                raise ValueError('report_date 必须为有效 YYYY-MM-DD 日期') from None
        if not isinstance(doc_ids, list) or any(not isinstance(d, str) or not _DOC_ID.fullmatch(d) for d in doc_ids):
            raise ValueError('doc_ids 必须为上传材料ID数组')
        doc_ids = [d for d in doc_ids if self.kb.record(d)]
        if not doc_ids:
            raise ValueError('请先上传或选择至少一份研报/财报')
        if mode not in ('auto', 'check', 'analysis'):
            raise ValueError('任务类型须为 auto / check / analysis')
        options['use_llm'] = options.get('use_llm', False)
        run_id = f'smart-{uuid.uuid4().hex[:12]}'
        frozen_config = json.loads(json.dumps(self.config(), ensure_ascii=False))
        snapshot_root = self.runs_root / run_id
        snapshot_root.mkdir(parents=True, exist_ok=True)
        config_path = snapshot_root / 'config_snapshot.json'
        config_path.write_text(json.dumps(frozen_config, ensure_ascii=False, indent=2), encoding='utf-8')
        from .docio import sha256_path
        names = [self.kb.record(d)['name'] for d in doc_ids]
        record = {'run_id': run_id, 'status': 'queued', 'mode': mode, 'docs': doc_ids, 'names': names, 'progress': 0, 'phase': '排队中',
                  'started_at': time.time(), 'options': dict(options), 'llm': options['use_llm'], 'children': [],
                  'config_revision': frozen_config.get('revision'), 'config_sha256': sha256_path(config_path),
                  '_config_snapshot': frozen_config, '_cancel': threading.Event()}
        with self.lock:
            self.runs[run_id] = record
            self._persist()
        thread = threading.Thread(target=self._work, args=(run_id, doc_ids, mode, options), name=f'mingjing-{run_id}', daemon=True)
        record['_thread'] = thread
        thread.start()
        return {k: v for k, v in record.items() if not k.startswith('_')}

    def _work(self, run_id: str, doc_ids: list[str], mode: str, options: dict) -> None:
        record = self.runs[run_id]

        def progress(phase: str, percent: int) -> None:
            if record['_cancel'].is_set():
                self.native.cancel(record)
                raise Cancelled('用户已取消')
            record.update(phase=phase, progress=max(0, min(100, int(percent))), status='running')
        record['status'] = 'running'
        try:
            batch = self.native.run_batch(doc_ids, mode, options, run_id, record, progress)
            partial = any(c.get('status') == 'partial' for c in batch['items'])
            record.update(status='partial' if partial else 'completed' if batch['items'] else 'failed', progress=100, phase='完成', batch=batch,
                          finished_at=time.time(), error='；'.join(f["error"] for f in batch['failures'])[:500] if batch['failures'] else '')
        except Cancelled as exc:
            record.update(status='cancelled', phase=str(exc), finished_at=time.time())
        except Exception as exc:
            record.update(status='failed', phase='失败', error=f'{type(exc).__name__}: {exc}', finished_at=time.time(),
                          trace=traceback.format_exc()[-1500:])
        with self.lock:
            self._persist()

    def public_run(self, run_id: str) -> dict:
        record = self.runs.get(run_id)
        if not record:
            raise KeyError(run_id)
        public = {k: v for k, v in record.items() if not k.startswith('_')}
        if self.native:
            native_rows = [task.summary() for c in record.get('children', []) if (task := self.host.get_task(c['native_run_id']))]
            public['native_runs'] = native_rows
            public['can_resume'] = any(r.get('can_resume') for r in native_rows)
        return public

    def cancel_all(self):
        with self.lock:
            for record in self.runs.values():
                if record.get('status') in {'queued', 'running'} and self.native:
                    self.native.cancel(record)

    def _resume_work(self, record):
        try:
            def progress(phase, percent):
                if record['_cancel'].is_set():
                    self.native.cancel(record)
                    raise Cancelled('用户取消了恢复')
                record.update(phase=phase, progress=percent)
            batch = self.native.refresh_record(record, progress)
            record.update(batch=batch, status='partial' if any(c.get('status') == 'partial' for c in batch['items']) else 'completed' if batch['items'] else 'failed',
                          phase='恢复完成', progress=100, finished_at=time.time())
        except Cancelled as exc:
            record.update(status='cancelled', phase=str(exc), finished_at=time.time())
        except Exception as exc:
            record.update(status='failed', phase='恢复失败', error=str(exc), finished_at=time.time())
        with self.lock:
            self._persist()

    # —— 路由 ——
    def handle(self, handler, method: str, path: str, query: str) -> bool:
        if not (path.startswith('/api/kb') or path.startswith('/api/smart') or path == '/smart' or path.startswith('/smart/')):
            return False
        try:
            self._route(handler, method, path, parse_qs(query or '', keep_blank_values=True))
        except KeyError as exc:
            _json(handler, 404, {'error': f'未找到：{exc}'})
        except (ValueError, PermissionError) as exc:
            _json(handler, 400, {'error': str(exc)})
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as exc:
            _json(handler, 500, {'error': f'{type(exc).__name__}: {exc}'})
        return True

    def static(self, handler, name: str) -> None:
        name = name or 'index.html'
        target = (STATIC / name).resolve()
        if STATIC.resolve() not in target.parents or not target.is_file():
            _json(handler, 404, {'error': 'not found'})
            return
        _send(handler, 200, target.read_bytes(), (mimetypes.guess_type(str(target))[0] or 'application/octet-stream') +
              ('; charset=utf-8' if target.suffix in ('.html', '.js', '.css') else ''),
              {'Content-Security-Policy': "default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'self'"})

    def _route(self, handler, method: str, path: str, query: dict) -> None:
        parts = [unquote(p) for p in path.strip('/').split('/')]
        if any(p in {'.', '..'} or '/' in p or '\\' in p for p in parts):
            raise ValueError('请求路径含无效材料或结果标识')
        if parts[0] == 'smart':
            return self.static(handler, '/'.join(parts[1:]))
        if parts[:2] == ['api', 'kb']:
            if len(parts) == 2 and method == 'GET':
                return _json(handler, 200, {'docs': self.kb.list(), 'stats': self.kb.stats()})
            if len(parts) == 4 and parts[2] == 'journal' and parts[3] == 'verify' and method == 'GET':
                return _json(handler, 200, self.kb.verify_journal())
            if len(parts) == 3 and parts[2] == 'journal' and method == 'GET':
                return _json(handler, 200, {'events': self.kb.journal(int((query.get('limit') or ['200'])[0]))})
            if len(parts) == 3 and parts[2] == 'upload' and method == 'POST':
                name = (query.get('name') or [''])[0].strip()
                if not name or '/' in name or '\\' in name or len(name) > 200:
                    raise ValueError('需要合法的文件名')
                hints = {key: (query.get(key) or [''])[0] for key in ('company', 'period', 'report_date', 'doc_type') if (query.get(key) or [''])[0]}
                record = self.kb.add_bytes(_read(handler, self.limit), name, origin='web', hints=hints)
                return _json(handler, 200, record)
            if len(parts) == 4 and parts[2] == 'evidence' and method == 'GET':
                return self._evidence(handler, parts[3])
            if len(parts) >= 3 and _DOC_ID.fullmatch(parts[2]):
                doc_id = parts[2]
                record = self.kb.record(doc_id)
                if record is None:
                    raise KeyError(doc_id)
                if len(parts) == 3 and method == 'PATCH':
                    payload = json.loads(_read(handler, 1 << 20) or b'{}')
                    return _json(handler, 200, self.kb.update(doc_id, payload if isinstance(payload, dict) else {}))
                if len(parts) == 3 and method == 'DELETE':
                    return _json(handler, 200, {'deleted': self.kb.remove(doc_id)})
                if len(parts) == 3 and method == 'GET':
                    return _json(handler, 200, record)
                if len(parts) == 4 and parts[3] == 'file' and method == 'GET':
                    raw = self.kb.validated_blob(doc_id).read_bytes()
                    from urllib.parse import quote
                    return _send(handler, 200, raw, mimetypes.guess_type(record['name'])[0] or 'application/octet-stream',
                                 {'Content-Disposition': "attachment; filename*=UTF-8''" + quote(record['name'])})
                if len(parts) == 5 and parts[3] == 'page' and method == 'GET':
                    found = re.fullmatch(r'(\d{1,4})\.png', parts[4])
                    if not found or record.get('ext') != '.pdf':
                        raise ValueError('仅支持 PDF 原文页图')
                    number = int(found[1])
                    if not 1 <= number <= int(record.get('pages') or 0):
                        raise ValueError('页码超出范围')
                    raw_box = (query.get('bbox') or [''])[0].split(',')
                    bbox = [float(v) for v in raw_box] if len(raw_box) == 4 and all(re.fullmatch(r'-?\d{1,5}(?:\.\d+)?', v) for v in raw_box) else None
                    raw = render_page(self.kb.validated_blob(doc_id), number, bbox, self.kb.root / 'parsed' / doc_id / 'pages')
                    self.kb.validated_blob(doc_id)
                    return _send(handler, 200, raw, 'image/png')
                if len(parts) == 4 and parts[3] == 'facts' and method == 'GET':
                    facts, diag = self.kb.facts(doc_id)
                    return _json(handler, 200, {'facts': [f.to_dict() for f in facts[:3000]], 'diagnostics': diag})
            raise KeyError(path)
        if parts[:2] == ['api', 'smart']:
            if len(parts) == 3 and parts[2] == 'status' and method == 'GET':
                return _json(handler, 200, self.status())
            if len(parts) == 3 and parts[2] == 'settings' and method == 'GET':
                return _json(handler, 200, self.settings())
            if len(parts) == 3 and parts[2] == 'settings' and method == 'POST':
                payload = json.loads(_read(handler, 1 << 16) or b'{}')
                return _json(handler, 200, self.save_settings(payload if isinstance(payload, dict) else {}))
            if len(parts) == 3 and parts[2] == 'llm' and method == 'GET':
                cfg = self.llm_config()
                return _json(handler, 200, {'configured': bool(cfg.get('enabled')), 'model': cfg.get('model', ''), 'source': cfg.get('source', '')})
            if len(parts) == 3 and parts[2] == 'demo' and method == 'POST':
                samples = next((c for c in (_BUNDLE / 'samples' / 'smart', _PROJECT / 'samples' / 'smart') if c.is_dir()), None)
                if samples is None:
                    raise KeyError('内置样例')
                ids = [self.kb.add_file(path, origin='demo')['doc_id'] for path in sorted(samples.glob('*.pdf'))]
                return _json(handler, 202, self.start(ids, 'auto', {'kb_lookup': False}))
            if len(parts) == 3 and parts[2] == 'run' and method == 'POST':
                payload = json.loads(_read(handler, 1 << 20) or b'{}')
                if not isinstance(payload, dict) or set(payload) - {'doc_ids', 'mode', 'options'}:
                    raise ValueError('上传核查只接受 doc_ids、mode 与 options')
                return _json(handler, 202, self.start(payload.get('doc_ids', []), payload.get('mode', 'auto'), payload.get('options', {})))
            if len(parts) == 3 and parts[2] == 'runs' and method == 'GET':
                rows = sorted((self.public_run(r) for r in list(self.runs)), key=lambda r: r.get('started_at', 0), reverse=True)
                return _json(handler, 200, {'runs': [{k: v for k, v in r.items() if k not in ('trace',)} for r in rows]})
            if len(parts) >= 4 and parts[2] == 'runs':
                run_id = parts[3]
                if not _RUN_ID.fullmatch(run_id):
                    raise ValueError('任务编号无效')
                if len(parts) == 4 and method == 'GET':
                    return _json(handler, 200, self.public_run(run_id))
                if len(parts) == 5 and parts[4] == 'cancel' and method == 'POST':
                    record = self.runs.get(run_id) or {}
                    if self.native:
                        self.native.cancel(record)
                    return _json(handler, 200, {'cancelled': True})
                if len(parts) == 5 and parts[4] == 'resume' and method == 'POST':
                    record = self.runs.get(run_id)
                    if record is None or self.native is None:
                        raise KeyError(run_id)
                    resumed = self.native.resume(record)
                    record.update(status='running', phase='原生子任务恢复中')
                    worker = threading.Thread(target=self._resume_work, args=(record,), daemon=True, name='mingjing-smart-resume')
                    record['_thread'] = worker
                    worker.start()
                    self._persist()
                    return _json(handler, 202, {'run_id': run_id, 'native_run_ids': resumed})
                if len(parts) == 7 and parts[4] == 'items' and parts[6] == 'materials' and method == 'POST':
                    record = self.runs.get(run_id)
                    if record is None or self.native is None:
                        raise KeyError(run_id)
                    payload = json.loads(_read(handler, 1 << 20) or b'{}')
                    if not isinstance(payload, dict) or set(payload) - {'doc_ids', 'scope_declaration'}:
                        raise ValueError('补证只接受上传材料ID与口径声明')
                    return _json(handler, 202, self.native.add_materials(record, parts[5], payload))
                root = (self.runs_root / run_id).resolve()
                if not root.is_dir() or self.runs_root.resolve() not in root.parents:
                    raise KeyError(run_id)
                if len(parts) == 5 and parts[4] == 'export' and method == 'GET':
                    buffer = io.BytesIO()
                    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
                        for file in sorted(root.rglob('*')):
                            if file.is_file():
                                archive.write(file, f'{run_id}/{file.relative_to(root).as_posix()}')
                        if self.host:
                            for child in self.runs[run_id].get('children', []):
                                task = self.host.get_task(child['native_run_id'])
                                if task:
                                    for file in sorted(task.out.rglob('*')):
                                        if file.is_file() and not file.is_symlink():
                                            archive.write(file, f'{run_id}/native/{task.id}/{file.relative_to(task.out).as_posix()}')
                    return _send(handler, 200, buffer.getvalue(), 'application/zip', {'Content-Disposition': f'attachment; filename="MingJing-{run_id}.zip"'})
                if len(parts) == 7 and parts[4] == 'items' and parts[6] == 'replay' and method == 'POST':
                    if not re.fullmatch(r'\d{2}-[\w\u4e00-\u9fff-]{1,40}', parts[5]):
                        raise ValueError('结果条目编号无效')
                    target = (root / parts[5]).resolve()
                    if root not in target.parents or not (target / 'results.json').is_file():
                        raise KeyError(parts[5])
                    from .pipeline import replay
                    return _json(handler, 200, replay(target, KnowledgeBase(self.kb.root)))
                if len(parts) == 7 and parts[4] == 'items' and method == 'GET':
                    item, name = parts[5], parts[6]
                    if not re.fullmatch(r'\d{2}-[\w\u4e00-\u9fff-]{1,40}', item) or name not in ('results.json', 'report.html', 'report.md', 'corrections.csv', 'audit.jsonl', 'run_manifest.json'):
                        raise ValueError('不支持的结果文件')
                    target = (root / item / name).resolve()
                    if root not in target.parents or not target.is_file():
                        raise KeyError(name)
                    kinds = {'.json': 'application/json; charset=utf-8', '.html': 'text/html; charset=utf-8', '.md': 'text/markdown; charset=utf-8',
                             '.csv': 'text/csv; charset=utf-8', '.jsonl': 'application/x-ndjson; charset=utf-8'}
                    extra = {'Content-Disposition': f'attachment; filename="{name}"'} if (query.get('download') or [''])[0] else {}
                    return _send(handler, 200, target.read_bytes(), kinds[target.suffix], extra)
            raise KeyError(path)
        raise KeyError(path)

    def _evidence(self, handler, identifier: str) -> None:
        identifier = identifier.removesuffix('.png')
        match = re.fullmatch(r'([0-9a-f]{12}):p(\d{1,4}):(?:(-?\d+),(-?\d+),(-?\d+),(-?\d+):[0-9a-f]{8}|text)', identifier)
        if not match:
            raise ValueError('证据编号格式无效')
        doc = next((r for r in self.kb.list() if r['doc_id'].startswith(match[1])), None)
        if doc is None or doc.get('ext') != '.pdf':
            raise KeyError('证据来源不在知识库中')
        bbox = [int(match[i]) for i in range(3, 7)] if match[3] is not None else None
        raw = render_page(self.kb.validated_blob(doc['doc_id']), int(match[2]), bbox if bbox and any(bbox) else None,
                          self.kb.root / 'parsed' / doc['doc_id'] / 'pages')
        self.kb.validated_blob(doc['doc_id'])
        _send(handler, 200, raw, 'image/png')


# —— 独立运行（轻量模式，不依赖原生 Agent 运行时） ——
def make_server(host: str = '127.0.0.1', port: int = 0, home: str | None = None):
    import os
    from ..server import AppState, MingJingServer
    previous = os.environ.get('MINGJING_HOME')
    try:
        if home:
            os.environ['MINGJING_HOME'] = str(Path(home).resolve())
        state = AppState()
    finally:
        if home:
            if previous is None:
                os.environ.pop('MINGJING_HOME', None)
            else:
                os.environ['MINGJING_HOME'] = previous
    server = MingJingServer((host, port), state)
    server.smart = state.smart
    return server


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog='mingjing-smart-web')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--home', default=None)
    args = parser.parse_args(argv)
    server = make_server(args.host, args.port, args.home)
    print(f'MingJing 直传模式：http://{args.host}:{server.server_port}/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
