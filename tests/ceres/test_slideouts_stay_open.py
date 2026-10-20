"""slid-out plot badges stay out until the flag or a plot's badge button is pressed again (or Escape)."""
import re
from pathlib import Path

JS = (Path(__file__).parents[2] / "ceres" / "static" / "badge-overlay.js").read_text(encoding="utf-8")


def _handler(event):
    start = JS.index(f"document.addEventListener('{event}'")
    return JS[start:JS.index("\n });", start)]


def test_a_click_elsewhere_does_not_close_the_slide_outs():
    handler = _handler("pointerdown")
    assert "closeAll" not in handler and "openRows" not in handler, handler


def test_a_click_elsewhere_still_closes_the_status_tray():
    assert "setTrayOpen(false)" in _handler("pointerdown")


def test_the_flag_the_plot_buttons_and_escape_still_close_them():
    assert re.search(r"if \(openRows\.size && tray\.dataset\.open !== 'true'\) closeAll\(false\)", JS)  # flag
    assert "setRowOpen(barRow.dataset.deedUid, !openRows.has(barRow.dataset.deedUid))" in JS             # plot badge button
    assert "closeAll(true)" in _handler("keydown")                                                         # Escape
