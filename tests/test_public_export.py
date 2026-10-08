from __future__ import annotations

import io
from pathlib import Path
import zipfile

import fitz
import pytest

from scripts import prepare_public_repository as public
from scripts.create_core_review import BASELINES


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / 'workspace/mainProj/project'
    root.mkdir(parents=True)
    for name in BASELINES:
        (root.parent / name).write_bytes(b'fixture')
    return root


def test_selection_includes_current_review_pdf_not_stale_or_private_material(project: Path):
    desired = project.parent / '明镜MingJing_项目计划书_初赛评审版.pdf'
    desired.write_bytes(b'current review PDF')
    (project.parent / '明镜MingJing_项目计划书.pdf').write_bytes(b'stale PDF')
    (project.parent / 'private-financial-report.pdf').write_bytes(b'private PDF')
    selected = public.project_documents(project)
    assert len(selected) == 3
    assert selected['docs/project/' + desired.name] == desired
    assert {path.name for path in selected.values()} == set(BASELINES) | {desired.name}


def test_missing_review_pdf_is_optional_but_requirement_baselines_are_required(project: Path):
    assert len(public.project_documents(project)) == 2
    (project.parent / BASELINES[0]).unlink()
    with pytest.raises(ValueError, match='missing'):
        public.project_documents(project)


def test_export_refuses_an_existing_repository_before_reading_credentials(project: Path, monkeypatch):
    monkeypatch.setattr(public, 'ROOT', project)
    destination = project.parents[1] / 'Light_Mirror'
    (destination / '.git').mkdir(parents=True)
    marker = destination / '.git/keep'
    marker.write_bytes(b'frozen old Git bytes')

    def must_not_resolve_keys(*_):
        pytest.fail('Existing destination must fail before credential access')

    monkeypatch.setattr(public, 'configured_secret_values', must_not_resolve_keys)
    with pytest.raises(ValueError, match='new, separate'):
        public.export_repository(destination)
    assert marker.read_bytes() == b'frozen old Git bytes'


def test_compressed_docx_secret_and_split_visible_xml_are_rejected():
    key = b'opaque-private-secret-value-123456789'
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as document:
        document.writestr('word/document.xml',
            '<document><t>opaque-private-secret-</t><t>value-123456789</t></document>')
    with pytest.raises(ValueError, match='Configured plaintext credential') as failure:
        public.scan_document(stream.getvalue(), [key], 'docs/project/plan.docx')
    assert key.decode() not in str(failure.value)


def test_pdf_compressed_text_is_scanned_without_exposing_credential():
    key = b'private-opaque-credential-123456789'
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), key.decode())
        data = document.tobytes(deflate=True)
    assert key not in data
    with pytest.raises(ValueError, match='Configured plaintext credential') as failure:
        public.scan_document(data, [key], 'docs/project/current.pdf')
    assert key.decode() not in str(failure.value)


def test_pdf_metadata_is_scanned():
    key = b'private-metadata-credential-123456789'
    with fitz.open() as document:
        document.new_page()
        document.set_metadata({'subject': key.decode()})
        data = document.tobytes(deflate=True)
    with pytest.raises(ValueError, match='Configured plaintext credential'):
        public.scan_document(data, [key], 'current.pdf')


def test_malformed_pdf_does_not_pass_as_scanned():
    with pytest.raises(ValueError, match='could not be decoded'):
        public.scan_document(b'%PDF-incomplete', [], 'current.pdf')


def test_valid_pdf_can_be_selected_and_scanned():
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), 'Synthetic public submission')
        data = document.tobytes(deflate=True)
    public.scan_document(data, [b'private-key-not-in-document'], 'current.pdf')
