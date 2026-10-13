import socket, json, sys

host, port = "localhost", 9876
try:
    s = socket.create_connection((host, port), 10)
except Exception as e:
    print("CONNECT_FAILED:", e)
    sys.exit(1)

s.sendall(json.dumps({"type": "get_scene_info", "params": {}}).encode())
s.settimeout(15)
buf = b""
try:
    while True:
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
        try:
            json.loads(buf.decode("utf-8"))
            break
        except Exception:
            continue
except Exception as e:
    print("RECV_ERROR:", e)
s.close()
print("RESPONSE:", buf.decode("utf-8", "replace")[:1500])
