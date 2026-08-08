# -*- coding: utf-8 -*-
"""
汽车之家：车型颜色列表 + 各颜色外观图片数量 爬虫。

输入: 每行一条车型的文本文件（默认 spec_id.txt）
      格式: 系列ID_车型ID.Html$spec_id$车型名
输出: CSV（url, series_id, spec_id, car_name, color_id, value, name, piccount, color_url, appearance_count）

特性:
- 两阶段线程池并发（颜色列表 → 外观数量），阶段间用全局令牌桶统一限速
- 断点续传：启动时读已有 CSV，跳过已完成 (spec_id, color_id)
- 颜色列表抓取失败会写入错误文件，下次自动优先重试
- 外观数量"解析失败"与"真为 0"区分：解析失败进错误文件重试，不污染 CSV
- 颜色列表失败带 stage 标记；--retry/错误文件自动处理两种失败类型

用法示例:
  python crawler_url_color_exterior_cnt.py
  python crawler_url_color_exterior_cnt.py --limit 20 --rate 5     # 测试前 20 个车型
  python crawler_url_color_exterior_cnt.py --input spec_id.txt --output out.csv
"""
import argparse
import csv
import json
import logging
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from typing import Any, Dict, List, Tuple

import requests
from bs4 import BeautifulSoup

from common import TokenBucket, get_session, load_json_list, save_json_list, setup_logging

# ---------- 默认配置（均可被命令行参数覆盖） ----------
INPUT_FILE = 'spec_id.txt'
OUTPUT_FILE = 'spec_id_pic_color_cnt.csv'
ERROR_FILE = 'error_tasks.json'
MAX_WORKERS = 20            # 线程池并发数
RATE = 15.0                 # 全局请求速率（次/秒），令牌桶限速
BATCH_SIZE = 1000           # 每批提交给线程池的任务数（控制内存）
RETRY_TIMES = 3
FLUSH_EVERY = 1000          # 每处理多少个颜色任务向 CSV 落盘一次

CSV_HEADER = ['url', 'series_id', 'spec_id', 'car_name', 'color_id', 'value',
              'name', 'piccount', 'color_url', 'appearance_count']

write_lock = Lock()
log = logging.getLogger('crawler')


# ---------- 断点续传 ----------
def load_completed(filepath: str) -> set:
    """读取已有 CSV，返回已完成任务的 (spec_id, color_id) 集合。"""
    done = set()
    if not os.path.exists(filepath):
        return done
    try:
        with open(filepath, 'r', encoding='utf-8-sig', newline='') as f:
            for row in csv.DictReader(f):
                try:
                    done.add((int(row['spec_id']), int(row['color_id'])))
                except (KeyError, ValueError):
                    continue
    except Exception as e:
        log.warning('读取已完成记录 %s 失败: %s', filepath, e)
    return done


# ---------- 网络请求 ----------
def fetch_next_data(url: str, session: requests.Session) -> Dict[str, Any]:
    """抓取页面中的 __NEXT_DATA__ JSON。"""
    for attempt in range(RETRY_TIMES):
        try:
            resp = session.get(url, timeout=15)
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


def _extract_count_from_next_data(html: str) -> Tuple[int, bool]:
    """从颜色页 __NEXT_DATA__ 的外观列表提取数量。

    返回 (count, found)：
    - found=True  表示页面结构解析成功（count 为外观图数量，可能为 0 = 确无外观图）
    - found=False 表示连 __NEXT_DATA__/callist 都没有，属于结构异常（反爬/改版）
    """
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not m:
        return 0, False
    try:
        data = json.loads(m.group(1).replace('&quot;', '"'))
    except Exception:
        return 0, False
    callist = (data.get('props', {}).get('pageProps', {})
               .get('SeriesPicList', {}).get('picinfo', {}).get('callist', []))
    if not isinstance(callist, list):
        return 0, False
    for item in callist:
        if item.get('claid') == 1:   # 1 = 外观
            total = item.get('total')
            if total is not None:
                try:
                    return int(total), True
                except (ValueError, TypeError):
                    pass
            image_list = item.get('list') or []
            if isinstance(image_list, list):
                return len(image_list), True
    # callist 存在但没有 claid=1 外观项 -> 该颜色确无外观图
    return 0, True


def fetch_appearance_count(color_url: str, session: requests.Session) -> Tuple[int, bool]:
    """抓取颜色页外观图片数量。

    优先从 __NEXT_DATA__ 的 callist 取 claid=1(外观) 的 total / list 长度，
    结构里没有时才退回解析"外观（N张）"显示文本。

    返回 (count, parsed_ok)：
    - parsed_ok=True  表示页面解析成功（count 可能为 0，即该颜色确实没有外观图）
    - parsed_ok=False 表示页面结构解析失败（可能是反爬/页面改版），调用方应记入错误重试
    """
    for attempt in range(RETRY_TIMES):
        try:
            resp = session.get(color_url, timeout=15)
            resp.encoding = 'utf-8'
            html = resp.text

            # 1) 优先从 NEXT_DATA 解析（最可靠）
            count, found = _extract_count_from_next_data(html)
            if found:
                return count, True

            # 2) 退回显示文本 "外观（N张）"
            m = re.search(r'外观\s*[（(]\s*(\d+)\s*张\s*[）)]', html)
            if m:
                return int(m.group(1)), True
            soup = BeautifulSoup(html, 'html.parser')
            for h3 in soup.find_all('h3'):
                text = h3.get_text()
                if '外观' in text:
                    m = re.search(r'[（(]\s*(\d+)\s*张\s*[）)]', text)
                    if m:
                        return int(m.group(1)), True
            # 页面请求成功但都解析不到 -> 结构失败
            return 0, False
        except Exception as e:
            if attempt == RETRY_TIMES - 1:
                raise RuntimeError(f'请求失败: {e}')
            time.sleep(random.uniform(0.5, 1.5))
    return 0, False


# ---------- 解析颜色列表 ----------
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


# ---------- 输入解析 ----------
def read_spec_id_txt(filepath: str) -> List[Dict[str, Any]]:
    items = []
    with open(filepath, 'r', encoding='utf-8-sig') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split('$')
            if len(parts) != 3:
                log.warning('跳过格式错误行: %s', line)
                continue
            first_part, spec_id_str, car_name = parts
            series_id_str = first_part.split('_')[0]
            try:
                series_id = int(series_id_str)
                spec_id = int(spec_id_str)
            except ValueError:
                log.warning('跳过数字解析失败行: %s', line)
                continue
            items.append({'series_id': series_id, 'spec_id': spec_id, 'car_name': car_name})
    return items


def build_series_url(item: Dict) -> str:
    return f'https://www.autohome.com.cn/cars/imglist-x-x-{item["series_id"]}-{item["spec_id"]}-x-x-x-x-x-1.html'


def build_color_url(item: Dict, color_id) -> str:
    return f'https://www.autohome.com.cn/cars/imglist-x-x-{item["series_id"]}-{item["spec_id"]}-x-{color_id}-x-x-1-1.html'


# ---------- 阶段一：抓颜色列表 ----------
def fetch_color_list(item: Dict, limiter: TokenBucket) -> Tuple[List[Dict], str]:
    """抓取单个车型的颜色列表。成功返回 (tasks, None)，失败返回 ([], error)。"""
    url = build_series_url(item)
    try:
        limiter.acquire()
        data = fetch_next_data(url, get_session())
        colors = extract_color_metadata(data)
        if not colors:
            return [], '未提取到颜色数据'
        tasks = []
        for c in colors:
            tasks.append({
                'url': url,
                'series_id': item['series_id'],
                'spec_id': item['spec_id'],
                'car_name': item['car_name'],
                'color_id': c['id'],
                'value': c.get('value'),
                'color_name': c['name'],
                'piccount': c['piccount'],
                'color_url': build_color_url(item, c['id']),
            })
        return tasks, None
    except Exception as e:
        return [], f'{e}'


def run_color_list_phase(items: List[Dict], limiter: TokenBucket,
                         workers: int, batch_size: int) -> Tuple[List[Dict], List[Dict]]:
    """并发抓取每个车型的颜色列表。返回 (color_tasks, spec_failures)。"""
    color_tasks: List[Dict] = []
    failures: List[Dict] = []
    total = len(items)
    done = 0
    log.info('开始抓取 %d 个车型的颜色列表 ...', total)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for start in range(0, total, batch_size):
            chunk = items[start:start + batch_size]
            futures = {ex.submit(fetch_color_list, item, limiter): item for item in chunk}
            for fut in as_completed(futures):
                item = futures[fut]
                try:
                    tasks, err = fut.result()
                except Exception as e:
                    tasks, err = [], str(e)
                done += 1
                if err:
                    failures.append({
                        'series_id': item['series_id'],
                        'spec_id': item['spec_id'],
                        'car_name': item['car_name'],
                        'stage': 'color_list',
                        'error': err,
                    })
                    log.warning('获取颜色列表失败 %s: %s', item['car_name'], err)
                else:
                    color_tasks.extend(tasks)
                    log.info('获取颜色列表 %d/%d %s: %d 种颜色',
                             done, total, item['car_name'], len(tasks))
                if done % 100 == 0:
                    log.info('颜色列表进度 %d/%d，累计 %d 个颜色任务', done, total, len(color_tasks))
    return color_tasks, failures


# ---------- 阶段二：抓外观数量 ----------
def process_color_task(task: Dict, limiter: TokenBucket) -> Dict[str, Any]:
    result = task.copy()
    try:
        limiter.acquire()
        count, parsed = fetch_appearance_count(task['color_url'], get_session())
        if not parsed:
            raise RuntimeError('页面未解析到外观数量')
        result['appearance_count'] = count
        result['success'] = True
    except Exception as e:
        result['appearance_count'] = 0
        result['success'] = False
        result['error'] = str(e)
    return result


# ---------- 输出 ----------
def write_batch_to_csv(results: List[Dict], output_file: str):
    """把一批成功结果追加到 CSV；文件不存在/为空时写表头。断点续传由此保证。"""
    success_results = [r for r in results if r.get('success', False)]
    if not success_results:
        return
    with write_lock:
        file_exists = os.path.isfile(output_file) and os.path.getsize(output_file) > 0
        with open(output_file, 'a', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(CSV_HEADER)
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
                    row['appearance_count'],
                ])
    log.info('已保存 %d 条成功记录到 %s', len(success_results), output_file)


# ---------- 主流程 ----------
def parse_args():
    p = argparse.ArgumentParser(description='汽车之家 车型颜色 & 外观图片数量 爬虫')
    p.add_argument('--input', default=INPUT_FILE, help='spec_id 文本文件（默认 spec_id.txt）')
    p.add_argument('--output', default=OUTPUT_FILE, help='输出 CSV（默认 spec_id_pic_color_cnt.csv）')
    p.add_argument('--error-file', default=ERROR_FILE, help='错误任务文件（默认 error_tasks.json）')
    p.add_argument('--workers', type=int, default=MAX_WORKERS, help='线程池并发数（默认 %d）' % MAX_WORKERS)
    p.add_argument('--rate', type=float, default=RATE, help='全局请求速率 次/秒（默认 %.1f）' % RATE)
    p.add_argument('--batch-size', type=int, default=BATCH_SIZE, help='每批提交任务数（默认 %d）' % BATCH_SIZE)
    p.add_argument('--limit', type=int, default=None, help='只处理前 N 个车型（测试用）')
    return p.parse_args()


def main():
    global log
    args = parse_args()
    log = setup_logging()

    completed = load_completed(args.output)
    log.info('断点续传：已读取 %d 条已完成记录', len(completed))

    limiter = TokenBucket(args.rate, int(args.rate))
    error_tasks = load_json_list(args.error_file)

    # ---------- 生成待处理的颜色任务 ----------
    color_tasks: List[Dict] = []
    spec_failures: List[Dict] = []

    if error_tasks:
        log.info('发现 %d 个待重试任务，优先处理', len(error_tasks))
        color_tasks = [t for t in error_tasks if t.get('color_url')]
        spec_retry = [t for t in error_tasks if not t.get('color_url')]
        save_json_list(args.error_file, [])
        if spec_retry:
            log.info('其中 %d 个是颜色列表阶段失败，重新抓取', len(spec_retry))
            retry_tasks, retry_failures = run_color_list_phase(
                spec_retry, limiter, args.workers, args.batch_size)
            color_tasks.extend(retry_tasks)
            spec_failures.extend(retry_failures)
    else:
        items = read_spec_id_txt(args.input)
        if args.limit:
            items = items[:args.limit]
        if not items:
            log.error('未读取到任何有效条目，退出。')
            return
        log.info('共读取 %d 个车型', len(items))
        color_tasks, spec_failures = run_color_list_phase(
            items, limiter, args.workers, args.batch_size)

    # 过滤已完成任务（断点续传）
    pending = [t for t in color_tasks if (t['spec_id'], t['color_id']) not in completed]
    skipped = len(color_tasks) - len(pending)
    if skipped:
        log.info('跳过 %d 个已完成任务', skipped)
    color_tasks = pending

    total = len(color_tasks)
    if total == 0:
        if spec_failures:
            save_json_list(args.error_file, spec_failures)
            log.warning('%d 个车型颜色列表失败，已保存至 %s', len(spec_failures), args.error_file)
        else:
            save_json_list(args.error_file, [])
            log.info('没有任务需要处理，退出。')
        return
    log.info('共需处理 %d 个颜色任务', total)

    # ---------- 阶段二：并发抓外观数量 ----------
    processed = 0
    success_count = 0
    failed_tasks: List[Dict] = []
    results_buffer: List[Dict] = []

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for batch_start in range(0, total, args.batch_size):
            batch = color_tasks[batch_start:batch_start + args.batch_size]
            futures = [ex.submit(process_color_task, t, limiter) for t in batch]
            for fut in as_completed(futures):
                result = fut.result()
                processed += 1
                if result.get('success'):
                    success_count += 1
                    results_buffer.append(result)
                else:
                    failed_tasks.append({k: v for k, v in result.items()
                                         if k not in ('success', 'error', 'appearance_count')})
                if processed % FLUSH_EVERY == 0:
                    write_batch_to_csv(results_buffer, args.output)
                    results_buffer = []
                    log.info('进度：%d/%d，成功 %d 条', processed, total, success_count)
            # 批次结束，落盘剩余
            write_batch_to_csv(results_buffer, args.output)
            results_buffer = []
    write_batch_to_csv(results_buffer, args.output)

    all_failures = failed_tasks + spec_failures
    save_json_list(args.error_file, all_failures)
    if all_failures:
        log.warning('共有 %d 个任务失败，已保存至 %s，下次运行会自动重试',
                    len(all_failures), args.error_file)
    else:
        log.info('所有任务处理成功，错误文件已清空')
    log.info('全部完成！成功 %d 条，失败 %d 条', success_count, len(all_failures))


if __name__ == '__main__':
    main()
