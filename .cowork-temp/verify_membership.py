# -*- coding: utf-8 -*-
import io, os, re, collections

ROOT = r"D:\hoi4-province-editor\output"

def read(p):
    return io.open(p, encoding="utf-8", errors="replace").read()

# --- definition.csv ---
dp = os.path.join(ROOT, "map", "definition.csv")
raw = open(dp, "rb").read()
print("definition.csv: CRLF=%d LF=%d" % (raw.count(b"\r\n"), raw.count(b"\n")))
valid = set()
for ln in re.split(r"\r\n|\n", raw.decode("utf-8", "replace")):
    p = ln.split(";")
    if len(p) >= 8 and p[0].isdigit():
        valid.add(int(p[0]))
print("definition 记录数:", len(valid))

def scan(dirpath, label):
    files = sorted(f for f in os.listdir(dirpath) if f.lower().endswith(".txt"))
    empty = []
    total = 0
    uniq = set()
    missing = collections.Counter()
    per_counts = []
    for fn in files:
        t = read(os.path.join(dirpath, fn))
        # provinces = { ... } / provinces={ ... }
        m = re.search(r"provinces\s*=\s*\{(.*?)\}", t, re.S)
        ids = []
        if m:
            ids = [int(x) for x in re.findall(r"\d+", m.group(1))]
        per_counts.append((fn, len(ids)))
        if not ids:
            empty.append(fn)
        total += len(ids)
        uniq.update(ids)
        for i in ids:
            if i not in valid:
                missing[i] += 1
    print("\n[%s] 文件 %d 个, 空文件 %d 个, 成员条目 %d, 唯一ID %d"
          % (label, len(files), len(empty), total, len(uniq)))
    if empty:
        print("  空文件示例:", empty[:8])
    if missing:
        print("  不在 definition 里的ID数:", len(missing), "示例:", list(missing)[:10])
    else:
        print("  所有成员ID都在 definition 中 ✔")
    return per_counts

st = scan(os.path.join(ROOT, "history", "states"), "states")
sr = scan(os.path.join(ROOT, "map", "strategicregions"), "strategicregions")

print("\n州文件成员数最多的 5 个:", sorted(st, key=lambda x: -x[1])[:5])
print("州文件为空的示例:", [x for x in st if x[1] == 0][:5])
