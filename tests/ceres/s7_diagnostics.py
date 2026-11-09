""" S7: failure diagnostics only (no assertion is changed or weakened).

`@print_diagnostics_on_failure` wraps a browser test that takes `live_browser`. Every page the test opens
is watched (console, page errors, network). If the test fails, the evidence is printed (shown by pytest
in the CI log) before the original error is re-raised: the `.op-state` texts, whether a wallet stub
(Keychain) was called, the console log and the last network calls.
"""
from __future__ import annotations

import functools
import time

NETWORK_TAIL = 25

_STATE_JS = """() => {
  const text = (el) => (el ? (el.innerText || el.textContent || '').trim() : null);
  const g = (name) => { try { return window[name]; } catch (e) { return 'unreadable'; } };
  return {
    url: location.href,
    opState: [...document.querySelectorAll('.op-state')].map(text),
    checkoutPanel: text(document.querySelector('#checkout-panel')),
    banner: text(document.querySelector('#ceres-confirmation-banner')),
    notices: [...document.querySelectorAll('.ceres-notice')].map((el) => ({code: el.dataset.noticeCode, text: text(el)})),
    keychainStub: {
      hkRefusalBroadcastCount: g('__hkRefusalBroadcastCount'),
      ceresBroadcastCount: g('__ceresBroadcastCount'),
      ceresBroadcastEvents: g('__ceresBroadcastEvents'),
      runiKeychain: g('__runiKeychain'),
      hive_keychain_defined: typeof window.hive_keychain !== 'undefined',
    },
  };
}"""


def _watch(page, store: dict) -> None:
    started = time.monotonic()

    def stamp() -> str:
        return f"+{time.monotonic() - started:7.2f}s"

    page.on("console", lambda m: store["console"].append(f"{stamp()} [{m.type}] {m.text}"))
    page.on("pageerror", lambda e: store["console"].append(f"{stamp()} [pageerror] {e}"))
    page.on("request", lambda r: store["network"].append(f"{stamp()} > {r.method} {r.url}"))
    page.on("response", lambda r: store["network"].append(f"{stamp()} < {r.status} {r.url}"))
    page.on("requestfailed", lambda r: store["network"].append(f"{stamp()} x FAILED {r.method} {r.url} {r.failure}"))


def _print_report(label: str, stores: list) -> None:
    print(f"\n===== checkout/refusal diagnostics for {label} =====")
    for number, (page, store) in enumerate(stores, 1):
        print(f"--- page {number} ---")
        try:
            state = page.evaluate(_STATE_JS)
            for key, value in state.items():
                print(f"{key}: {value!r}")
        except Exception as error:  # the page may already be closed
            print(f"page state unreadable: {error}")
        print("console log:")
        for line in store["console"][-60:]:
            print("  " + line)
        print(f"last {NETWORK_TAIL} network calls:")
        for line in store["network"][-NETWORK_TAIL:]:
            print("  " + line)
    print("===== end S7 diagnostics =====")


def print_diagnostics_on_failure(test):
    @functools.wraps(test)
    def wrapper(*args, **kwargs):
        live_browser = kwargs.get("live_browser")
        stores: list = []
        browser = live_browser[0] if live_browser else None
        original = browser.new_page if browser else None
        if browser is not None:
            def new_page(*a, **k):
                page = original(*a, **k)
                store = {"console": [], "network": []}
                _watch(page, store)
                stores.append((page, store))
                return page

            browser.new_page = new_page
        try:
            return test(*args, **kwargs)
        except BaseException:
            _print_report(test.__name__, stores)
            raise
        finally:
            if browser is not None:
                try:
                    del browser.new_page
                except Exception:
                    pass

    return wrapper
