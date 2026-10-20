"""Live art-continuity guards; run apart from the sync-Playwright files ().

These guards reuse the asynchronous browser fixture and controlled art gates from
``test_live_plot_reveal``. They are deliberately separate from the synchronous
Playwright files so this queue can be dispatched independently.
"""

from __future__ import annotations

import asyncio
from collections import Counter

import pytest

from .test_live_plot_reveal import _browser, _payload
from .vm_context import STATIC, read_entry_source


EOS_SOURCE = read_entry_source(STATIC / "eos.js")


def test_eos_p3_retained_node_handoff_contract() -> None:
    assert "function reconcilePlotHandoff" in EOS_SOURCE
    assert "session.apertureRecords" in EOS_SOURCE
    assert "old.src = url" not in EOS_SOURCE
    assert "settledImage.src = outcome.url" not in EOS_SOURCE


def test_eos_p3_quiet_refresh_retains_real_band_contract() -> None:
    assert "session.quietRetained = true" in read_entry_source()
    assert "!session.quietRetained || dataReady" in read_entry_source()
    assert "data-eos-detail-scaffold" in read_entry_source()
    assert "hasAttribute('data-eos-detail-scaffold')" in read_entry_source()


def test_eos_p3_global_queue_rank_contract() -> None:
    source = read_entry_source()
    assert "while (activeImages < 4 && queue.length)" in source
    assert "return rank >= 0 ? rank + 1" in EOS_SOURCE
    assert "plotTurns.set(plotUid, ++queueTurn)" in EOS_SOURCE


def test_eos_p3_recovery_contract() -> None:
    source = read_entry_source()
    assert "const SCENE_RELEASE_MS = 1200" in EOS_SOURCE
    assert "const CARD_RELEASE_MS = 1800" in EOS_SOURCE
    assert "const CARD_SERVICE_BUDGET_MS = 10000" in EOS_SOURCE
    assert "const RECOVERY_DELAYS_MS = IMAGE_RETRY_DELAYS_MS" in EOS_SOURCE
    assert "const MAX_RECOVERY_ATTEMPTS = IMAGE_RETRY_DELAYS_MS.length" in EOS_SOURCE
    assert "card.dataset.eosState = 'art-unavailable'" in EOS_SOURCE
    assert "phaseTimer(session, 'stall'" in EOS_SOURCE


def _decoded_snapshot(fixture):
    return fixture.page.evaluate("""uid => {
      const band = document.querySelector(`tr.expand-row[data-deed-uid="${uid}"] .editor-band`);
      return [...band.querySelectorAll('img[data-eos-managed][data-eos-outcome="decoded"]')].map(image => {
        const style = getComputedStyle(image);
        const rect = image.getBoundingClientRect();
        return {src: image.currentSrc || image.src, visible: style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0};
      });
    }""", "eos-0008")


async def _open_decoded(fixture):
    await fixture.open()
    fixture.release("detail", "scene", "portrait", "upgrade")
    await fixture.page.wait_for_function("""uid =>
      (() => {
        const row = document.querySelector(`tr.expand-row[data-deed-uid="${uid}"]`);
        return !!row && row.querySelectorAll('img[data-eos-managed]').length >= 9 &&
          row.querySelectorAll('img[data-eos-managed]:not([data-eos-outcome="decoded"])').length === 0;
      })()
    """, arg="eos-0008", timeout=10000)


@pytest.mark.browser  # drives Chromium through test_live_plot_reveal._browser
def test_live_art_continuity_m2(report, details_by_id, tmp_path):
    board, details = _payload(report, details_by_id, count=12, capacity=9)

    async def run(mutation=None):
        async with _browser(board, details, tmp_path / ("m2-red" if mutation else "m2"), mutation=mutation) as fixture:
            await _open_decoded(fixture)
            before = await _decoded_snapshot(fixture)
            before_managed = await fixture.page.locator(
                'tr.expand-row[data-deed-uid="eos-0008"] img[data-eos-managed]'
            ).count()
            await fixture.page.evaluate("""uid => {
              const band = document.querySelector(`tr.expand-row[data-deed-uid="${uid}"] .editor-band`);
              [...band.querySelectorAll('img[data-eos-managed][data-eos-outcome="decoded"]')]
                .forEach((image, index) => { image.dataset.artContinuityProbe = String(index); });
            }""", "eos-0008")
            await fixture.module("table", "m => m.render(false)")
            await fixture.module("rows", "m => m.updateMountedRow('eos-0008')")
            await fixture.module("rows", "m => m.updateMountedRow('eos-0008', {readiness: true})")
            await asyncio.sleep(0.1)
            after = await _decoded_snapshot(fixture)
            after_managed = await fixture.page.locator(
                'tr.expand-row[data-deed-uid="eos-0008"] img[data-eos-managed]'
            ).count()
            after_probes = await fixture.page.locator(
                'tr.expand-row[data-deed-uid="eos-0008"] img[data-eos-managed]'
            ).evaluate_all("images => images.map(image => image.dataset.artContinuityProbe)")
            assert after_managed == before_managed
            assert after_probes == [str(index) for index in range(before_managed)]
            assert len(after) == len(before) == before_managed
            assert all(item["visible"] for item in after)
            assert [item["src"] for item in after] == [item["src"] for item in before]

    asyncio.run(run())
    try:
        asyncio.run(run(("rows.js", "spliceDecodedArt(row.deed_uid, previousBand, td.querySelector('.editor-band'));", "void 0;")))
    except AssertionError:
        return
    raise AssertionError("the dropped decoded-node splice did not invalidate M2")


@pytest.mark.browser  # drives Chromium through test_live_plot_reveal._browser
def test_live_art_slot_order_and_fixed_frames(report, details_by_id, tmp_path):
    board, details = _payload(report, details_by_id, count=12, capacity=9)
    delayed = ("eos.js", "() => decodeImage(image, source, valid, session.artCleanups, CARD_SERVICE_BUDGET_MS)", "async () => { await new Promise(resolve => setTimeout(resolve, 250)); return decodeImage(image, source, valid, session.artCleanups, CARD_SERVICE_BUDGET_MS); }")

    async def run(red=False):
        async with _browser(board, details, tmp_path / ("slot-order-red" if red else "slot-order"), mutation=delayed) as fixture:
            await fixture.open()
            await fixture.page.evaluate("""() => {
              let release;
              const held = new Promise(resolve => { release = resolve; });
              window.releaseSlotFive = release;
              const decode = HTMLImageElement.prototype.decode;
              HTMLImageElement.prototype.decode = async function() {
                if (this.closest('[data-group-id="worker"][data-slot-index="4"]')) await held;
                return decode.call(this);
              };
            }""")
            fixture.release("detail", "scene", "portrait", "upgrade")
            await fixture.page.wait_for_function("""uid =>
              document.querySelectorAll(`tr.expand-row[data-deed-uid="${uid}"] img[data-eos-managed]`).length > 1
            """, arg="eos-0008", timeout=10000)
            managed = await fixture.page.locator(
                'tr.expand-row[data-deed-uid="eos-0008"] img[data-eos-managed]'
            ).count()
            assert managed > 1
            await fixture.page.wait_for_function("""({uid, managed}) =>
              document.querySelectorAll(`tr.expand-row[data-deed-uid="${uid}"] img[data-eos-managed][data-eos-outcome="decoded"]`).length === managed - 1
            """, arg={"uid": "eos-0008", "managed": managed}, timeout=10000)
            before = await fixture.page.evaluate("""uid => [...document.querySelectorAll(`tr.expand-row[data-deed-uid="${uid}"] .slot-tile-cell`)].map(n => {
              const r=n.getBoundingClientRect(); return [r.left,r.top,r.width,r.height];
            })""", "eos-0008")
            if red:
                await fixture.page.evaluate("""uid => {
                  const band = document.querySelector(`tr.expand-row[data-deed-uid="${uid}"] .editor-band`);
                  const observer = new MutationObserver(() => {
                    const cell = band?.querySelectorAll('.slot-tile-cell')[4];
                    if (!cell) return;
                    observer.disconnect();
                    cell.style.transform = 'translateX(4px)';
                  });
                  observer.observe(band, {attributes: true, childList: true, subtree: true});
                }""", "eos-0008")
            await fixture.page.evaluate("window.releaseSlotFive()")
            await fixture.page.wait_for_function("""uid =>
              (() => {
                const images = [...document.querySelectorAll(`tr.expand-row[data-deed-uid="${uid}"] img[data-eos-managed]`)];
                return images.length > 0 && images.every(image => image.dataset.eosOutcome);
              })()
            """, arg="eos-0008", timeout=10000)
            expected = await fixture.page.locator(
                'tr.expand-row[data-deed-uid="eos-0008"] img[data-eos-managed]'
            ).count()
            assert await fixture.page.locator(
                'tr.expand-row[data-deed-uid="eos-0008"] img[data-eos-managed][data-eos-outcome="decoded"]'
            ).count() == expected
            after = await fixture.page.evaluate("""uid => [...document.querySelectorAll(`tr.expand-row[data-deed-uid="${uid}"] .slot-tile-cell`)].map(n => {
              const r=n.getBoundingClientRect(); return [r.left,r.top,r.width,r.height];
            })""", "eos-0008")
            assert after == before

    asyncio.run(run())
    try:
        asyncio.run(run(red=True))
    except AssertionError:
        return
    raise AssertionError("the translated slot-frame mutation did not invalidate fixed frames")


@pytest.mark.browser  # drives Chromium through test_live_plot_reveal._browser
def test_live_art_bounded_retry(report, details_by_id, tmp_path):
    board, details = _payload(report, details_by_id, count=9, capacity=9)
    short_budget = ("eos.js", "const CARD_SERVICE_BUDGET_MS = 10000;", "const CARD_SERVICE_BUDGET_MS = 20;")
    unbounded_retry = (
        "eos.js",
        """function scheduleArtRecovery(session, key, record) {
  if (record.retryScheduled || record.recoveryAttempts >= MAX_RECOVERY_ATTEMPTS) return;
  const delay = RECOVERY_DELAYS_MS[record.recoveryAttempts];
  record.recoveryAttempts++;
  record.nextRetryAt = performance.now() + delay;
  record.retryScheduled = true;
  timer(session, () => {
    record.retryScheduled = false;
    record.nextRetryAt = 0;
    if (session.mountedFrame) { session.cardsSettled = false; prepareWorkers(session); }
  }, delay);
}

function rememberArtFailure(session, key, outcome, record) {
  record.lastOutcome = outcome.kind;
  if (outcome.permanent || record.recoveryAttempts >= MAX_RECOVERY_ATTEMPTS) freezeArt(session, key, record);
  else scheduleArtRecovery(session, key, record);
}""",
        """function scheduleArtRecovery(session, key, record) {
  if (record.retryScheduled) return;
  const delay = RECOVERY_DELAYS_MS[record.recoveryAttempts] ?? 0;
  record.recoveryAttempts++;
  record.nextRetryAt = performance.now() + delay;
  record.retryScheduled = true;
  timer(session, () => {
    record.retryScheduled = false;
    record.nextRetryAt = 0;
    if (session.mountedFrame) { session.cardsSettled = false; prepareWorkers(session); }
  }, delay);
}

function rememberArtFailure(session, key, outcome, record) {
  record.lastOutcome = outcome.kind;
  if (outcome.permanent) freezeArt(session, key, record);
  else scheduleArtRecovery(session, key, record);
}""",
    )

    # Asclepius diagnosis 3. Two things make the naive form of this guard vacuous.
    # (1) The deliberate permanent-404 branch is unreachable here: the fixture's art is
    # cross-origin CDN, which reports status 0, and eos.js:104-109 treats a 403/404
    # without an observable status as recoverable on purpose. So the failure driven
    # here is a decode TIMEOUT, not a permanent failure.
    # (2) `recoveryAttempts` only advances on a failure that prepareWorkers actually
    # remembers, and eos.js gates that on `record.failureRevision !== revision`. A
    # plain load reaches artRevision 3, so attempts never reach MAX_RECOVERY_ATTEMPTS
    # and the `unbounded_retry` mutation is a semantic no-op -- measured: green 43
    # requests, red 41, both already stable at 8 s. The guard must therefore DRIVE
    # revisions, spaced past RECOVERY_DELAYS_MS, until the bound is actually reached.
    # Measured with the drive: green freezes every key (12 fallbacks, 30 requests, no
    # further traffic); red climbs 21 -> 79 -> 133 -> 220 -> 230 and never freezes.
    async def run_timeout(red=False):
        mutation = unbounded_retry if red else short_budget
        async with _browser(board, details, tmp_path / ("timeout-red" if red else "timeout"), mutation=mutation) as fixture:
            await fixture.open()
            baseline = len(fixture.requests)
            if red:
                await fixture.page.evaluate("""() => {
                  const nativeSetTimeout = window.setTimeout;
                  window.setTimeout = (callback, delay, ...args) => nativeSetTimeout(callback, delay >= 10000 ? 20 : delay, ...args);
                  eosProbe.blockDecode = true;
                }""")
            else:
                await fixture.page.evaluate("eosProbe.blockDecode = true")
            fixture.release("detail", "scene", "portrait", "upgrade")

            def card_requests():
                return Counter(
                    request["path"] for request in fixture.requests[baseline:]
                    if request["kind"] == "image" and request["path"].startswith("/cards")
                )

            for _ in range(120):
                if card_requests():
                    break
                await asyncio.sleep(0.05)
            assert card_requests()
            managed = await fixture.band().locator("img[data-eos-managed]").count()
            assert managed
            for _ in range(7):
                await asyncio.sleep(1.5)
                await fixture.module("rows", "m => m.updateMountedRow('eos-0008')")
            bounded = card_requests()
            assert sum(bounded.values()) > len(bounded)
            await asyncio.sleep(3.0)
            assert card_requests() == bounded
            assert await fixture.band().locator(".eos-art-fallback").count() == managed
            assert await fixture.band().locator("img[data-eos-managed]").count() == 0

    asyncio.run(run_timeout())
    try:
        asyncio.run(run_timeout(red=True))
    except AssertionError:
        return
    raise AssertionError("the unbounded timed-out-art mutation did not outlive the bounded recovery")


@pytest.mark.browser  # drives Chromium through test_live_plot_reveal._browser
def test_live_art_queue_fairness_across_plots(report, details_by_id, tmp_path):
    # Asclepius diagnosis 3. With capacity == the pump's concurrency (4) each plot owns
    # exactly one pump window, so plot B's whole batch fills the pump and plot A's whole
    # batch follows it -- measured order "BBBBAAAA", no contended window ever exists and
    # rotation cannot be observed. The plots need more art than the pump is wide, and the
    # window under test is the CONTENDED one: the dispatches made while BOTH plots still
    # have art outstanding. Measured: green "BABBBAABAABB" (both plots, longest run 3)
    # red "BBBBBBB" (one plot only, run of 7).
    board, details = _payload(report, details_by_id, count=10, capacity=12)
    for uid, label in (("eos-0008", "PlotAWorker"), ("eos-0009", "PlotBWorker")):
        for slot, worker in enumerate(details[uid]["workers"], start=1):
            worker["name"] = f"{label}{slot}"
            worker["card_name"] = f"{label}{slot}"
    rotation_removed = (
        "eos.js",
        "return (aTurn - bTurn) || (a[0].sequence - b[0].sequence);",
        "return a[0].sequence - b[0].sequence;",
    )
    concurrency = 4

    def plot_of(request):
        return "A" if "PlotAWorker" in request["path"] else "B"

    def is_worker_art(request):
        return "PlotAWorker" in request["path"] or "PlotBWorker" in request["path"]

    async def run(mutation=None):
        async with _browser(board, details, tmp_path / ("queue-fairness-red" if mutation else "queue-fairness"), mutation=mutation) as fixture:
            await fixture.open("eos-0009")
            await fixture.open("eos-0008")
            fixture.release("detail", "scene", "upgrade")
            await fixture.page.wait_for_function("""uids => uids.every(uid =>
              document.querySelectorAll(`tr.expand-row[data-deed-uid="${uid}"] img[data-eos-managed][data-card-image-candidates*="PlotAWorker"], tr.expand-row[data-deed-uid="${uid}"] img[data-eos-managed][data-card-image-candidates*="PlotBWorker"]`).length >= 8
            )""", arg=["eos-0008", "eos-0009"], timeout=10000)
            await asyncio.sleep(0.6)
            totals = await fixture.page.evaluate("""() => ({
              A: document.querySelectorAll('img[data-eos-managed][data-card-image-candidates*="PlotAWorker"]').length,
              B: document.querySelectorAll('img[data-eos-managed][data-card-image-candidates*="PlotBWorker"]').length,
            })""")
            assert min(totals.values()) > 2 * concurrency
            dispatched = Counter(plot_of(request) for request in fixture.requests if is_worker_art(request))
            mark = len(fixture.requests)
            fixture.release("portrait")
            await asyncio.sleep(6.0)
            contended = []
            for request in fixture.requests[mark:]:
                if not is_worker_art(request):
                    continue
                if any(dispatched[plot] >= totals[plot] for plot in ("A", "B")):
                    break
                plot = plot_of(request)
                contended.append(plot)
                dispatched[plot] += 1
            assert len(contended) >= 2 * concurrency
            assert set(contended) == {"A", "B"}
            runs = []
            for plot in contended:
                if runs and runs[-1][0] == plot:
                    runs[-1][1] += 1
                else:
                    runs.append([plot, 1])
            assert max(run[1] for run in runs) <= concurrency

    asyncio.run(run())
    try:
        asyncio.run(run(rotation_removed))
    except AssertionError:
        return
    raise AssertionError("the removed plot-turn rotation did not invalidate queue fairness")


@pytest.mark.browser  # drives Chromium through test_live_plot_reveal._browser
def test_live_art_duplicate_candidates_share_queue_slot(report, details_by_id, tmp_path):
    # Asclepius diagnosis 3. Counting network requests cannot decide this: Chromium
    # collapses simultaneous identical image loads into a single fetch, so the two arms
    # overlap (measured green 2/2 requests, red 1/1 at capacity 2). And a session-wide
    # count cannot be compared against a revision count either, because a later prepare
    # pass short-circuits on `session.cardOutcomes` and issues nothing. The property is
    # per-revision: within ONE art revision, N images sharing a candidate list must take
    # ONE queue slot. Instrumented at the decode call, tagged with the band's live
    # data-art-revision: measured green max 1 per revision, red max 11.
    board, details = _payload(report, details_by_id, count=9, capacity=12)
    workers = details["eos-0008"]["workers"]
    for worker in workers:
        worker["name"] = "DedupWorker"
        worker["card_name"] = "DedupWorker"
    dedup_disabled = (
        "eos.js",
        "if (record.inFlightPromise && record.inFlightRevision === revision) return record.inFlightPromise;",
        "if (false && record.inFlightPromise && record.inFlightRevision === revision) return record.inFlightPromise;",
    )

    async def run(mutation=None):
        async with _browser(board, details, tmp_path / ("candidate-dedup-red" if mutation else "candidate-dedup"), mutation=mutation) as fixture:
            await fixture.page.evaluate("""uid => {
              window.eosDedupRevisions = [];
              const decode = HTMLImageElement.prototype.decode;
              HTMLImageElement.prototype.decode = function() {
                const source = this.currentSrc || this.src;
                if (source.includes('DedupWorker')) {
                  const band = document.querySelector(`tr.expand-row[data-deed-uid="${uid}"] .editor-band`);
                  window.eosDedupRevisions.push(Number(band?.dataset.artRevision ?? -1));
                }
                return decode.apply(this, arguments);
              };
            }""", "eos-0008")
            await fixture.open()
            fixture.release("detail", "scene", "portrait", "upgrade")
            await fixture.terminal()
            await asyncio.sleep(1.5)
            duplicates = await fixture.band().locator(
                'img[data-eos-managed][data-eos-outcome="decoded"]'
            ).count()
            assert duplicates > 1
            revisions = Counter(await fixture.page.evaluate("() => window.eosDedupRevisions"))
            assert revisions
            assert max(revisions.values()) == 1

    asyncio.run(run())
    try:
        asyncio.run(run(dedup_disabled))
    except AssertionError:
        return
    raise AssertionError("the disabled candidate deduplication did not invalidate the one-request guard")


@pytest.mark.browser  # drives Chromium through test_live_plot_reveal._browser
def test_edition_13_candidate_order(report, details_by_id, tmp_path):
    board, details = _payload(report, details_by_id, count=1, capacity=1)

    async def run(mutation=None):
        async with _browser(board, details, tmp_path / ("candidates-red" if mutation else "candidates"), mutation=mutation) as fixture:
            plain = await fixture.module("editor", "m => m.editorCardImageCandidates(arg)", {"card_name": "Rage", "name": "Rage", "edition": 13, "editions": "13", "card_set": "rebellion"})
            gold = await fixture.module("editor", "m => m.editorCardImageCandidates(arg)", {"card_name": "Rage", "name": "Rage", "edition": 13, "editions": "13", "card_set": "rebellion", "foil": "gold"})
            skinned = await fixture.module("editor", "m => m.editorCardImageCandidates(arg)", {"card_name": "Rage", "name": "Rage", "edition": 13, "editions": "13", "card_set": "rebellion", "skin": "golden", "foil": "gold"})
            assert plain[0].split("/")[3] == "cards_soulboundrb"
            assert gold[0].split("/")[3] == "cards_soulboundrb"
            assert skinned[0].split("/")[3] == "cards_soulboundrb"
            assert "/golden/" in skinned[0]

    asyncio.run(run())
    try:
        asyncio.run(run(("editor.js", "const sets = [...new Set([numericSet, sourceSet].filter(Boolean))];", "const sets = [...new Set([sourceSet, numericSet].filter(Boolean))];")))
    except AssertionError:
        return
    raise AssertionError("the edition-13 candidate-folder swap did not invalidate candidate order")


@pytest.mark.browser  # drives Chromium through test_live_plot_reveal._browser
def test_live_art_scene_soft_release_preserves_late_decode(report, details_by_id, tmp_path):
    board, details = _payload(report, details_by_id, count=9, capacity=1)
    soft_ceiling_removed = (
        "eos.js",
        "timer(session, releaseScene, SCENE_RELEASE_MS);",
        "void releaseScene;",
    )

    async def run(mutation=None):
        async with _browser(board, details, tmp_path / ("scene-soft-red" if mutation else "scene-soft"), mutation=mutation) as fixture:
            await fixture.open()
            fixture.release("detail")
            await asyncio.sleep(1.65)
            # desktop plot editor renders the horizontal splinter row
            # (`.editor-splinter-affinity [data-terrain-affinity="picker"]`), not the
            # vertical column retired.
            badges = fixture.band().locator('.editor-splinter-affinity [data-terrain-affinity="editor-row"]')
            assert await badges.is_visible()
            assert await badges.get_attribute("aria-hidden") != "true"
            assert not fixture.gates["scene"].is_set()
            scene_state = await fixture.module(
                "eos",
                "m => { const s = m.getPlotPresentation(arg); return {released: s.sceneReleased, kind: s.sceneOutcome.terrain?.kind}; }",
                "eos-0008",
            )
            assert scene_state == {"released": True, "kind": "timed-out"}
            assert await fixture.band().locator(".editor-scene").evaluate(
                "node => !getComputedStyle(node, '::before').backgroundImage.includes('url(')"
            )
            fixture.release("scene")
            await fixture.page.wait_for_function(
                "() => getComputedStyle(document.querySelector('.editor-scene'), '::before').backgroundImage.includes('url(')"
            )
            scene_state = await fixture.module(
                "eos",
                "m => m.getPlotPresentation(arg).sceneOutcome.terrain?.kind",
                "eos-0008",
            )
            assert scene_state == "decoded"

    asyncio.run(run())
    try:
        asyncio.run(run(soft_ceiling_removed))
    except AssertionError:
        return
    raise AssertionError("the removed scene soft ceiling did not keep badges gated")


@pytest.mark.browser  # drives Chromium through test_live_plot_reveal._browser
def test_live_art_scene_remount_preserves_inflight_decode(report, details_by_id, tmp_path):
    board, details = _payload(report, details_by_id, count=9, capacity=1)
    same_key_return_removed = (
        "eos.js",
        "if (sameKey && !retrying && !needsRecovery) { applyScene(session); return; }",
        "if (false && sameKey && !retrying && !needsRecovery) { applyScene(session); return; }",
    )

    async def run(mutation=None):
        async with _browser(board, details, tmp_path / ("scene-remount-red" if mutation else "scene-remount"), mutation=mutation) as fixture:
            # editorSceneLayers() emits TWO layers for this fixture row (terrain "Plains" plus
            # resource "grain"), so a first paint starts exactly two scene decodes
            # .../terrain-backgrounds/plains_natural_build.jpg and
            # .../productionBuildingUniform/grain/plot/plains_lvl_1.webp. Both match the probe
            # below, and pump() dispatches both in the same task (activeImages < 4), so the
            # counter steps 0 -> 2 with no observable 1 -- the earlier `=== 1` predicate could
            # never be true and timed out at 30s. The fixture's own request log is not usable
            # here either: it counts five scene-matching requests, because the `::before`
            # background-image fetches as well. This page-level Image() probe is the instrument
            # two is the count.
            await fixture.page.evaluate("""() => {
              window.eosSceneStarts = 0;
              const descriptor = Object.getOwnPropertyDescriptor(HTMLImageElement.prototype, 'src');
              Object.defineProperty(HTMLImageElement.prototype, 'src', {
                configurable: true,
                get: descriptor.get,
                set(value) {
                  if (/terrain-backgrounds|productionBuildingUniform/.test(String(value))) window.eosSceneStarts++;
                  return descriptor.set.call(this, value);
                },
              });
            }""")
            await fixture.open()
            await fixture.page.wait_for_function(
                "() => window.eosSceneStarts === 2"
            )
            for _ in range(3):
                await fixture.module("rows", "m => m.updateMountedRow('eos-0008')")
            assert await fixture.page.evaluate("() => window.eosSceneStarts") == 2
            fixture.release("detail", "scene", "portrait", "upgrade")
            await fixture.page.wait_for_function(
                "() => getComputedStyle(document.querySelector('.editor-scene'), '::before').backgroundImage.includes('url(')"
            )

    asyncio.run(run())
    try:
        asyncio.run(run(same_key_return_removed))
    except AssertionError:
        return
    raise AssertionError("the removed same-scene-key return did not invalidate in-flight scene continuity")


@pytest.mark.browser  # drives Chromium through test_live_plot_reveal._browser
def test_live_art_scene_retry_recovers_stale_unavailable_record(report, details_by_id, tmp_path):
    board, details = _payload(report, details_by_id, count=9, capacity=1)
    retry_cleanup_removed = (
        "eos.js",
        "  }, delay, reset);\n}",
        "  }, delay);\n}",
    )

    async def run(mutation=None):
        async with _browser(board, details, tmp_path / ("scene-retry-red" if mutation else "scene-retry"), mutation=mutation) as fixture:
            await fixture.open()
            await fixture.page.wait_for_function(
                "() => document.querySelector('.editor-scene')"
            )
            fixture.release("detail", "portrait", "upgrade")
            for _ in range(200):
                if fixture.scene_requests:
                    break
                await asyncio.sleep(0.01)
            assert fixture.scene_requests
            await asyncio.sleep(10.2)
            timed_out = False
            for _ in range(20):
                if await fixture.module("eos", "m => m.getPlotPresentation(arg).sceneOutcome.terrain?.kind", "eos-0008") == "timed-out":
                    timed_out = True
                    break
                await asyncio.sleep(0.1)
            assert timed_out
            await fixture.module("rows", "m => m.adoptRowPresentations('stale')")
            await asyncio.sleep(1.2)
            generation = await fixture.module("board", "m => m.BOARD_GENERATION")
            await fixture.module("rows", "(m, generation) => m.adoptRowPresentations(generation)", generation)
            fixture.release("scene")
            await fixture.module("rows", "m => m.updateMountedRow('eos-0008')")
            await fixture.page.wait_for_function(
                "() => getComputedStyle(document.querySelector('.editor-scene'), '::before').backgroundImage.includes('url(') && document.querySelector('.eos-status-text')?.textContent !== 'Some art is unavailable'",
                timeout=10000,
            )
            scene_state = await fixture.module(
                "eos",
                "m => { const s = m.getPlotPresentation(arg); return {kind: s.sceneOutcome.terrain?.kind, failed: s.sceneFailed}; }",
                "eos-0008",
            )
            assert scene_state == {"kind": "decoded", "failed": False}
            assert await fixture.band().locator(".eos-status-text").text_content() != "Some art is unavailable"

    asyncio.run(run())
    try:
        asyncio.run(run(retry_cleanup_removed))
    except AssertionError:
        return
    raise AssertionError("the stranded scene retry mutation did not invalidate recovery")
