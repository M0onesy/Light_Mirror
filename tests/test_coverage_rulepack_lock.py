"""A generated rule package is complete; existing signoff remains version-bound."""
import json
from pathlib import Path
import shutil

import pytest

from bench.v1.professional_review import rule_lock
from scripts.extend_coverage_rules import refresh_rulepack_lock


ROOT = Path(__file__).resolve().parents[1]


def test_build_refreshes_every_registered_source_without_touching_approvals(tmp_path):
    root = tmp_path/'rules'
    shutil.copytree(ROOT/'rulepack', root)
    old, cards = rule_lock(root)
    raw_cards = (root/'cards.json').read_bytes()
    source = root/'sources/MJ-COVERAGE-METHOD.md'
    source.write_bytes(source.read_bytes() + b'\nBuild-only integrity test.\n')
    with pytest.raises(ValueError, match='规则文件已变动'):
        rule_lock(root)
    lock = refresh_rulepack_lock(root)
    current, after_cards = rule_lock(root)
    assert lock['version'] == current['version'] == json.loads((root/'sources.json').read_text(encoding='utf-8'))['version']
    assert 'sources/MJ-COVERAGE-METHOD.md' in lock['files']
    assert current['rulepack_sha256'] != old['rulepack_sha256']
    assert (root/'cards.json').read_bytes() == raw_cards and after_cards == cards
    assert all(card.get('approved_by') is None for card in after_cards)
    assert json.loads((root/'sources.json').read_text(encoding='utf-8'))['professional_signoff_status'] == 'pending'
