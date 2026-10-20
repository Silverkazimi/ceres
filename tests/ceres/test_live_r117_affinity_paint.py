"""Independent browser paint guards for the R-117 affinity medallions.

These checks deliberately inspect pixels from a real rendered page.  They are
kept in their own dedicated queue because class names alone cannot prove the
four boundaries remain visually distinct.
"""

from __future__ import annotations

import math
from contextlib import closing
from io import BytesIO
from itertools import combinations
from pathlib import Path
from urllib.parse import urlparse

import pytest
from PIL import Image
from playwright.sync_api import sync_playwright

NAMES = ["Fire", "Water", "Earth", "Life", "Death", "Dragon"]
PROFILE = "[data-terrain-affinity]"
BADGE = ".terrain-affinity-badge"
ASSET_SUFFIX = {name: f"/static/assets/splinter/{name.lower()}.svg" for name in NAMES}
STATES = ("favoured", "penalised", "unchanged", "unknown")
STATIC = Path(__file__).resolve().parents[2] / "ceres" / "static"
COLOURS = dict(zip(NAMES, ("EA7474", "5EBCF2", "5DD978", "FFE694", "B181F3", "DA9656")))


@pytest.fixture(scope="module")
def paint_browser():
    with sync_playwright() as playwright:
        with closing(playwright.chromium.launch()) as browser:
            yield browser


def _paint_fixture(page):
    def route_local(route):
        path = urlparse(route.request.url).path
        if path == "/":
            route.fulfill(content_type="text/html", body='<!doctype html><html style="font-size:16px"><head><link rel="stylesheet" href="/static/ceres.css"></head><body></body></html>')
            return
        asset = (STATIC / path.removeprefix("/static/")).resolve()
        if not path.startswith("/static/") or not asset.is_relative_to(STATIC) or not asset.is_file():
            route.fulfill(status=404, body="missing fixture asset")
            return
        route.fulfill(path=str(asset))

    page.route("**/*", route_local)
    page.goto("http://r117.test/")
    page.evaluate("""async () => {
 await import('/static/wallet.js'); // the app's module entry order: board.js must finish before wallet.js reads it
 const {terrainAffinityHtml} = await import('/static/terrainAffinity.js');
 const row = {plot:1, terrain:'Mountain', biome:{red:.1, blue:-.5, green:0, white:null, black:0, gold:.1}};
 document.body.style.cssText = 'margin:0;padding:0;background:#101010';
 for (const [i, surface] of ['editor','picker'].entries()) {
 const host = document.createElement('div');
 host.style.cssText = `position:absolute;left:24px;top:${100 + i * 60}px;width:400px`;
 host.innerHTML = terrainAffinityHtml(row, surface);
 document.body.append(host);
 }
 await Promise.all([...document.querySelectorAll('.splinter-icon')].map(img => img.decode()));
 }""")
    page.wait_for_function("""() => {
      const images = [...document.querySelectorAll('[data-terrain-affinity] .splinter-icon')];
      return images.length === 12 && images.every(img => img.complete && img.naturalWidth > 0);
    }""")


def _fire_states(page):
    page.evaluate("""async () => {
 await import('/static/wallet.js'); // the app's module entry order: board.js must finish before wallet.js reads it
 const {terrainAffinityHtml} = await import('/static/terrainAffinity.js');
 const host = document.createElement('div');
 host.id = 'r117-paint-probe'; host.className = 'terrain-affinity';
 host.style.cssText = 'position:fixed;left:24px;top:24px;display:flex;gap:4px;z-index:9999;inline-size:auto;min-inline-size:0';
 for (const value of [.1, -.5, 0, null]) {
 const wrapper = document.createElement('div');
 wrapper.innerHTML = terrainAffinityHtml({terrain:'Mountain', biome:{red:value}}, 'editor');
 host.append(wrapper.querySelector('[data-splinter="Fire"]'));
 }
 document.body.append(host);
 await Promise.all([...host.querySelectorAll('img')].map(img => img.decode()));
 }""")


def _sample_bubbles(page):
    boxes = page.locator('#r117-paint-probe .terrain-affinity-badge').evaluate_all(
        "els => els.map(el => { const r=el.getBoundingClientRect(); return {state:el.dataset.affinityState, splinter:el.dataset.splinter, x:r.x, y:r.y, width:r.width, height:r.height}; })"
    )
    screenshot = Image.open(BytesIO(page.screenshot())).convert('RGBA')
    viewport = page.evaluate("() => [innerWidth, innerHeight]")
    scale_x, scale_y = screenshot.width / viewport[0], screenshot.height / viewport[1]

    def pixel(x, y):
        assert 0 <= x < viewport[0] and 0 <= y < viewport[1], "G11 sample within viewport"
        return screenshot.getpixel((math.floor(x * scale_x), math.floor(y * scale_y)))

    samples = []
    for box in boxes:
        cx, cy = box['x'] + box['width'] / 2, box['y'] + box['height'] / 2
        root = float(page.evaluate("() => parseFloat(getComputedStyle(document.documentElement).fontSize)"))
        radial = [pixel(cx + root * distance, cy) for distance in (0.59375, 0.65625, 0.71875)]
        arc_angles = [20 + 60 * i for i in range(6)]
        gap_angles = [50 + 60 * i for i in range(6)]
        def sample_angle(degrees, radii=(.69, .71875, .74)):
            radians = math.radians(degrees)
            return max(
                (pixel(cx + root * radius * math.sin(radians), cy - root * radius * math.cos(radians))
                 for radius in radii),
                key=_luma,
            )
        angles = [sample_angle(i) for i in range(360)]
        samples.append({'state': box['state'], 'splinter': box['splinter'],
                        'rect': [box['width'], box['height']], 'radial': radial, 'angles': angles,
                        'arcSamples': [sample_angle(angle) for angle in arc_angles],
                        'gapSamples': [sample_angle(angle) for angle in gap_angles],
                        'innerArcs': [sample_angle(angle, (.59375, .625)) for angle in arc_angles],
                        'innerGaps': [sample_angle(angle, (.59375, .625)) for angle in gap_angles]})
    return samples


def _assert_nonempty(samples, label, root=16):
    assert samples and all(s["rect"] == pytest.approx([1.5 * root, 1.5 * root], abs=.5) for s in samples), label
    assert all(any(_luma(px) > .10 for px in s["radial"] + s["angles"]) for s in samples), f"{label}: empty ring paint sample"


def _luma(pixel):
    values = [value / 255 for value in pixel[:3]]
    values = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in values]
    return sum(value * weight for value, weight in zip(values, (.2126, .7152, .0722)))


def _common_monochrome(page):
    page.add_style_tag(content="""
      #r117-paint-probe .terrain-affinity-badge {
        color: #f4f4f4 !important; background-color: #202020 !important;
      }
    """)


def _boundary_signature(samples):
    return {
        s["state"]: tuple(_luma(px) > .10 for px in s["innerArcs"] + s["innerGaps"] + s["angles"])
        for s in samples
    }


def _assert_four_topologies(page):
    assert page.locator('#r117-paint-probe [data-affinity-state="unknown"]').evaluate_all(
        "els => els.length === 1 && els.every(el => getComputedStyle(el).opacity === '1')"
    ), "G11 unavailable remains opaque"
    samples = _sample_bubbles(page)
    root = float(page.evaluate("() => parseFloat(getComputedStyle(document.documentElement).fontSize)"))
    _assert_nonempty(samples, "G11 four-state paint", root)
    states = {sample["state"] for sample in samples}
    assert len(samples) == 4 and states == set(STATES) and all(sample['splinter'] == 'Fire' for sample in samples), "G11 all four states paint with one identity"
    signatures = _boundary_signature(samples)
    by_state = {sample['state']: sample for sample in samples}
    assert all(signatures['favoured'][:12]), "G11 bonus has one heavy annulus"
    assert all(signatures['penalised'][:6]) and not any(signatures['penalised'][6:12]), "G11 penalty has six heavy arcs separated by broad gaps"
    assert not any(signatures['unchanged'][:12]), "G11 zero has one fine ring"
    assert all(_luma(pixel) > .10 for state in ('favoured', 'unchanged') for pixel in by_state[state]['angles']), "G11 known solid outer rings remain continuous"
    unknown = next(sample for sample in samples if sample["state"] == "unknown")
    assert not any(signatures['unknown'][:12]), "G11 unavailable has a fine boundary"
    dot_coverage = sum(_luma(pixel) > .10 for pixel in unknown['angles']) / len(unknown['angles'])
    assert .15 < dot_coverage < .85, "G11 unavailable dotted boundary paints separated dots"
    for left, right in combinations(STATES, 2):
        assert signatures[left] != signatures[right], f"G11 pair {left}/{right} survives hue removal"


@pytest.mark.parametrize(("root", "device_scale_factor"), [(16, 1), (16, 2), (32, 1), (32, 2)])
@pytest.mark.parametrize("palette", ["original_grayscale", "common_monochrome"])
@pytest.mark.parametrize("forced_colors", ["none", "active"])
def test_four_state_paint_survives_monochrome_and_grayscale(paint_browser, tmp_path, root, device_scale_factor, palette, forced_colors):
    with closing(paint_browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=device_scale_factor)) as page:
        page.emulate_media(forced_colors=forced_colors)
        _paint_fixture(page)
        page.evaluate("root => document.documentElement.style.fontSize = `${root}px`", root)
        _fire_states(page)
        stem = f"root{root}-dpr{device_scale_factor}-forced-{forced_colors}"
        page.screenshot(path=str(tmp_path / f"{stem}-original-sheet.png"))
        page.locator('#r117-paint-probe').screenshot(path=str(tmp_path / f"{stem}-original-closeup.png"))
        if palette == "common_monochrome":
            _common_monochrome(page)
        else:
            page.add_style_tag(content="#r117-paint-probe { filter: grayscale(1) !important; }")
        page.screenshot(path=str(tmp_path / f"{stem}-{palette}-sheet.png"))
        page.locator('#r117-paint-probe').screenshot(path=str(tmp_path / f"{stem}-{palette}-closeup.png"))
        _assert_four_topologies(page)


def test_bonus_vs_penalty_DPR1_hue_removed(paint_browser, tmp_path):
    with closing(paint_browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)) as page:
        _paint_fixture(page)
        _fire_states(page)
        _common_monochrome(page)
        page.locator('#r117-paint-probe').screenshot(path=str(tmp_path / "bonus_vs_penalty_DPR1_hue_removed.png"))
        samples = _sample_bubbles(page)
        _assert_nonempty(samples, "G11 named bonus/penalty check")
        signatures = _boundary_signature(samples)
        assert signatures["favoured"] != signatures["penalised"], "G11 DPR1 hue-removed bonus/penalty separator"
        assert all(signatures['penalised'][:6]) and not any(signatures['penalised'][6:12]), "G11 DPR1 penalty has broad six-sector gaps"


def _assert_original_assets(page):
    records = page.locator(f"{PROFILE} {BADGE}").evaluate_all("""async els => Promise.all(els.map(async el => {
      const img = el.querySelector('img.splinter-icon');
      if (!img || !img.complete || !img.naturalWidth) return {name: el.dataset.splinter, complete: false, width: 0};
      await img.decode();
      const s = getComputedStyle(img), box = img.getBoundingClientRect(), badge = el.getBoundingClientRect();
      const reference = new Image(); reference.src = `/static/assets/splinter/${el.dataset.splinter.toLowerCase()}.svg`;
      await reference.decode();
      const refCanvas = document.createElement('canvas'); refCanvas.width = refCanvas.height = 16;
      const refCtx = refCanvas.getContext('2d', {willReadFrequently:true}); refCtx.drawImage(reference, 0, 0, 16, 16);
      const expected = [...refCtx.getImageData(0, 0, 16, 16).data];
      return {name: el.dataset.splinter, src: img.currentSrc, width: img.naturalWidth,
        box: [box.width, box.height], x:box.x, y:box.y,
        badge: {x:badge.x, y:badge.y, width:badge.width, height:badge.height},
        complete: img.complete, filter: s.filter, mask: s.maskImage,
        blend: s.mixBlendMode, opacity: s.opacity, objectFit: s.objectFit,
        expected};
    }))""")
    assert len(records) == 12 and all(record["complete"] and record["width"] > 0 for record in records), "G12 loaded original rasters"
    assert {record["name"] for record in records} == set(NAMES), "G12 all six identities present"
    assert all(record["src"].endswith(ASSET_SUFFIX[record["name"]]) for record in records), "G12 exact original asset URLs"
    assert all(record["box"] == pytest.approx([16, 16], abs=.5) for record in records), "G12 16px original canvas"
    assert all(record["filter"] == "none" for record in records), "G12 no icon filter"
    assert all(record["mask"] in ("none", "") and record["blend"] == "normal" and record["opacity"] == "1" for record in records), "G12 no raster effects"
    assert all(record["objectFit"] == "contain" for record in records), "G12 complete contained artwork"
    screenshot = Image.open(BytesIO(page.screenshot())).convert("RGB")
    assert screenshot.size == (1920, 1080), "G12 reference comparison uses DPR1"
    for record in records:
        assert 0 <= record['x'] <= 1904 and 0 <= record['y'] <= 1064, "G12 original canvas within screenshot"
        badge = record['badge']
        assert [badge['width'], badge['height']] == pytest.approx([24, 24], abs=.1), "G12 24px bubble"
        assert [record['x'] + 8, record['y'] + 8] == pytest.approx(
            [badge['x'] + 12, badge['y'] + 12], abs=.1
        ), "G12 original canvas centred"
        original = tuple(int(COLOURS[record['name']][offset:offset + 2], 16) for offset in (0, 2, 4))
        reference = record['expected']
        main_pixels = [
            (index // 4 % 16, index // 64, tuple(reference[index:index + 3]))
            for index in range(0, len(reference), 4)
            if reference[index + 3] >= 250
            and max(abs(reference[index + channel] - original[channel]) for channel in range(3)) <= 4
        ]
        assert len(main_pixels) >= 3, "G12 nonempty original-colour main-path reference"
        transparent = [
            (index // 4 % 16, index // 64)
            for index in range(0, len(reference), 4)
            if reference[index + 3] == 0
            and (index // 4 % 16 + .5 - 8) ** 2 + (index // 64 + .5 - 8) ** 2 < 7 ** 2
        ]
        assert transparent, "G12 original canvas exposes the inner backing disc"
        fill = screenshot.getpixel((math.floor(record['x'] + transparent[0][0] + .5),
                                    math.floor(record['y'] + transparent[0][1] + .5)))
        for x, y, expected in main_pixels:
            painted = screenshot.getpixel((math.floor(record['x'] + x + .5), math.floor(record['y'] + y + .5)))
            assert max(abs(painted[channel] - expected[channel]) for channel in range(3)) <= 10, "G12 composed main-path pixels match original reference"
            assert (_luma(painted) + .05) / (_luma(fill) + .05) >= 4.5, "G12 painted original main-path contrast at least 4.5"


@pytest.mark.parametrize("theme", ["alchemical", "dark", "light", "wood"])
@pytest.mark.parametrize("forced_colors", ["none", "active"])
def test_original_icon_raster_and_identity_contract(paint_browser, tmp_path, theme, forced_colors):
    with closing(paint_browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)) as page:
        page.emulate_media(forced_colors=forced_colors)
        _paint_fixture(page)
        page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
        page.screenshot(path=str(tmp_path / f"original-assets-{theme}-forced-{forced_colors}.png"))
        _assert_original_assets(page)


ASSET_MUTATIONS = [
    ("recolour", "el.style.filter='hue-rotate(90deg)';return getComputedStyle(el).filter!=='none';", "G12 no icon filter"),
    ("ancestor_recolour", "el.parentElement.parentElement.style.filter='grayscale(1)';return getComputedStyle(el.parentElement.parentElement).filter==='grayscale(1)';", "G12 composed main-path pixels match original reference"),
    ("wrong_url", "el.src='/static/assets/splinter/water.svg';await el.decode();return el.currentSrc.includes('/water.svg');", "G12 exact original asset URLs"),
    ("broken_image", "el.src='/static/assets/splinter/r117-missing.svg';await el.decode().catch(()=>{});return el.complete && el.naturalWidth===0;", "G12 loaded original rasters"),
    ("undersize", "el.style.width='8px';el.style.height='8px';return el.getBoundingClientRect().width===8;", "G12 16px original canvas"),
    ("crop", "el.style.clipPath='inset(0 50% 0 0)';return getComputedStyle(el).clipPath!=='none';", "G12 composed main-path pixels match original reference"),
    ("lightened_backing", "const sheet=document.createElement('style');sheet.textContent='[data-terrain-affinity=picker] [data-splinter=Fire][data-affinity-state=favoured]::after {background:#444!important}';document.head.append(sheet);return getComputedStyle(el.parentElement,'::after').backgroundColor==='rgb(68, 68, 68)';", "G12 painted original main-path contrast at least 4.5"),
]


@pytest.mark.parametrize(("mutation", "script", "message"), ASSET_MUTATIONS, ids=[item[0] for item in ASSET_MUTATIONS])
def test_original_asset_guard_rejects_red_mutations(paint_browser, tmp_path, mutation, script, message):
    with closing(paint_browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)) as page:
        _paint_fixture(page)
        _assert_original_assets(page)
        # the desktop plot editor no longer renders the vertical
        # `[data-terrain-affinity="editor"]` column (retired by ) -- the live desktop
        # affinity surface is `terrainAffinityHtml(row, 'picker')`'s horizontal row, which
        # this fixture's second host already renders as `[data-terrain-affinity="picker"]`.
        # Same identity/state under mutation, just the surface the real page now uses.
        assert page.locator('[data-terrain-affinity="picker"] [data-splinter="Fire"][data-affinity-state="favoured"] .splinter-icon').evaluate(f"async el => {{{script}}}") is True, f"{mutation}: mutation landed"
        page.screenshot(path=str(tmp_path / f"asset-red-{mutation}.png"))
        with pytest.raises(AssertionError, match=message):
            _assert_original_assets(page)


RING_MUTATIONS = [
    (
        "all_fine",
        "favoured",
        "#r117-paint-probe .terrain-affinity-badge {border:0!important} "
        "#r117-paint-probe .terrain-affinity-badge::before {inset:0!important;padding:0!important;border:1px solid currentColor!important;background:none!important;mask:none!important;-webkit-mask:none!important} "
        "#r117-paint-probe .terrain-affinity-badge::after {display:none!important}",
        "el => getComputedStyle(el).borderTopWidth==='0px' && getComputedStyle(el,'::before').borderTopWidth==='1px' && getComputedStyle(el,'::before').maskImage==='none' && getComputedStyle(el,'::after').display==='none'",
        "G11 bonus has one heavy annulus",
    ),
    (
        "penalty_sector_gaps_close",
        "penalised",
        "#r117-paint-probe [data-affinity-state=penalised]::before {background-image:linear-gradient(currentColor,currentColor)!important}",
        "el => getComputedStyle(el,'::before').backgroundImage.startsWith('linear-gradient')",
        "G11 penalty has six heavy arcs separated by broad gaps",
    ),
    (
        "penalty_loses_heavy_stroke",
        "penalised",
        "#r117-paint-probe [data-affinity-state=penalised]::before {padding:1px!important}",
        "el => getComputedStyle(el,'::before').paddingTop==='1px'",
        "G11 penalty has six heavy arcs separated by broad gaps",
    ),
    (
        "bonus_fine",
        "favoured",
        "#r117-paint-probe [data-affinity-state=favoured]::before {border-width:1px!important}",
        "el => getComputedStyle(el,'::before').borderTopWidth==='1px'",
        "G11 bonus has one heavy annulus",
    ),
    (
        "zero_becomes_heavy",
        "unchanged",
        "#r117-paint-probe [data-affinity-state=unchanged]::before {border-width:3px!important}",
        "el => getComputedStyle(el,'::before').borderTopWidth==='3px'",
        "G11 zero has one fine ring",
    ),
    (
        "unknown_dots_become_solid",
        "unknown",
        "#r117-paint-probe [data-affinity-state=unknown]::before {border-style:solid!important}",
        "el => getComputedStyle(el,'::before').borderTopStyle==='solid'",
        "G11 unavailable dotted boundary paints separated dots",
    ),
    (
        "unknown_fades",
        "unknown",
        "#r117-paint-probe [data-affinity-state=unknown] {opacity:.4!important}",
        "el => getComputedStyle(el).opacity==='0.4'",
        "G11 unavailable remains opaque",
    ),
]


@pytest.mark.parametrize(("mutation", "state", "css", "landed", "message"), RING_MUTATIONS, ids=[item[0] for item in RING_MUTATIONS])
def test_ring_paint_guard_rejects_red_mutations(paint_browser, tmp_path, mutation, state, css, landed, message):
    with closing(paint_browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)) as page:
        _paint_fixture(page)
        _fire_states(page)
        _common_monochrome(page)
        _assert_four_topologies(page)
        page.add_style_tag(content=css)
        assert page.locator(f'#r117-paint-probe [data-affinity-state="{state}"]').evaluate(landed) is True, f"{mutation}: mutation landed"
        page.locator('#r117-paint-probe').screenshot(path=str(tmp_path / f"ring-red-{mutation}.png"))
        with pytest.raises(AssertionError, match=message):
            _assert_four_topologies(page)


def _assert_surface_contrast(page):
    records = page.locator('#r117-paint-probe [data-affinity-state]').evaluate_all("""els => {
      const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
      const ctx = canvas.getContext('2d', {willReadFrequently:true});
      const channels = colour => {
        ctx.clearRect(0,0,1,1); ctx.fillStyle=colour; ctx.fillRect(0,0,1,1);
        return [...ctx.getImageData(0,0,1,1).data];
      };
      const surfaces = {};
      for (const name of ['bg','bg3','top']) {
        const swatch = document.createElement('div');
        if (name === 'top') swatch.className='candidate-row is-top';
        else swatch.style.backgroundColor=`var(--${name})`;
        document.body.append(swatch);
        surfaces[name]=channels(getComputedStyle(swatch).backgroundColor);
        swatch.remove();
      }
      return els.map(el => {
        const style=getComputedStyle(el), before=getComputedStyle(el,'::before');
        let ink=before.borderTopColor;
        if (el.dataset.affinityState==='penalised') {
          const colours=before.backgroundImage.match(/(?:rgba?|color)\\([^)]*\\)/g) || [];
          ink=colours.find(colour => channels(colour)[3] > 0) || 'transparent';
        }
        return {state:el.dataset.affinityState, ink:channels(ink), surfaces,
          opacity:style.opacity, beforeOpacity:before.opacity};
      });
    }""")
    assert len(records) == 4 and {record['state'] for record in records} == set(STATES), "G13 contrast covers all four semantic states"
    ratios = {}
    for record in records:
        state = record['state']
        assert record['ink'][3] == 255 and record['opacity'] == record['beforeOpacity'] == '1', f"G13 {state} ink is opaque"
        ratios[state] = {}
        for surface, background in record['surfaces'].items():
            assert background[3] == 255, f"G13 {surface} has an opaque surface"
            luminances = sorted((_luma(record['ink']), _luma(background)))
            ratio = (luminances[1] + .05) / (luminances[0] + .05)
            ratios[state][surface] = ratio
            assert ratio >= 4.5, f"G13 {state} against {surface}: {ratio:.3f}:1 is below 4.5:1"
    return ratios


@pytest.mark.parametrize('theme', ['alchemical', 'dark', 'light', 'wood'])
def test_each_state_contrast_against_all_three_theme_surfaces(paint_browser, theme, record_property):
    with closing(paint_browser.new_page(viewport={'width':1920, 'height':1080}, device_scale_factor=1)) as page:
        _paint_fixture(page)
        page.evaluate('theme => document.documentElement.dataset.theme=theme', theme)
        _fire_states(page)
        record_property(f'contrast-{theme}', _assert_surface_contrast(page))


@pytest.mark.parametrize('theme', ['alchemical', 'dark', 'light', 'wood'])
@pytest.mark.parametrize('state', STATES)
def test_contrast_guard_rejects_state_ink_equal_to_bg3(paint_browser, theme, state):
    with closing(paint_browser.new_page(viewport={'width':1920, 'height':1080}, device_scale_factor=1)) as page:
        _paint_fixture(page)
        page.evaluate('theme => document.documentElement.dataset.theme=theme', theme)
        _fire_states(page)
        _assert_surface_contrast(page)
        badge = page.locator(f'#r117-paint-probe [data-affinity-state="{state}"]')
        assert badge.evaluate("""el => {
          el.style.color='var(--bg3)';
          const swatch=document.createElement('div');swatch.style.color='var(--bg3)';
          document.body.append(swatch);
          const landed=getComputedStyle(el).color===getComputedStyle(swatch).color;
          swatch.remove();return landed;
        }""") is True, f'{state}_ink_equals_bg3: mutation landed'
        with pytest.raises(AssertionError, match=f'G13 {state} against'):
            _assert_surface_contrast(page)
