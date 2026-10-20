"""Independent R-117 browser contracts and landed fault injections.

Only the dedicated test queue runs these guards. Rendered measurements and
screenshots are evidence of fit; maintainer acceptance remains a separate decision.
"""

from __future__ import annotations

import copy
from contextlib import closing
import json
import subprocess
import urllib.parse
from pathlib import Path


import pytest

from .conftest import spawn_live_ceres
from .test_candidate_redesign_frontend import _real_detail_row

ROOT = Path(__file__).parents[2]
BASELINE = "97fca000ff0a3c63792dff1ab927f3a16721f9c7"
MOUNTAIN = {"red": .1, "blue": -.5, "green": -.5, "white": 0, "black": .1, "gold": 0}
MIXED = {**MOUNTAIN, "white": None}
NAMES = ["Fire", "Water", "Earth", "Life", "Death", "Dragon"]
PROFILE = "[data-terrain-affinity]"
BADGE = "[data-splinter][data-affinity-state]"
EDITOR_PROFILE = f".slot-editor-footer > {PROFILE}"
PICKER_PROFILE = f".candidate-worker-affinity {PROFILE}"
THEMES = ("alchemical", "dark", "light", "wood")


@pytest.fixture(scope="module")
def live_browser():
    with spawn_live_ceres() as live:
        yield live


@pytest.fixture(scope="module")
def mountain_row(live_browser):
    _, base_url = live_browser
    row = copy.deepcopy(_real_detail_row(base_url))
    row.update(terrain="Mountain", biome=MOUNTAIN.copy())
    workers = [copy.deepcopy(w) for w in row["workers"] if not w.get("is_runi")][:2]
    assert workers, "fixture requires a real worker"
    if len(workers) == 1:
        workers.append(copy.deepcopy(workers[0]))
        workers[-1]["asset_ref"] = "r117-second-staked-worker"
    for slot, worker in enumerate(workers, 2):
        worker["slot"] = slot
    row["workers"] = workers
    row["capacity"].update(worker_count=2, max_workers_allowed=5, card_capacity=5, open_slots=3)
    if not row.get("swap_targets") and not row.get("worksite_completion"):
        row["swap_targets"] = [{"target": "grain_farm"}]
    return row


def _candidates(count=4):
    states = [("Fire", .1), ("Water", -.5), ("Neutral", 0), ("Life", None)]
    return [{
        "name": f"Affinity {splinter} {i:03}", "asset_ref": f"r117-{i}", "splinter": splinter,
        "terrain_boost": terrain, "level": 1, "base_pp": 1000, "rarity": "Common",
        "card_set": "chaos", "edition": 3,
        "construction_pp": {"status": "point", "value": 1000, "low": 1000, "high": 1000, "reason": None},
        "plot_impact": {"status": "point", "value": 100, "low": 100, "high": 100, "reason": None},
        "net_gain": 100, "net_gain_breakdown": {},
    } for i in range(count) for splinter, terrain in [states[i % 4]]]


def _mount(page, base_url, row, candidates=None, *, baseline=False):
    if baseline:
        for filename in ("editor.js", "candidates.js", "picker-actions.js", "format.js", "icons.js", "terrainAffinity.js", "eos.js", "ceres.css"):
            content = subprocess.check_output(
                ["git", "show", f"{BASELINE}:ceres/static/{filename}"], cwd=ROOT, text=True)
            mime = "text/css" if filename.endswith(".css") else "text/javascript"
            page.route(f"**/static/{filename}*", lambda route, body=content, kind=mime:
                       route.fulfill(status=200, content_type=kind, body=body))
    deed = urllib.parse.quote(row["deed_uid"], safe="")
    page.route(f"**/api/plots/{deed}?*", lambda route:
               route.fulfill(status=200, content_type="application/json", body=json.dumps(row)))
    rows = _candidates() if candidates is None else candidates
    page.route(f"**/api/plots/{deed}/candidates*", lambda route:
               route.fulfill(status=200, content_type="application/json", body=json.dumps({
                   "candidates": rows, "total": len(rows), "source": "test-fixture",
                   "excluded_counts": {}, "notices": [],
               })))
    page.goto(base_url)
    page.locator(f"tr.plot-row[data-deed-uid='{row['deed_uid']}']").click(timeout=30_000)
    page.locator(".slot-editor .editor-band").wait_for(timeout=30_000)
    page.evaluate("() => document.fonts.ready")
    _wait_presentation(page)


def _wait_presentation(page):
    page.wait_for_function("() => ['ready','degraded'].includes(document.querySelector('.slot-editor .editor-band')?.dataset.plotState)", timeout=30_000)


def _open(page, *, empty=False):
    page.locator(".slot-group[data-group-id='worker'] .slot-tile[data-slot-action='add']").first.click()
    page.locator(".inline-picker").wait_for(timeout=30_000)
    if not empty:
        page.locator(".candidate-row").first.wait_for(timeout=30_000)


def _geometry(page):
    return page.evaluate("""() => {
 const band=document.querySelector('.editor-band'), origin=band.getBoundingClientRect();
 const rect=el=>{const r=el.getBoundingClientRect();return {x:r.x-origin.x,y:r.y-origin.y,width:r.width,height:r.height};};
 const scene=band.querySelector('.editor-scene'), style=getComputedStyle(scene), art=getComputedStyle(scene,'::before');
 const table=document.querySelector('#plot-table');
 return {band:rect(band),main:rect(band.querySelector('.editor-main')),scene:rect(scene),
 tableWidth:table.getBoundingClientRect().width,
 registration:[style.backgroundSize,style.backgroundPosition,art.backgroundImage,art.backgroundSize,art.backgroundPosition,art.bottom,getComputedStyle(scene,'::after').bottom],
 tiles:[...band.querySelectorAll('.slot-tile')].map(rect),
 facts:[...band.querySelectorAll('.editor-facts, .slot-editor-footer')].map(rect)};
 }""")


def _assert_geometry(actual, expected):
    assert actual["registration"] == expected["registration"], "G6 unchanged art crop"
    assert actual["tableWidth"] == pytest.approx(expected["tableWidth"], abs=.5), "G6 zero added table width"
    for key in ("band", "main", "scene"):
        assert actual[key] == pytest.approx(expected[key], abs=.5), f"G6 zero added {key} geometry"
    for key in ("tiles", "facts"):
        assert len(actual[key]) == len(expected[key]), f"G6 {key} preserved"
        for a, b in zip(actual[key], expected[key]):
            assert a == pytest.approx(b, abs=.5), f"G6 {key} registration"


def _intrinsic_geometry(page):
    return page.evaluate("""() => {
 const band=document.querySelector('.editor-band'),origin=band.getBoundingClientRect();
 const measure=el=>{const r=el.getBoundingClientRect();return {x:r.x-origin.x,y:r.y-origin.y,width:r.width,height:r.height,
 scrollWidth:el.scrollWidth,scrollHeight:el.scrollHeight};};
 const selectors=['.editor-band','#plot-table','#plot-table-scroll','.editor-main','.editor-facts','.slot-editor-footer','.worksite-change-btn','.slot-grid'];
 return Object.fromEntries(selectors.map(selector=>[selector,[...document.querySelectorAll(selector)].map(measure)]));
 }""")


def _assert_zero_intrinsic_growth(page):
    run=page.locator(EDITOR_PROFILE)
    assert run.count() == 1, "G6 intrinsic fixture has one run"
    page.evaluate("() => {window.r117IntrinsicRun=document.querySelector('.slot-editor-footer > [data-terrain-affinity]');window.r117IntrinsicRun.remove()}")
    without=_intrinsic_geometry(page)
    page.evaluate("() => document.querySelector('.slot-editor-footer').prepend(window.r117IntrinsicRun)")
    with_run=_intrinsic_geometry(page)
    for selector, rectangles in without.items():
        assert rectangles and len(rectangles)==len(with_run[selector]), f"G6 intrinsic fixture includes {selector}"
        for before,after in zip(rectangles,with_run[selector]):
            assert after == pytest.approx(before,abs=.5), f"G6 zero intrinsic growth for {selector}"


def _assert_placement(page, *, picker=True):
    assert page.locator(EDITOR_PROFILE).count() == 1, "G5 editor run is an unconditional footer child"
    assert page.locator(".slot-editor-footer > .worksite-change-btn").count() <= 1
    assert page.locator(".editor-band > .editor-main").count() == 1, "G5 direct main"
    assert page.locator(".editor-band > .editor-scene").count() == 1, "G5 direct scene"
    assert page.locator(f"tr.plot-row {PROFILE}, .slot-tile {PROFILE}, .candidate-row {PROFILE}, .editor-scene {PROFILE}").count() == 0, "G5 no repeated/decorative run"
    assert page.locator(PROFILE).count() == (2 if picker else 1), "G5 constant profile count"
    if picker:
        assert page.locator(PICKER_PROFILE).count() == 1, "G5 Worker identity header owns run"
        header = page.locator(".candidate-columns")
        assert header.locator("scope > span").count() == 7, "G5 existing direct spans"
        assert header.get_attribute("aria-hidden") != "true", "G4 header facts exposed"
        assert header.locator("scope > span:not(:nth-child(2))[aria-hidden='true']").count() == 6, "G4 only non-Worker labels decorative"


def _expected_states(biome):
    result = []
    for key in MOUNTAIN:
        value = biome.get(key)
        result.append("unknown" if type(value) not in (int, float) else
                      "favoured" if value > 0 else "penalised" if value < 0 else "unchanged")
    return result


def _assert_badges(page, biome):
    states = _expected_states(biome)
    order = {state: i for i, state in enumerate(("favoured", "unchanged", "unknown", "penalised"))}
    peers = sorted(zip(NAMES, states), key=lambda peer: order[peer[1]])
    expected = [state for _, state in peers]
    for profile in page.locator(PROFILE).all():
        badges = profile.locator(BADGE)
        assert badges.count() == 6, "G4 six factual badges without Neutral"
        assert badges.evaluate_all("els=>els.map(el=>el.dataset.splinter)") == [name for name, _ in peers], "G4 meaning first and default splinter order within state"
        assert badges.evaluate_all("els=>els.map(el=>el.dataset.affinityState)") == expected, "G4 known zero differs from unavailable"
        for index, badge in enumerate(badges.all()):
            state = expected[index]
            assert badge.get_attribute("role") == "img", "G4 static image role"
            assert badge.get_attribute("tabindex") == "0", "G7 each tooltip has keyboard access"
            name = badge.get_attribute("aria-label") or ""
            assert peers[index][0] in name, "G4 factual identity name"
            assert "Mountain" in name, "G4 named terrain"
            word = {"unknown": "unavailable", "unchanged": "no change", "favoured": "bonus", "penalised": "penalty"}[state]
            assert word in name, "G4 factual state name"
            assert badge.get_attribute("data-tip") == peers[index][0], "G4 tooltip names splinter"
            assert word in (badge.get_attribute("data-tip-body") or ""), "G4 tooltip states terrain effect"
            if state == "unknown":
                assert "zero" not in name and "0%" not in name, "G4 unavailable is never zero"
                assert badge.get_attribute("data-tip-key") is None, "G4 unavailable tooltip never fabricates zero"
            else:
                expected_key = {"favoured": "+10%", "penalised": "−50%", "unchanged": "0%"}[state]
                assert badge.get_attribute("data-tip-key") == expected_key, "G4 tooltip carries signed modifier"
            style = badge.evaluate("el=>{const s=getComputedStyle(el);return {opacity:s.opacity,display:s.display,visibility:s.visibility,width:s.width,height:s.height,border:s.borderTopStyle};}")
            assert style["opacity"] == "1" and style["display"] != "none" and style["visibility"] == "visible", "G4 unavailable visibly opaque"
            root = float(badge.evaluate("el=>parseFloat(getComputedStyle(document.documentElement).fontSize)"))
            assert float(style["width"].replace("px", "")) == pytest.approx(1.5 * root, abs=.5), "G4 fixed circular bubble"
            assert float(style["height"].replace("px", "")) == pytest.approx(1.5 * root, abs=.5), "G4 fixed circular bubble"
            assert badge.evaluate("el=>getComputedStyle(el).borderRadius") in ("50%", f"{.75 * root:g}px"), "G4 circular bubble"
            assert badge.locator(".splinter-icon[aria-hidden='true']").count() == 1, "G4 decorative official identity"
            assert badge.locator("svg").count() == 0, "G4 no state symbols"
            icon = badge.locator(".splinter-icon")
            assert icon.evaluate("el=>{const r=el.getBoundingClientRect(),root=parseFloat(getComputedStyle(document.documentElement).fontSize);return [r.width/root,r.height/root]}") == pytest.approx([1, 1], abs=.05), "G4 original icon canvas"


def _assert_no_visible_copy(page):
    for profile in page.locator(PROFILE).all():
        assert profile.inner_text().strip() == "", "G4 no visible affinity prose"
        assert profile.locator("text, title, [title], .terrain-affinity-bar").count() == 0, "G4 no surface text/bar/native tooltip"
        profile.hover(force=True)
        assert profile.inner_text().strip() == "", "G4 no hover prose"
        shell = profile.locator("xpath=..").first
        assert shell.locator("svg, path, text, .terrain-affinity-edge svg, .terrain-affinity-edge path").count() == 0, "G4 no visible or generated symbols"
        assert shell.evaluate("el=>{const ok=new Set(['none','normal','\"\"',\"''\"]);return [el,...el.querySelectorAll('*')].every(n=>ok.has(getComputedStyle(n,'::before').content)&&ok.has(getComputedStyle(n,'::after').content))}"), "G4 no generated symbol content"


def _ax_nodes(page):
    client = page.context.new_cdp_session(page)
    try:
        return [n for n in client.send("Accessibility.getFullAXTree")["nodes"] if not n.get("ignored")]
    finally:
        client.detach()


def _assert_accessible(page):
    nodes = _ax_nodes(page)
    names = [(n.get("role", {}).get("value"), n.get("name", {}).get("value", "")) for n in nodes]
    for profile in page.locator(PROFILE).all():
        group_name = profile.get_attribute("aria-label")
        assert group_name and ("group", group_name) in names, "G4 named group in accessibility tree"
        for badge in profile.locator(BADGE).all():
            assert ("image", badge.get_attribute("aria-label")) in names, "G4 every factual image exposed"
    facts = [name for role, name in names if role == "image" and name.startswith(tuple(NAMES)) and "terrain" in name]
    assert len(facts) == 12, "G4 both sets of six accessible images, including scrolled badges"
    assert not any(role == "image" and name.startswith("Neutral") and "terrain" in name for role, name in names), "G4 no invisible Neutral affinity entry"


def _assert_fit(page):
    measured = page.evaluate("""() => {
 const rect=el=>{const r=el.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height};};
 const root=parseFloat(getComputedStyle(document.documentElement).fontSize);
 const footer=document.querySelector('.slot-editor-footer'),group=footer?.querySelector(':scope > [data-terrain-affinity]');
 const button=footer?.querySelector('.worksite-change-btn');
 const picker=document.querySelector('.candidate-visible-slice');
 const probe=document.createElement('span');probe.style.cssText='position:absolute;inline-size:var(--worksite-w);block-size:0';footer.append(probe);
 const worksite=probe.getBoundingClientRect().width;probe.remove();
 return {root,footer:rect(footer),button:button&&rect(button),run:rect(group),worksite,
 footerCenter:footer.getBoundingClientRect().left+worksite/2,badges:[...group.querySelectorAll('[data-splinter]')].map(rect),
 tiles:[...document.querySelectorAll('.editor-band .slot-tile')].map(rect),
 clientTop:footer?.clientTop,pointer:getComputedStyle(group).pointerEvents,
 editorGated:!!group.closest('[inert],[aria-hidden="true"]'),
 pickerOverflow:picker?picker.scrollWidth-picker.clientWidth:null,
 pageOverflow:document.documentElement.scrollWidth-document.documentElement.clientWidth};
 }""")
    root, footer, run = measured["root"], measured["footer"], measured["run"]
    assert run["width"] == pytest.approx(12 * root, abs=.5), "G6 existing 192px budget"
    assert run["height"] == pytest.approx(1.5 * root, abs=.5), "G6 no badge wrapping"
    assert run["bottom"] == pytest.approx(footer["top"] + (measured["clientTop"] or 0) - .25 * root, abs=.5), "G6 footer padding-box gap"
    assert run["left"] + run["width"] / 2 == pytest.approx(measured["footerCenter"], abs=.5), "G6 run keeps reserved centre when worksite button is absent"
    if measured["button"]:
        button = measured["button"]
        assert run["left"] + run["width"] / 2 == pytest.approx(button["left"] + button["width"] / 2, abs=.5), "G6 run centred on worksite button"
        assert run["bottom"] <= button["top"] + .5, "G6 bubbles leave worksite control unobstructed"
    for badge in measured["badges"]:
        assert badge["width"] == pytest.approx(1.5 * root, abs=.5), "G6 bubble width"
        assert badge["height"] == pytest.approx(1.5 * root, abs=.5), "G6 badge height"
        assert run["left"] <= badge["left"] < badge["right"] <= run["right"], "G6 bubbles stay in run"
        assert run["top"] <= badge["top"] < badge["bottom"] <= run["bottom"], "G6 bubbles stay in row"
    assert run["left"] >= footer["left"] - .5 and run["right"] <= footer["left"] + measured["worksite"] + .5, "G6 run stays within reserved worksite width"
    for tile in measured["tiles"]:
        overlap_x=min(run["right"],tile["right"])-max(run["left"],tile["left"])
        overlap_y=min(run["bottom"],tile["bottom"])-max(run["top"],tile["top"])
        assert min(overlap_x,overlap_y) <= .5, "G6 bubbles leave worker tiles unobstructed"
    assert measured["pointer"] == "none", "G9 static overlay cannot intercept controls"
    assert measured["badges"][0]["left"] + measured["badges"][-1]["right"] == pytest.approx(2 * measured["footerCenter"], abs=1), "G6 six visible bubbles centred on worksite"
    assert not measured["editorGated"], "G19 affinity remains on scenic release when footer gate is inherited"
    assert measured["pageOverflow"] <= 1, "G6 no page overflow"
    assert measured["pickerOverflow"] is None or measured["pickerOverflow"] <= 1, "G6 local overflow cannot widen picker"
    return measured


def _candidate_structure(page):
    return page.locator(".candidate-row").evaluate_all("""rows=>rows.map(row=>{
 const copy=row.cloneNode(true);for(const value of copy.querySelectorAll('.candidate-terrain > strong'))value.removeAttribute('class');return copy.outerHTML;
 })""")


@pytest.mark.parametrize("biome", [MOUNTAIN, MIXED, {}, {key: 0 for key in MOUNTAIN}])
def test_inline_truth_and_accessibility(live_browser, mountain_row, biome):
    browser, base_url = live_browser
    with closing(browser.new_page(viewport={"width": 1920, "height": 1080})) as page:
        _mount(page, base_url, {**mountain_row, "biome": biome})
        _assert_placement(page, picker=False)
        _open(page)
        _assert_placement(page)
        _assert_badges(page, biome)
        _assert_accessible(page)
        _assert_no_visible_copy(page)
        page.add_style_tag(content=f"{PROFILE} {{filter:grayscale(1)}}")
        _assert_badges(page, biome)
        _assert_accessible(page)


def _assert_tooltip_hit_targets(page):
    for badge in page.locator(EDITOR_PROFILE).locator(BADGE).all():
        assert badge.evaluate("el=>{const r=el.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return getComputedStyle(el).pointerEvents==='auto'&&(hit===el||el.contains(hit));}"), "G4 each editor tooltip has a real badge hit target"


@pytest.mark.parametrize("input_kind", ["hover", "keyboard", "touch"])
def test_each_semantic_state_uses_shared_tooltip(live_browser, mountain_row, input_kind):
    browser, base_url = live_browser
    with closing(browser.new_page(viewport={"width": 1920, "height": 1080}, is_mobile=input_kind == "touch", has_touch=input_kind == "touch")) as page:
        _mount(page, base_url, {**mountain_row, "biome": MIXED})
        _open(page)
        _assert_tooltip_hit_targets(page)
        before = _geometry(page)
        tooltip = page.locator("#ceres-tooltip")
        assert tooltip.count() == 1, "G4 reuses exactly one product tooltip"
        for surface in (EDITOR_PROFILE, PICKER_PROFILE):
            for state, expected in (("favoured", "+10%"), ("unchanged", "0%"), ("penalised", "−50%"), ("unknown", "unavailable")):
                badge = page.locator(surface).locator(f'[data-affinity-state="{state}"]').first
                badge.scroll_into_view_if_needed()
                if input_kind == "hover":
                    badge.hover()
                elif input_kind == "touch":
                    badge.tap()
                else:
                    page.keyboard.press("Tab")
                    badge.focus()
                    assert badge.evaluate("el=>el.matches(':focus-visible')"), "G4 keyboard tooltip has visible focus"
                    assert badge.evaluate("el=>parseFloat(getComputedStyle(el).outlineOffset)>=0"), "G4 focus outline leaves original icon pixels intact"
                tooltip.wait_for(state="visible")
                text = tooltip.inner_text()
                assert badge.get_attribute("data-splinter") in text and "Mountain" in text, "G4 visible tooltip identifies splinter and terrain"
                assert expected in text, "G4 visible tooltip carries semantic state value"
                if state == "unknown":
                    assert "0%" not in text, "G4 visible unknown tooltip never fabricates zero"
                assert badge.inner_text() == "", "G4 tooltip adds no surface prose"
                page.keyboard.press("Escape")
                tooltip.wait_for(state="hidden")
        _assert_geometry(_geometry(page), before)


def test_tooltip_hit_guard_rejects_pointer_disabled_mutation(live_browser, mountain_row):
    browser, base_url = live_browser
    with closing(browser.new_page(viewport={"width": 1920, "height": 1080})) as page:
        _mount(page, base_url, {**mountain_row, "biome": MIXED})
        badge = page.locator(EDITOR_PROFILE).locator('[data-affinity-state="favoured"]').first
        badge.evaluate("el=>el.style.pointerEvents='none'")
        assert badge.evaluate("el=>getComputedStyle(el).pointerEvents==='none'"), "disable_bonus_tooltip_pointer mutation landed"
        with pytest.raises(AssertionError, match="G4 each editor tooltip has a real badge hit target"):
            _assert_tooltip_hit_targets(page)


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize(("width", "root"), [(1920, 16), (1745, 16), (1024, 16), (900, 16), (850, 16), (700, 16), (1920, 32), (700, 32)])
def test_inline_geometry_against_clean_baseline(live_browser, mountain_row, theme, width, root, tmp_path):
    browser, base_url = live_browser
    snapshots = []
    for baseline in (True, False):
        with closing(browser.new_page(viewport={"width": width, "height": 1080})) as page:
            _mount(page, base_url, mountain_row, baseline=baseline)
            page.evaluate("([theme,size])=>{document.documentElement.dataset.theme=theme;document.documentElement.style.fontSize=`${size}px`;}", [theme, root])
            closed = _geometry(page)
            _open(page)
            snapshots.append((closed, page.locator(".candidate-columns").bounding_box()))
            _assert_geometry(_geometry(page), closed)
            if not baseline:
                _assert_zero_intrinsic_growth(page)
                _assert_fit(page)
                _assert_placement(page)
                _assert_geometry(closed, snapshots[0][0])
                header = page.locator(".candidate-columns")
                assert header.is_visible(), "G6 Worker header survives narrow breakpoint"
                if snapshots[0][1]:
                    assert header.bounding_box()["height"] <= snapshots[0][1]["height"] + .5, "G6 desktop label row does not grow"
                else:
                    assert header.bounding_box()["height"] == pytest.approx(1.5 * root, abs=.5), "G6 narrow header has exactly one badge row"
                page.screenshot(path=str(tmp_path / f"r117-{theme}-{width}-{root}-complete-plot-picker.png"), full_page=True)
                page.locator(".picker-close").click()
                _assert_geometry(_geometry(page), closed)


@pytest.mark.parametrize("css", [
    '.slot-editor-footer > [data-terrain-affinity] {position:relative!important;transform:none!important;left:auto!important;bottom:auto!important}',
    '.slot-editor-footer:has(> [data-terrain-affinity]) {padding-top:4rem!important}',
    '.editor-band:has(.slot-editor-footer > [data-terrain-affinity]) {min-block-size:30rem!important}',
])
def test_zero_intrinsic_growth_rejects_landed_mutation(live_browser, mountain_row, css):
    browser,base_url=live_browser
    with closing(browser.new_page(viewport={"width":1920,"height":1080})) as page:
        _mount(page,base_url,mountain_row)
        _assert_zero_intrinsic_growth(page)
        page.add_style_tag(content=css)
        mutated=_intrinsic_geometry(page)
        page.locator(EDITOR_PROFILE).evaluate("n=>{window.r117IntrinsicRun=n;n.remove()}")
        assert _intrinsic_geometry(page)!=mutated, "G6 sizing mutation must land in measured layout"
        page.evaluate("document.querySelector('.slot-editor-footer').prepend(window.r117IntrinsicRun)")
        with pytest.raises(AssertionError,match="G6 zero intrinsic growth"):
            _assert_zero_intrinsic_growth(page)


def test_candidate_terrain_value_integrity(live_browser, mountain_row):
    browser, base_url = live_browser
    with closing(browser.new_page(viewport={"width": 1920, "height": 1080})) as before:
        _mount(before, base_url, mountain_row, baseline=True)
        _open(before)
        baseline = _candidate_structure(before)
    with closing(browser.new_page(viewport={"width": 1920, "height": 1080})) as page:
        _mount(page, base_url, mountain_row)
        _open(page)
        assert _candidate_structure(page) == baseline, "G8 candidate ranking, signs, numbers and selection markup unchanged"
        assert page.locator(".candidate-terrain > strong").all_text_contents() == ["+10%", "−50%", "0%", "—"], "G8 genuine candidate zero preserved"
        assert page.locator(f".candidate-list {PROFILE}, .candidate-terrain img, .candidate-terrain svg").count() == 0
        for candidate in page.locator(".candidate-row").all():
            assert candidate.locator("scope > *").count() == 4
            assert candidate.locator(".candidate-metric").count() == 4
            assert candidate.locator(".candidate-terrain > *").count() == 2
            assert candidate.locator(".candidate-identity img.splinter-icon").count() == 1
            assert candidate.get_attribute("role") == "button"
            assert candidate.get_attribute("tabindex") == "0"


def test_header_constant_cost_and_nonworker_scope(live_browser, mountain_row):
    browser, base_url = live_browser
    results = []
    for count in (1, 100):
        with closing(browser.new_page(viewport={"width": 1920, "height": 1080})) as page:
            _mount(page, base_url, mountain_row, _candidates(count))
            _open(page)
            assert page.locator(".candidate-row").count() == count
            _assert_placement(page)
            profile = page.locator(PICKER_PROFILE)
            results.append((profile.inner_html(), profile.bounding_box()["height"]))
            for group in ("runi", "totem", "title", "power_core"):
                page.evaluate("""async group=>{
 const {S}=await import('/static/state.js');
 const {renderInlineCandidatePicker}=await import('/static/candidates.js');
 S.CERES_PICKER.groupId=group;
 document.querySelector('.candidate-visible-slice').outerHTML=renderInlineCandidatePicker(S.CERES_PICKER.row);
 }""", group)
                assert page.locator(f".inline-picker {PROFILE}").count() == 0, "G5 worker-only profile"
    assert results[0] == results[1], "G5 constant profile cost for one and one hundred candidates"


def test_shared_model_reaches_both_surfaces(live_browser, mountain_row):
    browser, base_url = live_browser
    source = (ROOT / "ceres/static/format.js").read_text(encoding="utf-8")
    anchor = "Object.freeze(['red', 'Fire'])"
    assert source.count(anchor) == 1, "shared-join mutation anchor must exist exactly once"
    mutated = source.replace(anchor, "Object.freeze(['red', ' model probe'])", 1)
    assert mutated != source
    with closing(browser.new_page(viewport={"width": 1920, "height": 1080})) as page:
        page.route("**/static/format.js*", lambda route: route.fulfill(status=200, content_type="text/javascript", body=mutated))
        _mount(page, base_url, mountain_row)
        _open(page)
        for selector in (EDITOR_PROFILE, PICKER_PROFILE):
            names = page.locator(selector).locator(BADGE).evaluate_all("els=>els.map(el=>el.dataset.splinter)")
            assert "model probe" in names and "Fire" not in names, "model mutation must land in both real renderers"
        with pytest.raises(AssertionError, match="G4 meaning first and default splinter order within state"):
            _assert_badges(page, MOUNTAIN)


VIEWPORT = ".terrain-affinity-viewport"


def _scroll_state(page):
    return page.locator(VIEWPORT).evaluate("""el=>{
 const rect=node=>{const r=node.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height};};
 const root=parseFloat(getComputedStyle(document.documentElement).fontSize), box=rect(el);
 return {root,box,cell:rect(el.closest('.candidate-columns > span')),left:el.scrollLeft,max:el.scrollWidth-el.clientWidth,
 overflow:getComputedStyle(el).overflowX,tab:el.getAttribute('tabindex'),
 focus:{active:el.matches(':focus-visible'),outline:getComputedStyle(el).outlineStyle,width:parseFloat(getComputedStyle(el).outlineWidth),offset:parseFloat(getComputedStyle(el).outlineOffset),shadow:getComputedStyle(el).boxShadow},
 badges:[...el.querySelectorAll('[data-splinter]')].map(b=>({name:b.dataset.splinter,box:rect(b)})),
 edges:[...el.parentElement.querySelectorAll('.terrain-affinity-edge')].map(edge=>({box:rect(edge),shadow:getComputedStyle(edge).boxShadow,outline:getComputedStyle(edge).outlineStyle,outlineWidth:parseFloat(getComputedStyle(edge).outlineWidth),visible:!edge.hidden&&getComputedStyle(edge).display!=='none'&&getComputedStyle(edge).visibility!=='hidden'}))};
 }""")


def _assert_visible_badge(page, name):
    state = _scroll_state(page)
    badge = next(b for b in state["badges"] if b["name"] == name)["box"]
    box = state["box"]
    assert badge["left"] >= box["left"] - 1 and badge["right"] <= box["right"] + 1, f"G7 {name} fully reachable"
    assert badge["top"] >= box["top"] - 1 and badge["bottom"] <= box["bottom"] + 1, "G7 no vertical badge clipping"
    focus=state["focus"]
    assert focus["active"], "G7 endpoint fixture has visible keyboard focus"
    assert focus["shadow"] == "none", f"G7 focus paint must not obscure settled {name}"
    assert focus["outline"] == "none" or focus["width"] == 0 or focus["offset"] >= 0, f"G7 focus paint must not obscure settled {name}"
    for edge in state["edges"]:
        if edge["visible"]:
            assert edge["shadow"] == "none" and (edge["outline"] == "none" or edge["outlineWidth"] == 0), f"G7 shade paint stays inside measured gap beside {name}"
            e = edge["box"]
            overlap = min(e["right"], badge["right"]) - max(e["left"], badge["left"])
            assert overlap <= .5, f"G7 indicator must not cover settled {name}"


def _assert_scroll_contract(page):
    state = _scroll_state(page)
    assert state["max"] > 0, "G7 floor fixture must actually overflow"
    assert state["overflow"] == "auto", "G7 overflow scrolls rather than clips"
    assert state["tab"] == "0", "G7 one overflow-only focus target"
    assert page.locator(f"{PICKER_PROFILE} {BADGE}[tabindex='0']").count() == 6, "G7 every tooltip is keyboard reachable"
    assert state["box"]["height"] == pytest.approx(1.5 * state["root"], abs=.5), "G7 scrollbar consumes no row height"
    assert state["box"]["left"] >= state["cell"]["left"] - 1 and state["box"]["right"] <= state["cell"]["right"] + 1, "G7 viewport stays in Worker cell"
    viewport = page.locator(VIEWPORT)
    viewport.focus()
    viewport.press("Home")
    page.wait_for_function("document.querySelector('.terrain-affinity-viewport').scrollLeft < 1")
    _assert_visible_badge(page, "Fire")
    reached = {"Fire"}
    for _ in range(8):
        viewport.press("ArrowRight")
        page.wait_for_timeout(60)
        current = _scroll_state(page)
        reached.update(b["name"] for b in current["badges"]
                       if b["box"]["left"] >= current["box"]["left"] - 1 and b["box"]["right"] <= current["box"]["right"] + 1)
    assert reached == set(NAMES), "G7 every badge reachable with Right"
    viewport.press("End")
    page.wait_for_function("(()=>{const el=document.querySelector('.terrain-affinity-viewport');return el.scrollLeft>=el.scrollWidth-el.clientWidth-1})()")
    _assert_visible_badge(page, state["badges"][-1]["name"])
    viewport.press("ArrowLeft")
    page.wait_for_function("(()=>{const el=document.querySelector('.terrain-affinity-viewport');return el.scrollLeft<el.scrollWidth-el.clientWidth-1})()")
    viewport.press("Home")
    page.wait_for_function("document.querySelector('.terrain-affinity-viewport').scrollLeft < 1")
    _assert_visible_badge(page, "Fire")
    viewport.hover()
    page.mouse.wheel(500, 0)
    page.wait_for_function("(()=>{const el=document.querySelector('.terrain-affinity-viewport');return el.scrollLeft>=el.scrollWidth-el.clientWidth-1})()")
    _assert_visible_badge(page, state["badges"][-1]["name"])
    page.mouse.wheel(-500, 0)
    page.wait_for_function("document.querySelector('.terrain-affinity-viewport').scrollLeft < 1")
    _assert_visible_badge(page, "Fire")
    _assert_accessible(page)
    _assert_no_visible_copy(page)


@pytest.mark.parametrize("root", [16, 32])
def test_worker_track_floor_scrolls_first_to_last_semantic_peer_and_back(live_browser, mountain_row, root):
    browser, base_url = live_browser
    with closing(browser.new_page(viewport={"width": 1920, "height": 1080})) as page:
        _mount(page, base_url, {**mountain_row, "biome": MIXED})
        _open(page)
        page.evaluate("size=>document.documentElement.style.fontSize=`${size}px`", root)
        page.add_style_tag(content=".candidate-columns.is-worker-columns {display:grid !important;grid-template-columns:1.4rem 8.5rem 6.2rem 6.2rem 4.6rem 6.2rem 6.5rem !important;}")
        page.wait_for_function("document.querySelector('.terrain-affinity-viewport').getAttribute('tabindex')==='0'")
        cell = page.locator(".candidate-columns > span:nth-child(2)").bounding_box()
        assert cell["width"] == pytest.approx(8.5 * root, abs=.5), "G7 measured actual Worker floor"
        _assert_scroll_contract(page)
        page.evaluate("document.querySelector('.candidate-columns').style.setProperty('grid-template-columns','1.4rem 20rem 6.2rem 6.2rem 4.6rem 6.2rem 6.5rem','important')")
        page.wait_for_function("document.querySelector('.terrain-affinity-viewport').getAttribute('tabindex')===null")
        state = _scroll_state(page)
        assert state["max"] <= 1, "G7 resize removes overflow"
        assert not any(edge["visible"] for edge in state["edges"]), "G7 resize removes overflow indicators"
        page.locator(".picker-close").click()
        _open(page)
        page.wait_for_function("document.querySelector('.terrain-affinity-viewport').getAttribute('tabindex')==='0'")
        _assert_scroll_contract(page)


def test_container_boundary_keeps_worker_states_and_metric_registration(live_browser, mountain_row):
    browser, base_url = live_browser
    with closing(browser.new_page(viewport={"width": 1920, "height": 1080})) as page:
        _mount(page, base_url, mountain_row)
        _open(page)
        for width in (865, 863, 865):
            page.locator(".candidate-visible-slice").evaluate("(el,width)=>{el.style.inlineSize=`${width}px`;el.style.maxInlineSize=`${width}px`;}", width)
            labels = page.locator(".candidate-columns > span:not(:nth-child(2))")
            if width < 864:
                assert labels.evaluate_all("els=>els.every(el=>getComputedStyle(el).display==='none')"), "G6 narrow hides only non-Worker labels"
            else:
                assert labels.evaluate_all("els=>els.every(el=>getComputedStyle(el).display!=='none')"), "G6 desktop metric labels retained"
                aligned = page.evaluate("""()=>{
 const heads=[...document.querySelector('.candidate-columns').children].slice(2,6);
 const metrics=[...document.querySelector('.candidate-row').querySelectorAll('.candidate-metric')];
 return heads.map((h,i)=>[h.getBoundingClientRect().left-metrics[i].getBoundingClientRect().left,h.getBoundingClientRect().right-metrics[i].getBoundingClientRect().right]);
 }""")
                assert len(aligned) == 4
                assert all(abs(delta) <= 1 for pair in aligned for delta in pair), "G6 shared metric registration"
            assert page.locator(PICKER_PROFILE).is_visible(), "G6 breakpoint never hides Worker facts"


def _contrast_samples(page):
    return page.locator(BADGE).evaluate_all("""badges=>{
 const canvas=document.createElement('canvas');canvas.width=canvas.height=1;
 const ctx=canvas.getContext('2d',{willReadFrequently:true});
 const rgba=color=>{ctx.clearRect(0,0,1,1);ctx.fillStyle=color;ctx.fillRect(0,0,1,1);return [...ctx.getImageData(0,0,1,1).data];};
 const luminance=color=>rgba(color).slice(0,3).map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);
 const ratio=(a,b)=>{const x=luminance(a),y=luminance(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);};
 return badges.map(badge=>{
 const s=getComputedStyle(badge),icon=badge.querySelector('.splinter-icon'),is=getComputedStyle(icon);
 return {name:badge.dataset.splinter,state:badge.dataset.affinityState,opacity:s.opacity,alpha:rgba(s.backgroundColor)[3],
 image:icon.hasAttribute('src')?icon.currentSrc:'',iconAlpha:rgba(is.backgroundColor)[3],iconOpacity:is.opacity,
 frame:ratio(s.borderTopColor,s.backgroundColor)};
 });
 }""")


def _assert_contrast(page):
    samples = _contrast_samples(page)
    assert len(samples) == 12, "G9 nonempty contrast sample for both surfaces"
    for sample in samples:
        assert sample["alpha"] == 255 and sample["opacity"] == "1", "G9 opaque backing and state"
        assert "/static/assets/splinter/" in sample["image"], "G9 original official image remains visible"
        assert sample["iconOpacity"] == "1", "G9 opaque original icon"
        assert sample["frame"] >= 4.5, "G9 ring ink contrast at least 4.5"


def _sign_style_snapshot(page):
    return page.locator(".candidate-row").evaluate_all("""rows=>rows.map(row=>{
 const cell=row.querySelector('.candidate-terrain'),label=cell.querySelector('small'),icon=row.querySelector('.candidate-identity .splinter-icon');
 const style=el=>{const s=getComputedStyle(el);return {color:s.color,background:s.backgroundColor,border:s.borderColor,filter:s.filter};};
 return {row:style(row),cell:style(cell),label:style(label),icon:style(icon),tooltip:cell.getAttribute('data-tip-body')};
 })""")


@pytest.mark.parametrize("theme", [None, *THEMES])
def test_all_meaningful_channels_meet_contrast_and_candidate_scope(live_browser, mountain_row, theme):
    browser, base_url = live_browser
    before = None
    for baseline in (True, False):
        with closing(browser.new_page(viewport={"width": 1920, "height": 1080})) as page:
            _mount(page, base_url, {**mountain_row, "biome": MIXED}, baseline=baseline)
            _open(page)
            if theme:
                page.evaluate("theme=>document.documentElement.dataset.theme=theme", theme)
            if baseline:
                before = _sign_style_snapshot(page)
            else:
                assert _sign_style_snapshot(page) == before, "G9 sign color confined to candidate figure"
                colored = page.locator("[class*='is-r117-']")
                assert colored.count() == 2, "G9 only known positive/negative figures colored"
                assert colored.evaluate_all("els=>els.every(el=>el.matches('.candidate-terrain > strong'))"), "G9 color scope"
                _assert_contrast(page)
                _assert_candidate_figure_contrast(page)
                page.locator(".candidate-row").nth(1).hover()
                _assert_contrast(page)
                _assert_candidate_figure_contrast(page)
                page.emulate_media(forced_colors="active")
                _assert_badges(page, MIXED)
                _assert_contrast(page)


def test_profiles_update_from_plot_without_candidate_selection(live_browser, mountain_row):
    browser, base_url = live_browser
    with closing(browser.new_page(viewport={"width": 1920, "height": 1080})) as page:
        requests = []
        page.on("request", lambda request: requests.append(request.url) if "/api/" in request.url else None)
        _mount(page, base_url, mountain_row)
        _open(page)
        first = page.locator(PICKER_PROFILE).inner_html()
        candidate_before = _candidate_structure(page)
        before_requests = len(requests)
        page.evaluate("""async row=>{
 const {S}=await import('/static/state.js');
 const {BOARD}=await import('/static/board.js');
 const {renderSlotEditor}=await import('/static/editor.js');
 const {beforePlotContentReplace,mountPlotPresentation}=await import('/static/eos.js');
 const {initPickerFilterDropdowns}=await import('/static/picker-actions.js');
 Object.assign(BOARD.plots.find(plot=>plot.deed_uid===row.deed_uid),row);
 S.CERES_PICKER.row=row;
 beforePlotContentReplace(row.deed_uid);
 document.querySelector('.slot-editor').outerHTML=renderSlotEditor(row);
 mountPlotPresentation(row, document.querySelector('.editor-band'), {dataReady:true});
 initPickerFilterDropdowns(document);
 }""", {**mountain_row, "biome": MIXED})
        _wait_presentation(page)
        assert page.locator(PICKER_PROFILE).inner_html() != first, "G8 plot change must refresh header"
        _assert_badges(page, MIXED)
        _assert_accessible(page)
        assert _candidate_structure(page) == candidate_before, "G8 independent plot facts leave candidates intact"
        assert len(requests) == before_requests, "G8 affinity introduces no API requests"
        page.locator("[data-picker-action='sort'][data-sort='name']").click()
        page.wait_for_function("document.querySelector('[data-sort=\"name\"]').classList.contains('is-active')")
        _assert_badges(page, MIXED)
        _assert_placement(page)


@pytest.mark.parametrize("status", ["loading", "empty", "error"])
def test_header_fact_survives_candidate_response_states(live_browser, mountain_row, status):
    browser, base_url = live_browser
    with closing(browser.new_page(viewport={"width": 1920, "height": 1080})) as page:
        _mount(page, base_url, {**mountain_row, "biome": MIXED}, [])
        _open(page, empty=True)
        page.evaluate("""async status=>{
 const {S}=await import('/static/state.js');
 const {renderInlineCandidatePicker}=await import('/static/candidates.js');
 S.CERES_PICKER.loading=status==='loading';
 S.CERES_PICKER.error=status==='error'?' injected candidate error':null;
 S.CERES_PICKER.candidates=[];
 document.querySelector('.candidate-visible-slice').outerHTML=renderInlineCandidatePicker(S.CERES_PICKER.row);
 }""", status)
        _assert_placement(page)
        _assert_badges(page, MIXED)
        _assert_accessible(page)


def _render_plaque_case(page, row, status):
    page.evaluate("""async ({row,status})=>{
 const {CERES_STAGE}=await import('/static/staging.js');
 const {renderSlotEditor}=await import('/static/editor.js');
 const {beforePlotContentReplace,mountPlotPresentation}=await import('/static/eos.js');
 CERES_STAGE.splice(0);
 if(status!=='unstaged') CERES_STAGE.push({deed_uid:row.deed_uid,group_id:'worker',slot_index:0,action:'stake',
 _asset:row.workers[0],base:{status:status==='overcap'?'point':status,value:status==='unknown'?null:1000000,low:500000,high:1000000}});
 beforePlotContentReplace(row.deed_uid);
 document.querySelector('.slot-editor').outerHTML=renderSlotEditor(row);
 mountPlotPresentation(row, document.querySelector('.editor-band'), {dataReady:true});
 }""", {"row": row, "status": status})
    _wait_presentation(page)


def _assert_plaque(page, *, present, cap=False):
    _assert_placement(page, picker=False)
    plaque = page.locator(".editor-change-badges .editor-change-badge").filter(has_text="Total Base")
    assert plaque.count() == int(present), "G9 staged Total Base badge conditions"
    if not present:
        return
    measure = plaque.evaluate("""el=>{
 const rect=node=>{const r=node.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height};};
 const anchor=el.parentElement,run=document.querySelector('.slot-editor-footer > [data-terrain-affinity]');
 const content=[...el.querySelectorAll('strong,.change-badge-cap')];
 return {root:parseFloat(getComputedStyle(document.documentElement).fontSize),box:rect(el),anchor:rect(anchor),run:rect(run),
 content:content.map(node=>{const s=getComputedStyle(node),range=document.createRange();range.selectNodeContents(node);return {box:rect(node),text:node.textContent,visible:s.display!=='none'&&s.visibility==='visible'&&s.opacity==='1',
 font:parseFloat(s.fontSize),overflow:node.scrollWidth-node.clientWidth,heightClipped:['hidden','clip'].includes(s.overflowY)&&node.scrollHeight>node.clientHeight+1,lines:[...range.getClientRects()].map(r=>({left:r.left,right:r.right,top:r.top,bottom:r.bottom}))};})};
 }""")
    box, run, root = measure["box"], measure["run"], measure["root"]
    # one line, never two -- a wrapped cap suffix would push the box past ~1.1 rem.
    assert box["height"] <= 1.2 * root + .5, "G9 Total Base badge stays on one line"
    assert box["bottom"] <= run["top"] - .25 * root + .5, "G9 Total Base badge and terrain run separated by four pixels"
    for child in measure["content"]:
        assert child["visible"] and child["text"].strip(), "G9 meaningful Total Base badge content visible"
        assert child["overflow"] <= 1, "G9 no ellipsis or horizontal clipping"
        assert not child["heightClipped"], "G9 complete Total Base badge text fits height"
        for line in child["lines"]:
            assert line["left"] >= box["left"] - .5 and line["right"] <= box["right"] + .5, "G9 complete plaque text fits width"
            assert line["top"] >= box["top"] - .5 and line["bottom"] <= box["bottom"] + .5, "G9 complete Total Base badge text fits height"
    assert plaque.locator("strong").evaluate("el=>parseFloat(getComputedStyle(el).fontSize)") == pytest.approx(.66 * root, abs=.5), "G9 figure remains readable"
    if cap:
        warning = plaque.locator(".change-badge-cap")
        assert warning.count() == 1 and "cap" in warning.inner_text() and "−" in warning.inner_text(), "G9 over-cap warning remains complete"
        assert warning.evaluate("el=>parseFloat(getComputedStyle(el).fontSize)") >= .66 * root - .1, "G9 cap warning not shrunk"


@pytest.mark.parametrize("root", [16, 32])
@pytest.mark.parametrize("status", ["unstaged", "point", "range", "unknown", "overcap"])
def test_plaque_and_badges_coexist_in_existing_art_rectangle(live_browser, mountain_row, root, status):
    browser, base_url = live_browser
    row = copy.deepcopy(mountain_row)
    row.update(base_pp=9999999, base_pp_after_cap=1000000, base_pp_cap=1000000 if status == "overcap" else 99999999)
    with closing(browser.new_page(viewport={"width": 1920, "height": 1080})) as page:
        _mount(page, base_url, row)
        page.evaluate("size=>document.documentElement.style.fontSize=`${size}px`", root)
        _render_plaque_case(page, row, status)
        _assert_plaque(page, present=status in ("point", "overcap"), cap=status == "overcap")
        _assert_badges(page, MOUNTAIN)
        assert page.locator(EDITOR_PROFILE).evaluate("el=>getComputedStyle(el).pointerEvents") == "none"
        page.locator(".worksite-change-btn").click(trial=True)
        page.locator(".slot-group[data-group-id='worker'] .slot-tile[data-actionable='1']").first.click(trial=True)


def test_empty_plot_missing_biome_and_missing_scene_still_show_badges(live_browser, mountain_row):
    browser, base_url = live_browser
    row = copy.deepcopy(mountain_row)
    row.update(workers=[], terrain=None, biome={}, worksite_type=None)
    row["capacity"].update(worker_count=0, open_slots=5)
    with closing(browser.new_page(viewport={"width": 1920, "height": 1080})) as page:
        _mount(page, base_url, row)
        _assert_placement(page, picker=False)
        assert page.locator(EDITOR_PROFILE).locator(BADGE).count() == 7
        assert page.locator(f"{EDITOR_PROFILE} [data-affinity-state='unknown']").count() == 6, "G9 no plaque/scene condition may hide unknown facts"
        page.locator(".editor-scene").evaluate("el=>el.remove()")
        assert page.locator(EDITOR_PROFILE).is_visible(), "G9 facts independent of decorative art"


@pytest.mark.parametrize("mutated", [False, True])
def test_no_button_source_fixture_keeps_reserved_affinity_center(live_browser, mountain_row, mutated):
    browser,base_url=live_browser
    row={**mountain_row,"swap_targets":[],"worksite_completion":None}
    with closing(browser.new_page(viewport={"width":1920,"height":1080})) as page:
        _mount(page,base_url,row)
        result=page.evaluate("""async row=>{
 const {worksiteChangeButtonHtml}=await import('/static/worksite.js');
 return worksiteChangeButtonHtml(row);
 }""",row)
        assert result == "" and page.locator('.slot-editor-footer > .worksite-change-btn').count()==0, "G5 real no-button fixture exercises worksite helper"
        _assert_placement(page,picker=False)
        _assert_fit(page)
        if mutated:
            assert page.locator(EDITOR_PROFILE).evaluate("n=>{n.style.left='0px';return getComputedStyle(n).left==='0px'}"), "G6 missing-button offset mutation landed"
            with pytest.raises(AssertionError,match="G6 run keeps reserved centre"):
                _assert_fit(page)


def test_identity_and_state_geometry_are_separate(live_browser, mountain_row):
    browser, base_url = live_browser
    with closing(browser.new_page(viewport={"width": 1920, "height": 1080})) as page:
        _mount(page, base_url, {**mountain_row, "biome": MIXED})
        _open(page)
        samples = page.locator(BADGE).evaluate_all("""els=>els.map(el=>{
 const i=el.querySelector('.splinter-icon').getBoundingClientRect(),b=el.getBoundingClientRect();
 return {icon:[i.width,i.height],bubble:[b.width,b.height],svg:el.querySelectorAll('svg').length};
 })""")
        assert len(samples) == 12
        for sample in samples:
            assert sample["icon"] == pytest.approx([16, 16], abs=.5), "G6 official silhouette remains 16px"
            assert sample["bubble"] == pytest.approx([24, 24], abs=.5), "G6 ring stays inside fixed bubble"
            assert sample["svg"] == 0, "G6 no state symbol overlays identity"


@pytest.mark.parametrize(("value", "expected"), [(.000001, None),
                                                    (-.000001, None),
                                                    (1.7976931348623157e308, "plus 1.7976931348623157e+310 percent")])
def test_accessible_names_preserve_exact_magnitude(live_browser, mountain_row, value, expected):
    browser, base_url = live_browser
    with closing(browser.new_page(viewport={"width": 1920, "height": 1080})) as page:
        _mount(page, base_url, {**mountain_row, "biome": {**MOUNTAIN, "red": value}})
        _open(page)
        if expected is None:
            expected = page.evaluate("value=>`${value>0?'plus':'minus'} ${Math.abs(value*100)} percent`", value)
        for profile in page.locator(PROFILE).all():
            fire = profile.locator("[data-splinter='Fire']")
            assert expected in fire.get_attribute("aria-label"), "G4 exact accessible finite magnitude"
            assert fire.get_attribute("data-affinity-value") is not None
        _assert_no_visible_copy(page)


def test_overflow_binding_cleanup_on_rerender_close_and_rebind(live_browser, mountain_row):
    browser, base_url = live_browser
    with closing(browser.new_page(viewport={"width": 1920, "height": 1080})) as page:
        _mount(page, base_url, mountain_row)
        _open(page)
        page.add_style_tag(content=".candidate-worker-affinity {max-inline-size:8.5rem !important}")
        page.wait_for_function("document.querySelector('.terrain-affinity-viewport').hasAttribute('tabindex')")
        page.evaluate("window.__r117OldViewport=document.querySelector('.terrain-affinity-viewport')")
        page.locator("[data-picker-action='sort'][data-sort='name']").click()
        page.wait_for_function("window.__r117OldViewport!==document.querySelector('.terrain-affinity-viewport')")
        page.wait_for_function("window.__r117OldViewport._terrainAffinityCleanup===undefined")
        page.evaluate("window.__r117OldViewport=document.querySelector('.terrain-affinity-viewport')")
        page.locator(".picker-close").click()
        page.wait_for_function("window.__r117OldViewport._terrainAffinityCleanup===undefined")
        _open(page)
        page.wait_for_function("document.querySelector('.terrain-affinity-viewport').hasAttribute('tabindex')")
        result = page.evaluate("""async()=>{
 const {bindTerrainAffinityOverflow}=await import('/static/terrainAffinity.js');
 const host=document.querySelector('.candidate-worker-affinity'),viewport=host.querySelector('.terrain-affinity-viewport');
 bindTerrainAffinityOverflow(host);const cleanup=bindTerrainAffinityOverflow(host);cleanup();
 viewport.scrollLeft=0;const event=new KeyboardEvent('keydown',{key:'End',bubbles:true,cancelable:true});viewport.dispatchEvent(event);
 return {prevented:event.defaultPrevented,scroll:viewport.scrollLeft,cleared:viewport._terrainAffinityCleanup===undefined};
 }""")
        assert result == {"prevented": False, "scroll": 0, "cleared": True}, "G7 cleanup removes old navigation handlers"


RENDER_MUTATIONS = [
    ("stale_plot_profile", "const el=document.querySelector('.candidate-columns [data-splinter=Life]');el.dataset.affinityState='unchanged';el.setAttribute('aria-label','Life, Mountain terrain modifier, zero percent, no change');return el.dataset.affinityState==='unchanged';", "badges", "G4 known zero differs from unavailable"),
    ("candidate_as_plot_source", "const el=document.querySelector('.candidate-columns [data-splinter=Fire]');el.dataset.affinityState='penalised';return el.dataset.affinityState==='penalised';", "badges", "G4 known zero differs from unavailable"),
    ("wide_tracks", "const el=document.querySelector('.inline-picker');el.style.setProperty('--candidate-worker-columns','1.4rem 160rem 6.2rem 6.2rem 4.6rem 6.2rem 6.5rem');return getComputedStyle(document.querySelector('.candidate-columns')).gridTemplateColumns.includes('2560px');", "fit", "G6 local overflow cannot widen picker"),
    ("wrap_bubbles", "const el=document.querySelector('[data-terrain-affinity]');el.style.minInlineSize='3.5rem';el.style.inlineSize='3.5rem';el.style.flexWrap='wrap';return getComputedStyle(el).flexWrap==='wrap';", "fit", "G6 existing 192px budget"),
    ("hide_responsive_worker", "const el=document.querySelector('.candidate-columns > span:nth-child(2)');el.style.display='none';return getComputedStyle(el).display==='none';", "visibility", "G6 breakpoint never hides Worker facts"),
    ("low_contrast_unknown", "const el=document.querySelector('[data-affinity-state=unknown]');el.style.color=getComputedStyle(el).backgroundColor;return getComputedStyle(el).color===getComputedStyle(el).backgroundColor;", "contrast", "G9 ring ink contrast at least 4.5"),
    ("candidate_row_color_leak", "const el=document.querySelector('.candidate-row');el.style.color='#123456';return getComputedStyle(el).color==='rgb(18, 52, 86)';", "sign_scope", "G9 sign color confined to candidate figure"),
    ("candidate_zero_lost", "const el=document.querySelectorAll('.candidate-terrain > strong')[2];el.textContent='—';return el.textContent==='—';", "candidate_values", "G8 genuine candidate zero preserved"),
    ("unknown_hidden", "const el=document.querySelector('[data-affinity-state=unknown]');el.style.display='none';return getComputedStyle(el).display==='none';", "badges", "G4 unavailable visibly opaque"),
    ("unknown_dimmed", "const el=document.querySelector('[data-affinity-state=unknown]');el.style.opacity='.4';return getComputedStyle(el).opacity==='0.4';", "badges", "G4 unavailable visibly opaque"),
    ("zero_unknown", "const el=document.querySelector('[data-splinter=Dragon]');el.dataset.affinityState='unknown';return el.dataset.affinityState==='unknown';", "badges", "G4 known zero differs from unavailable"),
    ("swap_bonus_default_order", "const group=document.querySelector('[data-terrain-affinity]');group.prepend(group.querySelector('[data-splinter=Death]'));return group.firstElementChild.dataset.splinter==='Death';", "badges", "G4 meaning first and default splinter order within state"),
    ("penalty_before_bonus", "const group=document.querySelector('[data-terrain-affinity]');group.prepend(group.querySelector('[data-affinity-state=penalised]'));return group.firstElementChild.dataset.affinityState==='penalised';", "badges", "G4 meaning first and default splinter order within state"),
    ("restore_neutral_bubble", "const group=document.querySelector('[data-terrain-affinity]'),el=group.firstElementChild.cloneNode(true);el.dataset.splinter='Neutral';group.append(el);return group.children.length===7;", "badges", "G4 six factual badges without Neutral"),
    ("unknown_zero_name", "const el=document.querySelector('[data-affinity-state=unknown]');el.setAttribute('aria-label','Life, Mountain terrain modifier, zero percent');return el.getAttribute('aria-label').endsWith('zero percent');", "badges", "G4 factual state name"),
    ("missing_name", "const el=document.querySelector('[data-splinter=Fire]');el.removeAttribute('aria-label');return !el.hasAttribute('aria-label');", "badges", "G4 factual identity name"),
    ("remove_tooltip_tabstop", "const el=document.querySelector('[data-affinity-state=favoured]');el.removeAttribute('tabindex');return !el.hasAttribute('tabindex');", "badges", "G7 each tooltip has keyboard access"),
    ("hidden_header", "const el=document.querySelector('.candidate-columns');el.setAttribute('aria-hidden','true');return el.getAttribute('aria-hidden')==='true';", "accessible", "G4 named group in accessibility tree"),
    ("visible_percent", "const el=document.querySelector('[data-terrain-affinity]');el.append('Fire +10%');return el.textContent.includes('Fire +10%');", "copy", "G4 no visible affinity prose"),
    ("missing_tooltip", "const el=document.querySelector('[data-affinity-state=favoured]');el.removeAttribute('data-tip');return !el.hasAttribute('data-tip');", "badges", "G4 tooltip names splinter"),
    ("unknown_tooltip_zero", "const el=document.querySelector('[data-affinity-state=unknown]');el.setAttribute('data-tip-key','0%');return el.dataset.tipKey==='0%';", "badges", "G4 unavailable tooltip never fabricates zero"),
    ("bonus_tooltip_zero", "const el=document.querySelector('[data-affinity-state=favoured]');el.setAttribute('data-tip-key','0%');return el.dataset.tipKey==='0%';", "badges", "G4 tooltip carries signed modifier"),
    ("native_tooltip", "const el=document.querySelector('[data-affinity-state=favoured]');el.setAttribute('title','Fire');return el.title==='Fire';", "copy", "G4 no surface text/bar/native tooltip"),
    ("generated_symbol", "const s=document.createElement('style');s.textContent='.terrain-affinity-badge::after{content:\"?\"}';document.head.append(s);return getComputedStyle(document.querySelector('[data-splinter=Fire]'),'::after').content==='\"?\"';", "copy", "G4 no generated symbol content"),
    ("edge_symbol", "const s=document.createElement('style');s.textContent='.terrain-affinity-edge::after{content:\"→\"}';document.head.append(s);return getComputedStyle(document.querySelector('.terrain-affinity-edge'),'::after').content==='\"→\"';", "copy", "G4 no generated symbol content"),
    ("candidate_profile", "const el=document.querySelector('[data-terrain-affinity]').cloneNode(true);document.querySelector('.candidate-row').append(el);return !!el.closest('.candidate-row');", "placement", "G5 no repeated/decorative run"),
    ("collapsed_profile", "const el=document.querySelector('[data-terrain-affinity]').cloneNode(true);document.querySelector('tr.plot-row td').append(el);return !!el.closest('tr.plot-row');", "placement", "G5 no repeated/decorative run"),
    ("terrain_header", "const el=document.querySelector('.candidate-columns [data-terrain-affinity]');document.querySelector('.candidate-columns > span:nth-child(5)').append(el);return el.parentElement===document.querySelector('.candidate-columns > span:nth-child(5)');", "placement", "G5 Worker identity header owns run"),
    ("conditional_footer", "const el=document.querySelector('.slot-editor-footer > [data-terrain-affinity]');el.remove();return !document.querySelector('.slot-editor-footer > [data-terrain-affinity]');", "placement", "G5 editor run is an unconditional footer child"),
    ("shift_center", "const el=document.querySelector('.slot-editor-footer > [data-terrain-affinity]');el.style.transform='translateX(20px)';return getComputedStyle(el).transform==='matrix(1, 0, 0, 1, 20, 0)';", "fit", "G6 run keeps reserved centre when worksite button is absent"),
    ("shift_button", "const el=document.querySelector('.slot-editor-footer > .worksite-change-btn');el.style.marginLeft='40px';return getComputedStyle(el).marginLeft==='40px';", "fit", "G6 run centred on worksite button"),
    ("button_under_run", "const el=document.querySelector('.worksite-change-btn'),run=document.querySelector('.slot-editor-footer > [data-terrain-affinity]').getBoundingClientRect();const box=el.getBoundingClientRect();el.style.transform=`translateY(${run.top-box.top}px)`;return Math.abs(el.getBoundingClientRect().top-run.top)<.5;", "fit", "G6 bubbles leave worksite control unobstructed"),
    ("tile_under_run", "const el=document.querySelector('.editor-band .slot-tile'),run=document.querySelector('.slot-editor-footer > [data-terrain-affinity]').getBoundingClientRect();const box=el.getBoundingClientRect();el.style.transform=`translate(${run.left-box.left}px,${run.top-box.top}px)`;return Math.abs(el.getBoundingClientRect().left-run.left)<.5;", "fit", "G6 bubbles leave worker tiles unobstructed"),
    ("no_button_reserve_center", "const button=document.querySelector('.slot-editor-footer > .worksite-change-btn');button?.remove();const run=document.querySelector('.slot-editor-footer > [data-terrain-affinity]');run.style.left='0px';return !document.querySelector('.slot-editor-footer > .worksite-change-btn')&&getComputedStyle(run).left==='0px';", "fit", "G6 run keeps reserved centre when worksite button is absent"),
    ("inherited_footer_gate", "const footer=document.querySelector('.slot-editor-footer');footer.inert=true;return footer.inert===true;", "fit", "G19 affinity remains on scenic release when footer gate is inherited"),
    ("extra_band_height", "const el=document.querySelector('.editor-band'),h=el.getBoundingClientRect().height;el.style.minHeight=`${h+68}px`;return el.getBoundingClientRect().height>=h+67;", "geometry", "G6 zero added"),
    ("wide_badge", "const el=document.querySelector('[data-splinter=Fire]');el.style.minWidth='600px';return el.getBoundingClientRect().width>=600;", "fit", "G6 bubble width"),
    ("transparent_backing", "const el=document.querySelector('[data-splinter=Fire]');el.style.background='transparent';return getComputedStyle(el).backgroundColor==='rgba(0, 0, 0, 0)';", "contrast", "G9 opaque backing and state"),
    ("missing_identity", "const el=document.querySelector('[data-splinter=Fire] .splinter-icon');el.removeAttribute('src');return !el.hasAttribute('src');", "contrast", "G9 original official image remains visible"),
    ("same_ink", "const el=document.querySelector('[data-splinter=Fire]');el.style.color=getComputedStyle(el).backgroundColor;return getComputedStyle(el).color===getComputedStyle(el).backgroundColor;", "contrast", "G9 ring ink contrast at least 4.5"),
    ("overlay_intercepts", "const el=document.querySelector('.slot-editor-footer > [data-terrain-affinity]');el.style.pointerEvents='auto';return getComputedStyle(el).pointerEvents==='auto';", "fit", "G9 static overlay cannot intercept controls"),
]


@pytest.mark.parametrize(("mutation", "script", "guard", "message"), RENDER_MUTATIONS, ids=[m[0] for m in RENDER_MUTATIONS])
def test_inline_guard_rejects_landed_render_mutation(live_browser, mountain_row, mutation, script, guard, message):
    browser, base_url = live_browser
    with closing(browser.new_page(viewport={"width": 1920, "height": 1080})) as page:
        _mount(page, base_url, {**mountain_row, "biome": MIXED})
        _open(page)
        before = _geometry(page)
        before_signs = _sign_style_snapshot(page)
        guards = {"badges": lambda: _assert_badges(page, MIXED), "accessible": lambda: _assert_accessible(page),
                  "copy": lambda: _assert_no_visible_copy(page), "placement": lambda: _assert_placement(page),
                  "geometry": lambda: _assert_geometry(_geometry(page), before), "fit": lambda: _assert_fit(page),
                  "contrast": lambda: _assert_contrast(page),
                  "visibility": lambda: _assert_worker_visible(page),
                  "sign_scope": lambda: _assert_sign_scope(page, before_signs),
                  "candidate_values": lambda: _assert_candidate_values(page)}
        guards[guard]()
        assert page.evaluate(f"() => {{{script}}}") is True, f"{mutation}: fault must actually land"
        with pytest.raises(AssertionError, match=message):
            guards[guard]()


@pytest.mark.parametrize(("mutation", "script", "message"), [
    ("clip", "el.style.overflowX='hidden';return getComputedStyle(el).overflowX==='hidden';", "G7 overflow scrolls rather than clips"),
    ("remove_focus", "el.removeAttribute('tabindex');return !el.hasAttribute('tabindex');", "G7 one overflow-only focus target"),
    ("disable_keys", "el.addEventListener('keydown',event=>event.stopImmediatePropagation(),true);el.dataset.r117Fault='keys';return el.dataset.r117Fault==='keys';", "G7 every badge reachable with Right"),
    ("grow_scrollbar", "el.style.height='40px';return el.getBoundingClientRect().height===40;", "G7 scrollbar consumes no row height"),
    ("cover_endpoint", "const edge=el.parentElement.querySelector('.terrain-affinity-edge.is-end');edge.style.setProperty('left','0px','important');edge.style.setProperty('width','20px','important');return getComputedStyle(edge).left==='0px';", "G7 indicator must not cover settled Fire"),
    ("focus_over_ring", "el.style.outline='2px solid white';el.style.outlineOffset='-2px';return getComputedStyle(el).outlineOffset==='-2px';", "G7 focus paint must not obscure settled Fire"),
    ("focus_inset_shadow", "el.style.boxShadow='inset 0 0 0 2px white';return getComputedStyle(el).boxShadow!=='none';", "G7 focus paint must not obscure settled Fire"),
    ("shade_shadow", "const edge=el.parentElement.querySelector('.terrain-affinity-edge.is-end');edge.style.boxShadow='0 0 0 8px white';return getComputedStyle(edge).boxShadow!=='none';", "G7 shade paint stays inside measured gap beside Fire"),
])
def test_scroll_guard_rejects_landed_mutation(live_browser, mountain_row, mutation, script, message):
    browser, base_url = live_browser
    with closing(browser.new_page(viewport={"width": 1920, "height": 1080})) as page:
        _mount(page, base_url, {**mountain_row, "biome": MIXED})
        _open(page)
        page.add_style_tag(content=".candidate-worker-affinity {max-inline-size:8.5rem !important}")
        page.wait_for_function("document.querySelector('.terrain-affinity-viewport').getAttribute('tabindex')==='0'")
        _assert_scroll_contract(page)
        assert page.locator(VIEWPORT).evaluate(f"el=>{{{script}}}") is True, f"{mutation}: fault must actually land"
        with pytest.raises(AssertionError, match=message):
            _assert_scroll_contract(page)


@pytest.mark.parametrize(("mutation", "script", "message"), [
    ("move_plaque_over_run", "const run=document.querySelector('.slot-editor-footer > [data-terrain-affinity]').getBoundingClientRect();const box=el.getBoundingClientRect();el.style.transform=`translateY(${run.top-box.bottom+4}px)`;return el.getBoundingClientRect().bottom>run.top;", "G9 Total Base badge and terrain run separated by four pixels"),
    ("clip_cap", "const cap=el.querySelector('.change-badge-cap');cap.style.height='1px';cap.style.overflow='hidden';return cap.clientHeight<=2;", "G9 complete Total Base badge text fits height"),
    ("hide_cap", "const cap=el.querySelector('.change-badge-cap');cap.style.display='none';return getComputedStyle(cap).display==='none';", "G9 meaningful Total Base badge content visible"),
])
def test_plaque_guard_rejects_landed_mutation(live_browser, mountain_row, mutation, script, message):
    browser, base_url = live_browser
    row = {**mountain_row, "base_pp": 9999999, "base_pp_after_cap": 1000000, "base_pp_cap": 1000000}
    with closing(browser.new_page(viewport={"width": 1920, "height": 1080})) as page:
        _mount(page, base_url, row)
        _render_plaque_case(page, row, "overcap")
        _assert_plaque(page, present=True, cap=True)
        assert page.locator('.editor-change-badges .editor-change-badge').filter(has_text="Total Base").evaluate(f"el=>{{{script}}}") is True, f"{mutation}: fault must actually land"
        with pytest.raises(AssertionError, match=message):
            _assert_plaque(page, present=True, cap=True)


def _assert_candidate_figure_contrast(page):
    samples = page.locator(".candidate-terrain > strong").evaluate_all("""els=>{
 const canvas=document.createElement('canvas');canvas.width=canvas.height=1;const ctx=canvas.getContext('2d',{willReadFrequently:true});
 const lum=color=>{ctx.clearRect(0,0,1,1);ctx.fillStyle=color;ctx.fillRect(0,0,1,1);return [...ctx.getImageData(0,0,1,1).data].slice(0,3).map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);};
 const ratio=(a,b)=>{const x=lum(a),y=lum(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);};
 return els.filter(el=>el.className.includes('is-r117-')).map(el=>{
 const row=el.closest('.candidate-row'),s=getComputedStyle(row),color=getComputedStyle(el).color;
 const probe=document.createElement('span');row.append(probe);probe.style.color='var(--bg3)';const bg3=getComputedStyle(probe).color;probe.style.color='var(--bg)';const bg=getComputedStyle(probe).color;probe.remove();
 return {color,ratios:[s.backgroundColor,bg,bg3].map(back=>ratio(color,back))};
 });
 }""")
    assert len(samples) == 2 and samples[0]["color"] != samples[1]["color"], "G9 two scoped signed figure samples"
    assert all(ratio >= 4.5 for sample in samples for ratio in sample["ratios"]), "G9 signed figures meet 4.5 against normal/top/hover backing"


def _assert_worker_visible(page):
    assert page.locator(PICKER_PROFILE).is_visible(), "G6 breakpoint never hides Worker facts"


def _assert_sign_scope(page, expected):
    assert _sign_style_snapshot(page) == expected, "G9 sign color confined to candidate figure"


def _assert_candidate_values(page):
    assert page.locator('.candidate-terrain > strong').all_text_contents() == ['+10%', '−50%', '0%', '—'], "G8 genuine candidate zero preserved"
