# -*- coding: utf-8 -*-
"""
历史批次 CSV 清洗：按 (spec_id, color_id) 去重（保留首次出现行）。

背景：早期版本输出文件曾被多次重叠抓取/多实例追加，导致同一
(spec_id, color_id) 出现多行（见 docs/代码走查报告 P1）。当前
crawler_url_color_exterior_cnt.py 已从源头防重复（排他锁 + 任务去重 +
解析去重），本脚本只用于清洗存量文件。

用法:
  python dedupe_pic_color_cnt.py <csv>            # 就地去重（写临时文件后原子替换）
  python dedupe_pic_color_cnt.py <csv> --dry-run  # 只看统计不动文件

说明:
- 读取/写出均 utf-8-sig（与爬虫输出一致，Excel 兼容）
- 列结构原样保留（以文件表头为准），仅按 spec_id/color_id 去重
- 去重键缺失时（列不存在）报错退出，不静默改文件
"""
import argparse
import csv
import os
import sys
import tempfile


def dedupe_csv(path: str, dry_run: bool = False):
    if not os.path.exists(path):
        print(f'文件不存在: {path}')
        return 1

    with open(path, 'r', encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        if fieldnames is None or 'spec_id' not in fieldnames or 'color_id' not in fieldnames:
            print(f'表头缺少 spec_id/color_id 列，跳过: {fieldnames}')
            return 1
        seen = set()
        kept = []
        removed = 0
        for row in reader:
            key = (row['spec_id'], row['color_id'])
            if key in seen:
                removed += 1
                continue
            seen.add(key)
            kept.append(row)

    total = len(kept) + removed
    print(f'{os.path.basename(path)}: 总行 {total} -> 去重后 {len(kept)}（移除重复 {removed}）')

    if dry_run or removed == 0:
        return 0

    # 写临时文件（同目录，保证 os.replace 原子性）后替换原文件
    tmp_path = path + '.tmp_dedupe'
    with open(tmp_path, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(kept)
    os.replace(tmp_path, path)
    print(f'已写入: {path}')
    return 0


def main():
    p = argparse.ArgumentParser(description='按 (spec_id, color_id) 清洗爬虫输出 CSV')
    p.add_argument('csv', help='要清洗的 CSV 文件路径')
    p.add_argument('--dry-run', action='store_true', help='只统计，不写文件')
    args = p.parse_args()
    sys.exit(dedupe_csv(args.csv, args.dry_run))


if __name__ == '__main__':
    main()
