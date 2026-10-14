/**
 * 极简静态文件服务器（用于托管 dist/ 做浏览器测试）。
 * 用法：node tests/static-server.mjs [port] [rootDir]
 * 监听 127.0.0.1，SPA fallback 到 index.html。
 */
import { createServer } from 'node:http';
import { readFile, stat } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { resolve, dirname, extname, join } from 'node:path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.argv[2] ?? 4317);
const ROOT = resolve(process.argv[3] ?? join(__dirname, '../dist'));

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
};

async function tryFile(path) {
  try {
    const s = await stat(path);
    if (s.isFile()) return path;
  } catch {
    /* ignore */
  }
  return null;
}

const server = createServer(async (req, res) => {
  try {
    const url = new URL(req.url, `http://127.0.0.1:${PORT}`);
    let pathname = decodeURIComponent(url.pathname);
    if (pathname === '/') pathname = '/index.html';
    let filePath = resolve(join(ROOT, pathname));
    if (!filePath.startsWith(ROOT)) {
      res.writeHead(403).end('forbidden');
      return;
    }
    let found = await tryFile(filePath);
    if (!found) {
      found = (await tryFile(filePath + '.html')) ?? (await tryFile(join(ROOT, 'index.html')));
    }
    if (!found) {
      res.writeHead(404).end('not found');
      return;
    }
    const body = await readFile(found);
    res.writeHead(200, { 'Content-Type': MIME[extname(found)] ?? 'application/octet-stream' });
    res.end(body);
  } catch (err) {
    res.writeHead(500).end('server error: ' + err.message);
  }
});

server.listen(PORT, '127.0.0.1', () => {
  console.log(`[static-server] http://127.0.0.1:${PORT} root=${ROOT}`);
});
