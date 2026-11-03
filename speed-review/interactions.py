"""Per-interaction redraw cost on the offline fixture board.
For each interaction: DOM nodes added+removed (MutationObserver on <body>, subtree), <tr> rows replaced in #plot-tbody,
CDP Performance ScriptDuration/LayoutDuration/RecalcStyleDuration deltas, and same-origin requests fired.
SCALE=k multiplies the board's plots k times (cloned rows with new deed uids, via a route on /api/board) to see how cost grows.
usage (repo root): SCALE=1 python -I interactions.py"""
import os, sys, json, time, collections, copy
sys.path.insert(0, os.getcwd())
from tests.ceres.conftest import _fixture_server, _offline_browser, LIVE_PLAYER
SCALE = int(os.environ.get("SCALE", "1"))
OBS = """() => { window.__m = {added:0, removed:0, trAdded:0, trRemoved:0};
  if (window.__mo) window.__mo.disconnect();
  window.__mo = new MutationObserver(recs => { for (const r of recs) { const tb = r.target.closest && r.target.closest('#plot-tbody') || (r.target.id==='plot-tbody' ? r.target : null);
     for (const n of r.addedNodes) { if (n.nodeType===1) { window.__m.added += 1 + n.getElementsByTagName('*').length; if (tb && n.tagName==='TR') window.__m.trAdded++; } }
     for (const n of r.removedNodes) { if (n.nodeType===1) { window.__m.removed += 1 + n.getElementsByTagName('*').length; if (tb && n.tagName==='TR') window.__m.trRemoved++; } } } });
  window.__mo.observe(document.body, {childList:true, subtree:true}); }"""
def metrics(cdp):
    m = {x["name"]: x["value"] for x in cdp.send("Performance.getMetrics")["metrics"]}
    return {k: m.get(k, 0) for k in ("ScriptDuration", "LayoutDuration", "RecalcStyleDuration", "TaskDuration", "LayoutCount")}
def measure(page, cdp, label, action, reqs):
    page.evaluate(OBS); reqs.clear()
    before = metrics(cdp); t = time.perf_counter()
    action()
    page.wait_for_timeout(50)
    page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
    try: page.wait_for_load_state("networkidle", timeout=5000)
    except Exception: pass
    page.wait_for_timeout(300)
    after = metrics(cdp); m = page.evaluate("() => window.__m")
    d = {k: round((after[k] - before[k]) * (1000 if k != "LayoutCount" else 1), 1) for k in after}
    paths = collections.Counter(u.split("?")[0].split("/", 3)[-1] for u in reqs)
    print(f"{label:58s} nodes +{m['added']:6d}/-{m['removed']:6d} rows(tr) +{m['trAdded']:4d}/-{m['trRemoved']:4d} "
          f"script {d['ScriptDuration']:7.1f}ms layout {d['LayoutDuration']:6.1f}ms style {d['RecalcStyleDuration']:6.1f}ms layouts {d['LayoutCount']:4.0f} reqs {dict(paths)}")
with _fixture_server() as base, _offline_browser(base) as browser:
    page = browser.new_page(viewport={"width": 1536, "height": 864})
    if SCALE > 1:
        def scale_board(route):
            resp = route.fetch(); body = resp.json()
            plots = body.get("plots", []); out = list(plots)
            for k in range(1, SCALE):
                for p in plots:
                    q = copy.deepcopy(p); q["deed_uid"] = f"{p['deed_uid']}x{k}"
                    for key in ("plot_label", "label"):
                        if key in q and isinstance(q[key], str): q[key] = f"{q[key]}~{k}"
                    out.append(q)
            body["plots"] = out
            route.fulfill(response=resp, body=json.dumps(body), headers={**resp.headers, "content-type": "application/json", "content-encoding": "identity"})
        page.route("**/api/board?player=*", scale_board)
    reqs = []
    page.on("request", lambda r: reqs.append(r.url) if r.url.startswith(base) else None)
    cdp = page.context.new_cdp_session(page); cdp.send("Performance.enable")
    page.goto(f"{base}/?player={LIVE_PLAYER}")
    page.wait_for_function("document.querySelectorAll('#plot-tbody > tr.plot-row').length > 0", timeout=60000)
    page.wait_for_load_state("networkidle"); page.wait_for_timeout(1500)
    HAS_OFF = os.environ.get("HAS_OFF", "")
    if HAS_OFF:
        n = page.evaluate('''(mode) => { let n = 0; const anchored = /(^|,)\\s*(html|body|:root)[^,{]*:has\\(/;
          const walk = (list, owner) => { for (let i = list.length - 1; i >= 0; i--) { const r = list[i];
            if (r.cssRules && !r.selectorText) { walk(r.cssRules, r); continue; }
            const sel = r.selectorText || ''; if (!sel.includes(':has(')) continue;
            const subject = /(^|,)\\s*(html|body|:root)[^\\s>+~,{]*:has\\(/; if (mode === 'all' || (mode === 'subject' ? subject.test(sel) : mode.startsWith('re:') ? new RegExp(mode.slice(3)).test(sel) : anchored.test(sel))) { (window.__deleted = window.__deleted || []).push(sel.slice(0, 110)); owner.deleteRule(i); n++; } } };
          for (const sh of document.styleSheets) { try { walk(sh.cssRules, sh); } catch (_) {} } return n; }''', HAS_OFF)
        print(f"HAS_OFF={HAS_OFF}: deleted {n} style rules"); [print("   ", x) for x in page.evaluate("window.__deleted")] if os.environ.get("LIST") else None
        page.wait_for_timeout(500)
    rows = page.evaluate("document.querySelectorAll('#plot-tbody > tr.plot-row').length")
    print(f"SCALE={SCALE}: plot rows mounted {rows}, DOM elements {page.evaluate('document.getElementsByTagName(\"*\").length')}")
    tf = page.locator("#text-filter")
    measure(page, cdp, "text filter: type 1 char", lambda: tf.press_sequentially("a"), reqs)
    measure(page, cdp, "text filter: type 2nd char", lambda: tf.press_sequentially("b"), reqs)
    measure(page, cdp, "text filter: clear", lambda: tf.fill(""), reqs)
    hdr = page.locator("th[data-key='terrain']").first
    if hdr.count():
        measure(page, cdp, "sort: click a sortable header", lambda: hdr.click(), reqs)
    measure(page, cdp, "window focus event (wallet notify path)", lambda: page.evaluate("window.dispatchEvent(new Event('focus'))"), reqs)
    # open one plot editor
    board = json.loads(page.evaluate("fetch('/api/board?player=%s').then(r=>r.text())" % LIVE_PLAYER))
    deed = next((p["deed_uid"] for p in board["plots"] if (p.get("capacity") or {}).get("open_slots", 0) >= 2), None)
    measure(page, cdp, "open a plot (expand + editor)", lambda: (page.locator(f"tr.plot-row[data-deed-uid='{deed}']").click(),
            page.locator(".slot-editor:visible .editor-band[data-plot-state='ready']").wait_for(timeout=30000)), reqs)
    worker = page.locator(".slot-editor:visible .slot-group[data-group-id='worker']")
    def stage(name):
        pattern = "**/api/plots/*/candidates*"
        cand = {"name": name, "asset_ref": "m-" + name.replace(" ", "-"), "base_pp": 2000, "level": 1, "bucket": "idle", "land_abilities": []}
        payload = json.dumps({"candidates": [cand], "total": 1, "source": "test-fixture", "excluded_counts": {}, "notices": []})
        page.route(pattern, lambda route: route.fulfill(status=200, content_type="application/json", body=payload))
        worker.locator(".slot-grid > .slot-tile-cell > .slot-tile.is-empty").first.click()
        page.locator(".candidate-row").first.wait_for(timeout=30000)
        page.evaluate(OBS); reqs.clear()
        b = metrics(cdp)
        page.locator(".candidate-row").first.click()
        page.wait_for_function("document.getElementById('checkout-btn') && !document.getElementById('checkout-btn').hidden", timeout=30000)
        page.wait_for_timeout(50)
        try: page.wait_for_load_state("networkidle", timeout=5000)
        except Exception: pass
        page.wait_for_timeout(300)
        a = metrics(cdp); m = page.evaluate("() => window.__m"); page.unroute(pattern)
        return b, a, m
    for label, badge in (("stage 1 worker, no badge filter", None), ("stage 1 worker, badge filter set", True)):
        if badge:
            opts = page.evaluate("[...document.querySelectorAll('#badge-filter option')].map(o=>o.value).filter(Boolean)")
            if not opts: print("  (no badge filter options)"); break
            counts = page.evaluate("(vals)=>vals.map(v=>{const s=document.getElementById('badge-filter'); s.value=v; s.dispatchEvent(new Event('change')); return [v, document.querySelectorAll('#plot-tbody > tr.plot-row').length]})", opts)
            best = max(counts, key=lambda c: c[1]); print("  badge filter options -> rows:", counts[:6], "using", best)
            page.select_option('#badge-filter', best[0]); page.wait_for_timeout(800)
            if not page.locator(".slot-editor:visible .slot-group[data-group-id='worker'] .slot-tile.is-empty").count():
                print("  (open plot hidden by this filter; skipping)"); break
        b, a, m = stage("M Worker " + label[-3:])
        d = {k: round((a[k] - b[k]) * (1000 if k != "LayoutCount" else 1), 1) for k in a}
        paths = collections.Counter(u.split("?")[0].split("/", 3)[-1] for u in reqs)
        print(f"{label:58s} nodes +{m['added']:6d}/-{m['removed']:6d} rows(tr) +{m['trAdded']:4d}/-{m['trRemoved']:4d} "
              f"script {d['ScriptDuration']:7.1f}ms layout {d['LayoutDuration']:6.1f}ms style {d['RecalcStyleDuration']:6.1f}ms layouts {d['LayoutCount']:4.0f} reqs {dict(paths)}")
    measure(page, cdp, "window focus event, Changes panel populated", lambda: page.evaluate("window.dispatchEvent(new Event('focus'))"), reqs)
    renders = page.evaluate("performance.getEntriesByName('ceres-board-render').map(e=>Math.round(e.duration*10)/10)")
    print("ceres-board-render measures (ms) over the session:", renders)
