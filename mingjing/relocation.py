"""Explicit read-only locations for immutable records moved within a workspace.

The map never changes a saved lock or resource identity. Callers still verify
the bytes against the original lock after resolving their physical location.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

SCHEMA = 'mingjing.workspace-relocation.v1'


class RelocationError(ValueError):
    pass


def _io(path):
    text = str(path)
    return Path('\\\\?\\'+text) if os.name == 'nt' and len(text) >= 240 and not text.startswith('\\\\?\\') else path


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RelocationError('Duplicate relocation JSON field: '+key)
        result[key] = value
    return result


def _path(value, *, absolute=True):
    if not isinstance(value, str) or not value or '\x00' in value:
        raise RelocationError('Relocation paths must be nonempty strings')
    if value.startswith(('\\\\', '//')):
        raise RelocationError('UNC and device paths are not relocation resources')
    path = Path(value)
    if '..' in path.parts or path.is_absolute() != absolute:
        raise RelocationError('Relocation path must be canonical and '+('absolute' if absolute else 'relative'))
    return path


def _unlinked(path):
    for component in (path, *path.parents):
        checked = _io(component)
        if checked.is_symlink() or (hasattr(checked, 'is_junction') and checked.is_junction()):
            raise RelocationError('Relocation links and junctions are forbidden: '+str(component))


def _sha(path):
    digest = hashlib.sha256()
    with _io(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _same_directory(left, right):
    _unlinked(left); _unlinked(right)
    first = {path.name: path for path in left.iterdir()}
    second = {path.name: path for path in right.iterdir()}
    if first.keys() != second.keys():
        return False
    for name, path in first.items():
        other = second[name]
        _unlinked(path); _unlinked(other)
        if path.is_file() and other.is_file():
            if path.stat().st_size != other.stat().st_size or _sha(path) != _sha(other):
                return False
        elif path.is_dir() and other.is_dir():
            if not _same_directory(path, other):
                return False
        else:
            return False
    return True


class RelocationMap:
    def __init__(self, payload, *, source=None):
        if not isinstance(payload, dict) or payload.get('schema') != SCHEMA:
            raise RelocationError('Unsupported workspace relocation schema')
        self.workspace = _path(payload.get('workspace_abs'))
        _unlinked(self.workspace)
        if not self.workspace.is_dir() or self.workspace == Path(self.workspace.anchor):
            raise RelocationError('Relocation workspace must be an existing non-root directory')
        self.workspace = self.workspace.resolve(strict=True)
        rows = payload.get('entries')
        if not isinstance(rows, list) or not rows:
            raise RelocationError('Relocation entries must be a nonempty array')
        self.entries = []
        originals, targets = set(), set()
        for row in rows:
            if not isinstance(row, dict) or row.get('kind') not in {'file', 'directory'}:
                raise RelocationError('Relocation entry needs file or directory kind')
            old, relative = _path(row.get('old')), _path(row.get('new'), absolute=False)
            if not old.is_relative_to(self.workspace):
                raise RelocationError('Original relocation resource is outside the workspace')
            if not relative.parts or relative.parts[0] not in {'Light_Mirror', '存档'}:
                raise RelocationError('Relocation targets must stay in Light_Mirror or 存档')
            new = self.workspace / relative
            _unlinked(new)
            if not _io(new).exists() or (_io(new).is_file() if row['kind'] == 'directory' else not _io(new).is_file()):
                raise RelocationError('Relocation target is missing or has the wrong kind: '+str(new))
            if new.resolve(strict=True) != new:
                raise RelocationError('Relocation target is not canonical')
            if old in originals or new in targets:
                raise RelocationError('Ambiguous duplicate relocation source or target')
            originals.add(old); targets.add(new)
            self.entries.append((old, new, row['kind']))
        self.entries.sort(key=lambda row: len(row[0].parts), reverse=True)
        self.inverse_entries = sorted(self.entries, key=lambda row: len(row[1].parts), reverse=True)
        self.source = Path(source) if source else None
        self.map_sha256 = _sha(self.source) if self.source else None

    @classmethod
    def load(cls, source):
        source = _path(str(source))
        _unlinked(source)
        if not source.is_file() or source.stat().st_size > 8*1024*1024:
            raise RelocationError('Relocation map must be a bounded local JSON file')
        raw = source.read_bytes()
        result = cls(json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_json_object), source=source)
        if hashlib.sha256(raw).hexdigest() != result.map_sha256:
            raise RelocationError('Relocation map changed while it was being loaded')
        return result

    def logical(self, value):
        path = Path(value)
        for old, new, kind in self.inverse_entries:
            if path == new or kind == 'directory' and path.is_relative_to(new):
                return old / path.relative_to(new)
        return path

    def resolve(self, value, *, must_exist=False, sha256=None, size=None):
        path = Path(value)
        if '..' in path.parts:
            raise RelocationError('Relocation reads cannot traverse parent components')
        target = path
        for old, new, kind in self.entries:
            if path == old or kind == 'directory' and path.is_relative_to(old):
                target = new / path.relative_to(old)
                break
        _unlinked(target)
        if target != path and _io(path).exists():
            _unlinked(path)
            if _io(path).is_file() != _io(target).is_file() or _io(path).is_dir() != _io(target).is_dir():
                raise RelocationError('Original and relocated resources have conflicting kinds')
            if _io(path).is_file() and (_io(path).stat().st_size != _io(target).stat().st_size or _sha(path) != _sha(target)):
                raise RelocationError('Original and relocated resources have different bytes')
            if _io(path).is_dir() and not _same_directory(path, target):
                raise RelocationError('Original and relocated directories have different contents')
        if must_exist and not _io(target).exists():
            raise RelocationError('Relocated resource is missing: '+str(path))
        if sha256 is not None:
            if not _io(target).is_file() or _sha(target) != sha256:
                raise RelocationError('Relocated resource hash differs from its frozen lock')
        if size is not None and (not _io(target).is_file() or _io(target).stat().st_size != size):
            raise RelocationError('Relocated resource size differs from its frozen lock')
        return target
