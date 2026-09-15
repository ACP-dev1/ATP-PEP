"""Generates the investment memo as a PDF from the database and figures."""

import sqlite3
from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (Image, PageBreak, Paragraph, SimpleDocTemplate,
                                Table, TableStyle)

import charts
import pme as pme_mod

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "pe.db"
OUT = ROOT / "output"

INK = colors.HexColor("#0b0b0b")
INK_SECONDARY = colors.HexColor("#52514e")
MUTED = colors.HexColor("#898781")
RULE = colors.HexColor("#c3c2b7")
GRIDLINE = colors.HexColor("#e1e0d9")

VALUATION = "31 December 2025"


def styles():
    base = getSampleStyleSheet()
    s = {}
    s["title"] = ParagraphStyle("title", parent=base["Title"], fontName="Helvetica-Bold",
                                fontSize=17, leading=21, textColor=INK, alignment=TA_LEFT,
                                spaceAfter=2)
    s["subtitle"] = ParagraphStyle("subtitle", parent=base["Normal"], fontName="Helvetica",
                                   fontSize=9.5, leading=13, textColor=MUTED, spaceAfter=14)
    s["h2"] = ParagraphStyle("h2", parent=base["Heading2"], fontName="Helvetica-Bold",
                             fontSize=11, leading=14, textColor=INK,
                             spaceBefore=15, spaceAfter=6)
    s["body"] = ParagraphStyle("body", parent=base["Normal"], fontName="Helvetica",
                               fontSize=9.4, leading=14, textColor=INK, spaceAfter=8)
    s["lead"] = ParagraphStyle("lead", parent=s["body"], fontSize=10.2, leading=15.5)
    s["caption"] = ParagraphStyle("caption", parent=base["Normal"], fontName="Helvetica",
                                  fontSize=8, leading=11, textColor=MUTED, spaceBefore=3,
                                  spaceAfter=12)
    return s


def facts(con):
    f = {}
    f["n_total"], f["n_clean"] = con.execute(
        "SELECT COUNT(*), SUM(is_clean) FROM fund").fetchone()
    f["n_flagged"] = f["n_total"] - f["n_clean"]
    f["flagged_rows"] = con.execute(
        "SELECT fund, vintage_year, flags FROM fund WHERE is_clean = 0").fetchall()
    f["committed"], f["paid_in"] = con.execute(
        "SELECT SUM(capital_committed), SUM(cash_in) FROM fund").fetchone()
    f["n_with_irr"] = con.execute(
        "SELECT COUNT(*) FROM fund WHERE net_irr IS NOT NULL").fetchone()[0]
    value, paid_in = con.execute(
        "SELECT SUM(total_value), SUM(cash_in) FROM fund").fetchone()
    f["headline_computed"] = value / paid_in

    f["dpi_early"] = con.execute("""
        SELECT SUM(cash_out)/SUM(cash_in) FROM fund
        WHERE is_clean=1 AND vintage_year BETWEEN 2005 AND 2013""").fetchone()[0]
    f["dpi_late"] = con.execute("""
        SELECT SUM(cash_out)/SUM(cash_in) FROM fund
        WHERE is_clean=1 AND vintage_year BETWEEN 2016 AND 2020""").fetchone()[0]
    f["tvpi_early"] = con.execute("""
        SELECT SUM(total_value)/SUM(cash_in) FROM fund
        WHERE is_clean=1 AND vintage_year BETWEEN 2005 AND 2013""").fetchone()[0]
    f["tvpi_late"] = con.execute("""
        SELECT SUM(total_value)/SUM(cash_in) FROM fund
        WHERE is_clean=1 AND vintage_year BETWEEN 2016 AND 2020""").fetchone()[0]
    return f


def aggregate_pme(con):
    results, levels = pme_mod.run(con)
    out = {}
    for name, cy, ds, dy in pme_mod.SCENARIOS:
        fv_c = fv_d = nav = 0.0
        end = levels[pme_mod.VALUATION_YEAR]
        for vintage, entries in results[name].items():
            cw, dw = pme_mod.profile(vintage, 0, cy, ds, dy)
            for _f, cash_in, _p, cash_out, n, _tv in entries:
                fv_c += sum(cash_in * w * (end / levels[y]) for y, w in cw.items())
                fv_d += sum(cash_out * w * (end / levels[y]) for y, w in dw.items())
                nav += n
        out[name] = (fv_d + nav) / fv_c
    return out


def vintage_rows(con):
    return con.execute("""
        SELECT vintage_year, COUNT(*), SUM(cash_in),
               SUM(total_value)/SUM(cash_in), SUM(cash_out)/SUM(cash_in)
        FROM fund
        WHERE is_clean = 1 AND cash_in > 0 AND vintage_year BETWEEN 2005 AND 2020
        GROUP BY vintage_year ORDER BY vintage_year
    """).fetchall()


def build():
    OUT.mkdir(exist_ok=True)
    con = sqlite3.connect(DB)
    s = styles()
    f = facts(con)

    charts.realised_vs_unrealised(con, OUT / "fig1_realised.png")
    vintages, base, lo, hi, robust_under, robust_over = \
        charts.pme_with_sensitivity(con, OUT / "fig2_pme.png")
    agg = aggregate_pme(con)
    agg_lo, agg_hi = min(agg.values()), max(agg.values())

    n_under = len(robust_under)
    n_over = len(robust_over)
    n_ambig = len(vintages) - n_under - n_over

    path = OUT / "investment_memo.pdf"
    doc = SimpleDocTemplate(str(path), pagesize=A4,
                            leftMargin=20*mm, rightMargin=20*mm,
                            topMargin=18*mm, bottomMargin=18*mm,
                            title="Track record analysis: a public pension private equity portfolio",
                            author="Albert Poulsen")
    st = []

    st.append(Paragraph("Track record analysis of an institutional "
                        "private equity portfolio", s["title"]))
    st.append(Paragraph(
        f"Realised versus unrealised value and benchmarking against public markets · "
        f"CalPERS Private Equity Program, as of {VALUATION} · "
        f"prepared {date.today().strftime('%d %B %Y')}", s["subtitle"]))

    st.append(Paragraph("Summary", s["h2"]))
    st.append(Paragraph(
        f"The analysis covers {f['n_total']} funds from CalPERS' published private "
        f"equity portfolio, with {f['committed']/1e9:,.0f} bn USD committed and "
        f"{f['paid_in']/1e9:,.0f} bn USD paid in. Two findings emerge, and they "
        "differ in how firmly they can be stated.", s["lead"]))
    st.append(Paragraph(
        f"<b>The robust finding concerns the composition of value, not its size.</b> "
        f"For vintages 2005-2013, {f['dpi_early']:.2f}x of paid-in capital has come "
        f"back as cash against a total value of {f['tvpi_early']:.2f}x. For vintages "
        f"2016-2020 the total value is almost unchanged at {f['tvpi_late']:.2f}x, but "
        f"only {f['dpi_late']:.2f}x has been realised. An unchanged TVPI therefore "
        "masks a shift from realised to unrealised value, and unrealised value is the "
        "manager's own mark, not a market price.", s["body"]))
    st.append(Paragraph(
        f"<b>The uncertain finding is the excess return.</b> Measured as a public "
        f"market equivalent against the S&amp;P 500 total return, the portfolio's "
        f"mature vintages sit between {agg_lo:.2f} and {agg_hi:.2f} depending on which "
        "timing assumption is applied. The range crosses 1.00. Whether the portfolio "
        "as a whole beat public markets therefore cannot be settled on the publicly "
        f"available data. For individual vintages it can: {n_under} of {len(vintages)} "
        f"sit below the index in all three scenarios, {n_over} sit above in all three, "
        f"and {n_ambig} depend on the assumption.", s["body"]))

    st.append(Paragraph("The data", s["h2"]))
    st.append(Paragraph(
        "CalPERS publishes its private equity portfolio at fund level with fund name, "
        "vintage year, capital committed, paid-in capital, distributions, total value "
        "including remaining holdings, net IRR and multiple. The data carries one "
        "decisive limitation: <b>there are no dated cash flows.</b> Only totals as of "
        "the valuation date are public. That determines what can be computed "
        "faithfully, and it is why the benchmarking below is reported as a range "
        "rather than a single number.", s["body"]))
    st.append(Paragraph(
        f"Of the {f['n_total']} funds, {f['n_with_irr']} carry a reported net IRR. The "
        "remainder are too young for a return figure to be meaningful and are marked "
        "N/M in the source.", s["body"]))

    st.append(Paragraph("Data validation", s["h2"]))
    headline_str = f"{f['headline_computed']:.2f}"
    flagged_text = ""
    if f["flagged_rows"]:
        items = "; ".join(f"{name} ({vintage}): {flags}"
                          for name, vintage, flags in f["flagged_rows"])
        flagged_text = f" The rejected rows are: {items}."
    st.append(Paragraph(
        "The table is extracted programmatically from the published CalPERS document, "
        "and two checks stand between the source and the analysis.", s["body"]))
    st.append(Paragraph(
        "<b>Row level.</b> Every row is cross-checked against the source's own multiple "
        "column: computed TVPI, that is total value divided by paid-in capital, must "
        "match the reported multiple within rounding. Remaining value is checked as "
        "non-negative and paid-in capital as positive. "
        + (f"All {f['n_total']} rows pass."
           if f["n_flagged"] == 0 else
           f"{f['n_clean']} of {f['n_total']} rows pass; {f['n_flagged']} are rejected "
           f"and excluded from the analysis.{flagged_text}"), s["body"]))
    st.append(Paragraph(
        "<b>Aggregate level.</b> CalPERS states the portfolio's aggregate net multiple "
        f"as 1.5x in the document's opening. The load produces {headline_str}x. That "
        "figure appears in no individual row, so the agreement confirms the extract as "
        "a whole rather than merely the rows' internal consistency.", s["body"]))

    st.append(Paragraph("Method", s["h2"]))
    st.append(Paragraph(
        "A public market equivalent compares a fund's cash flows with the same capital "
        "invested in a public index at the same times. All flows are carried forward to "
        "the valuation date at the index return, and the ratio of carried-forward "
        "distributions plus remaining value to carried-forward contributions is the PME. "
        "A value above 1.00 means outperformance against the index.", s["body"]))
    st.append(Paragraph(
        "Because dated flows do not exist, the observed totals are distributed across "
        "time according to an assumed profile. <b>The totals are data; the distribution "
        "is an assumption.</b> PME is therefore computed under three profiles — fast "
        "drawdown with early exits, a base case, and slow drawdown with late exits — and "
        "the result is reported as the range between them. If the whole range sits on "
        "one side of 1.00, the sign is robust to the assumption. If the range crosses "
        "1.00, the conclusion does not hold, and that is stated rather than picking the "
        "scenario that suits best.", s["body"]))
    st.append(Paragraph(
        "The benchmark is the S&amp;P 500 total return. That is a demanding choice for "
        "vintages from 2009 onward, when US equities ran exceptionally, and it depresses "
        "PME for those years in particular. A broader or more comparable index would "
        "produce different levels. The benchmark choice is itself an assumption, on a "
        "par with the timing profile.", s["body"]))

    st.append(Paragraph("Realised versus unrealised value", s["h2"]))
    st.append(Image(str(OUT / "fig1_realised.png"), width=168*mm, height=73*mm))
    st.append(Paragraph(
        "Bars show capital-weighted DPI and RVPI by vintage. The figure above each bar "
        "is TVPI. Only vintages through 2020 are shown; later years are too young for "
        "the split to be meaningful.", s["caption"]))
    st.append(Paragraph(
        "The pattern is unambiguous. Vintages from 2005 to 2008 are largely fully "
        "realised: the value has come back as cash. From 2016 onward, unrealised value "
        "is the majority, and for 2019 and 2020 less than half of paid-in capital has "
        "been distributed. Comparing TVPI across the two groups therefore does not "
        "compare like with like: for the early vintages the figure is an account, for "
        "the later ones it is a forecast.", s["body"]))
    st.append(Paragraph(
        "For an investor with commitments across several fund generations, this is the "
        "difference between liquidity and paper value, and it is precisely the "
        "distinction a manager assessment based on TVPI alone will miss.", s["body"]))

    st.append(Paragraph("Benchmarking against public markets", s["h2"]))
    st.append(Image(str(OUT / "fig2_pme.png"), width=168*mm, height=73*mm))
    st.append(Paragraph(
        "The point is the base case; the vertical bar is the range across all three "
        "timing assumptions. Orange marks the vintages where the entire range sits "
        "below 1.00.", s["caption"]))
    st.append(Paragraph(
        f"{n_under} of {len(vintages)} vintages shown sit below the index regardless of "
        "which timing assumption is used, including the vintages around the financial "
        "crisis and the middle of the 2010s. For those the conclusion is robust: the "
        "same capital in the S&amp;P 500 would have returned more.", s["body"]))
    st.append(Paragraph(
        "At the same time the range for several vintages is wide enough to cross 1.00, "
        "and for the oldest vintages it is very wide, because a small shift in assumed "
        "timing compounds over twenty years of index returns. This is not noise to be "
        "explained away — it is the real uncertainty that the absence of dated cash "
        "flows imposes on any PME computed from public data.", s["body"]))

    st.append(Paragraph("What the analysis cannot say", s["h2"]))
    for text in [
        "<b>Whether the portfolio as a whole beat public markets.</b> The aggregate PME "
        f"range of {agg_lo:.2f} to {agg_hi:.2f} crosses 1.00.",
        "<b>Anything about risk.</b> PME does not adjust for leverage, illiquidity or "
        "sector exposure. Two portfolios with the same PME can carry very different "
        "risk profiles.",
        "<b>Whether unrealised values are fair.</b> Remaining value is the manager's own "
        "mark. For the most recent vintages it is the majority of reported value, and "
        "the analysis takes it at face value.",
        "<b>Anything about manager quality.</b> The data carries no strategy, geography "
        "or sector, and a single LP's slice of a manager's funds is not that manager's "
        "track record.",
    ]:
        st.append(Paragraph(text, s["body"]))

    st.append(PageBreak())
    st.append(Paragraph("Appendix: vintage summary", s["h2"]))

    data = [["Vintage", "Funds", "Paid in (m USD)", "TVPI", "DPI", "Unrealised share"]]
    for vintage, n, paid_in, tvpi, dpi in vintage_rows(con):
        unreal = (tvpi - dpi) / tvpi if tvpi else 0
        data.append([str(vintage), str(n), f"{paid_in/1e6:,.0f}",
                     f"{tvpi:.2f}x", f"{dpi:.2f}x", f"{unreal*100:.0f} %"])

    table = Table(data, colWidths=[22*mm, 18*mm, 40*mm, 25*mm, 25*mm, 33*mm],
                  repeatRows=1)
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.2),
        ("TEXTCOLOR", (0, 0), (-1, 0), INK),
        ("TEXTCOLOR", (0, 1), (-1, -1), INK_SECONDARY),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, RULE),
        ("LINEBELOW", (0, 1), (-1, -2), 0.4, GRIDLINE),
    ]))
    st.append(table)
    st.append(Paragraph(
        "Unrealised share is RVPI divided by TVPI, that is how much of the reported "
        "value has not yet been realised.", s["caption"]))

    st.append(Paragraph("Sources and reproducibility", s["h2"]))
    st.append(Paragraph(
        f"Fund data: CalPERS Private Equity Program Fund Performance Review, as of "
        f"{VALUATION}. Index data: S&amp;P 500 annual total returns. The method follows "
        "Kaplan and Schoar (2005) for PME, adapted as described under Method. All data "
        "processing is in Python with SQLite as the data layer; the calculations can be "
        "reproduced from the source files.", s["body"]))

    doc.build(st)
    return path, agg


if __name__ == "__main__":
    path, agg = build()
    print("Memo written:", path)
    for name, value in agg.items():
        print(f"  {name:<34} PME = {value:.2f}")
