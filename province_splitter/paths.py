# -*- coding: utf-8 -*-
"""路径解析: mod 目录优先, 缺失的文件回退到原版(游戏)目录。"""
from __future__ import annotations

import os


def resolve(mod_dir: str, game_dir: str, *parts) -> str:
    """返回第一个存在的路径: <mod>/<rel> -> <game>/<rel>; 都不存在时返回 mod 路径。"""
    rel = os.path.join(*parts) if parts else ""
    m = os.path.join(mod_dir, rel) if mod_dir else ""
    if m and os.path.exists(m):
        return m
    if game_dir:
        g = os.path.join(game_dir, rel)
        if os.path.exists(g):
            return g
    return m


def _list_dir(root: str, rel: str, exts):
    if not root:
        return []
    d = os.path.join(root, rel)
    if not os.path.isdir(d):
        return []
    return [os.path.join(d, f) for f in sorted(os.listdir(d)) if f.lower().endswith(exts)]


def list_merged(mod_dir: str, game_dir: str, *parts, exts=(".txt",), merge: bool = False) -> list:
    """列出 <rel> 下的文件。

    merge=False(默认): mod 有该类文件就只用 mod 的; mod 缺失该类文件才回退用原版的
    —— 对应“mod 文件不全面时用原版补齐”, 不会把原版大量文件混进总转换 mod。
    merge=True: mod 与原版合并(同名以 mod 为准), 完全模拟 HOI4 的加载行为。
    """
    rel = os.path.join(*parts) if parts else ""
    exts = tuple(e.lower() for e in exts)
    mod_files = _list_dir(mod_dir, rel, exts)
    if mod_files and not merge:
        return mod_files
    if not merge:
        return _list_dir(game_dir, rel, exts)
    seen = set()
    out = []
    for root in (mod_dir, game_dir):
        for fp in _list_dir(root, rel, exts):
            name = os.path.basename(fp)
            if name in seen:
                continue
            seen.add(name)
            out.append(fp)
    return out


def out_rel(path: str, mod_dir: str, game_dir: str) -> str:
    """把绝对路径映射成相对 mod/game 根目录的相对路径(用于输出目录摆放)。"""
    for root in (mod_dir, game_dir):
        if not root:
            continue
        try:
            r = os.path.relpath(path, root)
        except ValueError:
            continue
        if r != os.pardir and not r.startswith(os.pardir + os.sep):
            return r
    return os.path.basename(path)
