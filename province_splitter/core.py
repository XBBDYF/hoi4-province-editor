# -*- coding: utf-8 -*-
"""HOI4 地块(province)细化核心引擎。

流程:
  1. 读取 map/provinces.bmp 与 map/definition.csv, 统计每个地块的像素面积;
  2. 按规则(阈值 / 类型 / 大洲 / 指定ID)筛选出需要细化的"过大地块";
  3. 用 KMeans 生成种子 + 多源BFS(测地线增长)把地块切成 k 个"连通"小块,
     其中面积最大的一块保留原ID, 其余分配新ID(从当前最大ID+1开始递增);
  4. 把新颜色写回 provinces.bmp, 新行追加到 definition.csv,
     并把新地块追加进原地块所属的 strategic region 与 state。

所有对文本文件的写回都采用 "bytes -> utf-8/surrogateescape -> 修改 -> 原样编码" 的方式,
保证不会破坏原文件里混合编码的注释与换行。
"""

from __future__ import annotations

import math
import os
import re
import collections
import colorsys
import time
import shutil
from collections import deque, Counter
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None

PROV_RE = re.compile(r"(provinces\s*=\s*\{)([^}]*)(\})", re.DOTALL)


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
@dataclass
class Config:
    mod_dir: str = ""
    game_dir: str = ""   # 原版(游戏)根目录: mod 缺失文件时回退到这里
    game_merge: bool = False  # True: states/战略区 与原版合并(完全模拟游戏); False: 仅缺失时回退
    clean_output: bool = True  # 细化前清空输出目录, 避免上次运行的残留文件冲突
    out_dir: str = ""
    in_place: bool = False
    copy_unchanged: bool = False

    # 检测规则
    threshold: int = 200            # 像素面积上限, 超过则视为"过大"
    target_pixels: int = 0         # 目标每块面积(0 => 使用 threshold)
    min_part_pixels: int = 80       # 新地块最小面积, 太小会并回邻居
    max_parts: int = 64            # 单个地块最多切成几块
    scope: str = "all"             # all | land | sea | lake
    continents: list = field(default_factory=lambda: [1, 2, 3])  # 大洲ID(数字)列表, 空=全部
    include_ids: list = field(default_factory=list)  # 强制细化(忽略阈值)
    exclude_ids: list = field(default_factory=lambda: [85, 69, 86, 114])  # 永不细化(地块/province ID)
    exclude_states: list = field(default_factory=list)  # 永不细化(州/state ID, 其下属地块全部跳过)
    only_ids: bool = False          # True: 只处理 include_ids 列出的地块
    parts_override: dict = field(default_factory=dict)  # {province_id: k}

    seed: int = 20240101
    dry_run: bool = False
    make_preview: bool = True
    preview_scale: float = 1.0

    # 渐变细化(导入第二张灰度图: 越黑拆得越小, 越白拆得越大)
    grad_path: str = ""
    grad_black_px: int = 80       # 纯黑处的目标地块像素
    grad_white_px: int = 250      # 纯白处的目标地块像素
    grad_invert: bool = False
    grad_skip_white: bool = True  # 纯白/近白区域不参与自动细化
    grad_white_cutoff: float = 0.96  # 近白像素不参与自动细化
    split_hysteresis_ratio: float = 1.25  # 实际面积未明显超过目标时不细化，减少临界碎块
    split_search_rounds: int = 6  # 多次尝试不同切割种子，选择形状更规整的结果
    neighbor_transition_enabled: bool = True  # 未细化陆地邻区目标面积渐变，可关闭
    neighbor_transition_hops: int = 3  # 沿陆地邻接图影响的最大距离(跳)

    # 完整性修复
    repair: bool = True            # 拆分后修复 战略区/州归属、铁路、海峡运河
    railway_max_hops: int = 25     # 铁路断点重连时允许的最大插入跳数
    strip_invalid: bool = True     # 清除 SR/state 中不存在的省份ID
    island_max_px: int = 5         # 只把"不大于此像素的碎块"当作孤立像素修复(群岛多块保留)
    canal_complete: bool = True    # 用 rivers.bmp 校验并为缺失的运河补 adjacencies 行

    def target(self) -> int:
        return self.target_pixels if self.target_pixels > 0 else self.threshold


# --------------------------------------------------------------------------- #
# I/O 工具
# --------------------------------------------------------------------------- #
def _read_text(path: str) -> str:
    return open(path, "rb").read().decode("utf-8", "surrogateescape")


def _write_text(path: str, text: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(text.encode("utf-8", "surrogateescape"))


def _clear_output_dir(out_dir: str, mod_dir: str, log=None) -> int:
    """清空输出目录, 避免上次运行的残留文件造成冲突。带安全护栏, 危险时跳过。"""
    if not out_dir:
        return 0
    o = os.path.abspath(out_dir)
    m = os.path.abspath(mod_dir) if mod_dir else ""
    drive, tail = os.path.splitdrive(o)
    unsafe = (not os.path.isdir(o)) or tail in ("", os.sep) \
        or (m and (o == m or m.startswith(o + os.sep) or o.startswith(m + os.sep)))
    if unsafe:
        if log:
            log("跳过清空输出目录(路径不安全或与 mod 重叠): %s" % out_dir)
        return 0
    removed = 0
    for name in os.listdir(o):
        p = os.path.join(o, name)
        try:
            if os.path.isdir(p) and not os.path.islink(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                os.remove(p)
            removed += 1
        except OSError:
            pass
    if log:
        log("已清空输出目录: %s (删除 %d 项)" % (out_dir, removed))
    return removed


def rgb_key(r, g, b) -> int:
    return (int(r) << 16) | (int(g) << 8) | int(b)


def key_rgb(key: int):
    return (key >> 16) & 255, (key >> 8) & 255, key & 255


# --------------------------------------------------------------------------- #
# definition.csv
# --------------------------------------------------------------------------- #
@dataclass
class Definition:
    raw: bytes
    lines: list            # 原始文本行 (不含换行符)
    recs: dict             # id -> [id, r, g, b, type, coastal, terrain, continent]  (字符串)
    color2id: dict         # rgb_key -> id
    id2color: dict         # id -> rgb_key

    @property
    def max_id(self) -> int:
        return max(self.recs) if self.recs else 0


def read_definition(path: str) -> Definition:
    with open(path, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-8", "surrogateescape")
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = [ln for ln in text.split(nl)]
    # 去掉末尾空行
    while lines and lines[-1] == "":
        lines.pop()
    recs, c2i, i2c = {}, {}, {}
    for ln in lines:
        p = ln.rstrip("\r").split(";")
        if len(p) < 8:
            continue
        try:
            pid = int(p[0])
            r, g, b = int(p[1]), int(p[2]), int(p[3])
        except ValueError:
            continue
        recs[pid] = p
        k = rgb_key(r, g, b)
        c2i[k] = pid
        i2c[pid] = k
    return Definition(raw, lines, recs, c2i, i2c)


# --------------------------------------------------------------------------- #
# provinces.bmp
# --------------------------------------------------------------------------- #
def load_provinces_bmp(path: str):
    im = Image.open(path)
    if im.mode != "RGB":
        im = im.convert("RGB")
    arr = np.asarray(im).astype(np.uint8)
    return arr


def save_provinces_bmp(arr: np.ndarray, path: str):
    """Windows 安全地写出 provinces.bmp。

    先写同目录临时文件，再用 os.replace 原子替换目标文件。
    如果历史运行意外留下了同名目录(例如 output/map/provinces.bmp/)，
    不删除它，而是先改名为 provinces.bmp.dir_backup*，避免数据丢失。
    """
    import tempfile

    path = os.path.abspath(os.fspath(path))
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, exist_ok=True)

    # 一个名为 provinces.bmp 的目录会让 Windows 的 open(path, "w+b")
    # 抛出 OSError: [Errno 22] Invalid argument。保留现场并让目标变回文件。
    if os.path.isdir(path):
        backup = path + ".dir_backup"
        n = 1
        while os.path.exists(backup):
            backup = path + ".dir_backup_%d" % n
            n += 1
        try:
            os.replace(path, backup)
        except OSError as e:
            raise OSError(
                "BMP 输出目标是一个目录，且无法自动备份：%s；请关闭占用该目录的程序后"
                "手动删除/改名这个目录，再重试。" % path
            ) from e

    fd, tmp = tempfile.mkstemp(prefix=".provinces_", suffix=".tmp", dir=parent)
    os.close(fd)
    try:
        Image.fromarray(arr.astype(np.uint8), "RGB").save(tmp, format="BMP")
        # Windows 下 os.replace 可以可靠地替换同目录中的已有普通文件，
        # 避免 PIL 直接以 w+b 打开目标时遇到文件锁/残留文件问题。
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def pack_rgb(arr: np.ndarray) -> np.ndarray:
    a = arr.astype(np.int32)
    return (a[:, :, 0] << 16) | (a[:, :, 1] << 8) | a[:, :, 2]


def load_gradient(path: str, h: int, w: int) -> np.ndarray:
    """读取灰度渐变图并缩放到 (h,w); 返回 float 0..1 (黑=0, 白=1)。"""
    im = Image.open(path).convert("L")
    if im.size != (w, h):
        im = im.resize((w, h), Image.BILINEAR)
    return np.asarray(im).astype(np.float32) / 255.0


def gradient_targets(pid_map: np.ndarray, grad: np.ndarray, cfg: Config,
                     return_averages: bool = False):
    """每个地块的目标像素大小: 越黑越小, 越白越大。

    return_averages=True 时同时返回反转设置生效后的每块平均灰度(0..1)，
    供白色区域跳过规则使用。保留默认只返回 dict 的旧接口。
    """
    flat_p = pid_map.ravel()
    flat_g = grad.ravel()
    n = int(flat_p.max()) + 1
    s = np.bincount(flat_p, weights=flat_g, minlength=n)
    c = np.bincount(flat_p, minlength=n)
    avg = s / np.maximum(c, 1)
    if cfg.grad_invert:
        avg = 1.0 - avg
    out = {}
    averages = {}
    for pid in np.unique(flat_p).tolist():
        g = float(avg[pid])
        out[int(pid)] = int(round(cfg.grad_black_px +
                                  (cfg.grad_white_px - cfg.grad_black_px) * g))
        averages[int(pid)] = g
    return (out, averages) if return_averages else out


class ColorIndex:
    """把整张图的像素按颜色分组, 便于快速取出某个颜色的所有像素坐标。"""

    def __init__(self, packed: np.ndarray):
        self.h, self.w = packed.shape
        flat = packed.ravel()
        self.uniq, self.counts = np.unique(flat, return_counts=True)
        order = np.argsort(flat, kind="stable")
        self.order = order.astype(np.int64)
        self.bounds = np.zeros(len(self.uniq) + 1, dtype=np.int64)
        np.cumsum(self.counts, out=self.bounds[1:])
        self.area = {int(v): int(c) for v, c in zip(self.uniq, self.counts)}

    def indices(self, key: int) -> np.ndarray:
        i = int(np.searchsorted(self.uniq, key))
        if i >= len(self.uniq) or self.uniq[i] != key:
            return np.array([], dtype=np.int64)
        return self.order[self.bounds[i]:self.bounds[i + 1]]


# --------------------------------------------------------------------------- #
# continent.txt
# --------------------------------------------------------------------------- #
def read_continent_names(mod_dir: str, game_dir: str = "") -> list:
    from . import paths as _paths
    p = _paths.resolve(mod_dir, game_dir, "map", "continent.txt")
    if not os.path.isfile(p):
        return []
    text = _read_text(p)
    m = re.search(r"continents\s*=\s*\{(.*?)\}", text, re.DOTALL)
    if not m:
        return []
    names = []
    for ln in m.group(1).splitlines():
        ln = ln.split("#", 1)[0].strip()
        if ln:
            names.append(ln)
    return names


# --------------------------------------------------------------------------- #
# states / strategic regions  (province -> 所属文件)
# --------------------------------------------------------------------------- #
def build_province_owner_index(files: list) -> tuple:
    """返回 (province_id -> 文件路径, 文件路径 -> 文本)。"""
    owner = {}
    texts = {}
    for fp in files:
        text = _read_text(fp)
        texts[fp] = text
        m = PROV_RE.search(text)
        if not m:
            continue
        block = re.sub(r"#[^\n]*", "", m.group(2))
        for tok in block.split():
            if tok.isdigit():
                owner.setdefault(int(tok), fp)
    return owner, texts


def _owner_from(texts: dict) -> dict:
    owner = {}
    for fp, text in texts.items():
        m = PROV_RE.search(text)
        if not m:
            continue
        block = re.sub(r"#[^\n]*", "", m.group(2))
        for tok in block.split():
            if tok.isdigit():
                owner.setdefault(int(tok), fp)
    return owner


def inject_ids(text: str, new_ids: list) -> tuple:
    """向 text 里第一个 provinces={...} 块追加 new_ids, 尽量保持原排版与注释。"""
    if not new_ids:
        return text, False
    m = PROV_RE.search(text)
    if not m:
        return text, False
    inner = m.group(2)
    comments = re.findall(r"#[^\n]*", inner)
    clean = re.sub(r"#[^\n]*", "", inner)
    toks = clean.split()
    ids = [str(i) for i in new_ids]
    if not toks:
        extra = ("  " + " ".join(c.strip() for c in comments)) if comments else ""
        newinner = " " + " ".join(ids) + extra + " "
    elif "\n" in inner:
        nl = "\r\n" if "\r\n" in inner else "\n"
        i0 = clean.find(toks[0]) if toks else -1
        lead = clean[:i0] if i0 >= 0 else ""
        ind = lead.split(nl)[-1] if nl in lead else "\t\t"
        if not ind:
            ind = "\t\t"
        body = "".join(ind + t + nl for t in toks)
        body += "".join(ind + t + nl for t in ids)
        for c in comments:
            body += ind + c.strip() + nl
        newinner = nl + body + ind
    else:
        i0 = clean.find(toks[0])
        lead = clean[:i0]
        tail = inner[len(inner.rstrip()):]
        extra = ("  " + " ".join(c.strip() for c in comments)) if comments else ""
        newinner = lead + " ".join(toks + ids) + extra + tail
    return text[:m.start(2)] + newinner + text[m.end(2):], True


# --------------------------------------------------------------------------- #
# 颜色生成
# --------------------------------------------------------------------------- #
def make_colors(n: int, used: set, rng: np.random.Generator) -> list:
    out = []
    attempts = 0
    while len(out) < n:
        h = rng.random()
        s = 0.55 + 0.45 * rng.random()
        v = 0.70 + 0.30 * rng.random()
        r, g, b = (int(round(255 * x)) for x in colorsys.hsv_to_rgb(h, s, v))
        attempts += 1
        key = rgb_key(r, g, b)
        if key == 0 or key in used:
            if attempts < 200 * n:
                continue
            # 兜底: 线性扫描
            for rr in range(1, 256):
                if len(out) >= n:
                    break
                for gg in range(1, 256, 7):
                    kk = rgb_key(rr, gg, b)
                    if kk not in used:
                        used.add(kk)
                        out.append(key_rgb(kk) + (kk,))
                        break
            break
        used.add(key)
        out.append((r, g, b, key))
    return out


# --------------------------------------------------------------------------- #
# 切分核心
# --------------------------------------------------------------------------- #
def _kmeans(coords: np.ndarray, k: int, rng: np.random.Generator, iters: int = 40):
    n = coords.shape[0]
    centers = np.empty((k, 2), dtype=np.float64)
    centers[0] = coords[rng.integers(n)]
    d2 = ((coords - centers[0]) ** 2).sum(1)
    for c in range(1, k):
        tot = d2.sum()
        if tot <= 0:
            centers[c] = coords[rng.integers(n)]
        else:
            p = d2 / tot
            centers[c] = coords[rng.choice(n, p=p)]
        d2 = np.minimum(d2, ((coords - centers[c]) ** 2).sum(1))
    labels = np.full(n, -1, dtype=np.int32)
    for _ in range(iters):
        dist = ((coords[:, None, :] - centers[None, :, :]) ** 2).sum(-1)
        new = dist.argmin(1).astype(np.int32)
        if np.array_equal(new, labels):
            break
        labels = new
        for c in range(k):
            mask = labels == c
            if mask.any():
                centers[c] = coords[mask].mean(0)
    return labels, centers


def _multi_source_bfs(mask: np.ndarray, seeds: list, slab: list) -> np.ndarray:
    """在 mask 内做多源 BFS, 每个种子生长出一块连通区域。返回 label 网格(-1 未分配)。"""
    h, w = mask.shape
    grid = np.full((h, w), -1, dtype=np.int16)
    dq = deque()
    for (sy, sx), sl in zip(seeds, slab):
        if 0 <= sy < h and 0 <= sx < w and mask[sy, sx] and grid[sy, sx] == -1:
            grid[sy, sx] = sl
            dq.append((sy, sx))
    while dq:
        y, x = dq.popleft()
        l = grid[y, x]
        if y > 0 and mask[y - 1, x] and grid[y - 1, x] == -1:
            grid[y - 1, x] = l; dq.append((y - 1, x))
        if y + 1 < h and mask[y + 1, x] and grid[y + 1, x] == -1:
            grid[y + 1, x] = l; dq.append((y + 1, x))
        if x > 0 and mask[y, x - 1] and grid[y, x - 1] == -1:
            grid[y, x - 1] = l; dq.append((y, x - 1))
        if x + 1 < w and mask[y, x + 1] and grid[y, x + 1] == -1:
            grid[y, x + 1] = l; dq.append((y, x + 1))
    return grid


def _connected_components_xy(ys: np.ndarray, xs: np.ndarray):
    """把像素坐标按四邻接拆成连通分量，返回若干 (ys, xs)。"""
    n = int(ys.size)
    if n == 0:
        return []
    y0, y1 = int(ys.min()), int(ys.max())
    x0, x1 = int(xs.min()), int(xs.max())
    mask = np.zeros((y1 - y0 + 1, x1 - x0 + 1), dtype=bool)
    mask[ys - y0, xs - x0] = True
    seen = np.zeros_like(mask, dtype=bool)
    comps = []
    for sy, sx in zip(*(np.nonzero(mask))):
        if seen[sy, sx]:
            continue
        q = deque([(int(sy), int(sx))])
        seen[sy, sx] = True
        cy, cx = [], []
        while q:
            y, x = q.popleft()
            cy.append(y + y0)
            cx.append(x + x0)
            if y > 0 and mask[y - 1, x] and not seen[y - 1, x]:
                seen[y - 1, x] = True; q.append((y - 1, x))
            if y + 1 < mask.shape[0] and mask[y + 1, x] and not seen[y + 1, x]:
                seen[y + 1, x] = True; q.append((y + 1, x))
            if x > 0 and mask[y, x - 1] and not seen[y, x - 1]:
                seen[y, x - 1] = True; q.append((y, x - 1))
            if x + 1 < mask.shape[1] and mask[y, x + 1] and not seen[y, x + 1]:
                seen[y, x + 1] = True; q.append((y, x + 1))
        comps.append((np.asarray(cy, dtype=np.int64), np.asarray(cx, dtype=np.int64)))
    comps.sort(key=lambda a: int(a[0].size), reverse=True)
    return comps


def _partition_score(grid: np.ndarray, mask: np.ndarray, order: list) -> float:
    """综合面积平衡和边界紧凑度评分，分数越低越好。"""
    if len(order) < 2:
        return float("inf")
    vals = grid[mask]
    labels, counts = np.unique(vals[vals >= 0], return_counts=True)
    if len(labels) < 2:
        return float("inf")
    areas = counts.astype(np.float64)
    mean_area = float(areas.mean())
    cv = float(areas.std() / max(1.0, mean_area))
    max_min = float(areas.max() / max(1.0, areas.min()))
    # 每个像素看四条边；和不同标签或背景相接的边都计入周长。
    h, w = grid.shape
    perim = np.zeros(int(max(order)) + 1, dtype=np.float64)
    for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        neigh_mask = np.zeros_like(mask)
        neigh_grid = np.full_like(grid, -1)
        ysrc0, ysrc1 = max(0, -dy), min(h, h - dy)
        xsrc0, xsrc1 = max(0, -dx), min(w, w - dx)
        ydst0, ydst1 = max(0, dy), min(h, h + dy)
        xdst0, xdst1 = max(0, dx), min(w, w + dx)
        neigh_mask[ydst0:ydst1, xdst0:xdst1] = mask[ysrc0:ysrc1, xsrc0:xsrc1]
        neigh_grid[ydst0:ydst1, xdst0:xdst1] = grid[ysrc0:ysrc1, xsrc0:xsrc1]
        edge = mask & ((~neigh_mask) | (grid != neigh_grid))
        vv = grid[edge]
        vv = vv[vv >= 0]
        if vv.size:
            perim += np.bincount(vv.astype(np.int64), minlength=len(perim))[:len(perim)]
    shape_terms = []
    for lab, area in zip(labels.tolist(), areas.tolist()):
        if area > 0 and lab < len(perim):
            # 方形附近约为 1；过细长/锯齿的地块得分更高。
            shape_terms.append((perim[int(lab)] ** 2) / (16.0 * area))
    shape_cost = float(np.mean(shape_terms)) if shape_terms else 10.0
    return shape_cost + 0.65 * cv + 0.08 * max(0.0, max_min - 1.0)


def split_region(ys: np.ndarray, xs: np.ndarray, k: int, rng, min_part: int,
                 optimize_rounds: int = 6):
    """切成 k 个连通区域；多次生成候选，选择面积更均衡、边界更规整的方案。

    返回 (grid, y0, x0, labels_sorted)。函数要求输入是一块四连通的像素区域；
    对于密度图造成的断裂区域，调用方应先按连通分量分别切割，白色像素不应进入本函数。
    """
    n = int(ys.shape[0])
    if n <= 1 or k <= 1 or n < 2 * max(1, min_part):
        return None
    k = int(min(k, n))
    coords = np.stack([ys, xs], 1).astype(np.float64)
    y0, y1 = int(ys.min()), int(ys.max())
    x0, x1 = int(xs.min()), int(xs.max())
    h, w = y1 - y0 + 1, x1 - x0 + 1
    mask = np.zeros((h, w), dtype=bool)
    mask[ys - y0, xs - x0] = True
    # 若输入不是单一连通分量，拒绝用一个种子方案把孤立片段并成怪异形状。
    comp = _connected_components_xy(ys, xs)
    if len(comp) != 1:
        return None

    def one_attempt():
        _lab, centers = _kmeans(coords, k, rng, iters=35)
        used, seeds, slab = set(), [], []
        for c in range(k):
            d = ((coords - centers[c]) ** 2).sum(1)
            # 先尝试最近点，重复时向后找；np.argsort 保证确定性。
            for idx in np.argsort(d):
                idx = int(idx)
                if idx not in used:
                    used.add(idx)
                    seeds.append((int(ys[idx]) - y0, int(xs[idx]) - x0))
                    slab.append(c)
                    break
        if len(seeds) < 2:
            return None
        grid = _multi_source_bfs(mask, seeds, slab)
        cnt = Counter(int(v) for v in grid[mask].ravel() if v >= 0)
        if len(cnt) < 2:
            return None
        core_label = cnt.most_common(1)[0][0]
        grid[(grid == -1) & mask] = core_label
        cnt = Counter(int(v) for v in grid[mask].ravel())

        # 对低于最小面积的块，仅并入共享边界最长的邻块，避免留下小碎片。
        for lab_id in [lab for lab, area in cnt.most_common()[1:] if area < min_part]:
            if lab_id not in cnt:
                continue
            boundary = Counter()
            yy, xx = np.nonzero(grid == lab_id)
            for py, px in zip(yy.tolist(), xx.tolist()):
                for ny, nx in ((py - 1, px), (py + 1, px), (py, px - 1), (py, px + 1)):
                    if 0 <= ny < h and 0 <= nx < w and grid[ny, nx] >= 0 and grid[ny, nx] != lab_id:
                        boundary[int(grid[ny, nx])] += 1
            if not boundary:
                continue
            target_lab = boundary.most_common(1)[0][0]
            grid[grid == lab_id] = target_lab
            cnt[target_lab] = int((grid == target_lab).sum())
            cnt.pop(lab_id, None)
        order = [lab for lab, _ in cnt.most_common()]
        if len(order) < 2 or any(int((grid == lab).sum()) < min_part for lab in order):
            return None
        score = _partition_score(grid, mask, order)
        return score, grid, order

    best = None
    rounds = max(1, int(optimize_rounds))
    # 超大地块缩减搜索次数，以免全图耗时失控；一般地块会进行 6 次候选比较。
    if n > 100000:
        rounds = min(rounds, 2)
    elif n > 25000:
        rounds = min(rounds, 3)
    for _ in range(rounds):
        cand = one_attempt()
        if cand is not None and (best is None or cand[0] < best[0]):
            best = cand
    if best is None:
        return None
    _, grid, order = best
    return grid, y0, x0, order


def split_active_components(ys: np.ndarray, xs: np.ndarray, k: int, rng, min_part: int,
                            target_pixels: int, split_margin: float = 1.25,
                            optimize_rounds: int = 6,
                            province_ys: Optional[np.ndarray] = None,
                            province_xs: Optional[np.ndarray] = None):
    """仅对非白色区域中足够大、连通且确实需要细化的分量分别切割。

    小型/离散的密度斑点保留原 ID，避免把密度图纹理切成毛刺和孤岛。
    合并各分量时为每个新标签分配唯一编号，未切割部分保持 -1（即留在原地块）。
    """
    if ys.size == 0:
        return None
    comps = _connected_components_xy(ys, xs)
    split_results = []
    for cy, cx in comps:
        area = int(cy.size)
        if area < 2 * max(1, min_part):
            continue
        if target_pixels > 0 and area <= target_pixels * max(1.0, float(split_margin)):
            continue
        if target_pixels > 0:
            kc = max(2, int(round(area / float(target_pixels))))
        else:
            kc = max(2, int(round(k * area / max(1, ys.size))))
        kc = min(kc, int(max(2, area // max(1, min_part))))
        if kc < 2:
            continue
        res = split_region(cy, cx, kc, rng, min_part, optimize_rounds=optimize_rounds)
        if res is not None and len(res[3]) >= 2:
            split_results.append(res)
    if not split_results:
        return None

    y0 = int(min(r[1] for r in split_results))
    x0 = int(min(r[2] for r in split_results))
    y1 = int(max(r[1] + r[0].shape[0] - 1 for r in split_results))
    x1 = int(max(r[2] + r[0].shape[1] - 1 for r in split_results))
    grid = np.full((y1 - y0 + 1, x1 - x0 + 1), -1, dtype=np.int32)
    next_label = 0
    for subgrid, sy0, sx0, order in split_results:
        for old_lab in order:
            new_lab = next_label
            next_label += 1
            yy, xx = np.nonzero(subgrid == old_lab)
            grid[yy + sy0 - y0, xx + sx0 - x0] = new_lab
    mask = grid >= 0
    if not mask.any():
        return None
    cnt = Counter(int(v) for v in grid[mask].ravel())
    # 尽量把最贴近未切分区域(尤其是白色像素)的子块留给原 ID，
    # 降低切分后原 ID 与保留白色区域断开的概率；没有锚点时按面积排序。
    anchor_contact = Counter()
    if province_ys is not None and province_xs is not None:
        ph, pw = grid.shape
        for py, px in zip(np.asarray(province_ys).tolist(), np.asarray(province_xs).tolist()):
            ly, lx = int(py) - y0, int(px) - x0
            if 0 <= ly < ph and 0 <= lx < pw and grid[ly, lx] >= 0:
                continue  # 该像素属于正在切分的活跃分量
            for ny, nx in ((ly - 1, lx), (ly + 1, lx), (ly, lx - 1), (ly, lx + 1)):
                if 0 <= ny < ph and 0 <= nx < pw and grid[ny, nx] >= 0:
                    anchor_contact[int(grid[ny, nx])] += 1
    order = sorted(cnt.keys(), key=lambda lab: (-anchor_contact.get(lab, 0), -cnt[lab]))
    return grid, y0, x0, order


def parts_for(area: int, cfg: Config, target: int = 0) -> int:
    t = target if target and target > 0 else cfg.target()
    k = int(math.ceil(area / t))
    k = max(2, k)
    k = min(k, cfg.max_parts)
    if cfg.min_part_pixels > 0:
        max_by_min = max(1, area // cfg.min_part_pixels)
        k = min(k, max(1, max_by_min))
    return max(2, k)


def _safe_remove_boundary_pixel(pid_map: np.ndarray, y: int, x: int, donor: int) -> bool:
    """保守地判断移走边界像素是否会把供体地块切断。

    只接受：该像素是端点，或其所有正交供体邻居能在不经过该像素的 3x3
    邻域内互相连通的情况。无法确认安全时就不移动该像素。
    """
    h, w = pid_map.shape
    y0, y1 = max(0, y - 1), min(h, y + 2)
    x0, x1 = max(0, x - 1), min(w, x + 2)
    local = (pid_map[y0:y1, x0:x1] == donor)
    cy, cx = y - y0, x - x0
    local[cy, cx] = False
    orth = []
    for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
        if 0 <= ny < local.shape[0] and 0 <= nx < local.shape[1] and local[ny, nx]:
            orth.append((ny, nx))
    if not orth:
        return False
    if len(orth) == 1:
        return True
    seen = {orth[0]}
    stack = [orth[0]]
    while stack:
        yy, xx = stack.pop()
        for ny, nx in ((yy - 1, xx), (yy + 1, xx), (yy, xx - 1), (yy, xx + 1)):
            if (0 <= ny < local.shape[0] and 0 <= nx < local.shape[1]
                    and local[ny, nx] and (ny, nx) not in seen):
                seen.add((ny, nx))
                stack.append((ny, nx))
    return all(p in seen for p in orth)


def balance_land_shapes(pid_map: np.ndarray, land_ids: set, gradient: Optional[np.ndarray] = None,
                        max_ratio: float = 3.0, gradient_gap: float = 0.22,
                        max_donor_fraction: float = 0.25, max_rounds: int = 4,
                        max_total_moves: int = 200000,
                        protected_mask: Optional[np.ndarray] = None) -> tuple:
    """仅在陆地省份之间轻微移动边界像素，缓和相邻省份面积悬殊。

    该处理不创建/删除省份，只修整相邻陆地之间的形状；不跨海，不触碰非陆地。
    有密度图时，比较相邻边界像素的灰度，而非整块地的平均灰度；这样同一密度
    区域内仍可均衡，即使两块地的整体灰度差异很大，也不会把密度边界当作整块禁区。
    protected_mask 用于保护海岸线等不能轻易移动的像素。
    为防止重塑过头，每个供体最多让出其初始面积的 20%，且每个像素都要通过
    连通性安全检查。返回 (新 ID 图, 移动像素数, 调整过的省份对数)。
    """
    if not land_ids or max_ratio <= 1.0 or max_total_moves <= 0:
        return pid_map, 0, 0
    p = pid_map.copy()
    h, w = p.shape
    max_id = max(int(p.max()), max(land_ids) if land_ids else 0)
    land_lut = np.zeros(max_id + 1, dtype=bool)
    ids = [int(v) for v in land_ids if 0 <= int(v) <= max_id]
    if not ids:
        return p, 0, 0
    land_lut[ids] = True
    initial_areas = np.bincount(p.ravel(), minlength=max_id + 1).astype(np.int64)
    donor_budget = np.floor(initial_areas * float(max_donor_fraction)).astype(np.int64)
    donor_budget[initial_areas > 0] = np.maximum(donor_budget[initial_areas > 0], 1)
    moved_by_donor = np.zeros(max_id + 1, dtype=np.int64)
    adjusted_pairs = set()
    total_moved = 0

    grad = None
    if gradient is not None and getattr(gradient, "shape", None) == p.shape:
        grad = np.asarray(gradient, dtype=np.float32)
    protected = None
    if protected_mask is not None and getattr(protected_mask, "shape", None) == p.shape:
        protected = np.asarray(protected_mask, dtype=bool)

    for _round in range(max_rounds):
        if total_moved >= max_total_moves:
            break
        flat = p.ravel()
        areas = np.bincount(flat, minlength=max_id + 1).astype(np.int64)
        pairs = collections.defaultdict(list)

        def collect(donor_view, receiver_view, row0, col0, row_step, col_step,
                    donor_grad=None, receiver_grad=None, donor_protected=None):
            valid = (donor_view != receiver_view)
            valid &= (donor_view <= max_id) & (receiver_view <= max_id)
            valid &= land_lut[donor_view] & land_lut[receiver_view]
            valid &= areas[donor_view] > (areas[receiver_view] * max_ratio)
            # 用边界两侧实际像素的密度做判断，而不是整块地的平均值。
            # 这样不会因为一块地跨过黑白渐变区，就完全失去形状均衡机会。
            if grad is not None and donor_grad is not None and receiver_grad is not None:
                valid &= np.abs(donor_grad - receiver_grad) <= gradient_gap
            if protected is not None and donor_protected is not None:
                valid &= ~donor_protected
            yy, xx = np.nonzero(valid)
            for ly, lx in zip(yy.tolist(), xx.tolist()):
                donor = int(donor_view[ly, lx])
                receiver = int(receiver_view[ly, lx])
                gy = row0 + ly * row_step
                gx = col0 + lx * col_step
                pos = gy * w + gx
                pairs[(donor, receiver)].append(pos)

        # 分块构建边界候选，控制大地图上的临时内存。
        chunk = 256
        for y0 in range(0, h, chunk):
            y1 = min(h, y0 + chunk)
            block = p[y0:y1, :]
            gblock = grad[y0:y1, :] if grad is not None else None
            prblock = protected[y0:y1, :] if protected is not None else None
            if w > 1:
                collect(block[:, 1:], block[:, :-1], y0, 1, 1, 1,
                        gblock[:, 1:] if gblock is not None else None,
                        gblock[:, :-1] if gblock is not None else None,
                        prblock[:, 1:] if prblock is not None else None)
                collect(block[:, :-1], block[:, 1:], y0, 0, 1, 1,
                        gblock[:, :-1] if gblock is not None else None,
                        gblock[:, 1:] if gblock is not None else None,
                        prblock[:, :-1] if prblock is not None else None)
        for y0 in range(0, max(0, h - 1), chunk):
            y1 = min(h - 1, y0 + chunk)
            upper = p[y0:y1, :]
            lower = p[y0 + 1:y1 + 1, :]
            g_upper = grad[y0:y1, :] if grad is not None else None
            g_lower = grad[y0 + 1:y1 + 1, :] if grad is not None else None
            pr_upper = protected[y0:y1, :] if protected is not None else None
            pr_lower = protected[y0 + 1:y1 + 1, :] if protected is not None else None
            collect(lower, upper, y0 + 1, 0, 1, 1,
                    g_lower, g_upper, pr_lower)
            collect(upper, lower, y0, 0, 1, 1,
                    g_upper, g_lower, pr_upper)

        if not pairs:
            break
        # 优先处理悬殊比例最大的邻接关系；同一像素可在多个候选中出现，应用时复核。
        ordered_pairs = sorted(
            pairs.items(),
            key=lambda kv: areas[kv[0][0]] / max(1, areas[kv[0][1]]),
            reverse=True,
        )
        moved_this_round = 0
        for (donor, receiver), positions in ordered_pairs:
            if total_moved >= max_total_moves:
                break
            if areas[donor] <= max_ratio * areas[receiver]:
                continue
            # 同一供体不能被削掉太多，防止省份形状变化过度。
            remaining_budget = int(donor_budget[donor] - moved_by_donor[donor])
            if remaining_budget <= 0:
                continue
            seen_pos = set()
            for pos in positions:
                if pos in seen_pos:
                    continue
                seen_pos.add(pos)
                if total_moved >= max_total_moves or remaining_budget <= 0:
                    break
                if areas[donor] <= max_ratio * areas[receiver]:
                    break
                y, x = divmod(int(pos), w)
                if int(p[y, x]) != donor:
                    continue
                # 原像素仍须与接收省份相邻，避免按过期候选跨过边界。
                adjacent = ((y > 0 and int(p[y - 1, x]) == receiver)
                            or (y + 1 < h and int(p[y + 1, x]) == receiver)
                            or (x > 0 and int(p[y, x - 1]) == receiver)
                            or (x + 1 < w and int(p[y, x + 1]) == receiver))
                if not adjacent or not _safe_remove_boundary_pixel(p, y, x, donor):
                    continue
                p[y, x] = receiver
                areas[donor] -= 1
                areas[receiver] += 1
                moved_by_donor[donor] += 1
                remaining_budget -= 1
                total_moved += 1
                moved_this_round += 1
                adjusted_pairs.add((donor, receiver))
        if moved_this_round == 0:
            break

    return p, total_moved, len(adjusted_pairs)


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
@dataclass
class Summary:
    detected: list = field(default_factory=list)
    created: list = field(default_factory=list)
    files_changed: list = field(default_factory=list)
    out_dir: str = ""
    report_csv: str = ""
    report_txt: str = ""
    preview_before: str = ""
    preview_after: str = ""
    notes: list = field(default_factory=list)
    elapsed: float = 0.0
    dry_run: bool = False

    def counts(self):
        return dict(detected=len(self.detected), created=len(self.created))


def _log(cb, msg):
    if cb:
        cb(msg)


def build_land_neighbor_graph(pid_map: np.ndarray, land_ids: set, chunk_rows: int = 256):
    """从省份 ID 图建立仅限陆地的邻接图，并记录每对省份的共享边长。"""
    p = np.asarray(pid_map, dtype=np.int32)
    h, w = p.shape
    if not land_ids:
        return {}
    max_id = max(int(p.max()), max(int(v) for v in land_ids))
    land_lut = np.zeros(max_id + 1, dtype=bool)
    ids = [int(v) for v in land_ids if 0 <= int(v) <= max_id]
    if not ids:
        return {}
    land_lut[ids] = True
    graph = collections.defaultdict(Counter)
    scale = max_id + 1

    def add_edges(a, b):
        valid = (a != b) & (a >= 0) & (b >= 0) & (a <= max_id) & (b <= max_id)
        valid &= land_lut[np.clip(a, 0, max_id)] & land_lut[np.clip(b, 0, max_id)]
        if not valid.any():
            return
        av = a[valid].astype(np.int64, copy=False)
        bv = b[valid].astype(np.int64, copy=False)
        lo, hi = np.minimum(av, bv), np.maximum(av, bv)
        keys, counts = np.unique(lo * scale + hi, return_counts=True)
        for key, count in zip(keys.tolist(), counts.tolist()):
            lo_id, hi_id = int(key // scale), int(key % scale)
            graph[lo_id][hi_id] += int(count)
            graph[hi_id][lo_id] += int(count)

    for y0 in range(0, h, max(1, int(chunk_rows))):
        y1 = min(h, y0 + max(1, int(chunk_rows)))
        block = p[y0:y1]
        if w > 1:
            add_edges(block[:, :-1], block[:, 1:])
        if y0 < h - 1:
            yv1 = min(y1, h - 1)
            add_edges(p[y0:yv1], p[y0 + 1:yv1 + 1])
    return graph


def apply_neighbor_transition(rows: list, pid_map: np.ndarray, definition: Definition,
                              cfg: Config, target_of=None):
    """按未细化陆地邻居的实际面积，平滑调整周边候选地块的目标面积。

    距离 1/2/3 跳的目标权重递减；沿途只穿过本轮待细化陆地，海洋、湖泊不参与。
    自动候选会迭代重判，移除的地块会成为新的保护源；手动指定拆分不被拦截。
    """
    if not cfg.neighbor_transition_enabled or not rows or cfg.scope not in ("all", "land"):
        return rows, 0
    land_ids = {int(pid) for pid, rec in definition.recs.items() if str(rec[4]).lower() == "land"}
    if not land_ids:
        return rows, 0
    graph = build_land_neighbor_graph(pid_map, land_ids)
    if not graph:
        return rows, 0
    flat = np.asarray(pid_map, dtype=np.int32).ravel()
    area_counts = np.bincount(flat[flat >= 0], minlength=max(max(land_ids), int(flat.max()) if flat.size else 0) + 1)
    weights_by_hop = {1: 0.65, 2: 0.40, 3: 0.20}
    max_hops = max(1, min(3, int(cfg.neighbor_transition_hops)))
    weights_by_hop = {d: weights_by_hop[d] for d in range(1, max_hops + 1)}
    selected = {int(r["prov_id"]) for r in rows if r.get("type") == "land"}
    manual_ids = set(int(v) for v in cfg.include_ids) | set(int(v) for v in cfg.parts_override)
    removed_total = 0

    def nearest_protected(pid: int, selected_ids: set):
        visited = {pid}
        frontier = [pid]
        for dist in range(1, max_hops + 1):
            sources = Counter()
            next_front = []
            for cur in frontier:
                for nb, edge_len in graph.get(cur, {}).items():
                    if nb in visited:
                        continue
                    visited.add(nb)
                    if nb not in selected_ids:
                        sources[nb] += int(edge_len)
                    else:
                        next_front.append(nb)
            if sources:
                vals = sorted((int(area_counts[src]), int(boundary)) for src, boundary in sources.items()
                              if src < len(area_counts) and area_counts[src] > 0)
                if vals:
                    total_w = sum(w for _, w in vals)
                    acc = 0
                    ref = vals[-1][0]
                    for a, w in vals:
                        acc += w
                        if acc * 2 >= total_w:
                            ref = a
                            break
                    return dist, ref
            frontier = next_front
            if not frontier:
                break
        return None, None

    def effective_target(row, selected_ids):
        pid = int(row["prov_id"])
        base = int(row.get("base_target_pixels", row.get("target_pixels", cfg.target())))
        if pid in manual_ids or row.get("forced"):
            return base, None, None
        dist, ref = nearest_protected(pid, selected_ids)
        if dist is None or ref is None:
            return base, None, None
        weight = weights_by_hop.get(dist, 0.0)
        # 不讓不合理的超大/極小邻居产生突变，目标值仍保持正数。
        eff = max(1, int(round(base * (1.0 - weight) + ref * weight)))
        return eff, dist, ref

    # 最多四轮，保证被判定为“不需要切”的地块会立即成为邻区保护源。
    current_rows = list(rows)
    for _ in range(4):
        current_selected = {int(r["prov_id"]) for r in current_rows if r.get("type") == "land"}
        to_remove = set()
        for row in current_rows:
            if row.get("type") != "land":
                continue
            eff, dist, ref = effective_target(row, current_selected)
            row["target_pixels"] = eff
            row["neighbor_hops"] = dist
            row["neighbor_reference_area"] = ref
            if int(row["prov_id"]) in manual_ids or row.get("forced"):
                continue
            # 有密度图时加 25% 缓冲，防止 150px/目标 120px 这类临界地块被切成 75+75。
            margin = max(1.0, float(cfg.split_hysteresis_ratio)) if target_of is not None else 1.0
            if int(row.get("split_area", row.get("area", 0))) <= eff * margin:
                to_remove.add(int(row["prov_id"]))
        if not to_remove:
            break
        prev_len = len(current_rows)
        current_rows = [r for r in current_rows if int(r["prov_id"]) not in to_remove]
        removed_total += prev_len - len(current_rows)
        if not current_rows:
            break

    current_selected = {int(r["prov_id"]) for r in current_rows if r.get("type") == "land"}
    for row in current_rows:
        if row.get("type") == "land":
            eff, dist, ref = effective_target(row, current_selected)
            row["target_pixels"] = eff
            row["neighbor_hops"] = dist
            row["neighbor_reference_area"] = ref
            if int(row["prov_id"]) not in manual_ids and not row.get("forced"):
                row["k"] = parts_for(int(row.get("split_area", row.get("area", 0))), cfg, eff)
    return current_rows, removed_total

def detect(packed: np.ndarray, cidx: ColorIndex, definition: Definition,
           continent_names: list, cfg: Config, state_of: dict, sr_of: dict,
           target_of=None, exclude_extra=None, skip_gradient_ids=None,
           split_area_of=None):
    rows = []
    include = set(int(x) for x in cfg.include_ids)
    exclude = set(int(x) for x in cfg.exclude_ids)
    skip_gradient_ids = set(int(x) for x in (skip_gradient_ids or ()))
    split_area_of = split_area_of or {}
    if exclude_extra:
        exclude |= set(int(x) for x in exclude_extra)
    conts = set(int(x) for x in cfg.continents)
    for pid, rec in definition.recs.items():
        if pid == 0:
            continue
        color = definition.id2color.get(pid)
        if color is None:
            continue
        area = cidx.area.get(color, 0)
        split_area = int(split_area_of.get(pid, area))
        if pid in include or pid in cfg.parts_override:
            split_area = area
        ptype = rec[4]
        if pid in exclude:
            continue
        # 白色/近白密度区域是明确的“不细化”区域，而不是仅把目标面积调大。
        # 显式 include 或 parts_override 属于用户手动指定，优先于此自动规则。
        if pid in skip_gradient_ids and pid not in include and pid not in cfg.parts_override:
            continue
        forced = (pid in include) and not cfg.only_ids
        thr = target_of(pid) if target_of is not None else cfg.threshold
        # 有渐变图且启用白色保护时，阈值依据“实际需要细化的非白像素面积”，
        # 而不是整块地块的面积；因此横跨黑白边界的地块不会连白色部分一起被切开。
        if cfg.only_ids:
            if include and pid not in include:
                continue
        elif not forced:
            if split_area <= thr:
                continue
        # 类型 / 大洲过滤(强制指定的地块不受影响)
        if not forced:
            if cfg.scope != "all" and ptype != cfg.scope:
                continue
            if conts:
                try:
                    cid = int(rec[7])
                except ValueError:
                    cid = -1
                if cid not in conts:
                    continue
        # 显式 include / parts 是手动指令，仍允许对整块地块操作。
        k = cfg.parts_override.get(pid) or parts_for(split_area, cfg, thr if target_of is not None else 0)
        if split_area < 2 * max(1, cfg.min_part_pixels):
            continue
        cname = ""
        try:
            # HOI4 的 definition.csv 里大洲ID从 1 开始（1=europe），0 通常表示海洋/湖泊。
            ci = int(rec[7]) - 1
            if 0 <= ci < len(continent_names):
                cname = continent_names[ci]
        except ValueError:
            pass
        rows.append(dict(
            prov_id=pid, color=color, hex="%02X%02X%02X" % key_rgb(color),
            area=area, split_area=split_area, k=k, target_pixels=max(1, int(thr)),
            base_target_pixels=max(1, int(thr)), type=ptype, coastal=rec[5], terrain=rec[6],
            continent=rec[7], continent_name=cname,
            state=os.path.basename(state_of.get(pid, "")),
            sr=os.path.basename(sr_of.get(pid, "")),
            state_path=state_of.get(pid, ""), sr_path=sr_of.get(pid, ""),
            forced=pid in include,
        ))
    rows.sort(key=lambda r: (-r["area"], r["prov_id"]))
    return rows


def _border_mask(mask: np.ndarray) -> np.ndarray:
    er = (mask & np.roll(mask, 1, 0) & np.roll(mask, -1, 0)
          & np.roll(mask, 1, 1) & np.roll(mask, -1, 1))
    return mask & ~er


def render_preview(packed: np.ndarray, target_keys: set, path: str, scale: float,
                   mode: str = "before", target_mask: Optional[np.ndarray] = None):
    """生成预览图。
    mode='before': 目标地块半透明红填充 + 亮红描边 + 图例(修改前哪些地方会被动)
    mode='after' : 新地图 + 白色省界 (强调所有地块边界)
    """
    from PIL import Image as _I, ImageDraw
    h, w = packed.shape
    r = ((packed >> 16) & 255).astype(np.uint8)
    g = ((packed >> 8) & 255).astype(np.uint8)
    b = (packed & 255).astype(np.uint8)
    img = np.stack([r, g, b], -1).astype(np.float32)

    if mode == "before":
        if target_mask is not None and target_mask.shape == packed.shape:
            mask = target_mask.astype(bool, copy=False)
        elif target_keys:
            keys = np.fromiter(target_keys, dtype=np.int64)
            mask = np.isin(packed, keys)
        else:
            mask = np.zeros(packed.shape, dtype=bool)
        if mask.any():
            img[mask] = img[mask] * 0.45 + np.array([255, 0, 0], np.float32) * 0.55
            bd = _border_mask(mask)
            img[bd] = (255, 255, 0)
        else:
            # 无目标时也画一下省界
            diff = np.zeros((h, w), bool)
            diff[:, 1:] = packed[:, 1:] != packed[:, :-1]
            diff[1:, :] |= packed[1:, :] != packed[:-1, :]
            img[diff] = (0, 0, 0)
    else:
        diff = np.zeros((h, w), bool)
        diff[:, 1:] = packed[:, 1:] != packed[:, :-1]
        diff[1:, :] |= packed[1:, :] != packed[:-1, :]
        img[diff] = (255, 255, 255)

    im = _I.fromarray(img.astype(np.uint8), "RGB")
    if scale != 1.0:
        im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), _I.NEAREST)
    # 图例
    dr = ImageDraw.Draw(im)
    box = "修改前: 黄色/红色 = 将被切分的地块" if mode == "before" else "修改后: 白线 = 新省界"
    dw = 8 + 7 * len(box)
    dr.rectangle([4, 4, 4 + dw, 26], fill=(0, 0, 0))
    dr.text((10, 9), box, fill=(255, 255, 255))
    n = len(target_keys) if mode == "before" else 0
    if mode == "before":
        dr.text((10, 30), "目标地块数: %d" % n, fill=(255, 255, 0))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    im.save(path)
    return path


def run(cfg: Config, progress: Optional[Callable[[str], None]] = None,
        cancel: Optional[Callable[[], bool]] = None) -> Summary:
    t0 = time.time()
    summary = Summary(dry_run=cfg.dry_run)
    mod = cfg.mod_dir
    game = getattr(cfg, "game_dir", "") or ""
    map_dir = os.path.join(mod, "map")
    from . import paths as _paths

    def _R(*parts):
        return _paths.resolve(mod, game, *parts)

    if game and os.path.isdir(game):
        _log(progress, "原版目录(缺失文件回退): %s" % game)

    _log(progress, "读取 definition.csv ...")
    definition = read_definition(_R("map", "definition.csv"))
    continent_names = read_continent_names(mod, game)

    _log(progress, "读取 provinces.bmp ...")
    arr = load_provinces_bmp(_R("map", "provinces.bmp"))
    packed = pack_rgb(arr)
    h, w = packed.shape

    _log(progress, "建立像素索引(统计各颜色面积) ...")
    cidx = ColorIndex(packed)

    # 未在 definition 中登记的颜色
    unknown = [v for v in cidx.uniq.tolist() if v not in definition.color2id]
    if unknown:
        _log(progress, "[注意] 有 %d 种颜色未登记在 definition.csv 中(将被忽略)" % len(unknown))

    # 颜色->省份ID 查表(后面多次用到)
    col2pid = np.zeros(1 << 24, dtype=np.int32)
    for _pid, _rec in definition.recs.items():
        col2pid[rgb_key(int(_rec[1]), int(_rec[2]), int(_rec[3]))] = _pid
    land_ids = set(i for i, r in definition.recs.items() if r[4] == "land")
    sea_ids = [i for i, r in definition.recs.items() if r[4] == "sea"]
    coast0 = None
    if cfg.repair:
        try:
            from .integrity import coastal_mask
            coast0 = coastal_mask(col2pid[packed], sea_ids)
        except Exception as _e:
            _log(progress, "沿海判定不可用: %s" % _e)

    _log(progress, "扫描 states / strategic regions ...")
    state_files = _paths.list_merged(mod, game, "history", "states", exts=(".txt",),
                                    merge=cfg.game_merge)
    sr_files = _paths.list_merged(mod, game, "map", "strategicregions", exts=(".txt",),
                                  merge=cfg.game_merge)
    state_of, state_txt = build_province_owner_index(state_files)
    sr_of, sr_txt = build_province_owner_index(sr_files)

    # ---- 拆分前先把"重复归属"校正, 保证新地块落到正确的战略区/州 ----
    sr_text = {fp: sr_txt[fp] for fp in sr_files}
    st_text = {fp: state_txt[fp] for fp in state_files}
    if cfg.repair:
        try:
            from .integrity import build_adjacency, plan_membership, apply_membership
            _nb0 = build_adjacency(col2pid[packed])[1]
            for _label, _files, _texts in (("战略区", sr_files, sr_text), ("州", state_files, st_text)):
                _remove, _rep, _memb = plan_membership(_files, _texts, _nb0, set(definition.recs))
                if _rep:
                    _ch = apply_membership(_files, _texts, _remove)
                    _texts.update(_ch)
                    msg = "%s: 拆分前校正 %d 个重复归属" % (_label, len(_rep))
                    summary.notes.append(msg)
                    _log(progress, msg)
            sr_of = _owner_from(sr_text)
            state_of = _owner_from(st_text)
        except Exception as _e:
            _log(progress, "拆分前校正跳过: %s" % _e)

    # 渐变细化: 使用局部像素密度决定哪些区域参与切割。
    target_of = None
    skip_gradient_ids = set()
    split_area_of = {}
    gradient_image = None
    gradient_effective = None
    density_active_mask = None
    if cfg.grad_path and os.path.isfile(cfg.grad_path):
        try:
            gradient_image = load_gradient(cfg.grad_path, h, w)
            _gt, _ga = gradient_targets(col2pid[packed], gradient_image, cfg, return_averages=True)
            gradient_effective = (1.0 - gradient_image) if cfg.grad_invert else gradient_image
            if cfg.grad_skip_white:
                cutoff = min(1.0, max(0.0, float(cfg.grad_white_cutoff)))
                density_active_mask = gradient_effective < cutoff
                flat_pid = col2pid[packed].ravel()
                active_flat = density_active_mask.ravel()
                active_pids = flat_pid[active_flat]
                max_pid_for_grad = max(definition.max_id, int(flat_pid.max()) if flat_pid.size else 0)
                active_counts = np.bincount(active_pids, minlength=max_pid_for_grad + 1)
                active_sums = np.bincount(
                    active_pids,
                    weights=gradient_effective.ravel()[active_flat],
                    minlength=max_pid_for_grad + 1,
                )
                active_means = active_sums / np.maximum(active_counts, 1)
                active_targets = cfg.grad_black_px + (cfg.grad_white_px - cfg.grad_black_px) * active_means
                target_map = {
                    int(pid): int(round(active_targets[pid]))
                    for pid in definition.recs
                    if pid < len(active_counts) and active_counts[pid] > 0
                }
                # 白色像素不参与自动切割；只有整块地块完全没有非白像素才直接跳过该 ID。
                skip_gradient_ids = {
                    int(pid) for pid in definition.recs
                    if pid < len(active_counts) and active_counts[pid] == 0
                }
                for pid, color in definition.id2color.items():
                    split_area_of[int(pid)] = int(active_counts[pid]) if pid < len(active_counts) else 0
                    # 手动 include / parts 指定时，保留“整块地块”操作语义。
                    if pid in cfg.include_ids or pid in cfg.parts_override:
                        split_area_of[int(pid)] = int(cidx.area.get(color, 0))
                        target_map[int(pid)] = _gt.get(pid, cfg.target())
                target_of = lambda pid, _tm=target_map: _tm.get(pid, cfg.target())
                _log(progress, "密度图白色保护: %d 个全白地块跳过；混合地块只细化非白像素 (阈值=%d/255)" % (
                    len(skip_gradient_ids), int(round(cutoff * 255))))
            else:
                target_of = lambda pid, _gt=_gt: _gt.get(pid, cfg.target())
            _log(progress, "已加载渐变图: %s (黑=%dpx, 白=%dpx)" % (
                os.path.basename(cfg.grad_path), cfg.grad_black_px, cfg.grad_white_px))
        except Exception as _e:
            _log(progress, "渐变图加载失败: %s" % _e)
    elif cfg.grad_path:
        _log(progress, "[注意] 渐变图不存在: %s" % cfg.grad_path)

    balance_protected_mask = coast0
    if cfg.grad_skip_white and density_active_mask is not None:
        white_protected = ~density_active_mask
        if balance_protected_mask is None:
            balance_protected_mask = white_protected.copy()
        else:
            balance_protected_mask = np.asarray(balance_protected_mask, dtype=bool) | white_protected

    _log(progress, "按规则检测目标地块 ...")
    # 按州(省份)排除: 把指定州的全部下属地块排除
    state_exclude = set()
    if cfg.exclude_states:
        want = set(int(x) for x in cfg.exclude_states)
        idre = re.compile(r"\bid\s*=\s*(\d+)")
        for _fp, _t in state_txt.items():
            _m = idre.search(_t)
            if not _m or int(_m.group(1)) not in want:
                continue
            _pm = PROV_RE.search(_t)
            if not _pm:
                continue
            for tok in re.sub(r"#[^\n]*", "", _pm.group(2)).split():
                if tok.isdigit():
                    state_exclude.add(int(tok))
        _log(progress, "排除州 %s -> 涉及地块 %d 个" % (sorted(want), len(state_exclude)))
    rows = detect(packed, cidx, definition, continent_names, cfg, state_of, sr_of,
                  target_of=target_of, exclude_extra=state_exclude,
                  skip_gradient_ids=skip_gradient_ids, split_area_of=split_area_of)
    # 陆地邻区过渡：把“本轮不细化”的陆地当作保护源，越靠近其边界，
    # 越采用这些未细化邻区的实际面积作为切分目标；最多迭代四轮以稳定候选集合。
    if rows and cfg.neighbor_transition_enabled:
        before_transition = len(rows)
        rows, _transition_removed = apply_neighbor_transition(
            rows, col2pid[packed], definition, cfg, target_of=target_of)
        _log(progress, "未细化陆地邻区过渡: 候选 %d -> %d，迭代移除 %d 个临界地块" % (
            before_transition, len(rows), _transition_removed))
    summary.detected = rows
    _log(progress, "检测到 %d 个待细化地块" % len(rows))

    target_preview_mask = None
    if rows and cfg.grad_skip_white and density_active_mask is not None:
        selected_ids = np.asarray([r["prov_id"] for r in rows], dtype=np.int32)
        id_map_for_preview = col2pid[packed]
        target_preview_mask = np.isin(id_map_for_preview, selected_ids)
        manual_ids = set(int(v) for v in cfg.include_ids) | set(int(v) for v in cfg.parts_override)
        if manual_ids:
            manual_mask = np.isin(id_map_for_preview, np.asarray(sorted(manual_ids), dtype=np.int32))
            target_preview_mask &= (density_active_mask | manual_mask)
        else:
            target_preview_mask &= density_active_mask

    # 预演(只分析): 不写文件, 但生成"修改前"预览
    if cfg.dry_run:
        if cfg.make_preview and rows:
            hl = set(r["color"] for r in rows)
            p = os.path.join(cfg.out_dir or os.path.join(mod, ".."), "preview_targets.png")
            try:
                render_preview(packed, hl, p, cfg.preview_scale, mode="before",
                               target_mask=target_preview_mask)
                summary.preview_before = p
                _log(progress, "预览(修改前): %s" % p)
            except Exception as e:
                _log(progress, "预览失败: %s" % e)
        summary.elapsed = time.time() - t0
        return summary

    out_dir = cfg.mod_dir if cfg.in_place else cfg.out_dir
    if cfg.clean_output and not cfg.in_place:
        _clear_output_dir(out_dir, cfg.mod_dir, progress)
    os.makedirs(out_dir, exist_ok=True)
    img = arr.copy()

    # 输出路径映射
    def out_path(src_abs):
        rel = _paths.out_rel(src_abs, mod, game)
        return os.path.join(out_dir, rel)

    changed = set()
    new_lines = []
    def_overrides = {}
    next_id = definition.max_id + 1
    used_colors = set(definition.color2id.keys())
    rng = np.random.default_rng(cfg.seed)
    hl_keys = set()

    # state / SR 文本(已在上面做完重复归属校正, 这里继续叠加新省份)
    sr_orig = {fp: sr_txt[fp] for fp in sr_files}
    st_orig = {fp: state_txt[fp] for fp in state_files}
    children = {}          # 父地块ID -> [新地块ID...]

    total = len(rows)
    for i, row in enumerate(rows, 1):
        if cancel and cancel():
            _log(progress, "已取消")
            break
        pid = row["prov_id"]
        color = row["color"]
        idx = cidx.indices(color)
        if idx.size == 0:
            continue
        ys = (idx // w).astype(np.int64)
        xs = (idx % w).astype(np.int64)
        # 密度图白色保护：对跨越黑白边界的地块，仅对非白像素子区域做切分。
        # 白色像素仍保持原 ID/原形状；手动 include 或 parts 指定仍按整块切分。
        use_density_components = (gradient_effective is not None and density_active_mask is not None
                                  and pid not in cfg.include_ids and pid not in cfg.parts_override)
        prng = np.random.default_rng(cfg.seed + pid * 7919)
        if use_density_components:
            active = gradient_effective[ys, xs] < min(1.0, max(0.0, float(cfg.grad_white_cutoff)))
            if int(active.sum()) < 2 * max(1, cfg.min_part_pixels):
                continue
            active_ys, active_xs = ys[active], xs[active]
            res = split_active_components(
                active_ys, active_xs, row["k"], prng, cfg.min_part_pixels,
                target_pixels=int(row.get("target_pixels", cfg.target())),
                split_margin=cfg.split_hysteresis_ratio,
                optimize_rounds=cfg.split_search_rounds,
                province_ys=ys, province_xs=xs)
        else:
            res = split_region(ys, xs, row["k"], prng, cfg.min_part_pixels,
                               optimize_rounds=cfg.split_search_rounds)
        if not res:
            continue
        grid, y0, x0, order = res
        hl_keys.add(color)
        if len(order) < 2:
            continue

        base = definition.recs[pid]
        stored_coastal = str(base[5]).lower() == "true"
        is_land = base[4] == "land"

        # 计算每块的像素与是否沿海(只有陆地省份能是"沿海")
        part_px = {}
        part_coastal = {}
        for lab in order:
            sub = grid == lab
            yy = np.nonzero(sub)[0] + y0
            xx = np.nonzero(sub)[1] + x0
            part_px[lab] = (yy, xx)
            part_coastal[lab] = bool(is_land and coast0 is not None and coast0[yy, xx].any())

        # 沿海: 把"最大且沿海"的块留给原ID, 保证原地块仍是沿岸的
        any_coast = any(part_coastal.values())
        if (stored_coastal or any_coast) and any_coast:
            coastal_labs = [l for l in order if part_coastal[l]]
            core_lab = max(coastal_labs, key=lambda l: part_px[l][0].size)
            order = [core_lab] + [l for l in order if l != core_lab]
        orig_coast = stored_coastal or part_coastal[order[0]]

        # order[0] = 原ID保留块
        created_ids = []
        need = len(order) - 1
        colors = make_colors(need, used_colors, rng)
        if len(colors) < need:
            continue
        for j, lab in enumerate(order[1:]):
            rc, gc, bc, key = colors[j]
            new_id = next_id
            next_id += 1
            created_ids.append(new_id)
            yy, xx = part_px[lab]
            img[yy, xx, 0] = rc
            img[yy, xx, 1] = gc
            img[yy, xx, 2] = bc
            ch_coastal = bool(part_coastal[lab])
            new_lines.append([str(new_id), str(rc), str(gc), str(bc),
                              base[4], "true" if ch_coastal else "false",
                              base[6], base[7]])

        # 原地块 coastal 可能变化
        new_cv = "true" if orig_coast else "false"
        if new_cv != str(base[5]).lower():
            nl2 = list(base)
            nl2[5] = new_cv
            def_overrides[pid] = nl2

        # 更新 state / SR (内存中)
        if created_ids:
            children[pid] = created_ids
        sp = row["state_path"]
        if sp and sp in st_text:
            st_text[sp], _ok = inject_ids(st_text[sp], created_ids)
        srp = row["sr_path"]
        if srp and srp in sr_text:
            sr_text[srp], _ok = inject_ids(sr_text[srp], created_ids)

        summary.created.append(dict(
            prov_id=pid, area=row["area"], parts=len(order),
            new_ids=created_ids, state=row["state"], sr=row["sr"],
        ))
        if i % 25 == 0 or i == total:
            _log(progress, "细化进度 %d/%d ..." % (i, total))

    # ===== 陆地邻接面积均衡(有无密度图都尝试) =====
    # 仅允许在符合大洲筛选、未被排除的陆地省份之间转移边界像素；海洋/湖泊永不参与。
    balance_land_ids = set()
    if cfg.scope in ("all", "land"):
        try:
            # 给新生成的 RGB 颜色补上 ID 映射；复用已有查找表，避免再复制一个 64MB 表。
            for _nl in new_lines:
                col2pid[rgb_key(int(_nl[1]), int(_nl[2]), int(_nl[3]))] = int(_nl[0])
            pid_map_before_balance = col2pid[pack_rgb(img)]
            balance_land_ids = set()
            all_records = list(definition.recs.items())
            all_records.extend((int(_nl[0]), _nl) for _nl in new_lines)
            allowed_continents = set(int(v) for v in cfg.continents)
            blocked_ids = set(int(v) for v in cfg.exclude_ids) | set(int(v) for v in state_exclude)
            for _pid, _rec in all_records:
                if _rec[4] != "land" or int(_pid) in blocked_ids:
                    continue
                if allowed_continents:
                    try:
                        _cid = int(_rec[7])
                    except (ValueError, TypeError):
                        continue
                    if _cid not in allowed_continents:
                        continue
                balance_land_ids.add(int(_pid))
            if balance_land_ids:
                _balanced, _moved, _pairs = balance_land_shapes(
                    pid_map_before_balance,
                    balance_land_ids,
                    gradient=gradient_effective,
                    max_ratio=3.0,
                    gradient_gap=0.22,
                    max_donor_fraction=0.25,
                    max_rounds=4,
                    max_total_moves=200000,
                    protected_mask=balance_protected_mask,
                )
                if _moved:
                    _changed_px = _balanced != pid_map_before_balance
                    _max_id_now = max(definition.max_id, next_id - 1)
                    _color_lut = np.zeros(_max_id_now + 1, dtype=np.int64)
                    for _pid, _color in definition.id2color.items():
                        _color_lut[int(_pid)] = int(_color)
                    for _nl in new_lines:
                        _color_lut[int(_nl[0])] = rgb_key(int(_nl[1]), int(_nl[2]), int(_nl[3]))
                    _new_colors = _color_lut[_balanced[_changed_px]]
                    img[_changed_px] = np.stack([
                        (_new_colors >> 16) & 255,
                        (_new_colors >> 8) & 255,
                        _new_colors & 255,
                    ], axis=-1).astype(np.uint8)
                _msg = "陆地邻接形状均衡: 调整 %d 个像素, 涉及 %d 对相邻地块" % (_moved, _pairs)
                summary.notes.append(_msg)
                _log(progress, _msg)
            else:
                _log(progress, "陆地邻接形状均衡: 当前筛选条件下没有可调整的陆地地块")
        except Exception as _balance_error:
            _log(progress, "陆地形状均衡跳过: %s" % _balance_error)

    # ===== 边界清理: 十字交叉 + 孤立/不连通地块 (写 bmp 之前做) =====
    if cfg.repair:
        try:
            from .integrity import (remove_crossings, fix_connectivity,
                                    count_disconnected, _count_crossings)
            _log(progress, "清理边界(十字交叉 / 不连通) ...")
            pid2 = col2pid[pack_rgb(img)]
            nfix1 = nfix2 = prov_fixed = frag_fixed = 0
            nleft = disc = 0
            for _round in range(6):
                pid2, _nf, _nl = remove_crossings(pid2, progress=lambda m: None)
                nfix1 += _nf
                pid2, _pf, _ff = fix_connectivity(pid2, progress=lambda m: _log(progress, m),
                                                 island_max=cfg.island_max_px)
                prov_fixed += _pf
                frag_fixed += _ff
                if _nf == 0 and _pf == 0 and _ff == 0:
                    break
            # 连通性修复本身可能挪动少量边界像素；先做一次轻量面积复核，
            # 再统一执行最终边界清理，避免均衡步骤把十字交叉重新带回来。
            if cfg.scope in ("all", "land") and balance_land_ids:
                try:
                    _balanced2, _moved2, _pairs2 = balance_land_shapes(
                        pid2,
                        balance_land_ids,
                        gradient=gradient_effective,
                        max_ratio=3.0,
                        gradient_gap=0.22,
                        max_donor_fraction=0.10,
                        max_rounds=1,
                        max_total_moves=25000,
                        protected_mask=balance_protected_mask,
                    )
                    if _moved2:
                        pid2 = _balanced2
                    _msg = "修复后陆地均衡复核: 调整 %d 个像素, 涉及 %d 对相邻地块" % (
                        _moved2, _pairs2)
                    summary.notes.append(_msg)
                    _log(progress, _msg)
                except Exception as _balance_error:
                    _log(progress, "修复后面积均衡复核跳过: %s" % _balance_error)
            # 收尾: 只清交叉(连通性修复会引入少量交叉)
            pid2, _nf3, _ = remove_crossings(pid2, progress=lambda m: None)
            nfix1 += _nf3
            disc, _dfrag = count_disconnected(pid2, cfg.island_max_px)
            nfix2 = _count_crossings(pid2)
            maxid = max(definition.max_id, next_id - 1)
            col_lut = np.zeros(maxid + 1, dtype=np.int64)
            for _pid, _c in definition.id2color.items():
                col_lut[_pid] = _c
            for _nl in new_lines:
                col_lut[int(_nl[0])] = rgb_key(int(_nl[1]), int(_nl[2]), int(_nl[3]))
            packed2 = col_lut[pid2]
            img = np.stack([(packed2 >> 16) & 255, (packed2 >> 8) & 255,
                            packed2 & 255], -1).astype(np.uint8)
            for _m in ("十字交叉边界: 修正 %d 处, 剩余 %d" % (nfix1, nfix2),
                       "不连通地块: 修正 %d 个 / %d 块碎片, 剩余 %d 个" % (prov_fixed, frag_fixed, disc)):
                summary.notes.append(_m)
                _log(progress, _m)
        except Exception as _e:
            import traceback
            _log(progress, "边界清理出错: %s" % _e)
            traceback.print_exc()

    # 写 provinces.bmp
    _log(progress, "写出 provinces.bmp ...")
    bmp_src = _R("map", "provinces.bmp")
    bmp_out = out_path(bmp_src)
    # 先记住原文件大小。in-place 模式下如果保存后再 getsize，就已经变成新文件大小了。
    try:
        original_bmp_size = os.path.getsize(bmp_src)
    except OSError:
        original_bmp_size = 0

    save_provinces_bmp(img, bmp_out)

    # 若原图末尾有填充字节, 补齐到相同大小, 尽量与原文件一致
    if original_bmp_size:
        try:
            new_bmp_size = os.path.getsize(bmp_out)
            if original_bmp_size > new_bmp_size:
                with open(bmp_out, "ab") as _f:
                    _f.write(b"\x00" * (original_bmp_size - new_bmp_size))
        except OSError as e:
            _log(progress, "BMP 尾部填充跳过: %s" % e)
    changed.add(bmp_out)

    # 写 definition.csv (保留原始排版与注释 + 覆写 coastal + 追加新行)
    _log(progress, "写出 definition.csv ...")
    def_out = out_path(_R("map", "definition.csv"))
    raw = definition.raw
    nl_b = b"\r\n" if b"\r\n" in raw[:200] else b"\n"
    nl_s = "\r\n" if nl_b == b"\r\n" else "\n"
    body_lines = []
    for ln in definition.lines:
        s = ln.rstrip("\r")
        p = s.split(";")
        if len(p) >= 8 and p[0].isdigit() and int(p[0]) in def_overrides:
            body_lines.append(";".join(def_overrides[int(p[0])]))
        else:
            body_lines.append(s)
    buf = bytearray((nl_s.join(body_lines) + nl_s).encode("utf-8", "surrogateescape"))
    for p in new_lines:
        buf += (";".join(p) + "\r\n").encode("utf-8", "surrogateescape")
    os.makedirs(os.path.dirname(def_out), exist_ok=True)
    with open(def_out, "wb") as f:
        f.write(bytes(buf))
    changed.add(def_out)

    # ================= 完整性修复 =================
    if cfg.repair:
        try:
            from .integrity import (load_pid_map, build_adjacency, plan_membership,
                                    apply_membership, fix_railways, fix_adjacencies,
                                    fix_adjacency_rules, add_row_links, complete_canals)
            from . import canals as _canals_mod
            _log(progress, "构建新地图邻接关系 ...")
            defn2 = read_definition(def_out)
            pidmap = load_pid_map(bmp_out, defn2)
            _pairs, nb = build_adjacency(pidmap)
            valid = set(defn2.recs)

            # 河道候选仅作辅助；真正的运河以 adjacency_rules.txt 的显式 CANAL 规则为准
            canal_list = []
            canals_idx = None
            rivers_path = _R("map", "rivers.bmp")
            if os.path.isfile(rivers_path):
                try:
                    _rivers = _canals_mod.load_rivers(rivers_path)
                    canal_list = _canals_mod.detect(pidmap, defn2, _rivers)
                    canals_idx = _canals_mod.index(canal_list)
                    _log(progress, "运河识别: %d 条 (rivers.bmp)" % len(canal_list))
                except Exception as _ce:
                    _log(progress, "运河识别跳过: %s" % _ce)
            sea_ids = set(i for i, r in defn2.recs.items() if r[4] == "sea")

            ap = _R("map", "adjacencies.csv")
            at_orig = _read_text(ap) if os.path.isfile(ap) else ""
            at2 = at_orig
            # 铁路/海峡连接: 像素相邻 + adjacencies.csv 的 From-To
            nb_all = collections.defaultdict(set)
            for k, v in nb.items():
                nb_all[k] = set(v)
            if at_orig:
                add_row_links(nb_all, at_orig)

            def _invalid(text):
                mm = PROV_RE.search(text)
                if not mm:
                    return []
                return [int(x) for x in mm.group(2).split()
                        if x.isdigit() and int(x) not in valid]

            # 1) 战略区 / 州 归属唯一化, 并去掉不存在的省份ID
            for label, files, texts in (("战略区", sr_files, sr_text), ("州", state_files, st_text)):
                invalid_n = sum(len(_invalid(texts[fp])) for fp in files)
                remove, rep, memb = plan_membership(files, texts, nb, valid)
                if cfg.strip_invalid:
                    for fp in files:
                        for i in _invalid(texts[fp]):
                            remove[fp].add(i)
                ch = apply_membership(files, texts, remove)
                for fp, t in ch.items():
                    texts[fp] = t
                msg = "%s: 修正 %d 个重复归属, %d 个无效省份ID" % (label, len(rep), invalid_n)
                summary.notes.append(msg)
                _log(progress, msg)

            # 2) 铁路 (只允许陆地地块作中间点, 考虑海峡/运河连接)
            rp = _R("map", "railways.txt")
            if os.path.isfile(rp):
                land2 = set(i for i, r in defn2.recs.items() if r[4] == "land")
                rt2, nch = fix_railways(_read_text(rp), nb_all, cfg.railway_max_hops,
                                        land=land2)
                if nch:
                    _write_text(out_path(rp), rt2)
                    changed.add(out_path(rp))
                    summary.notes.append("铁路: 修复 %d 处断点" % nch)
                    _log(progress, "铁路: 修复 %d 处断点" % nch)

            # 3) 海峡 / 运河 (adjacencies.csv + adjacency_rules.txt)
            if at_orig:
                at2, nach, rowsfix, unfix = fix_adjacencies(at_orig, nb, children, canals_idx)
                _crows = getattr(fix_adjacencies, "last_canal_rows", 0)
                _cchg = getattr(fix_adjacencies, "last_canal_changed", 0)
                if _crows:
                    summary.notes.append("运河: 识别 %d 条, 修正 %d 条" % (_crows, _cchg))
                    _log(progress, "运河: 识别 %d 条, 修正 %d 条" % (_crows, _cchg))
                if cfg.canal_complete:
                    rules_path = _R("map", "adjacency_rules.txt")
                    rules_text = _read_text(rules_path) if os.path.isfile(rules_path) else ""
                    at2, nadd, added_rows, legacy_removed = complete_canals(
                        at2, canal_list, nb, sea_ids, rules_text=rules_text, children=children)
                    if nadd:
                        summary.notes.append("运河: 补全 %d 条可唯一确认的缺失连线" % nadd)
                        _log(progress, "运河: 补全 %d 条可唯一确认的缺失连线" % nadd)
                    if legacy_removed:
                        summary.notes.append("运河: 清理旧版误生成的 %d 条无规则连线" % legacy_removed)
                        _log(progress, "运河: 清理旧版误生成的 %d 条无规则连线" % legacy_removed)
                else:
                    nadd = 0
                if at2 != at_orig:
                    _write_text(out_path(ap), at2)
                    changed.add(out_path(ap))
                if nach:
                    summary.notes.append("海峡/连线: 修正 %d 条" % nach)
                    _log(progress, "海峡/连线: 修正 %d 条" % nach)
                if unfix:
                    summary.notes.append("海峡/连线: %d 条无法自动修正(需人工检查)" % len(unfix))
                    _log(progress, "[注意] 海峡/连线: %d 条无法自动修正" % len(unfix))
            rlp = _R("map", "adjacency_rules.txt")
            if os.path.isfile(rlp):
                rules_text = _read_text(rlp)
                rlt2, nrch = fix_adjacency_rules(
                    rules_text, nb, children, adjacency_text=at2, canals_list=canal_list)
                _bank_report = getattr(fix_adjacency_rules, "last_report", [])
                _bank_removed = getattr(fix_adjacency_rules, "last_removed", 0)
                _bank_added = getattr(fix_adjacency_rules, "last_added", 0)
                if nrch:
                    _write_text(out_path(rlp), rlt2)
                    changed.add(out_path(rlp))
                _resolved = sum(1 for _r in _bank_report if _r.get("status") in ("OK", "UPDATED"))
                if nrch or _bank_report:
                    _msg = ("运河规则 required_provinces: 已核对 %d 条, 修改 %d 条, "
                            "移除 %d 个不接触河道的省份, 补入 %d 个两岸省份" %
                            (_resolved, nrch, _bank_removed, _bank_added))
                    summary.notes.append(_msg)
                    _log(progress, _msg)

                # Keep a full inventory for every CANAL rule/adjacency row, including
                # unresolved cases. This makes the matching decision auditable.
                if _bank_report:
                    import csv as _csv
                    _audit_path = os.path.join(out_dir, "canal_province_audit.csv")
                    with open(_audit_path, "w", newline="", encoding="utf-8-sig") as _f:
                        _fields = ["rule", "from_to", "through", "components", "component_pixels", "route_pixels",
                                   "component_banks", "off_route_banks", "required_before", "banks_detected",
                                   "removed", "added", "status", "note"]
                        _wr = _csv.DictWriter(_f, fieldnames=_fields, extrasaction="ignore")
                        _wr.writeheader()
                        _wr.writerows(_bank_report)
        except Exception as e:
            import traceback
            _log(progress, "完整性修复出错: %s" % e)
            traceback.print_exc()

    # 写出变动过的 state / SR
    for fp, t in st_text.items():
        if t != st_orig[fp]:
            _write_text(out_path(fp), t)
            changed.add(out_path(fp))
    for fp, t in sr_text.items():
        if t != sr_orig[fp]:
            _write_text(out_path(fp), t)
            changed.add(out_path(fp))

    # 报告
    rep_csv = os.path.join(out_dir, "report.csv")
    os.makedirs(os.path.dirname(rep_csv), exist_ok=True)
    import csv as _csv
    with open(rep_csv, "w", newline="", encoding="utf-8-sig") as f:
        wr = _csv.writer(f)
        wr.writerow(["省份ID", "面积", "拆分数", "新省份ID", "所属state", "所属战略区"])
        for c in summary.created:
            wr.writerow([c["prov_id"], c["area"], c["parts"],
                         " ".join(map(str, c["new_ids"])), c["state"], c["sr"]])
    summary.report_csv = rep_csv

    rep_txt = os.path.join(out_dir, "report.txt")
    with open(rep_txt, "w", encoding="utf-8-sig") as f:
        f.write("HOI4 地块细化报告\n")
        f.write("mod: %s\n" % mod)
        f.write("阈值: %d px  目标: %d px  范围: %s  大洲: %s\n" % (
            cfg.threshold, cfg.target(), cfg.scope, cfg.continents or "全部"))
        f.write("检测到 %d 个过大地块, 新建 %d 个地块\n" % (len(rows), len(summary.created)))
        f.write("新省份ID区间: %d .. %d\n" % (
            definition.max_id + 1, next_id - 1 if next_id > definition.max_id + 1 else definition.max_id))
        for n in summary.notes:
            f.write("修复: %s\n" % n)
        f.write("\n")
        for c in summary.created:
            f.write("%d (%dpx) -> %d块 : %s\n" % (
                c["prov_id"], c["area"], c["parts"], " ".join(map(str, c["new_ids"]))))
    summary.report_txt = rep_txt

    # 预览
    if cfg.make_preview:
        _log(progress, "生成预览图 ...")
        p_before = os.path.join(out_dir, "preview_before.png")
        p_after = os.path.join(out_dir, "preview_after.png")
        render_preview(packed, hl_keys, p_before, cfg.preview_scale, mode="before",
                       target_mask=target_preview_mask)
        render_preview(pack_rgb(img), set(), p_after, cfg.preview_scale, mode="after")
        summary.preview_before = p_before
        summary.preview_after = p_after

    # 清单
    with open(os.path.join(out_dir, "changed_files.txt"), "w", encoding="utf-8-sig") as f:
        for c in sorted(changed):
            f.write(os.path.relpath(c, out_dir) + "\n")

    summary.files_changed = sorted(changed)
    summary.out_dir = out_dir
    summary.elapsed = time.time() - t0
    _log(progress, "完成, 用时 %.1fs" % summary.elapsed)
    return summary


def write_definition_append(path: str, new_lines: list, def_raw: bytes):
    """独立工具: 只往 definition.csv 追加行(不用于主流程)。"""
    buf = bytearray(def_raw)
    if not buf.endswith(b"\n"):
        buf += b"\r\n"
    for p in new_lines:
        buf += (";".join(p) + "\r\n").encode("utf-8", "surrogateescape")
    with open(path, "wb") as f:
        f.write(bytes(buf))
