import io, os
needle = "\\r\\n"   # 源码里的 4 个字符: 反斜杠 r 反斜杠 n
for fn in ("core.py", "integrity.py", "canals.py", "cli.py"):
    p = os.path.join("province_splitter", fn)
    if not os.path.isfile(p):
        continue
    for i, l in enumerate(io.open(p, encoding="utf-8").read().splitlines(), 1):
        if needle in l:
            print("%s:%d: %s" % (fn, i, l.strip()))
