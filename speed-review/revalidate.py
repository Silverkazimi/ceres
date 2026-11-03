"""Count, via CDP, how a warm reload gets each static file: network 200, network 304, or cache. usage: python -I revalidate.py BASE"""
import sys, collections
from playwright.sync_api import sync_playwright
B = sys.argv[1]
with sync_playwright() as p:
    br = p.chromium.launch(proxy={"server": "http://127.0.0.1:9", "bypass": "127.0.0.1,localhost"})
    ctx = br.new_context(); page = ctx.new_page(); cdp = ctx.new_cdp_session(page); cdp.send("Network.enable")
    seen = []
    cdp.on("Network.responseReceived", lambda e: seen.append(e))
    page.goto(B + "/?player=sample01"); page.wait_for_load_state("networkidle"); page.wait_for_timeout(1500)
    for how in ("reload()", "new navigation (link click / typed URL)"):
        seen.clear()
        if how == "reload()": page.reload()
        else: page.goto(B + "/?player=sample01")
        page.wait_for_load_state("networkidle"); page.wait_for_timeout(1500)
        c = collections.Counter()
        for e in seen:
            r = e["response"]
            if "/static/" not in r["url"]: continue
            src = "memory" if r.get("fromMemoryCache") else "disk" if r.get("fromDiskCache") else f"net {r['status']}"
            c[(e["type"], src)] += 1
        print(how, dict(c))
    br.close()
