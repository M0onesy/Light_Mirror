# 明镜 MingJing 1.0.0.dev2

明镜面向研报核查、财报分析和同一材料追问。在线模式真正执行 DeepSeek Harness 原生 Agent Loop，由金融工具连接明镜自己的 Decimal 内核。模型只能提出语义绑定和引用报告槽位，不能提交金额真值、自由公式或判定。

## 使用

当前开发版在`mainProj/mingjing-next`运行`python -m mingjing.launcher`进入本地Harness Web；路径、密钥、供应商、角色模型和预算均可在页面配置。正式交付目标是唯一入口`MingJing.exe`，用户无需安装Python、Node或pnpm。功能与金融质量闸门通过后再制作该候选文件；历史RC EXE不能代表当前源码修复。

先点击“一键运行内置演示”验证纯内核。使用真实材料时选择研报/财报、语料目录、公司、期间及研报日期；配置供应商后选择原生 Harness。连接测试与 Agent 能力测试不同，后者必须完成真实工具往返。

支持 DeepSeek、Kimi、OpenAI 与自定义协议路由。真实 API 证据以收据为准；无密钥的供应商记为 `not_executed`，源码适配或模拟服务不等同真实验收。默认失败后降级到纯内核，不把材料静默发给另一供应商。

## 结果与发布状态

展示原文、三态、X1–X6 弃权原因、A1–A5 提示、公式、操作数、页级证据和五项修正。全文账本保留原文出现位置、范围外、规则排除及待处理项；`partial` 不代表全文完成，覆盖率不是金标测得的召回率。

历史支持实际角色/工具活动、内核重放、真实 Harness 缓存重放和导出。取消后未冻结任务可恢复；冻结结果只读。补材创建新版本，原结果不覆盖。Windows DPAPI 保存当前用户密钥；大型语料外置。默认监听 `127.0.0.1`，不依赖 CDN。

本版是开发版本。独立真人金标、规则签认、新D3封存评测、未提供密钥的供应商真实调用及干净Windows虚拟机不能由开发机测试替代。当前实测与问题关闭记录见[DEVELOPMENT_DEV3.md](DEVELOPMENT_DEV3.md)。

操作见 [RUNBOOK.md](RUNBOOK.md)，架构与闸门见 [NATIVE_AGENT_ACCEPTANCE.md](NATIVE_AGENT_ACCEPTANCE.md)，专业范围见 [NATIVE_AGENT_FINANCE.md](NATIVE_AGENT_FINANCE.md)，交付和许可见 [EXE_DELIVERY.md](EXE_DELIVERY.md)、[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。0.3 验收记录作为历史保留。
