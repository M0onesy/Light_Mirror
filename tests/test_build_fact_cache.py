import csv
import hashlib
import importlib.util
import json
import os
from decimal import Decimal
from pathlib import Path

import pytest

from mingjing.models import Fact


def _builder():
    path = Path(__file__).parents[1] / "scripts" / "build_fact_cache.py"
    spec = importlib.util.spec_from_file_location("mingjing_build_fact_cache", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _fixture(tmp_path):
    root = tmp_path / "materials"
    pdf = root / "reports" / "demo" / "2024-04-15_demo.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"original source bytes")
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    row = {
        "code": "D1", "name": "Demo", "orgId": "demo", "category": "年报",
        "date": "2024-04-15", "title": "2023年年度报告", "url": "",
        "file": "reports/demo/2024-04-15_demo.pdf",
        "size_bytes": str(pdf.stat().st_size), "sha256": digest,
    }
    root.mkdir(exist_ok=True)
    with (root / "index.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    manifest = {"schema_version": 1, "cutoff": "2024-04-22", "identities": {}, "documents": [row]}
    (root / "material_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # A stale old cache is intentionally present.  It must never be copied or
    # used as the new generation's facts.
    (root / "facts.json").write_text(
        json.dumps({"facts": [{"value": "999999"}]}), encoding="utf-8"
    )
    return root, pdf, digest


def test_reparse_does_not_copy_stale_amount_and_preserves_parent(tmp_path, monkeypatch):
    module = _builder()
    root, pdf, original_digest = _fixture(tmp_path)
    old_facts = (root / "facts.json").read_bytes()

    def fake_loader(stage, **kwargs):
        source = Path(stage) / "reports" / "demo" / "2024-04-15_demo.pdf"
        return [Fact(
            "rebuilt", "Demo", "REVENUE", "2023Y", value=Decimal("100"),
            source_path=str(source), source_doc=original_digest[:12],
            source_type="primary_statement", extractor_version="financial-tables-v3",
        )]

    monkeypatch.setattr(module, "load_or_extract_facts", fake_loader)
    monkeypatch.setattr(module, "extract_text", lambda path: [])
    out = module.build_generation(root, tmp_path / "cachegen", progress=None)

    payload = json.loads((out / "facts.json").read_text(encoding="utf-8"))
    assert payload["facts"][0]["value"] == "100"
    assert payload["facts"][0]["source_path"] == "reports/demo/2024-04-15_demo.pdf"
    assert payload["source_fact_cache"]["schema_version"] == module.CACHE_SCHEMA
    assert (root / "facts.json").read_bytes() == old_facts
    assert (root / "index.csv").read_bytes() == (out / "index.csv").read_bytes()
    assert os.stat(pdf).st_ino == os.stat(out / "reports/demo/2024-04-15_demo.pdf").st_ino
    assert os.stat(root / "index.csv").st_ino == os.stat(out / "index.csv").st_ino
    assert os.stat(root / "material_manifest.json").st_ino == os.stat(out / "material_manifest.json").st_ino


def test_changed_source_bytes_are_rejected_before_reparse(tmp_path):
    module = _builder()
    root, pdf, _ = _fixture(tmp_path)
    pdf.write_bytes(b"original source bytez")
    with pytest.raises(ValueError, match="SHA-256"):
        module.build_generation(root, tmp_path / "cachegen")
    assert not (tmp_path / "cachegen").exists()


def test_existing_generation_is_never_overwritten(tmp_path, monkeypatch):
    module = _builder()
    root, _, _ = _fixture(tmp_path)
    out = tmp_path / "cachegen"
    out.mkdir()
    marker = out / "marker"
    marker.write_text("keep", encoding="utf-8")
    monkeypatch.setattr(module, "_load_manifest", lambda *_: pytest.fail("read after overwrite check"))
    with pytest.raises(ValueError, match="拒绝覆盖"):
        module.build_generation(root, out)
    assert marker.read_text(encoding="utf-8") == "keep"
