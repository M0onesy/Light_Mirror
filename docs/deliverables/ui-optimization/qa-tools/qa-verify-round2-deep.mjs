/**
 * QA 第 2 轮深度复验 —— team-lead 指定的加强项。
 *
 *  D1. Bug-2 深度：375px scrollW==clientW，且最右 tab「需求模拟器」**真的可点击切换**
 *  D2. Bug-3 深度：320px scrollW==clientW；**280px 极端宽度**是否溢出 + console 零 error
 *  D3. Bug-1 深度：React 挂载前后 html 计算样式一致（无深色闪屏中间态）
 *  D4. 副作用：375px 下 5 个视图全部无横向溢出
 *  D5. 副作用：桌面 1440px 下 5 个视图渲染正常 + 无溢出
 *
 * 运行：node qa-tools/qa-verify-round2-deep.mjs
 */
import { spawn } from 'node:child_process';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { dirname as _d, resolve, join } from 'node:path';
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { existsSync, mkdirSync } from 'node:fs';

const require = createRequire(import.meta.url);
const __dirname = _d(fileURLToPath(import.meta.url));
const DASH_ROOT = resolve(__dirname, '../../abstention-dashboard');
const SHOT_DIR = join(__dirname, 'qa-shots');
const FILE_URL = pathToFileURL(resolve(__dirname, '../../scope-clarifier/index.html')).href;
const PORT = 4551;
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

  // ===== D3. React 挂载前后 html 颜色一致（无深色闪屏中间态）=====
  // 先在导航前注入 init script 记录 html 初始底色，再到 networkidle 后取一次
  const c0 = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const p0 = await c0.newPage();
  // 用 addInitScript 在最早时机采样（DOMContentLoaded 之前、内联 style 已应用）
  await p0.addInitScript(() => {
    window.__qaEarly = new Promise((res) => {
      const sample = () => {
        try { res(getComputedStyle(document.documentElement).backgroundColor); } catch (e) { res('ERR'); }
      };
      if (document.documentElement) {
        // 内联 style 在内联 <style> 解析后即生效；用 queueMicrotask 尽早取
        sample();
      } else {
        document.addEventListener('readystatechange', sample, { once: true });
      }
    });
  });
  await p0.goto(BASE, { waitUntil: 'commit', timeout: 30000 });
  const early = await p0.evaluate(() => window.__qaEarly);
  await p0.waitForLoadState('networkidle');
  await p0.waitForTimeout(400);
  const late = await p0.evaluate(() => getComputedStyle(document.documentElement).backgroundColor);
  await check('D3. Bug-1 深度：React 挂载前后 html 底色一致（无深色闪屏）', async () => {
    const isDark = (s) => /rgba?\(11,\s*14,\s*19/.test(s);
    assert(!isDark(early), `挂载前 html 底色仍为深色: ${early}`);
    assert(!isDark(late), `挂载后 html 底色为深色: ${late}`);
    assert(early === late, `挂载前后不一致 early=${early} late=${late}（存在中间态闪色）`);
    return `early=${early} == late=${late}（均浅色，无深色中间态）`;
  });
  await c0.close();

  // ===== D1. Bug-2 深度：375px 最右 tab 可点击切换 =====
  const c1 = await browser.newContext({ viewport: { width: 375, height: 780 } });
  const p1 = await c1.newPage();
  const e1 = [];
  p1.on('console', (m) => { if (m.type() === 'error') e1.push(m.text()); });
  p1.on('pageerror', (x) => e1.push('pageerror: ' + x.message));
  await p1.goto(BASE, { waitUntil: 'networkidle', timeout: 30000 });
  await p1.waitForTimeout(400);
  await check('D1. Bug-2 深度：375px 溢出归零 + 最右 tab「需求模拟器」可点击切换', async () => {
    const ov = await p1.evaluate(() => ({ s: document.documentElement.scrollWidth, c: document.documentElement.clientWidth }));
    assert(ov.s <= ov.c + 1, `375px 仍溢出 scrollW=${ov.s} > clientW=${ov.c}`);
    // 点击最右 tab（真实点击，需先滚入视口内可点）
    const tab = p1.getByRole('tab', { name: '需求模拟器' });
    await tab.scrollIntoViewIfNeeded();
    await tab.click({ timeout: 5000 });
    await p1.waitForTimeout(450);
    const bodyTxt = await p1.textContent('body');
    assert(/需求池|不做清单|模拟结果/.test(bodyTxt), '点击「需求模拟器」后内容未切换');
    // 确认 tab 的选中态
    const selected = await p1.evaluate(() => {
      const t = Array.from(document.querySelectorAll('[role=tab]')).find((x) => x.textContent.includes('需求模拟器'));
      return t ? t.getAttribute('aria-selected') : null;
    });
    assert(selected === 'true', `「需求模拟器」点击后 aria-selected=${selected}`);
    // 再点回「构成分解」确认双向可切
    await p1.getByRole('tab', { name: '构成分解' }).click();
    await p1.waitForTimeout(350);
    const t2 = await p1.textContent('body');
    assert(/弃权构成分解/.test(t2), '切回「构成分解」失败');
    return `overflow=0；最右 tab 可点击切换(aria-selected=true)并切回；console err=${e1.length}`;
  });
  await c1.close();

  // ===== D4. 副作用：375px 下 5 个视图全部无溢出 =====
  const c2 = await browser.newContext({ viewport: { width: 375, height: 780 } });
  const p2 = await c2.newPage();
  await p2.goto(BASE, { waitUntil: 'networkidle', timeout: 30000 });
  await p2.waitForTimeout(400);
  await check('D4. 副作用：375px 下 5 个视图均无横向溢出', async () => {
    const bad = [];
    for (const name of ['结论总览', '构成分解', '根因树', '逐条下钻', '需求模拟器']) {
      await p2.getByRole('tab', { name }).click();
      await p2.waitForTimeout(350);
      const r = await p2.evaluate(() => ({ s: document.documentElement.scrollWidth, c: document.documentElement.clientWidth }));
      if (r.s > r.c + 1) bad.push(`${name}: ${r.s}>${r.c}`);
    }
    assert(bad.length === 0, `仍有视图溢出: ${bad.join(' | ')}`);
    return '5 视图全部 scrollW<=clientW';
  });
  await c2.close();

  // ===== D5. 副作用：桌面 1440px 5 视图正常 + 无溢出 + tab 单行 =====
  const c3 = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const p3 = await c3.newPage();
  const e3 = [];
  p3.on('console', (m) => { if (m.type() === 'error') e3.push(m.text()); });
  p3.on('pageerror', (x) => e3.push('pageerror: ' + x.message));
  await p3.goto(BASE, { waitUntil: 'networkidle', timeout: 30000 });
  await p3.waitForTimeout(400);
  await check('D5. 副作用：桌面 1440px 5 视图渲染正常 + 无溢出 + tab 单行不裁切', async () => {
    const tabGeo = await p3.evaluate(() => {
      const tl = document.querySelector('[role=tablist]');
      const tabs = Array.from(tl.querySelectorAll('[role=tab]'));
      const rows = new Set(tabs.map((t) => Math.round(t.getBoundingClientRect().top))).size;
      const last = tabs[tabs.length - 1].getBoundingClientRect();
      return { rows, lastRight: Math.round(last.right), vw: document.documentElement.clientWidth };
    });
    assert(tabGeo.rows === 1, `桌面 tab 换行崩坏 rows=${tabGeo.rows}`);
    assert(tabGeo.lastRight <= tabGeo.vw, `桌面最右 tab 被裁切 right=${tabGeo.lastRight}>vw=${tabGeo.vw}`);
    const expects = {
      '结论总览': /30 秒结论/,
      '构成分解': /弃权构成分解/,
      '根因树': /根因树/,
      '逐条下钻': /命中|原始标签/,
      '需求模拟器': /需求池|不做清单|模拟结果/,
    };
    const bad = [];
    for (const [name, re] of Object.entries(expects)) {
      await p3.getByRole('tab', { name }).click();
      await p3.waitForTimeout(350);
      const t = await p3.textContent('body');
      if (!re.test(t)) bad.push(name);
      const r = await p3.evaluate(() => ({ s: document.documentElement.scrollWidth, c: document.documentElement.clientWidth }));
      if (r.s > r.c + 1) bad.push(`${name}:溢出`);
    }
    assert(bad.length === 0, `桌面异常: ${bad.join(' | ')}`);
    assert(e3.length === 0, `桌面 console 错误: ${e3.join(' | ')}`);
    return `5 视图渲染正常、无溢出；tab 单行 lastRight=${tabGeo.lastRight}<=${tabGeo.vw}`;
  });
  await c3.close();

  // ===== D2. Bug-3 深度：320px + 280px 极端宽度 =====
  for (const w of [320, 280]) {
    const cc = await browser.newContext({ viewport: { width: w, height: 780 } });
    const pp = await cc.newPage();
    const ec = [];
    pp.on('console', (m) => { if (m.type() === 'error') ec.push(m.text()); });
    pp.on('pageerror', (x) => ec.push('pageerror: ' + x.message));
    await pp.goto(FILE_URL, { waitUntil: 'load', timeout: 30000 });
    await pp.waitForTimeout(400);
    await check(`D2. Bug-3 深度：${w}px 澄清工具无溢出 + console 零 error`, async () => {
      const r = await pp.evaluate(() => {
        const de = document.documentElement;
        const offenders = [];
        for (const el of document.querySelectorAll('*')) {
          const rr = el.getBoundingClientRect();
          if (rr.right > de.clientWidth + 0.5) offenders.push(`${el.tagName}.${(el.className || '').toString().slice(0, 25)}`);
        }
        return { s: de.scrollWidth, c: de.clientWidth, offenders: offenders.slice(0, 5) };
      });
      assert(ec.length === 0, `${w}px console 错误: ${ec.join(' | ')}`);
      assert(r.s <= r.c + 1, `${w}px 溢出 scrollW=${r.s} > clientW=${r.c}；越界: ${r.offenders.join(' | ')}`);
      await pp.screenshot({ path: join(SHOT_DIR, `clarifier-${w}px-qa.png`), fullPage: false });
      return `scrollW=${r.s}<=clientW=${r.c}，console 零 error`;
    });
    await cc.close();
  }

  await Promise.race([browser.close().catch(() => {}), new Promise((r) => setTimeout(r, 3000))]);
  server.kill('SIGKILL');

  const pass = results.filter((x) => x.ok).length;
  const fail = results.length - pass;
  console.log('\n===== QA 第 2 轮深度复验（team-lead 加强项）=====');
  for (const x of results) console.log(`${x.ok ? 'PASS' : 'FAIL'} | ${x.name}${x.detail ? ' :: ' + x.detail : ''}`);
  console.log(`\n合计 ${results.length} | 通过 ${pass} | 失败 ${fail}`);
  process.exit(fail === 0 ? 0 : 1);
}

const wd = setTimeout(() => { console.error('[watchdog] 超时'); process.exit(3); }, 160000);
wd.unref?.();
main().catch((e) => { console.error('FATAL', e); process.exit(2); });
