# 明镜开发约定

唯一当前工程是仓库根 `Light_Mirror`，业务版本为 `1.0.0.dev3`。旧工程、旧Git worktree与旧发行包只在工作区归档目录保存，不加入模块搜索路径。本轮已发布的EXE保持冻结构建字节；后续源码或文档修改不自动视为该EXE的新验收结果。

从仓库根启动开发服务：

```powershell
$env:MINGJING_HOME = 'D:\MingJingDevProfile'
python -X utf8 -m mingjing.server --host 127.0.0.1 --port 8765 --no-browser
```

首页为上传工作台；原生Harness高级界面为 `/harness`，`/classic` 保留兼容。使用隔离profile，避免改动真实用户DPAPI、配置和运行历史。模型与金融工具按真实保存路由执行，不加载上游任意Shell的完整profile。

源码、规则、UI与运行时来源放在原有英文目录。项目文档只维护 `docs/project/` 的原件，当前使用说明在 `docs/`；原始验收留在 `evidence/`，新构建与交付写入 `artifacts/`，私有原件及本地工作区放在 `data/`。不把缓存、依赖树、个人配置或模型响应推送到GitHub。

先用真实片段及相邻反例关闭具体问题，再运行受影响测试；实际HTTP、浏览器、真实API、fixture及独立人工评测分别登记。只有功能稳定且准备发布新候选时才重新构建EXE。前端只在修改后运行typecheck和增量构建；锁定依赖未变时不重复安装。

```powershell
python -X utf8 -m pytest tests/test_core_review.py tests/test_public_export.py -q
python -X utf8 -m mingjing.cli rules check
python -X utf8 -m scripts.verify_harness_ui_sources --help
```

完整原生开发还需同版 `runtime/finance-carrier/staged`。公开源码不带Node与carrier二进制；重建说明见 [REBUILD_SOURCE.md](../scripts/REBUILD_SOURCE.md)。≤30MB核心包仅用于审阅，不是完整原生工程。

任务分工见 [开发任务手册](PROJECT_TASK_MANUAL.md)，真实结果与当前问题见 [验收说明](acceptance/README.md)，操作与构建分别见 [RUNBOOK.md](RUNBOOK.md)、[EXE_DELIVERY.md](EXE_DELIVERY.md)。旧编号开发记录保持原文，不作为当前操作指令。
