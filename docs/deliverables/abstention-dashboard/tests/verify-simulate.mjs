/**
 * 模拟器逻辑测试（B 组）—— 导入**真实源码**打包产物 `tests/.build/sim.mjs`。
 *
 * 覆盖：去重、fake_risk 加权、红线告警、上限封顶。
 * 运行：node tests/verify-simulate.mjs
 * 前置：node tests/build-sim.mjs（由 verify-all 自动执行）
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { simulateDiagnosis, RISK_FACTOR } from './.build/sim.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const data = JSON.parse(
  readFileSync(resolve(__dirname, '../src/data/diagnosis.sample.json'), 'utf-8')
);

const results = [];
function check(name, fn) {
  try {
    const detail = fn();
    results.push({ name, ok: true, detail: detail ?? '' });
  } catch (err) {
    results.push({ name, ok: false, detail: err.message });
  }
}
function assert(cond, msg) {
  if (!cond) throw new Error(msg);
}
function eq(actual, expected, label) {
  if (actual !== expected) throw new Error(`${label}: 期望 ${expected}，实际 ${actual}`);
  return `${label}=${actual}`;
}

const primary = data.versions.find((v) => v.id === data.meta.primary_version);
const base = { composition: data.composition, requirements: data.requirements, baseSnapshot: primary, denominator: primary.total_candidates };
const byId = (id) => data.requirements.find((r) => r.id === id);

// —— B7：去重 ——
check('B7-a. 勾选 P0-1(c01,65) + P1-2(c04,40) → 救回 105', () => {
  const r = simulateDiagnosis({ ...base, selected: ['P0-1', 'P1-2'] });
  // c01=65, c04=9；P1-2 rescue_estimate=40 > linkedSum(c04=9) → 增量 31 → 65+9+31=105
  return eq(r.rescued, 105, 'rescued');
});

check('B7-b. 两条需求指向同一 cause 时去重（不重复计数）', () => {
  // 构造两条需求都链接 c01（65）
  const dupReqs = [
    { ...byId('P0-1'), id: 'D1', rescue_estimate: 65, linked_causes: ['c01'] },
    { ...byId('P0-1'), id: 'D2', rescue_estimate: 65, linked_causes: ['c01'] },
  ];
  const r = simulateDiagnosis({ ...base, requirements: dupReqs, selected: ['D1', 'D2'] });
  // 二者都指向 c01：c01 只计一次（65），D1/D2 的 linkedSum 均=65=rescue，无增量 → 65
  return eq(r.rescued, 65, 'rescued(去重后)');
});

check('B7-c. 单条 P0-1 → 65', () => {
  const r = simulateDiagnosis({ ...base, selected: ['P0-1'] });
  return eq(r.rescued, 65, 'rescued');
});

check('B7-d. 空选择 → 0', () => {
  const r = simulateDiagnosis({ ...base, selected: [] });
  assert(r.rescued === 0, `rescued 应为 0，实际 ${r.rescued}`);
  eq(r.evr, (primary.verified + primary.violated) / primary.total_candidates, 'evr(基线)');
  return 'rescued=0';
});

check('B7-e. 救回不超过弃权总数上限', () => {
  // 勾选所有需求（rescue 合计远超 313）
  const allIds = data.requirements.map((r) => r.id);
  const r = simulateDiagnosis({ ...base, selected: allIds });
  assert(r.rescued <= primary.abstained, `rescued ${r.rescued} 超过 abstained ${primary.abstained}`);
  return `rescued=${r.rescued} ≤ ${primary.abstained}`;
});

// —— B8：fake_risk 加权 ——
check('B8. RISK_FACTOR 权重 无=0/低=0.1/中=0.3/高=0.6', () => {
  eq(RISK_FACTOR['无'], 0, '无');
  eq(RISK_FACTOR['低'], 0.1, '低');
  eq(RISK_FACTOR['中'], 0.3, '中');
  eq(RISK_FACTOR['高'], 0.6, '高');
  return '权重表正确';
});

check('B8b. 勾选 P0-1(65,低0.1) + P0-2(130,中0.3) → 风险加权 = 45.5', () => {
  const r = simulateDiagnosis({ ...base, selected: ['P0-1', 'P0-2'] });
  const expected = 65 * 0.1 + 130 * 0.3; // 6.5 + 39 = 45.5
  const diff = Math.abs(r.riskWeighted - expected);
  assert(diff < 1e-9, `风险加权期望 ${expected}，实际 ${r.riskWeighted}`);
  return `riskWeighted=${r.riskWeighted}`;
});

// —— B9：红线告警 ——
check('B9. 勾选 is_redline=true 需求触发红线告警', () => {
  const rlReq = { ...byId('P0-1'), id: 'RL', is_redline: true, fake_risk: '高', linked_causes: [] };
  const r = simulateDiagnosis({ ...base, requirements: [rlReq], selected: ['RL'] });
  assert(r.redlineTouched === true, 'redlineTouched 应为 true');
  return 'redlineTouched=true ✓';
});

check('B9b. 未勾选红线需求时 redlineTouched=false', () => {
  const r = simulateDiagnosis({ ...base, selected: ['P0-1'] });
  assert(r.redlineTouched === false, `redlineTouched 应为 false，实际 ${r.redlineTouched}`);
  return 'redlineTouched=false ✓';
});

// —— 附加：EVR 单调变化 ——
check('B10. 模拟后 EVR 高于基线（有效救回时）', () => {
  const baseEvr = (primary.verified + primary.violated) / primary.total_candidates;
  const r = simulateDiagnosis({ ...base, selected: ['P0-1', 'P0-2'] });
  assert(r.evr > baseEvr, `模拟 EVR ${r.evr} 应 > 基线 ${baseEvr}`);
  return `基线 ${baseEvr.toFixed(4)} → 模拟 ${r.evr.toFixed(4)}`;
});

const pass = results.filter((r) => r.ok).length;
const fail = results.length - pass;
console.log('\n===== 模拟器逻辑测试（B 组）=====');
for (const r of results) {
  console.log(`${r.ok ? 'PASS' : 'FAIL'} | ${r.name}${r.detail ? ' :: ' + r.detail : ''}`);
}
console.log(`\n合计 ${results.length} 项 | 通过 ${pass} | 失败 ${fail}\n`);
process.exit(fail === 0 ? 0 : 1);
