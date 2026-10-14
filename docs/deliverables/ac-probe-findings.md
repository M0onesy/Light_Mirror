# AC 探针：基线 / 54 条指标 / 口径机制 / 测试 —— 只读调研

> 调研者：engineer-probe　｜　调研方式：**严格只读**（`git show HEAD:<path>` 读取源码、`git grep` 搜索、磁盘 `ls/find`；未 checkout / restore / 修改任何文件）
> 项目根：`C:/Users/xy/Desktop/金融人工智能竞赛/Light_Mirror`
> HEAD：`66869cb`（"Unify dev3 project layout and add read-only relocation replay"）
> 本文件是本次调研唯一新建的文件。

## 0. 总体前提（决定后续所有结论）

- **工作区源码已被清理**：`mingjing/`、`tests/`、`bench/` 的部分文件在工作区**不存在**，但全部保存在 git HEAD。
  - 证据：`ls -la mingjing` → `cannot access 'mingjing': No such file or directory`；`ls -la tests` → 同。
  - `git status --short` 显示大量 ` D <path>`（工作区删除态），源码仍在 HEAD。
- **`runs/` 目录被 gitignore 且不在工作区**：
  - 证据：`git show HEAD:.gitignore` 含 `runs/`；`git ls-tree -r --name-only HEAD | grep "^runs/"` 无输出；`ls -la runs` → `No such file or directory`。
  - 结论：**所有 `runs/...` 基线产物（含 stable-04/08 的评测结果文件）既不在 HEAD、也不在磁盘，本次无法读取**。
- **默认 `python` 是 Python 2.7.4**：证据：`python --version` → `Python 2.7.4 :: Anaconda 1.5.0 (32-bit)`；项目要求 `>=3.12`（`pyproject.toml`）。可用解释器：`py -3.12` → `Python 3.12.5`，`py -3` / `python3` → `Python 3.13.2`。

---

## 问题 1：stable-04 和 stable-08 是什么、怎么跑出来的？

### 1.1 名字出现在哪些文件

`git grep -n -i "stable-04\|stable-08" HEAD` 的全部命中集中在 **`docs/DEVELOPMENT_DEV3.md`**（另有 2 处指代具体路径名出现在 tests，见下）：

| 命中位置 | 内容摘要 |
| --- | --- |
| `docs/DEVELOPMENT_DEV3.md:68` | `原44页研报 \| 319候选；6核实、0违背、313弃权；44次真实请求、213,099 Token；A/B各提交36条，其中24条语义待审… \| runs/source-real-stable-04/source_acceptance.json` |
| `docs/DEVELOPMENT_DEV3.md:69–75` | stable-04 系列其他子项：财报分析 / 数值追问 / CAS28定义追问 / 原生Web / 源码Web / 原生自主补证 / 生成机制基准 |
| `docs/DEVELOPMENT_DEV3.md:79` | `04的313条弃权为X3=163、X2=42、X6=108；` |
| `docs/DEVELOPMENT_DEV3.md:119` | `原44页研报 \| 314条陈述，6 verified/0 violated/308 abstained；79次真实请求、440,490 Token、271.31秒；A/B各81条，69条语义待审全部提交；两层离线重放通过。 \| runs/source-real-stable-08/source_acceptance.json` |
| `docs/DEVELOPMENT_DEV3.md:120–125` | stable-08 系列其他子项 |
| `docs/DEVELOPMENT_DEV3.md:129` | `308项弃权为X3=191、X2=58、X6=59` |
| `tests/fixtures/binding_account_rejection_04.json:3` 与 `tests/test_development_receipt.py:156` | 仅出现 `source-native-reparse-stable-04/...` 路径字符串 |

**关键结论**：文档中的表把 stable-04/08 命名为"同一份**原44页研报**"的两次全文稳定点运行；结果指标正是任务背景给出的数字（04：319/6/0/313，X3=163、X2=42、X6=108；08：314/6/0/308，X3=191、X2=58、X6=59），与 `docs/DEVELOPMENT_DEV3.md:79,119,129` 逐字一致。

### 1.2 可复现的运行命令

- **CLI 定义**（`mingjing/cli.py`，`build_parser()` 起于 `:497`）：
  - 顶层子命令：`check`（`:500`）、`replay`（`:522`）、`evidence show`、`rules check`、`serve`、`package`、`smart {run,check,analysis,kb,replay}`（`:534–538`）。
  - `check` 关键参数（`:501–521`）：`--draft`(必填) `--corpus`(必填) `--out`(必填) `--mode {kernel,agent,harness,replay}` `--task-type {check,analysis}` `--scope-declaration {合并,母公司}` `--knowledge-root` 等。
  - `replay`（`:522–529`）：`--run`(必填) `--relocation-map` `--replay-mode {kernel,agent,message}`。
  - `main()`（`:558`）只 reconfig UTF-8 后 `args.func(args)`。
- **`docs/RUNBOOK.md`** 给出的实际命令（`git show HEAD:docs/RUNBOOK.md`，第 1、2 节）：
  ```
  python -m pip install -r requirements-build.lock
  python -c "import mingjing; print(mingjing.__file__)"
  python -m mingjing.cli rules check
  python -m mingjing.launcher
  python -m mingjing.cli smart run samples/smart/*.pdf --out data/workspace/dev3-smart
  python -m mingjing.cli check --draft samples/demo_draft_defined.md --corpus samples/corpus --report-date 2025-03-31 --out data/workspace/dev3-kernel --mode kernel
  python -m mingjing.cli replay --run data/workspace/dev3-kernel
  ```
- `mingjing/launcher.py`（`:177 run` / `:256 main`）、`mingjing/replay.py`（`:196 replay_run`）、`mingjing/agent_replay.py`（`:43 replay_agent`、`:14 logical_session_trace`）是离线重放入口；`agent.py`（`:97` 起有一批需要冻结资源 registry 的模块）。

**未能确认**：文档**没有给出**精确复现 stable-04/08 这两个**稳定点全文运行**的逐条命令（例如具体 draft/corpus 路径、`--task-type`、模型路由）。文档仅以表格形式登记结果 + 收据路径，命令需从 `runs/.../` 内的 manifest 反推，而该目录不存在。**原因：产物被 gitignore 且未落盘。**

### 1.3 是否已有落盘评测结果文件

**没有（在当前工作区与 HEAD 中均不存在）**。
- 文中指向的结果文件（`runs/source-real-stable-04/source_acceptance.json`、`runs/source-real-stable-08/source_acceptance.json` 等）均位于 `runs/`。
- 证据：`find . -path ./.git -prune -o -type f \( -name "*stable-04*" -o -name "*stable-08*" -o -name "source_acceptance.json" \) -print` → **无任何输出**；`runs/` 目录不存在。
- `artifacts/` 中虽有 JSON，但全部是发布/打包/收据类，**不含 stable-04/08 金融评测**：
  - `artifacts/core-review/MingJing-core-review-receipt.json`、`artifacts/packages/*.receipt.json`、`artifacts/program/build-source-lock.json`、`artifacts/receipts/**`、`artifacts/source/**receipt.json` 等（`find artifacts -name "*.json"` 全集已列）。

### 1.4 重跑所需输入数据及位置

- 需要：**draft（研报/原稿）** + **corpus（核查材料）** + 可选 `--knowledge-root`。
- 核查材料在工作区**存在**：
  - `data/corpus/reports/920493_并行科技/年报/2024-04-15_2023年年度报告.pdf`（即核查用 2023 年报）
  - 同目录另有 2024 一季报/半年报/三季报、2024/2025 年报等（`find` 已列）。
- 原稿（44 页研报）：**未在磁盘/HEAD 找到 PDF**，但**内容与身份可从 fixture 完整取得**：
  - `tests/fixtures/broker_method_p1_p42.json`（HEAD）内含**第1页与第42页完整 PDF 文本层**，字段：
    - `original_pdf_sha256 = 7a70da91863feb0ce795748619f02b21a2b68ab6ec134c5520239b658e686a9d`
    - `company = 并行科技`，`cutoff = 2024-04-22`，`origin = Exact exposed original report pages 1 and 42; not a holdout`。
  - 该 fixture 第1页文本头部含："`北交所首次覆盖报告 … 1 / 44 并行科技（839493.BJ）… 数据来源：聚源、开源证券研究所`"，第42页含"`42 / 44 附：财务预测摘要`"及资产负债表/利润表/现金流量表/主要财务比率全套。
  - 研报正式身份见 `docs/REAL_ACCEPTANCE.md:13`：**开源证券 2024-04-22 并行科技首次覆盖报告（44页，资料标识 AP202404221630960822）**，核查材料为并行科技 2023 年年度报告（披露日 2024-04-15），公司"并行科技"、期间 `2023Y`、研报日期 `2024-04-22`。
  - 生成基准（`generated-stable-*`）另用 `bench/`（工作区存在）：`bench/acceptance_cases.json`、`bench/evaluate.py`、`bench/stats.py`、`bench/v1/**`。

### 1.5 当前沙箱环境能否真的跑起来？（**核心结论**）

**不能，实测失败。** 实测命令与输出：

```
$ python -m mingjing.cli --help
'import sitecustomize' failed; use -v for traceback
C:/Anaconda/python.exe: No module named mingjing        # 默认 python 是 2.7.4

$ py -3.12 -c "import mingjing; print(mingjing.__version__)"
ModuleNotFoundError: No module named 'mingjing'         # mingjing/ 源码不在工作区

$ ls -la mingjing
cannot access 'mingjing': No such file or directory
```

- 直接原因：(a) `mingjing/` 源码在工作区被删除；(b) 默认解释器是 Python 2.7.4（与项目 `requires-python>=3.12` 冲突）。
- 说明：即使切到 `py -3.12`，也因 `mingjing` 包不存在而 `ModuleNotFoundError`。
- 附带：`app_entry.py` 依赖 `mingjing.import_integrity.assert_application_origin`，同样无法 import。
- **未能确认**：把源码 checkout 出来 + 装 `requirements-build.lock` 依赖 + 提供原17页研报 PDF 后能否复现——**本次严禁 checkout，未做该尝试**。

---

## 问题 2：「第 42 页 54 条券商自定义指标」的数据在哪？

### 2.1 文档里到底有没有 143 条逐条归因表？

**没有逐条表。** `docs/ABSTENTION_SMALL_SAMPLE.md`（HEAD，共 122 行）**只有首因分组的计数表**，不是 143 行逐条记录。

- 分组计数表位置：`docs/ABSTENTION_SMALL_SAMPLE.md:42–52`（表头 `| 首要问题 | 原文位置数 |`，行：来源指标方法未知 65 / 公司总量合并·母公司口径未明示 20 / 缺事实或公式操作数 12 / 业务或客户口径未知 9 / 占比分母或维度不完整 9 / 券商残项绑定及后续依赖 8 / 负·零基数规则前提不成立 7 / 分部维度不完整 6 / 原文结构未完成解析 5 / CAGR 起止期不完整 2）。
- 上下文：`:40` "旧全文 `source-real-stable-16-full` 的 198 个主范围弃权中，以上局部修复涉及 55 个位置；**剩余 143 个历史位置逐项分类如下**"。
- 关于 54 条的唯一陈述在 `:55`：**"…当前第 42 页解析探针：`runs/diagnostics/current-blocker-triage-20261008/diagnosis.json`。…原第 42 页重新解析只需数秒，但其中 **54 个提及仍缺明确指标方法**…"**
- 另处呼应：`:116` "**65个来源方法未知位置中54个集中在第42页**，原44页PDF没有适用的方法附注或嵌入公式表"。

**结论：那 54 条的"原始记录（页码、指标名、原文片段、归因说明）"存在于 `runs/diagnostics/current-blocker-triage-20261008/diagnosis.json`（+ `broker-residual-source-20261008/findings.md`、`broker-method-web-research-20261008/receipt.json`）中，而不是 Markdown 表里。**

### 2.2 能否逐条列出这 54 条？（**未能确认**）

**未能确认，原因：`runs/` 被 gitignore 且未落盘，`diagnosis.json` 在本环境不存在。**
- 证据：`find . -name "diagnosis.json"` → 无输出；`runs/` 不存在；`git ls-tree HEAD | grep "^runs/"` → 无。
- 因此无法给出"前 15 条完整记录"。**未做任何猜测或编造。**

可替代的**可确证**线索（部分覆盖第42页"方法未知"指标，但**不是那 54 条原始记录**）：
1. `docs/ABSTENTION_SMALL_SAMPLE.md:116` 点名的指标类别："现金、长期投资、ROE、摊薄 EPS、净负债等定义"（未授权）。
2. `docs/project/明镜_计划书打磨与开发任务手册.md:174`（HEAD）"并行科技研报的数值均未命中，记X3定义缺口"——涉及 **EBITDA、ROIC、净负债比率、EV/EBITDA**。
3. 第42页原始表内**方法未知/自定义**的指标名（从 fixture 第42页文本可读）包括：`ROE(%)`、`ROIC(%)`、`净负债比率(%)`、`流动比率`、`速动比率`、`总资产周转率`、`应收账款周转率`、`应付账款周转率`、`每股经营现金流(最新摊薄)`、`每股净资产(最新摊薄)`、`每股收益(最新摊薄)`、`EBITDA`、`其他流动资产`、`其他流动负债/其他非流动负债`、`长期投资`、`现金` 等。这些与 `mentions.py` 的 `SOURCE_REVIEW_*` 常量一一对应（见问题 3.4）。
4. 有专门 fixture：`tests/fixtures/broker_method_p1_p42.json`（第1、42页）与 `tests/fixtures/broker_residual_p42_official_balance.json`（第42页官方余额残项），以及测试 `tests/test_broker_source_method.py`。

### 2.3 结构化机器可读数据

- **目标结构化数据：`runs/diagnostics/current-blocker-triage-20261008/diagnosis.json`（不存在，无法给出记录样例）。** 同批还有 `runs/diagnostics/issuer-scope-source-audit-20261008/scope_audit.json`、`runs/diagnostics/broker-residual-source-20261008/findings.md`、`runs/diagnostics/broker-method-web-research-20261008/receipt.json`（均由 `:55,:81,:116` 引用），**全部不存在**。
- `artifacts/` 下**没有任何** `*.jsonl`；目录树（`find artifacts -type f`）中 JSON 仅为发布/打包收据（见 1.3），**无第42页提及级数据**。
- `bench/`（工作区存在）有 `bench/samples_demo.jsonl`、`bench/acceptance_cases.json`，但属生成基准/演示，**非本研报 54 条**。

### 2.4 第 42 页对应哪一份研报？

**开源证券《并行科技（839493.BJ）北交所首次覆盖报告》，2024-04-22，共 44 页**（资料标识 `AP202404221630960822`）。
- 证据：`docs/REAL_ACCEPTANCE.md:13`；fixture `tests/fixtures/broker_method_p1_p42.json` 第1页文本（"北交所首次覆盖报告…并行科技（839493.BJ）…1 / 44…数据来源：聚源、开源证券研究所"）与第42页文本（"42 / 44 附：财务预测摘要"）。
- **原始 PDF 文件名未能确认**：仓库内只以 fixture（抽取后的文本层）形式保存，未见磁盘 PDF；`docs/FULL_REPORT_REVIEW.md:25` 把 draft 写作占位符 `"原研报.pdf"`（非真实文件名）。相关 corpus 目录 `data/corpus/reports/920493_并行科技/` 只含公司定期报告（年报/半年报/季报），**不含这份券商研报**。

---

## 问题 3：口径（scope/definition）相关的既有机制

### 3.1 `derive_source_method()` —— 什么条件下返回有效结果

文件 `mingjing/source_method.py`，函数定义 `:169–257`。返回"宿主自有、无金额、方法证明 dict"或 `None`。

**前置门槛（任一不满足即返回 None）**：
- `:171` 先过 `_summary_ok(claim,text)`（定义 `:149`）：要求 `table_profile=='broker_compact'`、`layout_source=='pdf_text_line_lanes'`、`summary_source.schema_version=='source-broker-summary.v1'`、heading/citation/title 字符引用与 bbox 均有效、标题匹配"主要财务比率/财务预测摘要"、citation 以"数据/资料来源："开头、几何顺序 `heading.end ≤ title.start ≤ claim.start < claim.end ≤ citation.start`、headers 有效且 `period_source=='source_header'`。
- `:173–175` 行标签 `caption`（`row_label`）必须命中 `_LATEST` 或 `_CFO_COMPONENTS` 之一。
- `:176–178` 必须能唯一定位页码（`_page_for`）。
- `:180–182` 必须有唯一 `row` 引用。

**两条互斥的有效分支**：
- **A. `method_kind='latest_total_share'`**（`:200–229`）：`caption ∈ _LATEST` 且标题文本 == `主要财务比率`；`每股指标(元)` 小节唯一、`_LATEST ⊆ rows`、全部最新股数行落在该小节内；`_snapshot(...)` 非空（`:222`）；分子候选唯一（`:224–230`）。产出的键含 `account / definition_id / method_kind='latest_total_share' / numerator_account / numerator_source / denominator_account='END_SHARES' / denominator_anchor / snapshot_date / source_section / latest_share_rows / statutory_equivalence=False`。
- **B. `method_kind='broker_indirect_cashflow_component'`**（`:230–247`）：`caption ∈ _CFO_COMPONENTS` 且标题正则匹配 `现金流量表(元|万元|百万元|亿元)`；`_CFO_ROWS+_CF_TOTALS ⊆ rows`；行序递增、末行高于"投资活动现金流"；列对齐（`|bbox[0]-title.bbox[0]| ≤ 20`）。产出键含 `account / definition_id / method_kind / indirect_cfo_rows / next_group='投资活动现金流' / source_group_total='CFO' / statutory_equivalence=False / requires_registered_component_rule=True`。
- **否则 `:255` 返回 None**；成功时 `:257` 补 `payload['sha256']=_hash(payload)` 后返回。

**公共 payload 字段（`:184–198`，两条分支共享的前缀）**：
`schema_version(=SCHEMA)`、`method_authorization='proposal_only'`、`source_text_sha256`、`source_start`、`source_end`、`source_numeric_start`、`source_original_account`、`source_row`、`source_title`、`source_summary`、`source_headers`、`source_layout_lane`、`source_document_text_sha256`、`professional_signoff_status='pending'`、`uses_amount_matching=False`、`source_numeric_value_is_fact=False`，末尾 `sha256`。

### 3.2 `validated_source_method(claim)` —— 什么条件下返回有效结果

定义 `:260–317`。**不读取事实或候选金额**，只复核已归档的证明。

- `:264` 读 `claim.table_context['source_method_proposal']`；`not dict` 或 `schema_version != SCHEMA` → None。
- 先 `:265` 重算 `_hash(payload)`（剔除 sha256）并逐字段比对，**任一不符即 None**（`:266–282`）：`sha256`、`source_text_sha256`、`source_start/source_end`、`source_numeric_start`、`source_title`、`source_summary`、`source_headers`、`source_layout_lane`，且 `layout_source=='pdf_text_line_lanes'`、`table_profile=='broker_compact'`、`source_row` 引用+box 有效、`row_label` 文本与证明一致且出现在 `claim.text`、`uses_amount_matching is False`、`source_numeric_value_is_fact is False`、`method_authorization=='proposal_only'`、`statutory_equivalence is False`。
- 再按 `method_kind` 分支校验（`:283–313`）：
  - `latest_total_share`：`_LATEST` 规格匹配、`denominator_anchor.date` 可 `fromisoformat`、`denominator_account=='END_SHARES'`、`snapshot_date==anchor.date`、`anchor.fact_policy=='independent_official_total_ordinary_shares_at_snapshot_date'`、`anchor.use_candidate_total_shares_as_fact is False`、`source_section` 文本为 `每股指标(元)`、`numerator_source` 有效、anchor 各引用有效、`total_shares_caption ∈ {总股本(股),总股本(万股),总股本(亿股)}`、`latest_share_rows` 文本序列等于 `list(_LATEST)`。
  - `broker_indirect_cashflow_component`：`_CFO_COMPONENTS` 规格匹配、`indirect_cfo_rows` 文本序列等于 `list(_CFO_ROWS)` 且引用有效、`next_group` 文本为 `投资活动现金流`、`requires_registered_component_rule is True`。
  - 其他 → None。
- 全部通过 → `:317 return deepcopy(proof)`。

### 3.3 `unresolved_source_definition_issue(claim)` —— 判定条件

文件 `mingjing/source_semantics.py`，定义 **`:694–737`**，签名返回 `tuple[str,str] | None`。

- **硬性门槛 `:696–697`**：`if not semantic_review_required(claim): return None`。
  - `semantic_review_required` 定义 `:641–645`：要求 **`claim.parse_status=='parsed'`** 且 **`table_context['numeric_parse_status']=='parsed'`** 且 **`table_context['semantic_review_required'] is True`** 且 **`str(table_context['source_account_unresolved']).endswith('_UNSPECIFIED')`**。
- `:699` 取 `account = claim.table_context['source_account_unresolved']`。
- `:700–702`：若 `source_definition_diagnostic(claim)`（`:647–692`，覆盖 7 个标准指标类 `*_UNSPECIFIED`）命中，返回 `(diagnostic['code'], diagnostic['reason'])`，code 为 `X3`（CURRENT_RATIO）或 `X2`。
- 之后按 `account` 分派（**关键行**）：
  - `:703–706` `EPS_BROKER_DILUTED_UNSPECIFIED` / `CFO_PER_SHARE_BROKER_UNSPECIFIED` / `EQUITY_PER_SHARE_BROKER_UNSPECIFIED`：调 `validated_source_method(claim)`，若 `method_kind=='latest_total_share'` 返回 `('X2', ...)`；否则继续。
  - `:707–708` `NET_MARGIN_PARENT_UNSPECIFIED` → `('X3', 归母净利率未明示分母…)`。
  - `:709–710` `EXP_*_RATIO_UNSPECIFIED` → `('X2', 费用率缺授权分母/聚合/公式…)`。
  - `:711–712` `account in SOURCE_REVIEW_FORMULAS` → `('X2', 尚无授权输入/定义/公式…)`。
  - `:713–714` `account in SOURCE_REVIEW_PER_SHARE` → `('X3', 缺股数/分子聚合/基本摊薄定义…)`。
  - `:715–723` `meanings` 表命中（ROE/NET_MARGIN/EPS/ASSET_CASH/INVESTMENTS/BROKER_CFO_FINANCE 等）→ `('X3', '数值、单位已解析，但语义定义未确定：…')`。
  - **`return` at `:724`**：兜底 `('X3', '…'+meanings.get(account,'原文科目缺少可用的明确金融定义')+'；不能依相近事实金额或双模型一致授权映射')`。
- 即：**只要 `semantic_review_required` 为真，本函数几乎必然返回非 None**（唯一"不返回"的可能是 per-share 分支里 proposal 命中——但那仍返回 `('X2',...)`；真正返回 None 只发生在门槛不满足时）。

### 3.4 已存在的「口径映射 / 指标别名 / 方法登记表」候选位置

`git grep -n -i "METRIC_SPECS|registry|alias|mapping" HEAD -- mingjing/` 的**全部候选**：

| # | 位置 | 作用 |
| --- | --- | --- |
| 1 | **`mingjing/metrics.py`** | **权威指标登记表**：`MetricSpec` 数据类（`:18`）、`_SPECS` 元组（`:29–67`）、`METRIC_SPECS = {item.account: item for item in _SPECS}`（`:70`）、`metric_spec(account, definition_id)`（`:75`，内含 **`aliases`** 映射 `:81`：`{'REVENUE_SHARE':{'metric:customer-revenue-share'}, 'NP_PARENT_ADJ_NET':{'metric:nri-parent-after-tax'}}`）、`metric_definitions()`（`:87`）、`unified_definition_catalog()`（`:95`，合并 canonical+smart 定义）。 |
| 2 | **`mingjing/mentions.py`** | **来源级口径登记**：`SOURCE_REVIEW_LABELS` 字典（`:56–76`，中文标签→`*_UNSPECIFIED` 账户）、`SOURCE_REVIEW_SCALARS`（`:74–75`）、`SOURCE_REVIEW_RATIOS = SOURCE_REVIEW_SCALARS\|{ROIC_UNSPECIFIED,NET_DEBT_RATIO_UNSPECIFIED}`（`:77`）、`SOURCE_REVIEW_PER_SHARE`（`:78`）、`SOURCE_REVIEW_FORMULAS = SOURCE_REVIEW_RATIOS\|{EBITDA_UNSPECIFIED}`（`:79`）、`ACCOUNT_LABELS`（`:80`）、`CONTEXT_TERMS/NOISE_TERMS/FORECAST_TERMS`（`:82–84`）。 |
| 3 | `mingjing/source_semantics.py` | 消费上述常量（import 于 `:14–20`），并含 `source_definition_diagnostic`（`:647`）的 `methods` 映射（`:674–684`，账户→(canonical, gap, required)）与 `meanings` 映射（`:715`）。 |
| 4 | `mingjing/source_registry.py` | 冻结**文档资源**登记（`canonical_report_resources` `:9`、`indexed_source_metadata` `:33`），非指标别名。 |
| 5 | `mingjing/financial_derivations.py` | `:321` `{'code':'X2','kind':'registry_composition',...}`；`:479–480` 引用 `METRIC_SPECS` 判 `definition_required`。 |
| 6 | `mingjing/financial_tools.py` | `:24` import `METRIC_SPECS`；`:157` `_VERIFICATION_REPAIR_RULES` 由 `METRIC_SPECS` 的 `rule_id` 构成。 |
| 7 | `mingjing/agent.py` | `:97` 的模块白名单含 `metrics/source_registry/source_method/source_semantics`；`:168/:438/:447/:451` 等为 knowledge registry 生命周期（非指标别名）。 |
| 8 | `mingjing/dimensions.py` | `:11–12,:28` 注释强调"来源证明的别名必须在其冻结表内解析，不得成为全局别名"。 |
| 9 | `mingjing/config.py:411 / :773`、`mingjing/audit.py:272`、`mingjing/artifacts.py:22,46,85` | 分别是配置兼容别名、services registry_root、缓存/资源 registry（非金融口径映射）。 |

**结论**：现存「口径/别名/方法登记表」主要就是 **`mingjing/metrics.py` 的 `METRIC_SPECS`+`aliases`**（canonical 定义）与 **`mingjing/mentions.py` 的 `SOURCE_REVIEW_*`**（来源级未知口径清单）。**未发现**独立的"指标别名总表"或"方法登记表 vX"文件；`unified_definition_catalog()`（`metrics.py:95`）是最接近"统一口径目录"的既有 API。

---

## 问题 4：测试情况

### 4.1 测试与配置位置

- 配置：**`pytest.ini`**（HEAD）：
  ```
  [pytest]
  pythonpath = .
  testpaths = tests
  addopts = -ra
  ```
  另有 `pyproject.toml`（HEAD）`[tool.pytest.ini_options] testpaths=["tests"]`；依赖 `dev=["pytest>=8"]`、`requires-python=">=3.12"`。
- 测试目录：`tests/`，**共 193 个 `tests/test_*.py`**（`git ls-tree -r HEAD | grep "^tests/test_.*\.py$" | wc -l` = 193）。
- fixtures：`tests/fixtures/*.json`（22 个，含第42页相关 `broker_method_p1_p42.json`、`broker_residual_p42_official_balance.json`）。
- 另有前端测试：`frontend/src/Conversation.test.tsx`、`ResultsView.test.tsx`、`ScopeClarification.test.tsx`（vitest，非 pytest）。

### 4.2 与 judge / abstention / source_method 相关的测试文件

`git ls-tree HEAD | grep "^tests/" | grep -iE "judge|abstain|source_method|source_semantics|scope|sample"`：

| 测试文件 | 关联点 |
| --- | --- |
| **`tests/test_broker_source_method.py`** | 直接测 `source_method`（第42页券商方法） |
| **`tests/test_source_semantics.py`** | `source_semantics` 判定 |
| **`tests/test_dispatcher_source_semantics.py`** | dispatcher×source_semantics |
| **`tests/test_current_ratio_definition_guard.py`** | 直接 import `unresolved_source_definition_issue`（`:17,:58`） |
| `tests/test_cli_company_scope.py` / `test_scope_clarifications.py` / `test_grouped_scope_clarifications.py` / `test_grouped_scope_product.py` / `test_parent_profit_scope.py` / `test_session_scope_and_preparation.py` | 口径（scope）澄清 |
| 其他高度相关（按名） | `tests/test_analysis_growth.py`、`test_adjusted_profit_identity.py`、`test_binding_*.py`、`test_broker_residual_complete_source.py`、`test_expense_ratio_mentions.py`、`test_cagr_source_preconditions.py` 等 |

**未发现**单独名为 `test_judge.py` / `test_abstention.py` 的文件；judge/abstention 逻辑由上述 source_semantics / binding / scope 类测试**间接覆盖**（judge.py 的 X3/X2/X6 归因在 `mingjing/judge.py:_all_blockers` `:370–401`）。

### 4.3 测试能否在当前环境跑？

**不能，实测失败。**
- `tests/` 目录在工作区**不存在**（`ls -la tests` → `No such file or directory`），且 `mingjing/` 包不存在，`pytest` 无法 import 被测模块。
- 默认 `python`（2.7.4）与 `pytest>=8`/`py3.12` 不匹配。
- 结论：即便有 `pytest.ini`，在**不 checkout 源码**的前提下**无法执行任何测试**。本次未执行 pytest（受只读约束且源码缺失）。文档登记的通过数（如 `full-stable-07.xml` 1,704 passed、`DEVELOPMENT_DEV3.md:62` "798 passed、5 failed、1 skipped" 等）**无法在本环境复核**。

---

## 附：未能确认事项清单（明确列出，未编造）

1. **stable-04 / stable-08 的逐条运行命令与结果文件内容** —— 未能确认；原因：`runs/` 被 gitignore 且未落盘。
2. **第42页 54 条方法未知指标的原始逐条记录（页码/指标名/原文片段/归因）** —— 未能确认；原因：数据在 `runs/diagnostics/current-blocker-triage-20261008/diagnosis.json`，该文件不存在。仅能给出可确证的类别线索（见 2.2）。
3. **44 页研报的原始 PDF 文件名** —— 未能确认；仓库仅保存抽取后的 fixture 文本层，磁盘 corpus 无该研报，`docs` 中 draft 名为占位符 `原研报.pdf`。
4. **当前沙箱能否复现基线的肯定性结论** —— 未做尝试（严禁 checkout）；实测"能否直接跑"= **否**（ModuleNotFoundError + Python2 默认）。
5. **`runs/` 内任何 JSON/CSV/Markdown 结果文件** —— 均不存在，未读取。
