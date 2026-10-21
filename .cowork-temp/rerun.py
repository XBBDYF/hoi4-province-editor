import json, time, urllib.request

BASE = "http://127.0.0.1:8730"
body = {
    "scope": "land", "threshold": 160, "target_pixels": 160,
    "min_part_pixels": 80, "max_parts": 64, "continents": "1",
    "include_ids": "", "exclude_ids": "85,69,86,114", "exclude_states": "",
    "parts_override": "", "seed": 20240101, "only_ids": False,
    "grad_path": "", "grad_black_px": 80, "grad_white_px": 250,
    "grad_skip_white": True, "grad_invert": False,
    "split_hysteresis_ratio": 1.25, "split_search_rounds": 6,
    "neighbor_transition_enabled": True,
    "repair": True, "canal_complete": True, "game_merge": False,
    "clean_output": True, "railway_max_hops": 25, "strip_invalid": True,
    "island_max_px": 5, "make_preview": False, "preview_scale": 1.0,
    "in_place": False,
    "mod_dir": "",
    "game_dir": r"D:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV",
    "out_dir": r"D:\hoi4-province-editor\output",
    "ref": "mod",
}
req = urllib.request.Request(BASE + "/api/run", data=json.dumps(body).encode(),
                             headers={"Content-Type": "application/json"})
tid = json.load(urllib.request.urlopen(req, timeout=20))["task"]
print("task", tid, flush=True)

last = 0
while True:
    time.sleep(2)
    d = json.load(urllib.request.urlopen(BASE + "/api/task/" + tid, timeout=20))
    for line in d["log"][last:]:
        print(line, flush=True)
    last = len(d["log"])
    if d["done"]:
        print("DONE error=", d["error"], flush=True)
        break
