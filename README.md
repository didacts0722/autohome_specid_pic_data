# autohome_specid_pic_data

汽车之家（autohome）车型 **颜色列表 + 各颜色外观图片数量** 取数工具。

> 维护范围（2026-09-08 定版）：**流水线 1** 是本项目的唯一维护对象——
> 逐车型抓颜色列表及各颜色外观图片数量（`spec_id × color_id → appearance_count`）。
> 原"按颜色下载外观图片"流水线 2 **已移交他处**，代码仅作历史存档保留，勿用于新任务（见文末归档节）。

## 流水线 1：颜色列表 + 各颜色外观图片数量

```
输入 spec_id.txt（每行 系列ID_车型ID.Html$spec_id$车型名）
   │  read_spec_id_txt()      按 $ 拆 3 段，坏行跳过并记 warning
   ▼
阶段一 抓车型页 __NEXT_DATA__ → 提取颜色列表（colorList/colors/…，解析时按 id 去重）
   │  并发 + 全局令牌桶限速；失败带 stage 标记写入 error_tasks.json
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

**输入 `spec_id.txt`**
- 每行一条记录：`系列ID_车型ID.Html$spec_id$车型名`（`$` 分隔 3 段）
- 编码 UTF-8（无 BOM）；LF / CRLF 行尾均可；格式错误的行跳过并告警
- 小样样例：`spec_id_sample_test.txt`（125 条）；全量文件为本地数据，不入库

**输出 CSV**
- 列（顺序固定 10 列）：`url, series_id, spec_id, car_name, color_id, value, name, piccount, color_url, appearance_count`
- 编码 `utf-8-sig`（带 BOM，Excel 直接打开中文不乱码）
- **去重键 `(spec_id, color_id)`：任一输出文件中该键全文件唯一**
- 断点续传即按此键判断"已完成"，因此**增量续传必须沿用同一输出文件**；新批次另存新文件

**运行**

```bash
# 全量
python crawler_url_color_exterior_cnt.py

# 测试前 20 个车型、放慢速率
python crawler_url_color_exterior_cnt.py --limit 20 --rate 5

# 自定义输入输出
python crawler_url_color_exterior_cnt.py --input spec_id.txt --output spec_id_pic_color_cnt_20260908.csv
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--input` | `spec_id.txt` | 车型清单 |
| `--output` | `spec_id_pic_color_cnt.csv` | 输出 CSV |
| `--error-file` | `error_tasks.json` | 失败任务，下次运行自动优先重试 |
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

## 数据产物（本地产物，不入库）

| 文件 | 说明 | 去重状态（2026-09-08） |
|---|---|---|
| `spec_id_pic_color_cnt_260715.csv` | 07-15 批次大库 | 775,920 → **709,348** 行（移除重复 66,572） |
| `spec_id_pic_color_cnt_260908.csv` | 09-08 批次 | 17,246 → **11,518** 行（移除重复 5,728） |
| `spec_id_pic_color_cnt_test.csv` | 测试集（小，入库跟踪） | 2,702 行 |
| `spec_id_url_sample_20260715_1.xlsx` | 流水线 2 输入样例（归档） | — |

## 文件清单

```
crawler_url_color_exterior_cnt.py   流水线 1：颜色列表 + 外观图片数量（维护中）
common.py                           共享底座：令牌桶限速 / Session 复用 / JSON 原子读写
dedupe_pic_color_cnt.py             历史 CSV 按 (spec_id, color_id) 去重清洗
crawler_specid_pic_by_color.py      流水线 2（已移交，仅归档）
spec_id_sample_test.txt             输入样例
spec_id_pic_color_cnt_test.csv      输出小样例
spec_id_url_sample_20260715_1.xlsx  流水线 2 输入样例
docs/运行简报.md                     项目运行简报（每轮增量维护）
docs/代码走查报告_20260908.md        走查报告（含重复行问题根因与修复记录）
```

## 归档：流水线 2（按颜色下载外观图片，已移交）

`crawler_specid_pic_by_color.py` 及其输入样例 `spec_id_url_sample_20260715_1.xlsx`
为历史存档：流水线整体已移交他处，**不再维护、勿用于新任务**。文件头已标注。
