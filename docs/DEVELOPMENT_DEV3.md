# 当前开发与验收记录

工作根为 `mainProj/mingjing-next`。本轮按项目计划书和开发任务手册修复功能与正确性，不发布正式 1.0，也不例行制作 EXE 或 ZIP。

仓库清理实际删除 8,233,265,180 字节。完整清单、逐文件哈希与保留原因见 `runs/workspace-cleanup-dev3/cleanup_receipt.json`。原财报、会计知识库、需求文件、锁定 Harness 源快照、许可、独有基线和历史失败记录均保留；83 份冻结运行清单在清理前后哈希一致。

已实现并加入实际反例回归的修复：

| 问题 | 实现与证据 |
| --- | --- |
| 同版冲突被公式绕过 | 结构化 FactQueryOutcome；X4 拒绝重算兜底；区间/扩展专项测试。 |
| 金额巧合被判期间/口径/版本错误 | E2/E3/E9要求明确来源证明；无证明只保留诊断线索；缺材料弃权。 |
| 披露精度比较错误 | Decimal 两侧半开区间相交；授权公式区间传播；默认模糊词 h=2%；报告与Web显示精度提示。 |
| 调整前后数字身份丢失 | 多层表头、版本及期间列证据；ROE扣非口径独立；附注编号不作为金额。 |
| 恢复损坏旧状态 | 冻结草稿只读重建，验证后发布检查点；失败不覆盖旧材料与状态。 |
| 补证在新版本丢失 | 私有继承计划，原PDF页与新基线重新校验；浏览器不能提交内部计划或数值。 |
| 原文引用绕过截止日 | 检索、读取、上下文、引用提交统一检查公开日期；未知/晚披露来源不可作为当时已知证据。 |
| 配置鉴权类型与请求走私 | 严格布尔/数值，绑定地址重新鉴权；拒绝负/重复长度和Transfer-Encoding；有读取超时。 |
| 混入旧应用模块 | 当前工程唯一命名空间、真实模块来源断言；拒绝历史后端覆盖。 |
| 消息无法重放 | 每条消息冻结输入、资源锁、缓存和工具轨迹；真实Harness缓存循环；Web按消息重放。 |
| 导出缺补证与引用 | 按冻结输出注册表逐文件验证；包含冻结消息/引用/图像；再次复核归档哈希。 |
| 人工金标可以空label蒙混 | 完整真人签认字段、单元格原PDF文本验证、内容锁、三份原始评级kappa；单类别kappa记unknown。 |
| 评测误用ULP代替模糊词h | 实际重跑h=.5%/1%/2%/5%的32个参数组合；不会改预测标签伪造工作点。 |

回归收据必须以实际最终稳定版本为准；专项通过次数不能相加成为独立用例总数。开发过程中的运行时切换会使已有开发锁失效，相关失败保留，使用稳定版本新建验证；绝不改旧锁伪造兼容性。

人工准备集发现了制度文件误选为年报的问题。旧 `annotations_v2` 和首份 `human_blind_dev3` 保留为失败准备记录，不能发给正式审阅者或用于金标。修正版本限定已曝光发行人，不为补齐规模查看新D3公司；缺年报如实登记。

尚需真实执行并登记的外部项目：三名真人独立盲标与资格核验、20项外部抽检、30张规则卡专业签发、稳定版本未曝光D3一次正式评测、独立无Python/Node Windows环境，以及缺少凭据时的Kimi/GPT真实工具闭环。当前开发机、mock或合成反例不能代替这些证据。

只有G0/G1/G4/G5/G6/G7全部通过且金融阈值达标，才冻结正式代码、规则、UI和运行时，制作最终EXE候选并在独立Windows环境验收。单入口交付目标不变。

## 本轮稳定版 02 实测

`runs/regression-dev3/full-stable-02.xml`：1,342 passed、1 skipped；跳过项需要实际捆绑 EXE。新增知识库无页证据浏览器检查另有 `ui-knowledge-final.xml`，不与全量计数相加。

已保存供应商 `legacy-default / deepseek-flash` 完成下列原生运行；配置和密钥原字节保持不变，产物明文密钥扫描未发现泄漏。

| 实测 | 结果及收据 |
| --- | --- |
| 原始 44 页研报 | 324 金融候选；6 核实、0 违背、318 弃权；22 次真实模型请求、74,060 Token；`runs/source-real-stable-02/source_acceptance.json`。 |
| 财报分析 | 57 可用、83 合法弃权指标、1 会计政策事件；6 请求、20,474 Token；`runs/source-analysis-stable-02/source_acceptance.json`。 |
| 营收同比追问 | 3 请求、10,855 Token；确定性展示“约58.47%”，来源区间和完整原始值保留；`runs/source-followup-stable-02/acceptance.json`。 |
| 会计知识库追问 | 工具/原文/消息重放技术通过，但定义回答引用保险合同应用段落，质量复核失败；`runs/source-knowledge-followup-stable-02/quality_review.json`。 |

上述任务内核重放和真实 Harness 缓存重放均通过，重放网络请求为零。核查原稿未明确合并/母公司口径，未擅自登记用户声明；318条弃权中 X3=145、X2=36、X6=137。账本处理完成不代表金融提及召回达标。

确定性来源修复将金融候选从362降至324，完整保留2,141处原文定位，296处显式金额或百分比零新增误排；这是原稿只读机制复核，不能代替独立召回金标。币种、数值范围与顺序多期间政策见 `FINANCIAL_SOURCE_POLICY.md`。

外部会计材料已实际冻结158文档、6,538块；本任务截止日可引用63份，90份发布日期未知、5份晚于截止日，均明确拒绝作为当时已知依据。知识锁、原材料哈希、解析缓存和消息证据纳入冻结、恢复与导出。Windows嵌套中文路径、DOCX实体、双盲屏蔽和准则误当财报补证的边界已修复。

独立金融载体闭包为161,392,943字节，不等于最终EXE大小。Schema 2运行时身份排除临时解包位置，稳定缓存按资源哈希发布；原生3工具依赖链、4模型步骤、跨进程恢复/取消、断网重放、4,597导入闭包实际验证见 `runs/runtime-cache-schema2-20261007-02/acceptance.json`。本轮未制作EXE或真实载体ZIP。

上述02记录保留原锁及失败复核，不改成后续版本证据。专业模板和全文盲标包均为空白真人任务；准备工具不是验收通过。

## 稳定版04的实测与保留失败

04修复了来源维度字段中的邻接金额泄漏、英文EPS提及漏召回，以及Windows文件同大小、恢复mtime后运行时完整性校验可能漏报的问题。数字可解析但金融定义不明的24条提及实际进入独立A/B会话，不再提前作为X6跳过；来源定义守卫仍禁止把未定义费用率改绑为金额或同比。

`runs/regression-dev3/full-stable-04.xml`保留原始结果：1,498 passed、5 failed、1 skipped。五项失败均为费用率测试的旧`unresolved/X6`预期；测试已按`parsed`、语义待审和缺授权公式X2更新，另增加“金额恰巧算出同一费用率也不得重绑”反例，定向116项通过。没有改生产代码或旧运行锁来使这些测试通过。

已保存供应商`legacy-default / deepseek-flash`的04实测如下，均保持用户配置与DPAPI密钥文件原字节。

| 实测 | 实际结果与限制 | 收据 |
| --- | --- | --- |
| 原始44页研报 | 319候选；6核实、0违背、313弃权；44次真实请求、213,099 Token；A/B各提交36条，其中24条语义待审。任务与双层重放技术通过，金融召回和覆盖未达正式验收证明。 | `runs/source-real-stable-04/source_acceptance.json` |
| 财报分析 | 57可用、83弃权指标，1会计政策事件；6次请求、20,478 Token；同比按来源精度显示“约58.47%”。 | `runs/source-analysis-stable-04/source_acceptance.json` |
| 数值追问 | 3次请求、11,457 Token；操作数、公式、原文证据与零联网消息重放通过。 | `runs/source-followup-stable-04/acceptance.json` |
| CAS28定义追问 | 4次请求、15,323 Token；引用“视同该项交易或事项初次发生时即采用变更后的会计政策”的原始定义，并校验定义主语和来源；未把外部知识材料当已签发判定规则。 | `runs/source-knowledge-followup-stable-04/acceptance.json` |
| 原生Web/API闭环 | 445次HTTP请求、20次真实模型请求；使用原研报未改写的第1页，2核实、21弃权；A/B、页证据、HTTP双层重放通过。已曝光页的技术证明，不是全文质量评测。 | `runs/source-native-web-stable-04/acceptance.json` |
| 源码Web闭环 | 129次真实HTTP请求；11条演示陈述为4核实、5违背、2弃权；取消、重启、鉴权、配置、证据、导出和重放通过。 | `runs/source-web-stable-04/acceptance.json` |
| 原生自主补证 | 父任务实际检索并调用局部重解析；子任务A未提交、B提交1条，保留`partial`和pending，不能算通过。 | `runs/source-native-reparse-stable-04/acceptance.json` |
| 生成机制基准 | 354条严格匹配、0误纳，32次实际参数重跑；不是独立真人金融指标。 | `runs/generated-stable-04/metrics.json` |

补证04失败的直接原因是模型提交中文`营业收入`而非登记代码`REVENUE`，通用异常没有给出可修正的字段提示；修复限定为规范枚举、静态结构拒绝反馈和有界重试。另修复A/B提交缺失时`all([])`错误显示批次完成的问题；账本和审计必须记录实际提交数量。04失败记录和旧检查点均保留，修复后的验收必须新建05运行。

原稿未明确合并/母公司口径，不能代填用户声明。04的313条弃权为X3=163、X2=42、X6=108；账本处理结束不能证明真实金融提及召回≥90%。全文独立盲标使用`runs/full-report-blind-dev3-v2/tasks.json`，44页、1,433块，三份真人模板仍为空白。旧盲标包保留且因旧解析锁被正确拒绝。

前端增量产物已验证147源文件到85静态资源；Unicode原文区间按码点定位，覆盖emoji和扩展汉字，不使用UTF-16下标错位。金融载体04闭包仍为161,392,943字节，独立Node、原生三工具链、跨进程恢复/取消、零联网缓存重放与4,597导入均有实测；该数值不是最终EXE体积。开发阶段没有新制作EXE或发行ZIP。

## 05修复后的真实复验

05补证新运行`runs/source-native-reparse-stable-05/acceptance.json`实际通过：父任务由原生Agent自主调用局部补证工具，子任务A/B各提交1条规范绑定，从原年报取值，1 verified、0 violated、0 abstained、`partial=false`。子任务13次真实模型请求、32,891 Token、46.69秒；父子内核和原生缓存重放均通过，重放联网请求为0，原PDF、父任务及用户配置/密钥文件保持原字节。该样例是已披露表格转写加故意缺失的私有索引，只证明补证机制，不能计入独立研报金融金标。

同一原44页研报05运行`runs/source-real-stable-05/source_acceptance.json`完成40次真实请求、209,088 Token、178.12秒，A/B各真实提交39条，27条语义待审均进入双盲会话。结果仍为6 verified、0 violated、313 abstained，其中X3=165、X2=42、X6=106。27条包含新增的正文净利率与中文最新摊薄每股指标；裸净利率和裸净利润率不能依据数值巧合或双模型一致套用NP/营业收入公式，只有原文完整比例定义支持计算。

新受控演示`samples/demo_draft_defined.md`明确给出净利率分子/分母，10条原始演示陈述为4 verified、5 violated、1 abstained；Web验收另附一条缺材陈述。旧`samples/demo_draft.md`保留原字节。新配置默认核查Token预算1,000,000、追问150,000，已有显式预算和冻结运行均不自动改变。

05尚未运行全量回归或其余全套发布证据，因为只读逐项审查已经找到下一批分类问题，先修复再进行一次完整稳定回归。`runs/regression-dev3/source-04-abstention-313-audit.json`保留313项原文和事实消费记录：原108条X6的数字字面量可读，问题涉及未知金融标签、单位、期间或范围，并不是108个损坏数字；仍有结构编号、图轴和估值范围分类待改进。05源状态保存于`runs/regression-dev3/source-freeze-stable-05.json`，不替换04或05原锁。

验收汇总器已修复弱校验，逐项检查运行manifest、输入输出哈希、审计链、模型缓存、消息父任务及运行时资源。独立只读汇总`runs/development-receipt-review-stable-04/acceptance.json`正确保持`needs_improvement`，包含04五项失败及补证子任务失败，不因05修复成功改写04历史结论。技术通过与专业/发布验收仍分别登记。

## 06失败记录与07修复

06统一冻结版本实际回归为1,692 passed、6 failed、1 skipped，完整原记录保存在`runs/regression-dev3/full-stable-06.xml`与日志。`runs/development-stable-06/acceptance.json`校验全部原运行、消息、模型缓存、前端和审计，明确保持`needs_improvement`，不因同轮真实API技术闭环成功而覆盖回归失败。唯一跳过项需要真实打包的Windows Harness EXE；开发阶段不为消除跳过而提前打包。

一项失败暴露实际排除问题：额外季度图轴旁路用标题季度、等间距刻度和系列图例代替原横轴证明。已经删除该旁路，统一复用要求真实年份或类别横轴的`pdf-chart-layout.v1`规则。原缺横轴负例保持不变，新正例须提供贴近底轴的真实类别；标题及系列图例不能替代。真实44页重建中6个百分比恢复待处理，314条陈述、245条数字解析成功、69条结构未解析、69条语义待审；2,141个原文位置全部守恒，新增合法金额或比例排除为0。修复收据为`runs/regression-dev3/quarter-baseline-production-freeze-07.json`，193项定向回归通过，原06输出与锁保持原字节。

其他五项失败是已登记独立`_UNSPECIFIED`来源标签与旧`UNKNOWN/unresolved`预期不一致。仅更新相关测试，没有放宽取数或公式权限：缺单位或期间仍X6，ROIC和周转率缺授权公式仍X2，资本公积增加的变化定义未知仍X3；预测列和EV/EBITDA估值保持范围外。新增四组金额巧合、伪造一致绑定仍不得调用任何事实查询的反例，139项回归通过。收据为`runs/regression-dev3/source-contract-06b-freeze.json`。

07以`runs/regression-dev3/source-freeze-stable-07.json`重新冻结统一版本。全年报分析、44页原研报、数值与准则追问、原生补证、实际Web/API及双层重放均在新目录复验；开发阶段未制作EXE或发行ZIP。全文空白盲标入口同步到`runs/full-report-blind-dev3-v4/tasks.json`，44页及1,433块原索引、原材料与执行任务身份和v3完全一致，仅登记新的解析代码锁；旧所有文件哈希仍与原收据一致。三名真人签认和金融召回尚待实际执行，不把这次重新准备当作质量验收。

07全量实际结束为1,704 passed、0 failed、1 bundled-only skipped，20项警告；`runs/regression-dev3/full-stable-07.xml`为原记录。原44页研报实际77次模型请求、419,597 Token，A/B各81条、69条语义待审均提交，314条输出为6 verified、0 violated、308 abstained，双层重放通过；6个verified是2个独立归母净利润事实在原文中的重复出现，不是6个独立指标或已测召回。分析57可用/83弃权指标、两类追问、补证及源码Web均技术通过。

07真实原生Web/API没有通过：两条NP_PARENT陈述的A/B公司字段不一致，planner留空、verifier使用冻结的并行科技主体，23条全部弃权。模型调用成功、A/B各10条完整提交及`partial=false`不能掩盖这项失败；Web双层重放未执行。原失败及诊断保存在`runs/source-native-web-stable-07/acceptance.json`和`runs/native-api-review-stable-07/acceptance.json`，统一汇总`runs/development-stable-07/acceptance.json`仍是`needs_improvement`。工具中本句公司是否显式出现与宿主锁定材料主体的适用关系缺少清晰契约，且未绑定的旧未知口径被提前报告为口径冲突；后续最小修复不得自动补空公司、复制A到B或改写原失败。

## 历史统一稳定版08

08解决了上述契约与诊断问题。两名绑定者收到相同的精简`issuer_context`：仅冻结材料主体与原提及身份一致、源证明为宿主主体上下文且无其他公司或歧义时适用。本句`explicit.company=false`继续如实表示省略公司名称，不改成源显式声明；模型仍独立提交，公司未知仍合法X3。可信公司名含数字时保留完整身份，候选和相邻财务数值仍屏蔽。公司上下文不授权默认合并口径。未达成一致时，旧解析器未知口径不再被误报成模型口径冲突；实际源口径缺失、澄清冲突和已提交错误口径仍拒绝。361项定向回归及新匿名原失败反例证明这些限制，收据为`runs/regression-dev3/issuer-context-production-freeze-08.json`。

实际全量结果为 **1,748 passed、0 failed、1 skipped**，20项警告，666.27秒。唯一跳过项需要真正打包的Windows Harness EXE，未计作通过。253个冻结实现、测试与资源文件从回归开始至全部真实运行及汇总结束保持一致。前端147份源码对应85份静态资源的原增量构建收据验证通过，本轮未重新构建前端或制作EXE/发行ZIP。

统一收据`runs/development-stable-08/acceptance.json`的18项技术检查均通过，状态为 **`development_checks_passed_final_acceptance_pending`**，`professional_acceptance=false`、`release_acceptance=false`。它逐一校验原manifest、输入输出与模型缓存、审计链、材料消息锁、原生插件和运行时锁；06/07失败及原锁仍原样保留。

| 同一08代码的真实执行 | 实际结果 | 原收据 |
| --- | --- | --- |
| 原44页研报 | 314条陈述，6 verified/0 violated/308 abstained；79次真实请求、440,490 Token、271.31秒；A/B各81条，69条语义待审全部提交；两层离线重放通过。 | `runs/source-real-stable-08/source_acceptance.json` |
| 财报分析 | 332条事实，140项指标中57 verified/83合法弃权；1项会计政策变更；证据、授权公式和操作数齐全，两层重放通过。 | `runs/source-analysis-stable-08/source_acceptance.json` |
| 数值与准则追问 | 营业收入同比操作数/公式/证据进入答案；CAS28追溯调整法完整定义与原始来源核验通过，外部知识仍不授权判定；两条消息原生离线重放通过。分析与两次追问合12次请求、42,390 Token。 | `runs/regression-dev3/analysis-followups-stable-08.json` |
| 原生补证 | 父任务缺材弃权，Agent自主请求局部重解析；子任务独立A/B提交后1 verified；原PDF数字及页级证据完整，父子四次重放通过。 | `runs/source-native-reparse-stable-08/acceptance.json` |
| 真实Web/API | 同一原研报页面2 verified/21 abstained，A/B各10条独立提交；两条NP_PARENT公司、期间、口径一致，实际执行facts/rule/verify；18次请求、75,851 Token；HTTP双层重放通过。 | `runs/source-native-web-stable-08/acceptance.json` |
| 源码Web功能 | 130次实际HTTP操作；11条虚构演示为4 verified/5 violated/2 abstained；配置、鉴权、路径、取消、历史、证据、导出、重启和内核重放通过。 | `runs/source-web-stable-08/acceptance.json` |
| 生成机制评测 | 354/354严格匹配，错误误纳0/240、陷阱误报0/60、修复后240/240通过；32个工作点实际重跑。 | `runs/generated-stable-08/metrics.json` |

上述列明的真实任务合140次模型请求、645,435 Token，供应商仍为用户已保存的`legacy-default/deepseek-flash`，费用未知；没有擅自更换模型、覆盖配置或记录明文密钥。所有已执行的内核、原生工具循环及消息重放数字一致、外连为0。

**全文金融质量仍未最终达标。** 6个verified只对应2个独立公司/科目/期间事实的重复出现；308项弃权为X3=191、X2=58、X6=59。原稿没有用户声明的合并/母公司口径，不能代填；未授权指标、来源定义与结构不足均保持可见。`runs/regression-dev3/source-quality-stable-08.json`独立关联本次原产物哈希，明确提及召回、完整材料可核查覆盖、精确率和F1仍未知。生成集成功与账本全部结算不能证明这些专业指标；`dual_binding_fixture`仍只有38/354严格匹配和30/354覆盖，且不是实际双模型实验。

后续正式闸门仍要求三名独立真人金标、30张规则卡专业签发、20项外部抽检、完整实验及未曝光D3一次评测、具备凭据时的Kimi/GPT真实验收，随后才构建最终EXE并在独立Windows环境验证。当前全文空白盲标任务为`runs/full-report-blind-dev3-v4/tasks.json`，A/P包为`bench/v1/human_blind_v6_corrected/`，专业任务为`bench/v1/professional_pending_20261007/`；这些是可审查的待办材料，没有签认即不算验收通过。D3仍未选择、查看、封存或执行，本轮不发布正式v1.0。

## 09残余修复、真实失败及10准备失败

09的真实专项验收使用已曝光的并行科技2023年原PDF及开发者编写的五条功能探针，不是原44页研报或独立金标。实际2 verified / 3 violated，A/B各提交5条，12次真实请求、49,349 Token；三条修订均再次通过确定性核查，原PDF页级证据和两层离线重放通过。但Agent只读E1，没有实际读取百分点公式卡，严格收据仍为`needs_improvement`，见`runs/source-financial-repairs-stable-09/acceptance.json`。09全量在53%处主动停止后再修源码，保留`full-stable-09-interrupted.json`，不能算回归通过。

后续修复已加入百分点、单季及基本EPS的逐项规则读取守卫；共享派生入口重新查实际维度，禁止用伪造missing绕过X4。普通股股东利润与总归母利润分开登记，加权平均股数须有明确股份单位。普通解析和局部重解析共享半年/单季表头语义；同报告累计成对才能推导单季金额，跨报告和季度二级比率暂不拼接。修订文本保留来源支持的精度并修正涨跌方向，恢复先验证再发布，追加材料继承补证，追问工具拒绝回显密钥。Web保留每个任务的会话，PDF高亮使用同一份验哈希的不可变内存快照。

源码保存审查发现08/09没有保存完整旧Python原字节：08的16个变化模块仅2个可从Git找回，09的7个变化模块均无对应历史副本。因此保留当时的通过记录和原锁，但当前严格复现所需旧源码明确记为unavailable；不能用新代码冒充旧版本。新增`scripts/source_snapshot.py`保存原字节与独立清单，运行时及静态资产保留受保护哈希引用。这是小型开发源码留档，不是发行包。

10准备阶段实际调用快照工具发现已登记的`samples/corpus`被错误拒绝；尚未启动全量或真实API，原10冻结锁和失败记录保留在`runs/regression-dev3/stable-10-preflight-failed.json`。修复后使用新的11锁和运行目录，不覆盖旧收据。A/P的623项空白真人任务、40份PDF，以及30张规则/20项外检锁仍有效；全文v4因解析锁更新需另建v5，原任务与原文索引不得改变。

## 11实际记录与12集中修复

11原源码字节已独立保存并复核：`runs/source-bytes-stable-11/source_snapshot.json`包含401份文件、4,875,408字节和87项受保护资源引用。当前12修改后的工作源码不再等于11，旧字节、旧锁和失败记录均保留，不以新代码冒充旧版重放。

11全量为 **2007 passed、0 failed、1 skipped**，716.89秒，原XML为`runs/regression-dev3/full-stable-11.xml`。唯一跳过项需要实际打包的Windows EXE，不算发布验收。真实供应商继续为用户保存的`legacy-default / deepseek-flash`。

| 11实际执行 | 结果与限制 | 正确记录位置 |
| --- | --- | --- |
| 五条金融修复探针 | 2核实/3违背；13请求、60,801 Token；实际读取百分点公式卡，3条修订重查通过。开发者短稿，不是独立研报金标。 | `runs/source-financial-repairs-stable-11/acceptance.json` |
| 原44页研报 | 313项、6核实/307弃权；78请求、466,744 Token。6次核实是2项归母净利润的重复出现；2141处原始定位保留。 | `runs/source-real-stable-11/source_acceptance.json` |
| 财报分析 | 332事实；140指标中57可用、83弃权；6请求、20,471 Token。包含会计政策变更事件。 | `runs/source-analysis-stable-11b/source_acceptance.json` |
| 数值追问 | 3请求、11,734 Token；公式、操作数和证据进入答案，消息离线重放通过。 | `runs/source-followup-stable-11/acceptance.json` |
| 追溯调整法定义 | 3请求、10,567 Token；完整定义和原来源核验通过。 | `runs/source-knowledge-followup-stable-11b/acceptance.json` |
| 原生Web | 19请求、87,446 Token；A/B各10项，2核实/21弃权，双层重放通过。 | `runs/source-native-web-stable-11/acceptance.json` |
| 源码Web | 129次HTTP；11条虚构演示为4核实/5违背/2弃权，配置、取消、重启、鉴权、证据、导出与重放通过。 | `runs/source-web-stable-11/acceptance.json` |
| 可信补证后的分析恢复 | 6请求、21,463 Token；缺营业收入的私有索引补证后同比恢复。它不是原生A/B自主补证证明。 | `runs/source-native-reparse-stable-11/acceptance.json` |
| 原生自主补证 | 父任务22请求、62,500 Token后仍partial，未实际调用补证，未产生子任务。明确失败。 | `runs/source-native-reparse-stable-11b/acceptance.json` |

原分析11和定义追问11因验收脚本参数错误而失败：事件应为`accounting_policy_change`，定义主语应明确指定`追溯调整法`。正确重跑采用独立11b目录；旧收据没有覆盖，也不能套用默认同名汇总假装整套通过。

12集中修复缺事实时遗漏规则依赖、通用拒绝反馈、检索上下文与多关键词、补证跨块次数，以及辅助脚本的准备失败脱敏收据和参数预检。另实际复现并修复EPS单位覆盖：明确万元/亿元、百分比或股数不能被默认成元/股；普通解析与局部解析共用检查。包含合法单元格单位和其他EPS定义的金融相邻回归324项通过，见`runs/regression-dev3/financial-family-12.xml`。12统一冻结与真实补证尚未完成时，不宣布已验收。

11独立产物复核为`runs/regression-dev3/source-quality-stable-11.json`：原PDF页、数字、操作数和区间核对完成；真实金融提及召回、完整材料覆盖及精确率/F1仍unknown。124条源口径未声明的提及不能被代填合并口径来增加verified。三名真人模板仍空白，D3未选择、查看、封存或执行；此轮不制作EXE、发行ZIP或发布正式v1.0。

## 当前统一版本13

12原生自主补证已真实通过，但财报对照发现EPS的合法增长率列被错误当成单位冲突，332条事实降为329条、57个可用指标降为56个。因此停止12全量回归，保留`runs/regression-dev3/full-stable-12-interrupted.json`、12所有实测及原字节，不能称12完整通过。13使用原始数字/单位边界，并依据原PDF的完整或换行居中列头证明额外的增长率列；百分比不进入EPS事实，无对应列头的百分比或万元、股数冲突仍拒绝。真实原年报第8页的三期EPS与332条事实重新核对通过，原始布局纳入可移植回归，未当作独立金融金标。

13完整回归为 **2165 passed、0 failed、1 skipped**，21项警告，770.03秒。原记录为`runs/regression-dev3/full-stable-13.xml`；唯一跳过项需要真正打包的Windows EXE。源码原字节为`runs/source-bytes-stable-13/source_snapshot.json`，410文件、5,008,870字节和87项受保护资源引用；创建、原字节及当前来源校验通过。回归、真实执行和汇总之间，冻结代码始终相同。

统一收据`runs/development-stable-13/acceptance.json`的18项技术检查全部通过，状态为`development_checks_passed_final_acceptance_pending`，`professional_acceptance=false`、`release_acceptance=false`。它核对冻结输入输出、A/B、原生循环及消息缓存、审计、知识材料、运行时、同版本内核与前端资源；不是最终专业验收证书。

| 同一13版本实测 | 结果与限制 | 收据 |
| --- | --- | --- |
| 原44页研报 | 313项，6核实/0违背/307弃权；A/B各81项、69项语义待审均提交；80请求、491,803 Token，328.27秒；双层重放通过。6核实仅对应2项独立归母净利润事实。 | `runs/source-real-stable-13/source_acceptance.json` |
| 财报分析 | 原年报332事实；140指标中57可用、83合法弃权；6请求、20,486 Token；会计政策事件、证据、规则、操作数及双层重放通过。 | `runs/source-analysis-stable-13/source_acceptance.json` |
| 五条功能探针 | 2核实/3违背，A/B各5项；14请求、65,961 Token；实际读取百分点卡，3条修订再次核查通过。开发者短稿，不是原研报金标。 | `runs/source-financial-repairs-stable-13/acceptance.json` |
| 数值追问 | 5请求、34,890 Token；约58.47%的同比来自登记公式及原年报操作数，保留精度提示和页证据；消息原生重放通过。 | `runs/source-followup-stable-13/acceptance.json` |
| 准则定义追问 | 3请求、11,208 Token；完整追溯调整法定义和原来源核验，引用仍明确不属于签发规则；消息原生重放通过。 | `runs/source-knowledge-followup-stable-13/acceptance.json` |
| 原生自主补证 | Agent实际缺材核查、检索、局部重解析及冻结；新子任务独立A/B提交后1核实，父任务保持1合法弃权。父子32请求、127,850 Token，原PDF/父任务保持；四次重放通过。使用人为删去营收的私有索引检验机制。 | `runs/source-native-reparse-stable-13/acceptance.json` |
| 原生Web/API | 已曝光原研报第1页2核实/21弃权，A/B各10项；20请求、96,293 Token，399次HTTP，双层重放通过。不是全文质量抽样。 | `runs/source-native-web-stable-13/acceptance.json` |
| 源码Web闭环 | 130次HTTP，11条虚构演示为4核实/5违背/2弃权；配置、鉴权、路径、取消、重启、页证据、导出和重放通过。 | `runs/source-web-stable-13/acceptance.json` |
| 生成机制 | 354/354严格匹配、错误误纳0/240、32个工作点实际重跑；不能代替真人或保留集金融指标。 | `runs/generated-stable-13/metrics.json` |

上述真实执行合 **160请求、848,491 Token**，供应商始终为保存的`legacy-default / deepseek-flash`，费用unknown。已执行的所有内核、原生工具与消息重放一致且外连为0；配置/DPAPI文件保持原字节，实测产物未发现明文密钥。重放与恢复补证预算跨块/会话累计，缺事实仍需实际读取对应规则，拒绝信息只返回可修正的静态契约。

只读金融产物审查`runs/regression-dev3/source-quality-stable-13.json`逐项校验原PDF、区间与操作数，保留2141处原始定位；召回、A集完整材料覆盖、精确率/F1和独立修订通过率仍unknown。源口径缺失、未签发公式及结构不足仍展示弃权，不增加来源不支持的verified。

现有金融载体无需重新复制或展开：`runs/staged-finance-isolation-stable-13/acceptance.json`证明内嵌Node、移除系统Node PATH、原生3工具/4步、跨进程恢复/取消及断网缓存重放。4,101项文件导入均留在锁定闭包。载体仍为161,392,943字节；此项为无密钥本地fixture，不是Kimi/GPT实测、独立Windows或最终EXE体积。

最新全文盲标为`runs/full-report-blind-dev3-v7/tasks.json`，仍是同一44页/1433块原稿与原任务；A/P和30规则/20抽检包不需重复生成。三名真人模板仍空白，D3未选择、查看、封存或执行。正式金标、规则专业签发、完整实验及未曝光保留集、具备凭据的Kimi/GPT实测与独立Windows发布闸门尚未完成；本轮不制作最终EXE、发行ZIP或正式v1.0标签。
