# -*- coding: utf-8 -*-
"""HOI4 地块细化 - 命令行入口。

示例:
  python -m province_splitter.cli --mod "C:\\...\\Project_IRIS" --out "D:\\地块细化\\output" \
      --threshold 3000 --scope land --analyze

  # 真正执行(指定大洲 + 指定ID)
  python -m province_splitter.cli --mod "...Project_IRIS" --out "D:\\地块细化\\output" \
      --threshold 4000 --scope all --continents 8,11 --include 225
"""
from __future__ import annotations

import argparse
import os
import sys

from .core import Config, run


def parse_int_list(s: str):
    out = []
    if not s:
        return out
    for part in str(s).replace("，", ",").split(","):
        part = part.strip()
        if part:
            out.append(int(part))
    return out


def parse_parts(s: str):
    d = {}
    if not s:
        return d
    for part in str(s).replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            k, v = part.split(":", 1)
            d[int(k)] = int(v)
    return d


def build_config(a) -> Config:
    cfg = Config(
        mod_dir=os.path.abspath(a.mod),
        game_dir=os.path.abspath(a.game) if a.game else "",
        game_merge=bool(a.game_merge),
        out_dir=os.path.abspath(a.out) if a.out else "",
        in_place=bool(a.in_place),
        threshold=a.threshold,
        target_pixels=a.target,
        min_part_pixels=a.min_part,
        max_parts=a.max_parts,
        scope=a.scope,
        continents=parse_int_list(a.continents),
        include_ids=parse_int_list(a.include),
        exclude_ids=parse_int_list(a.exclude),
        exclude_states=parse_int_list(a.exclude_states),
        only_ids=bool(a.only),
        parts_override=parse_parts(a.parts),
        seed=a.seed,
        dry_run=bool(a.analyze),
        make_preview=not a.no_preview,
        preview_scale=a.scale,
        repair=not a.no_repair,
        railway_max_hops=a.railway_hops,
        strip_invalid=not a.keep_invalid,
        island_max_px=a.island_max,
        canal_complete=not a.no_canal_complete,
        grad_path=a.gradient,
        grad_black_px=a.grad_black,
        grad_white_px=a.grad_white,
        grad_invert=bool(a.grad_invert),
        grad_skip_white=not bool(a.grad_allow_white),
        neighbor_transition_enabled=not bool(a.no_neighbor_transition),
        split_hysteresis_ratio=a.split_hysteresis,
        split_search_rounds=a.split_search_rounds,
    )
    return cfg


def main(argv=None):
    ap = argparse.ArgumentParser(description="HOI4 地块(province)细化工具")
    ap.add_argument("--mod", required=True, help="mod 根目录(包含 map/ 与 history/)")
    ap.add_argument("--game", default="", help="原版(游戏)根目录: mod 里缺失的文件(如全量 states/strategicregions/provinces.bmp)会从这里回退读取")
    ap.add_argument("--game-merge", action="store_true", help="states/战略区 与原版合并(完全模拟游戏加载); 默认仅在 mod 缺失该类文件时回退")
    ap.add_argument("--out", default="", help="输出目录(默认 <mod>/../地块细化输出)")
    ap.add_argument("--threshold", type=int, default=200, help="像素面积上限, 超过则细化")
    ap.add_argument("--target", type=int, default=0, help="目标每块面积(0=用 threshold)")
    ap.add_argument("--min-part", type=int, default=80, help="新地块最小面积")
    ap.add_argument("--max-parts", type=int, default=64, help="单块最多切几份")
    ap.add_argument("--scope", choices=["all", "land", "sea", "lake"], default="all",
                    help="检测类型范围")
    ap.add_argument("--continents", default="1,2,3", help="限定大洲ID, 逗号分隔")
    ap.add_argument("--include", default="", help="强制细化的ID, 逗号分隔")
    ap.add_argument("--only", action="store_true", help="只处理 --include 中列出的ID")
    ap.add_argument("--exclude", default="85,69,86,114", help="排除的地块(province) ID, 逗号分隔")
    ap.add_argument("--exclude-states", default="", help="排除的州(state/省份) ID, 逗号分隔(其下地块全部跳过)")
    ap.add_argument("--parts", default="", help="指定拆分份数, 如 225:4,300:2")
    ap.add_argument("--seed", type=int, default=20240101)
    _tool_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    _gradient_candidates = (
        r"D:\地块细化\底图密度.png",
        os.path.join(_tool_dir, "底图密度.png"),
        os.path.join(_tool_dir, "#U5e95#U56fe#U5bc6#U5ea6.png"),
    )
    _default_gradient = next((os.path.abspath(p) for p in _gradient_candidates if os.path.isfile(p)),
                             os.path.join(_tool_dir, "底图密度.png"))
    ap.add_argument("--gradient", default=_default_gradient, help="渐变灰度图路径(越黑拆得越小；纯白区域默认跳过)")
    ap.add_argument("--grad-black", type=int, default=80, help="纯黑处目标地块像素")
    ap.add_argument("--grad-white", type=int, default=250, help="纯白处目标地块像素")
    ap.add_argument("--grad-invert", action="store_true", help="反转渐变图(黑=大, 白=小)")
    ap.add_argument("--grad-allow-white", action="store_true",
                    help="允许纯白区域按 --grad-white 目标面积参与细化(关闭白色跳过规则)")
    ap.add_argument("--no-neighbor-transition", action="store_true",
                    help="关闭未细化陆地邻区的目标面积渐变")
    ap.add_argument("--split-hysteresis", type=float, default=1.25,
                    help="有密度图时，实际面积需超过目标面积的该倍数才自动细化(默认 1.25)")
    ap.add_argument("--split-search-rounds", type=int, default=6,
                    help="每块进行多少轮不同种子的候选切割搜索(默认 6；超大地块自动降轮数)")
    ap.add_argument("--island-max", type=int, default=5,
                    help="只修复不大于该像素的孤立碎块(群岛多块保留)")
    ap.add_argument("--scale", type=float, default=1.0, help="预览图缩放(1.0=原尺寸)")
    ap.add_argument("--no-preview", action="store_true")
    ap.add_argument("--analyze", action="store_true", help="只分析不写入")
    ap.add_argument("--audit", action="store_true", help="只检查地图完整性(战略区/州/铁路/海峡/运河), 不拆分")
    ap.add_argument("--no-repair", action="store_true", help="拆分后不做战略区/铁路/海峡修复")
    ap.add_argument("--railway-hops", type=int, default=25, help="铁路断点重连最大插入跳数")
    ap.add_argument("--keep-invalid", action="store_true", help="保留战略区/州中不存在的省份ID(默认会清除)")
    ap.add_argument("--no-canal-complete", action="store_true",
                    help="不自动补全缺失的运河连线(仅补全能与明确 CANAL 规则唯一匹配的连线)")
    ap.add_argument("--in-place", action="store_true", help="(谨慎)直接改原mod")
    ap.add_argument("--top", type=int, default=30, help="打印前N个")
    a = ap.parse_args(argv)

    if a.audit:
        from .integrity import audit
        info = audit(os.path.abspath(a.mod), progress=lambda m: print(m, flush=True),
                     game_dir=os.path.abspath(a.game) if a.game else "",
                     game_merge=bool(a.game_merge))
        print("\n==== 完整性检查 ====")
        for k, v in info.items():
            if k.endswith("_items") or k == "adjacency_bad_rows":
                continue
            print("  %-24s %s" % (k, v))
        if info["sr_dup_items"]:
            print("  战略区重复示例:", info["sr_dup_items"][:5])
        if info["state_dup_items"]:
            print("  州重复示例:", info["state_dup_items"][:5])
        if info["adjacency_bad_rows"]:
            print("  海峡/连线异常示例:", info["adjacency_bad_rows"][:5])
        return 0

    cfg = build_config(a)
    if not cfg.out_dir:
        cfg.out_dir = os.path.join(os.path.dirname(cfg.mod_dir), "地块细化输出")

    if not os.path.isdir(os.path.join(cfg.mod_dir, "map")):
        print("错误: %s 下找不到 map 目录" % cfg.mod_dir)
        return 2
    if cfg.in_place:
        print("⚠ in-place 模式会直接覆盖原 mod, 请先自行备份!")

    def log(m):
        print(m, flush=True)

    summary = run(cfg, progress=log)

    print("\n==== 结果 ====")
    for r in summary.detected[: a.top]:
        print("  #%-6d %-5s %-8s %6d px -> %d 块  state=%s sr=%s" % (
            r["prov_id"], r["hex"], r["type"], r["area"], r["k"], r["state"], r["sr"]))
    print("检测 %d, 新建 %d, 用时 %.1fs" % (
        len(summary.detected), len(summary.created), summary.elapsed))
    if summary.report_csv:
        print("报告: %s" % summary.report_csv)
    if summary.preview_before:
        print("预览: %s / %s" % (summary.preview_before, summary.preview_after))

    # 输出到文件
    if cfg.dry_run:
        os.makedirs(cfg.out_dir, exist_ok=True)
        p = os.path.join(cfg.out_dir, "analyze.csv")
        import csv
        with open(p, "w", newline="", encoding="utf-8-sig") as f:
            wr = csv.writer(f)
            wr.writerow(["省份ID", "颜色", "面积", "建议拆分数", "类型", "大洲", "大洲名", "state", "战略区"])
            for r in summary.detected:
                wr.writerow([r["prov_id"], r["hex"], r["area"], r["k"], r["type"],
                             r["continent"], r["continent_name"], r["state"], r["sr"]])
        print("分析清单: %s" % p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
