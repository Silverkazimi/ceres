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

// return to a source plot, using the shared 24px stroke geometry.
export const RETURN_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2"><path d="m8 4-5 5 5 5M3 9h11a7 7 0 0 1 0 14" transform="translate(0 -2)"/></svg>';
