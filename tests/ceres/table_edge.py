"""The desktop plot table's deliberate right-edge overhang, shared by the width guards.

ceres.css (pinned as source text by test_table_under_bar.py and test_plot_lock.py):

    html:not(.ceres-lw-le-1279) #plot-table-scroll > table.plot-table {
      margin-inline: -0.5px;
      inline-size: calc(100% + 8.5px);
    }

The two columns added after DEC stake (Lock, the listed-plot counter) take an 8px
budget beyond the scroller so the existing columns keep their measured widths. The
table box is therefore 8.5px wider than its scroller's client width and, starting
0.5px left of it, runs 8px past the scroller's right edge; `#plot-table-scroll`
clips on x (no scrollbar), and that strip lies inside the last column's
`--table-end-reserve` padding (the space under the badge bar), so no content is
hidden. The guards that used to require "table == scroller, nothing past it" now
require exactly this: the overhang is at most the budget, and it is narrower than
the last visible column's end padding, so only reserve padding is clipped.
"""

from __future__ import annotations

# scroller.scrollWidth - scroller.clientWidth (the clipped strip, whole px).
TABLE_OVERHANG_PX = 8
# table box width - scroller client width.
TABLE_EXTRA_WIDTH_PX = 8.5

# The clipped strip and the last visible column's end padding, in CSS px. A
# hidden table (card view) reports zeros.
OVERHANG_JS = """() => {
  const scroller = document.getElementById('plot-table-scroll');
  const table = document.getElementById('plot-table');
  if (!scroller || !table || getComputedStyle(scroller).display === 'none') {
    return { visible: false, overhang: 0, extra: 0, endPadding: 0, contentPastEdge: 0 };
  }
  const visibleLast = (cells) => [...cells].reverse().find(el => getComputedStyle(el).display !== 'none');
  const ends = [];
  const head = table.querySelector('thead tr');
  if (head) ends.push(visibleLast(head.children));
  for (const tr of table.querySelectorAll('tbody tr.plot-row')) ends.push(visibleLast(tr.children));
  const edge = scroller.getBoundingClientRect().right;
  let endPadding = Infinity, contentPastEdge = 0;
  for (const cell of ends.filter(Boolean)) {
    const cs = getComputedStyle(cell);
    endPadding = Math.min(endPadding, parseFloat(cs.paddingRight) || 0);
    // The cell's content box must end inside the scroller: only padding is clipped.
    const contentRight = cell.getBoundingClientRect().right - (parseFloat(cs.paddingRight) || 0)
      - (parseFloat(cs.borderRightWidth) || 0);
    contentPastEdge = Math.max(contentPastEdge, contentRight - edge);
  }
  return {
    visible: true,
    overhang: scroller.scrollWidth - scroller.clientWidth,
    extra: table.getBoundingClientRect().width - scroller.clientWidth,
    endPadding: endPadding === Infinity ? 0 : endPadding,
    contentPastEdge,
  };
}"""


def overhang_problems(got: dict) -> list[str]:
    """Failures for an OVERHANG_JS result: empty when the table's overhang is the
    deliberate budget (or less) and lies wholly in the last column's end padding."""
    if not got.get("visible"):
        return []
    problems = []
    if got["overhang"] > TABLE_OVERHANG_PX + 1:
        problems.append(f"table overhangs its clipping scroller by {got['overhang']}px (budget {TABLE_OVERHANG_PX}px)")
    if got["overhang"] > 0 and got["overhang"] >= got["endPadding"]:
        problems.append(f"the {got['overhang']}px overhang is wider than the last column's end padding {got['endPadding']:.1f}px")
    if got["contentPastEdge"] > 0.5:
        problems.append(f"last-column content runs {got['contentPastEdge']:.1f}px past the scroller's clip edge")
    return problems


def assert_overhang_is_reserve(page) -> dict:
    got = page.evaluate(OVERHANG_JS)
    problems = overhang_problems(got)
    assert not problems, f"{problems}: {got}"
    return got
