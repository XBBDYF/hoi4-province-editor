# HOI4 地块细化工具

把 `provinces.bmp` 里"过大的地块"自动切成若干小块，并在拆分后**自动修复地图系统**（战略区/州归属、铁路、海峡运河）。

新地块会：
1. 在 `provinces.bmp` 上得到一块新的唯一颜色；
2. 往 `map/definition.csv` 追加一行（**完整复制原地块**的类型/沿海/地形/大洲参数）；
3. 加入原地块所属的 **state**（`history/states/*.txt`）与 **strategic region**（`map/strategicregions/*.txt`）。
4. 拆分后自动 **修复铁路断连**（`map/railways.txt`）与 **海峡/运河**（`map/adjacencies.csv`、`map/adjacency_rules.txt`），并校正战略区/州的重复归属。

原地块 **ID 和颜色保持不变**（只是面积变小），所以引用原 ID 的地方（supply_nodes、buildings、victory_points 等）不会失效。

---

## 环境要求

- Python 3.9+，需要 `numpy` 与 `Pillow`；图形界面还需要 `tkinter`（Windows 官方 Python 自带）。
- 本机已验证：`py -3.13` 自带 tkinter + numpy + Pillow。

---

## 快速开始

### 图形界面（推荐）

双击 `启动GUI.bat`，或 `py -3.13 run_gui.py`。
界面默认参数已经调整为：面积阈值 `200`、目标面积 `0`、新块最小 `80`、最多 `64` 份；大洲 `1,2,3`；排除 ID `85,69,86,114`；黑/白目标面积 `80/250`；铁路重连跳数 `25`；孤岛阈值 `5`。默认生成预览并开启战略区/州/铁路/海峡修复。
界面里可以：**① 分析(不写入)** 先看会改哪里（同时生成"修改前"预览图）→ **② 开始细化** → **③ 完整性检查**。

### 命令行

```bat
:: 只检查地图完整性(战略区/州/铁路/海峡), 不拆分
py -3.13 run_cli.py --mod "..\Project_IRIS" --audit

:: 只分析不写入(会生成 preview_targets.png)
py -3.13 run_cli.py --mod "..\Project_IRIS" --out "D:\地块细化\output" --threshold 200 --analyze

:: 真正细化(海洋+湖泊), 并自动修复铁路/海峡/战略区
py -3.13 run_cli.py --mod "..\Project_IRIS" --out "D:\地块细化\output" --threshold 200 --scope all
```

---

## 参数说明

| 参数 | 说明 |
| --- | --- |
| `--mod` / `--out` | mod 根目录 / 输出目录（默认不覆盖原 mod） |
| `--threshold` | 像素面积上限，超过就细化（默认 200） |
| `--target` | 目标每块面积，`0` 表示用 `threshold`（默认 0）；拆分数 = ⌈面积/目标⌉ |
| `--min-part` | 新地块最小面积，小于它的碎块会并回邻居（默认 80） |
| `--max-parts` | 单块最多切成几份（默认 64） |
| `--scope` | 检测类型：`all` / `land` / `sea` / `lake`（默认 `all`） |
| `--continents` | 只处理这些大洲 ID，逗号分隔（默认 `1,2,3`） |
| `--include` / `--only` / `--exclude` | 强制细化 / 仅处理这些 / 排除（默认排除 `85,69,86,114`） |
| `--gradient` | 渐变密度图；近白区域默认不细化，黑色区域按目标面积细化 |
| `--grad-allow-white` | 关闭“近白区域跳过”规则，恢复原先白色也可按目标面积细化的行为 |
| `--parts` | 指定某块拆成几份，如 `225:4,300:2` |
| `--analyze` | 只分析不写入 |
| `--audit` | 只做完整性检查 |
| `--no-repair` | 关闭拆分后的完整性修复 |
| `--no-canal-complete` | 关闭运河辅助补全（默认只补全能与明确 `*_CANAL` 规则唯一匹配的连线） |
| `--railway-hops` | 铁路断点重连允许的最大插入跳数（默认 25） |
| `--keep-invalid` | 保留战略区/州里不存在的省份 ID（默认会清除） |
| `--scale` | 预览图缩放（默认 1.0 = 原尺寸） |
| `--seed` | 随机种子（结果可复现） |

---

## 工作原理

1. 读 `provinces.bmp` 按颜色统计每个地块像素面积；`definition.csv` 把颜色映射到省份 ID。
2. 按规则筛选"过大"地块。
3. 每块用 **KMeans 撒种子 + 多源 BFS(测地线)** 切成 k 个**保证连通**的小块；碎块并回边界接触最多的邻居；**面积最大的一块保留原 ID**，其余分配新 ID（从当前最大 ID+1 起）。
4. **完整性修复**（`--repair`，默认开）：
   - **十字交叉边界**：消除 2×2 里的"对角线歧义"（棋盘格 `A B / B A` 与 4 个省份相遇），保证能明确判断相邻关系。每个交叉只改一个像素，并保证不弄断原省份。
   - **孤立/不连通地块**：一个地块的像素必须 4-连通（上下左右）。不连通的多余碎片会并到共享边界最长、且不会新增交叉的邻居省份；迭代到全部连通。
   - **战略区/州**：保证每个地块只属于一个，重复归属按"多数邻居所在"判定；`#` 注释不会被当成地块；不存在的省份 ID 默认清除（`--keep-invalid` 可保留）。
   - **铁路**：`railways.txt` 格式是 `<等级> <省份数> <省份...>`。逐对检查相邻性；断开的用 BFS 插入**陆地**中间地块重连（不会插到海面/湖泊上；跨海铁路靠海峡判定，不会被误改）。
   - **海峡/运河连线**：普通海峡按地图像素邻接检查并修正被细化的 From/To/Through。运河规则的 `required_provinces` 则单独按实际河岸像素校准，不会因为某省被拆分就盲目纳入所有子省；无法唯一识别对应河段时保留原规则并写入审计报告。
   - **运河省份判定（`adjacencies.csv` + `adjacency_rules.txt` + `provinces.bmp` + `rivers.bmp`）**：先以 CSV 的 `adjacency_rule_name` 与规则文件里的 `name` 建立运河基准清单。将索引格式的 `rivers.bmp` 像素叠加到 `provinces.bmp` 省份 ID 图上后，依据每条记录的 From/To 水域接触点，沿河流像素的上下左右连接追踪指定的 From→To 路径；**不会把整条连通河网的陆地都当成运河两岸**。统计范围仅包括这条选定路径直接接触到的陆地省份，因此支流沿线省份不会仅因与主河道汇流就被补入。路径不唯一（例如环路/多条等长路径）时标记 `UNRESOLVED_ROUTE` 并保留原规则；其他未匹配情况也会留档，不猜路线。
     - `required_provinces` 仅保留仍接触选定路径的旧 ID，并补入该路径实际接触的省份；非运河规则不修改。完整清单、整条连通分量与实际路线像素数、两岸省份、移除/新增 ID 和未解决原因写入 `canal_province_audit.csv`；字段 `component_pixels` 与 `route_pixels`、`component_banks` 与 `off_route_banks` 可直接核对整条连通河网和选定主路线的差异。
     - 对 `adjacencies.csv` 的 From/To/Through 修复和缺失连接补全仍需符合明确声明的运河规则，不会单凭普通河流像素创建可通航运河。
   - **沿海属性**：切割沿海地块时，把"最大且临海"的那一块留给原 ID，保证**原地块拆分后仍然是沿岸的**；新地块的 coastal 按其是否临海重算（海面/湖泊地块恒为 false）。
5. 渐变密度图：默认黑色目标面积 `80px`、白色目标面积 `250px`。启用白色保护时，近白像素不会被直接切分；横跨黑白边界的混合地块只处理非白像素，整块纯白的地块会跳过。白色保护不拦截显式 `--include` / `--parts` 指定的 ID。命令行可用 `--grad-allow-white` 关闭白色保护。
6. 陆地邻接均衡：细化前后自动检测相邻陆地地块面积差异。默认当一侧面积超过另一侧约 3 倍时，尝试在共享边界上移动少量像素来修整形状；只考虑陆地与陆地，不把海洋或湖泊当作邻居。带密度图时会比较边界两侧实际像素灰度，避免因整块地平均灰度相差大而错过同一密度区内的修整；无密度图时也会运行。为避免过度改变原图，单个供体地块的移动量有上限，并保护海岸线像素。
7. 生成预览：**修改前**（目标地块半透明红填充 + 黄描边 + 图例）和**修改后**（新地图 + 白省界）。

## 只修复、不拆分

如果只是想修好已有地图（重复归属 / 十字交叉 / 铁路 / 海峡）：把阈值设得比最大地块还大，就不会有新拆分，只跑修复：

```bat
py -3.13 run_cli.py --mod "..\Project_IRIS" --out "D:\地块细化\output_repair" --threshold 999999999 --scope all
```

---

## 输出内容

```
output/
  map/provinces.bmp              细化后的地图
  map/definition.csv             追加了新省份行
  map/strategicregions/*.txt     有改动的战略区
  map/adjacencies.csv            修正过的海峡/连线(如有)
  map/railways.txt               修正过的铁路(如有)
  map/adjacency_rules.txt        修正过的海峡规则(如有)
  history/states/*.txt           有改动的州(如有)
  report.csv / report.txt        报告（每块拆成了哪些新 ID + 修复记录）
  canal_province_audit.csv       运河规则与河岸省份判定明细
  preview_before.png / preview_after.png
  changed_files.txt              改动清单
```

装回 mod：把 `output/map`、`output/history` 按相同路径覆盖进 mod。

---

## 注意事项

- 默认不修改原 mod，只写输出目录。
- `provinces.bmp` 保持 24 位、同尺寸；末尾填充字节会补齐。
- 新地块继承原地块的 地形/大洲/类型；`coastal` 则按"是否临海"重算（沿海地块拆分后原地块仍是沿海）。
- 新地块不会自动获得 `buildings.txt` / `supply_nodes.txt` / 单位堆叠条目（原 ID 的条目仍属于原地块）。
- **大洲别乱选**：如果给了大洲过滤却选错，会漏拆。
- 拆得太多会让省份总数暴涨，注意 HOI4 对地块总数的引擎上限。
- `adjacencies.csv` 里若本来就存在"Through 与两端不相邻"的条目（本 mod 原有约 110 条），工具只报告、不强行改。
