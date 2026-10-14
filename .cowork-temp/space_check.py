# -*- coding: utf-8 -*-
import io, sys, re
sys.stdout.reconfigure(encoding='utf-8')

def pairs(path):
    out = []
    for l in io.open(path, encoding='utf-8'):
        m = re.match(r'^("(?:[^"\\]|\\.)*")\s*:\s*"(.*)"\s*$', l)
        if m:
            out.append((m.group(1), m.group(2)))
    return out

src = pairs(r'D:\地块细化\.cowork-temp\logphr-en.yml')
t = io.open(r'D:\hoi4-province-editor\webui\i18n\phr-ar-SA.yml', encoding='utf-8').read()
tail = t[t.index('# 引擎日志/进度条'):]
ar = pairs_from = []
for l in tail.split('\n'):
    m = re.match(r'^("(?:[^"\\]|\\.)*")\s*:\s*"(.*)"\s*$', l)
    if m:
        ar.append((m.group(1), m.group(2)))

bad = 0
for (sk, sv), (ak, av) in zip(src, ar):
    # compare leading/trailing ascii space of english value vs arabic value
    ls = len(sv) - len(sv.lstrip(' '))
    ts = len(sv) - len(sv.rstrip(' '))
    lsa = len(av) - len(av.lstrip(' '))
    tsa = len(av) - len(av.rstrip(' '))
    if ls != lsa or ts != tsa:
        bad += 1
        print('SPACE MISMATCH:', sk)
        print('  en=', repr(sv))
        print('  ar=', repr(av))
print('space-mismatch count =', bad, '/', len(src))
