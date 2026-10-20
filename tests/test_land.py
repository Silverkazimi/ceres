import unittest

from praetoria.land import normalize_plots, rank_plots


class LandTests(unittest.TestCase):
    def test_normalizes_and_ranks_total_harvest_pp(self):
        payload = {
            "status": "success",
            "data": {
                "deeds": [
                    {"deed_uid": "a", "region_number": 41, "tract_number": 2, "plot_number": 27, "resource_symbol": "IRON", "rarity": "legendary", "plot_status": "natural", "worksite_type": "Ore Mine"},
                    {"deed_uid": "b", "region_number": 65, "tract_number": 9, "plot_number": 83, "resource_symbol": "GRAIN", "rarity": "epic", "plot_status": "natural", "worksite_type": "Grain Farm"},
                ],
                "staking_details": [
                    {"deed_uid": "a", "region_uid": "PR-BRL-41", "total_harvest_pp": "546350.001"},
                    {"deed_uid": "b", "region_uid": "PR-CEF-65", "total_harvest_pp": "505612.5"},
                ],
            },
        }
        ranked = rank_plots(normalize_plots(payload), 1)
        self.assertEqual(ranked[0].deed_uid, "a")
        self.assertEqual(ranked[0].plot, "41-2-27")
        self.assertEqual(ranked[0].pp, 546350.001)


if __name__ == "__main__":
    unittest.main()

