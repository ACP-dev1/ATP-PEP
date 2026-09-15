"""
Vintage-level analysis of the CalPERS private equity portfolio.

Two aggregation methods are reported side by side on purpose:

  pooled TVPI = sum(total value) / sum(paid-in capital)
  equal TVPI  = mean of the individual funds' TVPI

Pooled is what the LP actually earned, because large commitments carry weight.
Equal-weighted is what a manager list appears to show, because every fund
counts the same. The gap between them is itself a result: when it is wide, the
returns sit somewhere other than the activity.

Young vintages are excluded from conclusions. A 2023 fund has realised
nothing, its value is the GP's own mark, and the J-curve makes both IRR and
TVPI misleading early in a fund's life.
"""

import sqlite3
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "pe.db"

# Vintages from this year onward are treated as immature and excluded.
IMMATURE_FROM = 2021


def vintage_table(con, only_clean=True):
    where = "WHERE is_clean = 1" if only_clean else ""
    rows = con.execute(f"""
        SELECT vintage_year, fund, cash_in, cash_out, total_value, net_irr, tvpi, dpi
        FROM fund {where}
    """).fetchall()

    by_vintage = {}
    for vintage, fund, cash_in, cash_out, total_value, irr, tvpi, dpi in rows:
        if not cash_in:
            continue
        by_vintage.setdefault(vintage, []).append({
            "fund": fund, "cash_in": cash_in, "cash_out": cash_out,
            "total_value": total_value, "irr": irr, "tvpi": tvpi, "dpi": dpi,
        })

    out = []
    for vintage in sorted(by_vintage):
        funds = by_vintage[vintage]
        paid_in = sum(f["cash_in"] for f in funds)
        value = sum(f["total_value"] for f in funds)
        distributed = sum(f["cash_out"] for f in funds)
        tvpis = [f["tvpi"] for f in funds if f["tvpi"] is not None]
        irrs = [f["irr"] for f in funds if f["irr"] is not None]

        out.append({
            "vintage": vintage,
            "n": len(funds),
            "paid_in": paid_in,
            "pooled_tvpi": value / paid_in,
            "pooled_dpi": distributed / paid_in,
            "equal_tvpi": statistics.mean(tvpis) if tvpis else None,
            "median_tvpi": statistics.median(tvpis) if tvpis else None,
            "median_irr": statistics.median(irrs) if irrs else None,
            "n_with_irr": len(irrs),
            "mature": vintage < IMMATURE_FROM,
        })
    return out


def dispersion(con):
    """Spread within a vintage — how much the manager choice mattered."""
    rows = con.execute("""
        SELECT vintage_year, tvpi FROM fund
        WHERE is_clean = 1 AND tvpi IS NOT NULL AND net_irr IS NOT NULL
    """).fetchall()
    by_vintage = {}
    for vintage, tvpi in rows:
        by_vintage.setdefault(vintage, []).append(tvpi)

    out = []
    for vintage, tvpis in sorted(by_vintage.items()):
        if len(tvpis) < 5:
            continue
        tvpis.sort()
        n = len(tvpis)
        q1 = tvpis[n // 4]
        q3 = tvpis[(3 * n) // 4]
        out.append({
            "vintage": vintage, "n": n, "q1": q1, "median": statistics.median(tvpis),
            "q3": q3, "spread": q3 - q1, "best": tvpis[-1], "worst": tvpis[0],
        })
    return out


if __name__ == "__main__":
    con = sqlite3.connect(DB)

    print("VINTAGE SUMMARY  (validated rows only)")
    print(f"{'Vintage':>7} {'Funds':>6} {'Paid in':>12} {'Pooled':>8} {'Equal':>8} "
          f"{'Median':>8} {'Med.IRR':>8} {'DPI':>7}")
    print("-" * 70)
    for r in vintage_table(con):
        marker = "" if r["mature"] else "  (immature)"
        eq = f"{r['equal_tvpi']:.2f}x" if r["equal_tvpi"] else "-"
        med = f"{r['median_tvpi']:.2f}x" if r["median_tvpi"] else "-"
        irr = f"{r['median_irr']*100:.1f}%" if r["median_irr"] is not None else "-"
        print(f"{r['vintage']:>7} {r['n']:>6} {r['paid_in']/1e9:>10.2f} bn "
              f"{r['pooled_tvpi']:>7.2f}x {eq:>8} {med:>8} {irr:>8} "
              f"{r['pooled_dpi']:>6.2f}x{marker}")

    print()
    print("DISPERSION WITHIN VINTAGE  (vintages with at least 5 funds)")
    print(f"{'Vintage':>7} {'n':>4} {'Q1':>7} {'Median':>8} {'Q3':>7} "
          f"{'Q3-Q1':>7} {'Worst':>9} {'Best':>7}")
    print("-" * 62)
    for r in dispersion(con):
        print(f"{r['vintage']:>7} {r['n']:>4} {r['q1']:>6.2f}x {r['median']:>7.2f}x "
              f"{r['q3']:>6.2f}x {r['spread']:>6.2f}x {r['worst']:>8.2f}x {r['best']:>6.2f}x")
