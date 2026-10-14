# -*- coding: utf-8 -*-
import io, sys, re
sys.stdout.reconfigure(encoding='utf-8')

def keys(path):
    out = {}
    for l in io.open(path, encoding='utf-8'):
        m = re.match(r'^("(?:[^"\\]|\\.)*")\s*:', l)
        if m:
            k = m.group(1)[1:-1].replace('\\"', '"').replace('\\\\', '\\')
            out[k] = out.get(k, 0) + 1
    return out

ar = keys(r'D:\hoi4-province-editor\webui\i18n\phr-ar-SA.yml')
en = keys(r'D:\hoi4-province-editor\webui\i18n\phr-en-US.yml')
src = keys(r'D:\地块细化\.cowork-temp\logphr-en.yml')

print('ar unique', len(ar), 'lines', sum(ar.values()))
print('en unique', len(en), 'lines', sum(en.values()))
print('src unique', len(src), 'lines', sum(src.values()))
print('in ar dup keys:', [k for k,v in ar.items() if v>1])
print('in en dup keys:', [k for k,v in en.items() if v>1])
print('en - ar (missing in ar):', sorted(set(en)-set(ar)))
print('ar - en (extra in ar):', sorted(set(ar)-set(en)))
print('src keys already in ar:', sorted(set(src)&set(ar)))
print('src not in en:', sorted(set(src)-set(en)))
