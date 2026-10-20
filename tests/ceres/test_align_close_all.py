""" close/open-all alignment compares viewport rects directly (no page zoom)."""

from __future__ import annotations

from .vm_context import STATIC, read_entry_source, run_in_module_context


ROWS_SOURCE = (STATIC / "rows.js").read_text(encoding="utf-8")
ALIGN_SOURCE = (
    "function alignCloseAll()"
    + ROWS_SOURCE.split("export function alignCloseAll()", 1)[1].split(
        "\n// the plots the board shows right now", 1
    )[0]
)
ALIGN_MODULE_SOURCE = read_entry_source(STATIC / "scale.js") + "\n" + ALIGN_SOURCE


def test_align_close_all_uses_plain_viewport_geometry() -> None:
    run_in_module_context(
        r"""
getComputedStyle = () => ({ fontSize: '20px' });
const buttons = {
 'close-all-plots': { hidden: false, style: {}, getBoundingClientRect: () => ({ right: 300 }) },
 'open-all-plots': { hidden: false, style: {}, getBoundingClientRect: () => ({ right: 300 }) },
};
const table = { getBoundingClientRect: () => ({ right: 400 }) };
const close = { getBoundingClientRect: () => ({ right: 240 }) };
document.getElementById = (id) => buttons[id] || (id === 'plot-table' ? table : null);
document.querySelectorAll = () => [close];
alignCloseAll();
if (buttons['close-all-plots'].style.marginRight !== '60px') throw new Error('close-all does not align to the rightmost plot Close');
if (buttons['open-all-plots'].style.marginRight !== '60px') throw new Error('open-all does not align to the rightmost plot Close');

document.querySelectorAll = () => [];
alignCloseAll();
if (buttons['close-all-plots'].style.marginRight !== '-84px') throw new Error('close-all fallback is not 0.8rem inside the table edge');
if (buttons['open-all-plots'].style.marginRight !== '-84px') throw new Error('open-all fallback is not 0.8rem inside the table edge');
""",
        entry=STATIC / "rows.js",
        source=ALIGN_MODULE_SOURCE,
    )
