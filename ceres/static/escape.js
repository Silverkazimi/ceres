// escape.js -- : the one HTML-escape helper, in a module with no imports, so the shared
// tooltip engine (tooltip.js) can run on pages that do not load the board's module graph
// (the card browser at /cards). format.js re-exports it unchanged for every board module.
export function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
