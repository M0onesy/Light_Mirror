from pathlib import Path

import pytest

from scripts.verify_harness_ui_sources import safe_resource, verify


@pytest.mark.parametrize("relative", ["", "../escape.ts", "C:/escape.ts", "/escape.ts", "a\\escape.ts"])
def test_ui_source_lock_refuses_unsafe_manifest_paths(tmp_path, relative):
    with pytest.raises(ValueError, match="Unsafe vendor source path"):
        safe_resource(tmp_path, relative)


def test_current_vendor_bytes_and_frame_method_match_locked_archive():
    result = verify()
    assert result["status"] == "passed_source_and_adapter_lock", result["errors"]
    assert result["vendor_files"] == 125
    assert result["vendor_bytes"] == 869934
    assert result["adapter"]["generated_text_match"]
    assert result["upstream_commit"] is None
    assert all(reference["symbols_present"] for reference in result["component_references"])
