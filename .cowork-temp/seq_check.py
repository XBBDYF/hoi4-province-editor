# -*- coding: utf-8 -*-
import io, sys, re
sys.stdout.reconfigure(encoding='utf-8')

def seq(path):
    out = []
    for l in io.open(path, encoding='utf-8'):
        m = re.match(r'^("(?:[^"\\]|\\.)*")\s*:', l)
        if m:
            out.append(m.group(1)[1:-1].replace('\\"', '"').replace('\\\\', '\\'))
    return out

ar = seq(r'D:\hoi4-province-editor\webui\i18n\phr-ar-SA.yml')
en = seq(r'D:\hoi4-province-editor\webui\i18n\phr-en-US.yml')
print('ar key lines =', len(ar), '| en key lines =', len(en))
print('identical key sequence:', ar == en)
if ar != en:
    for i, (a, b) in enumerate(zip(ar, en)):
        if a != b:
            print('first diff at', i, repr(a), repr(b)); break
