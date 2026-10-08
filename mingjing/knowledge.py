"""Frozen external accounting materials, separate from issued financial rules.

Only the host selects a single root. Models receive opaque document/block IDs;
neither prose nor metadata in this store can create facts, formulas or verdicts.
Fresh scans parse original bytes. Frozen restores may reuse hash-locked caches.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import sys
import threading
import time
import uuid
import zipfile
from datetime import date
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from xml.etree import ElementTree

from .audit import digest, _module_code_data
from .config import validate_path
from .evidence import make_evidence_id, sha256_file

SCHEMA = 'mingjing-knowledge.v1'
METADATA_SCHEMA = 'mingjing-knowledge-metadata.v1'
EXTENSIONS = frozenset({'.pdf', '.docx', '.txt', '.md'})
IGNORED_DIRECTORIES = frozenset({'.git', '.hg', '.svn', 'node_modules', '__pycache__', '.venv', 'venv'})
RAW_STATUS = 'external_knowledge_raw_reference'
RAW_NOTICE = '外部会计材料原文，来源与有效性尚未独立核验；不属于签发规则、事实值或核查判定。'
ROLES = frozenset({'planner', 'verifier', 'coordinator', 'question', 'probe'})
_TRUSTED_IDENTIFIER_FIELDS = frozenset({'knowledge_id', 'block_id', 'document_sha256', 'span_sha256',
    'evidence_id', 'rules_version_hash', 'linked_rule_ids', 'matched_source_ids'})


class KnowledgeError(ValueError):
    pass


def definition_subject_matches(text, term):
    """An exact source subject, not an incidental/suffix occurrence of it."""
    if not isinstance(text, str) or not isinstance(term, str) or not term.strip():
        return False
    compact_term = re.sub(r'\s+', '', term).casefold()
    compact_text = re.sub(r'\s+', '', text).casefold()
    return bool(re.search(r'(?:^|[。！？；;：:])'+re.escape(compact_term)+
                          r'\s*[,，]?\s*(?:是指|指的是|定义为|系指)', compact_text))


def knowledge_query_terms(query, intent='search'):
    """Only explicit whitespace separates AND terms; no guessed Chinese NER."""
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        raise KnowledgeError('Knowledge query must be non-whitespace, at most 500 characters')
    if intent not in {'search', 'definition'}:
        raise KnowledgeError('Knowledge intent must be search or definition')
    parts = query.strip().split()
    definition = intent == 'definition' or any(part.casefold() in {'定义', 'definition'} for part in parts)
    terms = list(dict.fromkeys(part.casefold() for part in parts if part.casefold() not in {'定义', 'definition'}))
    if not terms or definition and len(terms) != 1:
        raise KnowledgeError('Definition search requires one exact subject term')
    return terms, definition


class _ReferenceXMLBuilder(ElementTree.TreeBuilder):
    def doctype(self, name, pubid, system):
        # A byte scan misses UTF-16 declarations. The XML parser invokes this
        # hook before entity expansion, independently of the declared encoding.
        raise KnowledgeError('DOCX XML entities are disabled')


def blind_knowledge_payload(value):
    """Mask prose and declared labels, retaining host-created lookup tokens."""
    if isinstance(value, str):
        from .coverage import mask_financial_numbers
        return mask_financial_numbers(value, preserve_bare_years=False)
    if isinstance(value, dict):
        return {key: copy.deepcopy(item) if key in _TRUSTED_IDENTIFIER_FIELDS else blind_knowledge_payload(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [blind_knowledge_payload(item) for item in value]
    return copy.deepcopy(value)


def _checkpoint(cancel_event=None, deadline_monotonic=None):
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError('Knowledge material loading cancelled')
    if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
        raise TimeoutError('Knowledge material time budget exhausted')


def _date(value, field):
    if value in ('', None):
        return None
    if not isinstance(value, str):
        raise KnowledgeError(f'{field} must be an explicit ISO date or unknown')
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except ValueError:
        raise KnowledgeError(f'{field} must be an explicit ISO date or unknown') from None
    return value


def _url(value):
    if not value:
        return None
    if not isinstance(value, str) or len(value) > 2048 or any(ord(ch) < 33 for ch in value):
        raise KnowledgeError('Knowledge source URL is invalid')
    parsed = urlsplit(value)
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
        raise KnowledgeError('Knowledge source URL must be an HTTP(S) reference without credentials')
    return value


def _atomic_json(path, payload):
    temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _metadata(raw=None, *, basis='unknown'):
    raw = raw or {}
    unknown = set(raw)-{'title', 'version', 'document_number', 'source_url', 'publish_date',
                       'effective_date', 'expires_date', 'declared_status'}
    if unknown:
        raise KnowledgeError('Unknown knowledge metadata fields: '+', '.join(sorted(unknown)))
    result = {key: raw.get(key) or None for key in ('title', 'version', 'document_number', 'declared_status')}
    if any(value is not None and (not isinstance(value, str) or len(value) > 1000) for value in result.values()):
        raise KnowledgeError('Knowledge metadata must contain bounded strings')
    result.update({key: _date(raw.get(key), key) for key in ('publish_date', 'effective_date', 'expires_date')})
    result['source_url'] = _url(raw.get('source_url'))
    result.update(metadata_basis=basis, authority_status='declared_unverified', authorizes_judgement=False)
    return result


def _header_metadata(text):
    """Read only an explicit metadata header; dates in prose have no authority."""
    lines = text.splitlines()
    values = {}
    mapping = {'版本': 'version', '文号': 'document_number', '发布': 'publish_date', '生效': 'effective_date',
               '失效': 'expires_date', '状态': 'declared_status', '来源': 'source_url'}
    if not lines or not re.fullmatch(r'#\s+\S.*', lines[0]):
        return _metadata()
    values['title'] = lines[0].lstrip('#').strip()
    for line in lines[1:16]:
        if line.startswith('##'):
            break
        match = re.fullmatch(r'-\s*(版本|文号|发布|生效|失效|状态|来源)[：:]\s*(.*)', line)
        if match:
            key = mapping[match[1]]
            if key in values and values[key] != match[2].strip():
                raise KnowledgeError('Conflicting explicit metadata headers')
            values[key] = match[2].strip()
        elif line.strip() and not line.startswith('#'):
            break
    issues = []
    for key in ('publish_date', 'effective_date', 'expires_date'):
        try:
            _date(values.get(key), key)
        except KnowledgeError:
            values[key] = None
            issues.append('invalid_or_conditional_declared_'+key)
    try:
        _url(values.get('source_url'))
    except KnowledgeError:
        values['source_url'] = None
        issues.append('invalid_declared_source_url')
    result = _metadata(values, basis='declared_document_header' if len(values) > 1 else 'title_only')
    result['metadata_issues'] = issues
    return result


def _docx_pages(path, control):
    with zipfile.ZipFile(path) as archive:
        matches = [entry for entry in archive.infolist() if entry.filename == 'word/document.xml']
        if len(matches) != 1 or matches[0].flag_bits & 1 or matches[0].file_size > 16*1024*1024:
            raise KnowledgeError('DOCX needs one bounded, unencrypted document.xml')
        _checkpoint(**control)
        raw = archive.read(matches[0])
    # No DTD/entity expansion or external fetch is permitted for local prose.
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise KnowledgeError('DOCX XML entities are disabled')
    root = ElementTree.fromstring(raw, parser=ElementTree.XMLParser(target=_ReferenceXMLBuilder()))
    ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
    paragraphs = []
    for paragraph in root.iter(ns+'p'):
        _checkpoint(**control)
        parts = []
        for item in paragraph.iter():
            if item.tag == ns+'t':
                parts.append(item.text or '')
            elif item.tag == ns+'tab':
                parts.append('\t')
            elif item.tag in {ns+'br', ns+'cr'}:
                parts.append('\n')
        text = ''.join(parts)
        if text.strip():
            paragraphs.append({'text': text, 'bbox': None, 'paragraph': len(paragraphs)+1})
    return [{'page': None, 'text': '\n'.join(row['text'] for row in paragraphs), 'blocks': paragraphs}]


def _parse(path, document_sha, control, block_chars):
    if path.suffix.lower() == '.docx':
        pages = _docx_pages(path, control)
    elif path.suffix.lower() in {'.txt', '.md'}:
        _checkpoint(**control)
        text = path.read_text(encoding='utf-8-sig', errors='strict')
        pages = [{'page': None, 'text': text, 'blocks': []}]
    else:
        from .parser import extract_text
        pages = extract_text(path, **control)
    blocks = []
    offset = 0
    for page in pages:
        _checkpoint(**control)
        source_blocks = page.get('blocks') or [{'text': match[0], 'bbox': None}
            for match in re.finditer(r'[^\n]+(?:\n(?!\s*\n)[^\n]+)*', page['text'])]
        cursor = 0
        for row in source_blocks:
            text = row['text']
            start = page['text'].find(text, cursor)
            if start < 0:
                start = page['text'].find(text)
            cursor = start+len(text) if start >= 0 else cursor
            for segment_start in range(0, len(text), block_chars):
                fragment = text[segment_start:segment_start+block_chars]
                if not fragment.strip():
                    continue
                fragment_sha = hashlib.sha256(fragment.encode('utf-8')).hexdigest()
                identity = 'kb-block-'+digest([document_sha, page['page'], len(blocks), fragment_sha])[:24]
                evidence_id = make_evidence_id(document_sha, page['page'], fragment, row.get('bbox'))
                if page['page'] is None:
                    evidence_id += ':b'+str(len(blocks))
                blocks.append({'block_id': identity, 'page': page['page'], 'bbox': row.get('bbox'),
                    'paragraph': row.get('paragraph'), 'text': fragment, 'span_sha256': fragment_sha,
                    'start': offset+start+segment_start if start >= 0 else None,
                    'end': offset+start+segment_start+len(fragment) if start >= 0 else None,
                    'position_basis': 'physical_pdf_page' if path.suffix.lower() == '.pdf' else 'logical_text',
                    'evidence_id': evidence_id})
        offset += len(page['text'])+1
    logical = '\n'.join(page['text'] for page in pages)
    return {'blocks': blocks, 'metadata': _header_metadata(logical),
            'parse_status': 'text_available' if blocks else 'no_text_layer',
            'text_sha256': hashlib.sha256(logical.encode('utf-8')).hexdigest()}


class KnowledgeRegistry:
    """A single host-selected primary root with duplicate paths kept as proofs."""
    def __init__(self, root, cache_root, *, allowed_roots=None, cancel_event=None, deadline_monotonic=None,
                 max_document_bytes=32*1024*1024, max_documents=4096, max_total_bytes=512*1024*1024,
                 max_blocks=100000, block_chars=3000):
        self.root = validate_path(root, allowed_roots=allowed_roots, kind='dir', must_exist=True, follow_symlinks=False)
        self.cache_root = validate_path(cache_root, allowed_roots=allowed_roots, kind='dir', follow_symlinks=False)
        if self.cache_root == self.root or self.cache_root.is_relative_to(self.root):
            raise KnowledgeError('Knowledge cache must be outside its immutable primary root')
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.control = {'cancel_event': cancel_event, 'deadline_monotonic': deadline_monotonic}
        self.limits = dict(max_document_bytes=max_document_bytes, max_documents=max_documents,
                           max_total_bytes=max_total_bytes, max_blocks=max_blocks, block_chars=block_chars)
        if any(type(value) is not int or value <= 0 for value in self.limits.values()) or block_chars > 3000:
            raise KnowledgeError('Knowledge resource limits must be positive bounded integers')
        self.loader_hash = digest(_module_code_data(sys.modules[__name__]))
        self.documents, self.resources, self.blocks = {}, [], {}
        self.resource_lock = ''

    def scan(self):
        """Fresh scans never trust a self-authenticated cache as original prose."""
        _checkpoint(**self.control)
        self.documents, self.resources, self.blocks, self.resource_lock = {}, [], {}, ''
        paths, total_bytes = [], 0
        for current, directories, names in os.walk(self.root, followlinks=False):
            _checkpoint(**self.control)
            parent = Path(current)
            directories[:] = sorted(name for name in directories if name not in IGNORED_DIRECTORIES)
            for name in directories:
                validate_path(parent/name, allowed_roots=[str(self.root)], kind='dir', follow_symlinks=False)
            for name in sorted(names):
                if Path(name).suffix.lower() not in EXTENSIONS:
                    continue
                path = validate_path(parent/name, allowed_roots=[str(self.root)], kind='file', must_exist=True, follow_symlinks=False)
                size = path.stat().st_size
                total_bytes += size
                if size > self.limits['max_document_bytes'] or total_bytes > self.limits['max_total_bytes']:
                    raise KnowledgeError('Knowledge material byte budget exhausted')
                paths.append(path)
                if len(paths) > self.limits['max_documents']:
                    raise KnowledgeError('Knowledge document budget exhausted')
        metadata, metadata_resource = self._indexed_metadata()
        if metadata_resource:
            self.resources.append(metadata_resource)
        sha_groups = {}
        for path in paths:
            _checkpoint(**self.control)
            sha = sha256_file(path)
            sha_groups.setdefault(sha, []).append(path)
            self.resources.append({'id': 'kb-input-'+digest(str(path.relative_to(self.root)))[:20],
                                   'path': str(path), 'sha256': sha})
        for sha, aliases in sorted(sha_groups.items()):
            _checkpoint(**self.control)
            source = min(aliases, key=lambda path: (len(path.relative_to(self.root).parts), path.as_posix().casefold()))
            try:
                parsed = _parse(source, sha, self.control, self.limits['block_chars'])
            except (InterruptedError, TimeoutError):
                raise
            except (OSError, ValueError, ElementTree.ParseError, zipfile.BadZipFile) as exc:
                parsed = {'blocks': [], 'metadata': _metadata(), 'parse_status': 'parse_failed',
                          'parse_error': type(exc).__name__, 'text_sha256': None}
            if sha256_file(source) != sha:
                raise KnowledgeError('Knowledge source changed while parsing')
            declarations = [metadata[str(path)] for path in aliases if str(path) in metadata]
            if declarations and any(row != declarations[0] for row in declarations[1:]):
                raise KnowledgeError('Duplicate knowledge bytes have conflicting indexed metadata')
            if declarations:
                indexed = declarations[0]
                header = parsed['metadata']
                for key in ('publish_date', 'effective_date', 'expires_date', 'source_url', 'version'):
                    if header.get(key) and indexed.get(key) and header[key] != indexed[key]:
                        raise KnowledgeError('Knowledge index conflicts with explicit document metadata')
                parsed['metadata'] = indexed
            identity = 'kb-'+sha[:24]
            parsed.update(knowledge_id=identity, document_sha256=sha, loader_hash=self.loader_hash)
            if len(self.blocks)+len(parsed['blocks']) > self.limits['max_blocks']:
                raise KnowledgeError('Knowledge block budget exhausted')
            cache = self.cache_root/(sha+'-'+self.loader_hash[:16]+'.json')
            validate_path(cache, allowed_roots=[str(self.cache_root)], kind='file', follow_symlinks=False)
            _atomic_json(cache, parsed)
            record = {'knowledge_id': identity, 'document_sha256': sha, 'name': source.name,
                'aliases': [path.relative_to(self.root).as_posix() for path in sorted(aliases)],
                'source_paths': [str(path) for path in sorted(aliases)], 'metadata': parsed['metadata'],
                'parse_status': parsed['parse_status'], 'block_count': len(parsed['blocks']),
                'cache_path': str(cache), 'cache_sha256': sha256_file(cache)}
            self.documents[identity] = record
            for block in parsed['blocks']:
                self.blocks[block['block_id']] = dict(block, knowledge_id=identity)
        self.resource_lock = digest(self._payload())
        return self

    def _indexed_metadata(self):
        path = self.root/'knowledge_index.json'
        if not path.exists():
            return {}, None
        path = validate_path(path, allowed_roots=[str(self.root)], kind='file', must_exist=True, follow_symlinks=False)
        if path.stat().st_size > 4*1024*1024:
            raise KnowledgeError('Knowledge metadata index is too large')
        raw = json.loads(path.read_text(encoding='utf-8'))
        if set(raw) != {'schema_version', 'documents'} or raw['schema_version'] != METADATA_SCHEMA or not isinstance(raw['documents'], list):
            raise KnowledgeError('Unsupported knowledge metadata index')
        if len(raw['documents']) > self.limits['max_documents']:
            raise KnowledgeError('Knowledge metadata document budget exhausted')
        records = {}
        for row in raw['documents']:
            if not isinstance(row, dict) or not {'file', 'sha256'} <= row.keys():
                raise KnowledgeError('Knowledge metadata must lock its source file hash')
            relative = PurePosixPath(row['file']) if isinstance(row['file'], str) else None
            if (relative is None or relative.is_absolute() or row['file'] != relative.as_posix()
                    or any(part in {'.', '..'} or ':' in part or '\\' in part for part in relative.parts)):
                raise KnowledgeError('Knowledge index accepts primary-root relative files only')
            source = validate_path(self.root/relative, allowed_roots=[str(self.root)], kind='file', must_exist=True, follow_symlinks=False)
            if source.suffix.lower() not in EXTENSIONS or sha256_file(source) != row['sha256']:
                raise KnowledgeError('Knowledge indexed source format or SHA-256 is invalid')
            value = _metadata({key: val for key, val in row.items() if key not in {'file', 'sha256'}}, basis='host_hash_locked_index')
            if str(source) in records and records[str(source)] != value:
                raise KnowledgeError('Conflicting indexed knowledge metadata')
            records[str(source)] = value
        return records, {'id': 'kb-metadata-index', 'path': str(path), 'sha256': sha256_file(path)}

    def _payload(self):
        return {'schema_version': SCHEMA, 'root': str(self.root), 'cache_root': str(self.cache_root), 'loader_hash': self.loader_hash,
                'documents': [self.documents[key] for key in sorted(self.documents)], 'resources': self.resources}

    def snapshot(self):
        if not self.resource_lock:
            raise KnowledgeError('Knowledge registry was not frozen')
        return copy.deepcopy(dict(self._payload(), knowledge_lock=self.resource_lock))

    @classmethod
    def restore(cls, snapshot, *, cache_root, allowed_roots=None, **control):
        if not isinstance(snapshot, dict) or snapshot.get('schema_version') != SCHEMA:
            raise KnowledgeError('Unsupported frozen knowledge registry')
        payload = {key: value for key, value in snapshot.items() if key != 'knowledge_lock'}
        if snapshot.get('knowledge_lock') != digest(payload):
            raise KnowledgeError('Knowledge resource lock changed')
        validate_path(cache_root, kind='dir', must_exist=True, follow_symlinks=False)
        registry = cls(snapshot['root'], cache_root, allowed_roots=allowed_roots, **control)
        if str(registry.cache_root) != snapshot['cache_root']:
            raise KnowledgeError('Knowledge frozen cache root changed')
        if registry.loader_hash != snapshot['loader_hash']:
            raise KnowledgeError('Knowledge loader implementation changed')
        registry.resources = copy.deepcopy(snapshot['resources'])
        resource_paths = {}
        for resource in registry.resources:
            path = validate_path(resource['path'], allowed_roots=[str(registry.root)], kind='file', must_exist=True, follow_symlinks=False)
            if str(path) in resource_paths:
                raise KnowledgeError('Duplicate frozen knowledge input path')
            resource_paths[str(path)] = resource['sha256']
        for row in snapshot['documents']:
            identity = row['knowledge_id']
            if identity in registry.documents or identity != 'kb-'+row['document_sha256'][:24]:
                raise KnowledgeError('Knowledge document identity conflicts')
            if not row['source_paths'] or len(row['source_paths']) != len(row['aliases']):
                raise KnowledgeError('Knowledge source aliases are incomplete')
            for path, alias in zip(row['source_paths'], row['aliases']):
                source = validate_path(path, allowed_roots=[str(registry.root)], kind='file', must_exist=True, follow_symlinks=False)
                if (source.relative_to(registry.root).as_posix() != alias
                        or resource_paths.get(str(source)) != row['document_sha256']):
                    raise KnowledgeError('Knowledge document and source input registry disagree')
            cache = validate_path(row['cache_path'], allowed_roots=[str(registry.cache_root)], kind='file', must_exist=True, follow_symlinks=False)
            if sha256_file(cache) != row['cache_sha256']:
                raise KnowledgeError('Knowledge parsed cache changed')
            parsed = json.loads(cache.read_text(encoding='utf-8'))
            if (parsed.get('document_sha256') != row['document_sha256'] or parsed.get('knowledge_id') != identity
                    or parsed.get('loader_hash') != registry.loader_hash or parsed['metadata'] != row['metadata']
                    or len(parsed['blocks']) != row['block_count']):
                raise KnowledgeError('Knowledge parsed cache differs from frozen document')
            registry.documents[identity] = copy.deepcopy(row)
            for block in parsed['blocks']:
                if block['block_id'] in registry.blocks:
                    raise KnowledgeError('Knowledge block identity conflicts')
                registry.blocks[block['block_id']] = dict(block, knowledge_id=identity)
        registry.resource_lock = snapshot['knowledge_lock']
        registry.validate_locked()
        return registry

    def validate_locked(self, document_ids=None):
        _checkpoint(**self.control)
        validate_path(self.root, kind='dir', must_exist=True, follow_symlinks=False)
        selected = set(self.documents if document_ids is None else document_ids)
        if not selected <= self.documents.keys():
            raise KnowledgeError('Knowledge document outside frozen resource scope')
        sources = {path for identity in selected for path in self.documents[identity]['source_paths']}
        for row in self.resources:
            if row['path'] not in sources and row['id'] != 'kb-metadata-index':
                continue
            path = validate_path(row['path'], allowed_roots=[str(self.root)], kind='file', must_exist=True, follow_symlinks=False)
            if sha256_file(path) != row['sha256']:
                raise KnowledgeError('Knowledge original source or metadata index changed')
        for identity in selected:
            row = self.documents[identity]
            path = validate_path(row['cache_path'], allowed_roots=[str(self.cache_root)], kind='file', must_exist=True, follow_symlinks=False)
            if sha256_file(path) != row['cache_sha256']:
                raise KnowledgeError('Knowledge parsed cache changed')

    @staticmethod
    def public_document(row, as_of_date=None):
        metadata = copy.deepcopy(row['metadata'])
        cutoff = _date(as_of_date, 'as_of_date')
        published, effective, expires = (metadata.get(key) for key in ('publish_date', 'effective_date', 'expires_date'))
        reason = ('publication_unknown' if not published else 'not_published_as_of' if cutoff and published > cutoff
                  else 'text_layer_missing' if row['parse_status'] != 'text_available' else None)
        effective_status = ('unknown' if not effective else 'not_yet_effective' if cutoff and effective > cutoff
                            else 'declared_expired' if cutoff and expires and expires <= cutoff else 'declared_dates_only')
        return {key: copy.deepcopy(row[key]) for key in ('knowledge_id', 'document_sha256', 'name', 'parse_status', 'block_count')} | {
            'aliases': row['aliases'][:10], 'duplicate_path_count': len(row['aliases'])-1,
            'metadata': metadata, 'reference_available': reason is None, 'unavailable_reason': reason,
            'effective_status': effective_status, 'reference_status': RAW_STATUS, 'notice': RAW_NOTICE}

    def read(self, block_id, *, as_of_date):
        if not _date(as_of_date, 'as_of_date'):
            raise KnowledgeError('Knowledge references require a known disclosure cutoff')
        if block_id not in self.blocks:
            raise KnowledgeError('Knowledge block outside frozen resource scope')
        block = self.blocks[block_id]
        self.validate_locked([block['knowledge_id']])
        document = self.public_document(self.documents[block['knowledge_id']], as_of_date)
        if not document['reference_available']:
            return {'available': False, 'knowledge_id': document['knowledge_id'], 'reason': document['unavailable_reason'], 'notice': RAW_NOTICE}
        return {'available': True, **copy.deepcopy(block), 'document_sha256': document['document_sha256'],
                'metadata': document['metadata'], 'effective_status': document['effective_status'],
                'reference_status': RAW_STATUS, 'authorizes_judgement': False, 'notice': RAW_NOTICE}

    def search(self, query, *, as_of_date, document_ids=None, cursor=0, limit=5, intent='search'):
        if not _date(as_of_date, 'as_of_date'):
            raise KnowledgeError('Knowledge references require a known disclosure cutoff')
        terms, definition = knowledge_query_terms(query, intent)
        _pagination(cursor, limit, 20)
        selected = set(self.documents if document_ids is None else document_ids)
        self.validate_locked(selected)
        matches, unavailable = [], []
        for identity in sorted(selected):
            document = self.public_document(self.documents[identity], as_of_date)
            if not document['reference_available']:
                unavailable.append({'knowledge_id': identity, 'reason': document['unavailable_reason']})
                continue
            title_text = re.sub(r'\s+', '', document['name']).casefold()
            for block in self.blocks.values():
                if block['knowledge_id'] != identity:
                    continue
                normalized_text = re.sub(r'\s+', '', block['text']).casefold()
                if (all(term in normalized_text or term in title_text for term in terms)
                        and (not definition or definition_subject_matches(block['text'], terms[0]))):
                    matches.append(block['block_id'])
        def rank(block_id):
            block = self.blocks[block_id]
            text = block['text'].strip()
            title = self.documents[block['knowledge_id']]['name']
            headings = all(line.startswith('#') or not line.strip() for line in text.splitlines())
            return (not any(definition_subject_matches(text, term) for term in terms),
                    not all(term in re.sub(r'\s+', '', title).casefold() for term in terms), headings,
                    -sum(re.sub(r'\s+', '', text).casefold().count(term) for term in terms), -min(len(text), 1000), block_id)
        matches.sort(key=rank)
        hits = [self.read(block, as_of_date=as_of_date) for block in matches[cursor:cursor+limit]]
        output = {'hits': hits, 'total_matches': len(matches), 'unavailable_documents': unavailable[:20],
                  'unavailable_document_count': len(unavailable), 'next_cursor': None,
                  'reference_status': RAW_STATUS, 'notice': RAW_NOTICE,
                  'query_terms': terms, 'definition_request': definition,
                  'definition_terms': terms if definition else [],
                  'no_match_reason': 'no_definition_match' if definition and not matches else None}
        while hits and len(json.dumps(output, ensure_ascii=False).encode('utf-8')) > 16384:
            hits.pop()
        if cursor+len(hits) < len(matches):
            output['next_cursor'] = cursor+len(hits)
        return output

    def rule_links(self, document_id, *, source_register, rule_cards):
        """Only trusted callers supply the issued rulepack; external prose cannot."""
        if document_id not in self.documents:
            raise KnowledgeError('Knowledge document outside frozen resource scope')
        row = self.documents[document_id]
        source_ids = {source['id'] for source in source_register.get('sources', [])
                      if source.get('sha256') == row['document_sha256']}
        return {'knowledge_id': document_id, 'rules_version_hash': source_register.get('version_hash'),
                'linked_rule_ids': sorted(card['id'] for card in rule_cards
                    if any(reference.get('source_id') in source_ids for reference in card.get('basis_refs', []))),
                'matched_source_ids': sorted(source_ids), 'link_basis': 'exact_issued_source_sha256',
                'external_material_authorizes_judgement': False}


def knowledge_summary(registry, as_of_date):
    if registry is None:
        return {'enabled': False, 'documents': 0, 'blocks': 0, 'knowledge_lock': None,
                'authority_status': 'external_materials_not_loaded'}
    documents = [registry.public_document(row, as_of_date) for row in registry.documents.values()]
    return {'enabled': True, 'documents': len(documents), 'blocks': len(registry.blocks),
            'input_paths': len(registry.resources), 'available_documents': sum(row['reference_available'] for row in documents),
            'unknown_publication': sum(row['unavailable_reason'] == 'publication_unknown' for row in documents),
            'future_publication': sum(row['unavailable_reason'] == 'not_published_as_of' for row in documents),
            'unreadable_documents': sum(row['parse_status'] != 'text_available' for row in documents),
            'knowledge_lock': registry.resource_lock, 'authority_status': 'raw_references_only',
            'authorizes_judgement': False, 'notice': RAW_NOTICE}


def knowledge_resources(registry, lock_path):
    resources = copy.deepcopy(registry.resources)
    for row in registry.documents.values():
        resources.append({'id': 'kb-cache-'+row['document_sha256'][:24], 'path': row['cache_path'], 'sha256': row['cache_sha256']})
    resources.append({'id': 'knowledge-lock', 'path': str(Path(lock_path).resolve()), 'sha256': sha256_file(lock_path)})
    return resources


def restore_run_knowledge(run_path, payload, *, resources=None, allowed_roots=None, **control):
    """Only the parent's frozen input manifest may nominate a knowledge lock."""
    if not (payload.get('knowledge') or {}).get('enabled'):
        return None
    root = Path(run_path).resolve()
    lock_path = validate_path(root/'knowledge_lock.json', allowed_roots=[str(root)], kind='file', must_exist=True, follow_symlinks=False)
    if resources is None:
        manifest = json.loads((root/'run_manifest.json').read_text(encoding='utf-8'))
        resources = manifest.get('inputs', [])
    matched = [row for row in resources if Path(row['path']).resolve() == lock_path]
    if len(matched) != 1 or matched[0]['sha256'] != sha256_file(lock_path):
        raise KnowledgeError('Knowledge lock is not registered in the frozen task input manifest')
    snapshot = json.loads(lock_path.read_text(encoding='utf-8'))
    if (payload['knowledge'].get('knowledge_lock') != snapshot['knowledge_lock']
            or str(Path(payload.get('knowledge_root') or '').resolve()) != snapshot['root']):
        raise KnowledgeError('Knowledge material coordinates disagree with the frozen task')
    return KnowledgeRegistry.restore(snapshot, cache_root=snapshot['cache_root'], allowed_roots=allowed_roots, **control)


def _pagination(cursor, limit, maximum):
    if type(cursor) is not int or cursor < 0 or type(limit) is not int or not 1 <= limit <= maximum:
        raise KnowledgeError('Knowledge pagination outside bounded integer range')


class KnowledgeTools:
    """Standalone read-only boundary, ready for the shared host dispatcher."""
    def __init__(self, registry, *, run_id, resource_lock, as_of_date, audit=None,
                 cancel_event=None, deadline_monotonic=None, max_tool_calls=1000):
        self.registry = registry
        self.run_id, self.resource_lock = run_id, resource_lock
        self.as_of_date = _date(as_of_date, 'as_of_date')
        if not self.as_of_date or not run_id or not resource_lock or type(max_tool_calls) is not int or max_tool_calls < 1:
            raise KnowledgeError('Knowledge tools require a trusted task, cutoff and positive budget')
        if deadline_monotonic is not None and (type(deadline_monotonic) not in (int, float) or not math.isfinite(deadline_monotonic)):
            raise KnowledgeError('Knowledge deadline must be finite')
        self.audit, self.control, self.max_tool_calls = audit, {'cancel_event': cancel_event, 'deadline_monotonic': deadline_monotonic}, max_tool_calls
        self.sessions, self.calls, self.trace = {}, set(), []
        self.lock = threading.RLock()

    def register_session(self, session_id, role, document_ids=None):
        if role not in ROLES or not isinstance(session_id, str) or not session_id or session_id in self.sessions:
            raise KnowledgeError('Invalid knowledge role or duplicate trusted session')
        selected = set(self.registry.documents if document_ids is None else document_ids)
        if not selected <= self.registry.documents.keys():
            raise KnowledgeError('Knowledge session resources outside frozen scope')
        self.sessions[session_id] = {'role': role, 'active': True, 'documents': selected}

    def close_session(self, session_id):
        if session_id in self.sessions:
            self.sessions[session_id]['active'] = False

    def dispatch(self, envelope):
        with self.lock:
            _checkpoint(**self.control)
            if not isinstance(envelope, dict):
                raise KnowledgeError('Knowledge trusted envelope must be an object')
            identity = self.sessions.get(envelope.get('session_id'))
            if not identity or not identity['active']:
                raise KnowledgeError('Unknown or closed knowledge session')
            if (envelope.get('role') != identity['role'] or envelope.get('run_id') != self.run_id
                    or envelope.get('resource_lock') != self.resource_lock
                    or envelope.get('knowledge_lock') != self.registry.resource_lock):
                raise KnowledgeError('Knowledge trusted identity or resource lock mismatch')
            name, params = envelope.get('tool_name'), envelope.get('params', {})
            allowed = {'mj_kb_list': {'cursor', 'limit'}, 'mj_kb_search': {'query', 'document_id', 'cursor', 'limit', 'intent'},
                       'mj_kb_read': {'block_id'}}
            if name not in allowed or not isinstance(params, dict) or set(params)-allowed[name]:
                raise KnowledgeError('Unknown knowledge tool or forbidden model parameters')
            call_id = envelope.get('call_id')
            key = envelope.get('session_id'), call_id
            if not isinstance(call_id, str) or not call_id or key in self.calls or len(self.calls) >= self.max_tool_calls:
                raise KnowledgeError('Knowledge call missing, repeated or budget exhausted')
            self.calls.add(key)
            documents = identity['documents']
            if name == 'mj_kb_list':
                cursor, limit = params.get('cursor', 0), params.get('limit', 20)
                _pagination(cursor, limit, 20)
                self.registry.validate_locked(documents)
                ids = sorted(documents)
                output = {'documents': [self.registry.public_document(self.registry.documents[doc], self.as_of_date)
                                       for doc in ids[cursor:cursor+limit]], 'total_documents': len(ids),
                          'next_cursor': cursor+limit if cursor+limit < len(ids) else None}
            elif name == 'mj_kb_search':
                if 'query' not in params:
                    raise KnowledgeError('Knowledge search needs a query')
                selected = documents
                if params.get('document_id'):
                    if not isinstance(params['document_id'], str):
                        raise KnowledgeError('Knowledge document identifier must be a string')
                    if params['document_id'] not in documents:
                        raise KnowledgeError('Knowledge document outside session scope')
                    selected = {params['document_id']}
                output = self.registry.search(params['query'], as_of_date=self.as_of_date, document_ids=selected,
                                              cursor=params.get('cursor', 0), limit=params.get('limit', 5), intent=params.get('intent', 'search'))
            else:
                if not isinstance(params.get('block_id'), str):
                    raise KnowledgeError('Knowledge read needs an opaque block identifier')
                block = self.registry.blocks.get(params.get('block_id'))
                if not block or block['knowledge_id'] not in documents:
                    raise KnowledgeError('Knowledge block outside session scope')
                output = self.registry.read(block['block_id'], as_of_date=self.as_of_date)
            if identity['role'] in {'planner', 'verifier'}:
                output = blind_knowledge_payload(output)
            _checkpoint(**self.control)
            event = {'session_id': envelope['session_id'], 'role': identity['role'], 'run_id': self.run_id,
                     'call_id': call_id, 'tool': name, 'input_hash': digest(params), 'output_hash': digest(output),
                     'knowledge_lock': self.registry.resource_lock, 'resource_lock': self.resource_lock, 'status': 'success'}
            self.trace.append(event)
            if self.audit:
                self.audit.emit('knowledge_tool_call', **event)
            return output
