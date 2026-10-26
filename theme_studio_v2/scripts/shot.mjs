import { chromium } from 'file:///G:/openbridge/node_modules/playwright-core/index.mjs';

const out = process.argv[2] ?? 'shot.png';
const browser = await chromium.launch({
  executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe',
  headless: true,
});
const page = await browser.newPage({ viewport: { width: 1560, height: 980 }, deviceScaleFactor: 2 });
const errors = [];
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
page.on('pageerror', (e) => errors.push(String(e)));
await page.goto('http://localhost:5178/', { waitUntil: 'networkidle' });
await page.waitForTimeout(1200);
await page.screenshot({ path: out });
console.log('errors:', errors.length ? errors.join('\n') : 'none');
await browser.close();
