# 明镜 1.0.0.dev3 Windows候选与发行

本页规定同版本发行方法，不提前证明EXE或独立环境已通过。`1.0.0.dev3`是阶段候选编号；源码、UI、规则、插件、载体、文档稳定后构建一次，技术、金融及正式发布结论依据实际回执分开。

## 1. 用户入口与资源

目标普通入口为 `MingJing.exe`，启动本地服务与上传首页，知识库、原生会话、核查/分析、追问/补证、重放/导出共享宿主。内嵌必要Python、PDF/MCP/schema、规则、静态UI、虚构样例、许可和锁定金融载体；真实大语料、用户配置/DPAPI、真实模型缓存与历史不内嵌。单入口仍产生受控用户缓存和结果文件。

载体复用原生Harness Loop并自带Node；运行时不安装npm/pnpm。首次按哈希原子发布缓存，半成品或资源不符拒绝。模型供应商分别真实工具闭环，无凭据not_executed；原生/API故障明确降级，默认不静默向另一供应商发送材料。

纯预测输入和行情输入的确定性规则排除保留逐处账本，不计历史verified；具备独立操作数的预测内部勾稽仍运行原生工具。用户选择的口径、来源时点及双方完整精度区间仍约束判定，规则排除不授予历史核实。

## 2. 构建与候选验收

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_exe.ps1 -Mode onefile -Clean -NoInstall
python scripts/dev3_delivery_smoke.py --exe artifacts/program/MingJing.exe --out evidence/new-exe-smoke
```

构建入口、参数以实际脚本为准；先验证来源/依赖/静态/载体/许可，登记完整输入锁，构建后再验输入未变。MingJing-build.json登记dev3、EXE SHA-256、字节数及资源哈希。实际启动、真实API、PDF证据、导出、重放均指向同一EXE，不沿用旧RC结果。

一般工程docs/requirements在最终回归与构建前冻结，后续运行数字写入外部验收记录。build prepare生成的第三方通知按最终构建原件复制到交付文档；构建后文档封存仅使用delivery-only，不回写工程输入，旧封存收据按原字节归档。

阶段smoke使用当前上传首页、只读保存配置、DPAPI隔离profile与受限PATH，验端口冲突/单实例、虚构PDF纯内核三态与逐处结果、报告/证据PNG/冻结导出/replay、非法scope/路径拒绝，以及保存供应商的真实工具闭环、小量smart原生任务、一次材料追问和消息/Agent缓存重放，最后验正常退出、重启、原配置/凭据不变。成功仅登记passed_scoped_dev3_candidate；真实响应缓存不复制到交付。开发机受限PATH不写成干净Windows。深TEMP/长路径须有实际验证，不能删除闭包文件制造未验轻量体积。

源码模拟使用--source --skip-real-model，不能批准EXE；最终可用--keep-open保留服务供实际录制，私有profile不入包，录制结束关闭并清理。输出目录须全新，原通过/失败回执保留。旧native_exe_smoke的44页整份旧路径不作为本版最终烟测入口。

## 3. 正式闸门与布局

正式发布需独立真人金标与规则签发、真实实验及未曝光D3、供应商真实能力、同版完整回归，以及独立Windows实时/断网/故障三场演练。未执行或缺凭据如实记录，不因阶段收尾改成v1.0。

真实全文技术运行以`real-full-*`收据的请求、分母、pending及partial为准；候选门禁`full_report_financial_acceptance`表示全文金融质量目标/认证状态，不能据此声称全文API没有执行。小样本、虚构夹具、全文运行与独立金融真值分别登记；human_gold、D3、Kimi/GPT及独立Windows没有实际收据时保持未执行或未认证。

```text
Light_Mirror/
  artifacts/program/       当前EXE、原构建回执与输入锁
  artifacts/packages/      原候选ZIP、包外哈希和包装回执
  artifacts/source/        原冻结完整源码ZIP与哈希
  artifacts/core-review/   ≤30MB当前核心审阅ZIP，非完整原生运行
  artifacts/demo/          实际操作MP4
  docs/project/            唯一计划书、PDF和任务手册
  docs/acceptance/         当前验收说明与问题
  evidence/dev3/           本地本版原始收据及运行证据
  evidence/relocation/     目录迁移映射及校验
```

清单只登记实际存在产物，未构建文件列待完成，ZIP哈希留包外。完整源码与现场版一致、保留第三方许可和运行说明；审阅包不能代替完整源码/载体。API密钥由用户自行配置，跨电脑不复制DPAPI。见 [DELIVERY_GUIDE.md](DELIVERY_GUIDE.md)。

本次整理未重建EXE，也未改写原build/source-lock。当前公开源码在辅助输出与文档导航上有后续整理变化；原EXE对应的冻结源码从 `artifacts/source/` 或GitHub Release获取，不能用整理后的HEAD替换旧构建输入。GitHub Release为阶段候选且标为prerelease。
