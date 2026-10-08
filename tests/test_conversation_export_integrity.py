"""Export retains independently frozen message proofs and rejects changes."""
import io
import zipfile

import pytest

from mingjing.server import _export_run
from test_session_evidence import citation


def test_original_quotation_images_and_registration_exported(citation):
    state, run = citation['state'], citation['run']
    raw = _export_run(run, state.config, state.sessions)
    sid = citation['session']['session_id']
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert f'conversations/{sid}.json' in archive.namelist()
        prefix = f'conversations/{sid}/message-0001/'
        assert prefix+'source_references.json' in archive.namelist()
        images = [name for name in archive.namelist() if name.startswith(prefix) and name.endswith('.png')]
        assert images and archive.read(images[0]).startswith(b'\x89PNG')
    for name, before in citation['frozen'].items():
        assert (run.out/name).read_bytes() == before


def test_changed_message_image_blocks_export(citation):
    image = next(citation['folder'].glob('*.png'))
    image.write_bytes(b'tampered')
    with pytest.raises(ValueError, match='conversation output changed'):
        _export_run(citation['run'], citation['state'].config, citation['state'].sessions)


def test_running_conversation_cannot_export_incomplete_cache(citation):
    state = citation['state']
    state.sessions.inflight.add(citation['session']['session_id'])
    try:
        with pytest.raises(ValueError, match='运行'):
            _export_run(citation['run'], state.config, state.sessions)
    finally:
        state.sessions.inflight.clear()
