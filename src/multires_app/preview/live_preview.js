(() => {
  const CFG = (typeof LIVE_CONFIG !== 'undefined') ? LIVE_CONFIG : {token: '', api: '/api/', import: false};
  const $ = (id) => document.getElementById(id);
  const stage = $('stage'), screen = $('screen'), layer = $('layer'), device = $('device');
  const nodes = new Map();
  let plan = null, scale = 1, fitScale = 1, zoom = 80;
  let baseTime = 0, epoch = 0, playing = true, speed = 1;
  let lastTap = null, lastGesture = '—', laidOutFor = '';

  const toastEl = $('toast');
  let toastTimer = null;
  function toast(message) {
    toastEl.textContent = message;
    toastEl.classList.add('on');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove('on'), 2200);
  }

  async function api(path, body) {
    const response = await fetch(CFG.api + path, {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Live-Token': CFG.token},
      body: JSON.stringify(body || {})
    });
    const data = await response.json().catch(() => ({error: '返回内容无法解析'}));
    if (!response.ok) throw new Error(data.error || ('请求失败 ' + response.status));
    return data;
  }

  /* ---------- animation sampling: mirrors scene_engine.Runtime.animation ---------- */
  function sample(track, T) {
    const kf = track.keyframes;
    if (!kf || !kf.length) return null;
    let t = T - (track.delay || 0);
    if (t < kf[0].t) return kf[0];
    const duration = track.duration || 0;
    if (duration > 0) t = track.loop ? (t % duration) : Math.min(t, duration);
    let prev = kf[0];
    for (let i = 1; i < kf.length; i++) {
      const cur = kf[i];
      if (t <= cur.t) {
        const span = cur.t - prev.t;
        const r = span > 0 ? (t - prev.t) / span : 0;
        const out = {};
        for (const key of Object.keys(cur)) {
          if (key === 't') continue;
          const a = prev[key], b = cur[key];
          out[key] = (typeof a === 'number' && typeof b === 'number') ? a + (b - a) * r : (r < 1 ? a : b);
        }
        return out;
      }
      prev = cur;
    }
    return kf[kf.length - 1];
  }

  function applyTracks(rec, T, baseT) {
    const data = rec.data, el = rec.el;
    let dx = 0, dy = 0, dw = 0, dh = 0, alphaFactor = 1, src = null;
    for (const track of data.tracks) {
      const now = sample(track, T);
      const base = sample(track, baseT);
      if (!now || !base) continue;
      if (track.kind === 'pos') { dx += (now.x || 0) - (base.x || 0); dy += (now.y || 0) - (base.y || 0); }
      else if (track.kind === 'alpha') {
        const b = base.a == null ? 255 : base.a;
        alphaFactor *= b > 0.001 ? Math.max(0, now.a == null ? 255 : now.a) / b : 1;
      } else if (track.kind === 'size') { dw += (now.w || 0) - (base.w || 0); dh += (now.h || 0) - (base.h || 0); }
      else if (track.kind === 'frames' && now.url) src = now.url;
    }
    el.style.transform = `translate(${dx.toFixed(2)}px, ${dy.toFixed(2)}px)`;
    el.style.opacity = String(Math.max(0, Math.min(1, data.alpha * alphaFactor)));
    if (dw || dh) {
      el.style.width = Math.max(0, data.rect.w + dw) + 'px';
      el.style.height = Math.max(0, data.rect.h + dh) + 'px';
    }
    if (src && rec.src !== src) { rec.src = src; el.src = src; }
  }

  /* ---------- element rendering ---------- */
  function createElement(item) {
    let el;
    if (item.kind === 'video') {
      el = document.createElement('video');
      el.autoplay = true; el.muted = true; el.loop = true; el.playsInline = true;
    } else if (item.url) {
      el = document.createElement('img');
      el.decoding = 'async';
    } else if (item.text) {
      el = document.createElement('div');
      el.className = 'txt';
    } else {
      el = document.createElement('div');
    }
    return el;
  }

  function place(rec, item, index) {
    const el = rec.el, r = item.rect;
    el.style.left = r.x + 'px';
    el.style.top = r.y + 'px';
    el.style.width = Math.max(0, r.w) + 'px';
    el.style.height = Math.max(0, r.h) + 'px';
    el.style.zIndex = String(index);
    el.style.opacity = String(item.alpha);
    if (item.text && el.classList.contains('txt')) {
      el.textContent = item.text;
      el.style.fontSize = Math.max(6, item.size) + 'px';
      el.style.color = item.color || '#fff';
    }
  }

  function render(next) {
    const revisionChanged = !plan || next.revision !== plan.revision;
    const seen = new Set();
    next.elements.forEach((item, index) => {
      seen.add(item.id);
      let rec = nodes.get(item.id);
      if (!rec) {
        const el = createElement(item);
        rec = {el, data: item, src: item.url};
        nodes.set(item.id, rec);
        layer.appendChild(el);
        if (item.url) el.src = item.url;
      } else {
        rec.data = item;
        if (item.url && rec.src !== item.url && !item.tracks.some(t => t.kind === 'frames')) {
          rec.src = item.url; rec.el.src = item.url;
        }
      }
      place(rec, item, index);
      if (revisionChanged) applyTracks(rec, next.timeMs, next.timeMs);
    });
    for (const [id, rec] of nodes) {
      if (!seen.has(id)) { rec.el.remove(); nodes.delete(id); }
    }
    if (revisionChanged) { baseTime = next.timeMs; epoch = performance.now(); }
    plan = next;
    updateMeta(next);
    const signature = next.canvas.width + 'x' + next.canvas.height;
    if (signature !== laidOutFor) { laidOutFor = signature; layout(); }
  }

  function updateMeta(next) {
    const c = next.canvas;
    $('meta').innerHTML = `<span><b>${escapeHtml(next.name)}</b></span>` +
      `<span>画布 <b>${Math.round(c.width)}×${Math.round(c.height)}</b></span>` +
      `<span>元素 <b>${next.counts.elements}</b></span>` +
      `<span>触发器 <b>${next.counts.triggers}</b></span>` +
      `<span>素材 <b>${next.counts.assets}</b></span>`;
    $('mControls').textContent = next.counts.controls;
    $('mTriggers').textContent = next.counts.triggers;
    $('empty').style.display = 'none';
    device.classList.remove('hidden');
    $('hudText').textContent = `${Math.round(c.width)}×${Math.round(c.height)} · ${next.counts.elements} 元素`;
    renderVars(next.variables);
    renderDiagnostics(next.diagnostics);
    renderHistory(next.history);
  }

  function escapeHtml(text) {
    return String(text).replace(/[&<>"']/g, (ch) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));
  }

  function renderVars(variables) {
    const entries = Object.entries(variables || {});
    $('vars').innerHTML = entries.length
      ? entries.map(([k, v]) => `<div class="row"><span>${escapeHtml(k)}</span><span>${escapeHtml(typeof v === 'number' ? (Math.round(v * 1000) / 1000) : v)}</span></div>`).join('')
      : '<div class="empty">没有变量</div>';
  }

  function renderDiagnostics(list) {
    $('diags').innerHTML = (list && list.length)
      ? list.map(d => `<div class="diag"><b>${escapeHtml(d.key)}</b><br>${escapeHtml(d.message)}</div>`).join('')
      : '<div class="empty">没有诊断项</div>';
  }

  function renderHistory(history) {
    $('history').innerHTML = (history && history.length)
      ? history.map(h => `<div class="row"><span>${escapeHtml(h.at)} ${escapeHtml(h.kind)}</span><span>${escapeHtml(h.owner)} · ${h.executed} 条命令</span></div>`).join('')
      : '<div class="empty" style="padding:14px 0">还没有操作</div>';
  }

  /* ---------- layout ---------- */
  function layout() {
    if (!plan) return;
    const c = plan.canvas;
    const box = stage.getBoundingClientRect();
    const availableW = Math.max(120, box.width - 48);
    const availableH = Math.max(200, box.height - 48);
    fitScale = Math.min(availableW / c.width, availableH / c.height);
    scale = fitScale * zoom / 100;
    screen.style.width = Math.round(c.width * scale) + 'px';
    screen.style.height = Math.round(c.height * scale) + 'px';
    layer.style.width = c.width + 'px';
    layer.style.height = c.height + 'px';
    layer.style.transform = `scale(${scale})`;
    $('zoomVal').textContent = zoom + '%';
  }

  window.addEventListener('resize', layout);

  /* ---------- animation loop ---------- */
  function tick() {
    if (plan && playing) {
      const T = baseTime + (performance.now() - epoch) * speed;
      for (const rec of nodes.values()) {
        if (rec.data.tracks && rec.data.tracks.length) applyTracks(rec, T, baseTime);
      }
    }
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);

  /* ---------- gestures ---------- */
  const MOVE_THRESHOLD = 8;
  let press = null;            // pointerdown bookkeeping
  let drag = null;             // active Slider / Unlocker drag
  let dragQueue = null, dragBusy = false;

  function pointOf(event) {
    const rect = screen.getBoundingClientRect();
    return {x: (event.clientX - rect.left) / scale, y: (event.clientY - rect.top) / scale};
  }

  function showTouch(point, on) {
    const ring = $('touch');
    if (point) {
      ring.style.left = (point.x * scale) + 'px';
      ring.style.top = (point.y * scale) + 'px';
    }
    ring.classList.toggle('on', on);
  }

  function updateDragHud(res) {
    const bar = $('dragbar');
    bar.classList.add('on');
    const p = Math.max(0, Math.min(1, res.progress || 0));
    $('dragFill').style.width = (p * 100).toFixed(1) + '%';
    bar.classList.toggle('reached', !!res.reached);
    if (res.reached) $('dragText').textContent = '已到达终点';
    else if (res.mode === 'touch') $('dragText').textContent = '跟随手指';
    else $('dragText').textContent = (p * 100).toFixed(0) + '%';
  }

  function hideDragHud() {
    $('dragbar').classList.remove('on', 'reached');
    $('dragFill').style.width = '0%';
  }

  /* Drag moves are fire-and-forget with at most one request in flight; the last
     finger position is always the one that gets sent next. */
  function queueMove(point) {
    dragQueue = point;
    if (!dragBusy) flushMove();
  }

  async function flushMove() {
    if (dragBusy || !dragQueue || !drag) return;
    const point = dragQueue;
    dragQueue = null;
    dragBusy = true;
    try {
      const res = await api('drag', {phase: 'move', x: point.x, y: point.y});
      if (res.plan) render(res.plan);
      updateDragHud(res);
    } catch (error) { /* 拖动中的单帧失败不打断交互 */ }
    dragBusy = false;
    if (dragQueue && drag) flushMove();
  }

  /* A released slider that never reached its EndPoint springs back server-side;
     follow it at ~30Hz until the engine settles so the return is visible. */
  async function followRebound() {
    const deadline = performance.now() + 1500;
    while (performance.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 33));
      let next;
      try { next = await api('plan', {}); } catch (error) { return; }
      render(next);
      if (!next.drag) return;
    }
  }

  async function beginDrag() {
    if (!press || drag || press.probing) return;
    const start = press.start;
    press.probing = true;
    try {
      const probe = await api('press', {x: start.x, y: start.y});
      if (!press) return;                       // 手指已经抬起
      if (!probe.slider) return;                // 不是滑动控件，交给普通手势
      const res = await api('drag', {phase: 'start', x: start.x, y: start.y, owner: probe.slider});
      if (!press) {
        // 手指在启动过程中已经抬起，立刻收尾，避免服务端留下悬挂的拖动
        try {
          const done = await api('drag', {phase: 'end', x: start.x, y: start.y});
          if (done.plan) render(done.plan);
        } catch (error) { /* 收尾失败不影响后续操作 */ }
        return;
      }
      if (!res.active) { if (res.message) toast(res.message); return; }
      drag = {name: res.name, mode: res.mode, length: res.length, direction: res.direction};
      if (res.plan) render(res.plan);
      updateDragHud({progress: 0, reached: false, mode: res.mode});
      if (press) queueMove(press.last);
    } catch (error) { toast(error.message); }
  }

  screen.addEventListener('pointerdown', (event) => {
    if (!plan) return;
    screen.setPointerCapture(event.pointerId);
    const point = pointOf(event);
    press = {start: point, last: point, at: performance.now(), moved: 0, probing: false};
    showTouch(point, true);
  });

  screen.addEventListener('pointermove', (event) => {
    if (!press) return;
    const point = pointOf(event);
    press.moved = Math.max(press.moved, Math.hypot(point.x - press.start.x, point.y - press.start.y));
    press.last = point;
    showTouch(point, true);
    if (!drag && press.moved > MOVE_THRESHOLD) beginDrag();
    if (drag) queueMove(point);
  });

  async function endPointer(event) {
    if (!press) return;
    const point = pointOf(event);
    const state = press;
    press = null;
    showTouch(point, false);
    if (drag) {
      const current = drag;
      drag = null;
      dragQueue = null;
      lastTap = null;
      hideDragHud();
      try {
        const res = await api('drag', {phase: 'end', x: point.x, y: point.y});
        if (res.plan) render(res.plan);
        reportDrag(current, res);
        if (res.rebounding) followRebound();
      } catch (error) { toast(error.message); }
      return;
    }
    const duration = performance.now() - state.at;
    const moved = Math.max(state.moved, Math.hypot(point.x - state.start.x, point.y - state.start.y));

    let kind = 'tap';
    if (moved > 12) kind = 'swipe';
    else if (duration >= 500) kind = 'long-press';
    else if (lastTap && performance.now() - lastTap.at < 320 &&
             Math.hypot(point.x - lastTap.x, point.y - lastTap.y) < 24) kind = 'double-tap';
    lastTap = kind === 'tap' ? {x: point.x, y: point.y, at: performance.now()} : null;

    try {
      const result = await api('gesture', {
        kind, x: state.start.x, y: state.start.y, toX: point.x, toY: point.y, durationMs: Math.round(duration)
      });
      if (!result.hit) { toast(result.message || '该位置没有响应'); }
      else { lastGesture = `${kind} · ${result.executed.length} 条命令`; }
      $('mLast').textContent = lastGesture;
      if (result.plan) render(result.plan);
    } catch (error) { toast(error.message); }
  }

  function reportDrag(current, res) {
    const executed = res.executed || 0;
    if (res.unlocked) toast(`已解锁 · ${current.name}`);
    else if (res.reached) toast(`到达终点 · ${current.name}`);
    else if (res.rebounding) toast(`未到达，回弹中 · ${current.name}`);
    else toast(`已释放 · ${current.name}（${executed} 条触发器）`);
    lastGesture = `drag · ${current.name} · ${executed} 条命令`;
    $('mLast').textContent = lastGesture;
  }

  screen.addEventListener('pointerup', endPointer);
  screen.addEventListener('pointercancel', endPointer);

  /* ---------- import ---------- */
  function clearState() {
    nodes.forEach(rec => rec.el.remove());
    nodes.clear();
    plan = null;
    press = null;
    drag = null;
    dragQueue = null;
    hideDragHud();
  }

  async function importFile(file) {
    if (!file) return;
    if (!/\.(hwt|zip)$/i.test(file.name)) { toast('只支持 .hwt / .zip'); return; }
    toast('正在解析 ' + file.name + ' …');
    try {
      const response = await fetch(CFG.api + 'import', {
        method: 'POST',
        headers: {'X-Live-Token': CFG.token, 'X-Filename': encodeURIComponent(file.name)},
        body: await file.arrayBuffer()
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || '导入失败');
      clearState();
      render(data);
      toast('已导入 ' + file.name);
    } catch (error) { toast(error.message); }
  }

  $('importBtn').addEventListener('click', () => $('file').click());
  $('file').addEventListener('change', (event) => importFile(event.target.files[0]));

  const drop = $('drop');
  ['dragenter', 'dragover'].forEach(name => document.addEventListener(name, (event) => {
    if (!CFG.import) return;
    event.preventDefault(); drop.classList.add('on');
  }));
  ['dragleave', 'drop'].forEach(name => document.addEventListener(name, (event) => {
    event.preventDefault();
    if (name === 'drop' || event.relatedTarget === null) drop.classList.remove('on');
  }));
  document.addEventListener('drop', (event) => {
    if (!CFG.import) return;
    const file = event.dataTransfer.files[0];
    if (file) importFile(file);
  });

  /* ---------- controls ---------- */
  $('resetBtn').addEventListener('click', async () => {
    try { const next = await api('reset', {}); clearState(); render(next); toast('已重置'); }
    catch (error) { toast(error.message); }
  });
  $('animToggle').addEventListener('change', (event) => { playing = event.target.checked; });
  $('speed').addEventListener('input', (event) => {
    speed = Number(event.target.value);
    $('speedVal').textContent = speed.toFixed(1) + '×';
  });
  $('zoom').addEventListener('input', (event) => { zoom = Number(event.target.value); layout(); });

  document.querySelectorAll('.tabs button').forEach(button => button.addEventListener('click', () => {
    document.querySelectorAll('.tabs button').forEach(b => b.classList.toggle('active', b === button));
    document.querySelectorAll('.panel').forEach(panel => panel.classList.toggle('active', panel.id === 'panel-' + button.dataset.tab));
  }));

  /* ---------- clock refresh ---------- */
  async function bootstrap() {
    try {
      const next = await api('plan', {});
      render(next);
    } catch (error) { /* 启动时没有主题，保持导入提示 */ }
  }
  bootstrap();

  setInterval(async () => {
    if (!plan || drag) return;
    try {
      const next = await api('plan', {});
      render(next);
    } catch (error) { /* 时钟刷新失败不打断预览 */ }
  }, 1000);
})();
