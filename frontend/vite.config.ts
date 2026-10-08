import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'

const local = (path: string) => fileURLToPath(new URL(path, import.meta.url))
export default defineConfig({
  base: './', plugins: [react()],
  resolve: { alias: {
    '@deepseek-ai/dsh-client-ui-primitives': local('./vendor/harness/packages/client/ui-primitives/src/index.ts'),
  } },
  define: {'process.env.DSH_CLIENT_TITLE': JSON.stringify('明镜金融 Agent')},
  server: {proxy: {'/api': 'http://127.0.0.1:8765'}},
  build: {target:'es2022', outDir:'../web/harness', emptyOutDir:true, sourcemap:false},
})
