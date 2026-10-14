import { createRequire } from 'node:module';
import { spawn } from 'node:child_process';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { homedir } from 'node:os';
import { existsSync } from 'node:fs';
const require = createRequire(import.meta.url);
const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, '..');
const PW_CANDIDATES = [join(__dirname,'.pwtools/node_modules/playwright-core'), join(homedir(),'pwqa/node_modules/playwright-core'), join(root,'node_modules/playwright-core')];
const pwPath = PW_CANDIDATES.find(p => existsSync(join(p,'package.json')));
const { chromium } = require(pwPath);
const CHROME = 'C:/Users/xy/AppData/Local/ms-playwright/chromium_headless_shell-1228/chrome-headless-shell-win64/chrome-headless-shell.exe';
const PORT = 4321;
const server = spawn(process.execPath, [join(__dirname,'static-server.mjs'), String(PORT)], { cwd: root, stdio: 'ignore' });
await new Promise(r=>setTimeout(r,1500));
const browser = await chromium.launch({ executablePath: existsSync(CHROME)?CHROME:undefined });
const page = await browser.newPage({ viewport:{width:1080,height:720}, deviceScaleFactor:2 });
await page.goto(`http://127.0.0.1:${PORT}/`, { waitUntil:'networkidle' });
for (const [label,file] of [['构成分解','shot-composition.png'],['根因树','shot-rootcause.png'],['逐条下钻','shot-drilldown.png']]) {
  await page.getByText(label,{exact:true}).first().click();
  await page.waitForTimeout(700);
  await page.screenshot({ path: join(__dirname,file), fullPage:true });
  console.log('captured', file);
}
await browser.close(); server.kill();
