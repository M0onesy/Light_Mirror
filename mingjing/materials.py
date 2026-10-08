"""Controlled additional financial materials; no source corpus is replaced."""
from __future__ import annotations

from pathlib import Path

from .config import validate_path

MAX_CORPUS_ADDITIONS = 32


def normalize_corpus_additions(value, corpus: str | Path, *, allowed_roots=None,
                               follow_symlinks: bool = False) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_CORPUS_ADDITIONS:
        raise ValueError(f'corpus_additions must be a list of at most {MAX_CORPUS_ADDITIONS} material paths')
    if any(not isinstance(item, str) or not item.strip() or len(item) > 4096 for item in value):
        raise ValueError('corpus_additions only accepts nonempty material path strings')
    seen = {str(Path(corpus).resolve()).casefold()}
    result = []
    for item in value:
        path = validate_path(item, allowed_roots=allowed_roots, must_exist=True,
                             kind='any', follow_symlinks=follow_symlinks)
        key = str(path).casefold()
        if key not in seen:
            seen.add(key)
            result.append(str(path))
    return result
