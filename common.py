# -*- coding: utf-8 -*-
"""
两个爬虫脚本共享的工具模块：
- setup_logging: 统一日志（带时间戳与级别）
- TokenBucket: 全局令牌桶限速（并发下控制整体 QPS，而非每个 worker 各自 sleep）
- get_session: 每线程复用 requests.Session（长连接 + 统一请求头）
- load_json_list / save_json_list: JSON 数组（如错误任务）文件的读写
"""
import json
import logging
import os
import threading
import time

import requests

DEFAULT_HEADERS = {
    'User-Agent': ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                   '(KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'),
    'Referer': 'https://www.autohome.com.cn/',
}


def setup_logging(level=logging.INFO):
    """统一日志配置：时间戳 + 级别，输出到控制台。"""
    logging.basicConfig(
        level=level,
        format='%(asctime)s [%(levelname)s] %(message)s',
        datefmt='%H:%M:%S',
    )
    return logging.getLogger('crawler')


class TokenBucket:
    """令牌桶限速器。rate=每秒补充令牌数，capacity=桶容量（突发上限）。

    并发场景下用它在多个 worker 之间做全局限速，
    而不是在每个 worker 里各自 sleep（那样整体速率会随并发数成倍放大）。
    """

    def __init__(self, rate: float, capacity: int = None):
        self.rate = rate
        self.capacity = capacity or max(1, int(rate))
        self._tokens = float(self.capacity)
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, tokens: float = 1.0):
        """阻塞直到拿到 tokens 个令牌。"""
        while True:
            with self._lock:
                now = time.monotonic()
                self._tokens = min(self.capacity,
                                   self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                wait = (tokens - self._tokens) / self.rate
            time.sleep(wait)


_thread_local = threading.local()


def get_session() -> requests.Session:
    """返回当前线程复用的 Session（TCP 长连接复用 + 统一请求头）。"""
    session = getattr(_thread_local, 'session', None)
    if session is None:
        session = requests.Session()
        session.headers.update(DEFAULT_HEADERS)
        _thread_local.session = session
    return session


def load_json_list(filepath: str):
    """读取 JSON 数组文件；文件不存在或损坏时返回空列表。"""
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = f.read().strip()
            if not data:
                return []
            return json.loads(data)
    except Exception as e:
        logging.getLogger('crawler').warning('读取 %s 失败: %s', filepath, e)
        return []


def save_json_list(filepath: str, tasks):
    """写入 JSON 数组文件（先写临时文件再替换，避免中断产生半截文件）。"""
    tmp = f'{filepath}.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)
    os.replace(tmp, filepath)
