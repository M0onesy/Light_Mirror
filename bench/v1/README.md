# CRRV v2 评测登记与机制验证

本目录不是已经完成的金融专业人工真值库。`generated.py` 生成 354 条已知机制样例：B 集 E1—E9 各 15 条 raw 与 15 条 rounded，共 270 条；C 集合法取整、单位换算、模糊表述、明示旧来源四个样例族共 60 条；X 集 X1—X6 各 4 条，共 24 条。E8 属于正确弃权，不作为数字错误的强行判错。

生成样例使用 synthetic 事实和证据，`gold_origin=deterministic_generated_fixture`，`human_reviews=[]`。它们可验证抽取、Decimal、判定、五项修复和修复复查，不能用来声称原 PDF 真值、人类盲标、证据单元格定位、真实模型双盲或未见公司泛化已验收。C 集尚未覆盖所有财务定义和披露陷阱。

运行真实确定性流水线，不复制期望标签作为预测：

```powershell
python scripts/agent_benchmark.py --out runs/generated-agent-bench-new --scan
```

输出 `generated_cases.jsonl`、`predictions.jsonl`、`metrics.json` 和 `measurement_audit.json`。目录不得覆盖。16 个工作点和消融会重新执行抽取与 Judge。纯模型、RAG 和原生 Harness 基线若未执行，会标 `not_executed`；双绑定 fixture 消融不是独立模型会话。

P 集三家公司及重述、A 集 20 家公司两年 15 指标仍须独立原 PDF 人工真值。每条独立真值必须登记原 PDF 路径及 SHA-256、原文陈述、公司、期间、报告日期、三态和错误或弃权代码，并由三名独立人类签认同一标签。模型和本开发 Agent 不替代人类。`load_human_cases` 校验这些记录；未提供记录时不制造审核签名。

D、D2 和开发已查看公司不可作为 D3。当前脚本知道既有 holdout 清单和开发公司 920493。D3 须在最终代码和规则冻结后，另选五家未参与开发的公司。首次步骤只登记路径、大小、哈希和代码指纹，避免提前读取财务真值：

```powershell
python scripts/agent_benchmark.py --seal-d3 --corpus "外部财报语料根" --out bench_seals/d3.json
```

代码指纹包括内核、Harness 集成、Web 静态资源、规则、构建和评测脚本及 v1 数据。修改后旧封存不能作新系统的最终验收。保留原封存，不反复替换同一批公司的指纹。

人工真值完成后的一次性规则基线：

```powershell
python scripts/agent_benchmark.py --evaluate-d3 bench_seals/d3.json --human-cases independent_human_d3.jsonl --corpus "外部财报语料根" --out runs/d3-final
```

签认标签结构为 `{"status":"verified", "error_type":null, "abstention_code":null}`，必须和同条真值字段一致。真值不能包含 `facts` 或 `binding_fixture_status`；预测事实从封存原 PDF 重新解析。执行前验证代码、源文件和人工记录。建立不可重复的 `.attempt.json` 后，即使运行失败也不能调参重跑同一 D3；输出单独保存执行审计。规则基线不等于 Harness Agent 验收，原生双盲模型会话与三次重放另需执行。

模型训练截止日若没有供应商可核实信息，登记为 `unknown`。不能仅凭报告年份声称数据晚于模型训练时间。当前 D3 尚未选取、封存或正式执行；独立人工真值和专业规则签认仍是待完成项。
