# 金融运行时闭包开发记录

本页记录从完整官方运行时收窄为独立金融载体的工程过程。当前应用已经接入 `runtime/finance-carrier/staged`，保留上游原生 Agent Loop；本轮阶段收尾构建单 EXE，历史 RC1 资产和旧收据保持只读。独立 Windows 环境与正式金融质量验收仍须分别登记。

官方完整 Python 运行时以 `@deepseek-ai/dsh` 为依赖根。CLI 同时登记多个 profile，递归带入 Office、终端等包，原 bootstrap 在 Windows 启动还解析 ACL runner。明镜没有直接删除这些原文件，而是在单独载体中限制可达入口、验证金融profile依赖闭包并保留必要原始许可。

`python -m scripts.plan_finance_runtime` 读取锁定 `0.1.6-alpha.2` 源快照、实际 Python profile builder、金融插件导入和上游 CLI 启动模块，生成 `runs/runtime-closure-plan/plan.json` 与 `dsh-packaging-overlay.json`。脚本验证使用的源文件及 pnpm lock 哈希，列出依赖、必需 peer、Windows x64 条件排除和未解析项。外部包版本来自当前已安装清单；缺失 optional 包只有在锁文件明确排除当前平台时才排除，不通过包名猜平台。

载体保持上游 `runCli`、profile boot 与 AgentLoop 代码，使用登记的packaging overlay收窄部署依赖。私有入口 `runtime/finance-carrier/bootstrap.mjs` 只接受 `--profile mingjing`，拒绝其他profile、额外patch、插件安装和subprocess/code环境选择器。宿主已使用独立载体的Node与入口，不依靠开发机Node或旧SDK SEA。

闭包图减少的是登记包数量，不能用它推算最终 EXE 体积或冷启动耗时。`baseline_asset_bytes` 来自既有运行时清单，不需要重新读或复制大文件。`candidate_executable_bytes=null` 表示未构建。候选依赖清单通过也不等于动态导入、native addon、资源读取、许可或运行验收通过。

已部署symlink-free金融包树并保留原始notice。当前载体含manifest的逻辑大小约165,926,269字节，具体大小以实际manifest和build receipt为准；这一数值不是EXE体积。闭包fixture与真实小样本分别验证原生工具循环、A/B隔离、取消/恢复及离线重放，最终EXE还需要独立冷启动与同哈希回执。

锁定上游源码现位于 `vendor/harness`，12,066个文件、98,606,848字节，逐项对照 `runtime/harness/source_snapshot.json`。这些是源码快照大小，不含node_modules或载体；未记录上游commit保持null。所有构建脚本默认读取此位置，显式外部来源也必须验证完整哈希。`build_harness_runtime`默认stage金融闭包，只有显式 `--legacy-sea` 才执行历史完整SEA路线。

规划工具需要开发环境的 PyYAML 来解析现有 pnpm lock；它不增加终端用户或 MingJing 应用依赖。

`python -m scripts.probe_finance_carrier --out runs/finance-carrier-probe-03` 在已经存在的上游 Node carrier 上验证金融入口，不复制依赖。只读 Node import hook 拒绝候选清单之外的裸包和解析到未登记包的绝对文件引用；仅将私有入口的 `dsh` 导入定位到现有安装。DeepSeek 协议 fixture 与 `openai-completions` fixture 均完成原生初始化、三个依赖前一步结果的金融工具、四个 Agent step、缓存离线重放及跨进程恢复／取消。它们分别观察到 64／66 个加载包，均在 173 包候选清单内，未加载 Office。证据为 `runs/finance-carrier-probe-03/acceptance.json` 与 `runs/finance-carrier-probe-04/acceptance.json`；这些 keyless fixture 不代表真实供应商 API 验收，也不代表已部署完整 standalone carrier。

`python -m scripts.verify_harness_ui_sources` 检查 Web vendor 的全部文件大小和哈希，逐项对照相同 runtime 源快照与当前上游原文件，并复算 `HarnessFrame` 的确定性 seat 适配。当前原文件 125 个，共 869,934 bytes；适配文件实际为 11,420 bytes，文本与声明转换一致。停靠面板、浮层和 Markdown 渲染的消费者导入均指向已登记的上游模块。源审查不覆盖浏览器交互和产品功能验收。未来执行 source sync 时会在复制前验证全部源文件，并登记源快照、sync 方法和 adapter 输出哈希；此次只验证，不重新 sync 或覆盖 UI。
