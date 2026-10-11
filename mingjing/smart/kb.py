"""本地知识库：内容寻址存储 + 解析/事实缓存 + 留痕日志。

- 去重：按 SHA-256 判定同一文件；重复上传只累加计数并留痕，不重复解析。
- 缓存：版面解析结果、抽取事实、模型应答分别按（内容哈希, 解析器/抽取器版本）缓存，命中即复用。
- 留痕：journal.jsonl 为哈希链，记录入库、去重命中、解析、被哪次任务引用。
- 同一公司同一报告期出现不同哈希的文件时标记为“同报告多版本”，由事实层按披露日期与版本择优。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
from pathlib import Path

from . import EXTRACTOR_VERSION, PARSER_VERSION
from .detect import DocMeta, detect, same_company
from .docio import SUPPORTED, Document, dump_document, load_document, sha256_bytes, sha256_path

_LOCK = threading.RLock()
TYPE_LABELS = {'research_report': '研究报告', 'annual_report': '年度报告', 'semiannual_report': '半年度报告', 'quarterly_report': '季度报告',
               'prospectus': '招股说明书', 'facts_json': '结构化事实', 'other': '其他材料', 'unknown': '未识别'}


def code_fingerprint(*modules: str) -> str:
    """解析/抽取代码指纹：代码一改，相关缓存自动失效（打包环境回退到字节码或版本号）。"""
    import importlib
    digest = hashlib.sha256()
    for name in modules:
        module = importlib.import_module(f'{__package__}.{name}')
        source = Path(getattr(module, '__file__', '') or '')
        candidates = [source.with_suffix('.py'), source]
        raw = next((path.read_bytes() for path in candidates if path.is_file()), b'')
        digest.update(name.encode() + b':' + (raw or (PARSER_VERSION + EXTRACTOR_VERSION).encode()))
    return digest.hexdigest()[:16]


PARSE_CODE = ('common', 'docio')
FACT_CODE = ('common', 'docio', 'taxonomy', 'tables', 'detect')


def default_root() -> Path:
    home = os.environ.get('MINGJING_HOME')
    if not home:
        local = os.environ.get('LOCALAPPDATA')
        home = str(Path(local) / 'MingJing') if local else str(Path.home() / '.mingjing')
    return Path(home) / 'kb'


class KnowledgeBase:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root else default_root()
        for name in ('blobs', 'parsed', 'cache'):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / 'index.json'
        self.journal_path = self.root / 'journal.jsonl'
        self.journal_head_path = self.root / 'journal-head.json'
        self.session = {'parse_hits': 0, 'parse_misses': 0, 'fact_hits': 0, 'fact_misses': 0, 'cache_hits': 0, 'cache_misses': 0, 'dedupe_hits': 0}
        self.cache_dependencies: dict[str, Path] = {}

    # —— 索引与留痕 ——
    def _load(self) -> dict:
        if self.index_path.is_file():
            try:
                return json.loads(self.index_path.read_text(encoding='utf-8'))
            except ValueError:
                pass
        return {'version': 1, 'docs': {}}

    def _save(self, index: dict) -> None:
        temp = self.index_path.with_suffix('.tmp')
        temp.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding='utf-8')
        os.replace(temp, self.index_path)

    def _journal(self, event: str, doc_id: str = '', **detail) -> None:
        with _LOCK:
            self._journal_locked(event, doc_id, **detail)

    def _journal_locked(self, event: str, doc_id: str = '', **detail) -> None:
        checked = self.verify_journal()
        if not checked['ok']:
            raise ValueError('知识库留痕完整性校验失败，拒绝追加掩盖损坏')
        previous = ''
        if self.journal_path.is_file():
            with self.journal_path.open('rb') as stream:
                try:
                    stream.seek(-4096, os.SEEK_END)
                except OSError:
                    stream.seek(0)
                lines = stream.read().decode('utf-8', errors='ignore').strip().splitlines()
                if lines:
                    try:
                        previous = json.loads(lines[-1]).get('hash', '')
                    except ValueError:
                        previous = ''
        row = {'time': time.strftime('%Y-%m-%dT%H:%M:%S'), 'event': event, 'doc_id': doc_id, **detail, 'prev': previous}
        row['hash'] = hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
        with self.journal_path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        self._atomic_json(self.journal_head_path, {'head': row['hash'], 'entries': checked['entries'] + 1,
                                                 'legacy_prefix': checked.get('legacy_prefix', not checked.get('anchored', False) and checked['entries'] > 0)})

    @staticmethod
    def _atomic_json(path: Path, data) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + '.tmp-' + str(threading.get_ident()))
        with temp.open('w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)

    def verify_journal(self) -> dict:
        """逐条重算留痕日志的哈希链：任何一条被改动、删除或插入都会在该处断链。"""
        if not self.journal_path.is_file() and self.journal_head_path.is_file():
            return {'ok': False, 'entries': 0, 'broken_at': 1, 'detail': '留痕日志缺失，检查点仍存在'}
        if not self.journal_path.is_file():
            return {'ok': True, 'entries': 0, 'broken_at': None, 'detail': '暂无留痕记录'}
        previous, count = '', 0
        for number, line in enumerate(self.journal_path.read_text(encoding='utf-8').splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                claimed = row.pop('hash')
            except (ValueError, KeyError, AttributeError):
                return {'ok': False, 'entries': count, 'broken_at': number, 'detail': f'第{number}行不是有效的留痕记录'}
            actual = hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
            if row.get('prev', '') != previous or actual != claimed:
                return {'ok': False, 'entries': count, 'broken_at': number,
                        'detail': f'第{number}条记录' + ('与前一条的哈希不衔接' if row.get('prev', '') != previous else '内容与其哈希不符（被改动）')}
            previous, count = claimed, count + 1
        anchor = None
        if self.journal_head_path.is_file():
            try:
                anchor = json.loads(self.journal_head_path.read_text(encoding='utf-8'))
            except (ValueError, OSError):
                return {'ok': False, 'entries': count, 'broken_at': count + 1, 'detail': '留痕检查点不可读'}
            if anchor.get('head') != previous or anchor.get('entries') != count:
                return {'ok': False, 'entries': count, 'broken_at': count + 1, 'detail': '留痕头或条数与检查点不符（可能被截尾）'}
        return {'ok': True, 'entries': count, 'broken_at': None, 'detail': f'共{count}条，哈希链完整',
                'head': previous[:16], 'anchored': anchor is not None, 'legacy_prefix': bool((anchor or {}).get('legacy_prefix'))}

    def journal(self, limit: int = 200) -> list[dict]:
        if not self.journal_path.is_file():
            return []
        lines = self.journal_path.read_text(encoding='utf-8').splitlines()[-limit:]
        return [json.loads(line) for line in lines if line.strip()]

    # —— 入库 ——
    def blob_path(self, doc_id: str, ext: str) -> Path:
        if not isinstance(doc_id, str) or not re.fullmatch(r'[a-f0-9]{64}', doc_id) or ext not in SUPPORTED:
            raise ValueError('无效的材料资源标识或类型')
        target = self.root / 'blobs' / doc_id[:2] / f'{doc_id}{ext}'
        from ..config import validate_path
        return validate_path(target, allowed_roots=[str(self.root)], must_exist=False, kind='file', follow_symlinks=False)

    def validated_blob(self, doc_id: str) -> Path:
        record = self.record(doc_id)
        if record is None:
            raise KeyError(doc_id)
        blob = self.blob_path(doc_id, record['ext'])
        if not blob.is_file() or blob.stat().st_size != record['size'] or sha256_path(blob) != doc_id:
            raise ValueError('材料原件与登记的大小或 SHA-256 不符')
        return blob

    def add_file(self, path: str | Path, *, name: str | None = None, origin: str = 'upload', hints: dict | None = None) -> dict:
        path = Path(path)
        return self._add(path, name or path.name, sha256_path(path), origin, hints or {})

    def add_bytes(self, raw: bytes, name: str, *, origin: str = 'upload', hints: dict | None = None) -> dict:
        digest = sha256_bytes(raw)
        ext = Path(name).suffix.lower()
        if ext not in SUPPORTED:
            raise ValueError(f'不支持的文件类型：{ext or name}（支持 PDF / DOCX / MD / TXT / JSON）')
        target = self.blob_path(digest, ext)
        if not target.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            temp = target.with_suffix(target.suffix + '.part')
            temp.write_bytes(raw)
            os.replace(temp, target)
        return self._add(target, name, digest, origin, hints or {}, stored=True)

    def _add(self, path: Path, name: str, digest: str, origin: str, hints: dict, stored: bool = False) -> dict:
        ext = Path(name).suffix.lower() or path.suffix.lower()
        if ext not in SUPPORTED:
            raise ValueError(f'不支持的文件类型：{ext or name}（支持 PDF / DOCX / MD / TXT / JSON）')
        with _LOCK:
            index = self._load()
            record = index['docs'].get(digest)
            if record:
                record['uploads'] = record.get('uploads', 1) + 1
                if name not in record.setdefault('names', [record['name']]):
                    record['names'].append(name)
                if hints:
                    record.setdefault('user_meta', {}).update({k: v for k, v in hints.items() if v})
                    self._apply_user_meta(record)
                self._save(index)
                self.session['dedupe_hits'] += 1
                self._journal('dedupe_hit', digest, name=name, origin=origin)
                return {**record, 'duplicate': True}
            target = self.blob_path(digest, ext)
            if not stored and not target.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
            record = {'doc_id': digest, 'name': name, 'names': [name], 'ext': ext, 'size': target.stat().st_size, 'origin': origin,
                      'added_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'uploads': 1, 'hits': 0, 'last_used_at': '', 'status': 'ready',
                      'user_meta': {k: v for k, v in hints.items() if v}, 'tags': []}
            index['docs'][digest] = record
            self._save(index)
            self._journal('added', digest, name=name, origin=origin, size=record['size'])
        try:
            document = self.document(digest)
            meta = detect(document, hints=record['user_meta'])
            patch = {**meta.to_dict(), 'pages': len(document.pages), 'chars': document.char_count, 'backend': document.backend,
                     'parser_version': PARSER_VERSION, 'type_label': TYPE_LABELS.get(meta.doc_type, meta.doc_type)}
            if not document.pages or (document.kind == 'pdf' and document.char_count < 50):
                patch.update(status='no_text', error='未提取到文本层（可能是扫描件），不会产生事实')
        except Exception as exc:                      # 解析失败也保留入库记录，便于用户看到原因
            patch = {'status': 'failed', 'error': f'{type(exc).__name__}: {exc}', 'doc_type': 'unknown', 'type_label': '解析失败'}
        with _LOCK:
            index = self._load()
            index['docs'][digest].update(patch)
            record = index['docs'][digest]
            twins = [other['doc_id'] for other in index['docs'].values() if other['doc_id'] != digest and other.get('doc_type') == record.get('doc_type')
                     and record.get('doc_type') not in ('research_report', 'other', 'unknown') and other.get('period') and other.get('period') == record.get('period')
                     and same_company(other, record)]
            if twins:
                record['same_report_versions'] = twins
                self._journal('version_sibling', digest, siblings=twins)
            self._save(index)
        return {**record, 'duplicate': False}

    @staticmethod
    def _apply_user_meta(record: dict) -> None:
        for key in ('company', 'stock_code', 'period', 'report_date', 'doc_type'):
            if record.get('user_meta', {}).get(key):
                record[key] = record['user_meta'][key]
        record['type_label'] = TYPE_LABELS.get(record.get('doc_type', ''), record.get('doc_type', ''))

    def update(self, doc_id: str, patch: dict) -> dict:
        with _LOCK:
            index = self._load()
            record = index['docs'][doc_id]
            allowed = {k: v for k, v in patch.items() if k in ('company', 'stock_code', 'period', 'report_date', 'doc_type', 'tags')}
            if 'tags' in allowed:
                record['tags'] = list(allowed.pop('tags') or [])
            record.setdefault('user_meta', {}).update(allowed)
            self._apply_user_meta(record)
            self._save(index)
            self._journal('meta_updated', doc_id, patch=allowed)
            facts_cache = self.root / 'parsed' / doc_id / 'facts.json'
            if facts_cache.is_file() and allowed:
                facts_cache.unlink()               # 公司/期间变更后事实需重抽
            return record

    def remove(self, doc_id: str) -> bool:
        with _LOCK:
            index = self._load()
            existing = index['docs'].get(doc_id)
            if existing and (existing.get('run_references') or existing.get('hits', 0)):
                raise PermissionError('材料被历史任务引用；请保留原件以便补证和重放')
            record = index['docs'].pop(doc_id, None)
            if not record:
                return False
            self._save(index)
            shutil.rmtree(self.root / 'parsed' / doc_id, ignore_errors=True)
            blob = self.blob_path(doc_id, record.get('ext', ''))
            if blob.is_file():
                blob.unlink()
            self._journal('removed', doc_id, name=record.get('name'))
            return True

    def record(self, doc_id: str) -> dict | None:
        return self._load()['docs'].get(doc_id)

    def list(self) -> list[dict]:
        return sorted(self._load()['docs'].values(), key=lambda r: r.get('added_at', ''), reverse=True)

    def meta(self, doc_id: str) -> DocMeta:
        record = self.record(doc_id) or {}
        return DocMeta(**{k: record.get(k, getattr(DocMeta(), k)) for k in DocMeta().to_dict()})

    # —— 缓存化的解析与事实 ——
    def document(self, doc_id: str) -> Document:
        record = self.record(doc_id)
        if record is None:
            raise KeyError(doc_id)
        blob = self.validated_blob(doc_id)
        cache = self.root / 'parsed' / doc_id / 'document.json'
        if cache.is_file():
            try:
                data = json.loads(cache.read_text(encoding='utf-8'))
                if data.get('sha256') == doc_id and data.get('parser_version') == PARSER_VERSION and data.get('code') == code_fingerprint(*PARSE_CODE):
                    self.session['parse_hits'] += 1
                    document = Document.from_json(data)
                    document.name = record['name']
                    return document
            except ValueError:
                pass
        self.session['parse_misses'] += 1
        document = load_document(blob, sha256=doc_id)
        document.name = record['name']
        dump_document(document, cache, code=code_fingerprint(*PARSE_CODE))
        self._journal('parsed', doc_id, backend=document.backend, pages=len(document.pages))
        return document

    def facts(self, doc_id: str) -> tuple[list, dict]:
        from ..facts import fact_from_dict
        from .tables import extract_filing_facts, facts_from_json
        record = self.record(doc_id) or {}
        self.validated_blob(doc_id)
        cache = self.root / 'parsed' / doc_id / 'facts.json'
        signature = [EXTRACTOR_VERSION, code_fingerprint(*FACT_CODE), record.get('company', ''), record.get('period', ''), record.get('report_date', '')]
        if cache.is_file():
            try:
                data = json.loads(cache.read_text(encoding='utf-8'))
                if data.get('signature') == signature:
                    self.session['fact_hits'] += 1
                    return [fact_from_dict(item) for item in data['facts']], data.get('diag', {})
            except (ValueError, KeyError):
                pass
        self.session['fact_misses'] += 1
        if record.get('doc_type') == 'facts_json':
            facts, diag = facts_from_json(self.blob_path(doc_id, record['ext']), record.get('company', '')), {'source': 'structured'}
        else:
            facts, diag = extract_filing_facts(self.document(doc_id), self.meta(doc_id))
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({'signature': signature, 'facts': [fact.to_dict() for fact in facts], 'diag': diag}, ensure_ascii=False), encoding='utf-8')
        with _LOCK:
            index = self._load()
            if doc_id in index['docs']:
                index['docs'][doc_id].update(facts=len(facts), accounts=diag.get('accounts', len({f.account for f in facts})),
                                             fact_periods=diag.get('periods', sorted({f.period for f in facts})), extractor_version=EXTRACTOR_VERSION)
                self._save(index)
        self._journal('facts_extracted', doc_id, facts=len(facts))
        return facts, diag

    def find_materials(self, meta: DocMeta | dict, exclude: set | None = None) -> list[dict]:
        exclude = exclude or set()
        rows = [r for r in self.list() if r['doc_id'] not in exclude and r.get('status') == 'ready'
                and r.get('doc_type') in ('annual_report', 'semiannual_report', 'quarterly_report', 'prospectus', 'facts_json')
                and same_company(r, meta)]
        return sorted(rows, key=lambda r: (r.get('period', ''), r.get('report_date', '')))

    def touch(self, doc_ids: list[str], run_id: str, purpose: str) -> None:
        with _LOCK:
            index = self._load()
            for doc_id in doc_ids:
                if doc_id in index['docs']:
                    index['docs'][doc_id]['hits'] = index['docs'][doc_id].get('hits', 0) + 1
                    index['docs'][doc_id]['last_used_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
                    refs = index['docs'][doc_id].setdefault('run_references', [])
                    if run_id not in refs:
                        refs.append(run_id)
            self._save(index)
        self._journal('used', '', run_id=run_id, purpose=purpose, docs=[d[:12] for d in doc_ids])

    # —— 通用 JSON 缓存（模型应答等），按键哈希寻址 ——
    def cache_get(self, namespace: str, key: str):
        if not re.fullmatch(r'[a-z0-9-]+', namespace):
            raise ValueError('无效的缓存命名空间')
        path = self.root / 'cache' / namespace / f'{hashlib.sha256(key.encode("utf-8")).hexdigest()}.json'
        if path.is_file():
            self.cache_dependencies[str(path.relative_to(self.root / 'cache'))] = path
            self.session['cache_hits'] += 1
            entry = json.loads(path.read_text(encoding='utf-8'))
            if entry.get('_schema') != 'mingjing-cache.v2' or entry.get('request_hash') != hashlib.sha256(key.encode('utf-8')).hexdigest():
                return None
            raw = json.dumps(entry['value'], ensure_ascii=False, sort_keys=True).encode('utf-8')
            if entry.get('value_hash') != hashlib.sha256(raw).hexdigest():
                raise ValueError('缓存应答完整性校验失败')
            return entry['value']
        self.session['cache_misses'] += 1
        return None

    def cache_put(self, namespace: str, key: str, value) -> None:
        if not re.fullmatch(r'[a-z0-9-]+', namespace):
            raise ValueError('无效的缓存命名空间')
        path = self.root / 'cache' / namespace / f'{hashlib.sha256(key.encode("utf-8")).hexdigest()}.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        self._atomic_json(path, {'_schema': 'mingjing-cache.v2', 'request_hash': hashlib.sha256(key.encode('utf-8')).hexdigest(),
                                'value_hash': hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest(), 'value': value})
        self.cache_dependencies[str(path.relative_to(self.root / 'cache'))] = path

    def stats(self) -> dict:
        docs = self.list()
        by_type: dict[str, int] = {}
        companies: dict[str, dict] = {}
        for record in docs:
            by_type[record.get('type_label', '未识别')] = by_type.get(record.get('type_label', '未识别'), 0) + 1
            name = record.get('company') or '未识别公司'
            item = companies.setdefault(name, {'company': name, 'stock_code': record.get('stock_code', ''), 'docs': 0, 'periods': set(), 'facts': 0})
            item['docs'] += 1
            item['facts'] += record.get('facts', 0) or 0
            if record.get('period'):
                item['periods'].add(record['period'])
        cache_files = sum(1 for _ in (self.root / 'cache').rglob('*.json'))
        return {'documents': len(docs), 'bytes': sum(r.get('size', 0) for r in docs), 'by_type': by_type,
                'companies': [{**c, 'periods': sorted(c['periods'])} for c in sorted(companies.values(), key=lambda c: -c['docs'])],
                'duplicate_uploads_saved': sum(max(r.get('uploads', 1) - 1, 0) for r in docs), 'reuse_hits': sum(r.get('hits', 0) for r in docs),
                'cache_entries': cache_files, 'session': dict(self.session), 'root': str(self.root)}
