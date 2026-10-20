// the new-version banner's poller, shared by the board (index.html) and the Cards page (cards.html) so a deploy is
// announced on both. Moved verbatim from index.html's inline script; each page carries its own #reload-banner and #board-version.
// Layer-1 auto-reload poller, per Polls
// api/dev/watch every 1.5s. Resilient to transient errors (network blip
// brief 5xx during a systemd restart) -- only stops on HTTP 404 (no dev endpoint); : never on errors
// endpoint); : errors never stop it -- after 20 misses in a row it checks every 15 s.
// Banner gated on VERSION, not hash (the owner, 2026-08-21: "Make the version
// banner notification on top only appear when an actual version changed, not
// after each file change"). `hash` (ceres/routes_board.py:_dev_watch_hash())
// seeds an md5 with __version__ and then mixes in every watched file's own
// mtime, so it changes on ANY edit -- that's exactly right for the separate
// Layer-3 process self-restart (which still watches `hash` server-side
// unchanged), but wrong for interrupting a human: the process may restart
// freely, the banner should only fire when `version` itself changes. `hash`
// is still read off the response below (kept, in case a future poller
// feature needs it) but no longer drives the banner.
// with the dev loop off (the production server
// CERES_DEV_WATCH=0) the endpoint answers only its version, without scanning, so the page keeps polling once
// a minute and shows the same banner when a newer version is deployed. The page's own version tag is the
// baseline, so a deploy that lands before the first poll is not missed.
(function () {
  var lastVersion = null;
  var errors = 0;
  var delay = 1500;
  function pageVersion() {
    var tag = document.getElementById('board-version');
    var text = tag ? String(tag.textContent || '').trim().replace(/^v/, '') : '';
    return text && text.indexOf('{{') === -1 ? baseVersion(text) : null;
  }
  // a laptop checkout with uncommitted shipped edits serves "<release>+local.<hash>", and agent edits change that
  // hash every few seconds. The banner compares the RELEASE part only, so it fires on a real version change, never on an edit.
  function baseVersion(version) {
    return String(version || '').split('+')[0];
  }
  function poll() {
    fetch('/api/dev/watch').then(function (resp) {
      if (resp.status === 404) return null; // no dev endpoint on this server -- stop
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      errors = 0;
      return resp.json();
    }).then(function (body) {
      if (body === null) return; // 404 -- stop polling
      // server runs with CERES_DEV_WATCH=0 (the production server). : keep polling, once a minute, for the version only.
      delay = body.enabled === false ? 60000 : 1500;
      if (lastVersion === null) lastVersion = pageVersion();
      var served = baseVersion(body.version);
      if (lastVersion !== null && served !== lastVersion) {
        var banner = document.getElementById('reload-banner');
        // Names the new version explicitly (the owner, 2026-08-21, board-polish batch
        // 11: "v0.2.1 available -- click to reload" reads far better than a generic
        // "New version deployed", and makes the before/after explicit against the
        // stale version printed in the page header). `body.version` here is this
        // poll's fresh fetch -- the correct source for "what's newly available", in
        // contrast to the header's version, which deliberately does NOT re-fetch
        // (see index()'s server-side substitution in ceres/app.py).
        banner.textContent = 'v' + served + ' available — click to load it';
        banner.hidden = false;
        banner.onclick = function () { location.reload(); };
      }
      lastVersion = served;
      setTimeout(poll, delay);
    }).catch(function () {
      // never give up -- a restart can take longer than 30 s, and a page that stopped polling never showed
      // the banner again. After 20 misses in a row, keep checking every 15 s.
      errors += 1;
      setTimeout(poll, errors < 20 ? delay : 15000);
    });
  }
  poll();
}());
