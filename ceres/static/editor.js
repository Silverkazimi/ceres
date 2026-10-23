import { humanizeInstant } from './format.js';
import { bindCountdowns, countdownText } from './countdown.js';
import { chainReturn, chainStageBackSource, deriveChain } from './combine-chain.js';
import { activeChainPlans, chainPlans } from './stage-plans.js';
import { RETURN_ICON } from './button-icons.js';
import { IMAGE_RETRY_DELAYS_MS, retryImageUrl, terrainBackgroundPath, worksiteArtGeometry } from './art.js';
import { abilityRowText, abilityTipModel, bloodlineCompanionRelations, bloodlineTileBadgesHtml, costLineHtml, foilBadgeHtml, markBloodlineApplied, workerSubType } from './badges.js';
import { BOARD, catalogNotice, noticeMessage } from './board.js';
import { cooldownReasonFragment, estimateBadge, renderInlineCandidatePicker, signedPp } from './candidates.js';
import { stagedBasePpCap, stageTotalBasePp } from './badge-state.js';
import { combineTileActionHtml } from './combine.js';
import { editionTooltip, esc, fmtFoil, formatPlotLabel, isBlackFoil, isGoldFoil, isRuniWorker, multiplierPartsHtml, num, setNameLabel, terrainValue } from './format.js';
import { terrainAffinityHtml } from './terrainAffinity.js';
import { isSoleSource, plotPoweredAfterStage } from './hazard.js';
import { CARD_CDN_BASE, CARD_CDN_SET, CARD_CDN_SOURCE_SET, CHEVRON_CLOSE_ICON, FILL_TRAY_ICON, POWER_CORE_IMAGE, TITLE_ICON_SLUG, TOTEM_ICON_FILE, abilityIconHtml, cardSplinters, cardSplinterTipHtml, orderLandAbilities, resourceIconHtml, tooltipIconPath } from './icons.js';
import { CERES_STAGE, capNoticesForRow, clearCapDerivationState, recordCapDerivationFailure, recordCapPrediction, stageDeltaForRow, stageImpactForRow, stagedEntriesForRow, stagedReleasedItemRefs, stagedWorksiteBuild } from './staging.js';
import { ASSET_BASE, S } from './state.js';
import { plainText, tipAttrs, tipIcon } from './tooltip.js';
import { worksiteChangeButtonHtml } from './worksite.js';
import { harvestBarHtml } from './harvest-state.js';
import { plotLockIconHtml } from './plot-lock.js';
import { buildPlotWarnings, plotWarningListHtml, plotWarningPillsHtml } from './plot-warnings.js';

export function annotateEditorBloodlines(row) {
  const workers = Array.isArray(row?.workers) ? row.workers : [];
  workers.forEach((worker) => {
    const entries = Array.isArray(worker.land_abilities) ? worker.land_abilities : [];
    worker.land_abilities = entries.map((entry) => {
      if (!Array.isArray(entry) || entry[0] !== 'BLOODLINE' || !entry[2]) return entry;
      const subType = String(entry[2]);
      const companions = workers.filter((other) => other !== worker && workerSubType(other) === subType).map((other) => other.name);
      return [...entry.slice(0, 3), { active: companions.length > 0, companions }];
    });
  });
  markBloodlineApplied(workers); // only the largest active Toil and Kin applies
}

function editorCardEdition(asset) {
  // A metadata-less asset (editions/edition both absent) must resolve to null, never a
  // guessed edition 0 -- a wrong-but-present edition sends editorCardImage() to the wrong
  // CDN set, which 404s and strips the <img> via slotAssetImageHtml()'s onerror chain
  // permanently (the record itself has no art fields to recover from). null instead
  // renders a no-art tile, which is recoverable.
  if (asset.editions === undefined && asset.edition === undefined) return null;
  const raw = Array.isArray(asset.editions) ? asset.editions : String(asset.editions ?? '').split(',').filter((value) => value !== '');
  const editions = raw.map((value) => Number(value)).filter((value) => Number.isInteger(value) && value >= 0);
  const selected = Number(asset.edition);
  return editions.includes(selected) ? selected : (editions[0] ?? (Number.isInteger(selected) && selected >= 0 ? selected : null));
}

function cardImagePart(value) {
  return encodeURIComponent(String(value)).replace(/[!'()*]/g, (char) => `%${char.charCodeAt(0).toString(16).toUpperCase()}`);
}

// every RUNI shows its own CARD art
// runi.splinterlands.com/cards/{n}.jpg (300x420, the normal card shape), where n is the number at the end of its card
// uid (C2-505-0000003149 -> 3149). Not the round avatars/{n}.png portrait he rejected (). A plot's staked RUNI
// arrives with `runi_number` (its asset_ref is a hash); a picker candidate carries its uid. The generic Runi card art
// stays behind it as the fallback, so a missing picture never leaves the tile empty.
const RUNI_CARD_BASE = 'https://runi.splinterlands.com/cards/';
export function runiCardImageUrl(asset) {
  const number = Number(asset?.runi_number);
  if (Number.isSafeInteger(number) && number > 0) return `${RUNI_CARD_BASE}${number}.jpg`;
  const uid = String(asset?.uid || asset?.card_uid || asset?.asset_ref || '');
  const match = /^C\d+-505-0*(\d+)$/.exec(uid);
  return match ? `${RUNI_CARD_BASE}${match[1]}.jpg` : null;
}

export function editorCardImageCandidates(asset) {
  const runi = runiCardImageUrl(asset);
  const cardArt = cardArtCandidates(asset);
  return runi ? [runi, ...cardArt.filter((url) => url !== runi)] : cardArt;
}

function cardArtCandidates(asset) {
  const edition = editorCardEdition(asset);
  const name = asset.card_name || asset.name;
  if (!name || edition === null) return [];
  const sourceSet = CARD_CDN_SOURCE_SET[String(asset.card_set || '').toLowerCase()];
  const numericSet = CARD_CDN_SET[edition];
  const sets = [...new Set([numericSet, sourceSet].filter(Boolean))];
  if (!sets.length) return [];
  const skins = asset.skin ? [asset.skin] : [null];
  // gold and gold arcane use "_gold", black and black arcane "_blk".
  const filename = `${cardImagePart(name)}${isGoldFoil(asset) ? '_gold' : isBlackFoil(asset) ? '_blk' : ''}`;
  const candidates = [];
  sets.forEach((set) => skins.forEach((skin) => ['jpg', 'png'].forEach((extension) => {
    candidates.push(`${CARD_CDN_BASE}/${set}${skin ? `/${cardImagePart(skin)}` : ''}/${filename}.${extension}`);
  })));
  return [...new Set(candidates)];
}

export function editorCardImage(asset) {
  return editorCardImageCandidates(asset)[0] || null;
}

function cardImageEventTarget(event) {
  const image = event.target;
  if (image?.dataset?.eosManaged !== undefined) return null;
  return typeof HTMLImageElement !== 'undefined' && image instanceof HTMLImageElement && image.dataset.cardImageCandidates !== undefined ? image : null;
}

function cardImageCandidates(image) {
  try {
    const candidates = JSON.parse(image.dataset.cardImageCandidates || '[]');
    return Array.isArray(candidates) ? candidates : [];
  } catch {
    return [];
  }
}

function renderCardImageFallback(image) {
  const parent = image.parentElement;
  if (!parent) return;
  parent.classList.add('card-art-fallback-container', 'is-none');
  let placeholder = parent.querySelector('.card-art-fallback');
  if (!placeholder) {
    placeholder = document.createElement('span');
    placeholder.className = 'card-art-fallback';
    placeholder.setAttribute('aria-label', image.dataset.cardImagePlaceholder || 'Unknown card — no image');
    const name = document.createElement('span');
    name.className = 'card-art-fallback-name';
    name.textContent = image.dataset.cardImageName || 'Unknown card';
    const marker = document.createElement('span');
    marker.className = 'card-art-fallback-marker';
    marker.textContent = 'no image';
    placeholder.append(name, marker);
    parent.append(placeholder);
  }
  image.hidden = true;
}

function advanceCardImage(image) {
  const candidates = cardImageCandidates(image);
  const next = Number(image.dataset.cardImageIndex || 0) + 1;
  if (next < candidates.length) {
    image.dataset.cardImageIndex = String(next);
    image.src = candidates[next];
    return;
  }
  renderCardImageFallback(image);
  const attempt = Number(image.dataset.skinRetry || 0);
  if (image.dataset.cardSkin && candidates.length && attempt < IMAGE_RETRY_DELAYS_MS.length) {
    image.dataset.skinRetry = String(attempt + 1);
    setTimeout(() => {
      if (!image.isConnected) return;
      image.dataset.cardImageIndex = '0';
      image.src = retryImageUrl(candidates[0], attempt + 1);
    }, IMAGE_RETRY_DELAYS_MS[attempt]);
  }
}

// worker-picker card art fades in once decoded (the plot opening's 420ms soft fade); a picture
// already shown once in this page appears at once on a redraw, like an already-revealed plot tile.
const PICKER_ART_SHOWN = new Set();
function revealPickerArt(image) {
  if (!image.closest?.('.candidate-art') || image.classList.contains('is-art-in')) return;
  const key = image.currentSrc || image.src;
  if (PICKER_ART_SHOWN.has(key)) { image.classList.add('is-art-in', 'is-art-instant'); return; }
  const show = () => { PICKER_ART_SHOWN.add(key); image.classList.add('is-art-in'); };
  (typeof image.decode === 'function' ? image.decode() : Promise.resolve()).then(show, show);
}

function handleCardImageLoad(event) {
  const image = cardImageEventTarget(event);
  if (!image) return;
  if (image.naturalWidth === 0) {
    advanceCardImage(image);
    return;
  }
  if (image.dataset.cardSkin) {
    image.hidden = false;
    image.parentElement?.querySelector('.card-art-fallback')?.remove();
    image.parentElement?.classList.remove('card-art-fallback-container', 'is-none');
  }
  revealPickerArt(image);
  // a picked card's picture has loaded -- fade it and its name in; the tile keeps
  // its empty-slot look until the picture is opaque over it (no black flash, ).
  startPickFade(image); // a picked card's picture has loaded -- fade it in
  if (Number(image.dataset.cardImageIndex || 0) > 0) {
    const folder = new URL(image.currentSrc || image.src).pathname.split('/').filter(Boolean)[0] || 'unknown';
    console.info('[ceres:card-art-fallback]', image.dataset.cardImageName, 'loaded from', folder);
  }
}

function handleCardImageError(event) {
  const image = cardImageEventTarget(event);
  if (image) advanceCardImage(image);
}

if (typeof document !== 'undefined') {
  document.addEventListener('load', handleCardImageLoad, true);
  document.addEventListener('error', handleCardImageError, true);
}

let managedEditorArt = false;
// how far each picked card's fade has got (pick key -> ms of fade shown). It advances
// only on painted frames, at most one frame's worth per frame, so a page busy redrawing after
// the pick (IR: the fade used to finish on the clock before a single frame was painted) still
// shows the whole fade; a redraw mid-fade continues it on the new picture.
const PICK_FADE_PROGRESS = new Map();
const PICK_FADE_MS = 420;
const PICK_FADE_STEP_MS = 34;
let pickFadeFrame = 0;
function pickFadeProgress(pickKey) {
  const progress = PICK_FADE_PROGRESS.get(pickKey);
  return progress === undefined ? null : progress;
}
function pickFadeOpacity(progress) {
  const x = Math.min(1, progress / PICK_FADE_MS);
  return (x * x * (3 - 2 * x)).toFixed(3);
}
function paintPickFade(key, progress) {
  document.querySelectorAll('img.pick-fade[data-pick-key]').forEach((img) => {
    if (img.dataset.pickKey !== key) return;
    if (progress >= PICK_FADE_MS) {
      img.style.removeProperty('opacity');
      img.classList.add('is-pick-done');
      img.closest('.slot-tile')?.classList.remove('is-art-pending', 'is-caption-pending');
    } else {
      img.style.opacity = pickFadeOpacity(progress);
    }
  });
}
function runPickFade() {
  if (pickFadeFrame) return;
  let last = null;
  const step = (now) => {
    const dt = last === null ? 0 : Math.min(now - last, PICK_FADE_STEP_MS);
    last = now;
    let active = false;
    PICK_FADE_PROGRESS.forEach((progress, key) => {
      if (progress >= PICK_FADE_MS) return;
      const next = Math.min(PICK_FADE_MS, progress + dt);
      PICK_FADE_PROGRESS.set(key, next);
      paintPickFade(key, next);
      if (next < PICK_FADE_MS) active = true;
    });
    pickFadeFrame = active ? requestAnimationFrame(step) : 0;
  };
  pickFadeFrame = requestAnimationFrame(step);
}
function startPickFade(image) {
  if (!image.classList.contains('pick-fade') || image.classList.contains('is-loaded')) return;
  const key = image.dataset.pickKey || '';
  image.classList.add('is-loaded');
  image.closest('.slot-tile')?.classList.remove('is-caption-pending');
  if (!PICK_FADE_PROGRESS.has(key)) {
    const reduced = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
    PICK_FADE_PROGRESS.set(key, reduced ? PICK_FADE_MS : 0);
  }
  paintPickFade(key, PICK_FADE_PROGRESS.get(key));
  runPickFade();
}
// A picked card's picture is often already in the browser's memory: it counts as loaded
// before its tile is on the page, so no load event reaches the document listener and the
// fade would wait for the next redraw. Start it as soon as such a picture is inserted.
if (typeof MutationObserver !== 'undefined' && typeof document !== 'undefined') {
  new MutationObserver(() => {
    document.querySelectorAll('img.pick-fade:not(.is-loaded)').forEach((img) => {
      if (img.complete && img.naturalWidth > 0) startPickFade(img);
    });
  }).observe(document.documentElement, { childList: true, subtree: true });
}

export function editorCardImageHtml(asset, { alt = '', candidates = editorCardImageCandidates(asset), pickKey = '' } = {}) {
  if (asset.skin) candidates = candidates.filter((url) => url.includes(`/${cardImagePart(asset.skin)}/`));
  const first = candidates[0];
  if (!first) return '';
  const skinAttribute = asset.skin ? ` data-card-skin="${esc(asset.skin)}"` : '';
  // a freshly picked card loads directly (never through the reveal's paced queue)
  // and fades in on load; once faded, later redraws render it visible straight away.
  if (pickKey) {
    const progress = pickFadeProgress(pickKey);
    const shown = progress === null ? '' : progress >= PICK_FADE_MS ? ' is-loaded is-pick-done' : ' is-loaded';
    const resume = progress !== null && progress < PICK_FADE_MS ? ` style="opacity:${pickFadeOpacity(progress)}"` : '';
    return `<img class="eos-portrait-fill pick-fade${shown}"${skinAttribute}${resume} src="${esc(first)}" data-pick-key="${esc(pickKey)}" alt="${esc(alt)}" loading="eager" decoding="async" data-card-image-candidates="${esc(JSON.stringify(candidates))}" data-card-image-index="0" data-card-image-name="${esc(asset.card_name || asset.name || 'Unknown card')}" data-card-image-placeholder="${esc(`${asset.card_name || asset.name || 'Unknown card'} — no image`)}">`;
  }
  return `<img class="eos-portrait-fill"${skinAttribute} ${managedEditorArt ? 'data-eos-managed=""' : `src="${esc(first)}"`} alt="${esc(alt)}" loading="lazy" decoding="async" data-card-image-candidates="${esc(JSON.stringify(candidates))}" data-card-image-index="0" data-card-image-name="${esc(asset.card_name || asset.name || 'Unknown card')}" data-card-image-placeholder="${esc(`${asset.card_name || asset.name || 'Unknown card'} — no image`)}">`;
}

export function editorAssetImage(asset, groupId) {
  if (groupId === 'worker' || groupId === 'runi') return editorCardImage(asset);
  if (groupId === 'totem') {
    // a picker candidate carries no `rarity` -- the name ("Epic Totem") says it.
    const rarity = String(asset.rarity || String(asset.name || '').split(' ')[0] || '').toLowerCase();
    return TOTEM_ICON_FILE.has(rarity) ? `${ASSET_BASE}/totems/totem-${rarity}.png` : null;
  }
  if (groupId === 'title') {
    const slug = TITLE_ICON_SLUG[asset.name];
    return slug ? `${ASSET_BASE}/titles/title-${slug}.svg` : null;
  }
  if (groupId === 'power_core') return asset?.name === 'Power Core' ? POWER_CORE_IMAGE : null;
  return null;
}

function slotAssetImageHtml(occupant, group, pickKey = '') {
  if (!occupant.image) return '';
  const alt = group.id === 'worker' ? `${occupant.name} card art` : `${occupant.name} icon`;
  const candidates = occupant.image_candidates?.length
    ? occupant.image_candidates
    : [occupant.image, ...(occupant.image.endsWith('.jpg') ? [occupant.image.slice(0, -4) + '.png'] : [])];
  return `<span class="slot-art slot-art-${esc(group.id)}">${editorCardImageHtml(occupant, { alt, candidates, pickKey })}</span>`;
}

export function editorOccupant(asset, groupId = 'worker') {
  if (!asset) return null;
  const imageCandidates = groupId === 'worker' || groupId === 'runi'
    ? editorCardImageCandidates(asset)
    : [editorAssetImage(asset, groupId)].filter(Boolean);
  const image = imageCandidates[0] || null;
  const baseImage = asset.skin && (groupId === 'worker' || groupId === 'runi')
    ? editorAssetImage({ ...asset, skin: null }, groupId)
    : null;
  return {
    asset_ref: asset.asset_ref || asset.uid || asset.card_uid || null,
    source_deed_uid: asset.source_deed_uid || null,
    slot: asset.slot === null || asset.slot === undefined ? null : Number(asset.slot),
    card_uid: asset.card_uid || asset.uid || null,
    // Needed to match this occupant against IDLE_CARDS_CACHE's own combineGroupKey()
    // (card_detail_id|edition|foil) -- without it a plot's Combine doors have no way
    // to find this worker's idle duplicates (FR combine-reachability).
    card_detail_id: asset.card_detail_id ?? null,
    name: asset.name || asset.card_name || 'Unknown asset',
    image,
    image_candidates: imageCandidates,
    base_image: baseImage,
    level: asset.level,
    base_pp: asset.base_pp,
    base_pp_after_cap: asset.base_pp_after_cap,
    construction_pp: asset.construction_pp,
    current_harvest_pp: asset.harvest_pp ?? asset.current_harvest_pp,
    badges: asset.badges || [],
    splinter: asset.splinter,
    splinters: cardSplinters(asset),
    rarity: asset.rarity,
    card_set: asset.card_set,
    card_name: asset.card_name,
    editions: asset.editions,
    edition: asset.edition,
    foil: asset.foil,
    gold: asset.gold,
    skin: asset.skin,
    sub_type: asset.sub_type,
    bcx: asset.bcx,
    bcx_max: asset.bcx_max,
    terrain_boost: asset.terrain_boost,
    dec_stake_needed: asset.dec_stake_needed,
    land_abilities: asset.land_abilities || [],
    sole_holder_for: asset.sole_holder_for || [],
    // Totem/title production-boost fraction (e.g. 0.25) -- absent on worker/runi/power_core
    // assets, read only by editorItemTip() above.
    boost: asset.boost,
    // Server-computed readiness (candidates.py::cooldown_state) -- present only on a
    // freshly-staged candidate (serialize_candidate attaches it; a plain board worker
    // never carries it, since a currently-staked card is never "cooling"). Carried
    // through so a staged-but-not-ready tile can mark itself without the client
    // re-deriving readiness on its own (the internal rules: never a second source of truth for
    // the cooldown rule).
    planned: asset.planned || false, chain_id: asset.chain_id || null, ready_at: asset.ready_at || null,
    cooldown: asset.cooldown || null,
  };
}

// card slots the plot needs drawn -- its powered capacity or its staked monster cards
// whichever is larger (a Runi counts in worker_count but has its own tile).
export function stakedCardSlots(row) {
  const capacity = row?.capacity || {};
  const potential = Number(capacity.potential_card_capacity) || 0;
  // subtract the Runi from the server's original data (`bonus.runi`, never mutated by
  // staging). `capacity.runi_staked` flips to false the moment a Runi unstake is staged, which
  // counted the Runi as a sixth worker card and drew an extra "Add Worker 6" tile.
  const runiOriginally = !!row?.bonus?.runi || row?.staking_facts?.is_runi_staked === true || capacity.runi_staked === true;
  const staked = Math.max(0, (Number(capacity.worker_count) || 0) - (runiOriginally ? 1 : 0));
  const slots = Math.max(potential, staked);
  return Number.isInteger(slots) && slots <= 50 ? slots : potential;
}

export function editorGroups(row) {
  const rawCapacity = Number(row.capacity?.potential_card_capacity);
  const capacity = Number.isInteger(rawCapacity) && rawCapacity >= 0 && rawCapacity <= 50 ? rawCapacity : 0;
  const workers = Array.isArray(row.workers) ? row.workers : [];
  const workerBySlot = new Map(workers.filter((worker) => Number(worker.slot) > 0).map((worker) => [Number(worker.slot), worker]));
  // a staked card can sit beyond the powered capacity (e.g. the Energized worker
  // was swapped out and the game dropped the plot from 5 slots to 4). It is still staked
  // and still costs the plot -- draw it, flagged unpowered, never hide it.
  const highestOccupied = Math.max(0, ...[...workerBySlot.keys()].filter((slot) => slot <= 50));
  // staging an unstake takes the card out of `row.workers` at once; a slot with a
  // staged change must still be drawn (as its pending-removal tile), or a card beyond the
  // powered capacity vanished the moment its × was clicked.
  const highestStaged = Math.max(0, ...CERES_STAGE
    .filter((entry) => entry.deed_uid === row.deed_uid && entry.group_id === 'worker' && Number.isInteger(entry.slot_index))
    .map((entry) => entry.slot_index + 1).filter((slot) => slot <= 50));
  // before the plot's detail arrives `row.workers` is empty, so the reveal's placeholder
  // frames counted only the powered slots (4 on 065-09-083) and dropped the 5th staked card.
  // The board row's own worker count (Runi excluded) says how many cards are staked.
  // (his ruling: "Unpowered plots must always show all five worker tiles"): an unpowered plot with
  // no structure reports 0 slots and has no powered peer to learn from, so the potential arrives as 0.
  // Every plot has the game's five worker slots; draw them, with the per-tile tooltip explaining unpowered slots.
  const slotFloor = capacity > 0 ? capacity : DEFAULT_WORKER_SLOTS;
  const tileCount = Math.max(slotFloor, highestOccupied, highestStaged, stakedCardSlots(row));
  // a Power Core (staged or staked) or an Energized worker gives the plot full
  // worker-slot power; a Runi alone powers fewer slots. With full power the slots count up
  // to what powered plots of the same structure report, so a staged Power Core lifts the
  // No power overlay from the card it now powers.
  // the Power Core state comes from `row.bonus.power_core`, which staging updates; the
  // server's "core staked" flags keep their original value after a core removal is staged. With
  // only a Runi left, the game powers one worker slot fewer than full power (065-09-083: 4 of 5).
  const coreNow = row.bonus && 'power_core' in row.bonus
    ? !!row.bonus.power_core
    : (row.is_power_core_staked === true || row.staking_facts?.is_power_core_staked === true);
  const coreOriginally = row.is_power_core_staked === true || row.staking_facts?.is_power_core_staked === true;
  const energizedOriginally = row.is_energized === true || row.staking_facts?.is_energized === true;
  const workersChanged = CERES_STAGE.some((entry) => entry.deed_uid === row.deed_uid && entry.group_id === 'worker');
  const energizedNow = workersChanged
    ? workers.some((worker) => (worker?.land_abilities || []).some((entry) => Array.isArray(entry) && entry[0] === 'ENERGIZED'))
    : energizedOriginally;
  const fullPower = coreNow || energizedNow;
  const runiNow = row.capacity?.runi_staked === true;
  const fullPowerOriginally = coreOriginally || energizedOriginally;
  const runiOriginally = Boolean(row.bonus?.runi);
  const rawFullCapacity = Number(row.capacity?.powered_card_capacity);
  const fullCapacity = Number.isInteger(rawFullCapacity) && rawFullCapacity <= 50 ? Math.max(slotFloor, rawFullCapacity) : slotFloor;
  const runiPoweredCapacity = fullCapacity === DEFAULT_WORKER_SLOTS ? 4 : Math.max(0, fullCapacity - 1);
  const rawLivePowered = Number(row.capacity?.powered_slots ?? row.capacity?.max_workers_allowed);
  const livePowered = Number.isInteger(rawLivePowered) && rawLivePowered >= 0 && rawLivePowered <= 50
    ? rawLivePowered
    : fullPower ? fullCapacity : runiNow ? runiPoweredCapacity : 0;
  const powerStateChanged = coreNow !== coreOriginally || energizedNow !== energizedOriginally || runiNow !== runiOriginally;
  const poweredCapacity = powerStateChanged
    ? fullPower ? fullCapacity : runiNow ? runiPoweredCapacity : 0
    : livePowered;
  const noPower = !fullPower && !runiNow; // staging-aware (a staged Power Core counts)
  const workerSlots = Array.from({ length: tileCount }, (_, index) => {
    const worker = workerBySlot.get(index + 1);
    return worker ? editorOccupant(worker, 'worker') : null;
  });
  const bonus = row.bonus || {};
  const facts = row.staking_facts || row;
  const sources = [];
  if (facts.is_power_core_staked === true) sources.push('Power Core');
  if (facts.is_energized === true) sources.push('Energized');
  if (facts.is_runi_staked === true || row.capacity?.runi_staked === true) sources.push('Runi');
  const powerSatisfied = facts.is_power_core_staked === true || facts.is_energized === true
    || facts.is_runi_staked === true || row.capacity?.runi_staked === true;
  const powerConstraint = {
    satisfied: powerSatisfied,
    label: powerSatisfied ? `worker slots powered by ${sources.join(' + ')}` : 'worker slots not powered',
    effect: 'a Power Core or Energized worker powers all five slots; a Runi alone powers four',
  };
  const itemGroup = (id, label, item, actionable, reason = null) => ({
    id, label, capacity: 1, actionable, viewOnlyReason: reason,
    slots: [editorOccupant(item, id)],
  });
  // the Runi tile takes only a Runi card (format.js isRuniWorker), never whatever sits on "slot 0".
  const runiWorker = workers.find((worker) => isRuniWorker(worker));
  return [
    { id: 'worker', label: 'Workers', capacity: tileCount, poweredCapacity, noPower, actionable: true, slots: workerSlots, slotNumbers: Array.from({ length: tileCount }, (_, index) => index + 1), constraint: powerConstraint },
    itemGroup('power_core', 'Power Core', bonus.power_core, true),
    // `bonus.runi` is the server's ORIGINAL snapshot and is never mutated (mutateEditorSlot's
    // runi branch flips `row.capacity.runi_staked` instead, deliberately, per its own comment)
    // so it must never gate this fallback: after a staged removal it would still read `true`
    // and this slot would keep resolving a stub occupant, leaving the tile permanently "occupied"
    // and unable to reach the empty/pending-removal render branch below. `row.capacity.runi_staked`
    // is the live, staging-aware boolean (same one the RUNI badge and power-source checks read)
    // so it is the only correct gate here.
    { id: 'runi', label: 'Runi', capacity: 1, actionable: true, slots: [editorOccupant(runiWorker || (facts.is_runi_staked === true || row.capacity?.runi_staked === true ? { name: 'Runi', card_name: 'Runi', editions: '2', edition: 2, base_pp: 1500 } : null), 'runi')] },
    itemGroup('totem', 'Totem', bonus.totem, true),
    itemGroup('title', 'Title', bonus.title, true),
  ];
}

function editorSlotNumber(group, index) {
  if (group.id !== 'worker') return index + 1;
  // `group.slots` itself (not just `group.slots[index]`) can be missing
  // renderInlineCandidatePicker() calls editorSlotLabel() with a minimal
  // `{id, label}` stand-in group that carries neither `.slots` nor
  // `.slotNumbers` (it only needs a label for the picker's context line, not
  // the full editorGroups() shape). Without the leading `?.` here
  // `group.slots[index]` throws on that call site -- confirmed live, it took
  // down the entire "Add Worker" picker (every candidates-fetch timed out in
  // the live test suite, 2026-08-26). Falls back to the same `index + 1` the
  // picker's `index` (== workerSlots' own array position, 1:1 with real slot
  // number by construction -- see editorGroups()) always resolved to before
  // this whole slot-number fix existed, so this call site's behaviour is
  // unchanged and still correct.
  const actualSlot = group.slots?.[index]?.slot ?? group.slotNumbers?.[index];
  return Number.isInteger(actualSlot) && actualSlot > 0 ? actualSlot : index + 1;
}

export function editorSlotLabel(group, index) {
  return group.id === 'runi' ? 'Runi' : `${group.label} slot ${editorSlotNumber(group, index)}`;
}

function editorEmptyLabel(group, index) {
  if (group.id === 'worker') return `Add Worker ${editorSlotNumber(group, index)}`;
  return `Add ${group.label}`;
}

const TILE_SHAPE = { worker: 'card', runi: 'card' };
// every plot has the game's five worker slots; an unpowered, structureless plot reports 0.
const DEFAULT_WORKER_SLOTS = 5;
const NO_POWER_TIP = 'Workers here need a Power Core in the power slot or an Energized worker; staked workers produce nothing until then.';

function editorTileShape(group) {
  return TILE_SHAPE[group.id] || 'item';
}

function editorTileCrest(group) {
  return '';
}

// every land
// ability shows as its own icon -- a plain centred row of up to 5 (22px icons, 2px apart, 118px: a slight overhang
// over the 107px tile that never reaches a neighbour's overhang across the 16.8px gap). On an Energized worker the
// icons sit on an arc around the Energized satellite. Only a 6th+ ability still folds into "+N".
const ABILITY_MAX = 5;
const ICON_PX = 22;
function arcPositions(n) {
  if (n === 1) return [90];
  const span = Math.min(150, 60 * (n - 1));
  const step = span / (n - 1);
  return Array.from({ length: n }, (_, i) => 90 + span / 2 - i * step);
}
function orderedWorkerAbilities(occupant) {
  const source = (occupant?.land_abilities || []).filter((entry) => {
    const code = Array.isArray(entry) ? entry[0] : entry;
    return code !== 'ENERGIZED';
  });
  return source.length ? orderLandAbilities(source) : [];
}
function workerHasEnergizedBubble(occupant, capacity) {
  return capacity <= 20 && (occupant?.land_abilities || []).some((entry) => Array.isArray(entry) && entry[0] === 'ENERGIZED');
}
function abilityArcFlags(ordered, hasBubble) {
  const isVovArc = !hasBubble && ordered.length === 6;
  return { hasAbilityArc: ordered.length > 0 && (hasBubble || isVovArc), hasVovArc: isVovArc };
}
// `omitBloodline` -- the flat row leaves Toil and Kin to the tile's labelled pill
// (bloodlineTileBadgesHtml, Echo's "one badge, no second icon"); the arcs keep every icon.
function renderAbilityRow(occupant, hasBubble = false, { omitBloodline = false } = {}) {
  const ordered = orderedWorkerAbilities(occupant);
  if (!ordered.length) return '';
  const arcFlags = abilityArcFlags(ordered, hasBubble);
  const flat = !arcFlags.hasAbilityArc && omitBloodline ? ordered.filter((entry) => (Array.isArray(entry) ? entry[0] : entry) !== 'BLOODLINE') : ordered;
  if (!flat.length) return '';
  const visible = flat.length > ABILITY_MAX ? flat.slice(0, ABILITY_MAX - 1) : flat;
  const hidden = flat.slice(visible.length);
  const item = (entry, style = '') => `<span class="slot-ability-item"${style}>${abilityIconHtml(entry, 'tile_ability_icons')}</span>`;
  const overflowTip = hidden.length ? tipAttrs({ title: `+${hidden.length} more abilities`, items: hidden.map((entry) => { const model = abilityTipModel(entry); return { icon: model.icon, name: model.title, effect: abilityRowText(entry) }; }), peer: 'tile_ability_overflow' }) : '';
  const overflow = hidden.length ? `<button type="button" class="slot-ability-overflow ceres-btn ceres-btn--secondary" data-control-density="dense" data-control-mode="icon" aria-label="${esc(`Show ${hidden.length} more abilities`)}"${overflowTip}>+${hidden.length}</button>` : '';
  const flatOverflow = hidden.length ? `<span class="slot-ability-count" role="img" aria-label="${esc(`${hidden.length} more abilities; open tile details`)}"${overflowTip}>+${hidden.length}</span>` : '';
  // a max VoV card carries six abilities -- its own flat arch above the tile, the six icons spaced
  // evenly along a half-ellipse (48 x 32px) so neighbours never touch and the top stays below the plot controls.
  if (arcFlags.hasVovArc) {
    const rx = 48; const ry = 32; const steps = 180; // 32: arc spacing ~25.6px, so no two icons touch
    const pts = Array.from({ length: steps + 1 }, (_, i) => { const a = Math.PI - (Math.PI * i) / steps; return [Math.cos(a) * rx, -Math.sin(a) * ry]; });
    const len = [0]; for (let i = 1; i < pts.length; i += 1) len.push(len[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
    const total = len[len.length - 1];
    const icons = ordered.map((entry, k) => {
      const target = (total * k) / 5; let i = len.findIndex((l) => l >= target); if (i < 0) i = pts.length - 1;
      return item(entry, ` style="--ax:calc(var(--ability-unit, 1px) * ${pts[i][0].toFixed(1)});--ay:calc(var(--ability-unit, 1px) * ${pts[i][1].toFixed(1)})"`);
    }).join('');
    return `<div class="slot-ability-arc is-vov" aria-label="Land abilities">${icons}</div>`;
  }
  if (!arcFlags.hasAbilityArc) {
    return `<div class="slot-ability-row is-centred" aria-label="Land abilities"><span class="slot-ability-row">${visible.map((e) => item(e)).join('')}${flatOverflow}</span></div>`;
  }
  // Energized: an arc around the satellite (centre = tile top centre). The radius grows with the count so
  // neighbouring icons keep a 2px gap along the arc.
  const entries = [...visible.map((e) => ({ html: (style) => item(e, style) })), ...(overflow ? [{ html: (style) => `<span class="slot-ability-item"${style}>${overflow}</span>` }] : [])];
  const angles = arcPositions(entries.length);
  const step = entries.length > 1 ? Math.abs(angles[0] - angles[1]) : 180;
  const radius = Math.max(31, (ICON_PX + 2) / (2 * Math.sin((step * Math.PI) / 360)));
  const icons = entries.map((entry, i) => {
    const a = (angles[i] * Math.PI) / 180;
    return entry.html(` style="--ax:calc(var(--ability-unit, 1px) * ${(Math.cos(a) * radius).toFixed(1)});--ay:calc(var(--ability-unit, 1px) * ${(-Math.sin(a) * radius).toFixed(1)})"`);
  }).join('');
  return `<div class="slot-ability-arc" aria-label="Land abilities">${icons}</div>`;
}

// Rebuilt (board-polish, item 2/3, the owner: worker tooltips "too noisy...
// must be beautifully designed and clear and free of redundant data" while
// item tooltips are "empty but show tons of default text that is 100%
// redundant"). One rule for both: a tooltip states THIS asset's own numbers
// PP and boost first -- and drops any line that would read the same on
// every tile of that type (a bare "—" placeholder is exactly that: it says
// nothing about the specific card, so it is now omitted rather than shown).
// Two purpose-built builders replace the single generic 13-line dump that
// used to run unchanged for both cards AND items (the actual cause of the
// item side being "empty but full of boilerplate" -- a card-shaped template
// has no `boost` field to show, so every item tooltip rendered five or six
// dash placeholders and nothing about its own contribution).
function basePpDisplay(occupant) {
  const raw = Number(occupant?.base_pp);
  const afterCap = occupant?.base_pp_after_cap === null || occupant?.base_pp_after_cap === undefined
    ? null : Number(occupant?.base_pp_after_cap);
  const clipped = Number.isFinite(afterCap) && afterCap < raw;
  const displayed = clipped ? afterCap : raw;
  return { raw, afterCap, clipped, displayed };
}

export const CAP_DERIVATION_ENABLED = true;

function finiteCapInput(value) {
  if (value === null || value === undefined || value === '') return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function stagedWorkerSlot(entry) {
  const targetIndex = finiteCapInput(entry?.group_id === 'worker' ? entry.slot_index : null);
  if (Number.isInteger(targetIndex) && targetIndex >= 0) return targetIndex + 1;
  const asset = entry?.action === 'unstake' ? entry?._previous_asset : entry?._asset;
  const cardSlot = finiteCapInput(asset?.slot);
  return Number.isInteger(cardSlot) && cardSlot > 0 ? cardSlot : null;
}

function workerAtSlot(asset, slot) {
  return { ...asset, slot, land_abilities: Array.isArray(asset?.land_abilities) ? asset.land_abilities.slice() : [] };
}

function effectiveWorkerRoster(row, staged) {
  let workers = Array.isArray(row?.workers)
    ? row.workers.filter((worker) => Number(worker?.slot) > 0).map((worker) => ({ ...worker }))
    : [];
  const workerChanges = (Array.isArray(staged) ? staged : []).filter((entry) => entry?.group_id === 'worker');

  for (const entry of workerChanges) {
    const slot = stagedWorkerSlot(entry);
    if (slot === null) return null;
    workers = workers.filter((worker) => Number(worker?.slot) !== slot);
  }
  for (const entry of workerChanges) {
    const slot = stagedWorkerSlot(entry);
    if (entry.action === 'unstake') continue;
    if (!['stake', 'replace'].includes(entry.action)) return null;
    const asset = entry._asset;
    const base = finiteCapInput(asset?.base_pp);
    if (slot === null || !asset || base === null) return null;
    workers = workers.filter((worker) => Number(worker?.slot) !== slot);
    workers.push(workerAtSlot({ ...asset, base_pp: base }, slot));
  }
  return workers;
}

export function deriveCapAllocation(workers, cap) {
  if (!CAP_DERIVATION_ENABLED || !Array.isArray(workers)) return null;
  const capValue = finiteCapInput(cap);
  if (capValue === null) return null;
  const entries = workers.map((worker) => ({
    worker,
    slot: finiteCapInput(worker?.slot),
    base: finiteCapInput(worker?.base_pp),
  }));
  if (entries.some((entry) => !Number.isInteger(entry.slot) || entry.slot <= 0 || entry.base === null)) return null;
  if (new Set(entries.map((entry) => entry.slot)).size !== entries.length) return null;
  const after = new Map(entries.map((entry) => [entry.slot, entry.base]));
  let remaining = entries.reduce((sum, entry) => sum + entry.base, 0) - capValue;
  entries.sort((left, right) => right.slot - left.slot).forEach((entry) => {
    if (remaining <= 0) return;
    const take = Math.min(remaining, after.get(entry.slot));
    after.set(entry.slot, after.get(entry.slot) - take);
    remaining -= take;
  });
  const target = Math.min(entries.reduce((sum, entry) => sum + entry.base, 0), capValue);
  if (remaining > 0.01 || Math.abs([...after.values()].reduce((sum, value) => sum + value, 0) - target) > 0.01) return null;
  return { after, workers: entries.map((entry) => entry.worker), cap: capValue };
}

function capPredictionForRow(row, allocation) {
  return {
    predicted_at: new Date().toISOString(), plot: row.plot,
    cap: allocation.cap,
    slots: allocation.workers.map((worker) => ({
      slot: Number(worker.slot), asset_ref: worker.asset_ref, base_pp: Number(worker.base_pp),
      predicted_after_cap: allocation.after.get(Number(worker.slot)),
    })),
  };
}

function editorWorkerTip(occupant, group, index, row) {
  // Gate document #1, the shape he approved (, ): identity inline in the
  // title with its icons, Base PP once, one aligned PP block, an ABILITIES group with one
  // "what and when" line per ability, and a foot only when moving the card really costs
  // the plot something.
  const basePp = basePpDisplay(occupant);
  const edition = editionTooltip(occupant.card_set, occupant.edition, setNameLabel(occupant.card_set, occupant.edition));
  const foil = fmtFoil(occupant);
  const cap = (value) => String(value).charAt(0).toUpperCase() + String(value).slice(1);
  const identity = [
    cardSplinterTipHtml(occupant) || null,
    occupant.rarity ? `${tipIcon(tooltipIconPath('rarity', occupant.rarity))}${cap(occupant.rarity)}` : null,
    edition ? `${tipIcon(tooltipIconPath('set', occupant.card_set, occupant.edition))}${edition}` : null,
  ].filter(Boolean).join(' · ');
  const slot = String(editorSlotLabel(group, index)).replace(/^Workers slot/, 'Worker slot');
  const copyCounts = [occupant.bcx, occupant.bcx_max].map((value) => value === null || value === undefined || value === '' ? null : Number(value));
  const copies = copyCounts.map((value) => Number.isFinite(value) && value >= 0 ? value.toLocaleString(undefined, { maximumFractionDigits: 20 }) : '—');
  const levelLine = [
    occupant.level ? `Level ${occupant.level}` : null,
    copyCounts.some((value) => Number.isFinite(value)) ? `CC ${copies[0]} / ${copies[1]}` : null,
    foil !== 'Regular' ? foil : null,
  ].filter(Boolean).join(' · ');
  const terrain = terrainValue(occupant.terrain_boost);
  const construction = occupant.construction_pp === null || occupant.construction_pp === undefined
    ? null : Number(occupant.construction_pp);
  const total = occupant.current_harvest_pp === null || occupant.current_harvest_pp === undefined
    ? null : Number(occupant.current_harvest_pp);
  const items = [];
  if (Number.isFinite(Number(basePp.displayed))) {
    items.push({ group: 'Production', name: 'Base PP', effect: `${num(basePp.displayed, 0)}${basePp.clipped ? ` (capped from ${num(basePp.raw, 0)})` : ''}` });
  }
  if (Number.isFinite(construction) && construction !== basePp.displayed) items.push({ name: 'Boostable', effect: num(construction, 0) });
  if (Number.isFinite(total) && total !== (Number.isFinite(construction) ? construction : basePp.displayed)) items.push({ name: 'Total', effect: num(total, 0) });
  const terrainName = row?.terrain ? cap(row.terrain) : 'Terrain';
  // the terrain icon follows the name, so this row does not claim the icon column.
  items.push({
    group: 'Land', name: terrainName,
    labelIcon: row?.terrain ? tooltipIconPath('terrain', row.terrain) : null,
    effect: terrain === null ? 'modifier not reported' : `${signedPp(terrain * 100)}%`,
  });
  if (occupant.dec_stake_needed) items.push({ name: 'DEC staked', labelIcon: 'land/token-dec.png', effect: num(occupant.dec_stake_needed) });
  // same order as the tile's ability icons, so hovering never contradicts them.
  const abilities = orderLandAbilities(occupant.land_abilities || []);
  abilities.forEach((entry) => {
    const model = abilityTipModel(entry);
    items.push({ name: model.title, labelIcon: model.icon, effect: abilityRowText(entry), group: 'Abilities' });
  });
  // A companion card carries no Toil and Kin of its own: say which pair it completes.
  if (group.id === 'worker' && row) {
    bloodlineCompanionRelations(row, occupant).forEach((rel) => {
      items.push({ name: 'Completes', labelIcon: 'abilities/ability_toil-and-kin.png', effect: `${rel.holderName}'s ${rel.entry[2]} pair · ${abilityRowText(rel.entry)}`, group: 'Bloodline' });
    });
  }
  return {
    title: occupant.name ? `${occupant.name} · ${slot}` : slot,
    layout: 'worker',
    identity,
    level: levelLine,
    items,
    peer: 'tile_occupants',
  };
}

// Item tiles (totem/title/power_core) carry no card stats at all -- their
// entire real content is a name plus (for totem/title) a production-boost
// percentage. Power Core has no boost of its own; its real fact is that it's
// this plot's power source, so that replaces the boost line rather than
// leaving one more "—".
function editorItemTip(occupant, group) {
  const boost = occupant.boost;
  if (group.id === 'power_core') return { title: occupant.name, body: 'Powers all five worker slots; a Runi alone powers four. Zero cooldown.', peer: 'tile_occupants' };
  return boost !== null && boost !== undefined
    ? { title: occupant.name, key: `${(Number(boost) * 100).toFixed(0)}%`, peer: 'tile_occupants' }
    : { title: occupant.name, peer: 'tile_occupants' };
}

// Runi is neither a worker card nor a boost item -- editorWorkerTip()'s rarity
// splinter/edition/level line and editorItemTip()'s bare boost line both undersell
// it (worse, the raw card record's `rarity: "legendary"`/`splinter: "Neutral"`
// fields are card-API artifacts with no real meaning for a Runi, so showing them
// reads as noise, not information). Owner's ruling (2026-08-26): "lead with the
// boost, not the slot" -- the +100% is the Runi's primary value, equal to a
// Legendary Totem but ADDITIVE rather than competing, so it comes first. Every
// other line states an established, always-true Runi mechanic (the internal rules) rather
// than a placeholder -- the same "boilerplate but real" precedent editorItemTip()
// already sets for Power Core's boost-less tile above. Only `boost` and `base_pp`
// are per-instance data and are omitted (never shown as "—") when unreachable.
function editorRuniTip(occupant, group, index, row) {
  // Gate document #5 (approved ): the boost in the title, one line per fact.
  const boost = row?.multiplier_parts?.runi_boost;
  const boostText = boost !== null && boost !== undefined ? `+${(Number(boost) * 100).toFixed(0)}% production` : 'production boost';
  const items = [];
  if (occupant.base_pp !== null && occupant.base_pp !== undefined) items.push({ name: '', effect: `Base PP ${num(occupant.base_pp, 0)} · not limited by the plot cap` });
  items.push({ name: '', effect: `${tipIcon('land/token-dec.png')}DEC stake 0 on this plot` });
  items.push({ name: '', effect: 'Power slot · powers four worker slots' });
  items.push({ name: '', effect: 'Power Core or other Power Source + Runi · opens a sixth worker slot (bare Runi caps at five)' });
  items.push({ name: '', effect: 'Takes one of the plot\'s worker slots itself' });
  return {
    title: `Runi — ${boostText}, stacks with Totem and Title`,
    items,
    foot: 'Zero cooldown.',
    peer: 'tile_occupants',
  };
}

export function mobileWorkerIdentity(occupant, index) {
  const cap = (value) => String(value).charAt(0).toUpperCase() + String(value).slice(1);
  const edition = editionTooltip(occupant.card_set, occupant.edition, setNameLabel(occupant.card_set, occupant.edition));
  return {
    slot: `Worker slot ${Number(index) + 1}`,
    traits: [cardSplinterTipHtml(occupant),
      occupant.rarity ? `${tipIcon(tooltipIconPath('rarity', occupant.rarity))}${cap(occupant.rarity)}` : ''].filter(Boolean).join(' · '),
    edition: edition ? `${tipIcon(tooltipIconPath('set', occupant.card_set, occupant.edition))}${edition}` : 'Edition not reported',
    level: occupant.level ?? '—',
    foil: foilBadgeHtml(occupant) || esc(fmtFoil(occupant)),
  };
}

export function mobileOccupantDetails(occupant, group, index, row) {
  const model = editorOccupantTip(occupant, group, index, row);
  if (group.id !== 'worker') return model;
  const abilities = orderLandAbilities(occupant.land_abilities || []);
  let abilityIndex = 0;
  const metric = (value) => value === null || value === undefined || !Number.isFinite(Number(value)) ? 'Not reported' : num(Number(value), 0);
  return { ...model, items: [
    { name: 'Base PP', effect: model.items.find((item) => item.name === 'Base PP')?.effect || metric(occupant.base_pp) },
    { name: 'Boostable', effect: metric(occupant.construction_pp) },
    { name: 'Total PP', effect: metric(occupant.current_harvest_pp) },
    ...model.items.filter((item) => !['Base PP', 'Boostable', 'Total'].includes(item.name)).map((item) => {
      if (item.group === 'Abilities') return { ...item, icon: item.labelIcon, labelIcon: null, effect: abilityRowText(abilities[abilityIndex++], { resourceIconAfter: true }) };
      if (item.group === 'Bloodline') return { ...item, icon: item.labelIcon, labelIcon: null };
      return item;
    }),
  ] };
}

function editorOccupantTip(occupant, group, index, row) {
  if (group.id === 'totem' || group.id === 'title' || group.id === 'power_core') return editorItemTip(occupant, group);
  if (group.id === 'runi') return editorRuniTip(occupant, group, index, row);
  return editorWorkerTip(occupant, group, index, row);
}

function powerCoresLeft(deedUid) {
  const family = BOARD?.unassigned_bonus?.power_core;
  if (!family?.capturable) return null;
  const idleReserved = CERES_STAGE.filter((entry) => entry.group_id === 'power_core' && entry.action !== 'unstake' && !entry._source_deed_uid).length;
  return Math.max(0, (Number(family.count) || 0) - idleReserved) + stagedReleasedItemRefs('power_core', deedUid).length;
}

export function alternativePowerSourceLabel(row) {
  const sources = [];
  const facts = row.staking_facts || row;
  if (facts.is_energized === true) sources.push('Energized');
  return sources.join(' + ');
}

// R-144, his request: "Opening the picker always related to a specific tile, which is
// marked in the top picker row. But it must be visually highlighted as well. Grey out all
// tiles but the selected/active tile. All greyed out tiles must still be clearly visible
// but clearly fade into the background."
// The state already exists -- S.CERES_PICKER carries deedUid/groupId/slotIndex -- it was
// simply never drawn, so a plot with five open slots showed a picker that could belong to
// any of them. Emitted at RENDER time rather than toggled afterwards because both
// openPicker() and every close path call updateMountedRow(), which re-renders the row
// classes applied post-render would be wiped by the very actions that change this state.
function pickerFocusClass(row, group, index) {
  const p = S.CERES_PICKER;
  if (!p || p.deedUid !== row.deed_uid) return '';
  return (p.groupId === group.id && p.slotIndex === index) ? ' is-picker-focus' : ' is-picker-dimmed';
}

function pickerFocusDecoration(row, group, index) {
  const p = S.CERES_PICKER;
  if (!p || p.deedUid !== row.deed_uid || p.groupId !== group.id || p.slotIndex !== index) return '';
  const elapsed = Number.isFinite(p.ringStartedAt) && typeof performance !== 'undefined'
    ? Math.max(0, performance.now() - p.ringStartedAt) : 0;
  const delay = `--picker-ring-delay:-${Math.round(elapsed)}ms`;
  // (1): the ring is drawn in the tile's OWN pixel box (the placeholder is the
  // product tile, 107x150, radius 11; syncPickerFocusRings() replaces both the viewBox
  // and the path from the measured box and computed border-radius), so the corners are
  // circular arcs of the tile's radius -- never a 100x100 drawing stretched into ellipses.
  // the SVG viewport is the tile's PADDING box (inset:0 inside a 1px border), so the
  // placeholder is 105x148 with the INNER radius 10 (outer 11 - 1px border); the sync below
  // re-measures with clientWidth/clientHeight and the same inner radius. Measuring the border
  // box here scaled the drawing by 0.985 and used the outer radius: a visible wedge at the
  // bottom corners over the dark caption (his screenshot, ).
  const placeholder = pickerRoundedPath(105, 148, 10);
  const gradientId = `picker-ring-grad-${String(row.deed_uid).replace(/[^A-Za-z0-9_-]/g, '')}-${String(group.id).replace(/[^A-Za-z0-9_-]/g, '')}-${index}`;
  // (2): no white -- a comet in theme tokens. The arc is a gradient stroke from
  // accent to --primary; two longer, fainter dash layers ride the same phase behind it
  // (their --picker-ring-shift pushes their dash start back along the path) as a fading
  // tail; the rail is a quiet gradient of the same two tokens over the background.
  const defs = `<defs><linearGradient id="${gradientId}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" class="picker-ring-stop-a"></stop><stop offset="1" class="picker-ring-stop-b"></stop></linearGradient><linearGradient id="${gradientId}-rail" x1="0" y1="0" x2="1" y2="1"><stop offset="0" class="picker-ring-stop-rail-a"></stop><stop offset="1" class="picker-ring-stop-rail-b"></stop></linearGradient></defs>`;
  const layer = (cls, dash, shift, extra = '') => `<path class="${cls}" data-picker-ring-path pathLength="100" stroke-dasharray="${dash}" style="--picker-ring-shift:${shift}"${extra} d="${placeholder}"></path>`;
  return `<svg class="picker-focus-ring" aria-hidden="true" focusable="false" pointer-events="none" viewBox="0 0 105 148" style="${delay}">${defs}`
    + `<path class="picker-focus-ring-rail" data-picker-ring-path pathLength="100" stroke="url(#${gradientId}-rail)" d="${placeholder}"></path>`
    + layer('picker-focus-ring-glow', '34 66', 2, ` stroke="url(#${gradientId})"`)
    + layer('picker-focus-ring-tail picker-focus-ring-tail-far', '62 38', 32, ` stroke="url(#${gradientId})"`)
    + layer('picker-focus-ring-tail picker-focus-ring-tail-near', '46 54', 16, ` stroke="url(#${gradientId})"`)
    + layer('picker-focus-ring-arc', '30 70', 0, ` stroke="url(#${gradientId})"`)
    + `</svg>`;
}

function pickerRoundedPath(width, height, innerRadius = 10) {
  const inset = 2;
  const right = Math.max(inset, width - inset);
  const bottom = Math.max(inset, height - inset);
  // The rail's centreline sits `inset` px inside the tile's PADDING box, so its corner
  // radius is the tile's INNER radius (border-radius minus border width) minus that inset
  // clamped to the box -- concentric with the tile's own rounding at every size and theme
  // ( (1), corrected by which measured a 1.6px wedge at the corners).
  const wanted = Math.max(0, (Number.isFinite(innerRadius) ? innerRadius : 10) - inset);
  const radius = Math.max(0, Math.min(wanted, Math.min(right - inset, bottom - inset) / 2));
  const x = inset;
  const y = inset;
  const r = radius;
  return `M ${x + r} ${y} H ${right - r} A ${r} ${r} 0 0 1 ${right} ${y + r} V ${bottom - r} A ${r} ${r} 0 0 1 ${right - r} ${bottom} H ${x + r} A ${r} ${r} 0 0 1 ${x} ${bottom - r} V ${y + r} A ${r} ${r} 0 0 1 ${x + r} ${y} Z`;
}

export function syncPickerFocusRings(scope = document) {
  if (!scope?.querySelectorAll) return;
  scope.querySelectorAll('.picker-focus-ring').forEach((svg) => {
    const tile = svg.closest('.slot-tile');
    if (!tile) return;
    // the SVG fills the padding box, so measure clientWidth/clientHeight (not the
    // border box) and use the inner radius, or the drawing is scaled by ~0.985 and letterboxed.
    const width = tile.clientWidth;
    const height = tile.clientHeight;
    if (!width || !height) return;
    const style = getComputedStyle(tile);
    const innerRadius = Math.max(0, (parseFloat(style.borderTopLeftRadius) || 11) - (parseFloat(style.borderTopWidth) || 0));
    const path = pickerRoundedPath(width, height, innerRadius);
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    svg.querySelectorAll('[data-picker-ring-path]').forEach((element) => element.setAttribute('d', path));
  });
}

const SLOT_CAPTION_WIDTH_PX = 107.2;
const SLOT_CAPTION_HORIZONTAL_PADDING_PX = 5.6;
const SLOT_NAME_CHAR_WIDTH_PX = 7.4;
const SLOT_NAME_LONG_TOKEN_LIMIT = Math.floor(
  (SLOT_CAPTION_WIDTH_PX - (SLOT_CAPTION_HORIZONTAL_PADDING_PX * 2)) / SLOT_NAME_CHAR_WIDTH_PX,
);

// a read-only tile in the board's own markup and style, for previews (Fill plot). No actions, no reveal gating.
// a proposed tile carries the card's own stage state -- green when it can be signed now, orange when the
// card sits on another plot (its unstake first), red when it cools or is dead from Survival -- exactly as the board
// will show it once accepted (`highlight` is that state, or false for a tile that stays as it is).
function previewStateHtml(highlight) {
  if (!highlight) return { cls: '', body: 'Stays as it is.' };
  const state = highlight === true ? 'ready' : highlight;
  const meaning = { ready: 'Proposed: signs with the next batch.', elsewhere: 'Proposed from another plot: its unstake there signs now; the stake here waits for its 72-hour cooldown.', blocked: 'Proposed, waiting: in its cooldown or dead from Survival — staged red until it is ready.', invalid: 'This plan conflicts with the live board. Open Changes to see why or undo it.' };
  return { cls: ` ${STAGE_TILE_STATES[state]?.cls || 'is-stage-ready'}`, body: meaning[state] || meaning.ready };
}
export function renderPreviewTile(group, occupant, { highlight = false, caption = true, emptyLabel = 'Empty', tip = true, extraClass = '', returnMarker = '' } = {}) {
  const shape = `slot-tile-${editorTileShape(group)}`;
  if (!occupant) return `<div class="slot-tile ${shape} is-empty is-view-only${extraClass ? ` ${extraClass}` : ''}" aria-hidden="true">${emptyLabelHtml(emptyLabel)}</div>`;
  const name = occupant.name || 'Unknown'; // a planned card wears its name; the badge says its level
  const captionHtml = caption ? `<span class="${slotCaptionClass(name)}"><span class="slot-name">${esc(name)}</span></span>` : '';
  const state = previewStateHtml(highlight);
  return `<div class="slot-tile ${shape}${occupant.image ? ' has-slot-art' : ''} is-view-only${state.cls}${extraClass ? ` ${extraClass}` : ''}"${tip ? tipAttrs({ title: name, body: state.body }) : ''}>${returnMarker}${slotAssetImageHtml(occupant, group)}${captionHtml}</div>`;
}
// the Power slot's satellite (the Power Core over the Runi tile), read-only.
export function renderPreviewSatellite(group, occupant, { highlight = false, unavailable = false, tip = true, extraClass = '' } = {}) {
  const what = group?.id === 'runi' ? 'Runi' : 'Power Core';
  if (!occupant) return `<div class="slot-power-satellite"><span class="power-satellite is-empty" role="img"${tipAttrs({ title: what, body: unavailable ? `No ${what} available to stake.` : `No ${what} on this plot.` })}><span class="power-satellite-plus">${unavailable ? '–' : '+'}</span></span></div>`;
  const state = previewStateHtml(highlight);
  return `<div class="slot-power-satellite"><span class="power-satellite is-filled${state.cls}${extraClass ? ` ${extraClass}` : ''}" role="img"${tip ? tipAttrs({ title: occupant.name || 'Power Core', body: state.body }) : ''}>${slotAssetImageHtml(occupant, group)}</span></div>`;
}
export function stagePreviewState(cooldown, sourceDeedUid = null) { return stageTileState(cooldown, sourceDeedUid); }

// a two-part label ("Power slot · Add Runi") puts its
// first part above the plus and the second below; a one-part label stays below.
function emptyLabelHtml(label) {
  const parts = String(label || '').split(' · ');
  if (parts.length < 2) return `<span class="plus">+</span><span class="empty-label">${esc(label)}</span>`;
  return `<span class="empty-label empty-label-top">${esc(parts[0])}</span><span class="plus">+</span><span class="empty-label">${esc(parts.slice(1).join(' · '))}</span>`;
}

function slotCaptionClass(name) {
  const longestToken = String(name || '').trim().split(/\s+/).reduce(
    (longest, token) => Math.max(longest, token.length),
    0,
  );
  return `slot-caption${longestToken > SLOT_NAME_LONG_TOKEN_LIMIT ? ' slot-caption--long' : ''}`;
}

// (maintainer: "use the same pulsating animation for the tile that has a staged change ... green (stage is possible
// now) and red (stage is currently not possible yet (died in Survival, has land unstake cooldown). Add a third stage
// orange (is staked elsewhere and needs unstake transaction first and then is in cooldown). Name all three clearly in
// an extended tooltip"). The state comes from the server's cooldown_state() verdict carried on the card.
// (4): the tile says the same words as the Changes modal.
const STAGE_TILE_STATES = {
  ready: { cls: 'is-stage-ready', name: 'Ready to sign' },
  planned: { cls: 'is-stage-planned', name: 'Planned card, signable after the combine' },
  blocked: { cls: 'is-stage-blocked', name: 'Waiting, not signed' },
  invalid: { cls: 'is-stage-blocked', name: "Can't stage" },
  elsewhere: { cls: 'is-stage-elsewhere', name: 'Moving from another plot' },
};
// an asset coming from another plot is orange whatever its cooldown says -- a zero-cooldown item (a Runi)
// still needs its unstake there first.
function stageTileState(cooldown, sourceDeedUid = null) {
  if (cooldown?.state === 'planned') return 'planned';
  if (sourceDeedUid || cooldown?.state === 'staked') return 'elsewhere';
  if (cooldown && cooldown.ready === false) return 'blocked';
  return 'ready';
}
// A red tile whose cooldown end has passed stays red until a live read says otherwise; it says so (no timer).
function stageTileDetail(state, cooldown) {
  if (state === 'ready') return '';
  const reason = cooldownReasonFragment(cooldown);
  const ended = cooldown?.ready_at && Date.parse(cooldown.ready_at) <= Date.now();
  return ended ? `${reason} — that time has passed; reload the page to check it again` : reason;
}

// a staged
// card that waits -- in its cooldown, dead from Survival, or moving from another plot -- carries a gold Waiting badge with
// the time left. A move whose unstake has no end time yet says 72h; a passed end time says "due" (the tile stays red
// until a live read). Shared by the board tile and the Changes strip.
export function stageWaitBadgeHtml(state, cooldown, groupId) {
  if (state !== 'blocked' && !(state === 'elsewhere' && groupId === 'worker')) return '';
  const until = cooldown?.ready_at && Number.isFinite(Date.parse(cooldown.ready_at)) ? cooldown.ready_at : null;
  if (until) Promise.resolve().then(() => bindCountdowns());
  const time = until
    ? `<span class="slot-wait-time" data-countdown-until="${esc(until)}" data-countdown-compact data-countdown-ended="due">${esc(Date.parse(until) <= Date.now() ? 'due' : countdownText(until, Date.now(), { compact: true }))}</span>`
    : state === 'elsewhere' ? '<span class="slot-wait-time">72h</span>' : '';
  const when = until ? humanizeInstant(until)?.absolute : '';
  const body = state === 'elsewhere' ? `Its unstake on the other plot signs first; the stake here waits for its 72-hour cooldown${when ? ` until ${when}` : ''}.` : `Signable ${when ? `after ${when}` : 'once its cooldown ends'}.`;
  return `<span class="slot-wait-badge${state === 'elsewhere' ? ' is-elsewhere' : ''}"${tipAttrs({ title: 'Waiting', body })}>⏳<span class="slot-wait-label">Waiting</span>${time}<span class="slot-wait-clock" aria-hidden="true">◷</span></span>`;
}

// (maintainer: "the badges must be inrow, aligned and start on the top left. Onlz one full width centered badge on one
// tile"): a tile wears at most ONE full-width centred band -- its highest-priority lifecycle state. Ceres had no explicit
// order; this is the one the queue brief set (Echo-compatible: removal and unpowered are bands there too). Ready and failed
// carry no band today (the tile's rim says them), so they sit in the order without markup.
const TILE_BAND_PRIORITY = ['failed', 'removal', 'unpowered', 'waiting', 'moving', 'planned', 'ready'];
function tileBand(states) {
  for (const key of TILE_BAND_PRIORITY) if (states[key]) return states[key];
  return '';
}

// the small tags (Toil & Kin / Companion, cooling) share one row in the name-strip tail.
function tileTagsHtml(...parts) {
  const html = parts.filter(Boolean).join('');
  return html ? `<div class="slot-tags">${html}</div>` : '';
}

// DOM widths are only final after Inter loads. Keep the row hidden until
// this pass has made the explicit fallback choice, then let the normal reveal run.
let tileDecorationFitQueued = false;
export function fitWorkerTileDecorations(root = document) {
  const tiles = root.querySelectorAll('.slot-tile[data-group-id="worker"]');
  for (const tile of tiles) {
    const row = tile.querySelector('.slot-tags');
    if (row && row.clientWidth) {
      const holders = [...row.querySelectorAll('.slot-kin-badge')];
      const companion = row.querySelector('.slot-kin-badge.is-companion');
      const fits = () => [...row.children].flatMap((node) => node.classList.contains('slot-kin-badges') ? [...node.children] : [node])
        .filter((node) => getComputedStyle(node).display !== 'none')
        .reduce((sum, node, i) => sum + node.getBoundingClientRect().width + (i ? parseFloat(getComputedStyle(row).gap) || 0 : 0), 0) <= row.clientWidth + .2;
      if (!fits() && companion) companion.classList.add('is-icon-only');
      if (!fits() && holders.length > 1) {
        holders.slice(1).forEach((badge) => badge.remove());
        const more = document.createElement('span');
        more.className = 'slot-kin-more';
        more.setAttribute('role', 'img');
        more.setAttribute('aria-label', `${holders.length - 1} more Toil and Kin relations; open tile details`);
        more.textContent = `+${holders.length - 1}`;
        row.querySelector('.slot-kin-badges')?.after(more);
      }
      row.dataset.fitted = '1';
    }
    const band = tile.querySelector('.slot-wait-badge');
    if (band) {
      const timer = band.querySelector('.slot-wait-time');
      if (timer && band.scrollWidth > band.clientWidth + 1) {
        timer.classList.add('is-compact-hidden');
        band.classList.add('is-timer-compact');
      }
      const label = band.querySelector('.slot-wait-label');
      if (band.classList.contains('is-planned') && label) {
        const style = getComputedStyle(band);
        const available = band.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
        if (label.getBoundingClientRect().width > available + .2) label.textContent = label.textContent.replace(' after combine', ' · planned');
      }
    }
  }
  for (const grid of root.querySelectorAll('.slot-grid')) {
    const cells = [...grid.querySelectorAll('.slot-tile-cell')];
    cells.forEach((cell, index) => {
      const arc = cell.querySelector('.slot-ability-arc');
      if (!arc) return;
      const icons = [...arc.querySelectorAll('.slot-ability-item')];
      const unit = cell.getBoundingClientRect().width / 107.2;
      const maxDx = Math.max(0, ...icons.map((icon) => Math.abs(parseFloat(icon.style.getPropertyValue('--ax').match(/\*\s*(-?[\d.]+)/)?.[1] || 0) * unit)));
      const box = cell.getBoundingClientRect();
      const leftGap = index ? Math.max(0, box.left - cells[index - 1].getBoundingClientRect().right) : Infinity;
      const rightGap = index < cells.length - 1 ? Math.max(0, cells[index + 1].getBoundingClientRect().left - box.right) : Infinity;
      const gap = Math.min(leftGap, rightGap);
      const diameter = icons[0]?.getBoundingClientRect().width || 0;
      const bound = (box.width + gap - 2 - diameter) / 2;
      if (maxDx > bound && bound > 0) arc.style.setProperty('--arc-scale', String(bound / maxDx));
    });
  }
}
function queueTileDecorationFit() {
  if (tileDecorationFitQueued || typeof document === 'undefined') return;
  tileDecorationFitQueued = true;
  queueMicrotask(() => document.fonts.ready.then(() => requestAnimationFrame(() => {
    tileDecorationFitQueued = false;
    fitWorkerTileDecorations();
  })));
}

function stageBackPlanFor(deedUid, slotIndex) {
  for (const [id, plan] of activeChainPlans()) {
    const source = chainStageBackSource(plan);
    if (source && source.deed_uid === deedUid && Number(source.slot_index) === slotIndex && !CERES_STAGE.some((entry) => entry.asset_ref === `planned:${id}`)) return { id, plan };
  }
  return null;
}

function stageTileFoot(state, detail) {
  // this tile's own state only.
  const meaning = { planned: 'signable after the combine', ready: 'signs with the next batch', blocked: 'died in Survival, or in its land unstake cooldown', invalid: 'the live board now conflicts with this plan; open Changes to see why or undo it', elsewhere: 'its unstake there signs now; the stake here waits for its 72-hour cooldown' }[state];
  return `Staged change — ${STAGE_TILE_STATES[state].name}: ${detail || meaning}.`;
}

function renderEditorTile(row, group, index, occupant, options = {}) {
  const canAct = group.actionable;
  const focusGroup = options.focusGroup || group;
  const stagedEntry = CERES_STAGE.find((entry) => !entry._plan_invalid_reason && entry.deed_uid === row.deed_uid && entry.group_id === group.id && entry.slot_index === index);
  const staged = !!stagedEntry;
  const inFlight = !!(stagedEntry?.txId && (stagedEntry?.broadcast_at || stagedEntry?.queued_at));
  const inFlightLabel = inFlight ? noticeMessage(catalogNotice('verification_in_flight')) : '';
  const textOnly = group.capacity > 20;
  const shapeClass = `slot-tile-${editorTileShape(group)}`;
  const focusDecoration = pickerFocusDecoration(row, focusGroup, index);
  if (!occupant) {
    // FEATURE (the owner, 2026-08-25, mirrors the v0.5.20 staged-ADD undo): "In-game
    // [bringing a removed card back] happens by clicking the empty tile again."
    // A slot emptied by a STAGED removal must never render as a plain empty "add"
    // tile -- that would let the same click mean two different things depending on
    // invisible state. Distinguish it here, before falling into the ordinary empty
    // branches below: look for a staged `unstake` entry at this exact deed/group
    // slot. `entry._previous_asset` is the exact occupant-shaped object
    // stageForSlot() cloned off `group.slots[slotIndex]` at staging time (already
    // run through editorOccupant() upstream in editorGroups()), so it renders with
    // the same fields (image, name, current_harvest_pp, land_abilities, ...) a live
    // occupant tile would -- no second art/data path, no client-side PP math.
    // (maintainer: the planned card appears on the original tile "When the plan is staged"): while the plan still owes
    // this slot its upgraded card (the combine menu's stage-back box), the slot shows that card with the centred badge.
    const owed = group.id === 'worker' ? stageBackPlanFor(row.deed_uid, index) : null;
    if (owed) {
      const asset = editorOccupant({ ...owed.plan.card, level: owed.plan.expected?.level, base_pp: owed.plan.expected?.base_pp, planned: true, chain_id: owed.id, asset_ref: `planned:${owed.id}` }, 'worker');
      const name = asset.name || owed.plan.card?.name || 'Upgraded card';
      const art = textOnly ? '' : slotAssetImageHtml(asset, group);
      const crest = textOnly ? '' : editorTileCrest(group);
      const unstake = CERES_STAGE.find((entry) => entry.chain_id === owed.id && entry.action === 'unstake' && entry.deed_uid === row.deed_uid && entry.slot_index === index);
      const tip = tipAttrs({ title: `${owed.plan.card?.name || 'Upgraded card'} · upgrade plan`, body: `The upgraded card (level ${asset.level}) is staged back here after the combine. ${unstake && !unstake.txId ? 'Sign the unstake in Changes first.' : 'Its stake is signed in the Sign after the combine.'}`, peer: 'tile_actions' });
      return `<div class="slot-tile ${shapeClass}${pickerFocusClass(row, focusGroup, index)} is-staged is-stage-planned is-view-only${asset.image && !textOnly ? ' has-slot-art' : ''}" data-deed-uid="${esc(row.deed_uid)}" data-group-id="${esc(group.id)}" data-slot-index="${index}"${tip}>${focusDecoration}${plannedReturnChip(asset, row.deed_uid, index)}${art}${crest}<span class="${slotCaptionClass(name)}"><span class="slot-name">${esc(name)}</span></span></div>`;
    }
    const removalEntry = CERES_STAGE.find((entry) => !entry._plan_invalid_reason && entry.deed_uid === row.deed_uid && entry.group_id === group.id
      && entry.slot_index === index && entry.action === 'unstake');
    if (removalEntry) {
      const previous = removalEntry._previous_asset || {};
      const name = previous.name || removalEntry._asset_name || 'this card';
      const hasArt = !textOnly && !!previous.image;
      const art = hasArt ? slotAssetImageHtml(previous, group) : '';
      const crest = textOnly ? '' : editorTileCrest(group);
      const tip = tipAttrs({
        title: 'Undo unstake',
        body: `Brings ${name} back and cancels its staged unstake.`,
        ...(inFlight ? {} : { foot: stageTileFoot('ready', 'the unstake signs with the next batch') }),
        peer: 'tile_actions',
      });
      const caption = textOnly
        ? `<span class="slot-caption"><span class="slot-meta">${esc(name)}</span></span>`
        : `<span class="${slotCaptionClass(name)}"><span class="slot-name">${esc(name)}</span></span>`;
      return `<button class="slot-tile ${shapeClass}${pickerFocusClass(row, focusGroup, index)} is-pending-removal${inFlight ? '' : ' is-stage-ready'}${hasArt ? ' has-slot-art' : ''}" data-slot-action="restore" data-deed-uid="${esc(row.deed_uid)}" data-group-id="${esc(group.id)}" data-slot-index="${index}"${inFlight ? ' disabled' : ''}${tip}>${focusDecoration}${tileBand({ removal: '<span class="slot-wait-badge is-removal"><span class="slot-wait-label">Unstaking</span></span>', unpowered: group.id === 'worker' && Number.isInteger(group.poweredCapacity) && index >= group.poweredCapacity ? '<span class="slot-wait-badge is-unpowered"><span class="slot-wait-label">Not powered</span></span>' : '' })}${art}${crest}${caption}</button>`;
    }
    if (stagedEntry && stagedEntry.action !== 'unstake' && inFlight) {
      const pendingAsset = stagedEntry._asset || {};
      const name = stagedEntry._asset_name || pendingAsset.name || 'this card';
      const hasArt = !textOnly && !!pendingAsset.image;
      const art = hasArt ? slotAssetImageHtml(pendingAsset, group) : '';
      const crest = textOnly ? '' : editorTileCrest(group);
      const caption = textOnly
        ? `<span class="slot-caption"><span class="slot-meta">${esc(name)}</span></span>`
        : `<span class="${slotCaptionClass(name)}"><span class="slot-name">${esc(name)}</span></span>`;
      return `<button class="slot-tile ${shapeClass}${pickerFocusClass(row, focusGroup, index)} is-pending-add${hasArt ? ' has-slot-art' : ''}" data-deed-uid="${esc(row.deed_uid)}" data-group-id="${esc(group.id)}" data-slot-index="${index}" disabled${tipAttrs({ title: 'Signed — waiting for the game to confirm', peer: 'tile_actions' })}>${focusDecoration}${art}${crest}${caption}</button>`;
    }
    // Item 17 -- Power Cores are fungible (the owner: "power cords dont need a
    // list, they get just added to the tile after one single click, if at
    // least 1 is available"). Verified before implementing, not assumed: the
    // whole family resolves to ONE `item_detail_id` (322, "Power Core")
    // confirmed live via `/api/board`'s own `unassigned_bonus.power_core`
    // (single catalogue entry, `boost: 0` -- Power Cores carry no per-unit
    // boost at all, unlike Totems/Titles) -- no level, rarity, foil, or
    // edition field exists on this item family (candidates.py's `_item_rows`
    // never reads any such field for items), so there is no real choice to
    // present and a picker really is pure friction here. This does NOT
    // extend to Totem/Title (both vary meaningfully -- rarity tier for
    // totems, Rare/Epic/Legendary production boost for titles) or Runi
    // (view-only, unchanged) -- both keep their pickers untouched below.
    if (group.id === 'power_core') {
      const alternativeSource = alternativePowerSourceLabel(row);
      if (alternativeSource) {
        const tip = tipAttrs({ title: `Powered by an ${tipIcon('abilities/ability_energized.png')}${alternativeSource} worker`, body: 'A Power Core here would do nothing.', peer: 'tile_empty_targets' });
        return `<div class="slot-tile ${shapeClass}${pickerFocusClass(row, focusGroup, index)} is-empty is-unavailable"${tip}>${focusDecoration}<span class="plus">✓</span><span class="empty-label">Power satisfied</span></div>`;
      }
      const count = powerCoresLeft(row.deed_uid);
      if (count > 0 && group.actionable) {
        // Tooltip states the real available count (items 2/3's own rule
        // real numbers, no boilerplate) -- read straight from the already
        // loaded board payload, no extra fetch needed just to show a count.
        const tip = tipAttrs({ title: 'Add a Power Core', key: `${count}`, body: 'Power Cores available to stake.', peer: 'tile_empty_targets' });
        return `<button class="slot-tile ${shapeClass}${pickerFocusClass(row, focusGroup, index)} is-empty" data-slot-action="add-power-core" data-deed-uid="${esc(row.deed_uid)}" data-group-id="${esc(group.id)}" data-slot-index="${index}"${tip}>${focusDecoration}<span class="plus">+</span><span class="empty-label">${esc(editorEmptyLabel(group, index))}</span></button>`;
      }
      // Zero available (or this group isn't actionable right now) -- NOT a
      // button, so a click can never look like it did something and
      // silently fail (the owner: "the slot must not be clickable-to-add...
      // do not let a click appear to work and silently do nothing").
      const reason = count === null ? 'Power Core availability could not be loaded' : !group.actionable ? (group.viewOnlyReason || 'View only') : '0 Power Cores left to stake';
      return `<div class="slot-tile ${shapeClass}${pickerFocusClass(row, focusGroup, index)} is-empty is-unavailable"${tipAttrs({ title: reason, peer: 'tile_empty_targets' })}>${focusDecoration}<span class="plus">+</span><span class="empty-label">Power Core unavailable</span></div>`;
    }
    const viewOnly = group.actionable ? '' : ' is-view-only';
    const label = group.actionable ? (options.emptyLabel || editorEmptyLabel(group, index)) : 'View only';
    // an empty worker slot on an unpowered plot stays addable (the game allows staking there) but is
    // drawn dimmed; the chip on the controls row says why.
    const noPowerEmpty = group.id === 'worker' && group.noPower;
    const tip = group.actionable
      ? (noPowerEmpty ? tipAttrs({ title: 'No power', body: NO_POWER_TIP, peer: 'tile_warnings' }) : '')
      : tipAttrs({ title: 'Not editable here', body: group.viewOnlyReason || 'This slot is shown for reference only.', peer: 'tile_empty_targets' });
    return `<button class="slot-tile ${shapeClass}${pickerFocusClass(row, focusGroup, index)}${viewOnly}${noPowerEmpty ? ' is-no-power' : ''} is-empty" data-slot-action="add" data-deed-uid="${esc(row.deed_uid)}" data-group-id="${esc(group.id)}" data-slot-index="${index}"${group.actionable ? '' : ' disabled'}${tip}>${focusDecoration}${emptyLabelHtml(label)}</button>`;
  }
  // a card staged to MOVE to another plot is leaving this one -- shown here like a staged unstake, in the
  // orange of a move; clicking it undoes the move (the entry lives on the target plot).
  if (!stagedEntry) {
    const leaving = CERES_STAGE.find((entry) => entry._source_deed_uid === row.deed_uid && !entry._synthetic && !entry.txId && !entry._plan_invalid_reason
      && String(entry.asset_ref) === String(occupant.asset_ref));
    if (leaving) {
      const target = BOARD?.plots.find((plot) => plot.deed_uid === leaving.deed_uid);
      const targetLabel = formatPlotLabel(target?.plot || leaving.deed_uid);
      const name = occupant.name || leaving._asset_name || 'this card';
      const hasArt = !textOnly && !!occupant.image;
      const art = hasArt ? slotAssetImageHtml(occupant, group) : '';
      const crest = textOnly ? '' : editorTileCrest(group);
      const tip = tipAttrs({ title: `Moving to plot ${targetLabel}`, body: `Signs the unstake from this plot now; the stake on plot ${targetLabel} waits for its 72-hour cooldown. Click to undo the move.`, peer: 'tile_actions' });
      const caption = textOnly
        ? `<span class="slot-caption"><span class="slot-meta">${esc(name)}</span></span>`
        : `<span class="${slotCaptionClass(name)}"><span class="slot-name">${esc(name)}</span></span>`;
      return `<button class="slot-tile ${shapeClass}${pickerFocusClass(row, focusGroup, index)} is-pending-removal is-stage-elsewhere${hasArt ? ' has-slot-art' : ''}" data-slot-action="undo-move" data-deed-uid="${esc(row.deed_uid)}" data-group-id="${esc(group.id)}" data-slot-index="${index}" data-move-deed-uid="${esc(leaving.deed_uid)}" data-move-group-id="${esc(leaving.group_id)}" data-move-slot-index="${esc(leaving.slot_index)}"${tip}>${focusDecoration}${tileBand({ moving: '<span class="slot-wait-badge is-elsewhere is-moving"><span class="slot-wait-label">Moving</span></span>' })}${art}${crest}${caption}</button>`;
    }
  }
  const remove = canAct ? `<button type="button" class="slot-remove ceres-btn ceres-btn--secondary" data-control-density="dense" data-control-mode="icon" data-slot-action="remove" data-deed-uid="${esc(row.deed_uid)}" data-group-id="${esc(group.id)}" data-slot-index="${index}"${tipAttrs(group.id === 'worker'
    ? { title: 'Stage an unstake', body: 'Worker cards: 72-hour cooldown · signed only in Changes.', peer: 'tile_actions' }
    : { title: 'Stage an unstake', body: 'No cooldown · signed only in Changes.', peer: 'tile_actions' })} aria-label="Stage an unstake"><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M5 5L19 19M19 5L5 19"/></svg></button>` : '';
  const stageState = staged && !inFlight
    ? stagedEntry?._plan_invalid_reason ? 'invalid' : stageTileState(stagedEntry?.cooldown ?? occupant.cooldown, stagedEntry?.action === 'unstake' || ['totem', 'title', 'power_core'].includes(group.id) ? null : stagedEntry?._source_deed_uid)
    : null;
  const baseTip = editorOccupantTip(occupant, group, index, row);
  const detailTip = occupant.planned
    ? { ...baseTip, body: `Not yet combined. ${occupant.ready_at ? `Ready to combine ${humanizeInstant(occupant.ready_at)?.absolute}` : 'Ready in about 3 days'}; its stake can be signed after the combine.`, foot: chainReturn(chainPlans()[occupant.chain_id], row.deed_uid, index)?.label || '' }
    : stageState
    ? { ...baseTip, foot: [baseTip.foot, stageTileFoot(stageState, stageState === 'invalid' ? null : stageTileDetail(stageState, stagedEntry?.cooldown ?? occupant.cooldown))].filter(Boolean).join(' ') }
    : baseTip;
  // the ⚑ tile flag is gone -- the ability icons above the card already say it.
  const soleFlag = '';
  // Item 1 -- a staged candidate the server already marked not-ready (see
  // checkoutBlockedCooldownEntries()) must not render identically to a ready one. Both
  // an icon (glanceable at grid scale) and a caption chip (readable, states words rather
  // than relying on colour alone) point at the same tooltip text.
  const cooling = !!(occupant.cooldown && occupant.cooldown.ready === false);
  const cooldownReason = occupant.planned ? `Not yet combined. ${occupant.ready_at ? `Ready to combine ${humanizeInstant(occupant.ready_at)?.absolute}` : 'Ready in about 3 days'}; its stake can be signed after the combine.` : cooling ? `Not ready to stake -- ${cooldownReasonFragment(occupant.cooldown)}.` : '';
  const waitBadge = stageWaitBadgeHtml(stageState, stagedEntry?.cooldown ?? occupant.cooldown, group.id);
  const coolingFlag = cooling && !waitBadge ? `<span class="slot-flag slot-flag-cooldown"${tipAttrs({ title: cooldownReason, peer: 'tile_warnings' })}>⏳</span>` : '';
  // staked beyond the plot's powered worker slots.
  const unpowered = group.id === 'worker' && Number.isInteger(group.poweredCapacity) && index >= group.poweredCapacity;
  // unpowered is a full-width band (Echo's .tc-badge--unpowered "Not powered"), not a corner flag.
  const unpoweredBand = unpowered ? `<span class="slot-wait-badge is-unpowered"${tipAttrs({ title: 'Unpowered slot', body: `This plot now has ${group.poweredCapacity} powered worker slots; this card is still staked but sits beyond them. Stake a Power Core or an Energized worker to power it again.`, peer: 'tile_warnings' })}><span class="slot-wait-label">Not powered</span></span>` : '';
  // one band, by TILE_BAND_PRIORITY.
  const band = tileBand({ unpowered: unpoweredBand, waiting: waitBadge, planned: plannedReturnChip(occupant, row.deed_uid, index) });
  // a freshly picked (staged) card loads directly and fades in once per pick.
  const pickKey = staged && occupant.image && !textOnly ? `${row.deed_uid}|${group.id}|${index}|${occupant.image}` : '';
  const art = textOnly ? '' : slotAssetImageHtml(occupant, group, pickKey);
  const crest = textOnly ? '' : editorTileCrest(group);
  const noArt = !occupant.image && !textOnly && (group.id === 'power_core' || group.id === 'runi');
  // LOCAL INVARIANT: tiles carry no level and no PP figure -- the plot's Base PP shows
  // once in the editor footer instead, so the same number is never rendered twice.
  const captionName = options.powerLarge && group.id === 'power_core' ? 'Active' : occupant.name; // planned cards keep their name
  const caption = textOnly
    ? `<span class="slot-caption"><span class="slot-meta">${esc(occupant.name)}</span></span>`
    : `<span class="${slotCaptionClass(captionName)}"><span class="slot-name">${esc(captionName)}</span></span>`;
  const addsRuni = group.id === 'power_core' && options.powerLarge;
  // the ability icons and the Toil & Kin / Companion pill sit INSIDE the tile, bottom-anchored above
  // the name (Echo's / placement). A tile wearing the centred waiting / planned badge shows
  // neither, as Echo does. The Energized and six-ability arcs keep their place around the tile's top edge
  // outside the tile's `overflow: hidden`, so they stay a `.slot-tile-cell` sibling.
  // Toil & Kin / Companion and cooling are tags in one measured row in the name-strip tail.
  const kinBadges = !textOnly && group.id === 'worker' ? bloodlineTileBadgesHtml(row, occupant) : '';
  const tags = tileTagsHtml(kinBadges, coolingFlag);
  const abilityHtml = textOnly ? '' : renderAbilityRow(occupant, options.hasEnergizedBubble, { omitBloodline: !!kinBadges });
  const abilityArc = abilityHtml.startsWith('<div class="slot-ability-arc') ? abilityHtml : '';
  const foot = !band && abilityHtml && !abilityArc ? `<div class="slot-foot">${abilityHtml}</div>` : '';
  const tileAction = addsRuni ? 'add-runi' : group.id === 'power_core' ? 'power-core-present' : 'replace';
  const tileActionable = canAct && (group.id !== 'power_core' || addsRuni);
  // The tile carries exactly ONE combine affordance: the compact "Combine N" chip
  // the caller places ABOVE the tile. A second in-tile button duplicated it, wrapped
  // to three lines and dominated the card it belonged to, so nothing renders here.
  // The combine chip is NOT embedded here -- it must sit ABOVE the tile, and
  // `.slot-tile` clips its own content (`overflow: hidden`, needed so art
  // captions never spill past the tile's fixed box). An absolutely-positioned
  // child with a negative top would be clipped here. The caller computes it
  // separately via combineTileActionHtml() and places it as a `.slot-tile-cell`
  // sibling instead.
  // during a reveal (managed art) the tile looks like an empty slot until its
  // picture starts fading in -- eos.js settlePortraitFill removes `is-art-pending`.
  const tileDetail = cooling && !waitBadge ? { ...detailTip, foot: [detailTip.foot, cooldownReason].filter(Boolean).join(' ') } : detailTip;
  const tile = `<div class="slot-tile ${shapeClass}${pickerFocusClass(row, focusGroup, index)}${occupant.image ? ' has-slot-art' : ''}${(occupant.image && managedEditorArt && !pickKey) || (pickKey && pickFadeProgress(pickKey) === null) ? ' is-art-pending is-caption-pending' : pickKey && pickFadeProgress(pickKey) < PICK_FADE_MS ? ' is-art-pending' : ''}${noArt ? ' no-slot-art' : ''}${staged ? ' is-staged' : ''}${stageState ? ` ${STAGE_TILE_STATES[stageState].cls}` : ''}${cooling ? ' is-cooling' : ''}${unpowered ? ' is-unpowered' : ''}${tileActionable && !inFlight ? '' : ' is-view-only'}" data-slot-action="${tileAction}" data-occupied="1" data-deed-uid="${esc(row.deed_uid)}" data-group-id="${esc(group.id)}" data-slot-index="${index}" data-actionable="${tileActionable && !inFlight ? '1' : '0'}" tabindex="${tileActionable && !inFlight ? '0' : '-1'}"${tileActionable && !inFlight ? ' role="button"' : ''}${inFlight ? tipAttrs({ title: inFlightLabel, peer: 'tile_actions' }) : tipAttrs(addsRuni ? { ...tileDetail, foot: 'Add a Runi alongside this Power Core.' } : tileDetail)}>${inFlight ? '' : remove}${focusDecoration}${band}${soleFlag}${tags}${art}${unpowered ? `<span class="slot-no-power" aria-hidden="true">${band ? '' : '<img class="slot-no-power-icon" src="/static/assets/land/no-power-source.svg" alt="">'}</span>` : ''}${crest}${foot}${caption}</div>`;
  return options.cell && abilityArc ? `${abilityArc}${tile}` : tile;
}

function renderEnergizedBadge(occupant) {
  const energized = (occupant?.land_abilities || []).some((entry) => Array.isArray(entry) && entry[0] === 'ENERGIZED');
  if (!energized) return '';
  const energizedImage = `${ASSET_BASE}/abilities/ability_energized.png`;
  return `<div class="slot-energized-satellite"><span class="power-satellite is-filled" role="img"${tipAttrs({ title: 'Energized', icon: 'abilities/ability_energized.png', body: "Powers this plot's worker slots, like a Power Core.", peer: 'tile_power' })}><span class="slot-art slot-art-power_core"><img class="slot-energized-art" src="${energizedImage}" alt=""></span></span></div>`;
}

function renderPowerSatellite(row, group, occupant, { requiresPower = false } = {}) {
  if (occupant) {
    const label = `Remove ${occupant.name}`;
    return `<button type="button" class="power-satellite is-filled" data-slot-action="remove" data-deed-uid="${esc(row.deed_uid)}" data-group-id="${esc(group.id)}" data-slot-index="0" aria-label="${esc(label)}"${tipAttrs(group.id === 'power_core'
    // Gate document #18: what the click stages and what the plot loses.
    ? { title: 'Unstake this Power Core', body: `Zero cooldown · worker slots lose power unless an ${tipIcon('abilities/ability_energized.png')}Energized worker stays.`, peer: 'tile_power' }
    : { title: `Unstake ${occupant.name}`, body: 'Zero cooldown · stages the unstake, signed only in Changes.', peer: 'tile_power' })}>${slotAssetImageHtml(occupant, group)}${foilBadgeHtml(occupant, 'power-satellite-foil')}</button>`;
  }
  const action = group.id === 'power_core' ? 'add-power-core' : 'add';
  const powerWarning = group.id === 'power_core' && requiresPower;
  const label = powerWarning ? 'Add Power Core — plot has no power source' : group.id === 'power_core' ? 'Add Power Core' : 'Add Runi';
  return `<button class="power-satellite is-empty${powerWarning ? ' is-power-required' : ''}" data-slot-action="${action}" data-deed-uid="${esc(row.deed_uid)}" data-group-id="${esc(group.id)}" data-slot-index="0" aria-label="${label}"${tipAttrs(group.id === 'power_core'
    // Gate document #19: what the item gives, not the button's own words.
    ? { title: 'Add a Power Core', body: 'Powers this plot\'s worker slots · zero cooldown.', peer: 'tile_power' }
    : { title: 'Add a Runi', body: `+100% production · ${tipIcon('land/token-dec.png')}DEC stake 0 · powers four worker slots · sixth slot with a Power Core or an Energized worker · Runi itself uses one worker-count slot`, peer: 'tile_power' })}><span class="power-satellite-plus" aria-hidden="true">+</span><span class="power-satellite-label">${label}</span></button>`;
}

function isClearedIdlePlot(row) {
  return row?.badges?.includes('idle') && !row.is_construction && Boolean(row.worksite_type || row.resource);
}

function renderPowerSlot(row, groups) {
  const core = groups.find((group) => group.id === 'power_core');
  const runi = groups.find((group) => group.id === 'runi');
  const coreOccupant = core?.slots[0] || null;
  const runiOccupant = runi?.slots[0] || null;
  const runiRemoval = CERES_STAGE.some((entry) => entry.deed_uid === row.deed_uid && entry.group_id === 'runi' && entry.slot_index === 0 && entry.action === 'unstake');
  const state = coreOccupant && runiOccupant ? 'both' : coreOccupant ? 'core-only' : runiOccupant ? 'runi-only' : 'neither';
  const large = runiOccupant
    ? renderEditorTile(row, runi, 0, runiOccupant)
    : coreOccupant
      ? renderEditorTile(row, core, 0, coreOccupant, { powerLarge: true, focusGroup: S.CERES_PICKER?.groupId === 'runi' ? runi : core })
      : runiRemoval
        ? renderEditorTile(row, runi, 0, null)
      : renderEditorTile(row, runi, 0, null, { emptyLabel: 'Power slot · Add Runi' });
  const satelliteGroup = runiOccupant ? core : coreOccupant ? runi : core;
  const satelliteOccupant = runiOccupant ? coreOccupant : null;
  // (maintainer): a Power Core as the large tile carries no empty Runi bubble above it.
  const workers = groups.find((group) => group.id === 'worker');
  const requiresPower = workers?.noPower === true && (workers.slots.some(Boolean) || isClearedIdlePlot(row));
  const satellite = coreOccupant && !runiOccupant ? '' : renderPowerSatellite(row, satelliteGroup, satelliteOccupant, { requiresPower });
  return `<section class="slot-power" data-power-state="${state}" aria-label="Power slot"><div class="slot-power-stage"><div class="slot-power-large">${large}</div><div class="slot-power-satellite">${satellite}</div></div></section>`;
}

// Item 12 (the owner: "Table header for workers is redundant and shows
// 'Workers 5/5 powered by Power core + Runi'. I dont want to see that
// remove the entire row completely. Build a new row instead only for
// filters, which are left aligned and have a strong contrast."). The old
// `<header class="slot-group-head">` -- label, live count, and the
// "powered by..."/"not powered" constraint line -- is gone entirely, replaced
// by `.slot-group-controls` below: JUST the bulk-action buttons.
// Nothing here was live state read only from that header's text -- `filled`
// `group.capacity` (was "4/5") are still computed the same way below and
// still drive every button's disabled state, just never rendered as
// "N/M" text. The constraint's UNSATISFIED case ("plot produces 0 PP and has
// ZERO worker capacity") is not lost either -- it already renders
// independently via `.slot-zero-state` a few lines down (`group.capacity ===
// 0`), which existed before this change and is untouched. This constraint
// text is NOT the sole-power-source-removal hazard warning (that's a
// different, action-triggered safety message, rendered separately in
// renderSlotEditor's footer / item 19's hazards list) -- only the routine
// "is this plot powered right now" status line is removed here.
function editorVisibleColumns(workerColumns, bundledGroups = [], powerGroup = null, preservePlaceholderPowerRule = false) {
  const units = [...workerColumns];
  // Normal output reserves a power unit with bundled items; placeholders omit it when no power group exists.
  const includePower = preservePlaceholderPowerRule ? !!powerGroup : bundledGroups.length > 0;
  if (includePower) {
    units.push({ kind: 'power', group: powerGroup, index: 0, occupant: powerGroup?.slots?.[0] || null, figure: null, sub: null, tip: null, flags: [] });
  }
  bundledGroups.forEach((group) => units.push({ kind: 'item', group, index: 0, occupant: group.slots[0], figure: null, sub: null, tip: null, flags: [] }));
  return units.flatMap((column, index) => index > 0 && column.kind === 'power' && units[index - 1].kind === 'worker' ? [{ kind: 'divider', flags: [] }, column] : [column]);
}

export function editorVisibleColumnCounts(row, dataReady = true) {
  const frameRow = dataReady ? row : { ...row, workers: [], bonus: {} };
  const groups = editorGroups(frameRow);
  const workerGroup = groups.find((group) => group.id === 'worker');
  if (!workerGroup) return { visibleSlots: 1, visibleDividers: 0 };
  const bundledGroups = groups.filter((group) => group.capacity === 1 && !['power_core', 'runi'].includes(group.id));
  const powerGroup = dataReady
    ? groups.find((group) => group.id === 'runi' && group.slots[0])
      || groups.find((group) => group.id === 'power_core' && group.slots[0])
      || groups.find((group) => group.id === 'runi')
    : groups.find((group) => group.id === 'runi') || groups.find((group) => group.id === 'power_core');
  const workerColumns = workerGroup
    ? dataReady
      ? workerGroup.slots.map((occupant, index) => ({ kind: 'worker', group: workerGroup, index, occupant }))
      : Array.from({ length: workerGroup.capacity }, () => ({ kind: 'worker', group: workerGroup }))
    : [];
  const visibleColumns = editorVisibleColumns(workerColumns, bundledGroups, powerGroup, !dataReady);
  const abilityArcs = dataReady
    ? workerGroup.slots.map((occupant) => abilityArcFlags(orderedWorkerAbilities(occupant), workerHasEnergizedBubble(occupant, workerGroup.capacity)))
    : [];
  return {
    visibleSlots: Math.max(1, visibleColumns.filter((column) => column.kind !== 'divider').length),
    visibleDividers: visibleColumns.filter((column) => column.kind === 'divider').length,
    hasAbilityArc: abilityArcs.some((flags) => flags.hasAbilityArc),
    hasVovArc: abilityArcs.some((flags) => flags.hasVovArc),
  };
}

function renderEditorGroup(row, group, bundledGroups = [], leadingHtml = '', powerGroup = null, columnDescriptors = null, presentation = null, upgradeOptionsLoading = false, controlsTrailingHtml = '') {
  const filled = group.slots.filter(Boolean).length;
  const constraint = group.constraint;
  // R-143, his request: 'Remove "Fill empty slots" button. It only opens the picker.'
  // Verified before removing rather than after: every empty WORKER slot is already a
  // `<button data-slot-action="add">` (see editorSlot below, ~:602) that opens the same
  // picker, so nothing is stranded -- his objection is the redundancy, not the picker.
  // Asset groups KEEP their `fill` action: there it reads 'Add <label>', which is not the
  // label he objected to and not always reachable the same way.
  const actions = group.id === 'worker' ? ['fill_plot', 'clear', 'clear_plot'] : ['fill', 'clear']; // Fill plot
  const plotHasActionableAsset = editorGroups(row).some((item) => item.actionable && item.slots.some(Boolean));
  // View-only reorder toggle -- worker group only, LOCAL INVARIANT: real slot
  // indices never move, only the render order of the derived list below does
  // (: the old handler sorted `row.workers` in place with no way back
  // and the same button appeared on asset groups where it never sorted anything).
  const sorted = false;
  const buttons = actions.map((action) => {
    const isWorkerGroup = group.id === 'worker';
    if (action === 'fill_plot') { // (maintainer-accepted plan ): proposes the best fill of this plot in a modal
      const tip = tipAttrs({ title: 'Fill plot', body: 'Propose the best ready cards and items for every open worker slot; nothing is staged until you accept.', peer: 'bulk_fill_worker' });
      return `<button type="button" class="slot-bulk fill-action ceres-btn ceres-btn--secondary" data-control-density="compact" data-control-case="signal" data-slot-action="fill_plot" data-deed-uid="${esc(row.deed_uid)}" data-group-id="${esc(group.id)}"${group.actionable ? '' : ' disabled'}${tip}>${FILL_TRAY_ICON}<span>Fill plot</span></button>`;
    }
    const fillLabel = isWorkerGroup ? 'Fill empty slots' : `Add ${group.label}`;
    const clearLabel = isWorkerGroup ? 'Clear workers' : `Clear ${group.label}`;
    const label = action === 'fill' ? fillLabel : action === 'clear' ? clearLabel : 'Clear plot';
    const disabled = action === 'fill'
      ? !group.actionable || group.capacity === 0 || filled === group.capacity
      : action === 'clear'
        ? filled === 0
        : action === 'clear_plot'
          ? !plotHasActionableAsset
          : filled < 2;
    // Severity is CAUTION for sort (a view-only toggle that corrects a fear
    // it touches no game state) and DANGER for clear_plot (a real staged
    // unstake with a 72-hour cooldown). The two must render visibly
    // differently, not just say different things -- a glance at the colour
    // alone should tell them apart (the owner, 2026-08-28).
    const tip = action === 'fill'
      // Gate document #20-#22: say that it only stages, and what it costs.
      ? tipAttrs({
        title: isWorkerGroup ? fillLabel : `Add a ${group.label}`,
        body: isWorkerGroup ? 'Stages worker cards in every empty slot · signed only in Changes.' : 'Stages it in the empty slot · signed only in Changes.',
        peer: isWorkerGroup ? 'bulk_fill_worker' : 'tile_empty_targets',
      })
      : action === 'clear'
      ? tipAttrs({
        kind: 'warn',
        severity: 'danger',
        title: clearLabel,
        body: isWorkerGroup ? 'Stages an unstake for every worker card.' : 'Stages its unstake · no cooldown.',
        ...(isWorkerGroup ? { foot: 'Worker cards get a 72-hour cooldown.' } : {}),
        peer: 'bulk_clear_worker',
      })
      : action === 'clear_plot'
      ? tipAttrs({
        kind: 'warn',
        severity: 'danger',
        title: 'Clear plot',
        body: 'Stages an unstake for every card on this plot.',
        foot: 'Worker cards get a 72-hour cooldown. Power Core, Runi, title and totem are cleared too. Cannot be undone once signed.',
        peer: 'bulk_clear_worker',
      })
      : '';
    const pressed = '';
    return `<button type="button" class="slot-bulk ceres-btn ceres-btn--secondary" data-control-density="compact" data-slot-action="${action}" data-deed-uid="${esc(row.deed_uid)}" data-group-id="${esc(group.id)}"${disabled ? ' disabled' : ''}${pressed}${tip}>${esc(label)}</button>`;
  }).join('');
  // Combine door 1 (reachability pass) -- rendered ONLY when at least one worker on
  // this plot actually has a combinable idle duplicate (plotCombineCandidates), never
  // as a disabled placeholder: a visible control is a promise something can be done
  // (the internal rules, the same rule the Idle Cards table's own Combine button already
  // follows). Absent entirely on a non-qualifying plot.
  // The plot-level Combine control is REMOVED unconditionally. Combine is a
  // per-card decision -- which copies are destroyed, and whether that card still
  // has BCX headroom -- so a plot-wide button could only ever be ambiguous about
  // what it would consume. The per-tile chip is the single combine affordance.
  // `tileEntries` is the DERIVED render order; `group.slots`/real slot indices
  // are never mutated -- reordering this array is what makes the toggle
  // reversible ('s second defect: the old code sorted `row.workers` in
  // place with no way back to real slot order).
  const tileEntries = group.slots.map((occupant, slotIndex) => ({ occupant, slotIndex }));
  if (sorted) {
    tileEntries.sort((a, b) => {
      const aValue = a.occupant?.current_harvest_pp;
      const bValue = b.occupant?.current_harvest_pp;
      const aContribution = aValue === null || aValue === undefined ? -Infinity : Number(aValue);
      const bContribution = bValue === null || bValue === undefined ? -Infinity : Number(bValue);
      return bContribution - aContribution || a.slotIndex - b.slotIndex;
    });
  }
  const workerColumns = tileEntries.map(({ occupant, slotIndex }) => ({
    kind: 'worker', group, index: slotIndex, occupant, figure: null, sub: null, tip: null, flags: [],
  }));
  const visibleColumns = editorVisibleColumns(workerColumns, bundledGroups, powerGroup);
  if (columnDescriptors) columnDescriptors.push(...visibleColumns);
  const zeroState = group.capacity === 0 ? `<div class="slot-zero-state" data-reveal-group="zero">⚠ ${esc(constraint?.effect || 'Zero capacity')}</div>` : '';
  const gridColumns = visibleColumns.map((column, order) => {
        if (column.kind === 'worker') {
          const hasEnergizedBubble = workerHasEnergizedBubble(column.occupant, group.capacity);
          const tile = renderEditorTile(row, column.group, column.index, column.occupant, { cell: true, hasEnergizedBubble });
          const energizedBadge = group.capacity > 20 ? '' : renderEnergizedBadge(column.occupant);
          return `<div class="slot-tile-cell eos-permanent-frame" data-eos-permanent-frame="" data-reveal-group="worker-${esc(column.occupant?.asset_ref || column.index)}" data-reveal-wave="${Math.min(3, order)}">${tile}${combineTileActionHtml(row, column.group, column.index, column.occupant)}${energizedBadge}${warningPills('worker', column.index)}</div>`;
        }
        if (column.kind === 'divider') return '<span class="slot-grid-divider" aria-hidden="true"></span>';
        if (column.kind === 'power') return `<div class="eos-permanent-frame" data-eos-permanent-frame="" data-reveal-group="power" data-reveal-wave="3">${leadingHtml}${warningPills(column.group?.id, 0)}</div>`;
        return `<div class="asset-slot eos-permanent-frame" data-eos-permanent-frame="" data-reveal-group="item-${esc(column.group.id)}" data-reveal-wave="3">${renderEditorTile(row, column.group, column.index, column.occupant)}${warningPills(column.group.id, column.index)}</div>`;
      }).join('');
  // `.slot-group-count` kept in the DOM, same `${filled}/${capacity}` text it
  // always had, but visually clipped (ceres.css, same sr-only technique as
  // item 10's `.slot-stage-summary`) -- the owner never sees it (the header
  // it used to live in is gone per item 12), but the underlying value is not
  // "lost silently" per your instruction: it is still there for anything
  // that reads it, including this project's own Playwright coverage
  // (tests/ceres/test_live_slot_editor.py and test_live_checkout.py both
  // read this exact class/format to learn a plot's worker capacity).
  const hiddenCount = `<span class="slot-group-count">${filled}/${group.capacity}</span>`;
  const upgradeStatus = group.id === 'worker' && upgradeOptionsLoading
    ? '<span class="worker-upgrade-loading" role="status" aria-live="polite">Loading upgrade options…</span>' : '';
  // (maintainer, verbatim: "mirror the plots splinter icons with the horizontal style we
  // alreader use in the picker filter. Place it before fill plots with a vertical devider
  // centrally aligned with the buttons. keep their sizes."; placement clarified: "Not the
  // filter row. Iside he plot before the buttons in the top left corner, before 'Fill the
  // plot' button."): the worker group's own top-left button row gets this plot's terrain
  // affinity splinter badges -- originally the exact same call/markup/classes the worker
  // picker's filter bar renders (candidates.js:816); / below changed this
  // row's own call to the non-interactive 'editor-row' surface -- immediately before the
  // Fill plot button, with a divider sized like the picker's own
  // `.picker-bar-sep`. Worker group only: Fill plot only exists on the worker group
  // (`actions` above), and terrain affinity is a worker-card concept. Desktop only: the
  // mobile plot sheet (rows.js's renderMobilePlotSheet/workerSubtable) is a different
  // simpler component with no Fill plot button of its own to sit before.
  // this row is informational only
  // (no filter, no picker) -- 'editor-row' renders the same terrain-affinity-badge
  // markup/classes/sizing as the picker's own 'picker' surface, but as non-interactive
  // spans (no data-picker-action, no aria-pressed). The picker-bar's own filter chips
  // are untouched (still 'picker' surface, still real buttons -- frozen).
  const splinterAffinityHtml = group.id === 'worker'
    ? `<span class="editor-splinter-affinity">${terrainAffinityHtml(row, 'editor-row')}</span><span class="editor-splinter-affinity-sep" aria-hidden="true"></span><span class="editor-plot-lock">${plotLockIconHtml(row, { button: true })}</span><span class="editor-splinter-affinity-sep" aria-hidden="true"></span>`
    : '';
  return `<section class="slot-group" data-group-id="${esc(group.id)}"><div class="slot-group-controls" data-reveal-group="controls">${upgradeStatus}${splinterAffinityHtml}${buttons}${controlsTrailingHtml}</div>${hiddenCount}<div class="slot-grid${group.capacity > 20 ? ' is-compact is-textonly' : ''}">${zeroState}${gridColumns}</div></section>`;
}

function renderEmptyEditorFrame(group, groups = [], presentation = null, layoutColumns = []) {
  if (!group) return '<div class="eos-permanent-frame" data-eos-permanent-frame=""></div>';
  const bundledGroups = groups.filter((item) => item.capacity === 1 && !['power_core', 'runi'].includes(item.id));
  const powerGroup = groups.find((item) => item.id === 'runi') || groups.find((item) => item.id === 'power_core');
  const workerColumns = Array.from({ length: group.capacity }, () => ({ kind: 'worker', group }));
  const visibleColumns = editorVisibleColumns(workerColumns, bundledGroups, powerGroup, true);
  layoutColumns.push(...visibleColumns);
  // (his screenshot: one spinner floating between tiles): every ghost tile carries its own
  // small spinner, centred inside the tile; ceres.css shows them only once rows.js's 400 ms timer
  // sets `is-slow-open` on the expand row, and eos.js hides them under reconnecting/error text.
  const emptyTile = (item) => `<div class="slot-tile slot-tile-${editorTileShape(item)} is-empty is-view-only" aria-hidden="true"><span class="slot-loading-spinner" aria-hidden="true"><span class="spinner spinner-sm"></span></span></div>`;
  const cells = visibleColumns.map((column) => {
    if (column.kind === 'worker') return `<div class="slot-tile-cell eos-permanent-frame" data-eos-permanent-frame="">${emptyTile(column.group)}</div>`;
    if (column.kind === 'divider') return '<span class="slot-grid-divider" aria-hidden="true"></span>';
    if (column.kind === 'power') return `<div class="eos-permanent-frame" data-eos-permanent-frame=""><section class="slot-power" aria-hidden="true"><div class="slot-power-stage"><div class="slot-power-large">${emptyTile(column.group)}</div><div class="slot-power-satellite"><span class="power-satellite is-empty"></span></div></div></section></div>`;
    return `<div class="asset-slot eos-permanent-frame" data-eos-permanent-frame="">${emptyTile(column.group)}</div>`;
  }).join('');
  return `<section class="slot-group" data-group-id="${esc(group.id)}"><div class="slot-group-controls" inert aria-hidden="true"></div><div class="slot-grid${group.capacity > 20 ? ' is-compact is-textonly' : ''}">${cells}</div></section>`;
}

// Renders the `.editor-facts` change-badge cluster for one plot. Each badge uses an
// exact estimate already used by the checkout panel; an unavailable estimate has no
// badge because the authoritative figure remains on the row.
// his words: "Any staged change must also show the chnge to the final production
// as a forth badge in editor-change-badges." Production is linear in Total PP on a plot
// (worksite_swap.projected_rate: work_per_hour = harvest_pp x the resource's rate per PP)
// so the staged figure scales the plot's own measured rate by the same Total PP ratio the
// Total badge already shows -- no second model, never a guessed rate. : SPS scales the
// same way (its pool pays per PP; the plot's own measured SPS/hr carries today's pool rate).
// A plot without a resource or Total PP has nothing to scale and gets no badge.
export function stagedProduction(row, resultingHarvest, resultingWorkPerHour = null) {
  const now = Number(row?.work_per_hour);
  const harvestNow = Number(row?.harvest_pp);
  // an unknown resulting harvest is unknown, not zero (Number(null) === 0 made the
  // badge claim the plot's whole output lost).
  if (resultingHarvest === null || resultingHarvest === undefined || resultingHarvest === '') return null;
  const after = Number(resultingHarvest);
  const resource = String(row?.resource || '').toLowerCase();
  if (!resource || !Number.isFinite(after)) return null;
  // A producing plot scales its own measured rate (today's pool factors included). A plot with no measured rate
  // empty, 0 PP -- takes the server's projection from the worksite model's rate per PP (: the badge
  // shows what the staged changes WILL produce); SPS has no per-PP rate and gets no badge.
  if (Number.isFinite(now) && harvestNow > 0) {
    const projected = now * (after / harvestNow);
    return { resource, now, after: projected, delta: projected - now };
  }
  // the same for a missing server projection: unknown, not a zero rate.
  if (resultingWorkPerHour === null || resultingWorkPerHour === undefined || resultingWorkPerHour === '') return null;
  const projected = Number(resultingWorkPerHour);
  if (!Number.isFinite(projected)) return null;
  const base = Number.isFinite(now) ? now : 0;
  return { resource, now: base, after: projected, delta: projected - base };
}

function fmtRate(value) {
  return num(value, Math.abs(value) < 10 ? 3 : 0);
}

function renderChangeBadges(basePpEstimate, harvestDelta, stagedCount, stageTotal, resultingHarvest, row = null, resultingWorkPerHour = null, grainEconomics = null) {
  if (!stagedCount) return '';
  const badges = [];
  // the plot's Base PP after the staged changes, with the
  // change in brackets -- a plot has one Base PP; the old separate "Base +X" badge said the same change twice.
  if (stageTotal?.estimate?.status === 'point') {
    const change = stageTotal.effectiveAfter - stageTotal.effectiveNow;
    const cls = change < 0 ? 'is-negative' : 'is-positive';
    const cap = stageTotal.loss > 0
      ? ` <span class="change-badge-cap">· cap <strong>−${num(stageTotal.loss)}</strong></span>`
      : '';
    const tip = {
      title: 'Total Base PP after the change',
      key: num(stageTotal.effectiveAfter),
      // Gate document #25: keep the word that matters -- projected.
      body: `Projected · ${signedPp(change)} from ${stagedCount} staged move${stagedCount === 1 ? '' : 's'} against ${num(stageTotal.effectiveNow)} now${stageTotal.loss > 0 ? ` · the ${num(stageTotal.cap)} cap takes ${num(stageTotal.loss)} PP` : ''}`,
      peer: 'editor_change_metrics',
    };
    badges.push(`<span class="editor-change-badge ${cls}"${tipAttrs(tip)}>Total Base <strong>${num(stageTotal.effectiveAfter)}</strong> (${esc(signedPp(change))})${cap}</span>`);
  }
  if (Number.isFinite(harvestDelta) && harvestDelta !== 0) {
    // R-67 renamed this aggregate to Total PP; R-104 confirms convergence, not reversal.
    const cls = harvestDelta < 0 ? 'is-negative' : 'is-positive';
    const tip = {
      title: 'Total PP after the change',
      key: num(resultingHarvest),
      body: `Changes by ${signedPp(harvestDelta)} across your ${stagedCount} staged move(s), after every boost.`,
      peer: 'editor_change_metrics',
    };
    badges.push(`<span class="editor-change-badge ${cls}"${tipAttrs(tip)}>Total ${esc(signedPp(harvestDelta))}</span>`);
  }
  const production = Number.isFinite(harvestDelta) && harvestDelta !== 0 ? stagedProduction(row, resultingHarvest, resultingWorkPerHour) : null;
  if (production && fmtRate(production.delta) !== fmtRate(0)) {
    const cls = production.delta < 0 ? 'is-negative' : 'is-positive';
    const icon = tooltipIconPath('resource', production.resource);
    const label = production.resource === 'sps' ? 'SPS' : production.resource.charAt(0).toUpperCase() + production.resource.slice(1);
    const sign = production.delta > 0 ? '+' : '−';
    // ONE tooltip. The icon inside the badge is decorative (no tooltip of its own)
    // and the resource icon sits inside the Now/After sentences, not in front of each row.
    const inline = tipIcon(icon);
    const moves = `${stagedCount} staged move${stagedCount === 1 ? '' : 's'}`;
    const tip = {
      title: `${label} production after the change`,
      icon,
      kind: 'number',
      items: [
        { name: 'Now', effect: `${fmtRate(production.now)} ${inline}${label} per hour` },
        { name: 'After', effect: `${fmtRate(production.after)} ${inline}${label} per hour` },
      ],
      foot: `Estimate: output follows the Total PP change (${signedPp(harvestDelta)}) of your ${moves}.`,
      peer: 'editor_change_metrics',
    };
    const badgeIcon = icon ? `<img class="resource-icon" src="${ASSET_BASE}/${icon}" alt="" aria-hidden="true">` : '';
    badges.push(`<span class="editor-change-badge editor-change-production ${cls}"${tipAttrs(tip)}>${sign}${esc(fmtRate(Math.abs(production.delta)))} ${badgeIcon}/h</span>`);
  }
  if (grainEconomics && Number.isFinite(grainEconomics.net_delta_before_tax)
      && Number.isFinite(grainEconomics.food_saved) && Number.isFinite(grainEconomics.gross_delta)
      && grainEconomics.gross_delta < 0 && grainEconomics.net_delta_before_tax > 0.005) {
    const tip = {
      title: 'Beneficial change',
      body: `Food savings exceed the harvest loss. ${fmtRate(grainEconomics.food_saved)} less food − ${fmtRate(-grainEconomics.gross_delta)} less harvested = ${fmtRate(grainEconomics.net_delta_before_tax)} more Grain retained per hour, before tax. Projected; gross production figures above are unchanged.`,
      icon: tooltipIconPath('resource', 'grain'), peer: 'editor_change_metrics',
    };
    badges.push(`<span class="editor-change-badge is-positive"${tipAttrs(tip)}>Net gain</span>`);
  }
  return badges.length ? `<span class="editor-change-badges">${badges.join('')}</span>` : '';
}

function editorBasePpColumn(column, capAllocation = null) {
  const occupant = column.occupant;
  const basePp = basePpDisplay(occupant);
  const value = basePp.raw;
  if (column.kind === 'divider') return { ...column, figure: null, sub: null, tip: null, flags: [] };
  if (column.kind === 'item') {
    // `occupant` is null whenever that item is not staked -- renderEditorGroup()
    // pushes the Totem and Title columns unconditionally so the ghost row keeps
    // column register with the tile grid. Dropping the `?.` here threw a
    // TypeError mid-render on 124 of 129 live plots (v0.7.137), which propagated
    // through renderSlotEditor() to updateMountedRow()'s innerHTML assignment and
    // left the editor unrendered, or frozen at its pre-change DOM.
    const boost = Number(occupant?.boost);
    if (!Number.isFinite(boost)) return { ...column, figure: null, sub: 'Boost', tip: null, flags: [] };
    const figure = `+${num(boost * 100)}%`;
    // Gate document #28: which item, what it boosts, with its own icon.
    const itemIcon = column.group?.id === 'totem'
      ? tooltipIconPath('totem', String(occupant.name || '').split(/\s+/)[0])
      : tooltipIconPath('title', occupant.name);
    const scope = column.group?.id === 'title' ? 'every worker' : 'this plot';
    return { ...column, figure, sub: 'Boost', tip: { title: occupant.name, icon: itemIcon, body: `${figure} production for ${scope}`, peer: 'tile_base_pp' }, flags: [] };
  }
  const hasValue = Number.isFinite(value);
  if (!hasValue) return { ...column, figure: null, sub: 'Base PP', tip: null, flags: [] };
  if (column.kind === 'power') {
    if (column.group?.id !== 'runi') return { ...column, figure: null, sub: 'Base PP', tip: null, flags: [] };
    // Gate document #29: the number plus the one fact it needs.
    return { ...column, figure: num(value), sub: 'Base PP', tip: { title: `Runi Base PP ${num(value)}`, body: 'Counts on top of the plot cap.', peer: 'tile_base_pp' }, flags: [] };
  }
  const derivedAfter = capAllocation?.after.get(Number(occupant.slot));
  const isDerived = Number.isFinite(derivedAfter);
  const afterCap = isDerived ? derivedAfter : basePp.afterCap;
  const clipped = Number.isFinite(afterCap) && afterCap < value;
  const displayed = clipped ? afterCap : value;
  const terrain = terrainValue(occupant.terrain_boost);
  const terrainText = terrain === null ? 'Terrain not calculated: plot modifier missing.' : `Terrain ${signedPp(terrain * 100)}% on this plot.`;
  const construction = occupant.construction_pp === null || occupant.construction_pp === undefined
    ? null : Number(occupant.construction_pp);
  const constructionText = Number.isFinite(construction) ? `Boostable ${num(construction)}.` : '';
  const bodyWithFragments = (opening) => [opening, terrainText, constructionText].filter(Boolean).join(' ');
  const penalised = terrain !== null && terrain < 0;
  // a card beyond the plot's powered worker slots produces nothing -- show 0, as the game does.
  const unpoweredColumn = column.group?.id === 'worker' && Number.isInteger(column.group?.poweredCapacity) && column.index >= column.group.poweredCapacity;
  if (unpoweredColumn) return { ...column, figure: '0', sub: 'Boostable PP', tip: { title: 'No power', body: `This plot has ${column.group.poweredCapacity} powered worker slots; this card sits beyond them and produces 0 PP until a Power Core or an Energized worker powers it.`, peer: 'tile_base_pp' }, flags: ['is-unpowered'] };
  // the badge shows what the
  // boosts multiply -- the card's Base PP after the plot's cap, with this plot's terrain applied -- the same figure
  // the Fill plot modal shows. The tooltip carries the parts: Base PP, terrain, and what the cap took.
  const boostable = isDerived && terrain !== null ? afterCap * (1 + terrain) : Number.isFinite(construction) ? construction : displayed * (1 + (terrain || 0));
  const figure = num(boostable);
  const terrainPart = terrain === null ? 'terrain not calculated' : terrain === 0 ? 'neutral terrain' : `${signedPp(terrain * 100)}% terrain`;
  const capPart = clipped ? ` · the plot's ${num(capAllocation?.cap ?? 100000)} cap takes ${num(value - afterCap)}` : '';
  const tip = {
    title: 'Boostable PP',
    key: figure,
    body: `${num(value)} Base PP · ${terrainPart}${capPart}. What this plot's boosts multiply.`,
    ...(isDerived ? { foot: "The cap share is calculated here from this plot's cap, not read from the card." } : {}),
    peer: 'tile_base_pp',
  };
  return {
    ...column,
    figure,
    sub: 'Boostable PP',
    tip,
    ariaLabel: penalised
      ? `Boostable PP ${figure}, reduced from ${num(value)} by a ${num(Math.abs(terrain * 100))}% terrain penalty on this plot`
      : clipped ? `Boostable PP ${figure}, reduced ${num(value - afterCap)} by the plot cap` : `Boostable PP ${figure}`,
    flags: [
      ...(isDerived ? ['is-derived'] : []),
      ...(clipped ? ['is-cap-clipped'] : []),
      ...(penalised ? ['is-terrain-penalised'] : []),
      // the cell's colour follows the terrain sign -- green bonus, red penalty, primary neutral.
      ...(!penalised && terrain !== null && terrain > 0 ? ['is-terrain-bonus'] : []),
    ],
  };
}

function renderEditorBasePpRow(columns, capAllocation = null) {
  if (!columns.length) return '';
  columns.forEach((column) => Object.assign(column, editorBasePpColumn(column, capAllocation)));
  const cells = columns.map((column) => {
    if (column.kind === 'divider') return '<span class="editor-base-pp-divider" aria-hidden="true"></span>';
    const classes = ['editor-base-pp-cell', ...(column.figure ? ['is-filled'] : []), ...column.flags];
    const identity = ` data-group-id="${esc(column.group?.id || '')}" data-slot-index="${esc(column.index ?? '')}"`;
    const attrs = column.figure
      ? `${identity} aria-label="${esc(column.ariaLabel || `${column.sub} ${column.figure}`)}"${tipAttrs(column.tip)}`
      : `${identity} aria-hidden="true"`;
    return `<span class="${classes.join(' ')}"${attrs}>${column.figure ? `<b>${esc(column.figure)}</b>` : ''}</span>`;
  }).join('');
  return `<span class="editor-base-pp-row" aria-label="Per-card Boostable PP">${cells}</span>`;
}

// the plot's warnings while its band renders, so each tile column can wear its pills.
let plotWarningsForRender = null;
function warningPills(groupId, index) { return plotWarningPillsHtml(plotWarningsForRender, groupId, index); }

export function renderSlotEditor(row, presentation = null, { dataReady = true, error = null, upgradeOptionsLoading = false } = {}) {
  managedEditorArt = !!presentation;
  try {
    queueTileDecorationFit();
    return renderEditorBandContents(row, presentation, { dataReady, error, upgradeOptionsLoading });
  } finally { managedEditorArt = false; }
}

export function renderEditorBandContents(row, presentation, { dataReady = true, error = null, upgradeOptionsLoading = false } = {}) {
  const groups = editorGroups(row);
  const worker = groups.find((group) => group.id === 'worker');
  const bundled = groups.filter((group) => group.capacity === 1 && !['power_core', 'runi'].includes(group.id));
  const largeCapacity = worker && worker.capacity > 20;
  const powerGroup = groups.find((group) => group.id === 'runi' && group.slots[0])
    || groups.find((group) => group.id === 'power_core' && group.slots[0])
    || groups.find((group) => group.id === 'runi');
  const columnDescriptors = [];
  const placeholderColumns = [];
  const staged = stagedEntriesForRow(row.deed_uid);
  let capAllocation = null;
  if (staged.length) {
    const workers = effectiveWorkerRoster(row, staged);
    capAllocation = deriveCapAllocation(workers, stagedBasePpCap(row));
    if (capAllocation) recordCapPrediction(row.deed_uid, capPredictionForRow(row, capAllocation));
    else recordCapDerivationFailure(row.deed_uid, row.plot, 'a worker Base PP or slot input is missing');
  } else {
    clearCapDerivationState(row.deed_uid);
  }
  const delta = stageDeltaForRow(row.deed_uid);
  const stageImpact = stageImpactForRow(row.deed_uid);
  const powerLead = renderPowerSlot(row, groups);
  const frameRow = dataReady ? row : { ...row, workers: [], bonus: {} };
  const frameGroups = dataReady ? groups : editorGroups(frameRow);
  const frameWorker = frameGroups.find((group) => group.id === 'worker');
  // the staged-change badges are computed before the groups so the worker group can dock them
  // on its controls line, directly after "Clear plot".
  const stageTotal = stageTotalBasePp(row, groups, staged);
  const basePpStageEstimate = stageTotal.estimate; // the same source as Total Base
  const changeBadgesHtml = renderChangeBadges(basePpStageEstimate, delta, staged.length, stageTotal, stageImpact?.resulting_harvest, row, stageImpact?.resulting_work_per_hour ?? null, stageImpact?.grain_economics ?? null);
  // the "no worker-slot power" warning is decided on the roster that REMAINS after the
  // staged changes -- clearing every worker leaves nothing to power, so it no longer fires.
  const runiCount = row.capacity?.runi_staked === true || row.staking_facts?.is_runi_staked === true ? 1 : 0;
  const remainingRoster = effectiveWorkerRoster(row, staged);
  const remainingWorkers = remainingRoster ? remainingRoster.length
    : Array.isArray(row.workers) ? row.workers.filter((worker) => Number(worker?.slot) > 0).length
      : Math.max(0, Number(row.capacity?.worker_count ?? row.worker_count ?? 0) - runiCount);
  const hasPowerConcern = plotPoweredAfterStage(row) === false
    && (remainingWorkers > 0 || (!staged.length && Number(row.harvest_pp || 0) > 0));
  // when the plot knows which card is unpowered, the warning names the slot, the card and
  // what it costs the whole plot (the same message the WKR badge carries); otherwise the general text.
  const detailedPowerMessage = (row.notices || []).find((notice) => notice.code === 'unpowered_worker'
    && /^Worker slot /.test(String(notice.message || '')))?.message;
  const powerNotice = hasPowerConcern
    ? { ...catalogNotice('workers_staged_without_power', { deed_uid: row.deed_uid }), ...(detailedPowerMessage ? { message: detailedPowerMessage } : {}) } : null;
  // Removal notices are stored on the staged entry so undoing that entry also
  // removes its responsive warning without a separate cleanup path.
  const stagedNotices = [...new Map(
    staged.filter((entry) => entry.action === 'unstake' && entry._notices?.length)
      .flatMap((entry) => entry._notices)
      .map((notice) => [notice.code + JSON.stringify(notice.context || {}), notice]),
  ).values()];
  // one warning model -- pills on the affected cards, one sentence each in the list below.
  const warnings = dataReady ? buildPlotWarnings(row.deed_uid, staged, stagedNotices, powerNotice, capNoticesForRow(row.deed_uid)) : [];
  plotWarningsForRender = warnings;
  const body = dataReady
    ? frameWorker
      ? renderEditorGroup(frameRow, frameWorker, bundled, powerLead, powerGroup, columnDescriptors, presentation, upgradeOptionsLoading, changeBadgesHtml)
      : `<div class="eos-permanent-frame" data-eos-permanent-frame="" data-reveal-group="power">${powerLead}</div>`
    : renderEmptyEditorFrame(frameWorker, frameGroups, presentation, placeholderColumns);
  // The per-row "Review -> Sign -> Verify" button that used to live here is gone
  // superseded by the portfolio Checkout panel, which stages and signs moves from any
  // number of rows (including just this one) through the same `requestOperationPreview()`
  // `renderOperationReview()` path. The Keychain-not-detected warning moved WITH it
  // (maintainer, 2026-09-01: it belongs at Checkout, where signing actually happens, not
  // here) -- see `renderCheckoutSurface()`'s own `keychainWarningHtml`.
  const sceneClass = `${terrainBackgroundPath(row) ? '' : ' no-scene'}${!row.resource && stagedWorksiteBuild(row.deed_uid) ? ' is-staged-worksite' : ''}`;
  const plotIdentity = `<span class="plot-identity">${esc(row.terrain || 'Plot')} · ${esc(row.worksite_type || '—')}</span>`;
  // Plot number, not `deed_uid` (the owner, caught on the staked chip, same
  // ruling applies here: "which is an internal ID that I cannot read. It
  // should say the actual Plot number, i.e. 65-10-88."). `row.plot` is the
  // real `facts.plot` number (see formatPlotLabel(), already used for the
  // main table's own Plot column); `deed_uid` stays the internal staging
  // lookup key end-to-end (CERES_STAGE, updateMountedRow(), etc.) and is kept
  // here only as a title-attribute tooltip for debugging, never in the
  // visible label.
  // Gate document #138: no tooltip -- the plot number is already shown.
  const plotLabelHtml = `<span class="slot-editor-deed">Plot ${esc(formatPlotLabel(row.plot))}</span>`;
  // Worker-table rebuild (maintainer's nine-item spec): this plot-overview line used to sit
  // ABOVE the worker tiles -- it now renders BELOW them (see the `.editor-main` assembly
  // at the bottom of this function), and it now carries Base PP too, not just Harvest PP
  // the two-stage chain's effective total after cap, shown here exactly once per
  // plot rather than only per-tile. `entry.base` remains the pre-cap estimate used to
  // derive the staged allocation, while the rendered cells show the effective figures.
  // A plot can be both over-cap AND changed at once (two
  // independent classes, not a single either/or state) -- CSS layers the
  // change highlight ON TOP of the over-cap colour rather than replacing it.
  // Instant per-plot change badges in `.editor-facts` (maintainer, 2026-09-01: "Any change
  // done to the tiles must be reflected instantly in editor-facts with colorful badges
  // for the exact changes and more details in the tooltips ... basePP change must be
  // recalculated ... and show in large text there instantly"). Nothing here re-derives a
  // PP figure -- `basePpStageEstimate` (above) and `delta` (from `stageDeltaForRow()`
  // computed earlier in this function) are the exact same server-computed estimates the
  // Base PP row and the checkout panel already show, so a badge can never disagree with
  // either. "Instant" falls out of `renderSlotEditor()` already re-running on every
  // CERES_STAGE mutation (stage/unstage/undo) -- no separate refresh or broadcast wait.
  // the plot-facts line (identity, plot, Total PP, boost parts, food cost) is gone -- the
  // plot row directly above shows the same. Only the instant staged-change badges survive; they
  // sit right-aligned on the controls line, left of Close.
  const facts = ''; // badges now render inside the worker group's controls line
  // Scene background (terrain/worksite photo) now lives on `.editor-band`
  // not this outer `<section>` (item 7, the owner: "opening the worker cards
  // list breaks the background image, which must be fixed and never bounce
  // around") -- the inline picker/review panels below are siblings of
  // `.editor-band`, appended INSIDE this `<section>`; when the old code put
  // the scene's `::before`/`::after` (position:absolute; inset:0) directly on
  // the section, opening the picker grew the section's total height (picker
  // content stacking below), and inset:0 stretched the SAME background image
  // to cover that new, taller box -- a visible resize/"bounce" of the scene
  // on every open/close. `.editor-band` only ever wraps the fixed-size facts
  // + tile-grid + footer, so its own box height never changes when the
  // picker opens -- see ceres.css's `.editor-band` comment for the full story.
  const profile = presentation?.geometryProfile;
  // the worksite type keys the art's per-type scale (same normalisation as art.js worksiteOverlayPath).
  const artGeo = worksiteArtGeometry(row); // building centre/bottom inside its image
  const worksiteKind = (row.resource || (row.pending_change?.kind === 'worksite_change' ? row.pending_change.to : '') || '').toLowerCase().replace(/[^a-z]/g, '');
  const layoutColumns = dataReady ? columnDescriptors : placeholderColumns;
  const visibleSlots = Math.max(1, layoutColumns.filter((column) => column.kind !== 'divider').length);
  const visibleDividers = layoutColumns.filter((column) => column.kind === 'divider').length;
  const bandStyle = ` style="--eos-visible-slots:${visibleSlots};--eos-visible-dividers:${visibleDividers};--eos-band-height:${profile?.bandRem || 18.5125}rem;--scene-terrain:none;--scene-worksite:none;--scene-y:87%;"`;
  // Staged-count / modelled-delta text removed from DISPLAY only (item 10
  // the owner: "seem broken and should be removed entirely" -- his complaint is
  // about what's visibly shown, not the underlying data). CERES_STAGE and
  // stageDeltaForRow() are untouched; the portfolio checkout panel still
  // reads them exactly as before. The two figures stay in the DOM inside
  // `.slot-stage-summary`, which ceres.css hides with an sr-only clip (not
  // `display: none` -- that removes text from Playwright's own `inner_text`
  // see the class's own CSS comment) -- never visible to the owner, but still
  // real, live-updating markup for
  // anything (a future stats view, or today's own Playwright regression
  // coverage in tests/ceres/test_live_slot_editor.py) that needs to observe a
  // staged move landing without re-adding a visible dump of the numbers.
  const stageSummary = `<span class="slot-stage-summary"><b>${staged.length}</b> moves staged · ${signedPp(delta)} PP (modelled)</span>`;
  // `.editor-scene` is a pure-decoration div (the terrain/worksite
  // background), first child of `.editor-band` -- see ceres.css's
  // `.editor-scene` comment for why the overflow:hidden clip lives THERE and
  // not on `.editor-band` itself (it's load-bearing for item 5's sticky
  // buttons below, not just tidiness).
  // No per-row server hazards/preview panel here any more -- both were fed by the
  // per-row preview fetch that lived behind the now-removed Review button, and neither has
  // anything left to render. The
  // same server hazards and the same `renderOperationReview()` panel still render inside
  // the portfolio Checkout panel (`renderCheckoutSurface()`), which is the surviving path.
  const worksiteChange = worksiteChangeButtonHtml(row);
  const basePpRow = !largeCapacity && worker ? renderEditorBasePpRow(columnDescriptors, capAllocation) : '';
  // `facts` (plot identity/Harvest PP/Base PP/multiplier/cost) now renders AFTER
  // `.slot-editor-body`, not before it -- the worker tiles are the first thing a
  // reader sees on open; the plot-level summary sits in the row directly below them.
  const notices = plotWarningListHtml(warnings);
  plotWarningsForRender = null;
  const status = `<div class="eos-status"><span class="eos-status-text" role="status" aria-live="polite"></span><a href="#" class="expand-retry-link" data-deed-uid="${esc(row.deed_uid)}" hidden>Retry</a></div>`;
  // R-150, his request: the close button moves to the top right, on the editor-facts
  // line. It is NOT parented into `.editor-facts`, because that element is rendered
  // only when `dataReady` -- putting the button inside it would delete the only way
  // to close a plot that is still loading or has failed, which are exactly the states
  // a user most wants to escape. It stays a sibling of the band, present in every
  // state, positioned onto that line instead.
  const closeBtn = `<button type="button" class="eos-close btn-close chevron-close" aria-label="Close plot ${esc(formatPlotLabel(row.plot))}" data-tip="Close">${CHEVRON_CLOSE_ICON}</button>`;
  // (maintainer: "Remove that new button in the top right corner of the plot, i never wanted that and was a
  // misunderstanding"): the Power Core/Build button beside Close is gone; the harvest box keeps its Power Core
  // button and the worksite window stays the one place to build.
  // The picker is a sibling of `.editor-band`, so Eos cannot hydrate managed candidate art there.
  const inlinePicker = (() => {
    const previousManagedEditorArt = managedEditorArt;
    managedEditorArt = false;
    try {
      return dataReady ? renderInlineCandidatePicker(row) : '';
    } finally {
      managedEditorArt = previousManagedEditorArt;
    }
  })();
  const detailScaffold = dataReady ? '' : ' data-eos-detail-scaffold=""';
  // the loading spinners live inside each scaffold ghost tile (renderEmptyEditorFrame).
  // the
  // `data-terrain-affinity="editor"` vertical column only renders when `presentation` is
  // absent -- that is true for the mobile plot sheet's own call (workerSubtable(row, {mobile
  // true}) passes presentation=null, picker-actions.js), which still moves this exact markup
  // into `.mobile-plot-affinity` (rows.js renderMobilePlotSheet). The desktop board always
  // passes a real presentation, so this column no longer renders there; the horizontal
  // row in `.slot-group-controls` is now the only desktop splinter display.
  return `<section class="slot-editor${largeCapacity ? ' is-large-capacity' : ''}${profile?.narrow ? ' eos-narrow' : ''}"><div class="editor-band${sceneClass}" data-plot-state="preparing" aria-busy="true"${detailScaffold}${bandStyle}><div class="editor-scene" aria-hidden="true"><div class="editor-worksite-art" data-worksite-kind="${esc(worksiteKind)}"${artGeo ? ` style="--art-cx:${artGeo.cx};--art-base:${artGeo.base};--art-h:${artGeo.hRem}rem;--art-l:${artGeo.l};--art-t:${artGeo.t};--art-r:${artGeo.r};--art-ratio:${artGeo.ratio};"` : ''}></div></div><div class="editor-main">${dataReady ? facts : ''}<div class="slot-editor-body">${body}</div><footer class="slot-editor-footer" data-reveal-footer="true">${dataReady ? `${harvestBarHtml(row, { cog: !!worksiteChange })}${basePpRow}${stageSummary}` : ''}</footer>${dataReady && !presentation ? terrainAffinityHtml(row, 'editor') : ''}</div><div class="eos-scenic-veil" aria-hidden="true"><span class="eos-plot-identity">Plot ${esc(formatPlotLabel(row.plot))} · ${esc(row.terrain || 'Land')}</span><span class="eos-horizon"></span></div><div class="eos-worker-cover" aria-hidden="true"></div>${status}${closeBtn}</div>${dataReady ? `${notices}${inlinePicker}` : ''}</section>`;
}

export { appendRich, appendItems } from './tooltip.js';

// the chip overlays the artwork inside its existing box; no tile geometry changes.
// a planned card
// carries the centred full-width badge -- its level after the combine and the time until the combine can run.
export function plannedReturnChip(asset, deedUid, slotIndex) {
  if (!asset?.chain_id) return '';
  const plan = chainPlans()[asset.chain_id];
  const back = chainReturn(plan, deedUid, slotIndex);
  const until = plan ? deriveChain(plan).ready_at : null;
  if (until && Date.parse(until) > Date.now()) Promise.resolve().then(() => bindCountdowns());
  const time = until && Date.parse(until) > Date.now()
    ? `<span class="slot-wait-time" data-countdown-until="${esc(until)}" data-countdown-compact data-countdown-ended="ready">${esc(countdownText(until, Date.now(), { compact: true }))}</span>` : '';
  const when = until ? humanizeInstant(until)?.absolute : '';
  return `<span class="slot-wait-badge is-planned"${tipAttrs({ title: back?.label || `${asset.name} · upgrade plan`, body: `Becomes level ${asset.level} in the combine${when ? `, possible from ${when}` : ''}. Its stake is signed in the Sign after the combine.` })}><span class="slot-wait-label">Lv ${esc(asset.level)} after combine</span>${time}</span>`; // no arrow; label and time, two rows
}
