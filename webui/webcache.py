# -*- coding: utf-8 -*-
"""为 HTML 前端生成地图缓存。

产物 (默认写到本文件旁的 cache/):
  meta.json             {width,height,max_id,disp_width,disp_height,mod,version}
  prov_id.png           全分辨率 RGB, 每像素编码省份ID -> (id&255,(id>>8)&255,(id>>16)&255)
  base_map.png          游戏内地形底图 (来自 colormap DDS, 否则用 provinces.bmp 缩放)
  provinces.png         纯省份色块图 (display 分辨率)
  edges.png             省份边界描边 (display 分辨率, 白=边界)
  overlay_sr.png        战略区着色
  overlay_state.png     州着色
  overlay_continent.png 大洲着色
  prov_index.json       {id:{type,coastal,terrain,continent,color,area,sr,state}}
  regions.json          {sr:[...], state:[...], continents:[...]}

用法:
  python webcache.py --mod "C:\\...\\mod\\Project_IRIS" [--out cache]
"""
from __future__ import annotations

import argparse
import colorsys
import json
import os
import re
import struct
import sys

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None
PROV_RE = re.compile(r"provinces\s*=\s*\{([^}]*)\}", re.DOTALL)

CACHE_VERSION = 2


def read_text(p: str) -> str:
    return open(p, "rb").read().decode("utf-8", "surrogateescape")


def pal(n: int):
    """生成 n 个尽量区分开的颜色 (与旧 Godot 缓存保持一致)。"""
    out = []
    for i in range(n):
        h = (i * 0.61803398875) % 1.0
        s = 0.55 + 0.35 * ((i * 7) % 5) / 5.0
        v = 0.75 + 0.25 * ((i * 3) % 4) / 4.0
        r, g, b = colorsys.hsv_to_rgb(h, s, v)
        out.append((int(r * 255), int(g * 255), int(b * 255)))
    return out


def build(mod: str, out: str, progress=None, disp_max_w: int = 2816):
    def log(m):
        if progress:
            progress(str(m))

    mod = os.path.abspath(mod)
    os.makedirs(out, exist_ok=True)
    mdir = os.path.join(mod, "map")

    # ---- definition.csv ----
    log("读取 definition.csv ...")
    recs = {}
    for line in open(os.path.join(mdir, "definition.csv"), encoding="utf-8-sig", errors="replace"):
        p = line.strip().split(";")
        if len(p) >= 8 and p[0].isdigit():
            recs[int(p[0])] = p
    log("省份数: %d" % len(recs))

    # ---- provinces.bmp -> 省份ID图 ----
    log("读取 provinces.bmp 并解码省份ID ...")
    arr = np.asarray(Image.open(os.path.join(mdir, "provinces.bmp")).convert("RGB")).astype(np.int64)
    h, w = arr.shape[:2]
    lut = np.zeros(1 << 24, dtype=np.int32)
    for pid, r in recs.items():
        lut[(int(r[1]) << 16) | (int(r[2]) << 8) | int(r[3])] = pid
    pid = lut[(arr[:, :, 0] << 16) | (arr[:, :, 1] << 8) | arr[:, :, 2]]

    # ---- prov_id.png (全分辨率, 用于悬停取省份ID) ----
    log("写出 prov_id.png (%dx%d) ..." % (w, h))
    idimg = np.stack([(pid & 255), (pid >> 8) & 255, (pid >> 16) & 255], -1).astype(np.uint8)
    Image.fromarray(idimg, "RGB").save(os.path.join(out, "prov_id.png"))
    del idimg, arr

    # ---- display 分辨率 ----
    step = max(1, int(round(w / float(disp_max_w))))
    dw, dh = w // step, h // step
    log("生成显示层 %dx%d (下采样 x%d) ..." % (dw, dh, step))
    pid_d = pid[::step, ::step]

    # 地形底图 (colormap DDS)
    dds = os.path.join(mdir, "terrain", "colormap_rgb_cityemissivemask_a.dds")
    if os.path.isfile(dds):
        log("从 colormap DDS 解出地形底图 ...")
        raw = open(dds, "rb").read()
        ch = struct.unpack('<I', raw[12:16])[0]
        cw = struct.unpack('<I', raw[16:20])[0]
        px = np.frombuffer(raw[128:128 + cw * ch * 4], dtype=np.uint32).reshape(ch, cw)
        bimg = np.stack([((px >> 16) & 255).astype(np.uint8),
                         ((px >> 8) & 255).astype(np.uint8),
                         (px & 255).astype(np.uint8)], -1)
        del px
        Image.fromarray(bimg, "RGB").save(os.path.join(out, "base_map.png"))
        del bimg
    else:
        log("未找到 colormap, 用 provinces.bmp 缩放为底图 ...")
        src = Image.open(os.path.join(mdir, "provinces.bmp")).convert("RGB")
        src = src.resize((dw, dh), Image.BILINEAR)
        src.save(os.path.join(out, "base_map.png"))

    # 纯省份色块
    log("生成省份色块层 ...")
    cmax = max(recs) if recs else 0
    color_lut = np.zeros(cmax + 2, dtype=np.uint32)
    for pid_, r in recs.items():
        if pid_ <= cmax:
            color_lut[pid_] = (int(r[1]) << 16) | (int(r[2]) << 8) | int(r[3])
    pcol = color_lut[np.clip(pid_d, 0, cmax + 1)]
    provimg = np.stack([(pcol >> 16) & 255, (pcol >> 8) & 255, pcol & 255], -1).astype(np.uint8)
    Image.fromarray(provimg, "RGB").save(os.path.join(out, "provinces.png"))
    del provimg, pcol

    # 边界描边 (在 display 网格上计算, 保持细线)
    log("生成边界层 ...")
    ex = np.zeros_like(pid_d, dtype=bool)
    if pid_d.shape[1] > 1:
        ex[:, 1:] = pid_d[:, 1:] != pid_d[:, :-1]
        ex[:, :-1] |= pid_d[:, :-1] != pid_d[:, 1:]
    if pid_d.shape[0] > 1:
        ex[1:, :] |= pid_d[1:, :] != pid_d[:-1, :]
        ex[:-1, :] |= pid_d[:-1, :] != pid_d[1:, :]
    Image.fromarray((ex.astype(np.uint8) * 255), "L").save(os.path.join(out, "edges.png"))
    del ex

    # ---- 战略区 / 州 ----
    def scan(sub):
        d = os.path.join(mod, sub)
        owner = {}
        names = []
        if os.path.isdir(d):
            for f in sorted(os.listdir(d)):
                if not f.lower().endswith(".txt"):
                    continue
                m = PROV_RE.search(read_text(os.path.join(d, f)))
                if not m:
                    continue
                idx = len(names)
                names.append(os.path.splitext(f)[0])
                for tok in re.sub(r"#[^\n]*", "", m.group(1)).split():
                    if tok.isdigit():
                        owner.setdefault(int(tok), idx)
        return owner, names

    log("扫描 战略区 / 州 归属 ...")
    sr_owner, sr_names = scan(os.path.join("map", "strategicregions"))
    st_owner, st_names = scan(os.path.join("history", "states"))

    cont_names = []
    cp = os.path.join(mdir, "continent.txt")
    if os.path.isfile(cp):
        m = re.search(r"continents\s*=\s*\{(.*?)\}", read_text(cp), re.DOTALL)
        if m:
            for ln in m.group(1).splitlines():
                ln = ln.split("#", 1)[0].strip()
                if ln:
                    cont_names.append(ln)

    maxid = max(recs) if recs else 0

    def overlay(owner, n, name):
        table = np.full(maxid + 2, -1, dtype=np.int32)
        for k, v in owner.items():
            if k <= maxid:
                table[k] = v
        idx = np.where(pid_d <= maxid, table[np.clip(pid_d, 0, maxid)], -1)
        out_img = np.zeros((dh, dw, 3), np.uint8)
        palette = np.array(pal(max(n, 1)), np.uint8)
        sel = idx >= 0
        out_img[sel] = palette[np.clip(idx[sel], 0, len(palette) - 1)]
        Image.fromarray(out_img, "RGB").save(os.path.join(out, "overlay_%s.png" % name))
        return idx

    log("生成 战略区 / 州 / 大洲 着色层 ...")
    overlay(sr_owner, len(sr_names), "sr")
    overlay(st_owner, len(st_names), "state")

    # HOI4 大洲ID从 1 开始 (1=anglia)。0/负数表示海洋/湖泊 -> 不着色。
    cidx_of = {}
    for pid_, r in recs.items():
        try:
            v = int(r[7]) - 1
            cidx_of[pid_] = v if v >= 0 else -1
        except (ValueError, IndexError):
            cidx_of[pid_] = -1
    carr = np.full(maxid + 2, -1, dtype=np.int32)
    for k, v in cidx_of.items():
        if k <= maxid:
            carr[k] = v
    ci = np.where(pid_d <= maxid, carr[np.clip(pid_d, 0, maxid)], -1)
    cpal = np.array(pal(max(len(cont_names), 1)), dtype=np.uint8)
    cimg = np.zeros((dh, dw, 3), np.uint8)
    csel = ci >= 0
    cimg[csel] = cpal[np.clip(ci[csel], 0, len(cont_names) - 1)]
    Image.fromarray(cimg, "RGB").save(os.path.join(out, "overlay_continent.png"))

    # ---- prov_index.json ----
    log("统计面积并生成 prov_index.json ...")
    vals, counts = np.unique(pid, return_counts=True)
    area = dict(zip(vals.tolist(), counts.tolist()))
    index = {}
    for pid_, r in recs.items():
        index[str(pid_)] = {
            "type": r[4], "coastal": r[5], "terrain": r[6], "continent": r[7],
            "color": [int(r[1]), int(r[2]), int(r[3])],
            "area": int(area.get(pid_, 0)),
            "sr": sr_names[sr_owner[pid_]] if pid_ in sr_owner else "",
            "state": st_names[st_owner[pid_]] if pid_ in st_owner else "",
        }
    json.dump(index, open(os.path.join(out, "prov_index.json"), "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    json.dump({"sr": sr_names, "state": st_names, "continents": cont_names},
              open(os.path.join(out, "regions.json"), "w", encoding="utf-8"),
              ensure_ascii=False, separators=(",", ":"))
    json.dump({"width": w, "height": h, "disp_width": dw, "disp_height": dh,
               "max_id": maxid, "mod": mod, "version": CACHE_VERSION},
              open(os.path.join(out, "meta.json"), "w", encoding="utf-8"),
              ensure_ascii=False)
    log("缓存完成 -> %s" % out)
    log("  %dx%d, max_id=%d, sr=%d, state=%d, continents=%d" % (
        w, h, maxid, len(sr_names), len(st_names), len(cont_names)))
    return {"width": w, "height": h, "disp_width": dw, "disp_height": dh,
            "max_id": maxid, "sr": len(sr_names), "state": len(st_names),
            "continents": len(cont_names)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mod", required=True)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    here = os.path.dirname(os.path.abspath(__file__))
    out = a.out or os.path.join(here, "cache")
    build(a.mod, out, progress=lambda m: print(m, flush=True))


if __name__ == "__main__":
    sys.exit(main())
