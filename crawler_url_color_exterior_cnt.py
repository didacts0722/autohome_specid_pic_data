import requests
from bs4 import BeautifulSoup
import json
import re
import csv
from typing import List, Dict, Any
import time
import random
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import os
import sys

# ---------- 配置 ----------
MAX_WORKERS = 20
RETRY_TIMES = 3
DELAY_RANGE = (0.1, 0.5)
BATCH_SIZE = 1000            # 每批提交给线程池的任务数
WRITE_INTERVAL = 1000        # 每处理 50 个任务写入一次
INPUT_FILE = 'spec_id.txt'
OUTPUT_FILE = 'spec_id_pic_color_cnt.csv'
ERROR_FILE = 'error_tasks.json'

# ---------- 全局锁 ----------
write_lock = Lock()

# ---------- 错误文件操作 ----------
def load_error_tasks(filepath: str) -> List[Dict[str, Any]]:
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = f.read().strip()
            if not data:
                return []
            return json.loads(data)
    except Exception as e:
        print(f'读取错误文件失败: {e}')
        return []

def save_error_tasks(filepath: str, tasks: List[Dict[str, Any]]):
    with open(filepath, 'w', encoding='utf-8') as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)

# ---------- 核心爬取函数 ----------
def fetch_next_data(url: str, session: requests.Session) -> Dict[str, Any]:
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
        'Referer': 'https://www.autohome.com.cn/',
    }
    for attempt in range(RETRY_TIMES):
        try:
            resp = session.get(url, headers=headers, timeout=15)
            resp.encoding = 'utf-8'
            soup = BeautifulSoup(resp.text, 'html.parser')
            script = soup.find('script', id='__NEXT_DATA__')
            if not script:
                raise ValueError('未找到 __NEXT_DATA__ 脚本标签')
            json_text = script.string
            json_text = re.sub(r'&quot;', '"', json_text)
            return json.loads(json_text)
        except Exception as e:
            if attempt == RETRY_TIMES - 1:
                raise RuntimeError(f'获取 __NEXT_DATA__ 失败: {e}')
            time.sleep(random.uniform(0.5, 1.5))
    return {}

def extract_color_metadata(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    candidates = [
        data.get('props', {}).get('pageProps', {}),
        data.get('initialState', {}),
        data.get('props', {}).get('initialState', {}),
        data.get('pageProps', {}),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        for key in ['colorList', 'colors', 'specColors', 'colorsInfo', 'extColors']:
            if key not in candidate:
                continue
            value = candidate[key]
            if isinstance(value, list) and value:
                sample = value[0]
                if isinstance(sample, dict) and 'id' in sample and 'name' in sample:
                    return _parse_colors(value)
            if isinstance(value, dict):
                color_list = []
                if 'color' in value and isinstance(value['color'], list):
                    color_list.extend(value['color'])
                if 'othercolor' in value and isinstance(value['othercolor'], list):
                    color_list.extend(value['othercolor'])
                if color_list:
                    seen = set()
                    unique_colors = []
                    for item in color_list:
                        cid = item.get('id')
                        if cid and cid not in seen:
                            seen.add(cid)
                            unique_colors.append(item)
                    return _parse_colors(unique_colors)
    return []

def _parse_colors(raw_colors: List[Dict]) -> List[Dict]:
    result = []
    for item in raw_colors:
        color_id = item.get('id')
        hex_val = item.get('value') or item.get('colorValue') or item.get('hex')
        name = item.get('name') or item.get('colorName') or item.get('title')
        piccount = item.get('piccount')
        if piccount is None:
            for img_key in ['images', 'imgList', 'picList']:
                if img_key in item and isinstance(item[img_key], list):
                    piccount = len(item[img_key])
                    break
        if piccount is None:
            piccount = 0
        if color_id is not None and name:
            result.append({
                'id': color_id,
                'value': hex_val,
                'name': name,
                'piccount': piccount
            })
    return result

def fetch_appearance_count(color_url: str, session: requests.Session) -> int:
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36',
        'Referer': 'https://www.autohome.com.cn/',
    }
    for attempt in range(RETRY_TIMES):
        try:
            resp = session.get(color_url, headers=headers, timeout=15)
            resp.encoding = 'utf-8'
            html = resp.text
            match = re.search(r'外观\s*[（(]\s*(\d+)\s*张\s*[）)]', html)
            if match:
                return int(match.group(1))
            soup = BeautifulSoup(html, 'html.parser')
            for h3 in soup.find_all('h3'):
                text = h3.get_text()
                if '外观' in text:
                    m = re.search(r'[（(]\s*(\d+)\s*张\s*[）)]', text)
                    if m:
                        return int(m.group(1))
            return 0
        except Exception as e:
            if attempt == RETRY_TIMES - 1:
                raise RuntimeError(f'请求失败: {e}')
            time.sleep(random.uniform(0.5, 1.5))
    return 0

def read_spec_id_txt(filepath: str) -> List[Dict[str, Any]]:
    items = []
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split('$')
            if len(parts) != 3:
                print(f'跳过格式错误行: {line}')
                continue
            first_part, spec_id_str, car_name = parts
            if '_' in first_part:
                series_id_str = first_part.split('_')[0]
            else:
                series_id_str = first_part
            try:
                series_id = int(series_id_str)
                spec_id = int(spec_id_str)
            except ValueError:
                print(f'跳过数字解析失败行: {line}')
                continue
            items.append({
                'series_id': series_id,
                'spec_id': spec_id,
                'car_name': car_name
            })
    return items

def process_color_task(task: Dict[str, Any]) -> Dict[str, Any]:
    color_url = task['color_url']
    result = task.copy()
    try:
        with requests.Session() as session:
            count = fetch_appearance_count(color_url, session)
            result['appearance_count'] = count
            result['success'] = True
    except Exception as e:
        result['appearance_count'] = 0
        result['success'] = False
        result['error'] = str(e)
    time.sleep(random.uniform(*DELAY_RANGE))
    return result

def write_batch_to_csv(results: List[Dict], output_file: str, mode: str = 'a'):
    """
    将一批结果写入 CSV，只写成功记录。
    mode='w' 覆盖写入（会写表头），mode='a' 追加（不会写表头）。
    通常首次全量写入用 'w'，后续用 'a'。
    """
    if not results:
        return
    success_results = [r for r in results if r.get('success', False)]
    if not success_results:
        return

    with write_lock:
        # 如果是 'w' 模式，直接覆盖；否则判断文件是否存在
        if mode == 'w':
            # 直接覆盖，写入表头
            with open(output_file, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                writer.writerow(['url', 'series_id', 'spec_id', 'car_name', 'color_id', 'value', 'name', 'piccount', 'color_url', 'appearance_count'])
                for row in success_results:
                    writer.writerow([
                        row['url'],
                        row['series_id'],
                        row['spec_id'],
                        row['car_name'],
                        row['color_id'],
                        row.get('value', ''),
                        row['color_name'],
                        row['piccount'],
                        row['color_url'],
                        row['appearance_count']
                    ])
        else:
            # 追加模式，文件存在则追加，不存在则创建并写表头
            file_exists = os.path.isfile(output_file)
            with open(output_file, 'a', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)
                if not file_exists:
                    writer.writerow(['url', 'series_id', 'spec_id', 'car_name', 'color_id', 'value', 'name', 'piccount', 'color_url', 'appearance_count'])
                for row in success_results:
                    writer.writerow([
                        row['url'],
                        row['series_id'],
                        row['spec_id'],
                        row['car_name'],
                        row['color_id'],
                        row.get('value', ''),
                        row['color_name'],
                        row['piccount'],
                        row['color_url'],
                        row['appearance_count']
                    ])
    print(f'✅ 已保存 {len(success_results)} 条成功记录到 {output_file}')
    sys.stdout.flush()

# ---------- 主流程 ----------
def main():
    error_tasks = load_error_tasks(ERROR_FILE)
    if error_tasks:
        print(f'发现 {len(error_tasks)} 个待重试任务，优先处理')
        tasks_to_process = error_tasks
        save_error_tasks(ERROR_FILE, [])
        mode = 'retry'
    else:
        print('未发现错误任务，开始全量爬取')
        items = read_spec_id_txt(INPUT_FILE)
        if not items:
            print('未读取到任何有效条目。')
            return
        print(f'共读取 {len(items)} 个车型')
        tasks_to_process = []
        with requests.Session() as session:
            for idx, item in enumerate(items, 1):
                series_id = item['series_id']
                spec_id = item['spec_id']
                car_name = item['car_name']
                url = f'https://www.autohome.com.cn/cars/imglist-x-x-{series_id}-{spec_id}-x-x-x-x-x-1.html'
                print(f'[{idx}/{len(items)}] 获取颜色列表: {car_name}')
                try:
                    data = fetch_next_data(url, session)
                except Exception as e:
                    print(f'  获取颜色列表失败: {e}')
                    continue
                colors = extract_color_metadata(data)
                if not colors:
                    print(f'  未提取到颜色数据')
                    continue
                print(f'  得到 {len(colors)} 种颜色')
                for c in colors:
                    color_url = f'https://www.autohome.com.cn/cars/imglist-x-x-{series_id}-{spec_id}-x-{c["id"]}-x-x-1-1.html'
                    tasks_to_process.append({
                        'url': url,
                        'series_id': series_id,
                        'spec_id': spec_id,
                        'car_name': car_name,
                        'color_id': c['id'],
                        'value': c.get('value'),
                        'color_name': c['name'],
                        'piccount': c['piccount'],
                        'color_url': color_url,
                    })
                time.sleep(random.uniform(0.5, 1.0))
        mode = 'full'

    total = len(tasks_to_process)
    if total == 0:
        print('没有任务需要处理，退出。')
        return

    print(f'共需处理 {total} 个颜色任务')
    # 全量模式下，第一次写入使用 'w' 覆盖，后续使用 'a' 追加
    # 重试模式全程使用 'a'
    first_write = True  # 仅在全量模式下有效

    processed = 0
    success_count = 0
    failed_tasks = []
    results_buffer = []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        for batch_start in range(0, total, BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, total)
            batch_tasks = tasks_to_process[batch_start:batch_end]
            futures = [executor.submit(process_color_task, task) for task in batch_tasks]

            for future in as_completed(futures):
                result = future.result()
                processed += 1
                if result.get('success', False):
                    success_count += 1
                    results_buffer.append(result)
                else:
                    failed_task = {k: v for k, v in result.items() if k not in ['success', 'error', 'appearance_count']}
                    failed_tasks.append(failed_task)

                # 每处理 WRITE_INTERVAL 个任务，写入一次
                if processed % WRITE_INTERVAL == 0:
                    if results_buffer:
                        # 决定写入模式
                        if mode == 'full' and first_write:
                            current_mode = 'w'
                            first_write = False
                        else:
                            current_mode = 'a'
                        write_batch_to_csv(results_buffer, OUTPUT_FILE, mode=current_mode)
                        results_buffer.clear()
                    print(f'📈 进度：已处理 {processed} / {total}，成功 {success_count} 条')
                    sys.stdout.flush()

            # 批次结束，若有剩余成功记录则写入
            if results_buffer:
                if mode == 'full' and first_write:
                    current_mode = 'w'
                    first_write = False
                else:
                    current_mode = 'a'
                write_batch_to_csv(results_buffer, OUTPUT_FILE, mode=current_mode)
                results_buffer.clear()
                print(f'📈 批次结束，已处理 {processed} 个任务，成功 {success_count} 条')
                sys.stdout.flush()

    # 所有任务完成后，清理最后的缓存（理论上已空）
    if results_buffer:
        if mode == 'full' and first_write:
            current_mode = 'w'
        else:
            current_mode = 'a'
        write_batch_to_csv(results_buffer, OUTPUT_FILE, mode=current_mode)
        results_buffer.clear()

    if failed_tasks:
        save_error_tasks(ERROR_FILE, failed_tasks)
        print(f'共有 {len(failed_tasks)} 个任务失败，已保存至 {ERROR_FILE}')
    else:
        save_error_tasks(ERROR_FILE, [])
        print('所有任务处理成功，错误文件已清空')

    print(f'全部完成！成功 {success_count} 条，失败 {len(failed_tasks)} 条')

if __name__ == '__main__':
    main()