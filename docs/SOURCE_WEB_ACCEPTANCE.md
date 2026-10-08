# 源码 Web 真实 HTTP 验收

`scripts/source_web_acceptance.py` 启动真实 `mingjing.launcher` 子进程，访问实际本地 HTTP 服务。它只新增验收工具、测试和记录，不修改生产代码、不构建 EXE，也不读取已有用户配置与密钥。

```powershell
python -X utf8 -m scripts.source_web_acceptance --out runs/source-web-new-01 --timeout 90
```

`--out` 必须是新目录，不能位于已有 MingJing 用户数据根内。工具创建隔离 `profile`，清除子进程继承的 MingJing/Harness/API 环境配置，关闭浏览器自动打开。模型、供应商与 Harness 默认关闭。Bearer 测试使用新生成的临时令牌；HTTP 记录只保存请求哈希，不保存令牌或请求凭据。Windows 下实际验证 DPAPI 加密与重启后可用。

验收覆盖：真实端口冲突恢复；Harness HTML 与全部静态文件的 HTTP 内容哈希；前端源码构建收据；Host、Origin 与 Bearer 拒绝；配置 revision 冲突、验证、导入和脱敏导出；演示材料路径及原文预览；纯内核任务、三态结果、活动事件和运行历史；PDF 页级证据与 PNG；冻结产物 ZIP 的字节一致；断开模型的内核重放；真实活动任务取消；正常关闭、重启后的配置和历史恢复。首尾检查应用源码与前端构建锁，源码变化使本次验收失败。

内置可再分发虚构材料增加一条缺材陈述，使 11 条测试陈述覆盖完整三态。此处的数量是功能测试，不是实际研报金融质量成绩。每次生成 `acceptance.json`、`http.jsonl`、每个实际 HTTP 响应原字节及启动日志；失败记录也保留，重复运行须使用新目录。

可明确指定小型旧冻结任务：

```powershell
python -X utf8 -m scripts.source_web_acceptance --out runs/source-web-with-frozen-01 `
  --existing-run runs/source-real-dev3-03 --timeout 90
```

工具先验证根内常规文件、任务 ID 和体积上限，将其复制到隔离运行历史，不覆盖原任务、不调整旧锁。只执行历史读取、结果查看、导出与 kernel replay。它不会重新调用供应商，不会将旧模型成功字段当作本次原生 Agent 执行。旧代码锁不兼容时如实失败。D3 路径及凭据存储目录禁止进入副本。

通过表示“本源码版本的实际本地 HTTP 功能闭环通过”。浏览器交互、真实研报金标、Kimi/GPT 云端接入、人工专业验收、干净 Windows 单 EXE 验收仍分别登记；此脚本始终记录 `final_project_acceptance: false`，不代替这些最终闸门。

## 已保存供应商的原生 Web 闭环

另一个工具实际经过`/api/run`、Web任务生命周期、受控凭据桥和原生Harness Agent Loop，使用用户当前已保存的供应商与模型，不覆盖其配置或密钥。

```powershell
python -X utf8 -m scripts.source_native_web_acceptance `
  --source-run runs/source-real-stable-03 `
  --out runs/source-native-web-new-01 --timeout 300
```

该工具从明确已曝光的源任务选择原始研报PDF第1页，不改写其句子、表格或数值。单页PDF是原页的派生材料，登记原文档与派生文件哈希；这只验证真实Web/API、独立A/B、确定性判定、证据和HTTP双层重放，不衡量全文召回或独立金标。

工具创建隔离配置快照，原DPAPI密钥存储只通过宿主只读解析器访问，不复制密钥或凭据文件。HTTP记录保存请求哈希；产物扫描不得含明文Token。配置与密钥首尾字节哈希、模型真实调用、工具依赖、原始页证据、配置冻结、历史、导出以及零联网重放均进入新`acceptance.json`。失败和原任务不改写。无可用已保存凭据时应明确失败，不能改为mock后宣布真实接入通过。

正式开发命令统一使用 `python -X utf8 -m scripts.<工具名>`。真实 Agent、材料追问与原生 Web 脚本也支持直接脚本启动，并在读取配置前断言应用模块均来自当前工程；不会混用 `mainProj/mingjing` 历史后端。原生 Web 的非法超时在创建输出、读取凭据前拒绝；合法调用的材料读取、配置、凭据或服务准备失败仍保留脱敏失败收据。
