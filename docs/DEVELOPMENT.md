# 明镜下一版功能开发

当前版本为 `1.0.0.dev2`，开发目录 `mainProj/mingjing-next`。`mainProj/mingjing` 及已有 RC1 发行文件保留为历史基线。本开发版本尚未正式发行。

按当前产品要求，开发阶段只运行源码、增量构建浏览器静态资产和执行必要测试，不反复构建 EXE、展开发行包或清理旧验收证据。最终功能与稳定性通过后再冻结、制作唯一入口 `MingJing.exe` 和执行干净 Windows 验收。

## 启动功能开发版

在本目录启动后端：

```powershell
$env:MINGJING_HOME = "D:\MingJingDevProfile"
python -m mingjing.server --host 127.0.0.1 --port 8765 --no-browser
```

浏览器打开 `http://127.0.0.1:8765`。隔离配置用于开发测试，避免改动已有用户密钥和运行历史。最终用户无需运行上述开发命令。

默认页面来自 `web/harness`。浏览器模块复用锁定 Harness 的三栏布局、DockController、DockSurface、FloatLayer、MarkdownText、主题和控件；财务任务与配置接到明镜本地宿主。原始 vendor 文件逐项记录 SHA，不直接启用上游带任意 Shell 的完整 profile。

仅在修改前端后运行：

```powershell
cd frontend
npm ci
npm run typecheck
npm run build
```

`npm ci` 只在依赖未安装或锁文件变化时需要。构建结果是离线静态页面，正式程序不依赖 Node、npm 或 CDN。

## 当前验证命令

```powershell
python -m pytest -q
python -m mingjing.cli rules check
python -m scripts.verify_harness_ui_sources --help
python -m scripts.source_agent_acceptance --help
python -m scripts.source_native_reparse_acceptance --help
```

浏览器产品测试覆盖内置核查、证据、配置 revision、分析无需草稿、本地令牌、导出和窄屏；需要开发机已安装 Playwright 浏览器。缺少浏览器或原生 EXE 的测试如实跳过，不能据此宣称干净机验收。

`source_agent_acceptance` 使用已曝光的开发研报、用户保存的默认路由及 DPAPI 密钥，在源码模式执行真实 Harness。它不会替换模型、改写用户配置或复制全量语料。完整研报复验默认独立快照为 512 模型请求、4000 工具调用、2000000 Token、60 分钟；用户保存的默认预算不变。输出单独登记处理完成率、有效判定覆盖率、弃权率、待处理数、数字证据和两种重放。处理完成率不是人工真值召回率。

新任务的机器事实缓存必须带当前提取器依赖指纹和原文件身份。旧缓存缺锁或指纹过期时，宿主只按冻结 `index.csv` 与原 PDF 重解析，不修改旧缓存；缺少可信原源则拒绝准入。目录、直接 JSON／JSONL、CLI 事实索引及 Agent 追加材料使用同一入口。历史重放使用原运行冻结事实，旧锁不更新为新版本。

追问 Token 默认预算为 150000，可在 Harness Web 的“角色与预算”设置。已有明确设置不覆盖，当前任务与会话继续采用冻结快照。真实开发材料的最新功能、测试结果及未完成项见 [DEVELOPMENT_DEV3.md](DEVELOPMENT_DEV3.md)，旧版记录见 [DEVELOPMENT_ACCEPTANCE_DEV2.md](DEVELOPMENT_ACCEPTANCE_DEV2.md)。原生 Agent 局部追检、补证版本、只读原页预览与离线重放见 [LOCAL_REPARSE.md](LOCAL_REPARSE.md)；真实补证开发验收使用独立的 500000 Token 任务快照，保存配置不改。验收脚本的输入预检、事件名称、定义主体与失败收据契约见 [SOURCE_AGENT_ACCEPTANCE.md](SOURCE_AGENT_ACCEPTANCE.md)。

## 轻量化与发布边界

金融载体入口与候选依赖闭包见 [FINANCE_RUNTIME_CLOSURE.md](FINANCE_RUNTIME_CLOSURE.md)。候选在现有 Node 安装上通过真实 Agent Loop 与 fixture 工具测试；物理独立载体、SEA、最终 EXE 体积及干净 Windows 尚未验证。保持旧完整运行时基线，不能通过删除资产来伪造闭包通过。

正式发布前仍需独立真人金融金标与规则签认、未曝光 D3、可用凭据下的 Kimi/GPT 实测及干净 Windows 验收。这些外部条件单独登记，不以 fixture 或开发材料统计代替。
