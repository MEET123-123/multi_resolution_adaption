// Drive a real drag through the preview page over CDP, then report the outcome.
const PORT = process.argv[2] || '9222';
const TARGET = process.argv[3] || 'http://127.0.0.1:8799/';
const SHOT = process.argv[4] || '';
const fs = require('fs');

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  let list = [];
  for (let i = 0; i < 40; i++) {
    try {
      list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
      if (list.some((t) => t.type === 'page')) break;
    } catch (error) { /* chrome still starting */ }
    await sleep(250);
  }
  const page = list.find((t) => t.type === 'page');
  if (!page) throw new Error('no page target');

  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let seq = 0;
  const pending = new Map();
  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result);
    }
  };
  const send = (method, params = {}) => new Promise((resolve, reject) => {
    const id = ++seq;
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
  });

  const evaluate = async (expression) => {
    const result = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };

  await send('Page.enable');
  await send('Runtime.enable');
  await send('Page.navigate', { url: TARGET });

  // Poll until the preview has actually bootstrapped; a fresh server can refuse
  // the first navigation while it finishes binding.
  let booted = null;
  for (let attempt = 0; attempt < 20; attempt++) {
    await sleep(500);
    booted = await evaluate(`(() => { const m = document.getElementById('meta'); const d = document.getElementById('device'); return m && d ? {meta: m.textContent, ready: !d.classList.contains('hidden')} : null; })()`);
    if (booted && booted.ready) break;
    if (booted === null) await send('Page.navigate', { url: TARGET });
  }
  if (!booted || !booted.ready) throw new Error('preview did not load: ' + JSON.stringify(booted));

  const canvas = await evaluate(`(() => { const c = document.getElementById('meta').textContent; const s = document.getElementById('screen').getBoundingClientRect(); return {meta: c, x: s.x, y: s.y, w: s.width, h: s.height, ready: !document.getElementById('device').classList.contains('hidden')}; })()`);
  if (!canvas.ready) throw new Error('preview did not load: ' + JSON.stringify(canvas));

  // Screen-space point at 50% width, 72% height, dragging straight up by 45% of the height.
  const x0 = Math.round(canvas.x + canvas.w * 0.5);
  const y0 = Math.round(canvas.y + canvas.h * 0.72);
  const steps = 14;
  const travel = canvas.h * 0.45;

  const mouse = (type, x, y, extra = {}) => send('Input.dispatchMouseEvent', {
    type, x, y, button: 'left', buttons: type === 'mouseReleased' ? 0 : 1, clickCount: 1, ...extra,
  });

  await mouse('mousePressed', x0, y0);
  await sleep(60);
  for (let i = 1; i <= steps; i++) {
    await mouse('mouseMoved', x0, Math.round(y0 - (travel * i) / steps));
    await sleep(35);
  }
  await sleep(250);
  const mid = await evaluate(`(() => { const b = document.getElementById('dragbar'); return {on: b.classList.contains('on'), reached: b.classList.contains('reached'), text: document.getElementById('dragText').textContent, fill: document.getElementById('dragFill').style.width}; })()`);

  if (process.env.SHOT_MID) {
    const shot = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(process.env.SHOT_MID, Buffer.from(shot.data, 'base64'));
  }

  await mouse('mouseReleased', x0, Math.round(y0 - travel));
  await sleep(900);
  const after = await evaluate(`(() => ({last: document.getElementById('mLast').textContent, toast: document.getElementById('toast').textContent, bar: document.getElementById('dragbar').classList.contains('on'), history: document.getElementById('history').textContent}))()`);

  if (SHOT) {
    const shot = await send('Page.captureScreenshot', { format: 'png' });
    fs.writeFileSync(SHOT, Buffer.from(shot.data, 'base64'));
  }
  console.log(JSON.stringify({ canvas, midDrag: mid, after }, null, 2));
  ws.close();
}

main().catch((error) => { console.error('FAIL', error.message); process.exit(1); });
