"""Figures for the investment memo. Light surface, PDF output."""

import sqlite3
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from pathlib import Path

import pme as pme_mod

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "pe.db"
OUT = ROOT / "output"

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
BASELINE = "#c3c2b7"
SERIES_1 = "#2a78d6"   # blue   — realised (DPI)
SERIES_2 = "#eb6834"   # orange — unrealised (RVPI)

FIRST_VINTAGE = 2005
LAST_MATURE = 2020

plt.rcParams.update({
    "font.family": "sans-serif",
    "font.sans-serif": ["DejaVu Sans"],
    "figure.facecolor": SURFACE,
    "axes.facecolor": SURFACE,
    "axes.edgecolor": BASELINE,
    "axes.labelcolor": INK_SECONDARY,
    "text.color": INK,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "axes.grid": True,
    "grid.color": GRID,
    "grid.linewidth": 0.6,
})


def _style(ax):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.spines["left"].set_color(BASELINE)
    ax.spines["bottom"].set_color(BASELINE)
    ax.set_axisbelow(True)
    ax.tick_params(length=0, labelsize=8)


def realised_vs_unrealised(con, path):
    """
    Stacked bars by vintage: DPI (realised) + RVPI (unrealised) = TVPI.

    The stack is legitimate — the parts sum to the whole — and it shows exactly
    what a single TVPI figure conceals: how much of the value has come back as
    cash, and how much is still the GP's own mark.
    """
    rows = con.execute("""
        SELECT vintage_year,
               SUM(cash_out) / SUM(cash_in)                      AS dpi,
               (SUM(total_value) - SUM(cash_out)) / SUM(cash_in) AS rvpi
        FROM fund
        WHERE is_clean = 1 AND cash_in > 0 AND vintage_year BETWEEN ? AND ?
        GROUP BY vintage_year ORDER BY vintage_year
    """, (FIRST_VINTAGE, LAST_MATURE)).fetchall()

    years = [str(r[0]) for r in rows]
    dpi = [r[1] for r in rows]
    rvpi = [max(r[2], 0.0) for r in rows]

    fig, ax = plt.subplots(figsize=(9.2, 4.0))
    # 2px surface gap between segments, drawn as a thin edge in the surface colour.
    ax.bar(years, dpi, color=SERIES_1, width=0.62, label="Realised (DPI)",
           edgecolor=SURFACE, linewidth=1.4)
    ax.bar(years, rvpi, bottom=dpi, color=SERIES_2, width=0.62,
           label="Unrealised (RVPI)", edgecolor=SURFACE, linewidth=1.4)

    for x, (d, r) in enumerate(zip(dpi, rvpi)):
        ax.text(x, d + r + 0.05, f"{d + r:.2f}", ha="center", va="bottom",
                fontsize=7, color=INK_SECONDARY)

    ax.set_ylabel("Multiple of paid-in capital", fontsize=8.5)
    ax.set_title("Realised versus unrealised value by vintage",
                 fontsize=11, color=INK, loc="left", pad=12)
    ax.set_ylim(0, max(d + r for d, r in zip(dpi, rvpi)) * 1.16)
    ax.grid(axis="x", visible=False)
    leg = ax.legend(frameon=False, fontsize=8.5, loc="upper right", ncol=2)
    for text in leg.get_texts():
        text.set_color(INK_SECONDARY)
    _style(ax)
    fig.tight_layout()
    fig.savefig(path, dpi=200, facecolor=SURFACE)
    plt.close(fig)


def pme_with_sensitivity(con, path):
    """
    PME by vintage: the base case as a point, the range across all three
    timing assumptions as a vertical bar. The reference line at 1.00 is the
    decision line — if the whole range sits on one side, the sign is robust;
    if it crosses, the conclusion does not hold.
    """
    results, levels = pme_mod.run(con)
    vintages = sorted(v for v in results["base case"] if v >= FIRST_VINTAGE)

    base, lo, hi = [], [], []
    for v in vintages:
        vals = []
        for name, cy, ds, dy in pme_mod.SCENARIOS:
            vals.append(pme_mod.pooled_pme(results[name][v], levels, cy, ds, dy, v))
        base.append(vals[1])
        lo.append(min(vals))
        hi.append(max(vals))

    fig, ax = plt.subplots(figsize=(9.2, 4.0))
    xs = range(len(vintages))

    for x, (l, h) in enumerate(zip(lo, hi)):
        ax.plot([x, x], [l, h], color=BASELINE, linewidth=2,
                solid_capstyle="round", zorder=1)

    robust_under = [i for i, (l, h) in enumerate(zip(lo, hi)) if h < 1.0]
    robust_over = [i for i, (l, h) in enumerate(zip(lo, hi)) if l > 1.0]

    ax.scatter(xs, base, s=42, color=SERIES_1, zorder=3,
               edgecolor=SURFACE, linewidth=1.4)
    ax.scatter([i for i in robust_under], [base[i] for i in robust_under], s=42,
               color=SERIES_2, zorder=4, edgecolor=SURFACE, linewidth=1.4)

    ax.axhline(1.0, color=INK_SECONDARY, linewidth=1.2, linestyle="--", zorder=2)
    ax.text(len(vintages) - 0.4, 1.02, "index", fontsize=7.5,
            color=INK_SECONDARY, ha="right", va="bottom")

    ax.set_xticks(list(xs))
    ax.set_xticklabels([str(v) for v in vintages])
    ax.set_ylabel("PME vs S&P 500 total return", fontsize=8.5)
    ax.set_title("PME by vintage, with the range across three timing assumptions",
                 fontsize=11, color=INK, loc="left", pad=12)
    ax.grid(axis="x", visible=False)

    handles = [
        Line2D([], [], marker="o", linestyle="", markersize=7, color=SERIES_2,
               markeredgecolor=SURFACE, label="Below index in every scenario"),
        Line2D([], [], marker="o", linestyle="", markersize=7, color=SERIES_1,
               markeredgecolor=SURFACE, label="Depends on the assumption"),
        Line2D([], [], linewidth=2, color=BASELINE, label="Range across scenarios"),
    ]
    leg = ax.legend(handles=handles, frameon=False, fontsize=8, loc="upper right")
    for text in leg.get_texts():
        text.set_color(INK_SECONDARY)
    _style(ax)
    fig.tight_layout()
    fig.savefig(path, dpi=200, facecolor=SURFACE)
    plt.close(fig)

    return vintages, base, lo, hi, robust_under, robust_over


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    con = sqlite3.connect(DB)
    realised_vs_unrealised(con, OUT / "fig1_realised.png")
    pme_with_sensitivity(con, OUT / "fig2_pme.png")
    print("Figures written to", OUT)
