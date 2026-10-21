import { beginLakeScene, endLakeScene, IMAGE_RETRY_DELAYS_MS, editorSceneLayers, retryImageUrl, worksiteArtGeometry } from './art.js';

const sessions = new Map();
const queue = [];
let activeImages = 0;
let openSerial = 0;
let queueSerial = 0;
let queueTurn = 0;
let pumpScheduled = false;
const plotTurns = new Map();
const CARD_SERVICE_BUDGET_MS = 10000;
const SCENE_SERVICE_BUDGET_MS = 10000;
const SCENE_RELEASE_MS = 1200;
const CARD_RELEASE_MS = 1800;
// the same schedule as art.js -- 1, 2, 4, 8, 15 s, a fresh URL each retry.
const RECOVERY_DELAYS_MS = IMAGE_RETRY_DELAYS_MS;
const MAX_RECOVERY_ATTEMPTS = IMAGE_RETRY_DELAYS_MS.length;
const FAILED_ART_RETRY_SPACING_MS = 30000;
const MAX_RETAINED_ART = 256;
let hooks = {};
const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const forcedColors = () => window.matchMedia('(forced-colors: active)').matches;

function revealNodes(frame) {
  return [...(frame?.querySelectorAll('[data-reveal-group]:not([data-eos-permanent-frame])') || [])];
}

export function configureEos(callbacks) {
  hooks = callbacks;
  let viewportWidth = hooks.geometry().width;
  let rootFont = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
  if (typeof ResizeObserver !== 'undefined') new ResizeObserver(() => {
    const geometry = hooks.geometry();
    if (viewportWidth === geometry.width && rootFont === geometry.font) return;
    viewportWidth = geometry.width;
    rootFont = geometry.font;
    sessions.forEach((session) => {
      const old = session.geometryProfile;
      if (old.width === geometry.width && old.fontSize === geometry.font) return;
      hooks.cancelLease();
      finishPlotReveal(session);
      session.geometryProfile = plotGeometryProfile(geometry.width, geometry.font, Math.max(Number(hooks.row(session.uid)?.capacity?.potential_card_capacity) || 0, Math.max(0, (Number(hooks.row(session.uid)?.capacity?.worker_count) || 0) - (hooks.row(session.uid)?.capacity?.runi_staked === true ? 1 : 0)))) /* staked cards count */;
      const frame = session.mountedFrame;
      if (frame) { frame.style.setProperty('--eos-band-height', `${session.geometryProfile.bandRem}rem`); hooks.redraw(session.uid); }
    });
  }).observe(document.documentElement);
}
export function getPlotPresentation(uid) { return sessions.get(uid); }
// the error value loadPlotDetail() stores after ~31.5 s of failed network
// retries; setStatus() renders it as "Could not reach Ceres." with a Try again button.
export const PLOT_UNREACHABLE_ERROR = 'Could not reach Ceres';
export function setPlotReconnecting(uid, on) {
  const session = sessions.get(uid);
  if (!session) return;
  session.reconnecting = Boolean(on);
  setStatus(session);
}
// the plot route answered 409 board_finishing -- the board on screen is the interim
// artifact and the read's final publish is still landing. The spinner stays on.
export function setPlotFinishing(uid, on) {
  const session = sessions.get(uid);
  if (!session) return;
  session.finishing = Boolean(on);
  setStatus(session);
}

export function plotGeometryProfile(width, fontSize) {
  const narrow = width / fontSize < 62;
  // the facts line is gone; the band is shorter by its height less a .45rem top inset.
  const bandRem = narrow ? 22.5125 : 18.5125; // FR1: +1.25rem (20px) after the facts line was removed
  return { width, fontSize, bandRem, height: bandRem * fontSize, narrow };
}

function owns(session) {
  return sessions.get(session.uid) === session && session.isOpen && session.generation === hooks.generation()
    && (!hooks.player || session.player === hooks.player());
}
function timer(session, fn, ms, onCancel = null) {
  const entry = { handle: null, cancel: null };
  entry.cancel = () => {
    clearTimeout(entry.handle);
    session.timers.delete(entry);
    onCancel?.();
  };
  entry.handle = setTimeout(() => {
    session.timers.delete(entry);
    if (owns(session)) fn();
    else onCancel?.();
  }, ms);
  session.timers.add(entry);
  return entry;
}
function cancelTimers(session) { session.timers.forEach((entry) => entry.cancel()); session.phaseTimers?.clear(); }
function stopPhaseTimer(session, phase) {
  const entry = session.phaseTimers?.get(phase);
  if (entry) entry.cancel();
  session.phaseTimers?.delete(phase);
}
function phaseTimer(session, phase, fn, ms) {
  stopPhaseTimer(session, phase);
  const entry = timer(session, () => { session.phaseTimers.delete(phase); fn(); }, ms);
  session.phaseTimers.set(phase, entry);
  return entry;
}
function nextQueuedJob() {
  const validJobs = queue.filter((job) => job.valid());
  if (!validJobs.length) return null;
  const plotJobs = new Map();
  validJobs.forEach((job) => plotJobs.set(job.plotUid, [...(plotJobs.get(job.plotUid) || []), job]));
  const plots = [...plotJobs.entries()].sort(([, a], [, b]) => {
    const aTurn = plotTurns.get(a[0].plotUid) ?? -1;
    const bTurn = plotTurns.get(b[0].plotUid) ?? -1;
    return (aTurn - bTurn) || (a[0].sequence - b[0].sequence);
  });
  const [plotUid, jobs] = plots[0];
  const sceneJobs = jobs.filter((job) => job.scene);
  const eligible = sceneJobs.length ? sceneJobs : jobs;
  eligible.sort((a, b) => (a.rank - b.rank) || (a.sequence - b.sequence));
  const job = eligible[0];
  queue.splice(queue.indexOf(job), 1);
  plotTurns.set(plotUid, ++queueTurn);
  return job;
}
function pump() {
  while (activeImages < 4 && queue.length) {
    const invalid = queue.find((job) => !job.valid());
    if (invalid) { queue.splice(queue.indexOf(invalid), 1); invalid.resolve({ kind: 'cancelled' }); continue; }
    const job = nextQueuedJob();
    if (!job) break;
    activeImages++;
    job.run().then(job.resolve, () => job.resolve({ kind: 'unavailable' })).finally(() => { activeImages--; schedulePump(); });
  }
}
function schedulePump() {
  if (pumpScheduled) return;
  pumpScheduled = true;
  queueMicrotask(() => { pumpScheduled = false; pump(); });
}
function enqueue(run, valid, { scene = false, plotUid = '', rank = Infinity } = {}) {
  return new Promise((resolve) => {
    const job = { run, valid, scene, plotUid, rank, sequence: queueSerial++,
      resolve: (outcome) => resolve(outcome) };
    queue.push(job);
    schedulePump();
  });
}

function permanentImageFailure(url) {
  const resource = performance.getEntriesByName(url).at(-1);
  const status = resource?.responseStatus;
  const sameOrigin = new URL(url, window.location.href).origin === window.location.origin;
  // A 403/404 is permanent only with an observable status; CDN entries commonly report 0 cross-origin, so use bounded recovery instead of guessing.
  return (sameOrigin || status > 0) && (status === 403 || status === 404);
}

function decodeImage(image, candidates, valid, cleanups, serviceBudget, identity, onDecoded = null) {
  return new Promise((resolve) => {
    let index = 0;
    let settled = false;
    let candidateRevision = 0;
    let sawFailure = false;
    let allFailuresPermanent = true;
    const deadline = performance.now() + serviceBudget;
    const cleanup = () => { clearTimeout(timeout); image.onload = null; image.onerror = null; cleanups.delete(cancel); };
    const finish = (kind, url = null, extra = {}) => {
      if (settled) return;
      settled = true;
      cleanup();
      if (kind === 'decoded') onDecoded?.(image);
      resolve({ kind, url, image: kind === 'decoded' ? image : null, identity, ...extra });
    };
    const cancel = () => finish('cancelled');
    const current = () => valid(identity);
    const timeout = setTimeout(() => finish(current() ? 'timed-out' : 'cancelled'), Math.max(0, deadline - performance.now()));
    cleanups.add(cancel);
    const next = () => {
      if (performance.now() >= deadline) { finish(current() ? 'timed-out' : 'cancelled'); return; }
      if (!current()) { finish('cancelled'); return; }
      if (index >= candidates.length) { finish('unavailable', null, { permanent: sawFailure && allFailuresPermanent }); return; }
      const url = candidates[index++];
      const revision = ++candidateRevision;
      image.onload = async () => {
        try {
          if (!image.naturalWidth || !image.naturalHeight) throw new Error('Empty image');
          await image.decode();
          if (settled || revision !== candidateRevision) return;
          if (!current()) { finish('cancelled'); return; }
          if (!image.naturalWidth || !image.naturalHeight) { next(); return; }
          finish('decoded', url);
        } catch (_) {
          sawFailure = true;
          allFailuresPermanent = false;
          if (!settled && revision === candidateRevision) next();
        }
      };
      image.onerror = async () => {
        sawFailure = true;
        const permanent = await permanentImageFailure(url);
        allFailuresPermanent = allFailuresPermanent && permanent;
        if (settled || revision !== candidateRevision) return;
        if (performance.now() >= deadline) { finish(current() ? 'timed-out' : 'cancelled'); return; }
        if (index >= candidates.length && allFailuresPermanent) finish('unavailable', null, { permanent: true });
        else next();
      };
      image.loading = 'eager';
      if (onDecoded && image.classList?.contains('eos-portrait-fill')) image.style.opacity = '0'; // no visible frame before the fade
      image.src = url;
    };
    next();
  });
}

function setStatus(session) {
  const frame = session.mountedFrame;
  if (!frame) return;
  const text = frame.querySelector('.eos-status-text');
  let message = '';
  const quietRedraw = session.terminalReplacement && session.dataReady && !session.error;
  const unreachable = session.error === PLOT_UNREACHABLE_ERROR;
  if (unreachable) message = 'Could not reach Ceres.';
  else if (session.error) message = `Could not load workers: ${session.error}`;
  else if (session.reconnecting) message = 'Reconnecting to Ceres…';
  else if (session.finishing) message = 'Finishing the read…';
  else if (session.sceneUpdating) message = 'Updating scene…';
  else if (!session.dataReady && !session.quietRetained) message = session.stalled ? 'Still loading workers…' : session.loadingNotice ? 'Loading workers…' : '';
  // a plot that was already revealed and is only being redrawn (a staged change, a
  // pick) must never fall back to "preparing" -- that state hides every control group via
  // ceres.css and flashed "Preparing worker art…" for ~200 ms on every change.
  else if (quietRedraw && (!session.workersReleased || session.pendingArt?.size || session.scenePending?.size)) message = '';
  else if (session.sceneReleased && session.scenePending?.size) message = 'Preparing worker art…';
  else if (!session.workersReleased) message = 'Preparing worker art…';
  else if (session.pendingArt?.size) message = 'Preparing worker art…';
  else if (session.failedArt.size || session.sceneFailed) message = 'Some art is unavailable';
  if (text) { text.textContent = message; text.title = message; }
  // the delayed per-tile loading spinners yield to the reconnecting/error text.
  const spinnersHidden = Boolean(session.error || session.reconnecting);
  frame.querySelectorAll('.slot-loading-spinner').forEach((el) => el.toggleAttribute('hidden', spinnersHidden));
  // the "Retry art" button is gone at his instruction; the message above still
  // says when art is unavailable, and the data-level Retry link below is unchanged.
  frame.querySelector('.expand-retry-link')?.toggleAttribute('hidden', !session.error || unreachable);
  // the Try again button exists only in the unreachable state; picker-actions.js
  // handles data-slot-action="retry-detail" by restarting loadPlotDetail().
  const retryButton = frame.querySelector('.slot-retry[data-slot-action="retry-detail"]');
  if (unreachable && !retryButton && text) {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'slot-retry ceres-btn ceres-btn--secondary'; button.dataset.controlDensity = 'compact'; button.dataset.slotAction = 'retry-detail'; button.dataset.deedUid = session.uid; button.textContent = 'Try again';
    text.insertAdjacentElement('afterend', button);
  } else if (!unreachable && retryButton) retryButton.remove();
  frame.setAttribute('aria-busy', String(!session.error && !session.workersReleased));
  frame.dataset.plotState = session.error ? 'error' : session.sceneReleased && session.scenePending?.size ? 'pending-art'
    : !session.workersReleased ? (quietRedraw ? 'ready' : 'preparing') : session.pendingArt?.size ? 'pending-art' : (session.failedArt.size || session.sceneFailed ? 'degraded' : 'ready');
}

function animate(session, node, frames, options) {
  if (!node || reducedMotion() || forcedColors() || !session.animateAllowed || typeof node.animate !== 'function') return null;
  try {
    node.style.willChange = 'opacity';
    const handle = node.animate(frames, options);
    session.animations.add(handle);
    const cleanup = timer(session, () => {
      session.animations.delete(handle);
      session.animationTimers.delete(handle);
      node.style.willChange = '';
    }, Number(options.duration || 0) + Math.max(0, Number(options.delay || 0)));
    session.animationTimers.set(handle, cleanup);
    return handle;
  } catch (_) { node.style.willChange = ''; return null; }
}

// the loading markers must come off wherever a picture becomes final
// WITHOUT the first-time fade -- reused after a re-render, already decoded, or failed
// or the tile keeps its empty look and its name stays hidden.
function clearTilePending(tile) {
  tile?.classList?.remove('is-art-pending', 'is-caption-pending');
}

function settlePortraitFill(session, image) {
  // the name fades in WITH the picture (caption marker off now), but the tile
  // keeps its empty-slot look until the picture is fully opaque over it -- dropping that
  // at the fade's start let the dark tile fill snap in under a still-transparent picture.
  const tile = image.closest?.('.slot-tile');
  tile?.classList.remove('is-caption-pending');
  image.style.opacity = '1';
  image.style.transform = 'scale(1)';
  // a worker just picked into a slot (filling it or replacing its worker) fades
  // in the same way as the opening reveal, even after the reveal itself has finished.
  const freshPick = !!tile?.matches?.('.is-staged, .is-pending-add');
  if (reducedMotion() || forcedColors() || (!session.animateAllowed && !freshPick) || image.dataset.eosFillShown === '1') {
    tile?.classList.remove('is-art-pending');
    return;
  }
  image.dataset.eosFillShown = '1';
  image.classList.add('eos-portrait-pop-in');
  const cleanup = timer(session, () => {
    session.portraitFillTimers.delete(image);
    image.classList.remove('eos-portrait-pop-in');
    image.style.opacity = '1';
    image.style.transform = 'scale(1)';
    tile?.classList.remove('is-art-pending');
  }, 440); // matches the 420ms soft fade-in (ceres.css) plus a frame
  session.portraitFillTimers.set(image, cleanup);
}

function settlePortraitFills(session) {
  session.portraitFillTimers.forEach((entry) => entry.cancel());
  session.portraitFillTimers.clear();
  session.mountedFrame?.querySelectorAll('.slot-tile.is-art-pending, .slot-tile.is-caption-pending').forEach((tile) => tile.classList.remove('is-art-pending', 'is-caption-pending')); // backstop
  session.mountedFrame?.querySelectorAll('.eos-portrait-fill').forEach((image) => {
    image.classList.remove('eos-portrait-pop-in');
    image.style.opacity = '1';
    image.style.transform = 'scale(1)';
  });
}

export function finishPlotReveal(session) {
  if (!session) return;
  session.animateAllowed = false;
  session.scenicVeilAnimating = false;
  settlePortraitFills(session);
  const veil = session.mountedFrame?.querySelector('.eos-scenic-veil');
  if (veil) veil.hidden = true;
  session.animationTimers.forEach((entry) => entry.cancel());
  session.animationTimers.clear();
  session.animations.forEach((animation) => { try { animation.finish(); } catch (_) { animation.cancel(); } });
  session.animations.clear();
  revealNodes(session.mountedFrame).forEach((node) => { node.style.opacity = ''; node.style.willChange = ''; });
}

function applyScene(session) {
  const frame = session.mountedFrame;
  if (!frame) return;
  const outcomes = session.sceneOutcome || {};
  for (const [name, outcome] of Object.entries(outcomes)) {
    if (name === 'worksite' || name === 'worksiteFallback') continue;
    frame.style.setProperty(`--scene-${name}`, outcome.kind === 'decoded' ? `url("${outcome.url}")` : 'none');
  }
  const primary = outcomes.worksite;
  const fallback = outcomes.worksiteFallback;
  const chosen = primary?.kind === 'decoded' ? primary : fallback?.kind === 'decoded' ? fallback : null;
  frame.style.setProperty('--scene-worksite', chosen ? `url("${chosen.url}")` : 'none');
  const geometry = worksiteArtGeometry(session.sceneRow, { fallback: Boolean(chosen && chosen === fallback) });
  const art = frame.querySelector('.editor-worksite-art');
  if (art && geometry) {
    art.style.setProperty('--art-cx', geometry.cx);
    art.style.setProperty('--art-base', geometry.base);
    art.style.setProperty('--art-h', `${geometry.hRem}rem`);
    art.style.setProperty('--art-l', geometry.l);
    art.style.setProperty('--art-t', geometry.t);
    art.style.setProperty('--art-r', geometry.r);
    art.style.setProperty('--art-ratio', geometry.ratio);
  }
  const badges = frame.querySelector('[data-terrain-affinity="editor"]');
  if (badges) {
    badges.inert = !session.sceneReleased;
    badges.setAttribute('aria-hidden', String(!session.sceneReleased));
    badges.style.visibility = session.sceneReleased ? '' : 'hidden';
  }
  const veil = frame.querySelector('.eos-scenic-veil');
  if (veil) veil.hidden = true;
}

function settleWorkers(session, revision) {
  if (!owns(session) || revision !== session.artRevision || !session.sceneReleased || !session.cardsSettled || session.workersReleased) return;
  const release = () => {
    if (!owns(session) || revision !== session.artRevision || session.workersReleased) return;
    session.workersReleased = true;
    session.phase = session.pendingArt.size || session.scenePending?.size ? 'pending-art' : (session.failedArt.size || session.sceneFailed ? 'degraded' : 'ready');
    const frame = session.mountedFrame;
    frame?.querySelector('.eos-worker-cover')?.setAttribute('hidden', '');
    const groups = revealNodes(frame);
    groups.forEach((node) => { node.inert = false; node.removeAttribute('aria-hidden'); node.style.visibility = ''; });
    setStatus(session);
    groups.forEach((node) => animate(session, node, [{ opacity: 0 }, { opacity: 1 }], {
      duration: 220, delay: Math.min(3, Number(node.dataset.revealWave || 0)) * 35, fill: 'backwards', easing: 'cubic-bezier(0.22, 1, 0.36, 1)',
    }));
  };
  const lead = session.animateAllowed && !reducedMotion() ? Math.max(0, session.sceneReleasedAt + 90 - performance.now()) : 0;
  if (lead) timer(session, release, lead); else release();
}

function sceneRecord(session, name, url) {
  let record = session.sceneRecords.get(name);
  if (!record || record.url !== url) {
    record = { url, outcome: null, recoveryAttempts: 0, retryScheduled: false, nextRetryAt: 0, frozenAt: 0, frozenRevision: -1, frozen: false };
    session.sceneRecords.set(name, record);
  }
  return record;
}

function captureSceneStyles(session, frame = session?.mountedFrame) {
  if (!session || !frame) return;
  session.sceneStyles ||= {};
  ['--scene-terrain', '--scene-worksite', '--scene-y'].forEach((property) => {
    const value = frame.style.getPropertyValue(property);
    if (value) session.sceneStyles[property] = value;
  });
}

function restoreSceneStyles(session, band) {
  if (!session?.sceneStyles || !band) return;
  Object.entries(session.sceneStyles).forEach(([property, value]) => band.style.setProperty(property, value));
}

function callbackIdentity(session, assetKey, attempt) {
  return { session: session.uid, player: session.player, deed: session.uid, epoch: session.openEpoch, asset: assetKey, attempt };
}

function freezeScene(session, name, record) {
  if (record.frozen) return;
  record.frozen = true;
  record.frozenAt = performance.now();
  record.frozenRevision = session.sceneRevision;
}

function scheduleSceneRecovery(session, name, record) {
  if (record.retryScheduled || record.recoveryAttempts >= MAX_RECOVERY_ATTEMPTS) return;
  const delay = RECOVERY_DELAYS_MS[record.recoveryAttempts];
  record.recoveryAttempts++;
  record.nextRetryAt = performance.now() + delay;
  record.retryScheduled = true;
  const reset = () => {
    record.retryScheduled = false;
    record.nextRetryAt = 0;
  };
  timer(session, () => {
    reset();
    if (session.sceneRecords.get(name) !== record) return;
    session.retryingScene = true;
    if (session.mountedFrame && session.sceneRow) prepareScene(session, session.sceneRow);
    else session.retryingScene = false;
  }, delay, reset);
}

function rememberSceneFailure(session, name, outcome, record) {
  record.outcome = outcome;
  if (outcome.permanent || record.recoveryAttempts >= MAX_RECOVERY_ATTEMPTS) {
    freezeScene(session, name, record);
    record.outcome = { kind: 'unavailable', permanent: true };
    session.sceneOutcome[name] = record.outcome;
    session.sceneFailed = true;
    applyScene(session);
    setStatus(session);
  } else scheduleSceneRecovery(session, name, record);
}

function prepareScene(session, row) {
  const layers = editorSceneLayers(row);
  const key = JSON.stringify(layers);
  const sameKey = session.sceneKey === key;
  const retrying = !!session.retryingScene;
  session.retryingScene = false;
  const wasReleased = session.sceneReleased;
  session.sceneRow = row;
  const needsRecovery = sameKey && Object.entries(layers).some(([name, url]) => {
    if (!url) return false;
    const record = sceneRecord(session, name, url);
    return record.outcome?.kind && record.outcome.kind !== 'decoded' && !record.frozen && !record.retryScheduled;
  });
  if (sameKey && !retrying && !needsRecovery) { applyScene(session); schedulePortraitEligibility(session); return; }
  if (!sameKey) session.sceneCleanups.forEach((cancel) => cancel());
  session.sceneKey = key;
  session.sceneRevision++;
  const revision = session.sceneRevision;
  session.sceneSoftReleased = false;
  const retainedOutcomes = {};
  Object.entries(layers).forEach(([name, url]) => {
    if (!url) return;
    const record = sceneRecord(session, name, url);
    if (record.outcome?.kind === 'decoded') retainedOutcomes[name] = record.outcome;
  });
  const keepVisible = sameKey && wasReleased;
  session.sceneReleased = keepVisible;
  session.sceneOutcome = retainedOutcomes;
  session.scenePending = new Set(Object.entries(layers).filter(([name, url]) => url && !retainedOutcomes[name]).map(([name]) => name));
  session.sceneUpdating = wasReleased && !keepVisible;
  if (session.sceneUpdating) session.animateAllowed = false;
  const frame = session.mountedFrame;
  if (!keepVisible && !retrying) { frame?.style.setProperty('--scene-terrain', 'none'); frame?.style.setProperty('--scene-worksite', 'none'); }
  applyScene(session);
  setStatus(session);
  const valid = (identity = null) => owns(session) && session.sceneRevision === revision
    && (!identity || (identity.session === session.uid && identity.player === session.player
      && identity.deed === session.uid && identity.epoch === session.openEpoch && identity.revision === revision));
  const releaseScene = () => {
    if (!valid() || session.sceneReleased) return;
    const outcomes = { ...session.sceneOutcome };
    Object.entries(layers).forEach(([name, url]) => {
      if (!url) outcomes[name] = { kind: 'unavailable', absent: true };
      else if (outcomes[name]?.kind !== 'decoded') outcomes[name] = { kind: 'timed-out' };
    });
    session.sceneOutcome = outcomes;
    session.scenePending = new Set(Object.entries(outcomes).filter(([name, outcome]) => layers[name] && outcome.kind !== 'decoded').map(([name]) => name));
    session.sceneFailed = false;
    stopPhaseTimer(session, 'scene');
    session.sceneReleased = true;
    session.sceneUpdating = false;
    session.sceneSoftReleased = true;
    session.sceneReleasedAt = performance.now();
    session.phase = session.scenePending.size ? 'pending-art' : 'scene-visible';
    session.scenicVeilAnimating = false;
    applyScene(session);
    setStatus(session);
    schedulePortraitEligibility(session);
    settleWorkers(session, session.artRevision);
  };
  const sceneReleaseTimer = timer(session, releaseScene, SCENE_RELEASE_MS);
  session.phaseTimers.set('scene', sceneReleaseTimer);
  Promise.all(Object.entries(layers).map(async ([name, url]) => {
    if (!url) return [name, { kind: 'unavailable', absent: true }];
    const record = sceneRecord(session, name, url);
    if (record.frozen && session.sceneRevision > record.frozenRevision && performance.now() - record.frozenAt >= FAILED_ART_RETRY_SPACING_MS) {
      record.frozen = false;
      record.recoveryAttempts = 0;
      record.retryScheduled = false;
      record.nextRetryAt = 0;
    }
    if (record.outcome?.kind === 'decoded') return [name, record.outcome];
    if (record.outcome?.kind === 'unavailable' && !record.frozen) record.outcome = null;
    if (record.frozen || record.retryScheduled || (record.nextRetryAt && performance.now() < record.nextRetryAt) || record.outcome?.kind === 'unavailable') {
      return [name, record.outcome || { kind: 'unavailable', permanent: record.frozen }];
    }
    const outcome = await enqueue(
      () => decodeImage(new Image(), [retryImageUrl(url, record.recoveryAttempts)], valid, session.sceneCleanups, SCENE_SERVICE_BUDGET_MS, { ...callbackIdentity(session, `${name}:${url}`, record.recoveryAttempts), revision }), valid,
      { scene: true, plotUid: session.uid, rank: 0 },
    );
    if (valid() && outcome.kind !== 'decoded' && outcome.kind !== 'cancelled') rememberSceneFailure(session, name, outcome, record);
    else if (valid()) {
      record.outcome = outcome;
      if (outcome.kind === 'decoded') {
        const outcomes = { ...session.sceneOutcome, [name]: outcome };
        session.sceneFailed = Object.values(outcomes).some((value) => value.kind === 'unavailable' && !value.absent);
        if (outcome.kind === 'decoded') session.scenePending.delete(name);
      }
    }
    return [name, outcome];
  })).then((entries) => {
    if (!valid()) return;
    stopPhaseTimer(session, 'scene');
    session.sceneOutcome = Object.fromEntries(entries);
    session.scenePending = new Set(entries.filter(([name, value]) => layers[name] && value.kind !== 'decoded' && !session.sceneRecords.get(name)?.frozen).map(([name]) => name));
    session.sceneFailed = entries.some(([name, value]) => value.kind === 'unavailable' && !value.absent
      && (value.permanent || session.sceneRecords.get(name)?.frozen)
      && !(name === 'worksite' && session.sceneOutcome.worksiteFallback?.kind === 'decoded'));
    session.sceneReleased = true;
    session.sceneUpdating = false;
    session.retryingScene = false;
    session.sceneReleasedAt = performance.now();
    session.phase = session.scenePending.size ? 'pending-art' : 'scene-visible';
    session.scenicVeilAnimating = !session.sceneSoftReleased && !retrying && !keepVisible && session.animateAllowed && !reducedMotion();
    applyScene(session);
    if (session.mountedFrame?.querySelector('.eos-scenic-veil')) session.mountedFrame.querySelector('.eos-scenic-veil').hidden = true;
    setStatus(session);
    schedulePortraitEligibility(session);
    settleWorkers(session, session.artRevision);
  });
}

function fallback(image) {
  const holder = image.parentElement;
  if (!holder) return;
  const card = document.createElement('span');
  card.className = 'card-art-fallback eos-art-fallback';
  card.dataset.eosState = 'art-unavailable';
  card.dataset.cardImageCandidates = image.dataset.cardImageCandidates;
  card.dataset.cardImageName = image.dataset.cardImageName;
  // R-163: this used to append a `.card-art-fallback-name` span carrying the card
  // name INTO `.slot-art`, at the top of the tile -- while `.slot-caption`'s `.slot-name`
  // kept showing the same name at the bottom. The maintainer saw both at once the moment art
  // started failing: "the name is on a badge on top of tile plus on the bottom as well".
  // His ruling settles the direction -- "would have to be on the bottom, to never block the
  // card art, which has uually their head there" -- so the TOP occurrence goes and the
  // caption is untouched. The name is still carried on `data-card-image-name` (set above)
  // for anything that needs it; only the visible duplicate is removed.
  // `.card-art-fallback-name` itself stays live in ceres.css and in editor.js's separate
  // non-Eos path, which is why no CSS change belongs here.
  const label = document.createElement('span');
  label.className = 'card-art-fallback-marker';
  label.textContent = 'Art unavailable';
  card.append(label);
  holder.classList.add('card-art-fallback-container');
  image.removeAttribute('src');
  image.replaceWith(card);
}

function decodedUrl(image) {
  return image.dataset.eosResolvedUrl || image.currentSrc || image.src || '';
}

function artSlotKey(image) {
  const tile = image.closest('[data-group-id][data-slot-index]');
  return tile ? `${tile.dataset.groupId}:${tile.dataset.slotIndex}:${image.dataset.subApertureRole || 'portrait'}` : '';
}

function artAssetKey(image) {
  return image.dataset.cardImageCandidates || decodedUrl(image);
}

function trimRetainedArt(session) {
  if (session.retainedArt.length <= MAX_RETAINED_ART) return;
  const evicted = new Set(session.retainedArt.splice(0, session.retainedArt.length - MAX_RETAINED_ART));
  session.apertureRecords.forEach((record, key) => { if (evicted.has(record.node)) session.apertureRecords.delete(key); });
}

export function captureDecodedArt(session, frame = session?.mountedFrame) {
  if (!session || !frame) return;
  const retained = new Set(session.retainedArt);
  frame.querySelectorAll('img[data-eos-managed][data-eos-outcome="decoded"]').forEach((image) => {
    const url = decodedUrl(image);
    if (!url || retained.has(image)) return;
    image.dataset.eosResolvedUrl = url;
    const slotKey = artSlotKey(image);
    if (slotKey) image.dataset.eosRetainedSlot = slotKey;
    const assetKey = artAssetKey(image);
    const apertureKey = slotKey || `node:${session.retainedArt.length}`;
    session.apertureRecords.set(apertureKey, { node: image, assetKey, url });
    session.retainedArt.push(image);
    retained.add(image);
  });
  trimRetainedArt(session);
}

export function spliceDecodedArt(uid, previousBand, nextBand) {
  const session = sessions.get(uid);
  if (!session || !nextBand) return;
  captureDecodedArt(session, previousBand);
  const buckets = new Map();
  const apertures = new Map();
  session.retainedArt.forEach((old) => {
    const url = decodedUrl(old);
    const key = artAssetKey(old);
    const apertureKey = old.dataset.eosRetainedSlot || '';
    if (key) buckets.set(key, [...(buckets.get(key) || []), old]);
    if (apertureKey) apertures.set(apertureKey, old);
  });
  const consumed = new Set();
  nextBand.querySelectorAll('img[data-eos-managed]').forEach((image) => {
    const slotKey = artSlotKey(image);
    const candidates = image.dataset.cardImageCandidates || '';
    const bucket = buckets.get(candidates) || [];
    const exact = slotKey ? apertures.get(slotKey) : null;
    const compatible = (old) => {
      const oldName = old.dataset.cardImageName || '';
      const newName = image.dataset.cardImageName || '';
      return artAssetKey(old) === candidates && (!oldName || !newName || oldName === newName);
    };
    // the `&&` chain used to evaluate to compatible()'s boolean `true` instead of
    // the element itself, so a same-slot re-render (opening the picker, removing a card)
    // handed `true` to decodedUrl() and threw, aborting the click.
    const old = (exact && !consumed.has(exact) && compatible(exact) ? exact : null)
      || bucket.find((candidate) => !consumed.has(candidate) && compatible(candidate));
    if (!old) return;
    const url = decodedUrl(old);
    if (!url) return;
    old.dataset.eosOutcome = 'decoded';
    old.dataset.eosResolvedUrl = url;
    old.alt = image.alt;
    old.style.visibility = '';
    image.replaceWith(old);
    clearTilePending(old.closest?.('.slot-tile')); // reused picture, no fade
    consumed.add(old);
  });
  if (consumed.size) {
    session.retainedArt = session.retainedArt.filter((image) => !consumed.has(image));
    session.apertureRecords.forEach((record, key) => { if (consumed.has(record.node)) session.apertureRecords.delete(key); });
  }
}

export function reconcilePlotHandoff(uid, previousBand, nextBand) {
  const session = sessions.get(uid);
  if (!session || !nextBand) return nextBand;
  captureDecodedArt(session, previousBand);
  restoreSceneStyles(session, nextBand);
  return nextBand;
}

function artRecord(session, key) {
  let record = session.assetRecords.get(key);
  if (!record) {
    record = { recoveryAttempts: 0, retryScheduled: false, retryRevision: -1, nextRetryAt: 0, inFlightPromise: null, inFlightRevision: -1, failureRevision: -1, frozen: false };
    session.assetRecords.set(key, record);
  }
  return record;
}

function freezeArt(session, key, record) {
  if (record.frozen) return;
  record.frozen = true;
  record.frozenAt = performance.now();
  record.frozenRevision = session.artRevision;
  session.failedArt.add(key);
}

function ownDecodedNode(image, url, valid, identity, cleanups, onDecoded = null) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (kind) => {
      if (settled) return;
      settled = true;
      image.onload = null;
      image.onerror = null;
      cleanups.delete(cancel);
      resolve(kind === 'decoded' ? { kind, url, image, identity } : { kind, url: null, image: null, identity });
    };
    const cancel = () => finish('cancelled');
    cleanups.add(cancel);
    image.onload = async () => {
      try {
        if (!image.naturalWidth || !image.naturalHeight) throw new Error('Empty image');
        await image.decode();
        if (!valid(identity)) return finish('cancelled');
        finish('decoded');
        onDecoded?.(image);
      } catch (_) { finish('unavailable'); }
    };
    image.onerror = () => finish('unavailable');
    image.loading = 'eager';
    if (onDecoded && image.classList?.contains('eos-portrait-fill')) image.style.opacity = '0'; // no visible frame before the fade
    image.src = url;
  });
}

function queueArtDecode(session, image, source, valid, revision, rank, record, assetKey, aperture, onDecoded) {
  if (record.inFlightPromise && record.inFlightRevision === revision) {
    return record.inFlightPromise.then((outcome) => outcome.kind === 'decoded'
      ? ownDecodedNode(image, outcome.url, valid, { ...outcome.identity, aperture }, session.artCleanups, onDecoded)
      : outcome);
  }
  const promise = enqueue(
    () => decodeImage(image, source, valid, session.artCleanups, CARD_SERVICE_BUDGET_MS, { ...callbackIdentity(session, assetKey, record.recoveryAttempts), revision, aperture }, onDecoded), valid,
    { plotUid: session.uid, rank },
  );
  record.inFlightPromise = promise;
  record.inFlightRevision = revision;
  promise.finally(() => {
    if (record.inFlightPromise === promise && record.inFlightRevision === revision) {
      record.inFlightPromise = null;
      record.inFlightRevision = -1;
    }
  });
  return promise;
}

function scheduleArtRecovery(session, key, record) {
  if (record.retryScheduled || record.recoveryAttempts >= MAX_RECOVERY_ATTEMPTS) return;
  const delay = RECOVERY_DELAYS_MS[record.recoveryAttempts];
  record.recoveryAttempts++;
  record.retryRevision = session.artRevision + 1;
  record.nextRetryAt = performance.now() + delay;
  record.retryScheduled = true;
  timer(session, () => {
    record.retryScheduled = false;
    record.nextRetryAt = 0;
    if (session.mountedFrame && session.dataReady) {
      if (record.retryRevision > session.artRevision) {
        session.artRevision = record.retryRevision;
        session.mountedFrame.dataset.artRevision = String(session.artRevision);
        session.cardPhaseRevision = -1;
        session.cardsSettled = false;
        session.pendingArt.clear();
      }
      prepareWorkers(session);
    }
  }, delay);
}

function rememberArtFailure(session, key, outcome, record) {
  record.lastOutcome = outcome.kind === 'timed-out' ? 'deferred' : outcome.kind;
  if (outcome.permanent || record.recoveryAttempts >= MAX_RECOVERY_ATTEMPTS) freezeArt(session, key, record);
  else scheduleArtRecovery(session, key, record);
}

function visualRank(image, index) {
  const tile = image.closest('[data-group-id="worker"][data-slot-index]');
  if (!tile) return 1000 + index;
  const band = image.closest('.editor-band');
  const slots = [...(band?.querySelectorAll('[data-group-id="worker"][data-slot-index]') || [])];
  const rank = slots.indexOf(tile);
  return rank >= 0 ? rank + 1 : 1000 + index;
}

function settleDecodedImage(image, outcome) {
  const settledImage = outcome.image === image ? image : null;
  if (!settledImage) return image;
  settledImage.dataset.eosOutcome = 'decoded';
  settledImage.dataset.eosResolvedUrl = outcome.url;
  settledImage.alt = image.alt;
  return settledImage === image ? image : (image.replaceWith(settledImage), settledImage);
}

function artApertureKey(image, index) {
  return artSlotKey(image) || `${artAssetKey(image)}:${index}`;
}

function schedulePortraitEligibility(session) {
  if (!session.sceneReleased || session.portraitEligibilityRevision === session.artRevision) return;
  const frame = session.mountedFrame;
  const revision = session.artRevision;
  session.portraitEligibilityRevision = revision;
  requestAnimationFrame(() => requestAnimationFrame(() => {
    if (!owns(session) || revision !== session.artRevision || session.mountedFrame !== frame || !session.sceneReleased) return;
    session.firstPaintAt = performance.now();
    session.portraitEligible = true;
    prepareWorkers(session);
  }));
}

function prepareWorkers(session) {
  const frame = session.mountedFrame;
  const revision = session.artRevision;
  if (!frame || session.error) return;
  if (!session.dataReady) {
    if (session.dataWatchdogRevision !== revision) {
      session.dataWatchdogRevision = revision;
      timer(session, () => {
        if (session.artRevision === revision && session.mountedFrame && !session.error && !session.dataReady) hooks.redraw(session.uid);
      }, CARD_SERVICE_BUDGET_MS);
    }
    return;
  }
  if (!session.portraitEligible) return;
  const valid = (identity = null) => owns(session) && revision === session.artRevision && session.mountedFrame === frame
    && (!identity || (identity.session === session.uid && identity.player === session.player
      && identity.deed === session.uid && identity.epoch === session.openEpoch && identity.revision === revision));
  if (session.cardPhaseRevision !== revision) {
    session.cardPhaseRevision = revision;
    phaseTimer(session, 'cards', () => {
      if (!valid()) return;
      session.cardsSettled = true;
      settleWorkers(session, revision);
    }, CARD_RELEASE_MS);
  }
  const images = [...frame.querySelectorAll('img[data-eos-managed]')];
  Promise.all(images.map(async (image, index) => {
    const candidates = JSON.parse(image.dataset.cardImageCandidates || '[]');
    const key = JSON.stringify(candidates);
    if (!candidates.length) return;
    const record = artRecord(session, key);
    const aperture = artApertureKey(image, index);
    const apertureValid = (identity = null) => valid(identity) && image.isConnected !== false
      && (frame.contains?.(image) !== false) && (!identity || identity.aperture === aperture);
    session.pendingArt.add(aperture);
    if (record.frozen && session.artRevision > record.frozenRevision && performance.now() - record.frozenAt >= FAILED_ART_RETRY_SPACING_MS) {
      record.frozen = false;
      record.recoveryAttempts = 0;
      record.nextRetryAt = 0;
      session.failedArt.delete(key);
    }
    const successfulUrl = session.cardOutcomes.get(key);
    const tileEl = image.closest?.('.slot-tile'); // captured before any fallback swap
    if (image.dataset.eosOutcome === 'decoded' && successfulUrl) {
      image.style.visibility = '';
      clearTilePending(tileEl);
      session.pendingArt.delete(aperture);
      setStatus(session);
      return;
    }
    if (session.terminalReplacement) image.style.visibility = 'hidden';
    let outcome;
    if (record.frozen || session.failedArt.has(key)) outcome = { kind: 'unavailable', permanent: true };
    else if (record.nextRetryAt && performance.now() < record.nextRetryAt) outcome = { kind: 'deferred' };
    else {
      const source = successfulUrl ? [successfulUrl] : candidates;
      outcome = await queueArtDecode(session, image, source, apertureValid, revision, visualRank(image, index), record, key, aperture,
        () => settlePortraitFill(session, image));
    }
    if (!apertureValid(outcome.identity)) return;
    image.style.visibility = '';
    if (outcome.kind !== 'decoded') clearTilePending(tileEl); // no picture coming -- show the name
    if (outcome.kind === 'decoded') {
      if (outcome.image !== image) return;
      const settledImage = settleDecodedImage(image, outcome);
      settledImage.style.visibility = '';
      session.cardOutcomes.set(key, outcome.url);
      session.pendingArt.delete(aperture);
      setStatus(session);
    } else if (outcome.kind === 'timed-out') {
      if (record.failureRevision !== revision) {
        record.failureRevision = revision;
        rememberArtFailure(session, key, outcome, record);
      }
      image.dataset.eosOutcome = 'deferred';
      image.removeAttribute('src');
    } else if (outcome.kind !== 'deferred') {
      if (record.failureRevision !== revision) {
        record.failureRevision = revision;
        rememberArtFailure(session, key, outcome, record);
      }
      if (record.frozen || session.failedArt.has(key)) fallback(image);
      else { image.dataset.eosOutcome = outcome.kind; image.removeAttribute('src'); }
      if (record.frozen || session.failedArt.has(key)) session.pendingArt.delete(aperture);
      setStatus(session);
    }
  })).then(() => {
    if (!valid()) return;
    stopPhaseTimer(session, 'cards');
    session.cardsSettled = true;
    settleWorkers(session, revision);
  });
}

export function beginPlotOpening(row, width, fontSize) {
  closePlotPresentation(row.deed_uid);
  const session = createSession(row, width, fontSize);
  session.openEpoch = ++openSerial;
  session.openingConsumed = true;
  session.animateAllowed = true;
  session.scrollLease = hooks.acquireLease({ boardGeneration: session.generation, deedUid: row.deed_uid, openEpoch: session.openEpoch }, () => cancelCentre(session));
  return session;
}
function createSession(row, width, fontSize) {
  beginLakeScene(row);
  const assetRecords = new Map();
  const session = { uid: row.deed_uid, player: row.player || row.owner || hooks.player?.() || '', generation: hooks.generation(), openEpoch: 0, artRevision: 0, sceneRevision: 0,
    isOpen: true, openingConsumed: false, animateAllowed: false, phase: 'preparing',
    geometryProfile: plotGeometryProfile(width, fontSize, Math.max(Number(row?.capacity?.potential_card_capacity) || 0, Math.max(0, (Number(row?.capacity?.worker_count) || 0) - (row?.capacity?.runi_staked === true ? 1 : 0)))) /* staked cards count */, startedAt: performance.now(),
    animations: new Set(), animationTimers: new Map(), portraitFillTimers: new Map(), timers: new Set(), artCleanups: new Set(), sceneCleanups: new Set(), failedArt: new Set(), pendingArt: new Set(), scenePending: new Set(), cardOutcomes: new Map(),
    assetRecords, artRecords: assetRecords, apertureRecords: new Map(), retainedArt: [], sceneRecords: new Map(), sceneStyles: {}, sceneOutcome: {}, sceneReleased: false, sceneSoftReleased: false, portraitEligible: false, portraitEligibilityRevision: -1, workersReleased: false, cardsSettled: false, phaseTimers: new Map(), quietRetained: false, reconnecting: false, finishing: false, lastRealBand: null, mountedFrame: null };
  sessions.set(row.deed_uid, session);
  phaseTimer(session, 'loading-notice', () => { session.loadingNotice = true; setStatus(session); }, 1200);
  phaseTimer(session, 'stall', () => { session.stalled = true; setStatus(session); }, 8000);
  return session;
}

export function ensurePlotPresentation(row, width, fontSize) {
  return sessions.get(row.deed_uid) || createSession(row, width, fontSize);
}
export function beforePlotContentReplace(uid, { readiness = false } = {}) {
  const session = sessions.get(uid);
  if (!session) return;
  if (session.mountedFrame && !session.mountedFrame.hasAttribute('data-eos-detail-scaffold')
    && session.mountedFrame.querySelector('[data-reveal-group]') && (session.workersReleased || session.terminalReplacement)) {
    session.lastRealBand = session.mountedFrame;
  }
  captureDecodedArt(session);
  captureSceneStyles(session);
  if (session.workersReleased) finishPlotReveal(session);
  session.artRevision++;
  session.artCleanups.forEach((cancel) => cancel());
  session.pendingArt.clear();
  session.portraitEligible = false;
  session.portraitEligibilityRevision = -1;
  session.cardsSettled = false;
  session.terminalReplacement = session.workersReleased || session.terminalReplacement;
  session.workersReleased = false;
}
export function parkPlotPresentation(uid, options = {}) {
  beforePlotContentReplace(uid, options);
}
export function mountPlotPresentation(row, frame, { dataReady = false, error = null } = {}) {
  const session = sessions.get(row.deed_uid);
  if (!session || !owns(session)) return;
  // adjacent: the quiet board re-read () can hand back an untrusted body=''
  // frame for a plot that is already fully revealed. Retain that last real band view rather
  // than adopting the emptied replacement; a later call with dataReady=true (or an error)
  // reconciles through the normal atomic handoff instead of blanking the tile in between.
  const hadRealView = session.dataReady && !session.error && session.mountedFrame
    && !session.mountedFrame.hasAttribute('data-eos-detail-scaffold')
    && (session.workersReleased || session.terminalReplacement);
  if (!dataReady && !error && hadRealView) {
    session.quietRetained = true;
    setStatus(session);
    return;
  }
  session.mountedFrame = frame;
  session.dataReady = dataReady;
  session.error = error;
  if (dataReady) {
    session.quietRetained = false;
    stopPhaseTimer(session, 'loading-notice');
    stopPhaseTimer(session, 'stall');
  }
  frame.dataset.openEpoch = String(session.openEpoch);
  frame.dataset.artRevision = String(session.artRevision);
  frame.querySelector('[data-reveal-footer]')?.querySelectorAll(':scope > :not([data-terrain-affinity])').forEach((node) => { node.dataset.revealGroup = 'controls'; });
  revealNodes(frame).forEach((node) => { node.inert = !session.terminalReplacement; node.setAttribute('aria-hidden', String(!session.terminalReplacement)); node.style.visibility = session.terminalReplacement ? '' : 'hidden'; });
  const detailScaffold = frame.hasAttribute('data-eos-detail-scaffold');
  frame.querySelectorAll('[data-eos-permanent-frame]').forEach((node) => { node.inert = detailScaffold; node.setAttribute('aria-hidden', String(detailScaffold)); node.style.visibility = ''; });
  frame.querySelector('.eos-worker-cover')?.setAttribute('hidden', '');
  frame.querySelector('.eos-close')?.addEventListener('click', () => hooks.close(row.deed_uid));
  frame.onpointerdown = () => { hooks.cancelLease(); finishPlotReveal(session); };
  frame.onfocusin = () => { hooks.cancelLease(); finishPlotReveal(session); };
  frame.onclick = () => { hooks.cancelLease(); finishPlotReveal(session); };

  if (!session.quietRetained || dataReady) {
    prepareScene(session, row);
  }
  setStatus(session);
}

function cancelCentre(session) {
  if (session.scrollRaf != null) cancelAnimationFrame(session.scrollRaf);
  session.scrollRaf = null;
  session.scrollLease = null;
}
// an opening plot's ROW lands directly under the sticky header
// the same line every other plot scroll uses (table.js scrollPlotUnderHeader) -- with this reveal's own 300 ms
// ease-out under the viewport lease. It used to centre the plot in the window.
export function centreReservedPlot(session, frame) {
  if (!session.scrollLease || !hooks.leaseIsCurrent(session.scrollLease)) return;
  const { scrollX: x, scrollY: y } = window; // V (no page zoom)
  const plotRow = frame.closest('tr.expand-row')?.previousElementSibling;
  // Re-read every frame: the page is still growing while the plot opens, so a goal fixed at the start lands short.
  const goal = () => {
    const rowTop = (plotRow || frame).getBoundingClientRect().top; // V (no page zoom)
    const headerLine = Math.max(0, ...['header.board-header', '#table-head-sticky'].map((selector) => document.querySelector(selector)?.getBoundingClientRect().bottom || 0)); // V (no page zoom)
    const extent = Math.max(0, document.documentElement.getBoundingClientRect().height - window.innerHeight); // V (no page zoom)
    return Math.max(0, Math.min(extent, window.scrollY + rowTop - headerLine)); // V (no page zoom)
  };
  const target = goal();
  const lease = session.scrollLease;
  session.centreTarget = target;
  const write = (position) => { if (hooks.leaseIsCurrent(lease) && owns(session)) window.scrollTo({ left: x, top: position, behavior: 'instant' }); };
  const finish = () => { if (!hooks.leaseIsCurrent(lease)) return; write(goal()); hooks.releaseLease(lease); cancelCentre(session); };
  session.finishCentre = finish;
  if (reducedMotion()) { finish(); return; }
  const start = performance.now();
  const step = (now) => {
    if (!hooks.leaseIsCurrent(lease) || !owns(session)) return;
    const progress = Math.min(1, (now - start) / 300);
    if (progress === 1) { finish(); return; }
    write(y + (goal() - y) * (1 - Math.pow(1 - progress, 3)));
    session.scrollRaf = requestAnimationFrame(step);
  };
  try { session.scrollRaf = requestAnimationFrame(step); } catch (_) { finish(); }
}
export function unmountPlotPresentation(uid, { parked = false } = {}) {
  const session = sessions.get(uid);
  if (!session) return;
  if (session.scrollLease) hooks.cancelLease(session.scrollLease);
  if (!parked) parkPlotPresentation(uid);
  finishPlotReveal(session);
  session.sceneCleanups.forEach((cancel) => cancel());
  session.sceneRevision++;
  if (!session.sceneReleased) session.sceneKey = null;
  session.mountedFrame = null;
  pump();
}
export function closePlotPresentation(uid) {
  endLakeScene(uid);
  const session = sessions.get(uid);
  if (!session) return;
  unmountPlotPresentation(uid);
  session.isOpen = false;
  cancelTimers(session);
  session.sceneCleanups.forEach((cancel) => cancel());
  session.retainedArt = [];
  session.apertureRecords.clear();
  session.assetRecords.clear();
  plotTurns.delete(uid);
  sessions.delete(uid);
  pump();
}
export function invalidatePlotPresentations() {
  [...sessions.keys()].forEach(closePlotPresentation);
  hooks.cancelLease?.();
}

// the quiet board re-read () keeps the reader's open plots but bumps the
// board generation; a session created under the previous generation would fail `owns()`
// from then on, `mountPlotPresentation` would return before `dataReady`, and the band
// would sit on "Still loading workers…" for good. Open sessions adopt the new generation
// BEFORE the re-render so the detail reload re-mounts them with data.
export function adoptPlotPresentations(generation) {
  sessions.forEach((session) => { if (session.isOpen) session.generation = generation; });
}

if (typeof window !== 'undefined') {
  window.matchMedia('(prefers-reduced-motion: reduce)').addEventListener('change', () => {
    if (!reducedMotion()) return;
    sessions.forEach((session) => { session.finishCentre?.(); finishPlotReveal(session); settleWorkers(session, session.artRevision); });
  });
}
