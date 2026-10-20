""" wallet switcher geometry and late harvest render guards."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from playwright.sync_api import Browser

from .conftest import spawn_live_ceres


ROOT = Path(__file__).resolve().parents[2]
CSS = (ROOT / "ceres/static/wallet.css").read_text()
WALLET_JS = (ROOT / "ceres/static/wallet.js").read_text()


def rule(selector: str) -> str:
    # Anchor at line start so ".x::after" never matches inside a grouped ".x::before, .x::after" rule.
    match = re.search(rf"(?m)^{re.escape(selector)}\s*\{{([^}}]*)\}}", CSS)
    assert match is not None, selector
    return match.group(1)


def test_drawer_uses_unclipped_tooltip_caret() -> None:
    drawer = rule(".wallet-sign-pair .wallet-drawer")
    assert "overflow: clip" in drawer
    assert "overflow-clip-margin: 8px" in drawer
    assert "overflow: hidden" not in drawer

    pair = rule(".wallet-sign-pair .wallet-drawer::before, .wallet-sign-pair .wallet-drawer::after")
    assert "inset-inline-start: var(--wallet-drawer-anchor-x, 50%)" in pair
    assert "border: 6px solid transparent" in pair
    assert "transform: translateX(-50%)" in pair
    assert "clip-path: polygon" not in pair
    assert "border-bottom-color: var(--wallet-anchor-border)" in rule(".wallet-sign-pair .wallet-drawer::after")
    assert "inset-block-end: calc(100% - 2px)" in rule(".wallet-sign-pair .wallet-drawer::before")
    assert "border-bottom-color: var(--wallet-chooser-overlay)" in rule(".wallet-sign-pair .wallet-drawer::before")
    upward_outer = rule(".wallet-sign-pair .wallet-controls.is-upward .wallet-drawer::after")
    upward_inner = rule(".wallet-sign-pair .wallet-controls.is-upward .wallet-drawer::before")
    assert "border-top-color: var(--wallet-anchor-border)" in upward_outer
    assert "border-top-color: var(--wallet-chooser-overlay)" in upward_inner
    assert "inset-block-start: calc(100% - 2px)" in upward_inner


def test_harvest_controls_are_one_row_and_disconnect_is_icon_only() -> None:
    body = rule("#harvest-modal-overlay .hm-foot .wallet-sign-pair .wallet-drawer-body")
    assert "flex-direction: row" in body
    assert "flex-wrap: nowrap" in body

    connection = WALLET_JS.split("const connection =", 1)[1].split("const providers =", 1)[0]
    providers = WALLET_JS.split("const providers =", 1)[1].split("const request =", 1)[0]
    assert '${connection}${close}</div>' in providers
    assert "ceres-btn--secondary" in WALLET_JS.split("const secondary =", 1)[1].split(";", 1)[0]
    for attribute in (
        'data-control-mode="icon"',
        'data-control-case="destructive"',
        'data-tip="Disconnect HiveAuth"',
        'aria-label="Disconnect HiveAuth"',
    ):
        assert attribute in connection
    assert "walletLogo('hiveauth')" in connection
    assert "<span>Disconnect HiveAuth</span>" not in connection
    assert "title=" not in connection
    assert "background: var(--danger)" in rule(".wallet-disconnect-mark::after")


@pytest.fixture(scope="module")
def live_browser() -> tuple[Browser, str]:
    with spawn_live_ceres() as value:
        yield value


STUB_SESSION = """
(() => {
  localStorage.setItem('ceres:hiveauth-session:v1:player0005', JSON.stringify({
    account: 'player0005', username: 'player0005', token: 'stub-token-not-real',
    key: 'stub-key-not-real', expire: Date.now() + 3600000
  }));
  localStorage.setItem('ceres.wallet-provider:player0005', 'hiveauth');
  const RealWorker = window.Worker;
  window.Worker = function(url, opts) {
    if (!String(url?.href || url).includes('hiveauth-worker')) return new RealWorker(url, opts);
    const fake = {
      onmessage: null, onerror: null,
      postMessage(message) {
        if (message.action !== 'restore') throw new Error('The wallet switcher test must never sign');
        window.__ir8331_finishRestore = () => fake.onmessage?.({
          data: { type: 'restored', account: message.account, expire: Date.now() + 3600000 }
        });
      },
      terminate() {}
    };
    return fake;
  };
})();
"""


SNAPSHOT = """() => {
  const controls = document.querySelector('#harvest-modal-overlay .hm-foot .wallet-sign-pair .wallet-controls');
  const drawer = controls.querySelector('.wallet-drawer.is-open');
  const toggle = controls.querySelector('[data-wallet-toggle]');
  const buttons = ['keychain', 'hiveauth'].map(kind =>
    drawer.querySelector(`[data-wallet-select="${kind}"]`));
  buttons.push(drawer.querySelector('[data-wallet-disconnect]'));
  const rect = drawer.getBoundingClientRect();
  const toggleRect = toggle.getBoundingClientRect();
  const anchor = parseFloat(drawer.style.getPropertyValue('--wallet-drawer-anchor-x'));
  return {
    rect: [rect.x, rect.y, rect.width, rect.height],
    tops: buttons.map(button => button.getBoundingClientRect().top),
    arrowX: rect.left + anchor,
    toggleX: toggleRect.left + toggleRect.width / 2,
    upward: controls.classList.contains('is-upward'),
    detachedPrevious: window.__ir8331_drawerBefore && !window.__ir8331_drawerBefore.isConnected
  };
}"""


@pytest.mark.parametrize("viewport", [(1536, 945), (1920, 1000)])
def test_harvest_wallet_stays_in_final_layout_after_late_render(
    live_browser: tuple[Browser, str], viewport: tuple[int, int]
) -> None:
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})
    try:
        page.emulate_media(reduced_motion="reduce")
        page.add_init_script(STUB_SESSION)
        page.goto(f"{base_url}/?player=sample01", wait_until="load", timeout=90_000)
        page.locator("#harvest-btn").wait_for(state="visible", timeout=60_000)
        page.locator("#harvest-btn").click()
        toggle = page.locator("#harvest-modal-overlay .hm-foot .wallet-sign-pair [data-wallet-toggle]")
        toggle.wait_for(state="visible", timeout=30_000)
        page.wait_for_function("typeof window.__ir8331_finishRestore === 'function'")
        toggle.click()
        before = page.evaluate(SNAPSHOT)
        page.evaluate("window.__ir8331_drawerBefore = document.querySelector('#harvest-modal-overlay .wallet-drawer'); window.__ir8331_finishRestore();")
        after_restore = page.evaluate(SNAPSHOT)
        page.evaluate("window.__ir8331_drawerBefore = document.querySelector('#harvest-modal-overlay .wallet-drawer'); window.dispatchEvent(new CustomEvent('ceres:wallet-changed'))")
        after_event = page.evaluate(SNAPSHOT)

        for state in (before, after_restore, after_event):
            assert max(state["tops"]) - min(state["tops"]) <= 1, state
            assert abs(state["arrowX"] - state["toggleX"]) <= 1, state
            assert abs(state["rect"][2] - 373) <= 1, state
            assert abs(state["rect"][3] - 62) <= 1, state
            assert state["upward"], state
        assert after_restore["detachedPrevious"], after_restore
        assert after_event["detachedPrevious"], after_event
        assert before["rect"] == after_restore["rect"] == after_event["rect"]
    finally:
        page.close()
