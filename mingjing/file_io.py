"""Filesystem-only Windows long paths; resource identities stay canonical."""
from __future__ import annotations

import os
from pathlib import Path
import tempfile
import time


def record_path(path: str | os.PathLike[str]) -> Path:
    """Remove an IO prefix before storing or comparing a resource identity."""
    text = os.fspath(path)
    if os.name == 'nt':
        if text.startswith('\\\\?\\UNC\\'):
            text = '\\\\' + text[8:]
        elif text.startswith('\\\\?\\'):
            text = text[4:]
    return Path(text)


def io_path(path: str | os.PathLike[str], *, force_extended: bool = False) -> Path:
    """Use extended paths only for IO, without resolving links or permissions."""
    value = Path(path)
    if os.name != 'nt':
        return value
    text = os.fspath(value)
    if text.startswith('\\\\?\\'):
        return value
    absolute = os.path.abspath(text)
    if not force_extended and len(absolute) < 240:
        return value
    if absolute.startswith('\\\\.\\'):
        raise ValueError('Device namespaces are not file paths')
    if absolute.startswith('\\\\'):
        return Path('\\\\?\\UNC\\' + absolute[2:])
    return Path('\\\\?\\' + absolute)


def atomic_write_text(path: str | os.PathLike[str], text: str, *,
                      before_replace=None, sync: bool = False,
                      create_parents: bool = False) -> None:
    """Publish in the same directory; keep the old bytes on write/cancel failure."""
    destination = Path(path)
    parent = io_path(destination.parent, force_extended=True)
    if create_parents:
        parent.mkdir(parents=True, exist_ok=True)
    # Long resource names must not be duplicated in a temporary filename.
    descriptor, temporary_name = tempfile.mkstemp(prefix='.mj-', suffix='.tmp', dir=str(parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(text)
            if sync:
                handle.flush()
                os.fsync(handle.fileno())
        for attempt in range(8):
            if before_replace is not None:
                before_replace()
            try:
                temporary.replace(io_path(destination))
                break
            except PermissionError as exc:
                if getattr(exc, 'winerror', None) not in {5, 32, 33} or attempt == 7:
                    raise
                time.sleep(min(0.01 * 2 ** attempt, 0.25))
    finally:
        temporary.unlink(missing_ok=True)
