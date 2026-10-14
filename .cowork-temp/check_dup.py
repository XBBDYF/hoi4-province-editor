# -*- coding: utf-8 -*-
import io, sys, collections, re
sys.stdout.reconfigure(encoding='utf-8')
p = r'D:\hoi4-province-editor\webui\i18n\phr-ar-SA.yml'
lines = io.open(p, encoding='utf-8').read().split('\n')
keys = []
pat = re.compile(r'^("(?:[^"\\]|\\.)*")\s*:')
for i, l in enumerate(lines):
    m = pat.match(l)
    if m:
        keys.append(m.group(1))
c = collections.Counter(keys)
print('lines', len(lines))
print('keycount', len(keys))
print('dups', [(k, v) for k, v in c.items() if v > 1])
