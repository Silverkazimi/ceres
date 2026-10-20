"""  readiness never asks the game for additional history."""
from pathlib import Path

READY = Path(__file__).parents[2] / 'ceres' / 'static' / 'ready-feed.js'


def test_ready_feed_has_no_network_or_history_lookup():
    source = READY.read_text().lower()
    for forbidden in ('fetch(', 'xmlhttprequest', 'trx_lookup', 'history', 'blockchain'):
        assert forbidden not in source
