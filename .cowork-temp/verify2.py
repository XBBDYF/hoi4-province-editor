# -*- coding: utf-8 -*-
import io, os, re, collections

ROOT = r"D:\hoi4-province-editor\output"
GAME = r"D:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV"

def read(p):
    return io.open(p, encoding="utf-8", errors="replace").read()

dp_out = os.path.join(ROOT, "map", "definition.csv")
raw = open(dp_out, "rb").read()
print("output definition.csv: CRLF=%d LF=%d" % (raw.count(b"\r\n"), raw.count(b"\n")))

def ids_of(raw_bytes):
    s = set()
    for ln in re.split(r"\r\n|\n", raw_bytes.decode("utf-8", "replace")):
        p = ln.split(";")
        if len(p) >= 8 and p[0].isdigit():
            s.add(int(p[0]))
    return s

out_ids = ids_of(raw)
orig_ids = ids_of(open(os.path.join(GAME, "map", "definition.csv"), "rb").read())
print("output ids=%d  vanilla ids=%d  new ids=%d" % (len(out_ids), len(orig_ids), len(out_ids - orig_ids)))

def scan(dirpath, label):
    files = sorted(f for f in os.listdir(dirpath) if f.lower().endswith(".txt"))
    empty, total, uniq, missing = [], 0, set(), collections.Counter()
    for fn in files:
        t = read(os.path.join(dirpath, fn))
        m = re.search(r"provinces\s*=\s*\{(.*?)\}", t, re.S)
        ids = [int(x) for x in re.findall(r"\d+", m.group(1))] if m else []
        if not ids:
            empty.append(fn)
        total += len(ids)
        uniq.update(ids)
        for i in ids:
            if i not in out_ids:
                missing[i] += 1
    print("[%s] files=%d empty=%d entries=%d uniq=%d missing_from_definition=%d"
          % (label, len(files), len(empty), total, len(uniq), len(missing)))
    if empty:
        print("   empty sample:", empty[:6])
    if missing:
        print("   missing sample:", list(missing)[:8])
    return uniq

st_ids = scan(os.path.join(ROOT, "history", "states"), "states")
sr_ids = scan(os.path.join(ROOT, "map", "strategicregions"), "strategic-regions")

new_ids = out_ids - orig_ids
print("\nnew provinces: %d" % len(new_ids))
print("  in states      : %d / %d" % (len(new_ids & st_ids), len(new_ids)))
print("  in stratregion : %d / %d" % (len(new_ids & sr_ids), len(new_ids)))
lost_st = new_ids - st_ids
lost_sr = new_ids - sr_ids
if lost_st:
    print("  NOT in any state (sample):", sorted(lost_st)[:10])
if lost_sr:
    print("  NOT in any stratregion (sample):", sorted(lost_sr)[:10])
