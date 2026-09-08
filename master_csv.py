# -*- coding: utf-8 -*-
"""
总表（master CSV）共享读写库。

总表规约（2026-09-08 定版）:
- 载体: 单个 CSV 文件, 编码 utf-8-sig（Excel 兼容）
- 列: 流水线 1 的 10 列 + 末尾追加 updated_at（抓取时间戳）
- 去重键: (spec_id, color_id), 全文件唯一
- updated_at 格式: 'YYYY-MM-DD HH:MM:SS'（ISO 文本序 = 时间序, 用于合并优先级与审计）
- 行序: 按 (int(spec_id), int(color_id)) 升序 —— 确定性输出, 便于 diff/入库
- 写入: 先写同目录临时文件再 os.replace 原子替换
"""
import csv
import os

CANONICAL_COLS = [
    'url', 'series_id', 'spec_id', 'car_name', 'color_id', 'value',
    'name', 'piccount', 'color_url', 'appearance_count', 'updated_at',
]


def load_master(path):
    """读取总表。返回 (rows, specs, exists)。

    rows: {(int spec_id, int color_id): row dict}（缺列自动补 ''）
    specs: 总表中出现过的所有 spec_id 集合（增量过滤依据）
    exists: 文件是否存在且可读
    """
    rows = {}
    specs = set()
    exists = os.path.exists(path)
    if not exists:
        return rows, specs, False
    with open(path, 'r', encoding='utf-8-sig', newline='') as f:
        for r in csv.DictReader(f):
            try:
                key = (int(r['spec_id']), int(r['color_id']))
            except (KeyError, ValueError):
                continue
            rows[key] = r
            specs.add(key[0])
    return rows, specs, True


def make_row(task, appearance_count, updated_at):
    """由爬虫颜色任务构建总表行（10 列 + updated_at）。

    task 含 url/series_id/spec_id/car_name/color_id/value/color_name/piccount/color_url。
    """
    return {
        'url': task.get('url', ''),
        'series_id': task.get('series_id', ''),
        'spec_id': task.get('spec_id', ''),
        'car_name': task.get('car_name', ''),
        'color_id': task.get('color_id', ''),
        'value': task.get('value', '') or '',
        'name': task.get('color_name', '') or task.get('name', ''),
        'piccount': task.get('piccount', 0),
        'color_url': task.get('color_url', ''),
        'appearance_count': appearance_count,
        'updated_at': updated_at,
    }


def save_master(path, rows, log=None):
    """把 rows 字典（键=(spec_id,color_id)）原子写出为总表文件（升序、utf-8-sig）。"""
    def _warn(msg):
        if log:
            log.warning(msg)

    items = sorted(rows.items(), key=lambda kv: (int(kv[0][0]), int(kv[0][1])))
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=CANONICAL_COLS, extrasaction='ignore')
        writer.writeheader()
        for (si, ci), row in items:
            out = {col: '' for col in CANONICAL_COLS}
            for col in CANONICAL_COLS:
                if col in row and row[col] is not None:
                    out[col] = row[col]
            writer.writerow(out)
    os.replace(tmp, path)
    return len(items)
