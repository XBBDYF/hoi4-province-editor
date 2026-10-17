import io, re
base = r"D:\hoi4-province-editor\webui\static"
s = io.open(base + r"\app.js", encoding="utf-8").read()
out = io.open(r"D:\hoi4-province-editor\.cowork-temp\mapview_extract.js", "w", encoding="utf-8")

# 抓以下关键函数: resizeCanvas, screenToWorld, provinceAtWorld, setupMapEvents, draw, drawBorders, fitView, initMap, boot
names = ["function resizeCanvas(", "function screenToWorld(", "function provinceAtWorld(",
         "function setupMapEvents(", "function draw(", "function drawBorders(",
         "function fitView(", "async function initMap(", "async function boot(", "function clampCam("]
idxs = []
for n in names:
    i = s.find(n)
    out.write("\n\n### === " + n + " @ index " + str(i) + " ===\n")
    if i >= 0:
        # 粗略取到下一个顶层 function 或 3500 字符
        j = s.find("\nfunction ", i+1)
        if j < 0: j = i+3500
        out.write(s[i:j][:3500])
    else:
        out.write("(not found)")
out.close()
print("done")
