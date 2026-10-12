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
  editMode: false, editLayer: 'state', editTarget: null, editSel: [], editOps: null, links: null, centers: null,
  mergedOut: {}, _idxState: null, _idxSr: null, newEnc: {}, newEncNext: 20000,
  ovState: {}, ovSr: {}, ovCont: {}, nsBuildings: {},
};

/* ------------------------------------------------------------------ */
/* 通用任务: POST 创建 -> SSE 订阅                                     */
/* ------------------------------------------------------------------ */

const PROG_TITLE = [
  ['/api/cache/build', '正在生成/刷新地图缓存…'],
  ['/api/edit/export', '正在导出并重建缓存…'],
  ['/api/run', '正在细化地块…'],
  ['/api/analyze', '正在分析…'],
  ['/api/audit', '正在完整性检查…'],
];
const PROG_STEP = [
  [/definition/i, 8], [/provinces/i, 18], [/下采样|显示层/i, 30], [/边界/i, 40],
  [/战略区/i, 50], [/着色层/i, 62], [/面积|prov_index/i, 78], [/中心|连线/i, 88], [/完成|导出完成/i, 100],
];
let _progTimer = null;
const _logState = new Map();
function _logAppend(el, m) {
  if (!el) return;
  let st = _logState.get(el);
  if (!st) { st = { buf: [], raf: 0 }; _logState.set(el, st); }
  st.buf.push(String(m));
  if (st.raf) return;
  st.raf = requestAnimationFrame(() => {
    st.raf = 0;
    let t = el.textContent + st.buf.join('\n') + '\n';
    st.buf.length = 0;
    if (t.length > 120000) t = t.slice(t.length - 120000);
    el.textContent = t;
    el.scrollTop = el.scrollHeight;
  });
}
function progShow(title) {
  const el = document.getElementById('bigProg'); if (!el) return;
  const ti = document.getElementById('bpTitle'); if (ti) ti.textContent = title || '处理中…';
  const tx = document.getElementById('bpTxt'); if (tx) tx.textContent = '准备中…';
  const b = document.getElementById('bpBar'); if (b) { b.parentElement.classList.add('indet'); b.style.width = '0%'; }
  el.classList.add('on');
}
function progStep(msg) {
  const el = document.getElementById('bigProg'); if (!el || !el.classList.contains('on')) return;
  const s0 = String(msg || '');
  const tx = document.getElementById('bpTxt'); if (tx) tx.textContent = s0;
  const b = document.getElementById('bpBar');
  let pct = null;
  let mm = s0.match(/(\d+)\s*\/\s*(\d+)/);
  if (mm && +mm[2] > 0) pct = Math.max(1, Math.min(99, Math.round(100 * (+mm[1]) / (+mm[2]))));
  if (pct == null) { mm = s0.match(/(\d{1,3}(?:\.\d+)?)\s*%/); if (mm) pct = Math.max(1, Math.min(100, Math.round(+mm[1]))); }
  if (pct == null) for (const [re, v] of PROG_STEP) { if (re.test(s0)) pct = v; }
  if (b && pct != null) { b.parentElement.classList.remove('indet'); b.style.width = pct + '%'; }
}
function progHide() {
  if (_progTimer) { clearTimeout(_progTimer); _progTimer = null; }
  const el = document.getElementById('bigProg'); if (el) el.classList.remove('on');
}
function progTitleFor(url) {
  for (const [u, ttl] of PROG_TITLE) { if (String(url).indexOf(u) >= 0) return ttl; }
  return '处理中…';
}
function runJob(url, body, { onLog, onDone, onErr } = {}) {
  return fetch(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  }).then(r => r.json()).then(({ task, error }) => {
    if (error || !task) { onErr && onErr(error || '任务创建失败'); return null; }
    _progTimer = setTimeout(() => progShow(progTitleFor(url)), 900);
    const es = new EventSource(`/api/task/${task}/stream`);
    let got = false;
    es.onmessage = (ev) => {
      let d; try { d = JSON.parse(ev.data); } catch { return; }
      if (d.type === 'log') { onLog && onLog(d.msg); progStep(d.msg); }
      else if (d.type === 'done') {
        es.close(); got = true; progHide();
        if (d.error) onErr && onErr(d.error); else onDone && onDone(d.result);
      }
    };
    es.onerror = () => { es.close(); progHide(); if (!got) onErr && onErr('连接中断'); };
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
    ['game_merge', 'check', 'states/战略区 与原版合并(完全模拟游戏)', false],
    ['clean_output', 'check', '细化前清空输出目录(防文件冲突)', true],
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
  o.game_dir = $('#gameDir') ? $('#gameDir').value.trim() : '';
  o.ref = ($('#edRef') ? $('#edRef').value : 'mod');
  try { localStorage.setItem(CFG_KEY, JSON.stringify(o)); } catch { }
  return o;
}

/* ------------------------------------------------------------------ */
/* 地图查看器                                                          */
/* ------------------------------------------------------------------ */
const MapView = {
  canvas: null, ctx: null, W: 0, H: 0, cam: { s: 1, x: 0, y: 0 },
  imgs: {}, pid: null, pidW: 0, pidH: 0, hl: null, tgt: null, tgtIds: null, layerMaps: {}, raf: 0, inited: false,
  layers: [
    { key: 'layers-base', name: '地形', img: 'base', vis: true, op: 1 },
    { key: 'layers-prov', name: '省份', img: 'prov', vis: false, op: 0.85 },
    { key: 'layers-cont', name: '大洲', img: 'cont', vis: false, op: 0.6 },
    { key: 'layers-sr', name: '战略区', img: 'sr', vis: false, op: 0.6 },
    { key: 'layers-state', name: '州', img: 'state', vis: false, op: 0.6 },
    { key: 'layers-edges', name: '边界', img: 'edges', vis: true, op: 0.85 },
    { key: 'layers-tgt', name: '细化目标', img: 'tgt', vis: true, op: 1 },
    { key: 'layers-rail', name: '铁路', img: 'rail', vis: false, op: 1 },
    { key: 'layers-rivers', name: '河流', img: 'rivers', vis: false, op: 0.9 },
    { key: 'layers-links', name: '海峡/运河', img: 'links', vis: false, op: 1 },
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
    MapView.pid = d.data; MapView.pidOrig = d.data.slice();
    MapView.pidW = MapView.W; MapView.pidH = MapView.H;
  } catch (e) { setMsg('省份ID图加载失败: ' + e.message); }

  try { MapView.imgs.rivers = await loadImg('/cache/overlay_rivers.png' + bust); }
  catch (e) { MapView.imgs.rivers = null; }
  try { App.links = await fetch('/cache/links.json' + bust).then(r => r.json()); }
  catch (e) { App.links = null; }
  try { App.centers = await fetch('/cache/prov_centers.json' + bust).then(r => r.json()); }
  catch (e) { App.centers = null; }

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
  if (App.editMode) {
    const layer = App.editLayer;
    const provImg = MapView.imgs.prov;
    if (provImg) { ctx.globalAlpha = 1; ctx.drawImage(provImg, x, y, dw, dh); }   // 底层=地块色(海洋也显地块)
    if (layer !== 'prov') {
      const ov = MapView.imgs[{ state: 'stateA', sr: 'srA', continent: 'contA' }[layer]];
      if (ov) ctx.drawImage(ov, x, y, dw, dh);                                     // 上半=图层色(有归属处)
    }
    if (MapView.efill) ctx.drawImage(MapView.efill, x, y, dw, dh);
    if (MapView.tgtHL) ctx.drawImage(MapView.tgtHL, x, y, dw, dh);
    if (MapView.esel) ctx.drawImage(MapView.esel, x, y, dw, dh);
    if (App.editLayer === 'rail') drawRails(ctx);
    if (App.editLayer === 'terrain' && App.terrainCanvas) ctx.drawImage(App.terrainCanvas, x, y, dw, dh);
    const bm = edBorderData();
    if (bm) drawBorders(ctx, 0.95, bm);
    return;
  }
  for (const L of MapView.layers) {
    if (L.img === 'edges' || L.img === 'tgt' || L.img === 'links' || L.img === 'rail') continue;   // 单独绘制
    const im = MapView.imgs[L.img];
    if (!L.vis || !im || L.op <= 0) continue;
    ctx.globalAlpha = L.op;
    ctx.drawImage(im, x, y, dw, dh);
  }
  ctx.globalAlpha = 1;
  if (MapView.efill) ctx.drawImage(MapView.efill, x, y, dw, dh);
  if (MapView.hl) ctx.drawImage(MapView.hl, x, y, dw, dh);
  if (MapView.esel) ctx.drawImage(MapView.esel, x, y, dw, dh);
  const tgtL = MapView.layers.find(l => l.img === 'tgt');
  if (tgtL && tgtL.vis && MapView.tgt) {
    ctx.globalAlpha = tgtL.op;
    ctx.drawImage(MapView.tgt, x, y, dw, dh);
    ctx.globalAlpha = 1;
  }
  const linkL = MapView.layers.find(l => l.img === 'links');
  if (linkL && linkL.vis && App.links && App.centers) drawLinks(ctx, linkL.op);
  const railL = MapView.layers.find(l => l.img === 'rail');
  if (railL && railL.vis && App.railData) { drawRails(ctx); }
  const borderL = MapView.layers.find(l => l.img === 'edges');
  if (borderL && borderL.vis && MapView.pid) drawBorders(ctx, borderL.op);
}

/* 等宽边界：直接在屏幕空间按像素计算，线宽与缩放无关（始终约1个屏幕像素）。 */
function drawLinks(ctx, alpha) {
  const { s, x, y } = MapView.cam;
  const C = App.centers;
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.lineWidth = 1.6;
  for (const lk of App.links) {
    const cf = C[lk.f], ct = C[lk.t];
    if (!cf || !ct) continue;
    const cth = (lk.th > 0 && C[lk.th]) ? C[lk.th] : null;
    ctx.strokeStyle = lk.canal ? '#ffb000' : (lk.type === 'impassable' ? '#ff5a5a' : '#42d0ff');
    ctx.beginPath();
    ctx.moveTo(x + cf[0] * s, y + cf[1] * s);
    if (cth) ctx.lineTo(x + cth[0] * s, y + cth[1] * s);
    ctx.lineTo(x + ct[0] * s, y + ct[1] * s);
    ctx.stroke();
    ctx.fillStyle = ctx.strokeStyle;
    for (const c of [cf, ct]) {
      ctx.beginPath(); ctx.arc(x + c[0] * s, y + c[1] * s, 2.2, 0, 6.2832); ctx.fill();
    }
  }
  ctx.restore();
}

function drawBorders(ctx, alpha, arr) {
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
  const pid = arr || MapView.pid, W = MapView.pidW, H = MapView.pidH;
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
  if (MapView.eventsBound) return;   // 防止 boot() 多次调用导致监听器重复绑定(点一次弹多次)
  MapView.eventsBound = true;
  const area = $('#maparea'), c = MapView.canvas;
  clampCam();
  const _uiHit = (e) => e.target && e.target.closest && e.target.closest('#layers,#maptools,#findbox,#legend,#tgtBox,#tooltip');
  area.addEventListener('wheel', (e) => {
    if (_uiHit(e)) return;
    e.preventDefault();
    const rect = c.getBoundingClientRect();
    zoomAt(e.clientX - rect.left, e.clientY - rect.top, e.deltaY < 0 ? 1.15 : 1 / 1.15);
  }, { passive: false });

  let drag = null;
  area.addEventListener('mousedown', (e) => {
    if (e.button !== 0 || _uiHit(e)) return;
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
    window._lastX = e.clientX; window._lastY = e.clientY;
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
      if (App.editMode && id > 0) edOnClick(id, e.shiftKey);
      else if (id > 0) selectProvince(id); else selectProvince(null);
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

function buildTargets(ids) {
  if (!MapView.pid || !ids || !ids.size) { MapView.tgt = null; return; }
  const W = MapView.pidW, H = MapView.pidH, d = MapView.pid;
  const oc = document.createElement('canvas'); oc.width = W; oc.height = H;
  const octx = oc.getContext('2d');
  const out = octx.createImageData(W, H);
  const o = out.data;
  const mask = new Uint8Array(W * H);
  for (let p = 0, i = 0; p < W * H; p++, i += 4) {
    const pid = d[i] | (d[i + 1] << 8) | (d[i + 2] << 16);
    if (ids.has(pid)) mask[p] = 1;
  }
  for (let p = 0, i = 0; p < W * H; p++, i += 4) {
    if (!mask[p]) continue;
    const x = p % W, y = (p / W) | 0;
    let edge = (x === 0 || x === W - 1 || y === 0 || y === H - 1);
    if (!edge) {
      for (const q of [p - 1, p + 1, p - W, p + W]) {
        const qi = q * 4;
        const qid = d[qi] | (d[qi + 1] << 8) | (d[qi + 2] << 16);
        if (qid !== (d[i] | (d[i + 1] << 8) | (d[i + 2] << 16))) { edge = true; break; }
      }
    }
    if (edge) { o[i] = 255; o[i + 1] = 215; o[i + 2] = 0; o[i + 3] = 255; }
    else { o[i] = 255; o[i + 1] = 70; o[i + 2] = 70; o[i + 3] = 90; }
  }
  octx.putImageData(out, 0, 0);
  MapView.tgt = oc; MapView.tgtIds = ids;
}

function setTargets(rows) {
  const list = rows || [];
  const ids = new Set(list.map(r => Number(r.prov_id)));
  buildTargets(ids);
  const box = $('#tgtBox'), body = $('#tgtBody');
  if (box && body) {
    if (ids.size) {
      body.innerHTML = list.slice().sort((a, b) => b.area - a.area).map(r =>
        `<div class="lg" data-id="${r.prov_id}"><span class="swatch" style="background:#${r.hex}"></span>`
        + `<span>#${r.prov_id} · ${r.area}px · ×${r.k}</span></div>`).join('');
      $$('#tgtBody .lg').forEach(el => el.onclick = () => locateProvince(Number(el.dataset.id)));
      box.style.display = 'block';
    } else {
      body.innerHTML = '<div class="tiny">无</div>';
    }
  }
  const chip = $('#stTargets'); if (chip) chip.textContent = ids.size ? (ids.size + ' 个') : '无';
  if (ids.size) setLayer('tgt', true);
  requestDraw();
}

function clearTargets(silent) {
  MapView.tgt = null; MapView.tgtIds = null;
  const box = $('#tgtBox'); if (box) box.style.display = 'none';
  const chip = $('#stTargets'); if (chip) chip.textContent = '无';
  if (!silent) setMsg('已清除细化目标');
  requestDraw();
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
async function depCheck() {
  const el = $('#depInfo');
  try {
    const r = await fetch('/api/deps').then(r => r.json());
    if (!r.ok) { if (el) el.textContent = '检测失败: ' + (r.error || ''); return; }
    const miss = [];
    if (!r.flask) miss.push('flask');
    if (!r.numpy) miss.push('numpy');
    if (!r.pillow) miss.push('pillow(PIL)');
    window._depMissing = miss;
    if (el) el.innerHTML = 'Python <b>' + r.python + '</b> · flask ' + (r.flask ? '✅' : '❌') +
      ' · numpy ' + (r.numpy ? '✅' : '❌') + ' · Pillow ' + (r.pillow ? '✅' : '❌') +
      (miss.length ? '　<b style="color:#ffd23f">缺少: ' + miss.join(', ') + '</b>（选好源后点"一键安装"）' : '　全部就绪 ✅');
  } catch (e) { if (el) el.textContent = '检测失败: ' + e.message; }
}
async function depInstall() {
  const el = $('#depInfo');
  const miss = window._depMissing || [];
  if (!miss.length) { setMsg('依赖已齐全，无需安装'); depCheck(); return; }
  const pkgs = miss.map(m => m.indexOf('pillow') === 0 ? 'pillow' : m);
  const mirror = $('#depMirror') ? $('#depMirror').value : '';
  if (el) el.textContent = '正在安装 ' + pkgs.join(', ') + '（' + (mirror || '官方源') + '）… 请稍候（可能 1-2 分钟）';
  try {
    const r = await fetch('/api/deps/install', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ packages: pkgs, mirror: mirror || null }),
    }).then(r => r.json());
    if (r.ok) { setMsg('依赖安装完成'); } else {
      setMsg('安装失败: ' + (r.error || ('pip 退出码 ' + r.code)));
      if (el) el.innerHTML = '安装失败（可换一个镜像源重试）：<br><span style="color:#ff9aa2">' +
        String(r.err || r.out || r.error || '').slice(-600) + '</span>';
    }
  } catch (e) { setMsg('安装失败: ' + e.message); }
  depCheck();
}
const FIND_CN = { prov: '地块', state: 'state', sr: '战略区', continent: '大洲' };
function _regionNum(v) { const m = String(v || '').match(/^(\d+)/); return m ? Number(m[1]) : null; }
function fitToIds(ids) {
  const C = App.centers || {};
  let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9, n = 0;
  for (const id of ids) {
    const c = C[String(id)]; if (!c) continue;
    n++;
    if (c[0] < x0) x0 = c[0]; if (c[0] > x1) x1 = c[0];
    if (c[1] < y0) y0 = c[1]; if (c[1] > y1) y1 = c[1];
  }
  if (!n || !MapView.cam) return;
  const area = $('#maparea'); const W = area.clientWidth, H = area.clientHeight;
  const bw = Math.max(x1 - x0, 8), bh = Math.max(y1 - y0, 8);
  let sc = Math.min((W - 80) / bw, (H - 80) / bh);
  sc = Math.max(0.15, Math.min(50, sc));
  MapView.cam.s = sc;
  MapView.cam.x = W / 2 - sc * (x0 + x1) / 2;
  MapView.cam.y = H / 2 - sc * (y0 + y1) / 2;
  if (typeof clampCam === 'function') clampCam();
  if (typeof updZoom === 'function') updZoom();
  requestDraw();
}
function doFind() {
  const q = ($('#findQuery') ? $('#findQuery').value : '').trim();
  const type = ($('#findType') ? $('#findType').value : 'prov') || 'prov';
  if (!q) return;
  const rows = App.provs || [];
  const num = /^\d+$/.test(q) ? Number(q) : null;
  const ql = q.toLowerCase();
  let hits = [];
  if (type === 'prov')         hits = rows.filter(r => String(r.id) === q);
  else if (type === 'state')   hits = rows.filter(r => num != null ? _regionNum(r.state) === num : String(r.state || '').toLowerCase().indexOf(ql) >= 0);
  else if (type === 'sr')      hits = rows.filter(r => num != null ? _regionNum(r.sr) === num : String(r.sr || '').toLowerCase().indexOf(ql) >= 0);
  else                         hits = rows.filter(r => num != null ? Number(r.continent) === num : contName(r).toLowerCase().indexOf(ql) >= 0);
  if (!hits.length) { setMsg('未找到匹配：' + q + '（' + (FIND_CN[type] || type) + '，按编号精确匹配）'); buildESel([]); return; }
  hits.sort((a, b) => a.id - b.id);
  const ids = hits.map(r => r.id);
  App.findIds = ids;
  buildESel(ids);            // 标记所有命中的地块
  fitToIds(ids);             // 缩放到命中范围
  App.sel = ids[0];
  edSelInfo && edSelInfo();
  setMsg('匹配 ' + hits.length + ' 个' + (FIND_CN[type] || '') + '（编号 ' + q + '），已全部标出' + (hits.length > 1 ? '并缩放到范围' : ''));
}
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

/* ------------------------------------------------------------------ */
/* 省份编辑                                                            */
/* ------------------------------------------------------------------ */
function editPost(url, body) {
  const cfg = collectCfg();
  return fetch(url, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(Object.assign({}, cfg, body)),
  }).then(r => r.json());
}
function opText(o) {
  if (o.kind === 'merge') return `合并: #${o.from.join(',')} → #${o.into}`;
  if (o.kind === 'mem') {
    const n = (o.pids && o.pids.length) || 1;
    return `${o.level === 'state' ? '州' : '战略区'}: ${n > 1 ? (n + ' 块') : ('#' + o.pid)} ${o.op === 'add' ? '加入' : '移出'} ${o.file}`;
  }
  if (o.kind === 'continent') return `大洲: #${o.pid} → ${o.value}`;
  if (o.kind === 'terrain') return `地形: #${o.pid} → ${o.value}`;
  if (o.kind === 'new_state') return `新建州: ${o.file}`;
  return o.kind;
}
function showEditOps(status) {
  App.editOps = status;
  const el = $('#edOps'); if (!el) return;
  const ops = (status && status.ops) || [];
  el.textContent = ops.length ? ops.map((o, i) => `${i + 1}. ${opText(o)}`).join('\n') : '（暂无编辑）';
}
function renderEditSel() {
  const el = $('#edSel'); if (!el) return;
  el.innerHTML = App.editSel.length
    ? App.editSel.map((id, i) => `<span class="chip">${i === 0 ? '★' : ''}#${id}</span>`).join(' ')
    : '未选中（编辑模式下点击选择，Shift 加选）';
}
function editToggleSelect(id, additive) {
  id = Number(id); if (!id) return;
  const i = App.editSel.indexOf(id);
  if (additive) { if (i >= 0) App.editSel.splice(i, 1); else App.editSel.push(id); }
  else { App.editSel = (i >= 0 && App.editSel.length === 1) ? [] : [id]; }
  renderEditSel(); buildESel();
}
/* 本地可视化: 被并入的省份像素改涂目标颜色 (只改显示用 pid 副本) */
function paintMerge(into, fromArr) {
  if (!MapView.pid) return;
  const W = MapView.pidW, H = MapView.pidH, d = MapView.pid;
  const fromset = new Set((fromArr || []).map(Number));
  const info = App.idx && App.idx[String(into)];
  const col = info ? info.color : [255, 255, 255];
  if (!MapView.efill) {
    const oc = document.createElement('canvas'); oc.width = W; oc.height = H;
    MapView.efill = oc; MapView.efctx = oc.getContext('2d');
    MapView.efimg = MapView.efctx.createImageData(W, H);
  }
  const o = MapView.efimg.data;
  for (let p = 0, i = 0; p < W * H; p++, i += 4) {
    const pid = d[i] | (d[i + 1] << 8) | (d[i + 2] << 16);
    if (fromset.has(pid)) {
      d[i] = into & 255; d[i + 1] = (into >> 8) & 255; d[i + 2] = (into >> 16) & 255;
      o[i] = col[0]; o[i + 1] = col[1]; o[i + 2] = col[2]; o[i + 3] = 255;
    }
  }
  MapView.efctx.putImageData(MapView.efimg, 0, 0);
  requestDraw();
}
function rebuildLocalEdits(ops) {
  if (!MapView.pidOrig) return;
  MapView.pid.set(MapView.pidOrig);
  MapView.efill = null; MapView.efctx = null; MapView.efimg = null;
  for (const op of (ops || [])) if (op.kind === 'merge') paintMerge(op.into, op.from);
  requestDraw();
}
function buildESel(ids) {
  ids = ids || App.editSel;
  if (!ids.length || !MapView.pid) { MapView.esel = null; requestDraw(); return; }
  const W = MapView.pidW, H = MapView.pidH, d = MapView.pid;
  const set = new Set(ids.map(Number));
  const oc = document.createElement('canvas'); oc.width = W; oc.height = H;
  const octx = oc.getContext('2d');
  const out = octx.createImageData(W, H); const o = out.data;
  const mask = new Uint8Array(W * H);
  for (let p = 0, i = 0; p < W * H; p++, i += 4) {
    const pid = d[i] | (d[i + 1] << 8) | (d[i + 2] << 16);
    if (set.has(pid)) mask[p] = 1;
  }
  for (let p = 0, i = 0; p < W * H; p++, i += 4) {
    if (!mask[p]) continue;
    const x = p % W, y = (p / W) | 0;
    let edge = (x === 0 || x === W - 1 || y === 0 || y === H - 1);
    if (!edge) {
      const pid = d[i] | (d[i + 1] << 8) | (d[i + 2] << 16);
      for (const q of [p - 1, p + 1, p - W, p + W]) {
        const qi = q * 4;
        const qid = d[qi] | (d[qi + 1] << 8) | (d[qi + 2] << 16);
        if (qid !== pid) { edge = true; break; }
      }
    }
    if (edge) { o[i] = 0; o[i + 1] = 240; o[i + 2] = 255; o[i + 3] = 255; }
    else { o[i] = 0; o[i + 1] = 200; o[i + 2] = 255; o[i + 3] = 70; }
  }
  octx.putImageData(out, 0, 0);
  MapView.esel = oc; requestDraw();
}
async function editRefresh() {
  try {
    const cfg = collectCfg();
    const qs = new URLSearchParams(cfg);
    const r = await fetch('/api/edit/status?' + qs.toString()).then(r => r.json());
    if (r.ok) { showEditOps(r.status); edReplay(r.status.ops); edFillTargets(); }
    else setMsg('编辑状态读取失败: ' + r.error);
  } catch (e) { setMsg('编辑状态读取失败: ' + e.message); }
}
async function doEditMerge() {
  if (App.editSel.length < 2) { setMsg('请先选 ≥2 个省份（第一个为目标，Shift 加选）'); return; }
  const into = App.editSel[0], ids = App.editSel.slice(1);
  const r = await editPost('/api/edit/merge', { into, ids });
  if (r.ok) { paintMerge(into, ids); App.editSel = [into]; renderEditSel(); buildESel(); showEditOps(r.status); setMsg(`已把 #${ids.join(',')} 并入 #${into}`); }
  else setMsg('合并失败: ' + r.error);
}
async function doEditDelete() {
  if (!App.editSel.length) { setMsg('先选一个省份'); return; }
  const id = App.editSel[0];
  if (!confirm(`删除 #${id}（并入其最大邻居）？`)) return;
  const r = await editPost('/api/edit/delete', { id });
  if (r.ok) { paintMerge(r.into, [id]); App.editSel = []; renderEditSel(); buildESel(); showEditOps(r.status); setMsg(`#${id} 已并入 #${r.into}`); }
  else setMsg('删除失败: ' + r.error);
}
async function doEditMove(kind) {
  if (!App.editSel.length) { setMsg('先选一个省份'); return; }
  const to = $(kind === 'state' ? '#edState' : '#edSr').value.trim();
  if (!to) { setMsg('请填写目标'); return; }
  const r = await editPost('/api/edit/move', { pid: App.editSel[0], kind, to });
  if (r.ok) { showEditOps(r.status); setMsg(`#${App.editSel[0]} 已改归属`); }
  else setMsg('改归属失败: ' + r.error);
}
async function doEditContinent() {
  if (!App.editSel.length) { setMsg('先选一个省份'); return; }
  const v = $('#edCont').value.trim();
  if (!v) { setMsg('请填大洲ID'); return; }
  const r = await editPost('/api/edit/continent', { pid: App.editSel[0], value: v });
  if (r.ok) { showEditOps(r.status); setMsg(`#${App.editSel[0]} 大洲 → ${v}`); }
  else setMsg('设置失败: ' + r.error);
}
async function doEditSimple(kind) {
  const r = await editPost('/api/edit/' + kind, {});
  if (r.ok) { edReplay(r.status.ops); App.editSel = []; renderEditSel(); edSelInfo(); buildESel(); showEditOps(r.status); setMsg(kind === 'undo' ? '已撤销' : kind === 'redo' ? '已重做' : '已重置'); }
  else setMsg('操作失败: ' + r.error);
}
async function doEditExport() {
  const cfg = collectCfg();
  if (!confirm('导出编辑结果到输出目录：\n' + cfg.out_dir + '\n并重建地图缓存？')) return;
  const log = $('#edOps'); log.textContent = '';
  await runJob('/api/edit/export', cfg, {
    onLog: (m) => { _logAppend(log, m); },
    onDone: async (res) => {
      log.textContent += `\n=== 导出完成: 移除 ${(res.removed || []).length} 个省份, 操作 ${res.ops} 个, ID 重命名 ${res.renamed} 处 ===`;
      setMsg('编辑已导出, 地图已刷新');
      App.editSel = []; renderEditSel(); buildESel();
      await boot(true);
      await editRefresh();
    },
    onErr: (e) => { log.textContent += '\n[错误]\n' + e; },
  });
}


/* ================= 图层式编辑 ================= */
const NONE_RGB = [42, 46, 54];
function edRegions(kind) { return (App.regions && App.regions[kind]) || []; }
function edMatch(cur, tgt) {
  if (!cur || !tgt) return false;
  const c = String(cur).replace(/\.txt$/i, ''), t = String(tgt).replace(/\.txt$/i, '');
  if (c === t) return true;
  const cp = c.split('-')[0], tp = t.split('-')[0];
  return cp === t || tp === c || (cp && cp === tp);
}
function edLayerColor(layer, name) {
  if (!name) return NONE_RGB;
  if (layer === 'state' || layer === 'sr') {
    const arr = edRegions(layer);
    const i = arr.findIndex(n => edMatch(n, name));
    if (i >= 0) return pal(Math.max(arr.length, 1))[i];
    const code = (layer === 'state') ? edStateEnc(name) : edSrEnc(name);
    return SPARE_RGB[code % SPARE_RGB.length];
  }
  if (layer === 'continent') {
    const arr = edRegions('continents');
    const v = parseInt(name, 10);
    return (v >= 1 && v <= arr.length) ? pal(Math.max(arr.length, 1))[v - 1] : NONE_RGB;
  }
  return NONE_RGB;
}
function edBaseImg() { return ({ prov: 'prov', state: 'state', sr: 'sr', continent: 'cont' })[App.editLayer] || 'prov'; }
function edBorderData() {
  if (App.editLayer === 'sr') return MapView.layerMaps.state || MapView.pid;
  if (App.editLayer === 'continent') return MapView.layerMaps.sr || MapView.pid;
  return MapView.pid;
}
async function edLoadLayerMaps() {
  for (const key of ['state', 'sr', 'continent']) {
    if (MapView.layerMaps[key]) continue;
    try {
      const im = await loadImg('/cache/layer_' + key + '.png?t=' + Date.now());
      const oc = document.createElement('canvas'); oc.width = MapView.W; oc.height = MapView.H;
      const cx = oc.getContext('2d', { willReadFrequently: true }); cx.imageSmoothingEnabled = false;
      cx.drawImage(im, 0, 0, MapView.W, MapView.H);
      MapView.layerMaps[key] = cx.getImageData(0, 0, MapView.W, MapView.H).data;
    } catch (e) { }
  }
  for (const [key, img] of [['stateA', 'state'], ['srA', 'sr'], ['contA', 'continent']]) {
    if (MapView.imgs[key]) continue;
    try { MapView.imgs[key] = await loadImg('/cache/overlay_' + img + '_a.png?t=' + Date.now()); }
    catch (e) { MapView.imgs[key] = null; }
  }
  MapView.layerMaps.sr_map = MapView.layerMaps.state;
}
function _norm(v) { return (v === undefined || v === null) ? '' : String(v).replace(/\.txt$/i, ''); }
function edCur(pid, layer) {
  const s = String(pid);
  if (layer === 'state') return _norm((pid in App.ovState) ? App.ovState[pid] : ((App.idx[s] || {}).state || ''));
  if (layer === 'sr') return _norm((pid in App.ovSr) ? App.ovSr[pid] : ((App.idx[s] || {}).sr || ''));
  if (layer === 'continent') return (pid in App.ovCont) ? App.ovCont[pid] : ((App.idx[s] || {}).continent || '0');
  return '';
}
function paintProvPid(pid, rgb) {
  if (!MapView.pid) return;
  const W = MapView.pidW, H = MapView.pidH, d = MapView.pid;
  pid = Number(pid);
  if (!MapView.efill) {
    const oc = document.createElement('canvas'); oc.width = W; oc.height = H;
    MapView.efill = oc; MapView.efctx = oc.getContext('2d');
    MapView.efimg = MapView.efctx.createImageData(W, H);
  }
  const o = MapView.efimg.data;
  for (let p = 0, i = 0; p < W * H; p++, i += 4) {
    const v = d[i] | (d[i + 1] << 8) | (d[i + 2] << 16);
    if (v === pid) { o[i] = rgb[0]; o[i + 1] = rgb[1]; o[i + 2] = rgb[2]; o[i + 3] = 255; }
  }
  MapView.efctx.putImageData(MapView.efimg, 0, 0);
  requestDraw();
}
function edRepaint(pid) { edRepaintMany([pid]); }
function edRepaintMany(pids) {
  const layer = App.editLayer;
  if (!MapView.pid || layer === 'prov' || !pids || !pids.length) return;
  const W = MapView.pidW, H = MapView.pidH, d = MapView.pid;
  const colOf = new Map();
  for (const p of pids) { const c = edCur(p, layer); colOf.set(Number(p), c ? edLayerColor(layer, c) : NONE_RGB); }
  if (!MapView.efill) {
    const oc = document.createElement('canvas'); oc.width = W; oc.height = H;
    MapView.efill = oc; MapView.efctx = oc.getContext('2d');
    MapView.efimg = MapView.efctx.createImageData(W, H);
  }
  const o = MapView.efimg.data;
  for (let p = 0, i = 0; p < W * H; p++, i += 4) {
    const v = d[i] | (d[i + 1] << 8) | (d[i + 2] << 16);
    const c = colOf.get(v);
    if (c) { o[i] = c[0]; o[i + 1] = c[1]; o[i + 2] = c[2]; o[i + 3] = 255; }
  }
  MapView.efctx.putImageData(MapView.efimg, 0, 0);
  requestDraw();
}
function edInfo(pid) {
  const info = App.idx[String(pid)];
  if (!info) return '';
  return `#${pid} · ${TYPE_CN[info.type] || info.type} · 州 ${edCur(pid, 'state') || '—'}`
    + ` · 战略区 ${edCur(pid, 'sr') || '—'} · 大洲 ${contName(info)}`;
}
function edSelInfo() { const el = $('#edInfo'); if (el) el.textContent = App.editSel.length ? edInfo(App.editSel[0]) : ''; }
const LAYER_CN = { prov: '地块', state: '州', sr: '战略区', continent: '大洲' };
function edSame(layer, a, b) {
  if (a === undefined || a === null || a === '' || b === undefined || b === null || b === '') return false;
  if (layer === 'continent') return String(a) === String(b);
  return edMatch(a, b);
}
function edUpdateTargetUI() {
  const el = $('#edTarget'); if (!el) return;
  el.textContent = '当前目标：' + (App.editTarget || '未选择') + '（图层：' + (LAYER_CN[App.editLayer] || '') + '）';
}

const SPARE_RGB = [[255, 90, 90], [90, 200, 255], [255, 200, 90], [150, 255, 120], [220, 130, 255], [120, 255, 220], [255, 150, 220], [180, 180, 255]];
function _newEnc(name) { const k = _norm(name); if (!(k in App.newEnc)) App.newEnc[k] = App.newEncNext++; return App.newEnc[k]; }
function edStateEnc(name) { if (!name) return 0; const a = edRegions('state'); const i = a.findIndex(n => edMatch(n, name)); return i >= 0 ? i + 1 : _newEnc(name); }
function edSrEnc(name) { if (!name) return 0; const a = edRegions('sr'); const i = a.findIndex(n => edMatch(n, name)); return i >= 0 ? i + 1 : _newEnc(name); }
function edContEnc(v) { const n = parseInt(v, 10); return (n >= 1 && n <= edRegions('continents').length) ? n : 0; }
function edPatchLayerMap(key, pids, enc) {
  const arr = MapView.layerMaps[key]; if (!arr) return;
  const W = MapView.pidW, H = MapView.pidH, d = MapView.pid;
  const set = new Set(pids.map(Number));
  const b0 = enc & 255, b1 = (enc >> 8) & 255, b2 = (enc >> 16) & 255;
  for (let p = 0, i = 0; p < W * H; p++, i += 4) {
    const v = d[i] | (d[i + 1] << 8) | (d[i + 2] << 16);
    if (set.has(v)) { arr[i] = b0; arr[i + 1] = b1; arr[i + 2] = b2; }
  }
}
function edSyncLayerMaps() {
  const W = MapView.pidW, H = MapView.pidH, d = MapView.pid;
  if (!d) return;
  const one = (key, encFn, store) => {
    const arr = MapView.layerMaps[key]; if (!arr) return;
    const enc = new Map();
    for (const k in store) enc.set(Number(k), encFn(store[k]));
    for (const k in App.mergedOut) {
      const into = App.mergedOut[k];
      enc.set(Number(k), encFn(edCur(into, key === 'state' ? 'state' : key === 'sr' ? 'sr' : 'continent')));
    }
    if (!enc.size) return;
    for (let p = 0, i = 0; p < W * H; p++, i += 4) {
      const v = d[i] | (d[i + 1] << 8) | (d[i + 2] << 16);
      const e = enc.get(v);
      if (e !== undefined) { arr[i] = e & 255; arr[i + 1] = (e >> 8) & 255; arr[i + 2] = (e >> 16) & 255; }
    }
  };
  one('state', edStateEnc, App.ovState);
  one('sr', edSrEnc, App.ovSr);
  one('continent', edContEnc, App.ovCont);
  requestDraw();
}
function edIndexBy() {
  if (App._idxState) return;
  const byState = {}, bySr = {};
  for (const p of App.provs) {
    if (p.state) (byState[p.state] = byState[p.state] || []).push(p.id);
    if (p.sr) (bySr[p.sr] = bySr[p.sr] || []).push(p.id);
  }
  App._idxState = byState; App._idxSr = bySr;
}
async function edFillNextIds() {
  try {
    const q = new URLSearchParams(collectCfg()).toString();
    const [a, b, c] = await Promise.all([
      fetch('/api/edit/next_id?' + q + '&which=state').then(r => r.json()),
      fetch('/api/edit/next_id?' + q + '&which=sr').then(r => r.json()),
      fetch('/api/edit/next_id?' + q + '&which=prov').then(r => r.json()),
    ]);
    if (a.ok && $('#nsId')) $('#nsId').value = a.next;
    if (b.ok && $('#nsrId') && !$('#nsrId').value) $('#nsrId').value = b.next;
    const info = $('#nextIdInfo');
    if (info) {
      const f = (x, nm) => nm + ' ' + (x.ok ? (x.next + (x.reused ? '（复用空闲）' : '')) : '?');
      info.textContent = f(a, '州') + ' · ' + f(b, '战略区') + ' · ' + f(c, '省份') + '（空闲ID优先复用；创建前会再核对一次）';
    }
  } catch (e) { }
}

function edBuildTargetHL() {
  if (!MapView.pid || !App.editMode || App.editLayer === 'prov' || !App.editTarget) {
    MapView.tgtHL = null; requestDraw(); return;
  }
  const layer = App.editLayer;
  const key = layer === 'state' ? 'state' : (layer === 'sr' ? 'sr' : 'continent');
  const src = MapView.layerMaps[key];
  if (!src) { MapView.tgtHL = null; return; }
  const enc = layer === 'state' ? edStateEnc(App.editTarget)
    : (layer === 'sr' ? edSrEnc(App.editTarget) : edContEnc(App.editTarget));
  const W = MapView.pidW, H = MapView.pidH;
  const oc = document.createElement('canvas'); oc.width = W; oc.height = H;
  const octx = oc.getContext('2d'); const out = octx.createImageData(W, H); const o = out.data;
  const mask = new Uint8Array(W * H);
  for (let p = 0, i = 0; p < W * H; p++, i += 4) {
    const v = src[i] | (src[i + 1] << 8) | (src[i + 2] << 16);
    if (enc && v === enc) mask[p] = 1;
  }
  for (let p = 0, i = 0; p < W * H; p++, i += 4) {
    if (!mask[p]) continue;
    const x = p % W, y = (p / W) | 0;
    const edge = (x === 0 || x === W - 1 || y === 0 || y === H - 1) ||
      !mask[p - 1] || !mask[p + 1] || !mask[p - W] || !mask[p + W];
    if (edge) { o[i] = 255; o[i + 1] = 235; o[i + 2] = 120; o[i + 3] = 255; }
    else { o[i] = 255; o[i + 1] = 235; o[i + 2] = 120; o[i + 3] = 36; }
  }
  octx.putImageData(out, 0, 0);
  MapView.tgtHL = oc; requestDraw();
}
async function edOnClick(pid, shift) {
  if (!MapView.inited) return;
  if (!App.idx) { setMsg('省份索引尚未就绪，请稍候…'); return; }
  const layer = App.editLayer;
  if (layer === 'terrain') {
    App.editSel = [pid]; App.sel = pid; renderEditSel(); edSelInfo(); buildESel();
    const nm = (App.terrain && App.terrain.prov) ? App.terrain.prov[String(pid)] : '';
    if ($('#terCur')) $('#terCur').textContent = '地块 #' + pid + ' 当前地形: ' + (nm || '(无)');
    if (nm && $('#terSel')) $('#terSel').value = nm;
    setMsg('已选地块 #' + pid + ' 地形 ' + (nm || '-'));
    return;
  }
  if (layer === 'rail') {
    const rect = MapView.canvas.getBoundingClientRect();
    const w = screenToWorld((window._lastX || 0) - rect.left, (window._lastY || 0) - rect.top);
    if (shift) {
      if (App.editSel.length >= 2) App.editSel = [];
      if (!App.editSel.includes(pid)) App.editSel.push(pid);
      renderEditSel(); edSelInfo(); buildESel();
      if (App.editSel.length === 2) {
        await edRailCreate();
        App.editSel = []; renderEditSel(); edSelInfo(); buildESel();
      } else {
        setMsg('已选 1 块，再 Shift 点第二块会自动创建铁路');
      }
      return;
    }
    const hit = railHit(w.x, w.y);
    if (hit) { App.railSel = hit.i; if ($('#railLevel')) $('#railLevel').value = hit.level; if ($('#railInfo')) $('#railInfo').textContent = '铁路 #' + hit.i + ' 等级 ' + hit.level + '（' + hit.nodes.length + ' 节点）'; requestDraw(); setMsg('已选中铁路 等级=' + hit.level); return; }
    const _hasSup = !!(App.railData && (App.railData.supplies || []).some(s => s.pid === pid));
    await edSupToggleAt(pid, _hasSup ? 'remove' : 'add');
    return;
  }
  if (layer === 'prov') {
    if (shift) { if (!App.editSel.includes(pid)) App.editSel.push(pid); }
    else { App.editSel = [pid]; App.sel = pid; }
    renderEditSel(); edSelInfo(); buildESel(); return;
  }
  const cur = edCur(pid, layer);
  if (!shift) {                                  // 点击 = 选择目标 / 空地块=新建
    if (layer === 'continent' && String(cur) === '0') {
      setMsg('海洋没有大洲；先点一个陆地省份来选大洲目标'); return;
    }
    if (!cur) {
      const info = App.idx[String(pid)] || {};
      if (layer === 'state' && (info.type === 'land' || info.type === 'lake')) {
        if (!confirm(`地块 #${pid} 不属于任何州，直接新建一个州并放入它？`)) return;
        const r = await editPost('/api/edit/new_state', {
          id: 0, provinces: [pid],
          owner: ($('#nsOwner') ? $('#nsOwner').value.trim() : ''),
          manpower: Number(($('#nsManpower') && $('#nsManpower').value) || 0),
          category: (($('#nsCategory') && $('#nsCategory').value) || 'rural').trim(),
        });
        if (r.ok) {
          showEditOps(r.status);
          App.editTarget = String(r.file).replace(/\.txt$/i, '');
          edUpdateTargetUI();
          await edReplay(r.status.ops);
          edBuildTargetHL(); await edLoadStateData(); edFillNextIds();
          setMsg('已在 #' + pid + ' 新建州 ' + r.file + '（可在“编辑州”里改数据）');
        } else setMsg('新建州失败: ' + r.error);
      } else if (layer === 'sr') {
        if (!confirm(`地块 #${pid} 不属于任何战略区，直接新建战略区并放入它？`)) return;
        const nid = Number(($('#nsrId') && $('#nsrId').value) || 0);
        const r = await editPost('/api/edit/new_sr', { id: nid, provinces: [pid] });
        if (r.ok) {
          showEditOps(r.status); App.editTarget = String(r.file).replace(/\.txt$/i, '');
          edUpdateTargetUI(); await edReplay(r.status.ops); edBuildTargetHL();
          setMsg('已新建战略区 ' + r.file);
        } else setMsg('新建战略区失败: ' + r.error);
      } else {
        setMsg('该地块不属于任何' + LAYER_CN[layer] + '（先点一个有归属的地块）');
      }
      return;
    }
    App.editTarget = String(cur);
    edUpdateTargetUI(); edBuildTargetHL();
    if (layer === 'state') await edLoadStateData();
    if (layer === 'sr') await edLoadSrData();
    setMsg('已选目标' + LAYER_CN[layer] + '：' + cur);
    return;
  }
  if (!App.editTarget) { setMsg('先点击一个地块来选择目标' + LAYER_CN[layer]); return; }
  if (layer === 'state') {
    const info = App.idx[String(pid)] || {};
    if (info.type === 'sea') { setMsg('海洋地块不能放入州（只能陆地/湖泊）'); return; }
    const op = edSame(layer, cur, App.editTarget) ? 'remove' : 'add';
    const r = await editPost('/api/edit/assign', { level: 'state', pid, target: App.editTarget, op });
    if (r.ok) { App.ovState[pid] = (op === 'add') ? App.editTarget : ''; edRepaint(pid); edSyncLayerMaps(); edBuildTargetHL(); showEditOps(r.status); setMsg(op === 'add' ? `#${pid} 加入 ${App.editTarget}` : `#${pid} 移出 ${App.editTarget}`); }
    else setMsg('失败: ' + r.error);
    return;
  }
  if (layer === 'sr') {                       // 战略区层: 以“整个州”为单位
    const st = edCur(pid, 'state');
    if (!st) { setMsg('该地块不在任何州，无法按州加入战略区'); return; }
    edIndexBy();
    const pids = App._idxState[_norm(st)] || [pid];
    const allIn = pids.length > 0 && pids.every(p => edSame('sr', edCur(p, 'sr'), App.editTarget));
    const op = allIn ? 'remove' : 'add';
    const r = await editPost('/api/edit/assign_many', { level: 'sr', pids, target: App.editTarget, op });
    if (r.ok) {
      for (const p of pids) App.ovSr[p] = (op === 'add') ? App.editTarget : '';
      edRepaintMany(pids);
      edSyncLayerMaps(); edBuildTargetHL(); showEditOps(r.status);
      setMsg(`${st}（${pids.length} 块）${op === 'add' ? '加入' : '移出'} 战略区 ${App.editTarget}`);
    } else setMsg('失败: ' + r.error);
    return;
  }
  if (layer === 'continent') {                // 大洲层: 以“整个战略区”为单位
    const sr = edCur(pid, 'sr');
    if (!sr) { setMsg('该地块不属于任何战略区，无法按战略区改大洲'); return; }
    edIndexBy();
    const pids = (App._idxSr[_norm(sr)] || [pid]).filter(p => { const t = (App.idx[String(p)] || {}).type; return t === 'land'; });
    if (!pids.length) { setMsg('该战略区没有陆地地块'); return; }
    const r = await editPost('/api/edit/assign_many', { level: 'continent', pids, target: App.editTarget, op: 'set' });
    if (r.ok) {
      for (const p of pids) App.ovCont[p] = App.editTarget;
      edRepaintMany(pids);
      edSyncLayerMaps(); edBuildTargetHL(); showEditOps(r.status);
      setMsg(`战略区 ${sr} 的 ${pids.length} 块陆地 → 大洲 ${App.editTarget}`);
    } else setMsg('失败: ' + r.error);
  }
}



/* ================= 全量记忆输入 ================= */
function saveInputs() {
  const o = {};
  document.querySelectorAll('#topbar input,#topbar select,#side input,#side select,#side textarea').forEach(el => {
    if (!el.id) return;
    o[el.id] = (el.type === 'checkbox') ? el.checked : el.value;
  });
  try { localStorage.setItem('uiState', JSON.stringify(o)); } catch (e) { }
}
function loadInputs() {
  let o = {};
  try { o = JSON.parse(localStorage.getItem('uiState') || '{}'); } catch (e) { }
  for (const k in o) {
    const el = document.getElementById(k);
    if (!el) continue;
    if (el.type === 'checkbox') el.checked = !!o[k]; else el.value = o[k];
  }
}
/* ================= 战略区数据 / 地块附加 ================= */

/* ================= 铁路 / 补给 图层 ================= */
function editParams() {
  const v = (id) => { const e = document.getElementById(id); return e ? e.value.trim() : ''; };
  return { mod_dir: v('modDir'), game_dir: v('gameDir'), out_dir: v('outDir'),
    game_merge: (document.getElementById('cfg_game_merge') && document.getElementById('cfg_game_merge').checked) ? 1 : 0,
    ref: (document.getElementById('edRef') ? document.getElementById('edRef').value : 'mod') };
}

/* ================= 地块地形 ================= */
async function edLoadTerrainData() {
  try {
    const r = await fetch('/api/edit/terrain_data?' + new URLSearchParams(editParams()).toString()).then(r => r.json());
    if (!r.ok) { setMsg('读取地形失败: ' + r.error); return; }
    App.terrain = r.data || { defs: {}, prov: {}, names: [] };
    if ($('#terSel')) $('#terSel').innerHTML = (App.terrain.names || []).map(n => `<option>${n}</option>`).join('');
    if ($('#terInfo')) $('#terInfo').textContent = '陆地 ' + Object.keys(App.terrain.prov || {}).length + ' 块 · 与 definition 一致 ' + (App.terrain.agree || 0) + '/' + (App.terrain.total || 0);
    _buildTerrainOverlay();
    requestDraw();
  } catch (e) { setMsg('读取地形失败: ' + e.message); }
}
function _buildTerrainOverlay() {
  if (!MapView.pid || !App.terrain || !App.terrain.defs) return;
  const W = MapView.pidW, H = MapView.pidH, d = MapView.pid;
  const defs = App.terrain.defs, prov = App.terrain.prov || {};
  const cache = new Map();
  const colOf = (pid) => {
    let c = cache.get(pid);
    if (c !== undefined) return c;
    const nm = prov[String(pid)];
    const rgb = nm && defs[nm];
    c = rgb ? rgb : null; cache.set(pid, c); return c;
  };
  const oc = document.createElement('canvas'); oc.width = W; oc.height = H;
  const octx = oc.getContext('2d'); const img = octx.createImageData(W, H); const o = img.data;
  for (let p = 0, i = 0; p < W * H; p++, i += 4) {
    const pid = d[i] | (d[i + 1] << 8) | (d[i + 2] << 16);
    const c = pid ? colOf(pid) : null;
    if (c) { o[i] = c[0]; o[i + 1] = c[1]; o[i + 2] = c[2]; o[i + 3] = 255; }
    else { o[i] = 20; o[i + 1] = 22; o[i + 2] = 26; o[i + 3] = 255; }
  }
  octx.putImageData(img, 0, 0);
  App.terrainCanvas = oc;
}
async function edTerrainSave() {
  if (!App.editSel.length) { setMsg('先在“地块地形”图层点一个地块'); return; }
  const pid = App.editSel[0], nm = $('#terSel') ? $('#terSel').value : '';
  if (!nm) { setMsg('先选地形'); return; }
  const r = await editPost('/api/edit/assign', { level: 'terrain', pid, target: nm, op: 'set' });
  if (r.ok) { if (App.terrain && App.terrain.prov) App.terrain.prov[String(pid)] = nm; _buildTerrainOverlay(); showEditOps(r.status); setMsg('#' + pid + ' 地形 → ' + nm); }
  else setMsg('失败: ' + r.error);
}
async function edTerrainExport() {
  const r = await editPost('/api/edit/export_terrain', {});
  if (r.ok) { setMsg('已导出 terrain.bmp: ' + (r.result && r.result.path)); alert('已生成并导出：\n' + ((r.result && r.result.path) || '')); }
  else setMsg('导出失败: ' + r.error);
}
async function edTerrainAuto() {
  if (!confirm('用 terrain.bmp 自动生成所有陆地地块的地形（会覆盖 definition.csv 的地形列）？')) return;
  const r = await editPost('/api/edit/terrain_auto', {});
  if (r.ok) { showEditOps(r.status); await edLoadTerrainData(); setMsg('已自动生成地形：' + (r.count || '') + ' 块'); }
  else setMsg('失败: ' + r.error);
}

async function edLoadRailData() {
  try {
    const r = await fetch('/api/edit/rail_data?' + new URLSearchParams(editParams()).toString()).then(r => r.json());
    if (!r.ok) { setMsg('读取铁路失败: ' + r.error); return; }
    App.railData = r.data || { rails: [], supplies: [], edges: [], land: [] };
    App.railLand = new Set(((App.railData.land) || []).map(Number));
    if ($('#railStat')) $('#railStat').textContent = App.railData.rails.length + ' 条铁路 · ' + App.railData.supplies.length + ' 个补给站';
    requestDraw();
  } catch (e) { setMsg('读取铁路失败: ' + e.message); }
}
function _lvColor(l) {
  return ['#e0a53c', '#e0a53c', '#7ac06a', '#5aa9e6', '#c07ae0', '#e06c6c'][Math.min(Math.max(l, 0), 6)];
}
function _drawAdjEdges(ctx) {
  const E = (App.railData && App.railData.edges) || [];
  if (!E.length) return;
  const C = App.centers;
  const { s, x, y } = MapView.cam;
  const st = { strait: ['#33d6ff', [7, 5]], canal: ['#ffd23f', [3, 4]], impassable: ['#ff4d4d', [2, 7]] };
  ctx.save(); ctx.lineWidth = 1.8; ctx.lineCap = 'round';
  for (const e of E) {
    const stl = st[e.type] || ['#9aa0a6', [3, 3]];
    const P = e.path;
    if (e.type === 'impassable' && P && P.length > 1) {
      // 不可通行: 沿两省公共边界的屏障折线(细化后会按新省界重算并重画)
      ctx.setLineDash([]); ctx.strokeStyle = '#ff3b3b'; ctx.lineWidth = 2.6;
      ctx.beginPath();
      ctx.moveTo(x + P[0][0] * s, y + P[0][1] * s);
      for (let k = 1; k < P.length; k++) ctx.lineTo(x + P[k][0] * s, y + P[k][1] * s);
      ctx.stroke();
      ctx.lineWidth = 1.8;
      continue;
    }
    const a = C[String(e.a)], b = C[String(e.b)];
    if (!a || !b) continue;
    ctx.setLineDash(stl[1]); ctx.strokeStyle = stl[0];
    ctx.beginPath();
    ctx.moveTo(x + a[0] * s, y + a[1] * s);
    ctx.lineTo(x + b[0] * s, y + b[1] * s);
    ctx.stroke();
  }
  ctx.restore();
}
function drawRails(ctx) {
  if (!App.railData || !App.centers) return;
  _drawAdjEdges(ctx);
  const { s, x, y } = MapView.cam, C = App.centers;
  const area = $('#maparea');
  const pad = 60 / s;
  const vx0 = (-x) / s - pad, vy0 = (-y) / s - pad;
  const vx1 = (area.clientWidth - x) / s + pad, vy1 = (area.clientHeight - y) / s + pad;
  ctx.save();
  ctx.lineCap = 'round';
  let drawn = 0;
  for (const rl of App.railData.rails) {
    let bx0 = 1e9, by0 = 1e9, bx1 = -1e9, by1 = -1e9;
    for (const n of rl.nodes) { const c = C[String(n)]; if (!c) continue;
      if (c[0] < bx0) bx0 = c[0]; if (c[0] > bx1) bx1 = c[0];
      if (c[1] < by0) by0 = c[1]; if (c[1] > by1) by1 = c[1]; }
    if (bx1 < vx0 || bx0 > vx1 || by1 < vy0 || by0 > vy1) continue;   // 视野外跳过
    if (++drawn > 900) break;
    const sel = (App.railSel === rl.i);
    ctx.strokeStyle = sel ? '#ffffff' : _lvColor(rl.level);
    ctx.lineWidth = sel ? 3.2 : (1.2 + 0.5 * rl.level);
    ctx.beginPath();
    let first = true;
    for (const n of rl.nodes) {
      const c = C[String(n)];
      if (!c) { first = true; continue; }
      const px = x + c[0] * s, py = y + c[1] * s;
      if (first) { ctx.moveTo(px, py); first = false; } else ctx.lineTo(px, py);
    }
    ctx.stroke();
  }
  ctx.fillStyle = '#f2d27a';
  for (const sp of App.railData.supplies) {
    const c = C[String(sp.pid)]; if (!c) continue;
    if (c[0] < vx0 || c[0] > vx1 || c[1] < vy0 || c[1] > vy1) continue;
    const px = x + c[0] * s, py = y + c[1] * s;
    ctx.beginPath(); ctx.arc(px, py, 3.2, 0, 6.2832); ctx.fill();
    ctx.strokeStyle = '#5a4a20'; ctx.lineWidth = 1; ctx.stroke();
  }
  ctx.restore();
  if ($('#railStat')) $('#railStat').textContent = drawn + ' 条铁路(视野内) · ' + App.railData.supplies.length + ' 个补给站';
}
function railHit(wx, wy) {
  if (!App.railData || !App.centers) return null;
  const C = App.centers;
  const tol = 8 / MapView.cam.s;
  let best = null, bestD = tol;
  for (const rl of App.railData.rails) {
    let prev = null;
    for (const n of rl.nodes) {
      const c = C[String(n)];
      if (!c) { prev = null; continue; }
      if (prev) {
        const x1 = prev[0], y1 = prev[1], x2 = c[0], y2 = c[1];
        const dx = x2 - x1, dy = y2 - y1, L2 = dx * dx + dy * dy;
        let t = L2 > 0 ? ((wx - x1) * dx + (wy - y1) * dy) / L2 : 0;
        t = Math.max(0, Math.min(1, t));
        const px = x1 + t * dx, py = y1 + t * dy;
        const d = Math.hypot(wx - px, wy - py);
        if (d < bestD) { bestD = d; best = rl; }
      }
      prev = c;
    }
  }
  return best;
}
async function edRailLevelSave() {
  if (App.railSel == null) { setMsg('先点击地图上的铁路选中它'); return; }
  const r = await editPost('/api/edit/rail_level', { i: App.railSel, level: Number($('#railLevel').value || 1) });
  if (r.ok) { showEditOps(r.status); await edLoadRailData(); setMsg('铁路等级已保存'); } else setMsg('失败: ' + r.error);
}
async function edRailDel() {
  if (App.railSel == null) { setMsg('先选中铁路'); return; }
  const r = await editPost('/api/edit/rail_rm', { i: App.railSel });
  if (r.ok) { App.railSel = null; showEditOps(r.status); await edLoadRailData(); setMsg('铁路已删除'); } else setMsg('失败: ' + r.error);
}
async function edRailCreate() {
  if (App.railLand && App.railLand.size && App.editSel.some(p => !App.railLand.has(Number(p)))) {
    setMsg('海上/湖泊地块不能放铁路（只能选陆地）'); return;
  }
  if (App.editSel.length < 2) { setMsg('先在铁路图层点两个地块（第二个 Shift 点）'); return; }
  const lv = Number(($('#railNewLevel') ? $('#railNewLevel').value : 0) || 1);
  const r = await editPost('/api/edit/rail', { a: App.editSel[0], b: App.editSel[1], op: 'add', level: lv });
  if (r.ok) { showEditOps(r.status); await edLoadRailData(); setMsg('已新建铁路（等级 ' + lv + '）'); } else setMsg('失败: ' + r.error);
}
async function edSupToggleAt(pid, op) {
  if (op === 'add' && App.railLand && App.railLand.size && !App.railLand.has(Number(pid))) {
    setMsg('海上/湖泊地块不能放补给中心（只能选陆地）'); return;
  }
  const r = await editPost('/api/edit/supply', { pid, op });
  if (r.ok) { showEditOps(r.status); await edLoadRailData(); setMsg(op === 'add' ? ('#' + pid + ' 加了补给站') : ('#' + pid + ' 删了补给站')); }
  else setMsg('失败: ' + r.error);
}

async function edSupToggle(op) {
  if (!App.editSel.length) { setMsg('先点一个地块'); return; }
  const r = await editPost('/api/edit/supply', { pid: App.editSel[0], op });
  if (r.ok) { showEditOps(r.status); await edLoadRailData(); setMsg(op === 'add' ? '已加补给站' : '已删补给站'); } else setMsg('失败: ' + r.error);
}

async function edLoadSrData() {
  if (App.editLayer !== 'sr' || !App.editTarget) { const e = $('#srCur'); if (e) e.textContent = '未选择战略区'; return; }
  try {
    const r = await fetch('/api/edit/sr_data?' + new URLSearchParams(Object.assign(editParams(), { file: App.editTarget })).toString()).then(r => r.json());
    if (!r.ok) { setMsg('读取战略区失败: ' + r.error); if ($('#srCur')) $('#srCur').textContent = '读取失败: ' + r.error; return; }
    const d = r.data || {};
    if ($('#srCur')) $('#srCur').textContent = '当前战略区: ' + d.file;
    if ($('#srName')) $('#srName').value = d.name || '';
    if ($('#srWeather')) $('#srWeather').value = d.weather || '';
    if ($('#srProv')) $('#srProv').textContent = d.provinces + ' 个地块';
  } catch (e) { setMsg('读取战略区失败: ' + e.message); }
}
async function edSaveSrData() {
  if (App.editLayer !== 'sr' || !App.editTarget) { setMsg('先选中一个战略区'); return; }
  const r = await editPost('/api/edit/set_sr', { file: App.editTarget, name: $('#srName').value.trim(), weather: $('#srWeather').value });
  if (r.ok) { showEditOps(r.status); setMsg('已保存战略区数据: ' + App.editTarget); }
  else setMsg('保存失败: ' + r.error);
}
async function edExtra(kind, op) {
  if (kind === 'rail') {
    if (App.editSel.length < 2) { setMsg('先选 2 个地块（点一个再 Shift 点另一个）'); return; }
    const r = await editPost('/api/edit/rail', { a: App.editSel[0], b: App.editSel[1], op });
    if (r.ok) { showEditOps(r.status); setMsg(op === 'add' ? '已连铁路' : '已断铁路'); } else setMsg('失败: ' + r.error);
    return;
  }
  if (!App.editSel.length) { setMsg('先选一个地块'); return; }
  const pid = App.editSel[0];
  if (kind === 'supply') {
    const r = await editPost('/api/edit/supply', { pid, op });
    if (r.ok) { showEditOps(r.status); setMsg(op === 'add' ? '已加补给站' : '已删补给站'); } else setMsg('失败: ' + r.error);
    return;
  }
  if (kind === 'building') {
    const key = $('#edBldKey') ? $('#edBldKey').value : '';
    if (!key) { setMsg('先选建筑类型'); return; }
    const cen = App.centers && App.centers[String(pid)];
    const wx = cen ? cen[0] * (App.meta ? App.meta.width / App.meta.disp_width : 2) : 0;
    const wy = cen ? cen[1] * (App.meta ? App.meta.height / App.meta.disp_height : 2) : 0;
    const r = await editPost('/api/edit/building', { pid, key, wx, wy, op });
    if (r.ok) { showEditOps(r.status); setMsg('已加建筑 ' + key + ' @#' + pid); } else setMsg('失败: ' + r.error);
  }
}
function edSyncPanels() {
  const L = App.editLayer;
  if ($('#fsState')) $('#fsState').style.display = (L === 'state') ? '' : 'none';
  if ($('#fsSr')) $('#fsSr').style.display = (L === 'sr') ? '' : 'none';
  if ($('#fsExtra')) $('#fsExtra').style.display = (L === 'prov' || L === 'state') ? '' : 'none';
  if ($('#fsRail')) $('#fsRail').style.display = (L === 'rail') ? '' : 'none';
  if ($('#fsNewSr')) $('#fsNewSr').style.display = (L === 'sr') ? '' : 'none';
  if ($('#fsProv')) $('#fsProv').style.display = (L === 'prov') ? '' : 'none';
  if ($('#fsTerrain')) $('#fsTerrain').style.display = (L === 'terrain') ? '' : 'none';
  if (L === 'terrain' && !App.terrain) edLoadTerrainData();
  if (L === 'sr') edLoadSrData();
  if (L === 'rail') edLoadRailData();
}

async function edLoadStateData() {
  if (App.editLayer !== 'state' || !App.editTarget) { if ($('#nsCur')) $('#nsCur').textContent = '未选择州'; if ($('#nsEditId')) $('#nsEditId').value = ''; return; }
  try {
    const r = await fetch('/api/edit/state_data?' + new URLSearchParams(Object.assign(editParams(), { file: App.editTarget })).toString()).then(r => r.json());
    if (!r.ok) { setMsg('读取州数据失败: ' + r.error); if ($('#nsCur')) $('#nsCur').textContent = '读取失败: ' + r.error; return; }
    const d = r.data || {};
    if ($('#nsCur')) $('#nsCur').textContent = '当前州: ' + d.file;
    if ($('#nsEditId')) $('#nsEditId').value = d.id;
    const set = (id, v) => { const e = $(id); if (e) e.value = v; };
    set('#nsOwner', d.owner || ''); set('#nsManpower', d.manpower || 0); set('#nsCategory', d.category || 'rural');
    const b = d.buildings || {};
    set('#nsInfra', b.infrastructure || 0); set('#nsInd', b.industrial_complex || 0);
    set('#nsArms', b.arms_factory || 0); set('#nsDock', b.dockyard || 0);
    App.nsBuildings = {};
    for (const k in b) {
      if (!['infrastructure', 'industrial_complex', 'arms_factory', 'dockyard'].includes(k)) App.nsBuildings[k] = b[k];
    }
    edRenderNsBuildings();
  } catch (e) { setMsg('读取州数据失败: ' + e.message); }
}
async function edSaveStateData() {
  if (App.editLayer !== 'state' || !App.editTarget) { setMsg('先选中一个州'); return; }
  const buildings = Object.assign({}, App.nsBuildings || {}, {
    infrastructure: Number($('#nsInfra').value || 0),
    industrial_complex: Number($('#nsInd').value || 0),
    arms_factory: Number($('#nsArms').value || 0),
    dockyard: Number($('#nsDock').value || 0),
  });
  const data = {
    owner: ($('#nsOwner').value || '').trim(),
    manpower: Number($('#nsManpower').value || 0),
    category: ($('#nsCategory').value || 'rural').trim(),
    buildings,
  };
  const r = await editPost('/api/edit/set_state', { file: App.editTarget, data });
  if (r.ok) { showEditOps(r.status); setMsg('已保存州数据: ' + App.editTarget); }
  else setMsg('保存失败: ' + r.error);
}

async function edCreateNewSr() {
  if (!App.editSel.length) { setMsg('先选中要放进新战略区的地块'); return; }
  let sid = Number(($('#nsrId') ? $('#nsrId').value : 0) || 0);
  let _reused = false;
  try {                                    // 创建前按当前数据核对 ID(空闲ID优先)
    const q = new URLSearchParams(collectCfg()).toString();
    const nx = await fetch('/api/edit/next_id?' + q + '&which=sr').then(r => r.json());
    if (nx.ok) {
      if (!sid || sid < nx.next) { if (sid && sid > 0) setMsg('战略区 ID ' + sid + ' 已被占用或过小，改用 ' + nx.next); sid = nx.next; }
      if (sid === nx.next) _reused = !!nx.reused;
      if ($('#nsrId')) $('#nsrId').value = sid;
    }
  } catch (e) { }
  const r = await editPost('/api/edit/new_sr', { id: sid, provinces: App.editSel.slice() });
  if (r.ok) {
    setMsg('新建战略区 ' + r.file + '（ID=' + sid + (_reused ? '，复用空闲ID' : '') + '）');
    App.editSel = []; renderEditSel(); edSelInfo(); buildESel();
    showEditOps(r.status);
    await edReplay(r.status.ops);
    App.editTarget = r.file.replace(/\.txt$/i, ''); edUpdateTargetUI(); edBuildTargetHL();
  } else setMsg('新建战略区失败: ' + r.error);
}

function edReplay(ops) {
  MapView.efill = null; MapView.efctx = null; MapView.efimg = null;
  App.ovState = {}; App.ovSr = {}; App.ovCont = {}; App.mergedOut = {};
  if (MapView.pidOrig) MapView.pid.set(MapView.pidOrig);
  for (const op of (ops || [])) {
    if (op.kind === 'merge') { paintMerge(op.into, op.from); for (const f of op.from) App.mergedOut[f] = op.into; }
    else if (op.kind === 'mem' && op.level === 'state') { for (const p of (op.pids || [op.pid])) App.ovState[p] = (op.op === 'add') ? op.file : ''; }
    else if (op.kind === 'mem' && op.level === 'sr') { for (const p of (op.pids || [op.pid])) App.ovSr[p] = (op.op === 'add') ? op.file : ''; }
    else if (op.kind === 'continent') { for (const p of (op.pids || [op.pid])) App.ovCont[p] = op.value; }
  }
  if (App.editMode && App.editLayer !== 'prov') {
    const ids = new Set([...Object.keys(App.ovState), ...Object.keys(App.ovSr), ...Object.keys(App.ovCont)]);
    for (const pid of ids) edRepaint(Number(pid));
  }
  edSyncLayerMaps();
  edBuildTargetHL();
  requestDraw();
}
function edFillTargets() {
  const st = edRegions('state'), sr = edRegions('sr'), ct = edRegions('continents');
  const mk = (el, arr) => { if (el) el.innerHTML = arr.map(v => `<option value="${v}">`).join(''); };
  mk($('#edStateList'), st); mk($('#edSrList'), sr);
  if ($('#edContList')) $('#edContList').innerHTML = ct.map((n, i) => `<option value="${i + 1}">${n}</option>`).join('');
}
function edFillBuildings() {
  fetch('/api/edit/assets?' + new URLSearchParams(collectCfg()).toString())
    .then(r => r.json()).then(r => {
      if (!r.ok) return;
      const sel = $('#nsBuilding'); if (!sel) return;
      App.nsAssets = r.assets;
      if ($('#edBldKey')) $('#edBldKey').innerHTML = (r.assets.buildings || []).map(b => `<option>${b}</option>`).join('');
      sel.innerHTML = (r.assets.buildings || []).map(b => `<option>${b}</option>`).join('');
      const tl = $('#nsTerrain'); // optional
    }).catch(() => { });
}
function edRenderNsBuildings() {
  const el = $('#nsBuildings'); if (!el) return;
  const b = App.nsBuildings || {};
  el.textContent = Object.keys(b).length ? Object.entries(b).map(([k, v]) => `${k}=${v}`).join(', ') : '—';
}
async function edCreateNewState() {
  if (!App.editSel.length) { setMsg('先在编辑模式下选中要放进新州的地块'); return; }
  const bk = Object.assign({}, App.nsBuildings || {});
  const payload = {
    id: 0,
    provinces: App.editSel.slice(),
    owner: ($('#nsOwner').value || '').trim(),
    manpower: Number($('#nsManpower').value || 0),
    category: ($('#nsCategory').value || 'rural').trim(),
    infrastructure: Number($('#nsInfra').value || 0),
    industrial_complex: Number($('#nsInd').value || 0),
    arms_factory: Number($('#nsArms').value || 0),
    dockyard: Number($('#nsDock').value || 0),
    buildings: bk,
  };
  const r = await editPost('/api/edit/new_state', payload);
  if (r.ok) {
    setMsg('新建州 ' + r.file);
    App.nsBuildings = {}; edRenderNsBuildings();
    App.editSel = []; renderEditSel(); edSelInfo(); buildESel();
    showEditOps(r.status);
    await edReplay(r.status.ops);
    App.editTarget = r.file.replace(/\.txt$/i, ''); edUpdateTargetUI(); edBuildTargetHL();
  } else setMsg('新建州失败: ' + r.error);
}

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
      const _em = $('#editMode');
      if (tab === 'edit') { if (_em && !App.editMode) { _em.checked = true; _em.onchange(); } editRefresh(); }
      else if (_em && App.editMode) { _em.checked = false; _em.onchange(); }
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
  App._idxState = null; App._idxSr = null;
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
  if (!$('#provTable') || !App.provs.length) return;
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
  setTargets(rows);
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
    onLog: (m) => { _logAppend($('#log'), m); },
    onDone: (res) => {
      setBusy('run', false);
      renderRunResult(res);
      setMsg(`细化完成: 检测 ${res.counts.detected}, 新建 ${res.counts.created}`);
    },
    onErr: (e) => { setBusy('run', false); $('#log').textContent += '\n[错误]\n' + e; setMsg('细化失败'); },
  });
}

function renderRunResult(res) {
  clearTargets(true);   // 地图已变化, 旧目标高亮失效
  const created = res.created || [];
  $('#runSummary').innerHTML = `
    检测 <b>${res.counts.detected}</b> · 新建 <b>${res.counts.created}</b> · 用时 <b>${res.elapsed.toFixed(1)}s</b><br>
    输出目录 <span class="tiny">${res.out_dir || ''}</span><br>
    报告 <span class="tiny">${res.report_csv || '—'}</span><br>
    ${(res.notes && res.notes.length) ? '备注: ' + res.notes.join(' / ') : ''}
    ${created.length ? `<div class="tiny" style="margin-top:6px">新建省份（示例）: ${created.slice(0, 12).map(c => '#' + c.prov_id + '→' + (c.new_ids || []).join(',')).join('  ')}${created.length > 12 ? ' …' : ''}</div>` : ''}`;
  const pb = res.preview_before ? `/api/file?path=${encodeURIComponent(res.preview_before)}&t=${Date.now()}` : '';
  const pa = res.preview_after ? `/api/file?path=${encodeURIComponent(res.preview_after)}&t=${Date.now()}` : '';
  if (pb && pa) {
    $('#previews').innerHTML = '<div class="cmp">'
      + `<img class="cmp-img" src="${pb}">`
      + `<img class="cmp-img cmp-after" src="${pa}" style="clip-path:inset(0 50% 0 0)">`
      + '<div class="cmp-line" style="left:50%"></div>'
      + '<input type="range" class="cmp-range" min="0" max="100" value="50">'
      + '</div><div class="tiny">拖动滑块对比：左=修改后，右=修改前</div>';
    const rng = $('#previews .cmp-range');
    rng.oninput = () => {
      const v = Number(rng.value);
      $('#previews .cmp-after').style.clipPath = `inset(0 ${100 - v}% 0 0)`;
      $('#previews .cmp-line').style.left = v + '%';
    };
  } else if (pb || pa) {
    const p = pb || pa, t = pb ? '修改前' : '修改后';
    $('#previews').innerHTML = `<div class="tiny">${t}</div><img class="previmg" src="${p}">`;
  } else {
    $('#previews').innerHTML = '<div class="tiny">未生成预览图（make_preview 关闭？）。</div>';
  }
}

async function doAudit() {
  collectCfg();
  $('#audSpinner').style.display = 'inline-flex';
  $('#auditBody').innerHTML = '检查中…';
  await runJob('/api/audit', { mod_dir: $('#modDir').value.trim(), game_dir: ($('#gameDir') ? $('#gameDir').value.trim() : '') }, {
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
    onLog: (m) => { _logAppend(log, m); },
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
  disconnected: '地块不连通', disconnect_fragments: '孤立碎片',
  state_split: '州不连续', sr_split: '战略区不连续',
  impassable_rows: '不可通行条目(地图自带,仅供参考)', impassable_bad: '不可通行异常',
  sr_files: '战略区文件', sr_dup: '战略区重复归属', sr_invalid: '战略区无效ID',
  state_files: '州文件', state_dup: '州重复归属', state_invalid: '州无效ID',
  railway_lines: '铁路线', railway_broken_lines: '铁路断连行', railway_broken_pairs: '断连区段',
  adjacency_bad: '海峡/运河异常',
  canals: '运河识别', canal_rows: '运河连线(已记录)', canal_missing: '运河缺连线',
  ref_supply_nodes_total: '补给点引用', ref_supply_nodes_invalid: '补给点悬空引用',
  ref_buildings_total: '建筑位置引用', ref_buildings_invalid: '建筑悬空引用',
  ref_vp_total: '胜利点引用', ref_vp_invalid: '胜利点悬空引用',
};
function renderAudit(info) {
  const bad = (k) => /dup|invalid|broken|bad|crossings|disconnected|fragments|missing|split/.test(k);
  const WARN = /^(adjacency_bad|impassable_bad|impassable_rows|canal_missing|canal_rules_unresolved|canal_required_provinces_to_remove|canal_bank_provinces_to_add)$/;
  let html = '<p class="hint">红色=需要处理；黄色=海峡/运河/不可通行预警，仅供参考，需<b>手动</b>修复。</p>';
  for (const k of Object.keys(info)) {
    if (k.endsWith('_items') || k.endsWith('_src') || k === 'adjacency_bad_rows' || k === 'ref_invalid_examples') continue;
    let cls = '';
    if (WARN.test(k)) cls = info[k] > 0 ? 'warn' : 'ok';
    else if (bad(k)) cls = info[k] > 0 ? 'bad' : 'ok';
    html += `<div class="stat"><span>${AUDIT_CN[k] || k}</span><span class="badge ${cls}">${info[k]}</span></div>`;
  }
  const dupSr = info.sr_dup_items || [], dupSt = info.state_dup_items || [], ab = info.adjacency_bad_rows || [];
  const stSplit = info.state_split_items || [], srSplit = info.sr_split_items || [];
  const list = (title, arr, f) => arr.length ? `<div style="margin-top:8px"><b>${title}</b><div class="tiny">${arr.slice(0, 12).map(f).join('<br>')}</div></div>` : '';
  html += list('战略区重复示例', dupSr, ([i, fs]) => `#${i} → ${fs.map(x => x.split(/[\\/]/).pop()).join(', ')}`);
  html += list('州重复示例', dupSt, ([i, fs]) => `#${i} → ${fs.map(x => x.split(/[\\/]/).pop()).join(', ')}`);
  if (info.adjacencies_src) html += `<div class="tiny" style="margin-top:6px">海峡/运河/不可通行 统计来源：<b>${info.adjacencies_src}</b></div>`;
  html += list('海峡/运河/不可通行异常示例（黄色预警，仅供参考，需手动修复）', ab, ([f, t, th, ty]) => `${f};${t};${ty};${th}`);
  html += list('州不连续示例（州内省份未连成一片）', stSplit, ([f, n, c]) => `${f} → ${n} 片 / ${c} 块`);
  html += list('战略区不连续示例', srSplit, ([f, n, c]) => `${f} → ${n} 片 / ${c} 块`);
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
  const game = $('#gameDir') ? $('#gameDir').value.trim() : '';
  const st = await fetch(`/api/state?mod=${encodeURIComponent(mod)}&out=${encodeURIComponent(out)}&game=${encodeURIComponent(game)}`).then(r => r.json());
  if (!mod) setChip(true, '仅原版（编辑原版）');
  else if (!st.mod_exists) setChip(false, 'mod 路径无效');
  else if (st.cache_exists) {
    const m = st.cache_meta || {};
    setChip(true, `缓存就绪 · ${m.width}×${m.height} · 省份${st.provinces || '?'}`);
  } else setChip(false, '需要构建缓存');
  return st;
}

async function buildCache() {
  const mod = $('#modDir').value.trim();
  const game = $('#gameDir') ? $('#gameDir').value.trim() : '';
  setMsg('正在构建缓存…');
  $('#btnCache').disabled = true;
  const t = await runJob('/api/cache/build', { mod_dir: mod, game_dir: game, ref: ($('#edRef') ? $('#edRef').value : 'mod') }, {
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
    if ($('#gameDir')) $('#gameDir').value = localStorage.getItem('gameDir') || App.defaults.game_dir || '';
    if ($('#cfg_grad_path')) $('#cfg_grad_path').value = localStorage.getItem('gradPath') || App.defaults.grad_path;
    $('#modDir').onchange = $('#outDir').onchange = $('#gameDir').onchange = () => {
      localStorage.setItem('modDir', $('#modDir').value.trim());
      localStorage.setItem('outDir', $('#outDir').value.trim());
      if ($('#gameDir')) localStorage.setItem('gameDir', $('#gameDir').value.trim());
      refreshState();
      if ($('#modDir').value.trim() === '') setMsg('mod 留空：只加载原版进行编辑');
      if (App.editMode) { MapView.layerMaps = {}; App.newEnc = {}; App.newEncNext = 20000; editRefresh(); }
    };
  } else {
    MapView.imgs = {}; MapView.pid = null; MapView.hl = null; MapView.tgt = null; MapView.tgtIds = null; MapView.inited = false;
    MapView.layerMaps = {}; MapView.efill = null; MapView.efctx = null; MapView.efimg = null; MapView.tgtHL = null; MapView.esel = null;
    App.terrain = null; App.terrainCanvas = null; App.railData = null; App.railSel = null;
    App.newEnc = {}; App.newEncNext = 20000; App.ovState = {}; App.ovSr = {}; App.ovCont = {}; App.mergedOut = {};
  }
  loadInputs();
  if (typeof edLoadRailData === 'function') edLoadRailData();
  document.addEventListener('change', saveInputs);
  document.addEventListener('input', saveInputs);
  edSyncPanels();
  const st = await refreshState();
  if (!st.cache_exists) {
    $('#mapInfo').innerHTML = '尚未构建缓存。点击右上角 <b>构建/刷新缓存</b> 生成地图图层。';
    return;
  }
  App.meta = await fetch('/cache/meta.json?t=' + Date.now()).then(r => r.json());
  if (!MapView.canvas) { MapView.canvas = $('#map'); MapView.ctx = MapView.canvas.getContext('2d', { willReadFrequently: true }); }
  await initMap();
  await loadIndex();
  if (App.editMode) {
    await edLoadLayerMaps(); await editRefresh();
    edSyncPanels();
    if (App.editLayer === 'terrain') await edLoadTerrainData();
    if (App.editLayer === 'rail') await edLoadRailData();
  }
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

  if ($('#findGo')) $('#findGo').onclick = doFind;
  if ($('#findQuery')) $('#findQuery').addEventListener('keydown', e => { if (e.key === 'Enter') doFind(); });
  $('#btnClearSel').onclick = () => { App.findIds = null; buildESel([]); selectProvince(null); };
  if ($('#btnClearTgt')) $('#btnClearTgt').onclick = () => clearTargets();
  $$('#side [data-leg]').forEach(b => b.onclick = () => showLegend(b.dataset.leg));

  $('#btnAnalyze').onclick = doAnalyze;
  $('#btnRun').onclick = doRun;
  $('#btnAudit').onclick = doAudit;
  $('#btnRepair').onclick = doRepair;
  $('#btnCancel1').onclick = () => activeJob.analyze && activeJob.analyze.cancel();
  $('#btnCancel2').onclick = () => activeJob.run && activeJob.run.cancel();
  $('#btnOpenOut').onclick = () => fetch('/api/openout', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: $('#outDir').value.trim() }) });

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

  const em = $('#editMode');
  if (em) em.onchange = () => {
    App.editMode = em.checked;
    if (App.editMode) { edLoadLayerMaps().then(requestDraw); edFillTargets(); edFillBuildings(); edFillNextIds(); }
    else { App.editSel = []; renderEditSel(); buildESel(); }
    setMsg(App.editMode ? '编辑模式：选图层与目标后点击地块' : '已退出编辑模式');
    requestDraw();
  };
  const el = $('#edLayer');
  if (el) el.onchange = () => {
    App.editLayer = el.value;
    App.editTarget = null; MapView.tgtHL = null; edUpdateTargetUI();
    if (!App.editMode) { $('#editMode').checked = true; $('#editMode').onchange(); }
    edSyncPanels();
    edLoadLayerMaps().then(() => { edReplay((App.editOps && App.editOps.ops) || []); });
    setMsg('图层: ' + el.options[el.selectedIndex].text);
  };
  if ($('#edClearSel')) $('#edClearSel').onclick = () => { App.editSel = []; renderEditSel(); edSelInfo(); buildESel(); };
  if ($('#edMerge')) $('#edMerge').onclick = doEditMerge;
  if ($('#edDelete')) $('#edDelete').onclick = doEditDelete;
  if ($('#edUndo')) $('#edUndo').onclick = () => doEditSimple('undo');
  if ($('#edRedo')) $('#edRedo').onclick = () => doEditSimple('redo');
  if ($('#edReset')) $('#edReset').onclick = () => doEditSimple('reset');
  if ($('#edExport')) $('#edExport').onclick = doEditExport;
  if ($('#nsAddBuilding')) $('#nsAddBuilding').onclick = () => {
    const k = $('#nsBuilding').value, v = Number($('#nsBuildingLv').value || 0);
    if (k && v > 0) { App.nsBuildings = App.nsBuildings || {}; App.nsBuildings[k] = v; edRenderNsBuildings(); }
  };
  if ($('#edNewStateCreate')) $('#edNewStateCreate').onclick = edCreateNewState;
  if ($('#edNewSrCreate')) $('#edNewSrCreate').onclick = edCreateNewSr;
  if ($('#nsSave')) $('#nsSave').onclick = edSaveStateData;
  if ($('#srSave')) $('#srSave').onclick = edSaveSrData;
  if ($('#edRailOn')) $('#edRailOn').onclick = () => edExtra('rail', 'add');
  if ($('#edRailOff')) $('#edRailOff').onclick = () => edExtra('rail', 'remove');
  if ($('#railLevelSave')) $('#railLevelSave').onclick = edRailLevelSave;
  if ($('#railDel')) $('#railDel').onclick = edRailDel;
  if ($('#railCreate')) $('#railCreate').onclick = edRailCreate;
  if ($('#terSave')) $('#terSave').onclick = edTerrainSave;
  if ($('#terAuto')) $('#terAuto').onclick = edTerrainAuto;
  if ($('#terExport')) $('#terExport').onclick = edTerrainExport;
  if ($('#railClearSel')) $('#railClearSel').onclick = () => { App.editSel = []; renderEditSel(); edSelInfo(); buildESel(); setMsg('已取消选中'); };
  if ($('#edSupOn')) $('#edSupOn').onclick = () => edExtra('supply', 'add');
  if ($('#edSupOff')) $('#edSupOff').onclick = () => edExtra('supply', 'remove');
  if ($('#edBldAdd')) $('#edBldAdd').onclick = () => edExtra('building', 'add');
  if ($('#btnCheckIds')) $('#btnCheckIds').onclick = () => { edFillNextIds(); setMsg('已重新核对可用 ID'); };
  if ($('#depInstall')) $('#depInstall').onclick = depInstall;
  if ($('#depRecheck')) $('#depRecheck').onclick = () => { depCheck(); setMsg('已重新检测依赖'); };
  depCheck();
  if ($('#edRef')) $('#edRef').onchange = () => {
    MapView.layerMaps = {}; App.newEnc = {}; App.newEncNext = 20000;
    setMsg('参考切换到 ' + $('#edRef').value + '，正在重建缓存…');
    buildCache();
  };

  boot(false);
}
let anaSort = { k: 'area', dir: -1 };
let curAna = [];
// wrap renderAnalyze to remember rows
const _renderAnalyze = renderAnalyze;
renderAnalyze = function (rows) { if (rows && rows.length !== 0) curAna = rows; _renderAnalyze(rows); };

document.addEventListener('DOMContentLoaded', initUI);
