# -*- coding: utf-8 -*-
"""省份/州/战略区/大洲 编辑引擎 (图层式)。

术语(沿用现有编辑器): 地块=province, 州=state, 战略区=strategic region, 大洲=continent。

支持:
  * 州归属编辑: 把地块加入/移出某个州 (只允许 land/lake)
  * 战略区归属编辑: 把地块加入/移出某个战略区
  * 大洲编辑: 改地块的大洲字段
  * 地形编辑: 改地块的地形字段
  * 合并/删除地块 (像素级)
  * 新建州: 指定 名称/人数/基建/工厂/船坞/建筑/资源, 并把选中地块放进去
  * 撤销/重做/重置; 导出 (provinces.bmp + definition.csv + states + strategicregions
    + railways/adjacencies/adjacency_rules 的 ID 重命名)

编辑只保存在内存, 导出时才写到输出目录。
"""
from __future__ import annotations

import collections
import os
import re

import numpy as np

from PIL import Image

from province_splitter.core import (read_definition, load_provinces_bmp, save_provinces_bmp,
                                    rgb_key, _read_text, _write_text)
from province_splitter import paths as _paths
from province_splitter.integrity import parse_provinces, rewrite_provinces

Image.MAX_IMAGE_PIXELS = None

STANDARD_TERRAINS = [
    "plains", "farmland", "forest", "dense_forest", "hills", "mountain", "urban",
    "desert", "marsh", "jungle", "swamp", "ocean", "lakes", "deep_ocean",
    "shallow_sea", "water_fjords", "water_shallow_sea", "water_deep_ocean", "unknown",
]


def _pk(a, b):
    a = int(a); b = int(b)
    return (a, b) if a <= b else (b, a)


def _centroid(pts):
    n = len(pts)
    if not n:
        return None
    return [int(round(sum(p[0] for p in pts) / float(n))), int(round(sum(p[1] for p in pts) / float(n)))]


def _chain_segments(pts, min_len=3, maxseg=16):
    """把公共边界像素点集串成若干段折线; 遇到断口就断开(保证不会画出横跨地图的乱线)。"""
    st = set((int(p[0]), int(p[1])) for p in pts)
    if len(st) < 2:
        return []
    remain = set(st)
    segs = []
    ring4 = ((1, 0), (-1, 0), (0, 1), (0, -1))
    ring8 = ring4 + ((1, 1), (1, -1), (-1, 1), (-1, -1))
    while remain:
        cur = min(remain, key=lambda q: (q[1], q[0]))
        remain.discard(cur)
        line = [cur]
        while remain:
            nxt = None
            for dx, dy in ring4:            # 先走正交邻域(贴边)
                q = (cur[0] + dx, cur[1] + dy)
                if q in remain:
                    nxt = q
                    break
            if nxt is None:
                for dx, dy in ring8:        # 再走对角(拐角)
                    q = (cur[0] + dx, cur[1] + dy)
                    if q in remain:
                        nxt = q
                        break
            if nxt is None:                 # 允许跨 2px 细缝, 但不做大跳变
                for dx in range(-2, 3):
                    for dy in range(-2, 3):
                        q = (cur[0] + dx, cur[1] + dy)
                        if q in remain:
                            nxt = q
                            break
                    if nxt:
                        break
            if nxt is None:
                break
            remain.discard(nxt)
            line.append(nxt)
            cur = nxt
        if len(line) >= min_len:
            if len(line) > maxseg:
                step = (len(line) - 1) / float(maxseg - 1)
                line = [line[int(round(k * step))] for k in range(maxseg)]
            segs.append([[int(q[0]), int(q[1])] for q in line])
    return segs

def _rresolve(mod, game, ref, *parts):
    rel = os.path.join(*parts)
    for root in (ref, mod, game):
        if root:
            p = os.path.join(root, rel)
            if os.path.exists(p):
                return p
    return os.path.join(mod, rel) if mod else ""


def _rlist(mod, game, ref, *parts, exts=(".txt",), merge=False):
    rel = os.path.join(*parts)
    exts = tuple(e.lower() for e in exts)

    def one(root):
        if not root:
            return []
        d = os.path.join(root, rel)
        if not os.path.isdir(d):
            return []
        return [(f, os.path.join(d, f)) for f in sorted(os.listdir(d)) if f.lower().endswith(exts)]

    seen = set(); out = []
    for root in (ref, mod):
        for name, fp in one(root):
            if name in seen:
                continue
            seen.add(name); out.append(fp)
    if (not out) or merge:
        for name, fp in one(game):
            if name in seen:
                continue
            seen.add(name); out.append(fp)
    return out


def _replace_block(text, key, inner):
    m = re.search(r"(\b" + re.escape(key) + r"\s*=\s*)\{", text)
    if not m:
        return text, False
    start = m.end() - 1
    depth = 0
    i = start
    while i < len(text):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    return text[:start] + "{\n" + inner + "}" + text[i + 1:], True


def _rewrite_state(text, data):
    nl = "\r\n" if "\r\n" in text else "\n"
    out = text
    # 标量
    if data.get("manpower") not in (None, ""):
        pat = re.compile(r"(?m)^([ \t]*)manpower\s*=\s*[^\r\n]*")
        out = pat.sub(lambda m: m.group(1) + "manpower=" + str(data["manpower"]), out, count=1) if pat.search(out) else out
    if data.get("category"):
        pat = re.compile(r"(?m)^([ \t]*)state_category\s*=\s*[^\r\n]*")
        out = pat.sub(lambda m: m.group(1) + "state_category=" + str(data["category"]), out, count=1) if pat.search(out) else out
    if data.get("owner") is not None:
        pat = re.compile(r"owner\s*=\s*[A-Za-z0-9_]+")
        if pat.search(out):
            out = pat.sub("owner = " + str(data["owner"]), out, count=1)
    # buildings 块
    if data.get("buildings") is not None:
        body = "".join("\t\t\t%s = %s\n" % (k, v) for k, v in sorted(data["buildings"].items()) if str(v) != "")
        out2, ok = _replace_block(out, "buildings", body)
        if ok:
            out = out2
    # resources 块
    if data.get("resources") is not None:
        if data["resources"]:
            body = "".join("\t\t\t%s = %s\n" % (k, v) for k, v in sorted(data["resources"].items()))
            out2, ok = _replace_block(out, "resources", body)
            if ok:
                out = out2
    return out


_SR_BODY = """strategic_region={{
\tid={id}
\tname="{name}"
\tprovinces={{
{prov}\t}}
\tweather={{
\t\tperiod={{
\t\t\tbetween={{ 0.0 29.5 }}
\t\t\ttemperature={{ 5.0 20.0 }}
\t\t\tno_phenomenon=1.500
\t\t\train_light=0.250
\t\t\train_heavy=0.100
\t\t\tsnow=0.000
\t\t\tblizzard=0.000
\t\t\tarctic_water=0.000
\t\t\tmud=0.000
\t\t\tsandstorm=0.000
\t\t\tmin_snow_level=0.000
\t\t}}
\t\tperiod={{
\t\t\tbetween={{ 0.6 30.11 }}
\t\t\ttemperature={{ 5.0 20.0 }}
\t\t\tno_phenomenon=1.500
\t\t\train_light=0.250
\t\t\train_heavy=0.100
\t\t\tsnow=0.000
\t\t\tblizzard=0.000
\t\t\tarctic_water=0.000
\t\t\tmud=0.000
\t\t\tsandstorm=0.000
\t\t\tmin_snow_level=0.000
\t\t}}
\t}}
}}
"""

_STATE_BODY = """state={{
\tid={id}
\tname="STATE_{id}"
\tprovinces={{
{prov}
\t}}
\thistory={{
\t\towner = {owner}
\t\tbuildings = {{
{buildings}\t\t}}
{resources}\t}}
\tstate_category={category}
\tmanpower={manpower}
\tbuildings_max_level_factor={mlf}
}}
"""


def _brace_blocks(txt, key):
    m = re.search(r"\b" + re.escape(key) + r"\s*=\s*\{", txt)
    if not m:
        return []
    start = m.end() - 1
    depth = 0
    j = start
    while j < len(txt):
        if txt[j] == "{":
            depth += 1
        elif txt[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return [txt[start:j + 1]]


def _block_at(txt, brace):
    depth = 0
    j = brace
    while j < len(txt):
        if txt[j] == "{":
            depth += 1
        elif txt[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return txt[brace:j + 1]


def _rewrite_sr(text, name, weather):
    out = text
    if name:
        out = re.sub(r'(name\s*=\s*)"[^"]*"', lambda m: m.group(1) + '"' + str(name) + '"', out, count=1)
    if weather is not None and weather.strip() != "":
        out2, ok = _replace_block(out, "weather", weather.rstrip() + "\n\t")
        if ok:
            out = out2
    return out


def _rename_railway_ids(text: str, idmap: dict) -> str:
    nl = "\r\n" if "\r\n" in text else "\n"
    out = []
    for ln in text.split("\n"):
        raw = ln.rstrip("\r")
        tok = raw.split()
        if len(tok) >= 3 and tok[0].lstrip("-").isdigit():
            rest = [str(idmap.get(int(t), int(t))) if t.isdigit() else t for t in tok[2:]]
            out.append(" ".join(tok[:2] + rest))
        else:
            out.append(raw)
    return nl.join(out)


def _rename_adjacency_ids(text: str, idmap: dict) -> str:
    nl = "\r\n" if "\r\n" in text else "\n"
    out = []
    for ln in text.split("\n"):
        raw = ln.rstrip("\r")
        c = raw.split(";")
        if len(c) >= 4 and c[0].strip().lstrip("-").isdigit():
            for idx in (0, 1, 3):
                v = c[idx].strip()
                if v.lstrip("-").isdigit() and int(v) > 0:
                    c[idx] = str(idmap.get(int(v), int(v)))
            out.append(";".join(c))
        else:
            out.append(raw)
    return nl.join(out)


class Editor:
    def __init__(self, mod_dir: str, game_dir: str = "", game_merge: bool = False, ref_dir: str = ""):
        self.mod = os.path.abspath(mod_dir) if mod_dir else ""
        self.game = game_dir or ""
        self.merge = bool(game_merge)
        self.ref = os.path.abspath(ref_dir) if ref_dir else ""
        self.ops = []
        self.redo = []
        self.loaded = False
        self._load()

    # ------------------------------------------------------------------ #
    def _load(self):
        self.definition = read_definition(_rresolve(self.mod, self.game, self.ref, "map", "definition.csv"))
        self.base_recs = {k: list(v) for k, v in self.definition.recs.items()}
        self.recs = {k: list(v) for k, v in self.base_recs.items()}
        bmp = _rresolve(self.mod, self.game, self.ref, "map", "provinces.bmp")
        self.base = load_provinces_bmp(bmp)
        self.h, self.w = self.base.shape[:2]
        lut = np.zeros(1 << 24, dtype=np.int32)
        for pid, r in self.base_recs.items():
            lut[rgb_key(int(r[1]), int(r[2]), int(r[3]))] = pid
        pk = ((self.base[:, :, 0].astype(np.int64) << 16) |
              (self.base[:, :, 1].astype(np.int64) << 8) |
              self.base[:, :, 2].astype(np.int64))
        self.base_pid = lut[pk].astype(np.int32)

        self.state_src = {}
        self.sr_src = {}
        self.state_txt = {}
        self.sr_txt = {}
        for fp in _rlist(self.mod, self.game, self.ref, "history", "states",
                         exts=(".txt",), merge=self.merge):
            b = os.path.basename(fp)
            self.state_src[b] = fp
            self.state_txt[b] = _read_text(fp)
        for fp in _rlist(self.mod, self.game, self.ref, "map", "strategicregions",
                         exts=(".txt",), merge=self.merge):
            b = os.path.basename(fp)
            self.sr_src[b] = fp
            self.sr_txt[b] = _read_text(fp)
        self._rail_p = _rresolve(self.mod, self.game, self.ref, "map", "railways.txt")
        self._sup_p = _rresolve(self.mod, self.game, self.ref, "map", "supply_nodes.txt")
        self._bld_p = _rresolve(self.mod, self.game, self.ref, "map", "buildings.txt")
        self.base_rail = _read_text(self._rail_p) if os.path.isfile(self._rail_p) else ""
        self.base_sup = _read_text(self._sup_p) if os.path.isfile(self._sup_p) else ""
        self.base_bld = _read_text(self._bld_p) if os.path.isfile(self._bld_p) else ""
        self.rail_txt = self.base_rail
        self.sup_txt = self.base_sup
        self.bld_txt = self.base_bld
        self.base_state_txt = dict(self.state_txt)
        self.base_sr_txt = dict(self.sr_txt)
        self._rebuild()
        self.loaded = True

    # ------------------------------------------------------------------ #
    def _color(self, pid):
        r = self.recs.get(int(pid))
        return (int(r[1]), int(r[2]), int(r[3])) if r else (0, 0, 0)

    def _merge_pixels(self, into, from_ids):
        fromset = [int(x) for x in from_ids]
        mask = np.isin(self.pid, fromset)
        if mask.any():
            self.pid[mask] = int(into)
            self.img[mask] = self._color(int(into))
        self.removed.update(fromset)

    def _mem_set(self, level, pid, file, add):
        """在 level(state/sr) 的所有文件里移除 pid; add=True 时加入 file。"""
        texts = self.state_txt if level == "state" else self.sr_txt
        pid = int(pid)
        for b in list(texts.keys()):
            ids = parse_provinces(texts[b])
            if ids is None:
                continue
            new = [i for i in ids if i != pid]
            if add and b == file and pid not in new:
                new.append(pid)
            if new != ids:
                texts[b] = rewrite_provinces(texts[b], new)

    def _apply(self, op):
        k = op["kind"]
        if k == "new_sr":
            self.sr_txt[op["file"]] = op["text"]
            return
        if k == "merge":
            self._merge_pixels(op["into"], op["from"])
        elif k == "mem":
            for _p in (op.get("pids") or [op.get("pid")]):
                self._mem_set(op["level"], _p, op.get("file"), op.get("op") == "add")
        elif k == "continent":
            for _p in (op.get("pids") or [op.get("pid")]):
                if int(_p) in self.recs:
                    self.recs[int(_p)][7] = str(op["value"])
        elif k == "terrain":
            if int(op["pid"]) in self.recs:
                self.recs[int(op["pid"])][6] = str(op["value"])
        elif k == "sr_edit":
            f = op.get("file")
            if f in self.sr_txt:
                self.sr_txt[f] = _rewrite_sr(self.sr_txt[f], op.get("name"), op.get("weather"))
        elif k == "rail":
            self._do_rail(op["a"], op["b"], op.get("op") == "add", op.get("level", 1))
        elif k == "rail_level":
            self._do_rail_level(op["i"], op["level"])
        elif k == "rail_rm":
            self._do_rail_rm(op["i"])
        elif k == "supply":
            self._do_supply(op["pid"], op.get("op") == "add")
        elif k == "building":
            self._do_building(op["pid"], op.get("key"), op.get("wx"), op.get("wy"), op.get("op") == "add")
        elif k == "terrain_auto":
            for _p, _v in (op.get("map") or {}).items():
                if int(_p) in self.recs:
                    self.recs[int(_p)][6] = str(_v)
        elif k == "state_edit":
            f = op.get("file")
            if f in self.state_txt:
                self.state_txt[f] = _rewrite_state(self.state_txt[f], op.get("data") or {})
        elif k == "new_state":
            self.state_txt[op["file"]] = op["text"]

    def _rebuild(self):
        self.pid = self.base_pid.copy()
        self.img = self.base.copy()
        self.recs = {k: list(v) for k, v in self.base_recs.items()}
        self.removed = set()
        self.state_txt = dict(self.base_state_txt)
        self.sr_txt = dict(self.base_sr_txt)
        self.rail_txt = self.base_rail
        self.sup_txt = self.base_sup
        self.bld_txt = self.base_bld
        for op in self.ops:
            self._apply(op)

    # ------------------------------------------------------------------ #
    def _type(self, pid):
        r = self.recs.get(int(pid))
        return r[4] if r else "?"

    def _resolve_file(self, txts, token):
        token = str(token).strip()
        if not token:
            return None
        if token in txts:
            return token
        low = token.lower()
        lowtxt = low if low.endswith(".txt") else low + ".txt"
        for b in txts:
            if b.lower() == low or b.lower() == lowtxt:
                return b
        # 文件名不可靠: 按文件内部 id = N 匹配 (token 本身是数字, 或以数字开头如 596-Zhejiang)
        m0 = re.match(r"^(\d+)", token)
        if m0:
            n = int(m0.group(1))
            for b in txts:
                m = re.search(r"\bid\s*=\s*(\d+)", txts[b])
                if m and int(m.group(1)) == n:
                    return b
        # 退一步: 前缀/后缀/包含
        for b in txts:
            stem = os.path.splitext(b)[0].lower()
            if stem == low or stem.startswith(low + "-") or stem.endswith("-" + low):
                return b
        for b in txts:
            if low and low in os.path.splitext(b)[0].lower():
                return b
        return None

    def do_assign(self, level, pid, target, op="add"):
        pid = int(pid)
        if pid not in self.recs:
            raise ValueError("地块 %d 不存在" % pid)
        if level == "continent":
            self.ops.append({"kind": "continent", "pid": pid, "pids": [pid], "value": str(target).strip()})
            self.redo.clear()
            self._apply(self.ops[-1])
            return self.ops[-1]
        if level == "terrain":
            self.ops.append({"kind": "terrain", "pid": pid, "value": str(target).strip()})
            self.redo.clear()
            self._apply(self.ops[-1])
            return self.ops[-1]
        if level == "state" and self._type(pid) == "sea":
            raise ValueError("海洋地块不能放入州 (只能是陆地/湖泊)")
        txts = self.state_txt if level == "state" else self.sr_txt
        target = self._resolve_file(txts, target)
        if target is None:
            raise ValueError("目标不存在(可填州ID或文件名)")
        op = "add" if op == "add" else "remove"
        rec = {"kind": "mem", "level": level, "pid": pid, "pids": [pid], "file": target, "op": op}
        self.ops.append(rec)
        self.redo.clear()
        self._apply(rec)
        return rec

    def do_assign_many(self, level, pids, target, op="add"):
        pids = [int(x) for x in pids if int(x) in self.recs]
        if not pids:
            raise ValueError("没有有效地块")
        if level == "continent":
            pids = [p for p in pids if self._type(p) == "land"]
            if not pids:
                raise ValueError("大洲只能记录陆地地块")
            rec = {"kind": "continent", "pids": pids, "pid": pids[0], "value": str(target).strip()}
            self.ops.append(rec); self.redo.clear(); self._apply(rec); return rec
        if level == "state":
            pids = [p for p in pids if self._type(p) != "sea"]
            if not pids:
                raise ValueError("海洋地块不能放入州")
        txts = self.state_txt if level == "state" else self.sr_txt
        target = self._resolve_file(txts, target)
        if target is None:
            raise ValueError("目标不存在")
        rec = {"kind": "mem", "level": level, "pids": pids, "pid": pids[0],
               "file": target, "op": "add" if op == "add" else "remove"}
        self.ops.append(rec); self.redo.clear(); self._apply(rec); return rec

    def do_merge(self, into, from_ids):
        into = int(into)
        from_ids = [int(x) for x in from_ids if int(x) != into]
        if not from_ids:
            raise ValueError("没有可并入的地块")
        for f in from_ids:
            if f not in self.recs:
                raise ValueError("地块 %d 不存在" % f)
            if f in self.removed:
                raise ValueError("地块 %d 已被并入其它地块" % f)
        op = {"kind": "merge", "into": into, "from": from_ids}
        self.ops.append(op)
        self.redo.clear()
        self._merge_pixels(into, from_ids)
        return op

    def do_delete(self, pid, into=None):
        pid = int(pid)
        if pid not in self.recs:
            raise ValueError("地块不存在")
        if into is None:
            into = self.largest_neighbor(pid)
            if into is None:
                raise ValueError("找不到可并入的邻居")
        return self.do_merge(int(into), [pid])

    def _region_id(self, base, txt):
        m = re.search(r"\bid\s*=\s*(\d+)", txt or "")
        if m:
            return int(m.group(1))
        mm = re.match(r"(\d+)", base or "")
        return int(mm.group(1)) if mm else None

    def _count_provinces(self, txt):
        m = re.search(r"provinces\s*=\s*\{([^}]*)\}", txt or "", re.S)
        if not m:
            return 0
        return len(re.findall(r"\d+", m.group(1)))

    def _region_ids(self, texts):
        out = {}
        for b, txt in texts.items():
            i = self._region_id(b, txt)
            if i is None:
                continue
            out.setdefault(i, []).append((b, self._count_provinces(txt)))
        return out

    def next_free_id(self, level="state"):
        """优先复用空闲 ID(编号空缺, 或文件存在但地块为空), 否则用最大编号+1。"""
        if level == "prov":
            used = set()
            for k in self.recs:
                try:
                    used.add(int(k))
                except Exception:
                    pass
            mx = max(used) if used else 0
            nid = mx + 1
            for i in range(1, mx + 1):
                if i not in used:
                    nid = i
                    break
            return {"next": nid, "max": mx, "reused": nid <= mx}
        if level == "continent":
            used = set()
            for k, r in self.recs.items():
                if r[4] == "sea":
                    continue
                try:
                    used.add(int(r[7]))
                except Exception:
                    pass
            mx = max(used) if used else 0
            nid = mx + 1
            for i in range(1, mx + 1):
                if i not in used:
                    nid = i
                    break
            return {"next": nid, "max": mx, "reused": nid <= mx}
        texts = self.state_txt if level == "state" else self.sr_txt
        ids = self._region_ids(texts)
        mx = max(ids) if ids else 0
        nid = mx + 1
        reused = False
        for i in range(1, mx + 1):
            lst = ids.get(i)
            if not lst or all(n == 0 for (_b, n) in lst):
                nid = i
                reused = True
                break
        return {"next": nid, "max": mx, "reused": reused}

    def do_new_state(self, sid, provinces, owner="", manpower=0, category="rural",
                     infrastructure=0, industrial_complex=0, arms_factory=0, dockyard=0,
                     buildings=None, resources=None, mlf=1.0):
        provinces = [int(x) for x in provinces if int(x) in self.recs]
        if not provinces:
            raise ValueError("至少要有 1 个地块")
        bad = [p for p in provinces if self._type(p) == "sea"]
        if bad:
            raise ValueError("海洋地块不能加入州: %s" % bad[:5])
        try:
            sid = int(sid) if str(sid).strip() not in ("", "0") and sid is not None else 0
        except (TypeError, ValueError):
            sid = 0
        _ids = self._region_ids(self.state_txt)
        if not sid:
            sid = self.next_free_id("state")["next"]
        if any(n > 0 for (_b, n) in _ids.get(sid, [])):
            raise ValueError("州ID %d 已被占用；下一个可用: %d" % (sid, self.next_free_id("state")["next"]))
        fname = None
        for (_b, n) in _ids.get(sid, []):
            if n == 0:                      # 复用存在但为空的州文件
                fname = _b
                break
        if not fname:
            fname = "%d-State_%d.txt" % (sid, sid)
        bl = dict(buildings or {})
        for k, v in (("industrial_complex", industrial_complex), ("arms_factory", arms_factory),
                     ("dockyard", dockyard)):
            if int(v) > 0:
                bl[k] = int(v)
        btxt = ""
        if int(infrastructure) > 0:
            btxt += "\t\t\tinfrastructure = %d\n" % int(infrastructure)
        btxt += "".join("\t\t\t%s = %d\n" % (k, int(v)) for k, v in sorted(bl.items()) if int(v) > 0)
        rl = sorted((resources or {}).items())
        rtxt = ""
        if rl:
            rtxt = "\t\tresources={\n" + "".join("\t\t\t%s = %d\n" % (k, int(v)) for k, v in rl) + "\t\t}\n"
        prov = "".join("\t\t%d\n" % p for p in provinces)
        text = _STATE_BODY.format(id=sid, prov=prov, owner=owner or "", buildings=btxt,
                                  resources=rtxt, category=category, manpower=int(manpower),
                                  mlf=float(mlf))
        op = {"kind": "new_state", "file": fname, "text": text, "id": sid}
        self.ops.append(op)
        self._apply(op)
        for p in provinces:
            rec = {"kind": "mem", "level": "state", "pid": p, "pids": [p], "file": fname, "op": "add"}
            self.ops.append(rec)
            self._mem_set("state", p, fname, True)
        self.redo.clear()
        return op

    def do_new_sr(self, sid, provinces):
        provinces = [int(x) for x in provinces if int(x) in self.recs]
        if not provinces:
            raise ValueError("至少要有 1 个地块")
        try:
            sid = int(sid) if str(sid).strip() not in ("", "0") and sid is not None else 0
        except (TypeError, ValueError):
            sid = 0
        _ids = self._region_ids(self.sr_txt)
        if not sid:
            sid = self.next_free_id("sr")["next"]
        if any(n > 0 for (_b, n) in _ids.get(sid, [])):
            raise ValueError("战略区ID %d 已被占用；下一个可用: %d" % (sid, self.next_free_id("sr")["next"]))
        nm = "STRATEGICREGION_%d" % sid
        fname = None
        for (_b, n) in _ids.get(sid, []):
            if n == 0:                      # 复用存在但为空的战略区文件
                fname = _b
                break
        if not fname:
            fname = "%d-%d.txt" % (sid, sid)
        prov = "".join("\t\t%d\n" % p for p in provinces)
        text = _SR_BODY.format(id=sid, name=nm, prov=prov)
        op = {"kind": "new_sr", "file": fname, "text": text, "id": sid}
        self.ops.append(op)
        self._apply(op)
        for p in provinces:
            rec = {"kind": "mem", "level": "sr", "pid": p, "pids": [p], "file": fname, "op": "add"}
            self.ops.append(rec)
            self._mem_set("sr", p, fname, True)
        self.redo.clear()
        return op

    def _find_state_text(self, file):
        f = self._resolve_file(self.state_txt, file)
        if f is None:
            raise ValueError("州不存在: %s" % file)
        return f, self.state_txt[f]

    def state_data(self, file):
        f, txt = self._find_state_text(file)
        d = {"file": f, "name": os.path.splitext(f)[0]}
        m = re.search(r"\bmanpower\s*=\s*([0-9.]+)", txt)
        d["manpower"] = int(float(m.group(1))) if m else 0
        m = re.search(r"\bstate_category\s*=\s*(\S+)", txt)
        d["category"] = m.group(1) if m else ""
        m = re.search(r"\bowner\s*=\s*([A-Za-z0-9_]+)", txt)
        d["owner"] = m.group(1) if m else ""
        m = re.search(r"\bid\s*=\s*(\d+)", txt)
        d["id"] = m.group(1) if m else ""
        def _kv(blk):
            out = {}
            if blk:
                for mm in re.finditer(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*([0-9.]+)", blk):
                    out[mm.group(1)] = mm.group(2)
            return out
        mb = re.search(r"buildings\s*=\s*\{([^}]*)\}", txt)
        d["buildings"] = _kv(mb.group(1) if mb else "")
        mr = re.search(r"resources\s*=\s*\{([^}]*)\}", txt)
        d["resources"] = _kv(mr.group(1) if mr else "")
        return d

    def do_state_edit(self, file, data):
        f, txt = self._find_state_text(file)
        op = {"kind": "state_edit", "file": f, "data": dict(data or {})}
        self.ops.append(op)
        self.redo.clear()
        self._apply(op)
        return op

    def _coast_points(self, ids, cap=400):
        """一次扫描取出若干地块与"任意其它地块"相接的边界像素(用于海峡的最近点对兜底)。"""
        import numpy as _np
        ids = [int(i) for i in set(ids) if int(i) > 0]
        if not ids:
            return {}
        pid = self.pid
        mx = int(pid.max())
        lut = _np.zeros(mx + 2, dtype=bool)
        for i in ids:
            if i <= mx:
                lut[i] = True
        if not lut.any():
            return {}
        acc = {}
        for (dx, dy) in ((1, 0), (0, 1)):
            if dx:
                Aa = pid[:, :-1]; Bb = pid[:, 1:]
            else:
                Aa = pid[:-1, :]; Bb = pid[1:, :]
            diff = (Aa != Bb)
            for (side, is_a) in ((Aa, True), (Bb, False)):
                m = diff & lut[_np.clip(side, 0, mx + 1)]
                if not m.any():
                    continue
                ys, xs = _np.nonzero(m)
                vals = side[m].tolist()
                if is_a:
                    for x0, y0, v in zip(xs.tolist(), ys.tolist(), vals):
                        acc.setdefault(int(v), []).append((x0, y0))
                else:
                    for x0, y0, v in zip(xs.tolist(), ys.tolist(), vals):
                        acc.setdefault(int(v), []).append((x0 + dx, y0 + dy))
        out = {}
        for k, pts in acc.items():
            uniq = list(dict.fromkeys(pts))          # 去重, 保持顺序
            if len(uniq) > cap:
                # 按 8x8 格子抽稀, 保持空间分布
                seen = set(); keep = []
                for (x0, y0) in uniq:
                    key = (x0 >> 3, y0 >> 3)
                    if key in seen:
                        continue
                    seen.add(key); keep.append((x0, y0))
                    if len(keep) >= cap:
                        break
                uniq = keep
            out[k] = uniq
        return out

    def _pair_points(self, pairs):
        """返回 {pair: [(x,y),...]} —— 每一对省的公共边界像素(pair=(lo,hi))。"""
        import numpy as _np
        want = {}
        ids = set()
        for pr in pairs:
            lo, hi = int(pr[0]), int(pr[1])
            if lo <= 0 or hi <= 0 or lo == hi:
                continue
            if lo > hi:
                lo, hi = hi, lo
            want[(lo, hi)] = True
            ids.add(lo)
            ids.add(hi)
        if not want:
            return {}
        pid = self.pid
        idarr = _np.fromiter(ids, dtype=_np.int64, count=len(ids))
        acc = {}
        for (A, B, dx, dy) in ((pid[:, :-1], pid[:, 1:], 1, 0), (pid[:-1, :], pid[1:, :], 0, 1)):
            A2 = A.astype(_np.int64)
            B2 = B.astype(_np.int64)
            m = (A > 0) & (B > 0) & (A != B)
            if not m.any():
                continue
            m = m & (_np.isin(A2, idarr) & _np.isin(B2, idarr))
            if not m.any():
                continue
            ys, xs = _np.nonzero(m)
            for x0, y0, a0, b0 in zip(xs.tolist(), ys.tolist(), A2[m].tolist(), B2[m].tolist()):
                key = (a0, b0) if a0 < b0 else (b0, a0)
                if key not in want:
                    continue
                # 只保留 1px 细边: 取较小 ID 那一侧的像素, 这样串联出来就是"一条边界线"
                if a0 < b0:
                    acc.setdefault(key, []).append((x0, y0))
                else:
                    acc.setdefault(key, []).append((x0 + dx, y0 + dy))
        return acc

    def _land_adj(self):
        try:
            _sig = (int(self.pid.shape[0]), int(self.pid.shape[1]), int(self.pid.sum()))
        except Exception:
            _sig = None
        if getattr(self, "_ladj", None) is not None and getattr(self, "_ladj_sig", None) == _sig:
            return self._ladj
        self._ladj_sig = _sig
        import collections as _c
        land = set(p for p, r in self.recs.items() if r[4] == "land")
        adj = _c.defaultdict(set)
        pid = self.pid.astype(np.int64)
        for (a, b) in ((pid[:, :-1], pid[:, 1:]), (pid[1:, :], pid[:-1, :])):
            m = a != b
            aa = a[m].tolist(); bb = b[m].tolist()
            for x, y in zip(aa, bb):
                if x > 0 and y > 0 and x in land and y in land:
                    adj[x].add(y); adj[y].add(x)
        # 海峡/运河: 两个陆地地块通过一个海/湖 province 相连
        edges = []
        blocked = []
        ap = _rresolve(self.mod, self.game, self.ref, "map", "adjacencies.csv")
        aps = []
        if os.path.isfile(ap):
            aps.append(ap)
        for base in (self.game, self.mod):     # 原版补足: 主文件里没有的相邻对从另一来源补齐
            q = os.path.join(base, "map", "adjacencies.csv")
            if os.path.isfile(q) and not any(os.path.normcase(q) == os.path.normcase(x) for x in aps):
                aps.append(q)
        seen_pairs = set()
        for pi, apx in enumerate(aps):
            for i, ln in enumerate(_read_text(apx).split("\n")):
                c = ln.rstrip("\r").split(";")
                if i == 0 or len(c) < 4 or not c[0].strip().lstrip("-").isdigit():
                    continue
                try:
                    f, t2_ = int(c[0]), int(c[1])
                except ValueError:
                    continue
                typ = (c[2] or "").strip().lower()
                rule = ((c[8] if len(c) > 8 else "") + " " + (c[9] if len(c) > 9 else "")).lower()
                pk = (min(f, t2_), max(f, t2_))
                if pk in seen_pairs:
                    continue
                if f in land and t2_ in land and f != t2_:
                    seen_pairs.add(pk)
                    if typ == "impassable":                 # 禁止直接相连
                        blocked.append((min(f, t2_), max(f, t2_)))
                        edges.append({"type": "impassable", "a": f, "b": t2_})
                    elif typ in ("sea", "canal", "strait"):  # 跨海连接(海峡/运河) => 可穿越
                        kind = "canal" if "canal" in rule else "strait"
                        adj[f].add(t2_); adj[t2_].add(f)
                        _th = c[3].strip()
                        edges.append({"type": kind, "a": f, "b": t2_,
                                      "th": int(_th) if _th.lstrip("-").isdigit() else 0})
        for (ba, bb) in blocked:                        # impassable: 从像素邻接中剔除
            adj[ba].discard(bb); adj[bb].discard(ba)
        try:
            _prs = set()
            for e in edges:
                if e.get("type") == "impassable":
                    _prs.add(_pk(e["a"], e["b"]))
                elif e.get("th"):
                    _prs.add(_pk(e["a"], e["th"]))
                    _prs.add(_pk(e["b"], e["th"]))
            _pt = self._pair_points(_prs) if _prs else {}
            for e in edges:
                if e.get("type") == "impassable":
                    e["segments"] = _chain_segments(_pt.get(_pk(e["a"], e["b"]), []))
                elif e.get("th"):
                    # 海峡/运河: 两端各自与水路的接触点质心 -> 真实跨越位置
                    pa = _centroid(_pt.get(_pk(e["a"], e["th"]), []))
                    pb = _centroid(_pt.get(_pk(e["b"], e["th"]), []))
                    if pa and pb:
                        e["path"] = [pa, pb]
            # 兜底: 有些 adjacencies 的 through 并不与两端相邻 -> 用"海岸最近点对"画短线
            _miss = [e for e in edges
                     if e.get("type") in ("strait", "canal") and not e.get("path")]
            if _miss:
                import numpy as _np
                _ids = []
                for e in _miss:
                    _ids.append(int(e["a"])); _ids.append(int(e["b"]))
                _coast = self._coast_points(_ids)
                for e in _miss:
                    A = _coast.get(int(e["a"])) or []
                    B = _coast.get(int(e["b"])) or []
                    if not A or not B:
                        continue
                    aa = _np.asarray(A, dtype=_np.float64)
                    bb = _np.asarray(B, dtype=_np.float64)
                    d2 = ((aa[:, None, :] - bb[None, :, :]) ** 2).sum(-1)
                    i, j = _np.unravel_index(int(_np.argmin(d2)), d2.shape)
                    _pair = [[int(round(aa[i, 0])), int(round(aa[i, 1]))],
                             [int(round(bb[j, 0])), int(round(bb[j, 1]))]]
                    # 海峡按"窄水道"150px; 运河可以跨更宽的水面(900px); 更远的存起来备选显示
                    _lim = 900.0 * 900.0 if e.get("type") == "canal" else 150.0 * 150.0
                    if float(d2[i, j]) <= _lim:
                        e["path"] = _pair
                    else:
                        e["far"] = _pair
        except Exception:
            pass
        self._ladj = adj
        self._adj_edges = edges
        return adj

    def rail_path(self, a, b):
        import collections as _c
        a, b = int(a), int(b)
        adj = self._land_adj()
        if a not in adj or b not in adj:
            return None
        seen = {a: None}
        q = _c.deque([a])
        while q:
            cur = q.popleft()
            if cur == b:
                break
            for n in adj.get(cur, ()):
                if n not in seen:
                    seen[n] = cur
                    q.append(n)
        if b not in seen:
            return None
        path = []
        cur = b
        while cur is not None:
            path.append(cur); cur = seen[cur]
        return path[::-1]

    def _do_rail_level(self, i, level):
        lines = self.rail_txt.split("\n")
        if 0 <= int(i) < len(lines):
            tok = lines[i].split()
            if len(tok) >= 3 and tok[0].isdigit():
                tok[0] = str(int(level))
                lines[int(i)] = " ".join(tok)
                self.rail_txt = "\n".join(lines)

    def _do_rail_rm(self, i):
        lines = self.rail_txt.split("\n")
        if 0 <= int(i) < len(lines):
            del lines[int(i)]
            self.rail_txt = "\n".join(lines)

    def rail_data(self):
        rails = []
        for idx, ln in enumerate(self.rail_txt.split("\n")):
            tok = ln.split()
            if len(tok) >= 3 and tok[0].isdigit() and tok[1].isdigit():
                nodes = [int(x) for x in tok[2:] if x.isdigit()]
                if len(nodes) >= 2:
                    rails.append({"i": idx, "level": int(tok[0]), "nodes": nodes})
        sups = []
        for ln in self.sup_txt.split("\n"):
            tok = ln.split()
            if len(tok) >= 2 and tok[1].isdigit():
                sups.append({"level": int(tok[0]), "pid": int(tok[1])})
        self._land_adj()
        return {"rails": rails, "supplies": sups,
                "edges": list(getattr(self, "_adj_edges", None) or []),
                "land": [p for p, r in self.recs.items() if r[4] == "land"]}

    def do_rail_level(self, i, level):
        rec = {"kind": "rail_level", "i": int(i), "level": int(level)}
        self.ops.append(rec); self.redo.clear(); self._apply(rec); return rec

    def do_rail_rm(self, i):
        rec = {"kind": "rail_rm", "i": int(i)}
        self.ops.append(rec); self.redo.clear(); self._apply(rec); return rec

    def _do_rail(self, a, b, add, level=1):
        a, b = int(a), int(b)
        if add:
            ra, rb = self.recs.get(a), self.recs.get(b)
            if not ra or not rb or ra[4] != "land" or rb[4] != "land":
                raise ValueError("海上/湖泊地块不能放铁路（只能选陆地）")
        out = []
        hit = False
        for ln in self.rail_txt.split("\n"):
            tok = ln.split()
            if len(tok) >= 4 and tok[0].isdigit():
                ids = [int(x) for x in tok[2:] if x.isdigit()]
                if a in ids and b in ids:
                    hit = True
                    if add:
                        out.append(ln)
                    continue
            out.append(ln)
        if add and not hit:
            path = self.rail_path(a, b) or [a, b]
            out.append("%d %d %s" % (int(level or 1), len(path), " ".join(map(str, path))))
        self.rail_txt = "\n".join(out)

    def _do_supply(self, pid, add):
        pid = int(pid)
        if add:
            r0 = self.recs.get(pid)
            if not r0 or r0[4] != "land":
                raise ValueError("海上/湖泊地块不能放补给中心（只能选陆地）")
        out = []
        hit = False
        for ln in self.sup_txt.split("\n"):
            tok = ln.split()
            if len(tok) >= 2 and tok[1].isdigit() and int(tok[1]) == pid:
                hit = True
                if add:
                    out.append(ln)
                continue
            out.append(ln)
        if add and not hit:
            out.append("1 %d " % pid)
        self.sup_txt = "\n".join(out)

    def _do_building(self, pid, key, wx, wy, add):
        pid = int(pid)
        out = []
        for ln in self.bld_txt.split("\n"):
            c = ln.split(";")
            if len(c) >= 2 and c[0].strip().isdigit() and int(c[0]) == pid and c[1].strip() == str(key):
                if add:
                    out.append(ln)
                continue
            out.append(ln)
        if add:
            out.append("%d;%s;%.2f;%.2f;0.50;0.00;%d" % (pid, key, float(wx or 0), float(wy or 0), pid))
        self.bld_txt = "\n".join(out)

    def do_rail(self, a, b, op="add", level=1):
        rec = {"kind": "rail", "a": int(a), "b": int(b), "level": int(level or 1), "op": "add" if op == "add" else "remove"}
        self.ops.append(rec); self.redo.clear(); self._apply(rec); return rec

    def do_supply(self, pid, op="add"):
        rec = {"kind": "supply", "pid": int(pid), "op": "add" if op == "add" else "remove"}
        self.ops.append(rec); self.redo.clear(); self._apply(rec); return rec

    def do_building(self, pid, key, wx, wy, op="add"):
        rec = {"kind": "building", "pid": int(pid), "key": str(key), "wx": wx, "wy": wy,
               "op": "add" if op == "add" else "remove"}
        self.ops.append(rec); self.redo.clear(); self._apply(rec); return rec

    def sr_data(self, file):
        f = self._resolve_file(self.sr_txt, file)
        if f is None:
            raise ValueError("战略区不存在")
        txt = self.sr_txt[f]
        name = re.search(r'name\s*=\s*"([^"]*)"', txt)
        weather = ""
        m = re.search(r"weather\s*=\s*\{", txt)
        if m:
            i = m.end() - 1; d = 0; j = i
            while j < len(txt):
                if txt[j] == "{":
                    d += 1
                elif txt[j] == "}":
                    d -= 1
                    if d == 0:
                        break
                j += 1
            weather = txt[i + 1:j].strip("\n")
        pm = re.search(r"provinces\s*=\s*\{([^}]*)\}", txt)
        ids = [x for x in (pm.group(1).split() if pm else []) if x.isdigit()]
        return {"file": f, "name": name.group(1) if name else "", "weather": weather, "provinces": len(ids)}

    def do_sr_edit(self, file, name=None, weather=None):
        f = self._resolve_file(self.sr_txt, file)
        if f is None:
            raise ValueError("战略区不存在")
        rec = {"kind": "sr_edit", "file": f, "name": name, "weather": weather}
        self.ops.append(rec); self.redo.clear(); self._apply(rec); return rec

    # ---- 地形 ----
    def _terrain_defs(self):
        if getattr(self, "_tdefs", None) is not None:
            return self._tdefs
        defs = {}
        for root in (self.game, self.mod):
            d = os.path.join(root, "common", "terrain") if root else ""
            if not d or not os.path.isdir(d):
                continue
            for f in sorted(os.listdir(d)):
                if f.lower().endswith(".txt"):
                    txt = _read_text(os.path.join(d, f))
                    for blk in _brace_blocks(txt, "categories"):
                        for m in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\{", blk):
                            seg = _block_at(blk, m.end() - 1)
                            cm = re.search(r"color\s*=\s*\{\s*(\d+)\s+(\d+)\s+(\d+)", seg)
                            if cm:
                                defs[m.group(1)] = (int(cm.group(1)), int(cm.group(2)), int(cm.group(3)))
        self._tdefs = defs
        return defs

    def terrain_data(self, autogen=False):
        defs = self._terrain_defs()
        names = sorted(defs.keys())
        if not names:
            return {"defs": {}, "prov": {}, "names": []}
        bp = _rresolve(self.mod, self.game, self.ref, "map", "terrain.bmp")
        if not os.path.isfile(bp):
            return {"defs": defs, "prov": {}, "names": names}
        him = Image.open(bp)
        pal = him.getpalette() or []
        palrgb = np.array((pal[:768] + [0] * (768 - len(pal[:768]))), dtype=np.int32).reshape(256, 3)
        idx = np.asarray(him).astype(np.int64)
        maxid = int(self.pid.max())
        # 每个地块的主色索引
        key = self.pid.astype(np.int64) * 256 + idx
        cnt = np.bincount(key.ravel(), minlength=(maxid + 1) * 256).reshape(-1, 256)
        maj = cnt.argmax(1)
        # 从 definition.csv 反推 索引->地形 (多数票)
        votes = {}
        for pid in range(1, maxid + 1):
            r = self.recs.get(pid)
            if not r or r[4] != "land":
                continue
            if cnt[pid].sum() <= 0:
                continue
            votes.setdefault(int(maj[pid]), {})
            nm = r[6]
            votes[int(maj[pid])][nm] = votes[int(maj[pid])].get(nm, 0) + 1
        idx2name = {i: max(v.items(), key=lambda kv: kv[1])[0] for i, v in votes.items()}
        # 未知索引 -> 用颜色最近的地形兜底
        cat = np.array([defs[n] for n in names], dtype=np.int32)
        dist = ((palrgb[:, None, :] - cat[None, :, :]) ** 2).sum(-1)
        color_best = dist.argmin(1)
        prov = {}
        agree = 0; tot = 0
        for pid in range(1, maxid + 1):
            r = self.recs.get(pid)
            if not r or r[4] != "land":
                continue
            mi = int(maj[pid])
            nm = idx2name.get(mi) or names[int(color_best[mi])]
            prov[str(pid)] = nm
            tot += 1
            if nm == r[6]:
                agree += 1
        self._tmap_agree = (agree, tot)
        return {"defs": defs, "prov": prov, "names": names, "agree": agree, "total": tot}

    def do_terrain_auto(self):
        d = self.terrain_data()
        m = d.get("prov") or {}
        if not m:
            raise ValueError("未能从 terrain.bmp 解析出地形")
        rec = {"kind": "terrain_auto", "map": m}
        self.ops.append(rec); self.redo.clear(); self._apply(rec); return {"count": len(m)}

    def export_terrain_bmp(self, out_dir):
        """由 definition.csv 的地形生成 map/terrain.bmp (索引位图, 沿用原调色板), 直接导出。"""
        bp = _rresolve(self.mod, self.game, self.ref, "map", "terrain.bmp")
        if not os.path.isfile(bp):
            raise ValueError("找不到 terrain.bmp 作为调色板参照")
        him = Image.open(bp)
        pal = him.getpalette()
        idxmap = np.asarray(him).astype(np.int64)
        maxid = int(self.pid.max())
        # 索引 -> 地形名 (由 definition.csv 投票) + 每个地形名偏好用哪个索引
        key = self.pid.astype(np.int64) * 256 + idxmap
        cnt = np.bincount(key.ravel(), minlength=(maxid + 1) * 256).reshape(-1, 256)
        votes = {}
        for pid in range(1, maxid + 1):
            r = self.recs.get(pid)
            if not r or r[4] != "land" or cnt[pid].sum() <= 0:
                continue
            i0 = int(cnt[pid].argmax())
            votes.setdefault(i0, {})
            votes[i0][r[6]] = votes[i0].get(r[6], 0) + 1
        idx2name = {i: max(v.items(), key=lambda kv: kv[1])[0] for i, v in votes.items()}
        name2idx = {}
        for i, nm in idx2name.items():
            if nm not in name2idx:
                name2idx[nm] = i
        # 需要但没索引的地形 -> 用颜色最近
        defs = self._terrain_defs()
        palrgb = np.array((pal[:768] + [0] * (768 - len(pal[:768]))), dtype=np.int32).reshape(256, 3)
        for nm, rgb in defs.items():
            if nm not in name2idx:
                d = ((palrgb - np.array(rgb, dtype=np.int32)) ** 2).sum(1)
                name2idx[nm] = int(d.argmin())
        lut = np.zeros(maxid + 2, dtype=np.uint8)
        for pid in range(1, maxid + 1):
            r = self.recs.get(pid)
            if not r or r[4] != "land":
                continue
            nm = r[6]
            if nm in name2idx:
                lut[pid] = name2idx[nm]
        out = lut[np.clip(self.pid, 0, maxid + 1)]
        im = Image.fromarray(out, "P")
        im.putpalette(pal)
        os.makedirs(os.path.join(out_dir, "map"), exist_ok=True)
        dst = os.path.join(out_dir, "map", "terrain.bmp")
        im.save(dst)
        return {"path": dst, "size": list(im.size), "terrains": len(name2idx)}

    def commit(self):
        """把当前编辑结果设为新基准(导出后调用), 之后导出只包含相对它的新增改动。"""
        self.base_recs = {k: list(v) for k, v in self.recs.items()}
        self.base = self.img.copy()
        self.base_pid = self.pid.copy()
        self.base_state_txt = dict(self.state_txt)
        self.base_sr_txt = dict(self.sr_txt)
        self.base_rail = self.rail_txt
        self.base_sup = self.sup_txt
        self.base_bld = self.bld_txt
        self.ops = []
        self.redo = []
        self.removed = set()

    def undo(self):
        if not self.ops:
            return 0
        self.redo.append(self.ops.pop())
        self._rebuild()
        return 1

    def redo_op(self):
        if not self.redo:
            return 0
        self.ops.append(self.redo.pop())
        self._rebuild()
        return 1

    def reset(self):
        self.ops = []
        self.redo = []
        self._rebuild()

    # ------------------------------------------------------------------ #
    def neighbors(self, pid):
        pid = int(pid)
        m = (self.pid == pid)
        cnt = collections.Counter()
        if not m.any():
            return cnt
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            nm = np.roll(m, (dy, dx), (0, 1))
            npid = np.roll(self.pid, (dy, dx), (0, 1))
            vals = npid[m & ~nm]
            vals = vals[vals > 0]
            u, c = np.unique(vals, return_counts=True)
            for a, b in zip(u.tolist(), c.tolist()):
                cnt[a] += b
        cnt.pop(pid, None)
        return cnt

    def largest_neighbor(self, pid):
        c = self.neighbors(pid)
        return max(c.items(), key=lambda kv: kv[1])[0] if c else None

    def status(self):
        return {
            "loaded": True,
            "ops": self.ops,
            "op_count": len(self.ops),
            "redo_count": len(self.redo),
            "removed": sorted(self.removed),
            "state_files": sorted(self.state_txt.keys()),
            "sr_files": sorted(self.sr_txt.keys()),
        }

    def assets(self):
        buildings = set()
        for root in (self.mod, self.game):
            d = os.path.join(root, "common", "buildings") if root else ""
            if not (d and os.path.isdir(d)):
                continue
            for f in sorted(os.listdir(d)):
                if f.lower().endswith(".txt"):
                    txt = _read_text(os.path.join(d, f))
                    for m in re.finditer(r"^\s*([a-zA-Z][a-zA-Z0-9_]*)\s*=\s*\{", txt, re.M):
                        k = m.group(1)
                        if k != "buildings":
                            buildings.add(k)
        terrains = set(STANDARD_TERRAINS)
        for r in self.base_recs.values():
            terrains.add(r[6])
        return {"buildings": sorted(buildings), "terrains": sorted(terrains)}

    # ------------------------------------------------------------------ #
    def export(self, out_dir: str, progress=None):
        def log(m):
            if progress:
                progress(str(m))
        os.makedirs(os.path.join(out_dir, "map"), exist_ok=True)
        n_ops = len(self.ops)
        merges = [o for o in self.ops if o["kind"] == "merge"]
        rec_changed = any(o["kind"] in ("continent", "terrain", "terrain_auto") for o in self.ops) or merges
        if merges:
            log("写出 provinces.bmp ...")
            save_provinces_bmp(self.img, os.path.join(out_dir, "map", "provinces.bmp"))
        if rec_changed:
            log("写出 definition.csv ...")
            lines = []
            for ln in self.definition.lines:
                p = ln.rstrip("\r").split(";")
                if len(p) >= 8 and p[0].isdigit():
                    pid = int(p[0])
                    if pid in self.removed:
                        continue
                    if pid in self.recs:
                        lines.append(";".join(self.recs[pid]))
                        continue
                lines.append(ln.rstrip("\r"))
            nl = "\r\n" if b"\r\n" in self.definition.raw[:200] else "\n"
            with open(os.path.join(out_dir, "map", "definition.csv"), "wb") as f:
                f.write((nl.join(lines) + nl).encode("utf-8", "surrogateescape"))

        n_st = n_sr = 0
        for b, txt in self.state_txt.items():
            if txt != self.base_state_txt.get(b):
                _write_text(os.path.join(out_dir, "history", "states", b), txt)
                n_st += 1
        for b, txt in self.sr_txt.items():
            if txt != self.base_sr_txt.get(b):
                _write_text(os.path.join(out_dir, "map", "strategicregions", b), txt)
                n_sr += 1
        log("写出 states(%d) / 战略区(%d) ..." % (n_st, n_sr))
        if self.rail_txt and self.rail_txt != self.base_rail:
            _write_text(os.path.join(out_dir, "map", "railways.txt"), self.rail_txt)
        if self.sup_txt and self.sup_txt != self.base_sup:
            _write_text(os.path.join(out_dir, "map", "supply_nodes.txt"), self.sup_txt)
        if self.bld_txt and self.bld_txt != self.base_bld:
            _write_text(os.path.join(out_dir, "map", "buildings.txt"), self.bld_txt)

        idmap = {}
        for o in merges:
            for f in o["from"]:
                idmap[int(f)] = int(o["into"])
        if idmap:
            log("重命名 railways / adjacencies 里的地块ID ...")
            p = _rresolve(self.mod, self.game, self.ref, "map", "railways.txt")
            if os.path.isfile(p):
                _write_text(os.path.join(out_dir, "map", "railways.txt"),
                            _rename_railway_ids(_read_text(p), idmap))
            p = _rresolve(self.mod, self.game, self.ref, "map", "adjacencies.csv")
            if os.path.isfile(p):
                _write_text(os.path.join(out_dir, "map", "adjacencies.csv"),
                            _rename_adjacency_ids(_read_text(p), idmap))
            p = _rresolve(self.mod, self.game, self.ref, "map", "adjacency_rules.txt")
            if os.path.isfile(p):
                txt = _read_text(p)

                def _r(m):
                    ids = [str(idmap.get(int(x), int(x))) for x in m.group(1).split() if x.isdigit()]
                    return "required_provinces = { %s }" % " ".join(ids)
                nt = re.sub(r"required_provinces\s*=\s*\{\s*([^}]*)\}", _r, txt)
                if nt != txt:
                    _write_text(os.path.join(out_dir, "map", "adjacency_rules.txt"), nt)

        self.commit()
        log("导出完成 -> %s (已把当前结果设为新基准, 之后导出只含新增改动)" % out_dir)
        return {"out_dir": out_dir, "removed": sorted(self.removed), "ops": n_ops,
                "states_written": n_st, "sr_written": n_sr, "renamed": len(idmap)}
