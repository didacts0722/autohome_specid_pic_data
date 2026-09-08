# autohome_specid_pic_data

汽车之家（autohome）车型 **颜色列表 + 各颜色外观图片数量** 取数工具。

> 维护范围（2026-09-08 定版）：**流水线 1** 是本项目的唯一维护对象——
> 逐车型抓颜色列表及各颜色外观图片数量（`spec_id × color_id → appearance_count`）。
> 原"按颜色下载外观图片"流水线 2 **已移交他处**，代码仅作历史存档保留，勿用于新任务（见文末归档节）。

**目录结构（2026-09-08 整理）**

```
根目录            代码(*.py)、README、requirements.txt、.gitignore
docs/             运行简报、代码走查报告
input/            车型清单输入（spec_id.txt 等）
output/           所有数据产物（批次 CSV、总表 master、错误文件；不入库）
archived/         流水线 2（已移交）的样例文件
```

## 流水线 1：颜色列表 + 各颜色外观图片数量

```
输入 input/spec_id.txt（每行 系列ID_车型ID.Html$spec_id$车型名）
   │  read_spec_id_txt()      按 $ 拆 3 段，坏行跳过并记 warning
   ▼
阶段一 抓车型页 __NEXT_DATA__ → 提取颜色列表（colorList/colors/…，解析时按 id 去重）
   │  并发 + 全局令牌桶限速；失败带 stage 标记写入 output/error_tasks.json（默认）
   ▼
生成颜色任务（url, series_id, spec_id, car_name, color_id, value, name, piccount, color_url）
   │  任务列表按 (spec_id, color_id) 防御性去重
   ▼
阶段二 逐颜色抓外观页 __NEXT_DATA__ callist[claid=1].total → appearance_count
   │  "解析失败"与"真为 0"区分：失败进错误文件重试，不写 CSV
   ▼
输出 CSV（列见下；写入前过滤已完成任务 = 断点续传）
```

### 确定性输入/输出规约

**输入 `input/spec_id.txt`**（脚本默认从此路径读取）
- 每行一条记录：`系列ID_车型ID.Html$spec_id$车型名`（`$` 分隔 3 段）
- 编码 UTF-8（无 BOM）；LF / CRLF 行尾均可；格式错误的行跳过并告警
- 同一 spec 可能出现在多行（同一车挂在多个系列/车型页下）：脚本自动按 `spec_id` 去重、保留首行
- 小样样例：`input/spec_id_sample_test.txt`（125 条）；全量文件为本地数据，不入库

**输出 CSV（默认写入 `output/`）**
- 列（顺序固定 10 列）：`url, series_id, spec_id, car_name, color_id, value, name, piccount, color_url, appearance_count`
- 编码 `utf-8-sig`（带 BOM，Excel 直接打开中文不乱码）
- **去重键 `(spec_id, color_id)`：任一输出文件中该键全文件唯一**
- 断点续传即按此键判断"已完成"，因此**增量续传必须沿用同一输出文件**；新批次建议命名 `output/spec_id_pic_color_cnt_YYYYMMDD.csv`
- 目录不存在时脚本自动创建；错误文件默认 `output/error_tasks.json`

**运行**

```bash
# 全量（默认读 input/spec_id.txt，写 output/ 下默认文件）
python crawler_url_color_exterior_cnt.py

# 测试前 20 个车型、放慢速率
python crawler_url_color_exterior_cnt.py --limit 20 --rate 5

# 自定义输入输出
python crawler_url_color_exterior_cnt.py --input input/spec_id.txt \
    --output output/spec_id_pic_color_cnt_20260908.csv
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--input` | `input/spec_id.txt` | 车型清单 |
| `--output` | `output/spec_id_pic_color_cnt.csv` | 输出 CSV（目录自动创建） |
| `--error-file` | `output/error_tasks.json` | 失败任务，下次运行自动优先重试 |
| `--workers` | 20 | 线程池并发 |
| `--rate` | 15 | 全局请求速率（次/秒，令牌桶） |
| `--limit` | — | 只处理前 N 个车型（测试用） |

**正确性/幂等保证（2026-09-08 加固，对应历史重复问题）**
1. **排他锁**：启动时创建 `<output>.lock`，同一输出文件同时只允许一个实例；
   多实例并发写同一文件是历史上 `(spec_id, color_id)` 重复行的主因。若误报
   （上次被强杀残留），删除对应 `.lock` 文件即可。
2. **任务级去重**：颜色任务按 `(spec_id, color_id)` 只保留首次出现。
3. **解析级去重**：页面颜色列表按 id 去重（list 与 color/othercolor 分支统一处理）。
4. 历史脏数据清洗：`python dedupe_pic_color_cnt.py <csv> [--dry-run]`（保留首次出现行；**清洗前自动备份** `<csv>.bak_<时间戳>`，确认后自行删备份）。

## 总表（master）：合并多批次 + 增量更新

总表是全量结果的**单张主表**：行键 `(spec_id, color_id)` 全文件唯一，列 = 原 10 列 + 末尾 `updated_at`（抓取时间，ISO 文本序可排序），行按 `(spec, color)` 升序（确定性输出）。总表体积大，属本地产物不入库。

**① 初始构建 / 重建总表**（合并任意批次 CSV，同键 `updated_at` 较新者胜，并列时命令行靠后者胜）

```bash
python merge_pic_color_cnt.py --out output/spec_id_pic_color_cnt_master.csv \
    output/spec_id_pic_color_cnt_260715.csv output/spec_id_pic_color_cnt_260908.csv
```

- `updated_at` 自动按文件名日期推断（`*_260715.csv → 2026-07-15`）；也可 `--date YYYY-MM-DD` 统一指定；`--dry-run` 只统计；`--out` 目录不存在时自动创建

**② 增量抓取并写入总表**（数据源 = 各期 `input/spec_id.txt`）

```bash
python crawler_url_color_exterior_cnt.py --master output/spec_id_pic_color_cnt_master.csv \
    --input input/spec_id.txt
```

- **只补缺失**：只抓总表尚未覆盖的 spec；已存在的 `(spec_id, color_id)` 不刷新旧值
- 新行 `updated_at` = 抓取时刻；**幂等**：中断后重跑同一命令即续传
- 失败任务默认落 `output/master_error_tasks.json`，下次运行自动优先重试
- `--limit N` 按"新增 spec"计，可小样试跑；`--rate/--workers` 同批次模式

**存量状态（2026-09-08）**：master 714,890 行 / 54,348 spec（0715∪0908 去重）；当前 `spec_id.txt` 共 77,365 个 spec，其中未覆盖约 **23,029** 个 = 首轮增量抓取规模。

## 数据产物（本地产物，不入库）

| 文件 | 说明 | 去重状态（2026-09-08） |
|---|---|---|
| `output/spec_id_pic_color_cnt_master.csv` | **总表**（0715∪0908 合并，11 列含 updated_at，不入库） | 714,890 行 / 0 重复 / 54,348 spec |
| `output/spec_id_pic_color_cnt_260715.csv` | 07-15 批次大库 | 775,920 → **709,348** 行（移除重复 66,572） |
| `output/spec_id_pic_color_cnt_260908.csv` | 09-08 批次 | 17,246 → **11,518** 行（移除重复 5,728） |
| `output/spec_id_pic_color_cnt_test.csv` | 测试集（小，入库跟踪） | 2,702 行 |
| `input/spec_id.txt` | 车型清单输入（入库跟踪） | 77,365 spec（去重后） |
| `input/spec_id_sample_test.txt` | 输入小样 | 125 条 |
| `archived/spec_id_url_sample_20260715_1.xlsx` | 流水线 2 输入样例（归档） | — |

## 文件清单

```
crawler_url_color_exterior_cnt.py   流水线 1：颜色列表 + 外观图片数量（维护中；--master 总表增量模式）
common.py                           共享底座：令牌桶限速 / Session 复用 / JSON 原子读写
master_csv.py                       总表规约与读写（11 列含 updated_at、排序、原子保存）
merge_pic_color_cnt.py              合并批次 CSV 构建/重建总表（同键较新覆盖）
dedupe_pic_color_cnt.py             历史 CSV 按 (spec_id, color_id) 去重清洗（自动备份）
crawler_specid_pic_by_color.py      流水线 2（已移交，仅归档）
input/                              车型清单输入（spec_id.txt、spec_id_sample_test.txt）
output/                             数据产物：批次 CSV、总表 master、错误文件（不入库）
archived/                           流水线 2 样例（spec_id_url_sample_20260715_1.xlsx）
docs/运行简报.md                     项目运行简报（每轮增量维护）
docs/代码走查报告_20260908.md        走查报告（含重复行问题根因与修复记录）
```

## 归档：流水线 2（按颜色下载外观图片，已移交）

`crawler_specid_pic_by_color.py` 及其输入样例 `archived/spec_id_url_sample_20260715_1.xlsx`
为历史存档：流水线整体已移交他处，**不再维护、勿用于新任务**。文件头已标注。
