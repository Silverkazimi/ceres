"""Unexecuted real-board computed-style guards for R-117 affinity colour."""

from __future__ import annotations

import copy
import re
from contextlib import closing

import pytest

from .conftest import spawn_live_ceres
from .test_candidate_redesign_frontend import _real_detail_row
from .test_live_r117_terrain_display import _mount, _open

THEMES = ("alchemical", "dark", "light", "wood")
PALETTE = {
    "alchemical": ("#74c98b", "#cf5555", 2.0),
    "dark": ("#7bd59a", "#d45b62", 2.0),
    "light": ("#2a804a", "#b52a38", 1.30),
    "wood": ("#227a47", "#b32633", 1.25),
}
STATES = {"favoured", "penalised", "unchanged", "unknown"}
MOUNTAIN = {"red": 0.1, "blue": -0.5, "green": -0.5, "white": None, "black": 0.1, "gold": 0}


@pytest.fixture(scope="module")
def live_browser():
    with spawn_live_ceres() as live:
        yield live


@pytest.fixture(scope="module")
def real_board_row(live_browser):
    _, base_url = live_browser
    row = copy.deepcopy(_real_detail_row(base_url))
    row.update(terrain="Mountain", biome=MOUNTAIN.copy())
    workers = [copy.deepcopy(worker) for worker in row["workers"] if not worker.get("is_runi")][:2]
    assert workers, " colour guard requires a real worker row"
    for slot, worker in enumerate(workers, 2):
        worker["slot"] = slot
    row["workers"] = workers
    row["capacity"].update(worker_count=len(workers), max_workers_allowed=5, card_capacity=5, open_slots=3)
    if not row.get("swap_targets") and not row.get("worksite_completion"):
        row["swap_targets"] = [{"target": "grain_farm"}]
    return row


def _rgb(value):
    if value.startswith("#"):
        value = value[1:]
        if len(value) == 3:
            value = "".join(channel * 2 for channel in value)
        return tuple(float(int(value[index:index + 2], 16)) for index in (0, 2, 4))
    values = re.findall(r"[\d.]+", value)
    return tuple(float(channel) for channel in values[:3])


def _luminance(value):
    channels = [channel / 255 for channel in _rgb(value)]
    channels = [channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4 for channel in channels]
    return sum(channel * weight for channel, weight in zip(channels, (0.2126, 0.7152, 0.0722)))


def _contrast(first, second):
    high, low = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _collect(page):
    return page.evaluate("""() => {
      const css = (element, pseudo) => getComputedStyle(element, pseudo);
      const rect = element => { const box = element.getBoundingClientRect(); return [box.x, box.y, box.width, box.height]; };
      const root = css(document.documentElement);
      const picker = document.querySelector('.inline-picker');
      return {
        rootSize: parseFloat(root.fontSize),
        bg: root.getPropertyValue('--bg').trim(), bg2: root.getPropertyValue('--bg2').trim(),
        heading: root.getPropertyValue('--heading').trim(), pickerBg: css(picker).backgroundColor,
        editorZ: css(document.querySelector('.editor-main')).zIndex,
        veilZ: css(document.querySelector('.eos-scenic-veil')).zIndex,
        scrollWidth: document.documentElement.scrollWidth,
        hosts: [...document.querySelectorAll('.terrain-affinity,.terrain-affinity-scroll')].map(element => ({
          bonus: css(element).getPropertyValue('--affinity-bonus').trim(),
          penalty: css(element).getPropertyValue('--affinity-penalty').trim(),
        })),
        rectangles: [...document.querySelectorAll('.slot-editor,.editor-band,.slot-editor-footer,#plot-table,.candidate-columns,.inline-picker,.terrain-affinity-scroll,.terrain-affinity,.terrain-affinity-badge')].map(rect),
        badges: [...document.querySelectorAll('.terrain-affinity-badge')].map(element => {
          const style = css(element), before = css(element, '::before'), after = css(element, '::after');
          const image = element.querySelector('.splinter-icon'), imageStyle = css(image);
          const ancestors = [];
          for (let parent = element.parentElement; parent; parent = parent.parentElement) {
            const parentStyle = css(parent);
            ancestors.push({filter: parentStyle.filter, opacity: parentStyle.opacity, blend: parentStyle.mixBlendMode});
          }
          return {
            state: element.dataset.affinityState, surface: element.closest('[data-terrain-affinity]').dataset.terrainAffinity,
            color: style.color, background: style.backgroundColor, size: [rect(element)[2], rect(element)[3]], opacity: style.opacity,
            ringWidth: before.borderTopWidth, ringStyle: before.borderTopStyle, ringColor: before.borderTopColor,
            gradient: before.backgroundImage, padding: before.paddingTop, mask: before.maskImage,
            webkitMask: before.webkitMaskImage, composite: before.maskComposite, webkitComposite: before.webkitMaskComposite,
            disc: after.backgroundColor, discInset: after.top,
            image: {filter: imageStyle.filter, mask: imageStyle.maskImage, webkitMask: imageStyle.webkitMaskImage,
              blend: imageStyle.mixBlendMode, opacity: imageStyle.opacity, width: imageStyle.width, height: imageStyle.height},
            ancestors,
          };
        }),
      };
    }""")


def _set_view(page, theme, width=1920, root=16):
    page.set_viewport_size({"width": width, "height": 1080})
    page.evaluate("([theme, root]) => { document.documentElement.dataset.theme = theme; document.documentElement.style.fontSize = `${root}px`; }", [theme, root])


def _assert_contract(page, theme, baseline):
    observed = _collect(page)
    bonus, penalty, pair_floor = PALETTE[theme]
    assert observed["rectangles"] == baseline["rectangles"], "NO_GROWTH: rectangles differ"
    assert observed["scrollWidth"] == baseline["scrollWidth"], "NO_GROWTH: document width differs"
    assert _rgb(observed["pickerBg"]) == _rgb(observed["bg2"]), "PICKER_SURFACE"
    assert all(_rgb(host["bonus"]) == _rgb(bonus) and _rgb(host["penalty"]) == _rgb(penalty) for host in observed["hosts"]), "TOKEN_SCOPE_OR_THEME"
    assert int(observed["editorZ"]) < int(observed["veilZ"]), "LIFT_EDITOR: editor escaped reveal veil"
    assert len(observed["badges"]) == 12 and {badge["state"] for badge in observed["badges"]} == STATES, "REAL_BOARD_STATES"
    signs = {badge["state"]: next(item for item in observed["badges"] if item["state"] == badge["state"]) for badge in observed["badges"]}
    assert _contrast(signs["favoured"]["color"], signs["penalised"]["color"]) >= pair_floor, "PAIR_SEPARATION"
    for badge in observed["badges"]:
        expected = bonus if badge["state"] == "favoured" else penalty if badge["state"] == "penalised" else observed["heading"]
        assert _rgb(badge["color"]) == _rgb(expected), f"COLOR:{theme}:{badge['state']}"
        assert _rgb(badge["disc"]) == _rgb(observed["bg"]), "THEMED_INNER_DISC"
        assert _rgb(badge["background"]) == _rgb(observed["bg"]), "BADGE_BACKGROUND"
        assert badge["opacity"] == "1" and badge["size"] == [1.5 * observed["rootSize"]] * 2, "BADGE_FOOTPRINT"
        assert badge["image"]["filter"] == "none" and badge["image"]["mask"] in ("none", ""), "ICON_FILTER_OR_MASK"
        assert badge["image"]["webkitMask"] in ("none", "") and badge["image"]["blend"] == "normal", "ICON_PRESENTATION"
        assert all(ancestor == {"filter": "none", "opacity": "1", "blend": "normal"} for ancestor in badge["ancestors"]), "ANCESTOR_EFFECT"
        if badge["state"] in {"favoured", "penalised"}:
            for surface, background in (("disc", badge["disc"]), ("badge", badge["background"]), ("picker", observed["pickerBg"])):
                assert _contrast(badge["color"], background) >= 3, f"CONTRAST:{theme}:{badge['state']}:{surface}"
        if badge["state"] == "penalised":
            assert "repeating-conic-gradient" in badge["gradient"] and "40deg" in badge["gradient"] and "60deg" in badge["gradient"], "PENALTY_SIX_ARCS"
            assert badge["color"] in badge["gradient"], "PENALTY_GRADIENT_COLOR"
            assert float(badge["padding"].removesuffix("px")) == pytest.approx(0.1875 * observed["rootSize"]), "PENALTY_RING_WIDTH"
            assert badge["mask"].count("linear-gradient") == 2 and badge["composite"] == "exclude", "PENALTY_MASK"
            assert badge["webkitMask"].count("linear-gradient") == 2 and badge["webkitComposite"] == "xor", "PENALTY_WEBKIT_MASK"
        else:
            expected_width = f"{int(observed['rootSize'] * (0.1875 if badge['state'] == 'favoured' else 0.0625))}px"
            assert badge["ringWidth"] == expected_width, f"RING_WIDTH:{badge['state']}"
            assert badge["ringStyle"] == ("dotted" if badge["state"] == "unknown" else "solid"), f"RING_STYLE:{badge['state']}"
            assert _rgb(badge["ringColor"]) == _rgb(expected), f"RING_COLOR:{badge['state']}"


def _page_for_guard(live_browser, row):
    browser, base_url = live_browser
    page = browser.new_page(viewport={"width": 1920, "height": 1080})
    _mount(page, base_url, row)
    _open(page)
    return page


def _neutral_baseline(page):
    style = page.add_style_tag(content=".terrain-affinity,.terrain-affinity-scroll{--affinity-bonus:var(--heading)!important;--affinity-penalty:var(--heading)!important}")
    baseline = _collect(page)
    style.evaluate("element => element.remove()")
    return baseline


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("width,root", [(1920, 16), (1280, 20), (375, 16)])
def test_real_board_affinity_colour_contract(live_browser, real_board_row, theme, width, root):
    with closing(_page_for_guard(live_browser, real_board_row)) as page:
        _set_view(page, theme, width, root)
        baseline = _neutral_baseline(page)
        _assert_contract(page, theme, baseline)


# SEMANTIC_COLOURS: replacing dedicated tokens with --ok/--danger must fail exact sign paint and contrast.
# REMOVE_SIGN_COLOURS: collapsing both signs to --heading must fail exact state-to-colour mapping.
# SWAP_SIGNS: exchanging bonus and penalty tokens must fail the state mapping.
# PENALTY_SOLID: replacing the six arcs with a solid ring must fail gradient and mask assertions.
# UNKNOWN_SOLID: replacing the unknown dotted ring with a solid ring must fail ring-style assertions.
# TINT_ICON: adding a grayscale filter must fail the original icon presentation assertion.
# ENLARGE_BADGE: changing a badge to 2rem must fail rectangle and footprint assertions.
# GROW_BAND: changing the fixed reveal band to 20rem must fail rectangle-identical no-growth.
# LIFT_EDITOR: raising the editor above the scenic veil must fail stacking assertions.
# NEUTRAL_GREEN: colouring zero and unknown green must fail the neutral heading assertion.
# ANCESTOR_TINT: filtering the affinity host must fail ancestor presentation assertions.
# ANCESTOR_FADE: fading the affinity host must fail ancestor opacity assertions.
# RESTORE_HARDCODED_DISC: restoring #101010 must fail the themed one-object disc assertion.
MUTATIONS = {
    "SEMANTIC_COLOURS": '[data-affinity-state="favoured"]{color:var(--ok)!important}[data-affinity-state="penalised"]{color:var(--danger)!important}',
    "REMOVE_SIGN_COLOURS": ".terrain-affinity-badge{color:var(--heading)!important}",
    "SWAP_SIGNS": '[data-affinity-state="favoured"]{color:var(--affinity-penalty)!important}[data-affinity-state="penalised"]{color:var(--affinity-bonus)!important}',
    "PENALTY_SOLID": '[data-affinity-state="penalised"]::before{background:none!important;border:3px solid currentColor!important}',
    "UNKNOWN_SOLID": '[data-affinity-state="unknown"]::before{border-style:solid!important}',
    "TINT_ICON": ".terrain-affinity-badge .splinter-icon{filter:grayscale(1)!important}",
    "ENLARGE_BADGE": ".terrain-affinity-badge{inline-size:2rem!important;block-size:2rem!important;flex-basis:2rem!important}",
    "GROW_BAND": ".editor-band{--eos-band-height:20rem!important}",
    "LIFT_EDITOR": ".editor-band>.editor-main{z-index:5!important}",
    "NEUTRAL_GREEN": '[data-affinity-state="unchanged"],[data-affinity-state="unknown"]{color:var(--affinity-bonus)!important}',
    "ANCESTOR_TINT": ".terrain-affinity{filter:grayscale(1)!important}",
    "ANCESTOR_FADE": ".terrain-affinity{opacity:.5!important}",
    "RESTORE_HARDCODED_DISC": ".terrain-affinity-badge::after{background:#101010!important}",
}


@pytest.mark.parametrize("theme", THEMES)
@pytest.mark.parametrize("mutation", MUTATIONS)
def test_real_board_affinity_colour_mutations_are_rejected(live_browser, real_board_row, theme, mutation):
    with closing(_page_for_guard(live_browser, real_board_row)) as page:
        _set_view(page, theme)
        baseline = _neutral_baseline(page)
        _assert_contract(page, theme, baseline)
        page.add_style_tag(content=MUTATIONS[mutation])
        with pytest.raises(AssertionError):
            _assert_contract(page, theme, baseline)


def test_guard_fixture_is_not_the_plan_synthetic_shell(live_browser, real_board_row):
    with closing(_page_for_guard(live_browser, real_board_row)) as page:
        assert page.locator("tr.plot-row").count() > 0
        # the desktop plot editor renders the horizontal splinter row
        # (`.editor-splinter-affinity [data-terrain-affinity='picker']`), not the vertical
        # `.slot-editor-footer` column retired.
        assert page.locator(".editor-band .editor-splinter-affinity [data-terrain-affinity='editor-row']").count() == 1
        assert page.locator(".candidate-worker-affinity [data-terrain-affinity='picker']").count() == 1
