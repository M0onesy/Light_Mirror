# 任务执行状态与金融判定

金融判定继续只有 `verified / violated / abstained` 三态。任务的 `pending`、`partial`、`cancelled`、`interrupted` 描述执行进度，不能计作已经完成的金融弃权。

`GET /api/runs/{id}/results` 返回 `run-result-view.v1` 展示投影。冻结的 `results.json` 和原始 `summary` 不被修改，数字重放继续读取原始文件。

页面统计使用 `execution.summary`。每条结果的显示、筛选和原文着色使用 `display_status`；`execution_status` 表示该项是否真正执行。历史版本的未执行弃权占位会显示为 `pending`。新版 Agent 不为未执行项生成结果，页面还应显示独立的 `pending_items`。

`execution.processed_mentions / financial_candidates` 是金融候选提及的处理覆盖率。它不是提及召回率，也不是判定正确率。`pending_mentions` 包含尚未绑定成陈述的提及，`unbound_pending_mentions` 单独列出这部分。没有全文账本时覆盖率为未知，不能显示“全文完成”。没有原生核查 ID 记录的旧任务，`native_tool_checked_claims` 为 `null`，不能当作已测得零。

已冻结的部分完成任务保持只读。`can_resume=false` 和 `resume_reason=frozen_results_read_only` 表示不能原地恢复。`POST /api/runs/{id}/rerun` 创建完整新任务，用原任务已验证的草稿快照、外部语料路径和当前配置中的预算、供应商路由。它不是仅续跑待处理项，也不会改写旧版本。

重新核查 body 可以为空，或提供字符串字段 `draft / corpus / company / period / report_date / task_type / mode / facts`。响应是新的 Run 公共结构，包含 `request.parent_run_id`、`config_revision` 和 `rerun_scope=complete_new_task`。修改预算或模型先通过配置页保存。运行中的原任务、候选数值、判定、密钥和未知参数均拒绝。

未冻结的原生任务可以通过 `/resume` 恢复。恢复前只读验证材料、内核、Agent 和运行时锁；不兼容时必须新建任务，旧结果保持不变。

导出 ZIP 保留原始 `results.json` 字节，并添加 `execution_view.json`，记录原结果哈希和真实完成/待处理统计。`/api/replay` 的 `validation_scope=reproducibility_only` 表示重放只证明复现一致；`source_execution` 同时说明原任务是否部分完成。重放成功不能被解读为全文核查或金融质量验收通过。
