/**
 * QA 独立验证 · 看板移动端 + 跨端 URL 契约（DASH-3/4/5/7, BOTH-1/2）。
 *
 *  M1. 375px：结论总览无横向溢出
 *  M2. 375px：逐条下钻宽表 sticky 首列生效（滚动后首列仍在视口左侧）
 *  M3. 375px：SegmentedControl 可横滚不换行崩坏
 *  M4. 跳转动线：构成分解 c02 行「→ 去澄清」href 含 filter=X3&cause=scope + target=_blank
 *  M5. 跳转动线：逐条下钻口径类行(P0-3)有链接、非口径行无链接
 *  M6. 看板顶栏/页脚存在指向澄清工具的链接且 target=_blank（DASH-7）
 *
 * 运行：node qa-tools/qa-verify-dashboard-mobile.mjs
 */
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join } from 'node:path';
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { existsSync, mkdirSync } from 'node:fs';

const require = createRequire(import.meta.url);
const __dirname = dirname(fileURLToPath(import.meta.url));
const DASH_ROOT = resolve(__dirname, '../../abstention-dashboard');
const SHOT_DIR = join(__dirname, 'qa-shots');
const PORT = 4531;
const BASE = `http://127.0.0.1:${PORT}`;
const CHROME =
  'C:/Users/xy/AppData/Local/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-win64/chrome-headless-shell.exe';
const PW_CANDIDATES = [
  join(DASH_ROOT, 'tests/.pwtools/node_modules/playwright-core'),
  join(homedir(), 'pwqa/node_modules/playwright-core'),
];
const pwPath = PW_CANDIDATES.find((p) => existsSync(join(p, 'package.json')));
const pw = require(pwPath);
if (!existsSync(SHOT_DIR)) mkdirSync(SHOT_DIR, { recursive: true });

const results = [];
async function check(name, fn) {
  try { const d = await fn(); results.push({ name, ok: true, detail: (d ?? '').toString() }); }
  catch (e) { results.push({ name, ok: false, detail: e.message }); }
}
function assert(c, m) { if (!c) throw new Error(m); }

async function startServer() {
  const child = spawn(process.execPath, [join(DASH_ROOT, 'tests/static-server.mjs'), String(PORT)], { stdio: ['ignore', 'pipe', 'pipe'] });
  await new Promise((res, rej) => {
    const t = setTimeout(() => rej(new Error('server 超时')), 10000);
    child.stdout.on('data', (d) => { if (d.toString().includes('static-server')) { clearTimeout(t); res(); } });
    child.on('exit', (c) => { clearTimeout(t); rej(new Error('server 退出 ' + c)); });
  });
  return child;
}

async function main() {
  const server = await startServer();
  const browser = await pw.chromium.launch({ executablePath: CHROME, headless: true });

  // ============ 桌面：跳转动线 ============
  const desktopCtx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await desktopCtx.newPage();
  const errs = [];
  page.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()); });
  page.on('pageerror', (e) => errs.push('pageerror: ' + e.message));
  await page.goto(BASE, { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(400);

  await page.getByRole('tab', { name: '构成分解' }).click();
  await page.waitForTimeout(400);
  await check('M4. 构成分解「→ 去澄清」href 含 filter=X3&cause=scope + target=_blank', async () => {
    const links = await page.$$eval('a', (as) =>
      as.filter((a) => (a.textContent || '').includes('去澄清'))
        .map((a) => ({ href: a.getAttribute('href'), target: a.getAttribute('target'), rel: a.getAttribute('rel') }))
    );
    assert(links.length >= 1, '构成分解未找到「去澄清」链接');
    for (const l of links) {
      assert(l.href && l.href.includes('filter=X3') && l.href.includes('cause=scope'),
        `href 契约错误: ${l.href}`);
      assert(l.target === '_blank', `target 不是 _blank: ${l.target}`);
    }
    return `${links.length} 个链接，href=${links[0].href}，target=_blank`;
  });

  await page.getByRole('tab', { name: '逐条下钻' }).click();
  await page.waitForTimeout(400);
  await check('M5. 逐条下钻：口径类行有链接、非口径行无（且 href 正确）', async () => {
    const r = await page.evaluate(() => {
      const rows = Array.from(document.querySelectorAll('table tbody tr'));
      let cal = 0, non = 0;
      const bad = [];
      for (const tr of rows) {
        const tds = tr.querySelectorAll('td');
        if (tds.length === 0) continue;
        const idCell = (tds[0].textContent || '').trim();
        const reqCell = (tds[tds.length - 2]?.textContent || '').trim();
        const last = tds[tds.length - 1];
        const a = last && last.querySelector('a');
        const hasLink = !!(a && (a.textContent || '').includes('去澄清'));
        const isCaliber = reqCell === 'P0-3';
        if (isCaliber) {
          if (hasLink) {
            cal++;
            const href = a.getAttribute('href') || '';
            if (!(href.includes('filter=X3') && href.includes('cause=scope'))) bad.push(`${idCell}:${href}`);
            if (a.getAttribute('target') !== '_blank') bad.push(`${idCell}:target=${a.getAttribute('target')}`);
          } else bad.push(`${idCell}: 口径类缺链接`);
        } else {
          if (!hasLink) non++; else bad.push(`${idCell}: 非口径行却有链接`);
        }
      }
      return { cal, non, bad };
    });
    assert(r.bad.length === 0, `异常: ${r.bad.join(' | ')}`);
    assert(r.cal >= 1, '本页未见口径类行');
    return `口径类 ${r.cal} 行均有正确链接；非口径 ${r.non} 行均无链接`;
  });

  await check('M6. 看板顶栏/页脚存在澄清工具外链且 target=_blank（DASH-7）', async () => {
    const r = await page.evaluate(() => {
      const as = Array.from(document.querySelectorAll('a'));
      const hits = as.filter((a) => (a.textContent || '').includes('口径澄清工具'));
      return hits.map((a) => ({ href: a.getAttribute('href'), target: a.getAttribute('target') }));
    });
    assert(r.length >= 1, '未找到「口径澄清工具」外链');
    for (const h of r) assert(h.target === '_blank', `外链 target 不是 _blank: ${h.target}`);
    return `${r.length} 个外链，href=${r[0].href}，target=_blank`;
  });

  await check('M0. 桌面全流程 console 零 error', async () => {
    assert(errs.length === 0, `错误: ${errs.join(' | ')}`);
    return '无 error';
  });
  await desktopCtx.close();

  // ============ 移动端 375px ============
  const mCtx = await browser.newContext({ viewport: { width: 375, height: 780 } });
  const m = await mCtx.newPage();
  const mErr = [];
  m.on('console', (x) => { if (x.type() === 'error') mErr.push(x.text()); });
  m.on('pageerror', (e) => mErr.push('pageerror: ' + e.message));
  await m.goto(BASE, { waitUntil: 'networkidle', timeout: 30000 });
  await m.waitForTimeout(400);

  await check('M1. 375px：结论总览无横向溢出', async () => {
    const r = await m.evaluate(() => ({ s: document.documentElement.scrollWidth, c: document.documentElement.clientWidth }));
    assert(r.s <= r.c + 1, `375px 溢出 scrollW=${r.s} > clientW=${r.c}`);
    await m.screenshot({ path: join(SHOT_DIR, 'dash-375-overview-qa.png'), fullPage: false });
    return `scrollW=${r.s}<=clientW=${r.c}`;
  });

  await check('M3. 375px：SegmentedControl 可横滚不换行崩坏', async () => {
    const r = await m.evaluate(() => {
      const tablist = document.querySelector('[role="tablist"]');
      if (!tablist) throw new Error('未找到 SegmentedControl');
      const wrap = tablist.parentElement;
      return {
        tabCount: tablist.querySelectorAll('[role="tab"]').length,
        tablistW: tablist.scrollWidth,
        parentOverflowX: getComputedStyle(wrap).overflowX,
        tabH: tablist.getBoundingClientRect().height,
      };
    });
    assert(r.tabCount === 5, `tab 数=${r.tabCount}`);
    // 单行高度（不换行）：5 个 tab 单行应 <= ~40px
    assert(r.tabH < 60, `SegmentedControl 高度 ${r.tabH} 疑似换行崩坏`);
    return `5 tab，高度 ${Math.round(r.tabH)}px（未换行），父 overflowX=${r.parentOverflowX}`;
  });

  await check('M2. 375px：逐条下钻宽表 sticky 首列生效', async () => {
    await m.getByRole('tab', { name: '逐条下钻' }).click();
    await m.waitForTimeout(500);
    const r = await m.evaluate(() => {
      const scroller = document.querySelector('table')?.closest('.overflow-x-auto');
      if (!scroller) throw new Error('未找到表格横滚容器');
      const th = scroller.querySelector('thead th');
      const stickyHeader = th ? getComputedStyle(th).position : null;
      // 记录首列文本，横向滚动后应仍可见（sticky left:0）
      const firstCell = scroller.querySelector('tbody tr td');
      const textBefore = firstCell ? firstCell.textContent.trim() : '';
      scroller.scrollLeft = 300;
      const rect = firstCell ? firstCell.getBoundingClientRect() : null;
      const scrollerRect = scroller.getBoundingClientRect();
      return {
        stickyHeader,
        textBefore,
        cellLeft: rect ? Math.round(rect.left) : null,
        scrollerLeft: Math.round(scrollerRect.left),
        scrollLeftSet: scroller.scrollLeft,
        stickyPos: firstCell ? getComputedStyle(firstCell).position : null,
      };
    });
    assert(r.stickyHeader === 'sticky', `表头 position=${r.stickyHeader}，非 sticky`);
    assert(r.stickyPos === 'sticky', `首列 position=${r.stickyPos}，非 sticky`);
    // 滚动后首列左边缘应贴近容器左边缘（仍在视口内），而非被推出
    assert(Math.abs(r.cellLeft - r.scrollerLeft) <= 3,
      `横向滚动后首列左边缘 ${r.cellLeft} 偏离容器左 ${r.scrollerLeft}（sticky 失效）`);
    return `表头/首列均 sticky；滚动 ${r.scrollLeftSet}px 后首列 left=${r.cellLeft}≈容器 left=${r.scrollerLeft}`;
  });

  await check('M7. 375px 全流程 console 零 error', async () => {
    assert(mErr.length === 0, `错误: ${mErr.join(' | ')}`);
    return '无 error';
  });
  await mCtx.close();

  await Promise.race([browser.close().catch(() => {}), new Promise((r) => setTimeout(r, 3000))]);
  server.kill('SIGKILL');

  const pass = results.filter((x) => x.ok).length;
  const fail = results.length - pass;
  console.log('\n===== QA 独立验证 · 看板移动端 + 跳转动线 =====');
  for (const x of results) console.log(`${x.ok ? 'PASS' : 'FAIL'} | ${x.name}${x.detail ? ' :: ' + x.detail : ''}`);
  console.log(`\n合计 ${results.length} | 通过 ${pass} | 失败 ${fail}`);
  process.exit(fail === 0 ? 0 : 1);
}

const wd = setTimeout(() => { console.error('[watchdog] 超时'); process.exit(3); }, 150000);
wd.unref?.();
main().catch((e) => { console.error('FATAL', e); process.exit(2); });
