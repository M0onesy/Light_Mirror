# 明镜 1.0.0.dev3 阶段验收摘要

本版为可审阅的阶段候选。真实44页研报技术测试已执行，但在预算边界停止后为partial，正式金融质量目标未达标、未获独立认证，不能据此发布正式v1.0。小样本通过不替代全文结果；计划书正文继续表述项目目标、实施方法与验收设计。

本摘要记录全文最终原始收据、唯一一次全量回归及其定向关闭证据。全文原始Kernel重放失败，修复后的独立离线重放已通过，Agent重放仍未执行；第二次构建的同EXE烟测、候选程序包、完整源码、核心包和视频均已有实际收据。状态按原始文件与版本锁核对，不用后续修复覆盖本次失败。阅读冻结计划书时须配套阅读[最终问题附录](最终问题附录_dev3.md)MJ-P69的适用前提澄清。

## 当前结论

| 验收范围 | 实际结果 | 可证明边界 |
| --- | --- | --- |
| 四原件真实小样本small05 | 28次请求、428,575 Token，26原文位置为20核实/1差异/5未能核实，partial=false；Kernel/Agent重放均一致且外连0 | 研报物理5–7页开发样本技术链，不是全文或独立专业质量 |
| 真实44页全文real-full-01 | requires_improvement，162次请求、2,005,811 Token，A/B各224条，partial=true，2,222.58秒 | 已实际调用供应商并产出部分冻结结果；未完成全部金融处理，原用户配置未变 |
| 全文原始重放 | Kernel failed、numeric_equal=false、integrity_ok=true、外连0；Agent not_executed | 210处输入跳过变未能核实、630字段差异；汇总数值相同不代表逐条字段一致 |
| 唯一一次全仓库pytest | 3,403通过、7失败、3跳过，exit_code=1 | 原全量失败记录保留，不能改称全量全通过 |
| 金融/夹具定向处置 | 43通过、2项可选历史gold缺失跳过；31家公司曝光防污染元数据恢复 | 局部机制/夹具校正，不是独立D3或专业金标通过 |
| 两项Web失败定向闭环 | 11通过、0失败、0跳过；真实图片相邻格式与console零错误验证 | 两项失败的定向修复，不改写原全量成绩 |
| 全文Kernel回放修复定向 | 44通过、0失败、0跳过，12.13秒，新API请求0 | 仅replay.py与对应测试变化，金融内核及Agent保持原哈希；不补完原全文 |
| 修复后全文离线重放 | closed_by_offline_replay，534重算/657冻结/123待处理，字段差异0，numeric_equal/integrity_ok=true；524.81秒，外连及新API均0 | 原冻结结果、旧失败收据及锁哈希未变；仅证明原部分结果可离线重算，不证明全文已完成 |
| 核心包工具定向 | 23通过、0失败、0跳过，1.74秒 | 直接脚本导入与输出边界修复；源码白名单和生产金融代码未变，最终新包另核对 |
| 文档 | 14章、1,809段、54表、28图保留；PDF49页、85个有效目录链接，已核验哈希 | Word已刷新保存；原生PDF导出失败，最终PDF由Chromium生成，原失败留档 |
| 第二冻结版本核心审阅包 | ZIP 7,437,147字节，761份来源文件，含manifest为762份；与749个共同构建输入逐项哈希一致 | build2审阅快照，满足30MB限制；完整运行与金融质量仍另验 |
| build2同EXE烟测 | passed_scoped_dev3_candidate，10项检查通过，source_simulation=false、原用户profile未改 | 当前上传UI、虚构内核及保存供应商真实小任务、材料追问与缓存重放；开发宿主限定范围 |
| 完整源码 | ZIP 147,214,870字节，22,385文件；9,522个carrier文件核对，packaged_and_hash_verified | 包装完整性，不另称金融/运行验收；源清单与构建输入锁分别保留 |
| 候选程序ZIP | packaged_and_hash_verified，259,733,258字节、74文件，formal_release=false、stage_candidate | 包含同版EXE、完整源码与四份封存文档；原用户profile未改，正式质量门禁未满足 |
| 实际演示MP4 | passed，48.64秒、1,620,570字节，10个实际操作、page_errors=[] | 虚构可分发样例、kernel_demo、无声；外部录制driver兼容已在库去重状态，不是全文质量证据 |

small05和全文均使用保存的legacy-default/deepseek-flash/deepseek路由，费用unknown。全文原隔离预算为2,000,000 Token；预算阻止后续新请求，实际累计用量保留为2,005,811，不把在途返回后的累计数改写为预算内完成。全量原始执行耗时1,108.39秒，定向结果另记。

全文Kernel原始重放重算534条、加载1,805个事实，冻结657条中123条仍待处理。原180核实/1差异/353弃权的顶部汇总保持相同，但207处预测输入与3处行情输入原为skipped，回放均变为unverifiable/X3，分别在abstention_code、reason、smart_result产生差异，共210处、630字段。Agent重放因`original_not_native_success`未执行；不能登记原生Agent回放通过或将原失败说成数字字段100%一致。

修复后的独立全文离线重放已重新校验输入排除证明，字段差异0、结果digest与原冻结结果一致，保留180核实/1差异/353弃权和123条已建模待处理。原acceptance、结果、锁及旧失败重放的哈希全部未变。该项关闭回放审计缺陷，不补做模型请求，不补完金融账本，不改变本次全文partial、requires_improvement或正式质量结论。

## 全文按原文位置分层

统计直接读取`验收记录/real-full-01/report/results.json`，使用冻结工程`scripts.acceptance_dev3.acceptance_statistics`。待处理项仍计入未决，不能当作合法缺证；下列范围存在包含关系，不相加作为全体总数。

| 全文范围 | 原文位置 | 去重断言 | 核实 | 差异 | 已处理未能核实 | 尚未处理 | 未决合计 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 核心公司历史财务 | 198 | 182 | 130 | 1 | 19 | 48 | 67 |
| 历史财务：核心＋开放定义 | 222 | 206 | 132 | 1 | 23 | 66 | 89 |
| 全公司历史：含业务、运营及未绑定 | 276 | 260 | 132 | 1 | 77 | 66 | 143 |
| 公司业务、运营及未绑定子范围 | 54 | 54 | 0 | 0 | 54 | 0 | 54 |

全公司历史的143处未决中66处未完成执行、77处已有未能核实结果；54处业务/运营/未绑定数字全部保留。它们不能一概解释为官方材料不存在，也不能凭类别标签移出主验收范围。

原运行`execution_statistics.legal_unverifiable=143`是全体已处理缺证的旧口径，恰好与公司历史未决143相同，不能混为同一集合。公司历史仍严格按77已处理未能核实＋66未处理登记，不称143处全部合法缺证。

原生顶部汇总为180核实、1差异、353弃权；其中48个核实属于预测内部勾稽，公司历史核实为132。预测scope另有207处输入跳过，行情3处输入跳过；这些规则排除保留账本，不计历史verified。预测scope可与外部scope交叉，不能把各视图直接相加。全体产品结果保留657处原文位置，原生顶部汇总不能代替分层分母。

完整性统计为ledger_complete=false、已建模待处理123、账本待处理提及176，口径不同，不相加。核心/历史财务/全公司历史的`target_at_most_9=null`、`target_assessment=not_assessed_incomplete_ledger`：本次“完整研报主范围弃权≤9”目标未评估，未通过验收。执行比例、已判覆盖或缓存重放一致都不是独立金融召回率；召回仍未知。

## 原收据与未执行项目

- [全文运行](../../evidence/dev3/real-full-01/acceptance.json)、[全文产品结果](../../evidence/dev3/real-full-01/report/results.json)：原始最终状态requires_improvement；部分执行、Kernel失败与Agent未执行均保留。
- [唯一全量回归](../../evidence/dev3/full-regression/receipt.json)、[金融失败处置](../../evidence/dev3/full-regression/finance-failure-triage.json)、[金融定向](../../evidence/dev3/full-regression/targeted-finance-fixtures.json)、[可选gold边界](../../evidence/dev3/full-regression/targeted-holdout-optional-final.json)、[Web定向闭环](../../evidence/dev3/web-regression-closure/closure.json)：保留各次实际范围，不合并成第二次全量。
- [Kernel回放定向修复](../../evidence/dev3/kernel-replay-closure/targeted-receipt.json)、[全文离线闭环](../../evidence/dev3/kernel-replay-closure/closure.json)：44项定向通过及一次独立全文离线重放通过；原失败继续留档。
- [核心包工具修复](../../evidence/dev3/core-review-tool-fix/receipt.json)：23项定向通过；首次产物和路径映射留档，最终新包不继承其哈希。
- [build2核心包](../../../存档/历史版本/2026-10-11整理前/交付/新版/核心审阅/MingJing-core-review-receipt.json)、[构建绑定](../../../存档/历史版本/2026-10-11整理前/交付/新版/核心审阅/MingJing-core-review-build-binding.json)、[首轮源码与核心归档映射](../../../存档/历史版本/2026-10-11整理前/dev3-build01/core-source-location-mapping.json)：新核心ZIP SHA-256为`cb31529c3d5d9033cf22c94bd50824b141f44a3b2322789ad2273a4a7981aa93`，原首轮产物保持独立字节。
- [build2同EXE烟测](../../evidence/dev3/dev3-exe-final/smoke.json)：EXE 108,374,880字节，SHA-256为`c47952ffe9811496de640f76b0eda160fc4185be0eab6fd52c994cbdd43482cb`，构建source SHA-256为`dd383919a7a34409d0d823c0012734db55f7f0b2d8cf23977a56fbb6cd1ea971`。
- [完整源码收据](../../artifacts/source/mingjing-1.0.0.dev3-full-20261010T085922Z.receipt.json)：ZIP SHA-256为`ed127233ad04885ad7c5b8a589344c75efda557c7bb44e02c099d44decfee816`，source_inventory_digest为`3ada01a3db4e2e3fefa597cebf9f9b38e4255b0c2d674360d2213dab1a105be6`；与EXE构建集合不同，不要求两个digest相同。
- [候选程序包装](../../artifacts/packages/MingJing-1.0.0.dev3-candidate.receipt.json)：ZIP SHA-256为`db3bfc9db45e173d88da001d644fa3c9b44140432a15696f74332d832f0df696`；`full_report_financial_acceptance`未执行门禁指正式金融认证，不表示本次真实全文API未执行。
- [视频收据](../../artifacts/demo/video-receipt.json)：MP4 SHA-256为`b7cd33892a50a1d19d76fc4fff88ed925cc2a24d39808c3673fe59b8e2056c18`，实际浏览器操作与外部driver来源分别留档。
- [small05](../../evidence/dev3/real-small-05/acceptance.json)、[原生局部冻结](../../evidence/dev3/native-final-freeze/acceptance.json)、[金融合法缺口说明](../../evidence/dev3/finance-freeze/问题关闭与合法缺口.md)：开发材料、小样本和局部反例分别留档。
- [文档封存](../../evidence/dev3/docs-revision.json)、[问题附录](最终问题附录_dev3.md)：封存DOCX/PDF及工程手册不因后续结果再生成，阶段进度在本摘要单独追加。

真人独立金标、规则专业签认、未曝光D3、Kimi/GPT真实能力与独立Windows干净环境验收均未完成本版认证；缺实际执行收据的项目保持未执行。最终EXE小任务烟测已经通过，只证明该EXE限定技术范围，不抹去本次全文partial和质量未达标结论；小任务的Agent回放通过也不改变原全文Agent回放未执行。

候选包内文档收据为封装时的`fd2a88bd3f3971b20b21f552c47cbb115cf10eb97b98d695fe8599bd0b253b74`，四核心文档保持原字节；外部摘要、附录、视频与导航随后增补。最终外部docs-revision按历史链归档该封装时点收据，不改EXE或重封候选包。
