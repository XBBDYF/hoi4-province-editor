# -*- coding: utf-8 -*-
import io, sys, re
sys.stdout.reconfigure(encoding='utf-8')

TGT = r'D:\hoi4-province-editor\webui\i18n\phr-ar-SA.yml'
EN  = r'D:\hoi4-province-editor\webui\i18n\phr-en-US.yml'
SRC = r'D:\地块细化\.cowork-temp\logphr-en.yml'

# 1) YAML parse
try:
    import yaml
    data = yaml.safe_load(io.open(TGT, encoding='utf-8').read())
    print('YAML parse: OK, top-level dict keys =', len(data))
except Exception as e:
    print('YAML parse FAILED:', repr(e))
    data = None

def kl(path):
    out = {}
    for l in io.open(path, encoding='utf-8'):
        m = re.match(r'^("(?:[^"\\]|\\.)*")\s*:', l)
        if m:
            k = m.group(1)[1:-1].replace('\\"', '"').replace('\\\\', '\\')
            out[k] = out.get(k, 0) + 1
    return out

ar = kl(TGT); en = kl(EN); src = kl(SRC)
print('ar unique =', len(ar), '| lines =', sum(ar.values()))
print('en unique =', len(en), '| src =', len(src))
print('missing (en-ar):', sorted(set(en) - set(ar)))
print('extra   (ar-en):', sorted(set(ar) - set(en)))

# 2) verify appended keys == source keys verbatim
src_lines = [l for l in io.open(SRC, encoding='utf-8').read().split('\n') if l.strip().startswith('"')]
tgt_text = io.open(TGT, encoding='utf-8').read()
tail = tgt_text[tgt_text.index('# 引擎日志/进度条'):]
tail_lines = [l for l in tail.split('\n') if l.strip().startswith('"')]
print('appended value lines =', len(tail_lines))
ok = True
for s, t in zip(src_lines, tail_lines):
    sk = re.match(r'^("(?:[^"\\]|\\.)*")', s).group(1)
    tk = re.match(r'^("(?:[^"\\]|\\.)*")', t).group(1)
    if sk != tk:
        ok = False
        print('KEY MISMATCH:', repr(sk), '!=', repr(tk))
print('all appended keys verbatim-equal to source:', ok)

# 3) sample 3
print('--- samples ---')
for i in (0, 12, 20):
    print(tail_lines[i])
