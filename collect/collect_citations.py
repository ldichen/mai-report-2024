"""
论文引用采集：对每篇相关论文（relevance=1），查询施引论文
支持多线程并发、断点续采、>5000 引用自动年份二分
"""
import os
import json
import time
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import CHECKPOINT_DIR, CITE_WORKERS
from scopus_client import ScopusClient
from db import (
    get_all_models, get_model_by_id, get_papers_for_model,
    deduplicate_cite, insert_cites_batch, update_paper_citings,
)


CHECKPOINT_FILE = os.path.join(CHECKPOINT_DIR, "paper_ref.json")


def load_checkpoint():
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, "r") as f:
            return json.load(f)
    return {"completed_models": []}


def save_checkpoint(completed_models):
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump({"completed_models": completed_models}, f, indent=2)


def fetch_citing_papers(client, eid, year_start=1900, year_end=None):
    """查询引用了指定论文的所有施引论文，一次请求获取总数+数据"""
    if year_end is None:
        year_end = datetime.datetime.now().year + 1
    query = f"REF({eid}) AND PUBYEAR > {year_start} AND PUBYEAR < {year_end}"
    return client.fetch_all_with_total(query)


def process_paper_citations(client, paper, year_start=1900, year_end=None):
    """处理单篇论文的引用采集"""
    eid = paper.get("eid")
    if not eid:
        return 0

    if paper.get("citations_collected"):
        return 0

    entries, total = fetch_citing_papers(client, eid, year_start, year_end)

    if not entries:
        update_paper_citings(paper["id"], [], 0)
        return 0

    citing_ids = []
    new_cites = []

    for entry in entries:
        existing_id = deduplicate_cite(entry)
        if existing_id:
            if existing_id not in citing_ids:
                citing_ids.append(existing_id)
        else:
            new_cites.append(entry)

    seen_eids = set()
    unique_new_cites = []
    for cite in new_cites:
        cite_eid = cite.get("eid", "")
        if cite_eid and cite_eid in seen_eids:
            continue
        if cite_eid:
            seen_eids.add(cite_eid)
        unique_new_cites.append(cite)

    if unique_new_cites:
        inserted_ids = insert_cites_batch(unique_new_cites)
        citing_ids.extend(inserted_ids)

    update_paper_citings(paper["id"], citing_ids, total)

    return len(citing_ids)


def process_model_citations(client, model_id, year_start=1900, year_end=None):
    """多线程处理单个模型所有论文的引用"""
    model = get_model_by_id(model_id)
    if not model:
        print(f"  模型 {model_id} 不存在，跳过")
        return 0

    full_name = model["full_name"]
    abbrev_name = model["abbrev"]

    papers = get_papers_for_model(model_id)
    pending = [p for p in papers if not p.get("citations_collected") and p.get("eid")]

    print(f"\n{'='*50}")
    print(f"引用采集: {full_name} ({abbrev_name}) [id={model_id}]")
    print(f"  相关论文: {len(papers)}, 待采集: {len(pending)}")
    print(f"{'='*50}")

    if not pending:
        return 0

    total_cites = 0
    done = 0
    failed = 0
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=CITE_WORKERS) as pool:
        futures = {
            pool.submit(process_paper_citations, client, p, year_start, year_end): p
            for p in pending
        }
        for future in as_completed(futures):
            try:
                cite_count = future.result()
                total_cites += cite_count
            except Exception as e:
                paper = futures[future]
                print(f"  paper_id={paper['id']} 失败: {e}")
                failed += 1
            done += 1
            if done % 100 == 0:
                elapsed = time.time() - t0
                speed = done / elapsed if elapsed > 0 else 0
                eta = (len(pending) - done) / speed / 60 if speed > 0 else 0
                print(f"  进度: {done}/{len(pending)} ({done*100//len(pending)}%) "
                      f"| 施引: {total_cites} | {speed:.1f} papers/s | ETA {eta:.0f}min"
                      f"{f' | 失败: {failed}' if failed else ''}")

    elapsed = time.time() - t0
    print(f"  完成! [{elapsed/60:.1f}min] 施引: {total_cites}, 失败: {failed}")
    return total_cites


def run(year_start=1900, year_end=None):
    """主入口"""
    if year_end is None:
        year_end = datetime.datetime.now().year + 1

    client = ScopusClient()
    models = get_all_models()
    checkpoint = load_checkpoint()
    completed_models = checkpoint.get("completed_models", [])

    pending_models = [m for m in models if m["id"] not in completed_models]
    print(f"共 {len(models)} 个模型，已完成 {len(completed_models)} 个，待处理 {len(pending_models)} 个")
    print(f"并发: {CITE_WORKERS} 线程\n")

    for model in pending_models:
        model_id = model["id"]

        if client.alive_count() == 0:
            print("\n所有 API key 已用完，退出。重新运行可从断点继续。")
            break

        try:
            process_model_citations(client, model_id, year_start, year_end)
            completed_models.append(model_id)
            save_checkpoint(completed_models)
        except Exception as e:
            print(f"  模型 {model_id} 引用采集失败: {e}")
            save_checkpoint(completed_models)
            continue

    print(f"\n全部完成! 共处理 {len(completed_models)} 个模型")


if __name__ == "__main__":
    run()
