import { chromium } from 'file:///G:/openbridge/node_modules/playwright-core/index.mjs';

const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
});
const page = await browser.newPage({ viewport: { width: 1560, height: 980 } });
const errors = [];
page.on('pageerror', (e) => errors.push(String(e)));
await page.goto('http://localhost:5178/', { waitUntil: 'networkidle' });
await page.waitForTimeout(600);

// 1. click a device thumbnail
await page.locator('.device-card', { hasText: '2560×1600' }).click();
await page.waitForTimeout(400);
const stageInfo = await page.locator('.stage-info').innerText();
console.log('device ->', stageInfo.replace(/\n/g, ' | '));

// 2. select the notice card in the layer tree
await page.locator('.layer-name', { hasText: '通知卡片' }).click();
await page.waitForTimeout(300);
const title = await page.locator('.inspector-title').innerText();
const xVal = await page.locator('.inspector .num').first().inputValue();
console.log('selected ->', title, 'x =', xVal);

// 3. drag it on canvas
const canvas = page.locator('.stage-canvas');
const box = await canvas.boundingBox();
const scale = box.width / 2560;
await page.mouse.move(box.x + 1500 * scale, box.y + 300 * scale);
await page.mouse.down();
await page.mouse.move(box.x + 1500 * scale - 120, box.y + 300 * scale, { steps: 8 });
await page.mouse.up();
await page.waitForTimeout(300);
const xAfter = await page.locator('.inspector .num').first().inputValue();
console.log('after drag x =', xAfter);

// 4. open the script preview
await page.locator('button', { hasText: '预览脚本' }).click();
await page.waitForTimeout(400);
const code = await page.locator('.code').innerText();
console.log('xml head:/n' + code.split('\n').slice(0, 12).join('\n'));
await page.screenshot({ path: 'shot-03.png' });

console.log('pageerrors:', errors.length ? errors.join('\n') : 'none');
await browser.close();
