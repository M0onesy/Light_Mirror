# 明镜交付、GitHub协作与完整拷贝指南

三种交付对应不同用途：GitHub提供可协作的源码；完整拷贝提供开发或重放所需闭包；30MB以内核心包供审阅修改。普通用户只需对应阶段的 `MingJing.exe`，操作见 [USER_GUIDE.md](USER_GUIDE.md)。

## 1. 发布到 GitHub 并协作

项目地址：<https://github.com/M0onesy/Light_Mirror>。公开仓库导出当前工程源码、规则、插件、测试、文档、虚构样例与锁定Harness源码。发布使用独立干净Git目录，原共享Git common-dir和旧开发历史保持本地保存。

这样做不会把以前提交的大文件带到新远端历史；它也不会删除原工作树中的历史证据。首次发布前检查远端是否为空及当前账号推送权限。若远端已有分支或其他人的提交，先合并现有内容，不执行未经审查的强推。

公开仓库不包含真实财报/研报、个人配置、DPAPI文件、Token、runs、node_modules、二进制载体、EXE或PyInstaller产物。真实模型响应和会话缓存也不公开。GitHub源码可以安装锁定依赖并运行离线内核；完整原生开发还需按来源锁构建金融运行时或取得对应受许可的载体。EXE可单独通过GitHub Releases发布，不提交进Git历史。

协作者安装Git与Python 3.12，使用短目录克隆：

```powershell
git clone https://github.com/M0onesy/Light_Mirror.git D:\MJ\Light_Mirror
Set-Location -LiteralPath D:\MJ\Light_Mirror
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.lock
.\.venv\Scripts\python.exe -m mingjing.cli rules check
.\.venv\Scripts\python.exe -m mingjing.cli check --draft samples/demo_draft_defined.md --corpus samples/corpus --report-date 2025-03-31 --out runs/my-demo --mode kernel
```

开发通过分支和PR协作。改动时先复现小样本再加相邻反例；不要提交本机配置或真实响应。提交前检查 `git diff --cached --stat` 和 `git status --short`。金融规则变更须同步规则锁、测试和方法定义，旧锁不升级成新规则。

`.gitignore` 只能防止未来新增，不能移除已有历史大对象。若有人已经误传大文件，应先冻结推送并备份所有分支，使用隔离镜像和 `git filter-repo` 删除指定路径/对象，检查所有分支和标签，再与协作者约定迁移。清除远端历史会改变commit，必须重新克隆；不在当前共享工作树直接重写历史。泄露过的Token应在供应商处撤销并重发，仅删Git文件无效。

项目采用AGPL-3.0-only覆盖自有代码；第三方组件保留各自许可。分发EXE时应一并提供对应版本源码和许可，详情见 [LICENSE_GUIDE.md](LICENSE_GUIDE.md)。

## 2. 完整拷贝给另一个人

### 2.1 只使用程序

提供 `MingJing.exe`、构建/验收回执、SHA-256、使用手册、许可及虚构样例。对方双击EXE，在Web配置自己的材料路径、模型和Token。不要把整个20GB工作区发送给普通用户。

### 2.2 开发当前工程

保留以下内容，并采用单一工程根；不要把旧 `mingjing` Python后端与新界面混合：

```text
mingjing-next/
  mingjing/ kernel/ legacy_tools/ rulepack/
  scripts/ tests/ bench/ docs/ frontend/ web/ samples/
  vendor/harness/                    锁定上游源码与许可
  runtime/harness/                   来源与资源清单
  runtime/finance-plugin/            金融插件源码
  runtime/finance-carrier/           独立金融载体及manifest
  pyproject.toml requirements-build.* MingJing.spec LICENSE
```

Python应用依赖按 `requirements-build.lock` 安装。需要修改前端时在 `frontend` 中运行 `npm ci`，再由项目脚本构建；需要重建上游时使用其锁定的pnpm工具链。依赖不要求完整复制现有node_modules，可重新安装。

完整开发拷贝可以附带原生载体以减少重建，但载体必须与当前manifest、插件、许可一致。复制后验证运行时，不用旧收据替代新电脑的启动测试。

```powershell
Set-Location -LiteralPath D:\MJ\mingjing-next
python -m pip install -r requirements-build.lock
python -m mingjing.cli rules check
python -m mingjing.launcher
```

在Web检查PDF、模型与Harness能力后，再跑自己的小样本。构建方式见 [REBUILD_SOURCE.md](../scripts/REBUILD_SOURCE.md)。

### 2.3 复现指定历史任务

另外复制指定run及其 `run_manifest.json` / 资源锁列出的实际材料、父任务、补证包、会话消息、模型缓存、工具轨迹、规则和源码版本。外部语料不在普通运行导出中自动全量复制；不要只发送 `results.json` 就声称可以重放。

先核对逐文件SHA-256，再按原版本执行kernel或Agent重放。历史锁中的绝对路径和真实字节保持不变；路径不兼容时用受控材料映射或在新任务中导入，不篡改旧锁指纹伪造重放。若某版本未保存源码或缓存闭包，如实记录无法完整恢复。

跨电脑或用户不复制 `%LOCALAPPDATA%\MingJing\secrets.json`；其DPAPI密文与原Windows用户绑定。配置可以从Web导出，再在新电脑导入、校验路径并重新填写Token。提供原始报告前先核对再分发权限。

## 3. ≤30MB核心审阅包

工作区根目录中的 `MingJing-core-review/` 和同名ZIP用于评审、指导及修改核心文件。它包含金融内核、规则、插件、Web源码/静态资源、测试、评测代码、虚构样例、需求基线、文档、依赖锁和来源清单。

它不包含完整Harness源码、carrier、Python/Node/npm依赖、真实PDF、运行历史、.git、个人配置或密钥，因此不是可独立运行的完整原生Agent。审阅者应结合README中的边界阅读；修改核心后提供补丁或在完整GitHub仓库开PR。

当前工程根执行：

```powershell
python -m scripts.create_core_review
```

脚本以严格白名单复制，扫描已配置明文密钥及私钥块，登记逐文件大小和SHA-256，并重新打开ZIP核对所有成员。压缩上限采用30,000,000字节；超过即失败，不静默删源码降体积。它拒绝覆盖现有交付物；需要新版时指定新目录：

```powershell
python -m scripts.create_core_review --out D:\Competition\金融人工智能竞赛\MingJing-core-review-v2
```

默认需求文件取当前工程的上级目录；从GitHub根运行时可用 `--requirements-dir` 指向两份中文需求文件所在位置。脚本输出同名ZIP及 `*-receipt.json`，其中记录ZIP SHA-256和真实压缩大小。清单不包含Token值。

审阅包保留完整引用清单，但未打入的大材料不会变成包内证据。若指导者需要某页真实案例，应另外提供经许可的材料摘页及对应证据锁，不修改核心包白名单混入私人报告。

## 4. 交付前核对

- EXE、源码、规则、插件、前端和运行时是否属于同一冻结版本。
- 文件SHA-256与构建/交付回执是否一致。
- 交付方式是否满足对方目的：使用、开发、重放还是审阅。
- 公开Git历史与包内容中是否存在大文件、密钥、个人配置或真实响应。
- 第三方许可与对应源码是否完整提供。
- 阶段候选与正式金融验收是否准确标注；没有实际收据的项目不写成通过。

清理和打包收据应与交付物保存。移除旧版本时先保护材料、Git common-dir、父子任务、补证和可重放记录，再执行有明确范围的清理。
