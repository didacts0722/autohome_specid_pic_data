# autohome_specid_pic_data

汽车之家（autohome）车型颜色/图片取数工具，两条流水线：

1. **crawler_url_color_exterior_cnt.py** — 抓取每个车型的颜色列表及各颜色外观图片数量
2. **crawler_specid_pic_by_color.py** — 按 车型+颜色 下载外观图片到 `out_put/<spec_id>_<颜色名>/`

## 安装

```bash
pip install -r requirements.txt
```

## 用法

### 1. 颜色列表 + 外观图片数量

```bash
# 全量
python crawler_url_color_exterior_cnt.py

# 只测试前 20 个车型，放慢速率
python crawler_url_color_exterior_cnt.py --limit 20 --rate 5

# 自定义输入输出
python crawler_url_color_exterior_cnt.py --input spec_id.txt --output out.csv
```

- 输入文件每行格式：`系列ID_车型ID.Html$spec_id$车型名`
- 输出 CSV 列：`url, series_id, spec_id, car_name, color_id, value, name, piccount, color_url, appearance_count`
- 断点续传：已有 CSV 中完成的 `(spec_id, color_id)` 会自动跳过，中断后直接重跑即可续传
- 失败任务写入 `error_tasks.json`，下次运行自动优先重试

### 2. 按颜色下载外观图片

```bash
# 全量
python crawler_specid_pic_by_color.py

# 只测试前 10 条，4 并发
python crawler_specid_pic_by_color.py --limit 10 --workers 4 --rate 4

# 只重试之前失败的颜色
python crawler_specid_pic_by_color.py --retry
```

- 输入 Excel 需含列：`color_url`, `spec_id`, `name`
- 断点续传：进度文件 `download_progress.json`；已完成目录/已下载图片会自动跳过
- 失败任务写入 `download_error_tasks.json`，可用 `--retry` 重试

## 说明

- 两个脚本共享 `common.py`（日志、全局限速令牌桶、每线程复用 Session、JSON 读写）
- 请求速率通过 `--rate` 控制整体 QPS，提高并发时请留意目标站点限流
