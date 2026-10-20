"""in the setup list and the routing window list the parts of a route sit in fixed columns (resource, arrow, destination icon +
label, percentage or state), so the DEC icon, the DEC text and the trailing text line up row to row. Measured in a real browser for the
release (spread 0.0 px for all three in both lists; it was 11.5 px); these guards pin the structure and the styles."""
from pathlib import Path

STATIC = Path(__file__).parents[2] / "ceres" / "static"


def test_both_lists_use_the_same_column_parts():
    shared = (STATIC / "routing-route-list.js").read_text(encoding="utf-8")   # built once, used by both screens
    assert 'class="r3-from"' in shared and 'class="r3-route-arrow"' in shared and 'class="r3-to"' in shared
    assert 'r3-route-name r3-route-name--icon' in shared
    for name in ("routing-window.js", "routing-setup.js"):
        assert "routeNameHtml(" in (STATIC / name).read_text(encoding="utf-8"), name


def test_columns_are_fixed_and_the_arrow_does_not_reuse_the_big_arrow_style():
    css = (STATIC / "ceres.css").read_text(encoding="utf-8")
    block = css[css.index("*"):]
    # the list is one grid and every chip a subgrid, so each column is as wide as its widest entry (no fixed widths)
    assert "grid-template-columns: subgrid" in block and "grid-template-columns: auto auto auto auto auto minmax(0, 1fr)" in block and "3.6em" not in block
    assert ".r3-route-arrow" in block and 'class="r3-arrow"' not in (STATIC / "routing-route-list.js").read_text(encoding="utf-8")
