# 口径澄清路径（scope-declaration）独立验收报告（QA · Edward）

**日期**：2026-10-14
**验收人**：Edward（QA，`qa-ac`）
**被测路径**：`py -3.12 -m mingjing.cli check --draft samples/real_broker_p42_draft.md --corpus "data/corpus/reports/920493_并行科技" --report-date 2024-04-22 --out runs/rp-scope --mode kernel --scope-declaration 合并`
**对照基线**：`runs/rp-off`（同参数，**无** `--scope-declaration`）
**性质**：零代码改动的对照实验（eng-scope 执行）；本次为**只读核验**，未修改任何文件。
**解释器**：`py -3.12`（Python 3.12.5）

## 最终判定：**通过（PASS）** —— 该路径合规，为「用户显式声明」，非「系统默认猜测」

5 条 verified 的 `basis` **真实可追溯**（记录了"口径来自用户补充澄清"+ 资源 ID + SHA-256，且 SHA 经独立重算吻合）；除这 5 条外**其余 22 条零变化**（含 1 条 violated 逐字节一致）；红线条文未违反；数值与年报一致、容差按披露精度推导**未放宽**；不加声明则 5 条仍弃权（声明为必要条件）。**无可疑之处，无 P0/P1。**

---

## 一、五个重点问题的逐条结论

### Q1. 合规性质疑：5 条 verified 的 basis 是否真实可追溯？（PASS）

**逐条贴出 basis 原文**（`runs/rp-scope/results.json`，5 条 verified 完全一致）：

```
- E1：原始披露、口径与显示精度
- 报表口径来自用户补充澄清，非原文明示；资源：scope-clarification-1b5320d7814965925fa7；SHA-256：351ae4e5b7767578893bc59470db2b4ea7ae9dc7489ec1e7a75fbfda7afade26
```

- claim-00001（营业收入）→ 同上 basis（规则 `E1-value`）
- claim-00002（营收同比）→ `E5：同报告同口径同比` + 同上澄清 basis
- claim-00003（毛利率）→ `E1` + 同上澄清 basis
- claim-00007（资产负债率）→ `E1` + 同上澄清 basis
- claim-00012（经营现金流净额）→ `E1` + 同上澄清 basis

**独立可追溯性验证（我重算，非橡皮图章）**：

| 证据项 | 值 | 独立核验 |
|---|---|---|
| basis 引用的 resource_id | `scope-clarification-1b5320d7814965925fa7` | ✅ 与 `runs/rp-scope/clarifications.json` 中归档的 `resource_id` 一致 |
| basis 引用的 SHA-256 | `351ae4e5b7767578893bc59470db2b4ea7ae9dc7489ec1e7a75fbfda7afade26` | ✅ **= `clarifications.json` 文件的真实 sha256**（我用 hashlib 重算，完全吻合） |
| resource_id 可重算 | `scope-clarification-` + sha256(canonical core)[:20] | ✅ 我用 `{schema_version,provenance,scope,draft_sha256}` 重算得 `1b5320d7814965925fa7`，吻合 |
| 归档 provenance | `user_supplied_scope` | ✅ |
| 归档 scope | `合并` | ✅ |
| 归档 draft_sha256 | `713d5a82a2d61acb318ec17484d7ca3fbde8b0a4338e023b80e90ac3c9cc533b` | ✅ **= 输入草稿的真实 sha256**（与 `run_manifest.json` 中该 draft 的 hash 一致）→ 声明被**内容寻址绑定到本份草稿**，不可张冠李戴 |
| 审计留痕 | `audit.jsonl` 有 `type='clarification'` 事件 | ✅ 记录 provenance/scope/resource_id/sha256 |

**结论**：basis **不是洗白**。它明确写"报表口径来自**用户补充澄清，非原文明示**"，并携带可独立核验的资源 ID 与 SHA-256，且 SHA 与该次运行归档的澄清材料**逐字节吻合**、澄清材料又绑定到本份草稿的 SHA。证据链闭合。此外 `report.md:10` 明确声明：「本轮口径：合并。**来源为用户补充声明，不能据此声称原文明示该口径。**」——措辞完全符合"宁弃权不造假"。

---

### Q2. 波及面质疑：除 5 条外其余 22 条是否真的零变化？（PASS）

以 claim_id 逐条对 `(status, abstention_code, error_type)` 比对 `rp-off` vs `rp-scope`：

```
rp-off claims: 27   rp-scope claims: 27   id 集合完全一致
相同条目: 22 | 变化条目: 5

claim-00001: (abstained,X3,E8) -> (verified,None,None)  | 1. 营业收入4.96亿元
claim-00002: (abstained,X3,E8) -> (verified,None,None)  | 2. 营收同比58.5%
claim-00003: (abstained,X3,E8) -> (verified,None,None)  | 3. 毛利率25.6%
claim-00007: (abstained,X3,E8) -> (verified,None,None)  | 7. 资产负债率69.4%
claim-00012: (abstained,X3,E8) -> (verified,None,None)  | 17. 经营现金流净额61百万元
```

**恰好 5 条变化，正是目标条目；其余 22 条零变化。** 额外交叉核验：
- 唯一一条 `violated` 的 claim-00013（归母净利润 -78百万元）在两次运行中 `(violated, E1, expected=-80499535.36, observed=-78000000)` **逐字节一致** → 无任何无辜条目被解锁或被误改。
- 补充观察：声明实际作用于 **26 条合格 claim**（`scope_provenance=user_supplied_scope`），但**只有 5 条变 verified**，其余 21 条仍弃权（因撞上定义缺口 X2 / 解析不足 X6 / 比率缺公式等其它守卫）。这证明**声明只补"口径缺口"这一道门，不会盲目放行**——是保守行为，不是"一键洗白"。

---

### Q3. 红线质疑：`docs/FINANCIAL_SOURCE_POLICY.md` 条文是否被违反？（PASS，未违反）

红线条文（该文件第 13 行）：

> 「以上源维度均在语义绑定之前由原文冻结；模型不能用候选金额、相近事实或改写关系来绕过。**报表口径不明时仍要求源材料或用户有来源的补充声明，不能默认为合并口径。**」

**本路径 = 用户显式声明（合规），非系统默认猜测（违规）。代码/数据层面的区别如下**：

| 维度 | 系统默认猜测（违规，本路径**未**做） | 用户显式声明（合规，本路径**实际**做的） |
|---|---|---|
| CLI 参数 | 无此参数，内部硬编码 `'合并'` | `--scope-declaration`，**`default=''`（空=仅按原文）**，仅用户显式传入才非空（`cli.py:516`） |
| 出处标记 | 无 provenance | `provenance='user_supplied_scope'`（`scope.py:112`） |
| 内容寻址 | 无 | `resource_id` + `sha256` + `draft_sha256`（绑定到具体草稿） |
| 冲突守卫 | 无 | 若声明与**原文明示口径冲突**，抛 `ValueError('冲突')`，不覆盖（`scope.py:409`、`test_user_scope_conflicts_do_not_override_explicit_source`） |
| 呈现措辞 | 会暗示"原文即合并" | 明确"来源为用户补充声明，**不能据此声称原文明示该口径**" |

**关键佐证**：5 条目标 claim 的原文（`1. 公司2023年营业收入为4.96亿元` 等）**均不含"合并/母公司"字样**，`source_dimensions.explicit.scope=false`。即原文口径**确实缺失**，声明是在**补充缺失信息**，而非**推翻原文**。冲突守卫测试 74 passed（`test_user_scope_conflicts_do_not_override_explicit_source` 断言矛盾声明被 `ValueError('冲突')` 拒绝且 claim 不变）。**结论：符合红线条文，未违反。**

---

### Q4. 数值质疑：expected 与年报事实是否真的一致？容差是否放宽？（PASS）

| claim | expected | observed | 差值 | tolerance | 是否放宽 |
|---|---|---|---|---|---|
| claim-00001 营业收入 | 495671818.88 | 496000000.00 | 328181.12 | `500000.005` | 否 |
| claim-00002 营收同比 | 0.5847451... | 0.585 | 0.000254889 | `0.0005000000413` | 否 |
| claim-00003 毛利率 | 0.2563 | 0.256 | 0.0003 | `0.00055` | 否 |
| claim-00007 资产负债率 | 0.6944 | 0.694 | 0.0004 | `0.00055` | 否 |
| claim-00012 经营现金流 | 61253669.05 | 61000000 | 253669.05 | `500000.005` | 否 |

**容差推导逻辑（已核代码，非硬编码）**：`judge.py:766/1122` 的 tolerance = `(expected_interval 宽 + observed_interval 宽) / 2`，两个区间均由**披露精度**决定：
- claim-00001 claim_interval=[495500000, 496500000]（"4.96亿元" 2 位小数 → ±0.005亿 = ±500000），tolerance 正来自此半宽。
- claim-00003/07 claim_interval=[0.2555,0.2565]/[0.6935,0.6945]（"25.6%"/"69.4%" 1 位小数 → ±0.05% → 半宽 0.0005 + 舍入 eps）→ `0.00055`。
- 每条均附 `precision_note`：「与披露精度相容；不表示原文所有精确位数均已独立核实」。

**expected 来自年报事实**：5 条 operand 均来自 `2024-04-15_2023年年度报告.pdf`（sha `595ba27f8900...`），如 claim-00001 operand = `595ba27f8900-REVENUE-2023Y-p84-209`（label「营业总收入」, 表「（三）合并利润表」, value 495671818.88, parse_conf 0.99）。**claim-00001 的 expected（495671818.88）落在草稿显示区间 [495500000, 496500000] 内**，说明草稿值"4.96亿元"与年报事实相符，非凑数。**结论：数值真实相符，容差为精度推导，未放宽。**

---

### Q5. 反向测试：不加 `--scope-declaration` 是否仍弃权？（PASS，声明为必要条件）

| claim | rp-off（无声明） | rp-scope（声明=合并） |
|---|---|---|
| claim-00001 | `abstained X3 E8` | `verified` |
| claim-00002 | `abstained X3 E8` | `verified` |
| claim-00003 | `abstained X3 E8` | `verified` |
| claim-00007 | `abstained X3 E8` | `verified` |
| claim-00012 | `abstained X3 E8` | `verified` |

rp-off 中这 5 条的 `source_dimensions.scope=''`、`scope_provenance='source_text_only'`，reason 均为「原文未唯一声明合并或母公司报表口径；**请补充有来源的口径声明后新建核查任务**」。二值对照证明：**无声明 → 必然弃权；有声明 → 才可能核实**。声明是必要条件。

---

## 二、发现的问题

**无 P0 / P1。** 仅一条 P2 观察（非本路径引入，供参考）：

| 严重度 | 问题 | 说明 |
|---|---|---|
| P2 | `mingjing/cli.py:342` 的**分析路径**（`--task-type analysis`）用 `scope_declaration or '合并'`，即分析模式在无声明时**回落默认合并**。 | 本路径是 `check`（task_type=check），**不经过该分支**，故与本次验收无关，也不违反红线（财报分析的计算口径是另一议题）。但若未来有人把该默认逻辑误引入 check/事实核查路径，即构成"系统默认猜测"违规。**建议加注释或防护，非阻塞。** |

---

## 三、明确未能验证 / 局限

1. **HTML 报告的交互渲染**未逐像素核验（只看 md 与 JSON）。原因：非本任务重点，且 md/JSON 已含全部证据。
2. **playwright 相关用例未跑**：`tests/test_scope_clarifications.py:168` 因环境无 `playwright` 而 SKIP（1 skipped），非失败。
3. 未重新执行该 CLI 命令（eng-scope 已完成；我以只读方式核验其产物 + 交叉重算哈希 + 对 rp-off 逐条比对）。如需，可重跑命令做运行级确定性复核。

---

## 四、最终判定

**验收通过。** 该口径澄清路径是**用户显式、有来源、内容寻址、可审计**的合规补充声明：
- 5 条 verified 的 basis 真实可追溯（资源 ID + SHA-256 经独立重算吻合，绑定到本份草稿）；
- 影响面**精确限于 5 条**目标条目，其余 22 条零变化；
- 符合 `FINANCIAL_SOURCE_POLICY.md` 红线（未默认猜测、未覆盖原文、措辞不冒充原文明示）；
- 数值与年报一致、容差按披露精度推导未放宽；
- 声明为核实的**必要条件**（无声明则仍弃权）。

**红线独立判断：守住了。** 该路径把"报表口径不明"从"静默弃权"变为"用户可显式补充、且补充内容全程留痕可核验、冲突则不覆盖"——这是合规的口径澄清，不是把弃权悄悄洗成核实。
</content>
