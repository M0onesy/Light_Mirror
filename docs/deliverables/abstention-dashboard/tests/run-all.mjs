/**
 * 全量测试运行器 —— 依次执行 A(数据契约) / B(模拟器逻辑) / C(类型与构建) / D+E(浏览器)。
 * 用法：node tests/run-all.mjs
 */
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const projRoot = resolve(__dirname, '..');

function run(title, cmd, args, cwd = projRoot, timeoutMs = 180000) {
  console.log(`\n############ ${title} ############`);
  const r = spawnSync(cmd, args, { cwd, stdio: 'inherit', timeout: timeoutMs, shell: process.platform === 'win32' });
  const ok = r.status === 0;
  console.log(`---- ${title} => ${ok ? 'PASS' : 'FAIL'} (exit ${r.status}) ----`);
  return ok;
}

const summary = [];
summary.push(['C1. tsc --noEmit', run('C · 类型检查', 'npx', ['tsc', '--noEmit'])]);
summary.push(['C2. build-sim (打包纯逻辑)', run('B · 构建 sim 产物', 'node', [join(__dirname, 'build-sim.mjs')])]);
summary.push(['A. 数据契约', run('A · 数据契约校验', 'node', [join(__dirname, 'verify-data.mjs')])]);
summary.push(['B. 模拟器逻辑', run('B · 模拟器逻辑测试', 'node', [join(__dirname, 'verify-simulate.mjs')])]);
summary.push(['C3. npm run build', run('C · 生产构建', 'npm', ['run', 'build'])]);
summary.push(['D. 浏览器渲染', run('D · 浏览器渲染测试', 'node', [join(__dirname, 'verify-browser.mjs')])]);
summary.push(['E. 边界情况', run('E · 边界情况测试', 'node', [join(__dirname, 'verify-edge.mjs')])]);

console.log('\n================ 汇总 ================');
let allOk = true;
for (const [name, ok] of summary) {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}`);
  if (!ok) allOk = false;
}
console.log(`\n总体：${allOk ? 'ALL PASS' : 'HAS FAILURES'}`);
process.exit(allOk ? 0 : 1);
