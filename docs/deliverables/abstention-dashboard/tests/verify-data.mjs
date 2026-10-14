/**
 * 独立数据契约校验脚本（不依赖应用内部 validateDiagnosis）。
 *
 * 依据：架构文档 §3.1 JSON Schema `abstention-diagnosis.v1` + §7 各任务验收要点 + PRD 关键数字。
 * 运行：node tests/verify-data.mjs
 * 退出码：0 = 全部通过；1 = 存在失败断言。
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const DATA_PATH = resolve(__dirname, '../src/data/diagnosis.sample.json');
const data = JSON.parse(readFileSync(DATA_PATH, 'utf-8'));

/** 简易断言收集器。 */
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
  if (actual !== expected) {
    throw new Error(`${label}: 期望 ${expected}，实际 ${actual}`);
  }
  return `${label}=${actual}`;
}

const X_CODES = ['X1', 'X2', 'X3', 'X4', 'X5', 'X6'];
const GAP_KINDS = [
  'missing_report',
  'missing_detail',
  'definition',
  'external',
  'mention',
  'premise',
  'conflict',
];
const AMENDABILITY = [
  'amendable',
  'amendable_partial',
  'needs_user_declaration',
  'intentional',
];
const FAKE_RISK = ['无', '低', '中', '高'];

// —— A1：每个 version 的 X1+..+X6 === abstained ——
check('A1. 每个 version 六码合计 === abstained', () => {
  const bad = [];
  for (const v of data.versions) {
    const sum = X_CODES.reduce((acc, c) => acc + (v.x_codes[c] ?? 0), 0);
    if (sum !== v.abstained) {
      bad.push(`${v.id}: 六码合计 ${sum} ≠ abstained ${v.abstained}`);
    }
  }
  assert(bad.length === 0, bad.join(' | '));
  return `${data.versions.length} 个版本全部自洽`;
});

check('A1b. stable-04 = 163+42+108 = 313', () => {
  const v = data.versions.find((x) => x.id === 'stable-04');
  assert(v, '找不到 stable-04');
  eq(v.x_codes.X3, 163, 'stable-04.X3');
  eq(v.x_codes.X2, 42, 'stable-04.X2');
  eq(v.x_codes.X6, 108, 'stable-04.X6');
  eq(v.abstained, 313, 'stable-04.abstained');
  return '163+42+108=313 ✓';
});

check('A1c. stable-08 = 191+58+59 = 308', () => {
  const v = data.versions.find((x) => x.id === 'stable-08');
  assert(v, '找不到 stable-08');
  eq(v.x_codes.X3, 191, 'stable-08.X3');
  eq(v.x_codes.X2, 58, 'stable-08.X2');
  eq(v.x_codes.X6, 59, 'stable-08.X6');
  eq(v.abstained, 308, 'stable-08.abstained');
  return '191+58+59=308 ✓';
});

check('A1d. verified+violated+abstained === total_candidates（各版本）', () => {
  const bad = [];
  for (const v of data.versions) {
    const total = v.verified + v.violated + v.abstained;
    if (total !== v.total_candidates) {
      bad.push(`${v.id}: ${total} ≠ ${v.total_candidates}`);
    }
  }
  assert(bad.length === 0, bad.join(' | '));
  return '全部版本守恒 ✓';
});

// —— A2：composition 计数合计 = 143；EVR 一致 ——
check('A2. composition 计数合计 === 143', () => {
  const sum = data.composition.reduce((acc, c) => acc + c.count, 0);
  return eq(sum, 143, 'composition 合计');
});

check('A2b. 北极星 current_value 与 (verified+violated)/total_candidates 一致（stable-04）', () => {
  const v = data.versions.find((x) => x.id === data.meta.primary_version);
  assert(v, `找不到主版本 ${data.meta.primary_version}`);
  const evr = (v.verified + v.violated) / v.total_candidates;
  // 数据里 current_value=0.019 为四舍五入值，容差 0.001
  const diff = Math.abs(evr - data.metrics.north_star.current_value);
  assert(
    diff <= 0.001,
    `(verified+violated)/total = ${evr.toFixed(4)} 与 current_value ${data.metrics.north_star.current_value} 偏差 ${diff.toFixed(4)} > 0.001`
  );
  return `EVR=${evr.toFixed(4)} ≈ current_value ${data.metrics.north_star.current_value} (6/319)`;
});

// —— A3：items 字段完整且枚举合法 ——
check('A3. items 每条字段完整且枚举合法', () => {
  assert(Array.isArray(data.items) && data.items.length > 0, 'items 为空');
  const errors = [];
  data.items.forEach((it, idx) => {
    const tag = `items[${idx}](${it.id ?? '?'})`;
    if (typeof it.id !== 'string') errors.push(`${tag} id 非法`);
    if (typeof it.page !== 'number') errors.push(`${tag} page 非法`);
    if (typeof it.seq !== 'number') errors.push(`${tag} seq 非法`);
    if (typeof it.raw_label !== 'string') errors.push(`${tag} raw_label 非法`);
    if (!X_CODES.includes(it.x_code)) errors.push(`${tag} x_code=${it.x_code} 非法`);
    if (!GAP_KINDS.includes(it.gap_kind)) errors.push(`${tag} gap_kind=${it.gap_kind} 非法`);
    if (typeof it.cause !== 'string') errors.push(`${tag} cause 非法`);
    if (!AMENDABILITY.includes(it.amendability))
      errors.push(`${tag} amendability=${it.amendability} 非法`);
    if (typeof it.evidence_ref !== 'string') errors.push(`${tag} evidence_ref 非法`);
    if (it.requirement_ref !== null && typeof it.requirement_ref !== 'string')
      errors.push(`${tag} requirement_ref 非法`);
  });
  assert(errors.length === 0, errors.slice(0, 8).join(' | '));
  return `${data.items.length} 条 items 全部合法`;
});

// —— A4：requirements 引用真实存在 ——
check('A4. requirements.linked_causes 引用真实 cause', () => {
  const causeIds = new Set(data.composition.map((c) => c.id));
  const bad = [];
  for (const r of data.requirements) {
    for (const c of r.linked_causes) {
      if (!causeIds.has(c)) bad.push(`${r.id}→${c}`);
    }
  }
  assert(bad.length === 0, `引用不存在的 cause: ${bad.join(', ')}`);
  return `${data.requirements.length} 条需求的 linked_causes 全部有效`;
});

check('A4b. requirements.linked_root_leaves 引用真实叶子', () => {
  const leafIds = new Set();
  (function collect(node) {
    if (!node.children || node.children.length === 0) {
      leafIds.add(node.id);
    } else {
      node.children.forEach(collect);
    }
  })(data.root_cause_tree);
  const bad = [];
  for (const r of data.requirements) {
    for (const l of r.linked_root_leaves) {
      if (!leafIds.has(l)) bad.push(`${r.id}→${l}`);
    }
  }
  assert(bad.length === 0, `引用不存在的叶子: ${bad.join(', ')}`);
  return `叶子集合 [${[...leafIds].join(',')}]；全部引用有效`;
});

check('A4c. requirements[].fake_risk 枚举合法', () => {
  const bad = data.requirements.filter((r) => !FAKE_RISK.includes(r.fake_risk));
  assert(bad.length === 0, bad.map((r) => `${r.id}:${r.fake_risk}`).join(', '));
  return `${data.requirements.length} 条 fake_risk 合法`;
});

// —— A5：root_cause_tree 计数一致性（架构师拍板口径，第 2 轮权威判据）——
// 权威约定：整树对齐主版本 stable-04，root = A + B + C 严格自洽；
//           叶子为逐因精确值（子集量化），不做等和断言。
check('A5-1（硬）root.count === Σ 一级分支 count（313）', () => {
  const root = data.root_cause_tree;
  const sum = root.children.reduce((acc, c) => acc + c.count, 0);
  if (sum !== root.count) {
    throw new Error(`root.count=${root.count} ≠ Σ(A,B,C)=${sum}（相差 ${root.count - sum}）`);
  }
  return `root=${root.count} === Σ(A,B,C)=${sum}`;
});

check('A5-2（硬）root.count === 313（主版本 stable-04 弃权数）', () => {
  eq(data.root_cause_tree.count, 313, 'root.count');
  const v4 = data.versions.find((v) => v.id === 'stable-04');
  if (v4.abstained !== 313) throw new Error(`stable-04.abstained=${v4.abstained} ≠ 313`);
  return 'root=313=stable-04.abstained';
});

check('A5-3（硬，防跨版本混用复发）A/B/C.count === stable-04 的 X3/X6/X2', () => {
  const v4 = data.versions.find((v) => v.id === 'stable-04');
  const map = { A: 'X3', B: 'X6', C: 'X2' };
  const bad = [];
  for (const b of data.root_cause_tree.children) {
    const code = map[b.id];
    if (!code) { bad.push(`${b.id}: 未知分支`); continue; }
    if (b.count !== v4.x_codes[code]) {
      bad.push(`${b.id}(${code}): ${b.count} ≠ stable-04.${code}=${v4.x_codes[code]}`);
    }
  }
  assert(bad.length === 0, bad.join(' | '));
  return 'A=163/B=108/C=42 与 stable-04 三码一致';
});

check('A5-4（软）root 带 aggregation_note 说明叶子子集量化', () => {
  const note = data.root_cause_tree.aggregation_note;
  if (typeof note !== 'string' || note.length === 0) {
    throw new Error('root_cause_tree 缺少 aggregation_note（架构 §3.2 已新增该契约字段）');
  }
  return `aggregation_note 存在（${note.slice(0, 24)}…）`;
});

check('A5-5（信息性）叶子合计（子集量化，不强制等和）', () => {
  let leafSum = 0;
  let leafCount = 0;
  (function walk(node) {
    if (!node.children || node.children.length === 0) {
      leafSum += node.count;
      leafCount += 1;
    } else {
      node.children.forEach(walk);
    }
  })(data.root_cause_tree);
  return `${leafCount} 个叶子，合计 ${leafSum}（预期 116，子集量化）`;
});

// —— 附加：x_code_defs / gap_kind_defs 完整性 ——
check('A6. x_code_defs 覆盖 6 码且字段合法', () => {
  const codes = data.x_code_defs.map((d) => d.code).sort();
  eq(JSON.stringify(codes), JSON.stringify([...X_CODES].sort()), 'x_code_defs 覆盖码');
  const bad = data.x_code_defs.filter((d) => !AMENDABILITY.includes(d.amendability));
  assert(bad.length === 0, bad.map((d) => d.code).join(','));
  return '6 码完整，amendability 合法';
});

check('A7. composition[].amendability / x_code / gap_kind 合法', () => {
  const errors = [];
  data.composition.forEach((c) => {
    if (!AMENDABILITY.includes(c.amendability)) errors.push(`${c.id} amendability=${c.amendability}`);
    if (!X_CODES.includes(c.x_code)) errors.push(`${c.id} x_code=${c.x_code}`);
    if (!GAP_KINDS.includes(c.gap_kind)) errors.push(`${c.id} gap_kind=${c.gap_kind}`);
  });
  assert(errors.length === 0, errors.join(', '));
  return `${data.composition.length} 条构成条目合法`;
});

// —— 输出 ——
const pass = results.filter((r) => r.ok).length;
const fail = results.length - pass;
console.log('\n===== 数据契约校验（A 组）=====');
for (const r of results) {
  console.log(`${r.ok ? 'PASS' : 'FAIL'} | ${r.name}${r.detail ? ' :: ' + r.detail : ''}`);
}
console.log(`\n合计 ${results.length} 项 | 通过 ${pass} | 失败 ${fail}\n`);

process.exit(fail === 0 ? 0 : 1);
