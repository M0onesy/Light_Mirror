# 明镜 1.0.0.dev3 交付与复现指南

本版唯一工程是仓库根 `Light_Mirror`，文档原件在 `docs/project/`，验收总结在 `docs/acceptance/`，成品和压缩包在 `artifacts/`。原融合包和旧资源锁集中归档只读。交付清单以实际存在文件与收据为准，不把阶段候选当成正式金融质量认证。

| 形态 | 包含内容 | 能力和范围 |
| --- | --- | --- |
| 初赛材料 | Word更新后PDF、MP4≤5分钟且≤500MB | 2026-10-18前提交；目标/方法/验收设计与实际演示 |
| 完整开发源码 | Python/内核、工具/Prompt/Skill/MCP、数据与日志、UI、规则/依赖、上游源码许可、脚本及说明 | 与现场版一致，原生运行还需同版finance-carrier闭包 |
| 完整历史复现 | 指定任务/父子/会话/缓存、获授权原件、原源码/规则/运行时闭包 | 精确验输入输出/源码锁，不改旧manifest伪造恢复 |
| ≤30MB核心审阅 | 核心源码、插件、UI、规则、小bench/样例、测试、文档及manifest | 非完整上游/Node/carrier/语料/runs，不独立运行全部原生功能 |
| EXE候选 | 同版程序、build lock、SHA-256、使用/许可、样例及实际smoke | 稳定后一次构建；正式发布另需质量和独立环境闸门 |

共享项目不复制LOCALAPPDATA配置或DPAPI密文；导出不含密钥的配置并重新填Token与路径。未获再分发许可材料不随包，现场样例提供清晰来源和范围。明镜自有AGPLv3与第三方许可分别保留，EXE对应完整源码要可获得。

GitHub协作仓库为 https://github.com/M0onesy/Light_Mirror 。公开Git历史包含当前源码、锁定Harness源快照、许可和文档；排除个人配置、密钥、真实模型缓存、全量PDF、运行历史、carrier及开发依赖树。EXE和交付ZIP放在 [mingjing-1.0.0.dev3 Release](https://github.com/M0onesy/Light_Mirror/releases/tag/mingjing-1.0.0.dev3)，不提交100MB以上二进制。协作先建分支和PR，保留已有公开提交，不重写历史。

完整本地拷贝以本目录为唯一工作树；按需要带上 `runtime/finance-carrier/staged`、`data/` 和指定 `evidence/` 任务依赖，不复制任何LOCALAPPDATA配置或DPAPI密文。公开clone的载体通过锁定来源重建，不能把Git里缺二进制误判成工具缺代码。旧版本仅在工作区 `存档/历史版本/` 留档，不复制进当前工程。

核心审阅包位于 `artifacts/core-review/MingJing-core-review.zip`，使用 `python -X utf8 -m scripts.create_core_review` 重建。该ZIP≤30MB，含当前核心源码与唯一文档基线；不展开为第二活动开发树。修改建议通过补丁或Git分支返回。历史完整源码与候选ZIP为冻结快照，保持原字节，不冒充整理后的HEAD。

核心包可用项目create_core_review生成，但需要核对其当前白名单、所有大小/SHA-256、源码/文件夹/ZIP一致与凭据扫描方法。正式完整代码不由核心包文件数代表。ZIP自身哈希放包外，避免自引用；旧ZIP哈希、测试计数不复制为本版回执。

决赛官方必须提交最终报告、可运行Web/CLI原型、同版完整源码/依赖/说明、现场实时样例并答辩；单EXE和30MB包是本项目交付设计。用户入口见 [USER_GUIDE.md](USER_GUIDE.md)，源码见 [RUNBOOK.md](RUNBOOK.md)，发行见 [EXE_DELIVERY.md](EXE_DELIVERY.md)。
