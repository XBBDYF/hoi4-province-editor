# -*- coding: utf-8 -*-
"""HOI4 地块细化 - 图形界面(tkinter)。

需要 Python 带 tkinter + numpy + Pillow。Windows 上如果是官方安装的 Python 都自带 tkinter。
运行: 双击 运行GUI.bat  或  py -3.13 run_gui.py
"""
from __future__ import annotations

import os
import sys
import queue
import threading
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from province_splitter.core import Config, run

DEFAULT_MOD = r"C:\Users\Administrator\Documents\Paradox Interactive\Hearts of Iron IV\mod\Project_IRIS"
DEFAULT_GAME = r"D:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV"
DEFAULT_OUT = r"D:\地块细化\output"
DEFAULT_GRAD = r"D:\地块细化\底图密度.png"
if not os.path.isfile(DEFAULT_GRAD):
    _bundled_candidates = (
        os.path.join(HERE, "底图密度.png"),
        # 兼容旧包被某些压缩/上传工具转义后的文件名。
        os.path.join(HERE, "#U5e95#U56fe#U5bc6#U5ea6.png"),
    )
    DEFAULT_GRAD = next((p for p in _bundled_candidates if os.path.isfile(p)), "")


def parse_int_list(s):
    out = []
    for part in str(s).replace("，", ",").split(","):
        part = part.strip()
        if part:
            out.append(int(part))
    return out


def parse_parts(s):
    d = {}
    for part in str(s).replace("，", ",").split(","):
        part = part.strip()
        if part and ":" in part:
            k, v = part.split(":", 1)
            d[int(k)] = int(v)
    return d


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("HOI4 地块细化工具  |  GUI界面")
        self.geometry("1180x820")
        self.q = queue.Queue()
        self.busy = False
        self._cancel = False
        self._imgs = {}
        self._build()
        self.after(120, self._pump)

    # ---------------- UI ----------------
    def _build(self):
        pad = dict(padx=6, pady=3)

        frm = ttk.LabelFrame(self, text="路径")
        frm.pack(fill="x", **pad)
        self.mod_var = tk.StringVar(value=DEFAULT_MOD)
        self.out_var = tk.StringVar(value=DEFAULT_OUT)
        self.game_var = tk.StringVar(value=DEFAULT_GAME)
        self.gmerge_var = tk.BooleanVar(value=False)
        self.clean_var = tk.BooleanVar(value=True)
        ttk.Label(frm, text="mod 目录").grid(row=0, column=0, sticky="w", padx=4, pady=2)
        ttk.Entry(frm, textvariable=self.mod_var, width=90).grid(row=0, column=1, sticky="we", pady=2)
        ttk.Button(frm, text="浏览", command=lambda: self._pick_dir(self.mod_var)).grid(row=0, column=2, padx=4)
        ttk.Label(frm, text="输出目录").grid(row=1, column=0, sticky="w", padx=4, pady=2)
        ttk.Entry(frm, textvariable=self.out_var, width=90).grid(row=1, column=1, sticky="we", pady=2)
        ttk.Button(frm, text="浏览", command=lambda: self._pick_dir(self.out_var)).grid(row=1, column=2, padx=4)
        ttk.Label(frm, text="原版目录").grid(row=2, column=0, sticky="w", padx=4, pady=2)
        ttk.Entry(frm, textvariable=self.game_var, width=90).grid(row=2, column=1, sticky="we", pady=2)
        ttk.Button(frm, text="浏览", command=lambda: self._pick_dir(self.game_var)).grid(row=2, column=2, padx=4)
        ttk.Checkbutton(frm, text="states/战略区 与原版合并(完全模拟游戏)", variable=self.gmerge_var).grid(row=3, column=1, sticky="w", padx=4)
        ttk.Checkbutton(frm, text="细化前清空输出目录", variable=self.clean_var).grid(row=3, column=2, sticky="w", padx=4)
        frm.columnconfigure(1, weight=1)

        opt = ttk.LabelFrame(self, text="检测 / 拆分规则")
        opt.pack(fill="x", **pad)
        self.scope_var = tk.StringVar(value="all")
        ttk.Label(opt, text="检测类型").grid(row=0, column=0, sticky="w", padx=4)
        for i, (val, txt) in enumerate([("all", "全部"), ("land", "仅陆地"),
                                        ("sea", "仅海洋"), ("lake", "仅湖泊")]):
            ttk.Radiobutton(opt, text=txt, value=val, variable=self.scope_var).grid(row=0, column=1 + i, padx=2)

        self.thr_var = tk.StringVar(value="200")
        self.tgt_var = tk.StringVar(value="0")
        self.minp_var = tk.StringVar(value="80")
        self.maxp_var = tk.StringVar(value="64")
        ttk.Label(opt, text="面积阈值(px)").grid(row=1, column=0, sticky="w", padx=4)
        ttk.Entry(opt, textvariable=self.thr_var, width=8).grid(row=1, column=1, sticky="w")
        ttk.Label(opt, text="目标面积(0=阈值)").grid(row=1, column=2, sticky="e")
        ttk.Entry(opt, textvariable=self.tgt_var, width=8).grid(row=1, column=3, sticky="w")
        ttk.Label(opt, text="新块最小(px)").grid(row=1, column=4, sticky="e")
        ttk.Entry(opt, textvariable=self.minp_var, width=8).grid(row=1, column=5, sticky="w")
        ttk.Label(opt, text="最多份数").grid(row=1, column=6, sticky="e")
        ttk.Entry(opt, textvariable=self.maxp_var, width=8).grid(row=1, column=7, sticky="w")

        self.cont_var = tk.StringVar(value="1,2,3")
        self.inc_var = tk.StringVar(value="")
        self.exc_var = tk.StringVar(value="85,69,86,114")
        self.excs_var = tk.StringVar(value="")
        self.parts_var = tk.StringVar(value="")
        self.only_var = tk.BooleanVar(value=False)
        ttk.Label(opt, text="限定大洲ID(逗号)").grid(row=2, column=0, sticky="w", padx=4)
        ttk.Entry(opt, textvariable=self.cont_var, width=14).grid(row=2, column=1, columnspan=2, sticky="we")
        ttk.Label(opt, text="强制ID").grid(row=2, column=3, sticky="e")
        ttk.Entry(opt, textvariable=self.inc_var, width=14).grid(row=2, column=4, sticky="we")
        ttk.Checkbutton(opt, text="仅处理这些", variable=self.only_var).grid(row=2, column=5, sticky="w")
        ttk.Label(opt, text="排除ID").grid(row=2, column=6, sticky="e")
        ttk.Entry(opt, textvariable=self.exc_var, width=14).grid(row=2, column=7, sticky="we")
        ttk.Label(opt, text="排除州ID").grid(row=2, column=8, sticky="e")
        ttk.Entry(opt, textvariable=self.excs_var, width=14).grid(row=2, column=9, sticky="we")

        ttk.Label(opt, text="指定份数(如 225:4,300:2)").grid(row=3, column=0, sticky="w", padx=4)
        ttk.Entry(opt, textvariable=self.parts_var, width=24).grid(row=3, column=1, columnspan=3, sticky="we")

        # 渐变细化 + 孤岛阈值
        self.grad_var = tk.StringVar(value=DEFAULT_GRAD)
        self.gb_var = tk.StringVar(value="80")
        self.gw_var = tk.StringVar(value="250")
        self.ginv_var = tk.BooleanVar(value=False)
        self.gskipwhite_var = tk.BooleanVar(value=True)
        self.ntrans_var = tk.BooleanVar(value=True)
        self.imax_var = tk.StringVar(value="5")
        ttk.Label(opt, text="渐变图(越黑越小)").grid(row=4, column=0, sticky="w", padx=4)
        ttk.Entry(opt, textvariable=self.grad_var, width=30).grid(row=4, column=1, columnspan=2, sticky="we")
        ttk.Button(opt, text="浏览", command=lambda: self._pick_file(self.grad_var)).grid(row=4, column=3, padx=2)
        ttk.Label(opt, text="黑px").grid(row=4, column=4, sticky="e")
        ttk.Entry(opt, textvariable=self.gb_var, width=7).grid(row=4, column=5, sticky="w")
        ttk.Label(opt, text="白px").grid(row=4, column=6, sticky="e")
        ttk.Entry(opt, textvariable=self.gw_var, width=7).grid(row=4, column=7, sticky="w")
        ttk.Checkbutton(opt, text="反转", variable=self.ginv_var).grid(row=4, column=8, sticky="w")
        ttk.Label(opt, text="孤岛阈值(px)").grid(row=4, column=9, sticky="e")
        ttk.Entry(opt, textvariable=self.imax_var, width=5).grid(row=4, column=10, sticky="w")
        ttk.Checkbutton(opt, text="白色区域不细化", variable=self.gskipwhite_var).grid(
            row=5, column=0, columnspan=2, sticky="w", padx=4)
        ttk.Checkbutton(opt, text="未细化陆地邻区渐变(平滑边界，可关闭)",
                        variable=self.ntrans_var).grid(
            row=5, column=2, columnspan=4, sticky="w", padx=4)

        self.prev_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="生成预览图", variable=self.prev_var).grid(row=3, column=5, sticky="w")
        self.repair_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="修复战略区/州/铁路/海峡", variable=self.repair_var).grid(row=3, column=6, sticky="w")
        self.canal_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="用 rivers.bmp 辅助校验已有运河（谨慎补全）", variable=self.canal_var).grid(row=6, column=4, columnspan=3, sticky="w")
        self.hops_var = tk.StringVar(value="25")
        ttk.Label(opt, text="铁路重连跳数").grid(row=3, column=7, sticky="e")
        ttk.Entry(opt, textvariable=self.hops_var, width=6).grid(row=3, column=8, sticky="w")

        bar = ttk.Frame(self)
        bar.pack(fill="x", **pad)
        self.btn_analyze = ttk.Button(bar, text="① 分析(不写入)", command=self.on_analyze)
        self.btn_analyze.pack(side="left", padx=4)
        self.btn_run = ttk.Button(bar, text="② 开始细化", command=self.on_run)
        self.btn_run.pack(side="left", padx=4)
        self.btn_audit = ttk.Button(bar, text="③ 完整性检查", command=self.on_audit)
        self.btn_audit.pack(side="left", padx=4)
        self.btn_repair = ttk.Button(bar, text="🔧 只修复(不拆分)", command=self.on_repair)
        self.btn_repair.pack(side="left", padx=4)
        self.btn_cancel = ttk.Button(bar, text="取消", command=self.on_cancel, state="disabled")
        self.btn_cancel.pack(side="left", padx=4)
        ttk.Button(bar, text="打开输出目录", command=self.open_out).pack(side="left", padx=4)
        self.pbar = ttk.Progressbar(bar, mode="indeterminate", length=220)
        self.pbar.pack(side="right", padx=8)

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, **pad)

        # 结果表
        t1 = ttk.Frame(nb)
        nb.add(t1, text="检测结果")
        cols = ("id", "area", "type", "cont", "k", "state", "sr")
        self.tree = ttk.Treeview(t1, columns=cols, show="headings", height=16)
        for c, w, txt in zip(cols, (90, 90, 70, 110, 70, 200, 160),
                             ("省份ID", "面积", "类型", "大洲", "拆分数", "state", "战略区")):
            self.tree.heading(c, text=txt)
            self.tree.column(c, width=w, anchor="w")
        vsb = ttk.Scrollbar(t1, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        # 日志
        t2 = ttk.Frame(nb)
        nb.add(t2, text="日志")
        self.log = tk.Text(t2, wrap="word")
        self.log.pack(fill="both", expand=True)

        # 预览
        t3 = ttk.Frame(nb)
        nb.add(t3, text="预览")
        self.lbl_before = ttk.Label(t3, text="细化前(红=目标地块)", anchor="center")
        self.lbl_after = ttk.Label(t3, text="细化后", anchor="center")
        self.lbl_before.pack(side="left", fill="both", expand=True)
        self.lbl_after.pack(side="left", fill="both", expand=True)

    def _pick_dir(self, var):
        d = filedialog.askdirectory(initialdir=var.get() or "C:\\")
        if d:
            var.set(os.path.normpath(d))

    def _pick_file(self, var):
        f = filedialog.askopenfilename(filetypes=[("图片", "*.png *.bmp *.jpg *.jpeg *.tif *.tiff"), ("全部", "*.*")])
        if f:
            var.set(os.path.normpath(f))

    # ---------------- 逻辑 ----------------
    def build_cfg(self, dry):
        return Config(
            mod_dir=self.mod_var.get(),
            out_dir=self.out_var.get(),
            game_dir=self.game_var.get(),
            game_merge=self.gmerge_var.get(),
            clean_output=self.clean_var.get(),
            threshold=int(self.thr_var.get() or 0),
            target_pixels=int(self.tgt_var.get() or 0),
            min_part_pixels=int(self.minp_var.get() or 0),
            max_parts=int(self.maxp_var.get() or 2),
            scope=self.scope_var.get(),
            continents=parse_int_list(self.cont_var.get()),
            include_ids=parse_int_list(self.inc_var.get()),
            exclude_ids=parse_int_list(self.exc_var.get()),
            exclude_states=parse_int_list(self.excs_var.get()),
            only_ids=self.only_var.get(),
            parts_override=parse_parts(self.parts_var.get()),
            dry_run=dry,
            make_preview=self.prev_var.get(),
            repair=self.repair_var.get(),
            canal_complete=self.canal_var.get(),
            railway_max_hops=int(self.hops_var.get() or 4),
            grad_path=self.grad_var.get().strip(),
            grad_black_px=int(self.gb_var.get() or 80),
            grad_white_px=int(self.gw_var.get() or 250),
            grad_invert=self.ginv_var.get(),
            grad_skip_white=self.gskipwhite_var.get(),
            neighbor_transition_enabled=self.ntrans_var.get(),
            island_max_px=int(self.imax_var.get() or 5),
        )

    def logw(self, s):
        self.log.insert("end", str(s) + "\n")
        self.log.see("end")

    def _set_busy(self, b):
        self.busy = b
        state = "disabled" if b else "normal"
        self.btn_analyze.configure(state=state)
        self.btn_run.configure(state=state)
        self.btn_repair.configure(state=state)
        self.btn_cancel.configure(state="normal" if b else "disabled")
        if b:
            self.pbar.start(12)
        else:
            self.pbar.stop()

    def on_analyze(self):
        self._start(dry=True)

    def on_audit(self):
        if self.busy:
            return
        self._set_busy(True)
        self.logw("=" * 40)
        self.logw("完整性检查开始 ...")
        mod = self.mod_var.get()
        game = self.game_var.get()
        gmerge = self.gmerge_var.get()
        def work():
            try:
                from province_splitter.integrity import audit
                info = audit(mod, progress=lambda m: self.q.put(("log", m)), game_dir=game, game_merge=gmerge)
                self.q.put(("audit", info))
            except Exception:
                self.q.put(("err", traceback.format_exc()))
        threading.Thread(target=work, daemon=True).start()

    def on_run(self):
        if not messagebox.askyesno("确认", "将开始细化并写出文件到输出目录, 继续吗?"):
            return
        self._start(dry=False)

    def on_repair(self):
        """只修复、不拆分: 关闭渐变与强制项, 阈值设无穷大。"""
        if self.busy:
            return
        if not messagebox.askyesno("确认", "只修复(不拆分)并写出到输出目录, 继续吗?"):
            return
        try:
            cfg = self.build_cfg(dry=False)
        except Exception as e:
            messagebox.showerror("参数错误", str(e))
            return
        cfg.threshold = 2147483647
        cfg.target_pixels = 0
        cfg.grad_path = ""
        cfg.include_ids = []
        cfg.parts_override = {}
        cfg.only_ids = False
        cfg.make_preview = False
        cfg.repair = True
        self.tree.delete(*self.tree.get_children())
        self._cancel = False
        self._set_busy(True)
        self.logw("=" * 40)
        self.logw("只修复(不拆分) 开始")
        threading.Thread(target=self._worker, args=(cfg,), daemon=True).start()

    def on_cancel(self):
        self._cancel = True
        self.logw(">> 已请求取消 ...")

    def _start(self, dry):
        if self.busy:
            return
        try:
            cfg = self.build_cfg(dry)
        except Exception as e:
            messagebox.showerror("参数错误", str(e))
            return
        self.tree.delete(*self.tree.get_children())
        self._cancel = False
        self._set_busy(True)
        self.logw("=" * 40)
        self.logw(("分析模式" if dry else "执行模式") + " 开始")
        threading.Thread(target=self._worker, args=(cfg,), daemon=True).start()

    def _worker(self, cfg):
        try:
            s = run(cfg, progress=lambda m: self.q.put(("log", m)),
                    cancel=lambda: self._cancel)
            self.q.put(("done", s))
        except Exception:
            self.q.put(("err", traceback.format_exc()))

    def _pump(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self.logw(payload)
                elif kind == "done":
                    self._finish(payload)
                elif kind == "audit":
                    self._set_busy(False)
                    for k, v in payload.items():
                        if k.endswith("_items") or k == "adjacency_bad_rows":
                            continue
                        self.logw("  %-22s %s" % (k, v))
                    if payload.get("adjacency_bad_rows"):
                        self.logw("  海峡/连线异常示例: %s" % payload["adjacency_bad_rows"][:5])
                elif kind == "err":
                    self.logw(payload)
                    self._set_busy(False)
                    messagebox.showerror("出错", payload.splitlines()[-1] if payload else "未知错误")
        except queue.Empty:
            pass
        self.after(120, self._pump)

    def _finish(self, s):
        self._set_busy(False)
        for r in s.detected:
            self.tree.insert("", "end", values=(
                r["prov_id"], r["area"], r["type"],
                ("%s %s" % (r["continent"], r["continent_name"])).strip(),
                r["k"], r["state"], r["sr"]))
        self.logw("检测到 %d 个, 新建 %d 个, 用时 %.1fs" % (
            len(s.detected), len(s.created), s.elapsed))
        if s.report_csv:
            self.logw("报告: " + s.report_csv)
        if s.preview_before and os.path.isfile(s.preview_before):
            self._show_img(self.lbl_before, s.preview_before)
            self._show_img(self.lbl_after, s.preview_after)

    def _show_img(self, label, path):
        try:
            from PIL import Image, ImageTk
            im = Image.open(path)
            maxw = 560
            if im.width > maxw:
                im = im.resize((maxw, int(im.height * maxw / im.width)))
            tkimg = ImageTk.PhotoImage(im)
            self._imgs[path] = tkimg
            label.configure(image=tkimg, text="")
        except Exception as e:
            label.configure(text="预览失败: %s" % e)

    def open_out(self):
        p = self.out_var.get()
        if os.path.isdir(p):
            os.startfile(p)
        else:
            messagebox.showinfo("提示", "输出目录还不存在")


if __name__ == "__main__":
    App().mainloop()
