"""Splinter-name mapping (`ceres.board._splinter_name`).

The board previously surfaced card_details' raw `color` field verbatim (Gray/Blue/
Green/Black/Red/White/Gold) where the UI needs actual Splinterlands splinter names.
Mapping verified 2026-08-20 against seven known summoners in
~/.praetoria/cache/card_details.json -- see the comment on `_SPLINTER_BY_COLOR` in
ceres/board.py for the source cards. These seven are the only colour values present
across all 1,100 rows in that file.
"""

from __future__ import annotations

import pytest

from ceres.board import _splinter_name

KNOWN_MAPPINGS = [
    ("Red", "Fire"),
    ("Blue", "Water"),
    ("Green", "Earth"),
    ("White", "Life"),
    ("Black", "Death"),
    ("Gold", "Dragon"),
    ("Gray", "Neutral"),
]


@pytest.mark.parametrize("color, splinter", KNOWN_MAPPINGS)
def test_known_colors_map_to_splinter_names(color, splinter):
    assert _splinter_name(color) == splinter


def test_missing_color_passes_through_as_none():
    assert _splinter_name(None) is None


def test_unknown_color_is_visible_not_mapped():
    """An unrecognised colour must render as itself (visibly unknown), never get
    silently coerced to one of the seven known splinter names."""
    assert _splinter_name("Purple") == "Purple"
