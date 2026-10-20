// Ceres has no page zoom any more (the root CSS zoom, its content-width
// cache and its factor are gone -- index.html's pre-paint block only publishes
// the board width and the width classes). Viewport rects and CSS lengths are
// one coordinate space again, so nothing here converts between them.

if (new URLSearchParams(location.search).get('diag') === 'width') {
  import('./diag-width.js');
}

// The ONE published "available width" for the components that still need a
// width in JS: the opened-plot geometry and its tile pager (rows.js
// geometrySnapshot()). It is the table scroller's own width, or -- when the
// table is hidden (the phone card view) -- #board-main's content box, so a
// mounted page never reports 0 (). The table itself needs no JS width
// it is sized by CSS (ceres.css `table.plot-table`).
export function availableTableWidth() {
  const scroller = document.getElementById('plot-table-scroll');
  if (scroller && scroller.clientWidth > 0) return scroller.clientWidth;
  const main = document.getElementById('board-main');
  const style = main ? getComputedStyle(main) : null;
  const pad = style ? (parseFloat(style.paddingLeft) || 0) + (parseFloat(style.paddingRight) || 0) : 16;
  const layout = main && main.clientWidth > 0 ? main.clientWidth : document.documentElement.clientWidth;
  return Math.max(0, layout - pad);
}
