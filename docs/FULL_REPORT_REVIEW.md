# 全文研报独立真人盲标

`scripts/full_report_review.py` 是开发验收工具，不改应用内核、Harness 插件或生产配置。它补足 A/P 单元格真值包不能证明的全文 occurrence 金标。缺少三份完整真人签认时，全文金融提及召回保持 `unknown`，不能以机器账本处理率替代。

2026-10-07 EPS原始单元格单位和增长列修复后，最新可发审目录为 **`runs/full-report-blind-dev3-v7/`**，任务入口为该目录的 `tasks.json`，审阅者分别领取 `reviewer-1.jsonl`、`reviewer-2.jsonl`、`reviewer-3.jsonl`。同一份已曝光并行科技原研报仍是44页、1433块，执行任务SHA保持`78a7e6bc8c38d2c52bdb91123dc175cdf7773d039c633f38577b1a04c6fc5d53`。三份提交保持空白，真人验收待完成。v5/v6及各原收据继续保留；不得改旧解析锁适配新代码。

旧目录`runs/full-report-blind-dev3/`及v2至v6保留原字节。金融代码改动使其`parser.py`或`coverage.py`锁失配，当前工具会正确拒绝恢复；不要修改旧包锁以伪装兼容。A/P包`bench/v1/human_blind_v6_corrected/`继续使用：它锁定原PDF与任务清单，直接校验PDF坐标，不依赖`parser.py/coverage.py`文件锁，因此本轮没有重建。再次生成全文包须选择尚不存在的新版本目录。

盲标包只包含原任务维度、原文件路径及字节 SHA-256、完整页面/块/字符索引、三份空白提交和说明。原文保留原始金额，方便真人独立识别；工具不生成或展示机器候选位置、候选金额、绑定、判定或预测，也不读取 `facts.json`、`coverage.json` 或模型输出。索引剔除 `table_candidate` 等机器提示。原材料保持只读，输出不覆盖旧版本。

## 准备明确的开发任务

输入计划须为：

```json
{
  "schema_version": "mingjing-full-report-review-plan.v1",
  "tasks": [{
    "id": "exposed-report-01",
    "set": "development",
    "company": "原任务公司",
    "period": "2024Y",
    "report_date": "2025-03-31",
    "scope_declaration": "",
    "draft": {"path": "原研报.pdf", "sha256": "原字节完整SHA256"},
    "source_documents": [{"path": "原年报.pdf", "sha256": "原字节完整SHA256"}]
  }]
}
```

路径相对于 `--corpus` 指定的材料根，使用 `/`，禁止绝对路径、`..` 和符号链接。公司、期间、研报日期与口径来自明确的执行输入，不能从机器判定补写。本开发工具拒绝 D3 及未知集合；只准备已经曝光的开发材料，不自动扫描或选择发行人。

```powershell
python scripts/full_report_review.py prepare `
  --plan runs/full-report-review-dev3-plan.json `
  --corpus "D:\Competition\金融人工智能竞赛\mainProj" `
  --out runs/full-report-blind-dev3-v7
```

每个 `indexes/<原稿SHA>.json` 包含完整原文 `text`、物理页面与文本块。`start/end` 是索引 `text` 的 Python Unicode codepoint 半开区间，不是 PDF 字节偏移或浏览器 UTF-16 偏移。JSON 中的 `\n` 解码后算一个字符。索引记录原文件 SHA、原文内容哈希和解析代码锁；恢复会再次从原字节解析比对，单纯重算索引摘要不能伪造原文。

## 三名真人分别标注

每位审阅者只获得本人空白提交、全部原材料及通用说明，不获得其他人的标注。分别独立登记全文每一个数字提及，保留重复数字的不同原文位置。分类为：

- `financial_in_scope`：主题公司的历史金融数值，包括待补材料、口径不明或无法核查的提及。
- `financial_out_of_scope`：金融数值但明确为预测、其他公司或其他不属于本核查范围的材料；填写 `scope_reason`。
- `non_financial_numeric`：页码、日期、代码、技术规格或其他非金融数字。

每个 occurrence 必须填写：

```json
{
  "location": {
    "page_id": "索引原页ID",
    "block_id": "索引原块ID",
    "start": 100,
    "end": 106,
    "text": "该字符区间的原数字及源写单位"
  },
  "category": "financial_in_scope",
  "form": "prose",
  "binding": {
    "company": "原文公司",
    "account": "原文科目",
    "period": "2024Y",
    "scope": "合并",
    "relation": "value",
    "base_period": ""
  },
  "unit": "万元",
  "currency": "CNY",
  "scope_reason": null,
  "notes": "真人据原文独立确定的依据"
}
```

这只是结构说明，不是已签认样本。`location.text` 必须与原文精确相同，包含明确的源写单位，不含尾空白；表格单位可在 `unit` 中据表头确认。`form` 可选 `prose/table/chart/header/other`。金融绑定的六字段都要填写，未知维度用空字符串，不以相近金额选择口径。非金融条目的 `binding/unit/currency` 填 `null`。

所有原页都要有 `page_reviews`，填写 `reviewed` 或 `unreadable` 及说明。不可读页面保留为未完成验收，不能被空列表或签认声明掩盖。空白 `occurrences: null` 不可签认；真人确认零项可以填写 `[]`，但三个零项提交也不会产生 kappa 或可评分共识。扫描原稿的人工 OCR 金标需要另行可审查的转录工程，本工具不伪造字符定位。原页缺失文本层，或者原块无法映射到字符区间时，即使三人都填写 `reviewed`，合并仍标记 `pending_unindexed_source_pages` 并列出原页与原因，不生成内容共识；空白页与扫描页的区分也需另行可审查地确认。

本人填写 `reviewer.id/name/profession`、`reviewer_kind: human`、`independent: true`、`attested: true`、带时区的 `signed_at` 以及 `completeness_attested: true` 后签认：

```powershell
python scripts/full_report_review.py sign `
  --tasks runs/full-report-blind-dev3-v7/tasks.json `
  --submission reviewer-1-filled.jsonl `
  --corpus "原材料根" `
  --out reviewer-1-signed.jsonl
```

`sign` 校验所有任务、页面、原字符区间、执行任务锁和身份声明，再计算 `signed_content_sha256`。修改任何签认内容都必须本人重新签认，不能由 Agent 补全。可用 `validate` 命令复核已有签认。

## 合并完整原始评级

```powershell
python scripts/full_report_review.py merge `
  --tasks runs/full-report-blind-dev3-v7/tasks.json `
  --submission reviewer-1-signed.jsonl reviewer-2-signed.jsonl reviewer-3-signed.jsonl `
  --corpus "原材料根" `
  --out runs/full-report-review-consensus-new
```

合并输出保留三份完整原始提交和每份文件 SHA，按原稿字符区间的并集统计。某位真人没登记的提及记为 `not_marked`，继续进入发现一致率、分类一致率、六字段一致率和 Fleiss kappa 分母，不先删分歧再计算成绩。重复文本的两个位置分别计数，重复提交同一位置不能凑数。

有分歧时状态为 `pending_human_dispute_resolution`；不可读页为 `pending_unreadable_pages`；单类别或零项 kappa 记不可估计；低于 0.80 须整批重新独立标注。Agent 不裁决、选择或删除原始分歧。只有全部一致、页面完整且分类 kappa 达标时才生成 `content_consensus.jsonl`，它仍是 **身份核验待完成的内容共识**，不是专业验收通过。

`reviewer_kind`、姓名、专业自述和哈希都不能证明真人身份或资格。三位原始审阅者的独立性与资格由独立真人核验，核验凭据要关联本批任务锁及原始提交 SHA。30 张规则卡专业签发、20 项外部抽检、完整评测与新 D3 封存另行验收。本工具始终输出 `professional_acceptance: false`、`identity_verification: external_pending`、`full_report_recall: null`，不拿合成测试或空白模板冒充实际金融质量成绩。
