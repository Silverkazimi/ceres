// (maintainer: "sync the staging animation between all staged tiles. Currently they all pulse in their own rhythm.
// Sync them to pulse in harmony."): a CSS animation starts when its element is drawn, so tiles and badges staged at
// different moments drift apart. Every stage pulse is put on the document timeline at 0 -- one shared phase, no
// restart, no flicker. Checked at most once per frame, only when the page changed.
// the pending clock in the badge column is a stage pulse too; it was left out when it was added.
const STAGE_PULSES = new Set(['stage-tile-outline', 'stage-tile-glow', 'staged-changes-pulse', 'badge-clock-pulse', 'row-pending-glow', 'lock-clock-pulse', 'changes-ready-pulse']);
let queued = false;

function syncStagePulses() {
  queued = false;
  for (const animation of document.getAnimations()) {
    if (STAGE_PULSES.has(animation.animationName) && animation.startTime !== 0) animation.startTime = 0;
  }
}

export function initPulseSync() {
  if (typeof document.getAnimations !== 'function' || typeof MutationObserver !== 'function') return;
  const schedule = () => { if (!queued) { queued = true; requestAnimationFrame(syncStagePulses); } };
  new MutationObserver(schedule).observe(document.body, { subtree: true, childList: true, attributes: true, attributeFilter: ['class'] });
  schedule();
}
