""": the board's wallet on the Cards page (/cards), above the Cards dialogs; : no empty frame between an
outcome's waiting and confirmed box, which also keeps its place; : the forced harvest after a DEC stake is laid out
as the Harvest all receipt. No network: every wallet read is a stub in the page. Nothing is signed or broadcast."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from .conftest import settle, spawn_live_ceres

STATIC = Path(__file__).parents[2] / "ceres" / "static"
CARDS = STATIC / "cards.html"
_IMPORT = re.compile(r"^import [^;]*? from '\./([A-Za-z0-9_-]+)\.js';", re.M | re.S)


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


# ── source contracts ─────────────────────────────────────────────────────────────────────────────────────────────────

def test_cards_page_carries_the_board_wallet_controls():
    html = CARDS.read_text(encoding="utf-8")
    header = html[html.index('<header class="board-header">'):html.index("</header>")]
    button = re.search(r'<button[^>]*id="regional-dec-button"[^>]*>', header)
    assert button, "the phone wallet button sits in the header"
    for attr in ('class="ceres-btn ceres-btn--primary cb-wallet-btn"', 'data-control-density="header"', 'data-control-mode="icon"',
                 'aria-controls="regional-dec-drawer"', 'aria-label="Wallet"'):
        assert attr in button.group(0), attr
    # the board's own edge slide-out markup, ids unchanged (regional-dec.js initRegionalDec() looks them up)
    board = (STATIC / "index.html").read_text(encoding="utf-8")
    picker = re.search(r'<div id="wallet-picker".*?</button></div>', board, re.S).group(0)
    assert picker in html
    assert '<div id="edge-tabs" class="edge-tabs">' in html
    # no new inline script or handler on the page (CSP: test_v1_c4_security_headers hashes what exists)
    assert len(re.findall(r"<script(?![^>]*\bsrc=)", html)) == 2
    assert not re.search(r"\son[a-z]+=", html)


def test_cards_page_static_graph_stays_board_free_and_loads_the_wallet_on_demand():
    page = _graph(STATIC / "card-browser.js")
    assert "cards-wallet.js" in page
    for board_module in ("board.js", "regional-dec.js", "wallet.js", "format.js", "icons.js", "app.js"):
        assert board_module not in page, board_module
    source = (STATIC / "cards-wallet.js").read_text(encoding="utf-8")
    assert not _IMPORT.findall(source), "cards-wallet.js imports nothing"
    assert "import('./regional-dec.js')" in source and "module.initRegionalDec();" in source
    assert "initCardsWallet();" in (STATIC / "card-browser.js").read_text(encoding="utf-8")


def test_toggle_icons_match_the_board_icons():
    icons = (STATIC / "icons.js").read_text(encoding="utf-8")
    board = dict(re.findall(r"^\s+(\w+): '([^']+)',$", icons[icons.index("const RESOURCE_ICON = {"):icons.index("};", icons.index("const RESOURCE_ICON = {"))], re.M))
    source = (STATIC / "cards-wallet.js").read_text(encoding="utf-8")
    block = source[source.index("CARDS_WALLET_ICON = {"):source.index("};", source.index("CARDS_WALLET_ICON = {"))]
    mine = dict(re.findall(r"(\w+): '([^']+)'", block))
    tabs = re.search(r"WALLET_TABS = \[([^\]]+)\]", (STATIC / "wallet-resources.js").read_text(encoding="utf-8")).group(1)
    assert set(mine) == set(re.findall(r"'(\w+)'", tabs))
    assert {k.lower(): v for k, v in mine.items()} == board


def test_wallet_stack_sits_above_the_cards_dialogs():
    css = (STATIC / "card-browser.css").read_text(encoding="utf-8")
    dialog = int(re.search(r"\.cm-dialog-overlay \{ z-index: (\d+);", css).group(1))
    assert int(re.search(r"\.card-browser \.edge-tabs \{ z-index: (\d+); \}", css).group(1)) > dialog
    assert int(re.search(r"body\.card-browser \.dec-drawer \{ z-index: (\d+); \}", css).group(1)) > dialog
    for name in ("card-checkout.js", "card-cart.js"):
        assert "if (!document.getElementById('regional-dec-drawer')?.open)" in (STATIC / name).read_text(encoding="utf-8"), name


def test_checkout_rereads_its_quote_when_the_wallet_closes():
    dec = (STATIC / "regional-dec.js").read_text(encoding="utf-8")
    assert "window.dispatchEvent(new CustomEvent('ceres:wallet-closed'" in dec
    co = (STATIC / "card-checkout.js").read_text(encoding="utf-8")
    assert "window.addEventListener('ceres:wallet-closed', () => { requoteAfterWallet(); });" in co
    fn = co[co.index("export async function requoteAfterWallet()"):co.index("async function testBuy()")]
    assert "co.stage = 'loading'" not in fn, "the re-read never shows the loading view"
    assert "['review', 'confirm', 'error'].includes(co.stage)" in fn


def test_outcome_refresh_is_quiet():
    dec = (STATIC / "regional-dec.js").read_text(encoding="utf-8")
    assert "reload: () => loadWallet({ quiet: true })," in dec
    assert "if (!(quiet && D.wallet)) { D.walletBusy = true; render(); }" in dec


# ── browser ──────────────────────────────────────────────────────────────────────────────────────────────────────────

WALLET = {
    "player": "sample01", "issues": [], "account": {"DEC": 100, "SPS": 0, "SPSP": 0, "VOUCHER": 0},
    "regions": [{"region_uid": "PR-65", "name": "Region 65", "plots_owned": 3,
                 "balances": {"GRAIN": 5000, "WOOD": 0, "STONE": 0, "IRON": 0, "RESEARCH": 0, "AURA": 0}}],
    "swap": {"symbols": ["GRAIN", "WOOD", "STONE", "IRON"], "account_symbols": [], "directions": ["swap_sell", "swap_buy"],
             "slippage": "2.5", "signing_enabled": True, "rates": {}, "fees": {}},
    "staked_dec": None, "soulbound": ["AURA", "RESEARCH"], "resources": ["GRAIN", "WOOD", "STONE", "IRON", "RESEARCH", "AURA"],
}

STUBS = """
(() => {
 const WALLET = __WALLET__;
 let walletReads = 0;
 window.__walletReads = () => walletReads;
 const json = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
 const realFetch = window.fetch.bind(window);
 window.fetch = async (input, init) => {
 const url = String(input?.url || input);
 if (url.includes('/api/wallet/rewards')) return json({ player: 'sample01', unclaimed_sps: null, reward_rows: [] });
 if (url.includes('/api/wallet/lp/verify')) { await new Promise(r => setTimeout(r, 1200)); return json({ status: 'confirmed', game_result: { dec_amount: '5' } }); }
 if (url.includes('/api/wallet/lp/')) return json({ status: 'not_found' });
 if (url.includes('/api/wallet?')) {
 walletReads += 1;
 // the re-read after the outcome is slow, so an emptied box would show for many frames
 await new Promise(r => setTimeout(r, walletReads > 1 ? 900 : 50));
 return json({ ...WALLET, account: { ...WALLET.account, DEC: walletReads > 1 ? 105 : 100 } });
 }
 if (url.includes('/api/dec')) return json({ player: 'sample01', balance: 100, regions: [] });
 return realFetch(input, init);
 };
})();
""".replace("__WALLET__", json.dumps(WALLET))


@pytest.fixture(scope="module")
def live_browser():
    with spawn_live_ceres() as value:
        yield value


def _cards(browser, base_url, viewport, *, intent=None):
    context = browser.new_context(viewport=viewport)
    context.add_init_script(STUBS)
    if intent:
        context.add_init_script(intent)
    page = context.new_page()
    requests: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    page.goto(f"{base_url}/cards?player=sample01", wait_until="load", timeout=90_000)
    page.wait_for_function("document.body.dataset.viewReady === 'true' || !!document.querySelector('.cb-message:not([hidden])')", timeout=60_000)
    return context, page, requests


def test_desktop_edge_slideout_opens_the_wallet_above_a_dialog(live_browser):
    browser, base_url = live_browser
    context, page, requests = _cards(browser, base_url, {"width": 1536, "height": 864})
    try:
        assert page.locator("#wallet-edge-tab").is_visible()
        assert not page.locator("#regional-dec-button").is_visible(), "desktop opens from the edge, as the board"
        assert not any("/static/board.js" in url or "/static/regional-dec.js" in url for url in requests), "no board module before a press"
        # an open Cards dialog (the cart's overlay) under the wallet
        page.evaluate("""() => { const o = document.createElement('div'); o.id = 'test-overlay'; o.className = 'slot-hazard-overlay cm-dialog-overlay';
            o.innerHTML = '<div class="slot-hazard-panel cm-dialog-panel"><header><h3>Cart</h3></header></div>'; document.body.append(o); }""")
        box = page.locator("#wallet-edge-tab").bounding_box()
        hit = page.evaluate("([x, y]) => document.elementFromPoint(x, y)?.closest('#wallet-edge-tab') !== null", [box["x"] + box["width"] / 2, box["y"] + box["height"] / 2])
        assert hit, "the wallet handle stays reachable over the dialog"
        page.locator("#wallet-edge-tab").click()
        page.wait_for_function("document.getElementById('regional-dec-drawer')?.open && document.getElementById('wallet-picker').dataset.open === 'true'", timeout=30_000)
        page.wait_for_function("!!document.querySelector('#regional-dec-drawer .dec-drawer-footer')", timeout=30_000)
        assert page.locator("#regional-dec-drawer .dec-drawer-footer span").inner_text() == "sample01"
        settle(page)  # the slide has finished
        geometry = page.evaluate("""() => {
            const panel = document.querySelector('#regional-dec-drawer .dec-drawer-panel').getBoundingClientRect();
            const picker = document.getElementById('wallet-picker').getBoundingClientRect();
            const x = panel.left + panel.width / 2, y = panel.top + Math.min(40, panel.height / 2);
            return { panelLeft: panel.left, panelRight: panel.right, pickerLeft: picker.left, pickerRight: picker.right,
                     onTop: !!document.elementFromPoint(x, y)?.closest('#regional-dec-drawer'),
                     dialogOpen: !document.getElementById('test-overlay').hidden };
        }""")
        assert geometry["onTop"] and geometry["dialogOpen"], geometry
        # the open slide-out spans the panel
        assert abs(geometry["pickerLeft"] - geometry["panelLeft"]) <= 1 and abs(geometry["pickerRight"] - geometry["panelRight"]) <= 1, geometry
        assert any("/static/regional-dec.js" in url for url in requests)
        # a click beside the wallet closes the wallet only; the dialog under it stays
        page.mouse.click(200, 600)
        page.wait_for_function("!document.getElementById('regional-dec-drawer').open", timeout=10_000)
        assert page.evaluate("document.getElementById('test-overlay') && !document.getElementById('test-overlay').hidden")
    finally:
        context.close()


def test_phone_header_button_opens_the_full_height_drawer(live_browser):
    browser, base_url = live_browser
    context, page, _ = _cards(browser, base_url, {"width": 390, "height": 844})
    try:
        button = page.locator("#regional-dec-button")
        assert not page.locator("#wallet-edge-tab").is_visible()
        box = button.bounding_box()
        assert box["width"] <= box["height"] + 1, "icon-only on the phone"
        # the Cards header fits 390 px; the wallet button sits inside it, fully on screen and pressable.
        assert box["x"] >= 0 and box["x"] + box["width"] <= 390, box
        assert page.evaluate("document.documentElement.scrollWidth") <= 390, "no horizontal page scroll"
        button.click()
        page.wait_for_function("document.getElementById('regional-dec-drawer')?.open && document.getElementById('regional-dec-drawer').matches(':modal')", timeout=30_000)
        page.wait_for_function("!!document.querySelector('#regional-dec-drawer .dec-drawer-footer')", timeout=30_000)
        assert page.locator("#regional-dec-drawer .dec-drawer-footer span").inner_text() == "sample01"
    finally:
        context.close()


INTENT = """
localStorage.setItem('ceres:wallet:resource', 'GRAIN');
localStorage.setItem('ceres:wallet-lp-intent:v1:sample01:GRAIN', JSON.stringify({ stage: 'confirming', token: 't-ir9261', savedAt: Date.now(),
 receipt: { kind: 'swap_sell', symbol: 'GRAIN', pay: 'GRAIN', receive: 'DEC', input: 100, output: 5, minimum: 4.9, region: 'PR-65',
 tx_id: 'e'.repeat(40), nonce: 'ab'.repeat(8), payload: {}, signed_at: new Date().toISOString() } }));
"""

SAMPLER = """() => new Promise((resolve) => {
  const frames = [];
  const t0 = performance.now();
  const tick = () => {
    const box = document.querySelector('#regional-dec-drawer .dec-receipt');
    frames.push({ t: Math.round(performance.now() - t0), box: !!box, top: box ? box.getBoundingClientRect().top : null,
                  state: box ? (box.classList.contains('is-confirmed') ? 'confirmed' : box.classList.contains('is-waiting') ? 'waiting' : 'other') : null,
                  reads: window.__walletReads(), scroll: document.querySelector('#regional-dec-drawer .dec-drawer-content')?.scrollTop ?? null,
                  height: box ? Math.round(box.getBoundingClientRect().height) : null });
    if (performance.now() - t0 < 4000) requestAnimationFrame(tick); else resolve(frames);
  };
  requestAnimationFrame(tick);
})"""


def test_waiting_box_turns_confirmed_in_place(live_browser):
    browser, base_url = live_browser
    context, page, _ = _cards(browser, base_url, {"width": 1536, "height": 864}, intent=INTENT)
    try:
        page.locator("#wallet-edge-tab").click()
        page.wait_for_function("!!document.querySelector('#regional-dec-drawer .dec-receipt.is-waiting')", timeout=30_000)
        frames = page.evaluate(SAMPLER)
        seen = [f for f in frames if f["box"]]
        first = next(i for i, f in enumerate(frames) if f["box"])
        assert all(f["box"] for f in frames[first:]), [f for f in frames[first:] if not f["box"]][:5]
        assert {f["state"] for f in seen} >= {"waiting", "confirmed"}
        assert max(f["reads"] for f in frames) >= 2, "the outcome's balance re-read happened inside the window"
        tops = {round(f["top"], 1) for f in seen}
        # the receipt keeps its top from waiting to confirmed, also when the confirmed
        # receipt is shorter (the drawer holds its scroll range below the content while the box keeps its place).
        assert max(tops) - min(tops) <= 0.5, [(f["t"], f["state"], f["top"], f["scroll"], f["height"]) for f in seen][::6]
    finally:
        context.close()


# ── : the forced harvest after a DEC stake is the usual Harvest all receipt ─────────────────────────────────────

HARVEST = {
    "state": "recorded", "pre_op_efficiency": 0.62, "post_op_efficiency": 1.0,
    "members": [{
        "outcome": "success", "region_uid": "PR-CEF-41",
        "received_by_token": {"GRAIN": 18250.5, "WOOD": 4210.25, "STONE": 1220.0, "IRON": 310.75, "RESEARCH": 12.5, "AURA": 3.25},
        "costs_by_token": {"GRAIN": 2740.0, "WOOD": 630.5, "STONE": 180.0, "IRON": 45.0},
        "evidence": ["regional_rewards"], "reward_eligibility": {"fragment": "eligible", "labors_luck": "eligible"}, "finds": [],
    }],
}
FORCED_HARVEST = "localStorage.setItem('ceres:regional-dec:pending:sample01', JSON.stringify(%s));" % json.dumps({
    "region_uid": "PR-CEF-41", "action": "stake", "amount": 2500, "nonce": "ab" * 8, "status": "confirmed",
    "tx_id": "f" * 40, "signed_at": 1, "message": "", "harvest": HARVEST})


def test_forced_harvest_uses_the_harvest_all_ledger_markup():
    source = (STATIC / "regional-dec.js").read_text(encoding="utf-8")
    assert "`<div class=\"harvest-receipt-ledger\">${harvestSuccessHtml({ members: harvest.members }, { costsOpen: D.costsOpen })}</div>`" in source
    css = (STATIC / "ceres.css").read_text(encoding="utf-8")
    assert "#regional-dec-drawer .dec-harvest-receipt .harvest-receipt-ledger .hm-batch-slots { display: flex; flex-wrap: wrap;" in css


@pytest.mark.parametrize("viewport", [{"width": 1536, "height": 864}, {"width": 390, "height": 844}], ids=["desktop", "phone"])
def test_forced_harvest_receipt_is_laid_out_in_the_wallet(live_browser, viewport):
    browser, base_url = live_browser
    context, page, _ = _cards(browser, base_url, viewport, intent=FORCED_HARVEST)
    try:
        page.locator("#regional-dec-button" if viewport["width"] < 1280 else "#wallet-edge-tab").click()
        page.wait_for_selector("#regional-dec-drawer .dec-harvest-receipt .harvest-receipt-ledger .hm-batch-total", timeout=30_000)
        settle(page)
        layout = page.evaluate("""() => {
            const receipt = document.querySelector('#regional-dec-drawer .dec-harvest-receipt');
            const content = receipt.closest('.dec-drawer-content');
            const badges = [...receipt.querySelectorAll('.hm-batch-total .hm-batch-badge:not(.is-empty)')].map(b => {
                const r = b.getBoundingClientRect();
                const parts = [...b.children].map(c => c.getBoundingClientRect());
                return { l: r.left, r: r.right, t: r.top, b: r.bottom, text: b.innerText.replace(/\\s+/g, ' ').trim(),
                         inside: parts.every(p => p.left >= r.left - 0.5 && p.right <= r.right + 0.5) };
            });
            return { badges, wide: content.scrollWidth > content.clientWidth, styled: getComputedStyle(receipt.querySelector('.hm-batch-total')).borderTopStyle };
        }""")
        assert layout["styled"] == "solid", "the Total box carries the Harvest all receipt's styling"
        assert not layout["wide"], "no sideways overflow in the wallet"
        badges = layout["badges"]
        assert [b["text"].split(" ")[0] for b in badges] == ["Grain", "Wood", "Stone", "Iron", "Aura", "Research"], badges
        assert all(b["inside"] for b in badges), badges
        for i, a in enumerate(badges):
            for b in badges[i + 1:]:
                assert a["r"] <= b["l"] + 0.5 or b["r"] <= a["l"] + 0.5 or a["b"] <= b["t"] + 0.5 or b["b"] <= a["t"] + 0.5, (a, b)
    finally:
        context.close()


@pytest.mark.parametrize("viewport", [{"width": 1536, "height": 864}, {"width": 390, "height": 844}], ids=["desktop", "phone"])
def test_show_costs_in_the_wallet_receipt_reveals_the_breakdown(live_browser, viewport):
    browser, base_url = live_browser
    context, page, _ = _cards(browser, base_url, viewport, intent=FORCED_HARVEST)
    try:
        page.locator("#regional-dec-button" if viewport["width"] < 1280 else "#wallet-edge-tab").click()
        page.wait_for_selector("#regional-dec-drawer .dec-harvest-receipt .hm-batch-costs", timeout=30_000)
        settle(page)
        probe = """() => ({ top: Math.round(document.querySelector('#regional-dec-drawer .dec-receipt').getBoundingClientRect().top),
            lines: document.querySelectorAll('#regional-dec-drawer .dec-harvest-receipt .hm-batch-total .hm-batch-ledger-line').length,
            label: document.querySelector('#regional-dec-drawer .dec-harvest-receipt .hm-batch-costs').textContent.trim(),
            text: document.querySelector('#regional-dec-drawer .dec-harvest-receipt').innerText })"""
        before = page.evaluate(probe)
        assert before["label"] == "Show costs" and before["lines"] == 1
        page.locator("#regional-dec-drawer .dec-harvest-receipt .hm-batch-costs").click()
        settle(page)
        opened = page.evaluate(probe)
        assert opened["label"] == "Hide costs" and opened["lines"] == 3, opened
        assert opened["top"] == before["top"], "expanding keeps the receipt where it was ()"
        assert not re.search(r"\b[A-Z]{2,}-[A-Z]{3}-\d+|\bC\d-", opened["text"]), "no UIDs on screen"
        page.locator("#regional-dec-drawer .dec-harvest-receipt .hm-batch-costs").click()
        settle(page)
        closed = page.evaluate(probe)
        assert closed["label"] == "Show costs" and closed["lines"] == 1 and closed["top"] == before["top"], closed
    finally:
        context.close()
