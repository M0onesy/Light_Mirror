# P1 调研：rulepack/caliber.json 出处审计与 C 落地路径修正

> 生成时间：2026-10-14
> 结论：**caliber.json 不可直接用于填充 C registry**——其 `scope='合并'` 为脚本硬编码，出处引用为格式凑数。C 的正确原料是 `source_clauses.json`（17 条真实条款）与 `sources/` 下 8 份已校验原文。

## 一、触发问题

为推进 C（口径映射框架）从「空 registry」走向「有据可填」，盘点仓库素材时发现 `rulepack/caliber.json` 已含 60 条口径定义，形如：

```json
{
  "id": "CAL-001",
  "label": "营业收入",
  "account": "REVENUE",
  "scope": "合并",
  "nr": "未扣非",
  "requires_context": false,
  "basis_refs": [{
    "source_id": "MJ-METHOD",
    "quote": "受限公式只允许已登记输入、常数、四则运算、绝对值、比较及比较的逻辑与或，分母区间非正或触零时弃权。",
    "quote_sha256": "6736761026ba821d...",
    "clause_id": "MJ-METHOD-1"
  }],
  "review_status": "pending_human_signoff"
}
```

表面上「60 条口径 × 全部带出处 × 哈希齐备」，看似正是 C registry 的原料。**但逐项审计后发现三个致命问题。**

## 二、三个问题

### 问题 1：出处挂错——60 条共用一条无关条款

`caliber.json` 全部 60 条的 `basis_refs`：

- 59 条挂 `common`（= MJ-METHOD-1：**公式运算符白名单**）
- 1 条（NP_PARENT）挂 `parent_profit`

而 MJ-METHOD-1 的原文（`rulepack/sources/MJ-METHOD.md:6`）为：

> 「受限公式只允许已登记输入、常数、四则运算、绝对值、比较及比较的逻辑与或，分母区间非正或触零时弃权。」

**这是「公式里能用哪些运算符」的规定，与「营业收入/毛利率/资产负债率各用什么口径」毫无关系。**

生成源在 `rulepack/build_v2.py:193`：

```python
'basis_refs':[parent_profit if account=='NP_PARENT' else common],
```

即：除 NP_PARENT 外，其余 59 条一律挂同一条 `common` 引用——**这是生成脚本的设计，为满足「必须带 basis_refs」的格式要求而做的填充**。

### 问题 2：`scope='合并'` 是硬编码，无任何依据

`rulepack/build_v2.py:190-192`：

```python
dictionary.append({'id':f'CAL-{len(dictionary)+1:03d}','label':label,'account':account,
                   'scope':'合并',                                     # ← 硬编码
                   'nr':'扣非' if account=='NP_PARENT_ADJ' else '未扣非',
                   'requires_context': label in {...},                 # ← 硬编码 label 列表
                   'basis_refs':[...], 'approved_by':None,
                   'review_status':'pending_human_signoff'})           # ← 硬编码
```

`scope='合并'` 直接写死在循环体里，**没有任何来源、判断或依据**。若据此让系统「认定义所有指标都是合并口径」，即构成 `docs/FINANCIAL_SOURCE_POLICY.md:13` 明令禁止的「**默认为合并口径**」。

**特别危险**：库内同一指标并存两个口径的事实——
- `REVENUE-2023Y` 合并 = 495,671,818.88
- `REVENUE-2023Y` 母公司 = 426,412,227.84

默认合并 → 一旦研报引用母公司口径，产出错误结论 = 造假。

### 问题 3：全部标 `pending_human_signoff`

60 条 `review_status` 全部为 `pending_human_signoff`（待人工签署），`approved_by: null`。**生成方自己也未认定该表可用。**

## 三、决定性对照：真正的依据表是 `source_clauses.json`

`rulepack/source_clauses.json` 含 **17 条真实条款**，每条带 `quote` + `quote_sha256`，**哈希 17/17 全部校验通过**。

其立场与 `caliber.json` **完全相反**——它们是「**口径必须明示**」的规则，而非「默认按合并」的规则：

| 来源 | 引文（节选） | 立场 |
|---|---|---|
| MJ-COVERAGE-METHOD | 「合并权益合计**只在合并报表范围内**采用归属于母公司所有者权益与少数股东权益之和。」 | 口径须限定范围 |
| MJ-COVERAGE-METHOD | 「负债权益合计按**原文明示**的负债与所有者权益组成加总同报告各项金额。」 | 须原文明示 |
| MJ-COVERAGE-METHOD | 「票据账款合计**仅适用于原文明示**应收票据及应收账款…的组成。」 | 须原文明示 |
| MJ-COVERAGE-METHOD | 「CAGR仅用于相同指标、**口径**、币种和同一报告版本的正首尾值…」 | 口径须一致 |
| CAS33 | 「母公司向子公司出售资产…应当全额抵销"归属于母公司所有者的净利润"。」 | 准则级列报要求 |
| CAS30 第十三条(四) | 「财务报表是合并财务报表的，**应当予以标明**。」 | 合并须标明 |

**关键洞察**：整个 rulepack 的设计哲学是「**口径从原文取，不能默认**」。CAS30 第十三条(四) 更从准则层面确立「合并须标明」——反推即「未标明则不可推定合并」。这为「口径不明 → 弃权」提供了准则级支撑。

`caliber.json` 硬编码的 `scope='合并'` 与上述哲学**直接冲突**。

## 四、sources/ 原文完整性（已校验）

`sources.json` 登记 8 个来源，`rulepack/sources/` 下 8 份原文齐全，**SHA-256 8/8 全部匹配**：

| ID | 名称 | 状态 |
|---|---|---|
| MJ-METHOD | 明镜数学与取数方法规范v3 | ✅ |
| CAS28 | 企业会计准则第28号 | ✅ |
| CAS30 | 企业会计准则第30号—财务报表列报 | ✅ |
| CAS31 | 企业会计准则第31号—现金流量表 | ✅ |
| CAS33 | 企业会计准则第33号—合并财务报表 | ✅ |
| NON_RECURRING_2023 | 非经常性损益（2023修订） | ✅ |
| MJ-COVERAGE-METHOD | 金融覆盖数学方法工程草案 | ✅ |
| MJ-THRESHOLD-METHOD | 来源引述阈值断言工程方法v1 | ✅ |

原文可信、可追溯，是 C 的合法依据来源。

## 五、结论与优化方向

### 结论

1. **`caliber.json` 不可用于填充 C registry。** 其 `scope='合并'` 是脚本硬编码，出处是格式凑数，且全部未签署。用它 = 把上一轮刚否决的「默认合并」造假路径换壳塞回。
2. **C 的合法原料是 `source_clauses.json`（17 条真实条款）+ `sources/` 8 份已校验原文。**
3. 真正的口径映射应遵循「**原文明示**」原则——这与 A+C 改造的护城河完全一致。

### 优化方向（两档，按风险排序）

**方向 B（防御优先，推荐先做）：给 registry 加「出处真实性」校验**

在 `mingjing/source_method_registry.py` 的 `_mapping_evidence_ok` 中，除现有 9 项校验外，增加一条抗凑数规则：

- **反「一源多标」**：同一条 `basis_ref.quote_sha256` 被 N 条不相关 account 共用时（N 超过阈值，如 ≤2 除外），拒绝该映射；
- 理由：真实口径依据应是「一标一条最相关条款」，而非「60 条共用一条运算符规定」。

此方向**零风险**，且直接提升系统抗造假能力。

**方向 A（进攻，需人工确认）：用真实条款重建口径依据**

以 `source_clauses.json` 中真实条款为锚，为确有准则/规范支撑的口径写映射。**但**：
- CAS30 第十三条(四) 只确立「合并须标明」，**不能反推「某指标默认用合并」**；
- 因此方向 A 的现实产出**可能很小**——因为「营收默认合并」这类断言，在准则里**找不到依据**，本就不该被写入。

**换句话说：C 的价值可能不在于「填更多的映射」，而在于「证明大部分映射本就不该存在」**——这正是护城河的体现。

### 一句话

C 不该被用来「把弃权洗成核实」；它应被用来「为真正有原文依据的口径建立可追溯映射，并挡住无依据的凑数映射」。

## 六、方向 B 实施记录（2026-10-14 已完成）

### 改动

**`mingjing/source_method_registry.py`**（+29 行）：

1. 新增常量 `MAX_ACCOUNTS_PER_SOURCE = 2`：同一原文片段（`text_sha256`）最多可支撑
   2 个互异目标指标（`canonical_account`），超过即判定为「一源多标」式凑数。
2. 新增 `_shared_source_accounts(text_sha256)`：统计 registry 中与给定片段哈希关联的
   互异 canonical_account 集合。**只做来源共享度统计，不读金额/事实/模型输出。**
3. `_mapping_evidence_ok` 新增第 10 项校验：

```python
# 10) 抗凑数：同一原文片段不得被过多互异指标共用（一源多标 → 整组拒绝）。
if len(_shared_source_accounts(row['text_sha256'])) > MAX_ACCOUNTS_PER_SOURCE:
    return False
```

### 测试（`tests/test_source_method_registry.py` 新增 3 用例）

- `test_shared_source_across_accounts_is_rejected` —— 一源多标被拒
- `test_distinct_sources_are_accepted` —— 一标一源通过
- `test_shared_source_within_threshold_is_accepted` —— 共用未超阈值仍通过

### 破坏性验证（实测）

| 场景 | 通过 | 拒绝 | 预期 | 结论 |
|---|---|---|---|---|
| 一源多标（5 指标共用 1 出处） | 0 | 5 | 0 通过 | ✅ 精确拦截 |
| 一标一源（5 指标各独立出处） | 5 | 0 | 5 通过 | ✅ 不伤正常 |

**证明：该校验只拦「凑数」，不影响「正常映射」——符合护城河语义。**

### 回归

核心回归 **45 passed**（42 原有 + 3 新增），0 失败。

### 意义

此校验直接封堵了「把 caliber.json 那类一源多标数据灌进 registry」的路径——
即使未来有人尝试用格式合规但实质凑数的映射来放行弃权条目，也会被第 10 项校验拒绝。
**这是 C 改造从「框架」走向「抗造假」的关键一步。**
