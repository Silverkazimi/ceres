import unittest

from praetoria.land_reallocate import build_plots
from tests.test_land_reallocate import build_report


class LandReallocateMalformedRowTests(unittest.TestCase):
    def test_missing_facts_names_plot_and_key(self):
        report = build_report(include_asset_ids=False)
        row = report["plots"][0]
        plot_id = row["deed_uid"]
        del row["facts"]

        with self.assertRaisesRegex(ValueError, rf"{plot_id}.*facts"):
            build_plots(report)

    def test_missing_staking_facts_names_plot_and_key(self):
        report = build_report(include_asset_ids=False)
        row = report["plots"][0]
        plot_id = row["deed_uid"]
        del row["staking_facts"]

        with self.assertRaisesRegex(ValueError, rf"{plot_id}.*staking_facts"):
            build_plots(report)

    def test_well_formed_report_builds(self):
        report = build_report(include_asset_ids=False)

        self.assertTrue(build_plots(report))
