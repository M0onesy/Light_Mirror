# 明镜 1.0.0.dev3 打包规范与封存边界

本规范描述本版包装与核对步骤。最终阶段候选源码、EXE和视频均已有实物，实际名称、字节数、SHA-256与限定验收范围见[交付导航](新版交付导航_dev3.md)。唯一一次完整回归已经执行，原结果为3,403通过、7失败、3跳过，后续定向关闭不改写原成绩。原融合交付包只读，旧成绩和旧ZIP哈希不转登记到本版。

全文实际API执行、未完成范围和原始重放失败见[阶段验收摘要](阶段验收摘要_dev3.md)。正式金融质量未达标；后续候选的技术烟测、视频与包装核对各自登记限定范围。

候选包内文档收据保留封装时点原字节，外部最终摘要、附录和导航随后追加，并归档前一收据形成历史链。阅读封存计划书须配套阅读[最终问题附录](最终问题附录_dev3.md)MJ-P69；外部澄清不修改四核心文档，不重建EXE或重封候选ZIP。

## 四类产物

| 产物 | 包含内容 | 可证明范围 |
| --- | --- | --- |
| 初赛评审材料 | 新版计划书PDF；实际操作MP4，时长≤5分钟、大小≤500MB | 方案与真实演示；报名截止2026-10-11，作品截止2026-10-18，通知未给具体小时 |
| Windows阶段候选 | 最终同版MingJing.exe、构建收据、源码输入锁、使用/许可说明、可分发虚构样例、同EXE验收摘要 | 同EXE在开发宿主的实际运行范围；独立Windows、真人金标及未曝光D3另记 |
| 完整源码包 | Python/内核/工具/Prompt/Skill/MCP、UI源码与静态、规则、测试/bench、虚构样例、需求和运行说明、锁定vendor、完整staged生产载体及其生产node_modules、许可 | 现场版本对应的完整开发与运行资源；专业效果看独立收据 |
| ≤30MB核心审阅包 | 核心源码、金融插件、UI、规则、小样例/bench、测试、说明和审阅manifest | 供核心审阅；完整上游、内嵌Node/载体和语料不在其范围，不能独立完成全部原生运行 |

历史任务复现是独立材料集合：需要当时的源码、规则、载体、原件授权、父子/会话/缓存与原锁。完整源码包不携带用户运行历史，不能用当前规则重写旧manifest制造精确历史恢复。

## 冻结与包内边界

完整源码由工程自身的 `scripts/create_full_source.py` 生成到 `交付/新版/源码`，该脚本本身也进入源码白名单；文档目录 `完整源码打包_dev3.py` 保留兼容入口。脚本必须取得主责人的JSON冻结收据并显式传 `--frozen`；未冻结不复制、不压缩。`--expected-source-digest` 可绑定此前完整白名单清单，打包期间源码再变即失败，保留未验候选供核对。

白名单目录为 `bench、docs、frontend、kernel、legacy_tools、mingjing、requirements、rulepack、runtime、samples、scripts、tests、vendor、web`；根文件按脚本枚举。`runtime/finance-carrier/staged` 全部保留，逐项对照 `carrier_manifest.json` 的路径、字节数和哈希，生产node_modules不得因减小体积被删除。合法源文件命名为cache、profiles、build或dist时，按src/tests/fixtures等源命名空间保留；普通用户状态目录排除。

排除 `.git、.pytest_cache、__pycache__、.aud_finance、.audit_finance、.tmp_sample_render` 等工具/验收缓存，普通runs/profile/logs/cache/build/dist、开发node_modules和虚拟环境、个人 `.env`、用户配置/DPAPI密文、真实模型响应缓存、历史任务及未获许可原件。未知根路径、符号链接或junction必须明确处理，不能静默漏包。审计缓存收据如需保留，主责人先移到验收记录的独立新目录，历史目标不覆盖。

源码清单记录每个文件的相对路径、字节数和SHA-256。复制后复验每份文件，再复核源目录没有改变；ZIP逐成员验哈希，ZIP自己的哈希保存在包外 `.zip.sha256`，避免自引用。完整包内 `FULL_SOURCE_MANIFEST.json` 与包外 `.receipt.json` 描述包装范围，不把文件数、大小或CRC写成运行/金融验收。

完整源码在复制前扫描全部included文件的已保存凭据精确值与private key块，并对DOCX解压后的XML/文字继续扫描；源码ZIP逐成员复验并重扫，个人配置不入包，placeholder/mock Token不因字段名称被当作真实密钥。回执只记扫描方法、数量与结果，不记录凭据值；读取保存DPAPI只读，旧格式必须先在UI迁移，不能在打包时迁移用户文件。

完整源码的 `source_inventory_digest` 与EXE构建的 `source_sha256` 采用不同集合：前者包括vendor和整个生产载体，后者绑定构建输入锁。两者应分别保留并核对共有文件，不要求数值相同。复制文档、重新生成静态或调整任何构建输入后，必须重新冻结并重建，不能手改旧锁的哈希。

## 最终EXE验收与视频

最终脚本为工程 `scripts/dev3_delivery_smoke.py`，使用当前上传首页；原 `delivery_smoke` 的生命周期函数继续复用，旧 `native_exe_smoke` 的整份44页路径不用于本版最终烟测。

```powershell
Set-Location -LiteralPath D:\Competition\金融人工智能竞赛\mainProj\新版\mingjing
python scripts/dev3_delivery_smoke.py --exe dist/MingJing.exe --out D:\Competition\金融人工智能竞赛\交付\新版\验收记录\dev3-exe-final
```

输出目录必须是新目录。脚本逐项验EXE哈希与最终build receipt、源码输入锁及全部构建输入；读取保存配置只读、将凭据通过SecretStore克隆成当前用户DPAPI隔离profile，保留供应商/路由/预算，绝不回显Token。

实际范围为：冷启动端口冲突、重复启动复用、当前首页上传虚构财报/研报，纯内核三态、全部逐处位置、核查报告、引用证据PNG、冻结ZIP导出与数字重放；非法scope/类型、目录穿越与越权路径拒绝；保存供应商的真实test-agent依赖工具闭环；五处小陈述（四断言）的smart原生任务；一次受控材料追问、消息缓存重放及Agent缓存重放；正常退出、重启、配置/历史留存与明文密钥扫描。DPAPI secrets文件不参加明文扫描，原用户profile前后哈希必须相同。

成功状态仅为 `passed_scoped_dev3_candidate`。开发宿主受限PATH不等于独立无Python Windows虚拟机；`independent_windows_vm、human_gold、D3_time_holdout` 保持 `not_executed`。`--source --skip-real-model` 只可得到源码离线模拟状态；源码模拟不代替最终EXE与真实供应商。

候选门禁的`full_report_financial_acceptance`登记的是全文金融质量目标及独立认证，不能由字段名称推断全文API未执行。若存在`real-full-01/acceptance.json`，候选说明应列其实际技术运行、分母与partial状态，并另外声明金融质量目标是否达成、是否有独立专业认证；保留原质量门禁占位，不将已执行的真实全文调用写成未执行。

阶段程序组装使用 `scripts/package_dev3_candidate.py`，显式 `--out` 指向 `交付/新版/程序/MingJing-1.0.0.dev3-candidate`，同目录产生对应ZIP和包外哈希/receipt。必须绑定matching build、dev3 smoke与源码输入锁，并逐项对照完整源码ZIP；没有formal-release选项，原package_delivery的正式发布门禁保持原样。源码/核心审阅/文档/演示/验收各归其目录，交付根不散放JSON/ZIP。

真实响应/缓存、DPAPI与私有原生会话位于系统临时目录，默认退出后清理；交付验收输出仅保留摘要、截图和虚构纯内核导出。`--keep-open` 供主责人录制视频，保留私有profile和服务URL，并明确禁止打入交付包；录制结束调用 `/api/shutdown` 后由主责人清理该次私有目录。`record_demo_dev3.py` 录真实页面操作，视频模式和输入范围以自己的实际receipt为准。

## 封存顺序

1. 冻结业务源码、规则、静态UI、carrier和可分发样例；工程docs/requirements及Word/PDF维持已审核封存字节，后续运行结果追加到交付外部摘要。保留唯一完整回归原成绩，按授权对失败作必要定向修复并记录新冻结点；不把定向通过改写成第二次全量通过。
2. 生成构建输入锁；构建dev3候选，登记EXE/载体/依赖和输入哈希，验构建期间源文件未变。
3. 对同EXE运行新烟测；需要录制时用 `--keep-open`，结束关闭，保留原始通过与失败收据。
4. 封存视频，绑定实际哈希；EXE烟测、视频和包装结论写入交付导航及外部验收记录。构建后不改工程输入，不再次运行会回写工程requirements的文档封存脚本；已审核DOCX/PDF不重新修订或渲染。需要修改工程文档时重新冻结、回归并重建。
5. 完整源码白名单复制与ZIP核对，再生成核心审阅包；体积限制只施加到核心审阅和官方视频，不阉割完整生产载体。
6. 导航列实际产物和范围；提交/推送/公开发布由主责人另行执行，文件准备不声称这些外部动作已经完成。

官方决赛要求最终报告、可运行Web/CLI原型、现场同版完整源码/依赖/说明和实时样例/答辩。单EXE、GitHub与30MB核心包是项目自选形态；正式v1.0还需独立专业质量和环境闸门，本版编号保留 `1.0.0.dev3`。
