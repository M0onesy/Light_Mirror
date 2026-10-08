# 明镜源码开发与运行手册

普通用户双击对应阶段的 `MingJing.exe`，详细Web操作见 [USER_GUIDE.md](USER_GUIDE.md)。本页用于当前工程源码运行、验证和阶段构建；开发根是 `mainProj/mingjing-next`，GitHub导出后为仓库根。

## 1. 环境与来源

应用开发使用 Windows x64 和 Python 3.12，锁定依赖记录在 `requirements-build.lock` / `.json`。前端修改使用其 `package-lock.json`；重建Harness使用 `vendor/harness` 的源快照及pnpm锁。不要合并旧 `mainProj/mingjing` 包或依赖隐式PYTHONPATH。

```powershell
Set-Location -LiteralPath D:\Competition\金融人工智能竞赛\mainProj\mingjing-next
python -m pip install -r requirements-build.lock
python -c "import mingjing; print(mingjing.__file__)"
python -m mingjing.cli rules check
python -m mingjing.launcher
```

启动器检查实际模块来源。默认本地监听127.0.0.1、自动端口并打开浏览器，退出使用Web按钮。程序读取当前用户配置；诊断时使用隔离 `MINGJING_HOME`，不得覆盖用户已有模型和密钥。

## 2. 最小离线闭环

使用虚构样例核查、查看证据并重放：

```powershell
python -m mingjing.cli check --draft samples/demo_draft_defined.md --corpus samples/corpus --report-date 2025-03-31 --out runs/demo --mode kernel
python -m mingjing.cli replay --run runs/demo
```

输出包括陈述、事实、JSON结果、Markdown/HTML报告、审计、会话和manifest。CLI页级证据查询需要同时指定run：

```powershell
python -m mingjing.cli evidence show "<从results.json复制的evidence_id>" --run runs/demo
```

财报分析可在Web选择入口，或CLI使用 `--task-type analysis`、公司、期间与对应事实材料。切勿用纯算术相容证明外部事实正确。

## 3. 原生运行时与前端

默认上游来源为 `vendor/harness`；显式 `--upstream` 也必须通过锁定快照哈希。载体包含自己的Node及必要依赖，不能依靠开发机Node代替运行验证。

```powershell
python -m scripts.verify_harness_ui_sources
python -m scripts.plan_finance_runtime
python -m scripts.verify_staged_finance_runtime --help
```

完整stage/rebuild命令见 [REBUILD_SOURCE.md](../scripts/REBUILD_SOURCE.md)。已有同版本 `runtime/finance-carrier/staged` 时，不例行重新stage或解包。修改金融插件后只同步插件并重新验证相应运行时锁。

前端必要修改后：

```powershell
Set-Location -LiteralPath frontend
npm ci
npm run typecheck
npm run test
Set-Location -LiteralPath ..
python -m scripts.build_frontend
```

源码和静态资源由build receipt关联；只有必要时才增量构建，不用旧静态页冒充新UI。

## 4. 验证顺序

先定位原文、材料、口径和定义，再用小样本修复。增加对应反例与相邻边界，执行受影响模块测试；不要因为一次大量弃权就盲跑全文或增加Token。单次功能稳定后再执行相关集成和全量回归。

```powershell
python -m pytest tests/test_source_snapshot.py -q
python -m pytest tests/test_core_review.py -q
```

具体金融回归按问题附录中的测试选择；全量回归命令为 `python -m pytest tests -q`，仅在发布稳定点执行。真实API实测必须沿用用户已保存配置，不切模型、不写明文Token。fixture、真人金标和真实供应商分别登记。

处理完成率、有效判定覆盖率、弃权率、待处理数分别计算。旧全文成绩与局部修复证据不能加减混算。负基数、零基数、未知公告日期、横线和方法缺失保持合法阻断；E2/E3/E9须有来源维度或版本证据，不按金额猜口径。

## 5. 历史、恢复和重放

冻结run只读；补证、口径澄清、追加材料和重新核查均建新版本。取消后恢复前校验冻结材料、配置、内核、插件和运行时；旧锁保持原字节。kernel replay重算数字；agent replay缓存驱动真实工具循环；消息级重放独立检查输入、引用和轨迹。

CLI `replay` 为内核重放，Agent与消息重放入口在Web。缓存缺失、不兼容或源文件变化要明确失败，不联网补全。分享重放记录按 [DELIVERY_GUIDE.md](DELIVERY_GUIDE.md) 提供实际材料闭包。

## 6. 阶段收尾

清理采用已批准清单，保护Git common-dir、材料、旧锁、父子任务、补证、许可证和可重放证据。普通日志数量/天数设置当前不自动删除运行依赖。

核心审阅包生成：

```powershell
python -m scripts.create_core_review
```

阶段EXE只在源码、规则、前端、载体和文档稳定后构建一次，具体命令与smoke检查见 [EXE_DELIVERY.md](EXE_DELIVERY.md)。本轮阶段候选仍为 `1.0.0.dev2`，以新构建哈希区分，不提前标记正式v1.0。

默认路径及用户排障详见 [USER_GUIDE.md](USER_GUIDE.md)。专业签发、独立真人金标、新保留集与独立Windows验收见 [NATIVE_AGENT_ACCEPTANCE.md](NATIVE_AGENT_ACCEPTANCE.md)、[PROFESSIONAL_REVIEW.md](PROFESSIONAL_REVIEW.md)；每个闸门须有对应实际记录。
