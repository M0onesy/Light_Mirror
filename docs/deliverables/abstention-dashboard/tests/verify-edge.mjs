/**
 * 边界情况浏览器测试（E14 / E15）。
 *
 * 通过内存补丁复刻两种真实数据变体（不修改 dist 文件、不做目录复制）：
 *  E14 - items 数组置空：验证下钻表不崩溃（渲染「无匹配条目」空态）
 *  E15 - denominator_scope 由 all_candidates 改为 main_scope：
 *        验证首屏分母由 319 → 295，EVR 相应变化（6/319≈1.9% → 6/295≈2.0%）
 *
 * 运行：node tests/verify-edge.mjs
 */
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join, extname, basename } from 'node:path';
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { existsSync } from 'node:fs';

const require = createRequire(import.meta.url);
const __dirname = dirname(fileURLToPath(import.meta.url));
const DIST = resolve(__dirname, '../dist');
const PORT = 4319;
const BASE = `http://127.0.0.1:${PORT}`;
const CHROME =
  'C:/Users/xy/AppData/Local/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-win64/chrome-headless-shell.exe';

const PW_CANDIDATES = [
  join(__dirname, '.pwtools/node_modules/playwright-core'),
  join(homedir(), 'pwqa/node_modules/playwright-core'),
];
const pwPath = PW_CANDIDATES.find((p) => existsSync(join(p, 'package.json')));
if (!pwPath) {
  console.error('找不到 playwright-core');
  process.exit(2);
}
const pw = require(pwPath);

const MIME = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml', '.ico': 'image/x-icon' };

/** 从 dist 找到主 JS 与 CSS 文件名。 */
async function findAssets() {
  const html = await readFile(join(DIST, 'index.html'), 'utf-8');
  const js = html.match(/assets\/index-[^"']+\.js/)[0];
  const css = html.match(/assets\/index-[^"']+\.css/)?.[0] ?? null;
  return { js, css, html };
}

/** 补丁：把 items 数组替换为空。 */
function patchEmptyItems(jsText) {
  const start = jsText.indexOf('am=[{id:"i0001"');
  if (start === -1) throw new Error('未找到 items 数组起点 am=[{id:"i0001"');
  // 从 start 往后找到匹配的顶层 ']'（简单括号计数，跳过字符串内括号）
  let i = jsText.indexOf('[', start);
  let depth = 0;
  let inStr = false;
  let quote = '';
  let end = -1;
  for (; i < jsText.length; i++) {
    const c = jsText[i];
    if (inStr) {
      if (c === '\\') { i++; continue; }
      if (c === quote) inStr = false;
      continue;
    }
    if (c === '"' || c === "'") { inStr = true; quote = c; continue; }
    if (c === '[') depth++;
    else if (c === ']') {
      depth--;
      if (depth === 0) { end = i; break; }
    }
  }
  if (end === -1) throw new Error('未找到 items 数组结束位置');
  return jsText.slice(0, start) + 'am=[]' + jsText.slice(end + 1);
}

/** 补丁：denominator_scope all_candidates → main_scope。 */
function patchScope(jsText) {
  const needle = 'denominator_scope:"all_candidates"';
  if (!jsText.includes(needle)) throw new Error('未找到 denominator_scope:"all_candidates"');
  return jsText.replace(needle, 'denominator_scope:"main_scope"');
}

/** 启动带补丁的静态服务器。 */
async function startPatchedServer(patchFn) {
  const { js, css, html } = await findAssets();
  const jsBody = patchFn(await readFile(join(DIST, js), 'utf-8'));
  const server = createServer(async (req, res) => {
    try {
      const url = new URL(req.url, BASE);
      const p = decodeURIComponent(url.pathname);
      if (p === '/' || p === '/index.html') {
        // 保持原 html（引用同一 js 文件名，内容由补丁提供）
        res.writeHead(200, { 'Content-Type': MIME['.html'] });
        res.end(html);
        return;
      }
      if (p === '/' + js) {
        res.writeHead(200, { 'Content-Type': MIME['.js'] });
        res.end(jsBody);
        return;
      }
      if (css && p === '/' + css) {
        res.writeHead(200, { 'Content-Type': MIME['.css'] });
        res.end(await readFile(join(DIST, css)));
        return;
      }
      const file = join(DIST, p);
      if (existsSync(file)) {
        res.writeHead(200, { 'Content-Type': MIME[extname(file)] ?? 'application/octet-stream' });
        res.end(await readFile(file));
        return;
      }
      res.writeHead(404).end('nf');
    } catch (e) {
      res.writeHead(500).end('err ' + e.message);
    }
  });
  await new Promise((r) => server.listen(PORT, '127.0.0.1', r));
  return server;
}

const results = [];
function check(name, fn) {
  try { results.push({ name, ok: true, detail: fn() ?? '' }); }
  catch (e) { results.push({ name, ok: false, detail: e.message }); }
}
function assert(c, m) { if (!c) throw new Error(m); }

async function scenario(name, patchFn, run) {
  const server = await startPatchedServer(patchFn);
  const browser = await pw.chromium.launch({ executablePath: CHROME, headless: true });
  const page = await browser.newPage();
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));
  try {
    await page.goto(BASE, { waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(400);
    await run(page, errors);
  } finally {
    await Promise.race([browser.close().catch(() => {}), new Promise((r) => setTimeout(r, 3000))]);
    server.close();
  }
}

async function main() {
  // —— E14：空 items ——
  await scenario('E14', patchEmptyItems, async (page, errors) => {
    await page.getByRole('tab', { name: '逐条下钻' }).click();
    await page.waitForTimeout(400);
    const t = await page.textContent('body');
    check('E14-a. 空 items 下钻视图不崩溃且有渲染', () => {
      assert(/无匹配条目|命中\s*0/.test(t), '空 items 未显示空态文案（无匹配条目/命中 0）');
      assert(/全部（0 条样例）/.test(t), '下拉未反映 0 条样例');
      return '显示空态「无匹配条目」';
    });
    check('E14-b. 空 items 场景 console 零错误', () => {
      assert(errors.length === 0, `捕获错误：${errors.join(' | ')}`);
      return 'console 无 error';
    });
  });

  // —— E15：denominator_scope = main_scope ——
  await scenario('E15', patchScope, async (page, errors) => {
    const t = await page.textContent('body');
    check('E15-a. 切换到 main_scope 后分母显示 295 而非 319', () => {
      assert(/分母\s*295/.test(t), '未显示分母 295');
      assert(/main_scope/.test(t), '未显示 denominator_scope=main_scope');
      return '分母=295, scope=main_scope';
    });
    check('E15-b. main_scope 下 EVR 大卡数值 = 6/295 ≈ 2.0%', () => {
      // 6/295 = 2.0339% → 大卡显示 2.0%
      // 注意：JSON 中 north_star.stages[0]（「现状」）为静态 1.9%，属阶段刻度，非实时 EVR，不算违规。
      const evrCtx = t.match(/有效核实率 EVR[0-9.]+%/)?.[0] ?? '';
      assert(/有效核实率 EVR2\.0%/.test(t), `EVR 大卡未显示 2.0%（6/295）。EVR 卡片段: ${evrCtx}`);
      assert(/6\/295/.test(t), '公式未反映分母 295');
      return 'EVR 大卡=2.0%，公式=6/295';
    });
    check('E15-c. main_scope 场景 console 零错误', () => {
      assert(errors.length === 0, `捕获错误：${errors.join(' | ')}`);
      return 'console 无 error';
    });
  });

  const pass = results.filter((r) => r.ok).length;
  const fail = results.length - pass;
  console.log('\n===== 边界情况测试（E 组）=====');
  for (const r of results) console.log(`${r.ok ? 'PASS' : 'FAIL'} | ${r.name}${r.detail ? ' :: ' + r.detail : ''}`);
  console.log(`\n合计 ${results.length} 项 | 通过 ${pass} | 失败 ${fail}`);
  process.exit(fail === 0 ? 0 : 1);
}

const wd = setTimeout(() => { console.error('[watchdog] 超时'); process.exit(3); }, 120000);
wd.unref?.();
main().catch((e) => { console.error('FATAL', e); process.exit(2); });
