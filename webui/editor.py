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
        for op in self.ops:
            self._apply(op)

    # ------------------------------------------------------------------ #
    def _type(self, pid):
        r = self.recs.get(int(pid))
        return r[4] if r else "?"

    def _resolve_file(self, txts, token):
        token = str(token).strip()
        if token in txts:
            return token
        low = token.lower()
        for b in txts:
            if b.lower() == low or b.lower() == (low + ".txt"):
                return b
        for b in txts:
            stem = os.path.splitext(b)[0].lower()
            if stem == low or stem.startswith(low + "-") or stem.endswith("-" + low):
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

    def do_new_state(self, sid, provinces, owner="", manpower=0, category="rural",
                     infrastructure=0, industrial_complex=0, arms_factory=0, dockyard=0,
                     buildings=None, resources=None, mlf=1.0):
        provinces = [int(x) for x in provinces if int(x) in self.recs]
        if not provinces:
            raise ValueError("至少要有 1 个地块")
        bad = [p for p in provinces if self._type(p) == "sea"]
        if bad:
            raise ValueError("海洋地块不能加入州: %s" % bad[:5])
        # 下一个州 ID
        maxid = 0
        for b, txt in self.state_txt.items():
            m = re.search(r"\bid\s*=\s*(\d+)", txt)
            if m:
                maxid = max(maxid, int(m.group(1)))
            else:
                mm = re.match(r"(\d+)", b)
                if mm:
                    maxid = max(maxid, int(mm.group(1)))
        try:
            sid = int(sid) if str(sid).strip() not in ("", "0") and sid is not None else 0
        except (TypeError, ValueError):
            sid = 0
        if not sid:
            sid = maxid + 1
        for b, txt in self.state_txt.items():
            m = re.search(r"\bid\s*=\s*(\d+)", txt)
            if (m and int(m.group(1)) == sid) or re.match(r"^%d-" % sid, b):
                raise ValueError("州ID %d 已被占用" % sid)
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
        maxid = 0
        for b, txt in self.sr_txt.items():
            m = re.search(r"\bid\s*=\s*(\d+)", txt)
            if m:
                maxid = max(maxid, int(m.group(1)))
            else:
                mm = re.match(r"(\d+)", b)
                if mm:
                    maxid = max(maxid, int(mm.group(1)))
        try:
            sid = int(sid) if str(sid).strip() not in ("", "0") and sid is not None else 0
        except (TypeError, ValueError):
            sid = 0
        if not sid:
            sid = maxid + 1
        for b, txt in self.sr_txt.items():
            m = re.search(r"\bid\s*=\s*(\d+)", txt)
            if (m and int(m.group(1)) == sid) or re.match(r"^%d-" % sid, b):
                raise ValueError("战略区ID %d 已被占用" % sid)
        nm = "STRATEGICREGION_%d" % sid
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

    def commit(self):
        """把当前编辑结果设为新基准(导出后调用), 之后导出只包含相对它的新增改动。"""
        self.base_recs = {k: list(v) for k, v in self.recs.items()}
        self.base = self.img.copy()
        self.base_pid = self.pid.copy()
        self.base_state_txt = dict(self.state_txt)
        self.base_sr_txt = dict(self.sr_txt)
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
        rec_changed = any(o["kind"] in ("continent", "terrain") for o in self.ops) or merges
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
