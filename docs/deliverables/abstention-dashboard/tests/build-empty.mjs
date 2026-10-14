/** 构建一个 items=[] 与 denominator_scope=main_scope 的测试变体 dist，用于 E14/E15 边界测试。 */
import { build } from 'vite';
import { fileURLToPath } from 'node:url';
import { resolve, dirname, join } from 'node:path';
import { readFileSync, writeFileSync, mkdirSync, rmSync, cpSync } from 'node:fs';
import react from '@vitejs/plugin-react';

const __dirname = dirname(fileURLToPath(import.meta.url));
const projRoot = resolve(__dirname, '..');
const workRoot = join(__dirname, '.empty-work');

// 1) 复制工程源到临时目录（不复制 node_modules —— vite/rollup 会向上查找项目根 node_modules）
rmSync(workRoot, { recursive: true, force: true });
mkdirSync(workRoot, { recursive: true });
for (const entry of ['src', 'index.html']) {
  cpSync(join(projRoot, entry), join(workRoot, entry), { recursive: true });
}

// 2) 改写数据：items=[] 且 denominator_scope=main_scope
const dataPath = join(workRoot, 'src/data/diagnosis.sample.json');
const d = JSON.parse(readFileSync(dataPath, 'utf-8'));
d.items = [];
d.meta.denominator_scope = 'main_scope';
writeFileSync(dataPath, JSON.stringify(d, null, 2));

// 3) 复制配置
for (const f of ['tailwind.config.ts', 'postcss.config.js', 'tsconfig.json']) {
  cpSync(join(projRoot, f), join(workRoot, f));
}

// 4) 构建（跳过 tsc，直接 vite build）
await build({
  configFile: false,
  root: workRoot,
  logLevel: 'error',
  plugins: [react()],
  resolve: { alias: { '@': join(workRoot, 'src') } },
  build: { outDir: join(workRoot, 'dist'), emptyOutDir: true, sourcemap: false, target: 'es2020' },
});
console.log('EMPTY BUILD OK ->', join(workRoot, 'dist'));
