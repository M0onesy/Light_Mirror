"""Evidence previews render only the PDF bytes checked against their lock."""
from pathlib import Path

import pytest

from mingjing import server
from mingjing.evidence import sha256_file


def _pdf(fitz, text):
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 100), text)
        return document.tobytes()


@pytest.fixture
def pdf_source(tmp_path):
    fitz = pytest.importorskip('fitz')
    original = _pdf(fitz, 'Original evidence revenue 100')
    modified = _pdf(fitz, 'Different evidence revenue 999')
    source = tmp_path / 'annual.pdf'
    source.write_bytes(original)
    lock = sha256_file(source)
    info = {'source_path': str(source), 'document_sha12': lock[:12],
            'document_sha256': lock, 'page': 1, 'bbox': [60, 80, 300, 110]}
    return fitz, source, original, modified, info


def test_path_replacement_after_snapshot_cannot_change_rendered_document(pdf_source, monkeypatch):
    fitz, source, original, modified, info = pdf_source
    original_open = fitz.open
    rendered_text = []

    def replace_before_parser_open(*args, **kwargs):
        source.write_bytes(modified)
        # A path reopen would parse the new PDF under the old evidence ID.
        document = original_open(*args, **kwargs)
        rendered_text.append(document[0].get_text())
        return document

    monkeypatch.setattr(fitz, 'open', replace_before_parser_open)
    preview = server._evidence_png(info, {'security': {'allow_external_paths': True}})
    assert preview.startswith(b'\x89PNG')
    assert source.read_bytes() == modified
    assert rendered_text == ['Original evidence revenue 100\n']


def test_changed_bytes_while_reading_cannot_pass_original_lock(pdf_source, monkeypatch):
    _, source, _, modified, info = pdf_source
    original_open = Path.open

    class ChangingReader:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.stream.close()

        def fileno(self):
            return self.stream.fileno()

        def read(self, limit):
            with original_open(source, 'wb') as writer:
                writer.write(modified)
            return self.stream.read(limit)

    def change_on_read(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        return ChangingReader(stream) if path == source and args == ('rb',) else stream

    monkeypatch.setattr(Path, 'open', change_on_read)
    with pytest.raises(ValueError, match='SHA-256'):
        server._evidence_png(info, {})


def test_full_document_lock_is_checked_in_addition_to_evidence_prefix(pdf_source):
    _, _, _, _, info = pdf_source
    info['document_sha256'] = info['document_sha12'] + '0' * 52
    with pytest.raises(ValueError, match='SHA-256'):
        server._evidence_png(info, {})


def test_legacy_prefix_evidence_still_renders_pdf_page(pdf_source):
    _, _, _, _, info = pdf_source
    del info['document_sha256']
    assert server._evidence_png(info, {}).startswith(b'\x89PNG')


def test_oversized_source_is_rejected_before_read_or_parser(pdf_source, monkeypatch):
    fitz, source, _, _, info = pdf_source
    original_open = Path.open
    reads = []

    class UnreadableLargeReader:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.stream.close()

        def fileno(self):
            return self.stream.fileno()

        def read(self, _):
            reads.append('read')
            raise AssertionError('A large PDF must be rejected before allocating it')

    def guarded_open(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        return UnreadableLargeReader(stream) if path == source and args == ('rb',) else stream

    original_fstat = server.os.fstat
    def large_size(fd):
        stat = original_fstat(fd)
        return type('Stat', (), {'st_size': 256 * 1024 * 1024 + 1})() if stat.st_size == source.stat().st_size else stat

    monkeypatch.setattr(Path, 'open', guarded_open)
    monkeypatch.setattr(server.os, 'fstat', large_size)
    monkeypatch.setattr(fitz, 'open', lambda *_a, **_k: pytest.fail('Oversized PDF entered parser'))
    with pytest.raises(ValueError, match='PDF 过大'):
        server._evidence_png(info, {})
    assert reads == []


def test_growth_past_snapshot_bound_is_rejected_after_bounded_read(pdf_source, monkeypatch):
    _, source, _, _, info = pdf_source
    original_open = Path.open
    limits = []

    class GrowingReader:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.stream.close()

        def fileno(self):
            return self.stream.fileno()

        def read(self, limit):
            limits.append(limit)
            # A sized sentinel checks the limit without allocating 256 MiB.
            return type('OversizedSnapshot', (), {'__len__': lambda _: limit})()

    def growing_open(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        return GrowingReader(stream) if path == source and args == ('rb',) else stream

    monkeypatch.setattr(Path, 'open', growing_open)
    with pytest.raises(ValueError, match='PDF 过大'):
        server._evidence_png(info, {})
    assert limits == [256 * 1024 * 1024 + 1]
