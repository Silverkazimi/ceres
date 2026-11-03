"""Warm-cache reload: same browser context, HTTP cache ON (no Playwright routing; external hosts go to a dead proxy).
Optional network emulation via CDP (RTT ms, kbit/s). usage: python -I warmload.py BASE [rtt_ms down_kbps]"""
import sys, time, collections
from playwright.sync_api import sync_playwright
B = sys.argv[1]; rtt = float(sys.argv[2]) if len(sys.argv) > 2 else 0; kbps = float(sys.argv[3]) if len(sys.argv) > 3 else 0
URL = B + "/?player=sample01"
READY = "() => document.querySelectorAll('#board tbody tr, .plot-card, [data-deed-uid]').length > 0"
with sync_playwright() as p:
    br = p.chromium.launch(proxy={"server": "http://127.0.0.1:9", "bypass": "127.0.0.1,localhost"})
    ctx = br.new_context(viewport={"width": 1536, "height": 864})
    page = ctx.new_page()
    cdp = ctx.new_cdp_session(page)
    if rtt or kbps:
        cdp.send("Network.enable")
        cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": rtt,
                 "downloadThroughput": kbps * 1024 / 8 if kbps else -1, "uploadThroughput": kbps * 1024 / 8 if kbps else -1})
    for label in ("first (empty cache)", "reload (warm cache)", "reload (warm cache)"):
        stats = collections.Counter(); byts = collections.Counter()
        def fin(r):
            resp = r.response(); s = r.sizes()
            k = (r.resource_type, resp.status if resp else 0)
            stats[k] += 1; byts[k] += s.get("responseBodySize", 0)
        page.on("requestfinished", fin)
        t = time.perf_counter()
        page.goto(URL) if label.startswith("first") else page.reload()
        page.wait_for_function(READY, timeout=60000)
        ready = (time.perf_counter() - t) * 1000
        page.wait_for_load_state("networkidle")
        nav = page.evaluate("() => { const n = performance.getEntriesByType('navigation')[0]; return [Math.round(n.domContentLoadedEventEnd), Math.round(n.loadEventEnd)] }")
        page.remove_listener("requestfinished", fin)
        print(f"{label:22s} rtt={rtt}ms: first board row at {ready:.0f} ms, DCL/load {nav}; " +
              ", ".join(f"{t}/{s}: n={n} {byts[(t,s)]//1024}KiB" for (t, s), n in sorted(stats.items())))
    br.close()
