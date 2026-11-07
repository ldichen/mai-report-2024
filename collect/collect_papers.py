"""
论文采集：全称 + 缩写（AND model），领域过滤，去重入库
"""
import os
import json
import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import CHECKPOINT_DIR

from scopus_client import ScopusClient
from db import (
    get_all_models, deduplicate_paper, insert_paper, add_paper_to_model,
)

ENRICH_WORKERS = 20


CHECKPOINT_FILE = os.path.join(CHECKPOINT_DIR, "1paper_search.json")


def load_checkpoint():
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, "r") as f:
            return json.load(f)
    return {"model_id": None, "search_type": None, "completed_models": []}


def save_checkpoint(model_id, search_type, completed_models):
    os.makedirs(CHECKPOINT_DIR, exist_ok=True)
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump({
            "model_id": model_id,
            "search_type": search_type,
            "completed_models": completed_models,
        }, f, indent=2)


def parse_paper_from_entry(entry, source="scopus"):
    """从 Scopus 搜索结果 entry 中提取论文字段"""
    paper = {}

    paper["eid"] = entry.get("eid", "")
    paper["oid"] = entry.get("oid", "")
    paper["title"] = entry.get("title", "")
    paper["doi"] = entry.get("doi", "")
    paper["publicationName"] = entry.get("publicationName", "")
    paper["pageRange"] = entry.get("pageRange", "")
    paper["issueIdentifier"] = entry.get("issueIdentifier", "")
    paper["volume"] = entry.get("volume", "")
    paper["citedby-count"] = int(entry.get("citedby-count", 0) or 0)
    paper["source"] = source

    cover_date = entry.get("coverDate", "")
    paper["publishedStr"] = cover_date

    # 学科领域
    areas = entry.get("subject-area", [])
    if isinstance(areas, dict):
        areas = [areas]
    paper["researchArea"] = []
    for area in areas:
        paper["researchArea"].append({
            "abbrev": area.get("valueabbrev", "") or area.get("abbrev", ""),
            "name": area.get("valuecontent", "") or area.get("content", ""),
        })

    return paper


def enrich_paper(client, paper):
    """用 Abstract API 补充论文详情（作者、摘要、关键词）"""
    eid = paper.get("eid")
    if not eid:
        return paper

    try:
        data = client.fetch_abstract(eid)
    except Exception as e:
        print(f"    Abstract API 请求失败 (eid={eid}): {e}")
        return paper

    if not data:
        return paper

    try:
        resp = data.get("abstracts-retrieval-response", {})
        coredata = resp.get("coredata", {})

        paper["abstract"] = coredata.get("description", "")

        # 作者关键词
        authkeywords = resp.get("authkeywords", {})
        if authkeywords:
            kw_list = authkeywords.get("author-keyword", [])
            if isinstance(kw_list, dict):
                kw_list = [kw_list]
            paper["authorKeyword"] = [kw.get("valuecontent", "") or kw.get("$", "") for kw in kw_list]

        # 作者
        authors = resp.get("authors", {}).get("author", [])
        if isinstance(authors, dict):
            authors = [authors]
        paper["author"] = []
        for a in authors:
            name = f"{a.get('surname', '')} {a.get('given-name', '')}".strip()
            affs = a.get("affiliation", [])
            if isinstance(affs, dict):
                affs = [affs]
            org_list = [{"id": af.get("valueid", ""), "orgName": af.get("affilname", "")} for af in affs]
            paper["author"].append({"name": name, "org": org_list})

        # 机构
        affiliations = resp.get("affiliation", [])
        if isinstance(affiliations, dict):
            affiliations = [affiliations]
        paper["agency"] = [
            {"id": af.get("valueid", ""), "orgName": af.get("affilname", "")}
            for af in affiliations
        ]

    except Exception as e:
        print(f"    补充详情失败 (eid={eid}): {e}")

    return paper


def search_papers_for_model(client, model, search_type, year_start=1900, year_end=None):
    """
    搜索单个模型的论文
    search_type: "full"、"abbrev" 或 "custom"
    返回通过领域过滤的 entry 列表
    """
    if year_end is None:
        year_end = datetime.datetime.now().year + 1

    full_name = model["full_name"]
    abbrev_name = model["abbrev"]

    if search_type == "full" and not full_name:
        return []
    if search_type == "abbrev" and not abbrev_name:
        return []

    if search_type == "custom":
        # model.search_expr：手写检索式，替代上面两条自动生成的。
        # 只含 TITLE-ABS-KEY() 括号内的表达式，年份子句在这里统一拼。
        query = f"TITLE-ABS-KEY ( {model['search_expr']} ) AND PUBYEAR > {year_start} AND PUBYEAR < {year_end}"
        label = f"自定义: {model['search_expr']}"
    elif search_type == "full":
        query = f'TITLE-ABS-KEY ( "{full_name}" ) AND PUBYEAR > {year_start} AND PUBYEAR < {year_end}'
        label = f"全称: {full_name}"
    else:
        query = f"TITLE-ABS-KEY ( {abbrev_name} AND model ) AND PUBYEAR > {year_start} AND PUBYEAR < {year_end}"
        label = f"缩写: {abbrev_name}"

    print(f"  搜索 [{label}]...")
    entries = client.fetch_all_results(query, label=label)

    return entries


def process_model(client, model, year_start=1900, year_end=None):
    """处理单个模型的论文采集"""
    model_id = model["id"]
    full_name = model["full_name"]
    abbrev_name = model["abbrev"]

    print(f"\n{'='*50}")
    print(f"模型: {full_name} ({abbrev_name}) [id={model_id}]")
    print(f"{'='*50}")

    # search_expr 非空时走手写检索式，替代全称 + 缩写两条自动生成的
    search_types = ["custom"] if (model.get("search_expr") or "").strip() else ["full", "abbrev"]
    total_new = 0
    total_dup = 0

    for search_type in search_types:
        entries = search_papers_for_model(client, model, search_type, year_start, year_end)

        new_count = 0
        dup_count = 0
        total = len(entries)

        # 第一步：去重，分出需要 enrich 的新论文
        to_enrich = []
        seen_eids = set()
        for idx, entry in enumerate(entries, 1):
            paper_data = parse_paper_from_entry(entry)

            # 批内去重（日期二分可能产生重复 entry）
            eid = paper_data.get("eid")
            if eid and eid in seen_eids:
                dup_count += 1
                print(f"\r  [{search_type}] 去重: {idx}/{total}", end="", flush=True)
                continue
            if eid:
                seen_eids.add(eid)

            existing_id = deduplicate_paper(paper_data)
            if existing_id:
                add_paper_to_model(model_id, existing_id)
                dup_count += 1
            else:
                to_enrich.append(paper_data)
            print(f"\r  [{search_type}] 去重: {idx}/{total}", end="", flush=True)

        if total > 0:
            print(f"\r  [{search_type}] 去重完成: {len(to_enrich)} 篇新论文, {dup_count} 篇重复        ")

        # 第二步：并发 enrich
        if to_enrich:
            enriched = [None] * len(to_enrich)

            with ThreadPoolExecutor(max_workers=min(ENRICH_WORKERS, len(to_enrich))) as pool:
                future_map = {
                    pool.submit(enrich_paper, client, paper): i
                    for i, paper in enumerate(to_enrich)
                }
                done_count = 0
                for future in as_completed(future_map):
                    i = future_map[future]
                    try:
                        enriched[i] = future.result()
                    except Exception as e:
                        print(f"\n    enrich 失败: {e}")
                        enriched[i] = to_enrich[i]
                    done_count += 1
                    print(f"\r  [{search_type}] enrich: {done_count}/{len(to_enrich)}", end="", flush=True)

            print(f"\r  [{search_type}] enrich 完成: {len(to_enrich)} 篇                    ")

            # 第三步：入库
            for paper_data in enriched:
                paper_id = insert_paper(paper_data)
                add_paper_to_model(model_id, paper_id)
                new_count += 1

        print(f"  [{search_type}] 新增: {new_count}, 重复: {dup_count}")
        total_new += new_count
        total_dup += dup_count

    print(f"  合计 新增: {total_new}, 重复: {total_dup}")
    return total_new, total_dup


def run(year_start=1900, year_end=None):
    """主入口"""
    if year_end is None:
        year_end = datetime.datetime.now().year + 1

    client = ScopusClient()
    models = get_all_models()
    checkpoint = load_checkpoint()
    completed_models = checkpoint.get("completed_models", [])

    print(f"共 {len(models)} 个模型，已完成 {len(completed_models)} 个")
    print(f"年份范围: PUBYEAR > {year_start} AND PUBYEAR < {year_end}")

    for model in models:
        model_id = model["id"]

        if model_id in completed_models:
            continue

        try:
            process_model(client, model, year_start, year_end)
            completed_models.append(model_id)
            save_checkpoint(model_id, "done", completed_models)
        except Exception as e:
            print(f"  模型 {model_id} 处理失败: {e}")
            save_checkpoint(model_id, "error", completed_models)
            continue

    print(f"\n全部完成! 共处理 {len(completed_models)} 个模型")


if __name__ == "__main__":
    run()
