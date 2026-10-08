# 明镜 Windows 单EXE阶段候选交付

当前版本为 **1.0.0.dev2 阶段候选**。本轮EXE须有自身构建哈希、测试回执和对应源码，历史RC收据不能覆盖当前修复。正式v1.0仍须全部金融与独立环境闸门通过。

## 1. 用户入口与内嵌资源

`MingJing.exe`是唯一启动入口：双击启动本地127.0.0.1服务并打开默认浏览器，配置、任务、分析、追问、补证、恢复、历史、重放和导出均在Harness Web完成。用户无须安装Python、Node或pnpm。

EXE包含Python运行环境、PyMuPDF、官方MCP及schema依赖、规则包、静态Web、虚构演示、项目LICENSE和已验证的金融Harness载体。载体保留上游Agent Loop和独立Node，其完整文件清单由 `runtime/finance-carrier/staged/carrier_manifest.json` 锁定；它不是旧完整SDK/Office运行时。开发node_modules、全量真实PDF、runs、模型缓存和个人配置不内嵌。

首次使用按资源哈希发布到用户应用缓存；必要文件校验和原子发布防止半成品。缓存、报告、证据和临时文件仍占用用户磁盘，因此“单EXE入口”不表示运行时永不产生文件。大型财报保持外部引用。

默认离线内核模式；原生Harness作为可选语义层，金融数字由明镜内核产生。可选DeepSeek、Kimi、GPT或兼容端点；实际供应商能力以真实工具闭环收据为准。API/原生故障明确降级，默认不把材料静默发给另一供应商。

## 2. 配置与进程行为

配置位于 `%LOCALAPPDATA%\MingJing\config.json`，密钥使用当前Windows用户DPAPI，导出只含引用。换电脑或用户需重新填写Token。任务冻结revision、角色路由和材料，后续设置不改变在跑任务。

用户数据目录文件锁防止重复实例；重复双击打开已有页面。关闭浏览器不终止服务，退出使用Web按钮。默认自动端口；监听和鉴权在实际绑定处再次校验。阶段开发保留运行历史，不自动按数量/天数删除父子依赖。

普通系统TEMP路径为测试基准。过深TEMP/TMP可能在PyInstaller进入应用前解包失败，应使用短目录；不能把开发机受限PATH测试描述成独立干净Windows已通过。

## 3. 构建与验收回执

唯一应用打包入口是 `scripts/build_exe.ps1`，默认onefile和无控制台。源码、前端来源回执、依赖锁、上游来源、载体manifest及许可先验证，再生成构建输入锁。构建后重新核对全部输入，变化时拒绝登记。

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_exe.ps1 -Mode onefile -Clean -NoInstall
python -m scripts.delivery_smoke --exe dist/MingJing.exe --out runs/stage-exe-smoke
```

`dist/MingJing-build.json`记录版本、EXE SHA-256、大小、源代码和资源输入哈希。smoke在隔离profile和受限PATH中检查无需外部Python/Node、中文目录、离线演示、PDF能力、API故障降级、配置保存、历史和退出。新增真实API验收必须指向同一EXE哈希，不覆盖用户配置或密钥。

对应源码可通过GitHub准确commit或同版本完整源码归档交付；构建方法见 [REBUILD_SOURCE.md](../scripts/REBUILD_SOURCE.md)。未提供凭据的供应商真实测试记not_executed。人工金标、独立D3和独立Windows记录缺失时不能宣称正式1.0。

## 4. 建议交付布局

```text
MingJing-stage/
  MingJing.exe
  MingJing-build.json
  SHA256SUMS.txt
  README.md
  docs/USER_GUIDE.md
  docs/DELIVERY_GUIDE.md
  LICENSE
  docs/THIRD_PARTY_NOTICES.md
  docs/licenses/
  samples/
  acceptance/
  SOURCE.md                         对应源码commit/归档与哈希
```

阶段文件夹首屏明确说明候选边界；ZIP自身SHA-256保存包外，避免循环变更包内清单。用户仅启动EXE，其余文件用于说明、许可和复核。完整对应源码需独立提供，不能用30MB审阅包替代。

正常使用见 [USER_GUIDE.md](USER_GUIDE.md)，GitHub、完整拷贝和轻量审阅见 [DELIVERY_GUIDE.md](DELIVERY_GUIDE.md)，许可见 [LICENSE_GUIDE.md](LICENSE_GUIDE.md)。
