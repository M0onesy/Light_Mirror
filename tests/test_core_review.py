from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

from scripts import create_core_review as core


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / 'workspace/mainProj/project'
    root.mkdir(parents=True)
    (root.parents[1] / '交付').mkdir()

    def write(name: str, content: str | bytes = 'fixture\n') -> None:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode() if isinstance(content, str) else content)

    write('README.md', '# Synthetic project\n')
    write('LICENSE', 'GNU AFFERO GENERAL PUBLIC LICENSE\n')
    write('pyproject.toml', '[project]\nname="mingjing"\nversion="1.0.0.dev2"\n')
    write('mingjing/__init__.py', '')
    write('mingjing/secrets.py', '"""Credential broker source, not a private credential file."""\n')
    write('kernel/mingjing_kernel/__init__.py', '')
    write('rulepack/cards.json', '[]\n')
    write('samples/demo_draft_defined.md', '虚构公司2023年收入100万元。\n')
    write('samples/corpus/facts.json', '[]\n')
    write('samples/corpus/demo_annual.pdf', b'%PDF-synthetic-fixture\n')
    write('runtime/harness/source_snapshot.json', '{"git_commit":null}\n')
    write('runtime/finance-plugin/index.mjs', 'export const fixture=true;\n')
    write('docs/USER_GUIDE.md', '# Synthetic user guide\n')
    for name in core.BASELINES:
        path = root.parent / name
        if path.suffix == '.docx':
            with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as document:
                document.writestr('word/document.xml', '<document>虚构需求</document>')
        else:
            path.write_text('# Synthetic development guide\n', encoding='utf-8')
    return root


def target(root: Path) -> Path:
    return root.parents[1] / 'MingJing-core-review'


def test_complete_manifest_folder_and_zip_are_verified(project: Path):
    out = target(project)
    receipt = core.create_core_review(project, out, secret_values=[])
    manifest = json.loads((out / 'core-review-manifest.json').read_text(encoding='utf-8'))
    archive = out.with_name(out.name + '.zip')
    assert receipt['status'] == 'passed'
    assert receipt['archive_bytes'] == archive.stat().st_size < 30_000_000
    assert receipt['archive_sha256'] == hashlib.sha256(archive.read_bytes()).hexdigest()
    assert manifest['independently_runnable_native_agent'] is False
    assert manifest['version'] == '1.0.0.dev2'
    assert receipt['file_count'] == len(manifest['files'])
    assert (out / 'PROJECT_README.md').is_file()
    assert '不能单独启动' in (out / 'README.md').read_text(encoding='utf-8')
    with zipfile.ZipFile(archive) as zipped:
        for record in manifest['files']:
            data = zipped.read(out.name + '/' + record['path'])
            assert len(data) == record['bytes']
            assert hashlib.sha256(data).hexdigest() == record['sha256']
            assert (out / record['path']).read_bytes() == data


def test_selection_excludes_histories_binaries_private_files_and_annotations(project: Path):
    extras = [
        'runs/task/results.json', 'runtime/finance-carrier/staged/node.exe',
        'vendor/harness/packages/private.ts', 'docs/credentials/token.json',
        'frontend/node_modules/pkg/index.js', 'frontend/.env.local',
        'samples/corpus/private.pdf', 'tests/fixtures/secrets.json',
        'bench/v1/annotations/private.json', 'bench/D3.jsonl',
    ]
    for name in extras:
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('DO NOT SHIP', encoding='utf-8')
    (project / 'bench/v1/evaluator.py').write_text('# public evaluator\n', encoding='utf-8')
    selected = {path.relative_to(project).as_posix() for path in core.select_files(project)}
    assert selected.isdisjoint(extras)
    assert 'mingjing/secrets.py' in selected
    assert 'runtime/finance-plugin/index.mjs' in selected
    assert 'bench/v1/evaluator.py' in selected


def test_plaintext_secret_fails_without_publishing(project: Path):
    private = b'configured-real-value-unique-123456789'
    (project / 'docs/leaked.md').write_bytes(b'prefix ' + private + b' suffix')
    with pytest.raises(ValueError, match='Configured plaintext credential'):
        core.create_core_review(project, target(project), secret_values=[private])
    assert not target(project).exists()
    assert not target(project).with_name(target(project).name + '.zip').exists()


def test_compressed_docx_secret_is_detected(project: Path):
    private = b'compressed-credential-not-visible-in-zip-container'
    docx = project.parent / core.BASELINES[0]
    with zipfile.ZipFile(docx, 'w', zipfile.ZIP_DEFLATED) as document:
        document.writestr('word/document.xml', b'<document>' + private + b'</document>')
    assert private not in docx.read_bytes()
    with pytest.raises(ValueError, match='Configured plaintext credential'):
        core.create_core_review(project, target(project), secret_values=[private])
    assert not target(project).exists()


def test_word_secret_split_across_text_runs_is_detected(project: Path):
    private = b'split-secret-value-in-visible-word-text'
    docx = project.parent / core.BASELINES[0]
    with zipfile.ZipFile(docx, 'w', zipfile.ZIP_DEFLATED) as document:
        document.writestr('word/document.xml',
            '<document><t>split-secret-value-</t><t>in-visible-word-text</t></document>')
    with pytest.raises(ValueError, match='Configured plaintext credential'):
        core.create_core_review(project, target(project), secret_values=[private])


def test_private_key_block_is_rejected_without_printing_value(project: Path):
    (project / 'docs/key.md').write_text('-----BEGIN PRIVATE KEY-----\nencoded_private_material\n-----END PRIVATE KEY-----\n', encoding='utf-8')
    with pytest.raises(ValueError, match='Private key material') as failure:
        core.create_core_review(project, target(project), secret_values=[])
    assert 'encoded_private_material' not in str(failure.value)


def test_existing_review_is_never_overwritten(project: Path):
    out = target(project)
    out.mkdir()
    marker = out / 'historical.txt'
    marker.write_bytes(b'historical frozen bytes')
    with pytest.raises(FileExistsError):
        core.create_core_review(project, out, secret_values=[])
    assert marker.read_bytes() == b'historical frozen bytes'


def test_archive_size_failure_cleans_only_stage(project: Path):
    unrelated = project.parents[1] / 'protected'
    unrelated.mkdir()
    (unrelated / 'keep.txt').write_bytes(b'protected')
    with pytest.raises(ValueError, match='archive exceeds'):
        core.create_core_review(project, target(project), secret_values=[], max_archive_bytes=1)
    assert not list(project.parents[1].glob('.core-review-incomplete-*'))
    assert not target(project).exists()
    assert (unrelated / 'keep.txt').read_bytes() == b'protected'


@pytest.mark.parametrize('invalid', [0, 30_000_001, True])
def test_size_limit_cannot_be_disabled(project: Path, invalid):
    with pytest.raises(ValueError, match='must not exceed'):
        core.create_core_review(project, target(project), secret_values=[], max_archive_bytes=invalid)


def test_destination_must_be_separate_and_inside_workspace(project: Path):
    for out in (project / 'review', project.parents[1], project.parents[2] / 'outside'):
        with pytest.raises(ValueError, match='separate folder'):
            core.create_core_review(project, out, secret_values=[])


def test_nested_project_can_publish_directly_to_delivery(project: Path):
    workspace, requirements = project.parents[1], project.parent
    nested = requirements / '新版/project'
    nested.parent.mkdir()
    project.rename(nested)
    out = workspace / '交付/新版/MingJing-core-review'
    receipt = core.create_core_review(nested, out, requirements_dir=requirements, secret_values=[])
    assert receipt['status'] == 'passed' and out.is_dir()
    assert core._review_workspace(nested) == workspace
    with pytest.raises(ValueError, match='separate folder'):
        core.create_core_review(nested, workspace.parent / 'outside',
                                requirements_dir=requirements, secret_values=[])


def test_nearest_matching_workspace_wins(project: Path):
    nearer = project.parent / 'nested-workspace'
    nested = nearer / 'mainProj/新版/project'
    nested.mkdir(parents=True)
    (nearer / '交付').mkdir()
    assert core._review_workspace(nested) == nearer


def test_plain_clone_uses_its_immediate_parent_boundary(project: Path):
    (project.parents[1] / '交付').rmdir()
    assert core._review_workspace(project) == project.parent
    receipt = core.create_core_review(project, project.parent / 'review', secret_values=[])
    assert receipt['status'] == 'passed'
    with pytest.raises(ValueError, match='separate folder'):
        core.create_core_review(project, target(project), secret_values=[])


def test_workspace_never_expands_to_a_filesystem_root(project: Path):
    with pytest.raises(ValueError, match='filesystem root'):
        core._review_workspace(Path(project.anchor))


@pytest.mark.parametrize('competition', [True, False])
def test_default_output_stays_in_managed_artifacts(project: Path, monkeypatch, competition):
    if not competition:
        (project.parents[1] / '交付').rmdir()
    captured = {}
    def capture(root, out, *, requirements_dir):
        captured.update(root=root, out=out, requirements_dir=requirements_dir)
        return {'status': 'fixture_only'}
    monkeypatch.setattr(core, 'ROOT', project)
    monkeypatch.setattr(core, 'create_core_review', capture)
    assert core.main([]) == 0
    assert captured == {'root': project, 'out': project / 'artifacts/core-review/MingJing-core-review',
                        'requirements_dir': project.parent}


def test_managed_artifact_output_and_single_current_baselines(project: Path):
    requirements = project / 'docs/project'
    requirements.mkdir(parents=True)
    for name in core.BASELINES:
        shutil.copyfile(project.parent / name, requirements / name)
    out = project / 'artifacts/core-review/MingJing-core-review'
    receipt = core.create_core_review(project, out, secret_values=[])
    manifest = json.loads((out / 'core-review-manifest.json').read_text(encoding='utf-8'))
    members = {row['path'] for row in manifest['files']}
    assert receipt['status'] == 'passed'
    assert core._baseline_directory(project) == requirements
    assert {'docs/project/' + name for name in core.BASELINES} <= members
    assert not {'requirements/' + name for name in core.BASELINES} & members


@pytest.mark.parametrize('relative', ['artifacts/core-review', 'artifacts/source/review',
                                    'docs/review', 'artifacts/core-review/../outside'])
def test_managed_output_does_not_allow_other_source_subtrees(project: Path, relative: str):
    with pytest.raises(ValueError):
        core.create_core_review(project, project / relative, secret_values=[])


def test_single_current_baselines_take_precedence_over_stale_parent(project: Path):
    requirements = project / 'docs/project'
    requirements.mkdir(parents=True)
    for name in core.BASELINES:
        shutil.copyfile(project.parent / name, requirements / name)
        (project.parent / name).write_bytes(b'stale parent source must not enter review')
    out = project / 'artifacts/core-review/selected'
    core.create_core_review(project, out, secret_values=[])
    for name in core.BASELINES:
        assert (out / 'docs/project' / name).read_bytes() == (requirements / name).read_bytes()


def test_direct_script_imports_its_own_package_from_an_independent_cwd(project: Path, tmp_path: Path):
    script = project / 'scripts/create_core_review.py'
    script.parent.mkdir()
    shutil.copyfile(Path(core.__file__), script)
    (script.parent / 'package_delivery.py').write_text(
        'def configured_secret_values(root):\n    return []\n', encoding='utf-8')
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    out = target(project)
    result = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(script), '--out', str(out),
                             '--requirements-dir', str(project.parent)], cwd=tmp_path, env=env,
                            capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt['status'] == 'passed' and receipt['credential_scan']['references_checked'] == 0
    assert out.with_name(out.name + '.zip').is_file()


def test_linked_delivery_marker_cannot_expand_the_workspace(project: Path, tmp_path: Path):
    delivery = project.parents[1] / '交付'
    delivery.rmdir()
    external = tmp_path / 'outside'
    external.mkdir()
    try:
        delivery.symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip('This Windows user cannot create symbolic links')
    with pytest.raises(ValueError, match='symlinks'):
        core.create_core_review(project, delivery / 'review', secret_values=[])


def test_missing_baseline_fails_instead_of_silently_omitting_it(project: Path):
    (project.parent / core.BASELINES[1]).unlink()
    with pytest.raises(ValueError, match='Required review resource'):
        core.create_core_review(project, target(project), secret_values=[])


def test_invalid_word_container_is_rejected(project: Path):
    (project.parent / core.BASELINES[0]).write_bytes(b'not a word container')
    with pytest.raises(ValueError, match='valid Word document'):
        core.create_core_review(project, target(project), secret_values=[])


def test_linked_source_directory_is_rejected(project: Path, tmp_path: Path):
    external = tmp_path / 'external'
    external.mkdir()
    (external / 'private.py').write_text('PRIVATE', encoding='utf-8')
    try:
        (project / 'docs/linked').symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip('This Windows user cannot create symbolic links')
    with pytest.raises(ValueError, match='symlinks'):
        core.select_files(project)
