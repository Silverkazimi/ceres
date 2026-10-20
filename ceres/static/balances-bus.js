// producers call this only after the game (or external settlement) confirms.
export const BALANCES_CHANGED = 'ceres:balances-changed';

const emitted = new Set();
export function balancesChanged({ player, assets = [], regions = [], id } = {}) {
  if (!player) return false;
  if (!assets.length && !regions.length) return false;
  const key = id ? `${String(player).toLowerCase()}:${id}` : null;
  if (key && emitted.has(key)) return false;
  if (key) { emitted.add(key); if (emitted.size > 500) emitted.delete(emitted.values().next().value); }
  const detail = {
    player: String(player).toLowerCase(),
    assets: [...new Set(assets.map(value => String(value).toUpperCase()))],
    regions: [...new Set(regions.map(String))],
  };
  window.dispatchEvent(new CustomEvent(BALANCES_CHANGED, { detail }));
  return true;
}

// Several confirmations in one batch cause one read per listening view.
export function onBalancesChanged(read, { player, delay = 40 } = {}) {
  let timer = null;
  let pending = { assets: new Set(), regions: new Set() };
  const listener = event => {
    const detail = event.detail || {};
    const owner = typeof player === 'function' ? player() : player;
    if (owner && String(owner).toLowerCase() !== detail.player) return;
    for (const asset of detail.assets || []) pending.assets.add(asset);
    for (const region of detail.regions || []) pending.regions.add(region);
    clearTimeout(timer);
    timer = setTimeout(() => {
      timer = null;
      const changed = pending;
      pending = { assets: new Set(), regions: new Set() };
      read({ assets: [...changed.assets], regions: [...changed.regions] });
    }, delay);
  };
  window.addEventListener(BALANCES_CHANGED, listener);
  return () => { clearTimeout(timer); window.removeEventListener(BALANCES_CHANGED, listener); };
}
