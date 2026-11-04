// Shared artwork source and fallback for My cards tiles and their owned-copy menu.
const CARD_CDN_BASE = 'https://d36mxiodymuqjm.cloudfront.net';
const CARD_CDN_SET = {
  0: 'cards_v2.2', 1: 'cards_beta', 2: 'cards_v2.2', 3: 'cards_beta', 4: 'cards_untamed', 5: 'cards_untamed',
  6: 'cards_gladiator', 7: 'cards_chaos', 8: 'cards_riftwatchers', 9: 'cards_nightmare', 10: 'cards_soulbound',
  11: 'cards_nightmare', 12: 'cards_rebellion', 13: 'cards_soulboundrb', 14: 'cards_v2.2', 15: 'cards_v2.2',
  16: 'cards_v2.2', 17: 'cards_v2.2', 18: 'cards_v2.2', 19: 'cards_land', 20: 'cards_v2.2', 21: 'cards_land',
};
const CARD_CDN_SOURCE_SET = {
  alpha: 'cards_v2.2', beta: 'cards_beta', chaos: 'cards_chaos', untamed: 'cards_untamed',
  rebellion: 'cards_rebellion', soulbound: 'cards_soulbound', conclave: 'cards_v2.2',
};
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
function cardImagePart(value) {
  return encodeURIComponent(String(value)).replace(/[!'()*]/g, (char) => `%${char.charCodeAt(0).toString(16).toUpperCase()}`);
}
export function cardArtCandidates(card) {
  const sets = [...new Set([CARD_CDN_SET[card.edition], CARD_CDN_SOURCE_SET[String(card.card_set || '').toLowerCase()]].filter(Boolean))];
  if (!card.name || !sets.length) return [];
  const suffix = [1, 2].includes(card.foil) || (card.foil == null && card.gold) ? '_gold' : [3, 4].includes(card.foil) ? '_blk' : '';
  const file = `${cardImagePart(card.name)}${suffix}`;
  const skin = card.skin ? `/${cardImagePart(card.skin)}` : '';
  const out = [];
  sets.forEach((set) => ['jpg', 'png'].forEach((ext) => out.push(`${CARD_CDN_BASE}/${set}${skin}/${file}.${ext}`)));
  if (skin) sets.forEach((set) => ['jpg', 'png'].forEach((ext) => out.push(`${CARD_CDN_BASE}/${set}/${file}.${ext}`)));
  return [...new Set(out)];
}
export const cardArtPlaceholderHtml = () => '<span class="cb-art-missing-text">No image</span>';
export function cardArtImageHtml(card, loading = 'eager') {
  const art = cardArtCandidates(card);
  return art.length
    ? `<img src="${esc(art[0])}" alt="" loading="${loading}" decoding="async" data-cb-art="${esc(JSON.stringify(art))}" data-cb-art-index="0">`
    : cardArtPlaceholderHtml();
}
export function onCardArtError(event) {
  const img = event.target;
  if (!(img instanceof HTMLImageElement) || img.dataset.cbArt === undefined) return;
  let list = [];
  try { list = JSON.parse(img.dataset.cbArt || '[]'); } catch (_) { list = []; }
  const next = Number(img.dataset.cbArtIndex || 0) + 1;
  if (next < list.length) {
    img.dataset.cbArtIndex = String(next);
    img.src = list[next];
    return;
  }
  const box = img.parentElement;
  img.remove();
  if (box) {
    box.classList.add('cb-art-missing');
    box.insertAdjacentHTML('afterbegin', cardArtPlaceholderHtml());
  }
}
