"""Rank panel and leaderboard mobility (Section 3.3 of the report).

Two steps, so that the analysis can be re-run from the CSV without database access:

  python analysis/rank_dynamics.py export     # mai_score -> data/rank_panel.csv (needs the database)
  python analysis/rank_dynamics.py analyse    # data/rank_panel.csv -> data/movers.csv, output/mobility.md

The panel ranks every model in every year by lifetime MAI (score_total) and, where the
column is present, by annual MAI (score_annual). Ties keep the model_id order so that a tie
cannot produce a spurious swap. Percentile tiers are assigned each year: top 5 % Foundation,
next 20 % Popular, the rest Niche.
"""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA, OUT = ROOT / "data", ROOT / "output"

LATEST = int(os.environ.get("MAI_LATEST", 2024))
START = 1990                       # window for the flux statistics
CROSS_YEARS = [LATEST - 15, LATEST - 10, LATEST - 5, LATEST]
MOVER_PERIOD = (LATEST - 10, LATEST)
TIERS = ["Foundation", "Popular", "Niche"]
TYPE_LABEL = {1: "Model", 2: "Method"}


def tiers_by_percentile(rank, n):
    rel = (np.asarray(rank) - 1) / n
    return np.select([rel < 0.05, rel < 0.25], ["Foundation", "Popular"], default="Niche")


# ----------------------------------------------------------------------------- export
def export():
    import psycopg2
    from config import PG_DSN
    conn = psycopg2.connect(PG_DSN)
    panel = pd.read_sql(f"""
        SELECT ms.model_id, ms.year, ms.score_total::float AS score_total, ms.score_annual::float AS score_annual,
               COALESCE(NULLIF(m.abbrev, ''), m.full_name) AS label, m.type, m.license_class, m.release_year
        FROM mai_score ms JOIN model m ON m.id = ms.model_id
        WHERE m.status = 1 AND ms.year <= {LATEST}
        ORDER BY ms.year, ms.score_total DESC, ms.model_id""", conn)
    conn.close()
    panel["type_label"] = panel.type.map(TYPE_LABEL).fillna("Unknown")
    panel["rank"] = panel.groupby("year").cumcount() + 1
    panel["n_year"] = panel.groupby("year").model_id.transform("size")
    panel["pct_rank"] = panel["rank"] / panel.n_year
    panel["rank_annual"] = (panel.sort_values(["year", "score_annual", "model_id"], ascending=[True, False, True])
                            .groupby("year").cumcount() + 1).reindex(panel.index)
    # age counts from the verified release year; models without one from their first year with records
    first = panel.groupby("model_id").year.transform("min")
    panel["first_year"] = panel.release_year.fillna(first).astype(int)
    panel["tier_year"] = panel.groupby("year", group_keys=False)["rank"].apply(
        lambda r: pd.Series(tiers_by_percentile(r.values, len(r)), index=r.index))
    cols = ["model_id", "label", "type_label", "license_class", "year", "score_total", "score_annual",
            "rank", "rank_annual", "n_year", "pct_rank", "first_year", "tier_year"]
    panel = panel.loc[panel.year >= START]      # ranks and first years are computed on the full series, the file starts in START
    panel[cols].to_csv(DATA / "rank_panel.csv", index=False)
    print(f"rank_panel.csv: {len(panel):,} rows, {panel.model_id.nunique()} models, {panel.year.min()}-{panel.year.max()}")


# ----------------------------------------------------------------------------- analyse
def tier_retention(panel):
    """Share of each tier's members that keep the tier between consecutive cross-sections."""
    rows = []
    cs = {y: panel.loc[panel.year == y].set_index("model_id").tier_year for y in CROSS_YEARS}
    for y0, y1 in zip(CROSS_YEARS[:-1], CROSS_YEARS[1:]):
        both = cs[y0].to_frame("t0").join(cs[y1].rename("t1"), how="inner")
        for t in TIERS:
            sub = both.loc[both.t0 == t]
            rows.append({"from": y0, "to": y1, "tier": t, "n": len(sub), "kept": int((sub.t1 == t).sum()),
                         "kept_share": (sub.t1 == t).mean() if len(sub) else np.nan,
                         "moved_up": int((sub.t1.map(TIERS.index) < TIERS.index(t)).sum())})
    y0, y1 = CROSS_YEARS[0], CROSS_YEARS[-1]
    both = cs[y0].to_frame("t0").join(cs[y1].rename("t1"), how="inner")
    for t in TIERS:
        sub = both.loc[both.t0 == t]
        rows.append({"from": y0, "to": y1, "tier": t, "n": len(sub), "kept": int((sub.t1 == t).sum()),
                     "kept_share": (sub.t1 == t).mean() if len(sub) else np.nan,
                     "moved_up": int((sub.t1.map(TIERS.index) < TIERS.index(t)).sum())})
    return pd.DataFrame(rows)


def top_flux(panel, rank_col, n0=20, start=START):
    """Iniguez et al. (2022): yearly share of top-n0 positions whose occupant was not in the top n0 the year before."""
    R = panel.loc[panel.year >= start].pivot(index="model_id", columns="year", values=rank_col)
    years = [y for y in R.columns if R[y].notna().sum() >= n0]
    tops = {y: set(R.index[R[y] <= n0]) for y in years}
    flux = pd.DataFrame({"year": years[1:], "entered": [len(tops[b] - tops[a]) for a, b in zip(years[:-1], years[1:])]})
    flux["F_t"] = flux.entered / n0
    return flux


def unchanged_since(panel, rank_col, k):
    """Last year in which the set of the first k positions differed from the latest year."""
    R = panel.pivot(index="model_id", columns="year", values=rank_col)
    latest = set(R.index[R[LATEST] <= k])
    for y in sorted(R.columns, reverse=True):
        if set(R.index[R[y] <= k]) != latest:
            return y + 1
    return int(R.columns.min())


def movers(panel, y0, y1):
    a = panel.loc[panel.year == y0, ["model_id", "rank", "pct_rank", "score_total"]]
    b = panel.loc[panel.year == y1, ["model_id", "label", "type_label", "license_class", "first_year",
                                     "rank", "pct_rank", "score_total", "tier_year"]]
    m = b.merge(a, on="model_id", suffixes=(f"_{y1}", f"_{y0}"))
    m = m.loc[m.first_year <= y0]          # only entries released by the start of the window
    m["rank_change"] = m[f"rank_{y0}"] - m[f"rank_{y1}"]
    m["pct_shift"] = m[f"pct_rank_{y0}"] - m[f"pct_rank_{y1}"]   # positive = moved up
    m["mai_gain"] = m[f"score_total_{y1}"] - m[f"score_total_{y0}"]
    m["age_at_start"] = y0 - m.first_year
    m.insert(0, "period", f"{y0}-{y1}")
    return m.sort_values("pct_shift", ascending=False).reset_index(drop=True)


def analyse(k=12):
    panel = pd.read_csv(DATA / "rank_panel.csv")
    OUT.mkdir(exist_ok=True)
    L = [f"# Leaderboard mobility, {START}-{LATEST}\n"]

    ret = tier_retention(panel)
    ret.to_csv(OUT / "tier_retention.csv", index=False)
    L += ["## Tier retention between cross-sections\n", ret.to_string(index=False), ""]

    for col, name in [("rank", "lifetime"), ("rank_annual", "annual")]:
        if col not in panel or panel[col].isna().all():
            L.append(f"(no {name} ranks in the panel; re-run `export`)\n")
            continue
        fl = top_flux(panel, col)
        fl.to_csv(OUT / f"top20_flux_{name}.csv", index=False)
        L += [f"## Top-20 flux, {name} ranking", f"mean F = {fl.F_t.mean():.3f} ({fl.entered.mean():.2f} entries a year), "
              f"{fl.year.min()}-{fl.year.max()}; top 3 unchanged since {unchanged_since(panel, col, 3)}, "
              f"first place since {unchanged_since(panel, col, 1)}", ""]

    y0, y1 = MOVER_PERIOD
    mv = movers(panel, y0, y1)
    mv.to_csv(DATA / "movers.csv", index=False)
    up, dn = mv.head(k), mv.tail(k)
    L += [f"## Largest risers and fallers in percentile position, {y0}-{y1} (entries released by {y0}, n = {len(mv)})",
          f"risers: median age in {y0} = {up.age_at_start.median():.1f}; fallers: {dn.age_at_start.median():.1f}", "",
          up[["label", "type_label", "first_year", f"rank_{y0}", f"rank_{y1}", "pct_shift", "mai_gain"]].to_string(index=False), "",
          dn[["label", "type_label", "first_year", f"rank_{y0}", f"rank_{y1}", "pct_shift", "mai_gain"]].to_string(index=False), ""]
    (OUT / "mobility.md").write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    {"export": export, "analyse": analyse}[sys.argv[1] if len(sys.argv) > 1 else "analyse"]()
