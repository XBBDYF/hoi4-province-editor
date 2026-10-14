# -*- coding: utf-8 -*-
import yaml, collections

def load(p):
    with open(p, encoding='utf-8') as f:
        return yaml.safe_load(f)

base = load(r'D:\hoi4-province-editor\webui\i18n\phr-en-US.yml')
fr = load(r'D:\hoi4-province-editor\webui\i18n\phr-fr-FR.yml')
pl = load(r'D:\hoi4-province-editor\webui\i18n\phr-pl-PL.yml')

bs, fs, ps = set(base), set(fr), set(pl)
print('en-US keys   :', len(bs))
print('fr-FR keys   :', len(fs), '| missing vs en:', sorted(bs - fs), '| extra:', sorted(fs - bs))
print('pl-PL keys   :', len(ps), '| missing vs en:', sorted(bs - ps), '| extra:', sorted(ps - bs))
print('fr == en set :', bs == fs)
print('pl == en set :', bs == ps)

# order check (first occurrence order)
def order(p):
    ks = []
    for k in load(p):
        if k not in ks:
            ks.append(k)
    return ks
ob, of, op = order(r'D:\hoi4-province-editor\webui\i18n\phr-en-US.yml'), order(r'D:\hoi4-province-editor\webui\i18n\phr-fr-FR.yml'), order(r'D:\hoi4-province-editor\webui\i18n\phr-pl-PL.yml')
print('fr order same:', ob == of, '| pl order same:', ob == op)

for k in ['phr.面积阈值 (px)', 'phr.\u5df2\u8d85\u8fc7 30s \u6ca1\u6709\u6536\u5230\u670d\u52a1\u7aef\u6d88\u606f\uff1a\u4efb\u52a1\u53ef\u80fd\u5df2\u505c\u6b62\u3002\u53ef\u70b9\u201c\u53d6\u6d88\u201d\uff0c\u6216\u5173\u6389\u540e\u91cd\u65b0\u53cc\u51fb \u2605\u4e00\u952e\u542f\u52a8Web\u7f16\u8f91\u5668.bat', 'phr.· 运河', 'phr.未选中（编辑模式下点击选择，Shift 加选）']:
    print('\nKEY:', k)
    print('  FR:', fr.get(k))
    print('  PL:', pl.get(k))
