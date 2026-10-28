""" design 4 (maintainer go ): the desktop header Menu -- non-browser checks..
So the player field (avatar medallion with its refresh ring, field, go button) must be the very same markup and CSS as
before; only its place in the desktop row changes. The rendered behaviour (open/close, focus, every entry, the boxes of
the wallet chip and Iris pill, the phone header) is checked in a real browser by test_header_menu_browser.py (CI).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from tests.ceres.vm_context import ENTRY, run_in_module_context

ROOT = Path(__file__).parents[2]
STATIC = ROOT / "ceres" / "static"
INDEX = STATIC / "index.html"
CARDS = STATIC / "cards.html"
CSS = STATIC / "ceres.css"
MENU = STATIC / "header-menu.js"

# sha256 of each page's `<span class="player-switch" ...> ... </span>` block at 1.0.1 (HEAD before ), and of every
# ceres.css line that styled the player field before . The maintainer's rule: keep it exactly as it is. Hashed without
# comments and with whitespace runs collapsed (_code_lines): the public export rewrites comments (ticket ids, wording), and
# this check must hold on the private tree and on the export alike; markup and rules are hashed byte for byte otherwise.
PLAYER_SWITCH_SHA = {
    "index.html": "9f73f629607796631698eaaa1ae4f1416fab9f05a0791078570d17a7c13006b5",
    "cards.html": "b9055defc9a666b6c3759710aa40c6d4410a2d667286f6919eb693d0409d83b0",
}
PLAYER_CSS_SHA = "ff399efb288d475f2cf5251ab49fad6e09463bf6298dd0c4c624de7fecca79c1"
BLOCK_MARK = "the desktop header: logo, Menu, the page's name"
DESKTOP = "html:not(.ceres-lw-le-1279)"


def _code_lines(text: str) -> list[str]:
    text = re.sub(r"<!--.*?-->|/\*.*?\*/", "", text, flags=re.S)
    return [" ".join(line.split()) for line in text.splitlines() if line.strip()]


def _selector_parts(selector: str) -> list[str]:
    """Split a selector list at its top-level commas only (not the ones inside :is(...) / :not(...))."""
    parts, depth, start = [], 0, 0
    for index, char in enumerate(selector):
        depth += char == "(" or -(char == ")")
        if char == "," and depth == 0:
            parts.append(selector[start:index])
            start = index + 1
    parts.append(selector[start:])
    return [part.strip() for part in parts if part.strip()]


def _player_switch(html: str) -> str:
    start = html.index('  <span class="player-switch"')
    end = html.index("\n  </span>\n", start) + len("\n  </span>\n")
    return html[start:end]


def _block() -> str:
    css = CSS.read_text(encoding="utf-8")
    start = css.index(BLOCK_MARK)
    return css[css.rfind("/*", 0, start):]


def _rules(css: str):
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        yield " ".join(match.group(1).split()), match.group(2).strip()


def _header_row(html: str) -> str:
    start = html.index('<div class="board-header-row">')
    return html[start:html.index("</header>", start)]


def test_player_field_with_avatar_ring_is_the_same_markup_on_both_pages():
    for page in (INDEX, CARDS):
        block = _player_switch(page.read_text(encoding="utf-8"))
        assert hashlib.sha256("\n".join(_code_lines(block)).encode()).hexdigest() == PLAYER_SWITCH_SHA[page.name], page.name
        assert 'id="player-avatar" class="player-avatar"' in block and 'id="player-input"' in block


def test_player_field_css_is_unchanged_and_design_4_only_moves_it():
    css = CSS.read_text(encoding="utf-8")
    before_block = css[:css.rfind("/*", 0, css.index(BLOCK_MARK))]
    lines = [line for line in _code_lines(before_block) if re.search(r"player-avatar|player-switch|player-input|player-go", line)]
    assert hashlib.sha256("\n".join(lines).encode()).hexdigest() == PLAYER_CSS_SHA
    touching = [(sel, body) for sel, body in _rules(_block()) if re.search(r"player-(avatar|switch|input|go)", sel)]
    assert touching == [(f"{DESKTOP} header.board-header .player-switch", "order: 5;")]


def test_menu_button_and_page_name_markup_on_both_pages():
    for page, place in ((INDEX, '<h1 id="hdr-place" class="hdr-place">Plots</h1>'), (CARDS, '<span id="hdr-place" class="hdr-place">My cards</span>')):
        row = _header_row(page.read_text(encoding="utf-8"))
        menu = re.search(r'<button type="button" id="hdr-menu-btn"[^>]*>', row).group(0)
        for attr in ('class="hdr-menu-btn ceres-btn ceres-btn--secondary"', 'data-control-density="header"', 'aria-haspopup="dialog"',
                     'aria-expanded="false"', 'aria-controls="hdr-menu"'):
            assert attr in menu, (page.name, attr)
        assert "<span>Menu</span></button>" in row and place in row
        # the old controls stay in the DOM for the phone header (hidden on desktop by CSS only)
        assert 'id="page-switch-plots"' in row and 'id="page-switch-cards"' in row
    board_row = _header_row(INDEX.read_text(encoding="utf-8"))
    assert 'id="account-menu-btn"' in board_row and 'id="checkout-btn"' in board_row
    assert re.search(r'id="harvest-btn"[^>]*>', board_row) and '<span class="hdr-label">Harvest</span>' in board_row
    cards_row = _header_row(CARDS.read_text(encoding="utf-8"))
    assert all(f'id="{i}"' in cards_row for i in ("cb-mode-cards", "cb-mode-market", "cb-mode-purchases"))
    harvest = re.search(r'<button type="button" class="harvest-header-btn[^>]*id="cb-harvest-btn"[^>]*>', cards_row).group(0)
    assert 'data-tip="Harvest"' in harvest and 'aria-label="Harvest. Review and harvest your regions."' in harvest
    assert '<span class="hdr-label">Harvest</span>' in cards_row


def test_desktop_rules_never_reach_the_phone_header():
    rules = list(_rules(_block()))
    assert rules, " block missing"
    panel = (".hdr-menu-panel", ".hdr-menu-top", ".hdr-menu-cols", ".hdr-menu-entry", ".hdr-menu-txt", ".hdr-menu-name", ".hdr-menu-desc")
    for selector, body in rules:
        assert "ceres-lw-le-1279)" in selector or "ceres-lw-le-1279" not in selector, selector
        if selector == "header.board-header :is(.hdr-menu-btn, .hdr-place, .hdr-label)":
            assert body == "display: none;"  # outside desktop the new elements do not exist visually
            continue
        if selector.startswith(panel):
            continue  # the panel only opens from the desktop-only Menu button
        assert all(part.startswith(DESKTOP) for part in _selector_parts(selector)), selector
    hides = {sel: body for sel, body in rules}
    assert hides[f"{DESKTOP} header.board-header :is(.ceres-page-switch, .cb-mode, #account-menu-btn)"] == "display: none;"
    assert hides[f'{DESKTOP} header.board-header #checkout-btn.is-icon-only:not([aria-expanded="true"])'] == "display: none;"
    # Find combinables: hidden on desktop by the Cards page's own sheet (ceres.css carries no .card-browser rule, )
    cards_rules = dict(_rules((STATIC / "card-browser.css").read_text(encoding="utf-8")))
    assert cards_rules[f"{DESKTOP} body.card-browser .cb-controls label:has(#cb-mode-combine)"] == "display: none;"
    # the mobile stylesheet is not part of this change and keeps its phone scope (P-0)
    assert "hdr-menu" not in (STATIC / "mobile-plot.css").read_text(encoding="utf-8")


def test_light_and_wood_page_name_chip_is_the_approved_theme_fix():
    rules = dict(_rules(_block()))
    chip = rules[f'{DESKTOP}:is([data-theme="light"], [data-theme="wood"]) header.board-header .hdr-place']
    for decl in ("padding: 0.15rem 0.7rem", "margin-inline-start: 0.35rem", "text-shadow: none",
                 "background: color-mix(in srgb, var(--bg2) 86%, transparent)", "border: 1px solid var(--border)", "border-radius: var(--radius)"):
        assert decl in chip, decl
    dark = rules[f"{DESKTOP} header.board-header .hdr-place"]
    assert "font-family: var(--font-display)" in dark and "font-size: 1.25rem" in dark and "color: var(--heading)" in dark
    assert "text-shadow: 0 1px 2px color-mix(in srgb, var(--shadow) 80%, transparent)" in dark
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(", _block()), " uses Iris tokens only"


def test_menu_lists_three_columns_with_the_approved_texts_and_marks_the_current_place():
    out = run_in_module_context(r"""
const html = hdrMenuPanelHtml('market');
const places = [['board', undefined], ['cards', 'cards'], ['cards', 'combine'], ['cards', 'market'], ['cards', 'purchases']]
  .map(([page, mode]) => hdrMenuPlace(page, mode));
console.log(JSON.stringify({ html, groups: HDR_MENU_GROUPS, places }));
""", entry=MENU).stdout
    got = json.loads(out.strip().splitlines()[-1])
    flat = [(g["title"], e["key"], e["name"], e["desc"]) for g in got["groups"] for e in g["entries"]]
    assert flat == [
        ("Places", "plots", "Plots", "Your land, plot by plot"),
        ("Places", "myCards", "My cards", "Your card collection"),
        ("Places", "market", "Market", "Find and buy cards"),
        ("Places", "purchases", "My purchases", "Cards you bought"),
        ("Tools", "harvest", "Harvest", "Review and collect resources"),
        ("Tools", "changes", "Changes", "Staged changes to sign"),
        ("Tools", "combine", "Find combinables", "Cards you can combine"),
        ("Player", "refresh", "Refresh", "Reload this player’s data"),
        ("Player", "account", "Account", "Sign in and your keys"),
    ]
    html = got["html"]
    assert html.count('data-menu-entry="') == 9 and html.count('aria-current="page"') == 1
    assert 'data-menu-entry="market" aria-current="page"' in html
    assert html.count('class="hdr-menu-col"') == 3 and '<h2 id="hdr-menu-title">Menu</h2>' in html
    assert 'aria-label="Close menu"' in html and "data-menu-close" in html
    assert html.count('class="hdr-menu-entry ceres-btn ceres-btn--secondary ceres-btn--glass" data-control-density="comfortable"') == 9
    assert [p["name"] for p in got["places"]] == ["Plots", "My cards", "My cards", "Market", "My purchases"]
    assert [p["key"] for p in got["places"]] == ["plots", "myCards", "myCards", "market", "purchases"]
    assert not re.search(r"[A-Z]\d-\d+-[A-Z0-9]{6,}|deed_uid|data-uid", html)  # no UIDs on screen


def test_menu_dialog_contract_in_source():
    src = MENU.read_text(encoding="utf-8")
    assert "panel.setAttribute('role', 'dialog')" in src and "panel.setAttribute('aria-labelledby', 'hdr-menu-title')" in src
    assert "event.key === 'Escape' && open" in src and "setOpen(false, { focusButton: true })" in src
    assert "if (open && !panel.contains(event.target) && !button.contains(event.target)) setOpen(false);" in src
    assert "button.setAttribute('aria-expanded', String(open));" in src
    assert "if (typeof action === 'function') setTimeout(() => action(), 0);" in src


def test_board_entries_press_todays_controls():
    out = run_in_module_context(r"""
const clicks = [];
const fake = (id) => ({ click() { clicks.push(id); } });
document.getElementById = (id) => ['harvest-btn', 'checkout-btn', 'account-menu-btn'].includes(id) ? fake(id) : null;
CURRENT_PLAYER = 'alice';
BOARD_MENU_ACTIONS.harvest(); BOARD_MENU_ACTIONS.changes(); BOARD_MENU_ACTIONS.account();
const urls = ['myCards', 'market', 'purchases', 'combine'].map((key) => { BOARD_MENU_ACTIONS[key](); return location.href; });
BOARD_MENU_ACTIONS.plots();
console.log(JSON.stringify({ clicks, urls, keys: Object.keys(BOARD_MENU_ACTIONS).sort() }));
""", entry=ENTRY).stdout
    got = json.loads(out.strip().splitlines()[-1])
    assert got["clicks"] == ["harvest-btn", "checkout-btn", "account-menu-btn"]
    assert got["urls"] == [f"/cards?player=alice&mode={m}" for m in ("cards", "market", "purchases", "combine")]
    assert got["keys"] == sorted(["plots", "myCards", "market", "purchases", "harvest", "changes", "combine", "refresh", "account"])
    app = ENTRY.read_text(encoding="utf-8")
    # Refresh is the avatar's own refresh: the same function the avatar's click runs
    assert "refresh: () => refreshBoardFromAvatar()," in app and "avatar?.addEventListener('click', () => refreshBoardFromAvatar());" in app
    assert "ready(() => mountHeaderMenu({ page: 'board', actions: BOARD_MENU_ACTIONS }));" in app
    assert "initDone.then(() => runBoardOpenRequest());" in app


def test_cards_entries_press_todays_controls():
    src = (STATIC / "card-browser.js").read_text(encoding="utf-8")
    start = src.index("mountHeaderMenu({ page: 'cards', actions: {")
    block = src[start:src.index("} });", start)]
    for key, target in (("myCards", "viewButton('cb-mode-cards')"), ("market", "viewButton('cb-mode-market')"),
                        ("purchases", "viewButton('cb-mode-purchases')"), ("refresh", "viewButton('player-avatar')"),
                        ("harvest", "toBoard('harvest')"), ("changes", "toBoard('changes')"), ("account", "toBoard('account')")):
        assert re.search(rf"{key}: (\(\) => )?{re.escape(target)}", block), key
    assert "const pill = $('cb-mode-combine');" in block and "pill.click();" in block
    assert "$('cb-harvest-btn')?.addEventListener('click', () => toBoard('harvest'));" in src
    # Find combinables from the board's Menu opens My cards, not the Market (cards.html start script and card-market.js)
    assert "if(m==='combine')m='cards';" in CARDS.read_text(encoding="utf-8")
    assert "if (fromUrl === 'combine') return 'cards';" in (STATIC / "card-market.js").read_text(encoding="utf-8")


def test_account_panel_hangs_from_menu_when_its_button_is_hidden():
    src = (STATIC / "account-menu.js").read_text(encoding="utf-8")
    assert "return (button?.getClientRects?.().length ? button : document.getElementById('hdr-menu-btn')) || button;" in src
    assert "const box = anchor().getBoundingClientRect();" in src and "anchor()?.focus?.({ preventScroll: true });" in src
