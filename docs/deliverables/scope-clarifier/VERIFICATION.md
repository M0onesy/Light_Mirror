# 报表口径澄清交互工具 · 验证与收口报告

> 关联任务：P0「拆解守卫级联短路」的落地形态 —— **用户可选 + 交互式补口径**
> 关联红线：`docs/FINANCIAL_SOURCE_POLICY.md:13`「报表口径不明时……**不能默认为合并口径**」
> 状态：**已验证可用**（浏览器实测通过、机制端到端跑通、反事实验证闭环）

---

## 一、一句话结论

明镜对「报表口径未声明」的条目**弃权是正确行为**，但弃权不等于死路：我们交付了一个**零依赖的单页交互工具**，让用户在**不违反护城河红线**的前提下，逐组声明口径，把该救的条目救回来、把真问题暴露出来。

**关键发现（本轮新增）**：口径选错会**制造假警报**。同一份研报的毛利率，选「母公司」口径 → 3 条被误判为 `violated`；改为「合并」口径 → 3 条全部 `verified`，且数值吻合到小数点后 3 位。**这证明交互式澄清不是锦上添花，而是防止误报的关键控制点。**

---

## 二、交付物清单

| 文件 | 字节 | 说明 |
|---|---|---|
| `docs/deliverables/scope-clarifier/index.html` | 54,346 | 单页交互工具。**零外部依赖**（无 CDN、无外链），数据内嵌 `<script id="scopeData">`，离线可开 |
| `docs/deliverables/scope-clarifier/scope_clarifier_data.json` | 41,439 | 数据快照，与 HTML 内嵌数据**逐字节一致**（已核验 `==` True） |
| `scripts/export_scope_clarifier_data.py` | 6,118 | 快照导出脚本。零第三方依赖，严格字段校验，缺字段直接 raise |

---

## 三、工具能力（代码级核验）

交互函数齐备，非残缺品：

- `renderGroups` / `paintGroup` —— 逐组渲染与状态着色
- `applyBulk` —— 批量选择
- `computeTallies` / `updateTallies` —— 实时统计（合并组数 / 母公司组数 / 保持弃权数 / 预计可核实条数）
- `buildSpecsFor` —— 按选择构造 `selector.claim_ids`
- `generate` —— 生成 CLI 命令，三种分支：
  1. **全不选** → 明确提示「无需重算，保持弃权符合红线」，**拒绝替用户猜测口径**
  2. **全选同一口径** → 生成全篇命令 `--scope-declaration 合并|母公司`
  3. **混合选择** → 生成分组命令 `--scope-clarifications-json '<JSON>'`
- `shellQuoteJson` —— POSIX shell 转义（`'` → `'\''`），防注入
- `copyOutput` / `fallbackCopy` / `toast` —— 复制与提示

---

## 四、浏览器实测（Playwright + Edge）

```
title:        "明镜 · 报表口径澄清工具"
groupCount:   25          ← 25 组全部渲染
hasAbstain:   true
hasConsolidated: true
hasParent:    true
errors:       []          ← 零 JS 错误、零 console 错误
```

截图：`runs/_sc_tmp/scope_shot.png`（579 KB，fullPage）
顶部统计条：`126 条 | 70 条待澄清 | 25 组 | 3 条已核实`；每组含「合并 / 母公司 / 保持弃权」三选一。

**结论：工具完整可用。**

---

## 五、数据一致性核验

| 核验项 | 结果 |
|---|---|
| 快照 groups vs `full-off` groups | 25 == 25 ✓ |
| 快照 claim_ids vs `full-off` scope claim_ids | 70 == 70，集合相等 ✓ |
| HTML 内嵌数据 vs 磁盘 JSON | `embedded == disk` → **True** ✓ |
| 澄清后剩余组数 | 25 − 3（已澄清组）= **22 组 / 50 条** ✓（70 − 20 = 50） |
| 导出脚本字段校验 | 缺字段 raise，不静默 ✓ |

数据管道无漂移。

---

## 六、机制效果实验（三组对照）

同一 draft（126 条候选）、同一 corpus、同一 report_date，仅改口径声明：

| 配置 | verified | violated | abstained | 说明 |
|---|---|---|---|---|
| **A. 无澄清**（full-off） | 3 | 0 | 123 | 基线。X3=67 / X6=34 / X2=20 / X1=2 |
| **B. 澄清 3 组，毛利率=母公司**（full-scope-test） | 11 | **3** | 112 | REVENUE 转正 8 条；毛利率**误报** 3 条 |
| **C. 澄清 3 组，毛利率=合并**（反事实） | **14** | **0** | 112 | 3 条误报全部消失 |

**11 条状态变化全部来自 X3（口径未声明）**，精确印证 P0 结论：这 5 组弃权**不是指标定义缺口**，而是**报表口径未声明**，C1 指标注册表对其无效。

### B → C 差异（单变量实验，仅 3 条变化，无副作用）

| claim | 研报值 | 合并口径算得 | 母公司口径算得 | 结论 |
|---|---|---|---|---|
| claim-00010 (2023Y) | 0.26 | **0.2563** ✅ | 0.1687 ❌ | 研报=合并口径 |
| claim-00066 (2022Y) | 0.283 | **0.2826** ✅ | 0.1091 ❌ | 研报=合并口径 |
| claim-00067 (2023Y) | 0.256 | **0.2563** ✅ | 0.1687 ❌ | 研报=合并口径 |

REVENUE 组、EXP_PERIOD_RATIO 组结果**完全不变** —— 干净的因果关系。

---

## 七、provenance 冻结（防偷换）

澄清资源 `scope_clarifications.json` 为 **内容寻址**：

```json
{
  "schema_version": "scope-clarifications.v2",
  "provenance": "user_supplied_scope",
  "draft_sha256": "8f1a20bb9d49…",           // 绑定具体 draft 版本
  "groups": [
    { "scope": "合并",
      "selector": { "claim_ids": [...] },
      "targets": [ { "claim_id": "...", "start": 423, "end": 430,
                     "text_sha256": "...", "numeric_sha256": "...",
                     "candidate_sha256": "..." } ] } ],
  "resource_id": "scope-clarification-ef7703dadac3b6dc0038"   // 内容哈希
}
```

每条 target 带 **三类 SHA-256 + 原文偏移**，可复现、可审计。裁决书 `basis` 亦如实记录：
> 「报表口径来自**用户补充澄清，非原文明示**；资源：scope-clarification-ef7703dadac3b6dc0038；SHA-25…」

**不造假、可追溯、口径来源与原文来源严格区分** —— 护城河守住。

---

## 八、产品结论与建议

1. **工具可用**，零依赖、离线可开、无报错，可直接交付用户。
2. **口径线索前置到 UI（已实现）**：每组卡片显示只读「原文线索」条 + 按线索预选，构成"提示 → 用户确认"闭环。
3. **「保持弃权」始终可用（已实现）**：不会被自动放开；批量按钮可一键全部改回弃权。
4. **硬闸门兜底（已实现）**：只要仍有未核对的建议值，生成命令时输出面板顶部要求用户**显式勾选**
   「我已核对研报原文」，勾选前后命令行注释口吻随之改变 —— 使"把线索当声明用"无法无痕发生。

---

## 八·补、预选 + 闸门 交互设计（本轮新增）

### 设计动机
反事实实验证明：按原文线索选「合并」→ 33 条转正、0 假警报。但线索是**解析产物**，不是**原文声明**。
若直接预选而无可追溯痕迹，等于把线索当声明用，触碰红线。故采用**「预选 + 强提示 + 硬闸门 + 命令留痕」**四层设计：

| 层 | 机制 | 效果 |
|---|---|---|
| 1. 预选 | `suggestFor(g)`：hint 含「合并/母公司」则预选，否则保持弃权 | 降低用户操作成本 |
| 2. 徽标 | 每组卡片打「**系统建议 · 待核对**」 | 视觉上区分"建议"与"已确认" |
| 3. 动态横幅 | `updateOriginBanner()`：实时显示"N / 25 组为线索预选" | 让未核对规模可见 |
| 4. 硬闸门 | `renderGate()`：有未核对值时要求勾选「我已核对研报原文」 | 无法无痕采纳线索 |
| 5. 命令留痕 | `originComment()`：命令前置注释块如实记录来源与是否已确认 | 审计可追溯 |

### 命令注释的两种口吻（随勾选切换）
```
# 资源类型：user_supplied_scope（用户显式声明）
# 来源：其中 25 / 25 组为「原文线索」预选，尚未逐组人工核对（标『系统建议 · 待核对』）
# ⚠ 预选仅是提示，非原文声明；请核对研报原文后再执行本命令
# 依据：docs/FINANCIAL_SOURCE_POLICY.md「口径不明时不得默认为合并口径」
```
（勾选确认后，第 2、3 行改为「用户已勾选确认已核对原文」）

### 数据事实
- `caliber_hint` 取值域：仅 `合并` / `未扣非`（无其它变体，精确匹配安全）
- 25 组中 **25 组**预选为「合并」、0 组保持弃权 → **正因如此，硬闸门不可省**

### 浏览器回归测试（Playwright + Edge，8/8 通过）

| # | 检查项 | 结果 |
|---|---|---|
| 1 | 默认生成 → 闸门可见 | ✅ |
| 2 | 默认未勾选确认 | ✅ |
| 3 | 命令含「尚未逐组人工核对」警告 | ✅ |
| 4 | 勾选后闸门转「已确认」样式 | ✅ |
| 5 | 勾选后命令改为「用户已勾选确认已核对原文」 | ✅ |
| 6 | 全部改弃权 → 闸门自动隐藏 | ✅ |
| 7 | 全部改弃权 → 标题变「无需重算」 | ✅ |
| 8 | 批量按合并 → 25 组预选、25 徽标 | ✅ |

`errors: []`（零 JS / console 错误）。

> **开发中发现并修复的缺陷**：`originComment()` 因两次编辑叠加被定义两次，后定义（无 confirmed 分支）
> 覆盖了前定义，导致勾选确认后命令注释不更新（测试项 5 首次为 false）。删除重复定义后通过。
> 教训：同一函数多次改动后应用 `grep -c "function X"` 校验唯一性。

---

## 九、复现指令

```bash
# 数据快照导出（from 基线 run）
py -3.12 scripts/export_scope_clarifier_data.py \
  --results runs/full-off/results.json \
  --out docs/deliverables/scope-clarifier/scope_clarifier_data.json

# 反事实：毛利率组 = 合并
py -3.12 -m mingjing.cli check \
  --draft samples/real_broker_full_draft.md \
  --corpus "data/corpus/reports/920493_并行科技" \
  --report-date 2024-04-22 --out runs/full-scope-margin-common --mode kernel \
  --scope-clarifications-json "$(cat runs/_sc_tmp/sc_fix_common.json)"

# 端到端：全篇声明
py -3.12 -m mingjing.cli check ... --scope-declaration 合并 --out runs/full-scope-e2e
```

---

_报告生成于 P0 落地验证阶段。verdict 结论机制一字未改（A 改造为只读旁证探查、C 为新增注册表），本批仅验证「口径澄清」这一既有入口的交互化形态。_
