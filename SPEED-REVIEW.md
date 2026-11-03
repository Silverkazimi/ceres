# Ceres speed review

Tree reviewed: `check/ir9547-runner-pin` @ `42349e0` (served version 1.0.1). This is analysis only: no product code was changed. The measurement scripts are in `speed-review/`. Each one runs from the repository root against the recorded fixtures (`tests/fixtures/upstream/board-basic`, player `sample01`, 40 plots) and never touches a live game API.

The words *measured*, *code reading* and *estimated* mark how each claim was established. "400 plots" means the fixture board cloned 10× in the browser (`SCALE=10`, a Playwright route on `/api/board`). It stands in for a large account; it is not a recorded whale.

---

## 1. Top 15, ranked by gain × effort

| # | Item | Level | Gain | Effort | Evidence |
|---|------|-------|------|--------|----------|
| 1 | Remove the document-wide style invalidation from `:has()` rules. Swap the mobile-tier and `html`/`body`-anchored `:has()` selectors for state classes, or disable the mobile-tier rules at desktop width. | 2 | **Very high.** Staging one worker costs 339 ms of style recalc at 40 plots and **1.9 s at 400 plots**. Opening a plot costs 158 ms and 1.8 s. With the anchored `:has()` rules removed, these drop to 191 ms and 199 ms; with all `:has()` removed, to 62 ms and 54 ms. | M | measured |
| 2 | Rebalance CI shards using per-test durations recorded per browser set. | 8 | **High.** The required core job's 20 shards ran 25–521 s (mean 195 s, max/mean 2.7). The slowest shard sets the 9.5 min wall time; balanced shards would take about 4.5 min. | S | measured (CI run 41700323211) |
| 3 | Give every module URL a version token (an import map plus versioned `modulepreload`) and serve versioned URLs `immutable`. | 1 | **High on real networks.** Today every load or reload makes **87 conditional requests** (80 scripts and 7 stylesheets, all 304). Warm-reload DCL is 0.38 s at 0 ms RTT and **1.2 s at 60 ms RTT**. | S–M | measured |
| 4 | Stop the full board re-render when staging, undoing or loading a detail while a badge or "staged only" filter is on. | 2 | **High** for filter users. One stage remounts **1,600 `<tr>`** (the whole board twice) at 400 plots: 694 ms script, 293 ms layout, 2.3 s style. At 40 plots: 160 `<tr>`, 109 ms script, 498 ms style. | S–M | measured |
| 5 | Take the fixed pacing out of fixture mode, and replace the 0.2 s sleep loop in the status long-poll with a condition variable. | 4/8 | **Tests: about −0.4 s per board load** (capture settles in 0.61 s → 0.21 s), times roughly 1,000 board loads in the browser suite. **Users: −0 to 200 ms per load** (the long-poll granularity, measured at 0.2 s). | S | measured |
| 6 | Make sort and filter keep the row nodes. Reorder the existing `<tr>`, toggle `hidden` for filters, and debounce the text filter. | 2 | **High at size.** Sort costs 357 ms script, 305 ms layout and 354 ms style at 400 plots (15,040 nodes replaced). The first keystroke in the text filter costs 441 ms, 239 ms and 698 ms. | M | measured |
| 7 | Strip comments from CSS and JS at serve time, cached by mtime, and pre-compress. Optionally minify. | 1/7 | **Medium–high.** Board-page CSS goes from 203 KB to **90 KB gzip** without comments; JS from 690 KB to **483 KB gzip** without comments, or 393 KB minified. | S–M | measured |
| 8 | Start `POST /api/capture/refresh` from a small inline head script, in parallel with the 80-module graph. | 1 | **Medium.** Today the refresh starts at 504 ms, after the module graph. On a 60 ms RTT network the graph alone takes about 1.2 s, so overlapping saves about 0.4–1 s of the capture wait. | S | measured timeline; gain estimated |
| 9 | Run the non-browser suite in parallel (pytest-xdist). | 8 | **Medium.** It is the slowest single required step: 306–315 s in CI and 289 s locally, serial over 3,123 tests. With 4 workers, estimate about 90 s. | S (plus an isolation check) | measured serial time; gain estimated |
| 10 | Fix or drop the 146 r117 "extended" ids. Their baseline commit `97fca00` is not in the repository, so they cannot pass. | 8 | **Medium (CI minutes).** The 8 extended shards spend 41–346 s each on tests that fail at `git show`. | S | measured |
| 11 | Stop re-reading data the page already has: `/api/idle-cards` on every `render()`, the full `/api/board` read for `.totals` after each confirmed plot, and picker candidates after an undo. | 2/3 | **Medium–high on big accounts** (code comments cite 3 MB idle cards and 14.6 MB board). Zero in the fixture, where `idle_cards_loaded` is false. | S | code reading |
| 12 | Batch `renderCheckoutSurface` during undo-all and invalid-change drops, and patch the Changes panel per plot group. | 2 | **Medium.** It does one full panel rebuild plus a persist pass per plot. | S–M | code reading |
| 13 | Reuse one `FixedGetTransport` (keep-alive pool) per process instead of building about 25 new ones per call. | 3 | **Medium live.** Saves a TLS handshake per call. | M | code reading; gain estimated |
| 14 | Add a short-TTL result cache plus single-flight for `/players/balances`, `decstaked`, `/settings` and `/land/liquidity/pools`, shared by `/api/wallet` and `/api/dec`. | 3 | **Medium live.** Every `/api/wallet` and `/api/dec` request re-reads balances; the drawer reads `decstaked` twice. | M (touches wallet *read* routes: owner's call) | measured call counts |
| 15 | Share one Chromium per test module in `test_live_plot_reveal.py` and other per-test launchers; use a new context per test. | 8 | **Low–medium.** A launch costs about 160 ms plus a 316 ms Playwright start, against 40–67 ms for a context. That is about 35 s per run of that file; the shard holding it is the critical path (370 s). | S | measured |

The best payoff for the effort is #1, #2, #3 and #5. Each is a small diff with a large measured effect, and none changes what the user sees.

---

## 2. Findings per level

### Level 1: first paint and page load

Measured with `speed-review/pageload.py` (the harness browser) and `warmload.py` / `revalidate.py` (a real HTTP cache with external hosts blackholed).

**Board page cold load (40 plots, loopback):**
- 147 requests: 80 scripts (697 KB gzip), 7 stylesheets (207 KB gzip), 50 images, 8 fetches.
- FCP 240 ms, DCL 480 ms, load 560 ms.
- The board is ready at about 2.2 s.

**Timeline (ms from navigation):**

| ms | Event |
|---|---|
| 0–19 | document (15.5 KB gzip) |
| 40–483 | module graph; `app.js` is the last module to finish, at 483 |
| 504 | `POST /api/capture/refresh`, which only starts once `app.js` has run (`board.js:1031`, `board.js:1275`) |
| 575–1025 | `GET /api/capture/status?wait=500` waits about 450 ms for the capture |
| 1023–1031 | `GET /api/board` (13.8 KB gzip, 3–4 ms on the server) |
| ~1269 | board drawn, icon requests start |

The capture is the critical path, and nothing overlaps it. Item #8 starts it in parallel with the module download.

**Revalidation on every load (item #3).** `_NoCacheStaticFiles` sends `Cache-Control: no-cache` for everything outside `/static/assets` (`ceres/app.py:91-108`). The 76 `modulepreload` links (`ceres/static/index.html:500-575`) and every inter-module `import` have no version token. CDP shows the effect on both `reload()` and a fresh navigation: 80 scripts and 7 stylesheets come back as `net 304`, with no cache hits (`speed-review/revalidate.py`).

With a 60 ms RTT emulated (`warmload.py BASE 60 20000`), a warm reload still has DCL at about 1.2 s, against 0.34–0.38 s at 0 RTT. The difference is the revalidation round trips: about 87 requests over 6 HTTP/1.1 connections.

The `?v=` token already exists for `app.js` and `ceres.css`.

**Change for #3:**
- Have the server stamp an import map (`{"imports": {"/static/board.js": "/static/board.js?v=1.0.1", …}}`, generated the way `tools/gen_modulepreload.py` builds the preload block). Import maps match on the resolved URL, so relative `import './board.js'` lines are covered without editing 80 files.
- Point the preload `href`s at the same versioned URLs.
- Serve a response `immutable` when its `?v=` equals the served version. Keep `no-cache` for unversioned URLs.

This keeps the auto-reload guarantee described at `app.py:57-68`: a version bump changes every URL.

**Other notes:**
- `GET /` re-reads and re-stamps `index.html` per request (`app.py:242-252`). It measured 3 ms, so it is not worth changing.
- Gzip is applied per request at level 5 (`app.py:176`), with no pre-compressed copy. Item #7 folds this in.
- `/static/assets` 404s get no `Cache-Control`, so a missing asset is re-asked on every load. Each miss also re-reads `sources.json` (`asset_sources.py:66`; agent code reading). In this offline checkout, 200+ icon requests per reload are 404s for that reason. That is an artefact of the missing artwork, but in production a genuinely unlisted file (for example `resources/grain.png`, which is not in `sources.json`) would behave the same way. A `Cache-Control: max-age=60` on that 404 would stop it.
- **Cards page:** 41 requests, 20 scripts (111 KB gzip), 9 stylesheets (211 KB gzip), DCL 260 ms, network idle at 1.1 s. Its CSS is heavier than its JS.

### Level 2: board render and redraw cost

Measured with `speed-review/interactions.py`. It records a MutationObserver over `<body>`, CDP `Performance.getMetrics` deltas, and the same-origin requests fired. Each run is a fresh page on the fixture server.

| Interaction | 40 plots: nodes ± / `<tr>` ± | 40: script / layout / style (ms) | 400 plots: nodes ± / `<tr>` ± | 400: script / layout / style (ms) |
|---|---|---|---|---|
| text filter, 1st char | +1,033 / −1,617 ; +54/−80 | 26 / 6 / 48 | +10,240 / −16,080 ; +540/−800 | 441 / 239 / 698 |
| text filter, clear | +2,061 ; +80 | 41 / 10 / 56 | +20,520 ; +800 | 474 / 147 / 541 |
| sort (Terrain header) | ±1,513 ; ±80 | 38 / 9 / 21 | ±15,040 ; ±800 | 357 / 305 / 354 |
| open a plot (expand + editor) | +214 / −48 | 46 / 23 / 158 | +214 / −48 | 161 / 92 / **1,796** |
| stage 1 worker, no filter | +66 / −345 ; 0 `<tr>` | 16 / 23 / **339** | +71 / −345 ; 0 `<tr>` | 83 / 53 / **1,937** |
| stage 1 worker, badge filter on | +3,444 / −3,726 ; **±160** | 109 / 92 / 498 | +30,498 / −30,780 ; **±1,600** | 694 / 293 / **2,333** |

**Finding A: style invalidation from `:has()` (item #1).** Staging one worker changes about 70 nodes, yet style recalc grows with the size of the board (339 ms → 1.9 s). I tested this by deleting rules through the CSSOM in the page, after load, for the measurement only:

| Rules deleted | Stage, no filter (style ms) | Open plot (style ms) |
|---|---|---|
| none (baseline; 4 runs) | 1,937–2,220 | 1,796–2,165 |
| 14 rules whose subject is `html`/`body` (`body:has(#board-loading…)`, `html…:has(#changes-overlay…)` …) | 1,326 | 1,181 |
| 31 rules under the mobile tiers `html.ceres-lw-le-1279` / `…1919.…1279` | **344** | 378 |
| 47 rules anchored at `html`/`body` (both of the above) | 191 | 199 |
| all 127 `:has()` rules | 62 | 54 |

No single group of up to 22 rules explains the cost on its own; it builds up across many `:has()` invalidation sets. The 31 mobile-tier rules, for example `html.ceres-lw-le-1279 .controls-row .board-clear-filters-row:has([hidden])` (`ceres/static/*.css`; `ceres.css` alone has 82 `:has(`), cannot match at desktop width, yet they account for about 80% of the cost there.

The change has two parts, and neither changes visuals:
- **(a)** Disable the tier-only rules when the tier does not apply. Move them into their own stylesheet(s), and have the existing pre-paint tier script (`index.html:23-77`) set `link.disabled` or `media` from the same `e<=1279` test it already computes.
- **(b)** Replace state-detection `:has()` on `html`/`body` (overlay open, loading, reload banner) with a class that the code opening that overlay already toggles, for example `html.changes-open`.

Re-run `interactions.py` with `HAS_OFF=…` to verify; the variants are documented in that script.

**Finding B: staging with a filter re-renders the whole board twice (item #4).** The chain:
- `picker-actions.js` "choose" → `stageForSlot`, which calls `renderCheckoutSurface()` synchronously at `staging.js:310` and again when stage-impact returns (`staging.js:~387`).
- That leads to `refreshChangeBadges` (`rows.js:985-1014`). `refilter = Boolean(badge-filter.value)` (`rows.js:991`) is true whenever *any* badge filter is selected.
- From there `queueBadgeFilterRender` → `table.render()` → `renderVirtualWindow(true)` disposes and remounts every row (`table.js:172-224`).
- Measured: ±1,600 `<tr>` at 400 plots, which is 2 × (400 plot rows + 400 expand rows).

Change: test only the changed deed against `matchesFilters`, and insert or remove that one row using `CURRENT_INDEX_BY_UID` / `MOUNTED_NODES`; otherwise call `updateMountedRow`. The same pattern appears in `rows.js:1402-1406`, where a detail load triggers a full `renderTable()` while a badge filter is on.

**Finding C: sort and filter rebuild every row (item #6).**
- `app.js:796-803` binds `render()` to each filter input with no debounce.
- `render()` recomputes `CURRENT_ROWS` and remounts every node (`table.js:186`, `table.js:202-207`).
- For sort, reorder the existing keyed `<tr>` nodes instead (`MOUNTED_NODES` already maps uid → node).
- For a narrowing text filter, hide the rows that no longer match.
- `matchesFilters` reads about 8 DOM controls per row (`board.js:1427-1453`, `boardFilterValues` at `board.js:648`); read them once per render.
- `VIRTUALIZE_THRESHOLD = 400` (`table.js:111`) means up to 800 `<tr>` are mounted eagerly. Lowering it would cap the worst case, but it is a behaviour and visual trade-off for the maintainer.

**Code reading (agent), not measured here:**
- `table.render()` sets `S.IDLE_CARDS_CACHE = null` and re-fetches `/api/idle-cards` on every render (`table.js:223-224`, `combine.js:644-650`), and that response has no ETag. This is 0 requests in the fixture only because `idle_cards_loaded` is false (`board.py:1057`). Reset the cache only when `isNewDataset` is true.
- `refreshBoardTotals` GETs all of `/api/board` to read `.totals` (`rows.js:1460-1467`), once per confirmed deed (`worksite.js:913-937`, `operations.js:1316,1372`). Return the totals with the per-plot response, or coalesce to one call per batch.
- Changes panel: `renderCheckoutSurface` (`checkout.js:718-861`) replaces the whole panel and runs `persistStagePlans` (stringify plus parse) on each of about 75 call sites.
  - Undo-all calls it once per deed (`checkout.js:1081`), and `dropInvalidChanges` once per entry (`checkout.js:1095-1097`). Wrap both in the existing `batchCheckoutRenders`.
  - Undo also re-fetches picker candidates (`checkout.js:1156-1157`, `staging.js:321`).
- Per-row O(n²) scans: `syncBadgeOverlay` (`badge-overlay.js:269-446`) and `updateMountedRow`'s `BOARD.plots.find` plus `captureTableAnchor` (`rows.js:1016-1059`). Use keyed Maps.
- Editor: `fitWorkerTileDecorations` interleaves reads and writes document-wide (`editor.js:1038-1092`), and there is a document-wide MutationObserver (`editor.js:259-266`).
- Cards page:
  - `card-browser.js:316-343` reads layout after each `insertAdjacentHTML` chunk.
  - `redrawFreshCards` stringifies the card array (`card-browser.js:345-367`).
  - Cart `syncTiles` is tiles × items (`card-cart.js:142-158`).
  - `card-combinables.js:472-488` re-renders the whole list when `renderEntry(pid)` exists.
- Wallet drawer:
  - The `focus`/`visibilitychange` path calls `notify()` (`wallet.js:696-697`) and rebuilds the controls with `outerHTML`. Dispatching `focus` in the fixture changed 0 nodes (no wallet is connected there), so I could not reproduce this.
  - LP inputs rebuild a whole card per keystroke (`wallet-lp.js:392-407`, `1002-1015`).
  - These sit in wallet UI code: any change should be a render-only patch, reviewed by the owner.

### Level 3: calls to the Splinterlands APIs

Measured with `speed-review/metered_server.py`. It wraps `FixtureSet.lookup`, so every upstream read is logged with host, path, bytes and timing.

**Board load:** one quick capture makes **6 upstream reads**: `players/balances`, `land/deeds` (157 KB), `cards/collection` (138 KB), and 3 × `land/stake/items/*/available`. `cards/get_details` and `item_details` are added on the first capture only; they are disk-cached for 24 h (`land_analysis.py:1382`, `:2085`). The reads are dispatched 100 ms apart (`CONCURRENT_DISPATCH_INTERVAL_SECONDS = 0.10`, `land_analysis.py:92`, applied in `_dispatch_ceiling`, `:1248-1260`), so a fixture capture takes 0.6 s. That pacing protects the game API live; in fixture mode it is pure wait (see #5).

**Every board load and every Cards-page cart refresh runs a fresh live capture.** The deeds bundle and collection use `ttl=0` (`land_analysis.py:1469`, `:1521`; `board.js:1275`, `card-cart.js:658`). The collection's ETag is compared only *after* the full response arrives (`praetoria/api.py:36-79`). `If-None-Match` is never sent (`praetoria/transport.py:275-282`). *Estimated:* if the game API honours `If-None-Match`, the whale collection (85 MB according to the code comment at `praetoria/collection_stream.py:3`) would become a 304. This needs one live check, which I did not make.

**Repeated reads per request:**

| Route | Upstream reads per request |
|---|---|
| `/api/wallet` | 5: balances, dec/overall, liquidity/region, liquidity/pools, decstaked |
| `/api/dec` | 3: deeds/owned, decstaked, balances; plus one per region by code reading (`routes_dec.py:98-102`, N+1, up to 150 regions, 4 at a time) |
| `/api/market` | 1 (no-cache path, because this fixture's answer was a miss) |
| `/api/plots/{uid}/harvest` | `active` is cached (10 s, `routes_harvest.py:41`); `liquidity/region` is not |

Two back-to-back requests to `/api/wallet` and `/api/dec` produce 2 × each read: neither route has a result cache. `decstaked` and balances are read by both routes when the drawer opens. The change is a 10–30 s per-player result cache with the existing single-flight (`praetoria/singleflight.py:127`), invalidated on a confirmed wallet transaction. Caveat: the fixture answers the wallet hosts with 599, so successful-path caching is from code reading.

**Code reading (agent):**
- About 25 sites build a new `FixedGetTransport` per call (for example `routes_wallet.py:324,413`, `routes_dec.py:34`, `card_market.py:769`), so its 8-connection keep-alive pool (`transport.py:211-243`) is never reused (#13).
- Live captures run in a child process per read (`capture_worker.py:105-140`), which discards warm connections and memos; tests use `CERES_CAPTURE_IN_PROCESS=1`.
- Harvest `_deed_rows` makes one or two serial reads per deed at a 0.3 s pace (`routes_harvest.py:340-370`), which comes to about 60 s for 200 tax deeds with `fresh=True`.
- `MarketCache` has a 60 s TTL but no single-flight (`card_market.py:974-1004`).
- A same-client refresh cancels the running capture instead of joining it (`capture.py:2017-2041`).

**Browser-side direct calls:**
- `avatar.js:78` (`no-store`). The 3 reads per load seen in the fixture are the retry ladder after a missing-fixture 599 (`board.js:957`); in production it is 1–2 reads.
- `chain-lag.js`, throttled to 15 s, only while the harvest modal is open.
- Hive `transaction-queue.js`, only while rows are pending.

### Level 4: backend endpoint latency

Measured with `speed-review/endpoints.py`, warm, 5 runs each, median:

| Route | ms | raw / gzip bytes | Notes |
|---|---|---|---|
| `/` | 3.2 | 48.8 K / 15.5 K | `no-cache, must-revalidate` |
| `/api/board` | 3.4 | 116 K / 13.8 K | artifact bytes plus ETag (`routes_board.py:841-866`); good |
| `/api/plots/{uid}` | 5.3 | 11.7 K / 3.2 K | rebuilds `_level_by_asset_ref` / `_skin_by_asset_ref` / capacity index per call (`board.py:1496-1506`, code reading); memoise per snapshot |
| `/api/cards` | 3.3 | 60.6 K / 6.7 K | no ETag; stdlib `json.dumps` per request (`routes_cards.py:89`) |
| `/api/cards/combinables` | 6.8 | 1.9 K | `no-store`; `build_combinables` per call (`routes_combinables.py:93-118`) |
| `/api/wallet` | 7.8 | 0.7 K | 5 upstream reads, see Level 3 |
| `/api/plots/{uid}/harvest` | **300** (max 560) | 1.0 K | pure `time.sleep`: the 0.3 s global pace lock (`routes_harvest.py:42`, `:64-70`), even though the upstream answered in under 1 ms |
| `/api/harvest/routing/state` | **300** | 90 B (502 in fixture) | same pace lock |
| `/api/capture/status?wait=…` | +0–200 | 0.6 K | server re-checks every `CAPTURE_STATUS_WAIT_POLL_SECONDS = 0.20` (`routes_board.py:1056`, loop at `:1109-1112`); each wait holds a threadpool thread |

With pacing off (`NO_PACING=1 speed-review/start_server.sh`), a capture's upstream work takes about 10 ms, yet `status?wait_for=settled` still returns at **0.21 s**. That 0.2 s is the sleep step. A `threading.Condition` notified on capture state changes would answer immediately and free the thread (#5).

On this fixture the board path is already fast. At scale, the gaps are the routes that build multi-MB JSON with no byte cache or ETag:
- `/api/idle-cards`, rebuilt by `build_idle_cards` per request (`routes_board.py:1008-1014`)
- `/api/cards`

Copy the `/api/board` artifact pattern: bytes plus pre-gzip plus ETag per snapshot token. Also, `POST /api/cards/eligibility` does an HMAC per card on the event loop (`routes_cards.py:137-153`, code reading); move it to `to_thread` or cache the map.

### Level 5: background polling

All from code reading (agent), spot-checked:
- `version-banner.js:15`: `/api/dev/watch` every 1.5 s in dev, 60 s in prod. It never pauses on `document.hidden`, and each dev poll globs and stats every watched file (`routes_board.py:407-424`). Pause it while hidden and poll once on `visibilitychange`.
- `board.js:473` `syncWorkersReady`: `setInterval` every 100 ms, forever, with no network. Cheap, but it runs in every tab; switch to driving it from the events that change it, or pause while hidden.
- `app.js:933` `recoverSignedTransactions`: every 15 s, never paused. It is cheap when the queue is empty. This is signing-adjacent, so leave it.
- `harvest-state.js:181,220`: error retry every 20 s with `force=1`, a fixed interval with no backoff and no hidden check. Add backoff and pause while hidden.
- `combine.js:602-614`: polls `/api/capture/status` every 2 s with no cap. On a fetch error it retries every 2 s forever. Cap it, or use the long-poll (`wait=`) the board already uses.
- `broadcast-gate.js:329` (45 s, stops when idle), `badge-overlay.js:252` (60 s, pauses when hidden), and the `harvest-state.js:305` ticker (pauses when hidden) are fine.
- **Server side:** no periodic upstream pollers. Only `reload_watch.py` (2 s, dev) and the long-poll above.

### Level 6: image pipeline

Code reading, verified with `grep`.

**The premise in the brief does not hold for card art.** `/art/card/{key}` (`card_art_cache.py:435-455`) exists, but **no frontend code requests it**. Card art is loaded straight from `https://d36mxiodymuqjm.cloudfront.net` (`card-browser.js:34`, `editor.js:77-90`), via a `.jpg` → `.png` → unskinned candidate ladder (`card-browser.js:90-100`), so a miss costs extra failed requests per tile. Only `/static/assets` icons are same-origin, and they get `public, max-age=2592000, immutable` (`app.py:87`).

Card tiles:
- `loading="eager"`, on purpose (`card-browser.js:120-124`, `:262`).
- No `width`/`height`, though `aspect-ratio` prevents layout shift.
- Full-size CDN art shown at about 216 CSS px (`card-browser.css:52-55`).

Board-loading scene: it loads `*_500.webp` resources (20–30 KB each) into nodes about 22% of the ring wide, while `_200.webp` variants exist (`index.html:395-409`, `ceres.css:911-931`). *Estimated* saving: about 80 KB on the first load.

If `/art/card` is ever wired in, each hit currently re-parses `manifest.json` and re-hashes the image (`card_art_cache.py:242-279`). Cache the manifest in memory and verify the hash once at write time.

I could not measure native image sizes or decode cost: game artwork is not in the repository, and fetching it would mean touching the CDN.

### Level 7: static asset weight

Measured with `speed-review/weight.py` (esbuild 0.25.0, used only to measure), on the 87 files the board page loads:

| | raw | gzip | comments stripped | stripped, gzip | minified | minified, gzip |
|---|---|---|---|---|---|---|
| JS (80 files) | 2,280 KB | 690 KB | 1,831 KB | **483 KB** | 1,173 KB | 393 KB |
| CSS (7 files) | 823 KB | 203 KB | 578 KB | **90 KB** | 498 KB | 84 KB |

- `ceres.css` alone is 677 KB raw: 174 KB gzip, 70 KB without comments.
- The largest JS files are `editor.js` (144 KB raw, 44 KB gzip), `wallet-lp.js`, `operations.js`, `harvest-modal.js` and `picker-actions.js`.
- The board page preloads every feature module, including `wallet-lp.js`, `harvest-routing.js`, `combine-*`, `routing-*` and `card-browser-model.js`. About 25 of the 80 belong to drawers and modals that are not open at first paint.

Change (#7): strip comments at serve time, memoised by `(path, mtime)`, and keep pre-gzipped bytes, so there is still no build step. Then consider loading drawer and modal modules with dynamic `import()` on first open; that is a bigger change, and its gain (fewer bytes and fewer requests on the first load) is estimated. Comment-stripping does not change the code, but check it against anything that reads source comments at runtime; I found none.

### Level 8: tests and CI wall time

Measured from CI run 41700323211 (this branch, 2026-11-03), local runs, and `speed-review/shards.py`.

**Core shards are badly unbalanced (#2).** Test-step seconds for the 20 shards were 25, 31, 60, 84, 91, 126, 127, 147, 168, 172, 174, 179, 183, 203, 206, 284, 291, 322, 510 and 521 (total 3,904 s, mean 195 s). The heaviest shards:
- **Shard 14 (521 s):** `test_live_plot_reveal.py`, 370 s, one file, never split.
- **Shard 20 (510 s):** `test_live_art_continuity.py` (185 s) plus `test_virtualization_guards.py` (184 s).

The cause is in `tests/conftest.py:83-118` together with `tests/ceres/browser_durations.json`:
- Durations are recorded **per file**, for the whole file.
- The core/extended split is **per test id** (`browser_extended.txt`).
- So `test_live_r117_terrain_display.py`, which has 6 core tests and 122 extended ones, charges all 2,370 s to its 6 core tests; `test_live_r117_affinity_paint.py` charges 1,790 s to 35 core tests; and so on.

The predicted core total is 13,668 s against 3,904 s real. The per-shard target is inflated to 683 s, so no real file (all under 400 s) is ever split, and shards packed with phantom seconds finish in 25–60 s.

The fix:
- Record durations per nodeid, separately for core and extended. The JUnit step at `ci.yml:83-98` already has per-case times; drop its per-file aggregation.
- Weight by test and split heavy files.

Expected result: about 195–210 s per shard, so the required job falls from about 9.5 min to about 4.5 min. Alternatively, keep the 9.5 min and run fewer shards.

**Extended job (#10).**
- 146 of its 201 ids are r117 tests that `git show 97fca000…` (`test_live_r117_terrain_display.py:23`, `:73-77`).
- That commit is not in the repository (`git cat-file` fails even in this full clone), so these tests fail at once, every run.
- Restore the baseline as committed files, or delete these ids.

**Non-browser job (#9).** It is the slowest required job: 306–315 s for the test step on 3.11 and 3.12.
- Locally it takes 289 s for 3,123 tests, serial.
- The slowest tests are 4.7–8.3 s each: `test_rows_detail_handoff`, `test_card_art_cache_route` and `test_worksite_restore` (×5).
- *Estimated:* `pytest-xdist -n 4` brings it to about 90 s. This needs a check that no test shares a fixed port or path.
- 3 tests failed locally (`test_button_contract::test_legacy_style_ownership_matches_exact_allowlist`, `test_dec_harvest_receipt`, `test_grain_reconcile`). The CI `test` job on this branch fails too. That is unrelated to speed and not investigated.

**Per-load cost inside browser tests (#5).**
- Each board load in a test pays the 0.6 s capture pacing, plus up to 0.2 s of long-poll granularity, against an upstream that answers in under 1 ms.
- Measured: settle takes 0.61 s with pacing and 0.21 s without; a warm reload to the first board row takes about 1,045 ms with pacing and about 830 ms without.
- The suite has 190 `.goto(` and 21 `.reload(` call sites; parametrised, that is roughly 1,000 or more board loads per CI run (estimated).
- Set the dispatch interval to 0 when `CERES_FIXTURE_UPSTREAM` is active. This is test-only and leaves the live path untouched.

**Browser launches (#15).**
- There are 132 `spawn_live_ceres` blocks: 83 in module-scoped fixtures, which are fine, and 49 at test level.
- `test_live_plot_reveal.py:425-426` runs `async_playwright()` plus `chromium.launch()` inside every one of its 86 tests.
- Measured: a launch costs about 160 ms plus the 316 ms Playwright start; a new context in a shared browser costs 40–67 ms.

**Smaller:**
- Fixed sleeps are rare: 9 `wait_for_timeout` (5 s in total) and 34 `asyncio.sleep` in `test_live_plot_reveal.py` (about 8 s per pass of that file).
- `playwright install --with-deps` takes 16–46 s per shard (125 s once) across 28 browser jobs. Caching `~/.cache/ms-playwright` keyed on the Playwright version would save about 10 min of runner time per CI run (estimated).

---

## 3. Measurement commands

All commands run from the repository root, after `git checkout check/ir9547-runner-pin` and `pip install -r requirements-ceres.txt -r requirements-dev.txt`.

On this sandbox, Playwright 1.60 expected Chromium revision 1223 while 1243 was installed. I symlinked the installed one under the expected name in a private `PLAYWRIGHT_BROWSERS_PATH`. On a normal machine, `python -m playwright install chromium` is enough.

```bash
# 1. Page-load inventory (harness browser, offline routes) -- board and cards
python speed-review/pageload.py                       # board: requests, bytes, timeline (OUT=file.json dumps the raw list)
python speed-review/pageload.py "/cards?player=sample01"

# 2. Metered fixture server (logs every upstream read to $SPEED_SCRATCH/home-PORT/upstream.jsonl)
speed-review/start_server.sh 27411                    # paced, as shipped
NO_PACING=1 speed-review/start_server.sh 27412        # dispatch pacing off (measurement only)
B=http://127.0.0.1:27411
curl -s -XPOST "$B/api/capture/refresh?player=sample01"; \
curl -s -o /dev/null -w "%{time_total}\n" "$B/api/capture/status?player=sample01&wait=5000&wait_for=settled"

# 3. Endpoint latency and sizes
python -I speed-review/endpoints.py http://127.0.0.1:27411

# 4. Warm-cache reloads (real HTTP cache), optional RTT/throughput emulation; per-file revalidation via CDP
python -I speed-review/warmload.py http://127.0.0.1:27411
python -I speed-review/warmload.py http://127.0.0.1:27411 60 20000
python -I speed-review/revalidate.py http://127.0.0.1:27411

# 5. Interaction cost (DOM nodes, <tr> churn, script/layout/style ms, requests)
SCALE=1  python -I speed-review/interactions.py
SCALE=10 python -I speed-review/interactions.py
SCALE=10 HAS_OFF=anchored python -I speed-review/interactions.py       # also: all | subject | 're:<regex>' ; LIST=1 prints deleted selectors

# 6. Static weight (needs esbuild only as a measuring tool: npm i esbuild@0.25.0 in a scratch dir)
OUT=/tmp/board_reqs.json python speed-review/pageload.py
python -I speed-review/weight.py /path/to/node_modules/.bin/esbuild /tmp/board_reqs.json

# 7. Shard balance as the conftest would compute it
CERES_BROWSER_SET=core python -m pytest -m browser --collect-only -q | grep :: > /tmp/core.txt
python -I speed-review/shards.py /tmp/core.txt 20 tests/ceres/browser_durations.json
# real per-shard times: CI run 41700323211, job steps "browser tests (shard i of 20)" and their DURATION lines

# 8. Non-browser suite time and slowest tests
CERES_CI_NO_NETWORK=1 python -m pytest -m "not browser and not live" -q --durations=40
```

Stop the servers with `kill $(cat $SPEED_SCRATCH/home-27411/pid)`. `SPEED_SCRATCH` defaults to `/tmp/ceres-speed`.

---

## 4. What I could not measure

- **A real large account.** The fixture has 40 plots. The 400-plot results use cloned rows; real whales (6,303 plots and 85 MB collections, according to code comments) were not available, and the virtualised path (more than 400 rows) was not exercised.
- **Live API latency, conditional-request support (`If-None-Match` → 304) and TLS handshake cost.** Calling game APIs was out of scope, so gains for #13, #14 and the ETag idea are estimated.
- **Successful-path caching for the wallet, DEC, market and purchases routes.** The fixture answers their upstreams with 599, so only call counts per request were measured.
- **Image sizes, formats and decode cost.** Game artwork is not in the repository and is fetched from the CDN on demand; the browser harness serves placeholders.
- **The wallet `focus` → `notify()` redraw.** No wallet is connected in the fixture: 0 nodes changed when `focus` was dispatched.
- **Real-network page load.** Everything was measured on loopback, with Chrome's network emulation for the 60 ms RTT case. No mobile CPU throttling was applied.
- **pytest-xdist speed-up and safety.** Not run; the estimate is mine.
- **Per-test (rather than per-file) CI durations.** I only pulled the per-file DURATION lines of 2 shards. The per-test split in #2 needs one CI run that keeps the JUnit XML.
- **Exact attribution of the `:has()` cost to individual rules.** The bisection shows the cost is cumulative; groups of up to 22 rules made no clear difference on their own.
