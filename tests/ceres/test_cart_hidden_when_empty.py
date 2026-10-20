""": the header cart button is hidden while the cart is empty."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSS = (ROOT / "ceres/static/card-browser.css").read_text()
CART = (ROOT / "ceres/static/card-cart.js").read_text()
HTML = (ROOT / "ceres/static/cards.html").read_text()


def test_empty_cart_button_is_not_displayed():
    assert '.card-browser .cm-cart-btn:has(> .cm-cart-count[data-state="empty"]) { display: none; }' in CSS


def test_count_starts_empty_and_sync_sets_empty_only_without_items_or_notices():
    assert 'id="cm-cart-count" data-state="empty"' in HTML
    assert "cart.state.removed.length ? 'sold' : cart.state.items.some((it) => it.status !== 'ok') ? 'changed' : totals.count ? 'ok' : 'empty'" in CART
