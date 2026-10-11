# 真实 Agent 开发验收输入与失败记录

在仓库根 `Light_Mirror` 使用模块入口运行，统一业务版本为 `1.0.0.dev3`。脚本只复验已曝光的开发材料，沿用当前保存的模型与密钥，不修改用户配置，也不替代独立真人金标、D3 或干净 Windows 验收。新运行使用明确 `--out evidence/<新任务目录>`，输出与既有 `evidence/dev3/` 原始记录分开；冻结EXE的源锁不因目录整理改写。

```powershell
python -X utf8 -m scripts.source_agent_acceptance --help
python -X utf8 -m scripts.source_session_acceptance --help
python -X utf8 -m scripts.source_native_web_acceptance --help
```

任务验收默认预算为 128 次模型调用、1000 次工具调用、1000000 Token 和 1800 秒；可显式提高本次任务快照的预算。`--followup-tokens` 只调整这份任务后续追问的冻结预算，不覆盖用户已保存设置。所有预算必须是正整数，布尔值、零、负数和非整数在创建目录或读取凭据之前拒绝。

分析验收可重复传入 `--expect-event`，只接受真实登记的四个事件：`accounting_policy_change`、`accounting_estimate_change`、`prior_period_error`、`disclosure_version_change`。必须同时使用 `--task-type analysis`。`policy_change` 不是有效名称，会在执行之前报错，不会先消耗真实 API 再错误判定失败。

材料定义追问必须明确写出 `--literal`，并同时检验答案和原文中的定义主体：

```powershell
python -X utf8 -m scripts.source_session_acceptance `
  --run runs/本轮冻结分析任务 --out runs/本轮定义追问验收 `
  --expect knowledge_quote --literal "追溯调整法" `
  --question "请从锁定的会计材料解释追溯调整法的定义并提供原文引用。"
```

缺少或空白 `--literal` 会在输出、凭据和网络调用前拒绝。`source_quote` 未指定 `--literal` 时保留“会计政策”默认节名；其他节名应显式指定。`--source-contains` 只接受非空的人工预审原文内容，避免空字符串使实质引用检验无条件通过。数字追问须指定登记指标和期间，例如 `--metric REVENUE_YOY --period 2023Y`。

旧任务可作为新任务的材料种子，脚本核验其已登记的原材料哈希，不要求历史代码指纹与当前代码一致，也不修改历史锁。实际追问继续使用冻结父任务：在读取密钥和提交消息之前校验父任务的材料、结果、规则和运行时锁；不兼容时保留失败原因，要求重新核查。

参数错误不创建运行目录。参数合法后，材料、配置、凭据、Agent 或会话服务构造失败均产出完整失败收据：包含失败阶段、脱敏具体原因、实际会话和已知用量，以及未执行的重放状态；未知用量不记为零。最终收据本身也进入密钥扫描。成功判据仍要求原生工具链、金融证据、非 partial 和离线重放通过，不能因参数修复而放宽。
