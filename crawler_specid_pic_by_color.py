# ==================== 配置区域 ====================
EXCEL_FILE = "spec_id_url_sample_20260715_1.xlsx"   # 源 Excel 文件名
OUTPUT_ROOT = "out_put"                  # 图片保存根目录
DOWNLOAD_COUNT = 8                       # 每种颜色下载前几张外观图片
REQUEST_DELAY = 1                        # 请求间隔（秒），避免过频
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
REQUEST_TIMEOUT = 15                     # 页面请求超时（秒）
IMAGE_TIMEOUT = 10                       # 单张图片下载超时（秒）
# ==================================================

import re
import json
import requests
import os
import pandas as pd
import time

def clean_name(name):
    """清理文件夹名称中的非法字符"""
    return re.sub(r'[\\/*?:"<>|]', "", str(name)).strip()

def download_images(url, spec_id, color_name):
    """
    从颜色图片列表页下载外观图片
    """
    try:
        # 构建输出目录
        folder_name = f"{spec_id}_{clean_name(color_name)}"
        output_dir = os.path.join(OUTPUT_ROOT, folder_name)
        os.makedirs(output_dir, exist_ok=True)

        print(f"处理 {spec_id} - {color_name}，URL: {url}")

        # 请求页面
        headers = {"User-Agent": USER_AGENT}
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()

        # 提取 JSON 数据
        match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', response.text, re.DOTALL)
        if not match:
            print(f"⚠️ 未找到 __NEXT_DATA__，URL: {url}")
            return
        data = json.loads(match.group(1))

        # 解析外观图片列表
        callist = data.get("props", {}).get("pageProps", {}).get("SeriesPicList", {}).get("picinfo", {}).get("callist", [])
        image_list = []
        for item in callist:
            if item.get("claid") == 1:   # 1 = 外观
                image_list = item.get("list", [])
                break

        if not image_list:
            print(f"⚠️ 未找到外观图片，URL: {url}")
            return

        # 取前 N 张
        target_images = image_list[:DOWNLOAD_COUNT]
        print(f"找到 {len(target_images)} 张图片，下载至 {output_dir} ...")

        for idx, img_info in enumerate(target_images):
            img_url = img_info.get("picpath")
            if not img_url:
                continue
            if img_url.startswith('//'):
                img_url = 'https:' + img_url

            try:
                img_resp = requests.get(img_url, headers=headers, timeout=IMAGE_TIMEOUT)
                img_resp.raise_for_status()

                filename = img_url.split('/')[-1].split('?')[0]
                if not filename.endswith(('.jpg', '.jpeg', '.png', '.webp')):
                    filename += '.jpg'

                save_path = os.path.join(output_dir, f"{idx+1:02d}_{filename}")
                with open(save_path, 'wb') as f:
                    f.write(img_resp.content)
                print(f"  ✓ 已下载: {save_path}")
            except Exception as e:
                print(f"  ✗ 下载失败: {img_url}, 错误: {e}")

        print(f"完成 {spec_id} - {color_name}\n")

    except Exception as e:
        print(f"❌ 处理失败 {spec_id} - {color_name}: {e}")

def main():
    if not os.path.exists(EXCEL_FILE):
        print(f"错误：文件 {EXCEL_FILE} 不存在，请检查路径。")
        return

    # 读取 Excel
    try:
        df = pd.read_excel(EXCEL_FILE, header=0)
    except Exception as e:
        print(f"读取 Excel 失败: {e}")
        return

    # 检查必要列
    required = ['color_url', 'spec_id', 'name']
    for col in required:
        if col not in df.columns:
            print(f"错误：Excel 缺少列 '{col}'")
            return

    # 过滤空 URL
    df_valid = df[df['color_url'].notna() & (df['color_url'] != '')]
    if df_valid.empty:
        print("没有有效 color_url 数据。")
        return

    print(f"共读取 {len(df_valid)} 条记录，开始处理...\n")

    for idx, row in df_valid.iterrows():
        url = row['color_url']
        spec_id = int(row['spec_id']) if isinstance(row['spec_id'], float) else row['spec_id']
        color_name = row['name']
        print(f"[{idx+1}/{len(df_valid)}] 处理: {spec_id} - {color_name}")
        download_images(url, spec_id, color_name)
        time.sleep(REQUEST_DELAY)

    print("全部任务完成！")

if __name__ == "__main__":
    main()