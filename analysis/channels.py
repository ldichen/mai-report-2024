"""Reach beyond the literature and intensity by tier (Sections 3.4 and 3.5 of the report).

  python analysis/channels.py export    # mai_score counts of the report year -> data/model_composition_<year>.csv
  python analysis/channels.py analyse   # -> output/channel_presence.csv, data/intensity_<year>.csv, data/intensity_tests.csv

Presence: share of entries in each lifetime-rank band with at least one funded project, patent
or patent citation. Intensity: citations per publication and projects per 100 publications of
each entry, compared across percentile tiers (Kruskal-Wallis; Foundation vs Niche by
Mann-Whitney U with Cliff's delta).
"""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA, OUT = ROOT / "data", ROOT / "output"

LATEST = int(os.environ.get("MAI_LATEST", 2024))
TIERS = ["Foundation", "Popular", "Niche"]
TYPE_LABEL = {1: "Model", 2: "Method"}
RANK_BANDS = [(1, 20), (21, 50), (51, 100), (101, 150), (151, 200), (201, 10 ** 6)]


def tiers_by_percentile(rank, n):
    rel = (np.asarray(rank) - 1) / n
    return np.select([rel < 0.05, rel < 0.25], ["Foundation", "Popular"], default="Niche")


def export():
    import psycopg2
    from config import PG_DSN
    conn = psycopg2.connect(PG_DSN)
    g = pd.read_sql(f"""
        SELECT ms.model_id, ms.year, ms.papers, ms.patents, ms.projects, ms.paper_cites, ms.patent_cites,
               ms.score_total::float AS score_total,
               COALESCE(NULLIF(m.abbrev, ''), m.full_name) AS label, m.type, m.license_class
        FROM mai_score ms JOIN model m ON m.id = ms.model_id
        WHERE m.status = 1 AND ms.year = {LATEST} ORDER BY ms.score_total DESC, ms.model_id""", conn)
    conn.close()
    g["type_label"] = g.type.map(TYPE_LABEL).fillna("Unknown")
    g["rank"] = g.score_total.rank(method="min", ascending=False).astype(int)
    g["tier"] = tiers_by_percentile(g["rank"], len(g))
    g.drop(columns="type").to_csv(DATA / f"model_composition_{LATEST}.csv", index=False)
    print(f"model_composition_{LATEST}.csv: {len(g)} entries")


def cliffs_delta(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    return float(np.sign(x[:, None] - y[None, :]).mean())


def presence(g):
    rows = []
    for a, b in RANK_BANDS:
        sub = g[(g["rank"] >= a) & (g["rank"] <= b)]
        rows.append({"band": f"{a}-{min(b, len(g))}", "n": len(sub),
                     **{f"{c}_pct": 100 * (sub[c] > 0).mean() for c in ("projects", "patents", "patent_cites")}})
    return pd.DataFrame(rows)


def intensity(g):
    out = g[["model_id", "label", "tier", "type_label", "rank", "score_total", "papers"]].copy()
    P = g.papers.where(g.papers > 0)
    out["cites_per_paper"] = g.paper_cites / P
    out["patents_per_100_papers"] = 100 * g.patents / P
    out["projects_per_100_papers"] = 100 * g.projects / P
    out["patent_cites_per_patent"] = g.patent_cites / g.patents.where(g.patents > 0)
    return out


def intensity_tests(it):
    rows = []
    f, p_, n_ = (it.loc[it.tier == t] for t in TIERS)
    mo, me = it.loc[it.type_label == "Model"], it.loc[it.type_label == "Method"]
    for col in ["cites_per_paper", "patents_per_100_papers", "projects_per_100_papers"]:
        d = {k: v[col].dropna() for k, v in {"f": f, "p": p_, "n": n_, "mo": mo, "me": me}.items()}
        rows.append({"variable": col,
                     "foundation_median": d["f"].median(), "popular_median": d["p"].median(), "niche_median": d["n"].median(),
                     "kruskal_p": stats.kruskal(d["f"], d["p"], d["n"]).pvalue,
                     "found_vs_niche_p": stats.mannwhitneyu(d["f"], d["n"]).pvalue,
                     "found_vs_niche_cliff": cliffs_delta(d["f"], d["n"]),
                     "model_median": d["mo"].median(), "method_median": d["me"].median(),
                     "model_vs_method_p": stats.mannwhitneyu(d["mo"], d["me"]).pvalue,
                     "model_vs_method_cliff": cliffs_delta(d["mo"], d["me"])})
    return pd.DataFrame(rows)


def analyse():
    g = pd.read_csv(DATA / f"model_composition_{LATEST}.csv")
    OUT.mkdir(exist_ok=True)
    pr = presence(g)
    pr.to_csv(OUT / "channel_presence.csv", index=False)
    it = intensity(g)
    it.to_csv(DATA / f"intensity_{LATEST}.csv", index=False)
    tests = intensity_tests(it)
    tests.to_csv(DATA / "intensity_tests.csv", index=False)
    top50 = g[g["rank"] <= 50]
    print(f"entries with at least one funded project: {100 * (g.projects > 0).mean():.0f}%; "
          f"top 50 with a patent: {100 * (top50.patents > 0).mean():.0f}%")
    print(pr.round(1).to_string(index=False))
    print("\npublications per entry by tier (min / median):")
    print(g.groupby("tier").papers.agg(["size", "min", "median"]).reindex(TIERS).to_string())
    print("\n" + tests.round(4).to_string(index=False))


if __name__ == "__main__":
    {"export": export, "analyse": analyse}[sys.argv[1] if len(sys.argv) > 1 else "analyse"]()
