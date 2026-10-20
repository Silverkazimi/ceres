"""Guards for the combine modal's outcome ledger and quantity control
(v0.7.73, work/combine-modal-redesign/redesign-plan.html) -- the block of pure
helpers in ceres/static/app.js between combinePpRate() and combineCardByUid()
(combinePpRate, combinePpAtBcx, combineLevelForBcx, combineCurrentSurvivor,
combineConsumptionOrder, combineAvailableCopies, combineQuantityForTargetBcx,
combinePresetQuantities, combineApplyQuantity, combineDefaultQuantity,
combineFreshnessNote, combineLedgerData).

: none of these guards have a rendered surface -- they assert
on numbers (`afterBcx`, `afterPp`) and on `COMBINE_SELECTION`'s contents, never
on DOM/innerHTML -- so per the campaign plan's method (census
`work/modularisation/TEST-CENSUS.md` row 13, §5: "pure helpers... no rendered
surface") this file loads the REAL, whole, unsliced module through the shared
`tests/ceres/vm_context.py` sandbox instead of a hand-`_slice`d subset, and
calls the module's own top-level functions/`COMBINE_SELECTION` directly --
the same real single-writer `const` app.js declares, not a per-test stand-in.
This retires the last `_slice()`-based marker matching in this file, which was
one stale-string-position failure away every time a function near these moved.

Mutation-tested per the internal rules's binding standard: each guard has its own
defect injected, watched red, and the injection recorded in a one-line
docstring note on the corresponding `*_mutation_is_red` test.
"""

from __future__ import annotations

import subprocess

import pytest

from .vm_context import ENTRY, read_entry_source, run_in_module_context

APP = ENTRY


# 1. The ledger's "after" figures are computed from the CURRENT selection, not
# echoed from a fixed baseline -- changing the quantity must change them.

_LEDGER_FIXTURE = r"""
const survivor = { uid: 'S', bcx: 10, base_pp: 1000, level: 3, bcx_max: 25, level_bcx_thresholds: [1, 5, 10, 15, 20, 25] };
const copyA = { uid: 'A', bcx: 6 };
const copyB = { uid: 'B', bcx: 4 };
const dialog = { copies: [survivor, copyA, copyB], blocked: new Map() };
"""

_LEDGER_RECOMPUTE_JS = _LEDGER_FIXTURE + r"""
combineApplyQuantity(dialog, 2); // survivor + largest (A, bcx 6) = 16
const afterAtTwo = combineLedgerData(dialog).afterBcx;
combineApplyQuantity(dialog, 3); // survivor + A + B = 20
const afterAtThree = combineLedgerData(dialog).afterBcx;
if (afterAtTwo === afterAtThree) throw new Error('afterBcx did not change with quantity (exit 60)');
if (afterAtTwo !== 16) throw new Error(`afterAtTwo expected 16, got ${afterAtTwo} (exit 61)`);
if (afterAtThree !== 20) throw new Error(`afterAtThree expected 20, got ${afterAtThree} (exit 62)`);
"""


def test_ledger_after_figures_track_the_selected_quantity() -> None:
    run_in_module_context(_LEDGER_RECOMPUTE_JS, entry=ENTRY)


def test_ledger_after_figures_mutation_is_red() -> None:
    """Injected defect: freeze afterBcx at the survivor's own current BCX,
 ignoring COMBINE_SELECTION entirely -- the two quantities would then report
 the identical afterBcx, and the guard above must go red."""
    source = read_entry_source(ENTRY)
    mutated = source.replace(
        "const afterBcx = selected.length\n ? selected.reduce((sum, c) => sum + (Number(c.bcx) || 0), 0)\n : beforeBcx;",
        "const afterBcx = beforeBcx;",
        1,
    )
    assert mutated != source, "combineLedgerData afterBcx mutation target is missing"
    with pytest.raises(subprocess.CalledProcessError):
        run_in_module_context(_LEDGER_RECOMPUTE_JS, entry=ENTRY, source=mutated)


# 2. PP never extrapolates past the printing's max-BCX ceiling (the internal rules).

_PP_CEILING_JS = r"""
survivor must stay the HIGHEST-bcx copy (combineCurrentSurvivor's own rule)
// so it is bcx 10 here and copyA is bcx 8 -- their sum (18) still clears the
// bcx_max ceiling of 15.
const survivor = { uid: 'S', bcx: 10, base_pp: 1000, level: 3, bcx_max: 15, level_bcx_thresholds: [1, 5, 10, 15] };
const copyA = { uid: 'A', bcx: 8 };
const dialog = { copies: [survivor, copyA], blocked: new Map() };
combineApplyQuantity(dialog, 2);
const ledger = combineLedgerData(dialog);
if (ledger.afterBcx !== 18) throw new Error(`afterBcx expected 18 (raw total, unclamped), got ${ledger.afterBcx} (exit 70)`);
if (ledger.afterPp !== 1500) throw new Error(`afterPp expected 1500 (rate x ceiling 15, not rate x raw 18), got ${ledger.afterPp} (exit 71)`);
"""


def test_pp_never_extrapolates_past_bcx_max() -> None:
    run_in_module_context(_PP_CEILING_JS, entry=ENTRY)


def test_pp_ceiling_mutation_is_red() -> None:
    """Injected defect: drop combinePpAtBcx's ceiling clamp (use the raw bcx
 directly instead of Math.min(bcx, bcxMax)) -- an overlevelling quantity
 then projects PP straight through the ceiling (rate x 30 = 3000 instead of
 the clamped rate x 15 = 1500), and the guard above must go red."""
    source = read_entry_source(ENTRY)
    mutated = source.replace(
        "const effective = bcxMax != null ? Math.min(bcx, bcxMax) : bcx;",
        "const effective = bcx;",
        1,
    )
    assert mutated != source, "combinePpAtBcx ceiling-clamp mutation target is missing"
    with pytest.raises(subprocess.CalledProcessError):
        run_in_module_context(_PP_CEILING_JS, entry=ENTRY, source=mutated)


# 3. Consumption order: largest non-survivor copies first, singles last
# (maintainer's ruling, 2026-08-30 -- liquidity, not size).

_CONSUMPTION_ORDER_JS = r"""
const survivor = { uid: 'S', bcx: 50 };
const big = { uid: 'BIG', bcx: 20 };
const single = { uid: 'SINGLE', bcx: 1 };
const dialog = { copies: [survivor, single, big], blocked: new Map() }; // deliberately NOT pre-sorted
combineApplyQuantity(dialog, 2); // survivor + exactly one more -- must be the largest, not the single
if (!COMBINE_SELECTION.has('BIG')) throw new Error('largest copy was not selected (exit 80)');
if (COMBINE_SELECTION.has('SINGLE')) throw new Error('single-CC copy was selected before the largest (exit 81)');
"""


def test_consumption_picks_largest_copy_before_the_single() -> None:
    run_in_module_context(_CONSUMPTION_ORDER_JS, entry=ENTRY)


def test_consumption_order_mutation_is_red() -> None:
    """Injected defect: reverse the sort direction in combineConsumptionOrder
 (ascending instead of descending BCX) -- the single-CC copy would then be
 consumed before the large one, exactly the liquidity mistake the maintainer's
 ruling forbids, and the guard above must go red."""
    source = read_entry_source(ENTRY)
    mutated = source.replace(
        ".sort((a, b) => (Number(b.bcx) || 0) - (Number(a.bcx) || 0)",
        ".sort((a, b) => (Number(a.bcx) || 0) - (Number(b.bcx) || 0)",
        1,
    )
    assert mutated != source, "combineConsumptionOrder sort-direction mutation target is missing"
    with pytest.raises(subprocess.CalledProcessError):
        run_in_module_context(_CONSUMPTION_ORDER_JS, entry=ENTRY, source=mutated)


# 4. Every destroyed Card ID reaches the op payload (COMBINE_SELECTION, sent
# verbatim as /api/combine/build's `uids`), even for a 400+-copy printing.
# Census §5 (row 13, test 4) labels this HARD and rules a vm-loaded unit test
# over a synthetic 401-copy collection acceptable: the live board cannot
# supply a fixture this large (the sanctioned live fixture
# tests/fixtures/land/collection_sample.json, has group (505, 2, False) with
# only 3 tied copies for the peel/tie-break arms -- nowhere near 400). This
# guard stays a synthetic-fixture vm test for that reason, not a live one.

_LARGE_SELECTION_JS = r"""
const survivor = { uid: 'SURV', bcx: 1000 };
const copies = [survivor];
for (let i = 0; i < 400; i += 1) copies.push({ uid: `C-${i}`, bcx: 1 });
const dialog = { copies, blocked: new Map() };
combineApplyQuantity(dialog, 401); // "Everything" -- every one of the 400 duplicates
if (COMBINE_SELECTION.size !== 401) throw new Error(`expected 401 selected uids, got ${COMBINE_SELECTION.size} (exit 90)`);
for (let i = 0; i < 400; i += 1) {
 if (!COMBINE_SELECTION.has(`C-${i}`)) throw new Error(`C-${i} missing from selection (exit 91)`);
}
if (!COMBINE_SELECTION.has('SURV')) throw new Error('survivor missing from selection (exit 92)');
"""


def test_large_selection_carries_every_destroyed_uid() -> None:
    run_in_module_context(_LARGE_SELECTION_JS, entry=ENTRY)


def test_large_selection_mutation_is_red() -> None:
    """Injected defect: cap the consumed slice at 50 copies regardless of the
 requested quantity (`.slice(0, clamped - 1)` -> `.slice(0, Math.min(clamped - 1, 50))`)
 -- a 401-copy combine would then silently drop 350 Card IDs from the op
 payload, and the guard above must go red."""
    source = read_entry_source(ENTRY)
    mutated = source.replace(
        "combineConsumptionOrder(available, String(survivor.uid || survivor.asset_ref)).slice(0, clamped - 1);",
        "combineConsumptionOrder(available, String(survivor.uid || survivor.asset_ref)).slice(0, Math.min(clamped - 1, 50));",
        1,
    )
    assert mutated != source, "large-selection slice-cap mutation target is missing"
    with pytest.raises(subprocess.CalledProcessError):
        run_in_module_context(_LARGE_SELECTION_JS, entry=ENTRY, source=mutated)
