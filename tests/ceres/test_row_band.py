""" / : plot card-top room for the land-ability icons.

 gave plots with a single row of abilities extra card-top room because the row sat ABOVE the
tile.  moved that row (and the Toil & Kin / Companion pill) INSIDE the tile, bottom-anchored in
`.slot-foot`, so the row needs no room above the card any more; only the Energized / six-ability arcs,
which still sit around the tile's top edge, keep theirs."""
from pathlib import Path

CSS = Path(__file__).resolve().parents[2] / "ceres" / "static" / "ceres.css"
ROW_ROOM = ".editor-band:has(.slot-ability-row) { --eos-card-top"
ARC = "html:not(.ceres-lw-le-1279) .slot-editor > .editor-band:has(.slot-ability-arc) { --eos-card-top: max(calc(40 * var(--plot-scene-unit)), calc(var(--eos-slot-w) * 55 / 107.2)); }"
FOOT = ".slot-foot { position: absolute; left: 0; right: 0; bottom: calc(52px + 3px);"
BAND_FOOT = ".editor-band .slot-foot { bottom: calc(48.5cqi + 3 * var(--ability-unit));"
ROW_STATIC = ".slot-ability-row { position: static;"


def test_ability_row_sits_inside_the_tile_bottom() -> None:
    css = CSS.read_text(encoding="utf-8")
    assert css.count(FOOT) == 1
    assert css.count(BAND_FOOT) == 1
    assert css.count(ROW_STATIC) == 1
    assert "bottom: calc(100% + 4" not in css  # the old above-the-tile placement is gone
    assert ROW_ROOM not in css


def test_arc_plots_keep_their_card_top_room() -> None:
    css = CSS.read_text(encoding="utf-8")
    assert css.count(ARC) == 1
