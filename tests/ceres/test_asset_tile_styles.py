"""Mutation: changing resting crest opacity to 0.85 must make this guard fail."""

from pathlib import Path


CSS = Path(__file__).parents[2] / "ceres" / "static" / "ceres.css"


def test_asset_tiles_keep_resting_icons_opaque_and_states_distinct() -> None:
    source = CSS.read_text(encoding="utf-8")
    assert ".slot-crest {" in source and "opacity: 1;" in source[source.index(".slot-crest {"):source.index(".slot-flag {")]
    assert ".slot-tile.is-pending-removal .slot-art img { opacity: 0.45; filter: grayscale(60%); }" in source
    assert ".slot-restore-label" not in source
    assert ".slot-tile.is-empty {" in source and "background: color-mix(" in source[source.index(".slot-tile.is-empty {"):source.index(".slot-tile.is-empty .plus")]
    assert ".slot-tile.is-empty.is-unavailable { cursor: not-allowed; opacity: 0.55; }" in source
    assert ".slot-tile-item.has-slot-art { background: color-mix(in srgb, var(--bg3) 66%, transparent); }" in source
    # (v0.9.512): caption background removed -- transparent over the shared art gradient.
    assert ".slot-caption {" in source and "background: none;" in source[source.index(".slot-caption {"):source.index(".slot-tile.has-slot-art .slot-name")]
    assert ".power-satellite.is-empty { background: color-mix(in srgb, var(--bg3) 66%, transparent); }" in source


def test_power_satellite_offset_is_derived_from_its_size() -> None:
    source = CSS.read_text(encoding="utf-8")
    assert "--power-satellite-size: 2.3rem" in source
    assert "top: calc(var(--power-satellite-size) / -2)" in source
