import json, subprocess, sys, os

uvx = os.path.expanduser(r"~\.local\bin\uvx.exe")
p = subprocess.Popen([uvx, "mcp-for-blender"], stdin=subprocess.PIPE,
                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)

def send(obj):
    p.stdin.write(json.dumps(obj) + "\n")
    p.stdin.flush()

def read_msg():
    while True:
        line = p.stdout.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except Exception:
            continue

send({"jsonrpc":"2.0","id":1,"method":"initialize","params":{
    "protocolVersion":"2024-11-05","capabilities":{},
    "clientInfo":{"name":"probe","version":"1.0"}}})
print("INIT:", read_msg() is not None)
send({"jsonrpc":"2.0","method":"notifications/initialized"})
send({"jsonrpc":"2.0","id":2,"method":"tools/list"})
resp = read_msg()
tools = (resp or {}).get("result", {}).get("tools", [])
print("TOOL_COUNT:", len(tools))
for t in tools:
    print("-", t.get("name"), "::", (t.get("description") or "").replace("\n", " ")[:120])
p.terminate()
