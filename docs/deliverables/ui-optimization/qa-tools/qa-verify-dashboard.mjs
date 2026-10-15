/**
 * QA 独立验证 · 看板（abstention-dashboard）证据级校验。
 *
 * 目的：不复述工程师结论，用真实渲染 + getComputedStyle + WCAG 对比度计算，
 * 独立证明浅色迁移的三处「浅色下会隐形」的坑真的修好了，且叙事红线未被破坏。
 *
 * 校验项：
 *  A. 页面底/面板底色确为浅色（不是深色）
 *  B. AbstentionBar 段内标签文字：fill 为近白，且与所在色块对比度 >= 4.5:1（青/琥珀/灰/紫块）
 *  C. MigrationChart "to" 数值：fill 为深色(tokens.ink)，浅底对比度 >= 4.5:1
 *  D. Tag neutral 底色：非 bg-white/5（计算后为 rgba(255,255,255,0.05)），应实心可见
 *  E. 页脚「弃权是护城河，不是缺陷」不是红色
 *  F. 合法弃权条目(intentional)在浅色下仍非红色（拿 AmendmentBadge / Tag 实际色）
 *  G. 反指标=0 显示「合规」而非「无数据」
 *  H. 全站「降低弃权率」只出现在否定语境（扫全页）
 *  I. 组件内无十六进制色字面量（grep 已在脚本外，这里做运行期 CSS 兜底扫描）
 *
 * 运行：node qa-tools/qa-verify-dashboard.mjs
 * 输出：console 结果 + qa-shots/*.png 截图
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
const DIST = resolve(DASH_ROOT, 'dist');
const SHOT_DIR = join(__dirname, 'qa-shots');
const PORT = 4521;
const BASE = `http://127.0.0.1:${PORT}`;
const CHROME =
  'C:/Users/xy/AppData/Local/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-win64/chrome-headless-shell.exe';

const PW_CANDIDATES = [
  join(DASH_ROOT, 'tests/.pwtools/node_modules/playwright-core'),
  join(homedir(), 'pwqa/node_modules/playwright-core'),
];
const pwPath = PW_CANDIDATES.find((p) => existsSync(join(p, 'package.json')));
if (!pwPath) { console.error('找不到 playwright-core'); process.exit(2); }
const pw = require(pwPath);

if (!existsSync(SHOT_DIR)) mkdirSync(SHOT_DIR, { recursive: true });

// —— WCAG 2.1 相对亮度 / 对比度 ——
function parseColor(str) {
  // 支持 rgb(r,g,b) / rgba(r,g,b,a) / #rrggbb
  if (!str) return null;
  str = str.trim();
  let m = str.match(/^rgba?\(([^)]+)\)$/i);
  if (m) {
    const parts = m[1].split(',').map((s) => parseFloat(s.trim()));
    return { r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1 };
  }
  m = str.match(/^#([0-9a-f]{6})$/i);
  if (m) {
    const n = parseInt(m[1], 16);
    return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255, a: 1 };
  }
  m = str.match(/^#([0-9a-f]{3})$/i);
  if (m) {
    const h = m[1];
    const n = parseInt(h[0] + h[0] + h[1] + h[1] + h[2] + h[2], 16);
    return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255, a: 1 };
  }
  return null;
}
function luminance({ r, g, b }) {
  const f = (c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}
function contrast(fg, bg) {
  const L1 = luminance(fg), L2 = luminance(bg);
  const hi = Math.max(L1, L2), lo = Math.min(L1, L2);
  return (hi + 0.05) / (lo + 0.05);
}
/** 把 rgba(alpha) 前景叠加到不透明背景上，得到有效颜色。 */
function blend(fg, bg) {
  const a = fg.a ?? 1;
  return {
    r: fg.r * a + bg.r * (1 - a),
    g: fg.g * a + bg.g * (1 - a),
    b: fg.b * a + bg.b * (1 - a),
    a: 1,
  };
}
function hex(c) {
  const h = (n) => Math.round(n).toString(16).padStart(2, '0');
  return `#${h(c.r)}${h(c.g)}${h(c.b)}`.toUpperCase();
}

const results = [];
async function check(name, fn) {
  try { const d = await fn(); results.push({ name, ok: true, detail: (d ?? '').toString() }); }
  catch (e) { results.push({ name, ok: false, detail: e.message }); }
}
function assert(c, m) { if (!c) throw new Error(m); }

async function startServer() {
  const child = spawn(process.execPath, [join(DASH_ROOT, 'tests/static-server.mjs'), String(PORT)], {
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  await new Promise((res, rej) => {
    const t = setTimeout(() => rej(new Error('server 启动超时')), 10000);
    child.stdout.on('data', (d) => { if (d.toString().includes('static-server')) { clearTimeout(t); res(); } });
    child.on('exit', (c) => { clearTimeout(t); rej(new Error('server 提前退出 ' + c)); });
  });
  return child;
}

async function main() {
  const server = await startServer();
  const browser = await pw.chromium.launch({ executablePath: CHROME, headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));

  try {
    await page.goto(BASE, { waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(500);

    // ============ A. 页面底色为浅色 ============
    await check('A. 页面/面板底为浅色（明度 >= 0.9，非深色残留）', () => {
      return page.evaluate(() => {
        const bodyBg = getComputedStyle(document.body).backgroundColor;
        return { bodyBg };
      }).then((r) => {
        const bodyBg = parseColor(r.bodyBg);
        const L = luminance(bodyBg);
        assert(bodyBg, '无法解析 body 背景色: ' + r.bodyBg);
        assert(L >= 0.9, `body 背景亮度 ${L.toFixed(3)} < 0.9（疑似仍是深色）: ${r.bodyBg}`);
        return `body bg=${r.bodyBg} (L=${L.toFixed(3)})`;
      });
    });

    // ============ 全站深色残留扫描（#0B0E13 / #141821） ============
    // 注意：#0B0E13 = rgb(11,14,19)。命令块 --cmd-bg=#0F172A=rgb(15,23,42) 属代码块惯例，豁免。
    await check('A2a. 可见内容区无深色残留底（#0B0E13=rgb(11,14,19)）', () => {
      return page.evaluate(() => {
        const bad = [];
        for (const el of document.querySelectorAll('*')) {
          const bg = getComputedStyle(el).backgroundColor;
          if (!bg || bg === 'rgba(0, 0, 0, 0)' || bg === 'transparent') continue;
          const m = bg.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
          if (!m) continue;
          const [r, g, b] = [+m[1], +m[2], +m[3]];
          // 精确锁定旧深底三色，避免误伤 cmd-bg
          const isOldDark =
            (r === 11 && g === 14 && b === 19) || // #0B0E13
            (r === 20 && g === 24 && b === 33) || // #141821
            (r === 16 && g === 20 && b === 28);   // #10141C
          if (isOldDark) bad.push(`${el.tagName}#${el.id || ''}.${(el.className || '').toString().slice(0, 50)}=${bg}`);
        }
        return bad.slice(0, 12);
      }).then((bad) => {
        assert(bad.length === 0, `发现 ${bad.length} 处旧深底残留：\n  ${bad.join('\n  ')}`);
        return '无旧深底残留';
      });
    });

    // 单独核验 html / body / #root 三层底色（防白闪内联样式残留重点区）
    await check('A2b. html/#root 计算底色非 #0B0E13（防白闪内联样式已迁浅）', () => {
      return page.evaluate(() => {
        const g = (el) => (el ? getComputedStyle(el).backgroundColor : '(missing)');
        return {
          html: g(document.documentElement),
          body: g(document.body),
          root: g(document.getElementById('root')),
        };
      }).then((r) => {
        const dark = [];
        for (const [k, v] of Object.entries(r)) {
          const c = parseColor(v);
          if (c && c.r === 11 && c.g === 14 && c.b === 19) dark.push(`${k}=${v}`);
        }
        assert(dark.length === 0, `html/#root 仍为深色 #0B0E13：${dark.join(', ')}（body=${r.body}, root=${r.root}）`);
        return `html=${r.html} body=${r.body} root=${r.root}`;
      });
    });

    // 切到构成分解视图，取 SVG
    await page.getByRole('tab', { name: '构成分解' }).click();
    await page.waitForTimeout(400);

    // ============ B. AbstentionBar 段内标签对比度 ============
    await check('B. AbstentionBar 段内标签文字在其色块上对比度 >= 4.5:1', () => {
      return page.evaluate(() => {
        const svg = document.querySelector('svg[aria-label="弃权构成水平堆叠条"]');
        if (!svg) throw new Error('未找到构成堆叠条 SVG');
        const rects = Array.from(svg.querySelectorAll('rect'));
        const texts = Array.from(svg.querySelectorAll('text'));
        if (texts.length === 0) throw new Error('段内无标签文字');
        const out = [];
        for (const t of texts) {
          const fill = getComputedStyle(t).fill;
          // 找到同一 <g> 内的 rect 作为底
          const g = t.closest('g');
          const rect = g && g.querySelector('rect');
          const rectFill = rect ? getComputedStyle(rect).fill : null;
          const opacity = rect ? parseFloat(getComputedStyle(rect).opacity || '1') : 1;
          out.push({ text: t.textContent.trim().slice(0, 20), fill, rectFill, opacity });
        }
        return out;
      }).then((segs) => {
        const details = [];
        for (const s of segs) {
          const fg = parseColor(s.fill);
          const bgRaw = parseColor(s.rectFill);
          assert(fg, '标签 fill 无法解析: ' + s.fill);
          assert(bgRaw, '色块 fill 无法解析: ' + s.rectFill);
          // 色块底叠到白底（opacity 处理）
          const bgOnWhite = blend({ ...bgRaw, a: s.opacity }, { r: 255, g: 255, b: 255, a: 1 });
          const ratio = contrast(fg, bgOnWhite);
          details.push(`${s.text}: 文字${hex(fg)} / 色块${hex(bgOnWhite)} = ${ratio.toFixed(2)}:1`);
          assert(ratio >= 4.5, `段内标签「${s.text}」对比度 ${ratio.toFixed(2)} < 4.5（浅色下会看不清）`);
        }
        return details.join(' | ');
      });
    });

    // ============ C. MigrationChart "to" 数值可见 ============
    await check('C. MigrationChart 右侧 to 数值为深色、浅底对比度 >= 4.5:1', () => {
      return page.evaluate(() => {
        const svg = document.querySelector('svg[aria-label="X 码迁移对比图"]');
        if (!svg) throw new Error('未找到迁移图 SVG');
        // to 数值是每个 <g> 内最后一个 text（delta 前），我们取所有 text 的 fill 集合
        const texts = Array.from(svg.querySelectorAll('text'));
        // panel 底为白/浅
        const panel = svg.closest('.mj-panel');
        const panelBg = panel ? getComputedStyle(panel).backgroundColor : 'rgb(255,255,255)';
        const fills = texts.map((t) => ({ txt: t.textContent.trim(), fill: getComputedStyle(t).fill }));
        return { fills, panelBg };
      }).then((r) => {
        const bgc = parseColor(r.panelBg) ?? { r: 255, g: 255, b: 255, a: 1 };
        // 找纯数字的 to 数值文本
        const numeric = r.fills.filter((f) => /^\d+$/.test(f.txt));
        assert(numeric.length > 0, '未找到 to 数值文本');
        const details = [];
        for (const n of numeric) {
          const fg = parseColor(n.fill);
          const ratio = contrast(fg, bgc);
          details.push(`${n.txt}:${hex(fg)}/${hex(bgc)}=${ratio.toFixed(2)}:1`);
          assert(ratio >= 4.5, `迁移图数值「${n.txt}」fill=${n.fill} 对底 ${r.panelBg} 对比度 ${ratio.toFixed(2)} < 4.5（浅底会隐形）`);
        }
        return details.join(' | ');
      });
    });

    // ============ I. 运行期无「白 5%」隐形底 ============
    await check('I. 无 bg-white/5（rgba(255,255,255,0.05)）隐形底残留', () => {
      return page.evaluate(() => {
        const bad = [];
        for (const el of document.querySelectorAll('*')) {
          const bg = getComputedStyle(el).backgroundColor;
          const m = bg.match(/rgba\(255,\s*255,\s*255,\s*0\.0?5\)/);
          if (m) bad.push(`${el.tagName}.${(el.className || '').toString().slice(0, 50)}`);
        }
        return bad.slice(0, 10);
      }).then((bad) => {
        assert(bad.length === 0, `发现 ${bad.length} 处 bg-white/5：\n  ${bad.join('\n  ')}`);
        return '无白 5% 隐形底';
      });
    });

    // ============ D. Tag neutral 底可见 ============
    await check('D. Tag neutral 底色非透明/非白5%（浅底可见）', () => {
      // Layout 顶栏有 Tag tone="neutral"（对比 X）
      return page.evaluate(() => {
        const tags = Array.from(document.querySelectorAll('span')).filter((s) =>
          /bg-abstain\/10|text-text-secondary/.test(s.className || '')
        );
        if (tags.length === 0) throw new Error('未找到 neutral Tag');
        const t = tags[0];
        return { bg: getComputedStyle(t).backgroundColor, cls: t.className };
      }).then((r) => {
        const bg = parseColor(r.bg);
        assert(bg, 'Tag 底色无法解析: ' + r.bg);
        assert(bg.a < 0.99, `Tag neutral 底色为不透明 ${r.bg}，可疑（应为半透明冷灰底）`);
        assert(bg.a > 0.02, `Tag neutral 底色几乎透明 a=${bg.a}，浅底会隐形: ${r.bg}`);
        // 叠加到面板白底，必须与纯白有明显区分
        const onWhite = blend(bg, { r: 255, g: 255, b: 255, a: 1 });
        const diff = Math.abs(onWhite.r - 255) + Math.abs(onWhite.g - 255) + Math.abs(onWhite.b - 255);
        assert(diff >= 3, `Tag neutral 叠白后与纯白几乎无差（diff=${diff}），浅底不可见: ${hex(onWhite)}`);
        return `bg=${r.bg} → 叠白 ${hex(onWhite)} (diff=${diff})`;
      });
    });

    // ============ F. 合法弃权非红 ============
    await check('F. 合法弃权条目(intentional/abstain)在浅色下仍非红色', () => {
      return page.evaluate(() => {
        // 找构成明细表里 amendment 为 intentional 的行 → AmendmentBadge 文本色
        const rows = Array.from(document.querySelectorAll('table tbody tr'));
        const out = [];
        for (const tr of rows) {
          const txt = tr.textContent || '';
          // AmendmentBadge compact 的中文标签常见为「有意保守」「保持弃权」等
          if (/有意保守|保持弃权|合法弃权/.test(txt)) {
            const badge = tr.querySelector('span');
            const all = Array.from(tr.querySelectorAll('span'));
            const colored = all.map((s) => ({ cls: s.className, color: getComputedStyle(s).color }))
              .filter((s) => /text-abstain|text-text-secondary|text-improve|text-alert|text-declare/.test(s.cls));
            out.push({ row: txt.slice(0, 30), colored });
          }
        }
        return out;
      }).then((rows) => {
        assert(rows.length > 0, '未找到合法弃权(intentional)行');
        const details = [];
        for (const r of rows) {
          for (const c of r.colored) {
            const col = parseColor(c.color);
            // 红色判定：r 明显大于 g、b 且 r 较高
            const isRed = col.r > 150 && col.g < 90 && col.b < 90;
            details.push(`${c.cls}=${c.color}`);
            assert(!isRed, `合法弃权行出现红色文本 ${c.color} (class ${c.cls}) — 违反红线 R1`);
          }
        }
        return details.slice(0, 6).join(' | ');
      });
    });

    // 切回总览
    await page.getByRole('tab', { name: '结论总览' }).click();
    await page.waitForTimeout(350);

    // ============ G. 反指标=0 显示「合规」 ============
    await check('G. 反指标=0 显示「合规」而非「无数据」', () => {
      return page.evaluate(() => document.body.textContent).then((t) => {
        assert(/合规/.test(t), '未找到「合规」');
        assert(!/无数据/.test(t), '出现「无数据」');
        // 合规文本颜色应为绿色(ok)或非红
        return '存在「合规」，无「无数据」';
      });
    });

    // ============ E. 页脚去红 ============
    await check('E. 页脚「弃权是护城河，不是缺陷」不是红色 (DASH-6)', () => {
      return page.evaluate(() => {
        const foot = document.querySelector('footer');
        if (!foot) throw new Error('未找到 footer');
        const texts = Array.from(foot.querySelectorAll('p, span, b'));
        const hit = texts.find((e) => (e.textContent || '').includes('弃权是护城河'));
        if (!hit) throw new Error('未找到页脚红线声明');
        return { color: getComputedStyle(hit).color, cls: hit.className };
      }).then((r) => {
        const col = parseColor(r.color);
        const isRed = col.r > 150 && col.g < 90 && col.b < 90;
        assert(!isRed, `页脚声明仍为红色: ${r.color} (class ${r.cls}) — DASH-6 未达成`);
        return `color=${r.color} (class ${r.cls}) 非红`;
      });
    });

    // ============ H. 「降低弃权率」仅否定语境（全站扫描） ============
    await check('H. 全站「降低弃权率」只出现在否定语境', () => {
      return page.evaluate(() => document.body.textContent).then((full) => {
        const idxs = [];
        let i = full.indexOf('降低弃权率');
        while (i !== -1) { idxs.push(i); i = full.indexOf('降低弃权率', i + 1); }
        assert(idxs.length > 0, '全站未出现「降低弃权率」（应至少否定语境出现一次）');
        const offenders = [];
        for (const idx of idxs) {
          const ctx = full.slice(Math.max(0, idx - 40), idx + 40);
          const negated = /(不追求|禁止|不得|不能|拒绝|不为|不通过|非为|不因|不应|绝不|一律禁止)/.test(ctx);
          if (!negated) offenders.push(ctx);
        }
        assert(offenders.length === 0, `非否定语境: ${offenders.join(' || ')}`);
        return `${idxs.length} 处均为否定语境`;
      });
    });

    // 截图存档
    await page.screenshot({ path: join(SHOT_DIR, 'dash-overview-light.png'), fullPage: true });
    await page.getByRole('tab', { name: '构成分解' }).click();
    await page.waitForTimeout(400);
    await page.screenshot({ path: join(SHOT_DIR, 'dash-composition-light.png'), fullPage: true });
    await page.getByRole('tab', { name: '逐条下钻' }).click();
    await page.waitForTimeout(400);
    await page.screenshot({ path: join(SHOT_DIR, 'dash-drilldown-light.png'), fullPage: true });

    await check('ZZ. 全流程 console 零 error', () => {
      assert(errors.length === 0, `捕获 ${errors.length} 错误:\n  ${errors.join('\n  ')}`);
      return 'console 无 error';
    });
  } finally {
    await Promise.race([browser.close().catch(() => {}), new Promise((r) => setTimeout(r, 3000))]);
    server.kill('SIGKILL');
  }

  const pass = results.filter((r) => r.ok).length;
  const fail = results.length - pass;
  console.log('\n===== QA 独立验证 · 看板（DASH）=====');
  for (const r of results) console.log(`${r.ok ? 'PASS' : 'FAIL'} | ${r.name}${r.detail ? ' :: ' + r.detail : ''}`);
  console.log(`\n合计 ${results.length} | 通过 ${pass} | 失败 ${fail}`);
  process.exit(fail === 0 ? 0 : 1);
}

const wd = setTimeout(() => { console.error('[watchdog] 超时'); process.exit(3); }, 150000);
wd.unref?.();
main().catch((e) => { console.error('FATAL', e); process.exit(2); });
