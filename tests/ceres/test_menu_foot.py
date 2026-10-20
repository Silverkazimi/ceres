""": the card menu's footer -- "Open cart" and the
"N listings in cart" line -- is absent while the cart is empty, appears with the first item and follows every change live."""
from __future__ import annotations

import json
import re
from pathlib import Path

from tests.ceres.vm_context import run_in_module_context

STATIC = Path(__file__).parents[2] / "ceres" / "static"
CART = STATIC / "card-cart.js"


def test_menu_footer_is_empty_without_items_and_returns_with_one():
    out = run_in_module_context(r"""
const L = { lkey: 'aaaaaaaaaaaaaaa2', name: 'Gargoya Lion', card_detail_id: 349, edition: 3, foil: 0, level: 1, bcx: 60, price_usd: 0.3, price_dec: 600 };
const empty = cartFootHtml(true);
cartAdd(cartState(), [L]);
const one = cartFootHtml(true);
cartRemove(cartState(), L.lkey);
console.log(JSON.stringify({ empty, one, again: cartFootHtml(true) }));
""", entry=CART, extra_context_js="").stdout
    got = json.loads(out.strip().splitlines()[-1])
    assert got["empty"] == "" and got["again"] == ""
    assert 'data-cm-action="open-cart">Open cart</button>' in got["one"] and "1 listing in cart" in got["one"]


def test_menu_footer_hides_and_syncs_live():
    js = CART.read_text(encoding="utf-8")
    render = js[js.index("function renderMenu()"):js.index("export async function cartOpenMenu")]
    assert "foot.hidden = !foot.innerHTML" in render
    save = js[js.index("function save()"):js.index("function syncCount()")]
    assert "syncMenuFoot();" in save and "foot.hidden = !foot.innerHTML" in save
    # the footer is display:flex in card-browser.css, so [hidden] needs its own rule (card-checkout.css, linked from cards.html)
    css = (STATIC / "card-checkout.css").read_text(encoding="utf-8")
    assert re.search(r"\.cm-dialog-foot\[hidden\]\s*\{\s*display:\s*none;", css)
    assert re.search(r'<link rel="stylesheet" href="/static/card-checkout.css', (STATIC / "cards.html").read_text(encoding="utf-8"))
