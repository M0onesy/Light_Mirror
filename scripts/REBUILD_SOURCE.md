# 从源码重建明镜阶段候选

普通用户双击 `MingJing.exe`，无需另装Python、Node或pnpm。本页供开发者在Windows x64重建当前 `1.0.0.dev2` 阶段候选；源码、规则、插件、UI和运行时必须属于同一锁定版本。

## 1. 源码与依赖

使用短目录，例如 `D:\MJ\Light_Mirror`。独立源码目录不依赖旧工程的包名或Git worktree；启动器拒绝混用模块来源。应用依赖使用Python 3.12和完整运行/构建锁：

```powershell
git clone https://github.com/M0onesy/Light_Mirror.git D:\MJ\Light_Mirror
Set-Location -LiteralPath D:\MJ\Light_Mirror
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.lock
.\.venv\Scripts\python.exe -m mingjing.cli rules check
```

锁记录metadata观察到的精确版本，不声称wheel哈希或字节完全相同的EXE。需要重建运行时规划时另装开发用PyYAML；它不进入终端用户内核依赖。

上游默认位置为 `vendor/harness`，与 `runtime/harness/source_snapshot.json` 逐文件核对。完整源码快照包含原始许可和锁；缺失上游commit保持null。不得混装公开旧SDK/运行时或用不同上游版本替代。显式 `--upstream` 也必须通过相同来源验证。

## 2. 选择已有载体或重建载体

若完整开发拷贝已经包含同版本 `runtime/finance-carrier/staged`，无需重复安装上游依赖或重新stage。检查前端和载体即可：

```powershell
.\.venv\Scripts\python.exe -m scripts.build_frontend --verify
.\.venv\Scripts\python.exe -m scripts.verify_staged_finance_runtime --out runs/rebuild-carrier-probe
```

后一个命令使用本机fixture并移除系统Node PATH，检查实际闭包；它不代表真实供应商或干净Windows验收。输出目录必须为新目录，不覆盖历史。

GitHub源码不包含node_modules和二进制载体。需要重建者使用Node 24 x64、pnpm 11.7.0及上游锁文件，按照锁定上游SDK路线生成编译后的Node carrier，再抽取金融闭包：

```powershell
Set-Location -LiteralPath vendor\harness
corepack prepare pnpm@11.7.0 --activate
pnpm install --frozen-lockfile
pnpm exec tsx scripts/build-exe-for-python-sdk.ts --targets=node24-win-x64
Set-Location -LiteralPath ..\..
.\.venv\Scripts\python.exe -m scripts.plan_finance_runtime
.\.venv\Scripts\python.exe -m scripts.stage_finance_runtime --node "C:\Program Files\nodejs\node.exe"
.\.venv\Scripts\python.exe -m scripts.verify_staged_finance_runtime --out runs/rebuild-carrier-probe
```

包含空格的Node路径要用引号，例如 `--node "C:\Program Files\nodejs\node.exe"`；也可省略 `--node` 使用PATH中实际Node。构建上游可能需要其README规定的Windows原生编译工具。官方SDK构建产生的完整Office/SEA产物只是上游构建中间资产，明镜最终嵌入已验证的金融闭包，不能简单删掉尚未验证的依赖减容。

`stage_finance_runtime`拒绝覆盖现有staged目录；需要重新生成时先保留旧版本及其manifest，在新受控目录stage并完成验证后再切换。已有载体只修改金融插件时可用 `--sync-plugin`，随后重做对应闭包测试和新锁，不能沿用旧收据。

## 3. 必要时构建前端

当前源码包括已构建静态网页和来源回执。未修改前端时只运行verify；修改后安装其锁定npm依赖并增量构建：

```powershell
Set-Location -LiteralPath frontend
npm ci
npm run test
Set-Location -LiteralPath ..
.\.venv\Scripts\python.exe -m scripts.build_frontend
```

`build_frontend`执行typecheck和build，并登记源代码到静态资源哈希。不得在EXE中混入与源码不匹配的旧页面。

## 4. 一次性构建阶段EXE

先确保相关测试、规则、来源锁、前端回执、载体manifest和许可完整，然后执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1 -Python .\.venv\Scripts\python.exe -Mode onefile -Clean -NoInstall
```

默认无控制台；`-Console`仅用于诊断。输出为 `dist/MingJing.exe`、`dist/MingJing-build.json` 和构建输入锁。构建期间任何输入变化会拒绝登记。必要资源包括PyMuPDF、官方MCP、schema、规则、静态UI、虚构样例及独立金融Harness载体；不包含真实语料和个人配置。

构建后针对准确EXE测试：

```powershell
.\.venv\Scripts\python.exe -m scripts.delivery_smoke --exe dist/MingJing.exe --out runs/stage-exe-smoke
Get-FileHash -LiteralPath dist\MingJing.exe -Algorithm SHA256
```

smoke使用隔离profile和受限PATH，检查启动、PDF、演示、配置、降级、历史及退出，不修改用户已保存DeepSeek配置。真实API、原生工具闭环及离线重放另外记录，不以fixture代替真实验收。

## 5. 对应源码与发布

EXE分发提供同版本完整对应源码、依赖锁及许可；详见 [LICENSE_GUIDE.md](../docs/LICENSE_GUIDE.md)。30MB核心审阅包用于审阅，不是完整对应源码。

阶段候选以版本和新构建哈希区分，不能提前打正式v1.0标签。独立真人金标、D3、Kimi/GPT真实API或干净Windows尚无记录时保持真实状态。交付与三种拷贝方式见 [DELIVERY_GUIDE.md](../docs/DELIVERY_GUIDE.md)。
