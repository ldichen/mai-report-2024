"""
Stage 2 清洗：LLM 逐条复核（DeepSeek）
对 Stage 1 保留下来的 relevance=1 论文，判断是否真的使用了该模型。

单准则判定：论文是否实际使用/评估了这个模型？跨领域使用算 keep。
只有缩写/名字明确指的是另一个东西才 reject。

hint 从 data/model_hints.csv 读取，没有专属 hint 的模型走通用 hint。

用法:
  python screen/llm_screen.py                         # 全量复核，结果写 CSV
  python screen/llm_screen.py --model-ids 103,138     # 只复核指定模型
  python screen/llm_screen.py --apply                 # 读 CSV 结果写库
"""
import csv
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import psycopg2
import requests
from psycopg2.extras import RealDictCursor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import PG_DSN, LLM_API_URL, LLM_API_KEY, LLM_MODEL

API_URL = LLM_API_URL
API_KEY = LLM_API_KEY
WORKERS = 12
OUT_CSV = os.path.join(os.path.dirname(os.path.dirname(__file__)), "output", "llm_screen.csv")
HINTS_CSV = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "model_hints.csv")

FIELDNAMES = ["model_id", "paper_id", "abbrev", "verdict", "title"]


def load_hints():
    hints = {}
    if os.path.exists(HINTS_CSV):
        with open(HINTS_CSV) as f:
            for row in csv.DictReader(f):
                hints[int(row["model_id"])] = row["hint"]
    return hints


def default_hint(abbrev, full_name):
    name = f"{full_name} ({abbrev})" if abbrev and full_name else (full_name or abbrev or "")
    return f"{name}. Keep if the paper uses or evaluates this model/method."


def build_prompt(abbrev, full_name, hint, title, abstract, journal, keywords):
    info = f"Title: {title or 'N/A'}"
    if journal:
        info += f"\nJournal: {journal}"
    if keywords:
        info += f"\nKeywords: {keywords}"
    if abstract:
        info += f"\nAbstract: {abstract}"
    return f"""You are screening papers for a specific model/method.

Model: {abbrev} ({full_name})
{hint}

Does this paper actually use, evaluate, or study {abbrev or full_name}?
Answer "keep" if yes or plausibly yes.
Answer "reject" ONLY if the name/acronym clearly refers to something else entirely.
When in doubt, keep it.

{info}

Reply with ONLY "keep" or "reject". No explanation."""


def call_llm(prompt, retries=3):
    for _ in range(retries):
        try:
            r = requests.post(API_URL,
                              headers={"Authorization": f"Bearer {API_KEY}",
                                       "Content-Type": "application/json"},
                              json={"model": LLM_MODEL,
                                    "messages": [{"role": "user", "content": prompt}],
                                    "temperature": 0, "max_tokens": 8},
                              timeout=40)
            if r.status_code == 200:
                a = r.json()["choices"][0]["message"]["content"].strip().lower()
                return "reject" if "reject" in a else "keep"
            if r.status_code == 429:
                time.sleep(3)
                continue
            time.sleep(2)
        except Exception:
            time.sleep(2)
    return "error"


def load_done():
    done = {}
    if os.path.exists(OUT_CSV):
        with open(OUT_CSV) as f:
            for row in csv.DictReader(f):
                done[(int(row["model_id"]), int(row["paper_id"]))] = row["verdict"]
    return done


def parse_model_ids(arg):
    """解析 --model-ids 103,138,206"""
    return [int(x.strip()) for x in arg.split(",") if x.strip()]


def run_review(model_ids=None):
    hints = load_hints()
    done = load_done()

    conn = psycopg2.connect(PG_DSN)
    cur = conn.cursor(cursor_factory=RealDictCursor)

    if model_ids:
        cur.execute("""
            SELECT mp.model_id, mp.paper_id, m.abbrev, m.full_name,
                   p.title, p.abstract, p.publication_name, p.author_keywords
            FROM model_paper mp
            JOIN paper p ON p.id = mp.paper_id
            JOIN model m ON m.id = mp.model_id
            WHERE mp.relevance = 1 AND mp.model_id = ANY(%s)
            ORDER BY mp.model_id, mp.paper_id
        """, (model_ids,))
    else:
        cur.execute("""
            SELECT mp.model_id, mp.paper_id, m.abbrev, m.full_name,
                   p.title, p.abstract, p.publication_name, p.author_keywords
            FROM model_paper mp
            JOIN paper p ON p.id = mp.paper_id
            JOIN model m ON m.id = mp.model_id
            WHERE mp.relevance = 1
            ORDER BY mp.model_id, mp.paper_id
        """)

    rows = cur.fetchall()
    conn.close()

    todo = []
    for r in rows:
        key = (r["model_id"], r["paper_id"])
        if key in done:
            continue
        kw_text = ""
        if r["author_keywords"]:
            if isinstance(r["author_keywords"], list):
                kw_text = ", ".join(r["author_keywords"])
            else:
                kw_text = str(r["author_keywords"])
        todo.append(r | {"kw_text": kw_text})

    scope = f"model_ids={model_ids}" if model_ids else "全量"
    print(f"范围: {scope}")
    print(f"relevance=1 共 {len(rows)} 条，已判定 {len(done)} 条，待复核 {len(todo)} 条（{WORKERS} 并发）")

    if not todo:
        print("无待处理项")
        return

    new_file = not os.path.exists(OUT_CSV) or os.path.getsize(OUT_CSV) == 0
    fout = open(OUT_CSV, "a", newline="", encoding="utf-8")
    writer = csv.DictWriter(fout, fieldnames=FIELDNAMES)
    if new_file:
        writer.writeheader()

    stats = {"keep": 0, "reject": 0, "error": 0}

    def work(item):
        mid = item["model_id"]
        abbrev = item["abbrev"] or ""
        full_name = item["full_name"] or ""
        hint = hints.get(mid, default_hint(abbrev, full_name))
        prompt = build_prompt(abbrev, full_name, hint,
                              item["title"], item["abstract"],
                              item["publication_name"], item["kw_text"])
        verdict = call_llm(prompt)
        return {
            "model_id": mid,
            "paper_id": item["paper_id"],
            "abbrev": abbrev,
            "verdict": verdict,
            "title": (item["title"] or "")[:120],
        }

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for i, result in enumerate(pool.map(work, todo), 1):
            writer.writerow(result)
            stats[result["verdict"]] = stats.get(result["verdict"], 0) + 1
            if i % 200 == 0:
                fout.flush()
                print(f"  [{i}/{len(todo)}] keep={stats['keep']} reject={stats['reject']} error={stats['error']}")

    fout.close()
    print(f"\n复核完成: keep={stats['keep']} reject={stats['reject']} error={stats['error']}")
    print(f"结果: {OUT_CSV}")
    print("确认无误后执行: python screen/llm_screen.py --apply")


def apply_results():
    if not os.path.exists(OUT_CSV):
        print(f"结果文件不存在: {OUT_CSV}")
        return
    rejects = []
    with open(OUT_CSV) as f:
        for row in csv.DictReader(f):
            if row["verdict"] == "reject":
                rejects.append((int(row["model_id"]), int(row["paper_id"])))
    print(f"待降级 (1 → -2): {len(rejects)} 条")

    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = True
    cur = conn.cursor()
    n = 0
    for mid, pid in rejects:
        cur.execute("UPDATE model_paper SET relevance = -2 WHERE model_id=%s AND paper_id=%s AND relevance=1",
                    (mid, pid))
        n += cur.rowcount
    conn.close()
    print(f"实际更新 {n} 条")


if __name__ == "__main__":
    if "--apply" in sys.argv:
        apply_results()
    else:
        model_ids = None
        for i, arg in enumerate(sys.argv):
            if arg == "--model-ids" and i + 1 < len(sys.argv):
                model_ids = parse_model_ids(sys.argv[i + 1])
        run_review(model_ids)
