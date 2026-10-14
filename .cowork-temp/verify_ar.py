import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

base = r"D:\hoi4-province-editor\webui\i18n"
src = base + r"\phr-en-US.yml"
dst = base + r"\phr-ar-SA.yml"

import re
pat = re.compile(r'^"(.*?)":\s"(.*)"\s*$')

def parse(path):
    keys = []
    vals = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.rstrip("\n")
            if s.startswith("#") or not s.strip():
                continue
            m = pat.match(s)
            if not m:
                print("UNPARSED:", repr(s))
                continue
            k = m.group(1); v = m.group(2)
            keys.append(k); vals[k] = v
    return keys, vals

k1, v1 = parse(src)
k2, v2 = parse(dst)

s1, s2 = set(k1), set(k2)
missing = [k for k in k1 if k not in s2]
extra = [k for k in k2 if k not in s1]
dup1 = len(k1) - len(s1)
dup2 = len(k2) - len(s2)

print("src keys:", len(k1), "dst keys:", len(k2))
print("unique src:", len(s1), "unique dst:", len(s2))
print("missing (in src not dst):", len(missing), missing[:10])
print("extra (in dst not src):", len(extra), extra[:10])
print("dup src:", dup1, "dup dst:", dup2)
print("order identical:", k1 == k2)

print("--- samples ---")
for k in ["phr.面积阈值 (px)", "phr.已定位 #"]:
    print(repr(k), "=>", repr(v2.get(k)))

import yaml
try:
    d = yaml.safe_load(open(dst, encoding="utf-8"))
    print("YAML parse OK, entries:", len(d))
except Exception as e:
    print("YAML parse FAIL:", e)
