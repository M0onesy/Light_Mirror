"""Rebuild dev3 documentation without touching the original delivery bundle.

Run from any directory with Python 3.12 and lxml. Word rendering is a separate
final step so field updates and PDF export happen only once after review.
"""
from __future__ import annotations

import hashlib
import argparse
import json
import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from lxml import etree as ET

PROJECT = Path(__file__).resolve().parents[2]
WORKSPACE = PROJECT.parent
DOCS = PROJECT / "artifacts" / "documents" / "dev3-revision"
DELIVERY = PROJECT / "evidence" / "documentation"
ORIGINAL = None  # A historical input must be selected explicitly at invocation.
VERSION = "1.0.0.dev3"
NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
W = "{" + NS["w"] + "}"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


PLAN = {
18: "本项目拟建设“明镜 MingJing”——面向A股历史财务事实的研究报告纠错核查与财报分析智能体。以选题五为主线，选题二共享事实、公式和证据能力。使用者上传研报、财报后，系统按内容哈希入库，从原文识别公司、期间与公告日期，调取同公司且截至研报日可用的披露。上传工作台拟统一进入原生Agent任务：模型负责语义理解、检索和补证，Python内核负责原文数值、事实查询、计算、判定及替换数字。来源精度、披露版本、币种、业务维度和指标定义共同约束结论；标准模式在公开登记的候选定义与假设下进行条件核查，审慎模式要求来源明确，不凭金额匹配补造口径。预测和估值只核内部勾稽，行业与第三方数字只作标明等级的原文佐证，观点不判对错。每项结果提供原文位置、依据等级、操作数、计算式、来源证据与修改文本，并保留缺证与待处理项。",
19: "工程方案复用锁定的DeepSeek Harness 0.1.6-alpha.2源码快照，通过金融profile、Cordis插件和受控Python桥接构建独立业务系统。产品拟以MingJing.exe为普通用户入口，首页为上传工作台；知识库材料绑定到任务和会话，支持财报分析、追问、补证、恢复与离线重放。planner与verifier在独立会话中提交A/B绑定，可信协调器比较后调用统一内核；报告数字只能引用冻结槽位。DeepSeek、Kimi、OpenAI GPT和自定义协议按用户配置路由，各供应商分别验真实工具往返。密钥由Windows DPAPI与批准的凭据代理管理。质量验收采用独立真人金标、程序化错误与相邻陷阱、实际基线和消融、未曝光D3，并将技术回归、金融质量和发行验收分层登记。",
238: "（1）将信任边界落实到接口。模型拟仅提交资源编号、语义枚举和引用槽位，数值观测来自原文解析，事实值来自冻结披露，计算和判定由统一内核执行。工具Schema、身份与资源守卫、输出复扫共同拒绝自由数值、越权调用及材料中的提示注入；验收以实际拒绝记录和工具轨迹证明。",
243: "（6）业务、模型与工具的可核验重放。任务锁拟冻结材料及识别元数据、配置revision、规则、角色、插件、运行时和执行源码；kernel replay重新计算数值，agent replay用匹配缓存重跑原生工具循环，消息重放冻结父任务、追问、引用和最终答案。输入、输出及源码锁先验证，缺缓存或不兼容明确失败，不联网兜底。",
244: "（7）上传入口与本地知识库形成同一任务闭环。文件按SHA-256去重，解析和事实缓存包含后端及代码指纹；内容或识别元数据改变时重新验证。只上传研报时拟自动调取同公司的截止日前材料，用户可查看并确认资源清单。会话中的追问、补证和重新核查均沿用冻结资源边界，追加材料建立新版本，不覆盖原结果。",
263: "DeepSeek、Kimi、GPT或兼容服务按用户路由调用原生Harness工具循环，承担语义绑定、检索与报告组织；上传工作台与高级会话调用统一宿主和内核，无模型时明确采用纯内核模式",
368: "公理一：结论数字来自可验证的数据和程序。模型输入可包含授权材料，绑定视图屏蔽待判数值；模型输出受限于资源ID、语义枚举和报告槽位。原文观测由解析器产生，来源事实、计算值与判定由内核产生。即使模型给出金额或公式，也不得进入事实和判定接口。",
376: "在线平面由Python宿主管理材料锁、知识库会话、任务状态、权限与结果；上传工作台调用同一任务入口。L0复用Harness原生循环、模型流与会话生命周期，L1提供金融profile、RPC桥接、角色、工具和守卫，L2为统一Decimal判定内核及MetricSpec注册。Node工具经受控stdio调用Python dispatcher，身份由插件注入后再次校验；标准MCP复用dispatcher。无模型或原生故障时显式降级到内核模式，保留未处理项和执行路径。",
405: "金融profile不向模型提供开放搜索、任意Shell、代码执行、任意文件写入及遥测；批准模型连接和受控宿主归档分别审计",
466: "草稿核查面向发布前质控与研究员自查，研报日期取用户确认的拟发布日期；回溯核查面向已发布报告，使用其原发布日。自动识别的日期可以修正，但任务开始后冻结，重新识别或更改日期必须创建新版本。缺回溯日期或来源公告日期未知时，不把材料推定为当时可用。",
468: "产品拟提供统一的上传工作台、材料会话和纯内核入口。首页上传一份或一批研报、财报，识别文档类型、公司、代码、报告期与日期，并由用户确认参考材料；在线核查通过原生Harness循环执行语义绑定和金融工具，财报分析、追问及补证复用同一资源和内核。纯内核入口用于无模型演示、离线回归与明确降级；执行模式进入清单，不将固定API流水线称为原生Agent。",
469: "知识库拟按SHA-256去重存储原件，缓存包含解析后端、版本与代码指纹，识别元数据修改触发相关缓存失效。相同公司同期间的不同文件保留多版本，按公告日和声明来源选择，未来或未知日期材料不进入事实、文本佐证和引用。研报关联同公司可用披露后形成冻结材料会话，追问引用当前会话实际读取的证据；补材、澄清与局部重解析创建可追溯子任务。虚构样例用于展示错误定位、修改、财报勾稽和离线重放，不替代真实质量评测。",
617: "mingjing金融profile运行契约（实现与验收源码随项目提交） 允许：登记金融工具、批准模型协议、持久会话与受控RPC 禁用：开放搜索、任意Shell/代码/文件工具、自修改、插件安装及遥测 模型入参：资源ID、登记维度、语义枚举；不得提交事实金额、自由公式或判定 身份：插件注入session/role/call_id/resource_lock，宿主再次校验 凭据：DPAPI引用经批准私有RPC解析，不写入命令行或普通导出 恢复与重放：先验输入输出及源码/资源锁；缺缓存失败且零外连",
766: "单季推导须有同公司、口径、连续期间和披露版本依据，优先使用明示季度值，跨报告拼接需独立授权。横线与空格分开登记：标准模式仅在表实体、列报习惯和规则允许时将横线按零计算并披露假设，审慎模式不采用；空单元格、缺列和来源冲突不能转为零。法定报表、业务表和券商简表保留来源身份，冲突输出X4与A4。",
783: "三级均无依据时输出X2。正基数同比、负基数绝对值增速和亏损收窄采用不同登记定义；零基数或缺授权前提保持未知。公式限于登记AST、科目和常数，校验量纲、期间、口径与方法。MetricSpec拟作为唯一注册源，贯通识别、候选定义、绑定枚举、工具依赖、核查、分析和重放；法定指标、来源表转述与券商自定义指标分别授权，不按金额拼造公式。",
811: "区间不相交时，内核分别检查数值差异和错误成因。单位、期间、口径、引用及版本诊断须有原单位、列头、声明或披露版本证据；只有金额相容的其他解释以“疑似原因”展示，不升级为E2、E3或E9。表5-6约定正式分类及其证据前提。",
856: "输出核实、违背、未能核实，并分列范围外与待处理。标准模式拟使用事先登记的有限候选定义，默认合并等通行假设逐条披露；核实结论注明所依赖条件，不能声称已经证明研报真实意图。审慎模式要求口径及方法来自原文、来源表或明确声明，无证口径、仅备选定义命中与需横线按零时弃权；合法上下文期间继承也须可反查。缺事实、公式前提、维度或方法时列完整阻断链和补材动作。取消、超时、预算耗尽保持pending/partial；补材和澄清创建新版本，旧结果只读。",
865: "口径、方法或绑定不确定；候选定义缺材料无法完成；原生A/B未知或不一致；审慎模式仅靠推定",
924: "财报分析拟将上述检查逐项登记输入、公式、披露精度、证据和状态，并据结构化结果生成要点。不一致项提供可复查差异，重叠分部或缺项标为无法判定。验收采用真实原表与相邻错误、缺格、重述和负基数反例；检查项数量只证明已执行范围，不证明整份报告无错误。",
926: "先对每个数字出现位置建立账本，再按依据等级决定核查方式（表5-10）。公司历史财务、开放定义指标、内部勾稽、文本佐证与观点分层登记，各层分别报告分母、已判、弃权和待处理。预测表与估值的数学相容不能计作历史财务真实性或真人召回。",
933: "按来源披露或授权公式核查，候选定义事先登记；结论标注假设与来源精度",
936: "登记定义命中时给条件相容与定义说明；缺方法或候选材料不全时弃权，不判有误",
945: "按截止日内批准材料检索同值与语境，仅记原文佐证；缺外部真值不判数字错误",
946: "多义指标的候选定义须有限、公开且通过统一MetricSpec登记（表5-11）。每个候选有方法来源、适用范围、公式及前提，内核逐一计算并披露命中。标准模式下命中仅表示在列明条件下相容，不反推研报作者意图；审慎模式要求明示方法。候选材料缺失导致未算全时，不因已算候选不命中而判错。",
966: "正式错误码依据证据前提归类：单位E4、期间E2、口径E3、计算E5、直接数值E1；增减方向或比较关系错误为E7。正文与自身预测表冲突为E6，表内计算及估值公式不平为E5。其他期间或科目金额恰好相容时仅给疑因提示，正式码仍由已证明差异决定；E9须有适用披露策略和版本证据。",
967: "全部来源通道拟使用同一截止日守卫：未来、公告未知或文件哈希变化的材料不能进入事实、文本佐证、引用、补证和重放。季报单季与累计须有列头证据，推算须有批准公式、同口径与可用版本；明确引用只复核所引文件。摘要由结构化模板或reporter生成，模型只能引用冻结数字和知识槽位，输出复扫拒绝自由数字、未读证据与无依据结论。",
968: "标准模式拟逐条披露默认合并、上下文期间、有限备选定义、负基数绝对值、横线按零、单季推算和内部勾稽等条件；审慎模式拒绝没有来源证明的口径与方法、备选定义推定和横线按零。负基数定义须明确区分披露转述与授权重算。首页行情按版面读取并校验股价×股数与市值；缺口按缺报告、缺明细、方法、外部来源和原文结构分组，不承诺补一份报告必定解除所有关联弃权。",
1077: "审慎模式X3；标准模式仅在登记条件下核查并披露",
1078: "金额相容与A/B一致不能证明原文定义；条件结果单独评测",
1086: "证据拟以文件哈希、页码、区域坐标、原片段及表格行列定位。宿主使用已验SHA-256的原件渲染页图并框出实际证据区域；全文无坐标时标明页级文本证据，不能声称单元格高亮已确认。原件、解析后端和代码锁共同登记，文件变化拒绝沿用旧证据。",
1088: "审计拟关联文件访问、工具、模型、计算、判定与渲染事件，并登记run/session/call身份、时间和前一条哈希。哈希链检查现存记录衔接与内容完整性；检出尾部截断还须将条数与链头纳入独立冻结清单。文件访问由共享dispatcher和知识库自行记录，与原生会话日志关联，不能只记录模型表面工具名。",
1115: "每次任务拟生成完整清单：草稿与参考原件哈希、识别元数据、研报日期、解析后端、事实与规则、注册表、源码、配置revision、插件、运行时与UI来源锁；登记请求及实际响应模型、实时/录制/重放方式、环境、预算和用量，列全部冻结输出哈希。未知价格记unknown，不推算为账单。",
1117: "金融profile不提供开放网络或任意执行工具，宿主只消费批准资源并受控归档。完整和轻量服务均须检查实际监听地址、Host/Origin、鉴权及请求长度；模型请求统一经批准协议验证TLS和重定向，拒绝跨源或HTTPS降级。Windows密钥使用DPAPI，经私有凭据RPC授权读取，不进入命令行、会话、缓存、异常或导出。受控官方建库与模型连接分开审计；缓存重放严格零外连。",
1119: "kernel replay拟按冻结原文、绑定、事实、定义与规则重算，并比较状态、期望值、公式、操作数、证据、假设及分析检查。agent replay以精确请求缓存重跑真实Harness循环并比对绑定、工具轨迹与冻结报告；消息重放关联父任务和引用。先验原始输入、输出和源码资源锁，缺缓存、不兼容或篡改立即失败，不联网补齐。实时重跑的模型稳定性单独评测。",
1120: "工作台与知识库拟提供条目和会话重放入口，核查及财报分析使用同一完整性协议，核对全部金融产物而非仅顶部统计。重放可审查原件和结果版本，补材建立新任务；知识库日志校验返回现存链头与条数，并与冻结可信摘要比对，区分内容篡改、断链和尾部截断。",
1293: "正式比例报告样本量、Wilson 95%区间、数据及代码/规则/运行时版本和运行编号；未知或未实测项不作为成绩。工作点完整报告，不只选最好单点。生成样例、重复执行、开发回归、原生技术闭环和独立金融金标分别登记；第十章说明工程方法及验收证据，不用测试数量证明金融正确。",
1326: "拟打开上传工作台与材料会话，提供核查、分析、追问、补证、重放与导出",
1357: "产品拟以单个MingJing.exe启动本地服务并打开上传工作台，知识库和结果在同一页面管理；会话面向多轮检索、追问、补证与恢复，复用Harness原生UI与循环并调用统一内核。设置管理供应商、协议、角色路由、预算、路径和凭据。发行包嵌入必要Python/解析/MCP/规则/UI及独立金融载体，财报语料与个人配置外置；首次按哈希原子发布缓存，运行期不安装npm/pnpm。CLI保留离线、开发与评测入口。",
1394: "工程验收拟按表10-4登记输入、执行方式、输出与版本收据，并使用真人或原披露对照核验。此表说明如何证明各项功能；正式金融质量按第九章独立数据集产生，不以自生成样例、技术回归或程序输出自证准确率。",
1395: "表10-4 工程能力的验收场景与证据设计",
1397: "验收材料与方法",
1398: "拟登记证据与通过条件",
1400: "真实原PDF完整表、附注、季度与分部；不同后端对照人工原格",
1401: "事实数、覆盖列、漏项和错值逐项登记；单位、负号、版本及页/单元格正确，差异可解释",
1403: "完整真实研报与截至发布日的可用材料；独立数字位置金标",
1404: "按历史财务、开放定义、预测估值和文本佐证列分母；正式召回、误纳、弃权按第九章验收",
1406: "真实财报及恒等式、同比、季度、跨表、分部的正反边界",
1407: "每项登记公式、完整操作数与证据；缺格、重叠和冲突不强判一致，实际错误可定位",
1409: "虚构样例与独立真实错误；每类E码配相邻合法换算/缺证反例",
1410: "登记适用错误类型及实测分母；修改后真实再核查；样例结果不替代独立金融评测",
1412: "内核、契约、权限、生命周期及浏览器测试；同版本冻结运行",
1413: "按实际运行器登记passed/failed/error/skipped及依赖；失败留存，跳过不能记通过",
1415: "冻结任务及追问；禁网重跑内核、原生缓存循环和消息",
1416: "先验完整输入/输出/源码锁；比较数值、定义、证据、假设、检查与轨迹，缺缓存明确失败",
1418: "上传→知识库→原生会话→核查/分析→追问补证→重放导出",
1419: "实际HTTP/浏览器收据、原页定位、取消恢复与窄屏；供应商分别验真实多工具往返",
1421: "实际不同系统/模型、单/双绑定及点值/区间消融；完整复跑",
1422: "统一范围与金标，记录各自提及及绑定分母、误纳、误报、Token和时延；不凭单样例推断泛化",
1426: "团队拟设置五个工作包（表11-1），实际成员与备份由真人确认。规则编写与评测出题分离，规则起草与专业签发分离；三至四人团队可合并工程和产品职责，但不合并独立评测责任。决赛按通知配备指导教师。",
1433: "WP1 平台与Agent",
1436: "统一上传到原生Loop、身份/RPC、MCP、多供应商、权限和生命周期；对任务会话负责",
1437: "WP2",
1438: "WP2 材料与知识库",
1441: "原PDF、识别与事实、截止日/发行人、证据、缓存、知识库会话和补材资源锁",
1442: "WP1",
1443: "WP3 金融内核",
1446: "统一MetricSpec与规则、标准/审慎策略、定义及量纲、E/X/A、分析勾稽、专业签发",
1447: "WP1",
1448: "WP4 独立评测",
1451: "独立真人金标、样例反例、基线/消融、D3、收据与提交数字审核；不代签",
1452: "指导教师/独立审阅者",
1453: "WP5 产品与交付",
1456: "上传和会话UI、计划书/视频、手册、版本与许可、审阅/源码/EXE、独立环境演练",
1457: "WP1",
1460: "实施按六阶段推进：基线冻结；上传到原生多工具竖切；知识库、金融账本及统一内核；会话、多供应商和生命周期；独立金标与真实实验；同版本发行和独立环境演练。报名截止2026年10月11日，初赛作品截止10月18日；11月中旬名单及11月底决赛为通知预计，具体以组委会更新为准。初赛PDF与视频单独冻结，各阶段记录实际证据，不以材料提交替代质量验收。",
1506: "团队与进度可行性。五个工作包覆盖工程、会计与金融能力，每项指定真人主责及备份；规则和独立评测分离。六阶段围绕可复查交付物推进，初赛材料与后续质量、发行分层完成；不以删除未知项、补造定义或改变指标分母缩短验收。",
1520: "发行后在独立Windows验证单EXE；开发阶段先验源码与载体闭包，冻结后一次构建",
1556: "条件结果逐条披露假设；审慎模式拒绝无来源定义；真人金标单独评价条件误纳和模式差异",
1559: "按真实原文意图独立标注，分别统计默认合并、备选定义、横线及期间假设；测误纳和置信区间",
1562: "拟准备实时原生Agent、断网Agent/消息重放和纯内核降级三种演练，均基于相同的任务锁、规则与证据；离线演示另使用虚构数据。正式发行在独立Windows 11 x64验证无外部Python/Node/pnpm启动、PDF、密钥/配置保持、正常退出与恢复。初赛MP4不超过5分钟、500MB，展示实际核查链和修改，不将预录或缓存演示称为实时新调用。",
1627: "正式源码须与现场展示版一致，包含任务编排、Tool、Prompt/Skill、MCP、数据、日志、依赖和运行说明。公开协作仓库保留明镜与锁定上游源码/许可，排除密钥、个人配置、真实模型缓存和未授权材料。完整开发/复现包提供所需闭包；不超过30MB核心包仅供审阅，不能替代完整原生运行或全部源码。EXE按同版构建输入、资源、SHA-256与实际验收登记，未知或未执行项如实标识。",
1635: "评委版计划书PDF；MP4介绍视频≤5分钟、≤500MB；技术路线、目标与验收方法，2026年10月18日前提交",
1637: "1.0.0.dev3阶段源码/原型、使用手册、许可、哈希与实际回执；按完成条件再生成同版本EXE候选",
1639: "最终报告、可运行Web/CLI（选定EXE）、现场同版完整源码/依赖/说明、实时样例与答辩；质量及环境收据",
1641: "干净源码仓库、完整开发拷贝/重放闭包指引、≤30MB核心审阅包、逐文件哈希及产物边界",
1649: "近期围绕上传工作台、材料知识库和原生会话建立核查、分析、追问与补证闭环，完成统一规则、独立真值和全文质量验收；中期在专业签发基础上扩展行业口径及研究机构流程，公开可再分发的代码、规则结构和评测方法。模型供应商按用户路由替换，实际语义能力分别验证，事实、公式、证据和数值判定继续由明镜宿主管理。",
}


def update_plan() -> dict:
    source = ORIGINAL / "明镜MingJing_项目计划书.docx"
    target = DOCS / source.name
    backup = DOCS / "原件留档" / (source.stem + "_融合原件.docx")
    backup.parent.mkdir(parents=True, exist_ok=True)
    if not backup.exists():
        shutil.copy2(source, backup)
    with zipfile.ZipFile(source) as z:
        root = ET.fromstring(z.read("word/document.xml"))
        paragraphs = root.findall(".//w:p", NS)
        before = len(paragraphs)
        for index, value in PLAN.items():
            p = paragraphs[index]
            texts = p.findall(".//w:t", NS)
            if not texts:
                raise ValueError(f"Paragraph {index} has no text")
            texts[0].text = value
            texts[0].set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
            for text in texts[1:]:
                text.text = ""
        for text in root.findall(".//w:t", NS):
            if text.text:
                text.text = text.text.replace("mainProj/mingjing-next", "mainProj/新版/mingjing").replace("1.0.0.dev2", VERSION)
        settings = ET.fromstring(z.read("word/settings.xml"))
        field = settings.find("w:updateFields", NS)
        if field is None:
            field = ET.SubElement(settings, W + "updateFields")
        field.set(W + "val", "true")
        staged = target.with_suffix(".tmp.docx")
        with zipfile.ZipFile(staged, "w", zipfile.ZIP_DEFLATED) as out:
            for item in z.infolist():
                data = z.read(item.filename)
                if item.filename == "word/document.xml":
                    data = ET.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True)
                elif item.filename == "word/settings.xml":
                    data = ET.tostring(settings, encoding="UTF-8", xml_declaration=True, standalone=True)
                out.writestr(item, data)
    staged.replace(target)
    after = len(root.findall(".//w:p", NS))
    assert after == before
    return {"source": str(source), "source_sha256": sha(source), "target": str(target), "target_sha256": sha(target),
            "replaced_paragraphs": len(PLAN), "paragraph_count_preserved": before,
            "tables": len(root.findall(".//w:tbl", NS)), "drawings": len(root.findall(".//w:drawing", NS))}


MANUAL = r'''# 明镜 MingJing 开发任务与文档手册（1.0.0.dev3）

> 内部协作版，2026-10-10。唯一工作工程为 `mainProj/新版/mingjing`，需求文档在 `mainProj/新版/文档`，阶段交付写入 `交付/新版`。原融合交付包和旧运行证据只读。本手册包含当前问题，评委版计划书保持十四章，表述目标成果、实施方法与验收设计；不把内部失败日志作为计划书正文。成员姓名和专业签认由真人确认。

## 0. 基线、提交日程与证据规则

统一开发版本为 **1.0.0.dev3**。版本号不是验收结论；实际成绩只能来自关联同版输入、源码、规则、UI、插件和运行时哈希的原始收据。修改、回归、真实API和发行分别登记，fixture不冒充真实供应商，技术通过不冒充金融质量。旧实测保留原版本与范围，不能继承为本版成绩。

| 正式日程 | 通知要求 | 操作 |
| --- | --- | --- |
| 报名截止2026-10-11 | 3–5人、资格证明，确认名单不能改；未规定截止小时 | 队长核实报名状态与真实名单 |
| 初赛作品截止2026-10-18 | PDF计划书、MP4≤5分钟且≤500MB | WP5导出检查与录制，全队审核后提交 |
| 预计11月中旬/拟11月底 | 名单公布/决赛，具体待通知 | 不把预计日期写成固定承诺 |
| 决赛 | 最终报告、可运行Web/CLI、现场同版完整源码/依赖/说明、实时样例、展示答辩；指导教师1人 | 质量与发行闸门后冻结 |

官方要求至少一个LLM为核心推理引擎、结构化产物、封闭/半封闭环境、访问/工具/计算/生成留痕和可复现，以及第三方范围说明。Harness、单EXE、30MB核心审阅、GitHub、三人金标、D3和数值门槛是项目路线与内部承诺。

## 1. 产品与规则的唯一契约

首页上传研报/财报→知识库入库与自动识别→确认公司、日期和可用材料→冻结任务/会话→原生Harness模型—工具—观察循环→A/B独立绑定→统一MetricSpec与Decimal内核→核查/分析与槽位报告。工作台不能仅启动固定HTTP模型流水线后称原生Agent；必须提供实际会话、依赖工具及观察轨迹。无配置、原生不可用或预算超限时明确显示纯内核降级、partial和未处理项。

| 策略 | 标准模式 | 审慎模式 |
| --- | --- | --- |
| 无明示合并/母公司 | 仅在登记通行假设下条件核查，逐条披露 | 无来源/声明证明则X3，不默认合并 |
| 多义指标 | 有限候选逐一计算，注明命中定义和条件 | 来源明确且被授权；只靠备选命中则X3 |
| 上下文期间/主体 | 保留原文出处、继承边界与假设 | 只有可反查的来源上下文允许继承，不能补造维度 |
| 负基数增速 | 披露转述、绝对值增速与亏损收窄分别登记 | 同样要求授权定义和完整前提，不套正基数公式 |
| 横线/空格 | 原表实体与规则允许的横线可按零并披露；空格/缺列不置零 | 横线不推零；缺项保持未知 |
| 错误诊断 | 金额巧合只给疑因，不证明E2/E3/E9 | 同一证据前提 |
| 预测/估值 | 仅核研报自身表、行情和公式 | 同一范围边界 |
| 外部/行业 | 截止日内原文佐证，标明未经外部独立核实 | 同一来源时点与证据等级 |

“核实”要区分直接披露、授权推算、内部勾稽、恒等式及文本佐证；条件核查的假设不能当作研报真实意图。各层都保留原文出现与去重断言；覆盖、召回、正确率和执行进度使用不同分母。

知识库材料由SHA-256寻址，但每次实际读取及重放仍须验哈希。缓存键包含后端、代码、识别元数据及事实/规则注册锁。未来或公告未知材料不能进入事实、原文佐证、引用、补证或离线重放。会话绑定冻结资源，追问只读实际工具取到的槽位；补材、澄清、追加文件、局部重解析创建子版本，不覆盖父产物。

安全边界覆盖完整服务及轻量入口：实际监听、Host/Origin、访问Token、请求长度；模型调用统一受控transport，TLS和重定向规则不能旁路。DPAPI拒绝失败持久化，身份/路径/取消守卫须在读取凭据前执行。代理只可读批准资源，不可任意Shell、文件写入、开放搜索或自修改；官方补源由独立有界建库流程完成。

## 2. 五个工作包与复核分离

| 工作包 | 主责与交付 | 负责人/备份 | 验收与交接 |
| --- | --- | --- | --- |
| WP1 平台与Agent | 上传到原生Loop、私有RPC/身份、供应商协议、MCP、会话、取消恢复、HTTP/凭据守卫 | 姓名待真人确认；WP2备份 | 实际依赖工具循环、A/B隔离、拒绝和生命周期回执；交WP4独立核对 |
| WP2 材料与知识库 | 自动识别/发行人、原PDF解析、时点/版本、KB缓存与哈希、证据、补材会话资源 | 姓名待真人确认；WP1备份 | 原格对照、未知/未来材料、篡改及缓存反例；WP3核方法 |
| WP3 金融内核 | MetricSpec唯一注册、规则卡/方法、标准与审慎、Decimal区间、E/X/A、分析勾稽 | 姓名待真人确认；WP1备份 | 来源和完整前提、正反边界、规则签发；WP4独立出题 |
| WP4 独立评测与审计 | 三人盲标、推定误纳、基线/消融、D3、数字和收据审计 | 姓名待真人确认；独立专业审阅者备份 | 不由规则编写者出题、不由AI签认；保留未标/分歧/失败 |
| WP5 产品、文档与发行 | 工作台/会话UI、计划书/视频/手册、源码与审阅包、EXE、许可、独立Windows演练 | 姓名待真人确认；WP1备份 | UI来源/静态锁、同版完整闭包、Word/PDF、包外哈希和实际冷启动 |

每项按“问题→原文/复现→全部阻断链→最小修复→相邻反例→局部回归→必要真实API→关闭收据”执行。通过以前写待验，不因增加测试或HTTP成功自动关闭。规则起草、签发和独立出题尽量分离；真人名单与签认身份不由Agent虚构。

## 3. 验证矩阵与实施次序

先冻结本版输入和审计问题，再修统一注册、严格策略、所有时序与完整性旁路，接通上传到原生任务及KB会话；每组先局部正反例，稳定后一次必要全量。多供应商分别真实工具往返，沿用用户保存路由，不自动切模型/凭据。没提供凭据的供应商记`not_executed`，适配源码与mock仍可单独验契约。

| 层次 | 必须登记 | 禁止替代 |
| --- | --- | --- |
| 机制回归 | 运行器、依赖、passed/failed/error/skipped/timeout、输入锁 | 自制兼容器或skip不能称pytest全通过 |
| 原生技术 | 真实supplier、request/实际model、session/tool/result、A/B、Token、预算、缓存 | mock或固定POST不能称原生闭环 |
| 来源与金融 | 原PDF/定义/精度/版本真值、误纳、分层覆盖、条件假设 | 同值命中或内部相加不能证明外部事实 |
| 独立质量 | 真人三份原始标注、分歧、置信区间、真实实验、新D3 | 自生成样例、账本结算或局部重复不能证明召回 |
| 重放 | 原件/缓存/输出/源码锁先验；数值、证据、公式、假设、分析检查和Agent轨迹 | 只比较顶部统计不能称完整一致 |
| 产品与发行 | 浏览器、鉴权、证据、退出/恢复；同版EXE/资源/许可与独立Windows | 开发机删PATH或旧EXE不能替代独立环境 |

验收目标：数字重放100%；错误误纳verified为0；真实金融提及召回≥90%；完整材料A覆盖≥95%；适用错误精确率≥90%、F1≥85%；修订后实际复查≥95%；完整研报主范围弃权≤9个原文位置。它们都是待验证目标，必须报告样本、分母、Wilson区间和版本。生成集和原生开发产物分开登记，不混算。

正式CRRV设计仍为P重述试点、A多公司原格、B raw/rounded多类型错误、C合法陷阱、X缺证与范围外、冻结后一次未曝光D3。重复局部运行不增加独立原文位置；历史不同版本不能相减得到当前全文质量。三人一致率不可估计不写满分，低于目标按原约定重标。

## 4. 计划书与视频冻结

保留十四章、图表、引用与附录。摘要/1–2章定位及赛题；3–4章统一上传/知识库/原生循环/内核；5–6章模式策略与规则前提；7章实际完整性和重放设计；8章人工治理；9章独立评测目标；10章功能验收证据；11章成员与正式日期；12章风险；13章第三方与产物边界；14章阶段交付。计划书写目标成果、方法与验收设计；当前失败、残余bug和旧版本数字留在内部附件。

Word修订先存原件，逐字/结构核验后一次更新域、目录、交叉引用并导出PDF。检查页码、标题、表格跨页、图边框、公式、中文字体和参考文献。实际团队、院校与指导教师待真人填写。文档脚本仅操作新目录，不重写原融合包。

| 视频时间 | 展示 | 讲述边界 |
| --- | --- | --- |
| 0:00–0:30 | 产品定位与上传首页 | 发布前数字审核；选题五主线、选题二扩展 |
| 0:30–1:15 | 上传→知识库识别→确认材料 | 同公司、公告日期、来源哈希和模式选择 |
| 1:15–2:20 | 原生任务活动→一条金融核查 | 实际模型工具链，原文、公式、规则、证据和修改；不用旧数字自证 |
| 2:20–3:15 | 错误/假设/补材与审慎对照 | 外部事实、内部勾稽、文本佐证分层；未知不判错 |
| 3:15–4:10 | 财报分析→会话追问→补证子版本 | 同一资源、实际取证槽位、父结果不覆盖 |
| 4:10–4:55 | 断网重放与版本清单 | 缓存重放不称实时新调用；列实际验收范围 |

视频录制用隔离用户目录，真实素材仅在允许范围内展示；导出MP4并实测时长≤5分钟、大小≤500MB。提交操作由参赛团队完成，报名和队员名单不能从代码推断。

## 5. 交付与正式发布

`交付/新版`区分源码、审阅、文档、验收记录与运行程序。完整源码含编排、Tool、Prompt/Skill、MCP、数据/日志、依赖、运行说明以及受许可的锁定上游；原生运行或历史复现另带实际资源/材料闭包。核心审阅包≤30MB只供评审源码，不带完整carrier/语料/历史，不能代替完整原生运行包。公开库不含Token、DPAPI、个人配置、真实模型缓存和未经授权材料。

本版源码/资源/UI/文档稳定后再构建一次同版EXE候选，生成build lock、EXE SHA-256、大小与必要smoke回执；未执行项如实标明。ZIP哈希保存在包外，避免自引用。独立金融质量、规则签发、未曝光保留集和独立Windows闸门未通过时，不标正式v1.0。阶段编号和正式质量分开。

## 附录：历史问题与本轮关闭账本

以下保留MJ-P01–P37的原始现象、来源和根因，作为本版问题输入；历史状态不是本版关闭结论。证据原路径指向旧 `mainProj/mingjing-next` 时按原锁只读核对，不搬路径伪造历史重放。所有本轮关闭须在 `交付/新版/验收记录` 或本版runs提供同版本收据。
'''

AUDIT_ITEMS = [
("MJ-P38", "标准/审慎策略不一致", "旧strict仍默认合并、上下文和负基数，README/计划书却称关闭所有推定。", "WP3", "无明示口径在两模式不同，审慎不得给无条件verified；横线/备选/上下文分别验。"),
("MJ-P39", "事实过滤与文本引用的时序旁路", "旧pipeline排除未来facts后仍向文本佐证/引用索引传excluded材料，未知日期也可入库查询。", "WP2/WP3", "同值未来或未知日期材料不得核实；事实、文本、引用、补证、重放共用守卫。"),
("MJ-P40", "重放未核全部产物与锁", "旧仅比九字段，分析漏勾稽/证据/假设；未先验证原输出及实际源码锁。", "WP1/WP3", "修改证据、公式、假设、勾稽、输入或缓存均失败；原件哈希及后端代码锁一致。"),
("MJ-P41", "知识库哈希链尾部截断", "仅验证现存链，无独立可信head/count；删末尾或整份日志仍可ok。", "WP2", "冻结可信摘要后删除末条/全部文件可检出，区分空库与丢失；不承诺无信任根绝对防篡改。"),
("MJ-P42", "HTTP和模型transport旁路", "旧smart轻量无Host/Origin/token准入，urllib直呼与http(s)正则未复用TLS/redirect守卫。", "WP1", "完整/轻量相同准入；跨源/降级重定向、越权监听、恶意Origin、长度与凭据前取消拒绝。"),
("MJ-P43", "两份指标注册和两套任务模型", "直传DEFS/IV/Result与原生MetricSpec/rulepack各自演进，工作台与classic会话缺桥。", "WP1/WP3", "同一授权定义、操作数与判定；删除规则两入口同失效；上传实际原生loop、KB会话可追问补证。"),
("MJ-P44", "诊断金额巧合与正式错误码", "默认按其他期间/兄弟/母公司金额相容归E2/E3，跟表5-6证据前提冲突。", "WP3", "金额巧合仅疑因；没有期间/口径/版本原证不能正式归E2/E3/E9，仍给证明充分的E1/E5。"),
("MJ-P45", "分层指标和自证限制", "627混历史、预测、模板、行业；492含内部/恒等式/文本，94.4%分母含预测而非历史真实性。", "WP4", "分列原文occurrence/去重、证据等级、条件推定及独立真值；0违背不写0误报。"),
("MJ-P46", "测试回执、依赖和运行器", "旧2961项只是minipytest前后未变，含失败/报错/超时；50执行中含继承重复，mock不是真API。", "WP1/WP4", "锁定依赖实际pytest；按全部状态和skip原因登记；真实模型/fixture/浏览器CSP分别注明。"),
("MJ-P47", "原生与供应商真验收分开", "历史原生DeepSeek有真实收据，直传新LLM仅mock；旧源码锁/局部通过不能证明融合本版。", "WP1/WP4", "每个供应商在本版实际多工具往返、request/response model与A/B；缺凭据not_executed。"),
("MJ-P48", "审阅包与完整项目的边界", "旧说明称完整项目但白名单排除vendor/carrier/runs；manifest和README实际声明非独立原生运行。", "WP5", "清单、ZIP和文件夹哈希匹配，核心包≤30MB；同版完整源码/载体闭包与EXE另列。"),
("MJ-P49", "自动识别、缓存和元数据冻结", "内容寻址不自动证明blob未变；缓存签名需包含后端、所有识别元数据、定义和代码；公司/期间更改影响任务。", "WP2", "blob修改拒绝；换后端/代码/公司/日期失效；重复上传及别名不越公司/版本。"),
("MJ-P50", "UI源码、静态产物与发行锁", "新增主页和classic任务入口先后建设，旧UI来源回执及EXE只登记历史版本。", "WP5/WP1", "新首页到实际原生会话闭环，UI源码/静态同锁；同EXE真实运行、导出与独立Windows。"),
("MJ-P51", "文档路径、日期和成绩漂移", "旧手册仍30测试/8错误、旧根与dev2；视频称全部627对回年报，旧Word目录不能提交。", "WP5/WP4", "dev3/唯一路径/策略一致，14章和引用保留，Word更新PDF，数字有本版原收据否则目标。"),
("MJ-P52", "真实工具get_facts局部变量异常", "本版real-small-01真实deepseek-flash执行中get_facts出现UnboundLocalError，金融取数链未完成；不能以模型重试或缓存重放掩盖。", "WP1/WP3", "消除全部分支中的局部变量绑定/初始化问题；合法取数与缺材/冲突反例均返回结构化结果；另建同版真实运行验证，原失败锁保留。"),
("MJ-P53", "A/B与canonical定义守卫不一致", "smart A/B路径与统一内核的scope、segment和base_period守卫不一致，相同原文可在入口间得到不同绑定与权限。", "WP1/WP3", "A/B提交、上传任务、分析、CLI及统一判定共用维度守卫；来源明示、来源缺失及相反定义在两模式分别一致，不能靠临近金额或模型一致通过。"),
("MJ-P54", "覆盖账本重复套用旧strict分类", "CoverageLedger再次使用旧strict rawclaim分类，把119个图轴/页码噪声记为金融弃权，与新版候选分类及任务策略不一致。", "WP2/WP4", "账本消费当前冻结分类与模式，不重复按旧策略解析；图轴/页码按原坐标证据登记范围外且保留原位置，真实图数据不得误排；分层分母可逐项还原。"),
("MJ-P55", "去重结果只映射首次出现", "去重断言发布时仅映射第一处原文，其他重复位置丢失结果或误记未处理；独立事实与原文位置分母因此失配。", "WP2/WP4", "断言与全部occurrence建立明确多对一映射；同一事实三处披露必须保留三位置且只算一事实；段落/表格重复、partial和修订后复查都逐位置可审计。"),
]

AUDIT_STATES = {
    "MJ-P51": "新版DOCX结构与Chromium PDF视觉检查已通过，保留Word原生PDF导出失败；视频、最终源码/发行与文档一致性仍待冻结复核。",
    "MJ-P52": "get_facts修复后real-small-02真实取数链通过；原real-small-01失败保留，待全量与全部错误分支收据。",
    "MJ-P53": "入口守卫修复后real-small-02的A/B与内核链通过；标准/审慎全部定义边界仍按关闭矩阵核对。",
    "MJ-P54": "real-small-02保留未建模金融pending与partial=true，未将模型success标成全文完成；待完整噪声/金融去向逐项复核。",
    "MJ-P55": "real-small-02报告21原文位置与17断言，逐处结果可见；待全量位置守恒、修订与partial反例关闭。",
}


def update_manual() -> None:
    original = (ORIGINAL / "明镜_计划书打磨与开发任务手册.md").read_text("utf-8")
    appendix = original.split("### B. 待解决或待独立验收的问题", 1)[1].split("### C. 已修复问题的持续回归约束", 1)[0]
    tail = "\n### A. MJ-P01–P37历史输入（逐条保留，本版待核对）\n" + appendix
    tail += "\n### B. 本轮审计问题及关闭证据\n\n"
    for code, title, cause, owner, acceptance in AUDIT_ITEMS:
        state = AUDIT_STATES.get(code, "本版待实施/待实际收据；仅在同版本输入、源码和产物校验后登记关闭。")
        tail += f"#### {code} {title}\n\n- 现象与根因：{cause}\n- 主责：{owner}，姓名待真人确认。\n- 实施：先冻结原复现和完整阻断链，最小修复后新增实际正反边界，按对应实现入口局部验证。\n- 关闭验收：{acceptance}\n- 状态：{state}\n\n"
    tail += """### C. 本版真实小样本限制记录

`交付/新版/验收记录/real-small-01/acceptance.json` 保留本版首轮真实小样本，实际路由为用户保存的 `deepseek-flash`，40次真实请求、327,356 Token；模型运行 `partial`，0核实、138弃权。实际内核输出在该运行的 `native-run/results.json`。内核重放100%一致只证明冻结产物可重算，不证明金融结果正确、召回充分、供应商完整闭环或正式质量通过。

此次运行暴露MJ-P52～P55，产品与金融工作包正在修复；原失败产物和原锁保持不变。修复后须另建同版运行，并逐项核对工具取数、A/B/内核维度、噪声去向和全部重复位置。不得用成功fixture、界面启动或旧回执覆盖本次partial结论。

修复后的 `交付/新版/验收记录/real-small-02/acceptance.json` 为 `passed_development_chain`。沿用已保存的 `legacy-default / deepseek-flash`，实际43次请求、1,152,762 Token，费用unknown，模型执行success。样本有21个逐处位置、17个断言；逐处15核实、0违背、6弃权，其中20个核心位置为15核实和5个合法缺证。内核与真实原生缓存重放均passed，逐字段数字、A/B绑定、报告、工具与执行轨迹一致，重放外连为0，原配置/凭据未改变。

该新收据仍为 `partial=true`：全文账本保留未建模金融pending。它证明本版小样本技术链和具体取数/绑定修复，不证明全文金融验收通过；21原文位置、17断言、20核心位置以及15核实不能混用分母。旧small01失败和原锁继续只读，后续全量与独立专业质量须另登记。

工作台浏览器启动、无JavaScript错误以及设置页协议选择/持久化已取得局部技术证据，截图在 `交付/新版/验收记录/web-smoke/workbench.png`；它不替代追问、补证、消息重放及真实供应商质量验收。原生Loop的三工具依赖往返、离线缓存重放和跨进程恢复分别登记技术范围，只有同版原始收据可核对后才关闭相应技术项。

### D. 共同关闭规则

每条保留原失败或限制，不覆盖历史锁。登记修复源码哈希、输入、实际期望来源、测试/API/重放原回执及限制。若只完成部分链，明确残余方法、材料、操作数和权限前提。已有生命周期、货币/EPS、表格负号、图轴几何、A/B数值屏蔽、补证继承、配置冻结、凭据脱敏和长路径反例继续回归，不将测试数量视为专业签认。
"""
    manual = MANUAL + tail
    write(DOCS / "明镜_计划书打磨与开发任务手册.md", manual)


README = r'''# 明镜 MingJing 1.0.0.dev3

明镜面向研究报告事实核查、财报分析和材料追问。首页上传研报/财报并存入本地知识库，在线任务复用锁定的 DeepSeek Harness 原生 Agent Loop，通过受控金融工具调用统一的 MetricSpec、规则和 Decimal 内核。模型负责语义、检索与报告组织；金额事实、计算、判定和替换数字由宿主产生。

当前工程根为 `mainProj/新版/mingjing`，配套计划书与任务手册在 `mainProj/新版/文档`，交付产物写入 `交付/新版`。这是 **1.0.0.dev3 阶段开发版**；实际功能、质量与发布结论以本版收据为准，旧版测试和历史 EXE 不替代本版验收。

## 使用与任务

```powershell
Set-Location -LiteralPath D:\Competition\金融人工智能竞赛\mainProj\新版\mingjing
python -m pip install -r requirements-build.lock
python -m mingjing.launcher
```

工作台上传材料后确认识别的公司、报告期间、研报日期及参考文件，选择标准或审慎策略。在线模式进入原生模型—工具—观察循环，绑定与核查轨迹可在任务/会话中查看。知识库会话支持财报分析、追问、补证和重放；追加材料、口径澄清及局部重解析建立新版本，父结果只读。无模型或运行时不可用时明确采用纯内核演示/降级，保留缺证与未处理项。

标准模式在事先登记的定义和假设下核查，并逐条显示默认合并、备选定义、横线及其他条件；审慎模式拒绝没有来源证明的口径与方法，不能凭相近金额补造定义。直接披露、授权推算、内部勾稽、恒等式和文本佐证分别展示；预测与估值一致不表示真实历史事实已独立核实。

模型可按用户路由配置 DeepSeek、Kimi、OpenAI GPT 或兼容协议。连接测试和 Agent 多工具能力测试分开，供应商真实请求与 mock 分别登记。密钥使用当前 Windows 用户 DPAPI，配置导出不含明文；已有用户模型、凭据和角色路由不得自动改换。

## 结果、复现与交付

输出结构化结果、证据页、操作数与公式、错误/弃权/提示和修改文本。全文账本保留数字原位置、范围外、排除和待处理；覆盖率与执行进度不是独立真人召回或正确率。正式数字必须有样本、分母、版本及原始收据。

内核重放重算金融产物，Agent/消息重放用匹配缓存重跑实际循环。先验输入、输出、源码和资源锁，缓存缺失或变化明确失败，不联网补齐。知识库原件也必须重新验哈希，未来或未知公告日期材料不能进入事实、佐证、引用及补证。

完整开发包与核心审阅包不同：完整原生运行需锁定上游源码和金融载体闭包；≤30MB核心包供源码审阅，排除完整载体、真实语料、用户配置和历史。普通用户的单 EXE 在同版源码、UI、资源稳定后构建，按实际 build/smoke 与独立环境记录验收。

- [使用手册](docs/USER_GUIDE.md)
- [源码运行与验证](docs/RUNBOOK.md)
- [统一入口与知识库设计](docs/SMART_PIPELINE.md)
- [五工作包与关闭账本](docs/PROJECT_TASK_MANUAL.md)
- [交付及复现闭包](docs/DELIVERY_GUIDE.md)
- [EXE构建与发行](docs/EXE_DELIVERY.md)
- [许可](LICENSE)、[第三方范围](docs/THIRD_PARTY_NOTICES.md)

明镜自有代码使用 AGPL-3.0-only，第三方组件保留各自许可。公开仓库不提交 Token、DPAPI、个人配置、真实模型缓存、全量语料或未获授权材料。完整源码应与现场展示版一致。
'''

GUIDE = r'''# 明镜 1.0.0.dev3 使用手册

明镜用于研报历史金融事实核查、财报分析和同一材料追问。以下说明本版统一产品流程；可执行能力与最终验收以随包收据为准。源码启动 `python -m mingjing.launcher`，发行程序启动 `MingJing.exe`。单 EXE 无外部 Python/Node 的能力须由对应文件哈希的冷启动实测证明。

## 1. 上传与知识库

在首页上传一份或一批 PDF、DOCX、Markdown 或 TXT，查看识别的文档类型、公司/证券代码、期间和公告日期。文件按 SHA-256 去重进入本地知识库；识别不准时先修正。只上传研报可选择同公司已入库披露，开始前确认参考材料和研报截止日。未来及公告日期未知材料不能充当当时已知依据。

选择研报核查、财报分析或自动识别，再选择标准/审慎策略和执行模式。在线任务使用已保存的供应商与角色路由，在原生 Harness 会话执行金融工具；没有模型时使用明确标注的纯内核模式。不要把离线演示、缓存重放或固定接口摘要当作实时原生调用。

知识库可查看原件、识别信息、来源哈希、引用记录和解析诊断。删除文件前确认它是否仍被冻结任务、会话或重放引用；清理不应破坏证据闭包。哈希链校验检查记录衔接及内容；尾部截断检查还须核对冻结的可信链头/条数。

## 2. 理解模式和结果

| 模式/依据 | 怎么理解 |
| --- | --- |
| 标准模式 | 在登记定义及列明假设下的条件核查；默认合并、备选定义、横线等逐条披露 |
| 审慎模式 | 来源/声明须证明口径和方法；无依据推定或仅备选定义相容仍未能核实 |
| 直接披露/授权推算 | 对回来源原格或按登记公式计算，仍需看期间、版本、单位和精度 |
| 内部勾稽/恒等式 | 检查研报表格、行情与公式是否相容，不能证明外部历史事实真实 |
| 原文佐证 | 在批准材料找到同值及语境，未经外部独立真值或公式核查 |
| 观点/范围外 | 目标价、评级或未授权范围单列，不强制判错 |

结果页的核实、有误、未能核实与不判定分别展示；预算/取消导致尚未执行项保持待处理。点开陈述查看原文位置、条件、候选定义、来源页、实际操作数和计算式。错误给应为值、证据、规则及可替换文本；E2/E3/E9需要期间、口径或版本证据，金额相近只显示疑似原因。

分级统计同时显示原文出现位置与去重断言。历史财务、开放定义、预测估值、模板、行情及外部数据分别计分母，覆盖不等于真人召回。财报分析展示各指标及报表恒等式、同比、季度、跨表和分部检查；无法判定项保持缺证，复核信号不表示投资或舞弊结论。

## 3. 会话追问、补证与恢复

从任务或知识库材料进入关联会话，追问指标依据、公式或准则；回答只引用本会话工具实际读取的证据和槽位。新增文件、补齐操作数、澄清口径或局部重解析会建立子任务，显示父版本与变化，原结果不覆盖。

设置中的新配置只影响后续任务，当前任务和追问使用冻结的资源、路由与预算。取消后未冻结任务按保存检查点恢复；恢复先检查材料、配置、内核、插件和运行时。完整服务的高级界面用于角色/预算及会话管理，地址 `/classic`；使用同一材料资源而非复制未知数字。

## 4. 模型与安全设置

配置 DeepSeek、Kimi/Moonshot、OpenAI 或自定义供应商的实际协议、地址、模型ID与密钥，再保存默认/角色路由。模型名称以账号可用目录为准，不能直接套别人的型号。普通连接检测只检查接口，Agent能力检测需要实际多步依赖工具往返；各供应商分别登记实际响应型号、用量和能力。

Windows使用当前用户DPAPI，密钥输入不回显、普通导出不含明文，跨用户/电脑重填Token。标准服务只监听本机地址并校验Host/Origin与访问Token；用户不要将端口暴露到公网。模型请求由批准transport处理TLS和重定向，错误、日志、缓存和导出均脱敏。跨供应商备用仅使用用户明确保存的路由。

## 5. 导出与离线重放

下载结构化结果、HTML/Markdown、修改CSV及任务包。分享完整复现时另提供清单所需且获授权的原件、规则、源码、插件/运行时及模型缓存；仅结果JSON不能完成原生重放。

内核重放重新计算数值、定义、公式、证据、假设及分析检查；Agent重放缓存驱动原生工具循环；消息重放关联父任务和引用。先验所有输入输出及代码资源锁，缺缓存或哈希变化应失败，不能联网补齐。工作台的轻量重算结果必须说明实际比较范围，不能代替完整冻结任务验收。

## 6. 运行与排障

首次使用先运行内置虚构演示，再查看证据和离线重放。没有文本层的扫描件需独立OCR/复核流程，不能假装已抽到事实。坐标后端不可用时证据可仅显示整页，要查看结果标识的后端及证据等级。

关闭浏览器不停止后台，退出使用页面按钮；换版先退出旧实例。用户目录默认 `%LOCALAPPDATA%\MingJing`，原件/缓存、结果与日志会占磁盘。换机先导出不含密钥的配置，核对材料路径，重新填写凭据。版本、依赖和功能失败查看同版收据，旧RC程序或历史日志不能证明当前源码。

源码操作见 [RUNBOOK.md](RUNBOOK.md)，分享与数据范围见 [DELIVERY_GUIDE.md](DELIVERY_GUIDE.md)，发行见 [EXE_DELIVERY.md](EXE_DELIVERY.md)。
'''

RUNBOOK = r'''# 明镜 1.0.0.dev3 源码运行与验证

唯一工程为 `mainProj/新版/mingjing`；文档在 `mainProj/新版/文档`，阶段交付在 `交付/新版`。原融合包、旧源码快照、失败和成功收据只读。公开导出后工程本身为仓库根，不通过PYTHONPATH拼接旧后端。

## 1. 环境与启动

```powershell
Set-Location -LiteralPath D:\Competition\金融人工智能竞赛\mainProj\新版\mingjing
python -m pip install -r requirements-build.lock
python -c "import mingjing; print(mingjing.__file__)"
python -m mingjing.cli rules check
python -m mingjing.launcher
```

依赖安装以已锁文件为准；应用使用Python 3.12，前端使用其package-lock，上游使用vendor/harness源码与pnpm锁。启动默认本机服务与上传首页。使用隔离MINGJING_HOME验证，不改变真实用户配置/DPAPI/模型。原生任务还需同版finance-carrier及manifest闭包，审阅包单独不提供完整原生运行。

## 2. 离线与在线入口

```powershell
python -m mingjing.cli smart run samples/smart/*.pdf --out runs/dev3-smart
python -m mingjing.cli check --draft samples/demo_draft_defined.md --corpus samples/corpus --report-date 2025-03-31 --out runs/dev3-kernel --mode kernel
python -m mingjing.cli replay --run runs/dev3-kernel
```

上传工作台的在线模式须经过原生Loop和统一核查工具。核查、分析与知识库会话使用冻结资源，追问/补证/澄清建新版本。纯内核或历史smart轻量重算分别标模式和实际比较范围。标准条件核查与审慎来源要求遵循任务手册，不能以同值命中扩大方法授权。

## 3. 实施及验证顺序

冻结输入/源码与问题→最小修复及完整阻断链→原披露正反例→受影响定向测试→实际HTTP/浏览器→必要真实供应商→同版稳定点全量。不能反复全文/打包消除skip或堆计数。

```powershell
python -m pytest tests/test_source_snapshot.py tests/test_core_review.py -q
python -m pytest tests/test_smart_pipeline.py tests/test_smart_web_ui.py -q
python -m pytest tests -q
```

记录实际运行器、依赖、全部pass/fail/error/skip/timeout及原因；浏览器、CSP、真实API和mock分别声明。源码适配、HTTP成功不等于原生多工具能力。DeepSeek/Kimi/GPT/自定义按实际保存路由分别验收，缺凭据not_executed，不能自动切换用户配置。

fitz/poppler/plumber按MINGJING_PDF_BACKEND强制对照：读取完整真实原格，目检原文及证据区域。未来/未知日期、同公司重名、哈希篡改、横线、单位、重述和金额巧合都有相邻反例。标准与审慎单独报告假设和误纳；生成样例结果不能证明真实召回。

## 4. 原生载体与前端

```powershell
python -m scripts.verify_harness_ui_sources
python -m scripts.plan_finance_runtime
python -m scripts.verify_staged_finance_runtime --help
```

同版载体已有时先验证，不例行重stage；修改插件和UI后更新相应来源/静态输入锁。必要前端构建使用npm ci/typecheck/test及项目build_frontend，实际源码到静态资源收据必须覆盖新增首页/会话代码。来源与重建见 [REBUILD_SOURCE.md](../scripts/REBUILD_SOURCE.md)。

## 5. 冻结、重放与交付

记录原件、识别元数据、后端、注册/规则、源码、配置、插件、运行时和UI锁。重放先验输入输出，核查全部数字/公式/证据/假设/检查，Agent和消息重放比实际缓存工具轨迹；缺缓存、旧版不兼容或篡改失败且零外连。

源码/资源/文档稳定后再生成审阅包和一次同版EXE候选，构建回执与smoke指向精确EXE哈希。完整原生运行/复现闭包与≤30MB审阅包分开，ZIP哈希放包外；正式v1.0需独立质量及环境闸门。见 [PROJECT_TASK_MANUAL.md](PROJECT_TASK_MANUAL.md)、[EXE_DELIVERY.md](EXE_DELIVERY.md)。
'''

EXE = r'''# 明镜 1.0.0.dev3 Windows候选与发行

本页规定同版本发行方法，不提前证明EXE或独立环境已通过。`1.0.0.dev3`是阶段候选编号；源码、UI、规则、插件、载体、文档稳定后构建一次，技术、金融及正式发布结论依据实际回执分开。

## 1. 用户入口与资源

目标普通入口为 `MingJing.exe`，启动本地服务与上传首页，知识库、原生会话、核查/分析、追问/补证、重放/导出共享宿主。内嵌必要Python、PDF/MCP/schema、规则、静态UI、虚构样例、许可和锁定金融载体；真实大语料、用户配置/DPAPI、真实模型缓存与历史不内嵌。单入口仍产生受控用户缓存和结果文件。

载体复用原生Harness Loop并自带Node；运行时不安装npm/pnpm。首次按哈希原子发布缓存，半成品或资源不符拒绝。模型供应商分别真实工具闭环，无凭据not_executed；原生/API故障明确降级，默认不静默向另一供应商发送材料。

## 2. 构建与候选验收

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_exe.ps1 -Mode onefile -Clean -NoInstall
python -m scripts.delivery_smoke --exe dist/MingJing.exe --out runs/dev3-exe-smoke
```

构建入口、参数以实际脚本为准；先验证来源/依赖/静态/载体/许可，登记完整输入锁，构建后再验输入未变。MingJing-build.json登记dev3、EXE SHA-256、字节数及资源哈希。实际启动、真实API、PDF证据、导出、重放均指向同一EXE，不沿用旧RC结果。

阶段smoke使用隔离profile、中文目录、受限PATH，验自动端口/单实例、离线演示、原生载体、PDF、配置/凭据、取消恢复、重放、导出和退出。开发机受限PATH不写成干净Windows。深TEMP/长路径须有实际验证，不能删除闭包文件制造未验轻量体积。

## 3. 正式闸门与布局

正式发布需独立真人金标与规则签发、真实实验及未曝光D3、供应商真实能力、同版完整回归，以及独立Windows实时/断网/故障三场演练。未执行或缺凭据如实记录，不因阶段收尾改成v1.0。

```text
交付/新版/
  文档/                    计划书PDF、任务手册、运行/许可说明
  验收记录/                本版测试、API、Word、构建、冷启动原回执
  MingJing.exe              稳定后构建的同版本候选
  MingJing-build.json
  SHA256SUMS.txt
  完整源码/                或精确源码归档/commit及哈希
  核心审阅/                ≤30MB源码审阅，非完整原生运行
  样例/                    可许可且现场可实时处理
```

清单只登记实际存在产物，未构建文件列待完成，ZIP哈希留包外。完整源码与现场版一致、保留第三方许可和运行说明；审阅包不能代替完整源码/载体。API密钥由用户自行配置，跨电脑不复制DPAPI。见 [DELIVERY_GUIDE.md](DELIVERY_GUIDE.md)。
'''

DELIVERY_GUIDE = r'''# 明镜 1.0.0.dev3 交付与复现指南

本版工程根为 `mainProj/新版/mingjing`，文档在 `mainProj/新版/文档`，输出在 `交付/新版`。原融合包和旧资源锁只读。交付清单以实际存在文件与收据为准，不提前登记未构建EXE或未执行测试为通过。

| 形态 | 包含内容 | 能力和范围 |
| --- | --- | --- |
| 初赛材料 | Word更新后PDF、MP4≤5分钟且≤500MB | 2026-10-18前提交；目标/方法/验收设计与实际演示 |
| 完整开发源码 | Python/内核、工具/Prompt/Skill/MCP、数据与日志、UI、规则/依赖、上游源码许可、脚本及说明 | 与现场版一致，原生运行还需同版finance-carrier闭包 |
| 完整历史复现 | 指定任务/父子/会话/缓存、获授权原件、原源码/规则/运行时闭包 | 精确验输入输出/源码锁，不改旧manifest伪造恢复 |
| ≤30MB核心审阅 | 核心源码、插件、UI、规则、小bench/样例、测试、文档及manifest | 非完整上游/Node/carrier/语料/runs，不独立运行全部原生功能 |
| EXE候选 | 同版程序、build lock、SHA-256、使用/许可、样例及实际smoke | 稳定后一次构建；正式发布另需质量和独立环境闸门 |

共享项目不复制LOCALAPPDATA配置或DPAPI密文；导出不含密钥的配置并重新填Token与路径。未获再分发许可材料不随包，现场样例提供清晰来源和范围。明镜自有AGPLv3与第三方许可分别保留，EXE对应完整源码要可获得。

GitHub协作目标为 https://github.com/M0onesy/Light_Mirror 。公开源码不包含个人配置、密钥、真实模型缓存、全量PDF、运行历史、二进制及开发依赖树。发布前检查远端和历史blob，保留现有提交，不盲目force-push；普通工作先创建分支和PR。实际推送/发布属于独立发布动作，文档准备不表示已完成远端发布。

核心包可用项目create_core_review生成，但需要核对其当前白名单、所有大小/SHA-256、源码/文件夹/ZIP一致与凭据扫描方法。正式完整代码不由核心包文件数代表。ZIP自身哈希放包外，避免自引用；旧ZIP哈希、测试计数不复制为本版回执。

决赛官方必须提交最终报告、可运行Web/CLI原型、同版完整源码/依赖/说明、现场实时样例并答辩；单EXE和30MB包是本项目交付设计。用户入口见 [USER_GUIDE.md](USER_GUIDE.md)，源码见 [RUNBOOK.md](RUNBOOK.md)，发行见 [EXE_DELIVERY.md](EXE_DELIVERY.md)。
'''

SMART = r'''# 明镜 1.0.0.dev3 上传工作台、知识库与原生会话

本页定义新首页与原生Agent/统一内核的衔接。实际可用接口、执行模式与验收以当前源码和本版收据为准；原融合版smart固定管线日志不能证明本版原生任务完成。

## 1. 统一任务流

上传→SHA-256去重→自动识别并确认→同公司截止日前材料→冻结资源与配置→Harness Loop→A/B独立绑定→共享dispatcher/MetricSpec/Decimal→结果与证据。工作台的文件和会话不是两份独立判断系统；会话使用同一KB资源与任务锁。无模型入口明确为纯内核，保留执行模式及降级原因。

知识库原件和解析/事实缓存分别验哈希，缓存包含后端、代码、识别元数据和定义锁。实际读取时不只信doc_id，材料变化拒绝沿用。未来或日期未知材料不能通过文本佐证/引用旁路；公司/期间修正建立新任务，原结果只读。

## 2. 模式和依据

标准模式在有限登记定义与公开假设下条件核查；审慎模式需要来源口径/方法，拒绝未证合并、仅备选命中及横线推零。上下文继承须有原文边界；负基数和亏损收窄采用独立授权定义。金额相容不能证明期间/口径/版本，疑因与E2/E3/E9正式分类分开。

直接披露、授权推算、内部勾稽、恒等式和文本佐证分别展示，预测/估值只验内部计算。每层列原文出现、去重断言、已判、弃权与待处理；覆盖和模型成功不自证金融准确率。真实质量由独立金标验收。

## 3. 会话与重放

核查和分析任务可追问实际工具读到的事实、公式、准则和来源；reporter只用冻结槽位。追加材料、澄清口径和局部补证产生子版本，父产物保持原字节。取消/恢复沿用原累计预算和资源锁。

内核、Agent及消息重放先验原件、输出、缓存、源码、规则与运行时；比较状态、数值、定义、公式、操作数、证据、假设及分析检查，原生缓存重跑实际工具轨迹。缺缓存或输入不匹配直接失败、零外连。旧smart仅若干字段重算应标实际范围，不称完整冻结重放。

## 4. 安全和供应商

完整与轻量HTTP服务统一监听/Host/Origin/token/长度准入，配置与供应商走受控transport/TLS/redirect。DPAPI仅当前Windows用户，错误和导出脱敏，取消和身份守卫先于凭据读取。模型不提供自由数值/公式/判定工具；不同供应商分别真实多工具验收，mock只验协议。

原轻量服务命令 `python -m mingjing.smart.web` 供确定性功能开发，是否具备原生会话和完整配置权限必须以当前实现声明，不替代主launcher。本版主要用户入口、路径与指南见 [USER_GUIDE.md](USER_GUIDE.md) 和 [RUNBOOK.md](RUNBOOK.md)。
'''

DELIVERY_NOTE = r'''# 明镜 MingJing 1.0.0.dev3 新版交付说明

新版工程位于 `mainProj/新版/mingjing`，需求文档在 `mainProj/新版/文档`，阶段输出在 `交付/新版`。原 `明镜MingJing_交付包_终版` 保持只读，它的源码、测试、API与ZIP收据只证明原版本和实际范围，不继承为dev3成绩。

## 产物与版本

| 位置 | 内容与边界 |
| --- | --- |
| 新版工程 | 上传工作台、知识库、原生Loop/会话与统一金融内核的本版源码；原生运行需要同版载体闭包 |
| 文档/明镜MingJing_项目计划书.docx、_初赛评审版.pdf | 保留14章、图表和引用；目标成果、实施方法及验收设计；DOCX经Word更新域/目录，PDF从该文件用实际登记的渲染器生成 |
| 文档/明镜_计划书打磨与开发任务手册.md | 五工作包、独立复核、实施次序、问题根因与关闭回执；人名待真人确认 |
| 交付/新版/验收记录 | 本版实际回归/API/Word/构建/冷启动等原始收据；未执行项不写通过 |
| 完整源码与核心审阅 | 完整现场同版源码/资源闭包与≤30MB审阅包分开，后者非独立原生运行 |
| EXE | 源码/UI/规则/运行时稳定后构建一次dev3候选，再指向其哈希验收；未构建不能称已交付 |

## 运行与产品

```powershell
Set-Location -LiteralPath D:\Competition\金融人工智能竞赛\mainProj\新版\mingjing
python -m pip install -r requirements-build.lock
python -m mingjing.launcher
```

首页上传材料、确认自动识别及参考文件，在线核查进入Harness真实模型—工具—观察循环，知识库会话支持分析、追问、补证和重放；无模型时明确纯内核演示/降级。标准模式按登记条件核查并逐条披露假设，审慎模式拒绝无来源口径/方法；直接事实、推算、内部勾稽和文本佐证分层展示。

完整使用、源码与发行见工程docs/USER_GUIDE、RUNBOOK、DELIVERY_GUIDE、EXE_DELIVERY。供应商按保存路由分别验真实能力，mock和旧DeepSeek历史不代替本版；API密钥不随包，Windows用当前用户DPAPI。

## 初赛和正式交付

报名截止2026-10-11，作品提交截止2026-10-18；通知未写具体截止小时。初赛只提交计划书PDF和MP4≤5分钟、≤500MB，团队信息由真人确认。11月中旬名单/11月底决赛为预计，具体以新通知为准。

决赛提交最终报告、可运行Web/CLI原型、现场同版完整源码/依赖/说明、可实时处理样例并展示答辩。单EXE、GitHub、30MB包及CRRV质量目标是项目自选路线；本版按实际闭环、金融独立真值及发行闸门分别验收，阶段版本不提前标正式v1.0。

正式质量数字来自独立真人金标、同版实际实验和原始收据，明确分母与置信区间；程序覆盖、生成样例、重复位置、内部相加和0违背输出不能自证准确率/召回。计划书中的质量门槛为待验证目标，当前问题留在内部手册。

## 本版文档渲染

DOCX已在本机Word更新域、目录与分页并保存，Word原生分页为70页。原生PDF导出、SaveAs2与Microsoft Print to PDF均停滞，三次失败收据保留在验收记录。初赛PDF改由Playwright Chromium读取已刷新的新版DOCX生成，实际49页；完整转换1809段落、54张表与28处图像，保留14章、参考文献、附录及可点击目录。PDF目录不沿用Word页码，页脚按实际PDF页数生成；逐页概览与关键可读页的核验记录见 `验收记录/docs-visual.json`，渲染器及哈希见 `pdf-render.json`。团队名称、成员与院校仍待真人填写。
'''


def update_project_docs() -> None:
    for filename, text in {
        "README.md": README, "PROJECT_README.md": README,
        "docs/README.md": README.replace("docs/", "").replace("](LICENSE)", "](../LICENSE)"),
        "docs/USER_GUIDE.md": GUIDE, "docs/RUNBOOK.md": RUNBOOK,
        "docs/EXE_DELIVERY.md": EXE, "docs/DELIVERY_GUIDE.md": DELIVERY_GUIDE,
        "docs/SMART_PIPELINE.md": SMART,
    }.items():
        write(PROJECT / filename, text)
    # Preserve detailed technical/historical evidence; update only current root
    # and version wording in architecture guidance, not its recorded outcomes.
    for filename in ["docs/NATIVE_AGENT_ACCEPTANCE.md", "docs/NATIVE_AGENT_FINANCE.md"]:
        p = PROJECT / filename
        text = p.read_text("utf-8")
        text = text.replace("当前应用为 `1.0.0.dev2` 源码开发版", "当前应用为 `1.0.0.dev3` 源码开发版；下述原生历史观察保留各自原版本")
        if filename.endswith("NATIVE_AGENT_FINANCE.md"):
            text = text.replace("财务首版支持", "历史财务首版观察支持")
            text = text.replace("规则包 30 卡、60 口径条目有", "历史规则包 30 卡、60 口径条目有")
        intro = "本版统一入口与实施契约见[SMART_PIPELINE.md](SMART_PIPELINE.md)及[PROJECT_TASK_MANUAL.md](PROJECT_TASK_MANUAL.md)。以下保留原生架构与验收标准，历史运行路径/数字不改写为1.0.0.dev3成绩。\n\n"
        if intro not in text:
            lines = text.splitlines(keepends=True)
            text = lines[0] + "\n" + intro + "".join(lines[1:])
        write(p, text)
    current_guidance = {
        "docs/DEVELOPMENT.md": [
            ("# 明镜下一版功能开发", "# 明镜 1.0.0.dev3 功能开发"),
            ("当前版本为 `1.0.0.dev2`，开发目录 `mainProj/mingjing-next`。`mainProj/mingjing` 及已有 RC1 发行文件保留为历史基线。本开发版本尚未正式发行。",
             "当前版本为 `1.0.0.dev3`，开发目录 `mainProj/新版/mingjing`。原融合交付包及已有 RC1 发行文件保留为历史基线。本开发版本尚未正式发行；当前产品与关闭标准以 [PROJECT_TASK_MANUAL.md](PROJECT_TASK_MANUAL.md)、[SMART_PIPELINE.md](SMART_PIPELINE.md) 和 [RUNBOOK.md](RUNBOOK.md) 为准。"),
        ],
        "docs/DEVELOPMENT_DEV3.md": [
            ("# 当前开发与验收记录", "# 明镜 1.0.0.dev3 开发与历史验收边界"),
            ("工作根为 `mainProj/mingjing-next`。本轮按项目计划书和开发任务手册修复功能与正确性，不发布正式 1.0，也不例行制作 EXE 或 ZIP。",
             "本版工作根为 `mainProj/新版/mingjing`，统一版本为 `1.0.0.dev3`。当前工作与关闭标准见 [PROJECT_TASK_MANUAL.md](PROJECT_TASK_MANUAL.md)，本版原始收据登记在 `交付/新版/验收记录`。\n\n下文保留原 `mainProj/mingjing-next` 工程截至版本13的开发记录、原路径和结果，仅证明对应原锁与版本。本版不继承这些测试/API/运行时成绩，也不改写历史锁；当前实现、回归与发行状态须另取本版收据。"),
            ("## 当前统一版本13", "## 原工程统一版本13（历史记录）"),
        ],
        "docs/SOURCE_AGENT_ACCEPTANCE.md": [
            ("在 `mainProj/mingjing-next` 使用模块入口运行。脚本只复验已曝光的开发材料，沿用当前保存的模型与密钥，不修改用户配置、不打包 EXE，也不替代独立真人金标、D3 或干净 Windows 验收。",
             "在 `mainProj/新版/mingjing` 使用模块入口运行，统一版本为 `1.0.0.dev3`。脚本只复验已曝光的开发材料，沿用当前保存的模型与密钥，不修改用户配置，也不替代独立真人金标、D3 或干净 Windows 验收。真实供应商结果、预算和冻结源码须在本版新收据登记；原工程运行记录不算本版已执行。"),
        ],
    }
    for filename, replacements in current_guidance.items():
        p = PROJECT / filename
        text = p.read_text("utf-8")
        for old, new in replacements:
            text = text.replace(old, new)
        write(p, text)
    historical = PROJECT / "docs/DEVELOPMENT_ACCEPTANCE_DEV2.md"
    text = historical.read_text("utf-8")
    intro = "本页为原工程历史记录，保留原路径、版本、数字与运行锁；不能作为 `mainProj/新版/mingjing` 的 `1.0.0.dev3` 验收成绩。当前工作与关闭标准见 [PROJECT_TASK_MANUAL.md](PROJECT_TASK_MANUAL.md)。\n\n"
    if intro not in text:
        first, rest = text.split("\n", 1)
        write(historical, first + "\n\n" + intro + rest.lstrip("\n"))
    write(DOCS / "交付说明.md", DELIVERY_NOTE)


def main() -> None:
    global DOCS, ORIGINAL, DELIVERY
    parser = argparse.ArgumentParser(description="Reproduce the historical dev3 document revision into a new draft folder; never overwrite current project documents.")
    parser.add_argument("--original", type=Path, required=True, help="Read-only historical bundle containing the original proposal and manual")
    parser.add_argument("--out", type=Path, required=True, help="New draft directory under artifacts/documents")
    args = parser.parse_args()
    ORIGINAL = args.original.resolve(strict=True)
    DOCS = args.out.resolve()
    allowed = (PROJECT / "artifacts" / "documents").resolve()
    if DOCS == allowed or not DOCS.is_relative_to(allowed) or DOCS.exists():
        raise ValueError("Document revision requires a new child directory of artifacts/documents")
    DOCS.mkdir(parents=True)
    DELIVERY = DOCS
    plan = update_plan()
    update_manual()
    write(DOCS / "交付说明.md", DELIVERY_NOTE)
    receipt = {"schema": "mingjing-docs-revision.v1", "version": VERSION,
               "created_at": datetime.now(timezone.utc).isoformat(), "status": "written_word_refresh_pending",
               "original_bundle_modified": False, "plan": plan,
               "files": [{"path": str(p), "sha256": sha(p), "bytes": p.stat().st_size}
                         for p in [DOCS / "明镜MingJing_项目计划书.docx", DOCS / "明镜_计划书打磨与开发任务手册.md", DOCS / "交付说明.md"]],
               "member_names": "awaiting_real_team", "official_acceptance": "not_claimed"}
    write(DELIVERY / "验收记录" / "docs-revision.json", json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps({"version": VERSION, "plan": plan, "manual_audit_items": len(AUDIT_ITEMS),
                      "receipt": str(DELIVERY / "验收记录" / "docs-revision.json")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
