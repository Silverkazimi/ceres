""": a plot row with a pending change (the pulsing clock) pulses a faint gold across the whole row."""
from pathlib import Path

STATIC = Path(__file__).parents[2] / "ceres" / "static"


def test_pending_rows_get_the_class():
    rows = (STATIC / "rows.js").read_text(encoding="utf-8")
    assert "badgeStripStatus(row).kind === 'pending' ? ' row-pending'" in rows


def test_the_glow_is_an_inner_edge_glow_never_a_background_change():
    """ (maintainer): "You change the background color. Dont do that. Use a strong and visible glow inside the row at the edges."""
    css = (STATIC / "ceres.css").read_text(encoding="utf-8")
    start = css.index("table.plot-table tbody tr.plot-row.row-pending > td {")
    block = css[start:css.index(".badge-strip.is-flagged {", start)]
    assert "animation: row-pending-glow 1.3s ease-in-out infinite;" in block
    keyframes = block[block.index("@keyframes row-pending-glow"):block.index("@media (prefers-reduced-motion: reduce)")]
    assert keyframes.count("inset") >= 8 and "var(--gold)" in keyframes           # edge glow in the gold token, both phases
    assert "#" not in keyframes and "rgb(" not in keyframes and "rgba(" not in keyframes
    assert "100vmax" not in block                                                  # the old full-cell tint is gone
    for property_ in ("background:", "background-color:", "background-image:", "filter:"):
        assert property_ not in block, property_                                    # the row's own colours are never touched


def test_the_glow_has_a_reduced_motion_answer():
    css = (STATIC / "ceres.css").read_text(encoding="utf-8")
    reduced = css[css.index("@media (prefers-reduced-motion: reduce)", css.index("@keyframes row-pending-glow")):][:700]
    assert "row-pending" in reduced and "animation: none" in reduced and "box-shadow" in reduced


def test_the_glow_shares_the_clock_pulse_timeline():
    sync = (STATIC / "pulse-sync.js").read_text(encoding="utf-8")
    assert "'badge-clock-pulse', 'row-pending-glow'" in sync


def test_the_row_class_follows_the_clock_on_every_sync():
    """ set the class only when a row was built, so a change staged afterwards pulsed the clock but not the row."""
    overlay = (STATIC / "badge-overlay.js").read_text(encoding="utf-8")
    assert "tr.classList.toggle('row-pending', barRow.dataset.pending === 'true');" in overlay
    assert "barRow.dataset.pending = row && badgeStripStatus(row).kind === 'pending' ? 'true' : 'false';" in overlay
