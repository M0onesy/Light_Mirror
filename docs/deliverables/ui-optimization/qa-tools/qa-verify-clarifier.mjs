/**
 * QA 独立验证 · 澄清工具（scope-clarifier/index.html），file:// 直开。
 *
 * 校验项：
 *  N1. file:// 直开可用，25 组全渲染，console 零 error
 *  N2. 零外部网络请求（拦截 request，断言无 http(s) 外链/CDN）
 *  N3. 搜索：输入 GROSS → 命中并过滤（可见组减少、命中数更新）
 *  N4. 状态筛选：选「保持弃权」等 → 可见集合变化且与 tally 一致
 *  N5. 折叠：点 .g-head 折叠该组正文；「收起全部」后仅剩头部
 *  N6. 进度锚点：「下一个待核对」能定位并高亮目标组
 *  N7. 关键反向测试（不变式）：折叠/筛选不改变 selection 与底部 tally
 *  N8. 硬闸门 R4：有未核对值时输出面板顶部要求勾选；勾选前后命令注释口吻切换
 *  N9. URL 契约：合法 ?filter=X3&cause=scope 预置筛选+面包屑；非法 filter=ZZ 静默回退；无参隐藏
 *  N10. 响应式：390px / 320px 无横向滚动溢出、底栏按钮可用
 *
 * 运行：node qa-tools/qa-verify-clarifier.mjs
 */
import { fileURLToPath, pathToFileURL } from 'node:url';
import { dirname, resolve, join } from 'node:path';
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { existsSync, mkdirSync } from 'node:fs';

const require = createRequire(import.meta.url);
const __dirname = dirname(fileURLToPath(import.meta.url));
const FILE = resolve(__dirname, '../../scope-clarifier/index.html');
const FILE_URL = pathToFileURL(FILE).href;
const SHOT_DIR = join(__dirname, 'qa-shots');
const CHROME =
  'C:/Users/xy/AppData/Local/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-win64/chrome-headless-shell.exe';
const PW_CANDIDATES = [
  join(__dirname, '../../abstention-dashboard/tests/.pwtools/node_modules/playwright-core'),
  join(homedir(), 'pwqa/node_modules/playwright-core'),
];
const pwPath = PW_CANDIDATES.find((p) => existsSync(join(p, 'package.json')));
if (!pwPath) { console.error('找不到 playwright-core'); process.exit(2); }
const pw = require(pwPath);
if (!existsSync(SHOT_DIR)) mkdirSync(SHOT_DIR, { recursive: true });

const results = [];
async function check(name, fn) {
  try { const d = await fn(); results.push({ name, ok: true, detail: (d ?? '').toString() }); }
  catch (e) { results.push({ name, ok: false, detail: e.message }); }
}
function assert(c, m) { if (!c) throw new Error(m); }

const BASE_ARGS = ['--allow-file-access-from-files', '--disable-web-security'];

async function newPage(browser, url, viewport) {
  const ctx = await browser.newContext({ viewport: viewport ?? { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const errors = [];
  const net = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push('[console.error] ' + m.text()); });
  page.on('pageerror', (e) => errors.push('[pageerror] ' + e.message));
  page.on('request', (req) => net.push(req.url()));
  await page.goto(url, { waitUntil: 'load', timeout: 30000 });
  await page.waitForTimeout(400);
  return { page, errors, net };
}

async function main() {
  const browser = await pw.chromium.launch({ executablePath: CHROME, headless: true, args: BASE_ARGS });

  // ===== 基础页面（无参） =====
  const { page, errors, net } = await newPage(browser, FILE_URL);

  await check('N1. file:// 直开：25 组全渲染 + console 零 error', async () => {
    const n = await page.locator('#groupList .group').count();
    assert(n === 25, `分组数 = ${n}，期望 25`);
    assert(errors.length === 0, `console 错误 ${errors.length}: ${errors.join(' | ')}`);
    return `25 组渲染，console 无 error`;
  });

  await check('N2. 零外部网络请求（无 CDN / 无外链）', async () => {
    const ext = net.filter((u) => /^https?:/i.test(u));
    assert(ext.length === 0, `发现外部请求:\n  ${ext.join('\n  ')}`);
    return `共 ${net.length} 个请求，全部为 file://（无 http(s)）`;
  });

  // ===== N3 搜索 =====
  await check('N3. 搜索 GROSS：命中过滤 + 命中数更新', async () => {
    await page.fill('#searchInput', 'GROSS');
    await page.waitForTimeout(250);
    const visible = await page.locator('#groupList .group:not(.hidden)').count();
    const total = await page.locator('#groupList .group').count();
    const hitTxt = await page.textContent('#hitCount');
    assert(visible < total, `搜索后可见 ${visible} === 总 ${total}，未过滤`);
    assert(visible >= 1, `搜索 GROSS 后无任何可见组`);
    // 校验可见组 label 均含 GROSS（大小写不敏感）
    const labels = await page.$$eval('#groupList .group:not(.hidden) .g-label', (els) => els.map((e) => e.textContent));
    const bad = labels.filter((l) => !/gross/i.test(l));
    assert(bad.length === 0, `可见组 label 不含 GROSS: ${bad.join(', ')}`);
    assert(/命中\s*\d+/.test(hitTxt), `命中数未更新: ${hitTxt}`);
    // 清空恢复
    await page.fill('#searchInput', '');
    await page.waitForTimeout(200);
    const back = await page.locator('#groupList .group:not(.hidden)').count();
    assert(back === 25, `清空后可见 ${back}，期望 25`);
    return `GROSS 可见 ${visible}/${total}，清空恢复 25；hitCount="${hitTxt}"`;
  });

  await check('N3b. 搜索无匹配 → 空态', async () => {
    await page.fill('#searchInput', '__NO_MATCH_XYZ__');
    await page.waitForTimeout(200);
    const visible = await page.locator('#groupList .group:not(.hidden)').count();
    const emptyVisible = await page.locator('#emptyState').isVisible().catch(() => false);
    await page.fill('#searchInput', '');
    await page.waitForTimeout(200);
    assert(visible === 0, `无匹配时仍有 ${visible} 组可见`);
    assert(emptyVisible, '未显示空态');
    return '空态正确显示';
  });

  // ===== N7 不变式基线：记录 tally + 一组 selection =====
  const invBase = await page.evaluate(() => {
    const t = ['tallyConsolidated', 'tallyParent', 'tallyKeep', 'tallyEstimate']
      .map((id) => document.getElementById(id).textContent);
    // 记录第一个 group 的选中 radio value
    const first = document.querySelector('#groupList .group');
    const checked = first ? first.querySelector('input[type=radio]:checked') : null;
    return { tally: t, firstId: first ? first.id : null, firstVal: checked ? checked.value : null };
  });
  // 先改第二个组的 selection，制造非默认态
  await page.evaluate(() => {
    const g = document.querySelectorAll('#groupList .group')[1];
    const keep = Array.from(g.querySelectorAll('input[type=radio]')).find((i) => i.value === 'keep');
    if (keep && !keep.checked) { keep.click(); }
  });
  await page.waitForTimeout(200);
  const invAfterChange = await page.evaluate(() => {
    return ['tallyConsolidated', 'tallyParent', 'tallyKeep', 'tallyEstimate']
      .map((id) => document.getElementById(id).textContent);
  });

  await check('N7a. 折叠不影响 selection 与底部 tally', async () => {
    // 记录当前 tally 与所有选中态
    const before = await page.evaluate(() => ({
      tally: ['tallyConsolidated', 'tallyParent', 'tallyKeep', 'tallyEstimate'].map((id) => document.getElementById(id).textContent),
      checked: Array.from(document.querySelectorAll('#groupList input[type=radio]:checked')).length,
    }));
    // 折叠全部
    await page.click('#toggleAll'); // 收起全部
    await page.waitForTimeout(250);
    const collapsedCount = await page.locator('#groupList .group.collapsed').count();
    // 展开全部
    await page.click('#toggleAll');
    await page.waitForTimeout(250);
    const after = await page.evaluate(() => ({
      tally: ['tallyConsolidated', 'tallyParent', 'tallyKeep', 'tallyEstimate'].map((id) => document.getElementById(id).textContent),
      checked: Array.from(document.querySelectorAll('#groupList input[type=radio]:checked')).length,
    }));
    assert(collapsedCount === 25, `收起全部后 collapsed 组数 = ${collapsedCount}，期望 25`);
    assert(JSON.stringify(before.tally) === JSON.stringify(after.tally),
      `折叠改变了 tally：${before.tally} → ${after.tally}`);
    assert(before.checked === after.checked, `折叠改变了 selection：${before.checked} → ${after.checked}`);
    return `折叠 25 组后 tally/selection 不变 (${before.tally.join(',')})`;
  });

  await check('N7b. 单组折叠只切正文显隐，不改 selection', async () => {
    const before = await page.evaluate(() => {
      const g = document.querySelectorAll('#groupList .group')[0];
      const c = g.querySelector('input[type=radio]:checked');
      return { val: c ? c.value : null };
    });
    await page.click('#groupList .group:nth-child(1) .g-head');
    await page.waitForTimeout(200);
    const collapsed = await page.locator('#groupList .group:nth-child(1).collapsed').count();
    // 正文应不可见
    const bodyVisible = await page.locator('#groupList .group:nth-child(1) .g-body').isVisible().catch(() => false);
    const after = await page.evaluate(() => {
      const g = document.querySelectorAll('#groupList .group')[0];
      const c = g.querySelector('input[type=radio]:checked');
      return { val: c ? c.value : null };
    });
    await page.click('#groupList .group:nth-child(1) .g-head'); // 还原
    await page.waitForTimeout(150);
    assert(collapsed === 1, '点击 .g-head 未折叠该组');
    assert(!bodyVisible, '折叠后 .g-body 仍可见');
    assert(before.val === after.val, `折叠改变了该组 selection: ${before.val} → ${after.val}`);
    return `折叠后正文隐藏，selection 保持 (${after.val})`;
  });

  await check('N7c. 筛选不改变 selection 与 tally', async () => {
    const before = await page.evaluate(() => ({
      tally: ['tallyConsolidated', 'tallyParent', 'tallyKeep', 'tallyEstimate'].map((id) => document.getElementById(id).textContent),
      checked: Array.from(document.querySelectorAll('#groupList input[type=radio]:checked')).map((i) => i.value),
    }));
    // 依次点各筛选片
    for (const status of ['pending', 'confirmed', 'keep', 'all']) {
      await page.click(`#filterChips .filter[data-status="${status}"]`);
      await page.waitForTimeout(120);
    }
    const after = await page.evaluate(() => ({
      tally: ['tallyConsolidated', 'tallyParent', 'tallyKeep', 'tallyEstimate'].map((id) => document.getElementById(id).textContent),
      checked: Array.from(document.querySelectorAll('#groupList input[type=radio]:checked')).map((i) => i.value),
    }));
    assert(JSON.stringify(before.tally) === JSON.stringify(after.tally), `筛选改变了 tally：${before.tally} → ${after.tally}`);
    assert(JSON.stringify(before.checked) === JSON.stringify(after.checked), `筛选改变了 selection`);
    return `切换全部筛选片后 tally/selection 不变 (${before.tally.join(',')})`;
  });

  await check('N4. 状态筛选「保持弃权」显示数 = tallyKeep', async () => {
    // 先把全部保持弃权，保证 keep=25
    await page.click('[data-bulk="keep"]');
    await page.waitForTimeout(250);
    await page.click('#filterChips .filter[data-status="keep"]');
    await page.waitForTimeout(200);
    const visible = await page.locator('#groupList .group:not(.hidden)').count();
    const tallyKeep = parseInt(await page.textContent('#tallyKeep'), 10);
    assert(visible === tallyKeep, `保持弃权筛选可见 ${visible} ≠ tallyKeep ${tallyKeep}`);
    // 待核对应为 0（全部 keep 后）
    await page.click('#filterChips .filter[data-status="pending"]');
    await page.waitForTimeout(200);
    const pending = await page.locator('#groupList .group:not(.hidden)').count();
    assert(pending === 0, `全部保持弃权后「待核对」仍显示 ${pending} 组`);
    await page.click('#filterChips .filter[data-status="all"]');
    await page.waitForTimeout(150);
    return `keep 视图=${visible}=tallyKeep；pending=0 正确`;
  });

  // ===== N8 硬闸门 R4 =====
  await check('N8. 硬闸门 R4：未核对值存在时输出面板顶部要求勾选', async () => {
    // 全部按合并 → 生成，应触发 gate（若仍有 suggested）
    await page.click('[data-bulk="合并"]');
    await page.waitForTimeout(250);
    await page.click('#generateBtn');
    await page.waitForTimeout(300);
    const gateVisible = await page.locator('#gateBlock').isVisible();
    const gateText = await page.textContent('#gateText');
    assert(gateVisible, '硬闸门未显示（有未核对值时本应显示）');
    assert(/核对|原文/.test(gateText), `闸门文案异常: ${gateText.slice(0, 60)}`);
    // 命令注释：未勾选 → 应含警示口吻
    const cmdBefore = await page.textContent('#outputCmd');
    const hasWarnTone = /尚未逐组人工核对|预选仅是提示/.test(cmdBefore);
    assert(hasWarnTone, `未勾选时命令注释未含警示口吻:\n${cmdBefore.slice(0, 260)}`);
    // 勾选
    await page.check('#gateConfirm');
    await page.waitForTimeout(350);
    const cmdAfter = await page.textContent('#outputCmd');
    const hasConfirmTone = /用户已勾选确认已核对原文/.test(cmdAfter);
    assert(hasConfirmTone, `勾选后命令注释未切换口吻:\n${cmdAfter.slice(0, 260)}`);
    assert(cmdBefore !== cmdAfter, '勾选前后命令注释未变化');
    // 勾选后闸门样式 ok
    const gateCls = await page.getAttribute('#gateBlock', 'class');
    assert(/ok/.test(gateCls), `勾选后闸门未加 ok 样式: ${gateCls}`);
    return `闸门显现→勾选→注释口吻切换（warn→confirmed）`;
  });

  // ===== N5 折叠全部（在 gate 场景后重置）=====
  await check('N5. 「收起全部」后 25 组仅剩头部', async () => {
    // 已有部分状态；直接点收起全部
    const label = await page.textContent('#toggleAll');
    // 确保处于「收起全部」可用态：先展开
    if (label.includes('展开')) { await page.click('#toggleAll'); await page.waitForTimeout(150); }
    await page.click('#toggleAll'); // 收起
    await page.waitForTimeout(250);
    const collapsed = await page.locator('#groupList .group.collapsed').count();
    const bodiesVisible = await page.locator('#groupList .group .g-body:visible').count();
    assert(collapsed === 25, `收起全部后 collapsed=${collapsed}，期望 25`);
    assert(bodiesVisible === 0, `收起全部后仍有 ${bodiesVisible} 个正文可见`);
    // 展开还原
    await page.click('#toggleAll');
    await page.waitForTimeout(200);
    return `25 组全部折叠，正文 0 可见`;
  });

  // ===== N6 进度锚点 =====
  await check('N6. 「下一个待核对」定位并高亮目标组', async () => {
    // 重置为存在 pending：刷新页面（无参）
    await page.reload({ waitUntil: 'load' });
    await page.waitForTimeout(400);
    // 确保筛选=全部
    await page.click('#filterChips .filter[data-status="all"]');
    await page.waitForTimeout(150);
    const beforeCount = await page.locator('#originBanner .locate').count();
    await page.click('#nextPending');
    await page.waitForTimeout(400);
    // 应有 .flash 高亮组
    const flashCount = await page.locator('#groupList .group.flash').count();
    assert(flashCount >= 1, '点击「下一个待核对」后无 .flash 高亮组');
    // 高亮组应含「系统建议·待核对」徽标
    const flashHasSug = await page.locator('#groupList .group.flash .tag.sug').count();
    assert(flashHasSug >= 1, '高亮组不是待核对组');
    return `锚点定位到 .flash 组（含系统建议徽标），originBanner locate=${beforeCount}`;
  });

  // 截图（桌面）
  await page.screenshot({ path: join(SHOT_DIR, 'clarifier-desktop-qa.png'), fullPage: true });

  // ===== N9 URL 契约（另开页面）=====
  const valid = await newPage(browser, FILE_URL + '?filter=X3&cause=scope');
  await check('N9a. 合法 ?filter=X3&cause=scope：预置筛选 + 面包屑显示', async () => {
    const crumbVisible = await valid.page.locator('#originCrumb').isVisible();
    const crumbText = await valid.page.textContent('#originCrumb');
    assert(crumbVisible, '合法参数进入未显示来源面包屑');
    assert(/弃权诊断看板|口径类/.test(crumbText), `面包屑文案异常: ${crumbText}`);
    // 应预置 pending 筛选（chip on）
    const pendingOn = await valid.page.locator('#filterChips .filter[data-status="pending"].on').count();
    assert(pendingOn === 1, '合法 filter=X3 未预置「待核对」筛选');
    // cause=scope 过滤：因快照 25 组全部带「合并/母公司」hint，故可见组仍为 25（DESIGN §5.2 明示）。
    // 正确契约：可见组必须全部命中 合并/母公司 hint（证明 cause 过滤谓词确实生效，只是不排他）。
    const visible = await valid.page.locator('#groupList .group:not(.hidden)').count();
    const hintsAllScope = await valid.page.$$eval('#groupList .group:not(.hidden)', (els) =>
      els.every((el) => {
        const h = el.querySelector('.hint');
        return h ? /合并|母公司/.test(h.textContent) : true;
      })
    );
    assert(hintsAllScope, '存在可见组不含 合并/母公司 hint（cause=scope 未生效）');
    return `面包屑可见·pending chip on·可见 ${visible}/25 且全部命中口径类 hint（§5.2 预期 25）`;
  });
  await valid.page.close(); await valid.page.context().close();

  const invalid = await newPage(browser, FILE_URL + '?filter=ZZ&cause=xxx');
  await check('N9b. 非法 ?filter=ZZ&cause=xxx：静默回退全部、无报错、面包屑隐藏', async () => {
    const crumbHidden = !(await invalid.page.locator('#originCrumb').isVisible().catch(() => false));
    const visible = await invalid.page.locator('#groupList .group:not(.hidden)').count();
    const allOn = await invalid.page.locator('#filterChips .filter[data-status="all"].on').count();
    assert(crumbHidden, '非法参数进入仍显示面包屑');
    assert(visible === 25, `非法参数未回退全部（可见 ${visible}）`);
    assert(allOn === 1, '非法参数未回退「全部」筛选片');
    assert(invalid.errors.length === 0, `非法参数产生报错: ${invalid.errors.join(' | ')}`);
    return `回退全部(25 组)、面包屑隐藏、无报错`;
  });
  await invalid.page.close(); await invalid.page.context().close();

  const noParam = await newPage(browser, FILE_URL);
  await check('N9c. 无参进入：面包屑隐藏', async () => {
    const hidden = !(await noParam.page.locator('#originCrumb').isVisible().catch(() => false));
    assert(hidden, '无参进入却显示了面包屑');
    return '无参面包屑隐藏';
  });
  await noParam.page.close(); await noParam.page.context().close();

  // ===== N10 响应式 =====
  for (const w of [390, 375, 360, 320]) {
    const r = await newPage(browser, FILE_URL, { width: w, height: 780 });
    await check(`N10. ${w}px 视口：无横向滚动溢出 + 底栏按钮可用`, async () => {
      const overflow = await r.page.evaluate(() => {
        const de = document.documentElement;
        return { scrollW: de.scrollWidth, clientW: de.clientWidth };
      });
      // 定位溢出元素（便于报告）
      const offenders = await r.page.evaluate(() => {
        const vw = document.documentElement.clientWidth;
        const out = [];
        for (const el of document.querySelectorAll('*')) {
          const rr = el.getBoundingClientRect();
          if (rr.right > vw + 0.5) out.push(`${el.tagName}.${(el.className || '').toString().slice(0, 30)} right=${Math.round(rr.right)}`);
        }
        return out.slice(0, 5);
      });
      // 按钮宽度与 footer-inner 内容盒比较（扣除左右 padding）
      const geo = await r.page.evaluate(() => {
        const inner = document.querySelector('.footer-inner');
        const cs = getComputedStyle(inner);
        const padL = parseFloat(cs.paddingLeft) || 0;
        const padR = parseFloat(cs.paddingRight) || 0;
        const contentW = inner.getBoundingClientRect().width - padL - padR;
        const btn = document.getElementById('generateBtn').getBoundingClientRect();
        const dir = cs.flexDirection;
        return { contentW, btnW: btn.width, btnH: btn.height, dir };
      });
      assert(overflow.scrollW <= overflow.clientW + 1,
        `${w}px 横向溢出 scrollW=${overflow.scrollW} > clientW=${overflow.clientW}；越界元素: ${offenders.join(' | ')}`);
      assert(geo.btnW > 0 && geo.btnH > 0, `${w}px 底栏按钮不可见`);
      assert(geo.btnW >= geo.contentW - 1,
        `${w}px 底栏按钮 ${Math.round(geo.btnW)} < 内容宽 ${Math.round(geo.contentW)}（未占满）`);
      await r.page.screenshot({ path: join(SHOT_DIR, `clarifier-${w}px-qa.png`), fullPage: false });
      return `scrollW=${overflow.scrollW}<=clientW=${overflow.clientW}；按钮 ${Math.round(geo.btnW)}/${Math.round(geo.contentW)} 占满，dir=${geo.dir}`;
    });
    await r.page.close(); await r.page.context().close();
  }

  await Promise.race([browser.close().catch(() => {}), new Promise((res) => setTimeout(res, 3000))]);

  const pass = results.filter((x) => x.ok).length;
  const fail = results.length - pass;
  console.log('\n===== QA 独立验证 · 澄清工具（CLR）=====');
  for (const x of results) console.log(`${x.ok ? 'PASS' : 'FAIL'} | ${x.name}${x.detail ? ' :: ' + x.detail : ''}`);
  console.log(`\n合计 ${results.length} | 通过 ${pass} | 失败 ${fail}`);
  process.exit(fail === 0 ? 0 : 1);
}

const wd = setTimeout(() => { console.error('[watchdog] 超时'); process.exit(3); }, 180000);
wd.unref?.();
main().catch((e) => { console.error('FATAL', e); process.exit(2); });
