""" dividers never render as a bar or at a line edge (source guards)."""
from pathlib import Path

STATIC = Path(__file__).parents[2] / "ceres" / "static"


def test_picker_pipe_has_explicit_width_and_is_hidden_in_the_mobile_sheet():
    css = (STATIC / "ceres.css").read_text()
    assert ".picker-bar-end .picker-bar-sep { flex: 0 0 1px; inline-size: 1px;" in css
    assert "html.ceres-lw-le-1919.ceres-lw-le-1279 .mobile-plot-sheet .picker-bar-sep { display: none; }" in css


def test_filter_row_dividers_are_made_invisible_at_line_edges_not_removed():
    # display:none dropped the divider's width, so a neighbour reflowed onto its line and
    # re-triggered the wrap (bistable). visibility:hidden keeps the width reserved instead.
    css = (STATIC / "ceres.css").read_text()
    js = (STATIC / "app.js").read_text()
    assert ".controls-row .controls-sep.is-line-edge { visibility: hidden; }" in css
    assert ".controls-row .controls-sep.is-line-edge { display: none; }" not in css
    assert "const syncControlsDividers = () => {" in js
    assert "window.addEventListener('resize', syncControlsDividers);" in js


def test_sync_controls_dividers_uses_the_exported_pure_comparison():
    js = (STATIC / "app.js").read_text()
    assert "export function dividerIsLineEdge(prevMid, nextMid) {" in js
    assert "return Math.abs(prevMid - nextMid) > 6;" in js
    assert "sep.classList.toggle('is-line-edge', dividerIsLineEdge(mid(prev), mid(next)));" in js


def test_divider_is_line_edge_pure_comparison_matches_the_6px_threshold():
    """Executes ONLY the exported pure function (extracted by source slice), never the whole
 module -- app.js runs a `ready(fn)` DOMContentLoaded bootstrap at EOF that needs `document`/
 `window`, which a plain Node process does not have; importing the whole file would throw."""
    import re
    import subprocess

    js = (STATIC / "app.js").read_text()
    match = re.search(r"export function dividerIsLineEdge\(prevMid, nextMid\) \{\n return Math\.abs\(prevMid - nextMid\) > 6;\n\}", js)
    assert match, "dividerIsLineEdge source shape changed -- update this test's slice"
    script = match.group(0).replace("export function", "function") + (
        "\nconsole.log(JSON.stringify([dividerIsLineEdge(10, 10), dividerIsLineEdge(10, 16), dividerIsLineEdge(10, 17)]));"
    )
    result = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    import json
    assert json.loads(result.stdout.strip()) == [False, False, True]
