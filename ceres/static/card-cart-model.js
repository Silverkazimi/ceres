// card-cart-model.js -- / : the Market's card menu and cart, pure functions (no imports, no DOM), so the vm
// harness tests them directly (tests/ceres/test_ir9200_shop_cart.py).
// (maintainer: "when clicking a card in the shop, open a menu that shows all available copies, a bundled buy for max and
// other levels, add to cart, price in dollar and dec, remove from cart. all clean in our approved style. use color coding for
// special situations"). (maintainer: "i want to be able to add one maxed card (or as many copies as it takes to combine
// one) to my cart with one button click"). (maintainer decision): checkout buys from Ceres in a later step; the cart only
// collects. A cart item is one listing, held by its opaque key (`lkey`, never shown) with the price seen when it was added.

export const CART_VERSION = 1;
export const CART_KEY_PREFIX = 'ceres:cards:cart:';
export const CART_MAX_ITEMS = 200;

export function cartStorageKey(player) {
  return CART_KEY_PREFIX + (String(player || '').trim().toLowerCase() || '_');
}

export function cartPrintId(row) {
  return `${Number(row.card_detail_id)}-${Number(row.edition)}-${Number(row.foil)}`;
}

const finite = (value) => (value === null || value === undefined || value === '' ? null : (Number.isFinite(Number(value)) ? Number(value) : null));

// One listing -> one cart item. Only what the cart panel shows and the re-check needs; no uid, seller or market id exists here.
export function cartItemFrom(listing, extra = {}) {
  if (!listing || !listing.lkey) return null;
  return {
    lkey: String(listing.lkey),
    pid: cartPrintId(listing),
    name: String(listing.name || 'Unknown card'),
    card_detail_id: Number(listing.card_detail_id),
    edition: Number(listing.edition),
    foil: Number(listing.foil),
    card_set: listing.card_set || null,
    rarity: listing.rarity || null,
    level: finite(listing.level),
    bcx: finite(listing.bcx),
    land_base_pp: finite(listing.land_base_pp),
    price_usd: finite(listing.price_usd),
    price_dec: finite(listing.price_dec),
    seen_usd: finite(listing.price_usd),
    bundle: extra.bundle || null,
    group: extra.group ? String(extra.group) : null,  // one "Add maxed card" press = one group, removed as one
    via: extra.via ? String(extra.via) : null,        // the tile (its listing keys) whose button added the group
    status: 'ok',
    was_usd: null,
    added_at: Number(extra.now) || 0,
  };
}

function cleanItem(raw) {
  if (!raw || typeof raw !== 'object' || typeof raw.lkey !== 'string' || !/^[0-9a-f]{8,64}$/.test(raw.lkey)) return null;
  if (!/^\d+-\d+-\d+$/.test(String(raw.pid || ''))) return null;
  const status = ['ok', 'changed', 'unverified'].includes(raw.status) ? raw.status : 'ok';
  return { ...raw, status, price_usd: finite(raw.price_usd), price_dec: finite(raw.price_dec), seen_usd: finite(raw.seen_usd), was_usd: finite(raw.was_usd) };
}

export function cartParse(text) {
  let data = null;
  try { data = JSON.parse(text || 'null'); } catch (_) { data = null; }
  const items = data && data.v === CART_VERSION && Array.isArray(data.items) ? data.items.map(cleanItem).filter(Boolean) : [];
  const seen = new Set();
  return { items: items.filter((it) => (seen.has(it.lkey) ? false : seen.add(it.lkey))).slice(0, CART_MAX_ITEMS), removed: [] };
}

export function cartSerialise(cart) {
  return JSON.stringify({ v: CART_VERSION, items: (cart.items || []).slice(0, CART_MAX_ITEMS) });
}

// Every storage access is wrapped: a blocked or full storage leaves a cart for this visit only.
export function cartLoad(storage, player) {
  try { return cartParse(storage ? storage.getItem(cartStorageKey(player)) : null); } catch (_) { return { items: [], removed: [] }; }
}
export function cartStore(storage, player, cart) {
  try { if (storage) storage.setItem(cartStorageKey(player), cartSerialise(cart)); return true; } catch (_) { return false; }
}

export function cartKeys(cart) {
  return new Set((cart.items || []).map((it) => it.lkey));
}
export function cartHas(cart, lkey) {
  return Boolean(lkey) && (cart.items || []).some((it) => it.lkey === lkey);
}
export function cartHasAll(cart, keys) {
  const set = cartKeys(cart);
  return Array.isArray(keys) && keys.length > 0 && keys.every((k) => set.has(k));
}
export function cartCountForPrint(cart, pid) {
  return (cart.items || []).filter((it) => it.pid === pid).length;
}

// Adds listings (the player's own and key-less ones never); returns how many were new.
export function cartAdd(cart, listings, extra = {}) {
  const have = cartKeys(cart);
  let added = 0;
  for (const listing of listings || []) {
    if (!listing || listing.own || !listing.lkey || have.has(listing.lkey) || cart.items.length >= CART_MAX_ITEMS) continue;
    const item = cartItemFrom(listing, extra);
    if (!item) continue;
    cart.items.push(item);
    have.add(item.lkey);
    added += 1;
  }
  return added;
}

export function cartRemove(cart, keys) {
  const drop = new Set(Array.isArray(keys) ? keys : [keys]);
  const before = cart.items.length;
  cart.items = cart.items.filter((it) => !drop.has(it.lkey));
  return before - cart.items.length;
}

// the maxed cards added with one press (groups "max-…"; bundles are "lv-…"), per print, oldest first: { group, via, keys }.
export function cartMaxGroups(cart, pid) {
  const groups = new Map();
  for (const it of cart.items || []) {
    if (!it.group || !String(it.group).startsWith('max-') || (pid && it.pid !== pid)) continue;
    if (!groups.has(it.group)) groups.set(it.group, { group: it.group, via: it.via || null, pid: it.pid, keys: [] });
    groups.get(it.group).keys.push(it.lkey);
  }
  return [...groups.values()];
}

export function cartRemoveGroup(cart, group) {
  const before = cart.items.length;
  cart.items = cart.items.filter((it) => it.group !== group);
  return before - cart.items.length;
}

export function cartClear(cart) {
  cart.items = [];
  cart.removed = [];
}

export function cartTotals(cart) {
  let usd = 0;
  let dec = 0;
  let decKnown = true;
  for (const it of cart.items || []) {
    usd += it.price_usd || 0;
    if (it.price_dec > 0) dec += it.price_dec; else decKnown = false;
  }
  return { count: (cart.items || []).length, usd, dec: decKnown ? dec : null };
}

// Items grouped by card print, in the order the prints were first added.
export function cartGroups(cart) {
  const groups = new Map();
  for (const it of cart.items || []) {
    if (!groups.has(it.pid)) groups.set(it.pid, { pid: it.pid, name: it.name, foil: it.foil, edition: it.edition, card_set: it.card_set, rarity: it.rarity, items: [], usd: 0, dec: 0, cc: 0 });
    const g = groups.get(it.pid);
    g.items.push(it);
    g.usd += it.price_usd || 0;
    g.dec += it.price_dec || 0;
    g.cc += it.bcx || 0;
  }
  return [...groups.values()];
}

// Re-check against what the market says now for some prints: { pid: { listings: { lkey: { price_usd, price_dec } }, complete } }.
// A key missing from a completely read print is sold (removed from the cart, kept in `removed` for the note); missing from a
// print read only in part (upstream gives at most 60 listings) it cannot be confirmed (amber); a new price is "changed".
export function cartApplyCheck(cart, prints) {
  const out = { sold: 0, changed: 0, unverified: 0 };
  if (!prints || typeof prints !== 'object') return out;
  const keep = [];
  for (const it of cart.items) {
    const p = prints[it.pid];
    if (!p) { keep.push(it); continue; }
    const live = (p.listings || {})[it.lkey];
    if (!live) {
      if (p.complete) { cart.removed.push({ ...it, status: 'sold' }); out.sold += 1; continue; }
      keep.push({ ...it, status: 'unverified' });
      out.unverified += 1;
      continue;
    }
    const usd = finite(live.price_usd);
    if (usd !== null && it.seen_usd !== null && Math.abs(usd - it.seen_usd) > 1e-9) {
      keep.push({ ...it, status: 'changed', was_usd: it.seen_usd, price_usd: usd, price_dec: finite(live.price_dec) });
      out.changed += 1;
    } else {
      keep.push({ ...it, status: it.status === 'changed' ? 'changed' : 'ok', price_usd: usd ?? it.price_usd, price_dec: finite(live.price_dec) ?? it.price_dec });
    }
  }
  cart.items = keep;
  return out;
}

// The same check from one print's full menu read (listings as /api/market/print gives them).
export function cartCheckFromPrint(cart, data) {
  if (!data || !data.print) return { sold: 0, changed: 0, unverified: 0 };
  const listings = {};
  for (const l of data.listings || []) if (l.lkey) listings[l.lkey] = { price_usd: l.price_usd, price_dec: l.price_dec };
  return cartApplyCheck(cart, { [data.print.id]: { listings, complete: !data.truncated } });
}

// the 0/1 min-cost cover -- the client twin of card_market._cover_states: for each CC total reached (capped
// at `cap`), the cheapest set of offers [cc, price] that sums to it, fewest offers on a tie (prices compared in integer micro-$).
export function cmCoverStates(offers, cap) {
  const goal = Math.floor(Number(cap));
  let best = new Map([[0, [0, 0, []]]]);
  if (!(goal > 0)) return best;
  offers.forEach(([cc, price], index) => {
    const c = Math.floor(Number(cc));
    const cost = Number(price);
    if (!(c > 0) || !(cost >= 0)) return;
    const micro = Math.round(cost * 1e6);
    const next = new Map(best);
    for (const [reached, [total, count, chosen]] of best) {
      if (reached >= goal) continue;
      const to = Math.min(goal, reached + c);
      const cand = [total + micro, count + 1, [...chosen, index]];
      const cur = next.get(to);
      if (!cur || cand[0] < cur[0] || (cand[0] === cur[0] && cand[1] < cur[1])) next.set(to, cand);
    }
    best = next;
  });
  return best;
}

// The cheapest set reaching at least `threshold` (states capped at `cap`); indices or null.
export function cmCoverFor(states, threshold, cap) {
  const need = Math.min(Number(threshold), Number(cap));
  let hit = null;
  for (const [reached, value] of states) {
    if (reached <= 0 || reached < need) continue;
    if (!hit || value[0] < hit[0] || (value[0] === hit[0] && value[1] < hit[1])) hit = value;
  }
  return hit ? hit[2] : null;
}

export function cmCheapestCover(offers, target) {
  return cmCoverFor(cmCoverStates(offers, target), target, target);
}

// The listings a new purchase can use: not your own, identifiable, and not already in the cart. (maintainer: "the following
// cards must always show the price AFTER the first maxed card has been removed from available cards"): whatever is in the cart
// counts as bought, so every price shown in the menu is the price of the next card.
export function cmFreeListings(data, cart) {
  const inCart = cartKeys(cart);
  return (data?.listings || []).filter((l) => !l.own && l.lkey && !inCart.has(l.lkey));
}

// the listings of the next maxed card -- the cheapest single max-level listing or combination from the free
// listings. Black foils: the cheapest free copy.
export function cmMaxedPick(data, cart) {
  if (!data || !data.print) return { listings: [], reason: 'This card could not be read.' };
  const pool = cmFreeListings(data, cart);
  const already = cartMaxGroups(cart, data.print.id).length > 0 || (data.listings || []).some((l) => cartHas(cart, l.lkey));
  const bcxMax = data.print.bcx_max;
  if (bcxMax === 0) {
    const cheapest = [...pool].sort((a, b) => a.price_usd - b.price_usd)[0];
    return cheapest ? { listings: [cheapest] } : { listings: [], reason: 'No further copy of this card is on sale.' };
  }
  if (!(bcxMax > 0)) return { listings: [], reason: 'The level table of this card is not known, so a maxed card cannot be combined.' };
  const chosen = cmCheapestCover(pool.map((l) => [l.bcx, l.price_usd]), bcxMax);
  if (!chosen) {
    const cc = pool.reduce((sum, l) => sum + (l.bcx || 0), 0);
    return { listings: [], reason: `Not enough copies on sale for ${already ? 'another' : 'a'} maxed card: ${Math.round(cc).toLocaleString('en-US')} of ${Math.round(bcxMax).toLocaleString('en-US')} CC.` };
  }
  return { listings: chosen.map((i) => pool[i]) };
}

const ppAt = (maxPp, cc, bcxMax) => (maxPp > 0 && cc > 0 && bcxMax > 0 ? maxPp * Math.min(cc, bcxMax) / bcxMax : null);

// the bundles per level from the free listings (the client twin of card_market.level_bundles, so they follow
// the cart): max level first; a level whose cheapest set already reaches a higher level is shown once, at that level; a level
// the free listings cannot reach says how much CC is missing. `thresholds` is the game's level table for the print.
export function cmLevelBundles(data, cart) {
  const p = data?.print;
  if (!p) return [];
  const pool = cmFreeListings(data, cart);
  const pack = (level, parts, reached, max) => {
    const cc = parts.reduce((s, l) => s + (l.bcx || 0), 0);
    const usd = parts.reduce((s, l) => s + (l.price_usd || 0), 0);
    const dec = parts.every((l) => l.price_dec > 0) ? parts.reduce((s, l) => s + l.price_dec, 0) : null;
    const pp = p.bcx_max === 0 ? Math.max(0, ...parts.map((l) => l.land_base_pp || 0)) || null : ppAt(p.max_level_pp, cc, p.bcx_max);
    return { level, reached_level: reached, max, reachable: true, bcx: cc, count: parts.length, keys: parts.map((l) => l.lkey),
      parts_cc: parts.map((l) => l.bcx), price_usd: usd, price_dec: dec, land_base_pp: pp, pp_per_usd: pp && usd > 0 ? pp / usd : null };
  };
  if (p.bcx_max === 0) {
    const cheapest = [...pool].sort((a, b) => a.price_usd - b.price_usd)[0];
    return cheapest ? [pack(p.max_level, [cheapest], p.max_level, true)] : [{ level: p.max_level, max: true, reachable: false, cc_needed: 1, cc_available: 0 }];
  }
  const thresholds = Array.isArray(p.thresholds) ? p.thresholds : null;
  if (!thresholds || !(p.bcx_max > 0) || !p.max_level) return [];
  const states = cmCoverStates(pool.map((l) => [l.bcx, l.price_usd]), p.bcx_max);
  const available = pool.reduce((s, l) => s + (l.bcx || 0), 0);
  const levelOf = (cc) => thresholds.reduce((lv, t, i) => (cc >= t ? i + 1 : lv), 0);
  const out = [];
  const seen = new Set();
  for (let level = p.max_level; level >= 1; level -= 1) {
    const chosen = cmCoverFor(states, thresholds[level - 1], p.bcx_max);
    if (!chosen) { out.push({ level, max: level === p.max_level, reachable: false, cc_needed: thresholds[level - 1], cc_available: available }); continue; }
    const parts = chosen.map((i) => pool[i]);
    const signature = parts.map((l) => l.lkey).sort().join(',');
    if (seen.has(signature)) continue;
    seen.add(signature);
    const cc = parts.reduce((s, l) => s + (l.bcx || 0), 0);
    out.push(pack(level, parts, levelOf(cc), level === p.max_level));
  }
  return out;
}

// The colour-coded state of one listing row in the menu.
export function cmRowState(listing, cart) {
  if (listing.own) return 'own';
  if (cartHas(cart, listing.lkey)) return 'in-cart';
  if (!listing.lkey) return 'unavailable';
  return listing.over_flag ? 'over' : 'ok';
}

// What of this print is in the cart, as the buyer added it: each one-press group (a maxed card or a bundle) and the single copies.
export function cartPrintParts(cart, pid) {
  const parts = new Map();
  for (const it of cart.items || []) {
    if (it.pid !== pid) continue;
    const id = it.group || `single:${it.lkey}`;
    if (!parts.has(id)) parts.set(id, { id, group: it.group || null, label: it.bundle || null, items: [] });
    parts.get(id).items.push(it);
  }
  return [...parts.values()];
}
