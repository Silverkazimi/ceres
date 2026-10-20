""": the worker group's top-left button row gets this plot's terrain-affinity
splinter icons -- the exact same markup/classes/renderer the worker picker's filter bar
already uses (terrainAffinityHtml(row, 'picker')) -- immediately before the Fill plot
button, with a divider, vertically centred on the button row, same icon sizes as the
picker's own. Placement clarified: "Not the filter row.
Iside he plot before the buttons in the top left corner, before 'Fill the plot' button."
"""

from contextlib import closing
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parents[2]
STATIC = ROOT / "ceres" / "static"
EDITOR = (ROOT / "ceres/static/editor.js").read_text(encoding="utf-8")
CANDIDATES = (ROOT / "ceres/static/candidates.js").read_text(encoding="utf-8")
CSS = (ROOT / "ceres/static/ceres.css").read_text(encoding="utf-8")


# Source guards

def test_splinter_row_uses_the_shared_picker_renderer() -> None:
    """The exact same call the worker picker's filter bar makes (candidates.js:816),
    not a re-implementation.

    /: the plot row itself is no
    longer clickable, so it now calls the 'editor-row' surface instead of 'picker'
    -- terrainAffinityHtml still shares the same badge renderer/classes/sizing,
    just non-interactive for this surface. The picker's own filter bar is
    untouched (still 'picker')."""
    assert "terrainAffinityHtml(row, 'picker', filter.splinter)" in CANDIDATES
    assert "terrainAffinityHtml(row, 'editor-row')" in EDITOR
    assert "terrainAffinityHtml(row, 'picker')" not in EDITOR


def test_editor_row_surface_is_non_interactive() -> None:
    """/: terrainAffinityHtml only renders real <button> filter chips
    (data-picker-action, aria-pressed) for surface === 'picker'; every other
    surface, including the new 'editor-row', renders plain <span role="img">
    icons with no button semantics -- and still gets the picker's scroll shell
    (terrain-affinity-scroll/-viewport) so sizing/alignment/shadow rules that key
    off those classes keep working unchanged."""
    TERRAIN_AFFINITY = (ROOT / "ceres/static/terrainAffinity.js").read_text(encoding="utf-8")
    assert "const interactive = surface === 'picker';" in TERRAIN_AFFINITY
    assert "if (surface !== 'picker' && surface !== 'editor-row') return run;" in TERRAIN_AFFINITY


def test_splinter_row_sits_before_fill_plot_with_a_divider() -> None:
    """Markup order inside the controls div: (optional upgrade status), the splinter
    row, its divider, THEN the worker-group buttons string (whose first element is
    the Fill plot button, action='fill_plot' -- see EDITOR's own `actions` array)."""
    marker = "const splinterAffinityHtml = group.id === 'worker'"
    assert marker in EDITOR
    block = EDITOR[EDITOR.index(marker):]
    aff_pos = block.index('class="editor-splinter-affinity"')
    sep_pos = block.index('class="editor-splinter-affinity-sep"')
    assert aff_pos < sep_pos, "the affinity row must render before its divider"
    controls_line = "${upgradeStatus}${splinterAffinityHtml}${buttons}${controlsTrailingHtml}"
    assert controls_line in EDITOR, "splinterAffinityHtml must sit directly before buttons (which opens with Fill plot for the worker group)"
    # Fill plot is the FIRST action for the worker group, so `buttons` opens with it.
    assert "const actions = group.id === 'worker' ? ['fill_plot', 'clear', 'clear_plot']" in EDITOR


def test_splinter_row_is_worker_group_only_and_desktop_only() -> None:
    # Fill plot only exists on the worker group -- the row must not render for
    # totem/title/power_core/runi groups, which have no Fill plot button to sit before.
    assert "group.id === 'worker'\n    ? `<span class=\"editor-splinter-affinity\">" in EDITOR
    # The mobile plot sheet (rows.js) never calls renderEditorGroup -- it builds its own
    # markup (workerSubtable) with no Fill plot button, so this is desktop-only by
    # construction; there is nothing to gate explicitly in that file.
    rows_js = (ROOT / "ceres/static/rows.js").read_text(encoding="utf-8")
    assert "fill_plot" not in rows_js
    assert "renderEditorGroup" not in rows_js


def test_divider_mirrors_the_pickers_own_bar_separator_proportions() -> None:
    # the divider now spans the button height (--ceres-control-min-block) instead
    # of a fixed 1.4rem, since the badge row itself now matches that button height.
    assert ".picker-bar-end .picker-bar-sep { flex: 0 0 1px; inline-size: 1px; align-self: stretch; min-block-size: 1.4rem; background: var(--border-bright); }" in CSS
    assert ".slot-group-controls > .editor-splinter-affinity-sep { flex: 0 0 1px; inline-size: 1px; align-self: start; min-block-size: var(--ceres-control-min-block, 1.875rem); background: var(--border-bright); pointer-events: none; }" in CSS


def test_affinity_row_is_vertically_centred_and_receives_pointer_events() -> None:
    # `.slot-group-controls` itself is `pointer-events: none` ( sticky-row
    # pattern) -- every real child must re-enable it, same as `.editor-change-badges`.
    # the row start-aligns with the buttons ('s own technique) and carries
    # an explicit min-block-size equal to the button token, so two equal-height, same-start
    # items share a vertical centre by construction -- measured centre delta: 0px at 1920.
    assert ".slot-group-controls > .editor-splinter-affinity { display: inline-flex; pointer-events: auto; align-self: start; min-block-size: var(--ceres-control-min-block, 1.875rem); }" in CSS
    assert ".slot-group-controls { display: flex; flex-wrap: wrap; gap: 0.35rem; margin-bottom: 0.35rem; position: sticky; left: 0; z-index: 5; background: transparent; border-radius: 0; pointer-events: none; }" in CSS


def test_no_filter_predicate_or_persistence_was_added() -> None:
    """'s maintainer clarification: this is the plot's own terrain-affinity display,
    NOT a board filter -- no new filter key, no board.js change, no localStorage key."""
    board_js = (ROOT / "ceres/static/board.js").read_text(encoding="utf-8")
    assert "editor-splinter-affinity" not in board_js
    assert "splinter-row" not in board_js.lower()


def test_touched_js_parses() -> None:
    import subprocess
    for rel in ("ceres/static/editor.js", "ceres/static/candidates.js"):
        result = subprocess.run(["node", "--check", str(ROOT / rel)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


def test_editor_badges_match_the_button_height() -> None:
    """: `.editor-splinter-affinity` sits inside `.editor-band`, whose
    own fluid `--eos-affinity-size` rule () would otherwise resize these badges away
    from the target size -- see ceres.css's `:is(.editor-band, .mobile-plot-affinity)
    .terrain-affinity-badge` block. These narrower, higher-specificity rules instead size
    the badges to `--ceres-control-min-block`, the same token `.slot-group-controls`'s own
    buttons use, so the badge box is always exactly the button height."""
    assert ".editor-band .editor-splinter-affinity .terrain-affinity-badge { padding: 0; border: 0; font: inherit; appearance: none; background: var(--bg); --eos-splinter-row-size: var(--ceres-control-min-block, 1.875rem); flex-basis: var(--eos-splinter-row-size); inline-size: var(--eos-splinter-row-size); block-size: var(--eos-splinter-row-size); }" in CSS
    assert ".editor-band .editor-splinter-affinity .terrain-affinity-badge > .splinter-icon { inline-size: 66.666667%; block-size: 66.666667%; }" in CSS


def test_editor_badges_have_no_ua_button_border() -> None:
    """: terrainAffinityHtml(row,
    'picker') renders a real <button>, and only `.picker-bar .terrain-affinity-badge` reset
    its UA chrome -- this row sits outside `.picker-bar`, so the default `2px outset` button
    border painted over the base `.terrain-affinity-badge` box-shadow. The reset above
    removes the border; the shared box-shadow rule (ceres.css's plain `.terrain-affinity-badge`)
    was never touched and needs no reset here."""
    assert "padding: 0; border: 0; font: inherit; appearance: none;" in CSS


# the row is now plain, non-interactive icons

@pytest.fixture(scope="module")
def bare_browser():
    """A plain Chromium instance with NO live Ceres server behind it -- same self-contained
    pattern as test_penalised_mark.py's `paint_browser`/`_page`: a stub route serves a
    minimal HTML shell for "/" and the project's own static files for "/static/*", so the real
    terrainAffinity.js module (and its transitive imports, which need `document`) can be
    exercised in a real browser without spawning `python3 -m ceres` or needing live game data."""
    with sync_playwright() as playwright:
        with closing(playwright.chromium.launch()) as browser:
            yield browser


def _bare_page(browser):
    page = browser.new_page()

    def route_local(route):
        path = urlparse(route.request.url).path
        if path == "/":
            route.fulfill(content_type="text/html", body="<!doctype html><html><body></body></html>")
            return
        asset = (STATIC / path.removeprefix("/static/")).resolve()
        if not path.startswith("/static/") or not asset.is_relative_to(STATIC) or not asset.is_file():
            route.fulfill(status=404, body="missing fixture asset")
            return
        route.fulfill(path=str(asset))

    page.route("**/*", route_local)
    page.goto("http://s14940.test/")
    return page


def test_editor_row_renders_plain_spans_picker_bar_renders_buttons(bare_browser) -> None:
    """/: the plot's own row is a non-clickable
    <span role="img"> with a tooltip, no data-picker-action, no aria-pressed, and no tab stop
    (dropped -- a plain icon should not be a tab stop); the worker picker's own filter bar
    (unchanged,  frozen) still renders real, tabbable <button> chips."""
    with closing(_bare_page(bare_browser)) as page:
        result = page.evaluate(
            """async () => {
 await import('/static/wallet.js'); // the app's module entry order: board.js must finish before wallet.js reads it
 const { terrainAffinityHtml } = await import('/static/terrainAffinity.js');
 const row = { terrain: 'Forest', plot: '001-001-1', biome: { fire: 10, water: -10, earth: 0 } };
 const host = document.createElement('div');
 host.innerHTML = terrainAffinityHtml(row, 'editor-row');
 const editorBadge = host.querySelector('.terrain-affinity-badge');
 const pickerHost = document.createElement('div');
 pickerHost.innerHTML = terrainAffinityHtml(row, 'picker', '');
 const pickerBadge = pickerHost.querySelector('.terrain-affinity-badge');
 return {
 editorTag: editorBadge.tagName,
 editorRole: editorBadge.getAttribute('role'),
 editorHasPickerAction: editorBadge.hasAttribute('data-picker-action'),
 editorHasAriaPressed: editorBadge.hasAttribute('aria-pressed'),
 editorHasTabindex: editorBadge.hasAttribute('tabindex'),
 editorHasTip: editorBadge.hasAttribute('data-tip'),
 pickerTag: pickerBadge.tagName,
 pickerHasPickerAction: pickerBadge.hasAttribute('data-picker-action'),
 pickerHasAriaPressed: pickerBadge.hasAttribute('aria-pressed'),
 };
 }"""
        )
    assert result["editorTag"] == "SPAN", result
    assert result["editorRole"] == "img", result
    assert result["editorHasPickerAction"] is False, result
    assert result["editorHasAriaPressed"] is False, result
    assert result["editorHasTabindex"] is False, result
    assert result["editorHasTip"] is True, result
    assert result["pickerTag"] == "BUTTON", result
    assert result["pickerHasPickerAction"] is True, result
    assert result["pickerHasAriaPressed"] is True, result
