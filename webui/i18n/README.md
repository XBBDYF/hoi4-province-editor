# 多语言 (i18n)

界面文案全部放在这个目录的 **YAML** 文件里，改完刷新页面即生效（后端按文件 mtime 自动重载，无需重启）。

## 现有语言

| 代码 | 语言 | 文件 | 说明 |
| --- | --- | --- | --- |
| `zh-CN` | 简体中文 | `zh-CN.yml` | 源语言（键名以它为准） |
| `en-US` | English | `en-US.yml` | |
| `ja-JP` | 日本語 | `ja-JP.yml` | |
| `ko-KR` | 한국어 | `ko-KR.yml` | |
| `ru-RU` | Русский | `ru-RU.yml` | |
| `de-DE` | Deutsch | `de-DE.yml` | |
| `fr-FR` | Français | `fr-FR.yml` | |
| `pl-PL` | Polski | `pl-PL.yml` | |
| `ar-SA` | العربية | `ar-SA.yml` | 需要 `dir: rtl`（从右到左） |

`locales.yml` 是语言清单：

```yaml
default: zh-CN
locales:
  - code: ar-SA
    label: العربية
    file: ar-SA.yml
    dir: rtl          # 可选: rtl 时前端会把 <html dir> 设为从右到左
```

## 新增一种语言

1. 复制 `zh-CN.yml` 为 `xx-XX.yml`，把**值**翻译成目标语言（**键名不要改**）。
2. 在 `locales.yml` 的 `locales:` 里追加 `code / label / file`（如果是阿拉伯语这类从右到左语言，再加 `dir: rtl`）。
3. 刷新网页，右上角语言下拉框里就会出现该语言；选择后会记在浏览器本地（`localStorage: hoi4-lang`）。

## 用法（开发者）

1. **静态 HTML**：给元素加 `data-i18n="key"`（文本）、`data-i18n-html="key"`（含 `<b>/<kbd>` 等标签）、`data-i18n-title="key"`（title 属性）、`data-i18n-ph="key"`（placeholder）。载入与切换语言时由 `applyI18n()` 统一套用。
2. **JS 动态文本**：用 `t('key')`；带占位符用 `tfmt('key', {id: 123})`，词典里写 `"删除 #{id}（并入其最大邻居）？"`。
3. **动态内容元素不要加 `data-i18n`**：像 `#mapInfo`、`#cacheChip`、`#edSel`、`#railInfo` 这些由 JS 填内容的元素，加了会在切换语言时被重置成初始文案（这些元素已经在 index.html 里排除）。
4. **后端（Python）**：`from server import i18n_dict; lang, d = i18n_dict('ja-JP')`。界面里显示的引擎日志行、进度条文字也会随语言翻译（见下方短语表）。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/i18n` | 返回默认语言词典 + 可用语言清单 `{ok, lang, default, locales, dict}` |
| GET | `/api/i18n?lang=ja-JP` | 返回指定语言词典（未知语言回退默认语言） |

## 约定

1. 键名用点号分层：`区域.元素`，例如 `top.buildCache`、`hint.rail`、`msg.cacheDone`。
2. 值里用 `{name}` 表示占位符，由 `tfmt()` 替换。
3. 每种语言的键集合必须与 `zh-CN.yml` 完全一致（当前共 **208** 个键）；新增文案时先加 `zh-CN.yml` 再补其它语言。
4. 缺键时前端回退：`data-i18n` 元素保留 HTML 原文，`t()` 返回键名。

## 短语表（把界面里"拼出来的中文"也翻译掉）

`app.js` 里很多文本是用中文**字面量**拼出来的（状态栏消息、参数表单标签、审计面板、地块信息…）。
这类文本用一个**短语表**处理，不用逐个改调用点：

1. 短语表文件：`phr-<语言>.yml`，内容形如 `"phr.<中文原文>": "<译文>"`（共 470 条：前端拼接文本 + 引擎日志/进度行 + 弹窗/结果摘要/审计文案；已提供 en-US / ja-JP / ko-KR / ru-RU / de-DE / fr-FR / pl-PL / ar-SA 八份；zh-CN 是源语言，不需要短语表）
   - 动态数字/路径用 **`{占位符}`** 写进**整句**里（如 `"phr.匹配 {n} 个{t}（编号 {q}）"`），前端用 `tpf(键, {n:…})` 先翻译整句再替换，避免半句中文混在译文里；键里不要放换行（`\n` 留在调用点）。。
2. 运行时 `tp(文本)`：先整串精确匹配，再按"最长的键"做片段替换；界面上的主要输出点（状态栏、参数表单、审计面板、悬停提示、地块信息、编辑操作列表…）都已套用。
3. 语言回退规则：
   - `zh-CN`：**不翻译**（它就是源语言）；
   - 其它语言：优先用**自己的** `phr-<语言>.yml`，没有就用 `phr-en-US.yml`（英文）；
   - 普通键（`data-i18n` / `t()`）也是三级回退：当前语言 → `en-US` → `zh-CN`。
4. 想让某种语言彻底本土化：把它自己的短语表建起来即可 ——

```bash
# 例: 建日语的短语表
cp webui/i18n/phr-en-US.yml webui/i18n/phr-ja-JP.yml   # 然后把每条的值翻成日文(键不要改)
```

（`phr-ja-JP.yml` 等文件保存后刷新页面即生效，服务端按 mtime 热加载。）

## 还没翻译的部分（已知）

1. **后台控制台 / 日志文件**里的引擎输出仍是中文（属工程日志）；**界面上**的日志面板与进度条已随语言翻译。
2. 极少数深层状态行（如某些图例里来自地图数据的名称）保持原样。
