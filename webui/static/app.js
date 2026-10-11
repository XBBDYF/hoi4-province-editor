'use strict';
/* HOI4 地块细化 - Web 前端逻辑 */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const CFG_KEY = 'hoi4-refine-web-cfg-v2';

function hsv2rgb(h, s, v) {
  const i = Math.floor(h * 6), f = h * 6 - i;
  const p = v * (1 - s), q = v * (1 - f * s), t = v * (1 - (1 - f) * s);
  let r, g, b;
  switch (i % 6) {
    case 0: r = v; g = t; b = p; break;
    case 1: r = q; g = v; b = p; break;
    case 2: r = p; g = v; b = t; break;
    case 3: r = p; g = q; b = v; break;
    case 4: r = t; g = p; b = v; break;
    default: r = v; g = p; b = q;
  }
  return [Math.round(r * 255), Math.round(g * 255), Math.round(b * 255)];
}
function pal(n) {
  const out = [];
  for (let i = 0; i < n; i++) {
    const h = (i * 0.61803398875) % 1.0;
    const s = 0.55 + 0.35 * ((i * 7) % 5) / 5.0;
    const v = 0.75 + 0.25 * ((i * 3) % 4) / 4.0;
    out.push(hsv2rgb(h, s, v));
  }
  return out;
}
const hex = (c) => '#' + c.map(x => x.toString(16).padStart(2, '0')).join('');
const rgbStr = (c) => `rgb(${c[0]},${c[1]},${c[2]})`;
const TYPE_CN = { land: '陆地', sea: '海洋', lake: '湖泊' };

const App = {
  defaults: null, meta: null, idx: null, regions: null, provs: [],
  sel: null, cfgTpl: null,
};

/* ------------------------------------------------------------------ */
/* 通用任务: POST 创建 -> SSE 订阅                                     */
/* ------------------------------------------------------------------ */
function runJob(url, body, { onLog, onDone, onErr } = {}) {
  return fetch(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  }).then(r => r.json()).then(({ task, error }) => {
    if (error || !task) { onErr && onErr(error || '任务创建失败'); return null; }
    const es = new EventSource(`/api/task/${task}/stream`);
    let got = false;
    es.onmessage = (ev) => {
      let d; try { d = JSON.parse(ev.data); } catch { return; }
      if (d.type === 'log') { onLog && onLog(d.msg); }
      else if (d.type === 'done') {
        es.close(); got = true;
        if (d.error) onErr && onErr(d.error); else onDone && onDone(d.result);
      }
    };
    es.onerror = () => { es.close(); if (!got) onErr && onErr('连接中断'); };
    return { es, cancel: () => fetch('/api/cancel', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ task }) }) };
  });
}

/* ------------------------------------------------------------------ */
/* 参数表单                                                            */
/* ------------------------------------------------------------------ */
const CFG_SCHEMA = [
  ['检测 / 拆分', [
    ['scope', 'select', '检测类型', [['all', '全部'], ['land', '仅陆地'], ['sea', '仅海洋'], ['lake', '仅湖泊']]],
    ['threshold', 'number', '面积阈值 (px)', 200],
    ['target_pixels', 'number', '目标面积 (0=阈值)', 0],
    ['min_part_pixels', 'number', '新块最小 (px)', 80],
    ['max_parts', 'number', '单块最多份数', 64],
    ['continents', 'text', '限定大洲ID (逗号)', '1,2,3'],
    ['include_ids', 'text', '强制细化ID (逗号)', ''],
    ['exclude_ids', 'text', '排除ID (逗号)', '85,69,86,114'],
    ['exclude_states', 'text', '排除州ID (逗号)', ''],
    ['parts_override', 'text', '指定份数 (如 225:4,300:2)', ''],
    ['seed', 'number', '随机种子', 20240101],
    ['only_ids', 'check', '仅处理 include 中的ID', false],
  ]],
  ['渐变密度', [
    ['grad_path', 'text', '渐变灰度图路径 (越黑越小)', ''],
    ['grad_black_px', 'number', '纯黑处目标 (px)', 80],
    ['grad_white_px', 'number', '纯白处目标 (px)', 250],
    ['grad_skip_white', 'check', '白色区域不细化', true],
    ['grad_invert', 'check', '反转 (黑=大, 白=小)', false],
    ['split_hysteresis_ratio', 'number', '滞后比 (需超过目标×此值)', 1.25],
    ['split_search_rounds', 'number', '候选切割搜索轮数', 6],
    ['neighbor_transition_enabled', 'check', '未细化陆地邻区渐变', true],
  ]],
  ['完整性修复', [
    ['repair', 'check', '修复 战略区/州/铁路/海峡', true],
    ['canal_complete', 'check', '用 rivers.bmp 辅助校验/谨慎补全运河连线', true],
    ['railway_max_hops', 'number', '铁路重连跳数', 25],
    ['strip_invalid', 'check', '清除无效省份ID', true],
    ['island_max_px', 'number', '孤岛阈值 (px)', 5],
    ['make_preview', 'check', '生成预览图', true],
    ['preview_scale', 'number', '预览图缩放', 1.0],
  ]],
  ['高级', [
    ['in_place', 'check', '⚠ 直接改原 mod (危险)', false],
  ]],
];

function buildCfgForm() {
  const wrap = document.createElement('div');
  wrap.id = 'cfgForm';
  for (const [title, fields] of CFG_SCHEMA) {
    const fs = document.createElement('fieldset');
    fs.innerHTML = `<legend>${title}</legend>`;
    const grid = document.createElement('div');
    grid.className = 'grid';
    for (const [key, kind, label, dflt] of fields) {
      const f = document.createElement('div');
      f.className = 'f' + (kind === 'text' ? ' full' : '');
      if (kind === 'check') {
        f.className = 'full check';
        f.innerHTML = `<input type="checkbox" id="cfg_${key}"><label for="cfg_${key}">${label}</label>`;
      } else if (kind === 'select') {
        const opts = dflt.map(([v, t]) => `<option value="${v}">${t}</option>`).join('');
        f.innerHTML = `<label>${label}</label><select id="cfg_${key}">${opts}</select>`;
      } else {
        f.innerHTML = `<label>${label}</label><input id="cfg_${key}" type="${kind === 'number' ? 'number' : 'text'}" ${kind === 'number' ? 'step="any"' : ''}>`;
      }
      grid.appendChild(f);
    }
    fs.appendChild(grid);
    wrap.appendChild(fs);
  }
  return wrap;
}

function cfgDefaults() {
  const o = {};
  for (const [, fields] of CFG_SCHEMA) for (const [key, , , d] of fields) o[key] = d;
  return o;
}

function loadCfg() {
  let stored = {};
  try { stored = JSON.parse(localStorage.getItem(CFG_KEY) || '{}'); } catch { }
  const def = cfgDefaults();
  for (const [key, kind] of allCfgKeys()) {
    const el = $('#cfg_' + key);
    if (!el) continue;
    const v = stored[key] !== undefined ? stored[key] : def[key];
    if (kind === 'check') el.checked = !!v; else el.value = v;
  }
}
function allCfgKeys() {
  const out = [];
  for (const [, fields] of CFG_SCHEMA) for (const [key, kind] of fields) out.push([key, kind]);
  return out;
}
function collectCfg() {
  const o = {};
  for (const [key, kind] of allCfgKeys()) {
    const el = $('#cfg_' + key);
    if (!el) continue;
    if (kind === 'check') o[key] = el.checked;
    else if (kind === 'number') o[key] = el.value === '' ? 0 : Number(el.value);
    else o[key] = el.value;
  }
  o.mod_dir = $('#modDir').value.trim();
  o.out_dir = $('#outDir').value.trim();
  try { localStorage.setItem(CFG_KEY, JSON.stringify(o)); } catch { }
  return o;
}

/* ------------------------------------------------------------------ */
/* 地图查看器                                                          */
/* ------------------------------------------------------------------ */
const MapView = {
  canvas: null, ctx: null, W: 0, H: 0, cam: { s: 1, x: 0, y: 0 },
  imgs: {}, pid: null, pidW: 0, pidH: 0, hl: null, raf: 0, inited: false,
  layers: [
    { key: 'layers-base', name: '地形', img: 'base', vis: true, op: 1 },
    { key: 'layers-prov', name: '省份', img: 'prov', vis: false, op: 0.85 },
    { key: 'layers-cont', name: '大洲', img: 'cont', vis: false, op: 0.6 },
    { key: 'layers-sr', name: '战略区', img: 'sr', vis: false, op: 0.6 },
    { key: 'layers-state', name: '州', img: 'state', vis: false, op: 0.6 },
    { key: 'layers-edges', name: '边界', img: 'edges', vis: true, op: 0.85 },
  ],
};

function loadImg(src) {
  return new Promise((res, rej) => {
    const im = new Image();
    im.onload = () => res(im);
    im.onerror = () => rej(new Error('图片加载失败: ' + src));
    im.src = src;
  });
}

async function initMap() {
  const meta = App.meta;
  MapView.W = meta.disp_width; MapView.H = meta.disp_height;
  const bust = '?t=' + Date.now();
  const jobs = [
    loadImg('/cache/base_map.png' + bust).then(i => MapView.imgs.base = i),
    loadImg('/cache/provinces.png' + bust).then(i => MapView.imgs.prov = i),
    loadImg('/cache/overlay_sr.png' + bust).then(i => MapView.imgs.sr = i),
    loadImg('/cache/overlay_state.png' + bust).then(i => MapView.imgs.state = i),
    loadImg('/cache/overlay_continent.png' + bust).then(i => MapView.imgs.cont = i),
    loadImg('/cache/edges.png' + bust).then(i => MapView.imgs.edges = i),
  ];
  try { await Promise.all(jobs); } catch (e) { setMsg('部分图层加载失败: ' + e.message); }

  // prov_id -> 显示分辨率 ImageData
  try {
    const pidImg = await loadImg('/cache/prov_id.png' + bust);
    const oc = document.createElement('canvas');
    oc.width = MapView.W; oc.height = MapView.H;
    const octx = oc.getContext('2d', { willReadFrequently: true });
    octx.imageSmoothingEnabled = false;
    octx.drawImage(pidImg, 0, 0, MapView.W, MapView.H);
    const d = octx.getImageData(0, 0, MapView.W, MapView.H);
    MapView.pid = d.data; MapView.pidW = MapView.W; MapView.pidH = MapView.H;
  } catch (e) { setMsg('省份ID图加载失败: ' + e.message); }

  MapView.inited = true;
  buildLayerControls();
  setupMapEvents();
  resizeCanvas();
  fitView();
  requestDraw();
}

function buildLayerControls() {
  const box = $('#layerList');
  box.innerHTML = '';
  for (const L of MapView.layers) {
    const row = document.createElement('div');
    row.className = 'lrow';
    row.innerHTML = `<input type="checkbox" ${L.vis ? 'checked' : ''}>
      <label>${L.name}</label>
      <input type="range" min="0" max="100" value="${Math.round(L.op * 100)}">
      <span class="pct">${Math.round(L.op * 100)}</span>`;
    const [cb, rng] = [row.querySelector('input[type=checkbox]'), row.querySelector('input[type=range]')];
    cb.onchange = () => { L.vis = cb.checked; requestDraw(); };
    row.querySelector('label').onclick = () => { cb.checked = !cb.checked; cb.onchange(); };
    rng.oninput = () => { L.op = Number(rng.value) / 100; row.querySelector('.pct').textContent = rng.value; requestDraw(); };
    box.appendChild(row);
  }
}
function setLayer(key, vis, op) {
  const L = MapView.layers.find(l => l.img === key);
  if (!L) return;
  if (vis !== undefined) L.vis = vis;
  if (op !== undefined) L.op = op;
  buildLayerControls(); requestDraw();
}

function requestDraw() {
  if (MapView.raf) return;
  MapView.raf = requestAnimationFrame(() => { MapView.raf = 0; draw(); });
}

function draw() {
  const c = MapView.canvas, ctx = MapView.ctx;
  if (!c || !ctx) return;
  const w = c.width, h = c.height;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.fillStyle = '#0d0f13'; ctx.fillRect(0, 0, w, h);
  const { s, x, y } = MapView.cam;
  if (!MapView.inited) return;
  const dw = MapView.W * s, dh = MapView.H * s;
  ctx.imageSmoothingEnabled = false;          // 放大后为锐利像素
  for (const L of MapView.layers) {
    if (L.img === 'edges') continue;          // 边界单独按“等宽”绘制
    const im = MapView.imgs[L.img];
    if (!L.vis || !im || L.op <= 0) continue;
    ctx.globalAlpha = L.op;
    ctx.drawImage(im, x, y, dw, dh);
  }
  ctx.globalAlpha = 1;
  if (MapView.hl) ctx.drawImage(MapView.hl, x, y, dw, dh);
  const borderL = MapView.layers.find(l => l.img === 'edges');
  if (borderL && borderL.vis && MapView.pid) drawBorders(ctx, borderL.op);
}

/* 等宽边界：直接在屏幕空间按像素计算，线宽与缩放无关（始终约1个屏幕像素）。 */
function drawBorders(ctx, alpha) {
  const area = $('#maparea');
  const cw = Math.max(1, area.clientWidth | 0), ch = Math.max(1, area.clientHeight | 0);
  if (!MapView.bc) {
    MapView.bc = document.createElement('canvas');
    MapView.bctx = MapView.bc.getContext('2d');
  }
  const bc = MapView.bc, bctx = MapView.bctx;
  if (bc.width !== cw || bc.height !== ch) {
    bc.width = cw; bc.height = ch; MapView.bim = null;
  }
  if (!MapView.bim) MapView.bim = bctx.createImageData(cw, ch);
  const out = MapView.bim.data;
  out.fill(0);

  const { s, x, y } = MapView.cam, inv = 1 / s;
  const pid = MapView.pid, W = MapView.pidW, H = MapView.pidH;
  const step = inv;                            // 世界坐标里对应 1 个屏幕像素
  for (let sy = 0; sy < ch; sy++) {
    const wy = (sy - y) * inv, wy1 = wy + step;
    const py = Math.floor(wy), py1 = Math.floor(wy1);
    const rowA = (py >= 0 && py < H) ? py * W : -1;
    const rowB = (py1 >= 0 && py1 < H) ? py1 * W : -1;
    let wx = (0 - x) * inv;
    let o = sy * cw * 4;
    for (let sx = 0; sx < cw; sx++, o += 4, wx += step) {
      const px = Math.floor(wx), px1 = Math.floor(wx + step);
      let a = -1, b = -1, d2 = -1;
      if (rowA >= 0) {
        if (px >= 0 && px < W) { const i = (rowA + px) * 4; a = pid[i] | (pid[i + 1] << 8) | (pid[i + 2] << 16); }
        if (px1 >= 0 && px1 < W) { const i = (rowA + px1) * 4; b = pid[i] | (pid[i + 1] << 8) | (pid[i + 2] << 16); }
      }
      if (rowB >= 0 && px >= 0 && px < W) { const i = (rowB + px) * 4; d2 = pid[i] | (pid[i + 1] << 8) | (pid[i + 2] << 16); }
      if (a > 0 && (a !== b || a !== d2)) { out[o] = 22; out[o + 1] = 19; out[o + 2] = 15; out[o + 3] = 255; }
    }
  }
  bctx.putImageData(MapView.bim, 0, 0);
  ctx.globalAlpha = alpha;
  ctx.drawImage(bc, 0, 0, cw, ch);
  ctx.globalAlpha = 1;
}

function resizeCanvas() {
  const area = $('#maparea');
  const c = MapView.canvas;
  const dpr = window.devicePixelRatio || 1;
  const cw = area.clientWidth, ch = area.clientHeight;
  c.style.width = cw + 'px'; c.style.height = ch + 'px';
  c.width = Math.round(cw * dpr); c.height = Math.round(ch * dpr);
  MapView.ctx.setTransform(1, 0, 0, 1, 0, 0);
  MapView.ctx.scale(dpr, dpr);
  MapView.dpr = dpr;
  requestDraw();
}

function fitView() {
  const area = $('#maparea');
  const cw = area.clientWidth, ch = area.clientHeight;
  const s = Math.min(cw / MapView.W, ch / MapView.H) * 0.98;
  MapView.cam.s = s;
  MapView.cam.x = (cw - MapView.W * s) / 2;
  MapView.cam.y = (ch - MapView.H * s) / 2;
  updZoom(); requestDraw();
}

function screenToWorld(px, py) {
  const { s, x, y } = MapView.cam;
  return { x: (px - x) / s, y: (py - y) / s };
}
function zoomAt(px, py, factor) {
  const { s, x, y } = MapView.cam;
  const ns = Math.max(0.05, Math.min(40, s * factor));
  const wx = (px - x) / s, wy = (py - y) / s;
  MapView.cam.s = ns;
  MapView.cam.x = px - wx * ns;
  MapView.cam.y = py - wy * ns;
  updZoom(); requestDraw();
}
function updZoom() { $('#stZoom').textContent = (MapView.cam.s * 100).toFixed(0) + '%'; }

function provinceAtWorld(wx, wy) {
  if (!MapView.pid) return -1;
  const px = Math.floor(wx), py = Math.floor(wy);
  if (px < 0 || py < 0 || px >= MapView.pidW || py >= MapView.pidH) return -1;
  const i = (py * MapView.pidW + px) * 4;
  const d = MapView.pid;
  return d[i] | (d[i + 1] << 8) | (d[i + 2] << 16);
}

function setupMapEvents() {
  const area = $('#maparea'), c = MapView.canvas;
  clampCam();
  area.addEventListener('wheel', (e) => {
    e.preventDefault();
    const rect = c.getBoundingClientRect();
    zoomAt(e.clientX - rect.left, e.clientY - rect.top, e.deltaY < 0 ? 1.15 : 1 / 1.15);
  }, { passive: false });

  let drag = null;
  area.addEventListener('mousedown', (e) => {
    if (e.button !== 0) return;
    drag = { x: e.clientX, y: e.clientY, cx: MapView.cam.x, cy: MapView.cam.y, moved: false };
    area.classList.add('dragging');
  });
  window.addEventListener('mousemove', (e) => {
    if (drag) {
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (Math.abs(dx) + Math.abs(dy) > 3) drag.moved = true;
      MapView.cam.x = drag.cx + dx; MapView.cam.y = drag.cy + dy;
      requestDraw();
      return;
    }
    const rect = c.getBoundingClientRect();
    const w = screenToWorld(e.clientX - rect.left, e.clientY - rect.top);
    updateHover(w, e.clientX, e.clientY);
  });
  window.addEventListener('mouseup', (e) => {
    if (!drag) return;
    area.classList.remove('dragging');
    if (!drag.moved) {
      const rect = c.getBoundingClientRect();
      const w = screenToWorld(e.clientX - rect.left, e.clientY - rect.top);
      const id = provinceAtWorld(w.x, w.y);
      if (id > 0) selectProvince(id); else selectProvince(null);
    }
    drag = null;
  });
  area.addEventListener('mouseleave', () => { $('#tooltip').style.display = 'none'; });
  window.addEventListener('resize', () => { resizeCanvas(); });
}

function clampCam() { /* 允许自由平移, 不强制限制 */ }

function updStatus(k, v) { const el = $('#' + k); if (el) el.textContent = v; }

function updateHover(w, sx, sy) {
  const id = provinceAtWorld(w.x, w.y);
  updStatus('stPos', Math.floor(w.x) + ', ' + Math.floor(w.y));
  const tip = $('#tooltip');
  if (id <= 0) { tip.style.display = 'none'; updStatus('stProv', '–'); return; }
  updStatus('stProv', '#' + id);
  const info = App.idx && App.idx[String(id)];
  tip.style.display = 'block';
  tip.innerHTML = tooltipHTML(id, info);
  const area = $('#maparea').getBoundingClientRect();
  let tx = sx - area.left + 14, ty = sy - area.top + 14;
  if (tx + 260 > area.width) tx = sx - area.left - 260;
  if (ty + 150 > area.height) ty = sy - area.top - 150;
  tip.style.left = tx + 'px'; tip.style.top = ty + 'px';
}

function contName(info) {
  if (!info || !App.regions) return '';
  const arr = App.regions.continents || [];
  const v = parseInt(info.continent, 10);   // HOI4 大洲ID从 1 开始
  if (!isFinite(v) || v <= 0) return '—';
  return (v - 1 < arr.length) ? arr[v - 1] : ('#' + v);
}

function tooltipHTML(id, info) {
  if (!info) return `<div class="t-id">#${id}</div><div class="t-row">未在 definition 中登记</div>`;
  const col = `<span class="swatch" style="background:${rgbStr(info.color)}"></span>`;
  return `<div class="t-id">#${id} ${col}${hex(info.color)}</div>
    <div class="t-row">类型 <b>${TYPE_CN[info.type] || info.type}</b> · 沿海 <b>${info.coastal}</b></div>
    <div class="t-row">面积 <b>${info.area.toLocaleString()} px</b></div>
    <div class="t-row">大洲 <b>${contName(info) || '–'}</b></div>
    <div class="t-row">state <b>${info.state || '–'}</b></div>
    <div class="t-row">战略区 <b>${info.sr || '–'}</b></div>
    <div class="t-row">地形 <b>${info.terrain || '–'}</b></div>`;
}

function selectProvince(id) {
  App.sel = id ? Number(id) : null;
  $('#stSel').textContent = App.sel ? ('#' + App.sel) : '无';
  buildHighlight(App.sel);
  requestDraw();
}

function buildHighlight(id) {
  if (!id || !MapView.pid) { MapView.hl = null; return; }
  const W = MapView.pidW, H = MapView.pidH, d = MapView.pid;
  const oc = document.createElement('canvas'); oc.width = W; oc.height = H;
  const octx = oc.getContext('2d');
  const out = octx.createImageData(W, H);
  const o = out.data;
  const mask = new Uint8Array(W * H);
  for (let p = 0, i = 0; p < W * H; p++, i += 4) {
    const pid = d[i] | (d[i + 1] << 8) | (d[i + 2] << 16);
    if (pid === id) mask[p] = 1;
  }
  for (let p = 0, i = 0; p < W * H; p++, i += 4) {
    if (!mask[p]) continue;
    const x = p % W, y = (p / W) | 0;
    const nb = (x === 0 || x === W - 1 || y === 0 || y === H - 1) ||
      !mask[p - 1] || !mask[p + 1] || !mask[p - W] || !mask[p + W];
    if (nb) { o[i] = 255; o[i + 1] = 245; o[i + 2] = 160; o[i + 3] = 255; }
    else { o[i] = 255; o[i + 1] = 210; o[i + 2] = 60; o[i + 3] = 120; }
  }
  octx.putImageData(out, 0, 0);
  MapView.hl = oc;
}

function provinceBBox(id) {
  const W = MapView.pidW, H = MapView.pidH, d = MapView.pid;
  let x0 = W, y0 = H, x1 = -1, y1 = -1;
  for (let p = 0, i = 0; p < W * H; p++, i += 4) {
    const pid = d[i] | (d[i + 1] << 8) | (d[i + 2] << 16);
    if (pid !== id) continue;
    const x = p % W, y = (p / W) | 0;
    if (x < x0) x0 = x; if (x > x1) x1 = x;
    if (y < y0) y0 = y; if (y > y1) y1 = y;
  }
  return x1 < 0 ? null : { x0, y0, x1, y1 };
}

function locateProvince(id) {
  if (!MapView.inited) { setMsg('缓存未就绪'); return; }
  const bb = provinceBBox(id);
  selectProvince(id);
  if (!bb) { setMsg('地图上找不到省份 #' + id); return; }
  const area = $('#maparea');
  const bw = Math.max(8, bb.x1 - bb.x0 + 1), bh = Math.max(8, bb.y1 - bb.y0 + 1);
  const s = Math.min(area.clientWidth / (bw * 1.8), area.clientHeight / (bh * 1.8), 20);
  MapView.cam.s = Math.max(0.05, s);
  const cx = (bb.x0 + bb.x1) / 2, cy = (bb.y0 + bb.y1) / 2;
  MapView.cam.x = area.clientWidth / 2 - cx * MapView.cam.s;
  MapView.cam.y = area.clientHeight / 2 - cy * MapView.cam.s;
  updZoom(); requestDraw();
  setMsg('已定位 #' + id);
}

/* ------------------------------------------------------------------ */
/* 图例                                                                */
/* ------------------------------------------------------------------ */
function showLegend(kind) {
  const names = (App.regions && App.regions[kind]) || [];
  const p = pal(Math.max(names.length, 1));
  const body = $('#legendBody');
  body.innerHTML = names.map((n, i) =>
    `<div class="lg"><span class="swatch" style="background:${rgbStr(p[i])}"></span><span>${n}</span></div>`).join('')
    || '<div class="tiny">无数据</div>';
  $('#legend').style.display = 'block';
  setLayer(kind, true);
}

/* ------------------------------------------------------------------ */
/* 标签切换                                                            */
/* ------------------------------------------------------------------ */
function initTabs() {
  const side = $('#side');
  $$('#side .tabs button').forEach(b => {
    b.onclick = () => {
      $$('#side .tabs button').forEach(x => x.classList.toggle('active', x === b));
      const tab = b.dataset.tab;
      $$('.pane').forEach(p => p.classList.toggle('active', p.dataset.pane === tab));
      side.classList.toggle('wide', tab === 'prov' || tab === 'report' || tab === 'analyze');
      const pane = $(`.pane[data-pane="${tab}"]`);
      if (tab === 'analyze' || tab === 'refine') {
        pane.appendChild(App.cfgTpl);
      }
      setTimeout(resizeCanvas, 30);
    };
  });
}

/* ------------------------------------------------------------------ */
/* 省份索引 / 表格                                                     */
/* ------------------------------------------------------------------ */
async function loadIndex() {
  try {
    const [idx, reg] = await Promise.all([
      fetch('/cache/prov_index.json').then(r => r.json()),
      fetch('/cache/regions.json').then(r => r.json()),
    ]);
    App.idx = idx; App.regions = reg;
    App.provs = Object.keys(idx).map(k => ({ id: Number(k), ...idx[k] }));
    App.provs.sort((a, b) => a.id - b.id);
    $('#mapInfo').innerHTML = `地图 <b>${App.meta.width}×${App.meta.height}</b>（显示 ${App.meta.disp_width}×${App.meta.disp_height}）<br>
      省份 <b>${App.provs.length}</b> · 战略区 <b>${(reg.sr || []).length}</b> · 州 <b>${(reg.state || []).length}</b> · 大洲 <b>${(reg.continents || []).length}</b><br>
      最大ID <b>${App.meta.max_id}</b>`;
    renderProvTable();
  } catch (e) {
    $('#mapInfo').textContent = '省份索引加载失败: ' + e.message;
  }
}

let provSort = { k: 'id', dir: 1 };
function renderProvTable() {
  if (!App.provs.length) return;
  const q = ($('#pFilter').value || '').trim().toLowerCase();
  const landOnly = $('#pLandOnly').checked;
  let rows = App.provs;
  if (landOnly) rows = rows.filter(r => r.type === 'land');
  if (q) {
    rows = rows.filter(r => {
      const c = contName(r).toLowerCase();
      return String(r.id) === q || String(r.id).includes(q) || (r.type || '').includes(q) ||
        c.includes(q) || (r.state || '').toLowerCase().includes(q) || (r.sr || '').toLowerCase().includes(q);
    });
  }
  const { k, dir } = provSort;
  rows = rows.slice().sort((a, b) => {
    let va = k === 'continent' ? contName(a) : a[k], vb = k === 'continent' ? contName(b) : b[k];
    if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * dir;
    return String(va).localeCompare(String(vb)) * dir;
  });
  const CAP = 600;
  const shown = rows.slice(0, CAP);
  $('#pCount').textContent = `匹配 ${rows.length} 个` + (rows.length > CAP ? `（仅显示前 ${CAP} 个，请细化筛选）` : '');
  const tb = $('#provTable tbody');
  tb.innerHTML = shown.map(r => `<tr data-id="${r.id}">
    <td>${r.id}</td>
    <td><span class="swatch" style="background:${rgbStr(r.color)}"></span></td>
    <td class="num">${r.area.toLocaleString()}</td>
    <td>${TYPE_CN[r.type] || r.type}</td>
    <td>${contName(r)}</td>
    <td>${r.state || ''}</td><td>${r.sr || ''}</td></tr>`).join('');
  $$('#provTable tbody tr').forEach(tr => tr.onclick = () => locateProvince(Number(tr.dataset.id)));
}

/* ------------------------------------------------------------------ */
/* 分析 / 细化 / 检查                                                  */
/* ------------------------------------------------------------------ */
const fmtErr = (e) => (e || '').split('\n').slice(-3).join('\n');

function renderAnalyze(rows) {
  const tb = $('#anaTable tbody');
  tb.innerHTML = rows.map(r => `<tr data-id="${r.prov_id}">
    <td>${r.prov_id}</td>
    <td><span class="swatch" style="background:#${r.hex}"></span></td>
    <td class="num">${r.area.toLocaleString()}</td>
    <td class="num">${r.k}</td>
    <td>${TYPE_CN[r.type] || r.type}</td>
    <td>${r.continent_name || r.continent}</td>
    <td>${r.state || ''}</td><td>${r.sr || ''}</td></tr>`).join('');
  $$('#anaTable tbody tr').forEach(tr => tr.onclick = () => locateProvince(Number(tr.dataset.id)));
}

function setBusy(which, busy) {
  const map = { analyze: ['#btnAnalyze', '#anaSpinner', '#btnCancel1'], run: ['#btnRun', '#runSpinner', '#btnCancel2'] };
  const [btn, sp, cx] = map[which];
  $(btn).disabled = busy; $(cx).disabled = !busy;
  $(sp).style.display = busy ? 'inline-flex' : 'none';
}

let activeJob = { analyze: null, run: null };

async function doAnalyze() {
  collectCfg();
  setBusy('analyze', true);
  $('#anaSummary').textContent = '运行中…';
  activeJob.analyze = await runJob('/api/analyze', collectCfg(), {
    onLog: () => { },
    onDone: (res) => {
      setBusy('analyze', false);
      const rows = res.detected || [];
      renderAnalyze(rows);
      $('#anaSummary').innerHTML = `检测到 <b>${rows.length}</b> 个待细化地块，预计新建 <b>${(res.created || []).length || '—'}</b> 个，用时 ${res.elapsed.toFixed(1)}s`;
      setMsg(`分析完成: ${rows.length} 个目标`);
    },
    onErr: (e) => { setBusy('analyze', false); $('#anaSummary').textContent = '失败: ' + fmtErr(e); setMsg('分析失败'); },
  });
}

async function doRun() {
  const cfg = collectCfg();
  if (!confirm('将开始细化并写出文件到输出目录：\n' + cfg.out_dir + '\n\n继续吗？')) return;
  setBusy('run', true);
  $('#log').textContent = '';
  $('#runSummary').textContent = '运行中…';
  $('#previews').textContent = '运行中…';
  activeJob.run = await runJob('/api/run', cfg, {
    onLog: (m) => { const l = $('#log'); l.textContent += m + '\n'; l.scrollTop = l.scrollHeight; },
    onDone: (res) => {
      setBusy('run', false);
      renderRunResult(res);
      setMsg(`细化完成: 检测 ${res.counts.detected}, 新建 ${res.counts.created}`);
    },
    onErr: (e) => { setBusy('run', false); $('#log').textContent += '\n[错误]\n' + e; setMsg('细化失败'); },
  });
}

function renderRunResult(res) {
  const created = res.created || [];
  $('#runSummary').innerHTML = `
    检测 <b>${res.counts.detected}</b> · 新建 <b>${res.counts.created}</b> · 用时 <b>${res.elapsed.toFixed(1)}s</b><br>
    输出目录 <span class="tiny">${res.out_dir || ''}</span><br>
    报告 <span class="tiny">${res.report_csv || '—'}</span><br>
    ${(res.notes && res.notes.length) ? '备注: ' + res.notes.join(' / ') : ''}
    ${created.length ? `<div class="tiny" style="margin-top:6px">新建省份（示例）: ${created.slice(0, 12).map(c => '#' + c.prov_id + '→' + (c.new_ids || []).join(',')).join('  ')}${created.length > 12 ? ' …' : ''}</div>` : ''}`;
  const pv = (p, t) => p ? `<div><div class="tiny">${t}</div><img class="previmg" src="/api/file?path=${encodeURIComponent(p)}&t=${Date.now()}"></div>` : '';
  const html = (res.preview_before || res.preview_after)
    ? `${pv(res.preview_before, '修改前')}${pv(res.preview_after, '修改后')}`
    : '<div class="tiny">未生成预览图（make_preview 关闭？）。</div>';
  $('#previews').innerHTML = html;
}

async function doAudit() {
  collectCfg();
  $('#audSpinner').style.display = 'inline-flex';
  $('#auditBody').innerHTML = '检查中…';
  await runJob('/api/audit', { mod_dir: $('#modDir').value.trim() }, {
    onLog: () => { },
    onDone: (info) => {
      $('#audSpinner').style.display = 'none';
      renderAudit(info);
    },
    onErr: (e) => { $('#audSpinner').style.display = 'none'; $('#auditBody').innerHTML = '失败: ' + fmtErr(e); },
  });
}

async function doRepair() {
  const cfg = collectCfg();
  if (!confirm('将按当前参数【只修复、不拆分】，把修正写到输出目录：\n' + cfg.out_dir + '\n\n继续吗？')) return;
  $('#btnRepair').disabled = true; $('#btnAudit').disabled = true;
  $('#repSpinner').style.display = 'inline-flex';
  const log = $('#repairLog'); log.textContent = '';
  await runJob('/api/repair', cfg, {
    onLog: (m) => { log.textContent += m + '\n'; log.scrollTop = log.scrollHeight; },
    onDone: async (res) => {
      $('#btnRepair').disabled = false; $('#btnAudit').disabled = false;
      $('#repSpinner').style.display = 'none';
      const notes = (res.notes || []).join('\n');
      log.textContent += `\n=== 完成: 检测 ${res.counts.detected}, 新建 ${res.counts.created}, 用时 ${res.elapsed.toFixed(1)}s ===\n` + notes + '\n输出: ' + (res.out_dir || '');
      log.scrollTop = log.scrollHeight;
      setMsg('修复完成');
      doAudit();
    },
    onErr: (e) => {
      $('#btnRepair').disabled = false; $('#btnAudit').disabled = false;
      $('#repSpinner').style.display = 'none';
      log.textContent += '\n[错误]\n' + e;
    },
  });
}

const AUDIT_CN = {
  provinces: '省份总数', adjacency_pairs: '相邻对', crossings: '十字交叉',
  disconnected: '不连通地块', disconnect_fragments: '孤立碎片',
  sr_files: '战略区文件', sr_dup: '战略区重复归属', sr_invalid: '战略区无效ID',
  state_files: '州文件', state_dup: '州重复归属', state_invalid: '州无效ID',
  railway_lines: '铁路线', railway_broken_lines: '铁路断连行', railway_broken_pairs: '断连区段',
  adjacency_bad: '海峡/运河异常',
  canals: '运河识别', canal_rows: '运河连线(已记录)', canal_missing: '运河缺连线',
};
function renderAudit(info) {
  const bad = (k) => /dup|invalid|broken|bad|crossings|disconnected|fragments|missing/.test(k);
  let html = '';
  for (const k of Object.keys(info)) {
    if (k.endsWith('_items') || k === 'adjacency_bad_rows') continue;
    const cls = bad(k) && info[k] > 0 ? 'bad' : (bad(k) ? 'ok' : '');
    html += `<div class="stat"><span>${AUDIT_CN[k] || k}</span><span class="badge ${cls}">${info[k]}</span></div>`;
  }
  const dupSr = info.sr_dup_items || [], dupSt = info.state_dup_items || [], ab = info.adjacency_bad_rows || [];
  const list = (title, arr, f) => arr.length ? `<div style="margin-top:8px"><b>${title}</b><div class="tiny">${arr.slice(0, 12).map(f).join('<br>')}</div></div>` : '';
  html += list('战略区重复示例', dupSr, ([i, fs]) => `#${i} → ${fs.map(x => x.split(/[\\/]/).pop()).join(', ')}`);
  html += list('州重复示例', dupSt, ([i, fs]) => `#${i} → ${fs.map(x => x.split(/[\\/]/).pop()).join(', ')}`);
  html += list('海峡/运河异常示例', ab, ([f, t, th, ty]) => `${f};${t};${ty};${th}`);
  $('#auditBody').innerHTML = html;
}

/* ------------------------------------------------------------------ */
/* 报告                                                                */
/* ------------------------------------------------------------------ */
async function loadReport(kind) {
  $('#reportBody').innerHTML = '加载中…';
  const out = $('#outDir').value.trim();
  const r = await fetch('/api/report?kind=' + kind + '&out=' + encodeURIComponent(out)).then(r => r.json());
  if (!r.exists) { $('#reportBody').innerHTML = `<div class="tiny">未找到报告文件: ${r.path}</div>`; return; }
  if (kind === 'csv') {
    const lines = r.text.split(/\r?\n/).filter(Boolean);
    const cells = lines.map(l => l.split(','));
    const head = cells[0] || [];
    const body = cells.slice(1);
    $('#reportBody').innerHTML = `<div class="tiny">${r.path} · ${body.length} 行</div>
      <div class="tblwrap"><table class="tbl"><thead><tr>${head.map(h => `<th>${h}</th>`).join('')}</tr></thead>
      <tbody>${body.slice(0, 2000).map(row => `<tr>${row.map(c => `<td>${c}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  } else {
    $('#reportBody').innerHTML = `<div class="tiny">${r.path}</div><pre class="code">${r.text.replace(/</g, '&lt;')}</pre>`;
  }
}

/* ------------------------------------------------------------------ */
/* 缓存 / 状态                                                         */
/* ------------------------------------------------------------------ */
function setMsg(m) { $('#stMsg').textContent = m; }
function setChip(ok, text) {
  const c = $('#cacheChip');
  c.className = 'chip ' + (ok ? 'ok' : 'bad');
  c.textContent = text;
}

async function refreshState() {
  const mod = $('#modDir').value.trim(), out = $('#outDir').value.trim();
  const st = await fetch(`/api/state?mod=${encodeURIComponent(mod)}&out=${encodeURIComponent(out)}`).then(r => r.json());
  if (!st.mod_exists) setChip(false, 'mod 路径无效');
  else if (st.cache_exists) {
    const m = st.cache_meta || {};
    setChip(true, `缓存就绪 · ${m.width}×${m.height} · 省份${st.provinces || '?'}`);
  } else setChip(false, '需要构建缓存');
  return st;
}

async function buildCache() {
  const mod = $('#modDir').value.trim();
  setMsg('正在构建缓存…');
  $('#btnCache').disabled = true;
  const t = await runJob('/api/cache/build', { mod_dir: mod }, {
    onLog: (m) => setMsg(m),
    onDone: async () => {
      $('#btnCache').disabled = false;
      setMsg('缓存完成');
      await boot(true);
    },
    onErr: (e) => { $('#btnCache').disabled = false; setMsg('缓存失败: ' + fmtErr(e)); alert('缓存构建失败:\n' + fmtErr(e)); },
  });
  return t;
}

/* ------------------------------------------------------------------ */
/* 启动                                                                */
/* ------------------------------------------------------------------ */
async function boot(reload) {
  App.defaults = await fetch('/api/defaults').then(r => r.json());
  if (!reload) {
    $('#modDir').value = localStorage.getItem('modDir') || App.defaults.mod_dir;
    $('#outDir').value = localStorage.getItem('outDir') || App.defaults.out_dir;
    if ($('#cfg_grad_path')) $('#cfg_grad_path').value = localStorage.getItem('gradPath') || App.defaults.grad_path;
    $('#modDir').onchange = $('#outDir').onchange = () => {
      localStorage.setItem('modDir', $('#modDir').value.trim());
      localStorage.setItem('outDir', $('#outDir').value.trim());
      refreshState();
    };
  } else {
    MapView.imgs = {}; MapView.pid = null; MapView.hl = null; MapView.inited = false;
  }
  const st = await refreshState();
  if (!st.cache_exists) {
    $('#mapInfo').innerHTML = '尚未构建缓存。点击右上角 <b>构建/刷新缓存</b> 生成地图图层。';
    return;
  }
  App.meta = await fetch('/cache/meta.json?t=' + Date.now()).then(r => r.json());
  if (!MapView.canvas) { MapView.canvas = $('#map'); MapView.ctx = MapView.canvas.getContext('2d', { willReadFrequently: true }); }
  await initMap();
  await loadIndex();
  setMsg('就绪');
}

function initUI() {
  App.cfgTpl = buildCfgForm();
  $('.pane[data-pane="refine"]').appendChild(App.cfgTpl);
  loadCfg();
  initTabs();

  $('#btnCache').onclick = buildCache;
  $('#zIn').onclick = () => { const a = $('#maparea'); zoomAt(a.clientWidth / 2, a.clientHeight / 2, 1.25); };
  $('#zOut').onclick = () => { const a = $('#maparea'); zoomAt(a.clientWidth / 2, a.clientHeight / 2, 0.8); };
  $('#zFit').onclick = fitView;
  $('#z1').onclick = () => { const a = $('#maparea'); MapView.cam.s = 1; MapView.cam.x = a.clientWidth / 2 - MapView.W / 2; MapView.cam.y = a.clientHeight / 2 - MapView.H / 2; updZoom(); requestDraw(); };

  $('#btnGoto').onclick = () => { const v = Number($('#gotoId').value); if (v > 0) locateProvince(v); };
  $('#gotoId').addEventListener('keydown', e => { if (e.key === 'Enter') $('#btnGoto').click(); });
  $('#btnClearSel').onclick = () => selectProvince(null);
  $$('#side [data-leg]').forEach(b => b.onclick = () => showLegend(b.dataset.leg));

  $('#btnAnalyze').onclick = doAnalyze;
  $('#btnRun').onclick = doRun;
  $('#btnAudit').onclick = doAudit;
  $('#btnRepair').onclick = doRepair;
  $('#btnCancel1').onclick = () => activeJob.analyze && activeJob.analyze.cancel();
  $('#btnCancel2').onclick = () => activeJob.run && activeJob.run.cancel();
  $('#btnOpenOut').onclick = () => fetch('/api/openout', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: $('#outDir').value.trim() }) });

  $('#pFilter').oninput = () => renderProvTable();
  $('#pLandOnly').onchange = () => renderProvTable();
  $$('#provTable th').forEach(th => th.onclick = () => {
    const k = th.dataset.k; if (!k) return;
    provSort = { k, dir: provSort.k === k ? -provSort.dir : 1 };
    renderProvTable();
  });
  $$('#anaTable th').forEach(th => th.onclick = () => {
    const k = th.dataset.k; if (!k) return;
    anaSort = { k, dir: anaSort.k === k ? -anaSort.dir : 1 };
    const rows = curAna.slice().sort((a, b) => {
      const va = a[k], vb = b[k];
      if (typeof va === 'number' && typeof vb === 'number') return (va - vb) * anaSort.dir;
      return String(va).localeCompare(String(vb)) * anaSort.dir;
    });
    renderAnalyze(rows);
  });
  $$('#side [data-rep]').forEach(b => b.onclick = () => loadReport(b.dataset.rep));

  boot(false);
}
let anaSort = { k: 'area', dir: -1 };
let curAna = [];
// wrap renderAnalyze to remember rows
const _renderAnalyze = renderAnalyze;
renderAnalyze = function (rows) { if (rows && rows.length !== 0) curAna = rows; _renderAnalyze(rows); };

document.addEventListener('DOMContentLoaded', initUI);
