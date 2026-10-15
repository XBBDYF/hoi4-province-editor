# -*- coding: utf-8 -*-
"""HOI4 province refinement - HTML frontend backend (Flask).

# Usage:
    py -3.13 server.py            # default http://127.0.0.1:8730
    py -3.13 server.py --port 9000

功能:
  * map preview (terrain/province/state/sr/continent/border layers, pan & zoom, hover)
  * analyze (read-only)
  * refine (write to output, live log)
  * integrity check (sr / railway / strait)
  * report viewer / output preview
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


@app.after_request
def _no_store_static(resp):
    try:
        if request.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            resp.headers["Pragma"] = "no-cache"
    except Exception:
        pass
    return resp

# --------------------------------------------------------------------------- #
# task manager (analyze / run / cache / audit run in background threads)
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
    """Return (effective source dir, game dir). Empty mod_dir means vanilla only."""
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
        items = str(s).replace("\uff0c", ",").split(",")
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
    for part in str(s).replace("\uff0c", ",").split(","):
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
# static / basic
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    try:
        with open(os.path.join(app.static_folder, "index.html"), "r", encoding="utf-8") as _f:
            _html = _f.read()
        _v = {}
        for _name in ("app.js", "style.css"):
            _p = os.path.join(app.static_folder, _name)
            _v[_name] = str(int(os.path.getmtime(_p))) if os.path.isfile(_p) else "0"
        _html = _html.replace('/static/app.js"', '/static/app.js?v=%s"' % _v["app.js"])
        _html = _html.replace('/static/style.css"', '/static/style.css?v=%s"' % _v["style.css"])
        return Response(_html, mimetype="text/html",
                        headers={"Cache-Control": "no-store, no-cache, must-revalidate"})
    except Exception:
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
    """Safely serve a file by absolute path (within project/mod/output/cache)."""
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


I18N_DIR = os.path.join(HERE, "i18n")
_I18N_MEM = {}


def _i18n_locales():
    p = os.path.join(I18N_DIR, "locales.yml")
    try:
        import yaml
        d = yaml.safe_load(open(p, encoding="utf-8")) or {}
        return (d.get("locales") or []), (str(d.get("default") or "zh-CN"))
    except Exception:
        return [], "zh-CN"


def i18n_dict(lang=None):
    """返回 (实际语言, 词典)。按 mtime 缓存。"""
    locales, default = _i18n_locales()
    codes = [str(l.get("code")) for l in locales]
    if not lang or lang not in codes:
        lang = default
    fname = "zh-CN.yml"
    for l in locales:
        if str(l.get("code")) == lang:
            fname = str(l.get("file") or fname)
    p = os.path.join(I18N_DIR, fname)
    pp = os.path.join(I18N_DIR, "phr-%s.yml" % lang)     # 可选: 短语表
    try:
        import yaml
    except Exception:
        return lang, {}
    try:
        ck = ((p, os.path.getmtime(p)),
              (pp, os.path.getmtime(pp)) if os.path.isfile(pp) else (pp, 0))
    except OSError:
        return lang, {}
    if ck in _I18N_MEM:
        return lang, _I18N_MEM[ck]
    try:
        d = yaml.safe_load(open(p, encoding="utf-8")) or {}
    except Exception:
        d = {}
    if os.path.isfile(pp):
        try:
            d2 = yaml.safe_load(open(pp, encoding="utf-8")) or {}
            merged = dict(d2)
            merged.update(d)          # 语言文件里的键优先于短语表
            d = merged
        except Exception:
            pass
    _I18N_MEM.clear()
    _I18N_MEM[ck] = d
    return lang, d


@app.route("/api/i18n")
def api_i18n():
    locales, default = _i18n_locales()
    lang, d = i18n_dict((request.args.get("lang") or "").strip() or None)
    return jsonify({"ok": True, "lang": lang, "default": default,
                    "locales": locales, "dict": d})


@app.route("/api/deps")
def api_deps():
    import importlib.util
    import sys as _sys

    def _has(m):
        try:
            return importlib.util.find_spec(m) is not None
        except Exception:
            return False
    return jsonify({"ok": True, "python": _sys.version.split()[0],
                    "flask": _has("flask"), "numpy": _has("numpy"), "pillow": _has("PIL"),
                    "yaml": _has("yaml")})


@app.route("/api/deps/install", methods=["POST"])
def api_deps_install():
    import subprocess
    import sys as _sys
    d = request.get_json(force=True, silent=True) or {}
    mirror = (d.get("mirror") or "").strip()
    pkgs = d.get("packages") or ["numpy", "pillow"]
    pkgs = [str(p) for p in pkgs if str(p).strip()]
    if not pkgs:
        return jsonify({"ok": False, "error": "没有要安装的库"})
    cmd = [_sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--upgrade"] + pkgs
    if mirror:
        cmd += ["-i", mirror]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        return jsonify({"ok": p.returncode == 0, "code": p.returncode,
                        "cmd": " ".join(cmd),
                        "out": (p.stdout or "")[-4000:], "err": (p.stderr or "")[-4000:]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


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
# build cache
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
            try:
                with EDIT["lock"]:
                    _had = EDIT.get("ed") is not None
                    EDIT["ed"] = None                 # 重建缓存 = 丢掉编辑器里未导出的改动
                if _had:
                    task_log(tid, "[提示] 已清除编辑器里未导出的改动(编辑缓存), 输出目录不受影响")
            except Exception:
                pass
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
            task_finish(tid, error="mod map dir not found: %s" % cfg.mod_dir)
            return
        if not cfg.out_dir:
            cfg.out_dir = os.path.join(os.path.dirname(cfg.mod_dir), "地块细化输出")
        task_log(tid, "mode: %s" % ("analyze(read-only)" if dry else "run(write)"))
        task_log(tid, "mod: %s" % cfg.mod_dir)
        if cfg.game_dir:
            task_log(tid, "原版: %s" % cfg.game_dir)
        task_log(tid, "输出: %s" % cfg.out_dir)
        if getattr(cfg, "in_place", False):
            task_log(tid, "[注意] 勾选了『直接改原 mod (危险)』-> 结果将直接写入: %s（不写输出目录，且不会备份）" % cfg.mod_dir)
        try:
            _src = os.path.normcase(os.path.abspath(cfg.mod_dir or ""))
            _out = os.path.normcase(os.path.abspath(cfg.out_dir or ""))
            if _src and _out == _src:
                task_log(tid, "[注意] 输出目录与数据来源目录相同 -> 会直接覆盖源目录(不会备份)")
            elif _src and _out.startswith(_src + os.sep):
                task_log(tid, "[注意] 输出目录在数据来源目录内部 -> 会改写其中的文件")
        except Exception:
            pass
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
    """Repair only (no splitting): disable gradient/forced ids, huge threshold."""
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
# task query / SSE / cancel
# --------------------------------------------------------------------------- #
@app.route("/api/tasks")
def api_tasks():
    out = []
    with TASK_LOCK:
        for tid, t in TASKS.items():
            out.append({"id": tid, "kind": t.get("kind"), "done": bool(t.get("done")),
                        "error": (t.get("error") or "")[:400],
                        "elapsed": round(time.time() - t.get("started", time.time()), 1),
                        "lines": len(t.get("log") or []),
                        "last": (t.get("log") or [""])[-1]})
    out.sort(key=lambda x: (x["done"], -x["elapsed"]))
    return jsonify({"ok": True, "count": len(out), "running": len([x for x in out if not x["done"]]), "tasks": out})


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
        tick = 0
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
            tick += 1
            if tick % 20 == 0:      # 每 ~5s 一个心跳, 让前端知道服务还活着
                yield "data: %s\n\n" % json.dumps({"type": "ping"})
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
    return jsonify({"ok": False, "error": "directory not found"})


# --------------------------------------------------------------------------- #
# edit (merge/delete/assign/continent + undo/redo + export)
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


@app.route("/api/edit/rail", methods=["POST"])
def api_edit_rail():
    d = request.get_json(force=True, silent=True) or {}
    try:
        ed = _edit_params(d)
        ed.do_rail(int(d.get("a")), int(d.get("b")), str(d.get("op") or "add"), int(d.get("level") or 1))
        return _edit_ok(ed)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/edit/rail_data")
def api_edit_rail_data():
    d = dict(request.args)
    try:
        ed = _edit_params(d)
        return jsonify({"ok": True, "data": ed.rail_data()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/edit/rail_level", methods=["POST"])
def api_edit_rail_level():
    d = request.get_json(force=True, silent=True) or {}
    try:
        ed = _edit_params(d)
        ed.do_rail_level(int(d.get("i")), int(d.get("level") or 1))
        return _edit_ok(ed)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/edit/rail_rm", methods=["POST"])
def api_edit_rail_rm():
    d = request.get_json(force=True, silent=True) or {}
    try:
        ed = _edit_params(d)
        ed.do_rail_rm(int(d.get("i")))
        return _edit_ok(ed)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/edit/supply", methods=["POST"])
def api_edit_supply():
    d = request.get_json(force=True, silent=True) or {}
    try:
        ed = _edit_params(d)
        ed.do_supply(int(d.get("pid")), str(d.get("op") or "add"))
        return _edit_ok(ed)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/edit/building", methods=["POST"])
def api_edit_building():
    d = request.get_json(force=True, silent=True) or {}
    try:
        ed = _edit_params(d)
        ed.do_building(int(d.get("pid")), d.get("key"), d.get("wx"), d.get("wy"), str(d.get("op") or "add"))
        return _edit_ok(ed)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/edit/terrain_data")
def api_edit_terrain_data():
    d = dict(request.args)
    try:
        ed = _edit_params(d)
        return jsonify({"ok": True, "data": ed.terrain_data()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/edit/export_terrain", methods=["POST"])
def api_edit_export_terrain():
    d = request.get_json(force=True, silent=True) or {}
    try:
        ed = _edit_params(d)
        out = str(d.get("out_dir") or DEFAULT_OUT)
        res = ed.export_terrain_bmp(out)
        return jsonify({"ok": True, "result": res})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/edit/terrain_auto", methods=["POST"])
def api_edit_terrain_auto():
    d = request.get_json(force=True, silent=True) or {}
    try:
        ed = _edit_params(d)
        res = ed.do_terrain_auto()
        return jsonify({"ok": True, "status": ed.status(), "count": res.get("count")})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/edit/sr_data")
def api_edit_sr_data():
    d = dict(request.args)
    try:
        ed = _edit_params(d)
        return jsonify({"ok": True, "data": ed.sr_data(d.get("file"))})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


@app.route("/api/edit/set_sr", methods=["POST"])
def api_edit_set_sr():
    d = request.get_json(force=True, silent=True) or {}
    try:
        ed = _edit_params(d)
        ed.do_sr_edit(d.get("file"), d.get("name"), d.get("weather"))
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
        r = ed.next_free_id(which)
        cnt = len(ed.recs)
        if which == "state":
            cnt = len(ed.state_txt)
        elif which == "sr":
            cnt = len(ed.sr_txt)
        return jsonify({"ok": True, "next": r["next"], "max": r["max"],
                        "reused": bool(r.get("reused")), "count": cnt})
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
                task_log(tid, "cache updated")
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
