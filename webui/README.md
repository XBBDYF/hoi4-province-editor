# HOI4 地块细化 · Web 控制台

把 `province_splitter` 的核心能力搬进浏览器：**地图预览 + 分析 / 细化 + 完整性检查 + 地图编辑**。
后端是本地 Flask 服务（复用 `province_splitter.core` / `integrity` / `canals`），
前端是纯 HTML/CSS/JS（无构建步骤、无前端框架）。

---

## 快速开始

1. 双击项目根目录的 [`★一键启动Web编辑器.bat`](../★一键启动Web编辑器.bat)
   （等价于 `py -3.13 webui\server.py`），浏览器会自动打开 <http://127.0.0.1:8730/>。
2. 顶栏填好 **mod / 原版 / 输出**（**mod 留空 = 只加载原版**）。
3. 点右上角 **构建/刷新缓存**（读取 `provinces.bmp` 生成地图图层，约 30 秒）。
4. 之后即可：地图预览 → ① 分析 → ② 细化 → ③ 完整性检查 → 编辑 → 导出。

命令行方式：

```bat
py -3.13 server.py --port 8730                 :: 启动服务(自动开浏览器)
py -3.13 server.py --port 8730 --no-browser    :: 不开浏览器
py -3.13 webcache.py --mod "<mod路径>" --out "<项目>\webui\cache"   :: 只重建缓存
```

---

## 界面功能

**地图预览（主视图）**

- 图层：地形 / 省份色块 / 大洲 / 战略区 / 州 / 边界，各自可开关并调不透明度。
- 交互：滚轮以光标为中心缩放，拖动平移，`适应` / `1:1`。
- 悬停：省份 ID、颜色、类型、沿海、面积、大洲、state、战略区、地形。
- 单击选中并高亮；`定位省份` 输入 ID 跳转居中。
- 右上角**搜索条**：下拉 **地块 / state / 战略区 / 大洲**，按编号**精确匹配**，把**所有命中地块一起高亮**
  并缩放到范围（`state` 输 `50` → 标出属于 50 号州的全部地块）。

**分析**：只读一次，列出所有"过大"地块（ID/色块/面积/拆分数/类型/大洲/state/战略区），可排序、点行定位。

**细化**：写出结果到输出目录（默认不覆盖原 mod），带实时 SSE 日志、进度条、修改前/修改后预览、结果摘要。

**检查（完整性）**：**红色=需要处理**，**黄色=仅供参考、需手动修复**。

- 红：战略区/州重复归属与无效 ID、铁路断连、地块不连通/孤立碎片、**州不连续 / 战略区不连续**、十字交叉。
- 黄：海峡/运河/不可通行异常 + 「不可通行条目」计数（结果底部显示统计来源 `adjacencies.csv` 路径）。

**编辑**（勾选「编辑模式」；图层 + 参考来源 + 点击地图）：

| 图层 | 操作 |
| --- | --- |
| 州 state | 改归属国/人数/类别/建筑；加入/移出州；用选中地块新建州 |
| 战略区 | 改名称与 weather；新建战略区 |
| 大洲 | 分配大洲 |
| 地块 prov | 合并、删除（并入最大邻居） |
| 铁路 / 补给 | Shift 点两块自动建铁路（只走陆地、可跨海峡/运河、绕开不可通行）；改等级/删除；点地块增删补给点 |
| 地块地形 | 读 `terrain.bmp` 显示/改地形；一键自动生成；反向导出 `terrain.bmp` |

- **参考**：`mod 原文件` 或 `output 输出`，决定编辑读取哪份数据。
- 编辑先记操作（撤销/重做/重置），点「导出到输出目录」才写盘，**只写改动文件、保持原文件名**。
- **新建 ID 优先复用空闲 ID**（编号空缺，或文件存在但无地块），否则最大编号 +1；页面实时显示"下一个可用 ID"。

**报告**：查看 `report.csv` / `report.txt` / `changed_files.txt`。

---

## 目录结构

```
webui/
  server.py             Flask 后端 (API + 静态资源 + 任务/SSE)
  editor.py             地图编辑层 (州/战略区/大洲/地块/铁路/地形 的读取与写回)
  webcache.py           地图缓存生成器 (图层 PNG + 索引 JSON)
  static/index.html     界面结构
  static/style.css      样式
  static/app.js         前端逻辑 (地图查看器 / 表单 / 表格 / 编辑)
  cache/                生成的地图缓存 (可删除, 重新"构建/刷新缓存"即可)
```

---

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
| GET | `/api/task/<id>` `/api/task/<id>/stream` | 任务状态 / SSE 日志 |
| GET | `/api/report?kind=csv\|txt\|changed` | 报告内容 |
| GET | `/api/file?path=<绝对路径>` | 安全提供预览图等文件（限定项目目录内） |
| GET | `/cache/<name>` | 地图图层 / 索引 |

**编辑相关（`/api/edit/*`）**

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/edit/status` | 当前会话状态（操作数、改动的州/战略区文件） |
| POST | `/api/edit/merge` `/delete` `/assign` `/assign_many` | 合并 / 删除 / 单个或批量改归属 |
| POST | `/api/edit/new_state` `/state_data` `/set_state` | 新建州 / 读州数据 / 写州数据 |
| POST | `/api/edit/sr_data` `/set_sr` `/new_sr` | 战略区 读 / 写 / 新建 |
| GET | `/api/edit/rail_data` | 铁路 + 补给 + 相邻边（含不可通行屏障折线） |
| POST | `/api/edit/rail` `/rail_level` `/rail_rm` | 建铁路 / 改等级 / 删铁路 |
| POST | `/api/edit/supply` `/building` | 增删补给点 / 建筑 |
| GET | `/api/edit/terrain_data` | 地形数据（prov → 地形名） |
| POST | `/api/edit/terrain_auto` `/export_terrain` | 按 terrain.bmp 自动生成 / 由 definition.csv 导出 terrain.bmp |
| GET | `/api/edit/next_id?which=state\|sr\|prov\|continent` | 下一个可用 ID（空闲优先复用） |
| POST | `/api/edit/undo` `/redo` `/reset` `/export` | 撤销 / 重做 / 重置 / 导出 |

---

## 说明

- 缓存图层用游戏内 colormap 地形底图，并叠加省份/战略区/州/大洲着色与边界线。
- 省份悬停/定位基于 `prov_id.png`（全分辨率 ID 图）。
- 参数与面板记忆在浏览器本地（localStorage）。
- 所有"细化 / 检查 / 编辑"都在本地运行，不会联网。
- 编辑操作只存在服务端会话里：**重启服务或切换 mod/参考路径会丢失未导出的改动**。
