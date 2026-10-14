# -*- coding: utf-8 -*-
"""HOI4 地块编辑器 · 启动器

功能:
1. 自动检测依赖库 (flask / numpy / Pillow);
2. 缺少时可一键安装, 并**可选择下载源**(官方 / 清华 / 阿里 / 腾讯 / 中科大);
3. 启动 server.py 并自动打开浏览器。

用法:
    py -3.13 webui_launch.py                正常启动(缺库会询问)
    py -3.13 webui_launch.py --check        只检测依赖, 不启动
    py -3.13 webui_launch.py --yes          缺库直接装(默认源), 不询问
    py -3.13 webui_launch.py --mirror 2     指定下载源编号(见下表)
    py -3.13 webui_launch.py --port 8730 --no-browser
"""
import os
import sys
import argparse
import importlib.util
import subprocess
import webbrowser

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))

# (显示名, pip -i 的地址; None=官方)
MIRRORS = [
    ("官方 PyPI (pypi.org)", None),
    ("清华 TUNA", "https://pypi.tuna.tsinghua.edu.cn/simple"),
    ("阿里云", "https://mirrors.aliyun.com/pypi/simple/"),
    ("腾讯云", "https://mirrors.cloud.tencent.com/pypi/simple"),
    ("中科大 USTC", "https://pypi.mirrors.ustc.edu.cn/simple/"),
]

# (import 名, pip 包名)
DEPS = [
    ("flask", "flask"),
    ("numpy", "numpy"),
    ("PIL", "pillow"),
]


def has(mod):
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


def missing():
    return [(mod, pkg) for mod, pkg in DEPS if not has(mod)]


def print_banner():
    print("=" * 58)
    print("   HOI4 地块细化 / 编辑器   ·   Web 控制台启动器")
    print("=" * 58)


def print_mirrors():
    for i, (name, _u) in enumerate(MIRRORS, 1):
        print("    %d) %s" % (i, name))


def do_install(pkgs, index):
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--upgrade"] + pkgs
    if index:
        cmd += ["-i", index]
    print("\n> " + " ".join(cmd))
    try:
        rc = subprocess.call(cmd)
    except Exception as e:
        print("[错误] 无法运行 pip: %s" % e)
        return False
    return rc == 0


def install_with_fallback(pkgs, prefer=None):
    """按顺序尝试镜像源, 任一成功即返回。"""
    order = []
    if prefer is not None and 0 <= prefer < len(MIRRORS):
        order.append(MIRRORS[prefer])
    for m in MIRRORS:
        if m not in order:
            order.append(m)
    for name, idx in order:
        print("\n-- 尝试下载源: %s --" % name)
        if do_install(pkgs, idx):
            print("[成功] 使用 %s 安装完成。" % name)
            return True
    return False


def _probe(port):
    """返回 'ok'(已在运行且响应) / 'occupied'(端口被占但不响应) / 'free'"""
    import socket
    s = socket.socket()
    s.settimeout(0.6)
    try:
        listening = (s.connect_ex(("127.0.0.1", int(port))) == 0)
    finally:
        s.close()
    if not listening:
        return "free"
    try:
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:%d/api/state" % int(port), timeout=2) as r:
            if getattr(r, "status", 0) == 200:
                return "ok"
    except Exception:
        pass
    return "occupied"


def _port_owner_pids(port):
    import re
    pids = set()
    try:
        out = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return pids
    for ln in out.splitlines():
        if (":%d" % int(port)) in ln and "LISTENING" in ln.upper():
            m = re.search(r"(\d+)\s*$", ln.strip())
            if m:
                pids.add(m.group(1))
    return pids


def _kill_port_owner(port):
    pids = _port_owner_pids(port)
    if not pids:
        print("  没找到占用 %d 的进程(可能需要管理员权限查看)。" % int(port))
        return False
    ok = False
    for pid in sorted(pids):
        print("  结束占用端口 %d 的进程 PID=%s ..." % (int(port), pid))
        try:
            if subprocess.call(["taskkill", "/PID", str(pid), "/F"]) == 0:
                ok = True
        except Exception as e:
            print("    失败: %s" % e)
    return ok


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--check", action="store_true", help="只检测依赖, 不启动服务器")
    ap.add_argument("--yes", action="store_true", help="缺库时直接安装, 不询问")
    ap.add_argument("--mirror", type=int, default=None, help="下载源编号 (见列表), 默认官方")
    ap.add_argument("--port", type=int, default=8730)
    ap.add_argument("--no-browser", action="store_true")
    args, extra = ap.parse_known_args()

    print_banner()
    print("\nPython : %s" % sys.version.split()[0])
    print("路径   : %s" % HERE)

    miss = missing()
    print("\n[依赖检测]")
    for mod, pkg in DEPS:
        print("   %-8s %s" % (mod, "OK" if has(mod) else "缺失"))
    if not miss:
        print("   全部就绪 ✅")
    else:
        print("   缺少: %s" % ", ".join(m[0] for m in miss))

    if args.check:
        return 0 if not miss else 2

    if miss:
        pkgs = [pkg for _mod, pkg in miss]
        if args.yes:
            ok = install_with_fallback(pkgs, args.mirror)
        else:
            print("\n需要安装: %s" % ", ".join(pkgs))
            print("请选择下载源 (回车=官方 PyPI):")
            print_mirrors()
            try:
                ans = input("选择源 [1-%d]: " % len(MIRRORS)).strip()
            except EOFError:
                ans = ""
            prefer = None
            if ans.isdigit() and 1 <= int(ans) <= len(MIRRORS):
                prefer = int(ans) - 1
            try:
                yn = input("现在安装吗? [Y/n]: ").strip().lower()
            except EOFError:
                yn = ""
            if yn in ("n", "no", "0", "否"):
                print("[跳过] 未安装依赖, 可能无法启动 Web 界面。")
            else:
                ok = install_with_fallback(pkgs, prefer)
            if not ok:
                print("\n[失败] 依赖仍未装好。可手动安装:")
                print("   %s -m pip install numpy pillow -i https://pypi.tuna.tsinghua.edu.cn/simple"
                      % sys.executable)
        still = missing()
        if still:
            print("\n[警告] 仍然缺少: %s，服务器可能启动失败。" % ", ".join(m[0] for m in still))

    st = _probe(args.port)
    if st == "ok":
        print("\n[提示] 检测到 Web 服务已经在运行 (端口 %d)。" % args.port)
        print("       不会重复启动；直接打开浏览器即可。")
        print("       如需重启：关掉原来的服务窗口(或结束后台 python 进程)后再运行本程序。")
        try:
            webbrowser.open("http://127.0.0.1:%d/" % args.port)
        except Exception:
            pass
        try:
            input("\n按回车退出...")
        except Exception:
            pass
        return 0
    if st == "occupied":
        print("\n[警告] 端口 %d 已被占用，但服务没有响应(可能是僵死的旧进程)。" % args.port)
        if args.yes:
            _kill_port_owner(args.port)
        else:
            try:
                ans = input("是否结束占用该端口的进程并重新启动? [Y/n]: ").strip().lower()
            except EOFError:
                ans = ""
            if ans in ("", "y", "yes", "是"):
                if not _kill_port_owner(args.port):
                    print("[退出] 无法释放端口，请手动结束占用进程后再试。")
                    return 1
            else:
                print("[退出] 端口被占用，未启动。")
                return 1

    print("\n" + "-" * 58)
    print("  正在启动 Web 服务 (端口 %d) …" % args.port)
    print("  浏览器会自动打开:  http://127.0.0.1:%d/" % args.port)
    print("  关闭本窗口 = 停止服务")
    print("-" * 58 + "\n")

    os.chdir(HERE)
    cmd = [sys.executable, "server.py", "--port", str(args.port)]
    if args.no_browser:
        cmd.append("--no-browser")
    cmd += extra
    try:
        return subprocess.call(cmd)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
