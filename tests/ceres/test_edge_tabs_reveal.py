""" (maintainer, verbatim): "the wallet slider must not be visible during the
animation at page load" -- extended by  (maintainer, verbatim): "flag button
appears, then disappears behind the loadig animation, then reappears with the
full board. Stop that flashing, it must first be visible when the board has
loaded". `#edge-tabs` (the Wallet picker's strip), `#badge-flag` and
`#badge-bar` all start `visibility: hidden; opacity: 0` in ceres.css and are
revealed TOGETHER by app.js only once the Wallet's own position, the bar's own
position, the bar's own rows (badge-overlay.js's first `syncBadgeOverlay()`
pass), and the board's own reveal (board.js::revealBoardTable()) are all true.
See library/ceres-edge-tabs.md.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ceres" / "static"
APP_JS = STATIC / "app.js"
BOARD_JS = STATIC / "board.js"
BADGE_OVERLAY_JS = STATIC / "badge-overlay.js"
CERES_CSS = STATIC / "ceres.css"


def test_edge_tabs_starts_hidden_pre_js():
    css = CERES_CSS.read_text(encoding="utf-8")
    assert ".edge-tabs {" in css
    rule = css.split(".edge-tabs {", 1)[1].split("}", 1)[0]
    assert "visibility: hidden;" in rule
    assert "opacity: 0;" in rule
    # No `transition` on the base rule -- nothing to animate FROM until the
    # reveal class is added; the fade lives entirely on `.edge-tabs-revealed`'s
    # own `animation`, not a `transition` on the base state.
    assert "transition:" not in rule


def test_badge_bar_and_flag_also_start_hidden_pre_js():
    """: joins the SAME treatment as #edge-tabs."""
    css = CERES_CSS.read_text(encoding="utf-8")
    bar_rule = next(
        (m.group(1) for m in re.finditer(r"(?:^|\n)\.badge-bar \{(.*?)\n\}\n", css, re.S) if "position: absolute;" in m.group(1)),
        None,
    )
    assert bar_rule, "standalone .badge-bar geometry rule not found"
    assert "visibility: hidden;" in bar_rule
    assert "opacity: 0;" in bar_rule
    head_rule_match = re.search(r"(?:^|\n)\.badge-bar-head \{(.*?)\n\}\n", css, re.S)
    assert head_rule_match, "standalone .badge-bar-head rule not found"
    head_rule = head_rule_match.group(1)
    assert "visibility: hidden;" in head_rule
    assert "opacity: 0;" in head_rule


def test_edge_tabs_revealed_class_fades_in_with_its_own_iris_style_keyframe():
    css = CERES_CSS.read_text(encoding="utf-8")
    assert "@keyframes edgeTabsFadeInRight {" in css
    keyframe = css.split("@keyframes edgeTabsFadeInRight {", 1)[1].split("\n}\n", 1)[0]
    # originally copied canonical.css's own `fadeInRight` (opacity +
    # translateX(20px)) under a new name -- but Iris's vendored version runs that
    # slide over 1s while this copy ran it over .6s, on two different start
    # times, so for a real stretch of the page load the Wallet/Flag were still
    # physically mid-slide while Iris had already settled at rest, landing them
    # up to ~20px right of Iris (occasionally enough to push #badge-flag's centre
    # past the viewport edge). These two elements are already JS-positioned
    # exactly where they belong before this class is ever added, so the
    # `transform` was dropped -- opacity-only, nothing left that can drift.
    assert re.search(r"\bfrom\s*\{\s*opacity: 0;\s*\}", keyframe)
    assert re.search(r"\bto\s*\{\s*opacity: 1;\s*\}", keyframe)
    assert "translateX" not in keyframe
    canonical_css = (STATIC / "canonical.css").read_text(encoding="utf-8")
    assert "@keyframes fadeInRight {" in canonical_css  # the vendored original, untouched
    assert ".edge-tabs-revealed {" in css
    revealed_rule = css.split(".edge-tabs-revealed {", 1)[1].split("}", 1)[0]
    assert "visibility: visible;" in revealed_rule
    assert "opacity: 1;" in revealed_rule
    assert "animation: edgeTabsFadeInRight" in revealed_rule
    # Joins the existing shared `.edge-tray`/`.scroll-top` reduced-motion block
    # (further down the file) rather than opening a NEW multi-line `@media {`
    # block up here -- another test (test_edge_stack.py's own
    # `.scroll-top` reduced-motion guard) finds its block by searching for the
    # FIRST multi-line `@media (prefers-reduced-motion: reduce) {` in the file
    # a second one earlier in the file would shadow it.
    assert re.search(r"(?m)^  #edge-tabs\.edge-tabs-revealed \{ animation: none; \}(?: /\*[^\n]*\*/)?$", css)


def test_badge_bar_and_flag_get_their_own_reveal_classes_reusing_the_keyframe():
    """: `.badge-bar-revealed`/`.badge-flag-revealed` reuse the SAME
    `edgeTabsFadeInRight` keyframe the Wallet's own reveal uses -- one shared
    Iris-style fade timing across all three handles, not three near-duplicate
    keyframes."""
    css = CERES_CSS.read_text(encoding="utf-8")
    for class_name in ("badge-bar-revealed", "badge-flag-revealed"):
        # Anchored at line-start so the combined reduced-motion selector further
        # up the file (`.badge-bar-revealed, .badge-flag-revealed { ... }`
        # which also contains the bare text ".badge-flag-revealed {") is never
        # matched by mistake.
        rule_match = re.search(rf"(?:^|\n)\.{class_name} \{{(.*?)\n\}}\n", css, re.S)
        assert rule_match, f".{class_name} own rule not found"
        rule = rule_match.group(1)
        assert "visibility: visible;" in rule
        assert "opacity: 1;" in rule
        assert "animation: edgeTabsFadeInRight" in rule
    assert (
        "#badge-bar.badge-bar-revealed, #badge-flag.badge-flag-revealed { animation: none; } "
        "/* id-qualified so this override always outranks the later un-scoped "
        ".badge-bar-revealed/.badge-flag-revealed rules, regardless of source order */"
    ) in css


def test_reduced_motion_override_is_id_qualified_for_all_three_handles():
    """: `.badge-bar-revealed`/`.badge-flag-revealed` redeclare `animation:
    edgeTabsFadeInRight` later in the file at equal class-only specificity, so the
    plain-class override above them lost the cascade on source order alone (measured
    on the live site: opacity 0 -> 0.19 -> 0.46 -> 1 under reduced motion). `#edge-tabs`
    happened to work only because its base rule sits earlier in the file. Every one of
    the three reduced-motion overrides must be id-qualified so the fix does not depend
    on rule order."""
    css = CERES_CSS.read_text(encoding="utf-8")
    # The file has several reduced-motion blocks; all three overrides must share one multi-line block.
    blocks = [m.group(1) for m in re.finditer(r"@media \(prefers-reduced-motion: reduce\) \{\n(.*?)\n\}\n", css, re.S)]
    assert blocks, "reduced-motion media block not found"
    assert any(
        "#edge-tabs.edge-tabs-revealed { animation: none; }" in block
        and "#badge-bar.badge-bar-revealed, #badge-flag.badge-flag-revealed { animation: none; }" in block
        for block in blocks
    )


def test_board_reveal_dispatches_the_hand_off_event():
    """board.js cannot import app.js directly (app.js already imports FROM
    board.js) -- a DOM CustomEvent is the hand-off, same pattern
    `ceres:player-changed` already uses in this file."""
    js = BOARD_JS.read_text(encoding="utf-8")
    fn_match = re.search(r"function revealBoardTable\(ready = true\) \{.*?\n\}\n", js, re.S)
    assert fn_match, "revealBoardTable source shape changed -- update this test's slice"
    assert "window.dispatchEvent(new CustomEvent('ceres:board-revealed'));" in fn_match.group(0)


def test_badge_overlay_dispatches_its_own_rows_synced_event_once():
    """: badge-overlay.js owns the "bar's own rows have been laid out"
    fact -- it dispatches this once, on its own first `syncBadgeOverlay()`
    pass, so app.js's reveal gate never shows the bar before it actually has
    content."""
    js = BADGE_OVERLAY_JS.read_text(encoding="utf-8")
    assert "let barRowsSyncedOnce = false;" in js
    fn_match = re.search(r"export function syncBadgeOverlay\(reason\) \{.*?\n\}\n", js, re.S)
    assert fn_match, "syncBadgeOverlay source shape changed -- update this test's slice"
    body = fn_match.group(0)
    assert "if (!barRowsSyncedOnce) {" in body
    assert "barRowsSyncedOnce = true;" in body
    assert "window.dispatchEvent(new CustomEvent('ceres:badge-bar-synced'));" in body


def test_app_js_reveals_all_three_handles_together_only_after_every_condition():
    js = APP_JS.read_text(encoding="utf-8")
    assert "let EDGE_TABS_POSITIONED = false;" in js
    assert "let BAR_POSITIONED = false;" in js
    assert "let BAR_ROWS_SYNCED = false;" in js
    assert "let BOARD_REVEALED = false;" in js
    fn_match = re.search(r"function maybeRevealEdgeHandles\(\) \{.*?\n\}\n", js, re.S)
    assert fn_match, "maybeRevealEdgeHandles source shape changed -- update this test's slice"
    body = fn_match.group(0)
    assert "if (!EDGE_TABS_POSITIONED || !BAR_POSITIONED || !BAR_ROWS_SYNCED || !BOARD_REVEALED) return;" in body
    # All three handles revealed in the SAME function call (same tick) -- no
    # flashing between them.
    assert "edgeTabs.classList.add('edge-tabs-revealed');" in body
    assert "badgeBar.classList.add('badge-bar-revealed');" in body
    assert "badgeFlag.classList.add('badge-flag-revealed');" in body
    init_match = re.search(r"function initEdgeTabsReveal\(\) \{.*?\n\}\n", js, re.S)
    assert init_match, "initEdgeTabsReveal source shape changed -- update this test's slice"
    init_body = init_match.group(0)
    assert "window.addEventListener('ceres:board-revealed'" in init_body
    assert "BOARD_REVEALED = true;" in init_body
    assert "window.addEventListener('ceres:badge-bar-synced'" in init_body
    assert "BAR_ROWS_SYNCED = true;" in init_body
    assert init_body.count("maybeRevealEdgeHandles();") == 2
    assert re.search(r"(?m)^  initEdgeTabsReveal\(\);(?:\s*//[^\n]*)?$", js)
    # centerThemePicker()'s own badge-bar block sets its position flag exactly once
    # the geometry has actually been written, not unconditionally. moved the
    # edge-tabs equivalent out into updateEdgeTabsPosition() (see
    # test_edge_tabs.py's own reveal-flag guard for that half) since it now
    # needs a per-scroll-frame update centerThemePicker() itself does not run.
    center_match = re.search(r"function centerThemePicker\(\) \{.*?\n\}\n", js, re.S)
    assert center_match, "centerThemePicker source shape changed -- update this test's slice"
    center_body = center_match.group(0)
    assert "updateEdgeTabsPosition();" in center_body
    assert re.search(r"badgeBarEl\.style\.height = `\$\{scrollerRect\.height\}px`;[ \t]*(?://[^\n]*)?\n\s*BAR_POSITIONED = true;", center_body)
    assert center_body.count("maybeRevealEdgeHandles();") == 1

    edge_tabs_fn_match = re.search(r"function updateEdgeTabsPosition\(\) \{.*?\n\}\n", js, re.S)
    assert edge_tabs_fn_match, "updateEdgeTabsPosition source shape changed -- update this test's slice"
    edge_tabs_body = edge_tabs_fn_match.group(0)
    assert re.search(r"edgeTabs\.style\.top = `\$\{edgeTabsY\}px`;[ \t]*(?://[^\n]*)?\n\s*EDGE_TABS_POSITIONED = true;", edge_tabs_body)
    assert "maybeRevealEdgeHandles();" in edge_tabs_body


def test_node_check_touched_js_files():
    for path in (APP_JS, BOARD_JS, BADGE_OVERLAY_JS):
        result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, f"{path}: {result.stderr}"
