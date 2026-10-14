import glob, os
root = r'D:\hoi4-province-editor\webui'
pats = ['的 ', ' + tp', '块 +', '个地块', '· 沿海', '个目标', '块陆地']
for f in glob.glob(root + '/**/*.js', recursive=True):
    try:
        s = open(f, encoding='utf-8').read()
    except Exception:
        continue
    for i, l in enumerate(s.split('\n')):
        if any(p in l for p in pats):
            print(os.path.basename(f), i + 1, l.strip()[:200])
