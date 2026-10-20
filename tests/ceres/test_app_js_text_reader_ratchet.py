"""G3 (campaign plan `ceres-modularisation-campaign.md` revision 3, §2.2/§2.3;
`work/modularisation/TEST-CENSUS.md` §4): the AST ratchet against text-position
coupling to any `ceres/static/*.js` file.

Installed BEFORE Phase 1 as the campaign's own progress meter, not as an
end-of-campaign audit -- a migrated file that still reads `app.js` (or a
post-split module) by a new spelling would otherwise pass review and re-enter
the population silently. `BASELINE` below is the census's 43 files still to
migrate (census §2 table, cited by row); `ALLOWED` is the five files the
census/plan rule are legitimately kept, each with its own census row cited.
Neither list is guessed at: both were re-derived from the AST detector below,
run against the working tree at the time this file was written, and matched
the census's own count exactly (see the module docstring's runtime note).

Deliberately conservative and spelling-independent (census §4): it globs
`ceres/static/*.js` at runtime, so a new post-split module is covered the day
it exists, and it matches on the AST rather than on any particular spelling of
a path (segmented `Path(...) / "app.js"`, a contiguous string, a read inside a
module-scope helper -- all caught the same way).

 queue F5, job 1: a verifier found a second blind spot -- a file
that never names a `ceres/static/*.js` string itself, but gets there
indirectly by importing `ENTRY` / `read_entry_source` / an `APP_JS`-like alias
from `tests/ceres/vm_context.py` and then slicing or mutating that source by
position (`.read_text()`/`.read()`/`open(...)`/`.replace(`/`.index(`/a
`*_slice(` helper), or handing it to `subprocess`, is just as coupled as a
file that spells the path out and reads it directly -- the detector now flags
that too (`_imports_vm_context_entry` / `_uses_vm_context_entry_by_position`).
The honest exemption: a file that ONLY calls `run_in_module_context(...)` --
the sanctioned whole-module loader vm_context.py exists to provide -- is not
an offender, since it never slices the source itself.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[2]

# Kept, reviewed, each cited by its work/modularisation/TEST-CENSUS.md §2 row.
ALLOWED = {
    "tests/ceres/test_candidate_redesign_frontend.py",  # row 6c — test_module_invariants_remain_closed RULED KEPT (split-safety guard on the entry file; retarget at M8)
    "tests/ceres/test_autocapture_frontend.py",          # row 2 — three *_mutation_goes_red route-injection tests read the source to inject (b-mut class)
    "tests/ceres/test_combine_reachability.py",          # row 14 — *_mutation_is_red companions read ENTRY by position to inject (b-mut class)
    "tests/ceres/test_combine_recovery_guards.py",       # row 15 — same as row 14
    "tests/ceres/test_combine_foil_guards.py",         # row 11 — (b-mut) route-injection halves read ENTRY by position (same class as rows 26/27/39)
    "tests/ceres/test_combine_ledger_and_quantity.py",  # row 13 — *_mutation_is_red tests read ENTRY by position to build the injected module (b-mut class, same as rows 11/26/27/39)
    # 2026-09-02 Phase 1 reconciliation (verifier-ruled, campaign plan §15.6 / rows cited)
    "tests/ceres/vm_context.py",                 # §15.6b — the ONE shared whole-module vm loader; reads app.js by design
    "tests/ceres/test_base_pp_tiles.py",         # row 4 — (b-mut) footer guard: count(anchor)==1 + injection_seen
    # row 46 -- (c) legitimate file read: pins the served-URL/cache-busting
    # contract (index.html's `?v=` token, /static's no-cache header), never
    # reads app.js as code-as-text.
    "tests/ceres/test_static_cache_busting.py",
    # row 19 -- almost entirely live-browser (already the target shape); its one
    # `test_g6_unknown_base_badge_is_reason_bearing` full-evaluates app.js in a
    # `vm.runInNewContext` whole-module loader, kept and retargeted at the
    # post-split entry file at M8, not migrated to a rendered-behaviour test.
    "tests/ceres/test_f0da_frontend_base_badge_truth.py",
    # row 26 -- (b-mut) route-injected mutation guard (campaign plan §2.1's
    # model for the pattern); must read the real source to build its injection.
    "tests/ceres/test_live_cooling_surface.py",
    # row 27 -- (b-mut) route-injected mutation guards, same reason as row 26.
    "tests/ceres/test_live_loading_spinners.py",
    # row 39 -- (b-mut) route-injected mutation guard, same reason as row 26.
    "tests/ceres/test_virtualization_guards.py",
    # (plan §3.1 Batch B rulings, census §2 rows cited): five files
    # ruled genuinely static -- kept, each retargeted at its post-split module at M8.
    # row 7 -- whole-surface absence claim ("the browser never hand-builds a broadcast
    # payload"); no single rendered interaction can prove an absence across the surface.
    "tests/ceres/test_boundaries.py",
    "tests/ceres/test_scale_guards.py",  # structural annotation-presence guard, same class as test_boundaries.py; not app.js source-position injection
    # row 23 -- "ready() once at EOF" module-invariant guard; retargeted at the entry
    # file post-split.
    "tests/ceres/test_frontend_skins_and_init.py",
    # row 24 -- symbol-absence list (removed idle/unassigned sections never reappear).
    "tests/ceres/test_idle_and_unassigned_sections_removed.py",
    # row 29 -- three undriven-overlay pattern checks.
    "tests/ceres/test_overlay_backdrop_close.py",
    # row 44 -- CDN-absence check.
    "tests/ceres/test_worksite_modal_states.py",
}

# The 43 files TEST-CENSUS.md §2 counts as text-readers of ceres/static/*.js
# not yet migrated to a rendered-behaviour equivalent. Each comment is the
# census's own row number for that file.
# queue F7 (census rows 22, 30): both remaining BASELINE entries
# test_frontend_removal_impact.py's two `APP_JS.read_text()`/`subprocess.run` node
# tests, and test_power_slot_ui.py's `source.count(...)`/`_slice(...)` structural
# checks -- were the last positional readers. Converted to whole-module
# `run_in_module_context` calls (frontend_removal_impact.py) and a
# `typeof renderPowerSlot === 'function'` existence check plus a ruled drop of the
# "renderSlotEditor calls it" claim (test_power_slot_ui.py; see that test's own
# docstring for the ruling). Neither file names a `ceres/static/*.js` string or
# imports `ENTRY`/`read_entry_source` by position anymore, so both left BASELINE with
# no successor entry -- BASELINE went empty at that point.
# (2026-09-20): BASELINE went stale as new feature/regression test files
# landed after that F7 pass -- none were individually reasoned into ALLOWED (that
# list requires the per-file kept/retargeted argument the existing entries carry)
# so honesty means re-populating BASELINE with the ratchet's own current offender
# set rather than guessing which ones deserve ALLOWED's stronger claim. This is a
# bulk "not yet migrated / not yet reasoned about" backlog, not a census re-walk
# TEST-CENSUS.md §2 row numbers for these are NOT re-derived here and a dedicated
# census-regeneration ticket is owed (see work/notes/-test-debt-2026-09-19.md
# and the manifest note below for the two follow-ups this pass identified).
BASELINE: set[str] = {
    "tests/ceres/test_board_guards.py",
    "tests/ceres/test_board_r111_guards.py",
    "tests/ceres/test_card_art_cache_route.py",
    "tests/ceres/test_eos_data_watchdog.py",
    "tests/ceres/test_eos_p5_contract.py",
    "tests/ceres/test_phase_d.py",
    "tests/ceres/test_phase_e.py",
    "tests/ceres/test_phase_f.py",
    "tests/ceres/test_fill_actions.py",
    "tests/ceres/test_badge_filter.py",
    "tests/ceres/test_button_contract.py",
    "tests/ceres/test_badge_colours.py",
    "tests/ceres/test_field_contract.py",
    "tests/ceres/test_terrain_penalty.py",
    "tests/ceres/test_picker_art.py",
    "tests/ceres/test_align_close_all.py",
    "tests/ceres/test_construction_box.py",
    "tests/ceres/test_broadcast_gate.py",
    "tests/ceres/test_fill_plot_source.py",
    "tests/ceres/test_ir6030_card_pool_freshness.py",
    "tests/ceres/test_ir6040_width_diag.py",
    "tests/ceres/test_divider_guards.py",
    "tests/ceres/test_colspan_matches_headers.py",
    "tests/ceres/test_lows_batch.py",
    "tests/ceres/test_kept_plan_visibility.py",
    "tests/ceres/test_live_art_continuity.py",
    "tests/ceres/test_live_plot_reveal.py",
    "tests/ceres/test_live_r117_terrain_display.py",
    "tests/ceres/test_mobile_plot_view.py",
    "tests/ceres/test_power_slot_ui.py",
    "tests/ceres/test_r111_property_guards.py",
    "tests/ceres/test_r117_terrain_display.py",
    "tests/ceres/test_r163_fallback_and_checkout.py",
    "tests/ceres/test_r89_r90_picker_guards.py",
    "tests/ceres/test_r97_game_acceptance.py",
    "tests/ceres/test_receipt_operation_details.py",
    "tests/ceres/test_rows_detail_handoff.py",
    "tests/ceres/test_transactions.py",
    "tests/ceres/test_grain_verdict.py",
    "tests/ceres/test_signed_queue.py",
    "tests/ceres/test_s13770_game_results_first.py",
    "tests/ceres/test_badge_overlay.py",
    "tests/ceres/test_wallet.py",
    "tests/ceres/test_immediate_routing_failure.py",
    "tests/ceres/test_resource_mentions.py",
    "tests/ceres/test_s14150_receipts_design.py",
    "tests/ceres/test_edge_tabs.py",
    "tests/ceres/test_wallet_tray.py",
    "tests/ceres/test_row_slideouts.py",
    "tests/ceres/test_table_redesign.py",
    "tests/ceres/test_chevron_close.py",
    "tests/ceres/test_edge_stack.py",
    "tests/ceres/test_wallet_panel.py",
    "tests/ceres/test_material_columns.py",
    "tests/ceres/test_status_longpoll.py",
    "tests/ceres/test_edge_tabs_reveal.py",
    "tests/ceres/test_badge_overlay_row_inset.py",
    "tests/ceres/test_two_layer_handles.py",
    "tests/ceres/test_picker_grid.py",
    "tests/ceres/test_filter_row_guards.py",
    # (2026-09-20): test_splinter_row.py (landed in ) was
    # missing from BASELINE, so the ratchet failed on master. Same backlog.
    "tests/ceres/test_splinter_row.py",
    # (2026-09-20): this branch's own new test file, same backlog.
    "tests/ceres/test_edge_resize_observer.py",
    # (2026-09-20): test_routing_contracts.py (/ handoff-drift
    # guards) reads ceres/static/harvest-routing.js and ceres.css by position in three
    # of its tests (test_ui_shows_handoff_error_at_the_sign_button_and_recovers_the_next_click
    # test_ui_top_notice_yields_its_live_region_to_the_footer_error
    # test_lp_deposit_card_shows_up_to_slippage_dec_line) to pin exact template-string
    # markup (error-banner placement, once-flag guard, the LP "Up to" line) against
    # source drift -- genuine text-position coupling, not a false trip like
    # test_modulepreload.py's HTML-vs-generator comparison was. Converting these three
    # to rendered-behaviour checks needs a driven harness for the harvest-routing
    # signing flow this ticket did not build; baselined rather than rushed. Same
# backlog, own entry since it is this ticket's own new offender, not pre-existing.
    "tests/ceres/test_routing_contracts.py",
    # (2026-09-20): this geometry test uses the sanctioned whole-module VM
    # renderer, but its entry path necessarily names harvest-results.js; keep the
    # production-render guard visible while its source-position coupling is migrated.
    "tests/ceres/test_harvest_modal_region_grid.py",
    # (2026-09-21): test_harvest_modal_side_by_side.py and
    # test_tooltip_scale.py migrated to the sanctioned whole-module VM harness
    # and no longer read ceres/static/*.js by position -- removed from BASELINE.
    # 2026-09-20 post-merge (master landed /0.9.693 while this branch was
    # open): two more offenders the detector itself found, same backlog.
    "tests/ceres/test_s14630_receipt_art.py",
    "tests/ceres/test_live_measured_geometry.py",
    "tests/ceres/test_stage_plan_hydration.py",
    "tests/ceres/test_staged_worksite_build.py",
    "tests/ceres/test_survival_frontend.py",
    "tests/ceres/test_tile_redesign_static.py",
    "tests/ceres/test_tooltip_icon_wiring.py",
    "tests/ceres/test_tooltip_surface.py",
    "tests/ceres/test_total_pp_labels.py",
    # exact footer template placement remains a source contract across
    # five independently rendered surfaces; the same file also carries a vm_context
    # DOM-shim geometry check for the shared CSS contract.
    "tests/ceres/test_s14960_wallet_sign_pair.py",
}


def _names_a_static_js_file(tree: ast.AST, js_names: set[str]) -> bool:
    return any(
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and (node.value in js_names or any(node.value.endswith(name) for name in js_names))
        for node in ast.walk(tree)
    )


def _reads_a_file(tree: ast.AST) -> bool:
    return any(
        (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"read_text", "read"})
        or (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open")
        for node in ast.walk(tree)
    )


def _mentions_a_node_subprocess_route(tree: ast.AST) -> bool:
    # The node-route blind spot (plan §2.2, census §4's "own blind spot, named")
    # a test can hand the JS path to a Node subprocess
    # (`node -e "require('fs').readFileSync(...)"`) instead of reading it in
    # Python -- no Python read call, but the same string-position coupling.
    return any(
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and ("subprocess" in node.value or "node" in node.value)
        for node in ast.walk(tree)
    )


def _imports_vm_context_entry(tree: ast.AST) -> bool:
    """The indirection blind spot (found by a verifier, this queue's job 1): a file
 can obtain the app.js path WITHOUT ever naming a `ceres/static/*.js` string
 itself, by importing `ENTRY` / `read_entry_source` / an `APP_JS`-like alias
 FROM `vm_context` (any of `tests.ceres.vm_context`, `.vm_context`,
 `vm_context`), then slicing or mutating the source by position -- the same
 coupling `_names_a_static_js_file` exists to catch, just one indirection
 removed. Importing `run_in_module_context` alone does NOT count here: that
 is the sanctioned whole-module loader (vm_context.py's own reason for
 being, plan §15.6b), not a name for the raw path or source text."""
    target_names = {"ENTRY", "read_entry_source"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[-1] == "vm_context":
            for alias in node.names:
                if alias.name in target_names or "APP_JS" in alias.name.upper():
                    return True
    return False


def _slices_or_mutates_by_position(tree: ast.AST) -> bool:
    # `.read_text()`/`.read()`/`open(...)` (loading it as text at all, outside the
    # sanctioned `run_in_module_context` loader) and `.replace(`/`.index(`/a
    # `*_slice(` helper (the actual position-coupling operations) -- same rule as
    # `_reads_a_file`, extended to the mutate/locate calls a positional patcher uses.
    method_names = {"read_text", "read", "replace", "index"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and (
                node.func.attr in method_names or node.func.attr.endswith("_slice")
            ):
                return True
            if isinstance(node.func, ast.Name) and node.func.id == "open":
                return True
    return False


def _passes_to_subprocess(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "subprocess":
            return True
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            return True
        if isinstance(node, ast.Import) and any(alias.name == "subprocess" for alias in node.names):
            return True
    return False


def _uses_vm_context_entry_by_position(tree: ast.AST) -> bool:
    if not _imports_vm_context_entry(tree):
        return False
    return _slices_or_mutates_by_position(tree) or _passes_to_subprocess(tree)


def _reads_a_static_js_file_by_position(path: Path, js_names: set[str]) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    if _uses_vm_context_entry_by_position(tree):
        return True
    if not _names_a_static_js_file(tree, js_names):
        return False
    return _reads_a_file(tree) or _mentions_a_node_subprocess_route(tree)


def test_vm_context_entry_import_and_position_read_is_flagged() -> None:
    """Offender-injection self-test for this queue's blind-spot fix: a dummy file
 that never names a `ceres/static/*.js` string itself, but imports `ENTRY`
 from `vm_context` and reads it by position, must be caught (RED)."""
    src = (
        "from tests.ceres.vm_context import ENTRY\n\n"
        "def test_something():\n"
        "    source = ENTRY.read_text()\n"
        "    assert 'foo' in source\n"
    )
    tree = ast.parse(src)
    assert _imports_vm_context_entry(tree)
    assert _uses_vm_context_entry_by_position(tree)


def test_vm_context_whole_module_loader_only_is_not_flagged() -> None:
    """The honest exemption: a dummy file that only calls
 `run_in_module_context(...)` -- the sanctioned whole-module loader, no
 positional slicing -- must NOT be flagged (GREEN), even though it imports
 from `vm_context`."""
    src = (
        "from tests.ceres.vm_context import run_in_module_context\n\n"
        "def test_something():\n"
        "    ctx = run_in_module_context()\n"
        "    assert ctx['window'] is not None\n"
    )
    tree = ast.parse(src)
    assert not _imports_vm_context_entry(tree)
    assert not _uses_vm_context_entry_by_position(tree)


def _collect_offenders() -> set[str]:
    js_names = {p.name for p in (ROOT / "ceres" / "static").glob("*.js")}
    assert js_names, "no static JS found under ceres/static -- guard would silently pass"

    offenders: set[str] = set()
    for path in sorted((ROOT / "tests").rglob("*.py")):
        rel = str(path.relative_to(ROOT))
        if rel in ALLOWED:
            continue
        if _reads_a_static_js_file_by_position(path, js_names):
            offenders.add(rel)
    return offenders


def test_no_test_reads_a_static_js_file_by_source_position() -> None:
    """E2 (campaign plan §3, "The bottleneck, re-measured"): no file under
 tests/ may locate code in any ceres/static/*.js by string position -- name a
 static JS file AND read/exec it, in Python or via a Node subprocess. Walks
 every *.py under tests/, not just test_*.py (plan §2.2's correction), so
 tests/ceres/conftest.py -- exactly where a migrator under pressure would put
 a shared source read -- is visible.

 Two-directional (plan §2.3 G3 acceptance): a new offender not in BASELINE
 fails here (guards against a repointed test reappearing under a new
 spelling), and a BASELINE entry that no longer reads the JS file also fails
 (guards against the baseline silently going stale instead of shrinking on
 purpose, batch by batch, as Phase 1 migrates each file)."""
    offenders = _collect_offenders()

    new_offenders = offenders - BASELINE
    stale_baseline_entries = BASELINE - offenders
    assert not new_offenders and not stale_baseline_entries, (
        "ratchet mismatch -- see work/modularisation/TEST-CENSUS.md §2 and the "
        "campaign plan §2.2 for the migration/allowlisting rule.\n"
        f"  offenders not accounted for in BASELINE (new offender, or dropped "
        f"from BASELINE without migrating): {sorted(new_offenders)}\n"
        f"  BASELINE entries that no longer read a static JS file (migrated "
        f"remove from BASELINE): {sorted(stale_baseline_entries)}"
    )


# Updated by each Phase 1 batch: A->36, B->23 (8 converted + 5 ruled into ALLOWED
# incl. rows 1/9/34's (b) halves), C->11, D->3, kept->0.
# Batch A actual: only 4 files left BASELINE this batch (rows 5, 21, 32, 43
# test_bloodline_filter.py, test_runi_hazard.py, test_runi_tile_feedback.py
# test_worksite_change_art.py), not the 7 the ticket's corrected arithmetic (43-7=36)
# assumed. Three of the ticket's assumed departures did not happen, each for a
# reported reason, not an oversight
# row 20 (test_editor.py): one test's subject is dead code (STOP, ).
# row 42 (test_worker_upgrade_frontend.py): the whole render surface under test
# is dead code (STOP, ).
# row 34's node half (test_terrain_worksite_column.py) WAS converted, but the
# file stays in BASELINE regardless -- its (b) substring half is untouched by
# design (Batch B), so it still reads app.js by position either way. Row 1 is
# the same shape (converted, but stays for its own (b) half) -- the ticket's
# text already expected this one.
# row 9 (test_checkout_base_total_pp.py) was left untouched: this batch ran
# out of time to build its live-broadcast/checkout-staging replacements
# safely (see the batch report).
# (Batch A part 3): row 38 (test_verification_verdict_handling.py)
# converted fully to a live-browser pair driving the real checkout with a
# route-mocked /api/op-verify (see the file itself) -- leaves BASELINE. Row 42
# (test_worker_upgrade_frontend.py) and row 9's node half were NOT converted this
# ticket; see the ticket's own report for why (row 42: independently re-confirmed
# dead code, ; row 9: two of its four node assertions have no live-fixture
# equivalent on the 129-plot board). 39 - 1 = 38.
# queue F5: the working tree at the start of this queue already
# carried 22 BASELINE entries against this stale EXPECTED_BASELINE = 15 (a
# pre-existing off-by-one left by prior work on this same file, not this
# queue's doing -- reported as-is, not silently absorbed). This queue's own job
# was the five plan-§3.1/census-§2 rulings moving into ALLOWED: row 7
# (test_boundaries.py), row 23 (test_frontend_skins_and_init.py), row 24
# (test_idle_and_unassigned_sections_removed.py), row 29
# (test_overlay_backdrop_close.py), row 44 (test_worksite_modal_states.py).
# queue F7 (the final queue, the last two readers): row 22
# (test_frontend_removal_impact.py) and row 30 (test_power_slot_ui.py) both
# converted this queue -- see BASELINE's own comment above for what changed in
# each. 2 - 2 = 0.
# (2026-09-20): the ratchet's own detector (_collect_offenders) found 67
# current offenders with BASELINE at 0 -- new test files landed across the
# 2026-09-02..09-20 window (feature/regression coverage for S-/IR-/FR- work) that
# read a ceres/static/*.js file by position and were never triaged into ALLOWED or
# BASELINE, so the ratchet went silently stale rather than catching them one at a
# time. This pass re-populates BASELINE with that exact 67-file set (see BASELINE's
# own comment for the honesty note and the owed follow-up) rather than guessing
# which deserve ALLOWED's stronger per-file argument. 0 + 67 = 67.
# Post-merge (2026-09-20): master landed /0.9.693 while this branch was
# open, adding two more offenders (test_s14630_receipt_art.py
# test_live_measured_geometry.py) -- same backlog, added to BASELINE.
# (2026-09-20): BASELINE was missing test_splinter_row.py (landed
# ), failing the ratchet on master; also adds this branch's own new
# test_edge_resize_observer.py. 69 + 2 = 71.
# (2026-09-20): the ratchet itself was failing on master because
# test_modulepreload.py and test_routing_contracts.py (both landed after the
# pass above) were neither converted nor baselined. test_modulepreload.py's
# trip was a false positive (HTML-vs-generator comparison, not JS-as-text; see
# gen_modulepreload.py's new dynamic_only_import_targets()) and was converted, not
# baselined -- it does not enter BASELINE. test_routing_contracts.py is a genuine
# offender (see its own BASELINE comment) and enters BASELINE. 72 + 1 = 73.
# Merge with master (2026-09-21): master independently added its own new offender
# (test_harvest_modal_region_grid.py) to this same 72-file base while this branch
# added test_s14960_wallet_sign_pair.py -- two disjoint additions on top of 72.
# 72 + 1 (this branch) + 1 (master) = 74. The merged tree also carries two further
# offenders neither side's own BASELINE had accounted for yet
# (test_harvest_modal_side_by_side.py, test_tooltip_scale.py, both already present
# on master's tip) -- same bulk backlog, added here. 74 + 2 = 76.
# (2026-09-21): test_harvest_modal_side_by_side.py and
# test_tooltip_scale.py migrated off source-text reading onto the sanctioned
# whole-module VM harness; both leave BASELINE. 76 - 2 = 74.
EXPECTED_BASELINE = 74


def test_ratchet_baseline_is_the_census_43() -> None:
    """The ratchet's own progress meter (plan §2.2 item 2): BASELINE must be
 exactly EXPECTED_BASELINE, updated by each Phase 1 batch (see the constant's
 own comment for this batch's arithmetic and why it departs from the ticket's
 assumption). Before G4 deleted the dead `APP` constant in
 test_b3_slot_editor.py, the AST pass over-counted by one (census §1b: 49
 flagged, 48 genuine, 1 dead-constant false positive) -- the original 43 was
 43, not 44, only because that constant is gone."""
    assert len(BASELINE) == EXPECTED_BASELINE, len(BASELINE)
