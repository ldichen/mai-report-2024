"""
Stage 1 清洗：基于领域关键词过滤明显不相关的关联
关键词来源：model_keywords.csv（每个模型的人工校准关键词）
匹配逻辑：论文/专利/项目文本与关键词完全无交集 → 标 -1
模型名匹配：abbrev + model.aliases（版本别名），见 model_names()

用法:
  python screen/keyword_filter.py [--dry-run] [--model-ids 151,166,289]
"""
import re
import csv
import time
import json
import os
import psycopg2
from psycopg2.extras import RealDictCursor

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import PG_DSN
BATCH_SIZE = 2000
KEYWORDS_CSV = str(Path(__file__).resolve().parents[1] / "data" / "model_keywords.csv")

ABSTRACT_MIN_MATCHES = 3

def extract_words(text):
    if not text:
        return set()
    return set(re.findall(r'[a-zA-Z]{3,}', text.lower()))


def build_all_model_keywords():
    """从 model_keywords.csv 加载关键词，计算跨模型泛词"""
    from collections import Counter
    word_model_count = Counter()
    raw_keywords = {}

    with open(KEYWORDS_CSV) as f:
        for row in csv.DictReader(f):
            mid = int(row["model_id"])
            words = set()
            for kw in row["keywords"].split(","):
                words |= extract_words(kw.strip())
            raw_keywords[mid] = words
            for w in words:
                word_model_count[w] += 1

    threshold = len(raw_keywords) * 0.10
    weak_words = {w for w, c in word_model_count.items() if c >= threshold}

    result = {}
    for mid, words in raw_keywords.items():
        result[mid] = {"all": words, "strong": words - weak_words}

    return result, weak_words


_abbrev_pattern_cache = {}


def model_names(model):
    """模型在文本中可能出现的名字：abbrev + aliases（'|' 分隔）。

    abbrev 是系列名（如 GFDL-ESM），论文里写的多是版本名
    （GFDL-ESM2M、HadGEM2-ES、CLM5）。裸 abbrev 的 \\b 边界匹配不到字母后紧跟数字
    的后缀，所以必须把 aliases 一起拿来匹配。model 可以是 dict（含 abbrev / aliases）
    或 str（兼容旧调用，只有 abbrev）。
    """
    if isinstance(model, str):
        return [model] if model else []
    names = []
    for n in [model.get("abbrev") or ""] + (model.get("aliases") or "").split("|"):
        n = n.strip()
        if n and n not in names:
            names.append(n)
    return names


def _has_abbrev_model(text, abbrev):
    """检查文本中是否包含 '缩写 + model' 模式，如 'SWAT model'、'PHREEQC models'"""
    if not text or not abbrev:
        return False
    if abbrev not in _abbrev_pattern_cache:
        _abbrev_pattern_cache[abbrev] = re.compile(
            r'\b' + re.escape(abbrev) + r'(\s+\w+)?\s+models?\b', re.IGNORECASE
        )
    return bool(_abbrev_pattern_cache[abbrev].search(text))


_name_pattern_cache = {}

def _text_has_abbrev(text, abbrev):
    """检查文本中是否包含模型缩写（整词匹配）"""
    if not text or not abbrev:
        return False
    if abbrev not in _name_pattern_cache:
        _name_pattern_cache[abbrev] = re.compile(r'\b' + re.escape(abbrev) + r'\b', re.IGNORECASE)
    return bool(_name_pattern_cache[abbrev].search(text))


def text_matches_tiered(title, abstract, author_kw_text, subject_text, keywords_info, abbrev=""):
    """分层匹配：
    0) 文本中出现 "缩写 model"（如 SWAT model）→ 直接相关
    1) title / author_keywords / subject_areas 命中任意 1 个关键词 → 相关
    2) abstract 命中 ≥ ABSTRACT_MIN_MATCHES 个关键词 → 相关
    3) abstract 命中 ≥ 1 个强关键词（非跨模型泛词）→ 相关
    4) 缩写出现在文本中 + 至少 1 个领域关键词也出现 → 相关
    5) 否则 → 不相关
    """
    # abbrev 可以是 str、list（多个名字）或 dict（含 abbrev / aliases）
    names = model_names(abbrev) if isinstance(abbrev, (str, dict)) else [n for n in (abbrev or []) if n]
    if names:
        for text in (title, abstract, author_kw_text):
            for n in names:
                if _has_abbrev_model(text, n):
                    return True

    all_kw = keywords_info["all"]
    strong_kw = keywords_info["strong"]

    title_words = extract_words(title)
    kw_words = extract_words(author_kw_text)
    subj_words = extract_words(subject_text)
    primary_words = title_words | kw_words | subj_words
    if primary_words & all_kw:
        return True

    abs_words = extract_words(abstract)

    if len(abs_words & all_kw) >= ABSTRACT_MIN_MATCHES:
        return True
    if abs_words & strong_kw:
        return True

    if names:
        all_text = " ".join(str(x) for x in (title, abstract, author_kw_text, subject_text) if x)
        if any(_text_has_abbrev(all_text, n) for n in names):
            all_words = title_words | kw_words | subj_words | abs_words
            if all_words & all_kw:
                return True

    return False


def process_model_papers(cur, model, keywords_info, dry_run=False):
    mid = model['id']
    cur.execute("""
        SELECT mp.paper_id, p.title, p.abstract, p.author_keywords, p.subject_areas
        FROM model_paper mp
        JOIN paper p ON mp.paper_id = p.id
        WHERE mp.model_id = %s AND (mp.relevance = 0 OR mp.relevance IS NULL)
    """, (mid,))
    papers = cur.fetchall()
    if not papers:
        return 0, 0

    irrelevant_ids = []
    relevant_ids = []
    for p in papers:
        subject_text = ""
        if p['subject_areas']:
            try:
                sa = p['subject_areas'] if isinstance(p['subject_areas'], list) else json.loads(p['subject_areas'])
                parts = []
                for s in sa:
                    if isinstance(s, dict):
                        parts.append(s.get("name", "") or s.get("$", ""))
                        parts.append(s.get("abbrev", ""))
                subject_text = " ".join(pt for pt in parts if pt)
            except:
                subject_text = str(p['subject_areas'])

        kw_text = ""
        if p['author_keywords']:
            if isinstance(p['author_keywords'], list):
                kw_text = " ".join(p['author_keywords'])
            else:
                kw_text = str(p['author_keywords'])

        if text_matches_tiered(p['title'], p['abstract'], kw_text, subject_text, keywords_info, model):
            relevant_ids.append(p['paper_id'])
        else:
            irrelevant_ids.append(p['paper_id'])

    if not dry_run:
        for i in range(0, len(irrelevant_ids), BATCH_SIZE):
            batch = irrelevant_ids[i:i+BATCH_SIZE]
            cur.execute("UPDATE model_paper SET relevance = -1 WHERE model_id = %s AND paper_id = ANY(%s)", (mid, batch))
        for i in range(0, len(relevant_ids), BATCH_SIZE):
            batch = relevant_ids[i:i+BATCH_SIZE]
            cur.execute("UPDATE model_paper SET relevance = 1 WHERE model_id = %s AND paper_id = ANY(%s)", (mid, batch))

    return len(papers), len(irrelevant_ids)


def process_model_patents(cur, model, keywords_info, dry_run=False):
    mid = model['id']
    cur.execute("""
        SELECT mp.patent_id, p.title, p.abstract, p.prior_art_keywords
        FROM model_patent mp
        JOIN patent p ON mp.patent_id = p.id
        WHERE mp.model_id = %s AND (mp.relevance = 0 OR mp.relevance IS NULL)
    """, (mid,))
    patents = cur.fetchall()
    if not patents:
        return 0, 0

    irrelevant_ids = []
    relevant_ids = []
    for p in patents:
        kw_text = ""
        if p['prior_art_keywords']:
            if isinstance(p['prior_art_keywords'], list):
                kw_text = " ".join(p['prior_art_keywords'])
            else:
                kw_text = str(p['prior_art_keywords'])
        if text_matches_tiered(p['title'], p['abstract'], kw_text, "", keywords_info, model):
            relevant_ids.append(p['patent_id'])
        else:
            irrelevant_ids.append(p['patent_id'])

    if not dry_run:
        for i in range(0, len(irrelevant_ids), BATCH_SIZE):
            batch = irrelevant_ids[i:i+BATCH_SIZE]
            cur.execute("UPDATE model_patent SET relevance = -1 WHERE model_id = %s AND patent_id = ANY(%s)", (mid, batch))
        for i in range(0, len(relevant_ids), BATCH_SIZE):
            batch = relevant_ids[i:i+BATCH_SIZE]
            cur.execute("UPDATE model_patent SET relevance = 1 WHERE model_id = %s AND patent_id = ANY(%s)", (mid, batch))

    return len(patents), len(irrelevant_ids)


def process_model_projects(cur, model, keywords_info, dry_run=False):
    mid = model['id']
    cur.execute("""
        SELECT mp.project_id, p.project_name, p.keyword, p.abstract,
               p.project_name_en, p.keyword_en, p.abstract_en
        FROM model_project mp
        JOIN project p ON mp.project_id = p.id
        WHERE mp.model_id = %s AND (mp.relevance = 0 OR mp.relevance IS NULL)
    """, (mid,))
    projects = cur.fetchall()
    if not projects:
        return 0, 0

    irrelevant_ids = []
    relevant_ids = []
    for p in projects:
        title = " ".join(str(x) for x in [p['project_name'], p['project_name_en']] if x)
        kw_text = " ".join(str(x) for x in [p['keyword'], p['keyword_en']] if x)
        abstract = " ".join(str(x) for x in [p['abstract'], p['abstract_en']] if x)
        if text_matches_tiered(title, abstract, kw_text, "", keywords_info, model):
            relevant_ids.append(p['project_id'])
        else:
            irrelevant_ids.append(p['project_id'])

    if not dry_run:
        for i in range(0, len(irrelevant_ids), BATCH_SIZE):
            batch = irrelevant_ids[i:i+BATCH_SIZE]
            cur.execute("UPDATE model_project SET relevance = -1 WHERE model_id = %s AND project_id = ANY(%s)", (mid, batch))
        for i in range(0, len(relevant_ids), BATCH_SIZE):
            batch = relevant_ids[i:i+BATCH_SIZE]
            cur.execute("UPDATE model_project SET relevance = 1 WHERE model_id = %s AND project_id = ANY(%s)", (mid, batch))

    return len(projects), len(irrelevant_ids)


def main():
    import sys
    dry_run = "--dry-run" in sys.argv

    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("SELECT id, full_name, abbrev, aliases FROM model ORDER BY id")
    models = cur.fetchall()
    if "--model-ids" in sys.argv:
        ids = {int(x) for x in sys.argv[sys.argv.index("--model-ids") + 1].split(",")}
        models = [m for m in models if m["id"] in ids]
        print(f"只处理 {len(models)} 个模型: {sorted(ids)}")

    print("从 model_keywords.csv 加载关键词...")
    all_keywords, weak_words = build_all_model_keywords()
    print(f"  共 {len(all_keywords)} 个模型有关键词，跨模型泛词 {len(weak_words)} 个: {sorted(weak_words)}")
    print(f"  abstract 仅命中泛词时需 ≥{ABSTRACT_MIN_MATCHES} 个才保留\n")

    if dry_run:
        print("=== DRY RUN 模式（不写数据库）===\n")
    else:
        print("=== 正式执行 ===\n")

    t0 = time.time()
    total_stats = {
        "paper_checked": 0, "paper_irrelevant": 0,
        "patent_checked": 0, "patent_irrelevant": 0,
        "project_checked": 0, "project_irrelevant": 0,
    }

    for i, m in enumerate(models):
        mid = m['id']
        kw_info = all_keywords.get(mid)

        if not kw_info or not kw_info["all"]:
            continue

        pc, pi = process_model_papers(cur, m, kw_info, dry_run)
        tc, ti = process_model_patents(cur, m, kw_info, dry_run)
        jc, ji = process_model_projects(cur, m, kw_info, dry_run)

        total_stats["paper_checked"] += pc
        total_stats["paper_irrelevant"] += pi
        total_stats["patent_checked"] += tc
        total_stats["patent_irrelevant"] += ti
        total_stats["project_checked"] += jc
        total_stats["project_irrelevant"] += ji

        if pc + tc + jc > 0 and (pi + ti + ji > 100 or (i+1) % 30 == 0):
            elapsed = time.time() - t0
            pct = f"{pi*100/pc:.0f}%" if pc else "-"
            print(f"  [{i+1}/{len(models)}] {m['abbrev']:<12} paper={pi}/{pc}({pct})  patent={ti}/{tc}  project={ji}/{jc}  [{elapsed:.0f}s]")

    elapsed = time.time() - t0
    print(f"\n{'DRY RUN ' if dry_run else ''}完成 [{elapsed:.0f}s]")
    print(f"  论文:   {total_stats['paper_irrelevant']:>6} / {total_stats['paper_checked']:>6} 标为不相关")
    print(f"  专利:   {total_stats['patent_irrelevant']:>6} / {total_stats['patent_checked']:>6} 标为不相关")
    print(f"  项目:   {total_stats['project_irrelevant']:>6} / {total_stats['project_checked']:>6} 标为不相关")

    if not dry_run:
        print("\n=== 最终统计 ===")
        for table in ['model_paper', 'model_patent', 'model_project']:
            cur.execute(f"SELECT relevance, COUNT(*) as cnt FROM {table} GROUP BY relevance ORDER BY relevance")
            rows = cur.fetchall()
            stats = {r['relevance']: r['cnt'] for r in rows}
            print(f"  {table}: {stats}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
