# -*- coding: utf-8 -*-
"""
汽车之家：按 车型+颜色 下载外观图片。

【已移交 / 不再维护】（2026-09-08 归档）：本流水线已整体移交他处，
仅作历史存档保留，勿用于新任务。当前维护范围只有流水线 1
（crawler_url_color_exterior_cnt.py，见 README）。

输入: Excel 文件（需含 color_url, spec_id, name 三列）
输出: out_put/<spec_id>_<颜色名>/NN_<原文件名>.jpg

特性:
- 线程池并发 + 全局令牌桶统一限速（而非每个任务各自 sleep）
- 断点续传：
    * 进度文件 download_progress.json 记录已完成 (spec_id, 颜色名)
    * 启动时还会跳过"目录已存在且文件数已满"的旧结果
    * 单张图片已存在且非空则跳过（中断后可续传未完成颜色）
- 失败任务落盘 download_error_tasks.json，--retry 可单独重试
- 图片内容校验（JPEG/PNG/WebP 魔数），避免把反爬 HTML/空文件存成 .jpg
- 解析成功但确无外观图的颜色会标记完成，不会反复重试

用法示例:
  python crawler_specid_pic_by_color.py
  python crawler_specid_pic_by_color.py --limit 10 --workers 4 --rate 4
  python crawler_specid_pic_by_color.py --retry     # 只重试失败的颜色
"""
import argparse
import json
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from common import (TokenBucket, get_session, load_json_list,
                    save_json_list, setup_logging)

# ---------- 默认配置（均可被命令行参数覆盖） ----------
EXCEL_FILE = 'archived/spec_id_url_sample_20260715_1.xlsx'
OUTPUT_ROOT = 'out_put'
DOWNLOAD_COUNT = 8              # 每种颜色下载前几张外观图片
REQUEST_TIMEOUT = 15
IMAGE_TIMEOUT = 10
MAX_WORKERS = 8                 # 线程池并发数
RATE = 8.0                      # 全局请求速率（次/秒），令牌桶限速
CHECKPOINT_FILE = 'download_progress.json'
ERROR_FILE = 'download_error_tasks.json'
CHECKPOINT_FLUSH_EVERY = 50     # 每完成多少个颜色落盘一次 checkpoint

JPEG_MAGIC = b'\xff\xd8\xff'
PNG_MAGIC = b'\x89PNG\r\n\x1a\n'
WEBP_MAGIC = b'WEBP'

log = logging.getLogger('crawler')


# ---------- 断点续传 ----------
class Checkpoint:
    """颜色完成进度。多线程安全，周期性落盘到 JSON，支持中断续传。"""

    def __init__(self, filepath: str, flush_every: int = CHECKPOINT_FLUSH_EVERY):
        self.path = filepath
        self.flush_every = flush_every
        self._keys = set()
        self._pending = 0
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self._keys = set(tuple(k) for k in data if isinstance(k, list))
            log.info('断点续传：已读取 %d 个已完成颜色', len(self._keys))
        except Exception as e:
            log.warning('读取进度文件 %s 失败: %s', self.path, e)

    def contains(self, key) -> bool:
        with self._lock:
            return key in self._keys

    def add(self, key):
        with self._lock:
            if key in self._keys:
                return
            self._keys.add(key)
            self._pending += 1
            if self._pending >= self.flush_every:
                self._flush()

    def _flush(self):
        tmp = f'{self.path}.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump([list(k) for k in self._keys], f, ensure_ascii=False)
        os.replace(tmp, self.path)
        self._pending = 0

    def flush(self):
        with self._lock:
            self._flush()


# ---------- 工具函数 ----------
def clean_name(name):
    """清理文件夹名称中的非法字符。"""
    return re.sub(r'[\\/*?:"<>|]', "", str(name)).strip()


def normalize_spec_id(v):
    """把 Excel 里可能是 float / '34942.0' / '34942' 的 spec_id 统一成 int。"""
    if isinstance(v, float):
        return int(v)
    s = str(v).strip()
    if s.endswith('.0'):
        s = s[:-2]
    return int(s)


def count_files(dirpath: str) -> int:
    try:
        return len([n for n in os.listdir(dirpath)
                    if os.path.isfile(os.path.join(dirpath, n))])
    except OSError:
        return 0


def validate_image(content: bytes) -> bool:
    """粗略校验图片内容，避免把反爬 HTML / 空响应存成图片。"""
    if len(content) < 16:
        return False
    if content.startswith(JPEG_MAGIC) or content.startswith(PNG_MAGIC):
        return True
    if content.startswith(b'RIFF') and content[8:12] == WEBP_MAGIC:
        return True
    return False


# ---------- 解析外观图片 ----------
def parse_exterior_images(html: str, download_count: int):
    """解析颜色图片列表页。返回 (image_list, parsed_ok)。

    parsed_ok=False 表示页面结构解析失败（反爬/改版），需要重试；
    parsed_ok=True 但 image_list 为空表示该颜色确实没有外观图。
    """
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if not match:
        return [], False
    data = json.loads(match.group(1))
    callist = (data.get('props', {}).get('pageProps', {})
               .get('SeriesPicList', {}).get('picinfo', {}).get('callist', []))
    for item in callist:
        if item.get('claid') == 1:   # 1 = 外观
            image_list = item.get('list', [])
            return image_list[:download_count], True
    return [], True


# ---------- 单个颜色处理 ----------
def process_one_color(row, limiter: TokenBucket, checkpoint: Checkpoint,
                      output_root: str, download_count: int):
    """处理一行（一个 车型+颜色）。

    返回 (status, msg)：status ∈ {'ok', 'skipped', 'failed'}。
    """
    spec_id = normalize_spec_id(row['spec_id'])
    color_name = str(row['name']).strip()
    color_url = str(row['color_url']).strip()
    key = (spec_id, color_name)
    output_dir = os.path.join(output_root, f'{spec_id}_{clean_name(color_name)}')

    # 断点续传：进度文件中已完成 / 旧版本已下满的目录 -> 跳过
    if checkpoint.contains(key):
        return 'skipped', 'checkpoint'
    if os.path.isdir(output_dir) and count_files(output_dir) >= download_count:
        return 'skipped', 'dir_exists'

    try:
        limiter.acquire()
        resp = get_session().get(color_url, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        image_list, parsed_ok = parse_exterior_images(resp.text, download_count)
        if not parsed_ok:
            raise RuntimeError('页面未找到 __NEXT_DATA__（可能被反爬拦截或页面改版）')
        if not image_list:
            # 解析成功但确无外观图 -> 标记完成，避免反复重试
            checkpoint.add(key)
            return 'ok', 'no_exterior'

        os.makedirs(output_dir, exist_ok=True)
        for idx, img_info in enumerate(image_list):
            img_url = img_info.get('picpath')
            if not img_url:
                continue
            if img_url.startswith('//'):
                img_url = 'https:' + img_url

            filename = img_url.split('/')[-1].split('?')[0]
            if not filename.lower().endswith(('.jpg', '.jpeg', '.png', '.webp')):
                filename += '.jpg'
            save_path = os.path.join(output_dir, f'{idx + 1:02d}_{filename}')

            # 单张已存在且非空 -> 跳过（中断后可续传）
            if os.path.isfile(save_path) and os.path.getsize(save_path) > 0:
                continue

            limiter.acquire()
            img_resp = get_session().get(img_url, timeout=IMAGE_TIMEOUT)
            img_resp.raise_for_status()
            content = img_resp.content
            if not validate_image(content):
                raise RuntimeError(f'图片校验失败(可能被反爬拦截或空文件): {img_url}')
            with open(save_path, 'wb') as f:
                f.write(content)

        checkpoint.add(key)
        return 'ok', color_name
    except Exception as e:
        return 'failed', str(e)


# ---------- 主流程 ----------
def parse_args():
    p = argparse.ArgumentParser(description='汽车之家 按车型+颜色 下载外观图片')
    p.add_argument('--excel', default=EXCEL_FILE, help='源 Excel 文件（默认 spec_id_url_sample_20260715_1.xlsx）')
    p.add_argument('--output-root', default=OUTPUT_ROOT, help='图片保存根目录（默认 out_put）')
    p.add_argument('--download-count', type=int, default=DOWNLOAD_COUNT,
                   help='每种颜色下载前几张外观图片（默认 %d）' % DOWNLOAD_COUNT)
    p.add_argument('--workers', type=int, default=MAX_WORKERS, help='线程池并发数（默认 %d）' % MAX_WORKERS)
    p.add_argument('--rate', type=float, default=RATE, help='全局请求速率 次/秒（默认 %.1f）' % RATE)
    p.add_argument('--checkpoint', default=CHECKPOINT_FILE, help='断点续传进度文件（默认 download_progress.json）')
    p.add_argument('--error-file', default=ERROR_FILE, help='失败任务文件（默认 download_error_tasks.json）')
    p.add_argument('--limit', type=int, default=None, help='只处理前 N 条记录（测试用）')
    p.add_argument('--retry', action='store_true', help='从错误文件重试失败任务，忽略 Excel')
    return p.parse_args()


def main():
    global log
    args = parse_args()
    log = setup_logging()

    if args.retry:
        err = load_json_list(args.error_file)
        if not err:
            log.info('错误文件 %s 没有任务，退出。', args.error_file)
            return
        log.info('从错误文件加载 %d 个待重试颜色', len(err))
        df_valid = pd.DataFrame(err)
        save_json_list(args.error_file, [])
    else:
        if not os.path.exists(args.excel):
            log.error('文件 %s 不存在，请检查路径。', args.excel)
            return
        try:
            df = pd.read_excel(args.excel, header=0)
        except Exception as e:
            log.error('读取 Excel 失败: %s', e)
            return
        required = ['color_url', 'spec_id', 'name']
        for col in required:
            if col not in df.columns:
                log.error("Excel 缺少列 '%s'", col)
                return
        df_valid = df[df['color_url'].notna() & (df['color_url'].astype(str).str.strip() != '')]
        # 同 车型+颜色 只处理一次
        df_valid = df_valid.drop_duplicates(subset=['spec_id', 'name'])
        if df_valid.empty:
            log.error('没有有效 color_url 数据。')
            return
        if args.limit:
            df_valid = df_valid.head(args.limit)
        log.info('共读取 %d 条记录（去重后）', len(df_valid))

    checkpoint = Checkpoint(args.checkpoint)
    limiter = TokenBucket(args.rate, int(args.rate))

    failed_tasks = []
    failed_lock = threading.Lock()
    processed = 0

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(process_one_color, row, limiter, checkpoint,
                             args.output_root, args.download_count): idx
                   for idx, row in df_valid.iterrows()}
        for fut in as_completed(futures):
            status, msg = fut.result()
            processed += 1
            if status == 'failed':
                row = df_valid.loc[futures[fut]]
                with failed_lock:
                    failed_tasks.append({
                        'spec_id': normalize_spec_id(row['spec_id']),
                        'name': str(row['name']).strip(),
                        'color_url': str(row['color_url']).strip(),
                        'error': msg,
                    })
            if processed % 50 == 0:
                log.info('进度：%d/%d（失败 %d 条）', processed, len(df_valid), len(failed_tasks))

    checkpoint.flush()
    if failed_tasks:
        save_json_list(args.error_file, failed_tasks)
        log.warning('共 %d 个颜色失败，已保存至 %s；可用 --retry 重试',
                    len(failed_tasks), args.error_file)
    else:
        save_json_list(args.error_file, [])
        log.info('全部完成，无失败任务，错误文件已清空')


if __name__ == '__main__':
    main()
