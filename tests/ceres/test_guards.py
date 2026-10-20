"""Source guards for (board scrollbar), (Terrain inset) and (badge pulse sync)."""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "ceres" / "static"


def test_badge_bar_and_flag_right_edge_is_css_only():
    # wrote the bar's `right` from the table's overhang (-8px), which pushed it past
    # the page edge and gave the board an empty horizontal overflow and a scrollbar.
    app = (STATIC / "app.js").read_text()
    assert not re.search(r"badgeBarEl\.style\.right\s*=", app)
    assert not re.search(r"\bflag\.style\.right\s*=", app)


def test_terrain_cell_keeps_its_left_inset():
    css = (STATIC / "ceres.css").read_text()
    assert re.search(
        r"table\.plot-table tbody tr\.plot-row td\.terrain-cell\s*\{\s*padding-inline-start:\s*1\.1rem;", css
    )


def test_every_badge_pulse_is_on_the_shared_timeline():
    css = (STATIC / "ceres.css").read_text()
    sync = (STATIC / "pulse-sync.js").read_text()
    names = set(re.findall(r"\.badge-status-glyph[^{]*\{[^}]*animation:\s*([\w-]+)", css)) - {"none"}
    assert names, "no badge glyph animation found"
    for name in names:
        assert f"'{name}'" in sync, f"{name} missing from STAGE_PULSES"


def test_badges_show_no_tooltip():
    # the badge column and its slide-out never open one.
    tooltip = (STATIC / "tooltip.js").read_text()
    assert "const NO_TIP_ZONE = '#badge-bar, .badge-overlay';" in tooltip
    assert "!trigger.closest(NO_TIP_ZONE)" in tooltip


def test_harvest_box_uses_ceres_tooltips_only():
    # no native title tooltips.
    source = (STATIC / "harvest-state.js").read_text()
    assert 'title="' not in source
    assert "setAttribute('title'" not in source


def test_wallet_chooser_keeps_its_direction_while_closing():
    # when closing -- the upward class must not depend on `expanded`.
    wallet = (STATIC / "wallet.js").read_text()
    assert "wallet-controls${state.drawerUpward ? ' is-upward' : ''}" in wallet
    assert "expanded && state.drawerUpward ? ' is-upward'" not in wallet


def test_shortfall_confirmation_uses_the_destructive_style():
    # Owner: the risky "Continue despite worker shortfall" must not look like a normal primary action.
    source = (STATIC / "regional-dec.js").read_text()
    call = source.split("button('confirm-shortfall'", 1)[1].split(")", 1)[0]
    assert "false, D.busy || review.previewing, ' data-control-case=\"destructive\"'" in call


def test_wallet_window_has_the_iris_glow_on_an_opacity_layer():
    # drawn once on the drawer's layer, only faded by opacity.
    css = (STATIC / "ceres.css").read_text()
    assert "html:not(.ceres-lw-le-1279) .dec-drawer { overflow-clip-margin: 20px; }" in css
    rule = css.split("html:not(.ceres-lw-le-1279) .dec-drawer::after {", 1)[1].split("}", 1)[0]
    assert "box-shadow: 0 0 18px -3px var(--primary)" in rule and "opacity: 0" in rule
    assert 'html:not(.ceres-lw-le-1279) .dec-drawer[data-wallet-motion="open"]::after { opacity: 1;' in css


def test_ability_losses_are_priced_over_the_whole_current_roster():
    # Verified live on 065-09-083: Toil and Kin -27,187.5 and abilities -87,000 whatever else is removed.
    src = (STATIC.parent / "candidates.py").read_text()
    assert '"pp_delta": bloodline_pp_delta' in src
    assert 'context={"deed_uid": deed_uid, "pp_delta": ability_pp_delta}' in src
    for name in ("bloodline_pp_delta = -sum(", "ability_pp_delta = -sum("):
        body = src.split(name, 1)[1][:400]
        assert "for card in plot.cards" in body


def test_changes_window_states_and_invalid_removed_on_close():
    # Owner: waiting shows the time until signable; no longer valid is red, says why, and leaves the queue on close
    # refused is orange with the exact refusal.
    js = (STATIC / "checkout.js").read_text()
    assert 'ch-status is-waiting">Waiting · ' in js
    assert "It is removed from Changes when you close this window." in js
    assert 'ch-status is-bad">Refused by the game' in js  # refused is red
    assert "queueMicrotask(dropInvalidChanges)" in js
    assert "item._plan_invalid_reason && !item.txId" in js
    assert ".ch-status.is-waiting { color: var(--warn); }" in (STATIC / "ceres.css").read_text()  # maintainer-approved state matrix (waiting = warning) replaces the gold


def test_changes_warnings_are_compact_and_say_after_signing():
    # Owner: "compact those infos further. Do not use UUID but normalized plot names ... combine all unpowered worker
    # slots together into one message ... clear that this all happens only AFTER signing".
    js = (STATIC / "checkout.js").read_text()
    assert "const notices = compactPlotNotices(group, plotNotices(group));" in js
    assert "After signing, ${list}" in js and "leaves ${plotName} without power" in js
    assert "if (notice.code === 'workers_staged_without_power' && powerLoss) return;" in js
    py = (STATIC.parent / "op_preview.py").read_text()
    assert 'return f"{name}" + (f" in slot {slot}" if slot is not None else "")' in py
    assert 'f"{name} to plot {deed_uid}"' not in py


def test_expired_routing_review_refreshes_quietly():
    # was a dead end -- an expired or restart-lost
    # review now refreshes its amounts instead of showing an error.
    js = (STATIC / "routing-setup.js").read_text()
    assert "if (!/expired or changed/i.test(error?.message || '')) throw error;" in js
    assert "S.preview = await preview();" in js


def test_duplicate_route_choice_moves_to_a_free_combination():
    # "unblocked when switching the route kind but
    # this is unclear and hidden". A choice keeps itself, moves the other fields, and says so.
    js = (STATIC / "routing-setup.js").read_text()
    assert "function freeRoute(candidate, field, taken, kinds) {" in js
    assert "const next = freeRoute(candidate, field, takenBy(S.rules, index), enabledKinds);" in js
    assert "blocked('source_region', { source_region: region.region_uid })" in js
    assert "blocked('symbol', { symbol })" in js
    assert "so this route is now ${KIND_LABEL[routeKindOf(next)]} for ${tokenLabel(next.symbol)}." in js
    assert "taken.has(routeKey({ ...rule, symbol }))" not in js


def test_only_the_route_list_scrolls_in_routing_setup():
    css = (STATIC / "ceres.css").read_text()
    rule = css.split("@media (min-width:761px) {\n .r3-setup .r3-flow-layout { align-items:stretch; }", 1)[1].split("}\n}", 1)[0]
    assert "align-self:stretch" in rule and "contain:size" in rule and "overflow-y:auto" in rule and "scrollbar-width:none" in rule
    assert "margin:-4px -16px -4px -4px; padding:4px 16px 4px 4px;" in rule
    modal = (STATIC / "harvest-modal.js").read_text()
    assert "routeList.scrollTop = routeListScroll;" in modal


def test_finished_routing_is_green_and_every_route_points_at_its_row():
    # then "All routes of the bundle should
    # use a pointer and point at their own respective part inside the bundle box" and "turn fully green".
    js = (STATIC / "routing-window.js").read_text()
    assert "Routing complete · all ${routes.length} route" in js
    assert "chip(route, shownRouteIds.has(route.member.route_id), record, excludedRouteIds, busy)" in js
    assert 'data-r3-operation-ref="${esc(operation.id)}"' in js
    assert "export function drawRoutePointers(root) {" in js
    assert "DEC ${done ? 'added' : 'needed'}" in js
    assert "if (name === 'view-bundle')" in (STATIC / "routing-run.js").read_text()
    modal = (STATIC / "harvest-modal.js").read_text()
    assert "patchRoutingWindow(panel.querySelector('.r3-window'), body)) { scrollRouteIndexToActive(panel); updateRouteScrollCue(panel); drawRoutePointers(panel); return; }" in modal
    css = (STATIC / "ceres.css").read_text()
    assert ".r3-pointers path.is-confirmed { stroke:var(--ok);" in css
    assert ".r3-flow-stage.is-confirmed { border-color:var(--ok);" in css


def test_region_cooldown_shows_the_time_left_and_sits_on_the_row_centre():
    # Owner: "make sure the cooldown for a region harvest is vertically aligned and shows the actual cooldown time until
    # the next harvest is possible again".
    js = (STATIC / "harvest-modal.js").read_text()
    assert "label: `Cooldown · ${fmtDuration(coolLeft(region))}`" in js
    assert "M.cooldownTimer = setTimeout(" in js
    css = (STATIC / "ceres.css").read_text()
    assert ".hm-batch-idle-status { align-self:center; font-size:.8rem; line-height:1; }" in css


def test_phone_board_is_art_cards_with_the_building_centred():
    # "wide pops more", "Align the
    # center of the building with the center of the wide plot box", badges "as an opt-in overlay", wallet in the bar.
    rows = (STATIC / "rows.js").read_text()
    assert "export function plotCardHtml(row, rowIndex) {" in rows and 'class="plot-card plot-tile rarity' in rows
    assert "const plate = tilePlateLayers(row);" in rows
    art = (STATIC / "art.js").read_text()
    assert "const PLATE_GEOMETRY = {" in art and "export function tilePlateLayers(row) {" in art
    css = (STATIC / "mobile-plot.css").read_text()
    assert "inset-inline-start: calc(50cqw - var(--p-cx) * var(--iw))" in css
    assert "html.ceres-lw-le-1919.ceres-lw-le-1279 .plot-tile-badges { display: none; }" in css
    assert "html.ceres-lw-le-1919.ceres-lw-le-1279 header.board-header { position: relative; }" in css
    index = (STATIC / "index.html").read_text()
    assert 'id="mobile-wallet-btn"' in index and 'id="scroll-top-btn"' in index and 'id="card-badges"' in index


def test_phone_wallet_scroll_lock_slider_swaps_and_header_art():
    # Owner: wallet scrolling "scrolls the board behind it"; "Activate LPs for mobile"; "use the wallet slider inside the
    # wallet"; "Remove the wallet button from the filter page"; landing artwork for the phone header, smaller file.
    css = (STATIC / "mobile-plot.css").read_text()
    assert "has(#regional-dec-drawer[open]) body { overflow: hidden; overscroll-behavior: none; }" in css
    assert "#regional-dec-drawer .wallet-tabs--icons { display: flex; flex-wrap: nowrap;" in css
    assert "html.ceres-lw-le-1919.ceres-lw-le-1279 #regional-dec-button { display: none; }" in css
    assert "hdr-scene: url('assets/ui/ceres-header-phone.webp')" in css
    assert (STATIC / "assets" / "ui" / "ceres-header-phone.webp").stat().st_size < 80_000
    assert "if (!player) return '';" in (STATIC / "wallet-lp.js").read_text()
    assert ".dec-region h3 .ch-plot-badge { display: inline-flex; align-items: center;" in (STATIC / "ceres.css").read_text()


def test_phone_harvest_is_part_of_the_page_with_pinned_actions():
    # Owner: "Viewport is locked in between top section being fixed and bottom section being fixed ... Make the top bar
    # unpinned and smaller ... Remove the redundant close button ... Keep wallet switcher and signing button sticky."
    css = (STATIC / "mobile-plot.css").read_text()
    assert "#harvest-modal-overlay:not([hidden]) { position: relative;" in css
    assert "#harvest-modal-overlay .hm-foot { position: sticky; inset-block-end: 0;" in css
    assert '#harvest-modal-overlay .hm-foot [data-hm="close"] { display: none; }' in css
    assert "#harvest-modal-overlay .hm-batch-slots { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));" in css
    assert "#harvest-modal-overlay .hm-headr .hm-sub-more { display: none; }" in css
    js = (STATIC / "harvest-modal.js").read_text()
    assert "if (phone && el.hidden) M.boardScrollY = window.scrollY;" in js


def test_phone_routing_is_compact():
    # one route per line, two-column form, footer on one row, no side pointers.
    css = (STATIC / "mobile-plot.css").read_text()
    assert "#harvest-modal-overlay .r3-route-index { grid-template-columns: minmax(0, 1fr);" in css
    assert "#harvest-modal-overlay .r3-edit, html.ceres-lw-le-1919.ceres-lw-le-1279 #harvest-modal-overlay .r3-edit--lp { grid-template-columns: repeat(2, minmax(0, 1fr));" in css
    assert '#harvest-modal-overlay .hm-foot:has(> [data-hm="routing-back"]) { display: flex; flex-wrap: nowrap;' in css
    assert "if (stage.left < list.right - 1) continue;" in (STATIC / "routing-window.js").read_text()


def test_phone_plot_sheet_scrolls_with_the_page_and_back_keeps_the_board_position():
    # the same page-flow fix as Harvest, and Back returns to the same board place.
    css = (STATIC / "mobile-plot.css").read_text()
    assert ".mobile-plot-sheet:not([hidden]) { position: relative; inset: auto; z-index: auto; display: block;" in css
    assert ".mobile-plot-sheet .slot-grid > .asset-slot { margin-block-start: -1.55rem; }" in css
    rows = (STATIC / "rows.js").read_text()
    assert "if (wasHidden) MOBILE_PLOT_SCROLL_Y = window.scrollY || 0;" in rows
    app = (STATIC / "app.js").read_text()
    assert "window.addEventListener?.('popstate', () => globalThis.requestAnimationFrame?.(() => window.scrollTo?.(0, boardY)), { once: true });" in app


def test_phone_harvest_box_straddles_the_worksite_art():
    css = (STATIC / "mobile-plot.css").read_text()
    assert ".mobile-plot-harvest-band > .mobile-plot-actions { position: relative; z-index: 3; margin-block-start: calc(-1 * (var(--hb-h, 61px) / 2)); }" in css


def test_phone_plot_view_uses_the_aligned_art_pair():
    rows = (STATIC / "rows.js").read_text()
    assert "scene.classList.toggle?.('has-plate', Boolean(plate));" in rows
    css = (STATIC / "mobile-plot.css").read_text()
    assert ".mobile-plot-worksite-art.has-plate > .sheet-plate { --ih: calc(66cqh / (var(--p-base) - var(--p-top)));" in css
