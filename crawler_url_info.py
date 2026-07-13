import os
import re
import json
import csv
import requests
from typing import Dict, Optional

# 需要提取的分类 ID 及其对应的名称
CATEGORY_MAP = {
    1: "外观",
    10: "内饰",
    3: "座椅",
    12: "细节"
}

def fetch_pic_counts(url: str) -> Optional[Dict[str, int]]:
    """从汽车之家图片列表页提取各分类图片数量，返回字典或 None"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"[错误] 请求失败 {url} : {e}")
        return None

    # 提取 __NEXT_DATA__ 的 JSON
    pattern = r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>'
    match = re.search(pattern, resp.text, re.DOTALL)
    if not match:
        print(f"[错误] 未找到 __NEXT_DATA__，页面可能不正确: {url}")
        return None

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as e:
        print(f"[错误] JSON 解析失败 {url} : {e}")
        return None

    # 导航到 picinfo.callist
    page_props = data.get("props", {}).get("pageProps", {})
    series_pic_list = page_props.get("SeriesPicList", {})
    pic_info = series_pic_list.get("picinfo", {})
    callist = pic_info.get("callist", [])

    counts = {}
    for item in callist:
        claid = item.get("claid")
        if claid in CATEGORY_MAP:
            counts[CATEGORY_MAP[claid]] = item.get("total", 0)

    # 补全缺失的分类
    for name in CATEGORY_MAP.values():
        counts.setdefault(name, 0)

    return counts


def is_error_row(row: dict) -> bool:
    """判断一行数据是否包含错误（任一数值字段不是整数）"""
    for key in ["外观", "内饰", "座椅", "细节"]:
        try:
            int(row.get(key, ""))
        except (ValueError, TypeError):
            return True
    return False


def main():
    input_file = "spec_id.txt"
    output_file = "result.csv"

    # ================== 1. 检查是否已存在结果文件 ==================
    if os.path.exists(output_file):
        # 读取现有 CSV
        with open(output_file, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        # 找出所有错误行
        error_rows = [row for row in rows if is_error_row(row)]

        if not error_rows:
            print("✅ 所有记录都已成功，无需重试。")
            return

        print(f"🔍 发现 {len(error_rows)} 条错误记录，开始重新抓取...")

        for row in error_rows:
            url = row["URL"]
            car_name = row["车型名称"]
            print(f"  重试: {car_name} ({url})")
            counts = fetch_pic_counts(url)
            if counts:
                # 更新该行数据
                row["外观"] = counts.get("外观", 0)
                row["内饰"] = counts.get("内饰", 0)
                row["座椅"] = counts.get("座椅", 0)
                row["细节"] = counts.get("细节", 0)
                print(f"    ✅ 成功: {counts}")
            else:
                print(f"    ❌ 重试失败，保留错误标记")

        # 写回（覆盖原文件）
        with open(output_file, "w", newline="", encoding="utf-8-sig") as f:
            fieldnames = ["URL", "车型名称", "外观", "内饰", "座椅", "细节"]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        print(f"🎉 重试完成，已更新 {output_file}")
        return

    # ================== 2. 首次运行：全量抓取 ==================
    # 读取 spec_id.txt
    try:
        with open(input_file, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        print(f"错误：文件 {input_file} 不存在，请创建并放入数据。")
        return

    if not lines:
        print("警告：spec_id.txt 为空。")
        return

    # 准备 CSV 写入
    fieldnames = ["URL", "车型名称", "外观", "内饰", "座椅", "细节"]
    with open(output_file, "w", newline="", encoding="utf-8-sig") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()

        for line in lines:
            parts = line.split('$')
            if len(parts) < 3:
                print(f"警告：行格式不正确，跳过: {line}")
                continue

            spec_id = parts[1]
            car_name = parts[2]
            prefix_part = parts[0]
            series_id = prefix_part.split('_')[0] if '_' in prefix_part else prefix_part

            url = f"https://www.autohome.com.cn/cars/imglist-x-x-{series_id}-{spec_id}-x-x-x-x-x-1.html"

            print(f"正在处理: {car_name} (seriesId={series_id}, specId={spec_id})")
            counts = fetch_pic_counts(url)

            if counts:
                row = {
                    "URL": url,
                    "车型名称": car_name,
                    "外观": counts.get("外观", 0),
                    "内饰": counts.get("内饰", 0),
                    "座椅": counts.get("座椅", 0),
                    "细节": counts.get("细节", 0)
                }
                writer.writerow(row)
                print(f"  结果: {counts}")
            else:
                row = {
                    "URL": url,
                    "车型名称": car_name,
                    "外观": "错误",
                    "内饰": "错误",
                    "座椅": "错误",
                    "细节": "错误"
                }
                writer.writerow(row)
                print(f"  结果: 获取失败，标记为错误")

    print(f"\n✅ 首次抓取完成，结果已保存至 {output_file}")
    print("💡 如需重试错误记录，请再次运行本脚本（不删除 result.csv）")


if __name__ == "__main__":
    main()