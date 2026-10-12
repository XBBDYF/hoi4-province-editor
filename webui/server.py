# -*- coding: utf-8 -*-
"""HOI4 地块细化 - HTML 前端后端 (Flask)。

启动:
    py -3.13 server.py            # 默认 http://127.0.0.1:8730
    py -3.13 server.py --port 9000

功能:
  * 地图预览 (地形/省份/战略区/州/大洲/边界 图层, 缩放平移, 悬停查省份)
  * 分析 (只读, 不写文件)
  * 细化 (真正写出到输出目录, 带实时日志)
  * 完整性检查 (战略区/州/铁路/海峡)
  * 报告查看 / 输出预览图
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
import time
import traceback
import uuid
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from flask import Flask, Response, jsonify, request, send_from_directory

from province_splitter.core import Config, run

CACHE_DIR = os.path.join(HERE, "cache")

DEFAULT_MOD = r"C:\Users\Administrator\Documents\Paradox Interactive\Hearts of Iron IV\mod\Project_IRIS"
DEFAULT_GAME = r"D:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV"
DEFAULT_OUT = os.path.join(ROOT, "output")


def _default_gradient():
    for p in (os.path.join(ROOT, "底图密度.png"),
              os.path.join(ROOT, "#U5e95#U56fe#U5bc6#U5ea6.png")):
        if os.path.isfile(p):
            return p
    return ""


app = Flask(__name__, static_folder=os.path.join(HERE, "static"), static_url_path="/static")

# --------------------------------------------------------------------------- #
# 任务管理 (分析 / 细化 / 建缓存 / 审计 都走后台线程)
# --------------------------------------------------------------------------- #
TASKS: dict = {}
TASK_LOCK = threading.Lock()

EDIT = {"ed": None, "lock": threading.Lock()}


def _normkey(p):
    return os.path.normcase(os.path.abspath(p)) if p else ""


def _get_editor(mod, game, gmerge, ref=""):
    want_mod = _normkey(mod)
    want_game = _normkey(game)
    want_ref = _normkey(ref)
    with EDIT["lock"]:
        ed = EDIT["ed"]
        if (ed is None or _normkey(ed.mod) != want_mod
                or _normkey(ed.game) != want_game or ed.merge != bool(gmerge)
                or _normkey(getattr(ed, "ref", "")) != want_ref):
            import editor as _ed
            ed = _ed.Editor(mod, game, gmerge, ref_dir=ref)
            EDIT["ed"] = ed
        return ed


def _src_mod(d):
    """返回 (实际源目录, game目录)。mod_dir 给了空串 => 只加载原版。"""
    game = str(d.get("game_dir") or DEFAULT_GAME).strip()
    mod = str(d.get("mod_dir") or "").strip()
    return (mod or game), game


def _as_bool(v):
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _edit_params(d):
    mod, game = _src_mod(d)
    gmerge = _as_bool(d.get("game_merge", False))
    ref = str(d.get("out_dir") or DEFAULT_OUT) if str(d.get("ref") or "mod") == "output" else ""
    return _get_editor(mod, game, gmerge, ref)


def new_task(kind: str) -> str:
    tid = uuid.uuid4().hex[:12]
    with TASK_LOCK:
        TASKS[tid] = {
            "id": tid, "kind": kind, "log": [], "done": False,
            "error": None, "result": None, "cancel": False, "started": time.time(),
        }
    return tid


def task_log(tid, msg):
    with TASK_LOCK:
        t = TASKS.get(tid)
        if t is not None:
            t["log"].append(str(msg))


def task_cancelled(tid):
    with TASK_LOCK:
        t = TASKS.get(tid)
        return bool(t and t["cancel"])


def task_finish(tid, result=None, error=None):
    with TASK_LOCK:
        t = TASKS.get(tid)
        if t is not None:
            t["done"] = True
            t["result"] = result
            t["error"] = error


def parse_int_list(s):
    out = []
    if s is None:
        return out
    if isinstance(s, (list, tuple)):
        items = s
    else:
        items = str(s).replace("，", ",").split(",")
    for part in items:
        part = str(part).strip()
        if part:
            try:
                out.append(int(part))
            except ValueError:
                pass
    return out


def parse_parts(s):
    d = {}
    if not s:
        return d
    if isinstance(s, dict):
        for k, v in s.items():
            try:
                d[int(k)] = int(v)
            except (ValueError, TypeError):
                pass
        return d
    for part in str(s).replace("，", ",").split(","):
        part = part.strip()
        if part and ":" in part:
            k, v = part.split(":", 1)
            try:
                d[int(k)] = int(v)
            except ValueError:
                pass
    return d


def build_config(d: dict, dry: bool) -> Config:
    g = d.get
    _game = str(g("game_dir") or DEFAULT_GAME).strip()
    _mod = str(g("mod_dir") or "").strip()
    cfg = Config(
        mod_dir=(_mod or _game),

        game_dir=_game,
        game_merge=bool(g("game_merge", False)),
        clean_output=bool(g("clean_output", True)),
        out_dir=str(g("out_dir") or DEFAULT_OUT).strip(),
        in_place=bool(g("in_place", False)),
        threshold=int(g("threshold", 200) or 0),
        target_pixels=int(g("target_pixels", 0) or 0),
        min_part_pixels=int(g("min_part_pixels", 80) or 0),
        max_parts=int(g("max_parts", 64) or 2),
        scope=str(g("scope", "all") or "all"),
        continents=parse_int_list(g("continents", [1, 2, 3])),
        include_ids=parse_int_list(g("include_ids", [])),
        exclude_ids=parse_int_list(g("exclude_ids", [85, 69, 86, 114])),
        exclude_states=parse_int_list(g("exclude_states", [])),
        only_ids=bool(g("only_ids", False)),
        parts_override=parse_parts(g("parts_override", "")),
        seed=int(g("seed", 20240101)),
        dry_run=dry,
        make_preview=bool(g("make_preview", True)),
        preview_scale=float(g("preview_scale", 1.0) or 1.0),
        repair=bool(g("repair", True)),
        canal_complete=bool(g("canal_complete", True)),
        railway_max_hops=int(g("railway_max_hops", 25) or 4),
        strip_invalid=bool(g("strip_invalid", True)),
        island_max_px=int(g("island_max_px", 5) or 5),
        grad_path=str(g("grad_path", "") or ""),
        grad_black_px=int(g("grad_black_px", 80) or 80),
        grad_white_px=int(g("grad_white_px", 250) or 250),
        grad_invert=bool(g("grad_invert", False)),
        grad_skip_white=bool(g("grad_skip_white", True)),
        split_hysteresis_ratio=float(g("split_hysteresis_ratio", 1.25) or 1.25),
        split_search_rounds=int(g("split_search_rounds", 6) or 6),
        neighbor_transition_enabled=bool(g("neighbor_transition_enabled", True)),
    )
    if os.path.isfile(cfg.grad_path) is False and cfg.grad_path:
        cfg.grad_path = ""
    return cfg


def summary_to_dict(s) -> dict:
    return {
        "detected": s.detected,
        "created": s.created,
        "files_changed": s.files_changed,
        "out_dir": s.out_dir,
        "report_csv": s.report_csv,
        "report_txt": s.report_txt,
        "preview_before": s.preview_before,
        "preview_after": s.preview_after,
        "notes": s.notes,
        "elapsed": s.elapsed,
        "dry_run": s.dry_run,
        "counts": s.counts(),
    }


# --------------------------------------------------------------------------- #
# 静态 / 基础
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.route("/cache/<path:name>")
def cache_file(name):
    return send_from_directory(CACHE_DIR, name)


@app.route("/out/<path:name>")
def out_file(name):
    return send_from_directory(DEFAULT_OUT, name)


ALLOWED_ROOTS = [ROOT, DEFAULT_MOD, DEFAULT_GAME, DEFAULT_OUT, CACHE_DIR]


@app.route("/api/file")
def api_file():
    """安全地按绝对路径提供文件 (限定在 project/mod/output/cache 根目录内)。"""
    from flask import send_file
    p = request.args.get("path", "")
    if not p:
        return jsonify({"error": "no path"}), 400
    rp = os.path.realpath(p)
    ok = any(rp == os.path.realpath(r) or rp.startswith(os.path.realpath(r) + os.sep)
             for r in ALLOWED_ROOTS)
    if not ok:
        return jsonify({"error": "forbidden"}), 403
    if not os.path.isfile(rp):
        return jsonify({"error": "not found"}), 404
    return send_file(rp)


@app.route("/api/defaults")
def api_defaults():
    return jsonify({
        "mod_dir": DEFAULT_MOD,
        "game_dir": DEFAULT_GAME,
        "out_dir": DEFAULT_OUT,
        "grad_path": _default_gradient(),
        "cache_dir": CACHE_DIR,
    })


@app.route("/api/state")
def api_state():
    game = request.args.get("game") or DEFAULT_GAME
    mod = (request.args.get("mod") or "").strip() or game
    out = request.args.get("out") or DEFAULT_OUT
    info = {
        "mod_dir": mod, "out_dir": out, "game_dir": game,
        "mod_exists": os.path.isdir(os.path.join(mod, "map")),
        "game_exists": os.path.isdir(os.path.join(game, "map")),
        "out_exists": os.path.isdir(out),
        "cache_exists": os.path.isfile(os.path.join(CACHE_DIR, "meta.json")),
        "cache_dir": CACHE_DIR,
    }
    meta_path = os.path.join(CACHE_DIR, "meta.json")
    if os.path.isfile(meta_path):
        try:
            info["cache_meta"] = json.load(open(meta_path, encoding="utf-8"))
        except Exception:
            info["cache_meta"] = None
    mp = os.path.join(mod, "definition.csv")
    if not os.path.isfile(mp):
        mp = os.path.join(game, "definition.csv")
    if os.path.isfile(mp):
        try:
            n = 0
            with open(mp, encoding="utf-8-sig", errors="replace") as f:
                for line in f:
                    if line[:1].isdigit():
                        n += 1
            info["provinces"] = n - 0
        except Exception:
            pass
    if os.path.isdir(out):
        info["out_files"] = sorted(os.listdir(out))[:40]
    return jsonify(info)


# --------------------------------------------------------------------------- #
# 建缓存
# --------------------------------------------------------------------------- #
@app.route("/api/cache/build", methods=["POST"])
def api_cache_build():
    d = request.get_json(force=True, silent=True) or {}
    mod, game = _src_mod(d)
    gmerge = _as_bool(d.get("game_merge", False))
    ref = str(d.get("out_dir") or DEFAULT_OUT) if str(d.get("ref") or "mod") == "output" else ""

    def work(tid):
        try:
            from webcache import build
            res = build(mod, CACHE_DIR, progress=lambda m: task_log(tid, m), game_dir=game,
                        game_merge=gmerge, overlay_dir=ref)
            task_finish(tid, result=res)
        except Exception:
            task_finish(tid, error=traceback.format_exc())

    tid = new_task("cache")
    threading.Thread(target=work, args=(tid,), daemon=True).start()
    return jsonify({"task": tid})


# --------------------------------------------------------------------------- #
# 分析 / 细化
# --------------------------------------------------------------------------- #
def _run_job(d, dry, tid):
    try:
        cfg = build_config(d, dry=dry)
        if not os.path.isdir(os.path.join(cfg.mod_dir, "map")):
            task_finish(tid, error="找不到 mod 的 map 目录: %s" % cfg.mod_dir)
            return
        if not cfg.out_dir:
            cfg.out_dir = os.path.join(os.path.dirname(cfg.mod_dir), "地块细化输出")
        task_log(tid, "模式: %s" % ("分析(只读)" if dry else "执行(写出)"))
        task_log(tid, "mod: %s" % cfg.mod_dir)
        if cfg.game_dir:
            task_log(tid, "原版: %s" % cfg.game_dir)
        task_log(tid, "输出: %s" % cfg.out_dir)
        s = run(cfg, progress=lambda m: task_log(tid, m),
                cancel=lambda: task_cancelled(tid))
        task_finish(tid, result=summary_to_dict(s))
    except Exception:
        task_finish(tid, error=traceback.format_exc())


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    d = request.get_json(force=True, silent=True) or {}
    tid = new_task("analyze")
    threading.Thread(target=_run_job, args=(d, True, tid), daemon=True).start()
    return jsonify({"task": tid})


@app.route("/api/run", methods=["POST"])
def api_run():
    d = request.get_json(force=True, silent=True) or {}
    tid = new_task("run")
    threading.Thread(target=_run_job, args=(d, False, tid), daemon=True).start()
    return jsonify({"task": tid})


@app.route("/api/repair", methods=["POST"])
def api_repair():
    """只修复、不拆分: 关闭渐变与强制项, 阈值设为无穷大, 只跑完整性修复。"""
    d = request.get_json(force=True, silent=True) or {}
    d2 = dict(d)
    d2["threshold"] = 2147483647
    d2["target_pixels"] = 0
    d2["grad_path"] = ""
    d2["include_ids"] = []
    d2["parts_override"] = ""
    d2["only_ids"] = False
    d2["make_preview"] = False
    d2["repair"] = True
    tid = new_task("repair")
    threading.Thread(target=_run_job, args=(d2, False, tid), daemon=True).start()
    return jsonify({"task": tid})


@app.route("/api/audit", methods=["POST"])
def api_audit():
    d = request.get_json(force=True, silent=True) or {}
    mod = str(d.get("mod_dir") or DEFAULT_MOD)
    game = str(d.get("game_dir") or DEFAULT_GAME)
    gmerge = bool(d.get("game_merge", False))

    def work(tid):
        try:
            from province_splitter.integrity import audit
            info = audit(mod, progress=lambda m: task_log(tid, m), game_dir=game, game_merge=gmerge)
            task_finish(tid, result=info)
        except Exception:
            task_finish(tid, error=traceback.format_exc())

    tid = new_task("audit")
    threading.Thread(target=work, args=(tid,), daemon=True).start()
    return jsonify({"task": tid})


# --------------------------------------------------------------------------- #
# 任务查询 / SSE / 取消
# --------------------------------------------------------------------------- #
@app.route("/api/task/<tid>")
def api_task(tid):
    with TASK_LOCK:
        t = TASKS.get(tid)
        if t is None:
            return jsonify({"error": "no such task"}), 404
        return jsonify({
            "id": t["id"], "kind": t["kind"], "log": t["log"],
            "done": t["done"], "error": t["error"], "result": t["result"],
        })


@app.route("/api/task/<tid>/stream")
def api_task_stream(tid):
    def gen():
        idx = 0
        while True:
            with TASK_LOCK:
                t = TASKS.get(tid)
                if t is None:
                    yield "data: %s\n\n" % json.dumps({"type": "error", "msg": "no such task"})
                    return
                log = list(t["log"])
                done = t["done"]
                result = t["result"]
                error = t["error"]
            while idx < len(log):
                yield "data: %s\n\n" % json.dumps({"type": "log", "msg": log[idx]})
                idx += 1
            if done:
                yield "data: %s\n\n" % json.dumps(
                    {"type": "done", "result": result, "error": error})
                return
            time.sleep(0.25)

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/api/cancel", methods=["POST"])
def api_cancel():
    d = request.get_json(force=True, silent=True) or {}
    tid = d.get("task")
    with TASK_LOCK:
        t = TASKS.get(tid)
        if t is not None:
            t["cancel"] = True
    return jsonify({"ok": True})


@app.route("/api/report")
def api_report():
    kind = request.args.get("kind", "csv")
    out = request.args.get("out") or DEFAULT_OUT
    names = {"csv": "report.csv", "txt": "report.txt", "changed": "changed_files.txt"}
    name = names.get(kind, kind)
    path = os.path.join(out, name)
    if not os.path.isfile(path):
        return jsonify({"exists": False, "path": path})
    text = open(path, encoding="utf-8-sig", errors="replace").read()
    return jsonify({"exists": True, "path": path, "text": text})


@app.route("/api/openout", methods=["POST"])
def api_openout():
    d = request.get_json(force=True, silent=True) or {}
    p = d.get("path") or DEFAULT_OUT
    if os.path.isdir(p):
        try:
            os.startfile(p)
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": False, "error": "目录不存在"})


# --------------------------------------------------------------------------- #
# 编辑 (合并/删除/改归属/大洲 + 撤销重做 + 导出)
# --------------------------------------------------------------------------- #
def _edit_ok(ed, extra=None):
    r = {"ok": True, "status": ed.status()}
    if extra:
        r.update(extra)
    return jsonify(r)


@app.route("/api/edit/status")
def api_edit_status():
    d = dict(request.args)
    try:
        ed = _edit_params(d)
        return _edit_ok(ed)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/edit/merge", methods=["POST"])
def api_edit_merge():
    d = request.get_json(force=True, silent=True) or {}
    try:
        ed = _edit_params(d)
        ed.do_merge(int(d.get("into")), [int(x) for x in (d.get("ids") or [])])
        return _edit_ok(ed)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/edit/delete", methods=["POST"])
def api_edit_delete():
    d = request.get_json(force=True, silent=True) or {}
    try:
        ed = _edit_params(d)
        into = d.get("into")
        op = ed.do_delete(int(d.get("id")), int(into) if into not in (None, "", 0) else None)
        return _edit_ok(ed, {"into": op["into"]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/edit/assign", methods=["POST"])
def api_edit_assign():
    d = request.get_json(force=True, silent=True) or {}
    try:
        ed = _edit_params(d)
        ed.do_assign(str(d.get("level") or "state"), int(d.get("pid")),
                     d.get("target"), str(d.get("op") or "add"))
        return _edit_ok(ed)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/edit/new_state", methods=["POST"])
def api_edit_new_state():
    d = request.get_json(force=True, silent=True) or {}
    try:
        ed = _edit_params(d)
        op = ed.do_new_state(
            d.get("id"), d.get("provinces") or [],
            owner=str(d.get("owner") or ""), manpower=int(d.get("manpower") or 0),
            category=str(d.get("category") or "rural"),
            infrastructure=int(d.get("infrastructure") or 0),
            industrial_complex=int(d.get("industrial_complex") or 0),
            arms_factory=int(d.get("arms_factory") or 0),
            dockyard=int(d.get("dockyard") or 0),
            buildings=d.get("buildings") or {}, resources=d.get("resources") or {},
            mlf=float(d.get("mlf") or 1.0))
        return _edit_ok(ed, {"file": op["file"], "id": op["id"]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/edit/state_data")
def api_edit_state_data():
    d = dict(request.args)
    try:
        ed = _edit_params(d)
        return jsonify({"ok": True, "data": ed.state_data(d.get("file"))})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/edit/set_state", methods=["POST"])
def api_edit_set_state():
    d = request.get_json(force=True, silent=True) or {}
    try:
        ed = _edit_params(d)
        ed.do_state_edit(d.get("file"), d.get("data") or {})
        return _edit_ok(ed)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/edit/assign_many", methods=["POST"])
def api_edit_assign_many():
    d = request.get_json(force=True, silent=True) or {}
    try:
        ed = _edit_params(d)
        ed.do_assign_many(str(d.get("level") or "state"), d.get("pids") or [],
                          d.get("target"), str(d.get("op") or "add"))
        return _edit_ok(ed)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/edit/next_id")
def api_edit_next_id():
    d = dict(request.args)
    try:
        ed = _edit_params(d)
        which = d.get("which", "state")
        txts = ed.state_txt if which == "state" else ed.sr_txt
        mx = 0
        import re as _re
        for b, txt in txts.items():
            m = _re.search(r"\bid\s*=\s*(\d+)", txt)
            if m:
                mx = max(mx, int(m.group(1)))
            else:
                mm = _re.match(r"(\d+)", b)
                if mm:
                    mx = max(mx, int(mm.group(1)))
        return jsonify({"ok": True, "next": mx + 1, "count": len(txts)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/edit/new_sr", methods=["POST"])
def api_edit_new_sr():
    d = request.get_json(force=True, silent=True) or {}
    try:
        ed = _edit_params(d)
        op = ed.do_new_sr(d.get("id"), d.get("provinces") or [])
        return _edit_ok(ed, {"file": op["file"], "id": op["id"]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/edit/assets")
def api_edit_assets():
    d = dict(request.args)
    try:
        ed = _edit_params(d)
        return jsonify({"ok": True, "assets": ed.assets()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/edit/continent", methods=["POST"])
def api_edit_continent():
    d = request.get_json(force=True, silent=True) or {}
    try:
        ed = _edit_params(d)
        ed.do_assign("continent", int(d.get("pid")), d.get("value"), "set")
        return _edit_ok(ed)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/edit/undo", methods=["POST"])
def api_edit_undo():
    d = request.get_json(force=True, silent=True) or {}
    try:
        ed = _edit_params(d)
        ed.undo()
        return _edit_ok(ed)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/edit/redo", methods=["POST"])
def api_edit_redo():
    d = request.get_json(force=True, silent=True) or {}
    try:
        ed = _edit_params(d)
        ed.redo_op()
        return _edit_ok(ed)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/edit/reset", methods=["POST"])
def api_edit_reset():
    d = request.get_json(force=True, silent=True) or {}
    try:
        ed = _edit_params(d)
        ed.reset()
        return _edit_ok(ed)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/edit/export", methods=["POST"])
def api_edit_export():
    d = request.get_json(force=True, silent=True) or {}
    out = str(d.get("out_dir") or DEFAULT_OUT)

    def work(tid):
        try:
            ed = _edit_params(d)
            res = ed.export(out, progress=lambda m: task_log(tid, m))
            task_log(tid, "重建地图缓存 ...")
            try:
                from webcache import build
                build(str(d.get("mod_dir") or DEFAULT_MOD), CACHE_DIR,
                      progress=lambda m: task_log(tid, m),
                      game_dir=str(d.get("game_dir") or DEFAULT_GAME),
                      game_merge=bool(d.get("game_merge", False)),
                      overlay_dir=out)
                task_log(tid, "缓存已更新")
            except Exception as _e:
                task_log(tid, "缓存重建失败: %s" % _e)
            task_finish(tid, result=res)
        except Exception:
            task_finish(tid, error=traceback.format_exc())

    tid = new_task("edit-export")
    threading.Thread(target=work, args=(tid,), daemon=True).start()
    return jsonify({"task": tid})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8730)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args()
    url = "http://%s:%d/" % (a.host, a.port)
    print("=" * 56)
    print("HOI4 地块细化 - Web 界面")
    print("  地址: %s" % url)
    print("  mod : %s" % DEFAULT_MOD)
    print("  out : %s" % DEFAULT_OUT)
    print("  cache: %s" % CACHE_DIR)
    print("=" * 56)
    if not a.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host=a.host, port=a.port, threaded=True, debug=False)


if __name__ == "__main__":
    main()
