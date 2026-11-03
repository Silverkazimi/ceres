"""Page-load inventory: requests, bytes, timings, DOM size, on the offline fixture server.
Run from repo root: PLAYWRIGHT_BROWSERS_PATH=... python -I <this> [path]"""
import sys, os, time, json, collections
sys.path.insert(0, os.getcwd())
from tests.ceres.conftest import _fixture_server, _offline_browser, wait_for_board, LIVE_PLAYER, DESKTOP

PATH = sys.argv[1] if len(sys.argv) > 1 else f"/?player={LIVE_PLAYER}"

def inventory(page, url, settle):
    reqs = []
    def on_finished(r):
        try:
            resp = r.response()
            sizes = r.sizes()
            reqs.append(dict(url=r.url, type=r.resource_type, status=resp.status if resp else None,
                             body=sizes.get("responseBodySize", 0), hdr=sizes.get("responseHeadersSize", 0),
                             cc=(resp.headers.get("cache-control") if resp else None),
                             enc=(resp.headers.get("content-encoding") if resp else None),
                             t=r.timing))
        except Exception as e:
            reqs.append(dict(url=r.url, err=str(e)))
    page.on("requestfinished", on_finished)
    t0 = time.perf_counter()
    page.goto(url)
    settle(page)
    wall = time.perf_counter() - t0
    page.wait_for_timeout(1500)
    nav = page.evaluate("""() => { const n = performance.getEntriesByType('navigation')[0];
      const p = Object.fromEntries(performance.getEntriesByType('paint').map(e=>[e.name, Math.round(e.startTime)]));
      return {dcl: Math.round(n.domContentLoadedEventEnd), load: Math.round(n.loadEventEnd), paint: p,
              nodes: document.getElementsByTagName('*').length,
              res: performance.getEntriesByType('resource').length}; }""")
    return wall, nav, reqs

def summarize(label, wall, nav, reqs):
    by = collections.defaultdict(lambda: [0, 0, 0])
    for r in reqs:
        if "err" in r: continue
        k = r["type"]
        by[k][0] += 1; by[k][1] += r["body"]; by[k][2] += (r["status"] == 304)
    print(f"== {label}: board-ready wall {wall*1000:.0f} ms; nav {nav}")
    for k, (n, b, n304) in sorted(by.items(), key=lambda kv: -kv[1][1]):
        print(f"   {k:10s} n={n:4d} bytes(on-wire body)={b:9d} 304s={n304}")
    api = collections.Counter(r["url"].split("?")[0].split("//",1)[1].split("/",1)[1] for r in reqs if "err" not in r and r["type"] in ("fetch","xhr"))
    print("   fetch/xhr by path:", api.most_common(40))
    hosts = collections.Counter(r["url"].split("/")[2] for r in reqs if "err" not in r)
    print("   hosts:", dict(hosts))
    return by

with _fixture_server() as base:
    with _offline_browser(base) as browser:
        # warm server-side caches once (as the harness does)
        p = browser.new_page(viewport=dict(DESKTOP)); p.goto(base + PATH); wait_for_board(p) if PATH.startswith("/?") else p.wait_for_load_state("networkidle"); p.close()
        for i in range(3):
            page = browser.new_page(viewport=dict(DESKTOP))
            settle = wait_for_board if PATH.startswith("/?") else (lambda pg: pg.wait_for_load_state("networkidle"))
            wall, nav, reqs = inventory(page, base + PATH, settle)
            by = summarize(f"cold-cache load #{i+1} (harness browser, routing on => no HTTP cache)", wall, nav, reqs)
            if i == 0:
                json.dump(reqs, open(os.environ.get("OUT", "/dev/null"), "w"), indent=1, default=str)
            page.close()
