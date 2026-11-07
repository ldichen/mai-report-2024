"""
Scopus API 客户端：多 key 文件、per-key 限速、429/401 自动禁用、线程安全
"""
import os
import json
import time
import threading
import requests

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import (
    SCOPUS_SEARCH_URL, SCOPUS_ABSTRACT_URL,
    SCOPUS_INTERVAL, SCOPUS_PAGE_SIZE, SCOPUS_MAX_OFFSET,
    API_KEY_FILES, MAX_RETRIES, REQUEST_TIMEOUT,
)


class ScopusClient:
    def __init__(self):
        self._local = threading.local()
        self._api_keys = self._load_api_keys()
        self._lock = threading.Lock()
        self._key_last_time = {key: 0.0 for key in self._api_keys}
        self._dead_keys = set()
        print(f"[ScopusClient] 已加载 {len(self._api_keys)} 个 API key")

    def _load_api_keys(self):
        keys = []
        for path in API_KEY_FILES:
            if not os.path.exists(path):
                continue
            with open(path, "r") as f:
                for line in f:
                    k = line.strip()
                    if k and k not in keys:
                        keys.append(k)
        if not keys:
            raise RuntimeError(f"无可用 API key: {API_KEY_FILES}")
        return keys

    def disable(self, key):
        with self._lock:
            self._dead_keys.add(key)
            alive = len(self._api_keys) - len(self._dead_keys)
            print(f"  [Key] {key[:8]}... 已禁用 (剩余 {alive} 可用)")

    def alive_count(self):
        with self._lock:
            return len(self._api_keys) - len(self._dead_keys)

    def _get_session(self):
        if not hasattr(self._local, "session"):
            self._local.session = requests.Session()
            self._local.session.trust_env = False
        return self._local.session

    def _pick_key(self):
        while True:
            with self._lock:
                if len(self._dead_keys) >= len(self._api_keys):
                    return None
                now = time.time()
                best_key = None
                best_elapsed = -1
                for key, last in self._key_last_time.items():
                    if key in self._dead_keys:
                        continue
                    elapsed = now - last
                    if elapsed > best_elapsed:
                        best_elapsed = elapsed
                        best_key = key
                if best_elapsed >= SCOPUS_INTERVAL:
                    self._key_last_time[best_key] = time.time()
                    return best_key
                wait = SCOPUS_INTERVAL - best_elapsed
            time.sleep(wait)

    @staticmethod
    def _clean_json(obj):
        """递归清理 Scopus JSON 的命名空间前缀，安全处理 $ 键"""
        if isinstance(obj, dict):
            cleaned = {}
            for k, v in obj.items():
                new_key = k
                for prefix in ('dc:', 'prism:', 'dcterms:'):
                    if new_key.startswith(prefix):
                        new_key = new_key[len(prefix):]
                        break
                if new_key == '$':
                    new_key = 'value'
                cleaned[new_key] = ScopusClient._clean_json(v)
            return cleaned
        if isinstance(obj, list):
            return [ScopusClient._clean_json(item) for item in obj]
        return obj

    def search(self, query, start=0, count=SCOPUS_PAGE_SIZE):
        api_key = self._pick_key()
        if api_key is None:
            raise Exception("所有 API key 已用完")
        params = {
            "query": query,
            "start": start,
            "count": count,
            "apiKey": api_key,
        }
        session = self._get_session()

        for attempt in range(MAX_RETRIES):
            try:
                resp = session.get(SCOPUS_SEARCH_URL, params=params, timeout=REQUEST_TIMEOUT)

                if resp.status_code == 429:
                    self.disable(api_key)
                    api_key = self._pick_key()
                    if api_key is None:
                        raise Exception("所有 API key 已用完")
                    params["apiKey"] = api_key
                    continue

                if resp.status_code == 401:
                    self.disable(api_key)
                    api_key = self._pick_key()
                    if api_key is None:
                        raise Exception("所有 API key 已用完")
                    params["apiKey"] = api_key
                    continue

                if not resp.ok:
                    print(f"  [Scopus] {resp.status_code}: {resp.text[:200]}")
                    api_key = self._pick_key()
                    if api_key is None:
                        raise Exception("所有 API key 已用完")
                    params["apiKey"] = api_key
                    continue

                return self._clean_json(resp.json())

            except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as e:
                wait = 2 ** attempt
                print(f"  [Scopus] {type(e).__name__}，等待 {wait}s 重试 ({attempt+1}/{MAX_RETRIES})...")
                time.sleep(wait)

        raise Exception(f"Scopus 请求失败，已重试 {MAX_RETRIES} 次: {query[:80]}...")

    def get_total(self, query):
        data = self.search(query, start=0, count=1)
        return int(data["search-results"]["opensearch:totalResults"])

    def fetch_abstract(self, eid):
        api_key = self._pick_key()
        if api_key is None:
            return None
        url = SCOPUS_ABSTRACT_URL + eid
        params = {
            "field": "author,affiliation,description,authkeywords,subject-area,eid,citedby-count",
            "apiKey": api_key,
            "httpAccept": "application/json",
        }
        session = self._get_session()

        for attempt in range(MAX_RETRIES):
            try:
                resp = session.get(url, params=params, timeout=REQUEST_TIMEOUT)

                if resp.status_code == 429:
                    self.disable(api_key)
                    api_key = self._pick_key()
                    if api_key is None:
                        return None
                    params["apiKey"] = api_key
                    continue

                if resp.status_code == 401:
                    self.disable(api_key)
                    api_key = self._pick_key()
                    if api_key is None:
                        return None
                    params["apiKey"] = api_key
                    continue

                if resp.status_code == 404:
                    return None

                if not resp.ok:
                    api_key = self._pick_key()
                    if api_key is None:
                        return None
                    params["apiKey"] = api_key
                    continue

                return self._clean_json(resp.json())

            except json.JSONDecodeError:
                wait = 2 ** attempt
                print(f"  [Scopus Abstract] JSON 解析失败，等待 {wait}s 重试...")
                time.sleep(wait)
            except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as e:
                wait = 2 ** attempt
                time.sleep(wait)

        return None

    def fetch_all_results(self, query, label=""):
        entries, _ = self._fetch_segment(query, label)
        return entries

    def fetch_all_with_total(self, query, label=""):
        """返回 (entries, total)，第一页请求同时获取总数和数据"""
        return self._fetch_segment(query, label)

    def _fetch_segment(self, query, label=""):
        data = self.search(query, start=0, count=SCOPUS_PAGE_SIZE)
        total = int(data["search-results"]["opensearch:totalResults"])

        if total == 0:
            return [], 0

        if total > SCOPUS_MAX_OFFSET:
            entries = self._bisect_by_year(query, total, label)
            return entries, total

        entries = self._collect_entries(data, query, total, label)
        return entries, total

    def _bisect_by_year(self, query, known_total, label=""):
        import re
        match = re.search(r"PUBYEAR\s*>\s*(\d+)\s+AND\s+PUBYEAR\s*<\s*(\d+)", query)
        if not match:
            print(f"  [{label}] 无法解析年份范围，跳过二分")
            return self._collect_entries(
                self.search(query, start=0, count=SCOPUS_PAGE_SIZE),
                query, SCOPUS_MAX_OFFSET, label
            )

        year_start = int(match.group(1))
        year_end = int(match.group(2))

        # 开区间 (year_start, year_end) 覆盖年份 year_start+1 .. year_end-1；
        # 只剩单一年份时无法再拆
        if year_end - year_start <= 2:
            print(f"  [{label}] {year_start}-{year_end} 无法继续拆分，截取前 {SCOPUS_MAX_OFFSET} 条")
            return self._collect_entries(
                self.search(query, start=0, count=SCOPUS_PAGE_SIZE),
                query, SCOPUS_MAX_OFFSET, label
            )

        mid = (year_start + year_end) // 2

        print(f"  [{label}] {year_start}-{year_end} 有 {known_total} 条，拆分为 ({year_start},{mid}] 和 ({mid},{year_end})")

        # 左半含 mid（PUBYEAR < mid+1），右半从 mid+1 起（PUBYEAR > mid）——
        # 旧写法两边都用严格不等号排除 mid，导致每次二分丢失中点年
        q1 = re.sub(r"PUBYEAR\s*<\s*\d+", f"PUBYEAR < {mid + 1}", query)
        q2 = re.sub(r"PUBYEAR\s*>\s*\d+", f"PUBYEAR > {mid}", query)

        entries1, _ = self._fetch_segment(q1, label)
        entries2, _ = self._fetch_segment(q2, label)
        return entries1 + entries2

    def _collect_entries(self, first_page_data, query, total, label=""):
        entries = []
        data = first_page_data
        start = 0

        while True:
            page_entries = data["search-results"].get("entry", [])
            if not page_entries or (len(page_entries) == 1 and "error" in page_entries[0]):
                break

            for entry in page_entries:
                if "error" not in entry:
                    entries.append(entry)

            start += SCOPUS_PAGE_SIZE
            if start >= min(total, SCOPUS_MAX_OFFSET):
                break

            if start % 1000 == 0 and label:
                print(f"  [{label}] 已获取 {start}/{total}")

            data = self.search(query, start=start, count=SCOPUS_PAGE_SIZE)

        if label:
            print(f"  [{label}] 完成，共 {len(entries)} 条")
        return entries
