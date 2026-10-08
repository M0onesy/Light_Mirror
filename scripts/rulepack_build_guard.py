"""Fail closed before an older builder can replace a newer rule release."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re


class RulepackBuildError(RuntimeError):
    pass


def _version(value, source):
    if not isinstance(value, str) or not re.fullmatch(r'\d+\.\d+\.\d+', value):
        raise RulepackBuildError(f'Cannot rebuild: {source} has an invalid release version')
    return tuple(int(part) for part in value.split('.'))


def assert_rebuild_compatible(root, target_version):
    """Inspect all release declarations before the first generated write.

    A partly overwritten sources registry must not let an old builder erase
    newer cards or a newer lock. Signed rule cards need a separate explicit
    review workflow; automatic generators cannot replace them.
    """
    root = Path(root)
    target = _version(target_version, 'builder')
    for name in ('sources.json', 'rulepack.lock', 'cards.json'):
        path = root / name
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RulepackBuildError(f'Cannot rebuild: unreadable {name}') from exc
        if name == 'cards.json':
            if not isinstance(payload, list) or any(not isinstance(card, dict) for card in payload):
                raise RulepackBuildError('Cannot rebuild: cards.json must contain rule objects')
            declarations = [(card.get('version'), f'card {card.get("id", "unknown")}') for card in payload]
            if any(card.get('approved_by') for card in payload):
                raise RulepackBuildError('Cannot rebuild signed cards: use the explicit professional review workflow')
        else:
            if not isinstance(payload, dict):
                raise RulepackBuildError(f'Cannot rebuild: {name} must be a release object')
            declarations = [(payload.get('version'), name)]
        for value, source in declarations:
            if _version(value, source) > target:
                raise RulepackBuildError(
                    f'Refusing rulepack downgrade: {source} is {value}, builder is {target_version}; '
                    'the old base generator cannot replace the extended release')


@contextmanager
def exclusive_rulepack_build(root):
    """Serialise the base and extension builders, without stealing stale locks."""
    lock = Path(root) / '.rulepack-build.lock'
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise RulepackBuildError('Another rulepack build owns .rulepack-build.lock; no files were changed') from exc
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
            handle.write(str(os.getpid()))
        yield
    finally:
        lock.unlink(missing_ok=True)
