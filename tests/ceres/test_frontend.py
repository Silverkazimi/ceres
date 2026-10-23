""" S7 (v1 clean-up, audit findings L4-01 ... L4-06): the browser side must not mix players, stall on storage, or inject markup."""
from pathlib import Path
import re

from tests.ceres.vm_context import run_in_module_context

STATIC = Path(__file__).parents[2] / "ceres" / "static"


def _src(name):
    return (STATIC / name).read_text(encoding="utf-8")


def test_l4_03_the_idle_card_grouping_is_built_once_per_collection():
    run_in_module_context(r"""
const cards = [{ card_detail_id: 1, edition: 1, foil: 0, uid: 'a' }, { card_detail_id: 1, edition: 1, foil: 0, uid: 'b' }];
const first = idleCombineGroups(cards);
if (idleCombineGroups(cards) !== first) throw Error('the grouping was rebuilt for the same collection');
if (idleCombineGroups([...cards]) === first) throw Error('a new collection must get its own grouping');
if (idleCombineGroups(null).size !== 0) throw Error('no collection -> empty grouping');
S.IDLE_CARDS_CACHE = cards;
const empty = plotCombineCandidates({}, { id: 'worker', slots: [null, null, null] });
if (empty.length !== 0) throw Error('an empty plot offers nothing');
""", entry=STATIC / "combine.js")


def test_l4_01_an_idle_cards_answer_is_applied_only_to_the_player_it_was_asked_for():
    source = _src("combine.js")
    prefetch = source[source.index("export async function prefetchIdleCardsCache"):]
    # Capture the local once, whatever its name, and use that identity through cleanup.
    owner = re.search(r"const (\w+) = BOARD\?\.player \?\? null;", prefetch)
    assert owner
    owner = owner.group(1)
    assert f"IDLE_CARDS_LOADING === {owner}" in prefetch
    assert prefetch.count("!stillMine()") >= 2                                    # checked after both awaits
    assert f"if (IDLE_CARDS_LOADING === {owner}) IDLE_CARDS_LOADING = null;" in prefetch   # a later player's request is never blocked or cleared by this one


def test_l4_02_a_blocked_storage_cannot_stop_the_board_starting():
    source = _src("theme.js")
    assert "try { saved = storageKey ? localStorage.getItem(storageKey) : null; } catch (_) { saved = null; }" in source
    assert "try { localStorage.setItem(storageKey, id); }" in source
    assert "const saved = storageKey ? localStorage.getItem(storageKey) : null;" not in source


def test_l4_04_only_the_newest_combine_preview_may_write():
    source = _src("combine-dialog.js")
    function = source[source.index("async function requestCombinePreview"):source.index("async function verifyCombineBroadcast")]
    assert "const previewRun = ++COMBINE_PREVIEW_RUN;" in function and function.count("previewRun !== COMBINE_PREVIEW_RUN") >= 3


def test_l4_06_the_navigation_notice_escapes_the_plot_label():
    source = _src("stage-navigation.js")
    assert "${esc(label)}" in source and "Showing plot ${label}" not in source
    assert "import { esc, formatPlotLabel } from './format.js';" in source


def test_l4_01_a_hostile_terrain_cannot_inject_markup_into_the_tax_bar():
    run_in_module_context(r"""
const hostile = 'x" onerror="alert(1)';
const row = { deed_uid: 'D-1', worksite_type: 'keep', terrain: hostile };
const html = taxBarHtml(row, '', { noFetch: true });
if (/onerror|alert/.test(html)) throw Error('hostile terrain reached the markup: ' + html);
if (html.includes('_icon.webp')) throw Error('an invalid terrain must not build an image address');
const good = taxBarHtml({ deed_uid: 'D-2', worksite_type: 'keep', terrain: 'Badlands' }, '', { noFetch: true });
if (!good.includes('/tax/keep/badlands_icon.webp')) throw Error('a normal terrain must still show its icon: ' + good);
""", entry=STATIC / "harvest-state.js")


def test_l4_04_a_confirmed_combine_invalidates_the_grouping():
    run_in_module_context(r"""
const cards = [{ card_detail_id: 1, edition: 1, foil: 0, uid: 'a' }, { card_detail_id: 1, edition: 1, foil: 0, uid: 'b' }];
S.IDLE_CARDS_CACHE = cards;
const before = idleCombineGroups(cards);
cards.length = 1;                                    // the confirmed-combine update shrinks the same array in place
GROUPS_BY_COLLECTION.delete(S.IDLE_CARDS_CACHE);
const after = idleCombineGroups(cards);
if (after === before || [...after.values()].flat().length !== 1) throw Error('the grouping kept a destroyed card');
""", entry=STATIC / "combine.js")
    source = _src("combine.js")
    assert "GROUPS_BY_COLLECTION.delete(S.IDLE_CARDS_CACHE);  " in source and source.count("idleCombineGroups(S.IDLE_CARDS_CACHE)") >= 3
    assert "combineGroupsFrom(S.IDLE_CARDS_CACHE)" not in source


def test_l4_06_a_hostile_protected_dec_value_cannot_stop_routing_setup():
    source = _src("routing-setup.js")
    assert "typeof saved.protected_wallet_dec === 'string' || typeof saved.protected_wallet_dec === 'number'" in source
    assert "String(saved.protected_wallet_dec ?? '0')" not in source


def test_l4_07_route_chips_answer_enter_and_space():
    source = _src("harvest-modal.js")
    assert "event.key !== 'Enter' && event.key !== ' '" in source and "chip.click();" in source


def test_r2_l4_06_a_missing_artwork_image_is_hidden_not_shown_broken():
    assert "data-art-missing" in _src("art.js") and "document.addEventListener('error'" in _src("art.js")
    assert "img[data-art-missing] { visibility: hidden; }" in _src("ceres.css")
