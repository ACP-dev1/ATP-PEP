"""
Extracts the CalPERS fund table directly from the published PDF.

This replaces an earlier text-scrape. Here the table cells are read
programmatically from the document, so there is no transcription step between
the source and the data file.

Usage:  python3 src/extract_pdf.py <path to CalPERS PDF>
"""

import csv
import re
import sys
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "calpers_pep_raw.csv"

HEADERS = ["fund", "vintage_year", "capital_committed", "cash_in", "cash_out",
           "cash_out_and_remaining_value", "net_irr", "investment_multiple"]


def clean_money(value):
    """'$24,096,354' -> '24096354'. Parentheses denote negative amounts."""
    if value is None:
        return ""
    v = value.strip().replace("$", "").replace(",", "").replace("\n", "")
    negative = v.startswith("(") and v.endswith(")")
    v = v.strip("()")
    if not v or not re.fullmatch(r"-?\d+(\.\d+)?", v):
        return ""
    return f"-{v}" if negative else v


def clean_marker(value):
    """Strips footnote markers: 'N/M 1' -> 'N/M', '5.3% 2' -> '5.3%'."""
    if value is None:
        return ""
    v = " ".join(value.split())
    v = re.sub(r"\s+\d+$", "", v)
    return v.strip()


def parse(pdf_path):
    rows = []
    seen = set()
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                for cells in table:
                    if not cells or len(cells) < 7:
                        continue
                    fund = (cells[0] or "").replace("\n", " ").strip()
                    vintage = (cells[1] or "").strip()

                    # Data rows have a fund name and a four-digit vintage year.
                    if not fund or not re.fullmatch(r"(19|20)\d{2}", vintage):
                        continue

                    committed = clean_money(cells[2])
                    cash_in = clean_money(cells[3])
                    cash_out = clean_money(cells[4])
                    total = clean_money(cells[5])

                    # Net IRR and multiple can land in different trailing cells
                    # because the column header is split in the PDF's layout.
                    tail = [clean_marker(c) for c in cells[6:] if clean_marker(c)]
                    irr = next((t for t in tail if t.endswith("%") or t == "N/M"), "")
                    mult = next((t for t in tail if t.endswith("x") or t == "N/M"), "")

                    if not cash_in:
                        continue

                    key = (fund, vintage)
                    if key in seen:
                        continue
                    seen.add(key)

                    rows.append([fund, vintage, committed, cash_in, cash_out,
                                 total, irr or "N/M", mult or "N/M"])
    return rows


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: python3 src/extract_pdf.py <path to CalPERS PDF>")

    rows = parse(sys.argv[1])
    with OUT.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(HEADERS)
        writer.writerows(rows)

    print(f"Extracted {len(rows)} funds to {OUT}")
    vintages = sorted({int(r[1]) for r in rows})
    print(f"Vintages: {vintages[0]}-{vintages[-1]}")
    committed = sum(float(r[2]) for r in rows if r[2])
    paid_in = sum(float(r[3]) for r in rows if r[3])
    print(f"Total committed: {committed/1e9:,.1f} bn USD")
    print(f"Total paid in:   {paid_in/1e9:,.1f} bn USD")
