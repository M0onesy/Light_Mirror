import pw from 'file:///C:/Users/xy/.workbuddy/binaries/node/workspace/node_modules/playwright/index.js';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';
const { chromium } = pw;

// 默认指向仓库内的交互工具；可用 argv[2] 覆盖。
const target = resolve(process.argv[2] || 'docs/deliverables/scope-clarifier/index.html');
const url = pathToFileURL(target).href;
const browser = await chromium.launch({ channel: process.env.PWBROWSER || 'msedge' });
const page = await browser.newPage({ viewport: { width: 1320, height: 1300 } });
const errors = [];
page.on('console', m => { if (m.type() === 'error') errors.push('CONSOLE: ' + m.text()); });
page.on('pageerror', e => errors.push('PAGEERROR: ' + e.message));

await page.goto(url, { waitUntil: 'load' });
await page.waitForTimeout(600);

const r = {};
// 1) 默认态：生成命令 -> 闸门应出现且未勾选
await page.locator('#generateBtn').click();
await page.waitForTimeout(300);
r.gateVisibleDefault = await page.locator('#gateBlock').isVisible();
r.gateCheckedDefault = await page.locator('#gateConfirm').isChecked();
r.cmdHasWarningDefault = (await page.locator('#outputCmd').innerText()).includes('尚未逐组人工核对');

// 2) 勾选确认 -> 闸门转 ok，注释口吻改变
await page.locator('#gateConfirm').check();
await page.waitForTimeout(300);
r.gateOkAfterCheck = (await page.locator('#gateBlock').getAttribute('class')).includes('ok');
r.cmdHasConfirmedAfterCheck = (await page.locator('#outputCmd').innerText()).includes('已勾选确认已核对原文');

// 3) 全部改回保持弃权 -> 闸门隐藏、无需重算
await page.locator('[data-bulk="keep"]').click();
await page.waitForTimeout(300);
await page.locator('#generateBtn').click();
await page.waitForTimeout(300);
r.gateVisibleAfterKeep = await page.locator('#gateBlock').isVisible();
r.titleAfterKeep = await page.locator('#outputTitle').innerText();

// 4) 恢复：全部按合并
await page.locator('[data-bulk="合并"]').click();
await page.waitForTimeout(200);
r.checkedAfterBulkConsolidated = await page.evaluate(() => {
  const c = {};
  document.querySelectorAll('.group input[type=radio]:checked').forEach(x => c[x.value] = (c[x.value]||0)+1);
  return c;
});
r.sugBadgesAfterBulk = await page.locator('.tag.sug').count();

r.errors = errors;
console.log(JSON.stringify(r, null, 2));
await browser.close();
