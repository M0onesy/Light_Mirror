# 明镜 MingJing 1.0.0.dev3 阶段候选交付导航

本版工作工程为 `mainProj/新版/mingjing`，需求与修订脚本在 `mainProj/新版/文档`，交付根为 `交付/新版`。原融合包保持只读。build2 EXE已完成同哈希烟测，候选程序、完整源码、核心审阅包与MP4均已有实物和收据；本版仍为阶段候选，正式金融质量未达标。

阅读评审PDF或包内文档时，请同时阅读[最终问题附录](最终问题附录_dev3.md)，尤其MJ-P69对简写前提、原文期间及人工审阅范围的澄清。包内文档索引是封装时点快照，随后外部附录与最终导航通过历史链增补，四核心文档、EXE和候选ZIP不重写。

| 使用目的 | 入口 | 当前边界 |
| --- | --- | --- |
| 先核对阶段结论 | [阶段验收摘要](阶段验收摘要_dev3.md)、[最终问题附录](最终问题附录_dev3.md) | 全文原始运行partial、Kernel失败；修复后独立离线重放通过，Agent仍未执行；正式金融质量未达标 |
| 初赛评审 | [计划书PDF](明镜MingJing_项目计划书_初赛评审版.pdf) | 49页，14章、图表、引用与目录；目标、方法及验收设计，配套阅读MJ-P69 |
| 编辑计划书 | [计划书DOCX](明镜MingJing_项目计划书.docx) | Word域/目录已刷新保存，团队名称、成员、院校仍待真人确认 |
| 了解交付 | [交付说明](交付说明.md) | 产品入口、标准/审慎策略、官方日期及实际PDF渲染方式 |
| 实施与内部缺陷 | [冻结开发任务手册](明镜_计划书打磨与开发任务手册.md) | 五工作包、根因、负责人待填、关闭条件与冻结进度；后续结论见外部摘要和附录 |
| 运行程序 | [程序目录](../程序/MingJing-1.0.0.dev3-candidate/)、[候选ZIP](../程序/MingJing-1.0.0.dev3-candidate.zip)、[包装收据](../程序/MingJing-1.0.0.dev3-candidate.receipt.json)、[同EXE烟测](../验收记录/dev3-exe-final/smoke.json) | 双击MingJing.exe；build2已实际通过限定烟测；用户自己的API密钥在设置页填写，跨电脑不复制DPAPI |
| 开发与完整运行 | [完整源码目录](../源码/mingjing-1.0.0.dev3-full-20261010T085922Z/)、[完整源码ZIP](../源码/mingjing-1.0.0.dev3-full-20261010T085922Z.zip)、[收据](../源码/mingjing-1.0.0.dev3-full-20261010T085922Z.receipt.json) | 22,385文件，9,522个carrier文件已核对；含锁定vendor、staged生产载体、生产依赖与原许可 |
| 核心源码审阅 | [核心ZIP](../核心审阅/MingJing-core-review.zip)、[生成收据](../核心审阅/MingJing-core-review-receipt.json)、[构建绑定](../核心审阅/MingJing-core-review-build-binding.json) | build2快照，7,437,147字节，761来源文件加manifest；原生Loop完整运行另需同版完整资源 |
| 实际操作演示 | [实际操作MP4](../演示/actual-dev3/明镜新版_实际操作演示.mp4)、[视频收据](../演示/actual-dev3/video-receipt.json) | 48.64秒、1,620,570字节；虚构样例、无声kernel_demo、真实浏览器10步、页面错误0 |
| 核对本版证据 | `验收记录/` | 保存通过、失败、范围和未执行项；旧版本收据不继承为本版成绩 |

用户从首页上传研报/财报，确认识别和参考材料，选择标准或审慎策略，再核查或分析；逐条查看公式、操作数、证据和修改建议。真实模型负责语义、检索与组织，宿主金融内核产生金额与判定；未配置模型时使用纯内核演示。会话可继续问依据、补材料并重放，历史结果保持原始冻结范围。

源码说明集中在 `docs/USER_GUIDE.md、RUNBOOK.md、DELIVERY_GUIDE.md、EXE_DELIVERY.md、THIRD_PARTY_NOTICES.md`。完整源码与核心包用途不同；个人配置、密钥、真实响应缓存、运行历史和未获授权原件不随交付。对应EXE的构建收据与源锁、ZIP包外哈希用于核对版本和字节。

交付按 `程序/、源码/、核心审阅/、文档/、演示/、验收记录/` 集中组织。build/manifest与候选ZIP收据放在程序目录及包内；完整源码、核心审阅、视频各自的JSON/ZIP/哈希留在所属目录，交付根不散放机器回执与压缩包。

文档已完成结构及视觉核验。Word原生PDF导出停滞的三次失败收据保留；最终PDF由Playwright Chromium读取刷新后的新版DOCX生成，目录使用PDF内部链接与实际页脚页码。准确渲染器、DOCX/PDF哈希和逐页核验见 `验收记录/docs-revision.json、docs-structure.json、docs-visual.json、pdf-render.json、word.json`。

原全文最终收据与分层统计另固化为[原始全文快照](阶段验收摘要_dev3.snapshot-01.json)。该快照绑定原输入结果和收据哈希；[修复后独立全文离线重放](../验收记录/kernel-replay-closure/closure.json)已通过，重建和候选烟测分别保留自己的新证据，不覆盖原始失败，也不使未完成金融处理自动完成。

最终EXE验收使用 `scripts/dev3_delivery_smoke.py`。源码模拟收据只证明当前页面和离线内核流程；只有同EXE真实供应商、小原生任务、材料追问及离线缓存重放全部实际通过，才登记 `passed_scoped_dev3_candidate`。专业效果、独立Windows、真人金标和D3以自己的新收据为准；未执行项保持未执行。

已通过的build2同EXE烟测为`passed_scoped_dev3_candidate`，10项检查通过、source_simulation=false、原用户profile未改。它包含当前上传UI的虚构内核流程、保存供应商真实小任务、一次材料追问及缓存重放；开发宿主环境不等于独立Windows。构建source SHA-256为`dd383919a7a34409d0d823c0012734db55f7f0b2d8cf23977a56fbb6cd1ea971`；完整源码采用另一白名单集合，其source_inventory_digest为`3ada01a3db4e2e3fefa597cebf9f9b38e4255b0c2d674360d2213dab1a105be6`。

| 已核对实物 | 字节数 | SHA-256 |
| --- | ---: | --- |
| build2 MingJing.exe | 108,374,880 | `c47952ffe9811496de640f76b0eda160fc4185be0eab6fd52c994cbdd43482cb` |
| 候选程序ZIP | 259,733,258 | `db3bfc9db45e173d88da001d644fa3c9b44140432a15696f74332d832f0df696` |
| 完整源码ZIP | 147,214,870 | `ed127233ad04885ad7c5b8a589344c75efda557c7bb44e02c099d44decfee816` |
| 核心审阅ZIP | 7,437,147 | `cb31529c3d5d9033cf22c94bd50824b141f44a3b2322789ad2273a4a7981aa93` |
| 实际操作MP4 | 1,620,570 | `b7cd33892a50a1d19d76fc4fff88ed925cc2a24d39808c3673fe59b8e2056c18` |

首轮构建、烟测、源码与核心包已移入`验收记录/build-01-artifacts/`，其[源码/核心映射](../验收记录/build-01-artifacts/core-source-location-mapping.json)及[构建/烟测映射](../验收记录/build-01-artifacts/build-smoke-location-mapping.json)保留原字节与路径解释；不登记为最终候选。

候选包包含74文件，formal_release=false、delivery_kind=stage_candidate。包内`full_report_financial_acceptance`未执行项指正式金融认证门禁；真实全文API已实际运行且为partial，不可将该字段解释为全文从未执行。包内文档收据SHA-256为`fd2a88bd3f3971b20b21f552c47cbb115cf10eb97b98d695fe8599bd0b253b74`，最终外部索引将这份原字节归档在`验收记录/文档封存历史/`；阅读时以本导航和配套最终附录理解阶段边界。

报名截止2026-10-11，作品截止2026-10-18，通知未给具体小时。初赛提交PDF和MP4；决赛提交最终报告、可运行Web/CLI原型、同版完整源码/依赖/说明并现场处理样例和答辩。计划书质量门槛是待验证目标，虚构样例与处理覆盖率不能作为实测金融准确率或召回率。
