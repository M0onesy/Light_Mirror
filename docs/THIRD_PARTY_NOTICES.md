# Third-party notices / 第三方组件

Python 3.12.4 — Python Software Foundation License. https://docs.python.org/3/license.html

明镜自有代码按根目录 LICENSE 的 AGPL-3.0-only 发布，详见 docs/LICENSE_GUIDE.md；第三方原许可保持不变。
本程序嵌入 PyMuPDF/MuPDF、复用官方 Agent Loop 的金融 Harness carrier 及其锁定生产依赖。
DeepSeek Harness 框架与模型供应商独立；接入 API 的服务条款由配置供应商规定。
PyMuPDF/MuPDF 提供 AGPL v3 / Artifex 商业许可选择，原许可条件保持不变。
PyInstaller 的 GPL 许可含 bootloader distribution exception；它属于构建工具。

下表来自已安装构建/运行依赖闭包，不等于逐字节 EXE 组件审计。版本由实际 metadata 生成。

| Python component | Version | Upstream license metadata |
| --- | --- | --- |
| altgraph | 0.17.5 | MIT |
| annotated-types | 0.6.0 | See upstream license files |
| anyio | 4.15.1 | MIT |
| attrs | 23.1.0 | MIT |
| cffi | 1.16.0 | MIT |
| click | 8.1.7 | BSD-3-Clause |
| colorama | 0.4.6 | BSD-3-Clause |
| cryptography | 42.0.5 | Apache-2.0 OR BSD-3-Clause |
| h11 | 0.16.0 | MIT |
| httpcore2 | 2.13.1 | BSD-3-Clause |
| httpx2 | 2.13.1 | BSD-3-Clause |
| idna | 3.20 | BSD-3-Clause |
| jsonschema | 4.26.0 | MIT |
| jsonschema-specifications | 2023.7.1 | MIT |
| mcp | 2.3.0 | MIT |
| mcp-types | 2.3.0 | MIT |
| opentelemetry-api | 1.45.0 | Apache-2.0 |
| packaging | 23.2 | See upstream license files |
| pefile | 2024.8.26 | MIT |
| pycparser | 2.21 | BSD |
| pydantic | 2.13.5 | MIT |
| pydantic_core | 2.46.5 | MIT |
| pyinstaller | 6.22.3 | GPLv2-or-later with a special exception which allows to use PyInstaller to build and distribute non-free programs (including commercial ones) |
| pyinstaller-hooks-contrib | 2026.8 | See upstream license files |
| PyJWT | 2.15.1 | MIT |
| pymupdf | 1.28.0 | Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License |
| python-multipart | 0.0.32 | Apache-2.0 |
| pywin32 | 312 | PSF |
| pywin32-ctypes | 0.2.2 | BSD-3-Clause |
| referencing | 0.30.2 | MIT |
| rpds-py | 2026.9.1 | MIT |
| setuptools | 69.5.1 | See upstream license files |
| sse-starlette | 3.5.0 | BSD-3-Clause |
| starlette | 1.7.0 | BSD-3-Clause |
| truststore | 0.10.4 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| typing-inspection | 0.4.4 | MIT |
| uvicorn | 0.54.0 | BSD-3-Clause |

已安装包附带的原始许可文件复制至 `docs/licenses/python/`；完整清单及 SHA-256 见 `docs/licenses/index.json`。

官方 Harness、内嵌 Node 与金融插件依赖的 npm/native 闭包许可按上游文件保留在 `runtime/finance-carrier` 资产中；
这些文件受同一 runtime manifest 哈希约束，构建/交付不会改写其许可。

Harness production manifest format: 1; declared upstream license files: 122.

不打入运行历史、个人 Token、真实研报、约 8.9GB 外部语料或 runtime/tooling。演示 PDF 为本项目虚构生成。

本文记录组件和原始许可来源，不声称已取得 Artifex 商业许可。
