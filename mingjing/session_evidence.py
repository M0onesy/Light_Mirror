"""Message-owned source quotations, separate from frozen numeric verdicts."""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

from .audit import digest
from .config import validate_path
from .evidence import make_evidence_id, sha256_file

REFERENCE_STATUS = 'unverified_source_quotation'
_MESSAGE_ID = re.compile(r'message-\d{4,8}\Z')
_SHA256 = re.compile(r'[a-f0-9]{64}\Z')


class SessionEvidenceError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _policy_roots(config):
    security = config.get('security', {})
    roots = None if security.get('allow_external_paths', True) else list(security.get('allowed_roots') or [])
    if roots == []:
        raise SessionEvidenceError('当前安全设置没有授权材料目录', 403)
    return roots


def _context(state):
    path = validate_path(Path(state['run_path']) / 'results.json', allowed_roots=[state['run_path']],
                         must_exist=True, kind='file', follow_symlinks=False)
    frozen = next((row for row in state.get('locks', {}).get('manifest', {}).get('outputs', [])
                   if row.get('path') == 'results.json'), None)
    if frozen is None or sha256_file(path) != frozen.get('sha256'):
        raise SessionEvidenceError('会话原结果缺少冻结哈希或已被修改', 409)
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('run_id', state['run_id']) != state['run_id']:
        raise SessionEvidenceError('会话与原运行不匹配', 403)
    company = data.get('company') or (data.get('analysis') or {}).get('company')
    if not company:
        companies = {fact.get('company') for fact in data.get('facts', []) if fact.get('company')}
        if len(companies) == 1:
            company = next(iter(companies))
    if not company:
        raise SessionEvidenceError('会话没有可信公司主体', 403)
    return data, company


def _page_blocks(source: Path, page_number: int):
    if source.suffix.lower() == '.pdf':
        import fitz
        with fitz.open(source) as document:
            if page_number < 1 or page_number > len(document):
                raise SessionEvidenceError('引用页码不在锁定PDF内')
            page = document[page_number - 1]
            text = page.get_text()
            blocks = [{'text': row[4], 'bbox': list(row[:4])} for row in page.get_text('blocks')]
            rect = list(page.rect)
    elif source.suffix.lower() in {'.md', '.txt'} and page_number == 1:
        text = source.read_text(encoding='utf-8-sig', errors='replace')
        blocks = [{'text': line, 'bbox': None} for line in text.splitlines() if line.strip()]
        rect = None
    else:
        raise SessionEvidenceError('引用材料不属于可读原文类型')
    if not blocks:
        blocks = [{'text': line, 'bbox': None} for line in text.splitlines() if line.strip()]
    page_id = 'page-' + digest([source.name, page_number, text])[:20]
    return [{'id': 'block-' + digest([page_id, index, block['text']])[:20], **block}
            for index, block in enumerate(blocks)], rect


def _verified_reference(state, reference, config):
    if not isinstance(reference, dict):
        raise SessionEvidenceError('引用登记必须是结构化记录')
    if reference.get('reference_status') != REFERENCE_STATUS:
        raise SessionEvidenceError('原文引用不能冒充数字核查结论')
    data, company = _context(state)
    if reference.get('company') != company:
        raise SessionEvidenceError('引用越过会话公司范围', 403)
    if reference.get('material_kind') == 'accounting_knowledge':
        return _verified_knowledge_reference(state, reference, config, data, company)
    resource_id = reference.get('report_id')
    if not isinstance(resource_id, str) or (resource_id != 'draft' and not resource_id.startswith('report-')):
        raise SessionEvidenceError('引用不是登记报告资源', 404)
    matches = [row for row in state.get('locks', {}).get('resources', []) if row.get('id') == resource_id]
    if len(matches) != 1:
        raise SessionEvidenceError('报告不属于本会话资源锁', 404)
    locked = matches[0]
    doc_sha = reference.get('doc_sha256')
    if not isinstance(doc_sha, str) or not _SHA256.fullmatch(doc_sha) or doc_sha != locked.get('sha256'):
        raise SessionEvidenceError('引用文档哈希与原资源锁不符', 409)
    source = validate_path(locked['path'], allowed_roots=_policy_roots(config), must_exist=True,
                           kind='file', follow_symlinks=False)
    if sha256_file(source) != doc_sha:
        raise SessionEvidenceError('原引用材料已修改，SHA-256不匹配', 409)
    known_companies = {fact.get('company') for fact in data.get('facts', [])
                       if fact.get('source_path') and Path(fact['source_path']).resolve() == source and fact.get('company')}
    if known_companies and known_companies != {company}:
        raise SessionEvidenceError('来源材料不属于本会话公司', 403)
    page = reference.get('page')
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        raise SessionEvidenceError('引用缺少合法原页码')
    quote = reference.get('quote_text')
    if not isinstance(quote, str) or not quote.strip() or len(quote) > 12000:
        raise SessionEvidenceError('引用原文为空或超过登记长度')
    if reference.get('quote_sha256') != _hash_text(quote):
        raise SessionEvidenceError('引用文本哈希不匹配', 409)
    blocks, _ = _page_blocks(source, page)
    block = next((row for row in blocks if resource_id + ':' + row['id'] == reference.get('block_id') or
                  (resource_id == 'draft' and row['id'] == reference.get('block_id'))), None)
    if block is None or _hash_text(block['text']) != reference.get('block_sha256'):
        raise SessionEvidenceError('引用块不在原文页内或块哈希不符', 409)
    if ''.join(quote.split()) not in ''.join(block['text'].split()):
        raise SessionEvidenceError('引用文本不属于锁定原文块', 409)
    bbox = reference.get('bbox')
    if bbox is not None:
        if (not isinstance(bbox, (list, tuple)) or len(bbox) != 4 or
            any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in bbox)):
            raise SessionEvidenceError('引用坐标格式非法')
        if block['bbox'] is None or any(abs(a - b) > 1 for a, b in zip(bbox, block['bbox'])):
            raise SessionEvidenceError('引用坐标不是原文块坐标', 409)
    evidence_id = make_evidence_id(doc_sha, page, quote, bbox)
    if reference.get('evidence_id') != evidence_id:
        raise SessionEvidenceError('证据ID与原文引用不一致', 409)
    return {key: reference[key] for key in ('evidence_id', 'slot_id', 'report_id', 'doc_sha256', 'page',
            'block_id', 'block_sha256', 'quote_sha256') if key in reference} | {
            'company': company, 'bbox': list(bbox) if bbox is not None else None,
            'quote_text': quote, 'reference_status': REFERENCE_STATUS, 'name': source.name,
            'title': f'{source.name} · 第{page}页 · 原文引用，未作数字核查',
            'source_path': str(source), 'image_available': source.suffix.lower() == '.pdf'}


def _verified_knowledge_reference(state, reference, config, data, company):
    from .knowledge import restore_run_knowledge, RAW_NOTICE
    roots = _policy_roots(config)
    try:
        validate_path(data.get('knowledge_root') or '', allowed_roots=roots, kind='dir', follow_symlinks=False)
    except ValueError:
        raise SessionEvidenceError('当前安全设置未授权会计知识材料目录', 403) from None
    try:
        registry = restore_run_knowledge(state['run_path'], data, resources=state.get('locks', {}).get('resources', []),
                                         allowed_roots=roots)
        raw = registry.read(reference.get('block_id'), as_of_date=data.get('report_date')) if registry else None
    except (ValueError, OSError):
        raise SessionEvidenceError('冻结会计知识库材料、解析缓存或引用定位已变化', 409) from None
    if not registry or reference.get('knowledge_lock') != registry.resource_lock:
        raise SessionEvidenceError('会计知识引用不属于冻结知识库资源锁', 409)
    if not raw.get('available'):
        raise SessionEvidenceError('会计材料不属于截止日期可引用原文', 403)
    for key, expected in {'knowledge_id': raw['knowledge_id'], 'report_id': raw['knowledge_id'],
                          'doc_sha256': raw['document_sha256'], 'evidence_id': raw['evidence_id'],
                          'quote_sha256': raw['span_sha256'], 'block_sha256': raw['span_sha256'],
                          'quote_text': raw['text'], 'page': raw['page'], 'bbox': raw['bbox']}.items():
        if reference.get(key) != expected:
            raise SessionEvidenceError('会计知识引用与原文块、位置或哈希不符', 409)
    document = registry.documents[raw['knowledge_id']]
    try:
        source = validate_path(document['source_paths'][0], allowed_roots=roots, kind='file', must_exist=True, follow_symlinks=False)
    except (ValueError, OSError):
        raise SessionEvidenceError('冻结会计知识原文路径已变化', 409) from None
    return {key: reference[key] for key in ('evidence_id', 'slot_id', 'report_id', 'doc_sha256', 'page',
                                          'block_id', 'block_sha256', 'quote_sha256', 'knowledge_id', 'knowledge_lock') if key in reference} | {
        'company': company, 'bbox': raw['bbox'], 'quote_text': raw['text'], 'reference_status': REFERENCE_STATUS,
        'material_kind': 'accounting_knowledge', 'name': document['name'],
        'title': document['name']+' · 原文引用，准则声明未核验', 'notice': RAW_NOTICE,
        'metadata': raw['metadata'], 'effective_status': raw['effective_status'], 'authorizes_judgement': False,
        'source_path': str(source), 'image_available': source.suffix.lower() == '.pdf' and raw['page'] is not None}


def _atomic_bytes(path: Path, raw: bytes):
    temporary = path.with_suffix(path.suffix + '.tmp')
    validate_path(path, allowed_roots=[str(path.parent)], follow_symlinks=False)
    validate_path(temporary, allowed_roots=[str(path.parent)], follow_symlinks=False)
    temporary.write_bytes(raw)
    temporary.replace(path)


def persist_source_references(state: dict, message_dir: Path, references: list[dict]) -> dict:
    """Called only by the trusted host after guarded report submission."""
    if not isinstance(references, list) or len(references) > 32:
        raise SessionEvidenceError('每条消息最多登记32条原文引用')
    message_dir = Path(message_dir)
    if not _MESSAGE_ID.fullmatch(message_dir.name) or message_dir.parent.name != state['session_id']:
        raise SessionEvidenceError('引用输出目录不属于本消息', 403)
    validate_path(message_dir, allowed_roots=[str(message_dir.parent.parent)], follow_symlinks=False)
    message_dir.mkdir(parents=True, exist_ok=True)
    validate_path(message_dir, allowed_roots=[str(message_dir.parent.parent)], kind='dir', follow_symlinks=False)
    records = []
    seen = set()
    for reference in references:
        record = _verified_reference(state, reference, state.get('config', {}))
        if record['evidence_id'] in seen:
            continue
        seen.add(record['evidence_id'])
        if record['image_available']:
            from .server import _evidence_png
            raw = _evidence_png({'source_path': record['source_path'], 'document_sha12': record['doc_sha256'][:12],
                                 'page': record['page'], 'bbox': record['bbox'] or [0, 0, 0, 0]}, state.get('config', {}))
            if sha256_file(record['source_path']) != record['doc_sha256']:
                raise SessionEvidenceError('引用图像生成期间材料发生变化', 409)
            image = hashlib.sha256(record['evidence_id'].encode('utf-8')).hexdigest()[:20] + '.png'
            _atomic_bytes(message_dir / image, raw)
            record.update(image=image, image_sha256=hashlib.sha256(raw).hexdigest())
        records.append({key: value for key, value in record.items() if key != 'source_path'})
    manifest = {'schema_version': 'session-source-evidence.v1', 'session_id': state['session_id'],
                'run_id': state['run_id'], 'message_id': message_dir.name, 'resource_lock': state['resource_lock'],
                'references': records}
    raw = json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8')
    _atomic_bytes(message_dir / 'source_references.json', raw)
    public = [{key: value for key, value in row.items() if key not in {'image', 'image_sha256', 'quote_text'}} for row in records]
    return {'references': public, 'manifest_sha256': hashlib.sha256(raw).hexdigest(), 'message_id': message_dir.name}


def load_source_evidence(sessions_root: Path, state: dict, message_id: str, evidence_id: str, config: dict) -> dict:
    if not isinstance(message_id, str) or not _MESSAGE_ID.fullmatch(message_id):
        raise SessionEvidenceError('消息ID非法')
    message = next((row for row in state.get('messages', [])
                    if row.get('role') == 'assistant' and row.get('message_id') == message_id), None)
    if message is None or not any(row.get('evidence_id') == evidence_id for row in message.get('source_references', [])):
        raise SessionEvidenceError('证据不属于该会话消息', 404)
    root = validate_path(Path(sessions_root) / state['session_id'] / message_id,
                         allowed_roots=[str(sessions_root)], must_exist=True, kind='dir', follow_symlinks=False)
    manifest_path = validate_path(root / 'source_references.json', allowed_roots=[str(root)],
                                 must_exist=True, kind='file', follow_symlinks=False)
    if sha256_file(manifest_path) != message.get('source_reference_manifest_sha256'):
        raise SessionEvidenceError('会话证据登记文件已修改', 409)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if (manifest.get('session_id') != state['session_id'] or manifest.get('run_id') != state['run_id'] or
        manifest.get('message_id') != message_id or manifest.get('resource_lock') != state['resource_lock']):
        raise SessionEvidenceError('证据登记不属于本会话资源锁', 403)
    references = manifest.get('references', [])
    matching = [row for row in references if row.get('evidence_id') == evidence_id]
    if len(matching) != 1:
        raise SessionEvidenceError('证据未唯一登记', 404)
    registered = matching[0]
    info = _verified_reference(state, registered, config)
    if info['image_available']:
        filename = hashlib.sha256(evidence_id.encode('utf-8')).hexdigest()[:20] + '.png'
        image = validate_path(root / filename, allowed_roots=[str(root)], must_exist=True,
                              kind='file', follow_symlinks=False)
        if registered.get('image') != filename or sha256_file(image) != registered.get('image_sha256'):
            raise SessionEvidenceError('会话证据图像已修改', 409)
        info['_image_path'] = str(image)
        info['_image_sha256'] = registered['image_sha256']
    return dict(info, session_id=state['session_id'], message_id=message_id, run_id=state['run_id'])
