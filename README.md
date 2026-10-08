# 明镜 MingJing 金融 Agent

明镜提供研报纠错核查、财报分析和材料追问。它复用锁定的 DeepSeek Harness 原生 Agent Loop，由模型调用受控金融工具完成检索和语义绑定；事实、公式、金额和判定由明镜自己的 Decimal 内核产生。可选择 DeepSeek、Kimi、OpenAI GPT 或兼容服务，模型不绑定于框架供应商。

Windows用户以 `MingJing.exe` 为唯一启动入口，在本地Harness Web设置材料、密钥、模型、角色与预算，查看原文、证据、公式、补证、恢复、重放和导出。默认纯内核离线演示，大型财报保留外部引用，API密钥使用当前Windows用户DPAPI保存。

当前版本为 **1.0.0.dev2 阶段候选**。阶段包以自身构建哈希及测试回执为准，不替代完整金融金标、独立保留集或干净Windows验收；历史RC程序不代表当前源码。

## 从哪里开始

- 普通用户：[详细使用手册](docs/USER_GUIDE.md)
- GitHub协作、完整项目拷贝、30MB核心审阅：[交付指南](docs/DELIVERY_GUIDE.md)
- 源码启动和针对性验证：[开发运行手册](docs/RUNBOOK.md)
- 单EXE构建：[重建说明](scripts/REBUILD_SOURCE.md)及[EXE交付说明](docs/EXE_DELIVERY.md)
- 架构与闸门：[原生Agent说明](docs/NATIVE_AGENT_ACCEPTANCE.md)
- 当前专业问题与局部证据：[小样本记录](docs/ABSTENTION_SMALL_SAMPLE.md)、[专业审阅](docs/PROFESSIONAL_REVIEW.md)

当前工程根为 `mainProj/mingjing-next`；公开GitHub导出后它本身就是仓库根。旧工程和历史锁保持独立，程序拒绝混用模块来源。

## 源码快速体验

开发者使用Python 3.12，在当前工程根安装锁定依赖：

```powershell
python -m pip install -r requirements-build.lock
python -m mingjing.cli rules check
python -m mingjing.cli check --draft samples/demo_draft_defined.md --corpus samples/corpus --report-date 2025-03-31 --out runs/demo --mode kernel
python -m mingjing.cli replay --run runs/demo
python -m mingjing.launcher
```

原生Harness模式另外需要同版本金融载体，公开源码仓库不携带二进制载体或node_modules。重建流程见[重建说明](scripts/REBUILD_SOURCE.md)，供应商设置与真实工具闭环测试均在Web完成。默认失败后降级到纯内核，不静默把材料发送给另一供应商。

所有原文出现位置保留在覆盖账本；已核实、有误、弃权、待处理、范围外与规则排除分别展示。处理完成率不等于金融有效判定覆盖率，局部小样本通过也不等于全文成绩。金额精度、口径、披露时点和方法定义均须有来源。

## 许可与数据边界

明镜自有代码采用 [AGPL-3.0-only](LICENSE)。第三方组件保留各自许可，说明见 [LICENSE_GUIDE.md](docs/LICENSE_GUIDE.md) 和 [THIRD_PARTY_NOTICES.md](docs/THIRD_PARTY_NOTICES.md)。虚构演示样例随源码提供；真实报告、运行历史、API响应缓存、个人配置、Token和约8GB外部财报不进入公开仓库。

协作地址：<https://github.com/M0onesy/Light_Mirror>。请以分支和PR提交可复现的小样本修复，保护历史资源锁与材料闭包。
