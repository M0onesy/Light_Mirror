# 唯一项目目录与历史重放

当前开发只使用 `Light_Mirror`。`mingjing/`、`kernel/`、`rulepack/`、`runtime/`、`vendor/` 等英文源码目录保持相对位置；无需从旧 `mainProj` 拼接模块或复制另一份后端。

| 目录 | 内容及处理方式 |
| --- | --- |
| `docs/project/` | 唯一计划书DOCX、PDF、开发任务MD和比赛要求，迁移保留原字节 |
| `docs/acceptance/` | 实际阶段摘要与问题，活动Markdown链接可修复位置，结论不改 |
| `artifacts/program/` | 当前冻结EXE与build/source-lock；本次不重建 |
| `artifacts/packages/`、`artifacts/source/` | 原候选与完整源码ZIP，原SHA保持；二进制通过GitHub Release分发 |
| `artifacts/core-review/` | 当前≤30MB核心审阅ZIP；不是完整原生工程 |
| `artifacts/demo/` | 实际操作MP4 |
| `data/` | 私有财报、准则及本地工作区，不进入公开Git历史 |
| `evidence/dev3/` | 原始模型、测试、构建和重放记录，不修改旧锁 |
| `evidence/relocation/` | 位置映射、搬迁收据、原字节快照及独立迁移重放输出 |

工作区 `存档/历史版本/` 是历史归档入口，旧源码、共享Git与关联worktree成组保留。它们不作为当前工作树，不接入应用搜索路径，也不通过目录链接创建第二个活动版本。

## 迁移后的冻结任务重放

原任务锁记录了执行时的绝对位置；不能改写旧JSON以适配新目录。迁移重放使用显式地图，保留旧逻辑资源身份，在独立短生命周期worker中读取新位置，并继续严格校验原件、输出锁、规则和模型请求缓存。

```powershell
python -X utf8 -B -m mingjing.cli replay `
  --run evidence/dev3/real-small-05/native-run `
  --relocation-map evidence/relocation/location-map.json `
  --out evidence/relocation/tests/small05-kernel-new `
  --replay-mode kernel
```

`--replay-mode` 可选 `kernel`、`agent`、`message`，默认 `kernel`。Agent使用冻结模型缓存重新执行原生循环，不允许联网补齐。消息级重放另给 `--session-file <冻结会话JSON>` 和 `--message-id message-0001`。

输出必须是 `evidence/relocation/` 下的新目录；已有输出、`..`、链接/junction、歧义或不匹配映射均拒绝。没有 `--relocation-map` 时保留普通重放行为。地图只解决位置变化，不授予哈希不符、缓存缺失、任务partial或金融缺证通过。实际通过项以该新目录的收据为准；历史失败和未执行记录继续保留。

## 新产物与协作

核心工具默认写 `artifacts/core-review/`，完整源码工具写 `artifacts/source/`，回归与诊断写 `evidence/`。清理工具默认仅预览，保护 `data/`、`evidence/`、`runtime/`、`vendor/`、`artifacts/` 和整个 `存档/`；不按运行数量删除证据。

公开仓库只提交协作源码、文档、许可和锁定源快照。个人配置、DPAPI、Token、真实模型缓存、原始财报、carrier、Node及100MB以上文件排除；程序与大包通过 [GitHub Release](https://github.com/M0onesy/Light_Mirror/releases/tag/mingjing-1.0.0.dev3) 下载。源码或文档的整理变更不改写EXE的冻结构建锁。
