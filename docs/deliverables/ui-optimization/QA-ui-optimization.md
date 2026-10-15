# 明镜双端 UI 重构 · 独立 QA 验证报告

> 验证人：严过关（QA 工程师）
> 性质：**独立验证**——不复述工程师"7/7 全绿、IS_PASS YES"的结论，用真实渲染 + `getComputedStyle` + WCAG 对比度计算 + Playwright 交互，独立证明"到底对不对"。
> 被测对象：`scope-clarifier/index.html`、`abstention-dashboard/`
> 依据：`ui-optimization/PRD-lite.md`（19 条）、`ui-optimization/DESIGN-ui-optimization.md`（§2/§4.2/§4.4/§5/§12）
> 时间：2026-10-15

---

## 0. 结论速览（TL;DR · 第 2 轮已闭环）

> **第 2 轮状态（2026-10-15，工程师修复后）**：3 个源码 Bug **全部修复并通过回归验证**；QA 三脚本 **38/38 PASS**，官方 `run-all.mjs` **7/7 ALL PASS**；额外反向回归确认修复未引入新问题（桌面 tab 单行不裁切、桌面 `.g-label` 不误截断）。**最终结论：全部通过。**

| 维度 | 结果 |
| --- | --- |
| 工程师自带回归 `node tests/run-all.mjs` | ✅ **7/7 ALL PASS**（第 1/2 轮各独立复跑，exit 0，日志 `qa-tools/qa-official-runall.log` / `qa-official-runall-round2.log`） |
| QA 独立验证（看板浅色/叙事红线） | 第 1 轮 10/12 → 第 2 轮 **12/12 PASS** |
| QA 独立验证（澄清工具） | 第 1 轮 17/18 → 第 2 轮 **18/18 PASS** |
| QA 独立验证（看板移动端+跳转动线） | 第 1 轮 7/8 → 第 2 轮 **8/8 PASS** |
| **独立发现真实缺陷** | 第 1 轮 **3 个源码 Bug**（工程师自带测试未覆盖）；第 2 轮**全部修复确认** |
| 测试代码自身缺陷 | 1 个（已自行修复：N9a 断言口径错误） |
| 路由判定 | 第 1 轮 → 工程师修 3 处；第 2 轮 → **全部通过（NoOne）** |

> **一句话**：浅色迁移的**核心三处隐形坑（段内白字 / to 深字 / Tag 中性底）+ 叙事红线 + 跨端契约 + R4 硬闸门**——全部经证据级验证**真实修复且未破坏**。工程师自称的"全绿"在第 1 轮**不完整**（漏掉 3 个真实缺陷，其中 2 个直接违反 PRD 明文标准 DASH-1③、DASH-5①③、CLR-5①）；经 QA 路由 → 工程师修复 → 第 2 轮复跑，**现已在证据级全部通过**。

---

## 0.5 第 2 轮回归（team-lead 指定加强项 · 独立复验）

> 触发：工程师 Alex 修复 3 个 Bug 后，team-lead 要求**独立复验**（不采信工程师自验）。本轮除重跑 3 个 QA 脚本外，额外执行 team-lead 指定的 6 项加强检查（脚本 `qa-tools/qa-verify-round2-deep.mjs`，可复跑）。

### 0.5.1 三个 QA 脚本重跑（FAIL → PASS 确认）

| 脚本 | 第 1 轮 | 第 2 轮 | 结果 |
| --- | --- | --- | --- |
| `qa-verify-dashboard.mjs` | 10/12（A2a/A2b FAIL） | **12/12** | ✅ 2 个 FAIL 转 PASS |
| `qa-verify-clarifier.mjs` | 17/18（N10-320 FAIL） | **18/18** | ✅ 1 个 FAIL 转 PASS |
| `qa-verify-dashboard-mobile.mjs` | 7/8（M1 FAIL） | **8/8** | ✅ 1 个 FAIL 转 PASS |
| **合计** | 34/38 | **38/38** | ✅ 全部转 PASS |

### 0.5.2 team-lead 指定加强项（6/6 PASS）

| # | 加强检查 | 结果 | 证据 |
| --- | --- | --- | --- |
| **D1** | Bug-2 深度：375px 溢出归零 + **最右 tab「需求模拟器」真的可点击切换**（不只是能滚） | ✅ PASS | `scrollW=375==clientW`；真实 `click()` 后 `aria-selected=true`、内容切到「需求池/不做清单」；再点回「构成分解」成功；console err=0 |
| **D2a** | Bug-3 深度：320px 无溢出 + console 零 error | ✅ PASS | `scrollW=320==clientW`，console err=0 |
| **D2b** | Bug-3 深度：**280px 极端宽度** | ✅ PASS | `scrollW=280==clientW`（**溢出窗口未右移**，280px 仍无溢出），console err=0 |
| **D3** | Bug-1 深度：**React 挂载前后 html 颜色一致（无深色闪屏中间态）** | ✅ PASS | 挂载前采样 `early=rgb(245,247,250)` == 挂载后 `late=rgb(245,247,250)`，全程浅色无中间态 |
| **D4** | 副作用：375px 下 **5 个视图**均无横向溢出 | ✅ PASS | 结论总览/构成分解/根因树/逐条下钻/需求模拟器 全部 `scrollW<=clientW` |
| **D5** | 副作用：桌面 1440px 下 **5 个视图**渲染正常 + 无溢出 + tab 单行不裁切 | ✅ PASS | 5 视图均匹配预期内容且无溢出；tab 单行 `rows=1`、最右 tab `right=1404<=1440`；console err=0 |

### 0.5.3 官方回归确认

- `node tests/run-all.mjs` → **7/7 ALL PASS（exit 0）**，留痕 `qa-tools/qa-official-runall-round2.log`。修复**未引入新问题**。

### 0.5.4 本轮结论

> **验收通过（ACCEPTED）。** 3 个 FAIL 全部转 PASS，6 项加强检查全绿，官方 7/7 全绿，无新增缺陷、无副作用。**无遗留 FAIL。** 判定：**NoOne（全部通过）**。

---

## 1. 验证方法与环境

- Playwright `playwright-core`（`C:/Users/xy/pwqa/node_modules/playwright-core`）+ `chrome-headless-shell-1228`。
- 看板：`tests/static-server.mjs` 起静态服务托管 `dist/`（真实生产构建产物）。
- 澄清工具：`file://` 直开（符合其单文件零依赖的部署形态），拦截全部 request 证明零外链。
- 对比度：WCAG 2.1 相对亮度公式 `(L1+0.05)/(L2+0.05)`，半透明前景按 alpha 叠加到实际底色后计算。
- **QA 自建测试脚本**（保留在 `qa-tools/`，可复跑）：
  - `qa-verify-dashboard.mjs` — 浅色 token + 三处隐形坑对比度 + 叙事红线（12 项）
  - `qa-verify-clarifier.mjs` — file:// 零网络 + 搜索/筛选/折叠/锚点 + R4 闸门 + URL 契约 + 响应式（18 项）
  - `qa-verify-dashboard-mobile.mjs` — 移动端溢出/sticky/横滚 + 跳转动线 href/target（8 项）
  - `qa-verify-round2-deep.mjs` — 第 2 轮加强项：最右 tab 可点击 / 280px 极端宽度 / 挂载前后无深色闪屏 / 5 视图副作用（6 项）
  - `qa-official-runall.log` / `qa-official-runall-round2.log` — 工程师自带 7/7 全量回归的两轮独立复跑留痕
  - `qa-shots/` — 验证截图（含 375/320/280px 响应式、深底残留、R4 闸门等）

---

## 2. 逐项验证清单与结果

### 2.1 看板全量回归（工程师自带）

| # | 检查 | 结果 | 证据 |
| --- | --- | --- | --- |
| R0 | `node tests/run-all.mjs` | ✅ PASS | **7/7 ALL PASS**，exit 0。D 组由基线 13 项 → **15 项**（新增 DASH-3/4 两项），均 PASS。日志：`qa-tools/qa-official-runall.log` |

> 复跑确认真实：`C1 tsc / C2 build-sim / A 数据契约 / B 模拟器 / C3 构建 / D 浏览器 / E 边界` 全部 PASS。

### 2.2 浅色迁移 · 证据级验证（不只看源码）

| # | 检查 | 结果 | 关键证据（计算样式 / 对比度） |
| --- | --- | --- | --- |
| A | 页面底为浅色（明度≥0.9） | ✅ PASS | `body` bg=`rgb(245,247,250)`=#F5F7FA，相对亮度 **L=0.928** ≥ 0.9 |
| **A2a** | 无旧深底残留（#0B0E13/#141821） | ✅ PASS（第2轮） | 第1轮 FAIL（`HTML`/`DIV#root`=`rgb(11,14,19)`），工程师修复后全站 **0 处旧深底** |
| **A2b** | `html`/`#root` 非 #0B0E13 | ✅ PASS（第2轮） | 修复后 `html=rgb(245,247,250)`、`root=rgb(245,247,250)`、`body=rgb(245,247,250)`（均浅色） |
| **B** | ① AbstentionBar 段内标签文字可见 | ✅ PASS | 「可改善 108」文字 **#FFFFFF / 色块 #0E7490 = 5.36:1**；「待用户声明 20」**#FFFFFF / #6D28D9 = 7.10:1**（均≥4.5:1） |
| **C** | ② MigrationChart `to` 数值可见 | ✅ PASS | to 值 `#1F2A37`(=tokens.ink) / 白底 = **14.54:1**；from 值 `#5C6B7A` / 白底 = 5.47:1（均≥4.5）。原 `#E5E7EB` 近白字已彻底消除 |
| **D** | ③ Tag neutral 底不再隐形 | ✅ PASS | `bg=rgba(100,116,139,0.1)`（冷灰 10%），叠白后 `#F0F1F3`（与纯白 diff=41）——**非** `bg-white/5` |
| I | 无 `bg-white/5` 隐形底残留 | ✅ PASS | 全 DOM 扫描 0 处 `rgba(255,255,255,0.05)` |
| E | 页脚「弃权是护城河」不再红色（DASH-6） | ✅ PASS | `footer p` color=`rgb(59,91,140)`=**#3B5B8C（accent 蓝）**，class=`text-accent`，非红 |
| F | 合法弃权（intentional）仍非红色（R1） | ✅ PASS | intentional 行 AmendmentBadge/Tag 文本色=`rgb(100,116,139)`（冷灰 #64748B），**非红** |
| G | 反指标=0 显示「合规」非「无数据」 | ✅ PASS | body 含「合规」，不含「无数据」 |
| H | 全站「降低弃权率」仅否定语境 | ✅ PASS | 全站扫描 1 处，上下文含「不追求」，否定语境成立 |

> **B/C/D 三项是本任务点名的"浅色下会隐形"三大坑**，均已用**对比度数值**证明修复到位（不是"看起来还行"）。截图 `qa-shots/dash-composition-light.png` 目视复核一致。

### 2.3 看板移动端 + 跳转动线

| # | 检查 | 结果 | 证据 |
| --- | --- | --- | --- |
| **M1** | **375px 结论总览无横向溢出（DASH-5①）** | ✅ PASS（第2轮） | 修复后 `scrollW=375 == clientW=375`（overflow=0）。第1轮为 FAIL（`scrollW=396 > 375`，越界 SegmentedControl）——见 §3 Bug-2 |
| M2 | 375px 下钻宽表 sticky 首列生效 | ✅ PASS | 表头/首列 `position=sticky`；横滚 300px 后首列 `left=17 ≈ 容器 left=17`（未被推出） |
| M3 | 375px SegmentedControl 可横滚不崩 | ✅ PASS | 5 个 tab 单行高 32px（未换行）；修复后整体不再溢出，tablist `scrollWidth=380 > clientWidth=343`，内部 `overflow-x-auto` 生效可横滚 |
| M4 | 构成分解 c02「→ 去澄清」href+target | ✅ PASS | 1 个链接，`href=../scope-clarifier/index.html?filter=X3&cause=scope`，`target=_blank` |
| M5 | 逐条下钻口径类行有链接、非口径行无 | ✅ PASS | 口径类（P0-3）**7 行**均有正确 href+target；非口径 **18 行**均无链接 |
| M6 | 顶栏/页脚澄清工具外链（DASH-7） | ✅ PASS | 2 个外链，`href=../scope-clarifier/index.html`，`target=_blank` |
| M0/M7 | 桌面 + 375px console 零 error | ✅ PASS | 无 error / pageerror |

### 2.4 澄清工具（file:// 直开）

| # | 检查 | 结果 | 证据 |
| --- | --- | --- | --- |
| N1 | 25 组全渲染 + console 零 error | ✅ PASS | `#groupList .group` = **25**，console 0 error |
| N2 | 零外部网络请求 | ✅ PASS | 拦截全部 request，**仅 1 个 file:// 请求，0 个 http(s)**——无 CDN/无外链 |
| N3 | 搜索 GROSS 命中并过滤 | ✅ PASS | GROSS 可见 **1/25**，命中数="命中 1 / 25 组"；可见组 label 均含 GROSS；清空恢复 25 |
| N3b | 无匹配 → 空态 | ✅ PASS | 显示「无匹配分组…」空态 |
| N4 | 状态筛选计数 = computeTallies | ✅ PASS | 「保持弃权」视图=25=tallyKeep；全部 keep 后「待核对」=0 |
| N5 | 「收起全部」后仅剩头部 | ✅ PASS | 25 组全 `.collapsed`，可见 `.g-body`=**0** |
| N6 | 「下一个待核对」定位并高亮 | ✅ PASS | 产生 `.flash` 组，且该组含「系统建议·待核对」徽标 |
| **N7a/b/c** | **折叠/筛选不影响 selection 与 tally**（不变式） | ✅ PASS | 折叠 25 组 + 单组折叠 + 切换全部筛选片后，`tally` 与选中态**逐位不变** |
| **N8** | **R4 硬闸门**：未核对值存在时要求勾选、口吻切换 | ✅ PASS | 闸门 `#gateBlock` 显现；未勾选注释含「尚未逐组人工核对/预选仅是提示」；勾选后注释切换为「用户已勾选确认已核对原文」+ 闸门加 `ok` 类 |
| N9a | 合法 `?filter=X3&cause=scope` | ✅ PASS | 面包屑显现；`pending` chip 预置；可见组全部命中「合并/母公司」hint（§5.2 明示本快照 25 组全部命中，故 25 可见符合预期） |
| N9b | 非法 `?filter=ZZ&cause=xxx` 静默回退 | ✅ PASS | 回退「全部」25 组、面包屑隐藏、**0 报错** |
| N9c | 无参进入面包屑隐藏 | ✅ PASS | 隐藏正确 |
| N10 | 390/375/360px 无溢出 + 底栏按钮占满 | ✅ PASS | 三档 `scrollW==clientW`，按钮 100% 内容宽，footer 纵向堆叠 |
| **N10-320** | **320px 无横向溢出（CLR-5①）** | ✅ PASS（第2轮） | 修复后 320/340/360/375px 均 `scrollW==clientW`（overflow=0）。第1轮为 FAIL（`scrollW=347 > 320`，越界 `SPAN.g-label`）——见 §3 Bug-3 |
| — | 澄清工具组件内十六进制色字面量 | ✅ PASS | `<style>` 内除 `:root` 外仅有 2 处 `#fff`（accent/toast 上的白字，合规）；无深色残留 |

---

## 3. 发现的缺陷与路由判定

> 路由规则：断言正确但实现错误 → **源码 Bug → 工程师**；断言错误 → **测试代码 Bug → QA 自修**。
> **第 2 轮状态：下列 3 个源码 Bug 已由工程师全部修复，QA 复跑确认通过（见 §3.5）。**

### 🐞 Bug-1（源码 Bug）· 看板 `index.html` 残留深底 #0B0E13　✅ 已修复
- **归属**：**工程师（Alex）**
- **严重度**：中低（挂载后不可见，但违反明文验收 + 载入瞬间反向闪深色）
- **位置**：`abstention-dashboard/index.html` 的**内联首屏防白闪 `<style>`**（该文件**不在**工程师声称的改动清单内，属遗漏）：
  ```html
  <style>
    html, body, #root { height:100%; margin:0; background-color:#0b0e13; color:#e5e7eb; }
  </style>
  ```
- **实测证据**：`getComputedStyle` → `html`=`rgb(11,14,19)`、`#root`=`rgb(11,14,19)`（= #0B0E13）；`body` 被 Tailwind `bg-base` 覆盖为 #F5F7FA。
- **为何是缺陷**：
  1. 违反 **DASH-1③**「全站无 #0B0E13 / #141821 等深色底残留」——字面不符。
  2. 该内联样式原意是"防白闪铺深色底"，迁移后**方向反了**：现在变成 **React 挂载前闪一下深色**（anti-light-flash），与浅色专业风背道而驰。
- **建议修复**：将 `index.html` 内联样式改为浅色（`background-color:#f5f7fa; color:#1f2a37;`），或删除该内联块交由 `index.css` 的 `:root{color-scheme:light}` + `body` 承担。

### 🐞 Bug-2（源码 Bug）· 看板 375px 横向溢出 +21px（DASH-5①③）　✅ 已修复
- **归属**：**工程师（Alex）**
- **严重度**：中（375px 下 5 个视图全部出现，最后一个 tab「需求模拟器」被裁切）
- **位置**：`components/ui/SegmentedControl.tsx`（`role="tablist"` 用 `inline-flex ... overflow-x-auto`）+ `components/Layout.tsx` 的 `<div className="ml-auto">` 包裹层。
- **实测证据**：375px 视口 `documentElement.scrollWidth=396 > clientWidth=375`（+21）；越界元素 `.ml-auto`/tablist 宽度=**380px**。截图 `qa-shots/dash-375-overview-qa.png` 可见最右 tab「需求模拟…」被裁切。
- **根因**：`role=tablist` 是 `inline-flex`，宽度=内容=380px；其父 `.ml-auto` 作为 `flex-wrap` 头部的 flex 项，自动最小尺寸（`min-width:auto`）= tablist 的 min-content=380px，因此**从未真正收缩到视口内**，内部 `overflow-x-auto` 无法生效。
- **注意**：`min-width:0` **仅加在 `.ml-auto` 上不够**（QA 已注入验证，仍溢出 396）；必须同时让 tablist 可收缩（如给 tablist 加 `max-w-full`，或让包裹层在窄屏 `w-full`），`overflow-x-auto` 才会触发。
- **违反**：DASH-5①「375px 宽下结论总览无横向溢出」；DASH-5③「视图切换控件在 375px 下可操作」。

### 🐞 Bug-3（源码 Bug）· 澄清工具 320px 横向溢出 +27px（CLR-5①）　✅ 已修复
- **归属**：**工程师（Alex）**
- **严重度**：中（320px 下溢出；375/360px 正常，溢出窗口 ≤340px）
- **位置**：`scope-clarifier/index.html` 的 `.g-head .g-label`（分组标题 span，无 `min-width:0` / `overflow:hidden` / `text-overflow:ellipsis`）。
- **实测证据**：320px `scrollW=347 > clientW=320`；越界元素 `SPAN.g-label`「正文 · DEPRECIATION_AMORTIZATION」`right=347`、`width=316`；第二处「正文 · EQUITY_PER_SHARE_BROKER_U」`right=326`。
- **溢出窗口**：≤340px 溢出（最长 label 347px）；375/360px 无溢出。
- **根因**：响应式规则（DESIGN §3.3 `@media(max-width:640px)`）只处理了 `.g-meta` 长 id 截断与 `.footer-inner` 堆叠，**遗漏了 `.g-head` 内的长 `.g-label`**。flex 项默认 `min-width:auto`，长标题撑破卡片并溢出视口。
- **违反**：CLR-5①「320px 宽下…无横向滚动条」。
- **建议修复**：`.g-label{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}`（或 `.g-head` 允许换行/flex-wrap）。

### 🧪 测试代码 Bug（QA 自修）
- **N9a 断言口径错误**：初版断言"`cause=scope` 过滤后可见组 <25"。但**快照 25 组全部带「合并/未扣非」hint**（已程序化枚举核实 25/25），按 DESIGN §5.2 明示"25 组全部命中"，故可见 25 是**正确行为**。已改为断言"面包屑显现 + pending chip on + 可见组全部命中口径类 hint"。**已由 QA 自行修复**，非源码问题。

### ✅ 3.5 第 2 轮修复确认（2026-10-15）

| Bug | 工程师修复 | QA 复跑证据 | 判定 |
| --- | --- | --- | --- |
| Bug-1 | `index.html` 内联底色 `#0b0e13/#e5e7eb` → `#f5f7fa/#1f2a37` | `getComputedStyle` html/#root/body 均 `rgb(245,247,250)`；全站 0 处旧深底 | ✅ 修复确认 |
| Bug-2 | `SegmentedControl.tsx` tablist 加 `max-w-full`；`Layout.tsx` 包裹层 → `ml-auto min-w-0 max-w-full` | 375/360px `scrollW==clientW`（overflow=0）；tablist 内部可横滚；**反向回归**：桌面 1440px 仍是单行 5 tab（rows=1，lastRight=1404≤1440，无溢出） | ✅ 修复确认，无新增回归 |
| Bug-3 | `scope-clarifier` `@media(max-width:640px)` 增加 `.g-label{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}` | 320/340/360/375px 均 `scrollW==clientW`，console 零 error；**反向回归**：桌面 1280px 下 25 个 `.g-label` 无一被截断（ellipsis 仅在窄屏生效） | ✅ 修复确认，无新增回归 |

**第 2 轮汇总**：QA 三脚本 **38/38 PASS**；官方 `run-all.mjs` **7/7 ALL PASS**（`qa-official-runall-round2.log`）。**全部通过，无遗留缺陷。**

---

## 4. 遗留风险

> **第 2 轮更新**：RK1/RK2/RK3 对应的 3 个缺陷已修复验证，风险消除。剩余为**非阻断**的部署/契约提示项。

| # | 风险 | 说明 | 影响 |
| --- | --- | --- | --- |
| ~~RK1~~ | ~~载入瞬间深色闪屏~~ | 已随 Bug-1 修复（内联底浅色化） | ✅ 消除 |
| ~~RK2~~ | ~~375px tab 裁切~~ | 已随 Bug-2 修复 | ✅ 消除 |
| ~~RK3~~ | ~~320px 长分组名溢出~~ | 已随 Bug-3 修复；反向回归确认桌面不误截断 | ✅ 消除 |
| RK4 | **跨端跳转相对路径依赖同根部署** | 看板链接为 `../scope-clarifier/index.html`。若看板单独部署到别的根，需 `VITE_CLARIFIER_URL` 覆盖（DESIGN §6/§13-1 已声明）。本次以同根托管验证通过 | 部署期配置提示（非阻断） |
| RK5 | **`cause=scope` 不排他** | 当前快照下 `cause=scope` 命中全部 25 组（快照每组均带「合并/未扣非」hint），筛选"看起来没生效"。符合 DESIGN §5.2 预期；若真实数据 hint 分布变化，语义会随之变化 | 契约语义提示（非阻断） |
| RK6 | **工程师自带测试盲区（已给出建议）** | 工程师 D 组断言只覆盖"链接存在/href 正确"，**未覆盖**：① html/#root 底色残留；② 视口级横向溢出；③ 320px 断点。**建议将本次 3 个 QA 脚本并入回归**以防复发 | 回归覆盖建议（已提交工程师） |

---

## 5. 附：证据文件索引

```
ui-optimization/qa-tools/
├── qa-verify-dashboard.mjs          # 浅色 token + 三处隐形坑对比度 + 叙事红线（12 项）
├── qa-verify-clarifier.mjs          # file:// 零网络 + 交互 + R4 闸门 + URL 契约 + 响应式（18 项）
├── qa-verify-dashboard-mobile.mjs   # 移动端溢出/sticky + 跳转动线（8 项）
├── qa-verify-round2-deep.mjs        # 第2轮加强项：tab可点/280px/无深色闪屏/5视图副作用（6 项）
├── qa-official-runall.log           # 第1轮：工程师自带 7/7 全量回归独立复跑留痕
├── qa-official-runall-round2.log    # 第2轮：修复后 7/7 复跑留痕
└── qa-shots/
    ├── dash-composition-light.png   # 浅色构成分解（含 c02「→去澄清」+ 迁移图 to 深字）
    ├── dash-drilldown-light.png     # 逐条下钻浅色
    ├── dash-overview-light.png      # 结论总览浅色
    ├── dash-375-overview-qa.png     # 375px 溢出证据（tab 裁切）
    ├── dash-375-drilldown-qa.png    # 375px 下钻 sticky
    ├── clarifier-desktop-qa.png     # 澄清工具桌面全页浅色
    ├── clarifier-gate-qa.png        # R4 硬闸门 + 3 步下一步引导
    ├── clarifier-390px-qa.png       # 澄清工具 390px
    ├── clarifier-375px-qa.png       # 澄清工具 375px
    ├── clarifier-360px-qa.png       # 澄清工具 360px
    ├── clarifier-320px-qa.png       # 澄清工具 320px（第2轮修复后）
    └── clarifier-280px-qa.png       # 澄清工具 280px 极端宽度（第2轮）
```

---

## 6. 路由判定（最终）

| 判定 | 内容 |
| --- | --- |
| **→ NoOne（全部通过）** | 3 个源码 Bug（Bug-1/2/3）经工程师修复，第 2 轮 QA 复跑 **38/38 + 官方 7/7 全绿**，反向回归确认无新增问题。**结论：全部通过，无需再路由。** |
| 第 1 轮 → 工程师（Alex） | 已闭环：修复 Bug-1（浅色化内联底）、Bug-2（SegmentedControl 375px 溢出）、Bug-3（`.g-label` 320px 溢出）。 |
| 第 1 轮 → QA（本人） | N9a 断言口径错误，已自修。 |
| 全通过项 | 浅色迁移核心三处隐形坑（B/C/D 对比度达标）、叙事红线（E/F/G/H + R4）、跨端 URL 契约（N9/M4/M5/M6）、移动端 sticky/横滚/折叠不变式（N7/M2/M3）——**均真实通过**。 |

**判定归属汇总**：第 1 轮发现 3 个源码 Bug（已定位文件+根因+修复建议）+ 1 个测试代码 Bug（QA 自修）；第 2 轮全部验证通过，**最终判定：NoOne（通过）**。
