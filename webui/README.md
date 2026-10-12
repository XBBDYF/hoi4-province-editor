# HOI4 地块细化 · Web 控制台

把原来的 Python 核心能力（`province_splitter`）搬到一个浏览器界面里，
参照 VS Code 插件 *HOI4 Mod Utilities* 的思路：**地图预览 + 参数化分析/细化 + 完整性检查**。

后端是本地 Flask 服务（复用现有 `province_splitter.core` / `integrity`），
前端是纯 HTML/CSS/JS（无构建步骤、无前端框架）。

---

## 快速开始

1. 双击 [启动Web界面.bat](file:///D:/地块细化/webui/启动Web界面.bat)
   （等价于 `py -3.13 webui/server.py`），浏览器会自动打开 <http://127.0.0.1:8730/>。
2. 首次使用点右上角 **构建/刷新缓存**（读取 `provinces.bmp` 生成地图图层，约 30 秒）。
3. 之后即可：**地图预览 → ① 分析 → ② 细化 → ③ 完整性检查**。

命令行方式：

```bat
py -3.13 webui\server.py --port 8730          :: 启动服务
py -3.13 webui\server.py --port 8730 --no-browser
py -3.13 webui\webcache.py --mod "<mod路径>" --out "D:\地块细化\webui\cache"   :: 只重建缓存
```

---

## 界面功能

**地图预览（主视图，始终可见）**

- 图层：地形 / 省份色块 / 大洲 / 战略区 / 州 / 边界，各自可开关并调不透明度。
- 交互：滚轮以光标为中心缩放，拖动平移，`适应` / `1:1` 一键归位。
- 悬停：显示省份 ID、颜色、类型、沿海、面积、大洲、state、战略区、地形。
- 单击：选中省份并高亮描边；`定位省份` 输入 ID 可直接跳转居中。
- 图例：战略区 / 州 / 大洲三条按钮，显示配色列表并自动开启对应图层。

**分析**：按参数跑一次只读分析，列出所有“过大”地块（ID/色块/面积/拆分数/类型/大洲/state/战略区），
点表头排序、点行定位到地图。

**细化**：真正写出结果到输出目录（默认不覆盖原 mod），带实时 SSE 日志、
修改前/修改后预览图、结果摘要（含修复备注）。

**检查**：地图完整性体检——战略区/州重复归属与无效 ID、铁路断连、海峡运河异常、
孤立碎块、十字交叉。

**省份**：全量省份浏览器（21949 个），支持按 ID/类型/大洲/state/战略区筛选与排序，点行定位。

**报告**：直接查看 `report.csv` / `report.txt` / `changed_files.txt`。

---

## 目录结构

```
webui/
  server.py             Flask 后端 (API + 静态资源 + 任务/SSE)
  webcache.py           地图缓存生成器 (图层 PNG + 索引 JSON)
  static/index.html     界面结构
  static/style.css      样式
  static/app.js         前端逻辑 (地图查看器 / 表单 / 表格)
  cache/                生成的地图缓存 (可删除, 重新“构建/刷新缓存”即可)
  启动Web界面.bat
```

## 后端 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/state` | 项目状态（mod/输出/缓存是否存在、省份数） |
| GET | `/api/defaults` | 默认路径 |
| POST | `/api/cache/build` | 重建地图缓存（后台任务） |
| POST | `/api/analyze` | 只读分析 |
| POST | `/api/run` | 执行细化并写出 |
| POST | `/api/audit` | 完整性检查 |
| POST | `/api/cancel` | 取消任务 |
| GET | `/api/task/<id>` / `/api/task/<id>/stream` | 任务状态 / SSE 日志 |
| GET | `/api/report?kind=csv\|txt\|changed` | 报告内容 |
| GET | `/api/file?path=<绝对路径>` | 安全提供预览图等文件（限定在项目目录内） |
| GET | `/cache/<name>` | 地图图层/索引 |

## 说明

- 缓存图层用的是游戏内 colormap 地形底图（羊皮纸风），并叠加省份/战略区/州/大洲着色与边界线。
- 省份悬停/定位基于 `prov_id.png`（全分辨率 ID 图），精度与地图一致。
- 与 GUI/CLI 完全一致地复用 `Config` 参数；参数会记忆在浏览器本地。
- 所有“细化/检查”只在本地运行，不会联网。


---

## 原版(游戏)目录回退  `--game`

mod 里的地图文件可能不完整（缺 `provinces.bmp` / `rivers.bmp` / `adjacency_rules.txt` / 全量
`history/states/*` 等），这时需要拿原版补上。用 `--game` 指定原版安装目录：

```bat
py -3.13 run_cli.py --mod "C:\...\Project_IRIS" ^
    --game "D:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV" ^
    --out "D:\out" --threshold 200 --scope all
```

规则：
1. **单文件**：mod 有就用 mod 的，没有才用原版的（`map/definition.csv`、`provinces.bmp`、
   `continent.txt`、`rivers.bmp`、`adjacencies.csv`、`railways.txt`、`adjacency_rules.txt`、
   `map/terrain/colormap_*.dds`）。
2. **目录（states / strategicregions）**：默认 **mod 有该类文件就只用 mod 的**；mod 完全没有该类
   文件时才回退原版。这样总转换 mod 不会被塞进上千个原版州文件。
3. 加 `--game-merge` 则**完全合并** mod 与原版（同名以 mod 为准），等价于 HOI4 的真实加载行为。
4. 输出目录里只会写出**真正改动过**的文件；来源是原版但未改动的文件不会复制进输出。

GUI 有「原版目录」输入框 +「states/战略区 与原版合并」复选；Web 控制台顶栏有「原版」输入框，
参数区有同名复选。
