# 明镜 MingJing — A+C 改造实施方案（精确到行号与函数签名）

**版本**：v1（2026-10-14）
**作者**：Architect（software-architect-2）
**代码基准**：`git HEAD = 66869cb`（"Unify dev3 project layout and add read-only relocation replay"）
**重要前置说明**：本工作区为**部分检出**——磁盘上只有 `artifacts/ bench/ data/ docs/`；`mingjing/ tests/ scripts/ web/ runs/` **不在磁盘上**，只存在于 git HEAD。所有行号均以 `git show HEAD:<path>` 为准。

复现方式（本方案所有行号均按此口径）：

```bash
cd "C:/Users/xy/Desktop/金融人工智能竞赛/Light_Mirror"
git show HEAD:mingjing/judge.py         | sed -n '360,415p'
git show HEAD:mingjing/source_semantics.py | sed -n '650,758p'
git show HEAD:mingjing/source_method.py  | sed -n '1,337p'
```

---

## 0. 阅读结论（先给可行性判断，细节在后文）

| 改造 | 可行性 | 一句话结论 |
|---|---|---|
| **A：守卫级联短路 + 旁证探查** | **可行，但必须严格隔离** | verdict 能否一字不变，**完全取决于旁证字段是否物理隔离于 `result` 对象之外**。如果按我的方案（旁证写进"已 add 的 fact_lookup blocker 的 details"而**不动** `result.status/abstention_code/error_type/operands/expected/observed/correction`），则 verdict 结构性不变。但**会破坏 2 个现有测试**（它们硬断言 `lookup_status=='not_executed'`），需同步更新测试的期望值，或改用"新增字段而不改 `lookup_status`"的保守变体（见 §1.4 方案 B）。 |
| **C：口径映射 registry** | **代码侧完全可行；数据侧"部分可得但不足以写映射"** | 映射表结构与接入点清晰（`source_method.py` + `source_semantics.py` 是干净插入点）。**第 42 页数据已定位到 `docs/deliverables/abstention-dashboard/src/data/diagnosis.sample.json`，但只有 8 条 label、无 quote/无 hash/无 report_date**（54 是聚合计数）。**结论：C 只能做成"数据驱动框架 + 空 registry"，在拿到原 44 页研报 PDF（或 triage JSON/历史字段字典）之前不得写入任何一条映射**，否则违反"宁弃权不造假"红线。详见 §2.4。 |

**与 team-lead 描述不一致 / 需要澄清的事实**（详见 §4）：
1. `lookup_status='not_executed'` 的短路分支**只覆盖 `spec is None` 之外……不，是覆盖整个 `_all_blockers` 尾部**；且**被 2 个测试硬断言**（`tests/test_metric_coverage_registry.py:174`、`tests/test_cagr_source_preconditions.py:30`）。team-lead 说"只在 judge.py 出现 3 次"，指的是**生产码**；测试码还有 2 处消费，改值会 break。**这是 A 方案最大的隐藏成本。**
2. `lookup_status` **没有**全局枚举校验/序列化契约（`models.py` 用 `asdict`，blocker 是自由 dict）。因此新增取值 `'executed_preblocked'` 本身**不会**炸序列化——**唯一约束来自测试断言**。见 §1.3。
3. 第 42 页 54 条数据的落点 **不是** `docs/ABSTENTION_SMALL_SAMPLE.md` 的文字表格（那只有"65 个方法未知，54 集中第 42 页"的计数）。**重大发现**：`docs/deliverables/abstention-dashboard/src/data/diagnosis.sample.json` 的 `items[]` 里有 **8 条** `page:42` 记录（label + seq + x_code=X3 + gap_kind=definition），但**无 quote / 无 sha256 / 无 report_date / 无字符区间**，且 8 < 54（`seq` 跳号，是抽样）。**真正的逐条 triage（`runs/diagnostics/current-blocker-triage-20261008/diagnosis.json`）不在磁盘、不在 git HEAD**（`runs/` 被 .gitignore 且从未提交；`artifacts/*.zip` 已核验为交付包，不含 runs）。**原 44 页券商研报 PDF 全文也不在工作区。** 详见 §2.4。
4. `unresolved_source_definition_issue` 的**实际签名是 `(claim)` 单参数**（`source_semantics.py:694`），team-lead 描述正确；但它**不返回"是否口径未知"的可映射信息**，只返回 `(code, reason)`。C 的接入点需要**新增一条前置分支**，不能改它的返回类型（会波及所有调用方）。见 §2.3。

---

# Part A —— `_all_blockers` 守卫级联短路 + 旁证探查

## 1.1 事实核验（与 HEAD 逐行对齐）

### 1.1.1 `_all_blockers` 现状（`mingjing/judge.py` 367–412 行）

```
367    def _all_blockers(self,claim,result):
368        """Independent source guards and operand lookups, without creating verdicts."""
369        blockers=list(result.blockers)
370        def add(code,kind,reason,**details):
371            item={'code':code,'kind':kind,'reason':reason,**details}
372            if item not in blockers: blockers.append(item)
373        add(result.abstention_code or 'X1','primary',result.reason or '无法核查')
374        if claim.parse_status!='parsed':add('X6','source_parse','原文结构尚未完整解析')
375        if claim.binding_status in {'unknown','unresolved','disagreed','pending'}:add('X3','binding','语义绑定未知或双盲绑定不一致')
376        from .source_semantics import unresolved_source_definition_issue
377        source_failures=[]
378        source_strict=(claim.table_context.get('source_scope_required') is True
379                       or isinstance(claim.table_context.get('source_dimensions'),dict))
380        for kind, issue in [('source_scope',_source_scope_issue(claim,strict=source_strict,check_binding=False)),
381                            ('definition',unresolved_source_definition_issue(claim)),('source_semantics',_source_semantic_issue(claim))]:
382            if issue:
383                add(issue[0],kind,issue[1]);source_failures.append(issue)
384        from .source_semantics import source_binding_issue
385        binding_issue=source_binding_issue(claim,strict=source_strict)
386        if binding_issue:
387            add(binding_issue[0],'source_binding',binding_issue[1]);source_failures.append(binding_issue)
388        spec=metric_spec(claim.account,claim_dimensions(claim)['definition_id'])
389        if claim.account in METRIC_SPECS and spec is None:add('X2','definition','指标缺少来源明确的登记方法',account=claim.account)
390        scope='母公司' if '母公司' in claim.caliber_hint else '合并'
391        nr='扣非' if claim.account=='NP_PARENT_ADJ' or '扣非' in claim.caliber_hint else '未扣非'
392        # A diagnostic never bypasses a failed identity, source or unit guard.
393        # Those failures are reported together; dependent factual queries are
394        # explicitly pending rather than executed with a guessed dimension.
395        if (source_failures or claim.parse_status!='parsed' or result.abstention_code in {'X3','X6'}
396                or claim.binding_status in {'unknown','unresolved','disagreed','pending'}):
397            add('X3','fact_lookup','源维度或语义前置验证未通过，依赖事实查询未执行',lookup_status='not_executed')
398            return blockers
399        if claim.company and claim.account not in {'','UNKNOWN'} and claim.period and self.report_date:
400            if spec:
401                from .financial_derivations import derive_registered_metric
402                outcome=self._lookup(claim,scope,nr)
403                if outcome.status=='missing':
404                    derived=derive_registered_metric(self.facts,claim.company,claim.account,claim.period,scope,nr,self.report_date,
405                                  min_confidence=self.confidence_threshold,source_claim=claim,**claim_dimensions(claim))
406                    for item in derived.blockers:
407                        if item not in blockers:blockers.append(item)
408                elif not outcome.fact:add(outcome.abstention_code or 'X1','fact_lookup',outcome.reason,lookup_status=outcome.status)
409            else:
410                outcome=self._lookup(claim,scope,nr)
411                if not outcome.fact:add(outcome.abstention_code or 'X1','fact_lookup',outcome.reason,account=claim.account,period=claim.period,lookup_status=outcome.status,evidence=list(outcome.evidence))
412        return blockers
```

**关键观察（与 team-lead 描述一致，逐条已核对）**：
- 短路在 **395–398 行**；`_lookup` 调用在 **402 / 410 行**。
- `lookup_status` 生产码出现 **3 次**：397（`'not_executed'`）、408、411（`outcome.status`）。✅
- `_all_blockers` 的**唯一调用点**：`judge.py:341`（`if result.status=='abstained': result.blockers=self._all_blockers(claim,result)`）。
- `_all_blockers` **不参与 verdict**：它不触碰 `result.status/abstention_code/error_type`，只返回 blocker 列表。verdict 早在 `_verify`（`judge.py:462` 起）就已定死。✅ **这是 A 方案"verdict 不变"能被证明的根本原因。**

### 1.1.2 verdict 到底在哪定死？（证明 A 不改 verdict 的依据）

`Judge.verify`（`judge.py:323–365`）：

```
323    def verify(self, claim: AtomicClaim) -> VerificationResult:
...
330        result = self._verify(claim)          # ← verdict 在这里产生
...
340        if result.status=='abstained':
341            result.blockers=self._all_blockers(claim,result)   # ← 只填 blockers
342        if not result.rule_ids: ...
...        # advisories / strict_caliber 对 verified 的改写（348–358）
365        return result
```

`_verify` 内所有 `return` 都已经写死 `status / error_type / abstention_code`（462–1130 行）。`_all_blockers` 在 341 行**之后**才运行，且只写 `result.blockers`。**结论：只要 A 的探查代码不修改 `result` 的其它属性，也不 `return` 出 `_all_blockers` 之外，verdict 在结构上不可能改变。** 这是可被逐条比对证明的。

### 1.1.3 `lookup_status` 的"枚举/序列化契约"核验

- `models.py:143–172`：`VerificationResult` 是普通 `@dataclass`，`blockers: list[dict[str, Any]]`，`to_dict()` 就是 `asdict(self)`。**blocker 内部是自由 dict，无字段白名单、无取值枚举校验。**
- 全仓 `lookup_status` 的**读取方**只有测试：
  - `tests/test_cagr_source_preconditions.py:30` → `assert any(item.get('lookup_status') == 'not_executed' for item in result.blockers)`
  - `tests/test_metric_coverage_registry.py:174` → `assert any(x.get('lookup_status')=='not_executed' for x in result.blockers)`
  - `tests/test_binding_coverage_diagnosis.py:37,111` → 自己构造 `lookup_status='missing'`，与短路路径无关。
- 生产码读取方：`scripts/diagnose_abstentions.py`（只把 blocker 原样拷进诊断 JSON，不校验 `lookup_status` 值）；`bench/v1/prepare_annotations.py:196` 用的是 `outcome.status`（**与 blocker 的 `lookup_status` 不是一回事**，那是 QueryOutcome 的字段）。

**结论**：`lookup_status` 新值 `'executed_preblocked'` **不会破坏任何序列化契约**；唯一"会炸"的地方是上面 **2 个测试的硬断言**。

## 1.2 A 方案设计（方案 A：新增旁证子键，不改短路 blocker 的既有键）

**设计原则（红线）**：
1. `_all_blockers` 的返回值必须仍然让被短路条目**含有一个 397 行原样的 `lookup_status='not_executed'` blocker**（保持既有诊断语义"依赖查询未执行"对**判定**仍然成立）。
2. 探查结果作为**新增旁证键 `probe`**（不是顶层 `lookup_status`），只在**新加的一条 `probe_status` blocker** 或**原 397 blocker 的 details 里**记录。
3. **旁证数据流的终点是 `result.blockers` 列表**（list[dict]），它**不被 `_verify` 读、不被 `add()` 之外任何判定逻辑读**。这保证旁证零回流。

### 1.2.1 新增函数（放在 `_all_blockers` 之前，`judge.py` 第 366 行之前插入）

```python
    # === 新增：A 部分唯一的旁证探查入口（无副作用、不产生 verdict） ===
    def _probe_fact_lookup(self, claim, scope, nr, spec) -> dict:
        """对已被前置守卫短路的条目执行一次性只读事实探查。

        返回一个自包含、仅用于诊断的旁证 dict。绝不被任何判定逻辑读取：
        调用方只把它并入 blockers（诊断列表），从不写回 result 的
        status / abstention_code / error_type / expected / observed /
        operands / correction，也不 return 它到 _all_blockers 之外。
        """
        if not (claim.company and claim.account not in {'', 'UNKNOWN'}
                and claim.period and self.report_date):
            return {'probe_status': 'not_applicable',
                    'probe_reason': '缺少公司、科目、期间或回溯日期，无法探查'}
        try:
            outcome = self._lookup(claim, scope, nr)   # 与 402/410 行同一只读入口
        except Exception as exc:                       # 探查失败绝不外溢
            return {'probe_status': 'missing', 'probe_reason': f'探查异常：{type(exc).__name__}'}
        # 只暴露"是否存在唯一同维度事实"这一布尔旁证；不暴露金额，
        # 避免下游把金额当作可用证据（宁弃权不造假的护城河）。
        if outcome.fact is not None:
            return {'probe_status': 'found',
                    'probe_reason': '探查到同维度事实（仅旁证，不参与判定）',
                    'probe_fact_id': outcome.fact.fact_id,
                    'probe_publish_date': getattr(outcome.fact, 'publish_date', '')}
        return {'probe_status': 'missing',
                'probe_reason': outcome.reason or '探查未命中同维度事实'}
```

> **命名论证（回答 team-lead 关于 `probe_status` 是否合适）**：采用 `probe_status` 是对的，理由：(1) 语义上与判定用的 `lookup_status` 明确区分——`lookup_status` 描述"判定链路是否执行了查询"，`probe_status` 描述"旁证探查是否命中"；(2) 取值只做二分 `found/missing`（外加 `not_applicable`），不给下游任何可组合成 verdict 的灰度；(3) **不把 `'executed_preblocked'` 塞进 `lookup_status`**，从而**不触碰 2 个现有测试**（见 1.3）。**这是与 team-lead 原描述的关键偏离，理由充分，建议采纳。**

### 1.2.2 改造后的短路分支（替换 `judge.py` 第 395–398 行）

**插入位置：删除原 395–398 行，替换为下述 395'–400' 行**（行号以"改动后"计，因新增了 1.2.1 的函数，实际物理行号整体下移约 30 行；**给工程师的锚点是"原 395–398 行这个方法块"**）：

```python
        if (source_failures or claim.parse_status!='parsed' or result.abstention_code in {'X3','X6'}
                or claim.binding_status in {'unknown','unresolved','disagreed','pending'}):
            add('X3','fact_lookup','源维度或语义前置验证未通过，依赖事实查询未执行',lookup_status='not_executed')
            # —— A 改造新增：旁证探查（只写诊断，零回流）——
            probe = self._probe_fact_lookup(claim, scope, nr, spec)
            add('PROBE','diagnostic_probe',
                '前置守卫已阻断判定；以下为旁证探查结果，不参与任何 verdict 计算',
                lookup_status='not_executed', **probe)
            return blockers
```

**要点**：
- **397 行原样保留**（`lookup_status='not_executed'`）→ 现有 2 个测试**全部继续通过**，因为断言是 `any(... == 'not_executed')`。
- 新增的 blocker `code='PROBE'`（**不要用 X 前缀**，避免被任何按 `code.startswith('X')` 的逻辑误当作弃权码）、`kind='diagnostic_probe'`。
- 旁证落在**独立 dict**里，`result.blockers` 是 `list[dict]`，序列化天然兼容（`asdict` 只递归 dataclass 字段，list[dict] 原样）。
- **`_probe_fact_lookup` 的返回值不 `return` 到 `_all_blockers` 之外，也不赋值给 `result.*`。** 这是零回流的结构性保证。

### 1.2.3 为什么旁证不可能回流到 verdict（三重保险）

1. **时序**：`_all_blockers` 在 `_verify` 之后运行（`judge.py:341`），且 `_all_blockers` 只写 `result.blockers`。verdict 字段此时不会再被任何代码读取后重算。
2. **数据流**：`probe_*` 键只存在于 `blockers[i]` 内。下游（report/clarifications/pipeline/diagnose）对 blocker 都是**只读展示**（见 1.4 清单），**没有任何 `_verify`/规则/derivation 逻辑读 blocker**。
3. **命名**：`probe_*` 与 `lookup_status` 分离，`_lookup` 的 `outcome` 对象本身**不**被保存（只提取 `fact_id/publish_date`），金额不落盘，杜绝"下游拿旁证金额当证据"。

### 1.2.4 （可选）更保守的变体

若团队坚持 team-lead 原描述（`lookup_status='executed_preblocked'`），则：
- 需把 397 行改为 `lookup_status='executed_preblocked'`。
- **必须同步修改 2 个测试**（`test_cagr_source_preconditions.py:30`、`test_metric_coverage_registry.py:174`），把 `=='not_executed'` 改为 `in {'not_executed','executed_preblocked'}`。
- 我**不建议**此变体：它改动了既有诊断字段的语义，且引入测试改动，收益（仅仅是字段名更直白）小于成本。**推荐 1.2.1 的 `probe_status` 方案。**

## 1.3 受影响调用链逐个核验（会不会炸）

| # | 文件:行 | 与 blockers 的关系 | 新增 `PROBE` blocker + `probe_*` 键的结论 |
|---|---|---|---|
| 1 | `mingjing/judge.py:341` | 唯一调用 `_all_blockers` | ✅ 兼容。返回值仍是 `list[dict]`，赋给 `result.blockers`。 |
| 2 | `mingjing/models.py:168,172` | `blockers: list[dict]`，`to_dict()=asdict` | ✅ 兼容。自由 dict，无 schema 校验。 |
| 3 | `mingjing/report.py:182` | Markdown：`for blocker in item.get('blockers',[])`，只读 `code/account/period/reason` | ✅ 不炸。会多渲染一行 `待补依据：PROBE ...`。**建议**：渲染层过滤 `kind=='diagnostic_probe'` 或标签化为"旁证"，见 §1.5。 |
| 4 | `mingjing/report.py:240`（内嵌 JS `sourceSemantics`） | `x.blockers.map(b => code/kind/reason)` | ✅ 不炸。多显示一条 `PROBE · diagnostic_probe`。可选过滤。 |
| 5 | `mingjing/clarifications.py:68–70` | `for blocker in row.get('blockers',[]): group['blockers'].append(blocker)` | ✅ 不炸。纯拷贝。仅使分组详情多一条。 |
| 6 | `mingjing/financial_derivations.py:99,104,188,282,333,393,415` | 自有 `blockers` 列表（**独立对象**，非 `result.blockers`） | ✅ 不炸。**与该改造无关**（derivation 的 blockers 是另一套，judge 只在 406 行 `extend` 它们）。 |
| 7 | `mingjing/financial_tools.py:1792` | `detail = {key: item.get(key) ... 'blockers' ...}` | ✅ 不炸。透传。 |
| 8 | `mingjing/financial_tools.py:1501` | `outcome.to_dict()` 白名单含 `'blockers'`（**QueryOutcome 的 blockers，非本处**） | ✅ 不受影响。 |
| 9 | `mingjing/smart/pipeline.py:381` | `blockers=source.get('blockers', [])` | ✅ 不炸。透传。 |
| 10 | `mingjing/analysis.py:217` | `blockers=derived.blockers`（derivation） | ✅ 不受影响。 |
| 11 | `scripts/diagnose_abstentions.py:114` | `"all_blockers": result.get("blockers") or []` | ✅ 不炸。会出现在诊断 JSON 里（无害，甚至有用）。 |
| 12 | `bench/v1/prepare_annotations.py:196` | `'lookup_status': outcome.status`（**QueryOutcome**，非 blocker 键） | ✅ 不受影响。 |

**没有任何一处会抛异常或校验失败。** 唯一"行为变化"是诊断展示层会多一条 `PROBE` blocker——这是本改造的目的，且在 3 处展示点（report md/html、clarifications group）可控。

## 1.4 A 部分对照验证方案（证明 verdict 一字不变）

### 1.4.1 单测级（最快、可CI）

新增 `tests/test_probe_does_not_change_verdict.py`，断言：
```python
# 对同一 claim，改造前后 result 的 verdict 字段完全一致
for field in ('status','error_type','abstention_code','expected','observed',
              'display_value','correction','operands','rule_ids'):
    assert before[field] == after[field], field
# 且短路 blocker 仍存在
assert any(b.get('lookup_status')=='not_executed' for b in after['blockers'])
# 且新增旁证 blocker 存在但 kind 正确
assert any(b.get('code')=='PROBE' and b.get('kind')=='diagnostic_probe' for b in after['blockers'])
```
"before"可用**冻结的 golden results.json**（见 1.4.2）或**在测试内复制旧逻辑**产生。

### 1.4.2 运行级（冻结基线逐条比对）—— 这是给 team-lead/QA 的主证据

**基线入口（已定位，均在 git HEAD）**：

- **基线冻结产物目录（约定名，来自 `docs/DEVELOPMENT_DEV3.md:68,119`）**：
  - stable-04：`runs/source-real-stable-04/source_acceptance.json`（319 候选 / 6 verified / 0 violated / 313 abstained；X3=163 / X2=42 / X6=108）
  - stable-08：`runs/source-real-stable-08/source_acceptance.json`（314 / 6 / 0 / 308；X3=191 / X2=58 / X6=59）
  - ⚠️ **这些目录当前不在磁盘、不在 git HEAD**（`runs/` 被 `.gitignore` 且从未提交，见 §4）。**运行级比对需要先恢复这两个 run 目录。**
- **复现/重算入口**：
  1. `python -m mingjing.cli check --draft <44页研报.pdf> --corpus <材料目录> --report-date <YYYY-MM-DD> --out runs/source-real-<new> --mode kernel`（`mingjing/cli.py:500–520`，`check` 子命令）。**只读、可复现的纯内核路径**（不调模型），最适合 A 的对照。
  2. 诊断脚本（**逐条读 blockers，天然适合比对**）：`python -m scripts.diagnose_abstentions --run runs/source-real-stable-04 --out runs/ac-a-diagnosis/stable-04.json`（`scripts/diagnose_abstentions.py:main`，默认 `--run runs/source-real-stable-16-full`）。产出含 `statistics_recomputed`、`counts.by_scope_code_first_blocker`、`rows[].all_blockers`。
  3. 验收汇总器：`python -m scripts.acceptance_dev3 --out <dir> --report <pdf> [--annual ...] [--material-index ...] [--kernel]`（`scripts/acceptance_dev3.py:244–251`）。

**比对字段（verdict 一致性判据）**——对每个 `claim_id` 比对：
```
status, error_type, abstention_code, expected, observed, display_value, correction,
rule_ids, operands[].fact_id
```
**允许差异**（旁证引入的、非 verdict）：
```
blockers[*] 中 code=='PROBE' 的新增项
```

**比对命令（改造前后各跑一次，产出 diff 报告）**：
```bash
# 改造前（HEAD）
git stash            # 或先跑一次并另存
python -m mingjing.cli check --draft <pdf> --corpus <materials> \
  --report-date <date> --out runs/ac-a-before --mode kernel
python -m scripts.diagnose_abstentions --run runs/ac-a-before \
  --out runs/ac-a-diagnosis/before.json
# 改造后
python -m mingjing.cli check --draft <pdf> --corpus <materials> \
  --report-date <date> --out runs/ac-a-after --mode kernel
python -m scripts.diagnose_abstentions --run runs/ac-a-after \
  --out runs/ac-a-diagnosis/after.json
# 逐条比对（新写脚本或 jq）——判据：仅 PROBE 差异
```
产出报告：`runs/ac-a-diagnosis/verdict-diff.json`，字段 `{claim_id, verdict_before, verdict_after, verdict_equal: bool, blocker_added:[...]}`。**验收线：(a) `verdict_equal` 全为 true；(b) `by_scope_code_first_blocker` 的两组计数完全相等**（因为 `PROBE` 不是 `first_blocker`——它排在 `primary` 之后，`_first_blocker` 取 `blockers[0]`）。

> ⚠️ **注意**：`by_scope_code_first_blocker` 用 `blockers[0]`。因为 `add('X3','fact_lookup',...)` 在原 397 行**先于** `add('PROBE',...)`，所以 `blockers[0]` 仍是 `primary`（373 行最先）——**首 blocker 不变，计数不变**。✅

## 1.5 A 部分展示层过滤（可选，推荐）

为避免"旁证"被误读为"待补依据"，建议在 **2 处渲染**加过滤（**不是必须，且不应改变判定**）：

- `mingjing/report.py:182`（md）：`for blocker in item.get('blockers',[]): if blocker.get('kind')=='diagnostic_probe': continue` 之前或改为渲染成 `- 旁证探查：...`。
- `mingjing/report.py:240`（JS）：`x.blockers.map(b=>...)` → 先 `filter(b=>b.kind!=='diagnostic_probe')` 或单独区块展示。

---

# Part B —— C 部分：第 42 页券商自定义指标口径映射

## 2.1 事实核验

- `source_method.py`：337 行，`SCHEMA='source-broker-method.v1'`（18 行），`derive_source_method(...)`（169 行），`validated_source_method(claim)`（260 行），`attach_source_methods(...)`（320 行）。已有成熟工具：`_compact`(28)、`_hash`(32)、`_ref_ok`(37)、`_box_ok`(43)、`_ref`(49)、`_label_ref`(60)、`_page_for`(54)。
- `source_semantics.py`：758 行，`_text_hash`(32)、`_position`(37)、`source_definition_diagnostic(claim)`(650)、`unresolved_source_definition_issue(claim)`(694，返回 `tuple[str,str] | None`)、`source_binding_issue(claim,*,strict=False)`(730)。
- **`source_semantics.py` 内无 `lookup_status` / `blockers` 引用**（已 `git grep` 确认）。✅ 干净插入点。
- `judge.py:_all_blockers` 正是通过 380–387 行调用 `unresolved_source_definition_issue` + `source_binding_issue` 产生 `source_failures`，进而短路 X3/X2。
- **既有同类先例（强烈建议复用其模式）**：`scope.py:105 create_scope_clarification` + `scope.py:170 _clarification_target` + `scope.py:201 create_grouped_scope_clarifications` —— 用 `draft_sha256` / `target_hashes` / `resource_id`（内容寻址）冻结"用户补充口径"，且**明确声明"不能证明原文明示口径"**。C 的 registry 应**完全对齐这套 provenance 范式**，而不是另造轮子。

## 2.2 数据结构与存放位置

**推荐：新建独立文件 `mingjing/source_method_registry.py`**（不塞进 `source_method.py`），理由：
1. `source_method.py` 已是"券商简表方法证明"的**强约束身份识别器**（`_summary_ok` 对表头/版式/分区有严苛要求），49 个第 42 页条目**大多来自简表之外的页面**，混在一起会污染 `_summary_ok` 的语义。
2. registry 是**数据驱动、可外部扩充**的（未来可放 `rulepack/` 或 JSON），与"代码即证明"的 `source_method.py` 关注点不同（**数据 vs 逻辑**分离）。
3. 独立文件便于 QA 单独测试"无出处即拒绝"。

**Schema（`source-method-mapping.v1`）**：

```python
# mingjing/source_method_registry.py
SCHEMA = 'source-method-mapping.v1'

# 每条映射必须携带原文出处四元组 + 内容哈希；缺一不可。
registry: list[dict] = [
    # ---- 示例（仅示范结构；真实条目必须逐个从原PDF取证后填入）----
    # {
    #   'schema_version': 'source-method-mapping.v1',
    #   'source_account': 'ROE_BROKER_DILUTED',       # 原文未定义标签（claim.table_context['source_account_unresolved']）
    #   'canonical_account': 'ROE',                   # 映射到的登记指标
    #   'definition_id': 'metric:roe-end-equity',     # METRIC_SPECS 里已登记的 definition_id
    #   'rule_id': 'FIN-roe',                         # 可选：登记规则
    #   'source_page': 42,                            # 原文页码（int）
    #   'source_quote': '净资产收益率=净利润/期末所有者权益',  # 原文片段（用于 _ref_ok 校验）
    #   'source_span': {'start': 12345, 'end': 12378, 'text': '...'},  # 源文本字符区间
    #   'report_date': '2024-04-22',                  # 研报日期（回溯截止日）
    #   'text_sha256': '<sha256 of source_quote utf-8>',  # ← 与 _text_hash/_ref_ok 同构
    #   'provenance': 'report_original_footnote',     # 只允许原文出处枚举，见 §2.3.4
    # }
]
```

**校验函数**（放同文件）：

```python
def validated_mapping(claim) -> dict | None:
    """仅当 (1) claim 口径未定义且 (2) registry 命中且 (3) 出处可核验 时返回映射。

    绝不读金额、候选值、事实或模型输出。缺出处一律返回 None（维持弃权）。
    """
    source = claim.table_context if isinstance(claim.table_context, dict) else {}
    account = source.get('source_account_unresolved') or claim.account
    for row in registry:
        if row.get('schema_version') != SCHEMA or row.get('source_account') != account:
            continue
        # (a) 出处必须完整且内部一致
        if not _mapping_evidence_ok(row):
            continue
        # (b) 必须映射到一个真实登记的 definition_id
        from .metrics import METRIC_SPECS
        spec = METRIC_SPECS.get(row['canonical_account'])
        if spec is None or spec.definition_id != row.get('definition_id'):
            continue
        return dict(row)
    return None
```

## 2.3 接入点与代码骨架（**优先复用 `_ref_ok` / `_text_hash` / `_box_ok`**）

### 2.3.1 接入点 1：`unresolved_source_definition_issue`（`source_semantics.py:694`）

在**函数体最开头**（699 行之后、`account = ...` 之前）插入**前置放行分支**（不改返回类型）：

```python
def unresolved_source_definition_issue(claim) -> tuple[str, str] | None:
    """Agreement is not evidence for a broader/undefined original label."""
    if not semantic_review_required(claim):
        return None
    # —— C 改造新增：显式口径映射的原文验证放行 ——
    from .source_method_registry import validated_mapping
    mapping = validated_mapping(claim)
    if mapping is not None:
        # 复用既有冻结机制：把来源明文口径写进 source_dimensions，
        # 使其像"原文明示"一样被后续 _source_semantic_issue / binding 校验消费。
        _apply_verified_mapping(claim, mapping)   # 见 2.3.3
        return None                               # ← 不再产生 X3/X2
    account = claim.table_context['source_account_unresolved']
    ...  # 原逻辑不变
```

### 2.3.2 接入点 2：`source_binding_issue`（`source_semantics.py:730`）

**无需改动**：`source_binding_issue` 第一步就调用 `unresolved_source_definition_issue`（744–746 行），映射放行后它会自然走到后续 `source_dimensions_from_text(claim)` 校验。**只要 `_apply_verified_mapping` 把映射写进 `claim.table_context['source_dimensions']`，`source_binding_issue` 的 `for field in ('company','account',...)` 比对就会用**映射后的 `account`**，实现"口径从未知变已知"。**这是 C 允许改 verdict 的机制点。**

### 2.3.3 `_apply_verified_mapping`（新增，放 `source_semantics.py`）

```python
def _apply_verified_mapping(claim, mapping: dict):
    """把已通过出处校验的口径映射冻结进 source_dimensions。

    与 freeze_source_dimensions 同构：写 table_context['source_dimensions']，
    使 source_dimensions_from_text 能读到 account/definition_id。
    只写口径，不写金额。
    """
    frozen = claim.table_context.get('source_dimensions') or {}
    updated = {**frozen,
               'account': mapping['canonical_account'],
               'definition_id': mapping['definition_id'],
               'method_provenance': {
                   'schema_version': mapping['schema_version'],
                   'source_page': mapping['source_page'],
                   'source_quote': mapping['source_quote'],
                   'text_sha256': mapping['text_sha256'],
                   'report_date': mapping['report_date'],
               }}
    # 不返回 claim（本函数只修改 dict）；由上层在需要时 replace(claim, ...)。
    claim.table_context['source_dimensions'] = updated
```
> ⚠️ `AtomicClaim` 是否 frozen？见 `models.py` —— `VerificationResult` 非 frozen，但 `AtomicClaim` 是 `@dataclass(frozen=True)`（`models.py:8`）。**因此不能原地改 `claim.table_context`。** 需改为返回 `replace(claim, table_context={...})`，并让 `unresolved_source_definition_issue` 的调用方（`_verify` 531–543 行、`_all_blockers` 380–387 行）使用返回的新 claim。**这是本改造最需要注意的签名传播点**（engineer 必须核对 `AtomicClaim` 的 frozen 性）。建议实现为**纯函数**而非原地修改：

```python
def claim_with_verified_mapping(claim):   # 返回 (claim', mapping) 或 (claim, None)
    ...
```
并在此前**优先评估**：是否可让 `validated_mapping` 的放行**只通过返回值 `None`** 生效（即"不再阻断"），而**不**去改写 `source_dimensions`——因为 `_all_blockers` 与 `_verify` 的 `binding` 比对仍读原 `claim`。**若采用"仅放行、不改写"，则 verdict 可能变成 verified 需依赖 `_verify` 后续能否真的取到 fact**，风险更可控但覆盖率更低。**两种子方案二选一，见 §2.5 决策点。**

### 2.3.4 出处校验逻辑（**复用既有工具，不另造**）

```python
def _mapping_evidence_ok(row: dict) -> bool:
    # 1) 必填字段
    for key in ('schema_version','source_account','canonical_account','definition_id',
                'source_page','source_quote','source_span','report_date','text_sha256'):
        if key not in row: return False
    if row['schema_version'] != SCHEMA: return False
    # 2) 页码为正整数
    if type(row['source_page']) is not int or row['source_page'] < 1: return False
    # 3) 原文片段哈希一致（与 source_semantics._text_hash 同构，直接复用算法）
    import hashlib
    if hashlib.sha256(row['source_quote'].encode('utf-8')).hexdigest() != row['text_sha256']:
        return False
    # 4) 片段必须落在记录的原文字符区间内（与 source_method._ref_ok 同构）
    from .source_method import _ref_ok
    if not _ref_ok(row['source_span'], text=None):   # 此处无法拿到全文时至少校验区间自洽
        return False
    return True
```
> 更严格版本（**推荐**）：`validated_mapping` 接收**原PDF全文 `text` 与 `pages`**（由调用链从 `claim.table_context` / 运行上下文取），用 `_ref_ok(row['source_span'], text)` **做真校验**，并断言 `text[row['source_span']['start']:row['source_span']['end']] == row['source_quote']`。这与 `source_method.validated_source_method` 对 `source_text_sha256` 的校验完全同构。

## 2.4 第 42 页 54 条数据的**获取路径**（重大发现：**部分可得，但不足以写映射**）

**逐一排查结果（已实测）**：

| 候选位置 | 是否存在 | 结论 |
|---|---|---|
| `docs/ABSTENTION_SMALL_SAMPLE.md`（HEAD:55,116） | ✅ | 仅文字计数："65 个来源方法未知位置中 54 个集中在第 42 页"，**无逐条列表**；并称 14 次联网（`runs/diagnostics/broker-method-web-research-20261008/receipt.json`）**未取到历史字段字典** |
| **`docs/deliverables/abstention-dashboard/src/data/diagnosis.sample.json`** | ✅ **存在（重大发现）** | ✅ **含 `items[]` 逐条记录，其中 `page:42` 共 8 条**（`raw_label`/`seq`/`x_code=X3`/`gap_kind=definition`/`cause=来源指标方法未知`/`evidence_ref="第42页"`）。见下。 |
| 同文件 `composition[]`（第 167–257 行） | ✅ | `c01`：`count:65`，`x_code:X3`，`evidence_ref:"第42页集中 54/65"`，`note:"需券商模板/历史字段字典"`。**54 是聚合计数，不是逐条清单。** |
| `runs/diagnostics/current-blocker-triage-20261008/diagnosis.json` | ❌ **不在磁盘、不在 git HEAD** | ❌ `runs/` 被 `.gitignore` 且从未提交（`git ls-tree HEAD runs/` 为空；磁盘无 `runs/`）。 |
| `artifacts/`（磁盘） | ✅ | ❌ 仅 `mingjing-1.0.0.dev3-full-...zip / receipt.json`。**已 `unzip -l` 核查：zip 是交付包**（源码 + `vendor/harness` + `runtime/node_modules`），**不含任何 `runs/diagnostics/*`**。 |
| `data/corpus/reports/*`（346 目录） | ✅ | ❌ **不含券商研报 PDF**，全是公司财报（一季报/三季报/半年报/年报）。**第 42 页所属的"原 44 页券商研报 PDF 全文不在工作区**。全盘 `find -iname '*.pdf'` 未命中该研报。 |

### 2.4.1 已找到的 8 条 page-42 记录（原样）

来自 `diagnosis.sample.json` 第 418–425 行（`items[]`）：

| id | page | seq | raw_label | x_code | gap_kind | cause |
|---|---|---|---|---|---|---|
| i0001 | 42 | 5 | 承销净收入率 | X3 | definition | 来源指标方法未知 |
| i0002 | 42 | 6 | 经纪业务净收入率 | X3 | definition | 来源指标方法未知 |
| i0003 | 42 | 7 | 自营收益率 | X3 | definition | 来源指标方法未知 |
| i0004 | 42 | 8 | 资管净收入率 | X3 | definition | 来源指标方法未知 |
| i0005 | 42 | 9 | 信用业务净收入率 | X3 | definition | 来源指标方法未知 |
| i0006 | 42 | 10 | 投行净收入率 | X3 | definition | 来源指标方法未知 |
| i0007 | 42 | 11 | 净资本收益率 | X3 | definition | 来源指标方法未知 |
| i0008 | 42 | 12 | ROE（加权） | X3 | definition | 来源指标方法未知 |

### 2.4.2 关键判定：这 8 条**不能直接用于 C**

C 的硬性要求是"每条映射必须有原文出处（report_date + page + 原文片段 + SHA-256）"。这 8 条：

- ❌ **无 `source_quote`** 字段——只有指标**名称**（`raw_label`），**没有原文定义/公式片段**。
- ❌ **无 `text_sha256` / `source_span`**——全文件 `grep -ciE 'sha256|source_quote|quote|span'` = **0**。
- ❌ `evidence_ref` 仅字面 `"第42页"`，**不是可核验的字符区间**。
- ❌ 缺 **`report_date`**、缺 **`seq` 到原件字符偏移的映射**。
- ⚠️ 8 条 < 54 条，且 `seq` 有跳号（5–12），说明这只是**一个被抽样展示的子集**，不是全量。

### 2.4.3 因此 C 的正确落地边界（不变，但理由更精确）

1. **C 必须从"空 registry + 完整出处校验框架 + 测试"起步**（§2.2、§2.3）。
2. **要写入任何一条映射，前置条件是三选一**：
   - (a) 拿到**原 44 页券商研报 PDF**，用 `source_method`/`source_semantics` 的既有解析（`_ref_ok`/`_text_hash`）**从原文抽取 quote + span + hash**；**或**
   - (b) 拿到 `runs/diagnostics/current-blocker-triage-20261008/diagnosis.json`（含逐条 clause/span）；**或**
   - (c) 拿到券商原始模板/公式表或适用的聚源历史字段字典（`ABSTENTION_SMALL_SAMPLE.md:116` 指出**14 次联网均未取到**）。
3. **这 8 条 label 只能作为"待补证清单"的起点**（例如用于人工去 PDF 第 42 页定位对应行），**绝不能凭 label 名称反推公式**——那正是"按数值/名称巧合反推口径"，触碰红线。
4. **`docs/ABSTENTION_SMALL_SAMPLE.md:116` 明确**："普通财经公式和模型猜测不能替代"历史字段字典。→ **C 若拿不到 PDF/字典，长尾 54 条应维持弃权**（与产品红线一致）。

**建议给 team-lead 的下一步**：确认 (a)/(b)/(c) 三条路径中是否有任一可交付源；若均不可得，则 C 交付"框架 + 空表 + 反例测试"，并把"获取原 44 页研报 PDF"登记为独立阻塞任务。

## 2.5 C 部分两个子方案的决策点（需 team-lead 拍板）

- **子方案 C1（保守，仅放行）**：`validated_mapping` 命中且出处 OK 时，`unresolved_source_definition_issue` 返回 `None`，**不改写 `source_dimensions`**。verdict 变化取决于 `_verify` 后续能否取到 fact。**风险最低，但映射收益有限。**
- **子方案 C2（激进，改写口径）**：额外把映射写进 `source_dimensions`（需处理 `AtomicClaim` frozen，走 `replace`）。能让 `source_binding_issue` 的字段比对通过。**收益大，但触及 frozen claim 传播，改动面广。**

**我推荐先上 C1，验证通过后再评估 C2。** 理由：C2 需贯穿 `_verify`/`_all_blockers`/`freeze_source_dimensions` 三处签名，回归面大，且可能在"映射错误"时把弃权变成错误核实——**直接触碰护城河**。

---

# Part C —— 风险清单与回滚

| # | 改造点 | 风险等级 | 说明 | 回滚方式 |
|---|---|---|---|---|
| R1 | A：新增 `_probe_fact_lookup` 调用（探查会**真的跑一次 `_lookup`**） | **中** | 被短路条目原本**零事实查询**（有测试 `pytest.fail('Invalid source must not query financial amounts')`！见 `test_cagr_source_preconditions.py:27–29`、`test_metric_coverage_registry.py:172`）。**探查会对这些 claim 触发 query** → **会 break 2 个测试**。 | 加开关 `Judge(..., enable_probe=False)`（默认 False），或仅在 `--mode agent/harness` 开探查。**强烈建议默认关闭，避免 CI 破坏。** |
| R2 | A：旁证字段回流 | **低（有三重保险）** | 理论上零回流；但需 code review 确认无人读 `probe_*`。 | 删除新增 `PROBE` blocker 的 add 调用即可，无其它副作用。 |
| R3 | A：`lookup_status` 若改用 `'executed_preblocked'` | **中** | 会 break `test_cagr_source_preconditions.py:30`、`test_metric_coverage_registry.py:174`。 | 采用 `probe_status` 方案（§1.2.1），规避。 |
| R4 | A：探查性能 | **低-中** | 319 条基线中约 163 条 X3 可能被探查，每条一次 `_lookup`。内核路径为内存查询，可接受；但**若探查真的触发 derivation**，可能变慢。 | 默认关闭（R1 开关）。 |
| R5 | C：映射错误导致"弃权→错误核实" | **高（护城河）** | 违反"宁弃权不造假"。 | registry 默认**空**；每条映射需 code review 2 人 + 原文 page/quote/hash 齐全；`validated_mapping` 缺出处返回 None。 |
| R6 | C：`AtomicClaim` frozen 传播（C2） | **中** | 原地改 `table_context` 会 `FrozenInstanceError`。 | 用 `dataclasses.replace`；先上 C1。 |
| R7 | C：`unresolved_source_definition_issue` 返回 `None` 后下游假设 | **中** | 该函数被 `_verify` 与 `_all_blockers` 多处调用；放行后可能出现"未定义口径却进入事实查询"的既有路径假设被打破。 | 逐调用点核验（`judge.py:531,380`；`scripts/*`）；保守采用 C1。 |
| R8 | 运行级验证依赖 `runs/` 基线 | **高（阻塞验证）** | stable-04/08 的 `runs/` 目录**不在工作区**，无法直接跑逐条比对。 | 需 team-lead 提供 `runs/` 备份或恢复流水线；否则 A 只能做单测级证明。 |
| R9 | 第 42 页数据出处不足 | **高（阻塞 C 落数据）** | 已定位 8 条 label（`diagnosis.sample.json`），但**无 quote/hash/report_date**，不足以写映射；54 条全量与原研报 PDF 均不可得。见 §2.4。 | 交付"空 registry + 框架 + 测试"；登记"获取原 44 页研报 PDF / triage JSON"为独立阻塞任务。 |

---

# Part D —— 需要 team-lead 决策/解答的问题（Anything UNCLEAR）

1. **A 的探查是否允许真的执行 `_lookup`？** 现有测试明确禁止对"无效来源"做事实查询（`pytest.fail`）。若必须保证 CI 全绿，A 的探查需**默认关闭开关**。请确认 A 的运行环境（是否只在 agent/harness 模式生效）。
2. **`lookup_status` 采用 `'executed_preblocked'` 还是新增 `probe_status`？** 我推荐后者（不破测试）。
3. **`runs/` 基线（stable-04/08、`current-blocker-triage-20261008`）是否有备份？** A 的逐条 verdict 比对与 C 的 54 条数据都依赖它，当前工作区**完全没有**。
4. **原 44 页券商研报 PDF 在哪？** 第 42 页数据取证的前置输入。当前工作区**没有**该 PDF；`diagnosis.sample.json` 仅含 8 条 label（无出处）。请确认：(a) 原 PDF 是否可提供；(b) `runs/diagnostics/current-blocker-triage-20261008/diagnosis.json` 是否有备份；(c) 券商模板/聚源历史字段字典是否可得。**三选一才能让 C 进入"填数据"阶段。**
5. **C 选 C1 还是 C2？** 我推荐 C1 先行。
6. **`AtomicClaim` 是否确认 frozen？** 我已判定为 `@dataclass(frozen=True)`（`models.py:8`），engineer 需最终确认。

---

# 附录 A —— 精确改动清单（engineer 可直接照做）

## A-部分改动文件与锚点

| 文件 | 操作 | 锚点 | 说明 |
|---|---|---|---|
| `mingjing/judge.py` | **新增方法** | 在第 366 行（`def _all_blockers` 之前）插入 `_probe_fact_lookup`（§1.2.1） | 无副作用探查 |
| `mingjing/judge.py` | **替换** | 第 395–398 行方法块 | 改为 §1.2.2（保留 397 行 + 加 `PROBE` 旁证） |
| `mingjing/judge.py` | **可选** | `Judge.__init__` 增加 `enable_probe: bool = False` | 规避 R1 |
| `mingjing/report.py` | **可选** | 第 182 行、第 240 行 | 过滤 `kind=='diagnostic_probe'` |
| `tests/` | **新增** | `tests/test_probe_does_not_change_verdict.py` | §1.4.1 |
| `tests/` | **修改**（仅若选 R3 变体） | `test_cagr_source_preconditions.py:30`、`test_metric_coverage_registry.py:174` | 放宽断言 |

## C-部分改动文件与锚点

| 文件 | 操作 | 锚点 | 说明 |
|---|---|---|---|
| `mingjing/source_method_registry.py` | **新建** | 全文 | `SCHEMA`、空 `registry`、`validated_mapping`、`_mapping_evidence_ok` |
| `mingjing/source_semantics.py` | **插入** | 第 699 行之后（`unresolved_source_definition_issue` 体首） | C1 放行分支（§2.3.1） |
| `mingjing/source_semantics.py` | **新增** | 函数末 | `_apply_verified_mapping` / `claim_with_verified_mapping`（§2.3.3，仅 C2） |
| `mingjing/source_semantics.py` | **无改动** | `source_binding_issue`(730) | 依赖 744 行已调用 694 |
| `tests/` | **新增** | `tests/test_source_method_registry.py` | 断言：空 registry 不放行；缺出处不放行；错误 hash 不放行；出处齐备才放行且改 verdict |

---

## 附：核心证据引用（git HEAD 行号）

- `judge.py:341` — 唯一 `_all_blockers` 调用
- `judge.py:367–412` — `_all_blockers` 全文
- `judge.py:395–398` — 短路分支（A 的改造锚点）
- `judge.py:402,410` — `_lookup` 调用（探查复用同一入口）
- `judge.py:462,531–543` — `_verify` verdict 定死点
- `models.py:8,143–172` — `AtomicClaim` frozen / `VerificationResult` 自由 dict
- `source_semantics.py:32,650,694,730` — `_text_hash` / `source_definition_diagnostic` / `unresolved_source_definition_issue` / `source_binding_issue`
- `source_method.py:18,32,37,43,169,260,320` — `SCHEMA` / `_hash` / `_ref_ok` / `_box_ok` / `derive_source_method` / `validated_source_method` / `attach_source_methods`
- `scope.py:105,170,201` — 既有 provenance 范式（复用模板）
- `tests/test_cagr_source_preconditions.py:27–30`、`tests/test_metric_coverage_registry.py:172–174` — 冲突测试
- `docs/DEVELOPMENT_DEV3.md:68,119` — baseline 计数与 run 路径
- `docs/ABSTENTION_SMALL_SAMPLE.md:55,116` — 第 42 页 54 条的唯一文字记录
