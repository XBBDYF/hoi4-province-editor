# -*- coding: utf-8 -*-
"""从 rivers.bmp 提取河流连通分量，为显式运河规则提供辅助定位。

HOI4 的 rivers.bmp 只描述可视河流像素；某段河流是否是可通航运河，必须以
adjacencies.csv 和 adjacency_rules.txt 为准，不能只看河流是否连通两个水域。

只依赖 numpy + PIL(+ scipy 可选)。
"""
from __future__ import annotations

import collections

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None

# rivers.bmp 调色板索引 0..11 都是“河流/运河”像素(0源/1流入/2流出/3-11河身)
RIVER_IDX = tuple(range(0, 12))
_WATER_TYPES = ("sea", "lake")

try:
    import scipy.ndimage as _ndi
    _STRUCT = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]])
except Exception:  # pragma: no cover
    _ndi = None
    _STRUCT = None


def load_rivers(path: str) -> np.ndarray:
    im = Image.open(path)
    if im.mode != "P":
        # 兼容误存成 RGB 的 rivers.bmp: 取最近邻调色板索引不方便, 直接报错更安全
        arr = np.asarray(im.convert("RGB")).astype(np.int64)
        # 用调色板反查: 这里退化为按蓝色阈值判定
        raise ValueError("rivers.bmp 不是 8-bit 索引图 (mode=%s)" % im.mode)
    return np.asarray(im).astype(np.uint8)


def _label(mask: np.ndarray):
    if _ndi is not None:
        lab, n = _ndi.label(mask, structure=_STRUCT)
        return lab.astype(np.int32), int(n)
    # BFS 回退
    h, w = mask.shape
    lab = np.zeros((h, w), np.int32)
    n = 0
    ys, xs = np.nonzero(mask)
    for sy, sx in zip(ys.tolist(), xs.tolist()):
        if lab[sy, sx]:
            continue
        n += 1
        dq = collections.deque([(sy, sx)])
        lab[sy, sx] = n
        while dq:
            y, x = dq.popleft()
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and lab[ny, nx] == 0:
                    lab[ny, nx] = n
                    dq.append((ny, nx))
    return lab, n


def detect(pid_map: np.ndarray, definition, rivers: np.ndarray):
    """检测河流连通分量，并保留逐像素的水域端点/陆岸接触信息。

    ``lands`` 仅代表整条连通河网碰到过的陆地集合，不可直接用于某条运河的
    ``required_provinces``：同一分量可能包含支流或汇流。应使用 ``trace_route``
    在指定 From/To 水域之间提取主路径，再统计这条路径接触到的两岸省份。
    """
    if pid_map.shape != rivers.shape:
        raise ValueError("provinces.bmp 与 rivers.bmp 尺寸不一致: %r != %r" %
                         (pid_map.shape, rivers.shape))
    recs = definition.recs
    water = {int(p) for p, r in recs.items() if r[4] in _WATER_TYPES}
    land = {int(p) for p, r in recs.items() if r[4] == "land"}
    rmask = np.isin(rivers, RIVER_IDX)
    if not rmask.any():
        return []
    lab, n = _label(rmask)
    h, w = rmask.shape

    comp_w = collections.defaultdict(set)
    comp_l = collections.defaultdict(set)
    water_contacts = collections.defaultdict(lambda: collections.defaultdict(set))
    bank_by_pixel = collections.defaultdict(lambda: collections.defaultdict(set))
    # Each tuple is (here-y, here-x, neighbor-y, neighbor-x, flat-index offsets).
    # No np.roll: image edges must not wrap around.
    neighbors = (
        (slice(1, None), slice(None), slice(None, -1), slice(None), 1, 0),
        (slice(None, -1), slice(None), slice(1, None), slice(None), 0, 0),
        (slice(None), slice(1, None), slice(None), slice(None, -1), 0, 1),
        (slice(None), slice(None, -1), slice(None), slice(1, None), 0, 0),
    )
    for hy, hx, ny, nx, oy, ox in neighbors:
        here_mask = rmask[hy, hx]
        sel = here_mask & ~rmask[ny, nx]
        if not sel.any():
            continue
        local_y, local_x = np.nonzero(sel)
        here = lab[hy, hx][sel]
        there = pid_map[ny, nx][sel]
        pixel_ids = (local_y.astype(np.int64) + oy) * w + local_x.astype(np.int64) + ox
        for c, p, px in zip(here.tolist(), there.tolist(), pixel_ids.tolist()):
            if c <= 0 or p <= 0:
                continue
            p = int(p)
            if p in water:
                comp_w[c].add(p)
                water_contacts[c][p].add(int(px))
            elif p in land:
                comp_l[c].add(p)
                bank_by_pixel[c][int(px)].add(p)

    # Group river pixels by component without scanning the full bitmap once per component.
    river_y, river_x = np.nonzero(rmask)
    river_comp = lab[river_y, river_x]
    flat_pixels = river_y.astype(np.int64) * w + river_x.astype(np.int64)
    order = np.argsort(river_comp, kind="stable")
    comp_sorted = river_comp[order]
    pix_sorted = flat_pixels[order]
    unique_comps, comp_counts = np.unique(comp_sorted, return_counts=True)
    offsets = np.concatenate(([0], np.cumsum(comp_counts)))
    pixels_by_comp = {
        int(c): pix_sorted[offsets[i]:offsets[i + 1]].astype(np.int32)
        for i, c in enumerate(unique_comps.tolist())
    }

    out = []
    for c, ws in comp_w.items():
        if len(ws) < 2:
            continue
        out.append(dict(
            comp=int(c), waters=sorted(ws), lands=sorted(comp_l.get(c, ())),
            river_px=int(comp_counts[np.searchsorted(unique_comps, c)])
                if c in pixels_by_comp else 0,
            pixels=pixels_by_comp.get(int(c), np.empty(0, dtype=np.int32)),
            water_contacts={int(p): tuple(sorted(v)) for p, v in water_contacts[c].items()},
            bank_by_pixel={int(px): tuple(sorted(v)) for px, v in bank_by_pixel[c].items()},
            map_shape=(h, w),
        ))
    out.sort(key=lambda d: -d["river_px"])
    return out


def trace_route(canal, from_ids, to_ids):
    """追踪一条指定 From/To 之间的河道路径，只返回路径本身触及的两岸省份。

    河流图用四邻接像素图构成。若最短路径不唯一（例如分叉/环路导致多条同长
    路径），返回 ``None``，要求调用方保留原规则并将问题写入审计报告，不猜支流。
    返回 dict(route_pixels, lands, distance) 或 (None, reason)。
    """
    required_fields = ("pixels", "water_contacts", "bank_by_pixel", "map_shape")
    if any(field not in canal for field in required_fields):
        return None, "missing pixel-route metadata; safe to leave this rule unchanged"
    h, w = map(int, canal["map_shape"])
    pixels = set(map(int, canal.get("pixels", ())))
    if not pixels:
        return None, "river component has no pixels"
    contacts = canal.get("water_contacts", {})
    starts = set()
    goals = set()
    for pid in set(map(int, from_ids)):
        starts.update(map(int, contacts.get(pid, ())))
    for pid in set(map(int, to_ids)):
        goals.update(map(int, contacts.get(pid, ())))
    starts.intersection_update(pixels)
    goals.intersection_update(pixels)
    if not starts or not goals:
        return None, "cannot locate From/To water contacts on this river component"

    # Multi-source BFS gives the shortest route between any valid endpoint contacts.
    # Count shortest paths (capped at 2); any tie is deliberately treated as ambiguous.
    dist = {}
    ways = {}
    prev = {}
    queue = collections.deque()
    for px in sorted(starts):
        dist[px] = 0
        ways[px] = 1
        prev[px] = None
        queue.append(px)
    total = h * w
    while queue:
        px = queue.popleft()
        d = dist[px]
        x = px % w
        neighbors_px = []
        if px - w >= 0:
            neighbors_px.append(px - w)
        if px + w < total:
            neighbors_px.append(px + w)
        if x > 0:
            neighbors_px.append(px - 1)
        if x + 1 < w:
            neighbors_px.append(px + 1)
        for nxt in neighbors_px:
            if nxt not in pixels:
                continue
            nd = d + 1
            if nxt not in dist:
                dist[nxt] = nd
                ways[nxt] = ways[px]
                prev[nxt] = px
                queue.append(nxt)
            elif dist[nxt] == nd:
                ways[nxt] = min(2, ways[nxt] + ways[px])

    reachable_goals = [px for px in goals if px in dist]
    if not reachable_goals:
        return None, "From and To contacts are disconnected inside the river component"
    best_distance = min(dist[px] for px in reachable_goals)
    best_goals = [px for px in reachable_goals if dist[px] == best_distance]
    path_count = min(2, sum(ways[px] for px in best_goals))
    if path_count != 1:
        return None, "ambiguous shortest river route (branch/loop or multiple endpoint contacts)"

    cursor = best_goals[0]
    route = []
    while cursor is not None:
        route.append(cursor)
        cursor = prev[cursor]
    route.reverse()

    banks = set()
    bank_by_pixel = canal["bank_by_pixel"]
    for px in route:
        banks.update(map(int, bank_by_pixel.get(px, ())))
    return {"route_pixels": route, "lands": sorted(banks), "distance": best_distance}, "unique From/To river route"


def index(canals):
    """把 detect() 的结果转成快速查询表。"""
    by_pair = collections.defaultdict(list)
    water2comp = collections.defaultdict(set)
    land2comp = collections.defaultdict(set)
    for ca in canals:
        c = ca["comp"]
        w = ca["waters"]
        for a in w:
            water2comp[a].add(c)
            for b in w:
                if a < b:
                    by_pair[frozenset((a, b))].append(c)
        for p in ca["lands"]:
            land2comp[p].add(c)
    return dict(by_pair=dict(by_pair), water2comp=water2comp, land2comp=land2comp,
                comp={ca["comp"]: ca for ca in canals})


def find(row_from, row_to, canals_idx, through=None):
    """找到一条指定端点之间的河道路径，并将返回记录裁剪到该路径。

    生产检测记录带有像素路由元数据时，Through 只会匹配 From->To 路线上的岸省份，
    不会因为它碰到同一连通分量的支流就把支流当成主通道。旧格式测试/调用记录仍兼容。
    """
    if not canals_idx:
        return None
    ids = canals_idx["by_pair"].get(frozenset((row_from, row_to)), ())
    if isinstance(ids, int):  # 兼容旧版 index() 生成的索引
        ids = (ids,)
    source_candidates = [canals_idx["comp"][c] for c in ids if c in canals_idx["comp"]]
    if not source_candidates:
        return None

    candidates = []
    for ca in source_candidates:
        if "water_contacts" not in ca:
            # Compatibility for older external callers that provide only component summaries.
            candidates.append((ca, set(map(int, ca.get("lands", ())))))
            continue
        route, _note = trace_route(ca, {int(row_from)}, {int(row_to)})
        if route is None:
            continue
        # Downstream repair code consumes waters/lands. Return those fields scoped to this
        # route so it cannot select a split child that only touches an unrelated tributary.
        route_view = dict(ca)
        route_view["waters"] = sorted({int(row_from), int(row_to)})
        route_view["lands"] = list(map(int, route["lands"]))
        route_view["route_pixels"] = list(map(int, route["route_pixels"]))
        candidates.append((route_view, set(map(int, route["lands"]))))

    if through is not None:
        through_ids = {through} if isinstance(through, int) else set(map(int, through))
        matched = [view for view, banks in candidates if through_ids.intersection(banks)]
        return matched[0] if len(matched) == 1 else None
    return candidates[0][0] if len(candidates) == 1 else None
