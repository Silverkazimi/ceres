""" in a real Chromium, hermetic: the page's files come from ceres/static through page.route, /api/cards and /api/market
are fixtures, and every card image is a small PNG answered after a network delay (nothing leaves the machine).

While the page scrolls (wheel steps and a fling on desktop with the pointer over the grid, touch drags and flings on a phone),
every animation frame records each on-screen tile: its node, its opacity and its art (opacity, source, decoded). The art of a
tile is read when the tile is built (not lazily as it nears the screen, which inside the content-visibility:auto tiles left the
art box empty, then faded it in on screen), and art that arrives off screen is shown without the fade. Checked: no frame where
an on-screen tile's art is missing, fading or swapped, no tile node replaced, no tile opacity change (: the hover state
holds while scrolling). Run under the shared browser lock:
flock -w 900 /tmp/er-browser.lock nice -n 15 python3 -m pytest -q tests/ceres/test_scroll_no_flicker_browser.py"""

from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.async_api import async_playwright

pytestmark = pytest.mark.browser

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ceres" / "static"
ORIGIN = "http://ceres.test"
ART_DELAY_S = 0.25  # each card image arrives a quarter second after it is asked for
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
COUNT = 150
SETS = ["beta", "chaos", "rebellion"]

CARDS = [{"uid": f"T{i}", "card_detail_id": 1000 + i, "edition": 7, "foil": 0, "name": f"Fixture Beast {i}", "rarity": "rare",
          "card_set": SETS[i % 3], "splinters": ["Fire"], "bcx": 1, "level": 1, "max_level": 8, "bcx_max": 22,
          "land_base_pp": 100.0 + i, "max_level_pp": 1000, "status": "idle", "maxed": False} for i in range(COUNT)]
LISTINGS = [{"card_detail_id": 2000 + i, "name": f"Fixture Seller {i}", "edition": 7, "card_set": SETS[i % 3], "rarity": "common",
             "foil": 0, "gold": False, "splinters": ["Neutral"], "level": 9, "max_level": 10, "bcx": 354.0, "bcx_max": 400,
             "maxed": False, "land_base_pp": 885.0, "pp_source": "listing", "max_level_pp": 1000.0, "price_usd": 1.0 + i / 100,
             "price_dec": 2000.0 + i * 20, "price_currency": "USD", "price_per_cc_usd": 0.003, "pp_per_dec": 0.37,
             "pp_per_usd": 885.0 / (1.0 + i / 100), "dec_per_pp": 2.6, "max_cost_usd": 1.3, "max_cost_dec": 2600.0, "missing_cc": 46.0,
             "pp_per_dec_max": 0.38, "dec_per_pp_max": 2.6, "own": False, "lkey": f"k{i:04d}", "market_url": "", "land_abilities": []}
            for i in range(COUNT)]
MARKET = {"player": "sample01", "read_at": "2026-10-15T00:00:00Z", "filters": {}, "dec_usd": 0.0005, "prints_on_sale": COUNT,
          "prints_matching": COUNT, "prints_read": COUNT, "page": 1, "start": 0, "next_start": COUNT, "has_more": False,
          "count": COUNT, "capped": False, "sort": "cost_per_pp", "listings": LISTINGS}

SAMPLER = r"""
() => {
 const S = window.__ir9287 = { frames: [], removed: 0, on: true };
 let nid = 0; const ids = new WeakMap(); const id = (el) => { if (!ids.has(el)) ids.set(el, ++nid); return ids.get(el); };
 const grid = document.querySelector(document.body.dataset.cbMode === 'market' ? '#cm-grid' : '#cb-grid');
 new MutationObserver((list) => { for (const m of list) S.removed += [...m.removedNodes].filter((n) => n.nodeType === 1).length; })
 .observe(grid, { subtree: true, childList: true });
 const tick = () => {
 if (!S.on) return;
 const vh = innerHeight, tiles = [];
 for (const el of grid.children) {
 if (!el.classList.contains('cb-tile')) continue;
 const r = el.getBoundingClientRect();
 if (r.bottom <= 0 || r.top >= vh || !r.height) continue;
 const img = el.querySelector('.cb-art > img');
 tiles.push({ id: id(el), top: Math.round(r.top), op: +getComputedStyle(el).opacity,
 art: img ? { op: +getComputedStyle(img).opacity, src: img.currentSrc || img.src, nw: img.naturalWidth } : null });
 }
 S.frames.push({ y: Math.round(scrollY), tiles });
 requestAnimationFrame(tick);
 };
 requestAnimationFrame(tick);
}
"""


def _page_html() -> str:
    html = (STATIC / "cards.html").read_text(encoding="utf-8")
    return html.replace("{{CERES_VERSION}}", "t").replace("{{CERES_RELEASED_VERSION}}", "1.0.0").replace("{{CERES_LOCAL_MARKER}}", "")


def _handler(page_html: str):
    async def handle(route):
        url = urlparse(route.request.url)
        path = url.path
        if url.hostname and url.hostname.endswith("cloudfront.net"):
            await asyncio.sleep(ART_DELAY_S)
            return await route.fulfill(status=200, content_type="image/png", body=PNG)
        if url.hostname != "ceres.test":
            return await route.fulfill(status=404, body="")
        if path == "/cards":
            return await route.fulfill(status=200, content_type="text/html", body=page_html)
        if path.startswith("/static/"):
            file = STATIC / path[len("/static/"):]
            if file.is_file():
                kind = "text/javascript" if file.suffix == ".js" else (mimetypes.guess_type(file.name)[0] or "application/octet-stream")
                return await route.fulfill(status=200, content_type=kind, body=file.read_bytes())
            return await route.fulfill(status=404, body="")
        if path == "/api/cards":
            return await route.fulfill(status=200, content_type="application/json", body=json.dumps({"cards": CARDS}))
        if path == "/api/market":
            return await route.fulfill(status=200, content_type="application/json", body=json.dumps(MARKET))
        return await route.fulfill(status=404, content_type="application/json", body=json.dumps({"detail": "not in this fixture"}))
    return handle


async def _scroll(page, ctx, width: int, height: int, touch: bool) -> None:
    cdp = await ctx.new_cdp_session(page)
    if touch:
        for k in range(5):
            x, y = width // 2, int(height * 0.8)
            await cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
            step = 30 if k < 2 else 90
            for _ in range(int(height * 0.6) // step):
                y -= step
                await cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x, "y": y}]})
                await asyncio.sleep(0.016)
            await cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
            await page.wait_for_timeout(400)
    else:
        for _ in range(20):
            await page.mouse.wheel(0, 120)
            await page.wait_for_timeout(40)
        await page.wait_for_timeout(300)
        await cdp.send("Input.synthesizeScrollGesture", {"x": width // 2, "y": height // 2, "yDistance": -1800, "speed": 3000,
                                                         "gestureSourceType": "mouse"})
    await page.wait_for_timeout(500)


async def _run(mode: str, width: int, height: int, touch: bool) -> dict:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            ctx = await browser.new_context(viewport={"width": width, "height": height}, has_touch=touch, is_mobile=touch)
            page = await ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.route("**/*", _handler(_page_html()))
            await page.goto(f"{ORIGIN}/cards?player=sample01&mode={mode}")
            tile = "#cm-grid .cm-tile:not(.cm-skeleton)" if mode == "market" else "#cb-grid .cb-tile"
            await page.wait_for_selector(tile, timeout=30000)
            # every built tile's art has arrived (read when the tile is built, not when it nears the screen)
            await page.wait_for_function("""() => { const imgs = [...document.querySelectorAll('.cb-grid > .cb-tile .cb-art > img')];
 return imgs.length > 0 && imgs.every((i) => i.complete && i.naturalWidth > 0); }""", timeout=30000)
            await page.wait_for_timeout(600)
            if not touch:
                await page.mouse.move(width / 2, height / 2)  # the pointer rests on a tile: the others dim ()
                await page.wait_for_timeout(600)
            await page.evaluate(SAMPLER)
            await _scroll(page, ctx, width, height, touch)
            data = await page.evaluate("() => { window.__ir9287.on = false; return window.__ir9287; }")
            return {"data": data, "errors": errors}
        finally:
            await browser.close()


def _faults(frames: list) -> list:
    seen, out = {}, []
    for i, f in enumerate(frames):
        for t in f["tiles"]:
            art, prev = t["art"], seen.get(t["id"])
            if art and (art["op"] < 0.99 or art["nw"] == 0):
                out.append((i, f["y"], t["id"], "art missing or fading", art["op"], art["nw"]))
            if prev and prev["art"] and art and prev["art"]["src"] != art["src"]:
                out.append((i, f["y"], t["id"], "art swapped"))
            if prev and abs(prev["op"] - t["op"]) > 0.001:
                out.append((i, f["y"], t["id"], "tile opacity changed", prev["op"], t["op"]))
            seen[t["id"]] = t
    return out


@pytest.mark.parametrize("mode", ["market", "cards"])
@pytest.mark.parametrize("width,height,touch", [(1536, 864, False), (390, 844, True)])
def test_scroll_keeps_every_on_screen_tile(mode, width, height, touch):
    got = asyncio.run(_run(mode, width, height, touch))
    frames = got["data"]["frames"]
    assert got["errors"] == []
    assert len(frames) > 30 and max(f["y"] for f in frames) > 1500          # the page really scrolled
    assert len({t["id"] for f in frames for t in f["tiles"]}) > 6           # through many tiles
    assert got["data"]["removed"] == 0                                      # no tile (or part of one) replaced while scrolling
    assert _faults(frames) == []                                            # no on-screen art pop, fade, swap or opacity change
