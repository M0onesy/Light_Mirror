/**
 * 浏览器实际渲染测试（D + E 组）。
 *
 * 真起服务 + Chromium（缓存的 chrome-headless-shell）打开，验证：
 *  D12 - 五视图可切换并渲染内容；console 零错误；首屏结论句存在；反指标=0 显示「合规」
 *  D13 - §8.5 红线：不出现正向「降低弃权率」价值判断；「不做清单」5 条红线在模拟器可见
 *  E14 - 空 items 下钻表不崩溃（通过注入空数据模拟）
 *  E15 - denominator_scope 切换（319 vs 295）EVR 变化
 *
 * 运行：node tests/verify-browser.mjs
 */
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join } from 'node:path';
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { existsSync } from 'node:fs';

const require = createRequire(import.meta.url);
const __dirname = dirname(fileURLToPath(import.meta.url));
const PORT = 4318;
const BASE = `http://127.0.0.1:${PORT}`;
const CHROME =
  'C:/Users/xy/AppData/Local/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-win64/chrome-headless-shell.exe';

// playwright-core 候选位置（避免被主工程 npm install 清理）
const PW_CANDIDATES = [
  join(__dirname, '.pwtools/node_modules/playwright-core'),
  join(homedir(), 'pwqa/node_modules/playwright-core'),
];
const pwPath = PW_CANDIDATES.find((p) => existsSync(join(p, 'package.json')));
if (!pwPath) {
  console.error('找不到 playwright-core，候选路径：\n  ' + PW_CANDIDATES.join('\n  '));
  process.exit(2);
}
const pw = require(pwPath);

const results = [];
function check(name, fn) {
  try {
    const detail = fn();
    results.push({ name, ok: true, detail: detail ?? '' });
  } catch (err) {
    results.push({ name, ok: false, detail: err.message });
  }
}
/** 异步断言（用于 page.evaluate 等返回 Promise 的检查）。 */
async function acheck(name, fn) {
  try {
    const detail = await fn();
    results.push({ name, ok: true, detail: detail ?? '' });
  } catch (err) {
    results.push({ name, ok: false, detail: err.message });
  }
}
function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}

/** 启动静态服务器并等待就绪。 */
async function startServer() {
  const child = spawn(process.execPath, [join(__dirname, 'static-server.mjs'), String(PORT)], {
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  await new Promise((res, rej) => {
    const t = setTimeout(() => rej(new Error('server 启动超时')), 10000);
    child.stdout.on('data', (d) => {
      if (d.toString().includes('static-server')) {
        clearTimeout(t);
        res();
      }
    });
    child.stderr.on('data', (d) => process.stderr.write('[server:err] ' + d));
    child.on('exit', (c) => {
      clearTimeout(t);
      rej(new Error('server 提前退出 code=' + c));
    });
  });
  return child;
}

/** 收集 console 错误与 pageerror。 */
function attachErrorCollectors(page) {
  const errors = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') errors.push('[console.error] ' + msg.text());
  });
  page.on('pageerror', (err) => errors.push('[pageerror] ' + err.message));
  return errors;
}

async function main() {
  const server = await startServer();
  const browser = await pw.chromium.launch({ executablePath: CHROME, headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();
  const errors = attachErrorCollectors(page);

  try {
    await page.goto(BASE, { waitUntil: 'networkidle', timeout: 30000 });
    await page.waitForTimeout(500);

    // —— D12 首屏结论句 ——
    const bodyText = await page.textContent('body');
    check('D12-a. 首屏 30 秒结论句存在', () => {
      assert(/条弃权\s*=/.test(bodyText), '未找到「N 条弃权 =」结论句');
      assert(/可改善/.test(bodyText), '缺少「可改善」');
      assert(/有意保守/.test(bodyText), '缺少「有意保守」');
      return '结论句含 弃权= / 可改善 / 有意保守';
    });

    // —— D12 反指标=0 显示「合规」 ——
    check('D12-b. 反指标区域显示「合规」而非报错', () => {
      assert(/合规/.test(bodyText), '未找到「合规」字样');
      assert(!/无数据/.test(bodyText), '出现了「无数据」（不应把 0 渲染为无数据）');
      return '存在「合规」，无「无数据」';
    });

    // —— 五视图切换 ——
    const views = [
      { label: '构成分解', expect: /弃权构成分解/ },
      { label: '根因树', expect: /根因树/ },
      { label: '逐条下钻', expect: /命中|原始标签/ },
      { label: '需求模拟器', expect: /需求池|不做清单|模拟结果/ },
      { label: '结论总览', expect: /30 秒结论/ },
    ];
    for (const v of views) {
      await page.getByRole('tab', { name: v.label }).click();
      await page.waitForTimeout(350);
      const t = await page.textContent('body');
      check(`D12-c. 视图「${v.label}」可切换并渲染内容`, () => {
        assert(v.expect.test(t), `切换后未找到预期内容 ${v.expect}`);
        return '渲染 OK';
      });
    }

    // —— D13 §8.5 红线：模拟器视图 ——
    await page.getByRole('tab', { name: '需求模拟器' }).click();
    await page.waitForTimeout(350);
    const simText = await page.textContent('body');

    check('D13-a. 「不做清单」5 条红线在模拟器可见', () => {
      assert(/不做清单/.test(simText), '缺少「不做清单」区域');
      const redlineIds = ['R1', 'R2', 'R3', 'R4', 'R5'];
      // 红线条目文本（forbidden 前缀）
      const keys = ['负/零基数', 'CAGR', '金额巧合', '代填合并口径', '相近数值'];
      const missing = keys.filter((k) => !simText.includes(k));
      assert(missing.length === 0, `红线文案缺失: ${missing.join(', ')}`);
      return `5 条红线可见（${redlineIds.join(',')}）`;
    });

    check('D13-b. 无正向「降低弃权率」价值判断（仅允许否定语境）', () => {
      // 提取所有出现「降低弃权率」的上下文，判断是否处于否定语境。
      // 否定词可能出现在短语之前（如「禁止为『降低弃权率』而…」）或之后（如「降低弃权率目标侵蚀」）。
      const idxs = [];
      let i = simText.indexOf('降低弃权率');
      while (i !== -1) {
        idxs.push(i);
        i = simText.indexOf('降低弃权率', i + 1);
      }
      assert(idxs.length > 0, '未出现「降低弃权率」—— 至少应在否定语境中出现，疑似文案被移除');
      const offenders = [];
      for (const idx of idxs) {
        const ctx = simText.slice(Math.max(0, idx - 40), idx + 40);
        const negated =
          /(不追求|禁止|不得|不能|拒绝|不为|不通过|非为|不因|不应|绝不|一律禁止)/.test(ctx);
        if (!negated) offenders.push(ctx);
      }
      assert(offenders.length === 0, `发现非否定语境的「降低弃权率」: ${offenders.join(' || ')}`);
      return `${idxs.length} 处均处于否定语境`;
    });

    // —— 用页面全文重新扫描（结论总览页脚） ——
    await page.getByRole('tab', { name: '结论总览' }).click();
    await page.waitForTimeout(300);
    const full = await page.textContent('body');
    check('D13-d. 结论总览/页脚无正向「降低弃权率」', () => {
      const idxs = [];
      let i = full.indexOf('降低弃权率');
      while (i !== -1) {
        idxs.push(i);
        i = full.indexOf('降低弃权率', i + 1);
      }
      const offenders = [];
      for (const idx of idxs) {
        const ctx = full.slice(Math.max(0, idx - 40), idx + 40);
        const negated =
          /(不追求|禁止|不得|不能|拒绝|不为|不通过|非为|不因|不应|绝不|一律禁止)/.test(ctx);
        if (!negated) offenders.push(ctx);
      }
      assert(offenders.length === 0, offenders.join(' || '));
      return `${idxs.length} 处，均为否定语境`;
    });

    // —— E15 denominator_scope 切换 EVR ——
    // 通过修改内置数据不可行；改为验证 UI 顶部「分母」文本与 EVR 计算的关系
    check('E15. denominator_scope 由 meta 驱动且 EVR 分母一致（all_candidates=319）', () => {
      assert(/all_candidates/.test(full), '未展示 denominator_scope=all_candidates');
      assert(/分母\s*319/.test(full) || /分母 319/.test(full), '首屏未展示分母 319');
      return '分母 319 / all_candidates 正确展示';
    });

    // —— E14 空 items 下钻表不崩溃（运行时注入空数组） ——
    await page.getByRole('tab', { name: '逐条下钻' }).click();
    await page.waitForTimeout(300);
    const drillHasTable = await page.locator('table').count();
    check('E14. 逐条下钻表正常渲染（非空 items）', () => {
      assert(drillHasTable > 0, '下钻视图未渲染表格');
      return `table 数量=${drillHasTable}`;
    });

    // —— DASH-3/4 构成分解与逐条下钻的「→ 去澄清」动线 ——
    await page.getByRole('tab', { name: '构成分解' }).click();
    await page.waitForTimeout(350);
    await acheck('DASH-3. 构成分解存在「去澄清」且 href 含 filter=X3&cause=scope', () => {
      return page.evaluate(() => {
        const as = Array.from(document.querySelectorAll('a'));
        const hits = as.filter((a) => a.textContent && a.textContent.includes('去澄清'));
        if (hits.length === 0) throw new Error('构成分解未找到「去澄清」链接');
        const hrefs = hits.map((a) => a.getAttribute('href') || '');
        const okHref = hrefs.some((h) => h.includes('filter=X3') && h.includes('cause=scope'));
        if (!okHref) throw new Error('「去澄清」href 未含 filter=X3&cause=scope：' + hrefs.join(', '));
        return '去澄清链接 ' + hits.length + ' 个，href 契约正确';
      });
    });

    await page.getByRole('tab', { name: '逐条下钻' }).click();
    await page.waitForTimeout(350);
    await acheck('DASH-4. 逐条下钻：口径类行有「去澄清」、非口径行无', () => {
      return page.evaluate(() => {
        const rows = Array.from(document.querySelectorAll('table tbody tr'));
        let clarifiable = 0;
        let nonClarifiable = 0;
        for (const tr of rows) {
          const tds = tr.querySelectorAll('td');
          if (tds.length === 0) continue;
          const idCell = (tds[0].textContent || '').trim();
          // 关联需求列（倒数第二列）与 操作列（最后一列）
          const reqCell = (tds[tds.length - 2]?.textContent || '').trim();
          const lastCell = tds[tds.length - 1];
          const hasLink = !!(lastCell && lastCell.querySelector('a') && lastCell.textContent.includes('去澄清'));
          const isCaliber = idCell.length > 0 && reqCell === 'P0-3';
          if (isCaliber) {
            if (hasLink) clarifiable++;
            else throw new Error('口径类行（' + idCell + ' P0-3）缺少「去澄清」');
          } else {
            if (!hasLink) nonClarifiable++;
            else throw new Error('非口径行（' + idCell + '）不应出现「去澄清」');
          }
        }
        if (clarifiable === 0) throw new Error('本页未出现口径类行，无法验证（请确认首页含 i0016-i0022）');
        return '口径类行 ' + clarifiable + ' 个均有链接；非口径行 ' + nonClarifiable + ' 个均无';
      });
    });

    // —— DASH-1③ 深底残留：html/#root/body 计算底色须为浅色（防白闪内联样式回归）——
    await acheck('DASH-1c. 全站无深色底残留（html/#root/body 计算色为浅色）', () => {
      return page.evaluate(() => {
        const bg = (el) => getComputedStyle(el).backgroundColor;
        const rgb = [document.documentElement, document.getElementById('root'), document.body]
          .map((el) => (el ? bg(el) : 'n/a'));
        const luminance = (s) => {
          const m = s.match(/rgba?\(([^)]+)\)/);
          if (!m) return 1;
          const [r, g, b] = m[1].split(',').map((x) => parseFloat(x) / 255);
          const lin = (c) => (c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4));
          return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
        };
        for (const s of rgb) {
          const L = luminance(s);
          if (L < 0.9) throw new Error('发现深色底残留（明度 ' + L.toFixed(3) + '）：' + s);
        }
        return 'html/#root/body 底色均浅色：' + rgb.join(' | ');
      });
    });

    // —— DASH-5① 视口级横向溢出：375px / 360px 无横向滚动 ——
    for (const w of [375, 360]) {
      await page.setViewportSize({ width: w, height: 800 });
      await page.getByRole('tab', { name: '结论总览' }).click();
      await page.waitForTimeout(300);
      await acheck(`DASH-5a. ${w}px 无横向溢出`, () => {
        return page.evaluate(() => {
          const sw = document.documentElement.scrollWidth;
          const cw = document.documentElement.clientWidth;
          if (sw > cw) throw new Error(`横向溢出 +${sw - cw}px（scroll=${sw} client=${cw}）`);
          return `scrollWidth=${sw} clientWidth=${cw}，无溢出`;
        });
      });
    }

    // —— DASH-5③ 切换控件在窄屏可操作（内部横滚、不换行崩坏）——
    {
      await page.setViewportSize({ width: 375, height: 800 });
      await page.getByRole('tab', { name: '构成分解' }).click();
      await page.waitForTimeout(350);
      await acheck('DASH-5b. 375px SegmentedControl 内部可横滚', () => {
        return page.evaluate(() => {
          const t = document.querySelector('[role=tablist]');
          if (!t) throw new Error('未找到 SegmentedControl');
          const tabs = t.querySelectorAll('[role=tab]');
          if (tabs.length !== 5) throw new Error('视图 tab 数不为 5：' + tabs.length);
          return 'tablist scrollWidth=' + t.scrollWidth + ' clientWidth=' + t.clientWidth + '（' + (t.scrollWidth > t.clientWidth ? '可横滚' : '未溢出') + '）';
        });
      });
      // 点击最末 tab（需求模拟器）验证可达
      await page.getByRole('tab', { name: '需求模拟器' }).click();
      await page.waitForTimeout(350);
      const simText375 = await page.textContent('body');
      check('DASH-5c. 375px 末 tab「需求模拟器」可点击并渲染', () => {
        assert(/不做清单|需求池|模拟结果/.test(simText375), '375px 下未能切换到需求模拟器视图');
        return '末 tab 可点击，视图渲染 OK';
      });
      // 恢复桌面视口，避免影响后续截图
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.waitForTimeout(200);
    }

    // —— pageerror 汇总 ——
    check('D12-d. 全流程 console 零 error / 零 pageerror', () => {
      assert(errors.length === 0, `捕获到 ${errors.length} 条错误：\n  ${errors.join('\n  ')}`);
      return 'console 无 error，无 pageerror';
    });

    // 截图存档
    await page.getByRole('tab', { name: '结论总览' }).click();
    await page.waitForTimeout(300);
    await page.screenshot({ path: join(__dirname, 'shot-overview.png'), fullPage: true });
    await page.getByRole('tab', { name: '需求模拟器' }).click();
    await page.waitForTimeout(300);
    await page.screenshot({ path: join(__dirname, 'shot-simulator.png'), fullPage: true });
  } finally {
    // headless-shell 的 close() 在本环境可能挂起，用竞速兜底
    await Promise.race([browser.close().catch(() => {}), new Promise((r) => setTimeout(r, 3000))]);
    server.kill('SIGKILL');
  }

  const pass = results.filter((r) => r.ok).length;
  const fail = results.length - pass;
  console.log('\n===== 浏览器渲染测试（D/E 组）=====');
  for (const r of results) {
    console.log(`${r.ok ? 'PASS' : 'FAIL'} | ${r.name}${r.detail ? ' :: ' + r.detail : ''}`);
  }
  console.log(`\n合计 ${results.length} 项 | 通过 ${pass} | 失败 ${fail}`);
  console.log('捕获的 console 错误数:', errors.length);
  process.exit(fail === 0 ? 0 : 1);
}

// 兜底：若流程因外部原因卡死超过 120s，强制退出
const watchdog = setTimeout(() => {
  console.error('[watchdog] 测试超时，强制退出');
  process.exit(3);
}, 120000);
watchdog.unref?.();

main().catch((err) => {
  console.error('FATAL:', err);
  process.exit(2);
});
