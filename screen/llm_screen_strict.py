"""Strict re-screening of paper links for models whose name is a common word or an
ambiguous acronym (NEWTS, GLM, OTIS, Hogback ...).

The lenient first pass keeps a paper when in doubt. For the models listed in
data/model_hints_strict.csv every paper link with relevance = 1 is judged again with a
strict prompt that rejects when the evidence is ambiguous, and with the release year as an
additional check. Verdicts are appended to output/llm_screen_strict*.csv, finished rows are
skipped on restart, and --apply writes the rejections (relevance 1 -> -2) to the database.

Usage:
  python screen/llm_screen_strict.py --shard 0/3        # one of three shards (model_id modulo 3)
  python screen/llm_screen_strict.py --models 288,283   # selected models only
  python screen/llm_screen_strict.py --limit 1          # a single batch, for testing
  python screen/llm_screen_strict.py --summary
  python screen/llm_screen_strict.py --apply
"""
import csv
import datetime as dt
import glob
import os
import re
import sys
import time

import psycopg2
from psycopg2.extras import RealDictCursor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import PG_DSN
from llm_cli import call_llm_batch, parse_batch_response, BACKEND

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HINTS = os.path.join(ROOT, "data", "model_hints_strict.csv")
BATCH = 40
ABS_MAX = 900
SNIP = 220
FIELDNAMES = ["model_id", "paper_id", "abbrev", "year", "verdict", "title"]


def arg_value(name, default=None):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def load_strict_hints():
    with open(HINTS) as f:
        return {int(r["model_id"]): r["hint"] for r in csv.DictReader(f)}


def name_pattern(n):
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(n).replace(r"\ ", r"[\s-]?") + r"(?![A-Za-z0-9])", re.I)


def fetch_rows(model_ids):
    conn = psycopg2.connect(PG_DSN)
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT mp.model_id, mp.paper_id, m.abbrev, m.full_name, m.aliases, m.release_year,
               p.title, p.abstract, p.publication_name, p.author_keywords, EXTRACT(YEAR FROM p.published)::int AS year
        FROM model_paper mp JOIN paper p ON p.id = mp.paper_id JOIN model m ON m.id = mp.model_id
        WHERE mp.relevance = 1 AND mp.model_id = ANY(%s)
        ORDER BY mp.model_id, mp.paper_id
    """, (model_ids,))
    rows = cur.fetchall()
    conn.close()
    return rows


def snippet(text, names):
    if len(text) <= ABS_MAX:
        return text
    spans = []
    for n in names:
        for m in name_pattern(n).finditer(text):
            spans.append((max(0, m.start() - SNIP), min(len(text), m.end() + SNIP)))
    spans.sort()
    merged = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    body = " ".join("…" + text[a:b] + "…" for a, b in merged[:3])
    head = text[:max(0, ABS_MAX - len(body) - 1)] if len(body) < ABS_MAX else ""
    return (head + " " + body).strip()[:ABS_MAX] if body else text[:ABS_MAX]


def build_prompt(abbrev, full_name, aliases, release_year, hint, items):
    names = [n for n in [abbrev, full_name] + [a.strip() for a in (aliases or "").replace("|", ";").split(";")] if n and len(n.strip()) >= 3]
    lines = []
    for r in items:
        kw0 = r["author_keywords"] or ""
        kw = ("; ".join(str(x) for x in kw0) if isinstance(kw0, (list, tuple)) else str(kw0).replace("|", "; "))[:200]
        ab = snippet((r["abstract"] or "").replace("\n", " "), names)
        line = f"ID={r['paper_id']} | {r['title'] or 'N/A'}"
        if r.get("year"):
            line += f" | Year: {r['year']}"
        if r.get("publication_name"):
            line += f" | Journal: {r['publication_name']}"
        if kw:
            line += f" | Keywords: {kw}"
        if ab:
            line += f" | Abstract: {ab}"
        lines.append(line)
    label = abbrev or full_name
    rel = f" The model was first released in {int(release_year)}; a paper published well before that year can only be kept if it describes the model's own development or an earlier version of it." if release_year else ""
    return f"""You are screening papers for a specific geoscientific model/method whose name is also a common word or an ambiguous acronym, so the screening must be strict.

Model: {abbrev} ({full_name})
{hint}{rel}

For each paper below, judge whether the paper actually runs, applies, develops, couples, evaluates or compares THIS specific model/software, or cites it as the tool used in the study.
- "keep" if the paper clearly or very plausibly uses this specific model.
- "reject" if the name refers to something else, if it is used only as a generic word or concept, or if the paper merely mentions the model in passing in its literature review without using it.
- Abstracts are excerpted: the opening plus the passages where the name occurs.
- Unlike a lenient screen, when the evidence is ambiguous, reject.

Papers:
{chr(10).join(lines)}

Reply with ONLY a JSON array, one object per paper, in order:
[{{"id": <paper_id>, "verdict": "keep"}}, {{"id": <paper_id>, "verdict": "reject"}}, ...]
No explanation, no markdown, just the JSON array."""


def load_done(paths):
    done = {}
    for path in paths:
        if os.path.exists(path):
            with open(path) as f:
                for row in csv.DictReader(f):
                    if row["verdict"] != "error":
                        done[(int(row["model_id"]), int(row["paper_id"]))] = row["verdict"]
    return done


def run(shard, limit, out_csv, model_ids):
    hints = load_strict_hints()
    ids = model_ids or sorted(hints)
    if shard:
        i, n = (int(x) for x in shard.split("/"))
        ids = [m for m in ids if m % n == i]
    rows = fetch_rows(ids)
    done = load_done([out_csv])
    todo = [r for r in rows if (r["model_id"], r["paper_id"]) not in done]
    print(f"{len(todo)} of {len(rows)} links to screen ({len(done)} done), models {ids} -> {os.path.basename(out_csv)}", flush=True)
    by_model = {}
    for r in todo:
        by_model.setdefault(r["model_id"], []).append(r)
    new_file = not os.path.exists(out_csv)
    n_batches = 0
    t0 = time.time()
    with open(out_csv, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if new_file:
            w.writeheader()
        for mid, items in by_model.items():
            ab, full, aliases, rel = items[0]["abbrev"], items[0]["full_name"], items[0]["aliases"], items[0]["release_year"]
            hint = hints[mid]
            for bi in range(0, len(items), BATCH):
                if limit and n_batches >= limit:
                    return
                batch = items[bi:bi + BATCH]
                pids = [r["paper_id"] for r in batch]
                verdicts = parse_batch_response(call_llm_batch(build_prompt(ab, full, aliases, rel, hint, batch)), pids)
                n_batches += 1
                nk = nr = ne = 0
                for r in batch:
                    v = (verdicts or {}).get(r["paper_id"], "error")
                    nk += v == "keep"; nr += v == "reject"; ne += v == "error"
                    w.writerow({"model_id": mid, "paper_id": r["paper_id"], "abbrev": ab or full, "year": r["year"] or "",
                                "verdict": v, "title": (r["title"] or "")[:80]})
                f.flush()
                print(f"  {(ab or full)[:12]:<12} batch {bi // BATCH + 1}/{-(-len(items) // BATCH)}: keep {nk} reject {nr} error {ne}  [{time.time() - t0:.0f}s]", flush=True)


def all_results():
    res = {}
    for path in sorted(glob.glob(os.path.join(ROOT, "output", "llm_screen_strict*.csv"))):
        with open(path) as f:
            for row in csv.DictReader(f):
                key = (int(row["model_id"]), int(row["paper_id"]))
                if row["verdict"] == "error" and key in res:
                    continue
                res[key] = row
    return res


def summary():
    from collections import Counter
    res = all_results()
    print("total", len(res), dict(Counter(r["verdict"] for r in res.values())))
    per = {}
    for r in res.values():
        d = per.setdefault(r["abbrev"], Counter()); d[r["verdict"]] += 1
        if r["verdict"] == "reject" and r["year"] and r["year"].isdigit():
            d["reject_pre"] += 1
    print(f"{'model':<24}{'keep':>6}{'reject':>8}{'error':>7}{'reject%':>9}")
    for ab, d in sorted(per.items(), key=lambda kv: -kv[1]["reject"]):
        tot = d["keep"] + d["reject"]
        print(f"{ab[:23]:<24}{d['keep']:>6}{d['reject']:>8}{d['error']:>7}{(100 * d['reject'] / tot if tot else 0):>8.0f}%")


def apply():
    res = all_results()
    today = dt.date.today().strftime("%Y%m%d")
    log_path = os.path.join(ROOT, "output", f"llm_screen_strict_applied_{today}.csv")
    conn = psycopg2.connect(PG_DSN); cur = conn.cursor()
    n = 0
    with open(log_path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["model_id", "paper_id", "abbrev", "year", "old_relevance", "new_relevance", "reason", "title"])
        for (mid, pid), r in res.items():
            if r["verdict"] != "reject":
                continue
            cur.execute("UPDATE model_paper SET relevance = -2 WHERE model_id = %s AND paper_id = %s AND relevance = 1", (mid, pid))
            if cur.rowcount:
                n += 1
                w.writerow([mid, pid, r["abbrev"], r["year"], 1, -2, f"strict {BACKEND} re-review (ambiguous name)", r["title"]])
    conn.commit()
    cur.execute("SELECT relevance, count(*) FROM model_paper GROUP BY 1 ORDER BY 1")
    print(f"{n} links set from 1 to -2; relevance counts now {cur.fetchall()}; log {log_path}")
    conn.close()


if __name__ == "__main__":
    if "--apply" in sys.argv:
        apply()
    elif "--summary" in sys.argv:
        summary()
    else:
        shard = arg_value("--shard")
        tag = f"_s{shard.split('/')[0]}" if shard else ""
        models = [int(x) for x in arg_value("--models", "").split(",") if x.strip()]
        run(shard, int(arg_value("--limit", 0)), arg_value("--out", os.path.join(ROOT, "output", f"llm_screen_strict{tag}.csv")), models)
