// (maintainer: "make cards and plots buttons a switcher ... create fitting svg icons for all new menu buttons and replace
// with them the current text"; : accent, glass, no blur). The one module for the header switcher's icons, loaded as a
// plain (blocking) script right after the header on index.html and cards.html, so the icons are there at first paint and the
// buttons never change size (L-6). The artwork keeps the crafted two-tone header geometry (, I-3) -- the same drawings
// the old Plots and Cards buttons carried -- and is painted from tokens by buttons.css (body in currentColor on glass).
// It also gives both halves their links: the other page for the same ?player= (the page's own half is aria-current and does
// nothing when pressed). The board keeps its own click handler for the player now on screen (app.js).
(function () {
  'use strict';
  var ICONS = {
    plots: '<svg class="hdr-ic" viewBox="0 0 24 24" aria-hidden="true"><path class="f" d="M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z"/><path class="d" d="M10 6.5h4M6.5 10v4M17.5 10v4M10 17.5h4"/></svg>',
    cards: '<svg class="hdr-ic" viewBox="0 0 24 24" aria-hidden="true"><path class="a" d="M3.2 6.4 9.6 4.6l3.9 14.2-6.4 1.8z"/><path class="f" d="M9.4 3.4h10.4v17.2H9.4z"/><path class="d" d="M12 7.6h5.2M12 10.8h5.2M12 14h3.4"/></svg>',
    myCards: '<svg class="hdr-ic" viewBox="0 0 24 24" aria-hidden="true"><path class="a" d="M3.5 7.2 14.8 4.3l2.1 8.2"/><path class="d" d="M5.6 9.4 16.9 6.5l2.1 8.2"/><path class="f" d="M4.2 9.1h13.3v11.5H4.2z"/><path class="d" d="M7 12.2h7.7M7 15.1h7.7M7 18h5"/></svg>',
    market: '<svg class="hdr-ic" viewBox="0 0 24 24" aria-hidden="true"><path class="d" d="M4 10.1v10.2h16V10.1M2.8 9.8l2-5h14.4l2 5"/><path class="f" d="M2.8 9.8c.4 2.5 4 2.5 4.4 0 .4 2.5 4 2.5 4.4 0 .4 2.5 4 2.5 4.4 0 .4 2.5 4 2.4 4.4 0"/><path class="a" d="M9.2 20.3v-5.6h5.6v5.6"/></svg>',
    purchases: '<svg class="hdr-ic" viewBox="0 0 24 24" aria-hidden="true"><path class="f" d="M5 2.8h14v18.4l-2.8-1.7-2.2 1.7-2-1.7-2 1.7-2.2-1.7L5 21.2z"/><path class="d" d="M8 7h8M8 10.5h8M8 14h5"/><path class="a" d="M15.8 14h2.1"/></svg>'
  };

  function fill(root) {
    var slots = (root || document).querySelectorAll('[data-header-icon]');
    for (var i = 0; i < slots.length; i += 1) {
      var svg = ICONS[slots[i].getAttribute('data-header-icon')];
      if (svg && !slots[i].querySelector('svg')) slots[i].insertAdjacentHTML('afterbegin', svg);
    }
  }

  function pageUrl(path, player) {
    return player ? path + '?player=' + encodeURIComponent(player) : path;
  }

  function wireSwitch(player) {
    var plots = document.getElementById('page-switch-plots');
    var cards = document.getElementById('page-switch-cards');
    if (plots) plots.setAttribute('href', pageUrl('/', player));
    if (cards) cards.setAttribute('href', pageUrl('/cards', player));
  }

  function currentPlayer() {
    try { return (new URLSearchParams(location.search).get('player') || '').trim().toLowerCase(); } catch (_) { return ''; }
  }

  fill(document);
  wireSwitch(currentPlayer());
  // The page's own half stays put: pressing it is not a navigation.
  document.addEventListener('click', function (event) {
    var half = event.target && event.target.closest ? event.target.closest('.ceres-page-switch > [aria-current="page"]') : null;
    if (half) event.preventDefault();
  });
  window.ceresHeaderIcons = { ICONS: ICONS, fill: fill, wireSwitch: wireSwitch };
}());
