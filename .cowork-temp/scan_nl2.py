import io, os
needle = "\\r\\n"
for fn in ("webui/editor.py", "webui/webcache.py", "province_splitter/cli.py",
           "province_splitter/integrity.py", "province_splitter/canals.py"):
    if not os.path.isfile(fn):
        continue
    for i, l in enumerate(io.open(fn, encoding="utf-8").read().splitlines(), 1):
        if needle in l or "def read_definition" in l:
            print("%s:%d: %s" % (fn, i, l.strip()))
