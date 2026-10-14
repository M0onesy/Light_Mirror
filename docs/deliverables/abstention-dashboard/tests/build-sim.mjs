import { build } from 'vite';
import { fileURLToPath } from 'node:url';
import { resolve, dirname } from 'node:path';
const __dirname = dirname(fileURLToPath(import.meta.url));
await build({
  configFile: false,
  root: resolve(__dirname, '..'),
  logLevel: 'error',
  resolve: { alias: { '@': resolve(__dirname, '../src') } },
  build: {
    outDir: resolve(__dirname, '.build'),
    emptyOutDir: true,
    lib: {
      entry: resolve(__dirname, 'sim-entry.ts'),
      formats: ['es'],
      fileName: () => 'sim.mjs',
    },
    rollupOptions: { external: ['react', 'react-dom', 'react/jsx-runtime'] },
    minify: false,
  },
});
console.log('BUILD OK');
