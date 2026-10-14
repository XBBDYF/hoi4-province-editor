# -*- coding: utf-8 -*-
import io, sys, os, re
sys.stdout.reconfigure(encoding='utf-8')

SRC = r'D:\地块细化\.cowork-temp\logphr-en.yml'
TGT = r'D:\hoi4-province-editor\webui\i18n\phr-ar-SA.yml'
ENUS = r'D:\hoi4-province-editor\webui\i18n\phr-en-US.yml'

AR = {
"phr.选择一份报告查看。": "اختر تقريرًا لعرضه.",
"phr.原版目录(缺失文件回退):": "دليل النسخة الأصلية (احتياطي للملفات المفقودة):",
"phr.读取 definition.csv": "قراءة definition.csv",
"phr.读取 provinces.bmp 并建立邻接": "قراءة provinces.bmp وبناء التجاور",
"phr.读取 provinces.bmp 并解码省份ID": "قراءة provinces.bmp وفك ترميز معرّفات المقاطعات",
"phr.读取 provinces.bmp": "قراءة provinces.bmp",
"phr.建立像素索引(统计各颜色面积)": "بناء فهرس البكسلات (المساحة لكل لون)",
"phr.扫描 states / strategic regions": "مسح states / strategic regions",
"phr.扫描 战略区 / 州 归属": "مسح تبعية المنطقة الاستراتيجية / الولاية",
"phr.沿海判定不可用:": "تحديد السواحل غير متاح:",
"phr.拆分前校正跳过:": "تم تخطي تصحيح ما قبل التقسيم:",
"phr.密度图白色保护:": "حماية اللون الأبيض في خريطة الكثافة:",
"phr. 个全白地块跳过；混合地块只细化非白像素 (阈值=": " مقاطعة بيضاء بالكامل تم تخطيها؛ المقاطعات المختلطة تُقسَّم عند البكسلات غير البيضاء فقط (العتبة=",
"phr.已加载渐变图:": "تم تحميل خريطة التدرّج:",
"phr. (黑=": " (أسود=",
"phr.px, 白=": "px، أبيض=",
"phr.px)": "px)",
"phr.渐变图加载失败:": "فشل تحميل خريطة التدرّج:",
"phr.按规则检测目标地块": "كشف المقاطعات المستهدفة وفق القاعدة",
"phr.排除州": "استثناء الولاية",
"phr. -> 涉及地块 ": " -> يؤثر على ",
"phr.未细化陆地邻区过渡: 候选": "انتقال الجوار لليابسة غير المقسّمة: المرشّحات",
"phr.，迭代移除 ": "، أُزيل تدريجيًا ",
"phr. 个临界地块": " مقاطعة حرجة",
"phr.检测到": "تم كشف",
"phr. 个过大地块, 新建 ": " مقاطعة كبيرة جدًا، وإنشاء ",
"phr. 个地块": " مقاطعة",
"phr. 个待细化地块": " مقاطعة بحاجة إلى تقسيم",
"phr.预览(修改前):": "معاينة (قبل):",
"phr.预览失败:": "فشلت المعاينة:",
"phr.开始逐个细化": "بدء التقسيم واحدة تلو الأخرى",
"phr. 个地块(每 5 个刷新一次进度) ...": " مقاطعة (تحديث التقدّم كل 5) ...",
"phr.细化进度": "تقدّم التقسيم",
"phr.陆地邻接形状均衡: 当前筛选条件下没有可调整的陆地地块": "موازنة شكل اليابسة: لا توجد مقاطعات يابسة قابلة للتعديل في ظل المُرشِّح الحالي",
"phr.陆地邻接形状均衡: 调整 ": "موازنة شكل اليابسة: تم تعديل ",
"phr. 个像素, 涉及 ": " بكسل موزّعة على ",
"phr. 对相邻地块": " زوج متجاور",
"phr.陆地形状均衡跳过:": "تم تخطي موازنة شكل اليابسة:",
"phr.修复后陆地均衡复核: 调整 ": "إعادة فحص اليابسة بعد الإصلاح: تم تعديل ",
"phr.修复后面积均衡复核跳过:": "تم تخطي إعادة فحص المساحة بعد الإصلاح:",
"phr.清理边界(十字交叉 / 不连通)": "تنظيف الحدود (تقاطعات / غير متصل)",
"phr.边界清理出错:": "خطأ في تنظيف الحدود:",
"phr.写出 provinces.bmp": "كتابة provinces.bmp",
"phr.写出 definition.csv": "كتابة definition.csv",
"phr.BMP 尾部填充跳过:": "تم تخطي حشو نهاية BMP:",
"phr.构建新地图邻接关系": "بناء تجاور الخريطة الجديدة",
"phr.运河识别:": "كشف القنوات:",
"phr. 条 (rivers.bmp)": " صف (rivers.bmp)",
"phr.运河识别跳过:": "تم تخطي كشف القنوات:",
"phr.铁路: 修复": "السكك الحديدية: أُصلح",
"phr. 处断点": " نقطة انقطاع",
"phr.运河: 识别": "القنوات: تم كشف",
"phr. 条, 修正 ": " صف، وتم إصلاح ",
"phr. 条": " صف",
"phr.运河: 补全": "القنوات: تم استكمال",
"phr. 条可唯一确认的缺失连线": " وصلة مفقودة قابلة للتحديد بشكل فريد",
"phr.运河: 清理旧版误生成的": "القنوات: إزالة القديمة التي وُلّدت خطأً",
"phr. 条无规则连线": " وصلة بلا قاعدة",
"phr.海峡/连线: 修正": "المضائق/الوصلات: تم إصلاح",
"phr.海峡/连线:": "المضائق/الوصلات:",
"phr. 条无法自动修正(需人工检查)": " صف تعذّر إصلاحه تلقائيًا (يلزم فحص يدوي)",
"phr. 条无法自动修正": " صف تعذّر إصلاحه تلقائيًا",
"phr.完整性修复出错:": "خطأ في إصلاح السلامة:",
"phr.生成预览图": "إنشاء صور المعاينة",
"phr.完成, 用时": "اكتمل، استغرق",
"phr.跳过清空输出目录(路径不安全或与 mod 重叠):": "تخطي تفريغ مجلد الإخراج (مسار غير آمن أو متداخل مع mod):",
"phr.已清空输出目录:": "تم تفريغ مجلد الإخراج:",
"phr. (删除 ": " (حُذف ",
"phr. 项)": " عنصر)",
"phr.检查 strategic regions": "فحص strategic regions",
"phr.检查 states": "فحص states",
"phr.检查 railways": "فحص railways",
"phr.检查 adjacencies": "فحص adjacencies",
"phr.检查其他引用 (supply_nodes/buildings/victory_points)": "فحص المراجع الأخرى (supply_nodes/buildings/victory_points)",
"phr.检查地块连通性": "فحص اتصال المقاطعات",
"phr.检查 州 / 战略区 连续性": "فحص استمرارية الولاية / المنطقة الاستراتيجية",
"phr.写出 states(": "كتابة states(",
"phr.) / 战略区(": ") / strategic regions(",
"phr.重命名 railways / adjacencies 里的地块ID": "إعادة تسمية معرّفات المقاطعات في railways / adjacencies",
"phr.导出完成 ->": "اكتمل التصدير ->",
"phr. (已把当前结果设为新基准, 之后导出只含新增改动)": " (تم تعيين النتيجة الحالية كخط أساس؛ التصديرات اللاحقة تحتوي التغييرات الجديدة فقط)",
"phr.[提示] 已清除编辑器里未导出的改动(编辑缓存), 输出目录不受影响": "[معلومة] تم مسح تغييرات المحرّر غير المصدَّرة (ذاكرة التحرير)، ومجلد الإخراج لم يتأثر",
"phr.原版:": "النسخة الأصلية:",
"phr.输出:": "الإخراج:",
"phr.[注意] 勾选了『直接改原 mod (危险)』-> 结果将直接写入:": "[تنبيه] تم تحديد «تعديل mod الأصلي مباشرةً (خطير)» -> ستُكتب النتائج مباشرةً في:",
"phr.[注意] 输出目录与数据来源目录相同 -> 会直接覆盖源目录(不会备份)": "[تنبيه] مجلد الإخراج مطابق لمجلد المصدر -> سيستبدل المصدر مباشرةً (دون نسخ احتياطي)",
"phr.[注意] 输出目录在数据来源目录内部 -> 会改写其中的文件": "[تنبيه] مجلد الإخراج داخل مجلد المصدر -> ستُعاد كتابة الملفات فيه",
"phr.[注意] 有": "[تنبيه] هناك",
"phr. 种颜色未登记在 definition.csv 中(将被忽略)": " لون غير مسجّل في definition.csv (سيُتجاهل)",
"phr.[注意] 渐变图不存在:": "[تنبيه] صورة التدرّج غير موجودة:",
"phr.[警告]": "[تحذير]",
"phr.[错误]": "[خطأ]",
"phr.重建地图缓存": "إعادة بناء ذاكرة الخريطة المؤقتة",
"phr.缓存重建失败:": "فشلت إعادة بناء الذاكرة المؤقتة:",
"phr.省份数:": "عدد المقاطعات:",
"phr.写出 prov_id.png (": "كتابة prov_id.png (",
"phr.生成显示层": "إنشاء طبقة العرض",
"phr.从 colormap DDS 解出地形底图 (PIL)": "فك ترميز الخريطة الأساسية للتضاريس من colormap DDS (PIL)",
"phr.colormap DDS 解码失败(": "فشل فك ترميز colormap DDS (",
"phr., 改用 provinces.bmp 缩放": ")، مع الرجوع إلى تحجيم provinces.bmp",
"phr.用 provinces.bmp 缩放为底图": "تحجيم provinces.bmp كخريطة أساسية",
"phr.生成省份色块层": "إنشاء طبقة ألوان المقاطعات",
"phr.生成边界层": "إنشاء طبقة الحدود",
"phr.生成 战略区 / 州 / 大洲 着色层": "إنشاء طبقات تلوين المنطقة الاستراتيجية / الولاية / القارة",
"phr.生成河流层": "إنشاء طبقة الأنهار",
"phr.rivers.bmp 不是索引图, 跳过河流层": "rivers.bmp ليس صورة مفهرسة؛ تخطي طبقة الأنهار",
"phr.河流层生成失败:": "فشل إنشاء طبقة الأنهار:",
"phr.计算省份中心并解析连线": "حساب مراكز المقاطعات وتحليل الوصلات",
"phr.省份中心失败:": "فشل حساب مراكز المقاطعات:",
"phr.连线解析失败:": "فشل تحليل الوصلات:",
"phr.统计面积并生成 prov_index.json": "حساب المساحات وكتابة prov_index.json",
"phr.缓存完成 ->": "اكتملت الذاكرة المؤقتة ->",
"phr.读取 provinces.bmp 并解码省份ID...": "قراءة provinces.bmp وفك ترميز معرّفات المقاطعات...",
}

def parse(path):
    d = {}
    order = []
    with io.open(path, encoding='utf-8') as f:
        for line in f:
            line = line.rstrip('\n')
            s = line.strip()
            if not s or s.startswith('#'):
                continue
            m = re.match(r'^("(?:[^"\\]|\\.)*")(?:\s*:\s*)(.*)$', line)
            if not m:
                raise SystemExit('BAD LINE: ' + repr(line))
            key = m.group(1)[1:-1]
            key = key.replace('\\"', '"').replace('\\\\', '\\')
            val = m.group(2)
            if key in d:
                print('NOTE dup key in', path, ':', key)
            d[key] = val
            order.append(key)
    return d, order

src, src_order = parse(SRC)
tgt, tgt_order = parse(TGT)
enus, _ = parse(ENUS)

print('src count =', len(src))
print('tgt count =', len(tgt))
print('enus count =', len(enus))

# verify all src keys exist in en-us
missing_in_enus = [k for k in src_order if k not in enus]
print('src keys missing in en-us:', missing_in_enus)

# verify AR covers all src keys
missing_ar = [k for k in src_order if k not in AR]
print('src keys missing AR translation:', missing_ar)
extra_ar = [k for k in AR if k not in src]
print('AR keys not in src:', extra_ar)
assert not missing_ar and not extra_ar

# keys already in target? (should be none overlapping)
overlap = [k for k in src_order if k in tgt]
print('overlap with target:', overlap)

# Build append block
lines = ['', '# 引擎日志/进度条']
for k in src_order:
    val = AR[k]
    # escape any double quotes/backslashes
    v = val.replace('\\', '\\\\').replace('"', '\\"')
    kq = k.replace('\\', '\\\\').replace('"', '\\"')
    lines.append('"%s": "%s"' % (kq, v))

block = '\n'.join(lines) + '\n'

# read original target text and append
with io.open(TGT, encoding='utf-8') as f:
    text = f.read()
if not text.endswith('\n'):
    text += '\n'
text = text + block
with io.open(TGT, 'w', encoding='utf-8', newline='\n') as f:
    f.write(text)

print('appended', len(src_order), 'lines')
