# -*- mode: python ; coding: utf-8 -*-
"""Single Windows EXE with a finance-only carrier deployed once to stable cache."""
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPEC).resolve().parent
datas = []
for relative in ("web", "rulepack", "samples", "docs"):
    datas.append((str(ROOT / relative), relative))
datas.append((str(ROOT / "LICENSE"), "."))
# Parsing and replay verify source hashes inside the frozen application too.
# Preserve the sources beside the compiled modules, under the same package
# paths used by source_cache and execution_fingerprint.
for source in (ROOT / "mingjing").rglob("*.py"):
    if "__pycache__" not in source.parts:
        datas.append((str(source), str(source.parent.relative_to(ROOT))))

sys.path.insert(0, str(ROOT))
from scripts.build_frontend import verify as verify_frontend, verify_smart
from scripts.build_receipt import runtime_assets, finance_payload_assets

verify_frontend(ROOT)
verify_smart(ROOT)
runtime_assets(ROOT)  # Verify compiled code, dependency resolution and provenance.
# PyInstaller extracts three payload files, not thousands of npm files on
# every start. The consumer validates and deploys one content-hash generation.
for source in finance_payload_assets(ROOT):
    datas.append((str(source), "runtime/finance-carrier"))
datas.append((str(ROOT / "runtime/harness/source_snapshot.json"), "runtime/harness"))

# Official builds resolve one application package from this checkout only.
modules = [path.stem for path in (ROOT / "mingjing").glob("*.py")
           if path.stem not in {"__init__", "exe_entry", "streamlit_app"}]
hiddenimports = ["mingjing"]
hiddenimports += [f"mingjing.{name}" for name in modules]
# 直传管线由服务端与命令行延迟导入，显式登记全部子模块
hiddenimports += ["mingjing.smart"] + [f"mingjing.smart.{path.stem}" for path in (ROOT / "mingjing/smart").glob("*.py")
                                         if path.stem != "__init__"]
hiddenimports += ["fitz", "pymupdf", "mcp_types", "jsonschema", "jsonschema_specifications",
                  "referencing", "rpds", "pydantic", "pydantic_core", "httpx2"]
# The optional upstream CLI imports typer and exits if its extra is absent.
# MingJing uses official client/server SDK APIs, never that developer CLI.
hiddenimports += collect_submodules("mcp", filter=lambda name: not name.startswith("mcp.cli"))
datas += collect_data_files("jsonschema_specifications")
datas += copy_metadata("mcp", recursive=True)
datas += copy_metadata("PyMuPDF")

analysis = Analysis(
    [str(ROOT / "scripts/launch_exe.py")], pathex=[str(ROOT)],
    binaries=[], datas=datas, hiddenimports=sorted(set(hiddenimports)),
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["streamlit", "torch", "tensorflow", "pytest", "frontend", "IPython",
              "notebook", "sphinx", "numpy", "scipy", "pandas", "matplotlib",
              "jupyter_client", "jupyter_core", "ipykernel", "ipywidgets", "debugpy", "_pytest", "dill"], noarchive=False,
)
# A sibling historical project must never satisfy the current package imports.
# PyInstaller adds an entry script's inferred package parent before pathex;
# the non-package scripts entry avoids the old root shim's parent inference.
for name in hiddenimports:
    if name == "mingjing" or name.startswith("mingjing."):
        node = analysis.graph.find_node(name)
        if node is None or not node.filename or not Path(node.filename).resolve().is_relative_to(ROOT / "mingjing"):
            raise RuntimeError(f"Frozen module is missing or comes from another checkout: {name}")
pyz = PYZ(analysis.pure)
mode = os.environ.get("MINGJING_BUILD_MODE", "onefile").lower()
console = os.environ.get("MINGJING_BUILD_CONSOLE", "0") == "1"
if mode == "onedir":
    exe = EXE(pyz, analysis.scripts, [], [], [], name="MingJing", debug=False,
              bootloader_ignore_signals=False, strip=False, upx=False, console=console,
              disable_windowed_traceback=False, argv_emulation=False)
    coll = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name="MingJing")
else:
    exe = EXE(pyz, analysis.scripts, analysis.binaries, analysis.datas, [], name="MingJing", debug=False,
              bootloader_ignore_signals=False, strip=False, upx=False, console=console,
              disable_windowed_traceback=False, argv_emulation=False)
