import re
import json
import requests
import os
import pandas as pd

def download_images(url):
    """
    从给定的汽车之家图片列表页下载外观分类的前8张图片，
    按 specid 归纳到 out_put/{specid}/ 目录下
    """
    download_count = 8
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }

    try:
        # 1. 从 URL 中提取 specid
        match_spec = re.search(r'/imglist-x-x-\d+-(\d+)-x-x-x-x-x-\d+\.html', url)
        if not match_spec:
            print(f"⚠️ 无法从 URL 提取 specid: {url}")
            return
        specid = match_spec.group(1)
        print(f"提取到 specid: {specid}")

        # 2. 创建输出目录
        output_dir = os.path.join("out_put", specid)
        os.makedirs(output_dir, exist_ok=True)

        # 3. 获取页面内容
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()

        # 4. 提取 __NEXT_DATA__ 的 JSON 数据
        match_data = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', response.text, re.DOTALL)
        if not match_data:
            print(f"⚠️ 未找到 __NEXT_DATA__ 脚本: {url}")
            return

        data = json.loads(match_data.group(1))

        # 5. 解析 JSON，找到外观分类（claid: 1）下的图片列表
        page_props = data.get("props", {}).get("pageProps", {})
        series_pic_list = page_props.get("SeriesPicList", {})
        pic_info = series_pic_list.get("picinfo", {})
        callist = pic_info.get("callist", [])

        image_list = []
        for item in callist:
            if item.get("claid") == 1:  # 1 代表外观
                image_list = item.get("list", [])
                break

        if not image_list:
            print(f"⚠️ 未找到外观分类的图片列表: {url}")
            return

        # 6. 取前8张图片进行下载
        target_images = image_list[:download_count]
        print(f"找到 {len(target_images)} 张图片，开始下载到 {output_dir} ...")

        # 7. 循环下载
        for idx, img_info in enumerate(target_images):
            img_url = img_info.get("picpath")
            if not img_url:
                continue

            if img_url.startswith('//'):
                img_url = 'https:' + img_url

            try:
                img_response = requests.get(img_url, headers=headers, timeout=10)
                img_response.raise_for_status()

                # 从 URL 中提取文件名
                filename = img_url.split('/')[-1].split('?')[0]
                if not filename.endswith(('.jpg', '.jpeg', '.png', '.webp')):
                    filename += '.jpg'

                save_name = f"{idx+1:02d}_{filename}"
                filepath = os.path.join(output_dir, save_name)

                with open(filepath, 'wb') as f:
                    f.write(img_response.content)
                print(f"  ✓ 已下载: {filepath}")
            except Exception as e:
                print(f"  ✗ 下载失败: {img_url}, 错误: {e}")

        print(f"完成 specid={specid} 的下载\n")

    except Exception as e:
        print(f"❌ 处理 {url} 时出错: {e}")

def main():
    excel_file = "spec_id_url_sample.xlsx"

    # 检查文件是否存在
    if not os.path.exists(excel_file):
        print(f"错误：文件 {excel_file} 不存在，请确认路径。")
        return

    # 读取 Excel 文件，第一行为表头，取第一列数据（跳过表头）
    try:
        # 方式1：使用 header=0 表示第一行为表头，然后通过 iloc 跳过第一行
        df = pd.read_excel(excel_file, header=0, usecols=[0])
        # 获取第一列的所有行（从索引0开始），但第一行是表头，实际数据从索引1开始
        # 使用 iloc[1:, 0] 跳过表头行
        urls = df.iloc[1:, 0].dropna().tolist()
    except Exception as e:
        print(f"读取 Excel 文件失败: {e}")
        return

    if not urls:
        print("Excel 文件中没有有效的 URL。")
        return

    print(f"共读取到 {len(urls)} 个 URL，开始批量处理...\n")

    for idx, url in enumerate(urls, 1):
        print(f"[{idx}/{len(urls)}] 处理 URL: {url}")
        download_images(url)

    print("所有任务处理完毕！")

if __name__ == "__main__":
    main()