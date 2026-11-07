"""Compute the annual and lifetime MAI of every model and write the mai_score table.

    MAI_t = log10(1 + a1*P + a2*Pat + a3*Proj + a4*PC + a5*PatC)

P, Pat, Proj: publications, patents and funded projects of year t that are linked to the
model with relevance = 1; PC / PatC: distinct citing publications / patents received within
the citation window (2 years for publications, 6 years for patents). The annual score uses
the counts of year t only, the lifetime score the counts accumulated up to year t. The series
of every model runs to MAX_YEAR, so a model without new records in a year gets an annual
score of 0 and an unchanged lifetime score.

Usage:
  python index/compute_mai.py                   # dry run: compute and report differences
  python index/compute_mai.py --backup --apply  # back up mai_score, then rewrite it
"""
import math
import sys
from collections import defaultdict
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor, execute_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import PG_DSN  # noqa: E402

A1, A2, A3, A4, A5 = 2.91, 1.03, 1.12, 3.56, 1.0   # Xu et al. (2022): papers, patents, projects, paper citations, patent citations
PAPER_CITE_WINDOW = 2
PATENT_CITE_WINDOW = 6
MAX_YEAR = 2025


def load_model_papers(conn):
    cur = conn.cursor()
    cur.execute("""
        SELECT mp.model_id, p.id, EXTRACT(YEAR FROM p.published)::int AS yr
        FROM model_paper mp JOIN paper p ON p.id = mp.paper_id
        WHERE mp.relevance = 1 AND p.published IS NOT NULL
    """)
    model_papers = defaultdict(list)
    for mid, pid, yr in cur.fetchall():
        model_papers[mid].append((pid, yr))
    cur.execute("""
        SELECT pc.paper_id, pc.cite_id, EXTRACT(YEAR FROM TO_DATE(c.cover_date, 'YYYY-MM-DD'))::int AS cyr
        FROM paper_citing pc JOIN paper_cite c ON c.id = pc.cite_id
        WHERE c.cover_date IS NOT NULL AND c.cover_date != ''
    """)
    paper_cites = defaultdict(list)
    for pid, cid, cyr in cur.fetchall():
        paper_cites[pid].append((cid, cyr))
    return model_papers, paper_cites


def load_model_patents(conn):
    cur = conn.cursor()
    cur.execute("""
        SELECT mp.model_id, p.id, EXTRACT(YEAR FROM p.grant_date::date)::int AS yr
        FROM model_patent mp JOIN patent p ON p.id = mp.patent_id
        WHERE mp.relevance = 1 AND p.grant_date IS NOT NULL AND p.grant_date != ''
    """)
    model_patents = defaultdict(list)
    for mid, pid, yr in cur.fetchall():
        model_patents[mid].append((pid, yr))
    cur.execute("""
        SELECT pc.patent_id, pc.cite_id, EXTRACT(YEAR FROM c.pub_date)::int AS cyr
        FROM patent_citing pc JOIN patent_cite c ON c.id = pc.cite_id
        WHERE c.pub_date IS NOT NULL
    """)
    patent_cites = defaultdict(list)
    for pid, cid, cyr in cur.fetchall():
        patent_cites[pid].append((cid, cyr))
    return model_patents, patent_cites


def load_model_projects(conn):
    """项目年份：优先开始年，其次立项年，最后结束年。"""
    cur = conn.cursor()
    cur.execute("""
        SELECT mp.model_id, p.start_date, p.end_date, p.create_year
        FROM model_project mp JOIN project p ON p.id = mp.project_id
        WHERE mp.relevance = 1
    """)
    model_projects = defaultdict(list)
    for mid, start, end, create_year in cur.fetchall():
        yr = None
        for cand in (start and start[:4], create_year, end and end[:4]):
            try:
                yr = int(cand)
                break
            except (ValueError, TypeError):
                continue
        if yr and 1900 <= yr <= MAX_YEAR:
            model_projects[mid].append(yr)
    return model_projects


def windowed_cites(id_year, cites, window):
    """cite_id 按年份去重：一条施引记录计入被引年起 window 年内的每一年。"""
    by_year = defaultdict(set)
    for pid, yr in id_year.items():
        for cid, cyr in cites.get(pid, []):
            if cyr >= yr and (cyr - yr) <= window:
                for y in range(cyr, yr + window + 1):
                    by_year[y].add(cid)
    return by_year


def compute_rows(conn):
    """返回 (model_id, year, papers, patents, projects, paper_cites, patent_cites, score_total, score_annual) 行。"""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("SELECT id FROM model WHERE status = 1 ORDER BY id")
    models = [r["id"] for r in cur.fetchall()]

    model_papers, paper_cites = load_model_papers(conn)
    model_patents, patent_cites = load_model_patents(conn)
    model_projects = load_model_projects(conn)
    print(f"models {len(models)}; links: papers {sum(map(len, model_papers.values())):,}, "
          f"patents {sum(map(len, model_patents.values())):,}, projects {sum(map(len, model_projects.values())):,}")

    rows = []
    for mid in models:
        paper_years, paper_id_year = defaultdict(int), {}
        for pid, yr in model_papers.get(mid, []):
            paper_years[yr] += 1
            paper_id_year[pid] = yr
        patent_years, patent_id_year = defaultdict(int), {}
        for pid, yr in model_patents.get(mid, []):
            patent_years[yr] += 1
            patent_id_year[pid] = yr
        project_years = defaultdict(int)
        for yr in model_projects.get(mid, []):
            project_years[yr] += 1
        pc_by_year = windowed_cites(paper_id_year, paper_cites, PAPER_CITE_WINDOW)
        patc_by_year = windowed_cites(patent_id_year, patent_cites, PATENT_CITE_WINDOW)

        years = {y for d in (paper_years, patent_years, project_years, pc_by_year, patc_by_year) for y in d if y is not None}
        if not years:
            continue
        p_t = pat_t = proj_t = 0
        pc_t, patc_t = set(), set()
        for yr in range(min(years), MAX_YEAR + 1):
            p, pat, proj = paper_years.get(yr, 0), patent_years.get(yr, 0), project_years.get(yr, 0)
            pc, patc = pc_by_year.get(yr, set()), patc_by_year.get(yr, set())
            p_t += p; pat_t += pat; proj_t += proj
            pc_t |= pc; patc_t |= patc
            su = math.log10(1 + A1 * p + A2 * pat + A3 * proj + A4 * len(pc) + A5 * len(patc))
            st = math.log10(1 + A1 * p_t + A2 * pat_t + A3 * proj_t + A4 * len(pc_t) + A5 * len(patc_t))
            rows.append((mid, yr, p_t, pat_t, proj_t, len(pc_t), len(patc_t), round(st, 6), round(su, 6)))
    print(f"{len(rows):,} model-years for {len({r[0] for r in rows})} models")
    return rows


def backup(conn):
    with conn.cursor() as cur:
        cur.execute(r"""SELECT coalesce(max(substring(tablename from 'mai_score_bak_(\d+)')::int), 0)
                        FROM pg_tables WHERE tablename ~ '^mai_score_bak_\d+$'""")
        name = f"mai_score_bak_{(cur.fetchone()[0] or 0) + 1}"
        cur.execute(f"CREATE TABLE {name} AS TABLE mai_score")
    conn.commit()
    print(f"mai_score backed up to {name}")


def diff_report(conn, rows, year=MAX_YEAR):
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""SELECT ms.model_id, coalesce(nullif(m.abbrev, ''), m.full_name) AS label, ms.score_total::float AS old
                   FROM mai_score ms JOIN model m ON m.id = ms.model_id AND m.status = 1 WHERE ms.year = %s""", (year,))
    old = {r["model_id"]: (r["label"], r["old"]) for r in cur.fetchall()}
    new = {r[0]: r[7] for r in rows if r[1] == year}
    changed = sorted(((old[m][0], old[m][1], v) for m, v in new.items() if m in old and abs(v - old[m][1]) > 1e-6),
                     key=lambda t: -abs(t[2] - t[1]))
    print(f"{year} cross-section: {len(old)} models in table, {len(new)} computed, {len(changed)} with a changed lifetime MAI")
    for lab, o, n in changed[:20]:
        print(f"  {lab:<16}{o:>9.4f} -> {n:<9.4f} ({n - o:+.4f})")


def apply_rows(conn, rows):
    with conn.cursor() as cur:
        cur.execute("TRUNCATE mai_score")
        execute_values(cur, """INSERT INTO mai_score (model_id, year, papers, patents, projects, paper_cites, patent_cites,
                                                      score_total, score_annual) VALUES %s""", rows, page_size=5000)
    conn.commit()
    print(f"mai_score rewritten: {len(rows):,} rows")


def main():
    args = set(sys.argv[1:])
    conn = psycopg2.connect(PG_DSN)
    if "--backup" in args:
        backup(conn)
    rows = compute_rows(conn)
    diff_report(conn, rows)
    if "--apply" in args:
        apply_rows(conn, rows)
    else:
        print("dry run; add --apply to write the table")
    conn.close()


if __name__ == "__main__":
    main()
