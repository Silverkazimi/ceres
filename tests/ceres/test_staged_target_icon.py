from pathlib import Path

from tests.ceres.vm_context import run_in_module_context


ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ceres" / "static"
PLOT_LOCK = STATIC / "plot-lock.js"


def test_plot_lock_surfaces_render_staged_target_and_current_state() -> None:
    run_in_module_context(
        r"""
const open = 'M8 10V6.8a4 4 0 0 0-7.1-2.5L.3 5';
const closed = 'M8 10V7a4 4 0 0 1 8 0v3';
const locked = { deed_uid: 'I-1-aaaaaaaa', plot: '1-1-1', lock_days: 3, unlock_date: null };
const unlocked = { deed_uid: 'I-2-bbbbbbbb', plot: '1-1-2', lock_days: null, unlock_date: null };
reconcilePlotLockPlans({ player: 'alice', plots: [locked, unlocked] });

const lockedCurrent = plotLockIconHtml(locked);
if (!lockedCurrent.includes(closed) || !lockedCurrent.includes('aria-label="Locked')) throw Error('unstaged locked plot did not show its current closed glyph');
if (!togglePlotLock(locked)) throw Error('could not stage unlock');
const stagedUnlock = plotLockIconHtml(locked);
const stagedUnlockButton = plotLockIconHtml(locked, { button: true });
if (!stagedUnlock.includes(open) || !stagedUnlock.includes('aria-label="Unlock staged"')) throw Error('staged unlock did not show the open glyph and label');
if (!stagedUnlock.includes('plot-lock-staged')) throw Error('staged unlock cell is not the orange pulsing class ()');
if (!stagedUnlockButton.includes(open) || !stagedUnlockButton.includes('aria-label="Unlock staged; click to undo for plot')) throw Error('plot view staged unlock differs from the board target');
if (!stagedUnlockButton.includes('data-tip="Unlock staged"') || !stagedUnlockButton.includes('is-staged')) throw Error('staged unlock tooltip or pulse is missing');
if (!togglePlotLock(locked)) throw Error('could not undo staged unlock');
if (STAGED_PLOT_LOCKS.has(locked.deed_uid) || !plotLockIconHtml(locked).includes(closed)) throw Error('undo did not restore current locked state');

if (!togglePlotLock(unlocked)) throw Error('could not stage lock');
const stagedLock = plotLockIconHtml(unlocked);
const stagedLockButton = plotLockIconHtml(unlocked, { button: true });
if (!stagedLock.includes(closed) || !stagedLock.includes('aria-label="Lock staged"')) throw Error('staged lock did not show the closed glyph and label');
if (!stagedLock.includes('plot-lock-staged')) throw Error('staged lock cell is not the orange pulsing class ()');
// signed (txId): the orange pulse is gone, the cell shows the current state again
STAGED_PLOT_LOCKS.get(unlocked.deed_uid).txId = 'a'.repeat(40);
const signedCell = plotLockIconHtml(unlocked);
if (signedCell.includes('plot-lock-staged') || !signedCell.includes(open)) throw Error('after signing the cell must show the current state without the orange pulse');
delete STAGED_PLOT_LOCKS.get(unlocked.deed_uid).txId;
if (!stagedLockButton.includes(closed) || !stagedLockButton.includes('aria-label="Lock staged; click to undo for plot')) throw Error('plot view staged lock differs from the board target');
if (!stagedLockButton.includes('data-tip="Lock staged"') || !stagedLockButton.includes('is-staged')) throw Error('staged lock tooltip or pulse is missing');
if (!togglePlotLock(unlocked)) throw Error('could not undo staged lock');
if (STAGED_PLOT_LOCKS.has(unlocked.deed_uid) || !plotLockIconHtml(unlocked).includes(open)) throw Error('undo did not restore current unlocked state');
process.exit(0);
""",
        entry=PLOT_LOCK,
    )

    picker = (STATIC / "picker-actions.js").read_text()
    assert picker.count("updateMountedRow(row.deed_uid);") >= 2
    assert "if (row && togglePlotLock(row)) { updateMountedRow(row.deed_uid); renderCheckoutSurface(); }" in picker
    assert "if (!togglePlotLock(row)) throw new Error('This signed change cannot be undone.');\n updateMountedRow(row.deed_uid);" in picker
