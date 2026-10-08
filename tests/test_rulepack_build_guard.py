"""Stale generators must fail before writing any frozen release artifact."""
import importlib.util
import json
from pathlib import Path

import pytest

from scripts.rulepack_build_guard import (
    RulepackBuildError, assert_rebuild_compatible, exclusive_rulepack_build,
)


def release(root, source='0.6.0', locked='0.6.0', card='0.6.0'):
    for name, value in [('sources.json', {'version': source, 'sources': []}),
                        ('rulepack.lock', {'version': locked, 'files': {}}),
                        ('cards.json', [{'id': 'extended', 'version': card, 'approved_by': None}])]:
        (root/name).write_text(json.dumps(value), encoding='utf-8')


@pytest.mark.parametrize('versions', [('0.6.0','0.6.0','0.6.0'),
    ('0.5.0','0.6.0','0.5.0'), ('0.5.0','0.5.0','0.6.0')])
def test_stale_base_generator_cannot_erase_new_sources_lock_or_cards(tmp_path, monkeypatch, versions):
    release(tmp_path, *versions)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    path = Path(__file__).parents[1]/'rulepack/build_v2.py'
    spec = importlib.util.spec_from_file_location('isolated_rule_builder', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    monkeypatch.setattr(module, '_build', lambda: pytest.fail('stale generator wrote files'))
    with pytest.raises(RulepackBuildError, match='downgrade'):
        module.main()
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_unknown_version_and_signed_rules_fail_closed(tmp_path):
    release(tmp_path, source='broken')
    with pytest.raises(RulepackBuildError, match='invalid release version'):
        assert_rebuild_compatible(tmp_path, '0.6.0')
    release(tmp_path)
    cards = json.loads((tmp_path/'cards.json').read_text('utf-8'))
    cards[0]['approved_by'] = 'independent-reviewer'
    (tmp_path/'cards.json').write_text(json.dumps(cards), encoding='utf-8')
    with pytest.raises(RulepackBuildError, match='signed cards'):
        assert_rebuild_compatible(tmp_path, '0.6.0')


def test_version_order_is_numeric_and_extension_cannot_downgrade(tmp_path):
    release(tmp_path, source='0.10.0', locked='0.10.0', card='0.10.0')
    with pytest.raises(RulepackBuildError, match='downgrade'):
        assert_rebuild_compatible(tmp_path, '0.6.0')
    assert_rebuild_compatible(tmp_path, '0.10.0')


def test_builders_share_exclusive_lock_and_failure_releases_owned_lock(tmp_path):
    with pytest.raises(RuntimeError, match='interrupted'):
        with exclusive_rulepack_build(tmp_path):
            with pytest.raises(RulepackBuildError, match='Another rulepack build'):
                with exclusive_rulepack_build(tmp_path):
                    pytest.fail('concurrent builder acquired lock')
            raise RuntimeError('interrupted')
    assert not (tmp_path/'.rulepack-build.lock').exists()
    with exclusive_rulepack_build(tmp_path):
        assert (tmp_path/'.rulepack-build.lock').exists()


def test_corrupt_release_json_does_not_allow_overwrite(tmp_path):
    release(tmp_path)
    (tmp_path/'rulepack.lock').write_text('{', encoding='utf-8')
    with pytest.raises(RulepackBuildError, match='unreadable rulepack.lock'):
        assert_rebuild_compatible(tmp_path, '0.6.0')
