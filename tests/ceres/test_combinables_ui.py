""" "Find combinables" on the Cards page (card-combinables.js), its board hand-off (combine-dialog.js, /?combine=<print>),
 (the combine dialog's "To max level" preset never passes max CC), and the Cards header items handed to this row:
 (the cart button is its icon and count),  (a Market tile that is not maxed shows "Lv N" where Maxed sits),
 (the whole cheap copy row in green),  ("PP/$").

The JS runs in the shared vm sandbox (tests/ceres/vm_context.py) on the real modules; no network, no browser."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .vm_context import ENTRY, run_in_module_context

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ceres" / "static"
BROWSER = STATIC / "card-browser.js"


def _out(body: str, entry: Path) -> dict:
    out = run_in_module_context(body, entry=entry).stdout
    return json.loads(out.strip().splitlines()[-1])


# "To max level" stops at max CC

FIT_JS = r"""
const copy = (uid, bcx, over = {}) => ({ uid, bcx, bcx_max: 11, level: 1, level_bcx_thresholds: [1, 3, 6, 9, 11], base_pp: 100 * bcx, available: true, ...over });
const sum = (list) => list.reduce((n, c) => n + c.bcx, 0);
const uids = (list) => list.map((c) => c.uid).sort();
// the  case: 6 + 4 + 3 = 13 on an 11-CC card with the old largest-first preset
const a = [copy('A', 6), copy('B', 4), copy('C', 3), copy('D', 1)];
const exact = combineFitToMaxCopies(a, 11);
const b = [copy('E', 6), copy('F', 6), copy('G', 3), copy('H', 3)];       // no subset makes 11: the largest under it (9)
const under = combineFitToMaxCopies(b, 11);
const c = [copy('M', 11), copy('S1', 1), copy('S2', 1), copy('S3', 1)];  // a maxed copy never joins a set
const withMax = combineFitToMaxCopies(c, 11);
const none = combineFitToMaxCopies([copy('X', 7), copy('Y', 6)], 11);
const singles = Array.from({ length: 400 }, (_, i) => copy(`S${String(i).padStart(3, '0')}`, 1));
const t0 = Date.now();
const many = combineFitToMaxCopies(singles, 400);
const ms = Date.now() - t0;
// the dialog: preset sizes and the explicit selection
const dialog = { copies: [...a], blocked: new Map() };
const presets = combinePresetQuantities(dialog);
combineApplySelection(dialog, combineToMaxCopies(dialog), 'to_max');
const selected = [...COMBINE_SELECTION.values()];
const ledger = combineLedgerData(dialog);
// a proposed set with a copy that is not available is refused, the selection stays
const blocked = { copies: [...a], blocked: new Map([['D', 'listed on the market']]) };
combineApplySelection(blocked, combineToMaxCopies(blocked), 'to_max');
const refused = combineApplySelection(blocked, ['A', 'D'], 'proposed');
const accepted = combineApplySelection(blocked, ['B', 'C'], 'proposed');
// a quantity clears the explicit preset
combineApplyQuantity(blocked, 2);
console.log(JSON.stringify({
  exact: uids(exact), exactSum: sum(exact), underSum: sum(under), under: uids(under), withMax: uids(withMax), none,
  manyLen: many.length, manySum: sum(many), ms,
  toMax: presets.toMax, selectedSum: sum(selected), preset: dialog.preset, after: ledger.afterBcx, before: ledger.beforeBcx,
  refused, accepted, blockedPresetAfterQty: blocked.preset,
}));
"""


def test_to_max_level_never_passes_max_cc():
    got = _out(FIT_JS, ENTRY)
    assert got["exactSum"] == 11 and got["exact"] == ["A", "B", "D"]          # exactly max when the copies allow it
    assert got["underSum"] == 9 and len(got["under"]) == 2                    # never over (the old preset made 12)
    assert got["withMax"] == ["S1", "S2", "S3"] and got["none"] is None
    assert got["manyLen"] == 400 and got["manySum"] == 400 and got["ms"] < 1500  # a 400-single common stays quick
    assert got["toMax"] == 3 and got["selectedSum"] == 11 and got["preset"] == "to_max"
    assert got["after"] == 11 and got["before"] == 6                          # the ledger follows the chosen set
    assert got["refused"] is False and got["accepted"] is True and got["blockedPresetAfterQty"] is None


def test_dialog_uses_the_fit_and_takes_the_proposed_set():
    dialog = (STATIC / "combine-dialog.js").read_text(encoding="utf-8")
    combine = (STATIC / "combine.js").read_text(encoding="utf-8")
    assert "export function openCombineGroupDialog(key, { preselect = null } = {})" in dialog
    assert "combineApplySelection(COMBINE_DIALOG, proposed, 'proposed')" in dialog
    assert "const fit = combineToMaxCopies(COMBINE_DIALOG);" in dialog
    assert "preset === 'to_max' ? combineToMaxCopies(COMBINE_DIALOG) : COMBINE_DIALOG.proposed" in dialog
    assert "{ plus_one: presets.plusOne, everything: presets.everything }" in dialog            # to_max is no quantity any more
    assert "out.toMax = combineQuantityForTargetBcx" not in combine
    # the signing flow is unchanged: confirm, then the build and the verify of S3
    assert "if (kind === 'sign') { confirmCombineBroadcast(); return; }" in dialog


ADDRESS_JS = r"""
const ok = combinePrintFromAddress('?player=player0005&combine=433-7-0');
const bad = ['?combine=C7-433-ABC', '?combine=433-7', '?combine=433-7-9', '?plot=1'].map(combinePrintFromAddress);
const group = { print: { id: '433-7-0', name: 'Chaos Dragon', card_detail_id: 433, edition: 7, foil: 0, card_set: 'chaos', rarity: 'legendary',
  bcx_max: 11, thresholds: [1, 3, 6, 9, 11] }, copies: [
  { uid: 'C7-433-AAA', bcx: 6, level: 3, land_base_pp: 818, combinable: true, reasons: [] },
  { uid: 'C7-433-BBB', bcx: 11, level: 5, land_base_pp: 1500, combinable: false, reasons: [{ code: 'max', text: 'card is already at max level' }] }] };
const copies = combineCopiesFromGroup(group);
console.log(JSON.stringify({ ok, bad, copies }));
"""


def test_board_address_opens_the_dialog_by_print_without_card_ids():
    got = _out(ADDRESS_JS, ENTRY)
    assert got["ok"] == {"id": "433-7-0", "card_detail_id": 433, "edition": 7, "foil": 0}
    assert got["bad"] == [None, None, None, None]
    first, second = got["copies"]
    assert first["available"] is True and first["level_bcx_thresholds"] == [1, 3, 6, 9, 11] and first["bcx_max"] == 11
    assert second["available"] is False and second["blocked_reason"] == "card is already at max level"
    dialog = (STATIC / "combine-dialog.js").read_text(encoding="utf-8")
    assert "url.searchParams.delete('combine')" in dialog and "/api/cards/combinables?print=" in dialog


# the Cards page mode

MODE_JS = r"""
const P = (id, over = {}) => ({ id, card_detail_id: Number(id.split('-')[0]), edition: Number(id.split('-')[1]), foil: Number(id.split('-')[2]),
  name: 'Card ' + id, card_set: 'chaos', rarity: 'rare', bcx_max: 22, thresholds: [1, 3, 5, 8, 12, 17, 22, 22].slice(0, 8), ...over });
const set = { uids: ['C7-1-SECRET01', 'C7-1-SECRET02'], survivor_uid: 'C7-1-SECRET01', count: 2, destroyed_count: 1, warnings: [],
  before: { bcx: 1, level: 1, land_base_pp: 91, maxed: false }, after: { bcx: 2, level: 1, land_base_pp: 182, maxed: false } };
const lockSet = { ...set, warnings: [{ code: 'card_lock', text: 'This copy is under a card lock; the combined card will be locked as well.' }] };
const g1 = { print: P('1-7-0'), copies: [
    { uid: 'C7-1-SECRET01', bcx: 1, level: 1, combinable: true, reasons: [], warnings: [] },
    { uid: 'C7-1-SECRET02', bcx: 1, level: 1, combinable: true, reasons: [], warnings: [] },
    { uid: 'C7-1-SECRET03', bcx: 3, level: 2, combinable: false, plot: { label: '065-09-083' }, reasons: [{ code: 'staked', text: 'staked on Land' }], warnings: [] },
    { uid: 'C7-1-SECRET04', bcx: 2, level: 1, combinable: false, reasons: [{ code: 'delegated_in', text: 'delegated to you' }], warnings: [] }],
  owned_count: 4, combinable_count: 2, sets: [lockSet, set], base: { source: 'copy', bcx: 3, level: 2, maxed: false }, upgrade_hint: { possible: true, discount_likely: false } };
const g2 = { print: P('2-7-0'), copies: [], owned_count: 1, combinable_count: 1, sets: [], base: { bcx: 1, level: 1, maxed: false }, upgrade_hint: { possible: true, discount_likely: true } };
const g3 = { print: P('3-7-1'), copies: [], owned_count: 2, combinable_count: 2, sets: [set], base: { bcx: 2, maxed: false }, upgrade_hint: { possible: false, reason: 'dearer' } };
const g4 = { print: P('4-7-0'), copies: [], owned_count: 2, combinable_count: 2, sets: [set], base: { bcx: 22, maxed: true }, upgrade_hint: { possible: false, reason: 'max' } };
const groups = [g1, g2, g3, g4];
const listing = (k, own = false) => ({ lkey: k, card_detail_id: 1, edition: 7, foil: 0, name: 'Card 1-7-0', bcx: 3, level: 2, price_usd: 1, price_dec: 2000, own });
const upgrades = new Map([['1-7-0', { data: { print: g1.print, combine_first: true, cart: { listings: [listing('aaaaaaaaaaaaaaa1'), listing('aaaaaaaaaaaaaaa2'), listing('aaaaaaaaaaaaaaa3', true)], keys: ['aaaaaaaaaaaaaaa1', 'aaaaaaaaaaaaaaa2'], bundle: 'Lv 5' },
  verdict: { kind: 'discount', level: 5, label: 'Buy this Level 3 copy (+1 more) — 7% below single copies', total_usd: 3.057, total_dec: 6202, pp_added: 364, to_bcx: 7,
    saving_usd: 0.743, saving_dec: 1503, stop: { to_level: 7, vs_going: 1.04 }, max_option: { kind: 'level', level: 6, total_usd: 6.4 } } } }]]);
const sortedLevels = cxSorted(groups, 'levels', new Map()).map((g) => g.print.id);
const sortedSaving = cxSorted(groups, 'saving', upgrades).map((g) => g.print.id);
const batch = cxNextBatch(groups, upgrades).map((p) => p.id);
const big = Array.from({ length: 12 }, (_, i) => ({ ...g2, print: P(`${10 + i}-7-0`) }));
const batchSize = cxNextBatch(big, new Map()).length;
const cards = [{ card_detail_id: 1, edition: 7, foil: 0, name: 'Card 1', rarity: 'rare', card_set: 'chaos', status: 'idle', bcx: 1, splinters: ['Fire'] },
  { card_detail_id: 2, edition: 7, foil: 0, name: 'Card 2', rarity: 'common', card_set: 'chaos', status: 'staked', bcx: 1, splinters: ['Water'] }];
const allowed = [...cxAllowedPrints(cards, { ...cbDefaultFilters(), rarity: ['rare'] })];
const allowedStatus = [...cxAllowedPrints(cards, { ...cbDefaultFilters(), status: ['staked'] })];
// the cart: group "up-", own listings never, not counted as "one maxed card"
const cart = { items: [], removed: [] };
const add = cxCartAdd(cart, upgrades.get('1-7-0').data, { now: 1000, rand: () => 0.5 });
const inCart = cxInCart(cart, upgrades.get('1-7-0').data);
const maxGroups = cartMaxGroups(cart, '1-7-0').length;
_cxForTests.deps = { tileHtml: (t) => `<article class="cb-tile">${t.card.name}</article>`, cards: () => [], filters: () => cbDefaultFilters(), player: () => 'player0005' };
_cxForTests.upgrades = upgrades;
const html1 = cxEntryHtml(g1);
const html2 = cxEntryHtml(g2);
const html3 = cxEntryHtml(g3);
console.log(JSON.stringify({ sortedLevels, sortedSaving, batch, batchSize, allowed, allowedStatus, add, inCart, maxGroups, items: cart.items.map((i) => [i.lkey, i.group, i.bundle]),
  states: ['max', 'discount', 'level', 'none', 'no_listings'].map(cxVerdictState), reasons: ['staked', 'delegated_in', 'max', 'listed'].map(cxReasonState), html1, html2, html3 }));
"""


def test_mode_order_batches_filters_and_cart():
    got = _out(MODE_JS, BROWSER)
    assert got["sortedLevels"] == ["2-7-0", "1-7-0", "3-7-1", "4-7-0"]       # a likely discount copy first ()
    assert got["sortedSaving"][0] == "1-7-0"                                  # the read saving first
    assert got["batch"] == ["2-7-0"]                                          # read, dearer and maxed cards are skipped
    assert got["batchSize"] == 5                                              # visible batches of 5
    assert got["allowed"] == ["1-7-0"] and got["allowedStatus"] == ["2-7-0"]  # the Cards filters, copy filters included
    assert got["add"]["added"] == 2 and got["add"]["group"].startswith("up-") and got["inCart"] is True and got["maxGroups"] == 0
    assert [i[0] for i in got["items"]] == ["aaaaaaaaaaaaaaa1", "aaaaaaaaaaaaaaa2"] and got["items"][0][2] == "Lv 5"
    assert got["states"] == ["ok", "ok", "warn", "danger", "meta"]
    assert got["reasons"] == ["warn", "danger", "danger", "warn"]


def test_entry_shows_sets_blockers_lock_and_verdict_but_never_a_card_id():
    got = _out(MODE_JS, BROWSER)
    html1, html2, html3 = got["html1"], got["html2"], got["html3"]
    for html in (html1, html2, html3):
        assert "SECRET" not in html and "C7-" not in html                    # no Card ID on screen (maintainer rule)
    assert "2 copies · CC 1 → 2 / 22" in html1 and "+ 1 more set from the copies left over" in html1
    assert "2 copies cannot combine now" in html1 and 'cx-reason is-warn">staked on Land' in html1 and 'cx-reason is-danger">delegated to you' in html1
    assert "plot=065-09-083" in html1                                         # a staked copy links its plot
    assert 'cx-line is-warn">This copy is under a card lock; the combined card will be locked as well.' in html1
    assert 'cx-verdict is-ok">Buy this Level 3 copy (+1 more) — 7% below single copies' in html1
    assert 'cm-dec-icon' in html1 and html1.index("cm-usd") < html1.index("cm-dec-icon")  # $ then DEC with its icon after
    assert "Saves $0.743 (1,503 DEC) against the going rate" in html1 and "combine the proposed set first" in html1
    assert 'cx-line is-danger">Level 7 would cost 1.04× the going rate' in html1 and "Or Level 6 for $6.40" in html1
    assert 'data-cx-action="combine" data-cx-pid="1-7-0"' in html1 and 'data-cx-action="cart" data-cx-pid="1-7-0"' in html1
    assert "Discount copy likely" in html2 and 'data-cx-action="price"' in html2 and "nothing to combine now" in html2
    assert "Fewer PP/$ than the going rate" in html3 and 'data-cx-action="price"' not in html3


def test_page_wiring_header_and_css():
    page = (STATIC / "cards.html").read_text(encoding="utf-8")
    js = BROWSER.read_text(encoding="utf-8")
    mode = (STATIC / "card-combinables.js").read_text(encoding="utf-8")
    css = (STATIC / "card-browser.css").read_text(encoding="utf-8")
    # Find combinables is the filter row's pill (the Maxed pill's markup), no longer a header mode button
    # the visible label is "Combinables"; the name and the tooltip keep "Find combinables"
    # the visible pill label is "Combine"
    assert '<input type="checkbox" id="cb-mode-combine" aria-label="Find combinables"><span>Combine</span></label>' in page
    assert 'data-control-density="compact" data-tip="Find combinables"><span class="ceres-btn__check"' in page
    assert 'id="cb-mode-combine" class="ceres-btn' not in page
    assert 'id="cx-list"' in page and 'class="cx-only"' in page
    # the cart button is its icon and count, the tooltip "Cart · N items" follows the count
    cart = re.search(r'<button[^>]*id="cm-cart-btn".*?</button>', page, re.S).group(0)
    assert ">Cart<" not in cart and "<span>Cart</span>" not in cart and 'data-tip="Cart · 0 items"' in cart and 'aria-label="Cart · 0 items"' in cart
    assert "const label = `Cart · ${n} ${n === 1 ? 'item' : 'items'}`;" in mode and "button.dataset.tip = label;" in mode
    assert "button.setAttribute('aria-label', label)" in mode
    # the bundle-button rules moved to card-checkout.css (checkout queue); none are left here
    assert "cm-bundle" not in css and "cm-bb-" not in css
    assert "import { initCombinables, renderCombinables } from './card-combinables.js';" in js
    assert "if (document.body?.dataset?.cbMode === 'combine') { renderCombinables(); syncClear(); return; }" in js
    # prices only on a press: no read on entering the mode beyond the snapshot list
    assert "market: '1'" in mode and mode.count("`/api/cards/combinables/upgrade?") == 1 and "setInterval" not in mode
    # the phone header: phone tier only, the board's own scope (P-0); the 1536 layout is untouched
    phone = [line for line in css.splitlines() if "header.board-header" in line]
    assert phone and all(line.startswith("html.ceres-lw-le-1919.ceres-lw-le-1279 .card-browser header.board-header") for line in phone)
    assert '.card-browser[data-cb-mode="combine"] .cb-main > .cb-only' in css
    assert "cyan" not in css and "--info" not in "\n".join(line for line in css.splitlines() if ".cx-" in line)


# the Market tile's level tag

LEVEL_JS = r"""
const card = { name: 'Gargoya Lion', card_detail_id: 349, edition: 3, card_set: 'chaos', rarity: 'common', foil: 0, level: 6, max_level: 8, bcx: 60,
  bcx_max: 115, land_base_pp: 400, max_level_pp: 1000, splinters: ['Neutral'], maxed: false };
const market = tileHtml({ card, extra: { metrics: '' } });
const maxed = tileHtml({ card: { ...card, maxed: true, level: 8, bcx: 115 }, extra: { metrics: '' } });
const mine = tileHtml({ card });
const combinables = tileHtml({ card, extra: { levelTag: false, metrics: '' } });
console.log(JSON.stringify({ market, maxed, mine, combinables }));
"""


def test_market_tile_level_tag():
    got = _out(LEVEL_JS, BROWSER)
    assert 'class="candidate-tag is-info cb-maxed cb-level-tag"' in got["market"] and ">Lv 6</span>" in got["market"]
    assert 'data-tip="Level 6 of 8 · CC 60 / 115"' in got["market"]
    assert "cb-level-tag" not in got["maxed"] and ">Maxed</span>" in got["maxed"]
    assert "cb-level-tag" not in got["mine"] and "cb-level-tag" not in got["combinables"]


# the whole cheap copy row is green

def test_cheap_row_whole_row_green_and_in_cart_stays_apart():
    css = (STATIC / "card-browser.css").read_text(encoding="utf-8")
    assert "\n.card-browser .cm-copy.is-cheap .cm-c-usd, .card-browser .cm-copy.is-cheap .cm-c-dec { color: var(--ok); }" not in css  # 's two-number rule is replaced
    assert ".card-browser .cm-copy-table > .cm-copy.is-cheap { border-color: color-mix(in srgb, var(--ok) 60%, transparent); background:" in css
    assert ".card-browser .cm-copy.is-cheap .cm-cell," in css
    assert '.card-browser .cm-copy-table > .cm-copy.is-cheap[data-state="in-cart"] { border-color: var(--ok);' in css
    assert "box-shadow: inset 3px 0 0 var(--ok); }" in css


# the Market tile's PP/$ box in its band colour (Cost per PP sort)

def test_rate_box_band_colours():
    css = (STATIC / "card-browser.css").read_text(encoding="utf-8")
    for band, token in (("is-top", "--gold"), ("is-good", "--ok"), ("is-fair", "--warn"), ("is-dear", "--danger")):
        box = f".card-browser .cm-tile .candidate-grid-metric.cm-metric-rate.{band} {{ border-color: var({token}); background: color-mix(in srgb, var({token}) 14%,"
        assert box in css, band
        assert f".card-browser .cm-tile .candidate-grid-metric.cm-metric-rate.{band} strong {{ color: var({token}); }}" in css, band
        rule = css[css.index(box):css.index("}", css.index(box))]
        assert "padding" not in rule and "size" not in rule  # same box size


# My cards tiles carry the ability icons below the name

ABILITY_JS = r"""
const card = { name: 'Goblin Chef', card_detail_id: 1, edition: 7, card_set: 'chaos', rarity: 'common', foil: 0, level: 10, max_level: 10, bcx: 400,
  bcx_max: 400, land_base_pp: 1000, max_level_pp: 1000, splinters: ['Fire'], maxed: true, land_abilities: [['GRAIN', 0.01]] };
const mine = tileHtml({ card, plot: { label: '065-09-083', territory: 'Central Fire' }, status: 'staked' });
const market = tileHtml({ card, extra: { metrics: '' } });
console.log(JSON.stringify({ mine, market }));
"""


def test_ability_icons_below_the_name_on_my_cards_only():
    got = _out(ABILITY_JS, BROWSER)
    mine, market = got["mine"], got["market"]
    caption = mine[mine.index('cb-caption">'):]
    assert caption.index('class="slot-name"') < caption.index("cb-abilities-below")         # below the name, in the caption
    foot = mine[mine.index("slot-foot cb-foot"):mine.index('cb-caption">')]
    assert "cb-abilities" not in foot and "Staked on" in foot                                # the staked badge stays over the name
    market_foot = market[market.index("slot-foot cb-foot"):market.index('cb-caption">')]
    assert "cb-abilities" in market_foot and "cb-abilities-below" not in market              # Market keeps them above
    css = (STATIC / "card-browser.css").read_text(encoding="utf-8")
    assert ".card-browser .cb-art .cb-caption .cb-abilities-below { position: absolute; inset-inline: 0; bottom: calc((18.6cqi - min(24px, 16cqi)) / 2);" in css


# the PP box in the theme accent on every tile

def test_pp_box_accent_on_every_tile():
    css = (STATIC / "card-browser.css").read_text(encoding="utf-8")
    box = ".card-browser .candidate-grid-metric.cb-metric-pp { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 14%,"
    num = ".card-browser .candidate-grid-metric.cb-metric-pp strong { color: var(--accent); font-size: 1.25rem; line-height: 1; }"
    assert box in css and num in css
    rule = css[css.index(box):css.index("}", css.index(num)) + 1]
    for state in ("--gold", "--warn", "--ok", "--danger"):
        assert state not in rule
    market = (STATIC / "card-market.js").read_text(encoding="utf-8")
    assert 'class="candidate-grid-metric cb-metric-pp"' in market and 'class="candidate-grid-metric cb-metric-pp"' in BROWSER.read_text(encoding="utf-8")


def test_cooling_badge_amber():
    # text and hourglass in var(--warn), the shared glass kept
    css = (STATIC / "card-browser.css").read_text(encoding="utf-8")
    js = BROWSER.read_text(encoding="utf-8")
    assert ".card-browser .cb-art .slot-wait-badge.cb-wait { color: var(--warn); }" in css
    assert ".card-browser .cb-art .slot-wait-badge.cb-wait .cb-wait-icon { color: transparent; text-shadow: 0 0 0 var(--warn); }" in css
    assert '<span class="cb-wait-icon" aria-hidden="true">⏳</span><span class="slot-wait-label">Cooling</span>' in js


def test_fold_tile_is_not_a_card():
    # no card art, a dashed
    # accent edge on darker glass, a drawn stack with a layers mark and the large count; no PP boxes or one-click button shown
    # their footprint kept (visibility, not display); in-cart keeps its green
    css = (STATIC / "card-browser.css").read_text(encoding="utf-8")
    fold = [line for line in css.splitlines() if "cm-fold-tile" in line and line.startswith(".card-browser")]
    assert fold and all(line.startswith(".card-browser .cb-grid > .cb-tile.cm-fold-tile") for line in fold)
    assert ".card-browser .cb-grid > .cb-tile.cm-fold-tile .cb-art > img { visibility: hidden; }" in css
    assert ".card-browser .cb-grid > .cb-tile.cm-fold-tile .candidate-grid-metrics, .card-browser .cb-grid > .cb-tile.cm-fold-tile .cm-tile-actions { visibility: hidden; }" in css
    assert ".card-browser .cb-grid > .cb-tile.cm-fold-tile:not(.is-in-cart) { border-color: color-mix(in srgb, var(--accent) 70%, transparent);" in css
    assert ".cm-fold-tile .cb-art::before { content: \"\";" in css and ".cm-tag.cm-fold-count::before { content: \"\";" in css
    assert "display: none" not in "\n".join(fold)
