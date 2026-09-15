"""
Public Market Equivalent under an explicit timing assumption.

THE PROBLEM
A Kaplan-Schoar PME requires dated cash flows. CalPERS publishes only
fund-level aggregates: total paid-in capital, total distributions and
remaining value as of the valuation date. The dated flows are not public.

THE FIX AND WHAT IT COSTS
The observed totals are distributed across time according to an assumed
profile, and PME is computed on the modelled flows. The totals are data; the
distribution across time is an assumption. PME is therefore never reported
here as a single number, but always as a range across several plausible
profiles. If the conclusion changes sign inside that range, the conclusion
does not hold, and the memo says so.

FORMULA
All flows are carried forward to the valuation date at the index return:

    PME = [ sum_t D_t * (I_T / I_t) + NAV ] / [ sum_t C_t * (I_T / I_t) ]

PME > 1: the fund beat the index for the same capital at the same times.
PME < 1: the LP would have done better in the index.

Note what PME does not adjust for: leverage, illiquidity, and the fact that
NAV on unrealised positions is the GP's own mark. The last of these is
decisive for recent vintages, where NAV is most of the reported value.
"""

import csv
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "pe.db"
INDEX_CSV = ROOT / "data" / "sp500_total_return.csv"

VALUATION_YEAR = 2025  # CalPERS data is as of 31 December 2025.


def load_index():
    """Build index levels, normalised to 1.0 at the end of the first year."""
    returns = {}
    with INDEX_CSV.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            returns[int(row["year"])] = float(row["total_return_pct"]) / 100.0

    first = min(returns)
    levels = {first - 1: 1.0}
    level = 1.0
    for year in range(first, max(returns) + 1):
        level *= 1.0 + returns[year]
        levels[year] = level
    return levels


def profile(vintage, elapsed, contrib_years, dist_start, dist_years):
    """
    Return (contribution weights, distribution weights) by year from the
    vintage onward, truncated at the valuation date and normalised to sum to 1.

    Contributions: linear over contrib_years from the vintage year.
    Distributions: linear over dist_years from vintage + dist_start.

    The profiles are deliberately simple. A more elaborate curve would imply a
    precision that is not there — the uncertainty lies in not knowing the
    timing at all, not in the shape of the curve.
    """
    contrib = {}
    for i in range(contrib_years):
        year = vintage + i
        if year <= VALUATION_YEAR:
            contrib[year] = 1.0

    dist = {}
    for i in range(dist_years):
        year = vintage + dist_start + i
        if year <= VALUATION_YEAR:
            dist[year] = 1.0

    # Truncated profiles: if no window is open yet, place everything on the
    # latest possible year rather than dropping the amount.
    if not contrib:
        contrib = {min(vintage, VALUATION_YEAR): 1.0}
    if not dist:
        dist = {VALUATION_YEAR: 1.0}

    c_total = sum(contrib.values())
    d_total = sum(dist.values())
    contrib = {y: w / c_total for y, w in contrib.items()}
    dist = {y: w / d_total for y, w in dist.items()}
    return contrib, dist


def fund_pme(cash_in, cash_out, nav, vintage, levels,
             contrib_years=4, dist_start=4, dist_years=7):
    if not cash_in or cash_in <= 0:
        return None
    end_level = levels[VALUATION_YEAR]
    contrib_w, dist_w = profile(vintage, VALUATION_YEAR - vintage,
                                contrib_years, dist_start, dist_years)

    fv_contrib = sum(cash_in * w * (end_level / levels[y]) for y, w in contrib_w.items())
    fv_dist = sum(cash_out * w * (end_level / levels[y]) for y, w in dist_w.items())

    if fv_contrib <= 0:
        return None
    return (fv_dist + nav) / fv_contrib


# Sensitivity scenarios: (name, contribution years, distribution start, distribution years)
SCENARIOS = [
    ("fast drawdown, early exits", 3, 3, 6),
    ("base case", 4, 4, 7),
    ("slow drawdown, late exits", 5, 5, 8),
]


def run(con, mature_only=True):
    levels = load_index()
    cutoff = 2021 if mature_only else 9999

    rows = con.execute("""
        SELECT fund, vintage_year, cash_in, cash_out, total_value
        FROM fund
        WHERE is_clean = 1 AND cash_in > 0 AND net_irr IS NOT NULL
    """).fetchall()

    results = {}
    for name, cy, ds, dy in SCENARIOS:
        by_vintage = {}
        for fund, vintage, cash_in, cash_out, total_value in rows:
            if vintage >= cutoff:
                continue
            nav = max(total_value - cash_out, 0.0)
            pme = fund_pme(cash_in, cash_out, nav, vintage, levels, cy, ds, dy)
            if pme is None:
                continue
            by_vintage.setdefault(vintage, []).append(
                (fund, cash_in, pme, cash_out, nav, total_value))
        results[name] = by_vintage
    return results, levels


def pooled_pme(entries, levels, contrib_years, dist_start, dist_years, vintage):
    """Capital-weighted PME for a vintage: aggregate flows before taking the ratio."""
    end_level = levels[VALUATION_YEAR]
    contrib_w, dist_w = profile(vintage, VALUATION_YEAR - vintage,
                                contrib_years, dist_start, dist_years)
    fv_c = fv_d = nav_total = 0.0
    for _fund, cash_in, _pme, cash_out, nav, _tv in entries:
        fv_c += sum(cash_in * w * (end_level / levels[y]) for y, w in contrib_w.items())
        fv_d += sum(cash_out * w * (end_level / levels[y]) for y, w in dist_w.items())
        nav_total += nav
    return (fv_d + nav_total) / fv_c if fv_c > 0 else None


if __name__ == "__main__":
    con = sqlite3.connect(DB)
    results, levels = run(con)

    print("PME BY VINTAGE — CAPITAL-WEIGHTED, BY SCENARIO")
    print("PME > 1.00 means outperformance against the S&P 500 total return.\n")

    vintages = sorted(results["base case"].keys())
    header = f"{'Vintage':>7} {'Funds':>6} {'TVPI':>7}"
    for name, *_ in SCENARIOS:
        header += f" {name.split(',')[0][:12]:>13}"
    print(header)
    print("-" * len(header))

    for vintage in vintages:
        entries = results["base case"][vintage]
        paid_in = sum(e[1] for e in entries)
        value = sum(e[5] for e in entries)
        line = f"{vintage:>7} {len(entries):>6} {value/paid_in:>6.2f}x"
        for name, cy, ds, dy in SCENARIOS:
            p = pooled_pme(results[name][vintage], levels, cy, ds, dy, vintage)
            line += f" {p:>13.2f}"
        print(line)

    print()
    print("AGGREGATE ACROSS MATURE VINTAGES (pre-2021)")
    for name, cy, ds, dy in SCENARIOS:
        fv_c = fv_d = nav_total = 0.0
        end_level = levels[VALUATION_YEAR]
        for vintage, entries in results[name].items():
            contrib_w, dist_w = profile(vintage, VALUATION_YEAR - vintage, cy, ds, dy)
            for _f, cash_in, _p, cash_out, nav, _tv in entries:
                fv_c += sum(cash_in * w * (end_level / levels[y]) for y, w in contrib_w.items())
                fv_d += sum(cash_out * w * (end_level / levels[y]) for y, w in dist_w.items())
                nav_total += nav
        print(f"  {name:<34} PME = {(fv_d + nav_total)/fv_c:.2f}")
