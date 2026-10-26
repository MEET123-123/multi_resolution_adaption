import { chromium } from 'file:///G:/openbridge/node_modules/playwright-core/index.mjs';

const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
});
const page = await browser.newPage({ viewport: { width: 1560, height: 980 }, deviceScaleFactor: 2 });
await page.goto('http://localhost:5178/', { waitUntil: 'networkidle' });
await page.waitForTimeout(800);

// tablet expanded view
await page.locator('.device-card', { hasText: '2560×1600' }).click();
await page.locator('.layer-name', { hasText: '通知卡片' }).click();
await page.waitForTimeout(600);
await page.screenshot({ path: 'shot-tablet.png' });

// export tab
await page.locator('.tab', { hasText: '导出' }).click();
await page.waitForTimeout(400);
await page.screenshot({ path: 'shot-export.png' });

await browser.close();
