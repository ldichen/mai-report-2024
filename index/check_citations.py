"""Citation completeness check: for every paper linked to a panel model with relevance = 1,
compare the citation count reported by Scopus (cited_by_count) with the number of citing
records actually collected. Writes output/citation_completeness.csv, one row per model.

Usage: python index/check_citations.py
"""
import os
import sys
import warnings

import pandas as pd
import psycopg2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import PG_DSN  # noqa: E402

warnings.filterwarnings("ignore")
LATEST = int(os.environ.get("MAI_LATEST", 2024))

conn = psycopg2.connect(PG_DSN)
df = pd.read_sql(f"""
WITH mp AS (SELECT mp.model_id, mp.paper_id FROM model_paper mp JOIN model m ON m.id = mp.model_id
            WHERE mp.relevance = 1 AND m.status = 1),
     pc AS (SELECT paper_id, count(*) AS n_cit FROM paper_citing pg JOIN paper_cite c ON c.id = pg.cite_id GROUP BY 1)
SELECT m.id AS model_id, m.abbrev, m.full_name, s.score_total AS mai_{LATEST},
       count(*) AS n_papers,
       sum(CASE WHEN NOT p.citations_collected THEN 1 ELSE 0 END) AS n_uncollected,
       sum(coalesce(p.cited_by_count, 0)) AS scopus_cites,
       sum(coalesce(pc.n_cit, 0)) AS collected_cites,
       sum(coalesce(p.cited_by_count, 0)) - sum(coalesce(pc.n_cit, 0)) AS gap
FROM mp JOIN paper p ON p.id = mp.paper_id JOIN model m ON m.id = mp.model_id
LEFT JOIN pc ON pc.paper_id = p.id
LEFT JOIN mai_score s ON s.model_id = m.id AND s.year = {LATEST}
GROUP BY 1, 2, 3, 4 ORDER BY 4 DESC NULLS LAST""", conn)
conn.close()

df["gap_share"] = (df.gap / df.scopus_cites.replace(0, pd.NA)).astype(float).round(5)
out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output", "citation_completeness.csv")
os.makedirs(os.path.dirname(out), exist_ok=True)
df.to_csv(out, index=False)
tot = df[["n_papers", "n_uncollected", "scopus_cites", "collected_cites", "gap"]].sum()
print(df.head(20).to_string())
print(f"\n{len(df)} models, {int(tot.n_papers):,} paper links, {int(tot.n_uncollected)} papers without collected citations; "
      f"Scopus citations {int(tot.scopus_cites):,}, collected {int(tot.collected_cites):,}, gap {int(tot.gap):,} ({tot.gap / tot.scopus_cites:.4%})")
print(f"models with a gap above 1%: {int((df.gap_share > 0.01).sum())}; largest gap share: {df.gap_share.max()}")
