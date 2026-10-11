# -*- coding: utf-8 -*-
"""地图完整性: 战略区/州归属校正 + 铁路修复 + 海峡/运河(adjacencies)修复。

所有函数都尽量"就地编辑文本并保留原格式", 输入输出都是文本/字节, 便于接入主流程。
"""
from __future__ import annotations

import collections
import os
import re

import numpy as np
from PIL import Image

from .core import PROV_RE, Definition, rgb_key
from . import canals as _canals

Image.MAX_IMAGE_PIXELS = None


# --------------------------------------------------------------------------- #
# 基础: 从图里取"省份ID地图"和邻接关系
# --------------------------------------------------------------------------- #
def load_pid_map(bmp_path: str, definition: Definition) -> np.ndarray:
    im = Image.open(bmp_path).convert("RGB")
    arr = np.asarray(im).astype(np.int64)
    pc = (arr[:, :, 0] << 16) | (arr[:, :, 1] << 8) | arr[:, :, 2]
    lut = np.zeros(1 << 24, dtype=np.int32)
    for pid, rec in definition.recs.items():
        lut[rgb_key(int(rec[1]), int(rec[2]), int(rec[3]))] = pid
    return lut[pc]


def build_adjacency(pid: np.ndarray):
    """返回 (pairs_set, neigh_dict)。4-连通。"""
    def enc(a, b):
        m = a != b
        x = a[m].astype(np.int64)
        y = b[m].astype(np.int64)
        return np.minimum(x, y) * 1000000 + np.maximum(x, y)
    k = np.unique(np.concatenate([enc(pid[:, 1:], pid[:, :-1]),
                                  enc(pid[1:, :], pid[:-1, :])]))
    pairs = set(int(v) for v in k.tolist())
    nb: dict = collections.defaultdict(set)
    for p in pairs:
        u, v = divmod(p, 1000000)
        nb[u].add(v)
        nb[v].add(u)
    return pairs, nb


def add_row_links(nb, text: str):
    """把 adjacencies.csv 里的 From-To 连接也当作"相邻",
    因为铁路可以跨过海峡/运河(它们靠 adjacency 而不是像素相邻连接)。"""
    n = 0
    for ln in text.split("\n"):
        c = ln.rstrip("\r").split(";")
        if len(c) < 4 or not c[0].strip().lstrip("-").isdigit():
            continue
        try:
            f = int(c[0]); t = int(c[1])
        except ValueError:
            continue
        if f > 0 and t > 0:
            nb[f].add(t)
            nb[t].add(f)
            n += 1
    return n


def bfs_path(nb, a, b, max_hops=4, allow=None):
    if a == b:
        return [a]
    if b in nb.get(a, ()):
        return [a, b]
    dq = collections.deque([[a]])
    seen = {a}
    while dq:
        p = dq.popleft()
        if len(p) - 1 >= max_hops:
            continue
        for n in nb.get(p[-1], ()):
            if n == b:
                return p + [b]
            if allow is not None and n not in allow:
                continue
            if n not in seen:
                seen.add(n)
                dq.append(p + [n])
    return None


# --------------------------------------------------------------------------- #
# provinces 块读写 (保留单行/多行排版)
# --------------------------------------------------------------------------- #
def _split_block(inner: str):
    """返回 (去掉注释后的 id 数字列表, 注释行列表)。"""
    comments = re.findall(r"#[^\n]*", inner)
    clean = re.sub(r"#[^\n]*", "", inner)
    ids = [int(t) for t in clean.split() if t.isdigit()]
    return ids, comments


def parse_provinces(text: str):
    m = PROV_RE.search(text)
    if not m:
        return None
    ids, _ = _split_block(m.group(2))
    return ids


def rewrite_provinces(text: str, ids: list) -> str:
    m = PROV_RE.search(text)
    if not m:
        return text
    inner = m.group(2)
    old_ids, comments = _split_block(inner)
    strs = [str(i) for i in ids]
    if "\n" in inner:
        nl = "\r\n" if "\r\n" in inner else "\n"
        toks = inner.split()
        ind = "\t\t"
        if toks:
            i0 = inner.find(toks[0])
            lead = inner[:i0]
            if nl in lead:
                ind = lead.split(nl)[-1] or ind
        body = "".join(ind + t + nl for t in strs)
        for c in comments:
            body += ind + c.strip() + nl
        newinner = nl + body + ind
    else:
        toks = inner.split()
        lead = inner[:inner.find(toks[0])] if toks else " "
        tail = inner[len(inner.rstrip()):]
        extra = ("  " + " ".join(c.strip() for c in comments)) if comments else ""
        newinner = lead + " ".join(strs) + extra + tail
    return text[:m.start(2)] + newinner + text[m.end(2):]


# --------------------------------------------------------------------------- #
# 战略区 / 州 归属唯一化
# --------------------------------------------------------------------------- #
def _collect(index_files, texts):
    """返回 id -> [files...]"""
    memb = collections.defaultdict(list)
    for fp in index_files:
        ids = parse_provinces(texts[fp])
        if not ids:
            continue
        for i in set(ids):
            memb[i].append(fp)
    return memb


def plan_membership(files, texts, nb, valid_ids):
    """计算需要从哪些文件里移除哪些 id。返回 {fp: set(ids_to_remove)}, 以及报告。"""
    memb = _collect(files, texts)
    remove = {fp: set() for fp in files}
    dup = {i: fs for i, fs in memb.items() if len(fs) > 1}
    report = []
    # 先建立"唯一归属"表
    owner = {i: fs[0] for i, fs in memb.items() if len(fs) == 1}
    for i in sorted(dup):
        fs = dup[i]
        # 用邻居投票决定归属
        score = collections.Counter()
        for n in nb.get(i, ()):
            o = owner.get(n)
            if o is not None:
                score[o] += 1
        if score:
            best = max(score.items(), key=lambda kv: (kv[1], kv[0] not in fs))[0]
        else:
            # 没有邻居信息: 保留在"文件里邻居最多"的地方, 否则保留第一个
            best = fs[0]
        if best not in fs:
            best = fs[0]
        owner[i] = best
        for fp in fs:
            if fp != best:
                remove[fp].add(i)
        report.append((i, fs, best))
    return remove, report, memb


def apply_membership(files, texts, remove):
    changed = {}
    for fp in files:
        if not remove.get(fp):
            continue
        ids = parse_provinces(texts[fp])
        newids = [i for i in ids if i not in remove[fp]]
        if len(newids) != len(ids):
            changed[fp] = rewrite_provinces(texts[fp], newids)
    return changed


# --------------------------------------------------------------------------- #
# 铁路修复
# --------------------------------------------------------------------------- #
def parse_railways(text: str):
    """返回 [(level, count, [ids])] 以及原行的行尾/结构信息(按行保留)。"""
    rows = []
    for ln in text.split("\n"):
        raw = ln.rstrip("\r")
        tok = raw.split()
        if len(tok) < 2:
            rows.append((None, raw))
            continue
        try:
            level = int(tok[0]); count = int(tok[1])
        except ValueError:
            rows.append((None, raw))
            continue
        ids = [int(x) for x in tok[2:] if x.isdigit()]
        rows.append(((level, count, ids), raw))
    return rows


def fix_railways(text: str, nb, max_hops=4, land=None):
    """重连断开的铁路。land 给出可插入的省份集合(只允许陆地地块作为中间点)。"""
    rows = parse_railways(text)
    nl = "\r\n" if "\r\n" in text else "\n"
    out = []
    changed = 0
    for item, raw in rows:
        if item is None:
            out.append(raw)
            continue
        level, count, ids = item
        if len(ids) < 2:
            out.append("%d %d %s" % (level, count, " ".join(map(str, ids))))
            continue
        newids = [ids[0]]
        for b in ids[1:]:
            a = newids[-1]
            if b in nb.get(a, ()):
                newids.append(b)
            else:
                p = bfs_path(nb, a, b, max_hops, allow=land)
                if p and len(p) > 1:
                    newids.extend(p[1:])
                    changed += 1
                else:
                    newids.append(b)
        out.append("%d %d %s" % (level, len(newids), " ".join(map(str, newids))))
    return nl.join(out), changed


# --------------------------------------------------------------------------- #
# adjacencies.csv / adjacency_rules.txt
# --------------------------------------------------------------------------- #
def _cand_ids(x, children):
    return [x] + list(children.get(x, ()))


def _canal_for(f, t, th, canals_idx, children):
    """定位运河路线，并保留实际匹配到的 From/To 子省，避免组件内支流干扰。"""
    if not canals_idx:
        return None
    through_ids = set(_cand_ids(th, children))
    matched = []
    endpoint_only = []
    for a in _cand_ids(f, children):
        for b in _cand_ids(t, children):
            if a == b:
                continue
            ca = _canals.find(a, b, canals_idx, through=through_ids)
            if ca is not None:
                view = dict(ca)
                view["_selected_from"] = int(a)
                view["_selected_to"] = int(b)
                score = int(a == f) + int(b == t)
                matched.append((score, view))
                continue
            # Through may be an old locator that no longer touches the river after splitting.
            # Endpoint fallback is allowed only if this exact pair has one unique pixel route.
            ca = _canals.find(a, b, canals_idx)
            if ca is not None:
                view = dict(ca)
                view["_selected_from"] = int(a)
                view["_selected_to"] = int(b)
                score = int(a == f) + int(b == t)
                endpoint_only.append((score, view))

    choices = matched if matched else endpoint_only
    if not choices:
        return None
    best_score = max(score for score, _view in choices)
    best = [view for score, view in choices if score == best_score]
    # Multiple equally plausible endpoint pairs are not safe to guess between.
    unique = {(view.get("comp"), view.get("_selected_from"), view.get("_selected_to")): view
              for view in best}
    return next(iter(unique.values())) if len(unique) == 1 else None


def _pick(side, cand, children):
    """在 [side]+子地块 中选仍属于集合 cand 的那个; 找不到保留原值。"""
    if side in cand:
        return side
    for x in children.get(side, ()):
        if x in cand:
            return x
    return side


def fix_adjacencies(text: str, nb, children, canals_idx=None):
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.split("\n")
    out = []
    changed = 0
    unfixed = []
    fixed_rows = []
    canal_rows = 0
    canal_changed = 0
    for idx, ln in enumerate(lines):
        raw = ln.rstrip("\r")
        c = raw.split(";")
        if idx == 0 or len(c) < 4 or not c[0].strip().lstrip("-").isdigit():
            out.append(raw)
            continue
        try:
            f = int(c[0]); t = int(c[1]); th = int(c[3])
        except ValueError:
            out.append(raw)
            continue
        if th <= 0:
            out.append(raw)
            continue
        if (len(c) >= 10 and not c[8].strip()
                and c[9].strip().lower() == "auto_canal"):
            out.append(raw)  # complete_canals removes stale rows from the old heuristic.
            continue

        # Only explicitly declared CANAL rows get river-assisted repair. rivers.bmp
        # is a visual river map, not proof that an arbitrary river is navigable.
        rule_name = c[8].strip() if len(c) > 8 else ""
        is_named_canal = "canal" in rule_name.lower()
        ca = _canal_for(f, t, th, canals_idx, children) if is_named_canal else None
        if ca is not None:
            canal_rows += 1
            waters = set(ca["waters"])
            lands = set(ca["lands"])
            nf = ca.get("_selected_from", _pick(f, waters, children))
            nt = ca.get("_selected_to", _pick(t, waters, children))
            nth = _pick(th, lands, children)
            if (nf, nt, nth) != (f, t, th) and nf != nt and nf > 0 and nt > 0:
                c[0] = str(nf); c[1] = str(nt); c[3] = str(nth)
                out.append(";".join(c))
                changed += 1
                canal_changed += 1
                fixed_rows.append((f, t, th, (nf, nt, nth)))
            else:
                out.append(raw)
            continue
        if is_named_canal:
            # Do not fall through to strait logic: canal Through need not border both ends.
            out.append(raw)
            if any(pid in children for pid in (f, t, th)):
                unfixed.append((f, t, th, c[2]))
            continue

        fset = [f] + list(children.get(f, []))
        tset = [t] + list(children.get(t, []))
        thset = [th] + list(children.get(th, []))
        need = not (th in nb.get(f, ()) and th in nb.get(t, ()))
        if not need:
            out.append(raw)
            continue
        best = None
        best_score = -1
        for thp in thset:
            for fp in fset:
                if thp not in nb.get(fp, ()):
                    continue
                for tp in tset:
                    if thp in nb.get(tp, ()):
                        score = (1 if fp == f else 0) + (1 if tp == t else 0) + (1 if thp == th else 0)
                        if score > best_score:
                            best_score = score
                            best = (fp, tp, thp)
        if best and best != (f, t, th):
            c[0] = str(best[0]); c[1] = str(best[1]); c[3] = str(best[2])
            out.append(";".join(c))
            changed += 1
            fixed_rows.append((f, t, th, best))
        else:
            out.append(raw)
            unfixed.append((f, t, th, c[2]))
    fix_adjacencies.last_canal_rows = canal_rows
    fix_adjacencies.last_canal_changed = canal_changed
    return nl.join(out), changed, fixed_rows, unfixed


def audit(mod_dir: str, progress=None):
    """检查一个 mod 的地图完整性, 返回统计字典。"""
    from .core import read_definition, _read_text
    def log(m):
        if progress:
            progress(m)
    mdir = os.path.join(mod_dir, "map")
    log("读取 definition.csv ...")
    definition = read_definition(os.path.join(mdir, "definition.csv"))
    log("读取 provinces.bmp 并建立邻接 ...")
    pid = load_pid_map(os.path.join(mdir, "provinces.bmp"), definition)
    pairs, nb = build_adjacency(pid)
    valid = set(definition.recs)

    def scan(sub):
        d = os.path.join(mod_dir, sub)
        files = []
        if os.path.isdir(d):
            files = sorted(os.path.join(d, f) for f in os.listdir(d)
                           if f.lower().endswith(".txt"))
        texts = {fp: _read_text(fp) for fp in files}
        memb = collections.defaultdict(list)
        for fp in files:
            ids = parse_provinces(texts[fp]) or []
            for i in set(ids):
                memb[i].append(fp)
        dup = {i: fs for i, fs in memb.items() if len(fs) > 1}
        invalid = sorted(i for i in memb if i not in valid)
        return files, texts, memb, dup, invalid

    log("检查 strategic regions ...")
    srf, srt, srm, srdup, srinv = scan(os.path.join("map", "strategicregions"))
    log("检查 states ...")
    stf, stt, stm, stdup, stinv = scan(os.path.join("history", "states"))

    log("检查 railways ...")
    rt = _read_text(os.path.join(mdir, "railways.txt"))
    at0 = _read_text(os.path.join(mdir, "adjacencies.csv"))
    nb_all = collections.defaultdict(set)
    for k, v in nb.items():
        nb_all[k] = set(v)
    add_row_links(nb_all, at0)
    rrows = parse_railways(rt)
    rbroken = rlines = rbad = 0
    for item, raw in rrows:
        if item is None:
            continue
        lvl, cnt, ids = item
        rlines += 1
        b = [(a, c) for a, c in zip(ids, ids[1:]) if c not in nb_all.get(a, ())]
        if b:
            rbroken += 1
            rbad += len(b)

    log("检查 adjacencies ...")
    # 运河行必须显式声明 CANAL 规则；Through 不要求与两端像素相邻
    canals_idx = None
    canal_list = []
    canal_count = 0
    canal_missing = 0
    rules_path = os.path.join(mdir, "adjacency_rules.txt")
    rules_text = _read_text(rules_path) if os.path.isfile(rules_path) else ""
    rivers_path = os.path.join(mdir, "rivers.bmp")
    if os.path.isfile(rivers_path):
        try:
            rivers = _canals.load_rivers(rivers_path)
            canal_list = _canals.detect(pid, definition, rivers)
            canals_idx = _canals.index(canal_list)
            canal_count = len(canal_list)
        except Exception as _e:
            log("运河识别跳过: %s" % _e)
    at = at0
    alines = at.split("\n")
    abad = 0
    canal_rows = 0
    abadrows = []
    pairs = set()
    for i, ln in enumerate(alines):
        if i == 0:
            continue
        c = ln.rstrip("\r").split(";")
        if len(c) < 4 or not c[0].strip().lstrip("-").isdigit():
            continue
        try:
            f = int(c[0]); t = int(c[1]); th = int(c[3])
        except ValueError:
            continue
        if th <= 0:
            continue
        if len(c) >= 10 and not c[8].strip() and c[9].strip().lower() == "auto_canal":
            continue  # Old heuristic output is not a real, rule-bound adjacency.
        pairs.add(frozenset((f, t)))
        is_named_canal = len(c) > 8 and "canal" in c[8].lower()
        if is_named_canal:
            canal_rows += 1
            continue  # Canal Through is a gateway; it need not border both ends in pixels.
        if not (th in nb.get(f, ()) and th in nb.get(t, ())):
            abad += 1
            if len(abadrows) < 20:
                abadrows.append((f, t, th, c[2]))
    if canals_idx:
        sea_ids = {p for p, rec in definition.recs.items() if rec[4] == "sea"}
        _at_safe, canal_missing, _safe_rows, _removed_legacy = complete_canals(
            at, canal_list, nb, sea_ids, rules_text=rules_text)

    _rules_preview, _ = fix_adjacency_rules(
        rules_text, nb, {}, adjacency_text=at, canals_list=canal_list)
    _bank_report = getattr(fix_adjacency_rules, "last_report", [])
    _bank_updated = sum(1 for r in _bank_report if r.get("status") == "UPDATED")
    _bank_checked = sum(1 for r in _bank_report if r.get("status") in ("OK", "UPDATED"))
    _bank_unresolved = sum(1 for r in _bank_report if r.get("status") not in ("OK", "UPDATED"))
    _bank_removed = getattr(fix_adjacency_rules, "last_removed", 0)
    _bank_added = getattr(fix_adjacency_rules, "last_added", 0)

    log("检查地块连通性 ...")
    disc, disc_frag = count_disconnected(pid)

    return dict(
        provinces=len(definition.recs),
        adjacency_pairs=len(pairs),
        crossings=_count_crossings(pid),
        disconnected=disc, disconnect_fragments=disc_frag,
        sr_files=len(srf), sr_dup=len(srdup), sr_invalid=len(srinv),
        state_files=len(stf), state_dup=len(stdup), state_invalid=len(stinv),
        railway_lines=rlines, railway_broken_lines=rbroken, railway_broken_pairs=rbad,
        adjacency_bad=abad, adjacency_bad_rows=abadrows,
        canals=canal_count, canal_rows=canal_rows, canal_missing=canal_missing,
        canal_rules_checked=_bank_checked, canal_rules_needing_update=_bank_updated,
        canal_rules_unresolved=_bank_unresolved,
        canal_required_provinces_to_remove=_bank_removed,
        canal_bank_provinces_to_add=_bank_added,
        sr_dup_items=list(srdup.items())[:20],
        state_dup_items=list(stdup.items())[:20],
    )


def _count_crossings(pid):
    a = pid[:-1, :-1]; b = pid[:-1, 1:]; c = pid[1:, :-1]; d = pid[1:, 1:]
    mac = (a == d) & (b == c) & (a != b)
    four = (a != b) & (a != c) & (a != d) & (b != c) & (b != d) & (c != d)
    return int((mac | four).sum())


def _bad_block(a, b, c, d):
    """2x2 是否为"十字交叉"(对角线歧义): 检查棋盘格 或 四个省份相遇。"""
    if a == d and b == c and a != b:
        return True
    if a != b and a != c and a != d and b != c and b != d and c != d:
        return True
    return False


def _nb_bad(pid, y, x, H, W):
    n = 0
    for yy in (y - 1, y):
        for xx in (x - 1, x):
            if 0 <= yy < H - 1 and 0 <= xx < W - 1:
                if _bad_block(pid[yy, xx], pid[yy, xx + 1], pid[yy + 1, xx], pid[yy + 1, xx + 1]):
                    n += 1
    return n


def _local_connected(pid, cy, cx, old, H, W, r=5):
    """把 (cy,cx) 变成别的省份后, old 省份在该点周围的像素是否仍连通。
    要求窗口内所有 old 像素彼此连通(偏保守: 宁可拒绝翻转也不制造孤立像素)。"""
    y0 = max(0, cy - r); y1 = min(H, cy + r + 1)
    x0 = max(0, cx - r); x1 = min(W, cx + r + 1)
    win = pid[y0:y1, x0:x1]
    oy, ox = np.nonzero(win == old)
    if len(oy) == 0:
        return True
    hh, ww = win.shape
    start = (int(oy[0]), int(ox[0]))
    seen = {start}
    dq = collections.deque([start])
    while dq:
        a0, b0 = dq.popleft()
        for da, db in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            na, nb = a0 + da, b0 + db
            if 0 <= na < hh and 0 <= nb < ww and win[na, nb] == old and (na, nb) not in seen:
                seen.add((na, nb))
                dq.append((na, nb))
    return len(seen) == len(oy)


def remove_crossings(pid: np.ndarray, max_iter: int = 15, progress=None):
    """消除地块边界的"十字交叉"(对角线歧义)。
    返回 (新pid, 修正点数, 剩余点数)。每个交叉只改一个像素, 并保证不弄断原省份。
    """
    pid = pid.copy()
    H, W = pid.shape
    total = 0
    for it in range(max_iter):
        a = pid[:-1, :-1]; b = pid[:-1, 1:]; c = pid[1:, :-1]; d = pid[1:, 1:]
        bad = ((a == d) & (b == c) & (a != b)) | \
              ((a != b) & (a != c) & (a != d) & (b != c) & (b != d) & (c != d))
        n = int(bad.sum())
        if n == 0:
            break
        ys, xs = np.nonzero(bad)
        fixed_n = 0
        for y, x in zip(ys.tolist(), xs.tolist()):
            cells = [(y, x), (y, x + 1), (y + 1, x), (y + 1, x + 1)]
            vals = [int(pid[cy, cx]) for cy, cx in cells]
            done = False
            for ci in (3, 2, 1, 0):          # 优先改右下角
                cy, cx = cells[ci]
                old = vals[ci]
                for cj in range(4):
                    if cj == ci or vals[cj] == old:
                        continue
                    nv = vals[cj]
                    before = _nb_bad(pid, cy, cx, H, W)
                    pid[cy, cx] = nv
                    after = _nb_bad(pid, cy, cx, H, W)
                    if after < before and _local_connected(pid, cy, cx, old, H, W):
                        total += 1
                        fixed_n += 1
                        done = True
                    else:
                        pid[cy, cx] = old
                    if done:
                        break
                if done:
                    break
        if progress:
            progress("交叉边界: 第%d轮 处理 %d/%d" % (it + 1, fixed_n, n))
        if fixed_n == 0:
            break
    a = pid[:-1, :-1]; b = pid[:-1, 1:]; c = pid[1:, :-1]; d = pid[1:, 1:]
    left = int((((a == d) & (b == c) & (a != b)) |
                ((a != b) & (a != c) & (a != d) & (b != c) & (b != d) & (c != d))).sum())
    return pid, total, left


def _bad_near(pid, gys, gxs, H, W):
    seen = set()
    n = 0
    for y, x in zip(gys, gxs):
        for yy in (y - 1, y):
            for xx in (x - 1, x):
                if 0 <= yy < H - 1 and 0 <= xx < W - 1 and (yy, xx) not in seen:
                    seen.add((yy, xx))
                    if _bad_block(pid[yy, xx], pid[yy, xx + 1], pid[yy + 1, xx], pid[yy + 1, xx + 1]):
                        n += 1
    return n


def _label_bfs(sub):
    """在布尔 bbox 上做 4-连通标记。返回 (lab, n, main_label)。无 scipy 时的回退。"""
    h, w = sub.shape
    lab = np.zeros((h, w), np.int32)
    n = 0
    sizes = [0]
    ys, xs = np.nonzero(sub)
    for sy, sx in zip(ys.tolist(), xs.tolist()):
        if lab[sy, sx] != 0:
            continue
        n += 1
        dq = collections.deque([(sy, sx)])
        lab[sy, sx] = n
        cnt = 0
        while dq:
            y, x = dq.popleft()
            cnt += 1
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                ny, nx = y + dy, x + dx
                if 0 <= ny < h and 0 <= nx < w and sub[ny, nx] and lab[ny, nx] == 0:
                    lab[ny, nx] = n
                    dq.append((ny, nx))
        sizes.append(cnt)
    if n == 0:
        return lab, 0, 0
    return lab, n, int(np.argmax(sizes))


def coastal_mask(pid: np.ndarray, sea_ids) -> np.ndarray:
    """返回一个布尔图: 该像素是否与"海洋"地块相邻(即属于沿海地块的岸线)。"""
    if not sea_ids:
        return np.zeros(pid.shape, bool)
    sea = np.isin(pid, np.fromiter(sea_ids, dtype=np.int64))
    coast = np.zeros(pid.shape, bool)
    coast[:, 1:] |= sea[:, :-1]
    coast[:, :-1] |= sea[:, 1:]
    coast[1:, :] |= sea[:-1, :]
    coast[:-1, :] |= sea[1:, :]
    return coast


def fix_connectivity(pid: np.ndarray, progress=None, max_iter=4, island_max=1):
    """把"不大于 island_max 像素"的孤岛碎片并到邻居省份。
    更大的分块(如群岛)视为合法, 保留不动。返回 (新pid, 被修正地块数, 修正碎片数)。
    """
    try:
        import scipy.ndimage as _ndi
        STRUCT = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]])
    except Exception:
        _ndi = None
        STRUCT = None
    pid = pid.copy()
    H, W = pid.shape
    prov_fixed = 0
    frag_fixed = 0
    for it in range(max_iter):
        flat = pid.ravel()
        uniq, inv, counts = np.unique(flat, return_inverse=True, return_counts=True)
        order = np.argsort(inv, kind="stable")
        bounds = np.zeros(len(uniq) + 1, np.int64)
        np.cumsum(counts, out=bounds[1:])
        changed_this = 0
        for i, v in enumerate(uniq.tolist()):
            if v == 0 or counts[i] <= 1:
                continue
            idx = order[bounds[i]:bounds[i + 1]]
            ys = (idx // W)
            xs = (idx % W)
            y0, y1 = int(ys.min()), int(ys.max())
            x0, x1 = int(xs.min()), int(xs.max())
            sub = np.zeros((y1 - y0 + 1, x1 - x0 + 1), bool)
            sub[ys - y0, xs - x0] = True
            if _ndi is not None:
                lab, n = _ndi.label(sub, structure=STRUCT)
                if n <= 1:
                    continue
                sizes = np.bincount(lab.ravel())
                sizes[0] = 0
                main = int(np.argmax(sizes))
            else:
                lab, n, main = _label_bfs(sub)
                if n <= 1:
                    continue
                sizes = np.bincount(lab.ravel())
            fixed_prov = False
            for c in range(1, n + 1):
                if c == main:
                    continue
                if int(sizes[c]) > island_max:
                    continue
                sel = (lab == c)
                yy, xx = np.nonzero(sel)
                nbr = collections.Counter()
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny = yy + dy
                    nx = xx + dx
                    ok = (ny >= 0) & (ny < sub.shape[0]) & (nx >= 0) & (nx < sub.shape[1])
                    if not ok.any():
                        continue
                    gy = (ny[ok] + y0)
                    gx = (nx[ok] + x0)
                    vals = pid[gy, gx]
                    for val in vals.tolist():
                        if val != v and val != 0:
                            nbr[val] += 1
                if not nbr:
                    continue
                candidates = [t for t, _ in nbr.most_common()]
                gys = (yy + y0).tolist()
                gxs = (xx + x0).tolist()
                before = _bad_near(pid, gys, gxs, H, W)
                target = candidates[0]
                for t in candidates:
                    pid[yy + y0, xx + x0] = t
                    after = _bad_near(pid, gys, gxs, H, W)
                    if after <= before:
                        target = t
                        break
                    pid[yy + y0, xx + x0] = v
                pid[yy + y0, xx + x0] = target
                frag_fixed += 1
                changed_this += 1
                fixed_prov = True
            if fixed_prov:
                prov_fixed += 1
        if progress:
            progress("连通性: 第%d轮 修正地块 %d" % (it + 1, changed_this))
        if changed_this == 0:
            break
    return pid, prov_fixed, frag_fixed


def count_disconnected(pid: np.ndarray, island_max=1):
    """返回 (含<=island_max像素孤岛碎片的地块数, 这类碎片总数)。"""
    try:
        import scipy.ndimage as _ndi
        STRUCT = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]])
    except Exception:
        _ndi = None
        STRUCT = None
    H, W = pid.shape
    flat = pid.ravel()
    uniq, inv, counts = np.unique(flat, return_inverse=True, return_counts=True)
    order = np.argsort(inv, kind="stable")
    bounds = np.zeros(len(uniq) + 1, np.int64)
    np.cumsum(counts, out=bounds[1:])
    bad = extra = 0
    for i, v in enumerate(uniq.tolist()):
        if v == 0 or counts[i] <= 1:
            continue
        idx = order[bounds[i]:bounds[i + 1]]
        ys = (idx // W); xs = (idx % W)
        y0, y1 = int(ys.min()), int(ys.max())
        x0, x1 = int(xs.min()), int(xs.max())
        sub = np.zeros((y1 - y0 + 1, x1 - x0 + 1), bool)
        sub[ys - y0, xs - x0] = True
        if _ndi is not None:
            _l, n = _ndi.label(sub, structure=STRUCT)
            _sz = np.bincount(_l.ravel()) if n else np.zeros(1, dtype=np.int64)
        else:
            _l, n, _m = _label_bfs(sub)
            _sz = np.bincount(_l.ravel()) if n else np.zeros(1, dtype=np.int64)
        if n > 1:
            small = [int(_sz[c]) for c in range(1, n + 1) if 0 < _sz[c] <= island_max]
            if small:
                bad += 1
                extra += len(small)
    return bad, extra


def _rule_blocks(text: str):
    """Yield (start, end, block_text) for each top-level adjacency_rule block."""
    for m in re.finditer(r"(?im)^[ \t]*adjacency_rule\s*=", text):
        brace = text.find("{", m.end())
        if brace < 0:
            continue
        depth = 0
        quote = False
        escaped = False
        comment = False
        end = None
        for i in range(brace, len(text)):
            ch = text[i]
            if comment:
                if ch == "\n":
                    comment = False
                continue
            if quote:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    quote = False
                continue
            if ch == '"':
                quote = True
            elif ch == "#":
                comment = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end is not None:
            yield m.start(), end, text[m.start():end]


def _canal_rows_by_rule(adjacency_text: str):
    """Collect explicit CANAL adjacency rows by the rule-name column (index 8)."""
    rows = collections.defaultdict(list)
    if not adjacency_text:
        return rows
    for line_no, raw in enumerate(adjacency_text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        c = raw.split(";")
        if len(c) < 9 or not c[0].strip().lstrip("-").isdigit():
            continue
        name = c[8].strip()
        if "canal" not in name.lower():
            continue
        try:
            f, t = int(c[0]), int(c[1])
            th = int(c[3]) if len(c) > 3 else -1
        except (ValueError, IndexError):
            continue
        rows[name].append({"line": line_no, "from": f, "to": t, "through": th, "raw": raw})
    return rows


def _resolve_canal_route(row, required, canals_list, children):
    """Resolve the *route between endpoints*, not the entire connected river network."""
    f, t, th = row["from"], row["to"], row["through"]
    fids = {int(f), *map(int, children.get(int(f), ())) }
    tids = {int(t), *map(int, children.get(int(t), ())) }
    through_ids = {int(th), *map(int, children.get(int(th), ())) } if th > 0 else set()
    required_ids = {int(p) for p in required}

    candidates = []
    route_failures = []
    for ca in canals_list or ():
        ws = set(map(int, ca.get("waters", ())))
        if not ((ws & fids) and (ws & tids) and len((ws & fids) | (ws & tids)) >= 2):
            continue
        route, route_note = _canals.trace_route(ca, fids, tids)
        if route is None:
            route_failures.append(route_note)
            continue
        candidates.append((ca, route, route_note))

    if len(candidates) == 1:
        ca, route, route_note = candidates[0]
        return (ca, route, "matched by From/To; " + route_note), ""
    if not candidates:
        if route_failures:
            return None, "; ".join(sorted(set(route_failures)))
        return None, "no river component touches both From and To (including split children)"

    # When several separate components join the same water endpoints, disambiguate using
    # the route's own bank provinces, never lands on unrelated tributaries of the component.
    through_matches = [item for item in candidates
                       if through_ids.intersection(map(int, item[1].get("lands", ())))]
    if len(through_matches) == 1:
        ca, route, route_note = through_matches[0]
        return (ca, route, "disambiguated by Through on route; " + route_note), ""
    if len(through_matches) > 1:
        candidates = through_matches

    scored = [(len(required_ids.intersection(map(int, route.get("lands", ())))), ca, route, note)
              for ca, route, note in candidates]
    best_score = max((score for score, _ca, _route, _note in scored), default=0)
    best = [item for item in scored if item[0] == best_score and score_positive(item[0])]
    if len(best) == 1:
        _score, ca, route, note = best[0]
        return (ca, route, "disambiguated by current required_provinces overlap; " + note), ""
    return None, "ambiguous river components/routes for the same From/To pair"


def score_positive(value):
    return value > 0


def _rewrite_required_body(inner: str, ids: list[int]) -> str:
    """Rewrite only list values, preserving single/multiline style and indentation."""
    comments = re.findall(r"#[^\n]*", inner)
    if "\n" not in inner and "\r" not in inner:
        lead = re.match(r"\s*", inner).group(0)
        trail = re.search(r"\s*$", inner).group(0)
        body = " ".join(map(str, ids))
        if comments:
            body = (body + " " if body else "") + " ".join(comments)
        return lead + body + trail

    nl = "\r\n" if "\r\n" in inner else "\n"
    code = re.sub(r"#[^\n]*", "", inner)
    m = re.search(r"(?:\r?\n)([ \t]*)\d+", code)
    indent = m.group(1) if m else "    "
    close_m = re.search(r"(?:\r?\n)([ \t]*)$", inner)
    close_indent = close_m.group(1) if close_m else ""
    out = nl
    out += nl.join(indent + str(pid) for pid in ids)
    if comments:
        if ids:
            out += nl
        out += nl.join(indent + c for c in comments)
    out += nl + close_indent
    return out


def fix_adjacency_rules(text: str, nb=None, children=None, adjacency_text: str = "",
                        canals_list=None):
    """Rebuild each CANAL rule's required_provinces from actual river-bank pixels.

    1. The CANAL rule name must link to an explicit adjacencies.csv row.
    2. From/To contacts identify the component and trace the pixel path between the two waters.
    3. provinces.bmp is overlaid with rivers.bmp. Only land provinces touching the selected
       From-to-To pixel route count as banks; side tributaries in the same connected component
       are excluded.
    4. Required IDs outside that route are removed and measured route banks are added.
       Ambiguous routes/components are left unchanged and written to the audit report.

    ``nb`` is retained for compatibility with older callers; pixel contacts, not province
    graph proximity, determine bank membership.
    """
    children = children or {}
    canals_list = canals_list or []
    rows_by_rule = _canal_rows_by_rule(adjacency_text)
    blocks = list(_rule_blocks(text))
    known_rule_names = set()
    replacements = []
    report = []
    changed = 0

    for block_start, block_end, block in blocks:
        name_m = re.search(r'\bname\s*=\s*"([^"\n]+)"', block)
        if not name_m:
            continue
        name = name_m.group(1)
        if "canal" not in name.lower():
            continue
        name_key = name.casefold()
        known_rule_names.add(name_key)
        rule_rows = rows_by_rule.get(name, [])
        if not rule_rows:
            report.append({"rule": name, "from_to": "", "through": "", "components": "",
                           "component_pixels": 0, "route_pixels": 0, "component_banks": "", "off_route_banks": "",
                           "required_before": "", "banks_detected": "",
                           "removed": "", "added": "", "status": "MISSING_ADJACENCY_ROW",
                           "note": "No adjacency row points to this CANAL rule; left unchanged."})
            continue

        req_m = re.search(r"\brequired_provinces\s*=\s*\{([^}]*)\}", block, re.S)
        if not req_m:
            report.append({"rule": name, "from_to": ",".join(f"{r['from']}-{r['to']}" for r in rule_rows),
                           "through": ",".join(str(r["through"]) for r in rule_rows), "components": "",
                           "component_pixels": 0, "route_pixels": 0, "component_banks": "", "off_route_banks": "",
                           "required_before": "", "banks_detected": "",
                           "removed": "", "added": "", "status": "MISSING_REQUIRED_PROVINCES",
                           "note": "Rule has no required_provinces block; left unchanged."})
            continue

        req_inner = req_m.group(1)
        req_code = re.sub(r"#[^\n]*", "", req_inner)
        current = []
        seen = set()
        for token in re.findall(r"\b\d+\b", req_code):
            pid = int(token)
            if pid not in seen:
                current.append(pid)
                seen.add(pid)

        resolved = []
        unresolved_notes = []
        for row in rule_rows:
            resolved_route, note = _resolve_canal_route(row, current, canals_list, children)
            if resolved_route is None:
                unresolved_notes.append("line %s: %s" % (row["line"], note))
            else:
                ca, route, route_note = resolved_route
                resolved.append((row, ca, route, route_note))

        if unresolved_notes or not resolved:
            unresolved_text = "; ".join(unresolved_notes) or "No associated river component."
            route_error_terms = ("ambiguous shortest river route", "cannot locate From/To water contacts",
                                 "disconnected inside the river component", "missing pixel-route metadata",
                                 "river component has no pixels")
            unresolved_status = ("UNRESOLVED_ROUTE" if any(term in unresolved_text for term in route_error_terms)
                                 else "UNRESOLVED_COMPONENT")
            report.append({"rule": name, "from_to": ",".join(f"{r['from']}-{r['to']}" for r in rule_rows),
                           "through": ",".join(str(r["through"]) for r in rule_rows),
                           "components": ",".join(str(ca["comp"]) for _r, ca, _route, _n in resolved),
                           "component_pixels": sum(int(ca.get("river_px", 0)) for _r, ca, _route, _n in resolved),
                           "route_pixels": sum(len(route.get("route_pixels", ())) for _r, _ca, route, _n in resolved),
                           "component_banks": " ".join(map(str, sorted({p for _r, ca, _route, _n in resolved for p in ca.get("lands", ())}))),
                           "off_route_banks": "",
                           "required_before": " ".join(map(str, current)), "banks_detected": "",
                           "removed": "", "added": "", "status": unresolved_status,
                           "note": unresolved_text})
            continue

        # One rule can in principle be referenced by multiple adjacency rows; union all
        # resolved river-bank sets so the rule covers the full named route.
        banks = set()
        component_banks = set()
        for _row, ca, route, _note in resolved:
            banks.update(map(int, route.get("lands", ())))
            component_banks.update(map(int, ca.get("lands", ())))
        off_route_banks = component_banks - banks
        if len(banks) < 2:
            report.append({"rule": name, "from_to": ",".join(f"{r['from']}-{r['to']}" for r in rule_rows),
                           "through": ",".join(str(r["through"]) for r in rule_rows),
                           "components": ",".join(str(ca["comp"]) for _r, ca, _route, _n in resolved),
                           "component_pixels": sum(int(ca.get("river_px", 0)) for _r, ca, _route, _n in resolved),
                           "route_pixels": sum(len(route.get("route_pixels", ())) for _r, _ca, route, _n in resolved),
                           "component_banks": " ".join(map(str, sorted(component_banks))),
                           "off_route_banks": " ".join(map(str, sorted(off_route_banks))),
                           "required_before": " ".join(map(str, current)), "banks_detected": " ".join(map(str, sorted(banks))),
                           "removed": "", "added": "", "status": "INSUFFICIENT_BANK_PROVINCES",
                           "note": "Fewer than two land provinces touch the detected canal; left unchanged."})
            continue

        # Keep old order for IDs that still touch the river; append new banks sorted for
        # a stable diff. Do not add every child blindly: only IDs seen beside river pixels.
        new_ids = [pid for pid in current if pid in banks]
        new_seen = set(new_ids)
        new_ids.extend(pid for pid in sorted(banks) if pid not in new_seen)
        removed = [pid for pid in current if pid not in banks]
        added = sorted(banks - set(current))
        before = " ".join(map(str, current))
        after = " ".join(map(str, new_ids))
        old_body = req_m.group(1)
        if new_ids != current:
            inner_new = _rewrite_required_body(old_body, new_ids)
            new_block = block[:req_m.start(1)] + inner_new + block[req_m.end(1):]
            replacements.append((block_start, block_end, new_block))
            changed += 1
            status = "UPDATED"
        else:
            status = "OK"

        report.append({"rule": name, "from_to": ",".join(f"{r['from']}-{r['to']}" for r in rule_rows),
                       "through": ",".join(str(r["through"]) for r in rule_rows),
                       "components": ",".join(str(ca["comp"]) for _r, ca, _route, _n in resolved),
                       "component_pixels": sum(int(ca.get("river_px", 0)) for _r, ca, _route, _n in resolved),
                       "route_pixels": sum(len(route.get("route_pixels", ())) for _r, _ca, route, _n in resolved),
                       "component_banks": " ".join(map(str, sorted(component_banks))),
                       "off_route_banks": " ".join(map(str, sorted(off_route_banks))),
                       "required_before": before, "banks_detected": after,
                       "removed": " ".join(map(str, removed)), "added": " ".join(map(str, added)),
                       "status": status,
                       "note": "; ".join(sorted(set(n for _r, _ca, _route, n in resolved)))})

    # Also record orphan CANAL rows which have no matching rule block.
    for name, rows in rows_by_rule.items():
        if name.casefold() in known_rule_names:
            continue
        report.append({"rule": name, "from_to": ",".join(f"{r['from']}-{r['to']}" for r in rows),
                       "through": ",".join(str(r["through"]) for r in rows), "components": "",
                       "component_pixels": 0, "route_pixels": 0, "component_banks": "", "off_route_banks": "",
                           "required_before": "", "banks_detected": "",
                       "removed": "", "added": "", "status": "MISSING_RULE_DEFINITION",
                       "note": "CANAL adjacency row has no matching adjacency_rule block."})

    for a, b, new_block in reversed(replacements):
        text = text[:a] + new_block + text[b:]

    fix_adjacency_rules.last_report = report
    fix_adjacency_rules.last_changed = changed
    fix_adjacency_rules.last_removed = sum(len(r["removed"].split()) for r in report if r["removed"])
    fix_adjacency_rules.last_added = sum(len(r["added"].split()) for r in report if r["added"])
    return text, changed


# --------------------------------------------------------------------------- #
# 运河补全: 画了运河但没有 adjacencies 行的, 补一行(两端都记录)
# --------------------------------------------------------------------------- #
def _declared_canal_rules(text: str):
    """Read explicit CANAL rules; a river component alone is not enough to invent an adjacency."""
    rules = []
    if not text:
        return rules
    blocks = re.split(r"(?m)(?=^[ \t]*adjacency_rule\s*=)", text)
    for block in blocks:
        name_m = re.search(r'\bname\s*=\s*"([^"\n]+)"', block)
        if not name_m or "canal" not in name_m.group(1).lower():
            continue
        icon_m = re.search(r"\bicon\s*=\s*(\d+)", block)
        req_m = re.search(r"required_provinces\s*=\s*\{([^}]*)\}", block, re.S)
        if not icon_m or not req_m:
            continue
        required = {int(x) for x in re.findall(r"\d+", req_m.group(1))}
        rules.append({"name": name_m.group(1), "icon": int(icon_m.group(1)), "required": required})
    return rules


def complete_canals(text: str, canals_list, nb, sea_ids, only_sea=True,
                    rules_text: str = "", children=None):
    """Repair only provable missing canal rows; remove unbound rows from the old auto-guesser.

    rivers.bmp alone cannot define navigability. New rows require a unique explicit CANAL
    rule, exactly two sea endpoints, and one uniquely identifiable Through province.
    """
    nl = "\r\n" if "\r\n" in text else "\n"
    children = children or {}
    # Normalize line endings before joining; splitlines alone keeps stray CR characters.
    lines = [ln.rstrip("\r") for ln in text.split("\n")]
    clean_lines = []
    removed_legacy = 0
    for ln in lines:
        c = ln.split(";")
        if (len(c) >= 10 and c[0].strip().lstrip("-").isdigit()
                and c[1].strip().lstrip("-").isdigit()
                and c[2].strip().lower() == "sea" and not c[8].strip()
                and c[9].strip().lower() == "auto_canal"):
            removed_legacy += 1
            continue
        clean_lines.append(ln)
    lines = clean_lines

    pairs = set()
    existing_rules = set()
    term_idx = None
    for i, ln in enumerate(lines):
        c = ln.split(";")
        if len(c) >= 1 and c[0].strip() == "-1":
            term_idx = i
            continue
        if len(c) < 4 or not c[0].strip().lstrip("-").isdigit():
            continue
        try:
            f, t = int(c[0]), int(c[1])
        except ValueError:
            continue
        if f > 0 and t > 0:
            pairs.add(frozenset((f, t)))
        if len(c) > 8 and "canal" in c[8].lower():
            existing_rules.add(c[8].strip())

    rules = _declared_canal_rules(rules_text)
    matches = []
    for ca in canals_list:
        waters = ca["waters"]
        if len(waters) != 2 or (only_sea and not all(p in sea_ids for p in waters)):
            continue
        # The whole connected component may contain tributaries; infer missing links only
        # from a unique route between the two water endpoints and its own bank provinces.
        route, _route_note = _canals.trace_route(ca, {int(waters[0])}, {int(waters[1])})
        if route is None:
            continue
        lands = set(map(int, route["lands"]))
        candidates = []
        for rule in rules:
            icon_choices = _cand_ids(rule["icon"], children)
            if not any(p in lands for p in icon_choices):
                continue
            required_choices = {p for base in rule["required"] for p in _cand_ids(base, children)}
            # Through must be uniquely implied by the selected route, not any tributary.
            through_choices = lands.intersection(required_choices)
            if len(through_choices) == 1:
                candidates.append((rule, next(iter(through_choices))))
        if len(candidates) == 1:
            matches.append((ca, candidates[0][0], candidates[0][1]))

    by_rule = collections.defaultdict(list)
    for ca, rule, through in matches:
        by_rule[rule["name"]].append((ca, through))

    added = []
    for name, found in by_rule.items():
        if len(found) != 1 or name in existing_rules:
            continue
        ca, through = found[0]
        a, b = ca["waters"]
        if a == b or frozenset((a, b)) in pairs:
            continue
        # The rule name belongs in CSV column 9 (index 8), not the comment column.
        added.append("%d;%d;sea;%d;-1;-1;-1;-1;%s;auto_canal" % (a, b, through, name))
        pairs.add(frozenset((a, b)))

    if term_idx is not None:
        lines[term_idx:term_idx] = added
    else:
        lines.extend(added)
    result = nl.join(lines)
    if not added and not removed_legacy and result == text:
        result = text
    return result, len(added), added, removed_legacy
