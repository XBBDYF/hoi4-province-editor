# -*- coding: utf-8 -*-
import io
for p in [r'webui\i18n\phr-en-US.yml', r'webui\i18n\phr-ja-JP.yml',
          r'webui\i18n\phr-ko-KR.yml', r'webui\i18n\phr-ru-RU.yml']:
    lines = io.open(p, encoding='utf-8').read().splitlines()
    print(p, len(lines), 'lines')
    for i, l in enumerate(lines):
        if 'phr.生成预览图"' in l:
            print('   raw-line %d: %s' % (i, l))
