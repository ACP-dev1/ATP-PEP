"""
Checks on the PME implementation.

The critical one is the identity test: a fund whose cash flows exactly track
the index must produce PME = 1.00. If it does not, the carry-forward is wrong
and every other figure in the memo is worthless.
"""

import sqlite3
import unittest
from pathlib import Path

import pme as pme_mod

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "pe.db"


class TestPME(unittest.TestCase):

    def setUp(self):
        self.levels = pme_mod.load_index()
        self.end = self.levels[pme_mod.VALUATION_YEAR]

    def test_index_tracking_fund_gives_pme_one(self):
        """
        Contribute 100 in the vintage year, hold to the valuation date, distribute
        exactly the index return. PME must be 1.00.
        """
        vintage = 2010
        cash_in = 100.0
        growth = self.end / self.levels[vintage]
        cash_out = cash_in * growth

        # Profile with one contribution year and one distribution in the final year.
        pme = pme_mod.fund_pme(
            cash_in, cash_out, nav=0.0, vintage=vintage, levels=self.levels,
            contrib_years=1, dist_start=pme_mod.VALUATION_YEAR - vintage, dist_years=1,
        )
        self.assertAlmostEqual(pme, 1.0, places=9)

    def test_outperformer_above_one(self):
        vintage = 2010
        cash_in = 100.0
        cash_out = cash_in * (self.end / self.levels[vintage]) * 1.5
        pme = pme_mod.fund_pme(
            cash_in, cash_out, 0.0, vintage, self.levels,
            contrib_years=1, dist_start=pme_mod.VALUATION_YEAR - vintage, dist_years=1)
        self.assertAlmostEqual(pme, 1.5, places=9)

    def test_nav_is_not_discounted(self):
        """Remaining value is already stated as of the valuation date."""
        vintage = 2015
        pme_no_nav = pme_mod.fund_pme(100.0, 0.0, 0.0, vintage, self.levels)
        pme_with_nav = pme_mod.fund_pme(100.0, 0.0, 50.0, vintage, self.levels)
        fv_contrib = 50.0 / (pme_with_nav - pme_no_nav)
        self.assertGreater(fv_contrib, 100.0)  # contributions are carried forward, NAV is not

    def test_profile_weights_sum_to_one(self):
        for vintage in range(2000, 2026):
            for cy, ds, dy in [(3, 3, 6), (4, 4, 7), (5, 5, 8)]:
                c, d = pme_mod.profile(vintage, 0, cy, ds, dy)
                self.assertAlmostEqual(sum(c.values()), 1.0, places=9)
                self.assertAlmostEqual(sum(d.values()), 1.0, places=9)

    def test_no_flow_lands_after_valuation_date(self):
        for vintage in range(2000, 2026):
            c, d = pme_mod.profile(vintage, 0, 5, 5, 8)
            self.assertLessEqual(max(c), pme_mod.VALUATION_YEAR)
            self.assertLessEqual(max(d), pme_mod.VALUATION_YEAR)


class TestData(unittest.TestCase):

    def setUp(self):
        self.con = sqlite3.connect(DB)

    def test_clean_rows_match_reported_multiple(self):
        """Every validated row must reconcile with the source's own multiple."""
        rows = self.con.execute("""
            SELECT fund, tvpi, reported_multiple FROM fund
            WHERE is_clean = 1 AND reported_multiple IS NOT NULL
        """).fetchall()
        self.assertGreater(len(rows), 100)
        for fund, tvpi, reported in rows:
            self.assertLess(abs(tvpi - reported), 0.051, msg=fund)

    def test_dpi_never_exceeds_tvpi(self):
        bad = self.con.execute("""
            SELECT fund FROM fund WHERE is_clean = 1 AND dpi > tvpi + 1e-9
        """).fetchall()
        self.assertEqual(bad, [])

    def test_headline_multiple_reproduced(self):
        """
        Independent check on the whole load: CalPERS states the portfolio's
        aggregate net multiple in the document's opening. The aggregate must
        reproduce it. That figure appears in no individual row, so a match
        confirms the extract as a whole.
        """
        value, paid_in = self.con.execute(
            "SELECT SUM(total_value), SUM(cash_in) FROM fund").fetchone()
        self.assertAlmostEqual(value / paid_in, 1.5, delta=0.05)

    def test_flagged_rows_excluded_from_aggregates(self):
        results, _ = pme_mod.run(self.con)
        names = {e[0] for entries in results["base case"].values() for e in entries}
        flagged = {r[0] for r in self.con.execute(
            "SELECT fund FROM fund WHERE is_clean = 0")}
        self.assertEqual(names & flagged, set())


if __name__ == "__main__":
    unittest.main(verbosity=2)
