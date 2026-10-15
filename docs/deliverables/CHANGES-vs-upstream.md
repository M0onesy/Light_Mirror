# 相对原版（MingJing 初版）的改动清单

> 基准点：`7db2392 Publish MingJing stage source`（对应桌面「金融人工智能（原版）」归档）
> 本仓库在其之上共 5 个后续提交，其中属于本次工作线的 3 个：
> - `067d53f` feat(mingjing): A+C 改造 + 弃权诊断看板 + P0 口径澄清证据
> - `721131b` feat(c-registry): 抗"一源多标"凑数校验 + caliber 出处审计
> - `5c05e25` feat(deliverables): 报表口径澄清交互工具与全文基线
>
> 其余两个（`dc8fe3d` 换行设置、`66869cb` dev3 布局重排）不属于本次工作线：
> - `dc8fe3d`（10-08）仅换行符设置，非业务逻辑。
> - `66869cb`（10-11）**并非纯布局改动**：该提交新增了整个 `mingjing/smart/` 子系统（21 个文件，含
>   `verify.py` 1474 行、`claims.py` 830 行、`tables.py` 580 行、`pipeline.py` 585 行、`web.py` 627 行等）
>   与 `mingjing/relocation.py`、`mingjing/relocation_replay.py`，并修改了 `agent.py`、`server.py`、
>   `financial_tools.py` 等。`mingjing/` 文件数由 78 → 99。该提交早于本次工作线，属既有工作。

> ⚠️ 口径澄清：本文第一至三节所述的「核心源码改动」仅指**本次工作线**（`067d53f`/`721131b`/`5c05e25`）
> 对平台源码的改动。若统计 `7db2392..HEAD` 的**全部**差异，实为 **43 个文件、+8922 行**
> （其中绝大多数来自既有的 `66869cb`）。两者范围不同，不可混用。

---

## 一、本次工作线对平台源码的改动（4 个文件，业务逻辑层）

### 1. `mingjing/judge.py`（A 改造）
- **唯一守卫短路点** `_all_blockers` 之后新增**只读旁证探查** `_probe_fact_lookup()`
- 新增构造参数 `enable_probe: bool = False`
- 命中枢卫时仍原样保留 `lookup_status='not_executed'`，**绝不**写回 verdict/status
- 旁证只暴露 `probe_status` / `probe_fact_id` / `probe_publish_date`，**金额零泄漏**
- ⚠️ diff 显示的 1423 行差异绝大多数是 **CRLF↔LF 换行符差异**（git diff 未忽略），实际逻辑改动约 +42 行
- **verdict 结论机制一字未改**（红线：护城河不可破）

### 2. `mingjing/cli.py`（A 改造）
- 新增命令行开关 `--enable-probe`（默认关）
- `Judge(...)` 构造点接入 `enable_probe`

### 3. `mingjing/source_method_registry.py`（C 改造 · 新建）
- 券商自定义指标 → 口径映射注册表（内容寻址，附 SHA-256）
- 常量：`SCHEMA='source-method-mapping.v1'`、`registry: list[dict] = []`（空表，必须由用户提供）
- 关键校验 `_mapping_evidence_ok`（9 项）+ 抗凑数校验 `_shared_source_accounts`（一源多标 >2 账户即拒绝）
- 入口 `validated_mapping(claim)`

### 4. `mingjing/source_semantics.py`（C 改造）
- `unresolved_source_definition_issue` 体首插入 **C1 放行分支**（仅当用户提供了有证据的口径声明才放行）

---

## 二、新增交付物

### A. 报表口径澄清交互工具 `docs/deliverables/scope-clarifier/`
- `index.html`（零依赖单页，内嵌数据，离线可用）
- `scope_clarifier_data.json`（与 HTML 内嵌逐字节一致）
- `VERIFICATION.md`（验证与收口报告，含反事实实验证据）
- 设计四层防误用：**预选 + 系统建议徽标 + 动态横幅 + 硬闸门（勾选"已核对原文"）+ 命令留痕**

### B. 弃权诊断看板 `docs/deliverables/abstention-dashboard/`（React + Vite，深色终端风）
- 定位"弃权是护城河，不是缺陷"，含 Overview / RootCause / Composition / Drilldown / Simulator 五视图
- 说明：与 scope-clarifier 互补，前者讲"为什么弃权"，后者讲"如何补口径"

### C. 12 份分析报告（docs/deliverables/ 下，排除以上两个目录）
| 文件 | 主题 |
|---|---|
| abstention-architecture.md | 弃权架构 |
| abstention-prd.md | 弃权 PRD |
| ac-refactor-plan.md | A+C 改造方案 |
| ac-probe-findings.md | 旁证探查发现 |
| ac-qa-report.md | A+C 质量验收 |
| baseline-report.md | 极小基线 |
| full-baseline-report.md | 全文基线（126 条） |
| broker-report-recovery.md | 研报 PDF 找回记录 |
| p0-guard-cascade-analysis.md | P0 守卫级联短路分析 |
| p0-scope-clarification-evidence.md | P0 口径澄清证据 |
| p1-caliber-provenance-audit.md | P1 口径出处审计 |
| rp-scope-qa-report.md | 口径澄清 QA 报告 |

### D. 脚本与测试
- `scripts/export_scope_clarifier_data.py` —— 快照导出（零第三方依赖，含 caliber_hint join）
- `scripts/verify_scope_clarifier.mjs` —— Playwright 回归脚本（8 项断言）
- `scripts/analyze_full_baseline.py` / `build_fulltext_draft.py` —— 全文基线分析/抽取
- `tests/test_scope_clarifier_export.py` —— 4 例单测
- `tests/test_source_method_registry.py` —— 3 例（一源多标拒绝 / 一标一源接受 / 阈值内接受）
- `tests/test_probe_does_not_change_verdict.py` —— 旁证零泄漏回归
- `samples/real_broker_full_draft.md` —— 197 条研报全文抽取大 draft

---

## 三、不可违背约束（全程守住）

1. **verdict 结论一字不变**：A 改造为只读探查，C 为新增注册表，从不改写原有裁决。
2. **护城河红线**：口径不明时仍弃权，**从不默认为合并口径**。`FINANCIAL_SOURCE_POLICY.md:13` 为证。
3. **provenance 可追溯**：澄清资源为内容寻址（draft_sha256 + 每 target 三类 SHA-256 + 偏移 + resource_id）。

---

## 四、相对原版的"没动"的地方（同样重要）

- 原版的事实抽取、公式引擎、证据链、审计报告等核心核查逻辑**完全未改**
- 原版的数据目录（`data/`，含 PDF 与语料）、`vendor/` 第三方依赖未动
- 原版的其余诊断/CLI 入口**仅**新增 `--enable-probe` 一个开关，向后兼容

---

_生成于相对原版改动梳理。本清单用于审计对照，不替代 `git diff 7db2392 HEAD`。_
