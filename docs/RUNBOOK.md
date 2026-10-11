# 明镜 1.0.0.dev3 源码运行与验证

唯一工程为仓库根 `Light_Mirror`；文档原件在 `docs/project/`，当前验收说明在 `docs/acceptance/`，成品在 `artifacts/`，本地原始证据在 `evidence/`，材料在 `data/`。原融合包、旧源码快照、失败和成功收据已归档只读，不通过PYTHONPATH拼接旧后端。

## 1. 环境与启动

```powershell
Set-Location -LiteralPath D:\Competition\金融人工智能竞赛\Light_Mirror
python -m pip install -r requirements-build.lock
python -c "import mingjing; print(mingjing.__file__)"
python -m mingjing.cli rules check
python -m mingjing.launcher
```

依赖安装以已锁文件为准；应用使用Python 3.12，前端使用其package-lock，上游使用vendor/harness源码与pnpm锁。启动默认本机服务与上传首页。使用隔离MINGJING_HOME验证，不改变真实用户配置/DPAPI/模型。原生任务还需同版finance-carrier及manifest闭包，审阅包单独不提供完整原生运行。

## 2. 离线与在线入口

```powershell
python -m mingjing.cli smart run samples/smart/*.pdf --out data/workspace/dev3-smart
python -m mingjing.cli check --draft samples/demo_draft_defined.md --corpus samples/corpus --report-date 2025-03-31 --out data/workspace/dev3-kernel --mode kernel
python -m mingjing.cli replay --run data/workspace/dev3-kernel
```

上传工作台的在线模式须经过原生Loop和统一核查工具。核查、分析与知识库会话使用冻结资源，追问/补证/澄清建新版本。纯内核或历史smart轻量重算分别标模式和实际比较范围。标准条件核查与审慎来源要求遵循任务手册，不能以同值命中扩大方法授权。

冻结分类中的纯预测输入、行情输入只有在可信内核同样判为跳过时，才按规则排除并免去A/B绑定；全部原文位置和排除证明仍入账本，不计公司历史verified。有独立操作数的预测内部勾稽及历史未知项继续执行原生工具。恢复检查点不能用排除证明授予核实；证明、状态或输入不符时拒绝排除授权，按正常绑定条件保持X3或待处理。

财报分析明确指定合并/母公司口径，严格策略未声明scope不生成指标格、checks或signals。核对原文发行人与精确证券代码；未冻结的新旧代码映射、后三位相同或PDF创建时间都不能补足主体/公告日准入。

## 3. 实施及验证顺序

冻结输入/源码与问题→最小修复及完整阻断链→原披露正反例→受影响定向测试→实际HTTP/浏览器→必要真实供应商→同版稳定点全量。不能反复全文/打包消除skip或堆计数。

```powershell
python -m pytest tests/test_source_snapshot.py tests/test_core_review.py -q
python -m pytest tests/test_smart_pipeline.py tests/test_smart_web_ui.py -q
python -m pytest tests -q
```

记录实际运行器、依赖、全部pass/fail/error/skip/timeout及原因；浏览器、CSP、真实API和mock分别声明。源码适配、HTTP成功不等于原生多工具能力。DeepSeek/Kimi/GPT/自定义按实际保存路由分别验收，缺凭据not_executed，不能自动切换用户配置。

fitz/poppler/plumber按MINGJING_PDF_BACKEND强制对照：读取完整真实原格，目检原文及证据区域。未来/未知日期、同公司重名、哈希篡改、横线、单位、重述和金额巧合都有相邻反例。标准与审慎单独报告假设和误纳；生成样例结果不能证明真实召回。

## 4. 原生载体与前端

```powershell
python -m scripts.verify_harness_ui_sources
python -m scripts.plan_finance_runtime
python -m scripts.verify_staged_finance_runtime --help
```

同版载体已有时先验证，不例行重stage；修改插件和UI后更新相应来源/静态输入锁。必要前端构建使用npm ci/typecheck/test及项目build_frontend，实际源码到静态资源收据必须覆盖新增首页/会话代码。来源与重建见完整源码中的`scripts/REBUILD_SOURCE.md`。

## 5. 冻结、重放与交付

记录原件、识别元数据、后端、注册/规则、源码、配置、插件、运行时和UI锁。重放先验输入输出，核查全部数字/公式/证据/假设/检查，Agent和消息重放比实际缓存工具轨迹；缺缓存、旧版不兼容或篡改失败且零外连。

源码/资源/文档稳定后再生成审阅包和一次同版EXE候选，构建回执与smoke指向精确EXE哈希。完整原生运行/复现闭包与≤30MB审阅包分开，ZIP哈希放包外；正式v1.0需独立质量及环境闸门。见 [PROJECT_TASK_MANUAL.md](PROJECT_TASK_MANUAL.md)、[EXE_DELIVERY.md](EXE_DELIVERY.md)。

本版真实小样本、全文技术运行、全量回归与同EXE烟测分别查本地 `evidence/dev3/` 中的 `real-small-*、real-full-*、full-regression、dev3-exe-final` 原始收据；文件状态与版本锁决定实际执行结论。原收据路径通过 `evidence/relocation/` 解释，不改写旧锁。真实全文技术测试与全文金融质量目标分开，真人金标、D3、Kimi/GPT及独立Windows未有收据则保持未执行或未认证。公开摘要见 [acceptance/README.md](acceptance/README.md)。

生成核心审阅包使用 `python -X utf8 -m scripts.create_core_review`，默认写入 `artifacts/core-review/` 并读取 `docs/project/` 的唯一基线。源码、候选ZIP与回归工具的默认输出均留在 `artifacts/` 或 `evidence/`；不要向工作区根散放JSON、ZIP或校验文件。

冻结任务搬迁后使用显式 `--relocation-map evidence/relocation/location-map.json --out evidence/relocation/<新目录> --replay-mode kernel`，不改旧锁。Agent与消息模式、路径拒绝条件及完整示例见 [WORKSPACE_LAYOUT.md](WORKSPACE_LAYOUT.md)。通过位置适配不意味着partial任务已完成或金融质量通过。

## 6. Git原字节与锁定快照

Harness快照、规则和静态资源按SHA-256登记，Windows换行转换会导致来源验证失败。第三方目录自己的 `.gitattributes` 可能覆盖根属性；不要修改该快照文件来绕过锁。协作前设置 `git config core.autocrlf false`，并在本地 `.git/info/attributes` 写入 `* -text`，使该本地最高优先级规则保留工作区原字节。

新的协作clone可先不展开，再设置本地规则：

```powershell
git clone --no-checkout https://github.com/M0onesy/Light_Mirror.git Light_Mirror
Set-Location -LiteralPath Light_Mirror
git config core.autocrlf false
[IO.File]::WriteAllText((Join-Path (Get-Location) '.git/info/attributes'), "* -text`n", [Text.UTF8Encoding]::new($false))
git checkout main
python -X utf8 -m scripts.upstream_source
```

这条本地规则不进入Git历史。提交前按逐文件SHA核对工作树与Git暂存blob，来源检查通过后再提交；禁止靠重新写入manifest或换行后的哈希冒充锁定快照。完整公开源码收据也只证明文件与来源一致，不代表金融质量通过。
