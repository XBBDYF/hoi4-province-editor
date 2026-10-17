

### === function resizeCanvas( @ index 23754 ===
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


### === function screenToWorld( @ index 24486 ===
function screenToWorld(px, py) {
  const { s, x, y } = MapView.cam;
  return { x: (px - x) / s, y: (py - y) / s };
}

### === function provinceAtWorld( @ index 24980 ===
function provinceAtWorld(wx, wy) {
  if (!MapView.pid) return -1;
  const px = Math.floor(wx), py = Math.floor(wy);
  if (px < 0 || py < 0 || px >= MapView.pidW || py >= MapView.pidH) return -1;
  const i = (py * MapView.pidW + px) * 4;
  const d = MapView.pid;
  return d[i] | (d[i + 1] << 8) | (d[i + 2] << 16);
}


### === function setupMapEvents( @ index 25297 ===
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


### === function draw( @ index 18470 ===
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
  if (App.editMode && edTabActive()) {          // 只有停在编辑页才用编辑图层样式
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
  if (MapView.hl) ctx.drawImage(MapView.hl, x, y, dw, dh);   // 注意: efill 是编辑专用, 普通视图不画
  if (MapView.esel) ctx.drawImage(MapView.esel, x, y, dw, dh);
  const tgtL = MapView.layers.find(l => l.img === 'tgt');
  if (tgtL && tgtL.vis && MapView.tgt) {
    ctx.globalAlpha = tgtL.op;
    ctx.drawImage(MapView.tgt, x, y, dw, dh);
    ctx.globalAlpha = 1;
  }
  const linkL = MapView.layers.find(l => l.img === 'links');
  if (linkL && linkL.vis) {
    if (App.railData) _drawAdjEdges(ctx);                    // 真实几何(沿边界/真实跨越)
    else {
      if (typeof edLoadRailData === 'function') edLoadRailData();
      if (App.links && App.centers) drawLinks(ctx, linkL.op); // 回退: 旧的中心连线
    }
  }
  const railL = MapView.layers.find(l => l.img === 'rail');
  if (railL && railL.vis && App.railData) { drawRails(ctx); }
  const borderL = MapView.layers.find(l => l.img === 'edges');
  if (borderL && borderL.vis && MapView.pid) drawBorders(ctx, borderL.op);
}

/* 等宽边界：直接在屏幕空间按像素计算，线宽与缩放无关（始终约1个屏幕像素）。 */

### === function drawBorders( @ index 21932 ===
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


### === function fitView( @ index 24179 ===
function fitView() {
  const area = $('#maparea');
  const cw = area.clientWidth, ch = area.clientHeight;
  const s = Math.min(cw / MapView.W, ch / MapView.H) * 0.98;
  MapView.cam.s = s;
  MapView.cam.x = (cw - MapView.W * s) / 2;
  MapView.cam.y = (ch - MapView.H * s) / 2;
  updZoom(); requestDraw();
}


### === async function initMap( @ index 15303 ===
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


### === async function boot( @ index 111643 ===
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
  if ($('#edLayer') && $('#edLayer').value) App.editLayer = $('#edLayer').value;   // 下拉为准(无 IO)
  if (edTabActive()) {                       // 只有停在"编辑"页时才做检查/加载
    if ($('#editMode')) App.editMode = !!$('#editMode').checked;
    if (typeof edSyncPanels === 'function') edSyncPanels();
    if (typeof edLoadRailData === 'function') edLoadRailData();
  }
  document.addEventListener('change', saveInputs);
  document.addEventListener('input', saveInputs);
  edSyncPanels();
  const st = await refreshState();
  if (!st.cache_exists) {
    $('#mapInfo').innerHTML = tp('尚未构建缓存。点击右上角 <b>构建/刷新缓存</b> 生成地图图层。');
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
  restoreLastTab();
  edApplyMode();
  applyI18n();
  setMsg(t('msg.ready', '就绪'));
}


### === function clampCam( @ index 27251 ===
function clampCam() { /* 允许自由平移, 不强制限制 */ }
