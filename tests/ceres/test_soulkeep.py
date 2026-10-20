""" / : Soulkeep (edition 9) art folder, and Soulkeep is never shown as soulbound."""
import re
from pathlib import Path

from ceres import card_art_cache, card_browser

ROOT = Path(__file__).resolve().parents[2]


def test_soulkeep_art_folder_is_cards_nightmare_everywhere():
    # cards_v2.2 answers 403 for edition 9
    js = (ROOT / "ceres/static/card-browser.js").read_text(encoding="utf-8")
    icons = (ROOT / "ceres/static/icons.js").read_text(encoding="utf-8")
    assert "9: 'cards_nightmare'" in js
    assert re.search(r"CARD_CDN_SET\[9\] = 'cards_nightmare';", icons)
    assert '9: "cards_nightmare"' in (ROOT / "ceres/card_art_cache.py").read_text(encoding="utf-8")


def test_soulkeep_is_not_soulbound():
    src = (ROOT / "ceres/card_browser.py").read_text(encoding="utf-8")
    assert "soulbound = _is_soulbound(facts)" in src
    assert "in SOULKEEP_EDITIONS or _is_soulbound" not in src
    assert card_browser._is_soulbound({"edition": 9, "bcx": 1, "bcx_unbound": 0}) is False
    assert card_browser._is_soulbound({"edition": 11, "bcx": 1, "bcx_unbound": 0}) is False
