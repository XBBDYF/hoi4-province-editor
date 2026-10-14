# -*- coding: utf-8 -*-
import re, sys
pat = re.compile(r'^"[^"]*": ')  # naive
kv = re.compile(r'^(\s*)"(.*)":\s?(.*)$')
for f in [r'D:\hoi4-province-editor\webui\i18n\phr-fr-FR.yml',
          r'D:\hoi4-province-editor\webui\i18n\phr-pl-PL.yml',
          r'D:\hoi4-province-editor\webui\i18n\phr-en-US.yml',
          r'D:\地块细化\.cowork-temp\logphr-en.yml']:
    print('=====', f)
    t = open(f, 'r', encoding='utf-8').read()
    for i, ln in enumerate(t.split('\n'), 1):
        s = ln.rstrip('\r')
        if s.strip() == '' or s.lstrip().startswith('#'):
            continue
        if not pat.match(s):
            print('  NON-KV', i, repr(s))
