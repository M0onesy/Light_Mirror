"""The reorganized project must not scatter outputs or modify frozen documents."""
from pathlib import Path
import ast
import subprocess
import sys

from scripts import create_full_source, run_final_regression


ROOT = Path(__file__).resolve().parents[1]


def test_generation_defaults_remain_in_current_project():
    assert create_full_source.DEFAULT_OUT == ROOT / 'artifacts/source'
    assert run_final_regression.DEFAULT_OUT.is_relative_to(ROOT / 'evidence/regression')
    assert {'artifacts', 'evidence', 'data'} <= create_full_source.EXCLUDED_DIRECTORIES


def test_document_tools_compile_and_help_without_opening_documents(tmp_path):
    tools = ROOT / 'scripts/documentation'
    for path in tools.glob('*.py'):
        ast.parse(path.read_text(encoding='utf-8-sig'), filename=str(path))
    for name in ('修订文档_dev3.py', '生成评审PDF_dev3.py', '验证文档_dev3.py', '封存文档_dev3.py'):
        result = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(tools / name), '--help'],
                                cwd=tmp_path, capture_output=True, text=True,
                                encoding='utf-8', timeout=15)
        assert result.returncode == 0, result.stderr
        assert '--out' in result.stdout


def test_archived_generators_reject_current_inputs_before_writing(tmp_path):
    original = (ROOT / 'rulepack/cards.json').read_bytes()
    generator = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(ROOT / 'scripts/freeze_rulepack.py'),
                                '--source-rulepack', str(ROOT / 'rulepack'), '--out', str(tmp_path / 'draft')],
                               cwd=tmp_path, capture_output=True, text=True, encoding='utf-8', timeout=15)
    assert generator.returncode == 2
    assert 'Current frozen rulepack' in generator.stderr
    assert not (tmp_path / 'draft').exists()
    assert (ROOT / 'rulepack/cards.json').read_bytes() == original
    template = subprocess.run([sys.executable, '-X', 'utf8', '-B', str(ROOT / 'scripts/update_project_documents.py')],
                              cwd=tmp_path, capture_output=True, text=True, encoding='utf-8', timeout=15)
    assert template.returncode == 2
    assert 'Archived dev2 template is read-only' in template.stderr
