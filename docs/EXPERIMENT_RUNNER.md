# 原材料对照与消融实验

`scripts/experiment_runner.py` 是独立进程中的评测入口，不由本地 Web 服务导入。每次重新解析同一批哈希锁定的原材料并执行对应系统；不通过改写已有预测标签模拟基线或消融。目录只能新建，失败、部分执行及降级均保留。

## 可执行矩阵

| 配置 | 实际执行 |
|---|---|
| `kernel` | 正式纯内核 CLI，完全不核验或启动 Harness 运行资产。 |
| `direct-llm`，B1 | 同一选定草稿及全部原始报告块，经选定供应商的原生适配器进入无工具独立会话，由模型直判。完整材料不会被静默截断；超限或失败计入失败记录。 |
| `rag`，B2 | 对同一报告块按草稿中的金融标签与期间作确定性检索，实际取 `top_k` 个块，再用同一原生适配器直判。检索不读取金标，也不按金额相近选择。 |
| `rule-exact-match`，B3 | 同公司、科目、期间下匹配 Decimal 点值；故意不辨合并/母公司、版本或披露日，不执行公式。 |
| `harness` | 完整 `FinancialAgent.run()`，真实 A/B 会话、工具往返、原生循环与内核判定。降级不计为原生执行成功。 |
| `kernel` + `oracle-binding`，E-a | 只取三名真人已签认的六字段语义绑定；仍重新解析原材料，原文候选数字不变。当前限定单个无歧义绑定与单个草稿陈述；多项输入明确失败。 |
| `harness` + `single-binding`，E-b | 只真实执行 A；B 比较闸以可信实验程序复制 A 结果绕过，不调用 B 模型。该复制明确写入审计，不声称独立双盲。 |
| `kernel/harness` + `cutoff-disabled`，E-c | 取数前关闭日期截断，其余查询/判定重跑；不会重新标注预测。 |
| `kernel/harness` + `point-tolerance`，E-d | 在计算前将操作数披露区间替为闭点区间；保留原文显示精度容差、模糊词政策与事实冲突处理，真实重算。所有实验策略在退出或异常时恢复。 |
| 两个不同 `route` + `harness` + `repetitions:3`，E-e | 每个配置三次实时执行，记录各次独立会话、预测与绑定产物。未提供两家可用模型/密钥则不会宣称已完成。 |

可选 E-f（相同 Skill 的 Python Agent 编排器对照）尚不支持；现有纯内核 CLI 不能冒充该实验。

## 输入与运行

创建包含以下字段的 JSON。`source_documents.path` 相对于 `--corpus`，必须锁定原 PDF/Markdown/文本的 SHA-256；不能提供 facts.json 或机器候选作为输入。

```json
{
  "schema_version": "mingjing-development-experiment.v1",
  "cases": [{
    "id": "case-1",
    "set": "development",
    "company": "公司名称",
    "period": "2024Y",
    "report_date": "2025-03-31",
    "draft_text": "原始待核查陈述",
    "source_documents": [{"path": "2025-03-01_2024年年度报告.pdf", "sha256": "实际文件的64位哈希"}]
  }],
  "experiments": [
    {"name": "kernel", "baseline": "kernel"},
    {"name": "direct", "baseline": "direct-llm"},
    {"name": "rag", "baseline": "rag", "top_k": 12},
    {"name": "exact", "baseline": "rule-exact-match"},
    {"name": "native", "baseline": "harness", "repetitions": 3}
  ]
}
```

```powershell
python scripts/experiment_runner.py `
  --manifest experiments.json `
  --corpus "原材料根目录" `
  --out runs/experiments-new `
  --human-cases "三名真人完整签认的human_gold.jsonl"
```

省略 `--human-cases` 可调试执行机制，但不生成金融质量成绩。默认读取当前 Web 所存配置，密钥仍从 DPAPI SecretStore 取出，不写入清单。可用 `--config` 指定普通配置文件；`route` 只能包含已有的 provider/model 标识。所有跨供应商自动降级在实验中关闭。

模型直判预测统一标记 `untrusted_baseline_only`，不得导入产品事实库。对模型响应检查完成状态、JSON、原文字符区间、三态/弃权代码以及实际来源块 ID；截断响应和虚构证据失败。未知 Token 用量和成本为 `unknown`。

## 评分和正式封存

人工真值使用 `agent_benchmark.load_human_cases()` 的原 PDF、三名完整真人签认及哈希验证。缺失/多项/失败结果留在分母。页、表和单元格分开评分：单元格需要坐标 IoU ≥ 0.5 与行列相符，页级大块包含真值不算单元格命中。当前原解析器通常提供块级证据，因此单元格指标可能较低，这是未完成的定位能力，不补成满分。

可执行研报任务还要求三名真人的每份 review 在本人签认前填写 `execution_task_sha256`。其值为 `digest(execution_task_identity(task))`，锁定任务 ID、集合、公司、期间、研报日期、原稿文本或带哈希的原稿引用、全部原 PDF 引用与口径声明。该字段纳入原有 `signed_content_sha256`。执行清单必须与真人签认的这组输入完全一致。单元格 A/P 包的原有审核仅证明原披露事实，不能为后来添加的研报、日期或口径背书；缺这个执行锁会在运行前失败。

每个实验输出由实际 records/scores 汇总的误纳数、E1—E9 精确率/召回率/F1、X1—X6 正确弃权率、陷阱误报、覆盖率、页/表/单元格命中率、修复复核率、耗时均值与 p90。失败、部分执行、降级和漏项保留在整个签认集合的召回/覆盖分母；精确率明确限定完整且唯一的预测。多输出或部分任务中的 unsafe verified 仍计入误纳审查。主任务和修复重跑的 Token/请求数分开汇总，缺失用量的总数为 `unknown`，只另列 known subtotal；不把未知成本记为零。

每条 violated 且有替换文本的建议会写入新草稿并重新执行对应系统，不使用人工正确金额填充修订稿。原始修正和重新执行结果分别保存。粗精度来源产生的区间修订无法重新解析时记录失败。E-a 修订稿已是新文本，复核使用普通内核，不复用旧稿签认绑定。

当前 signed cell 审核包没有完整研报 occurrence 金标。全文提及召回始终登记 `unknown`，机器 coverage 处理率不能代替真实召回率。`professional_acceptance` 始终为 false，不能把本入口的机制或初步质量测量等同 G0—G7 全部通过。

D3 不由开发 CLI 选择或执行。正式调用分三步：

1. `preregister_experiments(experiments, config)` 生成完整矩阵、配置及代码锁，并将返回值写入 D3 seal 的 `experiment_plan`。
2. 父评测器验证 seal 后，先调用 `preflight_sealed_suite()`。它要求 B1/B2/B3/kernel/Harness、E-a—E-d、两模型各三次、已有密钥、完整真人签认与五家已封存原 PDF 输入。E-a 当前要求一个唯一已签认六字段绑定和一个原稿陈述，预检用正式覆盖账本解析原稿，遇到多条或零条陈述立即拒绝；不能把该限制作为全文研报 E-a 已完成。此步失败不得消耗正式机会。
3. 父评测器用排他创建获取一次 attempt marker，再调用 `execute_sealed_suite(..., attempt_marker=...)`。marker 必须包含匹配的 `seal_sha256`、`plan_sha256`、`gold_sha256`、`status:started` 和 `out`。实际执行失败也消耗该机会，由父评测器写最终状态。

正式 CLI 在全部前提完成且代码冻结后执行：

```powershell
python scripts/agent_benchmark.py --seal-d3 --corpus "原材料根目录" `
  --experiment-plan preregistered-plan.json --out bench/d3-new-seal.json
python scripts/agent_benchmark.py --evaluate-d3 bench/d3-new-seal.json `
  --corpus "原材料根目录" --human-cases independently-signed-d3.jsonl `
  --config "普通配置文件" --out runs/d3-official-once
```

缺完整实验计划、任一必需模型的密钥、执行原稿锁或真人签认，均不会创建正式 attempt marker；不能以只运行 kernel 消耗 D3 后宣称 Agent 留出验收通过。

本轮测试使用虚构本地报告及协议 fixture，验证真实纯内核重跑、消融政策、来源隔离、修复重跑、物理定位和会话清理；没有调用真实供应商 API，没有打开 D3。
