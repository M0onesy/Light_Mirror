"""检查（必要时安装）明镜运行所需的 Python 版本与第三方依赖。

被「启动明镜.cmd」调用，因此本文件**只能依赖标准库** —— 它必须在依赖装好之前就能跑。

退出码：
    0  运行环境就绪
    2  Python 版本低于要求
    3  依赖缺失或安装失败

用法：
    python scripts/ensure_runtime_deps.py              # 检查，缺失则自动安装
    python scripts/ensure_runtime_deps.py --check-only # 只检查，不安装
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys

MIN_PYTHON = (3, 12)

# (用于检测的导入名, 安装时使用的包名/版本约束, 缺失时的用途说明)
REQUIREMENTS: tuple[tuple[str, str, str], ...] = (
    ("mcp", "mcp==2.3.0", "Agent 工具协议（高级界面的工具闭环）"),
    ("jsonschema", "jsonschema>=4.20,<5", "事实与配置的结构校验"),
    ("pymupdf", "PyMuPDF>=1.24", "PDF 解析（读研报与财报）"),
)

# 同一个包可能有新旧两个导入名，任一可用即视为已安装
IMPORT_ALIASES: dict[str, tuple[str, ...]] = {
    "pymupdf": ("pymupdf", "fitz"),
}


def _module_available(name: str) -> bool:
    for candidate in IMPORT_ALIASES.get(name, (name,)):
        try:
            if importlib.util.find_spec(candidate) is not None:
                return True
        except (ImportError, ValueError):
            continue
    return False


def main(argv: list[str]) -> int:
    check_only = "--check-only" in argv

    version = sys.version_info
    print(f"Python  {version.major}.{version.minor}.{version.micro}  ({sys.executable})")
    if (version.major, version.minor) < MIN_PYTHON:
        print()
        print(f"[错误] 需要 Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} 或更高版本，当前为 "
              f"{version.major}.{version.minor}。")
        print("       请安装较新的 Python 后重新双击启动脚本。")
        return 2

    missing = [req for req in REQUIREMENTS if not _module_available(req[0])]
    if not missing:
        print("依赖    mcp / jsonschema / PyMuPDF 均已就绪")
        return 0

    print()
    print("以下运行依赖尚未安装：")
    for name, spec, purpose in missing:
        print(f"  - {spec:<24} {purpose}")

    if check_only:
        return 3

    print()
    print("正在自动安装（首次运行需要联网，可能需要一两分钟）...")
    print("-" * 60)
    command = [sys.executable, "-m", "pip", "install", *[spec for _, spec, _ in missing]]
    print(" ".join(command))
    print("-" * 60)
    try:
        completed = subprocess.run(command, check=False)
    except OSError as error:
        print(f"[错误] 无法调用 pip：{error}")
        print("       请确认 Python 安装完整（含 pip），或手工执行上面的安装命令。")
        return 3
    if completed.returncode != 0:
        print()
        print(f"[错误] 依赖安装失败（pip 退出码 {completed.returncode}）。")
        print("       可手工执行上面的命令查看详细报错；若在网络受限的环境，")
        print("       请配置镜像源后重试，例如：")
        print("       pip install -i https://pypi.tuna.tsinghua.edu.cn/simple "
              + " ".join(spec for _, spec, _ in missing))
        return 3

    # 安装后复查，避免"装完仍不可导入"（例如装到了另一个解释器下）
    still_missing = [name for name, _, _ in missing if not _module_available(name)]
    if still_missing:
        print()
        print(f"[错误] 安装已执行，但仍无法导入：{', '.join(still_missing)}")
        print("       常见原因是 pip 装到了另一个 Python。请确认上面的 sys.executable "
              "与安装目标是同一个解释器。")
        return 3

    print()
    print("依赖安装完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
