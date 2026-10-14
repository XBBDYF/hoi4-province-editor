# -*- coding: utf-8 -*-
import io, sys, yaml
sys.stdout.reconfigure(encoding='utf-8')

SRC = r'D:\地块细化\.cowork-temp\logphr-en.yml'
ENU = r'webui\i18n\phr-en-US.yml'
JA = r'webui\i18n\phr-ja-JP.yml'
KO = r'webui\i18n\phr-ko-KR.yml'

def load(p):
    return yaml.safe_load(io.open(p, encoding='utf-8'))

src = load(SRC)
enu = load(ENU)
print('source logs =', len(src), '| phr-en-US total =', len(enu))

def rawentry(p):
    return [l for l in io.open(p, encoding='utf-8').read().splitlines()
            if l.startswith('"phr.')]

en_raw = rawentry(ENU)
print('en-US raw phr lines =', len(en_raw), '(unique', len(enu), ')')

for name, p in (('JA', JA), ('KO', KO)):
    print('=== %s ===' % name)
    # YAML parses
    d = load(p)
    print(' YAML OK, total =', len(d))
    ks = list(d.keys())
    # duplicate keys in raw
    raw = rawentry(p)
    from collections import Counter
    c = Counter(l.split('":', 1)[0] + '"' for l in raw)
    dups = [k for k, n in c.items() if n > 1]
    print(' raw phr lines =', len(raw), '| duplicate raw keys =', dups)
    # compare to en-US
    print(' key set == en-US key set :', set(ks) == set(enu))
    print(' key order == en-US order :', ks == list(enu.keys()))
    print(' missing vs en-US =', len([k for k in enu if k not in d]),
          '| extra vs en-US =', len([k for k in d if k not in enu]))
    # all 113 source keys present
    print(' all 113 source keys present:', all(k in d for k in src))
    # sample 3
    for k in ['phr.读取 definition.csv',
              'phr. -> 涉及地块 ',
              'phr.读取 provinces.bmp 并解码省份ID...']:
        print('  %r -> %r' % (k, d[k]))
