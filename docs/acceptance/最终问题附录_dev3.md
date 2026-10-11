# 明镜 1.0.0.dev3 最终问题附录

本附录只追加交付阶段的实际问题、处置和限制，不回写工程docs/requirements、原DOCX/PDF、原失败收据或源码锁。版本号是阶段候选编号；正式金融质量未达标。MJ-P01～P60的历史现象与关闭证据保留在[工程冻结任务手册](../project/明镜_计划书打磨与开发任务手册.md)。

## MJ-P61 全文预算终止后仍有未处理金融位置

real-full-01实际162次模型请求、2,005,811 Token，A/B各224条；预算阻止后续新请求，任务产出partial=true。全公司历史276位置中66位置尚未处理；历史财务222位置同样有66待处理，核心198位置有48待处理。它们不能转成合法缺证或从分母移除，也不能用small05完整结算替代。

当前状态：未关闭。全文技术测试已执行，完整金融处理未完成。关闭需要新的完整执行证据与逐处账本，不修改旧运行或旧缓存；本阶段不为凑目标再次改写预算、旧锁或运行统计。

证据：`验收记录/real-full-01/report/results.json`、该运行原生结果及最终acceptance；按`scripts.acceptance_dev3.acceptance_statistics`复算。

## MJ-P62 业务、运营及未绑定数字不能被类别隐藏

全公司历史scope的54处业务/运营/未绑定数字全部未能核实，保留54个原文位置。直接开发原件复核的事实集不接原生customer/business/quarter桥，全文任务又有未完成执行；因此“未能核实”不能一概归因为官方材料没有披露。

当前状态：未关闭。需要逐项核对原文位置、允许来源、主体/期间/维度、桥接表达和执行是否真正完成，再区分方法未授权、操作数缺失、材料未覆盖与未处理。金融归档中历史28处合法待定仍按自己的直接复核范围保留，不与原生全文143处未决相减制造质量提升。

证据：[金融合法缺口说明](../../evidence/dev3/finance-freeze/问题关闭与合法缺口.md)、[完整gap ledger](../../evidence/dev3/finance-freeze/real-direct-03/gap_ledger.json)、全文产品结果。

## MJ-P63 原生顶部核实混入预测内部勾稽

全文原生顶部180核实中48来自预测内部勾稽，公司历史仅132核实。全公司历史276位置为132核实/1差异/143未决；历史财务222位置为132/1/89；核心198位置为130/1/67。不同scope有包含或交叉关系，不能拼接分母，也不能将内部计算相容当作公司历史外部事实真实。

当前状态：统计展示按分层复算处理，专业质量仍未成立。partial导致三类历史目标均为not_assessed_incomplete_ledger、target_at_most_9=null，不得声称全文≤9通过。召回和误纳仍需独立真人金标。

原execution_statistics.legal_unverifiable的143是全体已处理缺证旧口径，恰好等于公司历史未决143；二者不是同一集合。公司历史为77已处理未能核实＋66未处理，不能将143全部解释为合法缺证。

证据：[阶段验收摘要](阶段验收摘要_dev3.md)的逐处分层表与全文产品结果；原生顶部结果保留原值，不手改成历史统计。

## MJ-P64 唯一全量回归失败与夹具边界

唯一一次全仓库pytest共3,413项：3,403通过、7失败、3跳过，exit_code=1。后续没有第二次全量；修复后的定向结果不能改写该原成绩。

| 原失败 | 原因与实际处置 | 当前限定状态 |
| --- | --- | --- |
| D3曝光防污染索引缺失 | 恢复原字节开发曝光登记及D/D2 seal元数据，保留31家历史公司排除 | 定向通过；未选择或执行新D3 |
| 规则版本测试过期 | 原断言0.6.0，锁定sources/lock实际0.6.1；校正技术版本断言 | 定向通过，不代表专业规则签发 |
| D/D2历史gold缺失两项 | 仅缺可选原始gold时显式skip；若已安装但必要metadata缺失仍fail | 两项明确跳过，不称专业金标通过 |
| 伪造百分点基期旧预期 | 当前入口已在提交时拒绝来源冲突；测试核对类型化拒绝及原始基期，不再期待其进入freeze | 定向通过，不放宽生产来源守卫 |
| smart证据图片400 | table来源ID被前端发给严格图片接口 | 见MJ-P65，定向关闭 |
| 新首页与旧driver不符 | 根路由现在为smart，旧driver期待Harness | 见MJ-P66，定向关闭 |

金融/夹具定向为43通过、2可选gold跳过；另一次可选gold边界最终检查为2通过/2跳过，属于重叠范围，不相加。金标原件未打开或复制，专业验收与D3仍未执行。原始全量日志、JUnit及收据保留；恢复元数据和校正测试的版本/hash另有原收据。

证据：[原全量](../../evidence/dev3/full-regression/receipt.json)、[金融处置](../../evidence/dev3/full-regression/finance-failure-triage.json)、[元数据恢复](../../evidence/dev3/full-regression/fixture-restoration.json)、[定向43通过](../../evidence/dev3/full-regression/targeted-finance-fixtures.json)、[可选gold最终边界](../../evidence/dev3/full-regression/targeted-holdout-optional-final.json)。

## MJ-P65 table来源ID误请求位图证据

真实静态UI缺陷：table provenance ID被发往严格PNG接口，HTTP400、提示“证据编号格式无效”。修复只对后端支持的精确bbox/hash或text编号取图；table来源文本保留，错误控制台仍须为0，不过滤错误来通过。

当前状态：已由定向验证关闭。相邻格式包含真实text/bbox图片、table文本、额外后缀、短hash、五位页码与尾随换行；合法图片实际加载且naturalWidth>0。与新首页driver联合11通过/0失败/0跳过，37.95秒。Python金融内核及规则未由该修复改变。

证据：[Web闭环收据](../../evidence/dev3/web-regression-closure/closure.json)。静态来源清单SHA为`93b5412b3816dd40c7580a486ee94436b00d2cc0be1b4358e4402442e9e71144`；最终EXE须绑定包含此静态版本的新构建输入锁。

## MJ-P66 根首页更新后验收driver仍期待Harness

旧driver将新smart根首页与Harness index比较，导致“Served HTML is not the actual Harness UI build”。修复后分别逐字节核对smart根与4份资源，以及/harness页面和每个锁定Harness资源；未删除来源验证。

当前状态：与MJ-P65的联合定向11项通过，原全量失败及原driver运行仍只读留档。它证明源码Web路径，不替代最终EXE冷启动、真实供应商及独立Windows。

证据：[Web闭环收据](../../evidence/dev3/web-regression-closure/closure.json)、该目录原始诊断与targeted-web-junit.xml。

## MJ-P67 全文Kernel重放未保留输入排除语义

real-full-01原始最终收据为requires_improvement、partial=true，耗时2,222.58秒、用户配置未变。Kernel重放为failed、numeric_equal=false、integrity_ok=true、外连0。顶部180核实/1差异/353弃权汇总相同，但210个原文位置的输入排除语义发生变化：207处预测输入及3处行情输入原smart_result.status为skipped，回放为unverifiable/X3；abstention_code、reason、smart_result各210个差异，共630字段。

当前状态：回放审计缺陷已由独立离线重放关闭，原始失败保留。修复定向44通过/0失败/0跳过，12.13秒、新API请求0；仅replay.py及对应测试变化，金融内核与Agent保持原哈希。修复先重算并对比完整可信输入排除证明，再应用原生未绑定X3守卫，没有直接信任跳过标签。新的全文离线重放534条、加载1,805事实、冻结657条、123条待处理，差异0、numeric_equal/integrity_ok=true，524.81秒，新API和外连均0；完整result digest与原冻结结果一致。

原acceptance、结果、锁、旧失败重放及history哈希全部未变。Agent重放状态仍not_executed，理由original_not_native_success。此次关闭不补完123条已建模待处理，不把partial变为完整金融执行，不构成正式质量认证；不能以修复后通过改写原始失败。

证据：[全文原始最终收据](../../evidence/dev3/real-full-01/acceptance.json)中的kernel_replay.differences、agent_replay与statistics，以及[定向修复收据](../../evidence/dev3/kernel-replay-closure/targeted-receipt.json)、[独立全文离线闭环](../../evidence/dev3/kernel-replay-closure/closure.json)。闭环收据SHA-256为`6f5e88eba0d53094f19fb0082fd5f476eafd53377f8e2bfa604eb84bc6b80c70`。修复与重建后的候选必须引用新的源码锁、重放回执及EXE哈希，不覆盖首个构建快照。

## MJ-P68 核心审阅包脚本的直接启动与输出边界

首次直接运行create_core_review.py时，scripts包导入失败；嵌套新版项目的旧输出边界又限制到mainProj，因此首次产物通过模块启动、独立暂存后成组移动到交付。该真实命令与位置映射保留，不改写成首次直接生成成功。

当前状态：工具最小修复的23项定向检查通过，0失败/0跳过，1.74秒。直接脚本只引入自己的工程根；输出采用最近的规范竞赛工作区，普通clone限制为工程直接父目录，并拒绝盘根与链接逃逸。默认输出适用相同边界。源码白名单未改变，生产金融代码与冻结档案一致；最终新核心包须重新生成和验哈希，不能继承首次包的验收结论。

最终build2核心包已用修复后的直接命令一次生成：ZIP 7,437,147字节，SHA-256为`cb31529c3d5d9033cf22c94bd50824b141f44a3b2322789ad2273a4a7981aa93`；761份来源文件加manifest共762份，与749个共同构建输入逐项哈希一致。该项包装工具问题已按此范围关闭。

证据：[核心包工具修复](../../evidence/dev3/core-review-tool-fix/receipt.json)、[首轮源码与核心归档映射](../../../存档/历史版本/2026-10-11整理前/dev3-build01/core-source-location-mapping.json)、[最终核心生成收据](../../../存档/历史版本/2026-10-11整理前/交付/新版/核心审阅/MingJing-core-review-receipt.json)、[build2绑定](../../../存档/历史版本/2026-10-11整理前/交付/新版/核心审阅/MingJing-core-review-build-binding.json)。此修复是包装工具的技术范围，不是金融质量修复，也未触发第二次完整回归。

## MJ-P69 冻结计划书简写须与严格契约合读

本项澄清冻结计划书早段简写的适用前提，以[PDF物理24页第5.7节](../project/明镜MingJing_项目计划书_初赛评审版.pdf#page=24)、[物理35页第9.3节](../project/明镜MingJing_项目计划书_初赛评审版.pdf#page=35)及[用户指南](../USER_GUIDE.md)的严格限定合读。正文仍表达项目目标和方法，不因本轮结果重写，不补造专业签认。

- 物理9页“确实缺材料才弃权”和10页“命中首选定义记为核实……全部不命中才判为违背”省略了来源主体、期间、口径、方法授权、完整操作数、精度及A/B执行等前提。方法、绑定或执行不确定仍须缺证或待处理；备选定义命中只说明标准模式下披露假设后的条件相容，不证明研报意图或外部真实性。审慎模式不以推定补足缺口。
- 物理21页“期间……须取自事实库已有的期间列表”不能理解为可迁就已有事实改期。期间必须锁定冻结原文；对应期事实不存在时保留原期间并给X1/缺证，绑定本身不确定时给相应X3，不得换成事实库中可用的另一期间。
- 物理44页“审核人员只需处理违背与弃权两类”是应用价值概述。实际审阅还包括条件核实及假设、pending与规则排除账本和分母，不能跳过这些位置。small05的21个已判位置均使用默认合并前提，其中6处原文披露、15处推定；技术链通过不消除该审阅要求。

当前状态：本阶段已在外部附录澄清，四核心文档封存字节不改。包内文档和其收据是候选封装时点快照，须与本外部附录配套阅读；未来文档版本可将这些前提直接写入对应早段。

## MJ-P70 录制driver未兼容已在库样例去重状态

最终录制时虚构样例已经入库，上传状态为“已在库中（去重）”；冻结录制脚本的状态匹配未覆盖该返回。工程输入与EXE不再改动，交付外部副本只兼容这一合法状态，保留原driver、差异来源及失败记录，不声称冻结原脚本本次录制成功。

当前状态：外部driver完成真实浏览器录制，10步骤、page_errors=[]，MP4为48.64秒、1,620,570字节，满足≤5分钟与≤500MB。视频为无声kernel_demo、虚构可分发样例；无真实模型回复进入视频，真实API测试另见同EXE烟测和全文原收据。

证据：[视频收据](../../artifacts/demo/video-receipt.json)、[外部driver来源](../../evidence/dev3/video-final-recording/driver-provenance.json)。MP4 SHA-256为`b7cd33892a50a1d19d76fc4fff88ed925cc2a24d39808c3673fe59b8e2056c18`；该项按实际录制范围关闭，不构成金融质量证据。

## 保留的金融合法缺口与正式门禁

唯一2016管理费率E5为所选更正材料下的技术差异，尚需专业版本/方法审阅。开放定义、原表空白、缺操作数和来源冲突不以挑值或自证清除。严格未声明scope保持X3，不补造指标；原文公司/代码与公告时点必须有来源，PDF创建时间和后三位别名不授予身份/时点。

P56～P60已由native-final-freeze局部48项及small05真实链按具体技术边界关闭，旧small01/04失败与small02 partial不覆盖。Human gold、规则专业签认、D3、Kimi/GPT、独立Windows及全文金融质量不由这些技术收据代替。

全文原始Kernel重放失败，修复后的独立全文离线重放通过，原全文Agent重放未执行。build2的[同EXE烟测](../../evidence/dev3/dev3-exe-final/smoke.json)已通过限定范围；其中小任务Agent回放不替代原全文未执行项。缓存重放一致只证明冻结的部分结果可重算；不会把partial变为完整执行，不会证明缺证答案正确或召回充分。程序、源码、核心包与MP4均已有实物，其最终名称、字节数及哈希见[交付导航](../project/新版交付导航_dev3.md)。
