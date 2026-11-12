"""Figures of the annual report, drawn from the CSV files in data/ (no database needed).

  python analysis/figures.py            # all four figures -> output/figs/
  python analysis/figures.py 1 3        # a subset

Fig. 1  bump chart of the top 20 by lifetime MAI, 2000-<year>      (data/rank_panel.csv)
Fig. 2  largest risers and fallers in percentile position           (data/movers.csv)
Fig. 3  reach beyond the literature by rank band; intensity by tier (data/model_composition_<year>.csv, intensity_*.csv)
Fig. 4  licence class and open-source licences by release period    (data/leaderboard_<year>.csv)
"""
import os
import sys
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import matplotlib.scale as mscale
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

ROOT = Path(__file__).resolve().parents[1]
DATA, OUT = ROOT / "data", ROOT / "output" / "figs"
LATEST = int(os.environ.get("MAI_LATEST", 2024))

MM = 1 / 25.4
DOUBLE = 180 * MM
mpl.rcParams.update({
    "font.family": "sans-serif", "font.sans-serif": ["Arial", "Helvetica", "DejaVu Sans"],
    "font.size": 7, "axes.titlesize": 7, "axes.labelsize": 7,
    "xtick.labelsize": 6, "ytick.labelsize": 6, "legend.fontsize": 6,
    "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6,
    "xtick.minor.width": 0.4, "ytick.minor.width": 0.4,
    "xtick.major.size": 2.5, "ytick.major.size": 2.5, "xtick.minor.size": 1.5, "ytick.minor.size": 1.5,
    "xtick.direction": "out", "ytick.direction": "out",
    "axes.spines.top": False, "axes.spines.right": False,
    "legend.frameon": False, "legend.handlelength": 1.6, "legend.borderaxespad": 0.2,
    "lines.linewidth": 1.0, "lines.markersize": 3,
    "pdf.fonttype": 42, "ps.fonttype": 42, "savefig.dpi": 600,
    "figure.constrained_layout.use": True,
    "figure.constrained_layout.h_pad": 0.04, "figure.constrained_layout.w_pad": 0.04,
    "axes.unicode_minus": False,
})
# Okabe-Ito palette
C = dict(orange="#E69F00", sky="#56B4E9", green="#009E73", yellow="#F0E442",
         blue="#0072B2", vermil="#D55E00", purple="#CC79A7")
TIER = {"Foundation": C["vermil"], "Popular": C["blue"], "Niche": "#A6A6A6"}
GROUP_LABEL = {"Foundation": "Top 5%", "Popular": "Next 20%", "Niche": "Remaining 75%"}   # names shown in figures
QUAL = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9",
        "#7B3294", "#8C510A", "#1B9E77", "#E7298A", "#A6761D", "#386CB0"]


def label(ax, letter, dx=-24, dy=2):
    ax.annotate(letter, xy=(0, 1), xycoords="axes fraction", xytext=(dx, dy),
                textcoords="offset points", fontsize=8, fontweight="bold", ha="left", va="bottom")


def save(fig, name):
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / f"{name}.pdf")
    fig.savefig(OUT / f"{name}.png", dpi=600)
    plt.close(fig)
    print(f"  output/figs/{name}.pdf|png")


def pstr(p):
    return "P < 0.001" if p < 0.001 else f"P = {p:.3f}" if p < 0.01 else f"P = {p:.2f}"


def box(ax, groups, colors, positions, width=0.55):
    bp = ax.boxplot(groups, positions=positions, widths=width, showfliers=False, patch_artist=True,
                    medianprops=dict(color="black", lw=0.9), whiskerprops=dict(lw=0.6),
                    capprops=dict(lw=0.6), boxprops=dict(lw=0.6))
    for patch, c in zip(bp["boxes"], colors):
        patch.set_facecolor(mpl.colors.to_rgba(c, 0.25)); patch.set_edgecolor(c)
    for pos, g, c in zip(positions, groups, colors):
        jitter = np.random.default_rng(0).uniform(-0.18, 0.18, len(g))
        ax.scatter(pos + jitter, g, s=3, color=c, alpha=0.55, lw=0, zorder=3)


# ----------------------------------------------------------------------------- Fig. 1
class RankScale(mscale.FuncScale):
    """Linear for ranks 1-20, logarithmic compression beyond."""

    def __init__(self, axis, k=8.0):
        fwd = lambda r: np.where(r <= 20, r, 20 + k * np.log10(np.maximum(r, 20) / 20))
        inv = lambda y: np.where(y <= 20, y, 20 * 10 ** ((y - 20) / k))
        super().__init__(axis, (fwd, inv))


def fig1_bump(y0=2000, y1=LATEST, top=20):
    rp = pd.read_csv(DATA / "rank_panel.csv")
    piv = rp.pivot(index="model_id", columns="year", values="rank")
    lab = rp.drop_duplicates("model_id").set_index("model_id").label
    sel = piv[piv[y1] <= top].index
    years = list(range(y0, y1 + 1))
    early = set(piv.index[piv[y0] <= top])
    fig, ax = plt.subplots(figsize=(DOUBLE, 95 * MM))
    tr = RankScale(None).get_transform()
    ax.set_yscale("function", functions=(tr.transform, tr.inverted().transform))
    ci = 0
    for mid in sorted(sel, key=lambda m: piv.loc[m, y1]):
        s = piv.loc[mid, years]
        if mid in early:
            col, lw, z = "#BFBFBF", 0.9, 2
        else:
            col, lw, z = QUAL[ci % len(QUAL)], 1.3, 3; ci += 1
        ax.plot(years, s.values, color=col, lw=lw, zorder=z, solid_capstyle="round")
        ax.scatter([y1], [s[y1]], s=8, color=col, zorder=4, lw=0)
        ax.text(y1 + 0.4, s[y1], lab[mid], fontsize=5.8, va="center", ha="left", color="#595959" if mid in early else "black")
    ax.set_ylim(300, 0.5)
    ax.set_yticks([1, 5, 10, 15, 20, 50, 100, 200]); ax.set_yticklabels([1, 5, 10, 15, 20, 50, 100, 200])
    ax.axhline(20.5, color="black", lw=0.5, ls=":")
    ax.set_xlim(y0, y1 + 4); ax.set_xticks(range(y0, y1 + 1, 5))
    ax.set_xlabel("Year"); ax.set_ylabel("Rank")
    ax.legend(handles=[Line2D([], [], color="#BFBFBF", lw=1.2, label=f"In top {top} in {y0}"),
                       Line2D([], [], color=C["blue"], lw=1.2, label=f"Entered top {top} after {y0}")], loc="lower left")
    save(fig, "fig1_bump")


# ----------------------------------------------------------------------------- Fig. 2
def fig2_movers(k=12):
    mv = pd.read_csv(DATA / "movers.csv")
    ys, ye = mv.period.iloc[0].split("-")
    up = mv.sort_values("pct_shift", ascending=False).head(k)
    dn = mv.sort_values("pct_shift").head(k)
    d = pd.concat([dn.sort_values("pct_shift"), up.sort_values("pct_shift")]).reset_index(drop=True)
    y = np.arange(len(d))
    fig, ax = plt.subplots(figsize=(DOUBLE, 95 * MM))
    ax.barh(y, 100 * d.pct_shift, color=[C["blue"] if v > 0 else C["orange"] for v in d.pct_shift], height=0.7, lw=0)
    for yi, (_, r) in zip(y, d.iterrows()):
        v = 100 * r.pct_shift
        name = f"{r.label}{' (method)' if r.type_label == 'Method' else ''}"
        detail = f"rank {int(r[f'rank_{ys}'])} → {int(r[f'rank_{ye}'])}, released {int(r.first_year)}"
        ax.text(-1.2 if v > 0 else 1.2, yi, name, fontsize=6, va="center", ha="right" if v > 0 else "left")
        ax.text(v + (1.2 if v > 0 else -1.2), yi, detail, fontsize=5.3, va="center", ha="left" if v > 0 else "right", color="#595959")
    ax.axvline(0, color="black", lw=0.6)
    ax.set_yticks([]); ax.spines["left"].set_visible(False)
    ax.set_xlabel(f"Change in percentile position, {ys}–{ye} (percentage points)")
    hi, lo = 100 * d.pct_shift.max(), 100 * d.pct_shift.min()
    ax.set_xlim(lo - 45, hi + 45)
    ax.text(0.99, 0.98, "Risers →", transform=ax.transAxes, ha="right", va="top", color=C["blue"], fontsize=6.5, fontweight="bold")
    ax.text(0.01, 0.02, "← Fallers", transform=ax.transAxes, ha="left", va="bottom", color=C["orange"], fontsize=6.5, fontweight="bold")
    save(fig, "fig2_movers")


# ----------------------------------------------------------------------------- Fig. 3
RANK_BANDS = [(1, 20), (21, 50), (51, 100), (101, 150), (151, 200), (201, 10 ** 6)]


def fig3_channels():
    mc = pd.read_csv(DATA / f"model_composition_{LATEST}.csv")
    it = pd.read_csv(DATA / f"intensity_{LATEST}.csv")
    itt = pd.read_csv(DATA / "intensity_tests.csv").set_index("variable")
    fig, axs = plt.subplots(1, 2, figsize=(DOUBLE, 62 * MM), width_ratios=[1.25, 1])

    ax = axs[0]
    chans = [("projects", "Funded projects", C["green"]), ("patents", "Patents", C["orange"]),
             ("patent_cites", "Patent citations", C["vermil"])]
    labs, rows = [], []
    for a, b in RANK_BANDS:
        sub = mc[(mc["rank"] >= a) & (mc["rank"] <= b)]
        labs.append(f"{a}–{min(b, len(mc))}\n(n = {len(sub)})")
        rows.append({c: 100 * (sub[c] > 0).mean() for c, _, _ in chans})
    d, x, w = pd.DataFrame(rows), np.arange(len(RANK_BANDS)), 0.26
    for k, (c, lab, col) in enumerate(chans):
        xs = x + (k - 1) * w
        ax.bar(xs, d[c], width=w, color=col, lw=0, label=lab)
        for xi, v in zip(xs, d[c]):
            ax.text(xi, v + 1.5, f"{v:.0f}", ha="center", va="bottom", fontsize=4.8)
    ax.set_xticks(x); ax.set_xticklabels(labs)
    ax.set_xlabel(f"Rank by lifetime MAI, {LATEST}")
    ax.set_ylabel("Entries with at least one record (%)"); ax.set_ylim(0, 108)
    ax.legend(loc="upper right", ncol=1); label(ax, "a")

    ax = axs[1]
    tiers = list(TIER)
    metr = [("cites_per_paper", "Citations per\npublication"), ("projects_per_100_papers", "Projects per\n100 publications")]
    pos, data, cols, ticks, tlabs = [], [], [], [], []
    for m, (col_, mlab) in enumerate(metr):
        for i, t in enumerate(tiers):
            pos.append(m * 4 + i); data.append(it.loc[it.tier == t, col_].dropna().values); cols.append(TIER[t])
        ticks.append(m * 4 + 1); tlabs.append(mlab)
        ax.text(m * 4 + 1, 0.97, pstr(itt.loc[col_, "kruskal_p"]), ha="center", va="top", fontsize=5.5,
                transform=mpl.transforms.blended_transform_factory(ax.transData, ax.transAxes))
    box(ax, data, cols, pos)
    ax.set_yscale("symlog", linthresh=1); ax.set_ylim(0, 150)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:g}"))
    ax.set_xticks(ticks); ax.set_xticklabels(tlabs); ax.set_xlim(-0.8, 6.8)
    ax.set_ylabel("Per-entry ratio")
    ax.legend(handles=[Patch(color=TIER[t], label=f"{GROUP_LABEL[t]} (n = {(it.tier == t).sum()})") for t in tiers],
              loc="lower right", bbox_to_anchor=(1.0, 1.0), ncol=3, columnspacing=1.0, handlelength=1.4)
    label(ax, "b")
    save(fig, "fig3_channels")


# ----------------------------------------------------------------------------- Fig. 4
LIC_GROUPS = [("GPL-3.0", ["GPL-3.0"]), ("GPL-2.0", ["GPL-2.0"]),
              ("Other copyleft", ["LGPL-2.1", "LGPL-3.0", "AGPL-3.0", "MPL-2.0", "EUPL-1.2", "CeCILL"]),
              ("MIT", ["MIT"]), ("BSD / Apache-2.0", ["BSD-2-Clause", "BSD-3-Clause", "Apache-2.0"]),
              ("Other permissive", None), ("Public domain", ["Public Domain"])]
CLASS_ORDER = [("open", "Open source", C["blue"]), ("restricted", "Restricted", C["orange"]),
               ("proprietary", "Proprietary", C["vermil"]), ("freeware", "Freeware", C["yellow"]),
               ("unknown", "Unknown", "#BFBFBF")]


def fig4_open():
    mf = pd.read_csv(DATA / f"leaderboard_{LATEST}.csv")
    mf = mf[mf.release_year >= 1990].copy()
    pers = [("1990–1999", 1990, 1999), ("2000–2009", 2000, 2009), (f"2010–{LATEST}", 2010, LATEST)]
    mf["per"] = None
    for lab, a, b in pers:
        mf.loc[mf.release_year.between(a, b), "per"] = lab
    fig, axs = plt.subplots(1, 2, figsize=(DOUBLE, 62 * MM))

    ax = axs[0]
    for i, (lab, _, _) in enumerate(pers):
        sub = mf[mf.per == lab]; n = len(sub); bottom = 0
        for cls, clab, col in CLASS_ORDER:
            k = int((sub.license_class == cls).sum()); v = 100 * k / n
            ax.bar(i, v, bottom=bottom, color=col, width=0.62, lw=0, label=clab if i == 0 else None)
            if cls == "open":
                ax.text(i, v / 2, f"{v:.0f}%\n{k} of {n}", ha="center", va="center", fontsize=6, color="white", fontweight="bold")
            bottom += v
    ax.set_xticks(range(len(pers))); ax.set_xticklabels([p[0] for p in pers])
    ax.set_xlabel("Release period"); ax.set_ylabel("Share of entries (%)"); ax.set_ylim(0, 100)
    ax.legend(loc="upper left", bbox_to_anchor=(1.0, 1.0)); label(ax, "a")

    ax = axs[1]
    op = mf[mf.license_class == "open"].copy()
    op["grp"] = "Other permissive"
    for g, ls in LIC_GROUPS:
        if ls:
            op.loc[op.license.isin(ls), "grp"] = g
    pal = [C["blue"], C["sky"], "#9ECAE1", C["green"], "#66C2A4", "#C7E9C0", "#A6A6A6"]
    for i, (lab, _, _) in enumerate(pers):
        sub = op[op.per == lab]; n = len(sub); bottom = 0
        for (g, _), col in zip(LIC_GROUPS, pal):
            k = int((sub.grp == g).sum()); v = 100 * k / n
            ax.bar(i, v, bottom=bottom, color=col, width=0.62, lw=0, label=g if i == 0 else None)
            if v >= 7:
                ax.text(i, bottom + v / 2, str(k), ha="center", va="center", fontsize=5.5,
                        color="white" if col in (C["blue"], C["green"], "#A6A6A6") else "black")
            bottom += v
    ax.set_xticks(range(len(pers))); ax.set_xticklabels([f"{p[0]}\n(n = {(op.per == p[0]).sum()})" for p in pers])
    ax.set_xlabel("Release period (open-source entries)"); ax.set_ylabel("Share of open-source entries (%)"); ax.set_ylim(0, 100)
    h, l = ax.get_legend_handles_labels()
    ax.legend(h[::-1], l[::-1], loc="upper left", bbox_to_anchor=(1.0, 1.0)); label(ax, "b")
    save(fig, "fig4_open")


if __name__ == "__main__":
    want = set(sys.argv[1:]) or {"1", "2", "3", "4"}
    for n, f in [("1", fig1_bump), ("2", fig2_movers), ("3", fig3_channels), ("4", fig4_open)]:
        if n in want:
            f()
