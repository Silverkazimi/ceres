// (maintainer IN-0858: "when on ceres the card picker opens, the top of the card tiles from the plot view must move
// to the top of the page. Like we do on echo"). Echo Reapers docks the tile row itself at the top of the scroller when
// its inline picker opens (ui/worksite-actions.js rowTopY/rowTop/scrollTo, /): the highest painted point
// of the tile row, so a Power Core badge reaching above the tiles is not cut off. This file holds the pure geometry
// table.js and picker-actions.js do the scrolling. No imports.

// Highest painted top (viewport coordinates) of the plot's slot tiles and Power Core sockets inside `host`, or null.
export function pickerTilesTop(host) {
  if (!host?.querySelectorAll) return null;
  let top = null;
  for (const node of host.querySelectorAll('.editor-band .slot-tile, .editor-band .power-satellite')) {
    const rect = node.getBoundingClientRect();
    if (rect.height > 0 && rect.width > 0 && (top === null || rect.top < top)) top = rect.top;
  }
  return top;
}

// Scroll offset that puts tiles at `line` (viewport y of the first free pixel under the sticky header, or the
// scroller's own top edge). Null when there are no tiles.
export function tilesScrollGoal(scroll, tilesTop, line) {
  if (!Number.isFinite(tilesTop)) return null;
  return Math.max(0, scroll + tilesTop - line);
}

// Extra room (px) a scroller of `viewport` height needs below its content so `goal` is reachable.
export function missingScrollRoom(goal, viewport, scrollHeight) {
  return Math.max(0, Math.ceil(goal + viewport - scrollHeight));
}
