"""R-138 browser guards. RUN ONLY in a separately authorised dedicated queue.

All endpoints are asynchronous browser routes, and all modules are the checked-in
modules. Chromium capability failure is a failure, never a skip. Deterministic
art identifies presentation and decode ordering; these images do not constitute
the maintainer's acceptance of the real artwork. See work/r138-plot-reveal-guards.md.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import io
import json
import mimetypes
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest
from playwright.async_api import async_playwright
from PIL import Image

from ceres.board import project_board

from .table_edge import OVERHANG_JS, overhang_problems

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "ceres" / "static"
ORIGIN = "http://ceres-eos.test"
TERMINAL = "ready|degraded"
ART = b'<svg xmlns="http://www.w3.org/2000/svg" width="320" height="240"><rect width="320" height="240" fill="#296d45"/><path d="M0 190L90 45L210 180L320 80V240H0Z" fill="#d79a35"/><circle cx="240" cy="45" r="24" fill="#fff1b0"/></svg>'

PROBE = r"""(() => {
  const p = window.eosProbe = {writes: [], animations: [], reads: [], frames: [],
    decodes: [], pending: [], blockDecode: false, rejectDecode: false, stopped: false,longTasks:[],virtualRenders:[],wholesale:0};
  const replaceChildren=Element.prototype.replaceChildren;
  Element.prototype.replaceChildren=function(...nodes){if(this.id==='plot-tbody')p.wholesale++;return replaceChildren.apply(this,nodes)};
  if(PerformanceObserver.supportedEntryTypes.includes('longtask')) {
    new PerformanceObserver(list=>p.longTasks.push(...list.getEntries().map(e=>({start:e.startTime,duration:e.duration}))))
      .observe({type:'longtask',buffered:true});
  }
  const originalDecode = HTMLImageElement.prototype.decode;
  HTMLImageElement.prototype.decode = async function() {
    const entry = {node:this, src:this.currentSrc || this.src, connected:this.isConnected,
      managed:this.hasAttribute('data-eos-managed'), started:performance.now(), done:null};
    p.decodes.push(entry);
    await originalDecode.call(this);
    if (p.rejectDecode && entry.managed) throw new DOMException('Controlled decode rejection');
    if (p.blockDecode && entry.managed) await new Promise(resolve => p.pending.push(resolve));
    entry.done = performance.now();
  };
  p.releaseDecode = () => {p.blockDecode=false; p.pending.splice(0).forEach(done=>done());};
  const animate = Element.prototype.animate;
  Element.prototype.animate = function(frames, options) {
    const animation = animate.call(this, frames, options);
    p.animations.push({node:this, frames, options, start:performance.now(), animation});
    return animation;
  };
  for (const name of ['scrollTo','scrollBy']) {
    const fn = window[name].bind(window);
    window[name] = (...args) => {
      const stack = new Error().stack;
      p.writes.push({name,args,stack,t:performance.now(),before:scrollY});
      return fn(...args);
    };
  }
  const rect = Element.prototype.getBoundingClientRect;
  Element.prototype.getBoundingClientRect = function() {
    const stack = new Error().stack;
    if (stack.includes('/eos.js')) p.reads.push({kind:'rect',stack,t:performance.now()});
    return rect.call(this);
  };
  const computedStyle = window.getComputedStyle;
  window.getComputedStyle = (...args) => {
    const stack = new Error().stack;
    if (stack.includes('/eos.js')) p.reads.push({kind:'style',stack,t:performance.now()});
    return computedStyle(...args);
  };
  const extent = Object.getOwnPropertyDescriptor(Element.prototype, 'scrollHeight');
  if (extent?.get) Object.defineProperty(Element.prototype, 'scrollHeight', {...extent,
    get() { const stack = new Error().stack;
      if (stack.includes('/eos.js')) p.reads.push({kind:'extent',stack,t:performance.now()});
      return extent.get.call(this);
    }});
  const sample = () => {
    if (p.stopped) return;
    if (p.uid) {
      const row = document.querySelector(`tr.expand-row[data-deed-uid="${CSS.escape(p.uid)}"]`);
      const band = row?.querySelector('.editor-band');
      if (band && row.style.display !== 'none') {
        const b=rect.call(band), td=rect.call(row.firstElementChild);
        const next=row.nextElementSibling, nr=next ? rect.call(next) : null;
        const veil=band.querySelector('.eos-scenic-veil'), cover=band.querySelector('.eos-worker-cover');
        p.frames.push({t:performance.now(),y:scrollY,x:scrollX,state:band.dataset.plotState,
          bandTop:b.top+scrollY,bandHeight:b.height,tdHeight:td.height,
          nextTop:nr ? nr.top+scrollY : null,
          veil:veil ? computedStyle(veil).opacity : '0',
          cover:cover ? computedStyle(cover).opacity : '0',
          mounted:[...document.querySelectorAll('tr.plot-row')].map(r=>r.dataset.deedUid)});
      }
    }
    requestAnimationFrame(sample);
  };
  requestAnimationFrame(sample);
})();"""


# Expected editor-band height, derived from the band's own terms rather than read back
# from its block-size. Phone layout (`html.ceres-lw-le-1279`) keeps the fixed rem band
# from eos.js plotGeometryProfile (22.5125rem below 62rem of width, else 18.5125rem).
# Desktop bands are scene-scaled (ceres.css `--eos-content-height`): facts + controls +
# cards + footer + 21 scene units, where one scene unit is 1/1920 of the `.slot-editor`
# container's inline size. Each term is measured on its own (a zero-width probe sized by
# that single term) and the scene unit comes from the container's measured content
# width, so the sum is an independent check of the band's resolved block-size.
EXPECTED_BAND = r"""band=>{
  const root=parseFloat(getComputedStyle(document.documentElement).fontSize);
  if(document.documentElement.classList.contains('ceres-lw-le-1279')){
    const width=Math.min(band.closest('td').getBoundingClientRect().width,document.querySelector('#plot-table-scroll').clientWidth,1920);
    return (width/root<62?22.5125:18.5125)*root;
  }
  const term=value=>{
    const probe=document.createElement('div');
    probe.style.cssText=`position:absolute;visibility:hidden;inline-size:0;padding:0;border:0;block-size:${value}`;
    band.append(probe);
    const height=probe.getBoundingClientRect().height;
    probe.remove();
    return height;
  };
  const container=band.closest('.slot-editor'), cs=getComputedStyle(container);
  const unit=(container.clientWidth-parseFloat(cs.paddingLeft)-parseFloat(cs.paddingRight))/1920;
  return ['var(--eos-facts)','var(--eos-controls)','var(--eos-cards)','var(--eos-footer)'].map(term).reduce((a,b)=>a+b,0)+21*unit;
}"""


def _payload(report, details_by_id, *, count=80, capacity=5, no_art=False, long=False):
    board = project_board(report, details_by_id)
    # A plain powered worker plot. The fixture report's worker plots are Runi plots whose
    # slot-0 card is the Runi (card_detail_id 505): every Eos worker cloned from that card
    # would be a Runi to the product (format.js isRuniWorker) and fill the Runi tile, and
    # the template's staked Runi would add a Runi occupant. Clone a non-Runi worker and
    # leave the Runi unstaked; power comes from the Power Core set below.
    def _runi(worker):
        return worker.get("runi_number") not in (None, "") or worker.get("card_detail_id") == 505 or str(worker.get("card_name") or worker.get("name") or "").strip().lower() == "runi"
    template = next(row for row in board["plots"] if any(not _runi(worker) for worker in row["workers"]))
    worker = copy.deepcopy(next(worker for worker in template["workers"] if not _runi(worker)))
    plots = []
    for index in range(count):
        row = copy.deepcopy(template)
        row["deed_uid"] = f"eos-{index:04d}"
        row["plot"] = str(index + 1)
        row["harvest_pp"] = count - index
        row["terrain"] = "Plains"
        row["resource"] = "grain"
        row["plot_status"] = "natural"
        row["assets_loaded"] = True
        row["capacity"].update(potential_card_capacity=capacity, display=f"{capacity}/{capacity}", runi_staked=False, worker_count=capacity)
        row["workers"] = []
        row["bonus"] = {
            "power_core":{"name":"Power Core","asset_ref":f"power-{index}"},
            "totem":{"name":"Common Totem","rarity":"common","asset_ref":f"totem-{index}","boost":0.1},
            "title":{"name":"The Untamed","asset_ref":f"title-{index}","boost":0.1},
        }
        row.setdefault("staking_facts", {})["is_power_core_staked"] = True
        row["staking_facts"]["is_runi_staked"] = False
        for slot in range(capacity):
            item = copy.deepcopy(worker)
            item.update(slot=slot + 1, name=f"Eos Worker {slot + 1}", card_name=f"Eos Worker {slot + 1}",
                        asset_ref=f"eos-card-{index}-{slot}", edition=2, editions="2", skin="eos-skin",sole_holder_for=[],land_abilities=[])
            if no_art:
                item.pop("edition", None)
                item.pop("editions", None)
            row["workers"].append(item)
        if long:
            # Staged-removal notices now feed one warning model (plot-warnings.js): any
            # notice keeps its sentence, and the Bloodline finder action is carried only by
            # a `bloodline_pair_deactivated` notice. So the long messages and the unique
            # actions ride on two notices each.
            row["notices"] = [
                {"code":f"eos_notice_{i}", "severity":"caution", "blocking":"none", "kind":"warning",
                 "message":f"Notice {i}: " + "A long but complete actionable warning. " * 12}
                for i in range(3)
            ] + [
                {"code":"bloodline_pair_deactivated", "severity":"caution", "blocking":"none", "kind":"warning",
                 "context":{"sub_type":f"Eos{i}"},
                 "action":{"id":"open_bloodline_pairs", "label":f"Inspect companion {i}", "sub_type":f"Eos{i}"}}
                for i in range(3)
            ]
        plots.append(row)
    board["plots"] = copy.deepcopy(plots)
    for row in board["plots"]:
        row["workers"] = []
        row["biome"] = {}
        row["notices"] = []
    return board, {row["deed_uid"]: row for row in plots}


class EosBrowser:
    def __init__(self, page, board, details, artifact, *, baseline=False, mutation=None):
        self.page, self.board, self.details, self.artifact = page, board, details, artifact
        self.gates = {name:asyncio.Event() for name in ("detail", "scene", "portrait", "upgrade")}
        self.requests, self.active, self.peak, self.errors = [], 0, 0, []
        self.fail_detail = False
        self.reserved = None
        self.fail_primary = False
        self.fail_art = False
        self.scene_requests = 0
        self.baseline = baseline
        self.mutation, self.mutation_hits = mutation, 0
        self.artifact.mkdir(parents=True, exist_ok=True)
        self._cdp = None  # lazily created, reused across a filmstrip
        page.on("pageerror", lambda error:self.errors.append(str(error)))

    def release(self, *names):
        for name in names:
            self.gates[name].set()

    async def route(self, route):
        request = route.request
        path = unquote(urlparse(request.url).path)
        self.requests.append({"path":path, "method":request.method, "kind":request.resource_type})
        if path == "/":
            source = self.source("index.html").decode()
            await route.fulfill(content_type="text/html", body=source.replace("{{CERES_VERSION}}", "eos-guard"))
        elif path == "/api/board":
            await route.fulfill(json=self.board)
        elif path.startswith("/api/plots/") and (path.endswith("/expand") or len(path.strip("/").split("/")) == 3):
            await self.gates["detail"].wait()
            uid = path.split("/")[3]
            await route.fulfill(status=503 if self.fail_detail else 200,
                                json={"detail":"Eos detail temporarily unavailable"} if self.fail_detail else self.details[uid])
        elif path.endswith("/removal-impact"):
            # A staged removal is priced first; the page refuses (and stages nothing) when
            # the answer is not a well-formed impact, so the fixture prices each removed
            # asset as a neutral, still-powered change.
            refs = (request.post_data_json or {}).get("asset_refs") or []
            await route.fulfill(json={
                "deed_uid": path.split("/")[3], "before_powered": True, "after_powered": True,
                "removed": [{"asset_ref": ref, "own_harvest_pp": 0, "own_construction_pp": 0, "own_base_pp": 0} for ref in refs],
                "plot_residual_delta": 0, "plot_ability_delta": 0, "harvest_pp_delta": 0,
                "own_construction_pp_delta": {"status": "point", "value": 0, "low": 0, "high": 0},
                "own_base_pp_delta": {"status": "point", "value": 0, "low": 0, "high": 0},
                "notices": [],
            })
        elif "upgrade" in path:
            await self.gates["upgrade"].wait()
            await route.fulfill(json={"options":[], "candidates":[]})
        elif path.startswith("/api/"):
            payload = {"state":"done"} if "status" in path else {"started":True} if "refresh" in path else {"cards":[], "receipts":[], "candidates":[], "options":[]}
            await route.fulfill(status=404 if path == "/api/dev/watch" else 200, json=payload)
        elif request.resource_type == "image":
            scene = "terrain-backgrounds/" in path or "productionBuildingUniform/" in path
            portrait = "cloudfront" in request.url or "/cards/" in path
            if scene or portrait:
                self.active += 1
                self.peak = max(self.peak, self.active)
                self.scene_requests += int(scene)
                try:
                    await self.gates["scene" if scene else "portrait"].wait()
                    primary = self.fail_primary(path) if callable(self.fail_primary) else (self.fail_primary and "eos-skin" in path)
                    if self.fail_art or primary:
                        await route.fulfill(status=404, body="controlled image failure")
                    else:
                        await route.fulfill(content_type="image/svg+xml", body=ART)
                finally:
                    self.active -= 1
            else:
                await route.fulfill(content_type="image/svg+xml", body=ART)
        elif path.startswith("/static/"):
            source = (STATIC / path.removeprefix("/static/")).resolve()
            assert source.is_relative_to(STATIC.resolve()), path
            if source.is_file():
                await route.fulfill(content_type=mimetypes.guess_type(str(source))[0] or "application/octet-stream", body=self.source(str(source.relative_to(STATIC))))
            else:
                await route.fulfill(status=404, body="fixture asset absent")
        else:
            await route.fulfill(status=404, body="unexpected fixture route")

    def source(self, relative):
        if self.baseline and Path(relative).suffix in (".js", ".css", ".html"):
            return subprocess.check_output(["git", "show", f"HEAD:ceres/static/{relative}"], cwd=ROOT)
        source = (STATIC / relative).read_bytes()
        if self.mutation and relative == self.mutation[0]:
            original, replacement = self.mutation[1:]
            assert source.count(original.encode()) == 1, "Mutation symbol moved or is not unique"
            source = source.replace(original.encode(), replacement.encode(), 1)
            self.mutation_hits += 1
        if relative == "table.js":
            symbol = b"function renderVirtualWindow(force = false, anchor = null) {"
            assert source.count(symbol) == 1, "Resolve moved virtualiser symbol before running its guard"
            # `depth` counts renderVirtualWindow frames on the stack: 2+ is a render nested
            # inside another one (the height-correction re-render).
            source = source.replace(symbol, symbol + b"\nwindow.eosProbe.virtualRenders.push({force,epoch:anchor?.viewportEpoch,t:performance.now(),depth:(new Error().stack.match(/renderVirtualWindow/g)||[]).length});", 1)
            source += b"\nexport { renderVirtualWindow as eosGuardRenderVirtualWindow };\n"
        return source

    async def start(self, *, width=1920, font=16, reduced=False):
        await self.page.set_viewport_size({"width":width, "height":1080})
        await self.page.emulate_media(reduced_motion="reduce" if reduced else "no-preference")
        await self.page.add_init_script(PROBE)
        await self.page.add_init_script(f"document.addEventListener('DOMContentLoaded',()=>document.documentElement.style.setProperty('font-size','{font}px','important'))")
        await self.page.route("**/*", self.route)
        # A bare address opens the start page; the board needs its account in the URL.
        await self.page.goto(f"{ORIGIN}/?player={self.board['player']}", wait_until="domcontentloaded")
        # The desktop table's rows, or the phone card view's plot cards (e <= 1279).
        await self.page.wait_for_selector("tr.plot-row, article.plot-card", timeout=15000)
        assert await self.page.evaluate("getComputedStyle(document.documentElement).fontSize") == f"{font}px"

    async def module(self, name, expression, arg=None):
        return await self.page.evaluate("""async ({name, expression, arg}) => {
          const entries=performance.getEntriesByType('resource').map(e=>e.name);
          const loaded=entries.find(url=>new URL(url).pathname===`/static/${name}.js`);
          if (!loaded) throw new Error(`Required actually-loaded module missing: ${name}`);
          const module=await import(loaded);
          return await (new Function('module','arg',`return (${expression})(module,arg)`))(module,arg);
        }""", {"name":name,"expression":expression,"arg":arg})

    async def open(self, uid="eos-0008"):
        selector = f'tr.plot-row[data-deed-uid="{uid}"]'
        await self.page.locator(selector).scroll_into_view_if_needed()
        await self.page.evaluate("uid=>{eosProbe.uid=uid;eosProbe.frames=[];eosProbe.opened=performance.now();eosProbe.focus=document.activeElement;}", uid)
        # blocker 1. This used to click 20px in from the row's left edge, which
        # lands on the FIRST cell — the territory cell, which carries
        # `data-tip-kind="explain"`. The context runs `has_touch=True`, so
        # `matchMedia('(hover: none)')` is true, so tooltip.js's capture-phase document
        # listener called stopPropagation() and rows.js's bubble handler never fired.
        # toggleRow() never ran, the band was never emitted, and all 85 tests died at a
        # SETUP assertion before any guard reached an assertion of its own.
        # td:nth-child(6) is `col-num`, which carries no tooltip attribute, so the click
        # reaches the row. Diagnosed and written by the SPL XL session's asclepius queue and
        # reproduced deterministically in both directions there; landed here under
        # which makes harness repair the Dark Factory's rather than the blocked session's.
        await self.page.locator(f'{selector} > td:nth-child(6)').click()
        snapshot = await self.page.evaluate("""uid=>{
          const band=document.querySelector(`tr.expand-row[data-deed-uid="${uid}"] .editor-band`);
          if(!band) throw new Error('Reservation must exist synchronously after explicit click');
          const rect=band.getBoundingClientRect();
          const height=(EXPECTED_BAND)(band), extent=Math.max(0,document.scrollingElement.scrollHeight-innerHeight);
          // An opening plot's ROW lands directly under the sticky header line (eos.js
          // centreReservedPlot; it no longer centres the band in the window).
          const plotRow=band.closest('tr.expand-row').previousElementSibling;
          const headerLine=Math.max(0,...['header.board-header','#table-head-sticky'].map(s=>document.querySelector(s)?.getBoundingClientRect().bottom||0));
          return {height, actual:rect.height, top:rect.top+scrollY, x:scrollX, headerLine,
            target:Math.max(0,Math.min(extent,scrollY+plotRow.getBoundingClientRect().top-headerLine)),extent,
            focusUnchanged:document.activeElement===eosProbe.focus};
        }""".replace("EXPECTED_BAND", EXPECTED_BAND), uid)
        assert abs(snapshot["actual"] - snapshot["height"]) <= 0.5, snapshot
        assert snapshot["focusUnchanged"]
        self.reserved = snapshot["height"]
        return snapshot

    async def expected_band(self, uid="eos-0008"):
        return await self.band(uid).evaluate(f"band=>({EXPECTED_BAND})(band)")

    def band(self, uid="eos-0008"):
        return self.page.locator(f'tr.expand-row[data-deed-uid="{uid}"] .editor-band')

    async def terminal(self, uid="eos-0008"):
        await self.page.wait_for_function("uid=>/^(ready|degraded)$/.test(document.querySelector(`tr.expand-row[data-deed-uid=\"${uid}\"] .editor-band`)?.dataset.plotState)", arg=uid, timeout=8000)

    async def screenshot(self, name):
        """Capture without waiting on fonts while the art gates are deliberately held.

        . `page.screenshot()` waits for `document.fonts.ready`, and that promise does
        not settle while the document is held at `readyState=interactive` — which is exactly
        the state these guards exist to create. Every guard that screenshots under a held
        gate died at `Timeout 30000ms exceeded — waiting for fonts to load`, so R-138's
        nineteen guards have never executed a single assertion since `68341bb9`.

        This is NOT a control whose input surface is too narrow. It is a control that is
        correct in general and wrong for the one condition the fixture exists to create: a
        wait whose correctness assumes a settled document, deliberately broken by the gates
        under test.

        THE FIX MUST NOT BE TO OPEN THE GATES. That would make nineteen guards pass by
        removing the condition they test — verification theatre by construction.

        Ceres now ships one local web face (badge-face.css, vendored Inter), so
        `document.fonts.size` is no longer 0 and the old "no fonts, skip the wait" check
        sent every capture back to `page.screenshot()` and its 30 s fonts timeout. The
        no-unstyled-text property is kept per face instead: after a forced layout, every
        face the page actually uses has left `unloaded`, and the capture first awaits each
        such face's own `loaded` promise (a face that fails to load fails the guard). That
        settles exactly the fonts on screen and never waits on the held document.
        """
        path = self.artifact / f"{name}.png"
        # Ceres now ships one local web face (badge-face.css, vendored Inter). The
        # no-unstyled-text property is kept per face instead of through
        # `document.fonts.ready`: after a forced layout every face the page actually
        # uses has left `unloaded`, so waiting on each such face's own `loaded`
        # promise settles exactly the fonts on screen and never waits on the held
        # document. A face that fails to load fails the guard.
        await self.page.evaluate("""async () => {
          document.body.getBoundingClientRect();
          const used=[...document.fonts].filter(face=>face.status!=='unloaded');
          await Promise.all(used.map(face=>face.loaded));
        }""")
        if self._cdp is None:
            self._cdp = await self.page.context.new_cdp_session(self.page)
        shot = await self._cdp.send("Page.captureScreenshot", {"format": "png"})
        path.write_bytes(base64.b64decode(shot["data"]))

    async def filmstrip(self, name, duration=0.5):
        deadline = asyncio.get_running_loop().time() + duration
        index = 0
        while asyncio.get_running_loop().time() < deadline:
            await self.screenshot(f"{name}-{index:03d}")
            index += 1
            await asyncio.sleep(0.035)

    async def trace(self):
        return await self.page.evaluate("""() => ({
          opened:eosProbe.opened,writes:eosProbe.writes, reads:eosProbe.reads, frames:eosProbe.frames,longTasks:eosProbe.longTasks,virtualRenders:eosProbe.virtualRenders,wholesale:eosProbe.wholesale,
          animations:eosProbe.animations.map(a=>({start:a.start,frames:a.frames,options:a.options,connected:a.node.isConnected,state:a.animation.playState,cls:a.node.className,group:a.node.dataset.revealGroup})),
          decodes:eosProbe.decodes.map(d=>({src:d.src,connected:d.connected,managed:d.managed,started:d.started,done:d.done}))
        })""")


@asynccontextmanager
async def _browser(board, details, artifact, *, baseline=False, mutation=None, **options):
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            context = await browser.new_context(has_touch=True)
            page = await context.new_page()
            fixture = EosBrowser(page, copy.deepcopy(board), copy.deepcopy(details), artifact, baseline=baseline, mutation=mutation)
            try:
                await fixture.start(**options)
                yield fixture
            finally:
                try:
                    trace = await fixture.trace()
                    (artifact / "trace.json").write_text(json.dumps(trace, indent=2))
                    (artifact / "requests.json").write_text(json.dumps(fixture.requests, indent=2))
                    (artifact / "page-errors.json").write_text(json.dumps(fixture.errors, indent=2))
                finally:
                    for gate in fixture.gates.values():
                        gate.set()
                    await context.close()
                    await browser.close()
    except Exception as error:
        raise AssertionError(f"R-138 browser guard did not complete; capability/setup/guard failure is NOT a skip: {error}") from error


def _stable(trace):
    frames = trace["frames"]
    assert len(frames) > 4, "No multi-frame geometry evidence"
    reference = frames[0]
    for frame in frames:
        for key in ("bandHeight", "tdHeight", "bandTop", "nextTop"):
            if frame[key] is not None and reference[key] is not None:
                assert abs(frame[key] - reference[key]) <= 0.5, (key, reference, frame)


async def _staged_removal(f):
    """A removal is staged only after its impact is priced (an async request), so wait
    for the removed tile's restore control instead of reading the stage at once."""
    await f.band().locator('[data-slot-action="restore"][data-group-id="worker"]').first.wait_for(state="attached")


async def _no_new_gesture(fixture, action):
    before = await fixture.trace()
    await action()
    await asyncio.sleep(0.45)
    after = await fixture.trace()
    assert len(after["animations"]) == len(before["animations"]), "Non-opening action replayed entrance"
    opening_writes = lambda trace:[w for w in trace["writes"] if "/eos.js" in w["stack"]]
    assert len(opening_writes(after)) == len(opening_writes(before)), "Non-opening action recentred"


async def _fit(fixture, *, strict=True, baseline=None):
    audit = await fixture.page.evaluate("""() => {
      const width=document.documentElement.clientWidth;
      const bands=[...document.querySelectorAll('tr.expand-row:not([style*="none"]) .editor-band')];
      const overs=[];
      for(const band of bands) for(const node of [band,...band.querySelectorAll('*')]) {
        const r=node.getBoundingClientRect(),s=getComputedStyle(node);
        if(!r.width||!r.height||s.display==='none'||s.visibility==='hidden') continue;
        if(r.right>width+.5 || r.left<-.5) overs.push({cls:node.className,right:r.right,left:r.left});
        if((s.overflowX==='auto'||s.overflowX==='scroll')&&node.scrollWidth>node.clientWidth+.5)
          overs.push({cls:node.className,scroll:node.scrollWidth});
      }
      return {width,scrollWidth:document.documentElement.scrollWidth,overs};
    }""")
    # The table scroller clips on x, so anything wider than it is hidden -- except the
    # desktop table's deliberate 8px right-edge budget (ceres.css: the table is
    # `calc(100% + 8.5px)` wide, and the overhang lies in the last column's end padding
    # tests/ceres/table_edge.py). Before any plot opens at 1920/16px that is the measured
    # 1918.5px table in its 1910px scroller.
    audit["table"] = await fixture.page.evaluate(OVERHANG_JS)
    audit["tableProblems"] = overhang_problems(audit["table"])
    assert not audit["overs"], audit
    if strict:
        assert audit["scrollWidth"] <= audit["width"] and not audit["tableProblems"], audit
    elif baseline:
        assert audit["scrollWidth"] <= baseline["scrollWidth"] + 0.5, (baseline, audit)
    return audit


async def _g1(f):
    await f.open()
    await f.screenshot("reserved")
    f.release("scene")
    await asyncio.sleep(0.45)
    await f.screenshot("scene-without-detail")
    f.release("detail", "portrait", "upgrade")
    await f.terminal()
    await f.filmstrip("ready")
    _stable(await f.trace())
    measurements = await f.band().evaluate("""band=>{
      const r=band.getBoundingClientRect();
      const regions=['.slot-group-controls','.slot-grid','.slot-editor-footer'];
      const container=band.closest('.slot-editor'), cs=getComputedStyle(container);
      return {band:r.height,td:band.closest('td').getBoundingClientRect().height,
        factsInset:band.querySelector('.slot-editor-body').getBoundingClientRect().top-r.top,
        sceneUnit:(container.clientWidth-parseFloat(cs.paddingLeft)-parseFloat(cs.paddingRight))/1920,
        tdBorder:getComputedStyle(band.closest('td')).borderBottomWidth,
        regions:regions.map(selector=>{const n=band.querySelector(selector);return {selector,height:n?.getBoundingClientRect().height??null}}),
        satellites:[...band.querySelectorAll('.power-satellite')].map(n=>{const b=n.getBoundingClientRect();return {top:b.top-r.top,bottom:b.bottom-r.top}})};
    }""")
    measurements["expected"] = await f.expected_band()
    measurements["reserved"] = f.reserved
    # The band settles at the height reserved synchronously on open, and that height is
    # the scene-scaled sum of its own terms (EXPECTED_BAND), not a fixed pixel count.
    assert abs(measurements["band"] - measurements["reserved"]) <= 0.5, measurements
    assert abs(measurements["band"] - measurements["expected"]) <= 0.5, measurements
    # The facts line is gone (editor.js renders `facts = ''`); its term survives as the
    # body's top inset. The band is still fully partitioned: inset + controls + cards +
    # footer + 21 scene units, measured from the rendered regions.
    assert all(region["height"] is not None for region in measurements["regions"]), measurements
    partition = sum(region["height"] for region in measurements["regions"]) + measurements["factsInset"] + 21 * measurements["sceneUnit"]
    assert abs(partition - measurements["band"]) <= 0.5, (partition, measurements)
    assert all(0 <= s["top"] <= s["bottom"] <= measurements["band"] + 0.5 for s in measurements["satellites"]), measurements
    (f.artifact / "measured-band-arithmetic.json").write_text(json.dumps(measurements, indent=2))


async def _page_all_slots(f, capacity):
    seen = set()
    units = set()
    for _ in range(60):
        current = await f.band().locator('[data-group-id="worker"][data-slot-index]').evaluate_all("nodes=>nodes.filter(n=>n.getBoundingClientRect().width>0).map(n=>Number(n.dataset.slotIndex))")
        seen.update(current)
        units.update(await f.band().locator('[data-occupied="1"][data-group-id],.power-satellite[data-group-id]').evaluate_all("nodes=>nodes.filter(n=>n.getBoundingClientRect().width>0).map(n=>n.dataset.groupId)"))
        next_page = f.band().get_by_role("button", name="Next", exact=False)
        if not await next_page.count() or await next_page.first.is_disabled():
            break
        await next_page.first.click()
        await asyncio.sleep(0.08)
        await _fit(f, strict=False)
    # A zero-capacity plot still renders the default five addable worker tiles
    # (editor.js editorGroups: slotFloor = capacity > 0 ? capacity : DEFAULT_WORKER_SLOTS).
    assert seen == set(range(capacity or 5)), (capacity, seen)
    if capacity:
        assert {"worker","power_core","totem","title"} <= units, (capacity,units)


async def _g2(f):
    baseline = await _fit(f, strict=False)
    await f.open()
    await _fit(f, strict=False, baseline=baseline)
    f.release("scene", "detail", "portrait", "upgrade")
    await f.terminal()
    width = await f.page.evaluate("innerWidth")
    font = await f.page.evaluate("getComputedStyle(document.documentElement).fontSize")
    await _fit(f, strict=width == 1920 and font == "16px", baseline=baseline)
    await _page_all_slots(f, f.details["eos-0008"]["capacity"]["potential_card_capacity"])
    await f.screenshot("all-tracks-and-last-page")


async def _g3(f):
    # The boot loading seal (index.html `.harvest-scene`) is a fixed piece of terrain art
    # shown before any plot exists; it is page chrome, not a plot reveal, so its own
    # static sources are read from the page and kept out of the reveal ledger.
    boot = set(await f.page.evaluate("()=>[...document.querySelectorAll('.harvest-scene img')].map(n=>new URL(n.getAttribute('src'),location.href).pathname)"))
    assert boot, "boot seal art moved; re-check which requests are page chrome"
    def reveal_requests():
        return [r for r in f.requests if r["path"] not in boot and ("terrain-backgrounds" in r["path"] or "productionBuildingUniform" in r["path"] or "Eos%20Worker" in r["path"] or "Eos Worker" in r["path"])]
    await asyncio.sleep(0.1)
    assert not reveal_requests(), reveal_requests()
    await f.module("table", "m=>m.render()")
    await asyncio.sleep(0.1)
    assert not reveal_requests(), reveal_requests()
    await f.open()
    await asyncio.sleep(0.1)
    assert [r for r in reveal_requests() if "terrain-backgrounds" in r["path"] or "productionBuildingUniform" in r["path"]], f.requests
    assert len({r["path"] for r in reveal_requests()}) <= 2, reveal_requests()
    assert not [r for r in f.requests if "/api/plots/" in r["path"] and "eos-0008" not in r["path"]]


async def _g4(f):
    await f.open()
    await f.filmstrip("initial-veil", 0.12)
    f.release("scene")
    await asyncio.sleep(0.4)
    # The opaque `.eos-worker-cover` is retired: mountPlotPresentation hides it on every
    # mount (pinned by test_eos_p5_contract). Worker tiles are now permanent frames, and
    # the workers' negative space is the portraits themselves: none exists while the
    # detail is held (inert, empty scaffold tiles), and each managed portrait stays
    # unpainted (opacity 0 / hidden) until its portrait request is released.
    state = await f.band().evaluate("""band=>{
      const scene=band.querySelector('.editor-scene'),veil=band.querySelector('.eos-scenic-veil'),cover=band.querySelector('.eos-worker-cover');
      const s=getComputedStyle(scene),v=veil&&getComputedStyle(veil);
      const cells=[...band.querySelectorAll('.slot-tile-cell')];
      return {background:s.backgroundImage,terrain:s.getPropertyValue('--scene-terrain'),veil:veil?.hidden?'0':v?.opacity??'0',
        coverShown:!!cover&&getComputedStyle(cover).display!=='none',cells:cells.length,
        live:cells.filter(n=>!n.inert||n.querySelector('img')).length};
    }""")
    assert state["veil"] == "0", state
    assert "url(" in state["background"] or "url(" in state["terrain"], state
    assert not state["coverShown"], state
    assert state["cells"] > 0 and state["live"] == 0, state
    await f.screenshot("scene-visible-worker-negative-space")
    f.release("detail")
    portraits = f.band().locator(".slot-tile-cell img.eos-portrait-fill")
    await portraits.first.wait_for(state="attached")
    await asyncio.sleep(0.1)
    painted = await portraits.evaluate_all("nodes=>nodes.filter(n=>{const s=getComputedStyle(n);return s.visibility!=='hidden'&&Number(s.opacity)>0}).length")
    assert painted == 0, "Held portraits are painted before their release"
    portrait = portraits.first
    box = await portrait.evaluate("n=>{const r=n.closest('.slot-tile').getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height}}")
    assert box and box["width"] > 20, box
    clip = {"x":box["x"]+box["width"]*.5-5,"y":box["y"]+box["height"]*.5-5,"width":10,"height":10}
    await portrait.evaluate("n=>n.style.background='rgb(255,0,255)'")
    pink = Image.open(io.BytesIO(await f.page.screenshot(clip=clip))).convert("RGB").tobytes()
    await portrait.evaluate("n=>n.style.background='rgb(0,255,0)'")
    green = Image.open(io.BytesIO(await f.page.screenshot(clip=clip))).convert("RGB").tobytes()
    assert pink == green, "Held portrait leaks changing pixels"
    await portrait.evaluate("n=>n.style.background=''")
    await f.filmstrip("portraits-held", 0.2)


async def _g5(f):
    # A skinned worker's candidates are now only that skin's URLs (editorCardImageHtml
    # filters to the skin; there is no fall back to the unskinned card). The primary
    # candidate (the skin's .jpg) fails; the next candidate (the same skin's .png)
    # must be the one decoded and shown.
    primary = lambda path: "/eos-skin/" in path and urlparse(path).path.endswith(".jpg")
    f.fail_primary = primary
    await f.page.evaluate("eosProbe.blockDecode=true")
    f.release("scene", "portrait", "detail", "upgrade")
    await f.open()
    await f.page.wait_for_function("eosProbe.pending.length > 0")
    decoded = await f.trace()
    assert any(d["managed"] and d["connected"] and d["done"] is None for d in decoded["decodes"])
    # The opaque worker cover is retired (see G4); a portrait whose decode is held is unpainted.
    assert await f.band().locator(".slot-tile-cell img.eos-portrait-fill").evaluate_all(
        "nodes=>nodes.filter(n=>{const s=getComputedStyle(n);return s.visibility!=='hidden'&&Number(s.opacity)>0}).length") == 0
    assert await f.band().locator("img[data-eos-managed]").count() > 0
    assert any(primary(r["path"]) for r in f.requests)
    await f.page.evaluate("eosProbe.releaseDecode()")
    await f.terminal()
    actual = await f.band().locator("img[data-eos-managed]").evaluate_all("nodes=>nodes.map(n=>({src:n.currentSrc,decoded:eosProbe.decodes.some(d=>d.node===n&&d.done!==null&&d.src===n.currentSrc)}))")
    assert actual and all(item["decoded"] and not primary(item["src"]) for item in actual), actual
    assert [item for item in actual if "/eos-skin/" in item["src"]], actual
    await _page_all_slots(f, 5)
    await f.page.wait_for_function("[...document.querySelectorAll('img[data-eos-managed]')].filter(n=>n.closest('.editor-band')).every(n=>n.dataset.eosOutcome==='decoded')")
    await f.screenshot("selected-dom-candidate-decoded")


async def _g6(f):
    f.release("detail", "upgrade")
    await f.open()
    await f.terminal()
    assert "Art unavailable" in await f.band().inner_text()
    assert "Eos Worker 1" in await f.band().inner_text()
    assert "unavailable" in (await f.band().locator('[role="status"]').inner_text()).lower()
    assert await f.band().evaluate("b=>!getComputedStyle(b).getPropertyValue('--scene-terrain').includes('url(')")
    before = await f.band().inner_html()
    data_requests = len([r for r in f.requests if "/api/plots/" in r["path"]])
    # The "Retry art" button is retired (eos.js: "gone at his instruction"; art recovery runs
    # on its own bounded schedule and the status line says when art is unavailable), so the
    # late-asset step is the one non-opening action left: the assets that finally answer
    # must neither change the frozen fallback, replay the entrance, recentre, nor re-read
    # the plot's data.
    assert not await f.band().get_by_role("button", name="Retry art", exact=False).count()
    async def late_assets():
        f.release("scene", "portrait")
        await asyncio.sleep(0.4)
        assert await f.band().inner_html() == before, "Late asset changed frozen fallback"
    await _no_new_gesture(f, late_assets)
    await f.screenshot("frozen-named-fallback")
    assert len([r for r in f.requests if "/api/plots/" in r["path"]]) == data_requests
    _stable(await f.trace())


async def _g7(f):
    f.release("scene", "portrait", "detail", "upgrade")
    await f.open()
    await f.terminal()
    await asyncio.sleep(0.45)
    before = await f.trace()
    await f.page.locator('tr.plot-row[data-deed-uid="eos-0008"]').click()
    await f.open()
    await f.filmstrip("warm-reopen", 0.55)
    await f.terminal()
    trace = await f.trace()
    fresh = trace["animations"][len(before["animations"]):]
    assert fresh, "Explicit reopen was lifetime-banned from entrance"
    assert all(a["start"] - min(x["start"] for x in fresh) < 200 for a in fresh)
    assert not await f.band().locator(".expand-loading,.skeleton,[class*='skeleton']").count()
    assert not [a for a in fresh if a["options"].get("duration", 0) >= 900]
    assert len([a for a in fresh if a["options"].get("duration") == 300]) <= 1


async def _g8(f):
    f.release("scene", "portrait", "detail")
    await f.page.evaluate("eosProbe.blockDecode=true")
    await f.open()
    await f.page.wait_for_function("eosProbe.pending.length>0")
    await f.page.evaluate("eosProbe.oldNodes=[...document.querySelectorAll('img[data-eos-managed]')];eosProbe.oldResolves=eosProbe.pending.splice(0)")
    await f.module("rows", "(m,uid)=>m.updateMountedRow(uid)", "eos-0008")
    await f.page.wait_for_function("eosProbe.pending.length>0")
    # A remount no longer replaces managed nodes: the patch keeps every in-flight portrait
    # node (rows.js morph keeps a picture of the same card), so the old replace-node
    # scenario cannot be built. The guard's property is that a decode started before the
    # remount cannot settle what the remount shows: the kept nodes get a fresh decode (the
    # wait above), and releasing the superseded decodes reveals nothing -- no portrait is
    # settled or painted (the opaque worker cover that used to prove it is retired, see G4).
    kept = await f.page.evaluate("""()=>{const now=[...document.querySelectorAll('img[data-eos-managed]')];
      return {old:eosProbe.oldNodes.length,connected:eosProbe.oldNodes.filter(n=>n.isConnected).length,now:now.length,
        same:now.filter(n=>eosProbe.oldNodes.includes(n)).length}}""")
    assert kept["old"] and kept["connected"] == kept["old"] == kept["now"] == kept["same"], kept
    await f.page.evaluate("eosProbe.oldResolves.forEach(done=>done())")
    await asyncio.sleep(0.05)
    stale = await f.band().evaluate("""b=>{const images=[...b.querySelectorAll('img[data-eos-managed]')];
      return {settled:images.filter(n=>n.dataset.eosOutcome==='decoded').length,
        painted:images.filter(n=>n.matches('.eos-portrait-fill')).filter(n=>{const s=getComputedStyle(n);return s.visibility!=='hidden'&&Number(s.opacity)>0}).length}}""")
    assert stale == {"settled": 0, "painted": 0}, "Superseded decode settled the remounted plot"
    await f.page.evaluate("eosProbe.releaseDecode()")
    await f.page.wait_for_function("eosProbe.animations.some(a=>a.animation.playState==='running')")
    f.release("upgrade")
    remove = f.band().locator('[data-slot-action="remove"][data-group-id="worker"]').first
    assert await remove.count(), "Representative fixture must expose a real stage control"
    await remove.click(force=True)
    await _staged_removal(f)
    staged = await f.module("staging", "m=>m.CERES_STAGE.length")
    assert staged == 1
    await f.terminal()
    async def restore():
        await f.band().locator('[data-slot-action="restore"][data-group-id="worker"]').first.click()
    await _no_new_gesture(f, restore)
    assert await f.module("staging", "m=>m.CERES_STAGE.length") == 0
    assert not await f.band().evaluate("b=>b.getAnimations({subtree:true}).some(a=>a.playState==='running')")


async def _g9(f):
    await f.open()
    await f.page.locator('tr.plot-row[data-deed-uid="eos-0008"]').click()
    await f.open()
    assert len([r for r in f.requests if "/api/plots/eos-0008" in r["path"]]) == 1, "Reopen duplicated in-flight detail"
    old_gate = f.gates["detail"]
    f.gates["detail"] = asyncio.Event()
    # A non-quiet loadBoard now closes every open plot on purpose (board.js: "All plots
    # should load closed"); the same-page board re-read that keeps open plots is the
    # quiet one, which is the generation change this guard is about.
    await f.module("board", "m=>{m.loadBoard({quiet:true});return true}")
    await f.page.wait_for_function("document.querySelector('.editor-band[data-plot-state=preparing]')!==null")
    old_gate.set()
    f.release("scene", "portrait")
    await asyncio.sleep(0.45)
    assert await f.module("rows", "m=>m.LOADING_PLOTS.has('eos-0008')"), "Old cleanup cleared current generation loading"
    assert await f.band().get_attribute("data-plot-state") not in ("ready", "degraded")
    f.release("detail", "upgrade")
    await f.terminal()
    assert "Eos Worker 1" in await f.band().inner_text()


async def _g10(f):
    f.release("scene", "portrait", "detail", "upgrade")
    await f.open()
    await f.terminal()
    await asyncio.sleep(0.45)
    async def remount():
        await f.page.evaluate("window.scrollTo({top:document.scrollingElement.scrollHeight,behavior:'instant'})")
        await f.page.wait_for_function("!document.querySelector('tr.plot-row[data-deed-uid=\"eos-0008\"]')")
        await f.page.evaluate("window.scrollTo({top:0,behavior:'instant'})")
        await f.page.wait_for_selector('tr.plot-row[data-deed-uid="eos-0008"]')
    await _no_new_gesture(f, remount)
    assert "Eos Worker 1" in await f.band().inner_text()
    await _no_new_gesture(f, lambda:f.module("table", "m=>m.render()"))
    await _no_new_gesture(f, lambda:f.module("board", "m=>m.loadBoard({quiet:true})"))  # see G9
    assert "Eos Worker 1" in await f.band().inner_text()
    assert not [r for r in f.requests if "/api/plots/" in r["path"] and "eos-0008" not in r["path"]]


async def _g11(f):
    target = await f.open()
    assert abs(await f.page.evaluate("scrollY") - target["target"]) <= 0.5
    f.release("scene", "detail", "portrait", "upgrade")
    await f.terminal()
    trace = await f.trace()
    assert not trace["animations"], trace["animations"]
    assert all("smooth" not in json.dumps(w["args"]) for w in trace["writes"])
    await f.page.locator('tr.plot-row[data-deed-uid="eos-0008"]').click()
    await f.page.emulate_media(reduced_motion="no-preference")
    target = await f.open()
    await f.page.emulate_media(reduced_motion="reduce")
    await asyncio.sleep(0.05)
    assert abs(await f.page.evaluate("scrollY") - target["target"]) <= 0.5
    assert not await f.band().evaluate("b=>b.getAnimations({subtree:true}).some(a=>a.playState==='running')")


async def _g12(f):
    f.release("scene", "detail", "portrait", "upgrade")
    await f.open()
    await f.terminal()
    capacity = f.details["eos-0008"]["capacity"]["potential_card_capacity"]
    await _page_all_slots(f, capacity)
    assert not [r for r in f.requests if "Eos Worker" in r["path"]], "Text-only or absent-art workers fetched portraits"


async def _g13(f):
    f.release("scene", "detail", "portrait", "upgrade")
    await f.open()
    scene = lambda:f.band().locator(".editor-scene").evaluate("n=>{const s=getComputedStyle(n,'::before');return {size:s.backgroundSize,position:s.backgroundPosition,y:s.getPropertyValue('--scene-y'),height:n.getBoundingClientRect().height}}")
    initial = await scene()
    await f.terminal()
    await asyncio.sleep(0.4)
    assert await scene() == initial
    tile = f.band().locator('[data-slot-action="choose"], [data-slot-action="add"], [data-slot-action="replace"]').first
    assert await tile.count(), "Fixture must expose a picker control"
    await tile.click()
    await asyncio.sleep(0.15)
    assert await scene() == initial, "Picker recropped the worksite"
    assert await f.band().evaluate("n=>getComputedStyle(n).overflowY") == "visible"
    assert await f.band().evaluate("n=>[...n.querySelectorAll('.eos-scenic-veil,.eos-worker-cover')].every(c=>getComputedStyle(c).pointerEvents==='none')")
    await f.screenshot("picker-preserves-worksite-groundline")


async def _g14(f, mutation=None):
    f.release("scene", "detail", "portrait", "upgrade")
    await f.open()
    await f.terminal()
    remove = f.band().locator('[data-slot-action="remove"][data-group-id="worker"]').first
    await remove.click()
    await _staged_removal(f)
    await f.module("staging", """(m,notices)=>{
      if(m.CERES_STAGE.length!==1)throw new Error('Expected one genuinely staged removal');
      m.CERES_STAGE[0]._notices=notices;
    }""", f.details["eos-0008"]["notices"])
    await f.module("rows", "m=>m.updateMountedRow('eos-0008')")
    if mutation:
        assert await f.page.evaluate(mutation), "G14 named disclosure mutation landed"
    before = await f.band().evaluate("b=>b.getBoundingClientRect().height")
    assert await f.page.locator('[data-eos-details], .eos-details').count() == 0, "G14 unrequested disclosure is absent"
    # The plot-facts line is gone (editor.js renders facts = ''); the plot's staged-change
    # facts are the change badges in the worker controls line, shown exactly once.
    assert await f.page.locator('.slot-editor .editor-change-badges').count() == 1, "G14 plot facts are not duplicated"
    for i in range(3):
        action = f.page.get_by_role("button", name=f"Inspect companion {i}", exact=True)
        assert await action.count() == 1 and await action.is_visible(), "G14 unique notice actions survive without disclosure"
        await action.wait_for(state="visible")
        await action.focus()
        assert await action.evaluate("n=>document.activeElement===n")
        assert f"Notice {i}:" in await f.page.locator("body").inner_text()
    assert await f.band().evaluate("b=>b.getBoundingClientRect().height") == before
    assert await f.band().evaluate("b=>!b.contains(document.querySelector('[data-notice-action]'))")
    await f.screenshot("notice-actions-without-disclosure")


@pytest.mark.parametrize("mutation,script,message", [
    ("restore_details", "()=>{const el=document.createElement('button');el.dataset.eosDetails='';el.textContent='Details';document.querySelector('.slot-editor-footer').append(el);return !!document.querySelector('[data-eos-details]');}", "G14 unrequested disclosure is absent"),
    ("restore_disclosure_panel", "()=>{const el=document.createElement('div');el.className='eos-details';document.querySelector('.slot-editor').append(el);return !!document.querySelector('.eos-details');}", "G14 unrequested disclosure is absent"),
    ("duplicate_plot_facts", "()=>{const el=document.querySelector('.slot-editor .editor-change-badges');el.parentElement.append(el.cloneNode(true));return document.querySelectorAll('.slot-editor .editor-change-badges').length===2;}", "G14 plot facts are not duplicated"),
    ("drop_notice_action", "()=>{const el=[...document.querySelectorAll('[data-notice-action]')].find(n=>n.textContent.includes('Inspect companion 0'));if(!el)return false;el.remove();return ![...document.querySelectorAll('[data-notice-action]')].some(n=>n.textContent.includes('Inspect companion 0'));}", "G14 unique notice actions survive without disclosure"),
])
def test_g14_disclosure_corrections_reject_named_mutations(mutation, script, message, report, details_by_id, tmp_path):
    board, details = _payload(report, details_by_id, long=True)

    async def run():
        async with _browser(board, details, tmp_path / f"G14-{mutation}") as f:
            with pytest.raises(AssertionError, match=message):
                await _g14(f, mutation=script)

    asyncio.run(run())


@pytest.mark.parametrize("mutation", [None, "hide_narrow_multiplier", "hide_narrow_cost"])
def test_g14_narrow_facts_remain_accessible_without_disclosure(mutation, report, details_by_id, tmp_path):
    board, details = _payload(report, details_by_id, long=True)
    details['eos-0008'].update(multiplier_parts={'totem_boost': .1}, grain_cost_per_hour=42)

    # The narrow band profile (width < 62em, eos.js plotGeometryProfile) and the desktop table
    # (e > 1279px, i.e. > 80em) no longer overlap: a narrow plot is the phone card view's plot
    # sheet, by design. The plot-facts line is gone there too (editor.js renders facts = ''); the
    # sheet carries the same two facts itself -- the production modifiers in its Total PP stat
    # (named by the stat's accessible text) and the food cost in its Resource flow block. They
    # must stay visible and exposed to assistive technology without any disclosure.
    multiplier, cost = ".mobile-plot-sheet .mobile-plot-total-pp", ".mobile-plot-sheet .mobile-plot-resource-flow"

    async def run():
        async with _browser(board, details, tmp_path / f"G14-{mutation or 'narrow-facts'}", width=850) as f:
            f.release("scene", "detail", "portrait", "upgrade")
            assert await f.page.evaluate("document.documentElement.classList.contains('ceres-lw-le-1279')"), "850px is the phone card view"
            card = f.page.locator('article.plot-card[data-deed-uid="eos-0008"]')
            await card.scroll_into_view_if_needed()
            await card.click()
            await f.page.wait_for_function("()=>/^(ready|degraded)$/.test(document.querySelector('.mobile-plot-sheet .editor-band')?.dataset.plotState)", timeout=8000)
            facts = f.page.locator(f"{multiplier}, {cost}")
            assert await facts.count() == 2, "G14 narrow fixture carries multiplier and cost"
            assert "Food/hr" in await f.page.locator(cost).inner_text() and "42" in await f.page.locator(cost).inner_text()
            if mutation:
                selector = multiplier if mutation == 'hide_narrow_multiplier' else cost
                await f.page.locator(selector).evaluate("el=>el.style.display='none'")
                assert await f.page.locator(selector).evaluate("el=>getComputedStyle(el).display==='none'"), f"{mutation} landed"

            async def check():
                assert await facts.evaluate_all("els=>els.every(el=>{for(let n=el;n;n=n.parentElement){const s=getComputedStyle(n);if(s.display==='none'||s.visibility==='hidden'||n.inert||n.getAttribute('aria-hidden')==='true')return false;}return !!el.textContent.trim();})"), "G14 narrow facts stay in accessibility tree"
                client = await f.page.context.new_cdp_session(f.page)
                try:
                    tree = await client.send('Accessibility.getFullAXTree')
                    names = [node.get('name', {}).get('value', '') for node in tree['nodes'] if not node.get('ignored')]
                    assert any('Totem: +' in name and 'Production modifiers total' in name for name in names), "G14 narrow multiplier text is exposed to assistive technology"
                    assert any(name.strip() in ('−42', '-42') for name in names), "G14 narrow cost value is exposed to assistive technology"
                finally:
                    await client.detach()

            if mutation:
                with pytest.raises(AssertionError, match="G14 narrow facts stay in accessibility tree"):
                    await check()
            else:
                await check()

    asyncio.run(run())


async def _g15(f):
    async with _browser(f.board, f.details, f.artifact / "same-head-baseline", baseline=True) as baseline:
        await asyncio.sleep(0.35)
        expected = {(r["path"],r["method"]) for r in baseline.requests if r["kind"] in ("fetch","xhr","image")}
        actual = {(r["path"],r["method"]) for r in f.requests if r["kind"] in ("fetch","xhr","image")}
        assert actual <= expected, {"extra_initial_requests":sorted(actual-expected)}
        baseline_trace = await baseline.trace()
        current_trace = await f.trace()
        timings = {"baseline":baseline_trace["longTasks"],"current":current_trace["longTasks"]}
        (f.artifact / "board-task-baseline.json").write_text(json.dumps(timings, indent=2))
        assert not current_trace["animations"] and not current_trace["decodes"]
    await f.open()
    await f.open("eos-0009")
    await f.open("eos-0010")
    await asyncio.sleep(0.1)
    assert 0 < f.peak <= 4, f.peak
    for uid in ("eos-0008", "eos-0009", "eos-0010"):
        await f.page.locator(f'tr.plot-row[data-deed-uid="{uid}"]').click()
    count = len(f.requests)
    f.release("scene", "detail", "portrait", "upgrade")
    await asyncio.sleep(0.5)
    assert not [r for r in f.requests[count:] if r["kind"] == "image"], "Closed queue launched new work"
    trace = await f.trace()
    assert all(a["options"].get("iterations", 1) != "Infinity" for a in trace["animations"])
    assert not await f.page.evaluate("[...document.querySelectorAll('.editor-band *')].some(n=>getComputedStyle(n).willChange!=='auto')")
    assert not [r for r in trace["reads"] if "centre" in r["stack"].lower() and "step" in r["stack"].lower()], trace["reads"]


async def _g16(f):
    target = await f.open()
    for _ in range(20):
        await f.page.keyboard.press("Tab")
        assert not await f.page.evaluate("document.activeElement?.closest('.editor-band [inert]')!==null")
    status = f.band().locator('[role="status"]')
    assert await status.count()
    assert await status.first.get_attribute("aria-live") in ("polite", None)
    close = f.band().get_by_role("button", name="Close", exact=False)
    assert await close.count() and await close.first.is_visible()
    f.fail_detail = True
    f.release("detail")
    await f.page.wait_for_selector('.editor-band[data-plot-state="error"]')
    retry = f.band().get_by_role("button", name="Retry", exact=True)
    if not await retry.count():
        retry = f.band().get_by_role("link", name="Retry", exact=True)
    assert await retry.count()
    await retry.focus()
    f.fail_detail = False
    f.release("scene", "portrait", "upgrade")
    await _no_new_gesture(f, lambda:f.page.keyboard.press("Enter"))
    await f.terminal()
    assert not await f.band().locator('[data-slot-action][inert]').count()
    assert "eos-0008" not in await status.first.inner_text()
    assert target["focusUnchanged"]


async def _g17(f):
    uid = "eos-0000" if len(f.details) == 1 else "eos-0008"
    snapshot = await f.open(uid)
    await f.filmstrip("centre-cold", 0.45)
    assert abs(await f.page.evaluate("scrollY") - snapshot["target"]) <= 0.5, snapshot
    assert await f.page.evaluate("scrollX") == snapshot["x"]
    await asyncio.sleep(0.25)
    assert abs(await f.page.evaluate("scrollY") - snapshot["target"]) <= 0.5
    f.release("scene", "detail", "portrait", "upgrade")
    await f.terminal(uid)
    await asyncio.sleep(0.5)
    assert abs(await f.page.evaluate("scrollY") - snapshot["target"]) <= 0.5
    _stable(await f.trace())
    if snapshot["target"] not in (0, snapshot["extent"]):
        offset = await f.band(uid).evaluate("""b=>{const row=b.closest('tr.expand-row').previousElementSibling;
          const line=Math.max(0,...['header.board-header','#table-head-sticky'].map(s=>document.querySelector(s)?.getBoundingClientRect().bottom||0));
          return row.getBoundingClientRect().top-line}""")
        assert abs(offset) <= 0.5, offset
    (f.artifact / "centre-arithmetic.json").write_text(json.dumps(snapshot, indent=2))


async def _g18(f):
    await f.page.evaluate("""() => {
      const first=document.querySelector('tr.plot-row');
      const height=first.getBoundingClientRect().height,top=first.getBoundingClientRect().top+scrollY;
      window.scrollTo({top:top+height*60,behavior:'instant'});
    }""")
    await asyncio.sleep(0.1)
    uid = await f.page.evaluate("""()=>[...document.querySelectorAll('tr.plot-row')].filter(r=>{
      const b=r.getBoundingClientRect();return b.top>innerHeight*.65 && b.bottom<innerHeight;
    }).at(-1)?.dataset.deedUid""")
    assert uid, "Virtual fixture needs a target near the viewport bottom"
    old = await f.module("table", "m=>m.captureTableAnchor()")
    assert old and "viewportEpoch" in old
    old["uid"] = uid
    old["top"] = (await f.page.locator(f'tr.plot-row[data-deed-uid="{uid}"]').bounding_box())["y"] - 180
    mounted = await f.page.locator("tr.plot-row").evaluate_all("nodes=>nodes.map(n=>n.dataset.deedUid)")
    target = await f.open(uid)
    await f.module("rows", "(m,uid)=>m.EXPANDED_HEIGHTS.set(uid,1)", uid)
    await f.module("table", "(m,anchor)=>m.eosGuardRenderVirtualWindow(true,anchor)", old)
    f.release("scene", "detail", "portrait", "upgrade")
    await f.filmstrip("virtual-window-during-centre", 0.45)
    trace = await f.trace()
    # The forced render above measures the reserved band against the stale cached height
    # and re-renders from inside itself (table.js: `if (rowsChanged || expandedChanged)
    # renderVirtualWindow(false, anchor)`). That nested correction is no longer forced
    # it re-windows only when the window moved -- so it is recognised by its nesting.
    renders = [r for r in trace["virtualRenders"] if r["t"] >= trace["opened"]]
    assert [r for r in renders if r["force"]], "Forced virtual render did not run"
    assert [r for r in renders if r["depth"] >= 2], "Nested height-correction render did not run"
    assert any(frame["mounted"] != mounted for frame in trace["frames"]), "No actual virtual-window boundary crossed: guard is NOT exercised"
    assert abs(await f.page.evaluate("scrollY") - target["target"]) <= 0.5
    before = await f.page.evaluate("scrollY")
    await f.module("table", "(m,arg)=>{m.refreshVirtualLayout(arg.uid,arg.anchor);requestAnimationFrame(()=>m.refreshVirtualLayout(arg.uid,arg.anchor));}", {"uid":uid,"anchor":old})
    await asyncio.sleep(0.1)
    assert abs(await f.page.evaluate("scrollY") - before) <= 0.5, "Queued stale anchor restored after lease release"
    assert not [w for w in (await f.trace())["writes"] if w["name"] == "scrollBy" and "restoreTableAnchor" in w["stack"] and w["t"] >= trace["frames"][0]["t"]], "Virtual anchor fought centre"
    reads = [r for r in trace["reads"] if r["t"] > trace["frames"][0]["t"] + 30 and r["t"] < trace["frames"][0]["t"] + 290]
    assert not reads, "Eos performed geometry reads during cached-number interpolation"
    await f.terminal(uid)
    await f.page.locator(f'tr.plot-row[data-deed-uid="{uid}"]').click()
    await f.open(uid)
    await f.page.mouse.wheel(0, 170)
    await asyncio.sleep(0.08)
    cancellation = await f.trace()
    count = len([w for w in cancellation["writes"] if "/eos.js" in w["stack"]])
    await asyncio.sleep(0.35)
    assert len([w for w in (await f.trace())["writes"] if "/eos.js" in w["stack"]]) == count


WORKSITE_CONTROL = '.slot-editor-footer .harvest-bar-cog[data-slot-action="open-worksite-modal"]'


async def _g19(f, expect_cover_mutation=False, after_scene_mutation=None):
    f.details['eos-0008']['swap_targets']=[{'target':'grain_farm'}]
    await f.open()
    if expect_cover_mutation:
        assert await f.band().locator('.eos-worker-cover').evaluate("n=>['top','bottom','left','right'].every(k=>getComputedStyle(n)[k]==='0px')"), "G19 cover red mutation did not land after plot setup"
    # retired the vertical `data-terrain-affinity="editor"` column on
    # desktop (`presentation` is always truthy here, so `renderEditorBandContents` no
    # longer emits it) -- the desktop splinter run is now the horizontal
    # `.editor-splinter-affinity [data-terrain-affinity="editor-row"]` row inside
    # `.slot-group-controls`, before Fill plot. Same invariant (visibility/gating through
    # every ancestor), new surface. The row is drawn with the plot's detail: the scaffold
    # shown while the detail is held keeps an empty, inert controls row (R-161 permanent
    # geometry), so the run is checked once the detail is in, with the scene still held.
    assert not await f.band().locator('.editor-splinter-affinity').count()
    f.release("detail")
    # The band's change-worksite control is the harvest bar's cog now (the footer's
    # .worksite-change-btn is no longer drawn); it is one of the gated worker controls.
    await f.band().locator(WORKSITE_CONTROL).wait_for(state="attached")
    assert await f.band().locator('.editor-splinter-affinity [data-terrain-affinity="editor-row"] [data-affinity-state]').count() == 6
    early = await f.band().evaluate("""band=>{
      const run=band.querySelector('.editor-splinter-affinity [data-terrain-affinity="editor-row"]'),veil=band.querySelector('.eos-scenic-veil'),cover=band.querySelector('.eos-worker-cover');
      if(!run||!veil||!cover) throw new Error('Missing footer affinity run, scenic veil, or worker cover');
      const r=run.getBoundingClientRect(), main=run.closest('.editor-main'),cr=cover.getBoundingClientRect();
      const overlap=Math.min(cr.right,r.right)-Math.max(cr.left,r.left), vertical=Math.min(cr.bottom,r.bottom)-Math.max(cr.top,r.top);
      return {veilParent:veil.parentElement===band,veilZ:Number(getComputedStyle(veil).zIndex),
        mainZ:Number(getComputedStyle(main).zIndex),height:band.getBoundingClientRect().height,
        hiddenByAria:!!run.closest('.editor-splinter-affinity').parentElement.closest('[aria-hidden="true"]'),controlsGated:!!band.querySelector('WORKSITE_CONTROL')?.closest('[inert]'),coverOverlap:overlap>0&&vertical>0,
        runTop:r.top,runBottom:r.bottom,coverTop:cr.top,coverBottom:cr.bottom};
    }""".replace("WORKSITE_CONTROL", WORKSITE_CONTROL))
    assert early["veilParent"] and early["veilZ"] > early["mainZ"], "G19 scenic veil remains above the affinity stacking context"
    assert abs(early["height"] - f.reserved) <= 0.5 and not early["hiddenByAria"], (early, f.reserved)
    assert early["controlsGated"], "G19 worker controls remain gated during scenic release"
    assert not early["coverOverlap"], "G19 worker cover excludes the full footer affinity run"
    await f.screenshot("badges-beneath-initial-veil")
    f.release("scene")
    await f.page.wait_for_function("() => {const n=document.querySelector('.editor-band .eos-scenic-veil');return n&&(n.hidden||getComputedStyle(n).opacity==='0')}",timeout=1200)
    assert await f.band().locator(".eos-scenic-veil").evaluate("n=>n.hidden?'0':getComputedStyle(n).opacity") == "0"
    # The opaque worker cover is retired (see G4): the workers stay unrevealed because their
    # held portraits are unpainted.
    assert await f.band().locator(".slot-tile-cell img.eos-portrait-fill").evaluate_all(
        "nodes=>nodes.filter(n=>{const s=getComputedStyle(n);return s.visibility!=='hidden'&&Number(s.opacity)>0}).length") == 0
    run = f.band().locator('.editor-splinter-affinity [data-terrain-affinity="editor-row"]')
    assert not f.gates['portrait'].is_set() and f.active>0, "G19 fixture holds actual portrait requests after scenic release"
    assert any('/cards/' in r['path'] or 'eos-skin' in r['path'] for r in f.requests), "G19 fixture requested worker portraits"
    if after_scene_mutation:
        assert await f.band().evaluate(after_scene_mutation), "G19 inherited footer gate mutation actually landed after scene release"
    assert await run.evaluate("""n=>{
      for(let node=n;node;node=node.parentElement){const s=getComputedStyle(node);
        if(node.inert||node.getAttribute('aria-hidden')==='true'||s.visibility!=='visible'||s.display==='none'||Number(s.opacity)<1)return false;
      }return true;
    }"""), "G19 scene-released badges are exposed before delayed portraits through every footer ancestor"
    assert await f.band().locator(WORKSITE_CONTROL).evaluate("n=>!!n.closest('[inert]')"), "G19 worker controls remain gated after scenic release"
    assert await f.band().locator(".slot-group-controls .slot-bulk").evaluate_all("nodes=>nodes.length>0&&nodes.every(n=>!!n.closest('[inert]'))"), "G19 worker controls remain gated after scenic release"
    labels = await run.locator('[aria-label]').evaluate_all("nodes=>nodes.map(n=>n.getAttribute('aria-label'))")
    # the editor's row shows the plot's six splinter facts, each with its own accessible label
    # only the interactive picker surface adds a seventh, Neutral (terrainAffinity.js, since 1.0.0).
    assert len(labels) >= 6 and all(labels), labels
    await f.screenshot("building-and-badges-before-workers")
    f.release("portrait", "upgrade")
    await f.terminal()
    _stable(await f.trace())


# Art that never arrives (or never decodes) is no longer frozen at the first 10 s budget
# eos.js retries it on the art.js schedule (1, 2, 4, 8, 15 s, each with a 10 s budget)
# and freezes it to the named fallback only after the last attempt, about a minute in.
# The guards that need that frozen fallback run the same schedule and budgets scaled 1/50.
_ART_TIMING = """const CARD_SERVICE_BUDGET_MS = 10000;
const SCENE_SERVICE_BUDGET_MS = 10000;
const SCENE_RELEASE_MS = 1200;
const CARD_RELEASE_MS = 1800;
// the same schedule as art.js -- 1, 2, 4, 8, 15 s, a fresh URL each retry.
const RECOVERY_DELAYS_MS = IMAGE_RETRY_DELAYS_MS;"""
FAST_ART_RECOVERY = ("eos.js", _ART_TIMING, _ART_TIMING
                     .replace("CARD_SERVICE_BUDGET_MS = 10000;", "CARD_SERVICE_BUDGET_MS = 200;")
                     .replace("SCENE_SERVICE_BUDGET_MS = 10000;", "SCENE_SERVICE_BUDGET_MS = 200;")
                     .replace("RECOVERY_DELAYS_MS = IMAGE_RETRY_DELAYS_MS;", "RECOVERY_DELAYS_MS = IMAGE_RETRY_DELAYS_MS.map((delay) => delay / 50);"))


SCENARIOS = {f"G{i}":globals()[f"_g{i}"] for i in range(1, 20)}


@pytest.mark.parametrize("guard", SCENARIOS)
def test_plot_reveal_guards(guard, report, details_by_id, tmp_path):
    capacity = 50 if guard == "G12" else 5
    count = 460 if guard in ("G10", "G18") else 80
    board, details = _payload(report, details_by_id, count=count, capacity=capacity, long=guard == "G14")

    async def run():
        async with _browser(board, details, tmp_path / guard, reduced=guard == "G11",
                            mutation=FAST_ART_RECOVERY if guard == "G6" else None) as fixture:
            await SCENARIOS[guard](fixture)
            assert not fixture.errors, fixture.errors

    asyncio.run(run())


# The board's layout tiers are in em of the root font (index.html: a larger browser text
# size narrows the EFFECTIVE width, `e = (innerWidth - scrollbar) * 16 / rem`, the same way
# browser zoom does), and at e <= 1279 the board is the phone card view by design -- no
# table, no desktop band. So a text-zoom case runs at the viewport that keeps the width
# under test in em at that root font (width * font / 16): the same em geometry the width x
# zoom grid means, on the desktop table, and the 16px cases at their own widths. The 1280
# column is the tier boundary itself: the board's 10px scrollbar puts a 1280 * font / 16
# viewport at e = 1270, the card view, so it runs just inside the desktop tier (16px past
# the boundary: measured, a viewport exactly one scrollbar past it can keep the card view
# it was first drawn in).
_SCROLLBAR_PX = 16


def _zoomed_viewport(width, font):
    if width > 1280:
        return width * font // 16
    return 1280 * font // 16 + _SCROLLBAR_PX


@pytest.mark.parametrize("width", (1920, 1745, 1536, 1280))
@pytest.mark.parametrize("font", (16, 20, 32))
def test_g2_effective_width_and_text_zoom(width, font, report, details_by_id, tmp_path):
    board, details = _payload(report, details_by_id, capacity=20)

    async def run():
        async with _browser(board, details, tmp_path / f"G2-{width}-{font}", width=_zoomed_viewport(width, font), font=font) as fixture:
            layout = await fixture.page.evaluate("""() => ({phone: document.documentElement.classList.contains('ceres-lw-le-1279'),
              effective: window.ceresScale.design * 16 / parseFloat(getComputedStyle(document.documentElement).fontSize)})""")
            assert not layout["phone"] and layout["effective"] >= min(width, 1280) - 16, (width, font, layout)
            await _g2(fixture)

    asyncio.run(run())


@pytest.mark.parametrize("capacity,no_art", ((0, True), (1, True), (50, False)))
def test_g1_g12_capacity_branches(capacity, no_art, report, details_by_id, tmp_path):
    board, details = _payload(report, details_by_id, capacity=capacity, no_art=no_art)

    async def run():
        async with _browser(board, details, tmp_path / f"G12-capacity-{capacity}") as fixture:
            await _g12(fixture)
            await asyncio.sleep(0.15)
            _stable(await fixture.trace())

    asyncio.run(run())


@pytest.mark.parametrize("reduced", (False, True))
@pytest.mark.parametrize("count", (1, 9, 80))
def test_g17_clamped_extent_and_reduced_motion(count, reduced, report, details_by_id, tmp_path):
    board, details = _payload(report, details_by_id, count=count)

    async def run():
        async with _browser(board, details, tmp_path / f"G17-{count}-{reduced}", reduced=reduced) as fixture:
            await _g17(fixture)

    asyncio.run(run())


@pytest.mark.parametrize("intent", ("wheel", "key", "touch", "scrollbar", "close", "sort", "filter", "refresh", "unmount", "resize", "stage", "open-b"))
@pytest.mark.parametrize("count", (80, 460))
def test_g18_pending_write_cancellation_matrix(intent, count, report, details_by_id, tmp_path):
    board, details = _payload(report, details_by_id, count=count)

    async def run():
        async with _browser(board, details, tmp_path / f"G18-{intent}-{count}") as f:
            f.release("scene", "portrait", "upgrade")
            if intent != "stage":
                f.release("detail")
            await f.open()
            if intent == "stage":
                # R-161 ruling: pending permanent frames stay inert
                # staging begins only after the detail-ready replacement is mounted.
                await f.page.wait_for_function("document.querySelector('.editor-band[data-eos-detail-scaffold]')?.dataset.plotState==='preparing'")

                # and the R-161 plan (ceres-r161-plot-open-loading-animation-2026-09-06.md)
                # require the scene, final tile frames, and controls row to survive the handoff.
                async def permanent_geometry():
                    return await f.band().evaluate("""band => {
                      const permanent = [...band.querySelectorAll('[data-eos-permanent-frame]')];
                      const workers = permanent.filter(node => node.matches('.slot-tile-cell'));
                      const power = permanent.find(node => node.querySelector('.slot-power'));
                      const assets = permanent.filter(node => node.matches('.asset-slot'));
                      const workerNames = new Map(workers.map((node, index) => [node, `worker[${index + 1}]`]));
                      const assetNames = new Map(assets.map((node, index) => [node, index === 0 ? 'totem' : index === 1 ? 'title' : `asset[${index}]`]));
                      const named = permanent.map((node, index) => [
                        workerNames.get(node) || (node === power ? 'power' : assetNames.get(node) || `permanent[${index}]`),
                        node,
                      ]);
                      const controls = band.querySelector('.slot-group-controls');
                      if (controls) named.push(['controls', controls]);
                      const boxes = named.map(([name, node]) => {
                        const rect = node.getBoundingClientRect();
                        return {name, x: rect.x, y: rect.y, width: rect.width, height: rect.height};
                      });
                      return {permanentCount: permanent.length, boxes};
                    }""")

                before = await permanent_geometry()
                required = {"power", "totem", "title", "controls"}
                names = {box["name"] for box in before["boxes"]}
                assert required <= names, f"R-161 missing permanent geometry frame(s): {sorted(required - names)}"
                await f.band().locator('.eos-permanent-frame .slot-tile').first.click(force=True)
                assert await f.module("staging", "m=>m.CERES_STAGE.length") == 0
                f.release("detail")
                await f.page.wait_for_function("!document.querySelector('.editor-band[data-eos-detail-scaffold]')")
                after = await permanent_geometry()
                names = {box["name"] for box in after["boxes"]}
                assert required <= names, f"R-161 missing permanent geometry frame(s): {sorted(required - names)}"
                assert after["permanentCount"] == before["permanentCount"], (
                    "R-161 permanent frame count changed: " f"before={before['permanentCount']} after={after['permanentCount']}"
                )
                assert [box["name"] for box in after["boxes"]] == [box["name"] for box in before["boxes"]], (
                    "R-161 permanent frame set changed: " f"before={[box['name'] for box in before['boxes']]} " f"after={[box['name'] for box in after['boxes']]}"
                )
                for old, new in zip(before["boxes"], after["boxes"]):
                    for dimension in ("x", "y", "width", "height"):
                        delta = new[dimension] - old[dimension]
                        assert abs(delta) <= 1, (
                            f"R-161 frame {old['name']} {dimension} delta={delta:.3f}px " f"(before={old[dimension]:.3f}px after={new[dimension]:.3f}px)"
                        )
                remove = f.band().locator('[data-slot-action="remove"][data-group-id="worker"]').first
                await remove.click(force=True)
                await _staged_removal(f)
                assert await f.module("staging", "m=>m.CERES_STAGE.length") == 1
            elif intent == "wheel":
                await f.page.mouse.wheel(0, 120)
            elif intent == "key":
                await f.page.keyboard.press("PageDown")
            elif intent == "touch":
                await f.page.touchscreen.tap(1800, 600)
            elif intent == "scrollbar":
                await f.page.mouse.click(1918, 400)
            elif intent == "close":
                await f.band().get_by_role("button", name="Close plot", exact=False).click()
            elif intent == "sort":
                await f.page.locator('th[data-key="plot"]').click()
            elif intent == "filter":
                await f.page.locator("#text-filter").fill("no matching plot")
            elif intent == "refresh":
                await f.module("board", "m=>{m.loadBoard();return true}")
            elif intent == "unmount":
                if count > 400:
                    # A script scroll cannot unmount the opening plot: the reveal's viewport
                    # lease keeps writing the plot's own position until it lands (eos.js
                    # centreReservedPlot), so the row stays in the window. The reader's way
                    # out -- End -- ends the lease and the virtual window drops the row.
                    await f.page.keyboard.press("End")
                    await f.page.wait_for_function("!document.querySelector('tr.plot-row[data-deed-uid=\"eos-0008\"]')")
                else:
                    await f.page.evaluate("window.scrollTo({top:document.scrollingElement.scrollHeight,behavior:'instant'})")
                    await f.module("table", "m=>m.render(true)")
            elif intent == "resize":
                await f.page.set_viewport_size({"width":1745,"height":1080})
            elif intent == "open-b":
                await f.open("eos-0009")
                await asyncio.sleep(0.35)
            await asyncio.sleep(0.05)
            trace = await f.trace()
            writes = len([w for w in trace["writes"] if "/eos.js" in w["stack"]])
            await asyncio.sleep(0.4)
            after = await f.trace()
            assert len([w for w in after["writes"] if "/eos.js" in w["stack"]]) == writes, (intent,after["writes"])
    asyncio.run(run())


async def _stale_anchor_observation(f):
    old = await f.module("table", "m=>m.captureTableAnchor()")
    assert old and "viewportEpoch" in old
    old["uid"], old["top"] = "eos-0008", -180
    target = await f.open()
    await asyncio.sleep(0.4)
    assert abs(await f.page.evaluate("scrollY") - target["target"]) <= 0.5
    await f.module("table", "(m,arg)=>requestAnimationFrame(()=>m.refreshVirtualLayout('eos-0008',arg))", old)
    await asyncio.sleep(0.08)
    assert abs(await f.page.evaluate("scrollY") - target["target"]) <= 0.5, "Queued stale anchor restored after lease release"


@pytest.mark.parametrize("mutated", (False, True))
def test_g18_stale_anchor_guard_rejects_its_red_mutation(mutated, report, details_by_id, tmp_path):
    board, details = _payload(report, details_by_id)
    mutation = ("table.js", " || anchor.viewportEpoch !== viewportEpoch", "") if mutated else None

    async def run():
        async with _browser(board, details, tmp_path / f"G18-stale-mutation-{mutated}", mutation=mutation) as f:
            if mutated:
                assert f.mutation_hits == 1, "Changed the loaded table module exactly once"
                with pytest.raises(AssertionError, match="Queued stale anchor restored after lease release"):
                    await _stale_anchor_observation(f)
            else:
                await _stale_anchor_observation(f)

    asyncio.run(run())


@pytest.mark.parametrize("guard,css", (
    ("G1", ".editor-band {block-size:auto!important;min-block-size:0!important}"),
    ("G2", ".editor-band {inline-size:100vw!important;max-inline-size:none!important}"),
    # the worker cover is retired (see _g4); the red mutation paints the held portraits.
    ("G4", ".slot-tile-cell img.eos-portrait-fill {opacity:1!important;visibility:visible!important}"),
    ("G19", ".editor-main {z-index:5!important}"),
    # the cover is retired (mounted `hidden`); the red mutation also shows it again.
    ("G19-cover", ".eos-worker-cover {display:block!important;top:0!important;bottom:0!important;left:0!important;right:0!important}"),
))
def test_visual_guards_reject_their_red_mutations(guard, css, report, details_by_id, tmp_path):
    board, details = _payload(report, details_by_id)

    async def run():
        async with _browser(board, details, tmp_path / f"{guard}-mutation") as fixture:
            await fixture.page.add_style_tag(content=css)
            message={"G19":"G19 scenic veil remains above the affinity stacking context", "G19-cover":"G19 worker cover excludes the full footer affinity run"}.get(guard)
            with pytest.raises(AssertionError,match=message):
                if guard == "G19-cover":
                    await _g19(fixture, expect_cover_mutation=True)
                else:
                    await SCENARIOS[guard](fixture)

    asyncio.run(run())


@pytest.mark.parametrize("gate", ["inert", "aria", "visibility", "opacity"])
def test_g19_inherited_footer_gate_rejected_after_scene_before_portraits(gate, report, details_by_id, tmp_path):
    board,details=_payload(report,details_by_id)
    mutations={
        "inert":"footer.inert=true;return footer.inert",
        "aria":"footer.setAttribute('aria-hidden','true');return footer.getAttribute('aria-hidden')==='true'",
        "visibility":"footer.style.visibility='hidden';return getComputedStyle(footer).visibility==='hidden'",
        "opacity":"footer.style.opacity='0';return getComputedStyle(footer).opacity==='0'",
    }

    async def run():
        async with _browser(board,details,tmp_path/f"G19-footer-{gate}") as fixture:
            # The badges' inherited gate is their own row now: the worker controls row they
            # lead (the footer that held the retired column no longer contains them).
            mutation="band=>{const footer=band.querySelector('.slot-group-controls');"+mutations[gate]+";}"
            with pytest.raises(AssertionError,match="G19 scene-released badges are exposed before delayed portraits through every footer ancestor"):
                await _g19(fixture,after_scene_mutation=mutation)

    asyncio.run(run())


@pytest.mark.parametrize("reduced", (False, True))
def test_g5_rejected_actual_node_decode_becomes_named_fallback(reduced, report, details_by_id, tmp_path):
    board, details = _payload(report, details_by_id)

    async def run():
        async with _browser(board, details, tmp_path / f"G5-rejected-{reduced}", reduced=reduced, mutation=FAST_ART_RECOVERY) as f:
            await f.page.evaluate("eosProbe.rejectDecode=true")
            f.release("scene", "detail", "portrait", "upgrade")
            await f.open()
            await f.terminal()
            assert await f.band().locator(".eos-art-fallback").count() > 0
            assert "Eos Worker 1" in await f.band().inner_text()
            assert not await f.band().locator('img[data-eos-managed][data-eos-outcome="decoded"]').count()
            if reduced:
                assert not (await f.trace())["animations"]

    asyncio.run(run())


@pytest.mark.parametrize("reduced", (False, True))
def test_g19_scene_timeout_releases_unknown_badges_with_frozen_field(reduced, report, details_by_id, tmp_path):
    board, details = _payload(report, details_by_id)

    async def run():
        async with _browser(board, details, tmp_path / f"G19-timeout-{reduced}", reduced=reduced) as f:
            await f.open()
            await asyncio.sleep(1.65)
            assert await f.band().evaluate("b=>!getComputedStyle(b).getPropertyValue('--scene-terrain').includes('url(')")
            # The field around the timed-out scene stays frozen; only the scene's own layers may
            # still change: a scene that decodes after its soft release is applied on purpose
            # (test_live_art_continuity: the soft release preserves a late decode).
            field = """b=>Object.fromEntries([...b.style].filter(name=>!/^--scene-(terrain|worksite)$/.test(name))
              .map(name=>[name,b.style.getPropertyValue(name).trim()]))"""
            before = await f.band().evaluate(field)
            geometry = await f.band().evaluate("b=>b.getBoundingClientRect().height")
            f.release("scene")
            await asyncio.sleep(0.25)
            assert await f.band().evaluate(field) == before
            assert await f.band().evaluate("b=>b.getBoundingClientRect().height") == geometry
            # The splinter run is drawn with the plot's detail: the held-detail scaffold keeps an
            # empty, inert controls row (R-161 permanent geometry). When the detail lands after
            # the scene's timed-out release, the badges show at once, before the portraits.
            assert not await f.band().locator('.editor-splinter-affinity').count()
            f.release("detail")
            # desktop splinter run is the horizontal row, not the retired column.
            badges = f.band().locator('.editor-splinter-affinity [data-terrain-affinity="editor-row"]')
            await badges.wait_for(state="attached")
            assert await badges.is_visible()
            assert await badges.get_attribute("aria-hidden") != "true"
            assert await badges.locator('[data-affinity-state]').count() == 6
            await f.screenshot("badges-over-final-unavailable-scene")
            f.release("portrait", "upgrade")
            await f.terminal()
            assert not [a for a in (await f.trace())["animations"] if "terrain-affinity" in a["cls"]]
            _stable(await f.trace())

    asyncio.run(run())


@pytest.mark.parametrize("mode", ("slow", "error", "art-timeout"))
def test_g11_reduced_slow_error_and_art_retry(mode, report, details_by_id, tmp_path):
    board, details = _payload(report, details_by_id)

    async def run():
        async with _browser(board, details, tmp_path / f"G11-{mode}", reduced=True,
                            mutation=FAST_ART_RECOVERY if mode == "art-timeout" else None) as f:
            if mode == "art-timeout":
                await _g6(f)
            elif mode == "error":
                await _g16(f)
            else:
                snapshot = await f.open()
                f.release("scene")
                await f.band().get_by_text("Loading workers…", exact=True).wait_for(timeout=2500)
                await f.band().get_by_text("Still loading workers…", exact=True).wait_for(timeout=9000)
                assert await f.band().get_by_role("button", name="Close plot", exact=False).is_visible()
                assert abs(await f.page.evaluate("scrollY") - snapshot["target"]) <= 0.5
                f.release("detail", "portrait", "upgrade")
                await f.terminal()
            assert not (await f.trace())["animations"]

    asyncio.run(run())
