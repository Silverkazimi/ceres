import os
import unittest

from praetoria.land import DEFAULT_PLAYER, LandClient


@unittest.skipUnless(os.environ.get("PRAETORIA_LIVE") == "1", "opt-in live smoke: set PRAETORIA_LIVE=1")
class LiveLandSmokeTests(unittest.TestCase):
    def test_live_deed_bundle_is_read_only_shape_check(self):
        response = LandClient().bundle("player0005")  # explicit account; DEFAULT_PLAYER is None since
        self.assertIn("data", response.data)
        self.assertIsInstance(response.data["data"].get("deeds"), list)


if __name__ == "__main__":
    unittest.main()
