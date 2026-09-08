# -*- coding: utf-8 -*-
"""
把若干批次 CSV（0715/0908…）合并构建/重建总表 master.csv。

合并规则:
- 去重键 (spec_id, color_id)，全文件唯一
- 同一键出现在多个批次时: updated_at 较新者胜；updated_at 相同（并列）时命令行靠后者胜
- updated_at 来源优先级: 批次内已有 updated_at 列 > 命令行 --date > 文件名日期后缀（*_260715.csv → 2026-07-15）> 文件修改日

用法:
  python merge_pic_color_cnt.py --out spec_id_pic_color_cnt_master.csv a.csv b.csv
  python merge_pic_color_cnt.py --out master.csv --date 2026-07-15 legacy_no_date.csv
  python merge_pic_color_cnt.py --out master.csv a.csv --dry-run   # 只统计不写文件
"""
import argparse
import csv
import os
import re
import sys
import time

from master_csv import CANONICAL_COLS, save_master

DATE_IN_NAME = re.compile(r'_(\d{6})\.csv$')


def infer_batch_date(path, default_date=None):
    """按 文件名_YYMMDD.csv → YYYY-MM-DD；否则 default_date；再否则文件 mtime 日期。"""
    if default_date:
        return default_date
    m = DATE_IN_NAME.search(os.path.basename(path))
    if m:
        ymd = m.group(1)
        try:
            return f'20{ymd[0:2]}-{ymd[2:4]}-{ymd[4:6]}'
        except Exception:
            pass
    return time.strftime('%Y-%m-%d', time.localtime(os.path.getmtime(path)))


def read_batch(path, default_date=None):
    """读取批次并给每行赋 updated_at（缺失时）。返回 (rows_by_key, 统计)。"""
    date = infer_batch_date(path, default_date)
    ts = f'{date} 00:00:00'
    rows = {}
    total = 0
    with open(path, 'r', encoding='utf-8-sig', newline='') as f:
        for r in csv.DictReader(f):
            try:
                key = (int(r['spec_id']), int(r['color_id']))
            except (KeyError, ValueError):
                continue
            total += 1
            r.setdefault('updated_at', ts)
            if not r.get('updated_at'):
                r['updated_at'] = ts
            rows[key] = r
    return rows, total


def merge_batches(paths, default_date=None):
    merged = {}
    conflicts = 0
    src_order = {p: i for i, p in enumerate(paths)}
    for p in paths:
        rows, total = read_batch(p, default_date)
        for key, row in rows.items():
            if key not in merged:
                merged[key] = (row, p)
                continue
            old_row, old_src = merged[key]
            old_ts = old_row.get('updated_at', '')
            new_ts = row.get('updated_at', '')
            if new_ts > old_ts or (new_ts == old_ts and src_order[p] > src_order[old_src]):
                merged[key] = (row, p)
                conflicts += 1
    return merged, conflicts


def main():
    p = argparse.ArgumentParser(description='合并批次 CSV 构建总表 master')
    p.add_argument('--out', required=True, help='输出总表路径')
    p.add_argument('--date', default=None,
                   help='为无日期批次统一指定的抓取日期 YYYY-MM-DD（否则按文件名/修改日推断）')
    p.add_argument('files', nargs='+', help='一个或多个批次 CSV')
    p.add_argument('--dry-run', action='store_true', help='只统计不写文件')
    args = p.parse_args()

    merged, conflicts = merge_batches(args.files, args.date)
    n_in = 0
    for f in args.files:
        _, t = read_batch(f, args.date)
        n_in += t
    print(f'输入批次 {len(args.files)} 个, 总行 {n_in}')
    print(f'合并后唯一键: {len(merged)}  (去重移除 {n_in - len(merged)})')
    print(f'同键冲突(较新值覆盖): {conflicts}')

    if args.dry_run:
        return 0
    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    n = save_master(args.out, {k: v[0] for k, v in merged.items()})
    print(f'已写入: {args.out} ({n} 行)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
