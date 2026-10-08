# 规则卡专业签发与外部二十项抽检

这套工具准备真人审阅材料，检查材料完整性与冻结哈希，不代替专业评审。当前模板没有真人身份、决定、评级或签认时间，均为待外部真人签认。生成模板、执行测试、填入内容哈希都不能宣布专业验收通过。

工具入口为 `python scripts/prepare_professional_review.py`。生产 `rulepack`、原始真人提交和旧验收记录不会被改写；输出目录和文件必须是新路径。

## 三十张规则卡

```powershell
python scripts/prepare_professional_review.py prepare-rules `
  --rulepack rulepack --out bench/v1/professional_pending/rules
```

`tasks.json` 固定整个规则包、每张规则卡版本哈希、依据与引文、公式、量纲、适用口径、正例、反例及边界例。`professional-review.jsonl` 留给专业人员独立填写；九项检查分别填写 `passed` 或 `failed`，决定填写 `approved`、`changes_required` 或 `rejected`，并说明理由。有失败项时不能签发 `approved`。

审阅者本人填写 `reviewer` 身份、专业资格、独立性声明、签认时间及决定后，冻结提交：

```powershell
python scripts/prepare_professional_review.py sign-rules `
  --tasks bench/v1/professional_pending/rules/tasks.json `
  --submission completed-rules.jsonl --rulepack rulepack --out signed-rules.jsonl

python scripts/prepare_professional_review.py validate-rules `
  --tasks bench/v1/professional_pending/rules/tasks.json `
  --submission signed-rules.jsonl --rulepack rulepack `
  --identity-confirmations external-rule-identities.json --out rule-review-result.json
```

整批卡片必须完整提交。规则、准则快照或卡片哈希变化后须建立新批次并重新审阅。该工具不会填写或更新规则卡 `approved_by`。

## 二十项原 PDF 抽检

```powershell
python scripts/prepare_professional_review.py prepare-external `
  --tasks bench/v1/human_blind_v6_corrected/tasks.json `
  --out bench/v1/professional_pending/external
```

抽检从冻结的 A/P 真人盲标任务全集中按公开种子与 SHA-256 排序取二十项。样本清单在审阅前冻结，不使用机器预测、金额或判定筛选；不读取 D3。少于二十项、重复任务、其他子集或损坏的源任务锁均拒绝。准备阶段只登记原 PDF 地址与哈希，不复制大型语料。

外部审阅者独立阅读原 PDF，填写 `external-review.jsonl` 的 `label`。字段与现有 `human_review` 一致，包括三态、E/X 类别、原披露数字、单位、小数位、六字段绑定、披露版本、页码、单元格坐标及原文摘录。外部审阅者不得是三名原始标注者之一。

```powershell
python scripts/prepare_professional_review.py sign-external `
  --tasks bench/v1/professional_pending/external/tasks.json `
  --submission completed-external.jsonl --corpus '外部语料目录' --out signed-external.jsonl

python scripts/prepare_professional_review.py validate-external `
  --tasks bench/v1/professional_pending/external/tasks.json `
  --submission signed-external.jsonl --corpus '外部语料目录' `
  --gold '三名真人原始一致金标/human_gold.jsonl' `
  --identity-confirmations external-inspector-identities.json --out external-review-result.json
```

校验查阅实际 PDF，检查文件哈希、页码、坐标、摘录与原披露金额。金标必须保留同批三名真人的完整原始签认，不能用机器候选替代；原始分歧、金标被修改或外部抽检不一致均保留问题清单。缺少独立人工金标时，一致结果保持未知。

## 外部身份与资格核验

各模板中的 `identity-confirmations.json` 是待填写的外部核验记录。由实际组织验收的人员核验审阅者身份、专业资格及独立性，登记核验者、时间、可回查凭据和该份原始提交文件 SHA-256。审阅者不能自己核验自己。

工具拒绝空身份、声明为模型的身份、占位或代理身份、未登记身份、资格或独立性未核验、提交文件哈希不符等情况。软件不能仅凭姓名、自声明或 JSON 中的哈希判断现实中的人是否真实、合格、独立。因此即使记录结构完整，输出也明确为 `external_attestation_recorded_authentication_requires_user_verification`，`professional_acceptance` 保持 `false`；验收组织者仍需在软件之外核验原始凭据并决定最终闸门。

测试中的身份、评级和 PDF 都是临时目录中的受控样例，不代表存在真实审阅者或已完成专业签发。
