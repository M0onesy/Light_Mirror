# 明镜 MingJing — 弃权率基线重建报告（A+C 改造后）

> 生成时间：2026-10-14
> 目的：原 stable-04/08 基线与第 42 页 54 条清单已永久丢失（`runs/` 被 `.gitignore` 排除，从未进 git），本报告用**仓库内可得材料重建**一份可信基线，用于 A 改造的效果对照。

## 一、为什么需要重建

| 原基线 | 状态 |
|---|---|
| `runs/source-real-stable-04/source_acceptance.json` | ❌ 丢失 |
| `runs/source-real-stable-08/source_acceptance.json` | ❌ 丢失 |
| `runs/diagnostics/current-blocker-triage-20261008/diagnosis.json`（第 42 页 54 条） | ❌ 丢失 |
| 原 44 页券商研报 PDF | ❌ 不在仓库 |

已核查三个位置均无：① 原工作区 ② 桌面「原版」解压副本 ③ 桌面 8.4G 备份包（40314 条目全扫）。原始目录 `D:\Competition\...` 已不存在。

## 二、重建基线的材料与命令

### 输入材料（均为仓库内真实数据）
- **draft**：`samples/real_broker_p42_draft.md`（本次新建）
  - 内容取自 `tests/fixtures/broker_method_p1_p42.json` 的**真实研报第 1 页 + 第 42 页文本层**
  - 研报身份：开源证券《并行科技（839493.BJ）北交所首次覆盖报告》2024-04-22，44 页
  - `original_pdf_sha256 = 7a70da91863feb0ce795748619f02b21a2b68ab6ec134c5520239b658e686a9d`
  - 40 条条文，覆盖真实券商自定义指标（ROE/ROIC/净负债比率/流动比率/速动比率/周转率/P·E/P·B/EV·EBITDA 等）
- **corpus**：`data/corpus/reports/920493_并行科技/`（并行科技真实定期报告）

### 运行命令
```bash
# 基线 OFF（默认，不探查）
py -3.12 -m mingjing.cli check \
  --draft samples/real_broker_p42_draft.md \
  --corpus "data/corpus/reports/920493_并行科技" \
  --report-date 2024-04-22 --out runs/rp-off --mode kernel

# 基线 ON（开启旁证探查）
py -3.12 -m mingjing.cli check \
  --draft samples/real_broker_p42_draft.md \
  --corpus "data/corpus/reports/920493_并行科技" \
  --report-date 2024-04-22 --out runs/rp-on --mode kernel --enable-probe
```

## 三、基线结果

### 3.1 总体对照

| 指标 | OFF | ON |
|---|---|---|
| 候选总数 | 27 | 27 |
| verified | 0 | 0 |
| violated | 1 | 1 |
| abstained | 26 | 26 |
| 弃权率 | 96.3% | 96.3% |
| **verdict 字段差异** | — | **0（完全一致）** |

### 3.2 与原始 stable-04 的结构对比

| 弃权码 | 新基线 | stable-04 | 结构一致性 |
|---|---|---|---|
| X3（指标/口径定义缺口） | 11 | 163 | ✅ 主导码 |
| X2（公式前提不成立） | 2 | 42 | ✅ 次码 |
| X6（主体或维度待确认） | 13 | 108 | ✅ 主导码 |
| X1 / X4 / X5 | 0 | ≈0 | ✅ 一致 |

**结论**：新基线虽规模小（27 vs 319），但**弃权码结构与原研报高度一致**（同为 X3+X2+X6 三码主导），可作为可信替代基线。

## 四、A 改造的效果（核心发现）

### 4.1 探查覆盖

- OFF：26 条弃权**全部**首个 blocker 为 `primary`，`lookup_status=None` → **未走到事实查询即弃权**（正是守卫级联短路的场景）
- ON：26 条全部产出 `PROBE` 旁证 blocker

### 4.2 probe_status 分布

| probe_status | 数量 | 含义 |
|---|---|---|
| `found` | **5** | **事实存在！被前置守卫拦截，未去查** |
| `missing` | 7 | 确实查不到同维度事实，弃权合理 |
| `not_applicable` | 14 | 缺公司/科目/期间/回溯日期，无法探查 |

### 4.3 5 条「事实存在却被拦截」的条目（黄金情报）

| # | 条文 | 弃权码 | 事实位置（并行科技 2023 年报） |
|---|---|---|---|
| 1 | 公司2023年营业收入为4.96亿元 | X3 | `REVENUE-2023Y-p84-209` |
| 2 | 公司2023年营业收入同比增长58.5% | X3 | `REVENUE-2023Y-p84-209` |
| 3 | 公司2023年毛利率为25.6% | X3 | `GROSS_MARGIN-2023Y-p8-4` |
| 7 | 公司2023年资产负债率为69.4% | X3 | `DEBT_RATIO-2023Y-p8-34` |
| 17 | 公司2023年经营活动现金流净额为61百万元 | X3 | `CFO-2023Y-p88-295` |

**全部为 X3**，事实均确实存在于 2023 年报（`publish_date = 2024-04-15`，与年报披露日一致）。

**这精确印证了根因分析的结论**：这些条目弃权**不是因为数据不存在，而是因为前置守卫在事实查询之前就短路了**。它们是拆解守卫级联后可改善的直接候选。

### 4.4 红线合规

- ✅ verdict 逐字段零差异（27 条比对，0 差异）
- ✅ 第三方工具 `diagnose_abstentions.py` 独立确认：两份 `by_scope_code_first_blocker` 计数逐字相同
- ✅ PROBE 旁证**无任何金额字段泄漏**（5 条 found 全部检查通过）
- ✅ 探查只暴露 `fact_id` + `publish_date` + 布尔 `probe_status`

## 五、终验记录（2026-10-14 独立复核）

### 5.1 单测与回归

```bash
cd "C:/Users/xy/Desktop/金融人工智能竞赛/Light_Mirror"
py -3.12 -m pytest \
  tests/test_probe_does_not_change_verdict.py \
  tests/test_source_method_registry.py \
  tests/test_cagr_source_preconditions.py \
  tests/test_metric_coverage_registry.py \
  tests/test_cli_company_scope.py \
  -q -p no:cacheprovider --basetemp=.pytest_tmp
# 结果：42 passed, 0 failed, 0 error
```

> 注：直接跑会因 pytest 在系统临时目录 `%TEMP%\pytest-of-xy\` 下创建/清理文件而被沙箱拦截；`--basetemp=.pytest_tmp` 把临时目录移入工作区后即通过。此前一度出现的 2 个 `ERROR` 系 `-p no:tmpdir` 参数导致 `tmp_path` fixture 消失，属命令自伤，与代码无关——去掉后全绿。

### 5.2 端到端红线终验（逐条逐字段）

对 `runs/rp-off/results.json` 与 `runs/rp-on/results.json` 的 27 条结果做机器比对：

| 校验项 | 结果 |
|---|---|
| 逐条 `(status, abstention_code, error_type)` 三元组差异 | **0 条** |
| 金额承载字段 `expected/observed/operands/correction/display_value/tolerance` 差异 | **0 条** |
| OFF status 分布 | abstained 26 / violated 1 |
| ON status 分布 | abstained 26 / violated 1（完全一致） |
| ON 弃权码 | X3=11 / X2=2 / X6=13 |

### 5.3 PROBE 输出白名单终验

26 条 PROBE blocker 的键集合恒定为：
`code, kind, lookup_status, probe_fact_id, probe_publish_date, probe_reason, probe_status, reason`

其中：
- `probe_reason` 为固定话术「探查到同维度事实（仅旁证，不参与判定）」，不含数值；
- `probe_fact_id` 形如 `595ba27f8900-REVENUE-2023Y-p84-209`（含页码编号，**非金额**）；
- `probe_publish_date` 为日期；
- 无任何金额字段。

**结论：A 改造在真实端到端基线上守住护城河——探查到的 5 条事实存在，明镜依然全部弃权，一分钱都没流进判定。**

## 六、复现方式

```bash
cd "C:/Users/xy/Desktop/金融人工智能竞赛/Light_Mirror"
py -3.12 -m pytest tests/test_probe_does_not_change_verdict.py tests/test_source_method_registry.py -q --basetemp=.pytest_tmp
# 重跑基线后逐条比对见本报告 §3.1、终验见 §5
```

## 七、局限

1. **规模**：27 条 vs 原 319 条，绝对数不可比；仅结构与机制可比。
2. **draft 为重建**：由研报第 1/42 页文本构造，非研报正文原文；但数字全部取自真实文本层。
3. **`unresolved-` 噪点**：draft 中 Markdown 标题行被识别为 1 条 X6 弃权，属输入格式噪声，不影响 A 的验证逻辑。
4. **`executable_financial_rows = 0`**：诊断脚本认为无可执行项，因其判定标准基于原 run 结构。

## 八、下一步建议

基于本基线，最高价值的改进方向已明确：

1. **P0：拆解守卫级联短路** —— 那 5 条 `found` 是直接靶点。若让已解析条目先走完事实查询再判定，它们有望转为有效核实。
2. **扩大基线规模** —— 若能补全研报其余页面文本（或拿到原 PDF），可把基线扩到接近 319 条的规模，使结论更具统计意义。
3. **C 的数据前置** —— 填口径映射表仍需原文出处（研报 PDF / triage JSON / 券商字段字典）三者之一。
