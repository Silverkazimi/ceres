"""Static guards for the  lock checkout and visual contract."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ceres/static"
CHECKOUT = (STATIC / "checkout.js").read_text()
PICKER = (STATIC / "picker-actions.js").read_text()
LOCK = (STATIC / "plot-lock.js").read_text()
CSS = (STATIC / "ceres.css").read_text()


def test_lock_review_button_and_handler_are_gone() -> None:
    plan = CHECKOUT.split("function plotLockPlanHtml", 1)[1].split("// inside the modal", 1)[0]
    assert "Review exact operation" not in plan
    assert 'data-checkout-action="lock-review"' not in plan
    assert "lock-review" not in PICKER
    assert "preparePlotLockReview(row)" in LOCK
    assert "reviewedActiveLock: witness" in LOCK


def test_lock_is_counted_and_can_reach_active_keychain_signing() -> None:
    assert "readyLocks = [...STAGED_PLOT_LOCKS.values()].filter(entry => !entry.txId && !entry.blockedReason)" in CHECKOUT
    assert "!stagedBuilds && readyLocks.length === 0" in CHECKOUT
    assert "stagedBuilds || readyLocks.length > 0" in CHECKOUT
    assert "const signatureCount = postingSignatures + readyLocks.length" in CHECKOUT
    assert "readyLocks.length ? `${readyLocks.length} Active (deed ${readyLocks.length === 1 ? readyLocks[0].action : 'locks/unlocks'})` : ''" in CHECKOUT
    assert 'data-checkout-action="sign-worksites"' in CHECKOUT
    assert "await signPlotLockReview(fresh)" in CHECKOUT
    assert "hasStagedWorksiteBuilds() || [...STAGED_PLOT_LOCKS.values()]" in PICKER
    assert "'Active', { reviewedActiveLock: witness }" in LOCK
    assert "'Active', review.prepared" in LOCK


def test_hiveauth_lock_guidance_is_only_in_signing_section() -> None:
    plan = CHECKOUT.split("function plotLockPlanHtml", 1)[1].split("// inside the modal", 1)[0]
    signing = CHECKOUT.split("const lockSigningHtml", 1)[1].split("const signingCard", 1)[0]
    assert "Locking needs Hive Keychain (Active key)" not in plan
    assert "Locking needs Hive Keychain (Active key)" in signing


def test_pp_total_is_absent_without_a_pp_stage() -> None:
    assert "const totalDeltas = [...groups.values()].filter((group) => group.items.length)" in CHECKOUT
    summary_at = CHECKOUT.index("${summary}")
    header_start = CHECKOUT.rfind("<header", 0, summary_at)
    header = CHECKOUT[header_start:CHECKOUT.index("</header>", header_start)]
    assert "${summary}" in header
    summary_line = next(line for line in CHECKOUT.splitlines() if line.lstrip().startswith("const summary ="))
    assert "totalDelta !== null" in summary_line
    assert "const summary = totalDelta !== null ? `Total <span" in summary_line
    assert "<p>${summary}</p>" in CHECKOUT


def test_plot_pipe_uses_one_symmetric_gap_token() -> None:
    assert "--plot-control-pipe-gap: .7rem" in CSS
    pipe = re.search(r"\.slot-group-controls > \.editor-splinter-affinity-sep\s*\{([^}]*)\}", CSS)
    assert pipe
    assert "margin-inline: calc(var(--plot-control-pipe-gap) - .25rem)" in pipe.group(1)
    assert "gap: .25rem" in CSS


def test_plot_button_icon_fills_button_without_box_growth() -> None:
    button = re.search(r"(?m)^\.plot-lock-button\s*\{([^}]*)\}", CSS)
    icon = re.search(r"(?m)^\.plot-lock-button svg\s*\{([^}]*)\}", CSS)
    cell = re.search(r"(?m)^table\.plot-table td\.lock-cell \.plot-lock-icon svg\s*\{([^}]*)\}", CSS)
    assert button and icon and cell
    assert "inline-size: var(--ceres-control-min-block, 1.875rem)" in button.group(1)
    assert "block-size: var(--ceres-control-min-block, 1.875rem)" in button.group(1)
    assert "padding: .1375rem" in button.group(1)
    assert "inline-size: 1.36rem" in icon.group(1)
    assert "block-size: 1.36rem" in icon.group(1)
    assert "max-inline-size: 100%" in icon.group(1)
    assert "max-block-size: 100%" in icon.group(1)
    assert "inline-size: 1.1rem" in cell.group(1)
    assert "block-size: 1.1rem" in cell.group(1)
    button_size = 1.875
    padding = .1375
    icon_size = 1.6
    assert icon_size <= button_size - 2 * padding


def test_plot_lock_helper_emits_owner_pair_b_paths() -> None:
    closed = "M8 10V7a4 4 0 0 1 8 0v3"
    opened = "M8 10V6.8a4 4 0 0 0-7.1-2.5L.3 5"
    body = "M6 10h12a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2z"
    keyhole = "M12 15v2"
    svg = LOCK.split("export function plotLockSvg(locked)", 1)[1].split("\n}", 1)[0]
    assert f"? '{closed}'" in svg
    assert f": '{opened}'" in svg
    assert f'<path d="{body}"/>' in svg
    assert '<path d="${shackle}"/>' in svg
    assert f'<path d="{keyhole}"/>' in svg
    assert 'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"' in svg
    assert closed != opened
