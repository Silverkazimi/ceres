"""DEDICATED TEST queue — GOLDEN RULE 6 EXEMPT.

 static tile guards; this synchronous queue does not run a browser.
"""

from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path

import pytest

from .vm_context import read_entry_source, run_in_module_context


ROOT = Path(__file__).parents[2]
CSS = ROOT / "ceres" / "static" / "ceres.css"
EDITOR = ROOT / "ceres" / "static" / "editor.js"
EOS = ROOT / "ceres" / "static" / "eos.js"
BADGES = ROOT / "ceres" / "static" / "badges.js"
ICONS = ROOT / "ceres" / "static" / "icons.js"
ABILITY_DOC = ROOT / "library" / "land-worker-abilities.md"
DOCUMENTED_ABILITY_CODES = frozenset({
    "ALTERATION", "AURA", "BLOODLINE", "DD", "ENERGIZED", "FORTUNE_SEEKER", "GLINT_RECOVERY", "GRAIN",
    "IRON", "LL", "RATIONING", "RATIONING_LITE", "RESEARCH", "SPS", "STONE", "WOOD",
})
BARE_BROWSER_GLOBALS_JS = """
window.getComputedStyle = () => ({
  fontSize: '16px',
  borderLeftWidth: '0px',
  borderRightWidth: '0px',
  borderTopLeftRadius: '11px',
  borderTopWidth: '0px',
  getPropertyValue() { return ''; },
});
context.getComputedStyle = window.getComputedStyle;
context.requestAnimationFrame = window.requestAnimationFrame;
context.cancelAnimationFrame = window.cancelAnimationFrame;
context.matchMedia = window.matchMedia;
context.performance = { now: () => Date.now() };
""".strip()


def _documented_ability_codes() -> set[str]:
    if not ABILITY_DOC.is_file():
        return set(DOCUMENTED_ABILITY_CODES)
    source = ABILITY_DOC.read_text(encoding="utf-8")
    table = source[source.index("## 2. Full ability code table"):source.index("## 2a.")]
    codes = set()
    for first_column in re.findall(r"^\| ([^|]+) \|", table, re.MULTILINE):
        codes.update(re.findall(r"`([A-Z][A-Z0-9_]*)`", first_column))
    codes.update(re.findall(r"\[\[\s*[\"']([A-Z][A-Z0-9_]*)[\"']", source))
    return codes


def _object_body(source: str, name: str) -> str:
    match = re.search(rf"(?:const|export const) {name} = (?:Object\.freeze\()?\{{(.*?)\n\}}\)?;", source, re.DOTALL)
    assert match, f"missing {name} object"
    return match.group(1)


def _object_keys(source: str, name: str) -> set[str]:
    return set(re.findall(r"\b([A-Z][A-Z0-9_]*)\s*:", _object_body(source, name)))


def test_documented_abilities_have_tiers_slugs_and_existing_assets() -> None:
    source = ICONS.read_text(encoding="utf-8")
    documented = _documented_ability_codes()
    tiers = _object_keys(source, "LAND_ABILITY_TIERS")
    slugs = _object_keys(source, "ABILITY_ICON_SLUG")
    assert documented <= tiers
    assert documented <= slugs
    fallback = re.search(r"ABILITY_ICON_FALLBACK_SLUG = '([^']+)'", source).group(1)
    slug_body = _object_body(source, "ABILITY_ICON_SLUG")
    slug_values = {
        code: (value[1:-1] if value.startswith("'") else fallback)
        for code, value in re.findall(r"\b([A-Z][A-Z0-9_]*)\s*:\s*('[^']+'|ABILITY_ICON_FALLBACK_SLUG)", slug_body)
    }
    assets = ROOT / "ceres" / "static" / "assets"
    source_manifest = assets / "sources.json"
    downloadable = {row["path"] for row in (json.loads(source_manifest.read_text(encoding="utf-8")) if source_manifest.is_file() else [])}
    asset_index = {
        row["path"]: row for row in json.loads(
            (ROOT / "tests/ceres/fixtures/export_boundary/asset-index.json").read_text(encoding="utf-8")
        )
    }
    unavailable = sorted(
        code for code in documented
        if not (assets / "abilities" / f"ability_{slug_values[code]}.png").is_file()
        and f"abilities/ability_{slug_values[code]}.png" not in downloadable
        and f"abilities/ability_{slug_values[code]}.png" not in asset_index
    )
    assert not unavailable, f"ability icons have no vetted asset record: {unavailable}"
    for code in documented:
        relative = f"abilities/ability_{slug_values[code]}.png"
        file = assets / relative
        if file.is_file() and relative in asset_index:
            assert hashlib.sha256(file.read_bytes()).hexdigest() == asset_index[relative]["sha256"]

    # (790c4e52, 2026-09-18 docs reconciliation) rewrote
    # library/land-worker-abilities.md's ElectroFox example prose that used to give
    # `_documented_ability_codes()` its only mention of STATIC_CHARGE (it has never
    # had a row in the "Full ability code table"); STATIC_CHARGE is therefore no
    # longer "documented" by this helper's own definition, so removing it from
    # LAND_ABILITY_TIERS no longer changes the diff. BLOODLINE has a real table row
    # and is unique to LAND_ABILITY_TIERS's own line (the shared string also
    # appears in ABILITY_ICON_SLUG above it, so the object's own opening brace is
    # included to target only this object), so it is the mutation canary now.
    mutated = source.replace(
        "export const LAND_ABILITY_TIERS = Object.freeze({\n  BLOODLINE: 'toil-and-kin',\n",
        "export const LAND_ABILITY_TIERS = Object.freeze({\n",
        1,
    )
    assert _documented_ability_codes() - _object_keys(mutated, "LAND_ABILITY_TIERS") == {"BLOODLINE"}


def _render(abilities: list[list[object]], *, bubble: bool = False, foil: str = "Regular", cooling: bool = False) -> str:
    body = f"""
const row = {{ deed_uid: 'static-tile', workers: [] }};
const group = {{ id: 'worker', label: 'Workers', actionable: true, capacity: 5, slots: [null], slotNumbers: [1] }};
const occupant = {{ asset_ref: 'worker-1', name: 'Static Worker', image: null, image_candidates: [], base_pp: 1000,
  base_pp_after_cap: null, current_harvest_pp: 1000, badges: ['server badge'], rarity: 'common',
  splinter: 'Water', card_set: 'land', edition: 19, editions: '19', foil: {json.dumps(foil)},
  gold: false, land_abilities: {json.dumps(abilities)}, sole_holder_for: ['Bountiful Grain'],
  cooldown: {json.dumps({"ready": False}) if cooling else 'null'}, sub_type: 'Human' }};
const html = renderEditorTile(row, group, 0, occupant, {{ cell: true, hasEnergizedBubble: {str(bubble).lower()} }}) + ({str(bubble).lower()} ? renderEnergizedBadge(occupant) : '');
console.log(JSON.stringify(html));
"""
    result = run_in_module_context(body, extra_context_js=BARE_BROWSER_GLOBALS_JS)
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_ability_order_and_overflow_render_from_real_tile_renderer() -> None:
    """MUTATION: changing `orderLandAbilities()` to preserve source order makes the
    first three URLs fail this assertion; the +N body also proves hidden source order."""
    # v0.9.192 (/) raised ABILITY_MAX from 3 to 5 (up to
    # 4 visible + overflow) and gave exactly 6 abilities their own dedicated arc
    # layout (no overflow at all) -- 7 abilities are used here so the overflow path
    # is still exercised (4 visible, 3 hidden) without landing on that special case.
    abilities = [
        ["DD", -0.5],
        ["GRAIN", 0.6],
        ["BLOODLINE", 0.3, "Elf", {"active": True, "companions": ["Elf mate"]}],
        ["STONE", 0.85],
        ["RATIONING_LITE", -0.03],
        ["WOOD", 0.4],
        ["IRON", 0.2],
    ]
    html = _render(abilities)
    icon_slugs = re.findall(r'<img class="ability-icon[^>]+src="[^\"]*/ability_([^\.]+)\.png"', html)
    # Toil and Kin is the tile's labelled pill (`.slot-kin-badge`, Echo's "one badge")
    # so the flat icon row no longer repeats it: 6 icons remain, 4 visible + "+2".
    assert icon_slugs[:3] == ["bountiful-grain", "bountiful-stone", "bountiful-wood"]
    assert "toil-and-kin" not in icon_slugs
    assert re.search(r'<span class="slot-ability-count"[^>]*>\+2</span>', html)
    assert html.index("Dark Discount") < html.index("Light Rationing")
    tile = html[html.index('<div class="slot-tile'):]
    assert tile.index('<div class="slot-foot">') < tile.index('class="slot-caption')  # the foot sits inside the tile, above the name
    assert 'class="slot-kin-badge is-paired"' in tile and "Toil &amp; Kin +30%" in tile


def test_energized_is_bubble_art_not_an_ability_icon() -> None:
    """Explicit guard: ENERGIZED has a separate power-bubble role, so it is
    excluded from the ability-icon query while the local asset remains present."""
    html = _render([["ENERGIZED"], ["GRAIN", 0.6], ["BLOODLINE", 0.3, "Elf"]], bubble=True)
    ability_images = re.findall(r'<img class="ability-icon[^>]+src="([^\"]+)', html)
    assert all("ability_energized.png" not in src for src in ability_images)
    assert 'class="slot-energized-art" src="/static/assets/abilities/ability_energized.png"' in html
    # v0.9.192 (/, 6c872d33) replaced the old two-queue
    # `.slot-ability-row.is-split` layout for the Energized/bubble case with an arc
    # of icons around the power satellite (`.slot-ability-arc`); `.is-split` is
    # dead CSS left over from before that landing.
    assert 'class="slot-ability-arc"' in html


def test_tile_has_no_moved_badge_markup_and_keeps_names_and_corner_marks() -> None:
    """MUTATION: restoring any old caption badge interpolation makes the forbidden
    selector set appear in this rendered tile; the corner selectors are the retained
    phase-one marks, while phase two consumes `tileBadgeChips()` separately."""
    html = _render([["BLOODLINE", 0.3, "Elf"]], foil="1", cooling=True)
    tile = html[html.index('<div class="slot-tile'):]
    for forbidden in ("slot-foil-badge", "slot-bloodline-badge", "slot-cooldown-badge", "slot-in-flight", "slot-badges"):
        assert forbidden not in tile
    assert 'class="slot-caption"' in tile and 'class="slot-name"' in tile
    # v0.9.111 (, 8ca621e8) removed the sole-source tile
    # badge -- the only flag that ever used the bare `slot-flag` class with no
    # modifier. Only the modified variants (`slot-flag-cooldown`
    # `slot-flag-unpowered`) exist now.
    assert 'class="slot-flag slot-flag-cooldown"' in tile
    # button consolidation (v0.9.515, a38cb80d, maintainer-approved) gave every
    # button on Ceres a shared `ceres-btn`/`ceres-btn--secondary` contract class
    # including this one.
    assert 'class="slot-remove ceres-btn ceres-btn--secondary"' in tile


def test_tile_badge_chips_preserve_phase_two_data_and_tooltips() -> None:
    """/:  (v0.9.130, aa4a22ae, "delete the worker badge row
    completely") deleted `tileBadgeChips()` and the whole worker-badge-row feature
    this test's phase-two chips (foil/combine/cooldown/bloodline/staged) covered --
    that commit's own message flags this exact test as needing an update ("two tile
    tests that also assert on it still need updating"). Its replacement: foil and
    companion pairing now live ONLY in the tile's own tooltip (this file's other
    tests already cover the forbidden old badge-row selectors), matching the
    commit's stated behaviour -- "Foil and companion stay in each tile's tooltip"."""
    source = read_entry_source(entry=BADGES)
    assert "tileBadgeChips" not in source
    abilities = [["BLOODLINE", 0.3, "Elf", {"active": True, "companions": ["Elf mate"]}]]
    html = _render(abilities, foil="1")
    tile = html[html.index('<div class="slot-tile'):]
    for forbidden in ("slot-foil-badge", "slot-bloodline-badge", "slot-cooldown-badge", "slot-combine-badge", "slot-staged-badge", "slot-badges"):
        assert forbidden not in tile
    assert "Gold" in tile  # fmtFoil('1') -- foil now surfaces only via the tooltip
    assert "Toil and Kin" in tile and "Elf pair" in tile  # companion pairing, same tooltip


def test_tile_css_preserves_frozen_satellite_and_clamped_base_pp_radius() -> None:
    """MUTATION: changing either frozen satellite geometry or the radius clamp is
    intended to make this source guard fail; rendered live radius is covered separately."""
    source = CSS.read_text(encoding="utf-8")
    assert "both wrappers remain 36.8px and left 35.2px/top -18.4px" in source
    assert "border-radius: var(--radius);" in source
    assert ".slot-caption {" in source
    # v0.9.512 (b49abfac) removed `.slot-caption`'s own gradient
    # it doubled the tile's own art gradient underneath it -- keeping `background
    # none` and leaning on each name's own text-shadow for legibility instead.
    assert "background: none; backdrop-filter: none;" in source
    assert "width: 19px; height: 19px" in source
    assert ".slot-tile-footer" not in source
    assert ".slot-cooldown-badge" not in source
    assert ".slot-abilities .ability-icon" not in source


def test_pending_removal_tile_has_no_restore_caption() -> None:
    editor_source = EDITOR.read_text(encoding="utf-8")
    css_source = CSS.read_text(encoding="utf-8")
    assert 'data-slot-action="restore"' in editor_source
    assert "Click to restore" not in editor_source
    assert "slot-restore-label" not in editor_source
    assert ".slot-restore-label" not in css_source


# v0.9.166 (26604fed, FR1) changed the band constants from
# 23.8125/18.8125 to 22.5125/18.5125 (+20px, "revealed plot got narrower after the
# top status line was removed"). Separately, v0.9.512 (b49abfac) removed the
# `.slot-caption` gradient scrim entirely -- it doubled the tile's own art
# gradient -- and leans on each name's own text-shadow for legibility instead.
def _assert_band_and_hover_contract(eos_source: str, css_source: str) -> None:
    assert "const bandRem = narrow ? 22.5125 : 18.5125;" in eos_source
    assert "text-shadow: 0 1px 2px rgba(0,0,0,.9);" in css_source
    band_rules = re.finditer(r"(?ms)^\.editor-band \{(?P<body>[^{}]*)\}", css_source)
    expected_geometry = (
        "block-size: var(--eos-band-height, 18.5125rem); "
        "min-block-size: var(--eos-band-height, 18.5125rem);"
    )
    assert sum(expected_geometry in match.group("body") for match in band_rules) == 1
    icon_rule = re.search(r"(?ms)^\.slot-ability-item \.ability-icon \{(?P<body>[^{}]*)\}", css_source)
    assert icon_rule is not None
    assert all(token not in icon_rule.group("body") for token in ("transform:", "will-change:", "contain:"))
    assert not re.search(
        r"(?ms)[^{}]*\.ability-icon(?::hover|:focus-visible)[^{}]*\{[^{}]*(?:scale|matrix)\(",
        css_source,
    )


def test_band_geometry_gradient_and_fixed_ability_icons_are_mutation_guarded() -> None:
    """The approved band constants, soft name scrim, and fixed icon boxes are fixed contracts."""
    eos_source = EOS.read_text(encoding="utf-8")
    css_source = CSS.read_text(encoding="utf-8")
    _assert_band_and_hover_contract(eos_source, css_source)

    band_geometry_needle = (
        ".editor-band {\n"
        "  box-sizing: border-box; inline-size: 100%; max-inline-size: 100%;\n"
        "  block-size: var(--eos-band-height, 18.5125rem); min-block-size: var(--eos-band-height, 18.5125rem);\n"
        "  --eos-facts: .45rem; --eos-controls: 1.6rem; --eos-cards: 12.45rem; --eos-footer: 2.7rem; /* flipped: the +1.25rem sits at the top */\n"
        "  background: radial-gradient(ellipse at 16% 75%, color-mix(in srgb, var(--accent) 13%, #13111d), #13111d 65%);\n"
        "}"
    )
    assert css_source.count(band_geometry_needle) == 1

    mutations = [
        (eos_source, "22.5125", "22.5"),
        (eos_source, "18.5125", "18.5"),
        (css_source, "text-shadow: 0 1px 2px rgba(0,0,0,.9);", "text-shadow: 0 1px 2px rgba(0,0,0,.3);"),
        (css_source, band_geometry_needle, band_geometry_needle.replace("18.5125rem", "17.5rem")),
        (
            css_source,
            ".slot-ability-item .ability-icon { display: block; width: 22px; height: 22px; margin: 0; object-fit: contain; }",
            ".slot-ability-item .ability-icon { display: block; width: 22px; height: 22px; margin: 0; object-fit: contain; will-change: transform; }",
        ),
        (
            css_source,
            ".slot-ability-item .ability-icon { display: block; width: 22px; height: 22px; margin: 0; object-fit: contain; }",
            ".slot-ability-item .ability-icon { display: block; width: 22px; height: 22px; margin: 0; object-fit: contain; contain: layout paint; }",
        ),
        (
            css_source,
            ".slot-ability-item .ability-icon { display: block; width: 22px; height: 22px; margin: 0; object-fit: contain; }",
            ".slot-ability-item .ability-icon { display: block; width: 22px; height: 22px; margin: 0; object-fit: contain; }\n.slot-ability-item .ability-icon:hover { transform: scale(1.6364); }",
        ),
    ]
    for original, needle, replacement in mutations:
        assert original.count(needle) >= 1
        mutated = original.replace(needle, replacement, 1)
        with pytest.raises(AssertionError):
            _assert_band_and_hover_contract(
                mutated if original == eos_source and needle in eos_source else eos_source,
                mutated if original == css_source and needle in css_source else css_source,
            )


def test_order_guard_rejects_a_source_order_mutation() -> None:
    """Red mutation: a source-order sort bypasses the runtime tier assertion and
    must fail before a reordered ability list can reach the renderer."""
    source = read_entry_source()
    anchor = "ranked.sort((left, right) => left.rank - right.rank || left.index - right.index);"
    assert source.count(anchor) == 1
    mutated = source.replace(anchor, "ranked.sort((left, right) => left.index - right.index);", 1)
    mutation_body = "\norderLandAbilities([['DD', -0.5], ['BLOODLINE', 0.3, 'Elf']]);"
    result = run_in_module_context(
        mutation_body,
        source=mutated,
        extra_context_js=BARE_BROWSER_GLOBALS_JS,
        check=False,
    )
    assert result.returncode != 0
    assert "Land ability tier order invariant violated" in result.stderr
