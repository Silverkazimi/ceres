"""Live mutation-standard guards for the virtualized plot table."""

from __future__ import annotations

import contextlib
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, sync_playwright
from .conftest import require_testclient_socket
from .vm_context import read_entry_source


ROOT = Path(__file__).parents[2]
APP_JS = ROOT / "ceres" / "static" / "app.js"


def _free_port() -> int:
    require_testclient_socket()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_server(base_url: str) -> None:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{base_url}/", timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError("Ceres did not become ready")


@contextlib.contextmanager
def _live_ceres(player: str):
    port = _free_port()
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as output_file:
        process = subprocess.Popen(
            ["python3", "-m", "ceres", "--port", str(port), "--no-reload-watch", "--player", player],
            cwd=ROOT,
            stdout=output_file,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            base_url = f"http://127.0.0.1:{port}"
            try:
                _wait_for_server(base_url)
            except RuntimeError as error:
                output_file.seek(0)
                raise RuntimeError(f"{error}: {output_file.read()}") from error
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                try:
                    yield browser, base_url
                finally:
                    browser.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


@pytest.fixture(scope="module")
def player0017_browser() -> tuple[Browser, str]:
    with _live_ceres("player0017") as value:
        yield value


def _page(
    browser: Browser,
    base_url: str,
    app_source: str | None = None,
    *,
    wait_timeout: int = 60_000,
    injection_seen: dict | None = None,
) -> Page:
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    if app_source is not None:
        # G2 (campaign plan revision 3, §2.3): widened to **/static/*.js* so
        # interception follows the code rather than a filename -- app_source is
        # always a mutated copy of app.js specifically, so every OTHER served
        # static module (theme.js, customSelect.js, and any post-split module)
        # is passed through untouched via route.continue_().
        def _serve_injected(route) -> None:
            request_path = route.request.url.split("/static/", 1)[-1].split("?", 1)[0]
            if request_path != APP_JS.name:
                route.continue_()
                return
            if injection_seen is not None:
                injection_seen["hit"] = True
            route.fulfill(content_type="application/javascript", body=app_source)

        page.route("**/static/*.js*", _serve_injected)
    page.goto(base_url)
    page.wait_for_function(
        "document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0",
        timeout=wait_timeout,
    )
    return page


def _in_window_mutations(page: Page) -> dict:
    return page.evaluate(
        """
        async () => {
          window.scrollTo(0, 0);
          await new Promise(resolve => requestAnimationFrame(resolve));
          const tbody = document.querySelector('#plot-tbody');
          const before = [...tbody.querySelectorAll('tr.plot-row')].map(row => row.dataset.deedUid);
          let mutations = 0;
          const observer = new MutationObserver(records => { mutations += records.length; });
          observer.observe(tbody, {subtree: true, childList: true, attributes: true, characterData: true});
          window.scrollTo(0, 1);
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          await new Promise(resolve => setTimeout(resolve, 50));
          observer.disconnect();
          const after = [...tbody.querySelectorAll('tr.plot-row')].map(row => row.dataset.deedUid);
          return {mutations, before, after, scrollY: window.scrollY};
        }
        """
    )


def test_small_in_window_scroll_has_zero_plot_tbody_mutations(player0017_browser: tuple[Browser, str]) -> None:
    """Injection: replace the unchanged-window return with tbody.innerHTML = tbody.innerHTML; this went RED, then the unmodified source went GREEN.
    G1 (campaign plan revision 3, §2.3): anchor asserted present and unique before
    mutating; injection_seen asserted after, so a routing miss (e.g. the anchor
    moving to a post-split module this glob no longer covers) fails loudly instead
    of silently serving the real, unmutated file."""
    browser, base_url = player0017_browser
    source = read_entry_source()
    anchor = "if (!force && start === MOUNTED_START && end === MOUNTED_END) return false;"
    assert source.count(anchor) == 1, "renderVirtualWindow()'s unchanged-window guard moved or is no longer unique"
    page = _page(browser, base_url)
    green = _in_window_mutations(page)
    page.close()
    assert green["mutations"] == 0, green
    assert green["before"] == green["after"], green

    injected = source.replace(
        anchor,
        "if (!force && start === MOUNTED_START && end === MOUNTED_END) { tbody.innerHTML = tbody.innerHTML; return false; }",
        1,
    )
    assert injected != source
    injection_seen: dict = {}
    page = _page(browser, base_url, injected, wait_timeout=180_000, injection_seen=injection_seen)
    red = _in_window_mutations(page)
    page.close()
    assert injection_seen.get("hit"), "injected app.js response was not intercepted"
    assert red["mutations"] > 0, red

    page = _page(browser, base_url)
    reverted = _in_window_mutations(page)
    page.close()
    assert reverted["mutations"] == 0, reverted
    assert reverted["before"] == reverted["after"], reverted


def test_player0017_mounted_window_is_bounded(player0017_browser: tuple[Browser, str]) -> None:
    """Injection: replace tableWindow()'s range with [0, CURRENT_ROWS.length]; this went RED, then the unmodified source went GREEN.
    G1 (campaign plan revision 3, §2.3): anchor asserted present and unique before
    mutating; injection_seen asserted after."""
    browser, base_url = player0017_browser
    source = read_entry_source()
    anchor = "if (!force && start === MOUNTED_START && end === MOUNTED_END) return false;"
    assert source.count(anchor) == 1, "renderVirtualWindow()'s unchanged-window guard moved or is no longer unique"
    page = _page(browser, base_url)
    green_count = page.locator("#plot-tbody > tr.plot-row").count()
    page.close()
    assert green_count < 300, green_count

    injected = source.replace(
        anchor,
        "if (!force && start === MOUNTED_START && end === MOUNTED_END) { for (let index = 0; index < CURRENT_ROWS.length; index++) mountRowAt(tbody, index, BACK_SPACER); return false; }",
        1,
    )
    assert injected != source
    injection_seen: dict = {}
    page = _page(browser, base_url, injected, injection_seen=injection_seen)
    page.evaluate("window.scrollTo(0, 1)")
    page.wait_for_function(
        "document.querySelectorAll('#plot-tbody > tr.plot-row').length >= 300",
        timeout=180_000,
    )
    red_count = page.locator("#plot-tbody > tr.plot-row").count()
    page.close()
    assert injection_seen.get("hit"), "injected app.js response was not intercepted"
    assert red_count >= 300, red_count

    page = _page(browser, base_url)
    reverted_count = page.locator("#plot-tbody > tr.plot-row").count()
    page.close()
    assert reverted_count < 300, reverted_count


def _rewindow_count_over_scroll(page: Page, *, step_px: int = 29, steps: int = 60) -> dict:
    # Counts how many separate re-window events (a childList mutation batch on
    # #plot-tbody -- mountRowAt()/unmountRowAt() add/remove <tr> nodes) happen while
    # scrolling in small row-sized increments -- the observable VIRTUAL_WINDOW_BLOCK
    # quantization controls. app.js is loaded as `type="module"` (index.html), so its
    # top-level `let`/`const` bindings (MOUNTED_START etc.) are module-scoped, not
    # `window` properties -- unreachable from an outside page.evaluate() call. A
    # MutationObserver on the real DOM sidesteps that: each renderVirtualWindow() call
    # that actually mounts/unmounts rows runs synchronously inside one rAF callback, so
    # the observer's microtask-queued callback fires once per real re-window, not once
    # per scroll step.
    return page.evaluate(
        f"""
        async () => {{
          window.scrollTo(0, 0);
          await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          const tbody = document.querySelector('#plot-tbody');
          let rewindows = 0;
          const observer = new MutationObserver(() => {{ rewindows++; }});
          observer.observe(tbody, {{childList: true}});
          for (let i = 0; i < {steps}; i++) {{
            window.scrollBy(0, {step_px});
            await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
          }}
          observer.disconnect();
          return {{rewindows, scrollY: window.scrollY}};
        }}
        """
    )


def test_player0017_row_window_is_quantized_not_per_frame(player0017_browser: tuple[Browser, str]) -> None:
    """Injection: set VIRTUAL_WINDOW_BLOCK to 1 (the pre-fix behaviour, re-window on
    nearly every scroll frame); this went RED (many more re-windows over the same
    60-row scroll), then the unmodified source (VIRTUAL_WINDOW_BLOCK = 50) went GREEN.
    G1 (campaign plan revision 3, §2.3): anchor asserted present and unique before
    mutating (upgraded from a bare membership check); injection_seen asserted after."""
    browser, base_url = player0017_browser
    source = read_entry_source()
    anchor = "const VIRTUAL_WINDOW_BLOCK = 50;"
    assert source.count(anchor) == 1, "VIRTUAL_WINDOW_BLOCK declaration moved or is no longer unique"

    page = _page(browser, base_url)
    green = _rewindow_count_over_scroll(page)
    page.close()
    # 60 rows scrolled at block=50 should re-window at most a couple of times
    # (crossing a block boundary), never once per row.
    assert green["rewindows"] <= 5, green

    injected = source.replace(anchor, "const VIRTUAL_WINDOW_BLOCK = 1;", 1)
    assert injected != source
    injection_seen: dict = {}
    page = _page(browser, base_url, injected, injection_seen=injection_seen)
    red = _rewindow_count_over_scroll(page)
    page.close()
    assert injection_seen.get("hit"), "injected app.js response was not intercepted"
    # At block=1 the window recomputes on essentially every step -- far more
    # re-windows over the same 60-row scroll than the quantized default.
    assert red["rewindows"] > green["rewindows"] * 3, (red, green)

    page = _page(browser, base_url)
    reverted = _rewindow_count_over_scroll(page)
    page.close()
    assert reverted["rewindows"] <= 5, reverted
