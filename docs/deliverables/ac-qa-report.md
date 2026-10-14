# A+C 改造独立验收报告（QA · Edward）

**日期**：2026-10-14
**验收人**：Edward（QA Engineer，`qa-ac`）
**被测对象**：明镜 MingJing 弃权诊断 A+C 改造（engineer-ac 完成，未 commit）
**解释器**：`py -3.12`（Python 3.12.5）
**工作目录**：`C:/Users/xy/Desktop/金融人工智能竞赛/Light_Mirror`
**验收依据**：`docs/deliverables/ac-refactor-plan.md`（§1.2.3 三重保险、§1.3 调用链核验、§2.5 C1/C2、Part C 风险清单）+ team-lead 派单说明

## 最终判定：**验收通过（PASS）** ✅

改造在**空 registry 交付形态**下**零行为变化**、**verdict 结构性不可被探查改变**、两个新产品测试**经破坏性注入验证为真实有效**。发现的均为 P2 级文档/规范性问题，无 P0/P1。护城河红线**未被突破**。

---

## 一、被测改动快照（验收时的真实基线）

```bash
$ git diff --ignore-all-space --stat
 mingjing/judge.py            | 43 ++++++++++++++++++++++++++++++++++++++++++-
 mingjing/source_semantics.py |  8 ++++++++
 2 files changed, 50 insertions(+), 1 deletion(-)
```

文件内容 SHA（`git hash-object`，验收起始与结束均一致）：

| 文件 | SHA | 状态 |
|---|---|---|
| `mingjing/judge.py` | `64251cd15a5bc40a3f17bf24b6c963233a5ff48f` | modified |
| `mingjing/source_semantics.py` | `e3ab024ed23d19af3e233a9d55f31373eba74abf` | modified |
| `mingjing/source_method_registry.py` | `cba4a7c888b1febdc8fccbb092fc6b106da88cfc` | untracked（新建） |
| `tests/test_probe_does_not_change_verdict.py` | `2b0e191f36605d0a018ddc75cd1cae93274a35b7` | untracked（新建） |
| `tests/test_source_method_registry.py` | `031e5405535548f36063043aae263b02b37aeb4f` | untracked（新建） |

> 验收结束后已确认：上述 5 个文件 SHA 与验收起始**完全一致**，无临时破坏残留。

---

## 二、验证项结果

### 验证项 1：护城河红线 —— verdict 绝对不变（PASS）

**方法**：自建独立脚本 `tmp_qa_verify_ac.py`（**不复用工程师测试**），构造 4 类会被前置守卫短路的 claim，在 `enable_probe=False/True` 下逐字段比对 **20 个 verdict 字段**（`status/error_type/abstention_code/expected/observed/correction/operands/rule_ids/display_value/reason/formula/tolerance/evidence/basis/diagnostics/advisories/claim_interval/expected_interval/source_dimensions/calculation_steps`）。

```bash
$ py -3.12 tmp_qa_verify_ac.py
=== [1] verdict 不变 / 无金额泄漏 / 边界安全 ===
  [ok]   [binding_unknown] 开/关探查 verdict 全字段一致 (差异: [])
  [ok]   [binding_unresolved] 开/关探查 verdict 全字段一致 (差异: [])
  [ok]   [account_substitution] 开/关探查 verdict 全字段一致 (差异: [])
  [ok]   [parse_unparsed] 开/关探查 verdict 全字段一致 (差异: [])
  ...
  [ok]   探查内部抛异常时 verdict 仍一致 (差异 [])
...
总计检查: 58，失败: 0
ALL INDEPENDENT CHECKS PASSED
```

**结论**：
- 4 类短路 claim（binding unknown / unresolved / account 替换 / parse 非 parsed）开/关探查 verdict **逐字段完全一致**。
- PROBE blocker **不含任何金额键**（`value/amount/probe_value/candidate_value/expected/observed` 全部为空）。
- 命中场景（存在同维度 Fact value=100）下，PROBE blocker 文本实测为 `{...'probe_status':'found','probe_fact_id':'revenue-2023Y','probe_publish_date':'2024-04-15'}` —— **无金额 100、无 value 键**，仅暴露 `fact_id`/`publish_date` 布尔旁证。

**边界情况（探查安全返回，不抛异常）**：

| 场景 | 结果 |
|---|---|
| 缺 company | `probe_status=not_applicable` ✅ |
| 缺 account（UNKNOWN） | `probe_status=not_applicable` ✅ |
| 缺 period | `probe_status=not_applicable` ✅ |
| 缺 report_date（None） | `probe_status=not_applicable` ✅ |
| `_lookup` 内部抛 `RuntimeError` | 被 try/except 捕获为 `probe_status=missing`，verdict 仍一致 ✅ |

---

### 验证项 2：测试真实性 —— 破坏性注入核验（PASS）

**2a. 破坏 `_mapping_evidence_ok` 恒返回 True**（编辑 `mingjing/source_method_registry.py`）：

```bash
$ py -3.12 -m pytest tests/test_source_method_registry.py -q
FAILED test_missing_evidence_field_is_rejected
FAILED test_wrong_hash_is_rejected
FAILED test_span_text_must_match_quote
FAILED test_definition_id_must_be_registered
FAILED test_non_source_provenance_is_rejected
5 failed, 5 passed
```

**2b. 破坏 `validated_mapping` 恒返回非 None**：

```bash
$ py -3.12 -m pytest tests/test_source_method_registry.py -q
FAILED test_empty_registry_does_not_allow
FAILED test_missing_evidence_field_is_rejected
... (共 7 项)
7 failed, 3 passed
```

**2c. 破坏 `judge.py` 探查回流 verdict**（在 `if self.enable_probe:` 块内加 `result.status='violated'`）：

```bash
$ py -3.12 -m pytest tests/test_probe_does_not_change_verdict.py -q
FAILED test_probe_does_not_change_verdict_and_keeps_short_circuit_blocker
  E  AssertionError: status
  E  assert 'abstained' == 'violated'
1 failed, 1 passed
```

**还原确认**：`source_method_registry.py` 已手动还原（该文件 untracked，`git checkout` 无法还原），SHA 回到 `cba4a7c...`；`judge.py`/`source_semantics.py` 已还原，SHA 回到基线；`grep -rn "QA-DELIBERATE-BREAK" mingjing/` 无残留。

**结论**：两个新测试**均为真实有效断言**，能捕获"出处校验失效""空表误放行""探查回流 verdict"三类核心回归。**非橡皮图章。**

**测试质量观察（非缺陷）**：
- `test_source_method_registry.py` 断言强（如 `test_registry_starts_empty` 断言 `registry == []`，直接冻结护城河空表形态）。
- `test_probe_does_not_change_verdict.py` 的 `VERDICT_FIELDS` 覆盖对齐方案 §1.4.1，且比对 20 字段；`test_probe_found_never_leaks_amount` 的 `'value' not in item` 属结构性断言但有效（能捕获 `**outcome.to_dict()` 式误加键）。
- 两个测试**未覆盖** `not_applicable` 边界与 `_lookup` 异常路径 —— 已由本报告 `tmp_qa_verify_ac.py` 独立补齐（均通过）。

---

### 验证项 3：默认关闭无回归（PASS）

```bash
$ py -3.12 -m pytest tests/test_cagr_source_preconditions.py \
    tests/test_metric_coverage_registry.py tests/test_broker_source_method.py \
    tests/test_source_semantics.py tests/test_current_ratio_definition_guard.py -q
143 passed in 6.93s
```

扩面安全集（含 judge/弃权/语义/维度/kernel 相关）：

```bash
$ py -3.12 -m pytest tests/test_probe_does_not_change_verdict.py \
    tests/test_source_method_registry.py tests/test_cagr_source_preconditions.py \
    tests/test_metric_coverage_registry.py tests/test_broker_source_method.py \
    tests/test_source_semantics.py tests/test_current_ratio_definition_guard.py \
    tests/test_binding_coverage_diagnosis.py tests/test_numeric_semantic_review.py \
    tests/test_source_dimension_guards.py tests/test_kernel.py -q
294 passed in 29.75s
```

全仓可收集性（捕获新模块导入错误）：

```bash
$ py -3.12 -m pytest tests/ --collect-only -q
3515 tests collected in 25.12s   # 无 collection / import error
```

**结论**：默认关闭（`enable_probe=False`）下：
- `test_cagr_source_preconditions.py` / `test_metric_coverage_registry.py` 中 `pytest.fail('...must not query...')` 的硬禁止**未被触发**（143 全绿）。
- `enable_probe=False` 时**不产生任何 PROBE blocker**（`tmp_qa_verify_ac.py` 断言 + 工程师测试断言双重确认）。
- 全仓 3515 项可正常 collect，新模块 `source_method_registry` 导入无副作用。

---

### 验证项 4：C 放行逻辑正确性（PASS）

**4a. 空 registry 行为与改造前完全一致（git stash 前后逐字节对比）**

构造 8 类 claim（ROE/净利率/速动比率/营收/EPS/现金/长期投资/CAGR，均强制 `semantic_review_required=True`），对 `unresolved_source_definition_issue` 求值并 JSON 序列化：

```bash
$ git stash push -- mingjing/source_semantics.py   # 回到 HEAD（无 C1）
$ py -3.12 tmp_qa_c_equivalence.py > /tmp/c_before.txt
$ git stash pop                                     # 恢复 C1
$ py -3.12 tmp_qa_c_equivalence.py > /tmp/c_after.txt
$ diff /tmp/c_before.txt /tmp/c_after.txt
NO DIFF -> C empty-registry behavior IDENTICAL
```

**结论**：空表时 `unresolved_source_definition_issue` 返回值**逐字节与改造前相同** —— 空表不改变任何行为。✅

**4b. 出处齐备 → 放行；逐项破坏 → 每项均被拒**（`tmp_qa_verify_ac.py`，monkeypatch registry）：

| 破坏项 | `validated_mapping` | 是否放行 |
|---|---|---|
| 缺 `source_page` | 被拒 | 未放行 ✅ |
| `text_sha256` 错 | 被拒 | 未放行 ✅ |
| `span.text ≠ source_quote` | 被拒 | 未放行 ✅ |
| `definition_id` 非登记 | 被拒 | 未放行 ✅ |
| `provenance='user_supplied'` | 被拒 | 未放行 ✅ |
| `source_quote` 为空 | 被拒 | 未放行 ✅ |
| `source_page` 非 int（'42'） | 被拒 | 未放行 ✅ |
| `source_page=0` | 被拒 | 未放行 ✅ |
| `report_date` 为空 | 被拒 | 未放行 ✅ |
| `schema_version` 错 | 被拒 | 未放行 ✅ |
| `source_account` 不匹配 | 不命中 | 未放行 ✅ |
| **出处齐备且自洽** | **命中** | **放行（返回 None）** ✅ |

**结论**：出处校验框架的每一项反例都能正确拒绝；仅当四元组完整、哈希自洽、definition_id 已登记、provenance 合法时才放行。✅

**4c. C1 链路行为变化被 registry 门控（额外发现，非缺陷）**

```bash
$ py -3.12 tmp_qa_c1_flow.py
=== registry EMPTY (real shipped state) ===
status: abstained code: X2      # 来源定义缺口，维持弃权
=== registry has valid mapping (monkeypatched, NOT shipped) ===
status: abstained code: X3      # 定义缺口放行后，落回 source_scope 守卫，仍弃权
```

**结论**：C1 放行**只移除"来源定义"这一道弃权门**，下游 binding/scope/fact 查询门仍在，**不会把弃权变成错误核实**；且该行为**完全被 registry 是否非空门控**，本轮 ship 空表 → 零线上行为变化。C1（保守放行）语义符合方案 §2.5。✅

---

### 验证项 5：代码质量（PASS）

**5a. `source_method_registry.py` 不读金额/事实/模型输出**（源码静态扫描 + 人工通读）：

```bash
$ py -3.12 tmp_qa_verify_ac.py   # 静态检查项
  [ok]   registry 源码不引用 outcome（事实查询结果）
  [ok]   registry 源码不含 'FactStore' 引用
  [ok]   registry 源码不含 'query_outcome' 引用
  [ok]   registry 源码不含 'judge' 引用
  [ok]   registry 源码不含 'model' 引用
  [ok]   registry 源码不含 'candidate' 引用
```

人工通读确认：`validated_mapping` 仅读取 `claim.table_context` 的 `source_account_unresolved` 与 registry 自身字段；`_mapping_evidence_ok` 仅校验出处四元组 + SHA-256 + `METRIC_SPECS[canonical_account].definition_id` 登记一致性。**无任何金额/候选事实/模型输出读取**。✅

**5b. C1 不改写 `source_dimensions`（无 `FrozenInstanceError` 风险）**：

```bash
  [ok]   C1 放行未改写 source_dimensions
  [ok]   （frozen 安全性）
```

人工确认：`source_semantics.py` 的 C1 分支体仅为 `if validated_mapping(claim) is not None: return None`，**无任何 `claim.table_context[...] = ...` 写入**，未触及 `AtomicClaim`（`models.py:108` `@dataclass(frozen=True)`）的冻结字段。方案 §2.3.3 的 C2 改写方案**本轮未实施**（符合 team-lead 拍板的 C1）。✅

---

## 三、发现的问题

| 严重度 | 问题 | 影响 | 建议 |
|---|---|---|---|
| **P2** | A 部分未做展示层过滤：`mingjing/report.py:182`（md）会把 PROBE blocker 渲染为 `- 待补依据：PROBE ...`，`:240`（JS）渲染 `PROBE · diagnostic_probe`。 | 仅在 `enable_probe=True` 且产出报告时出现；默认关闭 → **当前无线上影响**。语义上"旁证"被标成"待补依据"略有误导。 | 方案 §1.5 已标为可选。建议后续 `report.py` 对 `kind=='diagnostic_probe'` 单列"旁证探查"或过滤。**非阻塞。** |
| **P2** | `_probe_fact_lookup(self, claim, scope, nr, spec)` 的 `spec` 形参**从未被函数体使用**。 | 无功能影响；仅轻微冗余。 | 可后续移除形参，或在 docstring 标注保留原因（与方案 §1.2.1 签名一致故保留）。**非阻塞。** |
| **P2** | 两个新测试未覆盖 `not_applicable` 边界路径与 `_lookup` 异常路径。 | 这两条路径当前无测试守护，未来回归可能漏网。 | 建议追加边界用例（本报告 `tmp_qa_verify_ac.py` 已给出可移植断言模板，均已通过）。**非阻塞。** |

**无 P0 / P1 问题。**

---

## 四、明确未能验证的部分及原因

1. **运行级逐条 verdict 比对（方案 §1.4.2）未能执行** —— 原因：`runs/source-real-stable-04/`、`runs/source-real-stable-08/`、`runs/diagnostics/current-blocker-triage-20261008/` 基线目录**不在工作区、不在 git HEAD**（`R8`），原 44 页券商研报 PDF 亦不可得（`R9`）。**替代方案**：以 4 类构造 claim 的单测级逐字段比对（验证项 1）替代，覆盖短路路径的主要分支，结论等效但覆盖面小于全量基线。
2. **全量 `pytest tests/` 完整跑完未能完成** —— 原因：全仓 190+ 测试文件、含长时 agent/web 测试，超 19 分钟仍未结束，且触发 pytest 临时目录清理的权限交互。**替代方案**：`--collect-only` 全仓 3515 项无 collection/import error（排除新模块导入风险）+ 294 项安全集全绿 + 143 项 team-lead 指定回归集全绿。**对 A+C 改动的相关面已充分覆盖。**
3. **C 真实映射的端到端放行效果未能验证** —— 原因：registry 本轮**设计为空表**，无任何真实映射数据（护城河红线：严禁凭指标名反推公式）。**替代方案**：以 monkeypatch 构造"出处齐备"假映射验证放行链路 + 逐项破坏验证拒绝（验证项 4b/4c），证明框架正确。

---

## 五、对"是否真的守住护城河红线"的独立判断

**守住了。** 依据：

1. **A（探查）结构性零回流**：verdict 在 `_verify`（`judge.py:333`）定死后，`_all_blockers`（`:346`）才只写 `result.blockers`。探查产物仅落在**独立 `PROBE` blocker 的独立 `probe_*` 键**，不触碰任何 verdict 字段，不 return 出 `_all_blockers`。20 字段逐字段比对 + 破坏性注入（改坏必 FAIL）双重证明。金额（`Fact.value`）**永不落入 blocker**，只暴露 `fact_id`/`publish_date` 布尔旁证 —— 规避"下游拿旁证金额当可用证据"。三重保险（时序/数据流/命名）经代码级确认成立。
2. **A（探查）默认关闭**：`enable_probe=False` 时零 fact 查询、零 PROBE blocker；两个 `pytest.fail('...must not query...')` 硬禁止测试全绿。
3. **C（放行）空表即零行为变化**：`git stash` 前后 8 类 claim 返回值**逐字节相同**，证明"空 registry 不改变任何既有行为"。
4. **C（放行）严格出处取证**：11 类反例（缺字段/哈希错/span 不符/未登记 definition_id/非法 provenance/页码非法…）**全部被拒**；仅四元组完整自洽才放行。且 `validated_mapping` 与 `_mapping_evidence_ok` **不读金额/事实/模型输出**（静态 + 人工双确认）。
5. **C 采用 C1（保守放行）而非 C2**：放行只"取消来源定义弃权门"，不重写 `source_dimensions`，不把弃权直接变核实 —— 直接排除了方案 `R5/R6`（映射错误 → 弃权变错误核实 / frozen 传播）两条高危路径。
6. **registry 本轮为空表**：符合"拿到原 PDF/出处前不得写入任何映射"的护城河约束，**未凭指标名反推公式**。

**唯一需持续关注点**：C 的 registry 一旦非空，放行会改变 abstention code（X2→X3，见 4c），且未来若上 C2 会真正触及 binding 比对与 frozen claim 传播面 —— 届时应重新走完整运行级基线回归（依赖 `R8` 的 `runs/` 基线恢复）。**本轮交付形态下无此风险。**

---

## 六、验收结论

| 验证项 | 结论 |
|---|---|
| 1. verdict 绝对不变 + 无金额泄漏 + 边界安全 | **PASS** |
| 2. 测试真实性（破坏性注入） | **PASS** |
| 3. 默认关闭无回归 | **PASS** |
| 4. C 放行逻辑正确性（空表等价 + 反例拒绝） | **PASS** |
| 5. 代码质量（不读金额/不改写 source_dimensions） | **PASS** |

**最终判定：验收通过。** 无 P0/P1 阻塞问题；3 项 P2 为可选改进，不影响本轮交付。

**工作区清洁确认**：验收期间为测试有效性做的 3 处临时破坏均已还原，`git diff --ignore-all-space --stat` 与验收起始一致（`judge.py 43 / source_semantics.py 8`），5 个文件 SHA 全部匹配基线。
</content>
