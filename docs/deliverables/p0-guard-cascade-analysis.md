# P0 前置调研：`Judge._all_blockers` 守卫级联拆解方案依据（只读）

> 调研者：arch-cascade ｜ 方式：**严格只读源码 + 只读基线复算**（未修改任何已有文件；本报告为唯一新建文件）
> 项目根：`C:/Users/xy/Desktop/金融人工智能竞赛/Light_Mirror`
> 解释器：`py -3.12`（Python 3.12.5）
> 基线输入：`samples/real_broker_p42_draft.md` + `data/corpus/reports/920493_并行科技/`，`--report-date 2024-04-22`
> 基线产物：`runs/rp-off/results.json`（OFF）、`runs/rp-on/results.json`（ON，`--enable-probe`）

---

## 摘要（先给结论，供主理人快速决策）

1. `_all_blockers`（`mingjing/judge.py:400–453`）确实存在**守卫级联短路**：只要前置守卫任一失败，就在 `:439` 直接 `return blockers`，事实查询**从不执行**（`lookup_status='not_executed'`）。
2. **但 5 条 `found` 的真实成因不是 X3「指标口径定义缺口」，而是 X3「报表口径（合并/母公司）未唯一声明」**——即 `_source_scope_issue`（`judge.py:413` → `scope.py:191–192`）短路。实测：这 5 条 `unresolved_source_definition_issue` 返回 **None**，所以 **C1 registry（只 patch `unresolved_source_definition_issue`）对这 5 条完全无效**。
3. 关键实测：同一 draft + corpus，仅加 `--scope-declaration 合并`，`verified` 从 **0 → 5**（`violated` 1 不变，`abstained` 26 → 21）。5 条全部带真实证据、正确公式，且 basis 显式记录「报表口径来自用户补充澄清，非原文明示」。
4. 因此 **P0 的安全形态不是"拆掉守卫先查事实"，而是"补齐口径声明后放行"**。绝不能把 `_source_scope_issue` 短路改成"继续查事实"——那会在口径未声明时把全公司合并事实去对一个可能是母公司口径的数字，属造假。
5. 无论 P0 怎么设计，**红线"口径未定义 → 仍弃权"必须保留**。本报告给出可安全「延迟判定」的守卫清单与伪代码。

---

## 1. 完整枚举 `Judge._all_blockers` 的守卫级联

文件：`mingjing/judge.py`，方法定义 `:400`，短路 `return` 在 `:439`。

级联分两阶段：**(A) 守卫求值与登记（:406–422）** → **(B) 短路判定（:428–439）** → **(C) 事实查询（:440–452）**。

### 1.1 阶段 A：守卫求值（只 add blocker，不 return）

| # | 行号 | 守卫 | 判定条件 | 命中弃权码 / kind | 硬/软 |
|---|---|---|---|---|---|
| G0 | `:406` | primary | 无条件把 `result.abstention_code or 'X1'` 作为首因 blocker 加入 | 继承 `result.abstention_code` | —（汇总） |
| G1 | `:407` | source_parse | `claim.parse_status != 'parsed'` | **X6** | **硬**（结构未解析=客观不可判） |
| G2 | `:408` | binding | `claim.binding_status in {'unknown','unresolved','disagreed','pending'}` | **X3** | **软**（绑定未定，原则上可由来源证明/澄清解决） |
| G3 | `:413–414` | source_scope | `_source_scope_issue(claim, strict=source_strict, check_binding=False)` | 见下 | **软**（口径声明缺口，可由 `--scope-declaration` 解决） |
| G4 | `:414` | definition | `unresolved_source_definition_issue(claim)` | 多为 **X3/X2** | **软**（口径映射缺口，C registry 靶点） |
| G5 | `:414` | source_semantics | `_source_semantic_issue(claim)` | **X3/X2** | **硬~软混合**（见 §2） |
| G6 | `:417–420` | source_binding | `source_binding_issue(claim, strict=source_strict)` | **X3** | **硬**（冻结源维度与模型绑定冲突，不可猜） |
| G7 | `:421–422` | definition(登记) | `claim.account in METRIC_SPECS and metric_spec(...) is None` | **X2** | **软**（缺登记方法，可由 registry 解决） |

> `source_strict`（`:411–412`）= `table_context['source_scope_required'] is True` 或 `table_context['source_dimensions']` 是 dict。本基线的 5 条均满足，故 G3 以 strict 生效。

### 1.2 阶段 B：短路判定——**唯一的 `return blockers` 位置**

```python
# judge.py:428–439
if (source_failures or claim.parse_status!='parsed' or result.abstention_code in {'X3','X6'}
        or claim.binding_status in {'unknown','unresolved','disagreed','pending'}):
    add('X3','fact_lookup','源维度或语义前置验证未通过，依赖事实查询未执行',lookup_status='not_executed')
    if self.enable_probe:                       # A 改造：仅写诊断
        probe=self._probe_fact_lookup(claim,scope,nr,spec)
        add('PROBE','diagnostic_probe', ..., lookup_status='not_executed', **probe)
    return blockers                              # ← :439 唯一短路点
```

短路触发条件四选一（**全为 OR**）：
1. `source_failures` 非空（G3/G4/G5/G6 任一命中）；
2. `claim.parse_status != 'parsed'`（G1）；
3. `result.abstention_code in {'X3','X6'}`（`_verify` 已判 X3/X6）；
4. `claim.binding_status in {unknown/unresolved/disagreed/pending}`（G2）。

### 1.3 阶段 C：事实查询（仅当未短路）

| 行号 | 分支 | 行为 |
|---|---|---|
| `:440` | 前置 | 要求 `company & account∉{'' ,'UNKNOWN'} & period & report_date` 齐备 |
| `:441–449` | `spec` 非空（登记指标） | `_lookup`；若 missing 再 `derive_registered_metric`；错误入 blocker |
| `:450–452` | `spec` 为空 | `_lookup`；无 fact 则 add X1/outcome.code |

**要点**：`_all_blockers` 自身**不产生 verdict**，只产生诊断 blocker 列表（docstring `:401` "without creating verdicts"）。

### 1.4 注释错位提示（供工程师注意）

`:432` 注释写"397 行原样保留"，实际短路 `return` 在 **`:439`**、短路 `add` 在 **`:430`**。A 改造新增后行号漂移，注释里的"397"是旧行号，不是真实现状。`test_probe_does_not_change_verdict.py:42` 的断言只认 `lookup_status=='not_executed'`，不依赖行号，故无碍。

---

## 2. 每条 guard 的关键问题：改成"先查事实再决定"会不会**错误地**把弃权变 verified？

判定"错误变 verified"的通用判据：**若该 guard 拦下的正是"维度/口径/绑定不确定"的语义歧义，而事实查询用的是"猜一个维度"（如默认 `合并`+`未扣非`），那么一旦金额恰好落进事实区间，就会造出一个看似成立的 verified。**

| guard | 改成"先查事实"的风险 | 会错误变 verified 的条件 | 结论 |
|---|---|---|---|
| **G1 X6** 解析未完成 | **极高**。结构未解析⇒科目/期间/单位本身缺失，事实查询无从构造正确维度，必用默认维度猜测。 | 原文结构残缺但金额抽出来了，默认维度命中了某个事实 ⇒ 造假 | ❌ **不可延迟**（硬守卫） |
| **G2 X3** 绑定未定 | **高**。绑定未定⇒账户/期间/关系未定，查事实=用模型未证实的猜测去对齐。 | 模型猜的 account 与事实恰好一致 ⇒ 假 verified | ❌ **不可延迟**（除非先由来源/澄清锁定绑定，见 §3） |
| **G3 X3** 报表口径未声明 | **极高（本 P0 的核心！）**。原文没声明合并/母公司，事实层同时存在合并与母公司两套值。默认 `scope='合并'`（`:423`）会去匹配合并事实。 | 原文其实是母公司口径，但合并数值凑巧接近 ⇒ 用错口径的"verified" | ❌ **不可裸拆**；**必须先取得口径声明**（这正是实测 `--scope-declaration 合并` 能安全 +5 的原因） |
| **G4 X3/X2** 指标口径定义缺口 | **高**。原文明示的标签（如 `ROE_UNSPECIFIED`）未被登记公式证明。查事实=拿一个标准公式去套一个含义不明的数字。 | 原文明示 ROE 用"期末净资产"，事实里存在"加权 ROE"，二者数值接近 ⇒ 拿错误口径判定 | ❌ **不可裸拆**；须先由 **C registry 填原文出处映射**（这正是 C 的职责；`unresolved_source_definition_issue` 的唯一合法放行口） |
| **G5 X3/X2** `_source_semantic_issue` | **中~高，需分流**。内含多种情形：`X3 重复数值无法定位`（:95）、`X3 表格科目/期间冲突`（:108）、`X3 分部口径`（:179）、`X2 CAGR/占比/百分点缺定义`（:153/:157/:165）。**分部（segment）与"重复数值定位"是硬语义，不可猜**。 | 分部数字被当全公司、或重复数字选错一个 ⇒ 造假 | ⚠️ **半数不可延迟**；须逐子条件判断（见 §3） |
| **G6 X3** 冻结源绑定冲突 | **极高**。冻结源维度与模型绑定**直接冲突**（如源表头写"营业收入"，模型标成"净利润"）。查事实=用错误科目去查。 | 模型错标科目但金额相近 ⇒ 假 verified | ❌ **不可延迟**（硬守卫） |
| **G7 X2** 缺登记方法 | **中**。账户在 `METRIC_SPECS` 里但 `definition_id` 不符 ⇒ `metric_spec` 返回 None。 | 无授权公式时，若事实查询走了通用路径可能误配 | ⚠️ 与 G4 同源，须随 C registry 一起处理 |

### 2.1 特别关注 X3（口径定义缺口）——"含义不明的数字"会不会被当"已核实"？

**会。** 机理：`_all_blockers` 若对 G4 短路放行到 `:440`，代码会用 `scope='合并'`（`:423`）、`nr='未扣非'`（`:424`）、并在 `:441` 因 `spec is None` 走 `:450` 通用 `_lookup`。此时一个"原文语义未定的数字"会被拿去和"某个恰好同科目的合并事实"比较——`_lookup` 只校验维度，不校验**原文明示口径是否等于登记口径**。这正是护城河要求阻止的行为。

**佐证**：`source_semantics.py` 相关注释反复强调"不能依相近事实金额或双模型一致授权映射"（`:735`）、"不能借用其他科目、比率或相近金额核查"（`:723`）。

---

## 3. 哪些 guard 可以安全"延迟判定"？建议代码结构

### 3.1 可延迟判定（safely deferrable）清单

**定义**："延迟判定"= 先做事实查询，但**只有在事实存在且口径/维度**已由**来源或已归档澄清**锁定的情况下**才允许改变 verdict**；否则维持弃权。

| guard | 可延迟？ | 前置条件（必须同时满足才放行到事实查询） |
|---|---|---|
| G3 source_scope | ✅ **可**（这也是本 P0 唯一真正有价值的靶点） | `source_scope_dimensions(claim)['scope_header'] ∈ {合并,母公司,来源表}` 或 存在**可核验的** `scope_declaration`（`scope.py:409–418` 已实现） |
| G4 definition（C registry） | ✅ **可**（C1 已实现） | `source_method_registry.validated_mapping(claim) is not None`（出处四元组完整 + 哈希自洽） |
| G2 binding | ✅ **可**（仅当绑定被来源证明锁定） | `binding_status == 'single'/'agreed'` 或 澄清后 `caliber_hint[0] ∈ {合并,母公司}`（`scope.py:595–601` 的既有逻辑） |
| G5 source_semantics | ⚠️ **部分可**（仅非分部、非重复定位子条件） | `_source_semantic_issue` 仅在"分部维度""重复数值"命中时可延迟**失败**；CAGR/占比/百分点缺定义子条件**保持弃权** |
| G6 source_binding | ❌ **不可** | 该 guard 本身就是"源与模型冲突"，无中间态 |
| G1 X6 parse | ❌ **不可** | 结构未解析 |
| G7 缺登记方法 | ⚠️ 随 G4 一起 | 同 C registry |

### 3.2 建议代码结构（伪代码级，verdict 零变化风险最小）

核心思想：**把"短路"改成"分级放行"，但放行条件仍是"来源/澄清已锁定维度"**，而非"先查事实"。

```python
def _all_blockers(self, claim, result):
    blockers = list(result.blockers)
    def add(...): ...

    add(result.abstention_code or 'X1', 'primary', result.reason or '无法核查')

    # --- 阶段 A：求值（不变），但把 source_failures 分成两类 ---
    source_failures = []
    hard_failures  = []   # 不可延迟：G1/G6/G5-分部/G5-重复定位
    soft_failures  = []   # 可延迟（但需 prerequisite）：G3/G4/G7/G5-其余

    for kind, issue in [(...source_scope...), (...definition...), (...source_semantics...)]:
        if issue:
            add(issue[0], kind, issue[1])
            source_failures.append(issue)
            (hard_failures if _is_hard(kind, issue) else soft_failures).append(issue)

    # --- 阶段 B：短路判定 —— 改为"只有在硬失败/解析失败/绑定失败时才短路" ---
    # 关键：软失败是否放行，取决于"维度是否已被来源或澄清锁定"，
    #       而不是"是否想查事实"。
    dimension_locked = (
        _scope_locked(claim)          # scope_header ∈ {合并,母公司,来源表} 或可核验 declaration
        and _binding_locked(claim)    # binding_status ∈ {single, agreed}
        and _definition_locked(claim) # C: validated_mapping(claim) is not None
    )

    if (hard_failures or claim.parse_status != 'parsed'
            or result.abstention_code in {'X3','X6'} and not dimension_locked
            or claim.binding_status in {'unknown','unresolved','disagreed','pending'}):
        add('X3','fact_lookup', '源维度或语义前置验证未通过，依赖事实查询未执行',
            lookup_status='not_executed')
        if self.enable_probe:
            ...
        return blockers                      # 弃权保持不变

    # --- 阶段 C：仅当维度已锁定，才执行事实查询（沿用现 :440–452） ---
    ...
```

**verdict 零变化的最小保证策略（三选一，按风险递增）：**

1. **最保守（推荐先做）**：**不改 `_all_blockers` 的短路逻辑**，而是把 G3 的"口径未声明"通过**任务前置的 `--scope-declaration` / 分组口径澄清**解决。此时 `_source_scope_issue` 在 `_verify`（`:554`）与 `_all_blockers`（`:413`）就已返回 None，级联自然不触发——**零代码风险，且已实测 +5 verified**。
2. **中风险**：新增 `dimension_locked` 判定（如上），只在**同时**满足"scope 锁定 + binding 锁定 + definition 锁定"时放行软失败条目。任一条缺失→维持短路。
3. **禁止**：直接把 `:428` 的 `source_failures` 从 OR 条件里删掉（让软失败也去查事实）——这会打开 §2 表格里的所有造假路径。

**判定 `_is_hard` 的建议实现（对齐 §2）**：

```python
def _is_hard(kind, issue):
    code, reason = issue
    if kind in {'source_parse', 'source_binding'}:
        return True                                   # G1/G6 硬
    if kind == 'source_semantics':
        if '重复数值' in reason or '局部位置' in reason:   # judge.py:95
            return True
        if '分业务、分部、产品或客户口径' in reason:        # judge.py:179
            return True
        if 'CAGR' in reason or '占比' in reason or '百分点' in reason:  # :153/:157/:165
            return True                               # 缺定义，保持弃权
    return False                                       # G3/G4/G7 视为软（需 prerequisite）
```

---

## 4. `_all_blockers` 的调用方与 verdict 承载字段

### 4.1 调用方

唯一调用方：**`Judge.verify`**（`judge.py:326`）。

```python
# judge.py:333–346
result = self._verify(claim)                     # ← 真正产生 verdict 的地方
...
if result.status == 'abstained':
    result.blockers = self._all_blockers(claim, result)   # :346 仅补诊断
```

**注意**：`_all_blockers` **只在 `status=='abstained'` 时才被调用**（`:345`），且返回值**只赋给 `result.blockers`**——它**不写回** `status`/`abstention_code`。所以：

> ⚠️ **拆 `_all_blockers` 的短路本身，不能改变任何 verdict。** verdict 早在 `_verify`（`:503–825`）里由那些 `abstain(...)` 早早 return 决定了。

### 4.2 verdict 的真正来源：`Judge._verify` 的守卫级联

`_verify`（`:503`）内的 `abstain(reason, evidence, code)` 内联函数（`:504–506`）才是 verdict 工厂。其守卫顺序（本 P0 相关）：

| 行号 | 守卫 | 弃权码 |
|---|---|---|
| `:524–531` | 币种非 CNY / 未知 | X3 / X1 |
| `:532–535` | 金额区间不可解释 | X2 |
| `:536–537` | `parse_status != 'parsed'` | **X6** |
| `:538–539` | `modality != 'fact'` | X2 |
| `:540–544` | `unresolved_source_definition_issue` | **X3/X2**（C 靶点） |
| `:545–548` | `_source_semantic_issue` | X3/X2 |
| `:549–553` | `source_binding_issue` | X3 |
| `:554–557` | `_source_scope_issue(strict=self.require_dual_binding)` | **X3**（本 P0 5 条的真凶） |
| `:558–561` | `binding_status` 未定 / `'未知' in caliber_hint` | **X3** |
| `:594–595` | 缺公司/科目/期间 | X3 |
| `:600–610` | 单位量纲与科目不符 | X3 |

**结论**：**P0 若只动 `_all_blockers`，verdict 一条都不会变；必须同时（或改为）处理 `_verify` 里的对应守卫**（`:540` G4、`:554` G3、`:558` G2）。这与 A 改造"零回流"设计一致——A 也仅写诊断。

### 4.3 verdict 承载字段（判定"是否变化"的比对字段）

以 `VerificationResult` 为准（`models.py`；被 `test_probe_does_not_change_verdict.py:20–21` 明确列举）：

```
status, error_type, abstention_code, expected, observed,
display_value, correction, operands, rule_ids, reason
```

**红线比对**：P0 任何改动后，OFF vs ON 的这 10 个字段必须逐条一致（除**有合法澄清/映射**的靶点条目），且 `runs/rp-off` 与 `runs/rp-on` 的三元组 `(status, abstention_code, error_type)` 差异为 0。

---

## 5. 红线声明：**"口径未定义 → 仍弃权"必须保留**

### 5.1 对 5 条 found 的最终裁定（重要修正）

主理人任务描述假设"5 条 found 全部是 X3 且口径缺口未解决"。**实测修正如下**：

**这 5 条确实是 X3，但不是"C（口径映射）要解决的指标定义缺口"，而是"报表口径（合并/母公司）未唯一声明"缺口。**

实测证据（对 `runs/rp-on/results.json` 的 claim-00001/00002/00003/00007/00012 逐条复算）：

```
claim-00001 1. 公司2023年营业收入为4.96亿元
  scope_dimensions: {"scope_header": "", "provenance": "source_text_only"}
  _source_scope_issue(strict=True): ('X3', '原文未唯一声明合并或母公司报表口径；...')
  unresolved_source_definition_issue: None      ← C 靶点为 None！C1 registry 无效
  _source_semantic_issue: None
  source_binding_issue: None
```

5 条的 blocker 列表结构完全相同：
```
X3 primary     原文未唯一声明合并或母公司报表口径…
X3 source_scope 同上
X3 fact_lookup  源维度或语义前置验证未通过，依赖事实查询未执行  lookup=not_executed
PROBE diagnostic_probe  …（A 旁证）
```

### 5.2 C 与 P0 的关系：**C1 registry 对 P0 的 5 条无效**

- C1 改的是 `unresolved_source_definition_issue`（`source_semantics.py:698–705`，`validated_mapping` 命中则 return None）。
- 但 5 条的 `unresolved_source_definition_issue` **本就是 None**（它们的 `semantic_review_required` 为假——没有 `source_account_unresolved` 以 `_UNSPECIFIED` 结尾）。
- 因此**填 C registry 对让这 5 条转 verified 毫无帮助**。它们需要的是**口径声明**（`--scope-declaration` / 分组口径澄清），走的是 `scope.py` 路径，不是 `source_method_registry.py` 路径。

### 5.3 P0 能否安全把 5 条转为 verified？——**能，但前提是"补齐口径声明"，不是"拆守卫"**

实测（本报告唯一改动生态的实验，产物写在新目录 `runs/probe-scope-test/`，未覆盖既有基线）：

```bash
py -3.12 -m mingjing.cli check \
  --draft samples/real_broker_p42_draft.md \
  --corpus "data/corpus/reports/920493_并行科技" \
  --report-date 2024-04-22 --out runs/probe-scope-test \
  --mode kernel --scope-declaration 合并
# => summary: {"verified": 5, "violated": 1, "abstained": 21}
```

| 指标 | 无声明 | `--scope-declaration 合并` |
|---|---|---|
| verified | 0 | **5** |
| violated | 1 | 1 |
| abstained | 26 | 21 |
| 弃权率 | 96.3% | **77.8%** |

5 条 verified 全部**带真实证据 + 正确公式 + 显式口径 provenance**：

| claim | 原文 | observed | expected | display | formula |
|---|---|---|---|---|---|
| 00001 | 营业收入4.96亿 | 496000000.00 | 495671818.88 | 4.96亿元 | 披露值 495671818.88 / 单位倍率 1e8 |
| 00002 | 营收同比58.5% | 0.585 | 0.584745… | 58.5% | (495671818.88−312776998.45)/312776998.45 |
| 00003 | 毛利率25.6% | 0.256 | 0.2563 | 25.6% | 披露值 0.2563 / 单位倍率 0.01 |
| 00007 | 资产负债率69.4% | 0.694 | 0.6944 | 69.4% | 披露值 0.6944 / 单位倍率 0.01 |
| 00012 | 经营现金流净额61百万元 | 61000000 | 61253669.05 | 61百万元 | 披露值 61253669.05 / 单位倍率 1e6 |

每条 evidence 指向真实年报页（`p8`/`p84`/`p87`/`p88`），basis 显式写明：

> 「报表口径来自用户补充澄清，非原文明示；资源：scope-clarification-1b5320d7814965925fa7；SHA-256：351ae4e5…」（`judge.py:365–368`）

**判定：这 5 条转 verified 是合法的**——因为它们与"用户/任务显式声明合并口径"绑定，且证据链完整。**但这要求引入一个口径声明输入**，属"补齐口径"而非"拆守卫"。若在**无声明**情况下强行拆 G3 短路，则等价于**默认猜测合并口径**，属造假。故：

> **明确结论（对 C 依赖的修正）**：P0 不需要等 C registry 填充即可安全解锁这 5 条，但其安全的充要前提是**提供可核验的口径声明**（`--scope-declaration 合并`，或分组口径澄清 `scope-clarifications.v2`）。在口径声明缺位时，5 条**必须继续弃权**——这条红线成立。C registry 解决的是另一批条目（`*_UNSPECIFIED` 指标定义缺口），与这 5 条不重叠。

### 5.4 红线守卫清单（P0 必须逐条保留）

1. `_verify:554` `_source_scope_issue` 在**无有效口径声明**时必须 return X3（不得默认合并/母公司）。
2. `_verify:540` `unresolved_source_definition_issue` 对**无 registry 映射**的 `*_UNSPECIFIED` 必须 return X3/X2。
3. `_all_blockers:413` 的 G3/G4 必须在**维度未锁定**时触发短路（`:428`）。
4. `_verify:536` X6、`:549` source_binding、`:538` modality、`:600` 量纲——**一律不可延迟**。
5. P0 改动后回归必须证明：**除"已提供合法口径声明/registry 映射"的靶点外，其余条目 10 个 verdict 字段逐一零变化**。

---

## 附：验证命令（供工程师/QA 复现）

```bash
cd "C:/Users/xy/Desktop/金融人工智能竞赛/Light_Mirror"

# 基线 OFF
py -3.12 -m mingjing.cli check --draft samples/real_broker_p42_draft.md \
  --corpus "data/corpus/reports/920493_并行科技" --report-date 2024-04-22 \
  --out runs/rp-off --mode kernel
# => verified 0 / violated 1 / abstained 26

# 基线 ON（A 旁证）
py -3.12 -m mingjing.cli check --draft samples/real_broker_p42_draft.md \
  --corpus "data/corpus/reports/920493_并行科技" --report-date 2024-04-22 \
  --out runs/rp-on --mode kernel --enable-probe
# => verified 0 / violated 1 / abstained 26（verdict 零变化）

# P0 安全形态验证（补口径声明）
py -3.12 -m mingjing.cli check --draft samples/real_broker_p42_draft.md \
  --corpus "data/corpus/reports/920493_并行科技" --report-date 2024-04-22 \
  --out runs/probe-scope-test --mode kernel --scope-declaration 合并
# => verified 5 / violated 1 / abstained 21   ← 5 条 found 全部安全转正
```

## 附：证据文件索引

- `mingjing/judge.py`：`_all_blockers` `:400–453`（短路 `:439`）；`verify` `:326–370`（调用 `:346`）；`_verify` `:503–825`；`_probe_fact_lookup` `:373–398`
- `mingjing/source_semantics.py`：`unresolved_source_definition_issue` `:694–737`（C1 放行 `:698–705`）；`source_binding_issue` `:738–766`；`semantic_review_required` `:642–647`
- `mingjing/scope.py`：`source_scope_dimensions` `:349–425`（scope_header 未定 → `''` `:367`）；`apply_scope_clarification` `:542–602`
- `mingjing/metrics.py`：`METRIC_SPECS` `:70`；`metric_spec` `:75–85`（`definition_required` 校验）
- `mingjing/source_method_registry.py`：`validated_mapping` `:99–116`；registry 空表 `:29`
- 基线产物：`runs/rp-off/results.json`、`runs/rp-on/results.json`、`runs/probe-scope-test/results.json`
- 相关既有报告：`docs/deliverables/baseline-report.md`、`docs/deliverables/ac-probe-findings.md`、`docs/deliverables/ac-refactor-plan.md`
