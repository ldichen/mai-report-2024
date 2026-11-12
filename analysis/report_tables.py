"""Leaderboard and tables of the annual report.

  python analysis/report_tables.py export   # mai_score + model -> data/leaderboard_<year>.csv (needs the database)
  python analysis/report_tables.py tables   # leaderboard CSV -> output/tables/*.tex and licence summaries

Entries are ranked by the annual MAI of the report year; the lifetime MAI is a reference
column. The change column compares with the previous year's annual rank, which is computed
only over entries released by that year. The momentum table lists entries whose annual and
lifetime ranks differ by at least MIN_GAP places among the top TOP by the better of the two.
"""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA, TEX = ROOT / "data", ROOT / "output" / "tables"

LATEST = int(os.environ.get("MAI_LATEST", 2024))
PREV = LATEST - 1
MIN_GAP, TOP = 20, 100


def esc(s):
    return str(s).replace("&", r"\&").replace("_", r"\_").replace("%", r"\%").replace("#", r"\#")


def export():
    import psycopg2
    from config import PG_DSN
    conn = psycopg2.connect(PG_DSN)
    ms = pd.read_sql("""SELECT ms.model_id, ms.year, ms.papers, ms.patents, ms.projects, ms.paper_cites, ms.patent_cites,
                               ms.score_total::float AS mai, ms.score_annual::float AS annual
                        FROM mai_score ms JOIN model m ON m.id = ms.model_id WHERE m.status = 1""", conn)
    m = pd.read_sql("""SELECT id AS model_id, COALESCE(NULLIF(abbrev, ''), full_name) AS label, full_name, type, license,
                              license_class, release_year, level FROM model WHERE status = 1""", conn).set_index("model_id")
    conn.close()
    now = ms.loc[ms.year == LATEST].set_index("model_id")
    prev = ms.loc[ms.year == PREV].set_index("model_id")
    d = m.join(now[["papers", "patents", "projects", "paper_cites", "patent_cites", "mai", "annual"]])
    d["rank"] = d.annual.rank(ascending=False, method="first").astype(int)
    prev_ok = prev.loc[prev.index.isin(m.index[m.release_year.isna() | (m.release_year <= PREV)])]
    d[f"rank_{PREV}"] = prev_ok.annual.rank(ascending=False, method="first").reindex(d.index)
    d["change"] = d[f"rank_{PREV}"] - d["rank"]
    d["lifetime_rank"] = d.mai.rank(ascending=False, method="first").astype(int)
    d["annual_rank"] = d["rank"]
    d["type_label"] = np.where(d.type == 2, "Method", "Model")
    d.sort_values("rank").to_csv(DATA / f"leaderboard_{LATEST}.csv")
    print(f"leaderboard_{LATEST}.csv: {len(d)} entries")


def leaderboard_tables(d):
    head = r"Rank & {kind} & Annual & Lifetime & Year & Licence & $\Delta$\\"
    cols = r"{@{}r>{\raggedright\arraybackslash}p{5cm}rrrlr@{}}"
    note = (rf" Annual = {LATEST} annual MAI, by which entries are ranked; Lifetime = lifetime MAI; "
            rf"Year = release year; $\Delta$ = change from the {PREV} annual rank of the present panel (new = not ranked in {PREV}). "
            r"Licence = licence class (Section~\ref{sec:panel}): open, OSI-approved or public domain; restricted, academic or non-commercial; "
            r"proprietary; freeware; unknown, no licence found.")

    def table(sub, name, caption, label, long, kind):
        rows = []
        for _, r in sub.iterrows():
            ch = "new" if pd.isna(r[f"rank_{PREV}"]) else (f"{int(r.change):+d}" if r.change != 0 else "0")
            rows.append(f"{int(r['rank'])} & {esc(r.label)} & {r.annual:.2f} & {r.mai:.2f} & "
                        f"{'' if pd.isna(r.release_year) else int(r.release_year)} & {esc(r.license_class)} & {ch} \\\\")
        body, h = "\n".join(rows), head.replace("{kind}", kind)
        if long:   # full list of models as a longtable
            tex = (r"{\small\setlength{\tabcolsep}{5pt}" + "\n" + r"\begin{longtable}" + cols + "\n"
                   + rf"\caption{{{caption}{note}}}\label{{{label}}}\\" + "\n" + r"\toprule" + "\n"
                   + h + "\n" + r"\midrule\endfirsthead" + "\n" + r"\toprule " + h + r" \midrule\endhead" + "\n"
                   + r"\midrule\multicolumn{7}{r}{\textit{continued}}\\ \endfoot \bottomrule\endlastfoot" + "\n"
                   + body + "\n" + r"\end{longtable}}" + "\n")
        else:      # methods as a floating table
            tex = (r"\begin{table}[tbp]\centering\small\setlength{\tabcolsep}{5pt}" + "\n"
                   + rf"\caption{{{caption}{note}}}\label{{{label}}}" + "\n"
                   + r"\begin{tabular}" + cols + r"\toprule" + "\n" + h + r" \midrule" + "\n" + body + "\n"
                   + r"\bottomrule\end{tabular}\end{table}" + "\n")
        (TEX / f"{name}.tex").write_text(tex, encoding="utf-8")

    table(d[d.type_label == "Model"], "table_models", f"MAI leaderboard of models, {LATEST} (ranks are over all {len(d)} entries).", "tab:models", True, "Model")
    table(d[d.type_label == "Method"], "table_methods", f"MAI leaderboard of methods, {LATEST} (ranks are over all {len(d)} entries).", "tab:methods", False, "Method")


def licence_summary(d):
    lic = d.groupby("license_class").size().rename("n").to_frame()
    lic["share"] = lic.n / lic.n.sum()
    top20 = d.head(20)
    lic["n_top20"] = top20.groupby("license_class").size().reindex(lic.index).fillna(0).astype(int)
    lic.to_csv(DATA / "license_class_summary.csv")
    fam = d.loc[d.license_class == "open"].groupby("license").size().sort_values(ascending=False).rename("n")
    fam.to_csv(DATA / "open_license_counts.csv")
    print(f"models {int((d.type_label == 'Model').sum())}, methods {int((d.type_label == 'Method').sum())}, "
          f"open-source share {100 * (d.license_class == 'open').mean():.0f}%, open in top 20: {int((top20.license_class == 'open').sum())}, "
          f"new entries since {PREV}: {int(d[f'rank_{PREV}'].isna().sum())}, median |rank change| {d.change.abs().median():.0f}")
    print(lic.to_string()); print(fam.head(10).to_string())


def momentum_table(d):
    d = d.copy()
    d["gap"] = d.lifetime_rank - d.annual_rank     # positive: annual rank above lifetime rank
    up = d.loc[(d.annual_rank <= TOP) & (d.gap >= MIN_GAP)].sort_values("gap", ascending=False)
    down = d.loc[(d.lifetime_rank <= TOP) & (d.gap <= -MIN_GAP)].sort_values("gap")
    pd.concat([up.assign(group="rising"), down.assign(group="levelling")]).to_csv(DATA / f"momentum_{LATEST}.csv", index=False)

    def rows(sub):
        return "\n".join(f"{esc(r.label)} & {'' if pd.isna(r.release_year) else int(r.release_year)} & {int(r.annual_rank)} & "
                         f"{int(r.lifetime_rank)} & {int(r.gap):+d} \\\\" for _, r in sub.iterrows())
    tex = (r"\begin{table}[tbp]\centering\small\setlength{\tabcolsep}{5pt}" + "\n"
           + rf"\caption{{Entries whose {LATEST} annual rank and lifetime rank differ by at least {MIN_GAP} places, among entries ranked in the top {TOP} by the better of the two ranks. "
           + r"Gap = lifetime rank $-$ annual rank. A positive gap means that the influence of an entry in 2024 is ahead of its accumulated influence, and a negative gap the reverse. Year = release year.}\label{tab:momentum}" + "\n"
           + r"\begin{tabular}{@{}>{\raggedright\arraybackslash}p{3.2cm}rrrr@{}}\toprule" + "\n"
           + r"Entry & Year & Annual rank & Lifetime rank & Gap\\ \midrule" + "\n"
           + rf"\multicolumn{{5}}{{@{{}}l}}{{\textit{{Annual rank above lifetime rank}} ({len(up)} entries)}}\\" + "\n" + rows(up) + "\n" + r"\midrule" + "\n"
           + rf"\multicolumn{{5}}{{@{{}}l}}{{\textit{{Annual rank below lifetime rank}} ({len(down)} entries)}}\\" + "\n" + rows(down) + "\n"
           + r"\bottomrule\end{tabular}\end{table}" + "\n")
    (TEX / "table_momentum.tex").write_text(tex, encoding="utf-8")
    for name, sub in (("rising", up), ("levelling off", down)):
        print(f"{name}: {len(sub)} entries, median release year {sub.release_year.median():.0f}")
    print(f"Spearman correlation of annual and lifetime ranks: {d.annual_rank.corr(d.lifetime_rank, method='spearman'):.3f}")


def tables():
    TEX.mkdir(parents=True, exist_ok=True)
    d = pd.read_csv(DATA / f"leaderboard_{LATEST}.csv").sort_values("rank")
    leaderboard_tables(d)
    licence_summary(d)
    momentum_table(d)


if __name__ == "__main__":
    {"export": export, "tables": tables}[sys.argv[1] if len(sys.argv) > 1 else "tables"]()
