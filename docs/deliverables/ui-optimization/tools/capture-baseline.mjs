/**
 * 基线/优化后截图采集脚本（供 UI 优化前后对比）。
 *
 * 采集两个界面：
 *  1. abstention-dashboard（React dist 产物，走静态服务器）
 *  2. scope-clarifier（原生 HTML，走静态服务器以规避 file:// 的 fetch/CORS 限制）
 *
 * 桌面 + 移动两种视口，桌面拍 5 个视图，移动只拍首屏。
 *
 * 用法：
 *   node tools/capture-baseline.mjs <输出子目录名>
 * 例：
 *   node tools/capture-baseline.mjs before
 *   node tools/capture-baseline.mjs after
 */
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join, extname } from 'node:path';
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { existsSync, mkdirSync } from 'node:fs';

const require = createRequire(import.meta.url);
const __dirname = dirname(fileURLToPath(import.meta.url));
const OUT_ROOT = resolve(__dirname, '../baseline-shots');

const DASH = resolve(
  __dirname,
  '../../abstention-dashboard'
);
const CLARIFIER = resolve(__dirname, '../../scope-clarifier');
const CHROME =
  'C:/Users/xy/AppData/Local/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-win64/chrome-headless-shell.exe';

const PW_CANDIDATES = [
  join(DASH, 'tests/.pwtools/node_modules/playwright-core'),
  join(homedir(), 'pwqa/node_modules/playwright-core'),
];
const pwPath = PW_CANDIDATES.find((p) => existsSync(join(p, 'package.json')));
if (!pwPath) {
  console.error('找不到 playwright-core，候选：\n  ' + PW_CANDIDATES.join('\n  '));
  process.exit(2);
}
const pw = require(pwPath);

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.ico': 'image/x-icon',
};

/** 起一个静态服务器托管 root 目录。 */
async function serve(root, port) {
  const server = createServer(async (req, res) => {
    try {
      const url = new URL(req.url, 'http://127.0.0.1');
      let p = decodeURIComponent(url.pathname);
      if (p === '/' || p === '') p = '/index.html';
      const file = join(root, p);
      if (existsSync(file) && !file.endsWith('\\') && !file.endsWith('/')) {
        res.writeHead(200, {
          'Content-Type': MIME[extname(file)] ?? 'application/octet-stream',
        });
        res.end(await readFile(file));
        return;
      }
      res.writeHead(404).end('not found: ' + p);
    } catch (e) {
      res.writeHead(500).end('err ' + e.message);
    }
  });
  await new Promise((r) => server.listen(port, '127.0.0.1', r));
  return server;
}

/** 关闭浏览器（headless-shell 的 close 可能挂起，竞速兜底）。 */
async function closeBrowser(b) {
  await Promise.race([
    b.close().catch(() => {}),
    new Promise((r) => setTimeout(r, 3000)),
  ]);
}

const shots = [];
async function shoot(page, file, label) {
  await page.screenshot({ path: file, fullPage: true });
  shots.push({ file, label });
  console.log('  ✔ ' + label + ' → ' + file);
}

async function main() {
  const sub = process.argv[2] ?? 'before';
  const outDir = join(OUT_ROOT, sub);
  if (!existsSync(outDir)) mkdirSync(outDir, { recursive: true });
  console.log('输出目录：' + outDir + '\n');

  const browser = await pw.chromium.launch({ executablePath: CHROME, headless: true });

  // ── 1. 弃权看板（dist 产物）──
  console.log('[1/2] 弃权看板 abstention-dashboard（dist）');
  const dashServer = await serve(join(DASH, 'dist'), 4510);
  try {
    // 桌面视口
    const desktop = await browser.newContext({
      viewport: { width: 1440, height: 900 },
      deviceScaleFactor: 1,
    });
    const dp = await desktop.newPage();
    const errs = [];
    dp.on('console', (m) => {
      if (m.type() === 'error') errs.push(m.text());
    });
    dp.on('pageerror', (e) => errs.push('pageerror: ' + e.message));
    await dp.goto('http://127.0.0.1:4510/', { waitUntil: 'networkidle', timeout: 30000 });
    await dp.waitForTimeout(600);

    const views = ['结论总览', '构成分解', '根因树', '逐条下钻', '需求模拟器'];
    const slugs = ['overview', 'composition', 'rootcause', 'drilldown', 'simulator'];
    for (let i = 0; i < views.length; i++) {
      await dp.getByRole('tab', { name: views[i] }).click();
      await dp.waitForTimeout(400);
      await shoot(
        dp,
        join(outDir, `dashboard-desktop-${slugs[i]}.png`),
        `看板·桌面·${views[i]}`
      );
    }
    console.log('  console 错误数：' + errs.length);
    await desktop.close();

    // 移动视口
    const mobile = await browser.newContext({
      viewport: { width: 390, height: 844 },
      deviceScaleFactor: 2,
      isMobile: true,
      hasTouch: true,
    });
    const mp = await mobile.newPage();
    await mp.goto('http://127.0.0.1:4510/', { waitUntil: 'networkidle', timeout: 30000 });
    await mp.waitForTimeout(600);
    await shoot(mp, join(outDir, 'dashboard-mobile-overview.png'), '看板·移动·结论总览');
    await mobile.close();
  } finally {
    dashServer.close();
  }

  // ── 2. 口径澄清工具（原生 HTML）──
  console.log('\n[2/2] 口径澄清工具 scope-clarifier（原生 HTML）');
  const clServer = await serve(CLARIFIER, 4511);
  try {
    const desktop = await browser.newContext({
      viewport: { width: 1440, height: 900 },
      deviceScaleFactor: 1,
    });
    const dp = await desktop.newPage();
    const errs = [];
    dp.on('console', (m) => {
      if (m.type() === 'error') errs.push(m.text());
    });
    dp.on('pageerror', (e) => errs.push('pageerror: ' + e.message));
    await dp.goto('http://127.0.0.1:4511/', { waitUntil: 'networkidle', timeout: 30000 });
    await dp.waitForTimeout(500);
    await shoot(dp, join(outDir, 'clarifier-desktop-full.png'), '澄清工具·桌面·首屏');

    // 触发"生成重算命令"，拍生成态（含硬闸门）
    const gen = dp.locator('#generateBtn');
    if ((await gen.count()) > 0) {
      await gen.click();
      await dp.waitForTimeout(500);
      await shoot(dp, join(outDir, 'clarifier-desktop-generated.png'), '澄清工具·桌面·生成命令态');
    }
    console.log('  console 错误数：' + errs.length);
    await desktop.close();

    const mobile = await browser.newContext({
      viewport: { width: 390, height: 844 },
      deviceScaleFactor: 2,
      isMobile: true,
      hasTouch: true,
    });
    const mp = await mobile.newPage();
    await mp.goto('http://127.0.0.1:4511/', { waitUntil: 'networkidle', timeout: 30000 });
    await mp.waitForTimeout(500);
    await shoot(mp, join(outDir, 'clarifier-mobile-full.png'), '澄清工具·移动·首屏');
    await mobile.close();
  } finally {
    clServer.close();
  }

  await closeBrowser(browser);

  console.log('\n===== 采集完成 =====');
  console.log(`共 ${shots.length} 张截图 → ${outDir}`);
  process.exit(0);
}

const wd = setTimeout(() => {
  console.error('[watchdog] 采集超时');
  process.exit(3);
}, 180000);
wd.unref?.();
main().catch((e) => {
  console.error('FATAL', e);
  process.exit(2);
});
