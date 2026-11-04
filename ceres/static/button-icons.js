const ICONS = {
  "changes": "<svg class=\"hdr-ic\" viewBox=\"0 0 24 24\" aria-hidden=\"true\"><path class=\"f\" d=\"M2.8 4h11.4v6.2l-4 6.6-.6 4.2H2.8z\"/><path class=\"d\" d=\"M5.6 7.6h5.8M5.6 10.8h3.6M5.2 17.4c.9-1.3 1.7.9 2.6-.1\"/><path class=\"a\" d=\"M10.4 20.2c.4-7.2 4.8-14 12.2-18.4-.4 7.8-5 14.2-12.2 18.4z\"/><path class=\"d\" d=\"M11.8 18.2 19.8 6.2\"/></svg>",
  "harvest": "<svg class=\"hdr-ic\" viewBox=\"0 0 24 24\" aria-hidden=\"true\"><path class=\"f\" d=\"M9 15.8C5.4 11.6 7 4.7 13.5 3.3c4.7-1 8 2.2 7.2 6.1-1.4-2.4-4-3.3-6.5-2.5-3.6 1.1-5 5-3.5 8.3z\"/><path class=\"d\" d=\"M10.9 13.6c-.9-2.9.7-6.2 3.6-7.1\"/><path class=\"a h\" d=\"M9.4 15.3 3.6 21.1\"/></svg>",
  "search": "<svg viewBox=\"0 0 24 24\" aria-hidden=\"true\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\"><circle cx=\"10\" cy=\"10\" r=\"6\"/><path d=\"m15 15 6 6\"/></svg>",
  "filters": "<svg viewBox=\"0 0 24 24\" aria-hidden=\"true\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"2\"><path d=\"M3 6h18M6 12h12M9 18h6\"/></svg>"
};

export const COMMIT_ICON = `<span class="ceres-btn__icon" aria-hidden="true">${ICONS.changes}</span>`;

export function initializeButtonIcons() {
  for (const slot of document.querySelectorAll("[data-button-icon]")) {
    const svg = ICONS[slot.dataset.buttonIcon];
    if (svg) slot.innerHTML = svg;
  }
}

// one line glyph per card-menu action, in the shared 24px stroke geometry of RETURN_ICON below
// (viewBox 0 0 24 24, no fill, currentColor, stroke-width 2, default caps).
const LINE = (body) => `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2">${body}</svg>`;
export const CARD_ACTION_ICONS = {
  "card-stake": LINE('<path d="M4 14v6h16v-6M12 3v12M8 11l4 4 4-4"/>'),                                  // arrow into a tray
  "card-unstake": LINE('<path d="M4 14v6h16v-6M12 15V3M8 7l4-4 4 4"/>'),                                  // arrow out of a tray
  "card-plot": LINE('<path d="M12 22s-7-6.2-7-12a7 7 0 0 1 14 0c0 5.8-7 12-7 12z"/><circle cx="12" cy="10" r="2.5"/>'), // map pin
  "card-delegate": LINE('<circle cx="18" cy="7" r="3"/><path d="M13 21v-1a5 5 0 0 1 10 0v1M1 13h9M7 10l3 3-3 3"/>'),   // arrow to a person
  "card-recall": LINE('<circle cx="18" cy="7" r="3"/><path d="M13 21v-1a5 5 0 0 1 10 0v1M11 17H8a4 4 0 0 1-4-4V6M1 9l3-3 3 3"/>'), // curved arrow back from a person
  "card-return": LINE('<rect x="11" y="3" width="10" height="15" rx="1.5"/><path d="M11 14H2M5 11l-3 3 3 3"/>'),        // card with a back arrow
  "card-transfer": LINE('<path d="M3 8h17M16 4l4 4-4 4M21 16H4M8 12l-4 4 4 4"/>'),                       // two opposed arrows
  "card-burn": LINE('<path d="M12 22c4 0 7-2.8 7-6.6 0-3.1-1.7-5.3-3.7-7.3 0 2-.9 3.3-2.1 3.8C13.6 8 12.1 5 9 2c.3 3.5-1.5 5.6-3 7.4C4.8 11 5 13 5 15.4 5 19.2 8 22 12 22z"/><path d="M12 22c-1.7 0-3-1.2-3-3 0-1.6 1.3-2.7 3-4.5 1.7 1.8 3 2.9 3 4.5 0 1.8-1.3 3-3 3z"/>'), // flame
};

// return to a source plot, using the shared 24px stroke geometry.
export const RETURN_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2"><path d="m8 4-5 5 5 5M3 9h11a7 7 0 0 1 0 14" transform="translate(0 -2)"/></svg>';
