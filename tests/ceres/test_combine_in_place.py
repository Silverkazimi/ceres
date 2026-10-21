""": Find combinables'
Combine opens the board's combine dialog in place on /cards -- loaded on the first press (wallet.js first), never at first
paint, the board never drawn -- preselected with the proposed set; a confirmed combine refreshes My cards and the card's
entry. : the mode is the filter row's pill, the header keeps
My cards / Market. The /?combine= board link keeps working.

Plain tests: static reads and the shared vm sandbox (tests/ceres/vm_context.py) on the real modules; no network, no
browser. The browser half is test_combine_in_place_browser.py."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .vm_context import run_in_module_context

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ceres" / "static"
BROWSER = STATIC / "card-browser.js"
MODE = STATIC / "card-combinables.js"
DIALOG = STATIC / "combine-dialog.js"
_IMPORT = re.compile(r"^import \{[^}]*\} from '\./([A-Za-z0-9_-]+)\.js';$", re.M)


def _graph(entry: Path) -> set[str]:
    seen: set[str] = set()
    todo = [entry]
    while todo:
        path = todo.pop()
        if path.name in seen:
            continue
        seen.add(path.name)
        todo.extend(path.parent / f"{dep}.js" for dep in _IMPORT.findall(path.read_text(encoding="utf-8")))
    return seen


def _out(body: str) -> dict:
    out = run_in_module_context(body, entry=BROWSER).stdout
    return json.loads(out.strip().splitlines()[-1])


# first paint: no board graph; the dialog is loaded on the press

def test_first_paint_never_imports_the_board_or_the_dialog():
    page = _graph(BROWSER)
    assert "card-combinables.js" in page
    for module in ("board.js", "app.js", "combine-dialog.js", "combine.js", "wallet.js", "regional-dec.js", "editor.js"):
        assert module not in page, module
    src = MODE.read_text(encoding="utf-8")
    # wallet.js first, then the dialog (the CURRENT_PLAYER evaluation-order trap)
    assert "dialogModule = load('./wallet.js').then(() => load('./combine-dialog.js'));" in src
    assert "export function cxLoadCombineDialog(load = (path) => import(path))" in src
    # no navigation: the Combine press never leaves the page, and the label says so
    assert "location.href =" not in src and "location.assign" not in src
    assert "} else if (action === 'combine') cxOpenCombine(pid);" in src
    assert "Combine on the board" not in src and "the board's combine dialog for this card" not in src
    assert ">Combine</button>" in src


LOADER_JS = r"""
(async () => {
  const calls = [];
  let fail = true;
  const load = (path) => { calls.push(path); return (fail && path === './combine-dialog.js') ? Promise.reject(new Error('offline')) : Promise.resolve({ path }); };
  let first = null;
  try { await cxLoadCombineDialog(load); } catch (e) { first = String(e.message); }
  await new Promise((r) => setTimeout(r, 0));
  fail = false;
  const a = cxLoadCombineDialog(load);
  const b = cxLoadCombineDialog(load);
  const mod = await a;
  console.log(JSON.stringify({ calls, first, same: a === b, mod }));
})();
"""


def test_loader_order_memo_and_retry():
    got = _out(LOADER_JS)
    assert got["first"] == "offline"
    # wallet first each time; a failed load is retried on the next press; one load is shared by later presses
    assert got["calls"] == ["./wallet.js", "./combine-dialog.js", "./wallet.js", "./combine-dialog.js"]
    assert got["same"] is True and got["mod"] == {"path": "./combine-dialog.js"}


GROUP_JS = r"""
globalThis.CSS = { escape: (s) => s };
const P = { id: '1-7-0', card_detail_id: 1, edition: 7, foil: 0, name: 'Goblin Shaman', card_set: 'chaos', rarity: 'rare', bcx_max: 22, thresholds: [1, 3, 5, 8, 12, 17, 22] };
const set = { uids: ['C7-1-SECRET01', 'C7-1-SECRET02'], survivor_uid: 'C7-1-SECRET01', count: 2, destroyed_count: 1, warnings: [],
  before: { bcx: 1, level: 1, land_base_pp: 91 }, after: { bcx: 2, level: 1, land_base_pp: 182 } };
const group = { print: P, owned_count: 3, combinable_count: 2, sets: [set], base: { bcx: 1, level: 1, maxed: false }, upgrade_hint: { possible: false, reason: 'none' },
  copies: [{ uid: 'C7-1-SECRET01', bcx: 1, level: 1, combinable: true, reasons: [], warnings: [] },
           { uid: 'C7-1-SECRET02', bcx: 1, level: 1, combinable: true, reasons: [], warnings: [] },
           { uid: 'C7-1-SECRET03', bcx: 1, level: 1, combinable: false, reasons: [{ code: 'listed', text: 'listed on the market' }], warnings: [] }] };
const reloads = [];
_cxForTests.deps = { tileHtml: () => '<article class="cb-tile"></article>', cards: () => [], filters: () => cbDefaultFilters(), player: () => 'sample01',
  reloadCards: () => reloads.push('cards'), syncClear: () => {} };
_cxForTests.body = { groups: [group], summary: {} };
_cxForTests.upgrades.set('1-7-0', { data: { verdict: { kind: 'none', label: 'x' } } });
"""

OPEN_JS = GROUP_JS + r"""
(async () => {
  const opened = [];
  let release;
  const gate = new Promise((r) => { release = r; });
  const dialog = { openCombineInPlace: (g, host) => { opened.push({ id: g.print.id, preselect: g.sets[0].uids, hooks: Object.keys(host).sort() }); return true; } };
  const pending = cxOpenCombine('1-7-0', { load: () => gate.then(() => dialog) });
  const busy = cxEntryHtml(group);
  const again = cxOpenCombine('1-7-0', { load: () => { opened.push('second load'); return Promise.resolve(dialog); } });  // a second press while loading
  release();
  await pending; await again;
  const idle = cxEntryHtml(group);
  // the dialog closed with the outcome unknown: the card's Combine stays closed for this visit
  const host = cxCombineHost('1-7-0');
  host.onClosed({ outcome: 'none' });
  const afterNone = _cxForTests.held.has('1-7-0');
  host.onClosed({ outcome: 'unknown' });
  const held = cxEntryHtml(group);
  let loadsWhileHeld = 0;
  await cxOpenCombine('1-7-0', { load: () => { loadsWhileHeld += 1; return Promise.resolve(dialog); } });
  // a dialog that cannot open says so on the entry
  _cxForTests.held.clear();
  await cxOpenCombine('1-7-0', { load: () => Promise.reject(new Error('offline')) });
  const failed = cxEntryHtml(group);
  console.log(JSON.stringify({ opened, busy, idle, afterNone, held, loadsWhileHeld, failed }));
})();
"""


def test_combine_opens_in_place_with_the_proposed_set_and_never_twice_while_unknown():
    got = _out(OPEN_JS)
    assert got["opened"] == [{"id": "1-7-0", "preselect": ["C7-1-SECRET01", "C7-1-SECRET02"], "hooks": ["onClosed", "onConfirmed"]}]
    assert 'data-cx-action="combine" data-cx-pid="1-7-0" disabled aria-busy="true"' in got["busy"]
    assert 'data-cx-action="combine" data-cx-pid="1-7-0" data-tip="Combine"' in got["idle"] and ">Combine</button>" in got["idle"]
    assert got["afterNone"] is False
    assert 'data-cx-action="combine"' not in got["held"] and "never signed twice" in got["held"] and got["loadsWhileHeld"] == 0
    assert "The combine dialog could not be loaded" in got["failed"]
    for html in (got["busy"], got["idle"], got["held"], got["failed"]):
        assert "SECRET" not in html and "C7-" not in html  # no Card ID on screen ( / maintainer rule)


CONFIRMED_JS = GROUP_JS + r"""
(async () => {
  const urls = [];
  const fresh = { ...group, owned_count: 2, combinable_count: 0, sets: [], copies: [{ uid: 'C7-1-SECRET01', bcx: 2, level: 1, combinable: false, reasons: [{ code: 'listed', text: 'listed on the market' }], warnings: [] }] };
  globalThis.fetch = async (url) => { urls.push(String(url)); return { ok: true, status: 200, json: async () => ({ groups: [fresh] }) }; };
  const owned = [];
  _cxForTests.held.add('1-7-0');
  const running = afterCombine('1-7-0', { triggered: true }, { refreshOwned: async (player, reload, opts) => { owned.push({ player, start: opts.start }); reload(); } });
  const during = cxEntryHtml(group);
  await running;
  const g = _cxForTests.body.groups[0];
  const after = cxEntryHtml(g);
  // the server could not start its read: this page starts it (the checkout hook's start flag)
  await afterCombine('1-7-0', { triggered: false }, { refreshOwned: async (player, reload, opts) => { owned.push({ player, start: opts.start }); } });
  // a re-read that fails says so, and the entry is not left spinning
  globalThis.fetch = async () => { throw new Error('offline'); };
  await afterCombine('1-7-0', null, { refreshOwned: async () => {} });
  const failed = cxEntryHtml(_cxForTests.body.groups[0]);
  console.log(JSON.stringify({ urls, owned, reloads, during, after, upgrades: _cxForTests.upgrades.has('1-7-0'), held: _cxForTests.held.has('1-7-0'),
    refreshing: _cxForTests.refreshing.size, sets: g.sets.length, failed }));
})();
"""


def test_confirmed_combine_refreshes_my_cards_and_the_entry():
    got = _out(CONFIRMED_JS)
    assert got["owned"] == [{"player": "sample01", "start": False}, {"player": "sample01", "start": True}]
    assert got["reloads"] == ["cards"]                                               # My cards reloads after the settled read
    assert got["urls"][0] == "/api/cards/combinables?print=1-7-0&player=sample01"    # that card's group, the stored snapshot
    assert "Reading your cards after the combine" in got["during"] and 'data-cx-action="combine"' not in got["during"]
    assert got["sets"] == 0 and 'data-cx-action="combine"' not in got["after"]       # the fresh group replaced the old one
    assert got["upgrades"] is False and got["held"] is False and got["refreshing"] == 0
    assert "could not be read again" in got["failed"] and "Reading your cards" not in got["failed"]


# the dialog: in-place mount, hooks, the board path kept

def test_dialog_mount_hooks_and_board_path():
    src = DIALOG.read_text(encoding="utf-8")
    start = src.index("export function openCombineInPlace(group, host = null) {")
    body = src[start:src.index("\n}\n", start)]
    assert "const copies = combineCopiesFromGroup(group);" in body                  # the Find combinables copies, no board, no idle-cards read
    assert "openCombineGroupDialog(key, { preselect: group.sets?.[0]?.uids || null });" in body
    assert "fetch(" not in body and "idleCardsReady" not in body and "BOARD" not in body
    # a confirmed combine calls the page's hook instead of the board refresh (which would load the board)
    assert ("if (COMBINE_HOST?.onConfirmed) COMBINE_HOST.onConfirmed({ combineRefresh: result.combine_refresh });  // : the Cards page\n"
            "      else settleCombineRefresh(result.combine_refresh);") in src
    # closing reports an unresolved combine (wallet open, ambiguous broadcast, sent without the game's verdict)
    assert "COMBINE_SIGN_STEP || COMBINE_ARRIVAL?.provisional || COMBINE_PREVIEW?.broadcastStalled ? 'unknown' : 'none';" in src
    assert "if (wasOpen) COMBINE_HOST?.onClosed?.({ outcome });" in src
    # the host is never cleared (a wallet that answers after a close still lands on the page's hook, never on loadBoard)
    assert src.count("COMBINE_HOST = ") == 2 and "let COMBINE_HOST = null;" in src and "COMBINE_HOST = host || COMBINE_HOST;" in src
    # the signing flow is the dialog's own, unchanged
    assert "if (kind === 'sign') { confirmCombineBroadcast(); return; }" in src and "rebuildCombineForSigning(uids)" in src
    # the /?combine= board link still works
    assert src.rstrip().endswith("openCombineFromAddress();  // : registered when the board's module graph loads (the Cards page never loads it)")
    assert "export async function openCombineFromPrint(print) {" in src and "url.searchParams.delete('combine')" in src


def test_dialog_notice_fallback_matches_the_served_catalog():
    # Off the board (no /api/board read, so no NOTICE_CATALOG) the dialog's texts come from its own copy; it must be the
    # served catalog's text, never "Unknown safety notice: combine_sign" on the sign button.
    from ceres.notices import notice_catalog

    src = DIALOG.read_text(encoding="utf-8")
    block = src[src.index("const COMBINE_NOTICE_FALLBACK = {"):src.index("};", src.index("const COMBINE_NOTICE_FALLBACK = {"))]
    copy = dict(re.findall(r"^  (combine_[a-z_]+): '((?:[^'\\]|\\.)*)',$", block, re.M))
    used = set(re.findall(r"combineNoticeText\('([a-z_]+)'", src))
    assert used and used == set(copy), (used, set(copy))
    catalog = notice_catalog()
    for code, text in copy.items():
        assert catalog[code]["message"] == text, code
    assert "noticeMessage(catalogNotice(" not in src
    assert "if (!notice.message && COMBINE_NOTICE_FALLBACK[code]) notice.message = COMBINE_NOTICE_FALLBACK[code];" in src


def test_dialog_layers_over_the_cards_page():
    css = (STATIC / "card-checkout.css").read_text(encoding="utf-8")
    assert ".card-browser .slot-hazard-overlay.combine-dialog-overlay { z-index: 1200; }" in css
    assert "combine-dialog-overlay" not in (STATIC / "card-browser.css").read_text(encoding="utf-8")


def test_reload_hook_is_passed_to_find_combinables():
    js = BROWSER.read_text(encoding="utf-8")
    assert "reloadCards: () => load(),  // : My cards after a confirmed combine in place" in js


# Find combinables in the filter row

def test_find_combinables_is_the_filter_row_pill():
    page = (STATIC / "cards.html").read_text(encoding="utf-8")
    header = page[page.index('<header class="board-header">'):page.index("</header>")]
    row = page[page.index('<div class="controls-row cb-controls" id="cb-controls">'):page.index('<main id="cb-main"')]
    assert "cb-mode-combine" not in header and "Find combinables" not in header
    # My cards / Market / My purchases (+ the Plots | Cards switcher's two halves, links)
    assert header.count('<button type="button" id="cb-mode-') == 3
    assert header.count('class="ceres-btn ceres-btn--choice" data-control-density="header"') == 5
    # the visible label is "Combine"; the name and the tooltip keep "Find combinables"
    pill = '<label class="picker-toggle board-filter-pill ceres-btn ceres-btn--choice cb-only" data-control-density="compact" data-tip="Find combinables"><span class="ceres-btn__check" aria-hidden="true"></span><input type="checkbox" id="cb-mode-combine" aria-label="Find combinables"><span>Combine</span></label>'
    assert pill in row
    # the end of the row, after Maxed and before Sort
    assert row.index('id="cb-maxed"') < row.index(pill) < row.index('id="cb-sort"')
    src = MODE.read_text(encoding="utf-8")
    assert "$('cb-mode-combine')?.addEventListener('change', (event) => {" in src
    assert "if (event.target.checked) enterCombinables();" in src and "else { setMode('cards'); syncModeButtons('cards'); cx.deps.syncClear?.(); }" in src
    assert "$('cb-mode-cards')?.setAttribute('aria-pressed', String(mode === 'cards' || mode === 'combine'));" in src  # a view of My cards (: not in purchases mode)
    assert "if (pill) pill.checked = mode === 'combine';" in src
    assert "if (new URLSearchParams(location.search).get('mode') === 'combine') enterCombinables();" in src  # the deep link
    contract = json.loads((Path(__file__).parent / "fixtures" / "ceres-button-contract.json").read_text(encoding="utf-8"))
    controls = {c["id"]: c for c in contract["controls"]}
    assert controls["cards-mode-switch"]["selector"] == "#cb-mode-cards, #cb-mode-market"
    mode = controls["cards-combinables-mode"]
    assert (mode["selector"], mode["surface"], mode["group"], mode["treatment"], mode["density"]) == ("#cb-mode-combine", "board-filter-row", "board-filters", "C", "compact")
    maxed = controls["cards-maxed-filter"]
    assert (maxed["surface"], maxed["group"], maxed["treatment"], maxed["density"]) == (mode["surface"], mode["group"], mode["treatment"], mode["density"])
