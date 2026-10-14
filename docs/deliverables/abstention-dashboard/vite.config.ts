import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { fileURLToPath, URL } from 'node:url';

/**
 * Vite 构建配置。
 * - 使用 React 插件（Babel）。
 * - 配置 `@/*` 路径别名，指向 `src/`，避免深层相对路径。
 * - JSON 导入为 Vite 原生支持，无需额外插件。
 */
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    outDir: 'dist',
    // 关闭自动清空 outDir：在部分受限环境中批量删除会被安全策略拦截，
    // 改为直接覆盖同名产物（内容哈希文件名，反复构建安全）。
    emptyOutDir: false,
    sourcemap: false,
    target: 'es2020',
  },
});
