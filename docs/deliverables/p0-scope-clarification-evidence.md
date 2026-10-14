# P0 口径澄清路径实跑留证报告

- **任务**：实跑 `--scope-declaration 合并` 第三条基线（`runs/rp-scope`），核验 5 条转正质量，产出三基线对照，证明"只解锁该解锁的、未波及无辜"。
- **约束**：本任务**不修改任何源码**（`mingjing/*.py`、`tests/*` 全部只读）。仅新增 `runs/rp-scope/` 运行产物与本报告。
- **解释器**：`py -3.12`（默认 `python` 为 2.7，不可用）。
- **项目根**：`C:\Users\xy\Desktop\金融人工智能竞赛\Light_Mirror`

---

## 1. 问题陈述

在未开探查基线（`runs/rp-off`）中有 5 条财务摘要类断言（claim-00001 / 02 / 03 / 07 / 12，对应草稿条文 1/2/3/7/17）被判为 **X3 弃权**。经前期定位，其真因**不是事实缺失**，而是：

> 草稿把年报"财务摘要表"改写成叙事句时，**丢失了"合并 / 母公司"报表口径上下文**，导致 `_source_scope_issue(strict=True)` 判定"原文未唯一声明合并或母公司报表口径"，触发 X3。

一句话：**原文里数是有出处的，只是口径（合并 vs 母公司）没有被唯一声明**，属于"口径不明"，而非"数值无法解析"。

**合规解法**：提供**有来源的口径补充声明**（`--scope-declaration 合并`）。这不是放宽标准，而是补足原文缺失的口径前提 —— 与 `docs/FINANCIAL_SOURCE_POLICY.md` 第 13 行"报表口径不明时仍要求源材料或用户有来源的补充声明，不能默认为合并口径"完全一致。

---

## 2. 运行命令（完整可复现）

确认参数签名（`mingjing/cli.py:516`，`choices=('', '合并', '母公司')`）：

```bash
py -3.12 -m mingjing.cli check --help
#   --scope-declaration {,合并,母公司}  用户口径澄清；默认仅按原文
```

实跑命令：

```bash
cd "C:/Users/xy/Desktop/金融人工智能竞赛/Light_Mirror"
py -3.12 -m mingjing.cli check \
  --draft samples/real_broker_p42_draft.md \
  --corpus "data/corpus/reports/920493_并行科技" \
  --report-date 2024-04-22 --out runs/rp-scope --mode kernel \
  --scope-declaration 合并
```

**输出**：

```json
{"run_id": "run-a9c6cbce97b3", "out": "runs\\rp-scope", "summary": {"verified": 5, "violated": 1, "abstained": 21}}
```

生成口径澄清 provenance 产物 `runs/rp-scope/clarifications.json`：

```json
{
  "schema_version": "scope-clarification.v1",
  "provenance": "user_supplied_scope",
  "scope": "合并",
  "draft_sha256": "713d5a82a2d61acb318ec17484d7ca3fbde8b0a4338e023b80e90ac3c9cc533b",
  "resource_id": "scope-clarification-1b5320d7814965925fa7"
}
```

---

## 3. 5 条转正明细表

| claim_id | 条文 | status | observed | expected | 是否吻合 | 证据页码 |
|---|---|---|---|---|---|---|
| claim-00001 | 公司2023年营业收入为4.96亿元 | verified | 496000000.00 | 495671818.88 | ✅（展示精度一致） | p84 |
| claim-00002 | 公司2023年营业收入同比增长58.5% | verified | 0.585 | 0.5847451102…（58.4745%） | ✅（展示精度一致） | p84 |
| claim-00003 | 公司2023年毛利率为25.6% | verified | 0.256 | 0.2563 | ✅（展示精度一致） | p8 |
| claim-00007 | 公司2023年资产负债率为69.4% | verified | 0.694 | 0.6944 | ✅（展示精度一致） | p8 |
| claim-00012 | 公司2023年经营活动现金流净额为61百万元 | verified | 61000000 | 61253669.05 | ✅（展示精度一致） | p87, p88 |

> 说明：`observed` 为草稿叙述句内四舍五入后的**披露值**，`expected` 为由年报源事实按公式重算的**精确值**。两者在草稿声明的显示精度下一致，故 `verified`（非 violated）。
>
> - claim-00001 公式：`披露值 495671818.88 / 单位倍率 100000000`
> - claim-00002 公式：`(495671818.88 - 312776998.45) / 312776998.45`（同报告同口径同比）
> - claim-00003 公式：`披露值 0.2563 / 单位倍率 0.01`
> - claim-00007 公式：`披露值 0.6944 / 单位倍率 0.01`
> - claim-00012 公式：`披露值 61253669.05 / 单位倍率 1000000`

**证据出处**：5 条 evidence 均指向真实源文件 `2024-04-15_2023年年度报告.pdf`（`document_sha256` 前缀 `595ba27f8900…`），页码分别为 p84（营收/同比）、p8（毛利率/资产负债率）、p87–p88（经营现金流），与年报"财务摘要 / 主要会计数据"表位置吻合。每条 evidence 均带 `image_sha256`、`quote_sha256`、`bbox`，可回溯到原文页图与原文引文。

---

## 4. 每条 basis 的 provenance 原文（合规、可追溯）

5 条转正断言的 `basis` 数组结构一致：**第一元素**为核查依据类型，**第二元素**为口径 provenance 记录。逐条贴出：

**claim-00001**
```
E1：原始披露、口径与显示精度
报表口径来自用户补充澄清，非原文明示；资源：scope-clarification-1b5320d7814965925fa7；SHA-256：351ae4e5b7767578893bc59470db2b4ea7ae9dc7489ec1e7a75fbfda7afade26
```

**claim-00002**
```
E5：同报告同口径同比
报表口径来自用户补充澄清，非原文明示；资源：scope-clarification-1b5320d7814965925fa7；SHA-256：351ae4e5b7767578893bc59470db2b4ea7ae9dc7489ec1e7a75fbfda7afade26
```

**claim-00003**
```
E1：原始披露、口径与显示精度
报表口径来自用户补充澄清，非原文明示；资源：scope-clarification-1b5320d7814965925fa7；SHA-256：351ae4e5b7767578893bc59470db2b4ea7ae9dc7489ec1e7a75fbfda7afade26
```

**claim-00007**
```
E1：原始披露、口径与显示精度
报表口径来自用户补充澄清，非原文明示；资源：scope-clarification-1b5320d7814965925fa7；SHA-256：351ae4e5b7767578893bc59470db2b4ea7ae9dc7489ec1e7a75fbfda7afade26
```

**claim-00012**
```
E1：原始披露、口径与显示精度
报表口径来自用户补充澄清，非原文明示；资源：scope-clarification-1b5320d7814965925fa7；SHA-256：351ae4e5b7767578893bc59470db2b4ea7ae9dc7489ec1e7a75fbfda7afade26
```

**合规要点**：

1. **诚实披露**：basis 显式写明"报表口径来自**用户补充澄清，非原文明示**"，未被包装成原文事实。
2. **可追溯**：附 `资源 ID`（`scope-clarification-1b5320d7814965925fa7`，与 `clarifications.json` 一致）+ `SHA-256`，可对口径声明资源做完整性/来源校验。
3. **来源标注**：`clarifications.json` 中 `"provenance": "user_supplied_scope"`，明确这是用户提供的口径，不是系统默认或模型推测。
4. **不越权**：系统并未"默认为合并口径"，而是**仅在用户显式声明后**才采用；未声明时保持弃权（见下方 rp-off 基线）。

---

## 5. 三基线对照表

| 指标 | rp-off | rp-on | rp-scope |
|---|---|---|---|
| verified | 0 | 0 | **5** |
| violated | 1 | 1 | 1 |
| abstained | 26 | 26 | **21** |
| 总条数 | 27 | 27 | 27 |
| **弃权率** | 26/27 ≈ **96.30%** | 26/27 ≈ **96.30%** | 21/27 ≈ **77.78%** |
| 弃权码 X1 | 0 | 0 | 0 |
| 弃权码 X2 | 2 | 2 | 2 |
| 弃权码 X3 | 11 | 11 | **6** |
| 弃权码 X4 | 0 | 0 | 0 |
| 弃权码 X5 | 0 | 0 | 0 |
| 弃权码 X6 | 13 | 13 | 13 |

**说明**：

- `rp-off` 与 `rp-on` 的 status / 弃权码分布**完全相同**（探查只增加诊断信息、永不改变 verdict，符合 `--enable-probe` 设计承诺）。
- `rp-scope` 相对 `rp-off`：`verified 0→5`、`abstained 26→21`、**X3 从 11 减到 6**（正好减少 5），其余弃权码 X2/X6 及 violated 数量**完全不变**。

---

## 6. "其余 22 条零变化"比对结论

**方法**：以 `claim_id` 为键，对 `rp-off` 与 `rp-scope` 逐条比对三元组 `(status, abstention_code, error_type)`，**排除** 5 条目标断言后统计变化数。

**结果**：

```
non-target claims compared: 22 | changed: 0
```

**逐条 sanity（5 条目标 off→scope）**：

```
claim-00001 abstained X3 -> verified None
claim-00002 abstained X3 -> verified None
claim-00003 abstained X3 -> verified None
claim-00007 abstained X3 -> verified None
claim-00012 abstained X3 -> verified None
```

**结论**：✅ 补口径声明后，**只有这 5 条 X3 弃权转为 verified**，其余 **22 条断言的 verdict / 弃权码 / 错误类型零变化**。这证明 `--scope-declaration 合并` 的作用范围**精确收敛**于"因口径不唯一而弃权"的条目，**没有波及任何无辜断言**，也未放宽其它弃权理由（X2/X6 保持不变）或篡改 violated 判定。

---

## 7. 结论段：这是"补口径声明"，不是"改代码放宽标准"

1. **未改一行源码**：本路径仅新增运行参数 `--scope-declaration 合并`，`mingjing/` 与 `tests/` 全程只读。5 条 X3→verified 完全由**输入前提补齐**驱动，而非修改判定逻辑。
2. **合规依据**：`docs/FINANCIAL_SOURCE_POLICY.md` 第 13 行明文规定 ——
   > "以上源维度均在语义绑定之前由原文冻结；模型不能用候选金额、相近事实或改写关系来绕过。**报表口径不明时仍要求源材料或用户有来源的补充声明，不能默认为合并口径。**"
   
   本路径正是该条文的**正向执行**：口径不明 → 不默认合并 → 要求有来源的补充声明 → 用户提供 `合并` 声明后放行。系统**没有**"默认合并"，而是**等待并采信**了用户显式、带 provenance 的声明。
3. **可追溯、可审计**：每条转正断言都带 `basis` provenance（"来自用户补充澄清，非原文明示" + 资源 ID + SHA-256），`clarifications.json` 记录 `provenance: user_supplied_scope`。结论来源**诚实标注**，不冒充原文明示。
4. **作用域受控**：22 条零变化证明解锁范围精确，未越界放宽其它标准（X2/X6 弃权不变，violated 不变）。

---

## 8. 局限

1. **样本级演示**：本证据基于单一样本（并行科技 920493，2023 年报，27 条断言），结论仅证明该路径"可行且合规"，不代表全量生产召回/准确率成绩。
2. **口径声明需外部提供**：真实产品中 `--scope-declaration` 不应由系统默认填充，**必须由用户或来源材料明确给出**（如研报正文/年报声明段）。本演示中的"合并"声明属于**假设性用户澄清输入**，用于验证链路，不含独立金标。
3. **未覆盖 X2/X6 真因**：本路径只解决"口径不唯一"这一类 X3 弃权；rp-scope 中仍余 6 条 X3、2 条 X2、13 条 X6 弃权，需另行定位根因（不在本任务范围）。
4. **baseline 一致性**：`rp-scope` 的 draft 哈希 `713d5a82…` 已冻结在 `clarifications.json`，可复现；但若草稿或语料变更，需重新核验 5 条是否仍成立。

---

*报告生成：eng-scope | 运行 ID：run-a9c6cbce97b3 | 产物目录：`runs/rp-scope/` | 本报告不修改任何源码。*
