"""
Loading and validation of CalPERS Private Equity Program fund data.

Source: CalPERS Private Equity Program Fund Performance Review, as of
31 December 2025. The data is fund-level and AGGREGATED — the public source
contains no dated cash flows. That constrains which metrics can be computed
faithfully, and the constraint is documented explicitly in the memo.

The validation layer is not decoration. Every row is cross-checked against
CalPERS' own Investment Multiple column, and rows that do not reconcile are
flagged rather than entering the analysis silently.
"""

import sqlite3
import csv
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "calpers_pep_raw.csv"
DB = ROOT / "data" / "pe.db"

# Tolerance when reconciling against the reported multiple. CalPERS rounds to
# one decimal (1.4x), so anything within +/- 0.05 is consistent.
MULTIPLE_TOLERANCE = 0.05

# CalPERS states the portfolio's aggregate net multiple at the top of the
# document. It serves as an independent check on the entire load: if the
# aggregate reproduces the stated figure, the extract is confirmed against a
# number that appears in no individual row. The tolerance reflects that the
# source rounds to one decimal.
HEADLINE_NET_MULTIPLE = 1.5
HEADLINE_TOLERANCE = 0.05


def parse_pct(value):
    """'12.3%' -> 0.123, 'N/M' -> None."""
    value = value.strip()
    if not value or value == "N/M":
        return None
    return float(value.rstrip("%")) / 100.0


def parse_multiple(value):
    """'1.4x' -> 1.4, 'N/M' -> None."""
    value = value.strip()
    if not value or value == "N/M":
        return None
    return float(value.rstrip("x"))


def parse_money(value):
    value = value.strip().replace(",", "").replace("$", "")
    return float(value) if value else None


def implied_holding_period(tvpi, irr):
    """
    Implied holding period in years: the t solving TVPI = (1+IRR)^t.

    This is not an observed quantity but a consistency check and an input to
    benchmarking: without dated cash flows it is the only way to give a fund's
    multiple a time dimension. It assumes a single contribution and a single
    distribution, which understates true duration for funds with long
    investment periods. Used only to match a benchmark window, never reported
    as a result in its own right.
    """
    if tvpi is None or irr is None:
        return None
    if tvpi <= 0 or irr <= -1:
        return None
    if abs(irr) < 1e-9:
        return None
    return math.log(tvpi) / math.log(1.0 + irr)


def load_rows():
    rows = []
    with RAW.open(newline="", encoding="utf-8") as fh:
        for raw in csv.DictReader(fh):
            cash_in = parse_money(raw["cash_in"])
            cash_out = parse_money(raw["cash_out"])
            total_value = parse_money(raw["cash_out_and_remaining_value"])
            committed = parse_money(raw["capital_committed"])
            reported_multiple = parse_multiple(raw["investment_multiple"])
            net_irr = parse_pct(raw["net_irr"])

            tvpi = total_value / cash_in if cash_in else None
            dpi = cash_out / cash_in if cash_in else None
            rvpi = tvpi - dpi if (tvpi is not None and dpi is not None) else None

            flags = []
            if not cash_in:
                flags.append("no paid-in capital")
            if total_value is not None and cash_out is not None and total_value < cash_out:
                # Remaining value cannot be negative.
                flags.append("total value below distributions")
            if reported_multiple is not None and tvpi is not None:
                if abs(tvpi - reported_multiple) > MULTIPLE_TOLERANCE:
                    flags.append(
                        f"multiple mismatch (computed {tvpi:.2f}x vs reported {reported_multiple:.1f}x)"
                    )
            if rvpi is not None and rvpi < -1e-6:
                flags.append("negative remaining value")

            rows.append({
                "fund": raw["fund"],
                "vintage_year": int(raw["vintage_year"]),
                "capital_committed": committed,
                "cash_in": cash_in,
                "cash_out": cash_out,
                "total_value": total_value,
                "net_irr": net_irr,
                "reported_multiple": reported_multiple,
                "tvpi": tvpi,
                "dpi": dpi,
                "rvpi": rvpi,
                "implied_years": implied_holding_period(tvpi, net_irr),
                "flags": "; ".join(flags),
                "is_clean": 1 if not flags else 0,
            })
    return rows


def build_db(rows):
    DB.unlink(missing_ok=True)
    con = sqlite3.connect(DB)
    con.execute("""
        CREATE TABLE fund (
            fund TEXT PRIMARY KEY,
            vintage_year INTEGER NOT NULL,
            capital_committed REAL,
            cash_in REAL,
            cash_out REAL,
            total_value REAL,
            net_irr REAL,
            reported_multiple REAL,
            tvpi REAL,
            dpi REAL,
            rvpi REAL,
            implied_years REAL,
            flags TEXT,
            is_clean INTEGER NOT NULL
        )
    """)
    con.executemany(
        """INSERT OR REPLACE INTO fund VALUES
           (:fund, :vintage_year, :capital_committed, :cash_in, :cash_out,
            :total_value, :net_irr, :reported_multiple, :tvpi, :dpi, :rvpi,
            :implied_years, :flags, :is_clean)""",
        rows,
    )
    con.commit()
    return con


if __name__ == "__main__":
    rows = load_rows()
    con = build_db(rows)

    total = len(rows)
    flagged = [r for r in rows if not r["is_clean"]]
    with_irr = [r for r in rows if r["net_irr"] is not None]

    print(f"Funds loaded:          {total}")
    print(f"With reported net IRR: {len(with_irr)}")
    print(f"Flagged for review:    {len(flagged)}")
    print()

    if flagged:
        print("Rows failing validation:")
        for r in flagged:
            print(f"  {r['fund'][:55]:<55} vintage {r['vintage_year']}  {r['flags']}")
        print()

    committed = sum(r["capital_committed"] or 0 for r in rows)
    paid_in = sum(r["cash_in"] or 0 for r in rows)
    value = sum(r["total_value"] or 0 for r in rows)
    print(f"Total committed capital: {committed/1e9:,.1f} bn USD")
    print(f"Total paid-in capital:   {paid_in/1e9:,.1f} bn USD")
    print()

    computed = value / paid_in
    print(f"Aggregate net multiple, computed: {computed:.2f}x")
    print(f"Aggregate net multiple, stated:   {HEADLINE_NET_MULTIPLE:.1f}x")
    delta = abs(computed - HEADLINE_NET_MULTIPLE)
    print(f"Difference: {delta:.3f} — "
          f"{'pass' if delta <= HEADLINE_TOLERANCE else 'OUT OF TOLERANCE'}")
