"""Static contract for the Lock column markup and sizing."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = (ROOT / "ceres/static/index.html").read_text()
CSS = (ROOT / "ceres/static/ceres.css").read_text()
LOCK = (ROOT / "ceres/static/plot-lock.js").read_text()


def test_lock_header_uses_cell_glyph_family_and_shared_sort_markup() -> None:
    header = re.search(r'<th[^>]*data-key="lock_state"[^>]*>.*?</th>', INDEX, re.S)
    neighbor = re.search(r'<th[^>]*data-key="dec_needed"[^>]*>.*?</th>', INDEX, re.S)
    assert header and neighbor
    lock_header = header.group(0)
    assert 'export function plotLockSvg(locked)' in LOCK
    assert 'if (slot) slot.innerHTML = plotLockSvg(true);' in LOCK
    assert 'const icon = plotLockSvg(state.locked);' in LOCK
    assert 'return `<span class="plot-lock-icon"' in LOCK
    assert '>${icon}</span>`' in LOCK
    assert '>${icon}</button>`' in LOCK
    assert 'initializePlotLockHeaderIcon' in LOCK
    assert "import { initializePlotLockHeaderIcon } from './plot-lock.js';" in (ROOT / "ceres/static/app.js").read_text()
    assert '<span class="plot-lock-icon" aria-hidden="true"></span>' in lock_header
    sort_markup = '<span class="sort-chevron" aria-hidden="true"></span>'
    assert sort_markup in lock_header
    assert sort_markup in neighbor.group(0)
    assert lock_header.index('class="plot-lock-icon"') < lock_header.index(sort_markup)
    assert 'aria-label="Deed lock status"' in lock_header


def test_lock_track_has_no_fixed_width_and_keeps_equal_gap_budget() -> None:
    for selector, body in re.findall(r'([^{}]+)\{([^{}]*)\}', CSS):
        if '.plot-lock-icon' in selector or '.sort-chevron' in selector:
            continue
        if any(token in selector for token in ('.col-w-lock', 'data-key="lock_state"', '.lock-cell')):
            assert not re.search(r'(?<!min-)inline-size\s*:', body), selector
            assert not re.search(r'(?<!min-)width\s*:', body), selector
    assert 'padding-inline: clamp(.25rem, .3125cqi, .375rem);' in CSS
    assert 'padding-inline-end: calc(var(--table-end-reserve) + clamp(.25rem, .3125cqi, .375rem));' in CSS
    assert 'justify-content: flex-start;' in CSS
    assert 'th[data-key="lock_state"] .sort-chevron' not in CSS


def test_plot_lock_helper_emits_owner_pair_b_paths() -> None:
    closed = "M8 10V7a4 4 0 0 1 8 0v3"
    opened = "M8 10V6.8a4 4 0 0 0-7.1-2.5L.3 5"
    body = "M6 10h12a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2z"
    keyhole = "M12 15v2"
    svg = LOCK.split("export function plotLockSvg(locked)", 1)[1].split("\n}", 1)[0]
    assert f"? '{closed}'" in svg
    assert f"'{opened}'" in svg
    assert f'<path d="{body}"/>' in svg
    assert '<path d="${shackle}"/>' in svg
    assert f'<path d="{keyhole}"/>' in svg
    assert 'viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"' in svg
    assert closed != opened
