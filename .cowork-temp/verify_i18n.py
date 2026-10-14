# -*- coding: utf-8 -*-
import io, json

base = r'D:\hoi4-province-editor\webui\i18n'
def unescape(s):
    out=[];i=0
    while i<len(s):
        c=s[i]
        if c=='\\':
            n=s[i+1]
            if n=='n':out.append('\n');i+=2
            elif n=='t':out.append('\t');i+=2
            elif n=='"':out.append('"');i+=2
            elif n=='\\':out.append('\\');i+=2
            elif n=='/':out.append('/');i+=2
            elif n=='r':out.append('\r');i+=2
            elif n=='u':out.append(chr(int(s[i+2:i+6],16)));i+=6
            else:out.append(n);i+=2
        else:out.append(c);i+=1
    return ''.join(out)

def parse_entry(line):
    i=1
    while True:
        if line[i]=='\\': i+=2; continue
        if line[i]=='"': break
        i+=1
    return line[0:i+1], line[i+3:].strip()

def load(path):
    data=io.open(path,encoding='utf-8').read()
    keys=[]; vals={}
    for ln in data.replace('\r\n','\n').split('\n'):
        st=ln.lstrip()
        if not st or st.startswith('#'): continue
        kraw,vraw=parse_entry(ln)
        k=unescape(kraw[1:-1]); v=unescape(vraw[1:-1])
        keys.append(k); vals[k]=v
    return keys, vals

en_keys, en_vals = load(base+r'\phr-en-US.yml')
ja_keys, ja_vals = load(base+r'\phr-ja-JP.yml')
ko_keys, ko_vals = load(base+r'\phr-ko-KR.yml')

se, sj, sk = set(en_keys), set(ja_keys), set(ko_keys)
print('entries en/ja/ko lines:', len(en_keys), len(ja_keys), len(ko_keys))
print('unique keys:', len(se), len(sj), len(sk))
print('ja missing (in en not ja):', len(se-sj))
print('ja extra   (in ja not en):', len(sj-se))
print('ko missing (in en not ko):', len(se-sk))
print('ko extra   (in ko not en):', len(sk-se))
print('ja duplicates in file lines:', len(ja_keys)-len(sj))
print('ko duplicates in file lines:', len(ko_keys)-len(sk))
print('ja order == en order:', ja_keys==en_keys)
print('ko order == en order:', ko_keys==en_keys)

def show(t, en, ks):
    for k in ks:
        print('%s  %r  ->  %r' % (t, k, en_vals.get(k), ''))

print()
print('--- samples ja ---')
for k in ['phr.面积阈值 (px)','phr.先选一个省份','phr.· 运河','phr.</b> 个待细化地块，预计新建 <b>']:
    print(repr(k), '=>', repr(ja_vals[k]))
print('--- samples ko ---')
for k in ['phr.面积阈值 (px)','phr.先选一个省份','phr.· 运河','phr.</b> 个待细化地块，预计新建 <b>']:
    print(repr(k), '=>', repr(ko_vals[k]))
