""": the plan card's four steps run as one full-width timeline in Changes (they sat two by two before). : no "Leave out" on a copy whose unstake is signed."""
from pathlib import Path

STATIC = Path(__file__).parents[2] / "ceres" / "static"


def test_leave_out_only_for_unsigned_copies():
    ui = (STATIC / "combine-chain-ui.js").read_text(encoding="utf-8")
    assert "const leave = (input) => signed || input.unstake?.tx_id ? '' :" in ui


def test_plan_card_steps_one_timeline():
    css = (STATIC / "ceres.css").read_text(encoding="utf-8")
    # one column at every viewport, steps in order, a rail behind the step markers
    assert ".chain-card .chain-steps { position: relative; grid-template-columns: minmax(0, 1fr); }" in css
    assert ".chain-card .chain-step { position: relative; grid-column: 1;" in css
    assert ".chain-card .chain-steps { grid-template-columns: repeat(2" not in css


def test_planned_tile_beside_the_steps_and_not_repeated():
    ui = (STATIC / "combine-chain-ui.js").read_text(encoding="utf-8")
    checkout = (STATIC / "checkout.js").read_text(encoding="utf-8")
    css = (STATIC / "ceres.css").read_text(encoding="utf-8")
    assert "placeHtml(id, plan, place)], states)}</article>" in ui and '<div class="chain-place">${tile}' in ui  # inside step 4
    assert "export function planPlacementTileHtml(id, plan)" in checkout
    assert "entry._asset?.planned && planIds.has(entry.chain_id)" in checkout
    assert ".chain-step-4 .chain-place-tile { grid-column: 2; grid-row: 1; align-self: start;" in css  # docked top right
    assert ".chain-step-4 .chain-place > p { grid-column: 1; grid-row: 1;" in css  # the text wraps left of the tile
    assert "after combine</b>" not in checkout[checkout.index("export function planPlacementTileHtml"):][:2500]  # no box


def test_place_step_names_the_plot_with_its_badge():
    ui = (STATIC / "combine-chain-ui.js").read_text(encoding="utf-8")
    assert "esc(place).replace(esc(chainPlotLabel({ deed_uid: deed })), plotBadgeHtml(row, deed))" in ui


def test_cooldown_step_has_one_large_live_timer():
    ui = (STATIC / "combine-chain-ui.js").read_text(encoding="utf-8")
    css = (STATIC / "ceres.css").read_text(encoding="utf-8")
    assert '<div class="chain-timer" role="timer"' in ui and '<span class="spinner" aria-hidden="true"></span><b data-countdown-until=' in ui
    assert "Ready when the last one ends:" not in ui
    assert ".chain-timer b { font-size: 1.5rem;" in css


def test_planned_tile_shows_the_card_name():
    editor = (STATIC / "editor.js").read_text(encoding="utf-8")
    assert "· planned`" not in editor
