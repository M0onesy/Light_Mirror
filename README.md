# 明镜 MingJing 1.0.0.dev3

明镜面向研究报告事实核查、财报分析和材料追问。首页上传研报/财报并存入本地知识库，在线任务复用锁定的 DeepSeek Harness 原生 Agent Loop，通过受控金融工具调用统一的 MetricSpec、规则和 Decimal 内核。模型负责语义、检索与报告组织；金额事实、计算、判定和替换数字由宿主产生。

唯一当前工程是本目录 **Light_Mirror**，版本为 **1.0.0.dev3 阶段候选**。计划书与任务手册只在 `docs/project/` 维护；实际验收说明在 `docs/acceptance/`，成品在 `artifacts/`，本地原始证据在 `evidence/`，外置材料在 `data/`。旧工程集中在工作区的 `存档/历史版本/`，不能作为另一个开发入口。

普通用户下载 [当前 GitHub Release](https://github.com/M0onesy/Light_Mirror/releases/tag/mingjing-1.0.0.dev3)，解压后双击 `MingJing.exe`。本地对应入口为 `artifacts/program/MingJing.exe`。该程序保留原已冻结构建字节；本轮目录整理和辅助说明不构成新的金融验收或重打包。

```text
Light_Mirror/
├─ mingjing/ kernel/ rulepack/   宿主、金融内核与规则
├─ frontend/ web/               界面源码与已构建静态资源
├─ runtime/ vendor/             原生金融插件、载体与锁定上游
├─ scripts/ tests/ bench/        工具、测试与评测
├─ docs/project/                唯一计划书、PDF及开发任务手册
├─ docs/acceptance/             阶段验收摘要和当前问题
├─ artifacts/                   程序、ZIP、视频、源码及核心审阅包
├─ evidence/                    只读原始收据、运行记录及迁移映射
└─ data/                        外置财报、准则及本地工作材料
```

## 使用与任务

```powershell
Set-Location -LiteralPath D:\Competition\金融人工智能竞赛\Light_Mirror
python -m pip install -r requirements-build.lock
python -m mingjing.launcher
```

工作台上传材料后确认识别的公司、报告期间、研报日期及参考文件，选择标准或审慎策略。在线模式进入原生模型—工具—观察循环，绑定与核查轨迹可在任务/会话中查看。知识库会话支持财报分析、追问、补证和重放；追加材料、口径澄清及局部重解析建立新版本，父结果只读。无模型或运行时不可用时明确采用纯内核演示/降级，保留缺证与未处理项。

标准模式在事先登记的定义和假设下核查，并逐条显示默认合并、备选定义、横线及其他条件；审慎模式拒绝没有来源证明的口径与方法，不能凭相近金额补造定义。直接披露、授权推算、内部勾稽、恒等式和文本佐证分别展示；预测与估值一致不表示真实历史事实已独立核实。

模型可按用户路由配置 DeepSeek、Kimi、OpenAI GPT 或兼容协议。连接测试和 Agent 多工具能力测试分开，供应商真实请求与 mock 分别登记。密钥使用当前 Windows 用户 DPAPI，配置导出不含明文；已有用户模型、凭据和角色路由不得自动改换。

## 结果、复现与交付

输出结构化结果、证据页、操作数与公式、错误/弃权/提示和修改文本。全文账本保留数字原位置、范围外、排除和待处理；覆盖率与执行进度不是独立真人召回或正确率。正式数字必须有样本、分母、版本及原始收据。

内核重放重算金融产物，Agent/消息重放用匹配缓存重跑实际循环。先验输入、输出、源码和资源锁，缓存缺失或变化明确失败，不联网补齐。知识库原件也必须重新验哈希，未来或未知公告日期材料不能进入事实、佐证、引用及补证。

完整开发包与核心审阅包不同：完整原生运行需锁定上游源码和金融载体闭包；≤30MB核心包供源码审阅，排除完整载体、真实语料、用户配置和历史。普通用户的单 EXE 在同版源码、UI、资源稳定后构建，按实际 build/smoke 与独立环境记录验收。

- [使用手册](docs/USER_GUIDE.md)
- [源码运行与验证](docs/RUNBOOK.md)
- [统一入口与知识库设计](docs/SMART_PIPELINE.md)
- [五工作包与关闭账本](docs/PROJECT_TASK_MANUAL.md)
- [计划书与项目文档](docs/project/README.md)
- [阶段验收与当前问题](docs/acceptance/README.md)
- [交付及复现闭包](docs/DELIVERY_GUIDE.md)
- [EXE构建与发行](docs/EXE_DELIVERY.md)
- [许可](LICENSE)、[第三方范围](docs/THIRD_PARTY_NOTICES.md)

明镜自有代码使用 AGPL-3.0-only，第三方组件保留各自许可。公开仓库不提交 Token、DPAPI、个人配置、真实模型缓存、全量语料或未获授权材料。完整源码应与现场展示版一致。
